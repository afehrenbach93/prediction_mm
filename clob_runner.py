"""
Polymarket global CLOB liquidity-reward quoter.

SAFETY:
  - Default CLOB_MODE=shadow
  - Live requires CLOB_MODE=live AND ELIGIBILITY_CONFIRMED=true
  - Kill via CLOB_KILL=true or Supabase clob_control.kill (polled each loop)
  - Ledger source of truth: Supabase (CSV dump optional)
"""
from __future__ import annotations

import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from core.clob_bookws import BookMidCache, MarketWsThread
from core.clob_ledger import ClobLedger
from core.clob_shadowfills import (
    ShadowFillState,
    ShadowQuote,
    process_tape,
    rollover_utc_day,
    summary,
)
from core.clobmaker import ClobQuoteParams, maker_quotes
from core.clobscore import max_spread_cents, min_size
from core.clobtrader import ClobTrader
from core.eligibility import resolve_live_mode

MODE = os.getenv("CLOB_MODE", "shadow").strip().lower()
BUDGET_PER = float(os.getenv("CLOB_BUDGET_PER_MARKET", "75"))
MAX_MARKETS = int(os.getenv("CLOB_MAX_MARKETS", "3"))
# Share ceiling is a backstop only — low mids need many shares per $ of risk.
MAX_INV = float(os.getenv("CLOB_MAX_INVENTORY", "200"))
# Primary inventory risk limit (USD notional = |shares| * mid).
MAX_INV_USD = float(os.getenv("CLOB_MAX_INVENTORY_USD", str(BUDGET_PER * 1.5)))
SPREAD_FRAC = float(os.getenv("CLOB_SPREAD_FRACTION", "0.5"))
POLL = int(os.getenv("CLOB_POLL_SECS", "30"))
EXPOSURE_CAP = float(os.getenv("CLOB_EXPOSURE_CAP", str(BUDGET_PER * MAX_MARKETS * 1.5)))
MIN_HOURS = float(os.getenv("CLOB_MIN_HOURS_TO_END", "168"))
PILOT_CSV = Path(os.getenv("CLOB_PILOT_CSV", "data/clob_scans/pilot_universe.csv"))
EARNINGS_SECS = int(os.getenv("CLOB_EARNINGS_SECS", "3600"))
STATUS_SECS = int(os.getenv("CLOB_STATUS_SECS", "120"))
MID_MOVE_TICKS = float(os.getenv("CLOB_MID_MOVE_TICKS", "1"))
MAX_REFRESH_SECS = float(os.getenv("CLOB_MAX_REFRESH_SECS", "2"))
WS_ENABLED = os.getenv("CLOB_WS", "1").strip().lower() not in ("0", "false", "no")
SHADOW_MAX_INV = float(os.getenv("CLOB_SHADOW_MAX_INVENTORY", "150"))
SHADOW_MAX_FILL = float(os.getenv("CLOB_SHADOW_MAX_FILL", "5"))
SHADOW_FILLS_PER_CYCLE = int(os.getenv("CLOB_SHADOW_FILLS_PER_CYCLE", "5"))
# Dashboard host label: ec2 (live) vs render (shadow). Override with CLOB_HOST_LABEL.
HOST_LABEL = (
    os.getenv("CLOB_HOST_LABEL")
    or ("ec2" if MODE == "live" else "render")
)


def log(msg: str):
    print(f"[clob] {datetime.now(timezone.utc):%H:%M:%S}Z {msg}", flush=True)


def load_pilot(path: Path, max_n: int, trader: ClobTrader | None = None) -> list[dict]:
    """Load up to ``max_n`` eligible pilot rows; skip empty books when trader given."""
    if not path.exists():
        log(f"pilot universe missing: {path}")
        return []
    candidates: list[dict] = []
    now = datetime.now(timezone.utc)
    # Pull extra candidates so no-book skips can still fill max_n slots.
    want = max_n * 8 if trader is not None else max_n
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if int(float(r.get("near_zero_days") or 0)) > 0:
                continue
            end = r.get("end_date") or ""
            if end:
                try:
                    end_ts = datetime.fromisoformat(end.replace("Z", "+00:00"))
                    if (end_ts - now).total_seconds() / 3600.0 < MIN_HOURS:
                        continue
                except Exception:
                    pass
            if str(r.get("provisional", "")).lower() in ("true", "1", "yes"):
                log(f"WARNING: quoting provisional market {r.get('slug')}")
            candidates.append(r)
            if len(candidates) >= want:
                break
    if trader is None:
        return candidates[:max_n]
    rows: list[dict] = []
    for r in candidates:
        tid = r.get("token_id") or ""
        try:
            bids, asks = trader.get_book(str(tid))
        except Exception as e:
            log(f"pilot skip book-err {(r.get('slug') or '')[:40]}: {e}")
            continue
        if not bids or not asks:
            log(f"pilot skip no book {(r.get('slug') or '')[:48]}")
            continue
        rows.append(r)
        if len(rows) >= max_n:
            break
    return rows


