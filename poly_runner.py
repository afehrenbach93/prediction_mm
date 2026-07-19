"""
Polymarket US liquidity-reward maker — autonomous worker (Render Background Worker).

Rests post-only orders at the touch on ACTIVE reward markets during their reward
windows (day_of / live), to farm the liquidity-incentive pool + maker rebate, with
an inventory cap and a daily-loss breaker. Mirrors the Kalshi fleet shell
(heartbeat + breaker + kill switch).

SAFETY: defaults to BOT_MODE=shadow. In shadow, PolyClient records intended orders
and places NONE — a real order is impossible unless BOT_MODE=live is explicitly set.
The flip to live is the operator's action, after the reward economics validate.

Env:
  BOT_MODE              shadow | live | off      (default shadow)
  POLY_BUDGET           total resting $ across markets   (default 200)
  POLY_SIZE             contracts per side per market    (default 100)
  POLY_MAX_INVENTORY    |net| contracts cap per market   (default 300)
  POLY_DAILY_LOSS       $ daily realized-loss breaker     (default 15)
  POLY_POLL_SECS        quote-refresh cadence            (default 20)
  POLY_DENY_PREFIXES    comma-separated slug prefixes to never quote
                        (default: aec-cod-)
  POLY_MIN_HOURS_TO_END skip markets settling sooner than this (default 72)
  POLY_MIN_MID / MAX    mid band for pilot selection (default 0.10–0.90)
  POLY_REQUIRE_COMPETED skip near-zero competition books (default 1)
  POLY_EARNINGS_SECS    poll /v1/incentives/earnings cadence (default 3600)
  POLYMARKET_API_KEY / POLYMARKET_SECRET   credentials
"""
import os
import sys
import time
from datetime import datetime, timezone

from core.ledger import OpsLedger
from core.polyclient import PolyClient, load_env
from core.polymaker import MakerParams, maker_quotes, program_active
from core.rewardscore import (
    hours_to_settle,
    mid_in_band,
    score_market,
    slug_denied,
)

MODE = os.getenv("BOT_MODE", "shadow").strip().lower()
BUDGET = float(os.getenv("POLY_BUDGET", "200"))
SIZE = float(os.getenv("POLY_SIZE", "100"))
MAX_INV = float(os.getenv("POLY_MAX_INVENTORY", "300"))
DAILY_LOSS = float(os.getenv("POLY_DAILY_LOSS", "15"))
# Hard exposure ceiling — bounds how much capital can be at risk regardless of
# P&L-field uncertainty. Filled exposure beyond this trips the breaker.
EXPOSURE_CAP = float(os.getenv("POLY_EXPOSURE_CAP", str(round(BUDGET * 1.5))))
MAX_MARKETS = int(os.getenv("POLY_MAX_MARKETS", "5"))   # cap breadth -> bound budget
POLL = int(os.getenv("POLY_POLL_SECS", "20"))
META_REFRESH = 600          # refresh reward-market metadata every 10 min
PARAMS = MakerParams(size=SIZE, max_inventory=MAX_INV)

# Pilot selection / scope guards (deep-dive §7: competed + catalyst-free first)
DENY_PREFIXES = [
    p.strip() for p in os.getenv("POLY_DENY_PREFIXES", "aec-cod-").split(",")
    if p.strip()
]
MIN_HOURS_TO_END = float(os.getenv("POLY_MIN_HOURS_TO_END", "72"))
MIN_MID = float(os.getenv("POLY_MIN_MID", "0.10"))
MAX_MID = float(os.getenv("POLY_MAX_MID", "0.90"))
REQUIRE_COMPETED = os.getenv("POLY_REQUIRE_COMPETED", "1").strip().lower() not in (
    "0", "false", "no", ""
)
EARNINGS_SECS = int(os.getenv("POLY_EARNINGS_SECS", "3600"))


def log(msg: str):
    print(f"[poly] {datetime.now(timezone.utc):%H:%M:%S}Z {msg}", flush=True)


def iso_ts(s: str) -> float:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


