"""
Polymarket global CLOB liquidity-reward quoter (deep-dive §7.4–7.5).

Two-sided post-only quotes at configurable fraction of max_spread, full
cancel/replace each cycle, inventory caps, hard kill switch, fill + reward logs.

SAFETY: CLOB_MODE=shadow (default) records intended orders; nothing hits the
exchange until CLOB_MODE=live. Touch kill file or CLOB_MODE=off to cancel/halt.

Pilot defaults (§7.5): $50–100/market, max 3 competed markets from
data/clob_scans/pilot_universe.csv (near-zero excluded).
"""
from __future__ import annotations

import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from core.clob_ledger import ClobLedger
from core.clobmaker import ClobQuoteParams, maker_quotes
from core.clobscore import daily_rate, max_spread_cents, min_size
from core.clobtrader import ClobTrader

MODE = os.getenv("CLOB_MODE", "shadow").strip().lower()
BUDGET_PER = float(os.getenv("CLOB_BUDGET_PER_MARKET", "75"))
MAX_MARKETS = int(os.getenv("CLOB_MAX_MARKETS", "3"))
MAX_INV = float(os.getenv("CLOB_MAX_INVENTORY", "200"))
SPREAD_FRAC = float(os.getenv("CLOB_SPREAD_FRACTION", "0.5"))
POLL = int(os.getenv("CLOB_POLL_SECS", "30"))
EXPOSURE_CAP = float(os.getenv("CLOB_EXPOSURE_CAP", str(BUDGET_PER * MAX_MARKETS * 1.5)))
MIN_HOURS = float(os.getenv("CLOB_MIN_HOURS_TO_END", "168"))
KILL_FILE = Path(os.getenv("CLOB_KILL_FILE", "data/clob_logs/KILL"))
PILOT_CSV = Path(os.getenv("CLOB_PILOT_CSV", "data/clob_scans/pilot_universe.csv"))
EARNINGS_SECS = int(os.getenv("CLOB_EARNINGS_SECS", "3600"))


def log(msg: str):
    print(f"[clob] {datetime.now(timezone.utc):%H:%M:%S}Z {msg}", flush=True)


def load_pilot(path: Path, max_n: int) -> list[dict]:
    if not path.exists():
        log(f"pilot universe missing: {path} — run clob_yield_scan + clob_stability")
        return []
    rows = []
    now = datetime.now(timezone.utc)
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
            rows.append(r)
            if len(rows) >= max_n:
                break
    return rows


def kill_requested() -> bool:
    return KILL_FILE.exists() or MODE == "off"


def positions_from_trades(trades: list) -> dict[str, float]:
    net: dict[str, float] = {}
    for t in trades or []:
        tid = str(t.get("asset_id") or t.get("token_id") or "")
        if not tid:
            continue
        try:
            sz = float(t.get("size") or t.get("matched_amount") or 0)
        except (TypeError, ValueError):
            continue
        side = (t.get("side") or "").upper()
        net[tid] = net.get(tid, 0.0) + (sz if side == "BUY" else -sz)
    return net


def breaker(positions: dict[str, float], mids: dict[str, float]) -> tuple[bool, str]:
    for tid, n in positions.items():
        if abs(n) > MAX_INV:
            return True, f"inventory {n:+.1f} on {tid[:16]}… > {MAX_INV}"
    exposure = sum(abs(n) * mids.get(tid, 0.5) for tid, n in positions.items())
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