def _trade_size_shares(t: dict) -> float:
    """Normalize CLOB trade size to shares (API sometimes returns e6 base units)."""
    try:
        sz = float(t.get("size") or t.get("matched_amount") or 0)
    except (TypeError, ValueError):
        return 0.0
    # Heuristic: human share sizes for micro-pilot are << 1e5; e6 raw looks huge.
    if abs(sz) >= 1e5:
        sz = sz / 1e6
    return sz


def _our_trade_side(t: dict) -> str:
    """Return OUR buy/sell. ClobTrade.side is the taker side when we are maker."""
    side = (t.get("side") or "").upper()
    trader_side = (t.get("trader_side") or t.get("traderSide") or "").upper()
    if trader_side == "MAKER":
        if side == "BUY":
            return "SELL"
        if side == "SELL":
            return "BUY"
    return side


def positions_from_trades(trades: list) -> dict[str, float]:
    net: dict[str, float] = {}
    for t in trades or []:
        tid = str(t.get("asset_id") or t.get("token_id") or "")
        if not tid:
            continue
        sz = _trade_size_shares(t)
        if sz <= 0:
            continue
        side = _our_trade_side(t)
        if side not in ("BUY", "SELL"):
            continue
        net[tid] = net.get(tid, 0.0) + (sz if side == "BUY" else -sz)
    return net


def share_room_for_mid(mid: float) -> float:
    """Shares allowed at this mid from USD inventory cap (and share backstop)."""
    m = max(float(mid) or 0.0, 0.01)
    from_usd = MAX_INV_USD / m
    return max(MAX_INV, from_usd)


def breaker(positions: dict[str, float], mids: dict[str, float]) -> tuple[bool, str]:
    """Risk check. Requires a real mark in ``mids`` for USD limits — never assume 0.5."""
    hard_shares = max(MAX_INV * 20, share_room_for_mid(0.02) * 2)
    exposure = 0.0
    for tid, n in positions.items():
        mid = mids.get(tid)
        if mid is not None and mid > 0:
            notional = abs(n) * mid
            exposure += notional
            if notional > MAX_INV_USD:
                return True, (
                    f"inventory ${notional:.1f} ({n:+.1f} sh @ {mid:.3f}) "
                    f"on {tid[:16]}… > ${MAX_INV_USD:.0f}"
                )
        elif abs(n) > hard_shares:
            return True, (
                f"inventory {n:+.1f} sh on {tid[:16]}… > {hard_shares:.0f} "
                f"(no mark — cannot price USD risk)"
            )
        # else: unmarked modest bag — do not false-trip on assumed mid=0.5
    if exposure > EXPOSURE_CAP:
        return True, f"exposure ${exposure:.0f} > cap ${EXPOSURE_CAP:.0f}"
    return False, ""


def build_token_index(trader: ClobTrader) -> dict:
    idx = {}
    for m in trader.public.iter_sampling_markets():
        for t in m.get("tokens") or []:
            tid = t.get("token_id")
            if tid:
                idx[str(tid)] = m
    return idx


def resolve_mid(token_id: str, cache: BookMidCache, trader: ClobTrader) -> tuple[float, list, list]:
    """Prefer WS mid+book; REST fallback."""
    bids, asks = trader.get_book(token_id)
    rest_mid = ((bids[0][0] + asks[0][0]) / 2.0) if (bids and asks) else None
    ws_mid = cache.get_mid(token_id, max_age=5.0)
    mid = ws_mid if ws_mid is not None else (rest_mid if rest_mid is not None else 0.5)
    return mid, bids, asks