class RewardMarketCache:
    """Reward markets (from /v1/incentives) joined with each market's game timing,
    refreshed periodically so the fast quote loop just re-checks windows + books."""
    def __init__(self, client: PolyClient):
        self.c = client
        self.markets: dict[str, dict] = {}   # slug -> meta
        self._last = 0.0

    def refresh(self):
        # group ALL active programs by market (any period — robust to rotation)
        by_slug: dict[str, list] = {}
        for tp in self.c.get_incentives():
            by_slug.setdefault(tp["marketSlug"], []).append(tp)
        out = {}
        skipped_deny = 0
        for slug, tps in by_slug.items():
            if slug_denied(slug, DENY_PREFIXES):
                skipped_deny += 1
                continue
            mk = self.c.get_market(slug)
            if not mk or mk.get("closed"):
                continue
            # prefer live program for pool/discount; else max pool
            live = next((t for t in tps if t.get("period") == "live"), None)
            chosen = live or max(tps, key=lambda t: float(t.get("rewardPool") or 0))
            out[slug] = {
                "game_start": iso_ts(mk.get("gameStartTime")),
                "settle": iso_ts(mk.get("endDate")),
                "discount": float(chosen.get("discountFactor") or 0.3),
                "programs": [{"period": tp.get("period"),
                              "pool": float(tp.get("rewardPool") or 0),
                              "discount": float(tp.get("discountFactor") or 0.3),
                              "start": iso_ts(tp.get("start")),
                              "end": iso_ts(tp.get("end"))} for tp in tps],
            }
        self.markets = out
        self._last = time.time()
        log(f"reward-market meta refreshed: {len(out)} open reward markets"
            + (f" (denied {skipped_deny} by prefix)" if skipped_deny else ""))

    def in_window(self, now: float):
        """Markets with an active reward program right now.
        Returns [(slug, period, pool, discount, settle)]."""
        if time.time() - self._last > META_REFRESH or not self.markets:
            self.refresh()
        live = []
        for slug, m in self.markets.items():
            for pg in m["programs"]:
                if program_active(now, pg["period"], pg["start"], pg["end"],
                                  m["game_start"], m["settle"]):
                    live.append((slug, pg["period"], pg["pool"],
                                 pg.get("discount", m["discount"]), m["settle"]))
                    break
        return live


_logged_raw_pos = False


def _first_num(d: dict, keys) -> float:
    """First parseable numeric among `keys` (handles {"value": "..."} wrappers)."""
    for k in keys:
        v = d.get(k)
        if isinstance(v, dict):
            v = v.get("value")
        try:
            if v is not None:
                return float(v)
        except (TypeError, ValueError):
            pass
    return 0.0


# Live API uses netPosition (confirmed 2026-06-20); keep legacy aliases too.
_NET_KEYS = ("netPosition", "net", "netQuantity", "quantity", "size", "position")
_ENTRY_KEYS = ("avgPrice", "averagePrice", "avgEntryPrice", "costBasis", "price")


def positions_net(client: PolyClient) -> dict:
    """slug -> {net, entry} from /v1/portfolio/positions, parsed defensively.
    Logs the raw shape ONCE on first real fills so the field names can be
    verified live (shadow stays empty, so this never trips in shadow)."""
    global _logged_raw_pos
    s, d = client.get_positions()
    raw = (d or {}).get("positions", {}) if isinstance(d, dict) else {}
    if raw and not _logged_raw_pos:
        log(f"RAW positions (verify breaker fields): {str(raw)[:500]}")
        _logged_raw_pos = True
    items = raw.items() if isinstance(raw, dict) else \
        [(p.get("marketSlug"), p) for p in raw if isinstance(p, dict)]
    out = {}
    for slug, p in items:
        if not isinstance(p, dict):
            continue
        out[slug] = {
            "net": _first_num(p, _NET_KEYS),
            "entry": _first_num(p, _ENTRY_KEYS),
        }
    return out