def quote_one(trader: ClobTrader, ledger: ClobLedger, row: dict, position: float,
              params: ClobQuoteParams, idx: dict) -> tuple[int, float]:
    token_id = row["token_id"]
    bids, asks = trader.get_book(token_id)
    if not bids or not asks:
        log(f"  no book {(row.get('slug') or '')[:40]}")
        return 0, 0.5
    mid = (bids[0][0] + asks[0][0]) / 2.0
    m = idx.get(str(token_id)) or {}
    rew = m.get("rewards") or {}
    v = max_spread_cents(rew) or 4.5
    msz = min_size(rew)
    tick = str(m.get("minimum_tick_size") or "0.01")
    neg = bool(m.get("neg_risk"))
    try:
        if row.get("max_spread"):
            v = float(row["max_spread"]) * 100.0
        if row.get("min_size"):
            msz = float(row["min_size"])
    except (TypeError, ValueError):
        pass

    quotes = maker_quotes(mid, v, float(tick), position, msz, params)
    trader.cancel_market(token_id=token_id, condition_id=row.get("condition_id") or "")
    n = 0
    for q in quotes:
        trader.place_limit(
            token_id, q.side, q.price, q.size,
            tick_size=tick, neg_risk=neg, post_only=True,
        )
        ledger.log_quote(
            token_id, q.side, q.price, q.size, mid, MODE,
            shadow=not trader.live, slug=row.get("slug") or "",
        )
        n += 1
    log(f"  {(row.get('slug') or '')[:36]} mid={mid:.3f} v={v}¢ -> {n}@{params.budget_usd:.0f}$"
        + (" [SHADOW]" if not trader.live else ""))
    return n, mid


def main():
    if MODE == "off":
        log("CLOB_MODE=off — exit")
        return
    live = MODE == "live"
    trader = ClobTrader(live=live)
    ledger = ClobLedger()
    params = ClobQuoteParams(
        budget_usd=BUDGET_PER, spread_fraction=SPREAD_FRAC, max_inventory=MAX_INV,
    )
    log(f"START mode={MODE.upper()} budget/mkt=${BUDGET_PER} max_mkts={MAX_MARKETS} "
        f"spread_frac={SPREAD_FRAC} poll={POLL}s")
    if not live:
        log("SHADOW: no orders reach CLOB. Set CLOB_MODE=live only for micro-pilot.")
    log(f"kill file: touch {KILL_FILE}")

    tripped = False
    last_earnings = 0.0
    idx = None

    while True:
        try:
            if kill_requested():
                log("KILL — cancelling all, standing aside")
                trader.cancel_all()
                ledger.event("kill", mode=MODE)
                if MODE == "off":
                    return
                time.sleep(POLL)
                continue

            pilot = load_pilot(PILOT_CSV, MAX_MARKETS)
            if not pilot:
                log("no pilot markets — idle")
                time.sleep(POLL)
                continue

            if idx is None:
                log("building sampling index…")
                idx = build_token_index(trader)
                log(f"index tokens={len(idx)}")

            trades = trader.get_trades()
            for t in trades:
                ledger.log_fill(t)
            positions = positions_from_trades(trades)

            mids: dict[str, float] = {}
            total = 0
            if not tripped:
                for row in pilot:
                    pos = positions.get(row["token_id"], 0.0)
                    n, mid = quote_one(trader, ledger, row, pos, params, idx)
                    mids[row["token_id"]] = mid
                    total += n
                trip, reason = breaker(positions, mids)
                if trip:
                    tripped = True
                    trader.cancel_all()
                    ledger.event("breaker", reason=reason)
                    log(f"*** BREAKER: {reason} ***")
            else:
                log("breaker tripped — standing aside")

            now = time.time()
            if now - last_earnings >= EARNINGS_SECS:
                earn = trader.get_earnings_today()
                if earn is not None:
                    ledger.log_rewards(earn, note="daily_poll")
                # daily pnl stub for scale gate (operator fills trading_pnl when known)
                est = sum(float(r.get("avg_est_daily") or 0) for r in pilot)
                ledger.log_daily_pnl(
                    trading_pnl=0.0, rewards_usd=0.0, est_gross=est,
                    note="auto_stub_update_from_fills_rewards",
                )
                last_earnings = now

            log(f"cycle: {len(pilot)} mkts, {total} orders, "
                f"shadow_log={len(trader.shadow_orders)} tripped={tripped}")
        except Exception as e:
            log(f"loop error: {e}")
            ledger.event("error", error=str(e))
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