def quote_one(trader: ClobTrader, ledger: ClobLedger, row: dict, position: float,
              params: ClobQuoteParams, idx: dict, cache: BookMidCache,
              last_quote_mid: dict, last_refresh: dict) -> tuple[int, float, ShadowQuote | None]:
    token_id = row["token_id"]
    mid, bids, asks = resolve_mid(token_id, cache, trader)
    if not bids or not asks:
        log(f"  no book {(row.get('slug') or '')[:40]}")
        return 0, mid, None

    m = idx.get(str(token_id)) or {}
    rew = m.get("rewards") or {}
    v = max_spread_cents(rew) or 4.5
    msz = min_size(rew)
    tick = float(m.get("minimum_tick_size") or 0.01)
    neg = bool(m.get("neg_risk"))
    try:
        if row.get("max_spread"):
            from core.clobscore import normalize_max_spread_cents
            v = normalize_max_spread_cents(float(row["max_spread"]))
        if row.get("min_size"):
            msz = float(row["min_size"])
    except (TypeError, ValueError):
        pass

    # Refresh rate limit + mid-move threshold
    prev = last_quote_mid.get(token_id)
    now = time.time()
    moved = prev is None or abs(mid - prev) >= MID_MOVE_TICKS * tick
    cooled = (now - last_refresh.get(token_id, 0.0)) >= MAX_REFRESH_SECS

    def _shadow_quote_from_mid() -> ShadowQuote | None:
        """Recompute bid/ask from current mid for the tape simulator (no place)."""
        room = share_room_for_mid(mid)
        qparams = ClobQuoteParams(
            budget_usd=params.budget_usd,
            spread_fraction=params.spread_fraction,
            max_inventory=room,
            min_price=params.min_price,
            max_price=params.max_price,
        )
        qs = maker_quotes(mid, v, tick, position, msz, qparams)
        if not qs:
            return None
        bid_px = ask_px = None
        bid_sz = ask_sz = 0.0
        for qq in qs:
            if qq.side == "BUY":
                bid_px, bid_sz = qq.price, qq.size
            else:
                ask_px, ask_sz = qq.price, qq.size
        last_quote_mid[f"{token_id}:bid"] = bid_px
        last_quote_mid[f"{token_id}:ask"] = ask_px
        return ShadowQuote(
            token_id=token_id, bid=bid_px, ask=ask_px,
            bid_size=bid_sz, ask_size=ask_sz, mid=mid, slug=row.get("slug") or "",
        )

    if not moved and token_id in last_quote_mid:
        # Hold resting shadow orders, but refresh sim quote levels to current mid.
        return 0, mid, _shadow_quote_from_mid()
    if not cooled and prev is not None:
        return 0, mid, _shadow_quote_from_mid()

    # Per-market share room from USD inventory cap (cheap mids need more shares).
    room = share_room_for_mid(mid)
    qparams = ClobQuoteParams(
        budget_usd=params.budget_usd,
        spread_fraction=params.spread_fraction,
        max_inventory=room,
        min_price=params.min_price,
        max_price=params.max_price,
    )
    quotes = maker_quotes(mid, v, tick, position, msz, qparams)
    # Live: cannot SELL outcome tokens we don't hold (CLOB checks conditional
    # balance). Skip asks until inventory exists from buys/fills.
    if trader.live:
        held = max(0.0, position)
        before = len(quotes)
        quotes = [
            q for q in quotes
            if q.side != "SELL" or q.size <= held + 1e-9
        ]
        if before and not quotes:
            log(f"  {(row.get('slug') or '')[:36]} skip — no buy/sell sized for inv={position:.2f}")
            return 0, mid, None
        skipped_sells = before - len(quotes)
        if skipped_sells:
            log(f"  {(row.get('slug') or '')[:36]} skip {skipped_sells} SELL (inv={position:.2f})")

    trader.cancel_market(token_id=token_id, condition_id=row.get("condition_id") or "")
    n = 0
    bid_px = ask_px = None
    bid_sz = ask_sz = 0.0
    for q in quotes:
        # simple 429 backoff
        for attempt in range(4):
            try:
                trader.place_limit(
                    token_id, q.side, q.price, q.size,
                    tick_size=str(tick), neg_risk=neg, post_only=True,
                )
                break
            except Exception as e:
                if "429" in str(e) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                log(f"  place fail {q.side} {q.size}@{q.price}: {e}")
                raise
        ledger.log_quote(
            token_id, q.side, q.price, q.size, mid, MODE,
            shadow=not trader.live, slug=row.get("slug") or "",
        )
        if q.side == "BUY":
            bid_px, bid_sz = q.price, q.size
        else:
            ask_px, ask_sz = q.price, q.size
        n += 1
    last_quote_mid[token_id] = mid
    last_quote_mid[f"{token_id}:bid"] = bid_px
    last_quote_mid[f"{token_id}:ask"] = ask_px
    last_refresh[token_id] = now
    src = "ws" if cache.get_mid(token_id) is not None else "rest"
    log(f"  {(row.get('slug') or '')[:36]} mid={mid:.3f}({src}) v={v}¢ -> {n}@{params.budget_usd:.0f}$"
        + (" [SHADOW]" if not trader.live else ""))
    sq = ShadowQuote(
        token_id=token_id, bid=bid_px, ask=ask_px,
        bid_size=bid_sz, ask_size=ask_sz, mid=mid, slug=row.get("slug") or "",
    )
    return n, mid, sq