def cancel_all_orders(client: PolyClient) -> int:
    """Cancel all our resting orders (best-effort) — used on a breaker trip."""
    s, d = client.get_open_orders()
    orders = []
    if isinstance(d, dict):
        orders = d.get("orders") or d.get("openOrders") or []
    elif isinstance(d, list):
        orders = d
    n = 0
    for o in orders:
        if not isinstance(o, dict):
            continue
        oid = o.get("id") or o.get("orderId")
        if oid:
            # the cancel body REQUIRES the order's marketSlug (else 400)
            cs, _ = client.cancel_order(oid, o.get("marketSlug", ""))
            if cs == 200:
                n += 1
    return n


def breaker_check(client: PolyClient, positions: dict) -> tuple[bool, str]:
    """Robust live risk guard. Trips on (a) any single-market inventory past the
    cap (got run over on one side — the in-play adverse-selection failure mode),
    (b) total filled exposure past EXPOSURE_CAP (bounds capital at risk without
    needing exact P&L fields), or (c) best-effort unrealized loss past DAILY_LOSS."""
    total_exposure, unreal = 0.0, 0.0
    for slug, info in positions.items():
        net, entry = info["net"], info["entry"]
        if abs(net) > MAX_INV:
            return True, f"inventory {net:+.0f} on {slug[:34]} exceeds cap {MAX_INV:.0f}"
        b, o = client.get_book(slug)
        mark = ((b[0][0] + o[0][0]) / 2) if (b and o) else (entry or 0.5)
        total_exposure += abs(net) * mark
        if entry:
            unreal += net * (mark - entry)   # long: gain if mark>entry; short: opposite
    if total_exposure > EXPOSURE_CAP:
        return True, f"exposure ${total_exposure:.0f} exceeds cap ${EXPOSURE_CAP:.0f}"
    if unreal <= -DAILY_LOSS:
        return True, f"unrealized P&L ${unreal:.2f} <= -${DAILY_LOSS:.0f} daily limit"
    return False, ""


def select_markets(client: PolyClient, windows: list, budget: float, now: float,
                   max_markets: int) -> list[tuple]:
    """Rank in-window markets by estimated reward capture (US score), apply
    pilot filters: mid band, min hours to settle, competed book preferred.

    Returns [(slug, period, pool, est_reward, near_zero, mid), ...] top N.
    """
    ranked = []
    for slug, period, pool, discount, settle in windows:
        hrs = hours_to_settle(settle, now)
        if hrs is not None and hrs < MIN_HOURS_TO_END:
            continue
        bids, offers = client.get_book(slug)
        sr = score_market(bids, offers, pool=pool, budget=budget / max(1, max_markets),
                          discount=discount)
        if sr is None:
            continue
        if not mid_in_band(sr.mid, MIN_MID, MAX_MID):
            continue
        if REQUIRE_COMPETED and sr.near_zero:
            continue
        ranked.append((slug, period, pool, sr.est_reward, sr.near_zero, sr.mid,
                       sr.book_score))
    ranked.sort(key=lambda r: -r[3])  # est_reward desc
    return ranked[:max_markets]


def refresh_quotes(client: PolyClient, slug: str, positions: dict, size: float,
                   ledger: OpsLedger | None = None, mode: str = "shadow"):
    """Post fresh post-only quotes at the current touch for `slug`, sized to the
    per-market budget allocation. The caller cancels all resting orders first each
    cycle (full reconcile), so this only PLACES — it must not be called without
    that preceding cancel, or orders accumulate."""
    bids, offers = client.get_book(slug)
    best_bid = bids[0][0] if bids else None
    best_ask = offers[0][0] if offers else None
    mid = ((best_bid + best_ask) / 2) if (best_bid is not None and best_ask is not None) else None
    pos = positions.get(slug, {}).get("net", 0.0)
    params = MakerParams(size=size, max_inventory=MAX_INV)
    quotes = maker_quotes(best_bid, best_ask, pos, params)
    n = 0
    for intent, price, qty in quotes:
        client.place_order(slug, intent, price, qty, post_only=True)
        if ledger is not None:
            ledger.log_quote(slug, intent, price, qty, mid, mode=mode,
                             shadow=not client.live)
        n += 1
    return n, best_bid, best_ask


