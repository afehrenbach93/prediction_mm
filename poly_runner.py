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
  POLYMARKET_API_KEY / POLYMARKET_SECRET   credentials
"""
import os
import sys
import time
from datetime import datetime, timezone

from core.polyclient import PolyClient, load_env
from core.polymaker import MakerParams, maker_quotes, program_active

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
# Slugs the bot must NOT quote AND must NOT let trip the inventory breaker. Used to
# carve out held legacy/manual positions the bot isn't managing (e.g. a pre-existing
# WC-futures bet) so they neither get quoted nor stand the bot down. Comma-separated.
DENY_SLUGS = {s.strip() for s in os.getenv("POLY_DENY_SLUGS", "").split(",") if s.strip()}
META_REFRESH = 600          # refresh reward-market metadata every 10 min
PARAMS = MakerParams(size=SIZE, max_inventory=MAX_INV)


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
        self.markets: dict[str, dict] = {}   # slug -> {game_start, settle, pool}
        self._last = 0.0

    def refresh(self):
        # group ALL active programs by market (any period — robust to rotation)
        by_slug: dict[str, list] = {}
        for tp in self.c.get_incentives():
            by_slug.setdefault(tp["marketSlug"], []).append(tp)
        out = {}
        for slug, tps in by_slug.items():
            if slug in DENY_SLUGS:        # never quote denied/held-legacy markets
                continue
            mk = self.c.get_market(slug)
            if not mk or mk.get("closed"):
                continue
            out[slug] = {
                "game_start": iso_ts(mk.get("gameStartTime")),
                "settle": iso_ts(mk.get("endDate")),
                "programs": [{"period": tp.get("period"),
                              "pool": float(tp.get("rewardPool") or 0),
                              "start": iso_ts(tp.get("start")),
                              "end": iso_ts(tp.get("end"))} for tp in tps],
            }
        self.markets = out
        self._last = time.time()
        log(f"reward-market meta refreshed: {len(out)} open reward markets")

    def in_window(self, now: float):
        """Markets with an active reward program right now: [(slug, period)]."""
        if time.time() - self._last > META_REFRESH or not self.markets:
            self.refresh()
        live = []
        for slug, m in self.markets.items():
            for pg in m["programs"]:
                if program_active(now, pg["period"], pg["start"], pg["end"],
                                  m["game_start"], m["settle"]):
                    live.append((slug, pg["period"], pg["pool"]))
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
        # `netPosition` is the CONFIRMED live field (raw shape logged 2026-06-20:
        # {'netPosition':'332','qtyBought':'332','qtySold':'0',...}). It MUST come
        # first — without it the breaker read net=0 and did not trip on a
        # 332-contract position that exceeded MAX_INV. The remaining keys are
        # defensive fallbacks for schema drift. If netPosition is absent, derive
        # net from qtyBought-qtySold.
        net = _first_num(p, ("netPosition", "net", "netQuantity",
                             "quantity", "size", "position"))
        if net == 0.0 and ("qtyBought" in p or "qtySold" in p):
            net = _first_num(p, ("qtyBought",)) - _first_num(p, ("qtySold",))
        out[slug] = {
            "net": net,
            # entry/avg-cost field name is NOT yet confirmed from a live position;
            # keep a broad defensive set. The breaker's inventory + exposure caps
            # do not depend on entry, so an unparsed entry only disables the
            # (best-effort) unrealized-loss check, never the hard caps.
            "entry": _first_num(p, ("avgPrice", "averagePrice", "avgEntryPrice",
                                    "avgPriceBought", "costBasis", "entryPrice",
                                    "price")),
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
        if slug in DENY_SLUGS:    # held legacy position the bot isn't managing — ignore
            continue
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


def refresh_quotes(client: PolyClient, slug: str, positions: dict, size: float):
    """Post fresh post-only quotes at the current touch for `slug`, sized to the
    per-market budget allocation. The caller cancels all resting orders first each
    cycle (full reconcile), so this only PLACES — it must not be called without
    that preceding cancel, or orders accumulate."""
    bids, offers = client.get_book(slug)
    best_bid = bids[0][0] if bids else None
    best_ask = offers[0][0] if offers else None
    pos = positions.get(slug, {}).get("net", 0.0)
    params = MakerParams(size=size, max_inventory=MAX_INV)
    quotes = maker_quotes(best_bid, best_ask, pos, params)
    ok = rej = 0
    for intent, price, qty in quotes:
        st, resp = client.place_order(slug, intent, price, qty, post_only=True)
        if st == 200:
            ok += 1
            # DIAGNOSTIC: order is 200-accepted but the account shows 0 resting.
            # Log the create response and read the order back to see its true state
            # (OPEN vs CANCELLED/REJECTED + reason) — `placed_ok` (HTTP 200) is NOT
            # proof of a resting order.
            oid = (resp.get("orderId") or resp.get("id")
                   or (resp.get("order") or {}).get("id")) if isinstance(resp, dict) else None
            log(f"  place OK {slug[:22]} {intent.split('_')[-1]}@{price} resp={str(resp)[:150]}")
            if oid:
                # orders are ASYNC — the create returns an id before the order is
                # queryable/terminal. Poll after a short delay (docs: ~100ms) so the
                # read-back shows the TRUE state (OPEN vs REJECTED/CANCELLED + reason),
                # not a transient 404.
                time.sleep(0.4)
                rs, ro = client.get_order(str(oid))
                log(f"    readback id={oid} st={rs} {str(ro)[:200]}")
        else:
            rej += 1
            # surface WHY a live order bounced (post-only cross / tick / market state)
            # — the response was previously discarded, hiding rejections as phantom
            # "placed" orders that never rested.
            log(f"  place REJECT {slug[:28]} {intent.split('_')[-1]}@{price} "
                f"st={st} {str(resp)[:140]}")
    return ok, rej, best_bid, best_ask


def scan_markets(client: PolyClient, budget: float):
    """READ-ONLY: rank every active reward market by the retail reward share a
    `budget`-sized resting order would capture. Pure public reads (no orders).
    share = my_contracts / (touch_depth + my_contracts); est = share * pool is an
    optimistic per-market ceiling for relative ranking."""
    progs: dict[str, list] = {}
    for tp in client.get_incentives():
        progs.setdefault(tp["marketSlug"], []).append(tp)
    rows = []
    for slug, tps in progs.items():
        mk = client.get_market(slug)
        if not mk or mk.get("closed"):
            continue
        pool = max((float(t.get("rewardPool") or 0) for t in tps), default=0.0)
        period = ",".join(sorted({t.get("period", "") for t in tps}))
        bids, offers = client.get_book(slug)
        bb = bids[0][0] if bids else None
        ba = offers[0][0] if offers else None
        bbq = bids[0][1] if bids else 0.0
        baq = offers[0][1] if offers else 0.0
        spread = round(ba - bb, 4) if (bb and ba) else None
        mycon = (budget / bb) if bb else 0.0           # contracts if we rest at bid
        share = mycon / (bbq + mycon) if bb else 0.0   # our share of the bid touch
        rows.append((share * pool, slug, period, pool, bb, ba, bbq, baq, spread, share))
    rows.sort(reverse=True)
    log(f"=== REWARD SCAN (rest ${budget:.0f} at bid) — {len(rows)} markets, ranked ===")
    for er, slug, period, pool, bb, ba, bbq, baq, spread, share in rows[:18]:
        log(f"  {slug[:40]:40} {period:12} pool=${pool:>7.0f} "
            f"bid={bb}x{bbq:.0f} ask={ba}x{baq:.0f} spr={spread} "
            f"myShare={share*100:>4.1f}% estReward=${er:.2f}")


def main():
    if MODE == "off":
        log("BOT_MODE=off — exiting without quoting.")
        return
    env = load_env() if os.path.exists(".env") else {}
    api_key = os.getenv("POLYMARKET_API_KEY") or env.get("POLYMARKET_API_KEY", "")
    secret = os.getenv("POLYMARKET_SECRET") or env.get("POLYMARKET_SECRET", "")
    live = (MODE == "live")
    client = PolyClient(api_key_id=api_key, secret_b64=secret, live=live)
    if MODE == "scan":
        # READ-ONLY market scan — no orders ever placed. Loops so the ranked table
        # is easy to capture from logs, then refreshes as books move.
        log(f"START mode=SCAN budget=${BUDGET} (read-only reward-market ranking)")
        while True:
            try:
                scan_markets(client, BUDGET)
            except Exception as e:
                log(f"scan error: {e}")
            time.sleep(120)
    log(f"START mode={'LIVE' if live else 'SHADOW'} budget=${BUDGET} size={SIZE} "
        f"max_inv={MAX_INV} daily_loss=${DAILY_LOSS} poll={POLL}s")
    if not live:
        log("SHADOW: orders are recorded, NONE reach the exchange. "
            "Flip BOT_MODE=live (operator action) only after validation.")

    log(f"breaker: max_inv={MAX_INV}/mkt, exposure_cap=${EXPOSURE_CAP:.0f}, "
        f"daily_loss=${DAILY_LOSS:.0f}")
    cache = RewardMarketCache(client)
    tripped = False
    while True:
        try:
            now = datetime.now(timezone.utc).timestamp()
            # risk check every cycle (reads real positions; shadow stays flat)
            positions = positions_net(client)
            if not tripped:
                trip, reason = breaker_check(client, positions)
                if trip:
                    tripped = True
                    nx = cancel_all_orders(client)
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
                    # cap breadth to the highest-pool markets and scale size so total
                    # resting collateral (~$1/contract worst case) stays within budget
                    sel = sorted(windows, key=lambda w: -w[2])[:MAX_MARKETS]
                    size = max(1.0, min(SIZE, BUDGET / len(sel)))
                    placed_ok = placed_rej = 0
                    for slug, period, pool in sel:
                        ok, rej, bb, ba = refresh_quotes(client, slug, positions, size)
                        placed_ok += ok
                        placed_rej += rej
                        log(f"  {period:10} {slug[:38]} bid={bb} ask={ba} -> ok={ok} rej={rej}@{size:.0f}"
                            + (" [SHADOW]" if not live else ""))
                    # `resting` is the authoritative count from the exchange (what the
                    # next cancel will see); placed_ok/rej shows this cycle's placements.
                    log(f"cycle: resting(pre-cancel)={ncx}, {len(sel)}/{len(windows)} mkts, "
                        f"placed_ok={placed_ok} rej={placed_rej} @size={size:.0f}; "
                        f"shadow_log={len(client.shadow_orders)}")
        except Exception as e:
            log(f"loop error: {e}")
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