def main():
    if MODE == "off":
        log("CLOB_MODE=off — exit")
        return

    live, refuse = resolve_live_mode(MODE)
    if MODE == "live" and not live:
        log(refuse)

    trader = ClobTrader.from_env()
    ledger = ClobLedger()
    params = ClobQuoteParams(
        budget_usd=BUDGET_PER, spread_fraction=SPREAD_FRAC, max_inventory=MAX_INV,
    )
    log(f"START mode={'LIVE' if trader.live else 'SHADOW'} "
        f"(CLOB_MODE={MODE}) budget/mkt=${BUDGET_PER} max_mkts={MAX_MARKETS} "
        f"spread_frac={SPREAD_FRAC} max_inv_usd=${MAX_INV_USD:.0f} poll={POLL}s")
    if not trader.live:
        log("SHADOW: no orders reach CLOB. Live needs CLOB_MODE=live AND "
            "ELIGIBILITY_CONFIRMED=true.")
    log("kill: set CLOB_KILL=true or clob_control.kill=true in Supabase")

    tripped = False
    standing_aside = False
    last_earnings = 0.0
    last_status = 0.0
    idx = None
    cache = BookMidCache()
    ws_thread: MarketWsThread | None = None
    last_quote_mid: dict = {}
    last_refresh: dict = {}
    shadow_state = ShadowFillState()
    if not trader.live:
        log(f"shadow-sample: max_inv={SHADOW_MAX_INV} max_fill={SHADOW_MAX_FILL} "
            f"fills/cycle={SHADOW_FILLS_PER_CYCLE} (UTC day rollover enabled)")

    while True:
        try:
            if ledger.kill_requested() or MODE == "off":
                if not standing_aside:
                    log("KILL — cancelling all, standing aside (process stays up)")
                    trader.cancel_all()
                    ledger.event("kill", mode=MODE)
                    standing_aside = True
                if MODE == "off":
                    return
                time.sleep(POLL)
                continue
            else:
                standing_aside = False

            pilot = load_pilot(PILOT_CSV, MAX_MARKETS, trader=trader)
            if not pilot:
                log("no pilot markets — idle")
                time.sleep(POLL)
                continue

            if idx is None:
                log("building sampling index…")
                idx = build_token_index(trader)
                log(f"index tokens={len(idx)}")
            # (Re)subscribe WS when pilot set changes (e.g. empty-book swap).
            pilot_assets = [str(r["token_id"]) for r in pilot]
            if WS_ENABLED and (
                ws_thread is None
                or list(ws_thread.asset_ids) != pilot_assets
            ):
                if ws_thread is not None:
                    try:
                        ws_thread.stop()
                    except Exception:
                        pass
                ws_thread = MarketWsThread(pilot_assets, cache)
                ws_thread.start()
                log(f"ws subscribed assets={len(pilot_assets)} "
                    f"slugs={[ (r.get('slug') or '')[:28] for r in pilot ]}")

            trades = trader.get_trades()
            new_fills = 0
            for t in trades:
                if ledger.log_fill(t, simulated=False):
                    new_fills += 1
            if new_fills:
                log(f"fills: +{new_fills} new (unique trade_id; "
                    f"seen={len(ledger.seen_trade_ids)})")
            # Live: prefer open positions API (trade netting misses maker side /
            # pagination). Shadow keeps sim inventory.
            pos_marks: dict[str, float] = {}
            if trader.live:
                book = trader.get_positions()
                if book is not None:
                    positions, pos_marks = book
                else:
                    positions = positions_from_trades(trades)
            else:
                positions = positions_from_trades(trades)
                for tid, n in shadow_state.inventory.items():
                    positions[tid] = positions.get(tid, 0.0) + n

            mids: dict[str, float] = {}
            # Seed marks: WS → book → position cur/avg (never invent 0.5).
            for tid in positions:
                wm = cache.get_mid(tid, max_age=30.0)
                if wm is not None:
                    mids[tid] = wm
                    continue
                try:
                    bids, asks = trader.get_book(tid)
                    if bids and asks:
                        mids[tid] = (bids[0][0] + asks[0][0]) / 2.0
                        continue
                except Exception:
                    pass
                if tid in pos_marks:
                    mids[tid] = pos_marks[tid]
            pilot_ids = {str(r["token_id"]) for r in pilot}
            stray = {t: n for t, n in positions.items()
                     if t not in pilot_ids and abs(n) > 1e-6}
            if stray:
                bits = []
                for tid, n in sorted(stray.items(), key=lambda kv: -abs(kv[1]))[:5]:
                    mk = mids.get(tid)
                    bits.append(
                        f"{tid[:12]}…={n:+.1f}"
                        + (f"(${abs(n)*mk:.1f})" if mk else "(no mark)")
                    )
                log("  leftover inventory (not in pilot): " + ", ".join(bits))
            shadow_quotes: list[ShadowQuote] = []
            total = 0
            for row in pilot:
                pos = positions.get(row["token_id"], 0.0)
                if tripped:
                    mid, _, _ = resolve_mid(row["token_id"], cache, trader)
                    mids[row["token_id"]] = mid
                    continue
                n, mid, sq = quote_one(
                    trader, ledger, row, pos, params, idx, cache,
                    last_quote_mid, last_refresh,
                )
                mids[row["token_id"]] = mid
                total += n
                if sq is not None:
                    shadow_quotes.append(sq)
            # shadow fills from public tape
            if not tripped and not trader.live and shadow_quotes:
                new_f = process_tape(
                    shadow_quotes, shadow_state, ledger=ledger,
                    max_fills_per_cycle=SHADOW_FILLS_PER_CYCLE,
                    max_fill_size=SHADOW_MAX_FILL,
                    max_inventory=SHADOW_MAX_INV,
                )
                if new_f:
                    log(f"shadow-fills: {len(new_f)} simulated "
                        f"(today={shadow_state.fills_today})")
            trip, reason = breaker(positions, mids)
            if trip:
                if not tripped:
                    trader.cancel_all()
                    ledger.event("breaker", reason=reason)
                    log(f"*** BREAKER: {reason} ***")
                    top = sorted(positions.items(), key=lambda kv: -abs(kv[1]))[:5]
                    log("  positions: " + ", ".join(
                        f"{tid[:12]}…={n:+.1f}" for tid, n in top
                    ))
                tripped = True
                # Shadow inventory is in-process; clear so a redeploy isn't
                # required to resume quoting after a sim backfill spike.
                if not trader.live:
                    shadow_state.inventory.clear()
                    shadow_state.avg_entry.clear()
                    log("shadow inventory reset after breaker")
                    tripped = False
                else:
                    log("breaker tripped — standing aside")
            elif tripped:
                log("breaker clear — resuming quotes")
                tripped = False
                ledger.event("breaker_clear", reason="under_limits")

            # UTC day rollover → lock prior day PnL sample, start fresh inventory
            if not trader.live and mids:
                rolled, _, prior = rollover_utc_day(shadow_state, mids)
                if rolled and prior:
                    est = sum(float(r.get("avg_est_daily") or 0) for r in pilot)
                    ledger.log_daily_pnl(
                        trading_pnl=float(prior.get("realized_pnl") or prior.get("net_pnl_today") or 0),
                        rewards_usd=0.0, est_gross=est,
                        note=f"shadow_day_close fills={prior.get('fills_today')} "
                             f"adverse={prior.get('avg_adverse_move')}",
                    )
                    log(f"shadow-day-close {prior.get('day')}: "
                        f"fills={prior.get('fills_today')} "
                        f"net=${prior.get('realized_pnl')} "
                        f"adverse={prior.get('avg_adverse_move')}")

            now = time.time()
            if now - last_status >= STATUS_SECS:
                coll = trader.get_collateral_usd() if trader.live else None
                ledger.update_runner_status(
                    mode="live" if trader.live else "shadow",
                    host=HOST_LABEL,
                    collateral_usd=coll,
                    note="heartbeat",
                    payload={
                        "pilot_n": len(pilot),
                        "orders": total,
                        "tripped": tripped,
                        "seen_fills": len(ledger.seen_trade_ids),
                    },
                )
                if coll is not None:
                    log(f"collateral=${coll:.2f} host={HOST_LABEL}")
                last_status = now

            if now - last_earnings >= EARNINGS_SECS:
                day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                rewards_usd = 0.0
                if trader.live:
                    # Authenticated CLOB earnings = actuals (not score estimates).
                    per_mkt = trader.get_earnings_for_day(day)
                    for a in per_mkt:
                        try:
                            amt = float(
                                a.get("earnings") or a.get("amount_usd")
                                or a.get("amount") or 0
                            )
                        except (TypeError, ValueError):
                            amt = 0.0
                        cid = str(a.get("condition_id") or "")
                        ledger.log_rewards(
                            a,
                            note=f"actual_day:{day}",
                            source="actual",
                            amount_usd=amt,
                            market_slug=str(
                                a.get("market_slug") or a.get("slug") or ""
                            ),
                            condition_id=cid,
                            dedupe_key=f"actual:{day}:{cid or 'row'}",
                        )
                    earn = trader.get_earnings_today()
                    rewards_usd = ClobTrader.sum_earnings_usd(earn)
                    if earn is not None and not (
                        isinstance(earn, dict) and earn.get("_err")
                    ):
                        ledger.log_rewards(
                            earn,
                            note=f"actual_total:{day}",
                            source="actual",
                            amount_usd=rewards_usd,
                            dedupe_key=f"actual:{day}:total",
                        )
                        log(f"rewards actual day={day} ${rewards_usd:.4f} "
                            f"markets={len(per_mkt)}")
                    elif isinstance(earn, dict) and earn.get("_err"):
                        ledger.log_rewards(
                            earn, note="earnings_poll_error", source="event",
                        )
                else:
                    # Shadow: keep score-based estimate for scale-gate dry runs.
                    est_payload = {
                        "pilot": [
                            {
                                "slug": r.get("slug"),
                                "avg_est_daily": r.get("avg_est_daily"),
                            }
                            for r in pilot
                        ],
                    }
                    ledger.log_rewards(
                        est_payload, note="shadow_estimate", source="estimate",
                        amount_usd=sum(
                            float(r.get("avg_est_daily") or 0) for r in pilot
                        ),
                        dedupe_key=f"estimate:{day}:shadow_pilot",
                    )

                est = sum(float(r.get("avg_est_daily") or 0) for r in pilot)
                trading = 0.0
                if not trader.live:
                    s = summary(shadow_state, mids)
                    trading = float(s["net_pnl_today"])
                    log(f"shadow-pnl: day={s['day']} fills={s['fills_today']} "
                        f"net=${s['net_pnl_today']} mtm=${s['mtm_pnl']} "
                        f"realized=${s['realized_pnl_today']} "
                        f"avg_adverse={s['avg_adverse_move']}")
                ledger.log_daily_pnl(
                    trading_pnl=trading,
                    rewards_usd=rewards_usd,
                    est_gross=est,
                    note="shadow_mtm" if not trader.live else "live_actual",
                )
                last_earnings = now

            log(f"cycle: {len(pilot)} mkts, {total} orders, "
                f"shadow_log={len(trader.shadow_orders)} tripped={tripped} "
                f"ws={'up' if cache.connected else 'down'} "
                f"fills_today={shadow_state.fills_today if not trader.live else '-'} "
                f"host={HOST_LABEL}")
        except Exception as e:
            log(f"loop error: {e}")
            try:
                ledger.event("error", error=str(e))
            except Exception:
                pass
            if "429" in str(e):
                time.sleep(min(60, POLL * 2))
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