def maybe_poll_earnings(client: PolyClient, ledger: OpsLedger, last_poll: float) -> float:
    now = time.time()
    if now - last_poll < EARNINGS_SECS:
        return last_poll
    s, d = client.get_incentive_earnings()
    ledger.log_rewards(d if isinstance(d, (dict, list)) else {"status": s, "body": d},
                       note=f"status={s}")
    log(f"earnings poll status={s} (logged to data/logs/rewards.csv)")
    return now


def main():
    if MODE == "off":
        log("BOT_MODE=off — exiting without quoting.")
        return
    env = load_env() if os.path.exists(".env") else {}
    api_key = os.getenv("POLYMARKET_API_KEY") or env.get("POLYMARKET_API_KEY", "")
    secret = os.getenv("POLYMARKET_SECRET") or env.get("POLYMARKET_SECRET", "")
    live = (MODE == "live")
    client = PolyClient(api_key_id=api_key, secret_b64=secret, live=live)
    ledger = OpsLedger()
    log(f"START mode={'LIVE' if live else 'SHADOW'} budget=${BUDGET} size={SIZE} "
        f"max_inv={MAX_INV} daily_loss=${DAILY_LOSS} poll={POLL}s")
    log(f"selection: deny={DENY_PREFIXES} min_hours={MIN_HOURS_TO_END} "
        f"mid=[{MIN_MID},{MAX_MID}] require_competed={REQUIRE_COMPETED}")
    if not live:
        log("SHADOW: orders are recorded, NONE reach the exchange. "
            "Flip BOT_MODE=live (operator action) only after validation.")
        log("Leftover LIVE orders cannot be cancelled in shadow — run "
            "PYTHONPATH=. python scripts/poly_cancel_all.py")

    log(f"breaker: max_inv={MAX_INV}/mkt, exposure_cap=${EXPOSURE_CAP:.0f}, "
        f"daily_loss=${DAILY_LOSS:.0f}")
    cache = RewardMarketCache(client)
    tripped = False
    last_earnings = 0.0
    while True:
        try:
            now = datetime.now(timezone.utc).timestamp()
            last_earnings = maybe_poll_earnings(client, ledger, last_earnings)
            # risk check every cycle (reads real positions; shadow stays flat)
            positions = positions_net(client)
            if not tripped:
                trip, reason = breaker_check(client, positions)
                if trip:
                    tripped = True
                    nx = cancel_all_orders(client)
                    ledger.log_event("breaker_trip", reason=reason, cancelled=nx)
                    log(f"*** BREAKER TRIPPED: {reason} -> cancelled {nx} orders, "
                        f"standing aside. Set BOT_MODE and redeploy to resume. ***")
            windows = cache.in_window(now)
            if tripped:
                log("breaker tripped — standing aside (no quotes).")
            else:
                # FULL RECONCILE every cycle: cancel ALL resting orders first, then
                # re-post only the selected set. This prevents order accumulation
                # and clears stale orders on markets that rotated out of reward.
                ncx = cancel_all_orders(client)
                if not windows:
                    log(f"no reward window now — idle (cleared {ncx} stale orders).")
                else:
                    sel = select_markets(client, windows, BUDGET, now, MAX_MARKETS)
                    if not sel:
                        log(f"no markets passed pilot filters "
                            f"({len(windows)} in window; cleared {ncx}).")
                    else:
                        size = max(1.0, min(SIZE, BUDGET / len(sel)))
                        total = 0
                        for slug, period, pool, est, nz, mid, book in sel:
                            n, bb, ba = refresh_quotes(
                                client, slug, positions, size,
                                ledger=ledger, mode=MODE)
                            total += n
                            log(f"  {period:10} {slug[:34]} mid={mid:.3f} "
                                f"est=${est:.2f} book={book:.0f} "
                                f"bid={bb} ask={ba} -> {n}@{size:.0f}"
                                + (" [SHADOW]" if not live else ""))
                        log(f"cycle: cancelled {ncx}, {len(sel)}/{len(windows)} quoted @ "
                            f"size={size:.0f}, {total} orders; "
                            f"shadow_log={len(client.shadow_orders)}")
        except Exception as e:
            log(f"loop error: {e}")
            try:
                ledger.log_event("loop_error", error=str(e))
            except Exception:
                pass
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
