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
# Stage 2 selection-first quoting: hard-exclude reward markets whose rolling volatility
# (adverse-selection proxy, $ mid-move/min) exceeds this cap. 0 = no hard exclusion (still
# ranks by reward-rate/vol when vol data is available). Vol comes from the REWARD_YIELD sampler.
VOL_CAP = float(os.getenv("POLY_VOL_CAP", "0"))
# Slugs the bot must NOT quote AND must NOT let trip the inventory breaker. Used to
# carve out held legacy/manual positions the bot isn't managing (e.g. a pre-existing
# WC-futures bet) so they neither get quoted nor stand the bot down. Comma-separated.
DENY_SLUGS = {s.strip() for s in os.getenv("POLY_DENY_SLUGS", "").split(",") if s.strip()}
# Allow-list of lowercase substrings: if set, ONLY reward markets whose slug or any
# program id contains one of these tokens are quoted. The live "Go Live" path uses
# this to stay World-Cup-only (default tokens below). Empty = quote everything.
# Safe failure mode: if nothing matches, the bot just idles (no orders).
ALLOW_TOKENS = {s.strip().lower() for s in
                os.getenv("POLY_ALLOW", "worldcup,fwc,-wc-").split(",") if s.strip()}
# Real orders require BOTH an app "live" request AND this operator-set env arm. Unarmed,
# the live path runs in shadow (records intended orders, $0) — so the button is safe.
LIVE_ARMED = os.getenv("POLY_LIVE_ARMED", "").strip().lower() in ("1", "true", "yes")
META_REFRESH = 600          # refresh reward-market metadata every 10 min
PARAMS = MakerParams(size=SIZE, max_inventory=MAX_INV)
# LIVE weather sell-taker (the settlement-validated edge). off|live; needs LIVE_ARMED too.
WX_TAKER = os.getenv("WX_TAKER", "off").strip().lower()
WX_BUDGET = float(os.getenv("WX_BUDGET", "75"))
MLB_TAKER = os.getenv("MLB_TAKER", "off")          # off | live — MLB game-market probe
MLB_BUDGET = float(os.getenv("MLB_BUDGET", "50"))
MLB_EDGE = float(os.getenv("MLB_EDGE", "0.05"))    # min model-vs-ask edge to bet


LIQ_SPREAD = 0.06        # a weather bucket is "tradeable" only if its book is this tight


def log(msg: str):
    print(f"[poly] {datetime.now(timezone.utc):%H:%M:%S}Z {msg}", flush=True)


def _allowed(slug: str, tps: list) -> bool:
    """True if this reward market matches ALLOW_TOKENS (slug or any program id). Used
    to restrict the live path to World-Cup markets."""
    hay = slug.lower() + " " + " ".join(
        str(tp.get("programId", "")).lower() for tp in tps)
    return any(tok in hay for tok in ALLOW_TOKENS)


def wx_pass(client, recorded_days: set):
    """One weather scan: forecast vs tc-temp book; log edges; record predictions
    once per UTC day. Read-only — places no orders. Used by wxedge + track modes."""
    from core import wxfeed, track
    from lib import weather as wx
    from datetime import date as _date
    today = datetime.now(timezone.utc).date()
    mks = client.get_markets(max_pages=300)
    temps = []
    for m in mks:
        p = wx.parse_temp_slug(m.get("slug", ""))
        if p:
            temps.append((m.get("slug", ""), p))
    log(f"tc-temp markets live: {len(temps)}")
    fc, intraday, rows, taker_buckets = {}, {}, [], []
    for slug, p in temps:
        key = (p["station"], p["date"])
        if key not in fc:
            ff = wxfeed.daily_high_forecast(p["station"], p["date"])
            fc[key] = ff[0] if ff else None
        high = fc[key]
        if high is None:
            continue
        # lead-time sigma: forecast error grows with days-out. same-day ~2°F,
        # +1.5°F/day, capped — stops the model being overconfident on far dates.
        try:
            d_out = max(0, (_date.fromisoformat(p["date"]) - today).days)
        except Exception:
            d_out = 0
        sigma = min(8.0, 2.0 + 1.5 * d_out)
        # INTRADAY conditioning (today only): the daily high can't be below the observed
        # max-so-far, and less daytime remaining -> tighter sigma. Collapses the boundary
        # uncertainty that the forecast-vs-official settlement gap turned into losses.
        floor = None
        if d_out == 0:
            if key not in intraday:
                intraday[key] = wxfeed.intraday_max_so_far(p["station"], p["date"])
            if intraday[key]:
                floor, frac_left = intraday[key]
                sigma = max(1.0, sigma * (frac_left ** 0.5))
        prob = wx.bucket_probability(high, sigma, p["lo"], p["hi"], floor=floor)
        bids, offers = client.get_book(slug)
        bid = bids[0][0] if bids else None
        bid_qty = bids[0][1] if bids else 0
        ask = offers[0][0] if offers else None
        spread = (ask - bid) if (bid is not None and ask is not None) else None
        fee = wx.taker_fee(ask) if ask else 0.0
        edge = wx.buy_edge(prob, ask, fee)
        liquid = spread is not None and spread <= LIQ_SPREAD
        rows.append((edge if edge is not None else -9.0, slug, p,
                     high, sigma, prob, bid, ask, spread, liquid, d_out))
        # share the (slug, prob, bid, bid_qty) with the live taker so it doesn't re-read
        # all ~60 books in the same cycle (that duplicate read was tripping the rate limit).
        taker_buckets.append({"slug": slug, "prob": prob, "bid": bid, "bid_qty": bid_qty})
    rows.sort(reverse=True)
    liq = [r for r in rows if r[9] and r[0] >= 0.05]
    log(f"=== WEATHER EDGES — {len(rows)} buckets; "
        f"*** TRADEABLE (liquid, edge>=5pts): {len(liq)} *** ===")
    for r in rows[:26]:
        edge, slug, p, high, sigma, prob, bid, ask, spread, liquid, d_out = r
        if edge < 0.05:
            continue
        tag = "LIQUID-EDGE" if liquid else "thin(skip)"
        log(f"  [{tag}] edge={edge:+.3f} P={prob:.2f} ask={ask} bid={bid} "
            f"spr={spread} d+{d_out} sig={sigma:.1f} {p['city'][:10]} {p['lo']}-{p['hi']}")
    if not liq:
        log("  no tradeable (liquid) edges this pass.")
    today_iso = today.isoformat()
    if today_iso not in recorded_days:
        payload = []
        for r in rows:
            edge, slug, p, high, sigma, prob, bid, ask, spread, liquid, d_out = r
            payload.append({
                "model": "weather", "sport": "temp", "market_slug": slug,
                "outcome": f"{p['lo']}-{p['hi']}", "model_prob": round(prob, 4),
                "market_bid": bid, "market_ask": ask,
                "edge": round(edge, 4) if edge > -9 else None,
                "liquid": liquid, "settle_date": p["date"], "run_date": today_iso,
                "meta": {"city": p["city"], "forecast_high": high, "sigma": sigma,
                         "days_out": d_out, "spread": spread, "run_date": today_iso},
            })
        st, note = track.record_predictions(payload)
        log(f"tracker: recorded {len(payload)} weather predictions -> http={st} {note}")
        if st in (200, 201):
            recorded_days.add(today_iso)
    return taker_buckets


def _wx_buckets(client):
    """Scan tc-temp markets -> [{slug, prob, bid, bid_qty}] (forecast prob + YES best bid).
    Shared by the read-only edge scan and the live taker."""
    from core import wxfeed
    from lib import weather as wx
    from datetime import date as _date
    today = datetime.now(timezone.utc).date()
    fc, out = {}, []
    for m in client.get_markets(max_pages=300):
        slug = m.get("slug", "")
        p = wx.parse_temp_slug(slug)
        if not p:
            continue
        key = (p["station"], p["date"])
        if key not in fc:
            ff = wxfeed.daily_high_forecast(p["station"], p["date"])
            fc[key] = ff[0] if ff else None
        high = fc[key]
        if high is None:
            continue
        try:
            d_out = max(0, (_date.fromisoformat(p["date"]) - today).days)
        except Exception:
            d_out = 0
        sigma = min(8.0, 2.0 + 1.5 * d_out)
        prob = wx.bucket_probability(high, sigma, p["lo"], p["hi"])
        bids, _ = client.get_book(slug)
        out.append({"slug": slug, "prob": prob,
                    "bid": bids[0][0] if bids else None,
                    "bid_qty": bids[0][1] if bids else 0})
    return out


def wx_taker_cycle(live_client, budget, state, log, buckets=None):
    """LIVE bounded weather sell-taker. Sells (SELL_SHORT @ YES bid, taker) the overpriced
    buckets, held to settlement, capped by `budget` collateral. Probe-first: until a
    confirmed short exists, place only ONE tiny order and verify direction via position
    readback. Halts on wrong-direction or over-exposure. Returns a short status string."""
    from core import wxtaker
    pos = positions_net(live_client)
    tc = {s: v for s, v in pos.items() if s.startswith("tc-temp")}
    ours = state.setdefault("our_slugs", set())   # only slugs WE traded this process
    # direction safety: every short WE OPEN must be net<=0. a LONG = the order did the
    # opposite of intent -> halt. (scope to OUR slugs so a pre-existing wrong-way position
    # from a prior run doesn't block a fresh test on a different bucket.)
    bad = wxtaker.wrong_direction({s: {"netPosition": v["net"]} for s, v in tc.items()},
                                  ours & set(tc))
    if bad:
        state["tripped"] = True
        # surface raw qtyBought/qtySold so we can tell a GENUINE long (bought>sold) from a
        # sign-convention false halt (sold>bought but net reported positive).
        try:
            _, dd = live_client.get_positions()
            rawp = (dd or {}).get("positions", {}) if isinstance(dd, dict) else {}
            detail = {s: {k: rawp.get(s, {}).get(k) for k in
                          ("netPosition", "qtyBought", "qtySold", "avgPrice")} for s in bad}
        except Exception:
            detail = {}
        log(f"WX-TAKER HALT: wrong-direction (LONG) {bad} raw={detail} — standing aside")
        return f"halt: wrong-direction raw={detail}"
    have_short = any(v["net"] < 0 for v in tc.values())
    # read resting tc-temp offers up front (avoid piling up; count toward budget). if
    # rate-limited we're blind to existing orders -> place nothing (never risk a pile-up).
    oo_s, oo_d = live_client.get_open_orders()
    if oo_s == 429:
        log("wx-taker: open-orders 429 (rate-limited) — skipping placement this cycle")
        return "skip: rate-limited"
    olist = oo_d if isinstance(oo_d, list) else (oo_d.get("orders", []) if isinstance(oo_d, dict) else [])
    open_tc = [o for o in olist if "tc-temp" in str(o.get("marketSlug", o))]
    # collateral committed by existing shorts (~0.6/contract when entry unknown)
    used = sum(abs(v["net"]) * (1.0 - (v["entry"] or 0.4)) for v in tc.values())
    if used > budget * 1.25:
        state["tripped"] = True
        log(f"WX-TAKER HALT: collateral ${used:.0f} > 1.25x budget ${budget} — standing aside")
        return f"halt: over-exposed ${used:.0f}"
    # while resting offers are still pending, wait (don't double-place / pile up).
    if open_tc:
        state["probe_fails"] = 0
        log(f"wx-taker: {len(open_tc)} resting offers pending, {len(tc)} positions, "
            f"${used:.0f}/{budget} — waiting for fills")
        return f"resting {len(open_tc)}, {len(tc)}pos"
    # PROBE until the first short confirms direction; then SCALE across fresh overpriced
    # buckets up to the budget (diversified live test at the authorized size).
    probe = not have_short
    if probe:
        state["probe_fails"] = state.get("probe_fails", 0) + 1
        if state["probe_fails"] > 3:
            state["tripped"] = True
            log("WX-TAKER HALT: order never rested after 3 tries — standing aside")
            return "halt: never rested"
    else:
        state["probe_fails"] = 0
    # reuse the books wx_pass just read this cycle (avoid the duplicate ~60-book read that
    # tripped the rate limit); fall back to a fresh read only if not provided.
    if not buckets:
        buckets = _wx_buckets(live_client)
    # skip buckets we already hold so we diversify onto fresh overpriced buckets
    cands = [c for c in wxtaker.sell_candidates(buckets, margin=0.10) if c["slug"] not in tc]
    orders = wxtaker.allocate(cands, budget=budget, used=used,
                              per_bucket=int(os.getenv("WX_PER_BUCKET", "10")), probe=probe)
    if not orders:
        log(f"wx-taker: {len(cands)} overpriced buckets but none fit budget — idle "
            f"(${used:.0f}/{budget} used)")
        return f"idle: full (${used:.0f}/{budget})"
    placed = 0
    for o in orders:
        # SELL_SHORT empirically opened a LONG on this venue, so BUY_SHORT opens the short.
        price = round(o["sell_price"] + 0.01, 2)
        st, resp = live_client.place_order(o["slug"], "ORDER_INTENT_BUY_SHORT",
                                           price, o["qty"], post_only=True)
        ours.add(o["slug"])                        # track for the scoped direction guard
        placed += 1 if st == 200 else 0
        log(f"  wx-taker BUY_SHORT {o['slug'][:34]} {o['qty']}@{price} "
            f"(bid {o['sell_price']}, edge~{o['edge']:+.2f}) -> http={st} resp={str(resp)[:140]}")
    return f"{'PROBE' if probe else 'scale'} placed {placed}/{len(orders)}, ${used:.0f}/{budget}"


def mlb_taker_cycle(live_client, budget, state, log, edge_min=None):
    """LIVE bounded MLB probe: buy the model-cheap side of matched game markets near
    kickoff, at executable book prices (only rows odds_refresh_pass has stamped). Mirrors
    the weather taker's rails: probe-first (one 2-lot until a position confirms direction),
    then scale to `budget` with a per-game cap; halts on wrong-direction / over-exposure /
    never-rests. ALWAYS cancels our resting game orders once kickoff passes (an unfilled
    maker order left in-play is pure adverse selection). Independent risk accounting from
    the cricket farm and the weather taker. Returns a short status string."""
    from core import mlbtaker, track
    today_iso = datetime.now(timezone.utc).date().isoformat()
    now = time.time()
    rows = track.fetch_rows_for_odds("mlb", today_iso)
    ko_by_slug: dict[str, float] = {}
    for r in rows:
        meta = r.get("meta") or {}
        if meta.get("pm_slug"):
            ko_by_slug[meta["pm_slug"]] = mlbtaker._ts(meta.get("kickoff", "")) or None
    # stale-order sweep FIRST (risk-reducing) — runs even when tripped/idle
    oo_s, oo_d = live_client.get_open_orders()
    if oo_s == 429:
        return "skip: rate-limited"
    olist = oo_d if isinstance(oo_d, list) else (oo_d.get("orders", []) if isinstance(oo_d, dict) else [])
    stale = mlbtaker.stale_order_ids(olist, ko_by_slug, now)
    for oid, slug in stale:
        cs, _ = live_client.cancel_order(oid, slug)
        log(f"  mlb-taker cancel stale (in-play) {slug[:32]} id={oid} -> http={cs}")
    if state.get("tripped"):
        return state.get("status", "halt")
    pos = positions_net(live_client)
    mlb_pos = {s: v for s, v in pos.items() if s.startswith("aec-mlb")}
    # EXECUTION LEARNING: once a held game reaches kickoff, stamp the row with the
    # post-fill price move (fill_drift = mid - entry, signed by our direction) — the
    # adverse-selection measure the promotion gate needs to score edge AFTER execution.
    row_by_id = {r.get("id"): r for r in rows}
    for slug, v in mlb_pos.items():
        rid = state.setdefault("row_by_slug", {}).get(slug)
        ko = ko_by_slug.get(slug)
        r = row_by_id.get(rid)
        if not rid or not r or not v.get("net") or (ko and now < ko):
            continue
        meta = r.get("meta") or {}
        if "fill_drift" in meta:
            continue
        try:
            bids, offers = live_client.get_book(slug)
            mid = (float(bids[0][0]) + float(offers[0][0])) / 2 if bids and offers else None
        except Exception:
            mid = None
        if mid is None or not v.get("entry"):
            continue
        sign = 1 if v["net"] > 0 else -1
        drift = round((mid - float(v["entry"])) * sign, 4)   # negative = adverse selection
        meta.update(fill_px=v["entry"], fill_drift=drift)
        from core import track as _t
        if _t.patch_meta(int(rid), meta) in (200, 204):
            log(f"  mlb-taker fill-drift {slug[:32]}: {drift:+.3f} "
                f"(entry {v['entry']}, kickoff mid {mid:.3f})")
    expected = state.setdefault("expected", {})    # slug -> +1 long / -1 short WE opened
    bad = mlbtaker.wrong_direction(
        {s: {"netPosition": v["net"]} for s, v in mlb_pos.items()},
        {s: sg for s, sg in expected.items() if s in mlb_pos})
    if bad:
        state["tripped"] = True
        state["status"] = f"halt: wrong-direction {bad}"
        log(f"MLB-TAKER HALT: wrong-direction {bad} — standing aside")
        return state["status"]
    # collateral committed: long = qty*entry, short = qty*(1-entry) (~0.5 when unknown)
    used = 0.0
    for v in mlb_pos.values():
        e = v["entry"] or 0.5
        used += abs(v["net"]) * (e if v["net"] > 0 else (1 - e))
    if used > budget * 1.25:
        state["tripped"] = True
        state["status"] = f"halt: over-exposed ${used:.0f}"
        log(f"MLB-TAKER HALT: collateral ${used:.0f} > 1.25x budget ${budget}")
        return state["status"]
    open_mlb = [o for o in olist if str(o.get("marketSlug", "")).startswith("aec-mlb")
                and str(o.get("id") or o.get("orderId")) not in {i for i, _ in stale}]
    if open_mlb:
        state["probe_fails"] = 0
        return f"resting {len(open_mlb)}, {len(mlb_pos)}pos, ${used:.0f}/{budget}"
    cands = [c for c in mlbtaker.candidates(rows, now,
                                            edge_min=edge_min if edge_min is not None else MLB_EDGE)
             if c["slug"] not in mlb_pos]
    if not cands:
        return f"idle: no edge near kickoff ({len(mlb_pos)}pos ${used:.0f}/{budget})"
    probe = not any(v["net"] for v in mlb_pos.values())
    if probe:
        state["probe_fails"] = state.get("probe_fails", 0) + 1
        if state["probe_fails"] > 3:
            state["tripped"] = True
            state["status"] = "halt: never rested"
            log("MLB-TAKER HALT: probe order never rested/filled after 3 tries")
            return state["status"]
    else:
        state["probe_fails"] = 0
    per_game = float(os.getenv("MLB_PER_GAME", "10"))
    room = budget - used
    placed = 0
    for c in (cands[:1] if probe else cands[:4]):
        try:
            bids, offers = live_client.get_book(c["slug"])
        except Exception:
            continue
        od = mlbtaker.order_for(c["outcome"], c["side0"],
                                bids[0][0] if bids else None,
                                offers[0][0] if offers else None)
        if not od:
            continue
        intent, px, cpc = od
        qty = 2 if probe else int(min(per_game, room) // max(cpc, 0.01))
        if qty < 1 or qty * cpc > room:
            continue
        st, resp = live_client.place_order(c["slug"], intent, px, qty, post_only=True)
        expected[c["slug"]] = 1 if intent.endswith("BUY_LONG") else -1
        if c.get("row_id"):
            state.setdefault("row_by_slug", {})[c["slug"]] = c["row_id"]
        room -= qty * cpc
        placed += 1 if st == 200 else 0
        log(f"  mlb-taker {intent.replace('ORDER_INTENT_', '')} {c['slug'][:36]} "
            f"{qty}@{px} (edge {c['edge']:+.2f} side0={c['side0']}) "
            f"-> http={st} resp={str(resp)[:120]}")
    return f"{'PROBE' if probe else 'scale'} placed {placed}, ${used:.0f}/{budget}"


def wx_settle_check(client, log):
    """VALIDATION (read-only): the weather edge is only real if OUR settlement (observed
    daily high) matches how Polymarket actually resolves these buckets. For recently-settled
    weather buckets, fetch PM's resolution (resolved outcomePrices) + its rules text, and
    compare to our realized_yes. High agreement => edge is real; divergence => the Kalshi
    settlement-source illusion. Logs agreement + sample mismatches + the resolution rules."""
    import json as _json
    from core import track
    rows = track.fetch_settled("temp", 120)
    if not rows:
        log("wx-settle check: no settled weather rows yet")
        return
    agree = mismatch = unknown = corrected = 0
    samples, ruled = [], False
    for r in rows:
        m = client.get_market(r.get("market_slug", ""))
        if not m:
            continue
        if not ruled:
            log(f"  wx-settle PM RULES: {str(m.get('description') or m.get('question'))[:260]}")
            ruled = True
        try:
            prs = [float(x) for x in _json.loads(m.get("outcomePrices") or "[]")]
        except Exception:
            continue
        if not prs:
            continue
        pm_yes = prs[0]                       # resolved markets go to ~1/0
        pm = 1 if pm_yes > 0.9 else 0 if pm_yes < 0.1 else None
        if pm is None:
            unknown += 1
            continue
        ours = 1 if r.get("realized_yes") else 0
        if pm == ours:
            agree += 1
        else:
            mismatch += 1
            # RE-SETTLE to PM's authoritative outcome (what actually pays) so the edge
            # re-measures against the real settlement, not our raw-obs proxy.
            if r.get("id"):
                if track.set_realized(int(r["id"]), bool(pm)) in (200, 204):
                    corrected += 1
            if len(samples) < 8:
                samples.append(f'{r.get("market_slug")} ours={ours} pm={pm}')
    tot = agree + mismatch
    rate = f"{agree}/{tot} ({100*agree/tot:.0f}%)" if tot else "n/a"
    log(f"wx-settle check: AGREE {rate}, unknown/unresolved={unknown}, "
        f"re-settled {corrected} rows to PM's outcome")
    for s in samples:
        log(f"  wx-settle mismatch: {s}")


def effective_config(ctrl: dict) -> dict:
    """Resolve the live strategy config: app-set poly_control values win, worker env
    is the fallback default (NULL column = env). Pure — unit-tested."""
    def pick(key, env_val, cast=float):
        v = ctrl.get(key)
        if v is None or v == "":
            return env_val
        try:
            return cast(v)
        except (TypeError, ValueError):
            return env_val
    return {
        "wx_on": pick("wx_taker", WX_TAKER, str).lower() == "live",
        "mlb_on": pick("mlb_taker", MLB_TAKER, str).lower() == "live",
        "wx_budget": pick("wx_budget", WX_BUDGET),
        "mlb_budget": pick("mlb_budget", MLB_BUDGET),
        "mlb_edge": pick("mlb_edge", MLB_EDGE),
    }


class TradeAccount:
    """One Polymarket account the SHARED worker trades for (multi-user execution:
    one brain, N venue accounts). The client is REAL only when the worker is armed
    (POLY_LIVE_ARMED) AND the user's own poly_users.armed switch is on; otherwise
    shadow — the user's kill switch disconnects THEIR account from order flow
    without stopping the shared models. All strategy state is per-account."""
    def __init__(self, email: str, name: str):
        self.email, self.name = email, name
        self.client = None
        self.live = False
        self.key = ""
        self.secret = ""
        self.wx_state: dict = {"tripped": False}
        self.mlb_state: dict = {"tripped": False}
        self.live_state: dict = {"tripped": False}
        self.wx_status = ""
        self.mlb_status = ""


def refresh_accounts(accounts: dict, log) -> dict:
    """Sync TradeAccounts with the poly_users table. key_env/secret_env in each row
    name worker env vars holding that user's keys (secrets never touch the DB).
    SELF-SERVE key source (preferred; zero operator steps): pm_key_enc/pm_secret_enc —
    the app sealed the user's Polymarket keys CLIENT-SIDE to the deployment public key
    (ECDH P-256 sealed box); only this worker (POLY_KEYRING_PRIV) can unseal them.
    key_env/secret_env (worker env-var names) remain as the operator-managed fallback.
    Rebuilds a client when keys or armed-state change; on DISARM, cancels that
    account's resting BOT orders (risk-reducing, via a one-shot live client) so
    nothing keeps working their book. Falls back to the base env account only when
    the table has never been readable (single-user back-compat)."""
    from core import track, keyring
    rows = track.fetch_users()
    if not rows and not accounts:
        rows = [{"email": "operator", "name": "operator", "armed": True,
                 "key_env": "POLYMARKET_API_KEY", "secret_env": "POLYMARKET_SECRET"}]
    kr_priv = os.getenv("POLY_KEYRING_PRIV", "")
    unseal_cache = getattr(refresh_accounts, "_unseal_cache", {})
    refresh_accounts._unseal_cache = unseal_cache
    for r in rows:
        email = r.get("email") or "?"
        key = os.getenv(r.get("key_env") or "", "")
        secret = os.getenv(r.get("secret_env") or "", "")
        if (not key or not secret) and kr_priv \
                and r.get("pm_key_enc") and r.get("pm_secret_enc"):
            ck = (email, r["pm_key_enc"][:24], r["pm_secret_enc"][:24])
            if ck not in unseal_cache:
                unseal_cache[ck] = (keyring.unseal(kr_priv, r["pm_key_enc"]),
                                    keyring.unseal(kr_priv, r["pm_secret_enc"]))
                if unseal_cache[ck] == (None, None):
                    log(f"account {email}: sealed keys present but unseal FAILED "
                        f"(wrong deployment keyring?)")
            key = unseal_cache[ck][0] or ""
            secret = unseal_cache[ck][1] or ""
        if not key or not secret:
            continue                    # registered in the app; no usable keys yet
        live = bool(LIVE_ARMED and r.get("armed"))
        acct = accounts.get(email)
        if acct is None:
            acct = accounts[email] = TradeAccount(email, r.get("name") or email)
        if acct.client is None or acct.key != key or acct.live != live:
            was_live = acct.live and acct.client is not None
            acct.key, acct.secret, acct.live = key, secret, live
            acct.client = PolyClient(api_key_id=key, secret_b64=secret, live=live)
            log(f"account {acct.name}: client {'LIVE' if live else 'shadow'}")
            if was_live and not live:
                try:
                    canceller = PolyClient(api_key_id=key, secret_b64=secret, live=True)
                    n = cancel_bot_orders(canceller)
                    log(f"account {acct.name}: DISARMED -> cancelled {n} resting bot orders")
                except Exception as e:
                    log(f"account {acct.name}: disarm-cancel error: {e}")
    return accounts


def primary_account(accounts: dict, base_key: str):
    """The operator's account (matches the base env key), else any one — used for
    the account-level P&L snapshot and back-compat heartbeat fields."""
    for a in accounts.values():
        if a.key == base_key:
            return a
    return next(iter(accounts.values()), None)


def cancel_bot_orders(client: PolyClient, prefixes=("tc-temp", "aec-")) -> int:
    """Cancel resting orders ONLY on the bot's market prefixes — used when a user
    disarms. Never touches manual orders they placed elsewhere on their account."""
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
        slug = o.get("marketSlug", "")
        if not slug.startswith(prefixes):
            continue
        oid = o.get("id") or o.get("orderId")
        if oid and client.cancel_order(oid, slug)[0] == 200:
            n += 1
    return n


def sport_settle_check(client, log, max_reads: int = 60):
    """VALIDATION (read-only): re-settle sports rows against PM's AUTHORITATIVE resolution
    — the generalized wx_settle_check. Our ESPN-based settlement can diverge from what the
    venue actually pays (the weather settlement-source flaw); every Brier/P&L number carries
    that asterisk until rows settle against PM. For settled rows that matched a PM market
    (meta.pm_slug), read the resolved market's outcomePrices, map the winning side via the
    outcomes/team names, compare to our realized_yes, and correct mismatches."""
    from core import track, pmodds
    import json as _json
    for sport in ("mlb", "nba", "nfl", "ncaaf"):
        rows = [r for r in track.fetch_settled(sport, 150)
                if (r.get("meta") or {}).get("pm_slug")]
        if not rows:
            continue
        agree = mismatch = unresolved = corrected = 0
        cache, reads, samples = {}, 0, []
        for r in rows:
            meta = r.get("meta") or {}
            slug = meta["pm_slug"]
            if slug not in cache:
                if reads >= max_reads:
                    continue
                cache[slug] = client.get_market(slug)
                reads += 1
            m = cache[slug]
            if not m:
                continue
            hp, ap = pmodds._outcome_prices(m, meta.get("home", ""), meta.get("away", ""))
            if hp is None or ap is None:
                unresolved += 1
                continue
            home_won = True if (hp > 0.9 and ap < 0.1) else \
                       False if (ap > 0.9 and hp < 0.1) else None
            if home_won is None:                    # market not resolved to ~1/0 yet
                unresolved += 1
                continue
            pm_yes = (r.get("outcome") == "home") == home_won
            if bool(r.get("realized_yes")) == pm_yes:
                agree += 1
            else:
                mismatch += 1
                if r.get("id") and track.set_realized(int(r["id"]), pm_yes) in (200, 204):
                    corrected += 1
                if len(samples) < 6:
                    samples.append(f'{slug} outcome={r.get("outcome")} '
                                   f'ours={bool(r.get("realized_yes"))} pm={pm_yes}')
        tot = agree + mismatch
        rate = f"{agree}/{tot} ({100*agree/tot:.0f}%)" if tot else "n/a"
        log(f"{sport}-settle check: AGREE {rate}, unresolved={unresolved}, "
            f"re-settled {corrected} rows to PM's outcome")
        for s in samples:
            log(f"  {sport}-settle mismatch: {s}")


def odds_refresh_pass(client, log, state, ahead_secs: int = 9000):
    """Near-game EXECUTABLE odds capture. The daily snapshot records outcomePrices at
    ~listing time — often a stale pre-liquidity print (market Brier came out WORSE than a
    coin flip, an anti-informative artifact), so sims against it aren't executable evidence.
    For today's matched games starting within `ahead_secs`, read the market's actual BOOK,
    map it to per-side bid/ask (pmodds.executable_sides), and overwrite the row's market
    fields. One refresh per row (meta.odds_at marks done). Read-only on the exchange."""
    from core import track, pmodds
    today_iso = datetime.now(timezone.utc).date().isoformat()
    now = datetime.now(timezone.utc).timestamp()
    updated = skipped = 0
    blend_rows: list[dict] = []
    for sport in ("mlb", "nba", "nfl", "ncaaf"):
        rows = track.fetch_rows_for_odds(sport, today_iso)
        if not rows:
            continue
        by_slug: dict[str, list] = {}
        for r in rows:
            meta = r.get("meta") or {}
            if meta.get("odds_at"):                 # already refreshed near game time
                continue
            ko = iso_ts(meta.get("kickoff", ""))
            if not ko or not (0 <= ko - now <= ahead_secs):
                continue                            # not near kickoff yet (or started)
            by_slug.setdefault(meta["pm_slug"], []).append(r)
        for slug, srows in by_slug.items():
            m = client.get_market(slug)
            if not m:
                continue
            try:
                bids, offers = client.get_book(slug)
            except Exception:
                continue
            meta0 = srows[0].get("meta") or {}
            quotes, side0, drift = pmodds.executable_sides(
                m, bids, offers, meta0.get("home", ""), meta0.get("away", ""))
            if not quotes:
                skipped += 1
                # DIAG (first few per process): WHY is this market unmappable — empty/
                # one-sided book vs outcomes that don't name the teams.
                if state.get("unmap_diags", 0) < 3:
                    state["unmap_diags"] = state.get("unmap_diags", 0) + 1
                    log(f"  odds-refresh UNMAPPABLE {slug}: outcomes={str(m.get('outcomes'))[:80]} "
                        f"prices={str(m.get('outcomePrices'))[:40]} "
                        f"book_top=bid:{bids[0] if bids else None} ask:{offers[0] if offers else None} "
                        f"home={meta0.get('home','')[:20]} away={meta0.get('away','')[:20]}")
                continue
            if not state.get("odds_probed"):
                log(f"  odds-refresh PROBE {slug}: book_side0={side0} drift={drift} "
                    f"quotes={quotes} vs outcomePrices={m.get('outcomePrices')}")
                state["odds_probed"] = True
            for r in srows:
                q = quotes.get(r.get("outcome") or "")
                if not q or q.get("ask") is None:   # one-sided book: can't price this row yet
                    continue
                edge = (round(float(r["model_prob"]) - q["ask"], 4)
                        if r.get("model_prob") is not None else None)
                meta = r.get("meta") or {}
                meta.update(odds_at=datetime.now(timezone.utc).isoformat()[:19] + "Z",
                            book_side0=side0, book_drift=drift,
                            snap_ask=r.get("market_ask"))   # keep the morning print
                if track.update_market_odds(int(r["id"]), q["bid"], q["ask"],
                                            edge, meta) in (200, 204):
                    updated += 1
                # MARKET-BLEND tracked model: shrink the model toward the executable
                # market price and record it as its own row — protection-against-
                # blind-spots hypothesis, judged by the same gate (never bet directly).
                if r.get("model") == "elo-mlb" and r.get("model_prob") is not None:
                    w = float(os.getenv("BLEND_W", "0.30"))
                    pb = round(w * float(r["model_prob"]) + (1 - w) * q["ask"], 4)
                    blend_rows.append({
                        "model": "blend-mlb", "sport": sport,
                        "market_slug": r.get("market_slug", ""),
                        "outcome": r.get("outcome"), "model_prob": pb,
                        "market_bid": q["bid"], "market_ask": q["ask"],
                        "edge": round(pb - q["ask"], 4), "liquid": True,
                        "settle_date": r.get("settle_date"), "run_date": r.get("run_date"),
                        "meta": dict(meta, w=w, base_model="elo-mlb"),
                    })
    if blend_rows:
        st, note = track.record_predictions(blend_rows)
        log(f"odds-refresh: recorded {len(blend_rows)} blend-mlb rows -> http={st}")
    if updated or skipped:
        log(f"odds-refresh: {updated} rows updated to executable book quotes, "
            f"{skipped} markets unmappable")


def soccer_pass(client, recorded_days: set):
    """One soccer pass: seed Elo from recent results, predict upcoming fixtures,
    record 1X2 probabilities once per UTC day. Read-only. wxedge analogue for soccer."""
    from core import soccerfeed as sf, track
    from lib import soccer as sc
    from datetime import timedelta
    leagues = [s.strip() for s in os.getenv("SOCCER_LEAGUES", "wc,epl,mls").split(",") if s.strip()]
    seed_days = int(os.getenv("SOCCER_SEED_DAYS", "120"))   # history window for Elo
    ahead_days = int(os.getenv("SOCCER_AHEAD_DAYS", "3"))   # fixtures to predict
    today = datetime.now(timezone.utc).date()
    today_iso = today.isoformat()
    window = f"{(today - timedelta(days=seed_days)):%Y%m%d}-{today:%Y%m%d}"
    fut = f"{today:%Y%m%d}-{(today + timedelta(days=ahead_days)):%Y%m%d}"
    from core import pmodds
    payload = []
    all_fixtures = []
    for lg in leagues:
        model = sc.EloTable()
        results = sf.recent_results(lg, window)
        for m in results:
            model.observe(m["home"], m["away"], m["home_score"], m["away_score"])
        fixtures = sf.upcoming_fixtures(lg, fut)
        log(f"  {lg}: seeded {len(results)} results, {len(fixtures)} upcoming fixtures")
        all_fixtures += fixtures
        for fx in fixtures:
            ph, pd, pa = model.probabilities(fx["home"], fx["away"])
            sdate = (fx["date"] or "")[:10] or today_iso
            for outcome, prob in (("home", ph), ("draw", pd), ("away", pa)):
                payload.append({
                    "model": "soccer-elo", "sport": "soccer",
                    "market_slug": f"espn:{lg}:{fx['id']}:{outcome}",
                    "outcome": outcome, "model_prob": round(prob, 4),
                    "market_bid": None, "market_ask": None, "edge": None,
                    "liquid": None, "settle_date": sdate, "run_date": today_iso,
                    "meta": {"league": lg, "espn_id": fx["id"], "home": fx["home_raw"],
                             "away": fx["away_raw"], "r_home": round(model.rating(fx["home"]), 1),
                             "r_away": round(model.rating(fx["away"]), 1),
                             "kickoff": fx["date"], "run_date": today_iso},
                })
    # attach the internally-consistent 1X2 market (home/draw/away YES prices that sum ~1.0)
    # and compare the model's 1X2 prob to the market price directly. edge = model - market.
    odds = pmodds.attach_soccer_odds(client, all_fixtures, log)
    for row in payload:
        o = odds.get(str(row["meta"].get("espn_id", "")))
        if not o:
            continue
        implied = o.get(f"{row['outcome']}_price")
        row["market_ask"] = implied
        row["liquid"] = implied is not None
        if implied is not None and row.get("model_prob") is not None:
            row["edge"] = round(row["model_prob"] - implied, 4)
        row["meta"].update(pm_slug=o["slug"], pm_alts=o["alts"], pm_psum=o.get("psum"))
    if payload and today_iso not in recorded_days:
        st, note = track.record_predictions(payload)
        log(f"tracker: recorded {len(payload)} soccer predictions -> http={st} {note}")
        if st in (200, 201):
            recorded_days.add(today_iso)
    elif not payload:
        log("  no upcoming fixtures to predict this pass.")


def sports_pass(client, recorded_days: set):
    """One pass over all configured head-to-head sports (NBA/NFL/NCAAF/MLB/tennis):
    seed Elo from recent ESPN results, predict upcoming fixtures, record once per UTC
    day. Read-only. Golf is excluded (field/winner model, separate)."""
    from core import espnfeed, sportstrack, track, pmodds
    from datetime import timedelta
    enabled = [s.strip() for s in os.getenv(
        "SPORTS", "nba,nfl,ncaaf,mlb,atp,wta").split(",") if s.strip()]
    seed_days = int(os.getenv("SPORTS_SEED_DAYS", "120"))
    ahead_days = int(os.getenv("SPORTS_AHEAD_DAYS", "3"))
    today = datetime.now(timezone.utc).date()
    today_iso = today.isoformat()
    # one-time PM-catalog sample per process — learn the real sports slug format so we
    # can attach market odds to predictions (model-vs-market edge). Runs before the gate.
    if "pmdiag" not in recorded_days:
        pmodds.sports_market_sample(client, log)
        recorded_days.add("pmdiag")
    if today_iso in recorded_days:
        return
    window = f"{(today - timedelta(days=seed_days)):%Y%m%d}-{today:%Y%m%d}"
    fut = f"{today:%Y%m%d}-{(today + timedelta(days=ahead_days)):%Y%m%d}"
    payload = []
    all_fixtures = []
    for key in enabled:
        cfg = sportstrack.SPORTS.get(key)
        if not cfg:
            log(f"  {key}: unknown sport, skipping")
            continue
        path, neutral = cfg
        # tennis history isn't returned for a wide range — seed it week-by-week.
        if key in ("atp", "wta"):
            recent = espnfeed.results_over(path, (today - timedelta(days=seed_days)).isoformat(),
                                           today.isoformat(), step_days=7)
            if not recent:
                # DIAG: ESPN tennis returns 0 completed even chunked — log what it
                # actually returns (state histogram) so the shape can be fixed from logs.
                from collections import Counter
                probe = espnfeed.fetch(path, f"{(today - timedelta(days=7)):%Y%m%d}-{today:%Y%m%d}")
                states = Counter(m["state"] for m in probe)
                log(f"  {key} DIAG: {len(probe)} raw matches, states={dict(states)}, "
                    f"completed={sum(1 for m in probe if m['completed'])}")
        else:
            recent = espnfeed.recent_results(path, window)
        fixtures = espnfeed.upcoming_fixtures(path, fut)
        log(f"  {key}: seeded {len(recent)} results, {len(fixtures)} upcoming fixtures")
        # tennis went dark when Wimbledon started — when a slam is on but 0 fixtures
        # parse, log the raw structure once/day so the nesting can be fixed from logs.
        if key in ("atp", "wta") and not fixtures and f"tdiag-{today_iso}" not in recorded_days:
            log(f"  {key} SHAPE: {espnfeed.raw_shape(path, fut)}")
            recorded_days.add(f"tdiag-{today_iso}")
        all_fixtures += fixtures
        payload += sportstrack.build_sport_rows(key, path, neutral, recent, fixtures, today_iso)
    # attach live PM market odds to each game so we can measure model-vs-MARKET edge
    # (not just calibration). Best-effort + richly logged so the PM moneyline structure
    # can be confirmed from logs; edge stays null until that's pinned down.
    odds = pmodds.attach_market_odds(client, all_fixtures, log)
    for row in payload:
        o = odds.get(str(row["meta"].get("espn_id", "")))
        if not o:
            continue
        # market-implied prob for THIS row's outcome (home/away), then edge = model - market
        implied = o["home_price"] if row["outcome"] == "home" else o["away_price"]
        row["market_ask"] = implied
        row["liquid"] = implied is not None
        if implied is not None and row.get("model_prob") is not None:
            row["edge"] = round(row["model_prob"] - implied, 4)
        row["meta"].update(pm_slug=o["slug"], pm_alts=o["alts"],
                           pm_home_price=o["home_price"], pm_away_price=o["away_price"])
    if payload:
        st, note = track.record_predictions(payload)
        log(f"tracker: recorded {len(payload)} sports predictions -> http={st} {note}")
        if st in (200, 201):
            recorded_days.add(today_iso)
    else:
        log("  no upcoming fixtures across enabled sports this pass.")


def golf_pass(recorded_days: set):
    """Seed player skill from recent tournaments, predict P(win) for the field of the
    current/next tournament, record the top contenders once per UTC day. Read-only."""
    from core import golffeed, track
    from lib import golf
    from datetime import timedelta
    if not os.getenv("GOLF", "pga"):
        return
    tour = os.getenv("GOLF_TOUR", "golf/pga")
    seed_days = int(os.getenv("GOLF_SEED_DAYS", "120"))
    top_n = int(os.getenv("GOLF_TOP_N", "50"))   # record the most-probable contenders
    today = datetime.now(timezone.utc).date()
    today_iso = today.isoformat()
    if today_iso in recorded_days:
        return
    window = f"{(today - timedelta(days=seed_days)):%Y%m%d}-{today:%Y%m%d}"
    model = golf.SkillTable().seed(golffeed.recent_events(tour, window))
    fut = f"{today:%Y%m%d}-{(today + timedelta(days=7)):%Y%m%d}"
    ev = golffeed.current_event(tour, fut) or golffeed.current_event(tour)
    if not ev:
        log("  golf: no current/upcoming tournament.")
        return
    field = [p["player"] for p in ev["field"]]
    raw = {p["player"]: p["player_raw"] for p in ev["field"]}
    probs = model.win_probs(field)
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    # settle on the tournament END (winner only known then). Prefer ESPN endDate; else
    # start + 4 days (a typical Thu–Sun event) so settlement fires after it finishes.
    sdate = (ev.get("end_date") or "")[:10]
    if not sdate:
        from datetime import timedelta as _td
        try:
            sdate = ((datetime.fromisoformat((ev["date"] or "").replace("Z", "+00:00")).date())
                     + _td(days=4)).isoformat()
        except Exception:
            sdate = today_iso
    payload = [{
        "model": "golf-skill", "sport": "golf",
        "market_slug": f"espn:golf:{ev['id']}:{player}",
        "outcome": "win", "model_prob": round(prob, 4),
        "market_bid": None, "market_ask": None, "edge": None, "liquid": None,
        "settle_date": sdate, "run_date": today_iso,
        "meta": {"sport": "golf", "tourney_id": ev["id"], "tournament": ev["name"],
                 "player": player, "player_name": raw.get(player, player),
                 "skill": round(model.skill(player), 3), "run_date": today_iso},
    } for player, prob in ranked]
    log(f"  golf: {ev['name'][:40]} — field {len(field)}, recorded top {len(payload)}")
    if payload:
        st, note = track.record_predictions(payload)
        log(f"tracker: recorded {len(payload)} golf predictions -> http={st} {note}")
        if st in (200, 201):
            recorded_days.add(today_iso)


def settle_pass():
    """Resolve predictions whose settle_date has passed against realized outcomes
    (weather: observed daily high; soccer: ESPN final), writing realized_yes + pnl
    back to the tracker. Read/scoring only — touches no exchange. Used by settle +
    track modes; safe to run repeatedly (only unsettled past-date rows are touched)."""
    from core import track, settle, soccerfeed, wxfeed, espnfeed, golffeed
    today_iso = datetime.now(timezone.utc).date().isoformat()
    rows = track.fetch_unsettled(today_iso)
    if not rows:
        log("settle: nothing due.")
        return
    wx_rows = [r for r in rows if r["model"] == "weather"]
    sc_rows = [r for r in rows if r["model"] == "soccer-elo"]
    sport_rows = [r for r in rows if (r["model"] or "").startswith(("elo-", "blend-"))]
    golf_rows = [r for r in rows if r["model"] == "golf-skill"]
    resolved = {}
    resolved.update(settle.settle_weather(wx_rows, wxfeed.daily_high_observed))
    resolved.update(settle.settle_soccer(sc_rows, soccerfeed.finals_map))
    resolved.update(settle.settle_sport(sport_rows, espnfeed.finals_map))
    resolved.update(settle.settle_golf(golf_rows, lambda d: golffeed.winners_map(dates=d)))
    ok = 0
    for pid, (ry, pnl) in resolved.items():
        if track.mark_settled(pid, ry, pnl) in (200, 204):
            ok += 1
    log(f"settle: due={len(rows)} (wx={len(wx_rows)} soc={len(sc_rows)} "
        f"sport={len(sport_rows)} golf={len(golf_rows)}) resolved={len(resolved)} written={ok}")
    # DIAG: golf/tennis have settled 0 for a week. When rows are due but none resolve,
    # log the ID mismatch — what ESPN returns as completed in the settle window vs the
    # tourney_id/espn_id we're trying to match (this sandbox is geo-blocked from ESPN, so
    # the fix comes from the worker's own logs). Bounded, once per pass.
    golf_due = [r for r in golf_rows if r["id"] not in resolved]
    if golf_due:
        r = golf_due[0]
        d = settle._golf_window(r.get("settle_date") or today_iso)
        try:
            wm = golffeed.winners_map(dates=d)
            raw = golffeed.fetch(dates=d)
            log(f"golf-settle DIAG: window={d} want tourney_id={ (r.get('meta') or {}).get('tourney_id')} "
                f"| ESPN completed winners={dict(list(wm.items())[:6])} "
                f"| events={[(t['id'], t['name'][:18], t['state'], t['completed']) for t in raw[:6]]}")
            if not wm:                       # winner not surfacing — dump the raw shape
                log(f"golf-settle SHAPE: {golffeed.debug_shape(dates=d)}")
        except Exception as e:
            log(f"golf-settle DIAG err={str(e)[:80]}")
    tennis_due = [r for r in sport_rows if r["id"] not in resolved
                  and (r.get("meta") or {}).get("espn_path", "").startswith("tennis")]
    if tennis_due:
        r = tennis_due[0]
        meta = r.get("meta") or {}
        try:
            fm = espnfeed.finals_map(meta["espn_path"], settle._window(r.get("settle_date") or today_iso))
            log(f"tennis-settle DIAG: want espn_id={meta.get('espn_id')} home={meta.get('home')} "
                f"| ESPN completed ids (window)={list(fm.keys())[:10]}")
        except Exception as e:
            log(f"tennis-settle DIAG err={str(e)[:80]}")


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
        # DIAG: what reward markets exist BEFORE the allow-filter — distinguishes
        # "no WC rewards right now" from "filter too narrow / slug schema changed".
        from collections import Counter as _C
        pref = _C("-".join(s.split("-")[:2]) for s in by_slug)
        progs = _C(str(tp.get("programId")) for tps in by_slug.values() for tp in tps)
        wc = [s for s in by_slug if any(k in s for k in ("fwc", "fifa", "-wc-", "worldcup", "soccer"))]
        log(f"reward markets pre-filter: {len(by_slug)} | allow={sorted(ALLOW_TOKENS)} | "
            f"prefixes={pref.most_common(12)} | programs={progs.most_common(8)} | "
            f"wc_markets={wc[:10]}")
        out = {}
        for slug, tps in by_slug.items():
            if slug in DENY_SLUGS:        # never quote denied/held-legacy markets
                continue
            if ALLOW_TOKENS and not _allowed(slug, tps):   # WC-only (or other) gate
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


def cancel_all_orders(client: PolyClient, exclude_prefixes=("tc-temp", "aec-mlb")) -> int:
    """Cancel our resting orders (best-effort) — used by the cricket reconcile + breaker.
    EXCLUDES tc-temp (weather sell-taker) and aec-mlb (MLB probe) orders by default so the
    cricket farm's cancel-all reconcile doesn't nuke the separately-managed strategy orders
    every cycle. Pass exclude_prefixes=() to cancel literally everything."""
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
        slug = o.get("marketSlug", "")
        if any(slug.startswith(p) for p in exclude_prefixes):
            continue
        oid = o.get("id") or o.get("orderId")
        if oid:
            # the cancel body REQUIRES the order's marketSlug (else 400)
            cs, _ = client.cancel_order(oid, slug)
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
        # ignore held-legacy (deny) positions AND the weather/MLB takers' positions,
        # which have their OWN budgets/breakers — otherwise their exposure trips the
        # cricket farm (each strategy must be accounted independently).
        if slug in DENY_SLUGS or slug.startswith(("tc-temp", "aec-mlb")):
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


def live_cycle(client: PolyClient, cache: "RewardMarketCache", state: dict,
               budget: float, live: bool, vol_by_slug: dict | None = None) -> dict:
    """One reward-maker iteration: breaker check, full reconcile (cancel-all then
    re-post), quote the in-window reward markets (WC-only via ALLOW_TOKENS), bounded by
    `budget`. `state['tripped']` persists across cycles. Returns a status dict for the
    heartbeat. Shared by the legacy BOT_MODE=live loop and the app-driven live path."""
    now = datetime.now(timezone.utc).timestamp()
    positions = positions_net(client)           # reads real positions; shadow stays flat
    if not state.get("tripped"):
        trip, reason = breaker_check(client, positions)
        if trip:
            state["tripped"] = True
            nx = cancel_all_orders(client)
            log(f"*** BREAKER TRIPPED: {reason} -> cancelled {nx} orders, standing aside. ***")
    windows = cache.in_window(now)
    if state.get("tripped"):
        log("breaker tripped — standing aside (no quotes).")
        return {"status": "tripped"}
    ncx = cancel_all_orders(client)             # full reconcile: cancel ALL first
    if not windows:
        log(f"no reward window now — idle (cleared {ncx} stale orders).")
        return {"status": "idle", "markets": 0}
    # Stage 2: prefer fat, low-volatility pools (low adverse selection); falls back to
    # top-by-pool exactly when no vol data is available, so this never regresses.
    from core import rewardyield as _ry
    sel = _ry.select_reward_markets(windows, vol_by_slug, max_markets=MAX_MARKETS,
                                    vol_cap=VOL_CAP)
    if not sel:
        log(f"no market cleared selection (vol_cap={VOL_CAP}) — idle "
            f"(cleared {ncx} stale orders).")
        return {"status": "idle", "markets": 0}
    size = max(1.0, min(SIZE, budget / len(sel)))
    placed_ok = placed_rej = 0
    for slug, period, pool in sel:
        ok, rej, bb, ba = refresh_quotes(client, slug, positions, size)
        placed_ok += ok
        placed_rej += rej
        log(f"  {period:10} {slug[:38]} bid={bb} ask={ba} -> ok={ok} rej={rej}@{size:.0f}"
            + (" [SHADOW]" if not live else ""))
    log(f"cycle: resting(pre-cancel)={ncx}, {len(sel)}/{len(windows)} mkts, "
        f"placed_ok={placed_ok} rej={placed_rej} @size={size:.0f}"
        + ("" if live else " [SHADOW]"))
    return {"status": "quoting", "markets": len(sel), "placed_ok": placed_ok,
            "rej": placed_rej, "size": size}


def pnl_snapshot(client: PolyClient) -> dict:
    """Read-only account snapshot — positions (realized P&L per market) + balance — so
    the live P&L source is visible in logs/heartbeat. /v1/portfolio/positions returns
    a dict keyed by slug, each with realized.value + netPosition. Returns a summary
    dict. Never raises."""
    summ = {}
    try:
        sp, pos = client.get_positions()
        positions = pos.get("positions") if isinstance(pos, dict) else {}
        positions = positions if isinstance(positions, dict) else {}
        total_real = 0.0
        net_contracts = 0.0
        per = []
        for slug, r in positions.items():
            if not isinstance(r, dict):
                continue
            try:
                rv = float((r.get("realized") or {}).get("value", 0) or 0)
            except Exception:
                rv = 0.0
            try:
                np_ = float(r.get("netPosition", 0) or 0)
            except Exception:
                np_ = 0.0
            total_real += rv
            net_contracts += abs(np_)
            per.append((round(rv, 2), int(np_), slug[:34]))
        per.sort()
        log(f"PNL SUMMARY: markets={len(positions)} total_realized={total_real:+.2f} "
            f"open_contracts={net_contracts:.0f}")
        if per:
            log(f"PNL worst: {per[:3]}")
            log(f"PNL best:  {per[-3:]}")
        # correct endpoints (from the official SDK): balance + activity history. The
        # positions snapshot drops settled markets, so the realized $ gain lives in the
        # balance + shows up in activities (TRADE / SETTLEMENT / REWARD / REBATE).
        balance = buying_power = None
        try:
            sb, bal = client.signed_get("/v1/account/balances")
            log(f"PNL balance http={sb} {str(bal)[:300]}")
            b0 = ((bal or {}).get("balances") or [{}])[0] if isinstance(bal, dict) else {}
            balance = float(b0.get("currentBalance")) if b0.get("currentBalance") is not None else None
            buying_power = float(b0.get("buyingPower")) if b0.get("buyingPower") is not None else None
        except Exception as e:
            log(f"PNL balance err={str(e)[:80]}")
        try:
            sa, act = client.signed_get("/v1/portfolio/activities")
            items = act.get("activities") if isinstance(act, dict) else act
            items = items if isinstance(items, list) else []
            by_type = {}
            for a in items[:300]:
                if not isinstance(a, dict):
                    continue
                t = a.get("type") or a.get("activityType") or "?"
                amt = a.get("amount")
                if isinstance(amt, dict):
                    amt = amt.get("value")
                try:
                    by_type[t] = round(by_type.get(t, 0.0) + float(amt or 0), 2)
                except Exception:
                    by_type[t] = by_type.get(t, 0.0)
            log(f"PNL activities http={sa} count={len(items)} by_type={by_type}")
            log(f"PNL activities sample={str(items[:4])[:520]}")
        except Exception as e:
            log(f"PNL activities err={str(e)[:80]}")
        summ = {"markets": len(positions), "realized_pnl": round(total_real, 2),
                "open_contracts": round(net_contracts)}
        if balance is not None:
            summ["balance"] = round(balance, 2)
        if buying_power is not None:
            summ["buying_power"] = round(buying_power, 2)
    except Exception as e:
        log(f"pnl snapshot error: {e}")
    return summ


def _wx_clean_struct(obj, _depth=0):
    """Recursively strip large/base64 blobs (icon/image/logo) and truncate long strings so a
    resolution struct can be logged in full without the marketMetadata icon swallowing it."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("icon", "image", "logo", "imageUrl", "iconUrl", "resolvedIcon"):
                out[k] = "-"
            else:
                out[k] = _wx_clean_struct(v, _depth + 1)
        return out
    if isinstance(obj, list):
        return [_wx_clean_struct(v, _depth + 1) for v in obj[:8]]
    if isinstance(obj, str) and len(obj) > 60:
        return obj[:57] + "..."
    return obj


def _wx_money(cand):
    """Coerce a money-ish field to float dollars. Accepts {'value': '1.23'} / {'value': 123}
    or a bare number/string. Returns None if it can't."""
    if isinstance(cand, dict):
        cand = cand.get("value")
    try:
        return float(cand)
    except (TypeError, ValueError):
        return None


def _wx_find_realized(pr, ap, bp):
    """Extract the authoritative settled realized dollars from a positionResolution.
    VENUE SEMANTICS (from the live STRUCT log): `realized` is CUMULATIVE per position —
    beforePosition.realized prints 0.0000 pre-resolution, so the settled figure is the
    after−before DELTA, and a flat-zero candidate means 'nothing realized yet', NOT the
    settled amount (latching bp's zero flattened all 8 rows to $0.00). Returns
    (value, source) or (None, '')."""
    a, b = _wx_money(ap.get("realized")), _wx_money(bp.get("realized"))
    if a is not None and b is not None and abs(a - b) > 1e-9:
        return round(a - b, 4), "ap-bp.realized"
    candidates = [
        ("pr.realized", pr.get("realized")),
        ("pr.realizedPnl", pr.get("realizedPnl")),
        ("pr.pnl", pr.get("pnl")),
        ("ap.realized", ap.get("realized")),
    ]
    for src, cand in candidates:
        v = _wx_money(cand)
        if v is not None and abs(v) > 1e-9:    # zero = not-yet-realized, keep looking
            return v, src
    return None, ""


def _settlement_pnl(client: PolyClient, log, state, prefix, label: str) -> dict:
    """Account-level realized P&L for one strategy, isolated by market-slug `prefix`
    (the shared balance mixes strategies + open marks). Prefers the resolution's
    AUTHORITATIVE settled `realized` (after−before delta); falls back to computing from
    the venue's own cost + the resolved outcome. Logs the cleaned resolution structure
    ONCE. Returns {settled_pnl, settled_n, settled_auth, settled_est}. Never raises."""
    import json as _json
    try:
        _, act = client.signed_get("/v1/portfolio/activities")
    except Exception as e:
        log(f"{label}-pnl activities err={str(e)[:70]}")
        return {}
    items = act.get("activities") if isinstance(act, dict) else act
    items = items if isinstance(items, list) else []
    seen, rows, total = set(), [], 0.0
    struct_logged = state.get(f"{label}_pnl_logged", False)
    n_auth = n_est = 0
    outcome_cache = {}
    for a in items:
        if not isinstance(a, dict) or (a.get("type") or "") != "ACTIVITY_TYPE_POSITION_RESOLUTION":
            continue
        pr = a.get("positionResolution") or {}
        slug = pr.get("marketSlug", "")
        if not slug.startswith(prefix):
            continue
        bp = pr.get("beforePosition") or {}
        ap = pr.get("afterPosition") or {}
        key = (slug, bp.get("updateTime", ""))
        if key in seen:
            continue
        seen.add(key)
        if not struct_logged:
            # Render truncates log messages ~930 chars — split before/after onto their
            # own lines so afterPosition (where the realized delta lives) is visible.
            log(f"{label}-pnl STRUCT.pr: {_json.dumps(_wx_clean_struct({k: v for k, v in pr.items() if k not in ('beforePosition', 'afterPosition')}))[:800]}")
            log(f"{label}-pnl STRUCT.before: {_json.dumps(_wx_clean_struct(bp))[:800]}")
            log(f"{label}-pnl STRUCT.after: {_json.dumps(_wx_clean_struct(ap))[:800]}")
            struct_logged = True
        # authoritative settled realized, if the resolution carries it (after−before delta)
        realized, src = _wx_find_realized(pr, ap, bp)
        est = False
        if realized is None:
            # compute from the venue's own numbers: bp.cost is CONFIRMED collateral, and
            # the resolved market's outcomePrices are the authoritative outcome — both
            # venue-sourced, so this path is authoritative-computed, not estimated.
            try:
                net = float(bp.get("qtyBought", 0)) - float(bp.get("qtySold", 0))
                cost = float((bp.get("cost") or {}).get("value", 0))
            except Exception:
                continue
            if net == 0 or cost <= 0:
                est = True                        # cost unknown -> anything we compute is a guess
                cost = abs(net) * 0.5
            if slug not in outcome_cache:
                m = client.get_market(slug)
                try:
                    prs = [float(x) for x in _json.loads((m or {}).get("outcomePrices") or "[]")]
                    outcome_cache[slug] = (prs[0] > 0.9) if prs else None
                except Exception:
                    outcome_cache[slug] = None
            yes = outcome_cache[slug]
            if yes is None or net == 0:
                continue
            won = (net < 0 and not yes) or (net > 0 and yes)     # short wins on NO
            realized = (abs(net) - cost) if won else -cost
            src = "calc(cost+outcome)"
        total += realized
        n_est += int(est)
        n_auth += int(not est)
        rows.append((round(realized, 2), ("~" if est else " ") + slug[-30:], src))
    state[f"{label}_pnl_logged"] = struct_logged
    rows.sort()
    tag = f"{n_auth} authoritative + {n_est} estimated" if n_est else f"{n_auth} authoritative"
    log(f"{label}-pnl: {len(rows)} settled {label} positions ({tag}), total realized ${total:+.2f}")
    for r in rows[:12]:
        log(f"  {label}-pnl {r}")
    return {"settled_pnl": round(total, 2), "settled_n": len(rows),
            "settled_auth": n_auth, "settled_est": n_est}


def wx_settlement_pnl(client: PolyClient, log, state) -> dict:
    """Weather-only settled P&L (tc-temp positions)."""
    r = _settlement_pnl(client, log, state, "tc-temp", "wx")
    return {"wx_settled_pnl": r.get("settled_pnl"), "wx_settled_n": r.get("settled_n"),
            "wx_settled_auth": r.get("settled_auth"), "wx_settled_est": r.get("settled_est")} if r else {}


def mlb_settlement_pnl(client: PolyClient, log, state) -> dict:
    """MLB-probe-only settled P&L (aec-mlb positions)."""
    r = _settlement_pnl(client, log, state, "aec-mlb", "mlb")
    return {"mlb_settled_pnl": r.get("settled_pnl"), "mlb_settled_n": r.get("settled_n")} if r else {}


def _clob_book(token_id):
    """(best_ask, best_bid) for a Polymarket CLOB token, else (None, None). The gamma
    /events payload has no live prices — the real book lives on the CLOB keyed by
    clobTokenIds. best_ask = lowest ask (what we'd pay to BUY), best_bid = highest bid."""
    import json as _json
    import urllib.request as _u
    try:
        with _u.urlopen(_u.Request(f"https://clob.polymarket.com/book?token_id={token_id}",
                                   headers={"User-Agent": "prediction-mm/shadow"}),
                        timeout=8) as r:
            d = _json.loads(r.read())
        asks = d.get("asks") or []
        bids = d.get("bids") or []
        a = min((float(x["price"]) for x in asks), default=None)
        b = max((float(x["price"]) for x in bids), default=None)
        return a, b
    except Exception:
        return None, None


def _updown_prices(m):
    """(up_ask, up_bid) for a Polymarket Up/Down (or Yes/No) market object, else (None,None).
    Uses bestAsk/bestBid when present, else the outcomePrices mid for the Up/Yes side."""
    import json as _json
    try:
        outs = [str(x).strip().lower() for x in _json.loads(m.get("outcomes") or "[]")]
        prs = [float(x) for x in _json.loads(m.get("outcomePrices") or "[]")]
    except Exception:
        outs, prs = [], []
    idx = next((i for i, o in enumerate(outs) if o in ("up", "yes")), None)
    mid = prs[idx] if (idx is not None and idx < len(prs)) else None
    try:
        ba = float(m.get("bestAsk")) if m.get("bestAsk") is not None else None
        bb = float(m.get("bestBid")) if m.get("bestBid") is not None else None
    except Exception:
        ba = bb = None
    # bestAsk/bestBid on the market are for outcome[0]; align to Up/Yes if it's outcome[0]
    if idx == 0 and (ba is not None or bb is not None):
        return ba if ba is not None else mid, bb if bb is not None else mid
    return mid, mid


def crypto_shadow(log, state):
    """LIVE-DATA PAPER measurement of the crypto Up/Down 'late-snipe' edge on Polymarket's
    5-minute markets. READ-ONLY, NO orders, NO venue account — pulls the public event book
    + Coinbase spot, snapshots each market's reference spot at open, and in the final 60s
    'buys' (on paper) the side that spot currently favors if its ask still leaves margin
    (<=0.92), then settles vs spot at resolution. Answers the only question that matters
    before anything else: does this edge survive our polling latency? Paper rows live in
    model_predictions as model='crypto-updown-shadow' so the app shows them automatically."""
    import json as _json
    import urllib.request as _u
    from datetime import date as _date
    from core import track
    def _get(url):
        try:
            with _u.urlopen(_u.Request(url, headers={"User-Agent": "prediction-mm/shadow"}),
                            timeout=10) as r:
                return _json.loads(r.read())
        except Exception:
            return None
    def _iso_epoch(s):                    # ISO endDate -> epoch seconds (authoritative close)
        try:
            dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
        except Exception:
            return None
    spot = {}
    for sym, pid in (("btc", "BTC-USD"), ("eth", "ETH-USD")):
        d = _get(f"https://api.coinbase.com/v2/prices/{pid}/spot")
        try:
            spot[sym] = float(d["data"]["amount"])
        except Exception:
            pass
    if not spot:
        log("crypto-shadow: spot feed unavailable this cycle")
        return
    # The 5-minute updown markets resolve minutes from now, so their endDate is just ABOVE
    # `now`. An unfiltered ascending-by-endDate sort returns ancient never-closed zombie
    # markets first (endDate months in the past) and buries the live ones past limit=100 —
    # so filter to endDate >= now and take the soonest-resolving future markets.
    now = datetime.now(timezone.utc).timestamp()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    evs = _get("https://gamma-api.polymarket.com/events?closed=false"
               f"&end_date_min={now_iso}&order=endDate&ascending=true&limit=100") or []
    evs = evs if isinstance(evs, list) else evs.get("data", [])
    ud = [e for e in evs if "updown-5m" in str(e.get("slug", ""))]
    if not state.get("diag"):                                 # one-time: is the data LIVE?
        state["diag"] = True
        tls = []
        for e in ud[:5]:
            try:
                rts = int(str(e.get("slug", "")).rsplit("-", 1)[-1])
                tls.append((round((rts - now) / 60, 1), str(e.get("slug"))[:26],
                            str(e.get("endDate"))[:19]))
            except Exception:
                pass
        log(f"crypto-shadow SCAN: events={len(evs)} updown={len(ud)} spot={spot} "
            f"now_utc={now_iso} nearest_updown_[t_left_min,slug,endDate]={tls}")
    today = _date.today().isoformat()
    existing = {r["market_slug"]: r for r in track.fetch_open_crypto(today)}
    new_rows, sniped, settled, skipped, fast_sniped = [], 0, 0, 0, 0
    for e in evs:
        eslug = str(e.get("slug", ""))
        if "updown-5m" not in eslug:
            continue
        sym = "btc" if eslug.startswith("btc") else "eth" if eslug.startswith("eth") else None
        if sym not in spot:
            continue
        try:
            open_ts = int(eslug.rsplit("-", 1)[-1])         # slug ts = the 5-min window OPEN
        except Exception:
            continue
        # resolution is the window CLOSE (endDate), 5 min after open — anchoring the snipe/
        # settle windows on open_ts fired them ~5 min early and recorded nothing.
        resolve_ts = _iso_epoch(e.get("endDate")) or (open_ts + 300)
        t_left = resolve_ts - now
        mkts = e.get("markets") or []
        if not mkts:
            continue
        m = mkts[0]
        if not state.get("logged"):
            log(f"crypto-shadow STRUCT {eslug}: keys={sorted(m.keys())[:16]} "
                f"outcomes={m.get('outcomes')} prices={m.get('outcomePrices')} "
                f"bestAsk={m.get('bestAsk')} bestBid={m.get('bestBid')}")
            state["logged"] = True
        up_ask, up_bid = _updown_prices(m)
        cur = spot[sym]
        row = existing.get(eslug)
        try:
            fids = _json.loads(m.get("clobTokenIds") or "[]")
        except Exception:
            fids = []
        if row is None and 60 < t_left < 400:                 # first sight — record reference
            new_rows.append({
                "model": "crypto-updown-shadow", "sport": "crypto", "market_slug": eslug,
                "outcome": "pending", "model_prob": None, "market_bid": None,
                "market_ask": None, "edge": None, "liquid": None,
                "settle_date": today, "run_date": today,
                "meta": {"sym": sym, "ref_spot": cur, "resolve_ts": resolve_ts,
                         "open_ts": open_ts, "first_seen_left": round(t_left, 1),
                         "open_up_ask": up_ask,
                         "up_tok": (fids[0] if fids else None),
                         "down_tok": (fids[1] if len(fids) >= 2 else None)}})
        elif row and row.get("outcome") == "pending" and 0 < t_left <= 60:   # THE SNIPE
            ref = (row.get("meta") or {}).get("ref_spot")
            if ref is None:
                continue
            side = "up" if cur >= ref else "down"
            # live price from the CLOB for the side we'd BUY (outcomes=[Up,Down] aligns to
            # clobTokenIds=[up_token, down_token]); gamma /events carries no live prices.
            try:
                ids = _json.loads(m.get("clobTokenIds") or "[]")
            except Exception:
                ids = []
            tok = ids[0] if (side == "up" and ids) else (ids[1] if len(ids) >= 2 else None)
            ask, _bid = _clob_book(tok) if tok else (None, None)
            meta = dict(row.get("meta") or {}, snipe_spot=cur, side=side, snipe_ask=ask,
                        spot_move=round(cur - ref, 4),
                        up_tok=(ids[0] if ids else None),
                        down_tok=(ids[1] if len(ids) >= 2 else None))
            if ask is not None and 0.02 <= ask <= 0.92:       # margin left to be worth it
                if track.set_snipe(int(row["id"]), side, ask, meta) in (200, 204):
                    sniped += 1
            else:
                track.set_snipe(int(row["id"]), "skip", ask, meta)
                skipped += 1
    # FINAL-SECONDS snipe — pspspsps5's real edge is the last few seconds, not T-60s. Sleep
    # to ~T-4s of the soonest closing window, then stamp fast_side/fast_ask onto the same rows
    # (graded later against the same venue outcome) so we can compare T-4s vs the T-60s snipe.
    import time as _time
    fnow0 = datetime.now(timezone.utc).timestamp()
    fast_due = [(s, r) for s, r in existing.items()
                if r.get("outcome") in ("up", "down", "pending")
                and not r.get("settled")                      # settled rows have a PAST close
                and not (r.get("meta") or {}).get("fast_side")
                and (r.get("meta") or {}).get("resolve_ts")
                and float((r.get("meta") or {})["resolve_ts"]) - fnow0 > 3]  # future closes only
    if fast_due:
        soonest = min(float((r.get("meta") or {})["resolve_ts"]) for _, r in fast_due)
        lead = soonest - datetime.now(timezone.utc).timestamp()
        if 3 < lead <= 45:                                    # in reach of a final-seconds read
            _time.sleep(max(0.0, lead - 4.0))                 # wake ~4s before the close
            fspot = {}
            for _sym, _pid in (("btc", "BTC-USD"), ("eth", "ETH-USD")):
                _d = _get(f"https://api.coinbase.com/v2/prices/{_pid}/spot")
                try:
                    fspot[_sym] = float(_d["data"]["amount"])
                except Exception:
                    pass
            fnow = datetime.now(timezone.utc).timestamp()
            for eslug2, row2 in fast_due:
                meta2 = row2.get("meta") or {}
                if abs(float(meta2["resolve_ts"]) - soonest) > 2:   # only the soonest window
                    continue
                sym2, ref2 = meta2.get("sym"), meta2.get("ref_spot")
                curf = fspot.get(sym2)
                if curf is None or ref2 is None:
                    continue
                fside = "up" if curf >= ref2 else "down"
                ftok = meta2.get("up_tok") if fside == "up" else meta2.get("down_tok")
                fask, _fb = _clob_book(ftok) if ftok else (None, None)
                m3 = dict(meta2, fast_side=fside, fast_ask=fask, fast_spot=curf,
                          fast_left=round(float(meta2["resolve_ts"]) - fnow, 1))
                if track.patch_meta(int(row2["id"]), m3) in (200, 204):
                    fast_sniped += 1
    # SETTLEMENT — decoupled from the live-window feed (resolved markets drop out of the
    # end_date_min fetch) and graded against the VENUE'S outcome, not our own spot signal.
    # The winning token settles to ~1 on the CLOB; a spot-vs-open proxy is a logged fallback
    # only when the venue book is uninformative. Self-grading against the same near-expiry
    # spot the snipe used produced a bogus ~86% win-rate — this replaces it.
    now = datetime.now(timezone.utc).timestamp()               # refresh (the fast pass slept)
    settle_diag = 0
    for eslug, row in existing.items():
        if row.get("outcome") not in ("up", "down") or row.get("settled"):
            continue
        meta = row.get("meta") or {}
        r_ts = meta.get("resolve_ts")
        if not r_ts or now <= float(r_ts) + 15:               # give the oracle a few seconds
            continue
        v_a, v_b = _clob_book(meta.get("up_tok")) if meta.get("up_tok") else (None, None)
        venue_up = next((p >= 0.5 for p in (v_b, v_a)
                         if p is not None and (p >= 0.85 or p <= 0.15)), None)
        cur2, ref = spot.get(meta.get("sym")), meta.get("ref_spot")
        spot_up = (cur2 >= ref) if (cur2 is not None and ref is not None) else None
        if venue_up is not None:
            src, final_up = "venue", venue_up
        elif spot_up is not None:
            src, final_up = "spot", spot_up
        else:
            continue
        win_side = "up" if final_up else "down"
        realized = (row["outcome"] == win_side)
        ask = row.get("market_ask") or 0.5
        pnl = round((1 - ask) if realized else -ask, 3)
        m2 = dict(meta, settle_src=src, venue_up_px=(v_b if v_b is not None else v_a))
        # grade the final-seconds snipe against the SAME venue outcome (T-4s vs T-60s)
        fside, fask = meta.get("fast_side"), meta.get("fast_ask")
        if fside and fask is not None:
            f_realized = (fside == win_side)
            m2["fast_realized"] = f_realized
            m2["fast_pnl"] = round((1 - fask) if f_realized else -fask, 3)
        if track.mark_settled(int(row["id"]), realized, pnl, m2) in (200, 204):
            settled += 1
        if settle_diag < 3:
            settle_diag += 1
            log(f"crypto-shadow SETTLE {eslug}: side={row['outcome']} "
                f"up_tok_book=(a={v_a},b={v_b}) spot_up={spot_up} src={src} "
                f"final_up={final_up} realized={realized}")
    if new_rows:
        track.record_predictions(new_rows)
    # COMPLETE-SET ARB (his leg #4): if up_ask + down_ask < $1, buying BOTH sides locks a
    # risk-free 1-(sum) at resolution (one settles $1). Scan the near-expiry window (books are
    # thinnest there, so arb is likeliest) and record any real crossing. Answers whether the
    # arb leg of his method actually exists on these books.
    arb_scan = arb_hits = 0
    arb_best = None
    for e in evs:
        if arb_scan >= 6:
            break
        eslug = str(e.get("slug", ""))
        if "updown-5m" not in eslug:
            continue
        try:
            rts = _iso_epoch(e.get("endDate")) or (int(eslug.rsplit("-", 1)[-1]) + 300)
        except Exception:
            continue
        tl = rts - now
        if not (4 < tl < 120):
            continue
        mk = (e.get("markets") or [{}])[0]
        try:
            aids = _json.loads(mk.get("clobTokenIds") or "[]")
        except Exception:
            aids = []
        if len(aids) < 2:
            continue
        ua, _ua = _clob_book(aids[0])
        da, _da = _clob_book(aids[1])
        arb_scan += 1
        if ua is None or da is None:
            continue
        s = round(ua + da, 4)
        if arb_best is None or s < arb_best:
            arb_best = s
        if s < 1.0:                                           # genuine complete-set arb
            arb_hits += 1
            track.record_predictions([{
                "model": "crypto-updown-arb", "sport": "crypto", "market_slug": eslug,
                "outcome": "arb", "model_prob": None, "market_bid": None,
                "market_ask": s, "edge": round(1.0 - s, 4), "liquid": None,
                "settle_date": today, "run_date": today,
                "meta": {"up_ask": ua, "down_ask": da, "sum": s,
                         "profit": round(1.0 - s, 4), "t_left": round(tl, 1)}}])
    if arb_scan:
        log(f"crypto-arb scan: markets={arb_scan} arb_hits={arb_hits} best_sum={arb_best}")
    if new_rows or sniped or settled or skipped or fast_sniped:
        log(f"crypto-shadow: +{len(new_rows)} tracked, {sniped} sniped, {fast_sniped} fast, "
            f"{skipped} no-edge, {settled} settled | active_updown_events="
            f"{sum(1 for e in evs if 'updown-5m' in str(e.get('slug','')))}")


def mirror_pspspsps5(log, state):
    """READ-ONLY mirror of pspspsps5's PUBLIC Polymarket account — his positions + trade
    history from the public data API — the most DIRECT test of his method: measure his ACTUAL
    realized results, not our reconstruction. No account, no orders, $0. Resolves his proxy
    wallet from the handle once (discovery-logged; wire the working endpoint from worker logs),
    then records new TRADE activity to model_predictions (model='pspspsps5-mirror') and logs a
    positions/P&L snapshot. His trades on the offshore .com surface are public on-chain; we only
    OBSERVE them — a US person still can't place these orders."""
    import json as _json
    import re as _re
    import urllib.request as _u
    from core import track
    def _get(url):
        try:
            with _u.urlopen(_u.Request(url, headers={"User-Agent": "prediction-mm/mirror"}),
                            timeout=12) as r:
                return r.status, r.read()
        except Exception as e:
            return getattr(e, "code", -1), str(e)[:140].encode()
    addr = state.get("mirror_addr") or os.getenv("PSPS_ADDR")
    if not addr:                                              # DISCOVERY — resolve handle->wallet
        # Primary: scrape the PUBLIC profile page — Next.js embeds proxyWallet in the HTML
        # (__NEXT_DATA__). Fallback: candidate public APIs. Log hit + status to wire the winner.
        for name, url in (
            ("page-at", "https://polymarket.com/@pspspsps5"),
            ("page-profile", "https://polymarket.com/profile/pspspsps5"),
            ("gamma-pubprofile", "https://gamma-api.polymarket.com/public-profile?username=pspspsps5"),
            ("data-username", "https://data-api.polymarket.com/username?username=pspspsps5"),
        ):
            st, body = _get(url)
            txt = body.decode("utf-8", "replace")
            mo = (_re.search(r'"proxyWallet"\s*:\s*"(0x[a-fA-F0-9]{40})"', txt)
                  or _re.search(r'"proxyAddress"\s*:\s*"(0x[a-fA-F0-9]{40})"', txt)
                  or _re.search(r'0x[a-fA-F0-9]{40}', txt))
            log(f"mirror-probe {name}: http={st} len={len(txt)} hit={bool(mo)} "
                f"head={txt[:80].replace(chr(10),' ')}")
            if st == 200 and mo:
                addr = mo.group(1) if (mo.lastindex or 0) >= 1 else mo.group(0)
                state["mirror_addr"] = addr
                log(f"mirror: resolved pspspsps5 -> {addr}")
                break
        if not addr:
            return
    # ACTIVITY — record any new TRADE rows (idempotent on the tx/asset key)
    st_a, body_a = _get(f"https://data-api.polymarket.com/activity?user={addr}&limit=100")
    if st_a != 200:
        log(f"mirror activity: http={st_a} {body_a[:120].decode('utf-8','replace')}")
        return
    try:
        acts = _json.loads(body_a)
        acts = acts if isinstance(acts, list) else acts.get("data", [])
    except Exception:
        acts = []
    seen = state.setdefault("mirror_seen", set())
    from datetime import date as _date
    today = _date.today().isoformat()
    rows = []
    for a in acts:
        if str(a.get("type", "")).upper() != "TRADE":
            continue
        tx = str(a.get("transactionHash", ""))
        key = f"{tx}-{a.get('asset','')}-{a.get('side','')}-{a.get('size','')}"
        if key in seen:
            continue
        seen.add(key)
        base = str(a.get("slug") or a.get("conditionId") or "")[:88]
        uniq = (tx[-10:] or str(a.get("timestamp", "")))       # keep EVERY trade (index dedups)
        rows.append({
            "model": "pspspsps5-mirror", "sport": "crypto",
            "market_slug": f"{base}|{uniq}"[:120],
            "outcome": str(a.get("side", "")).lower(), "model_prob": None,
            "market_bid": None, "market_ask": a.get("price"), "edge": None, "liquid": None,
            "settle_date": today, "run_date": today,
            "meta": {"slug": base, "title": str(a.get("title", ""))[:160],
                     "outcome_name": a.get("outcome"), "size": a.get("size"),
                     "usdc": a.get("usdcSize"), "ts": a.get("timestamp"), "tx": tx}})
    if rows:
        track.record_predictions(rows)
    # P&L — realized from open positions + lifetime stats discovered off the profile page
    st_pos, body_pos = _get(f"https://data-api.polymarket.com/positions?user={addr}"
                            "&sizeThreshold=0.01&limit=500")
    npos = 0
    realized = None
    try:
        ps = _json.loads(body_pos)
        ps = ps if isinstance(ps, list) else ps.get("data", [])
        npos = len(ps)
        realized = round(sum(float(p.get("realizedPnl") or p.get("cashPnl") or 0) for p in ps), 2)
    except Exception:
        pass
    # OFFICIAL lifetime P&L — his settlement redemptions aren't TRADE events, so trade cashflow
    # understates it. Probe Polymarket's actual P&L/volume endpoints once and log the winner.
    if not state.get("mirror_pnl"):
        state["mirror_pnl"] = True
        for name, url in (
            ("lb-profit", f"https://lb-api.polymarket.com/profit?window=all&limit=1&address={addr}"),
            ("lb-volume", f"https://lb-api.polymarket.com/volume?window=all&limit=1&address={addr}"),
            ("user-pnl", f"https://user-pnl-api.polymarket.com/user-pnl?user_address={addr}&interval=all&fidelity=1d"),
            ("data-pnl", f"https://data-api.polymarket.com/pnl?user_address={addr}&interval=all"),
            ("data-traded", f"https://data-api.polymarket.com/traded?user={addr}"),
        ):
            st_x, body_x = _get(url)
            log(f"mirror-pnl {name}: http={st_x} "
                f"body={body_x[:200].decode('utf-8','replace').replace(chr(10),' ')}")
    log(f"mirror: +{len(rows)} trades | positions n={npos} realizedPnl_sum={realized}")


def flow_scout(log, state):
    """READ-ONLY informed-flow scout — flag unusually LARGE tape prints on offshore
    .com, stamp lagged copy_ask, paper-score later. $0 / no orders.

    Endgame is DURATION-RELATIVE (not a fixed 180m): short sports (≤4h listed life)
    treat the whole live window as endgame; longer markets use last 50% clamped to
    [30m, 6h]. By default we record ALL size spikes and TAG endgame for stratified
    scoring — set FLOW_SCOUT_ENDGAME_ONLY=1 to drop non-endgame (legacy strict).

    Env: FLOW_SCOUT_MULT (5), FLOW_SCOUT_MIN_SIZE (100), FLOW_SCOUT_TRADE_LIMIT (120),
    FLOW_SCOUT_ENDGAME_FRAC (0.5), FLOW_SCOUT_ENDGAME_FLOOR (30),
    FLOW_SCOUT_ENDGAME_CAP (360), FLOW_SCOUT_SHORT_MAX (240),
    FLOW_SCOUT_ENDGAME_ONLY (0)."""
    import json as _json
    import urllib.request as _u
    from datetime import date as _date
    from urllib.parse import quote as _quote
    from core import track, flowscout as fs
    def _get(url):
        try:
            with _u.urlopen(_u.Request(url, headers={"User-Agent": "prediction-mm/flow-scout",
                                                     "Accept": "application/json"}),
                            timeout=15) as r:
                return r.status, _json.loads(r.read())
        except Exception as e:
            return getattr(e, "code", None), {"_err": str(e)[:160]}
    def _clob_ask(token_id):
        if not token_id:
            return None
        st, d = _get(f"https://clob.polymarket.com/book?token_id={token_id}")
        if st != 200 or not isinstance(d, dict):
            return None
        try:
            asks = d.get("asks") or []
            return min(float(a["price"]) for a in asks if a.get("price") is not None) if asks else None
        except Exception:
            return None
    def _schedule(slug):
        """Cache gamma (start_ts, end_ts) per slug; either may be None."""
        cache = state.setdefault("sched", {})
        if slug in cache:
            return cache[slug]
        st, raw = _get(f"https://gamma-api.polymarket.com/markets?slug={_quote(slug, safe='')}")
        start = end = None
        if st == 200 and isinstance(raw, list) and raw:
            m = raw[0]
            for key, slot in (("startDate", "start"), ("endDate", "end"),
                              ("eventStartTime", "start"), ("end_date_iso", "end")):
                ed = m.get(key)
                if not ed:
                    continue
                try:
                    ts = datetime.fromisoformat(str(ed).replace("Z", "+00:00")).timestamp()
                except Exception:
                    continue
                if slot == "start" and start is None:
                    start = ts
                if slot == "end" and end is None:
                    end = ts
        cache[slug] = (start, end)
        return cache[slug]

    mult = float(os.getenv("FLOW_SCOUT_MULT", "5"))
    min_size = float(os.getenv("FLOW_SCOUT_MIN_SIZE", "100"))
    trade_lim = int(os.getenv("FLOW_SCOUT_TRADE_LIMIT", "120"))
    eg_frac = float(os.getenv("FLOW_SCOUT_ENDGAME_FRAC", "0.5"))
    eg_floor = float(os.getenv("FLOW_SCOUT_ENDGAME_FLOOR", "30"))
    eg_cap = float(os.getenv("FLOW_SCOUT_ENDGAME_CAP", "360"))
    short_max = float(os.getenv("FLOW_SCOUT_SHORT_MAX", "240"))
    endgame_only = os.getenv("FLOW_SCOUT_ENDGAME_ONLY", "").strip() in ("1", "true", "yes")
    st, raw = _get(f"https://data-api.polymarket.com/trades?limit={trade_lim}")
    if st != 200 or not isinstance(raw, list):
        log(f"flow-scout: trades http={st} {str(raw)[:120]}")
        return
    today = _date.today().isoformat()
    now_ts = datetime.now(timezone.utc).timestamp()
    seen = state.setdefault("seen", set())
    baselines = state.setdefault("baselines", {})  # slug -> [sizes]
    new_rows, n_flagged, n_endgame, n_dropped = [], 0, 0, 0
    # process oldest→newest so baseline warms before later spikes
    trades = list(reversed(raw))
    for t in trades:
        if not isinstance(t, dict):
            continue
        key = fs.trade_dedupe_key(t)
        if key in seen:
            continue
        seen.add(key)
        slug = str(t.get("slug") or "")
        if not slug:
            continue
        size = fs.trade_size(t)
        hist = baselines.setdefault(slug, [])
        # decide on PRE-update baseline (spike vs prior tape)
        spiked = fs.is_spike(size, hist, mult=mult, min_size=min_size)
        med = fs.median(hist)
        fs.push_baseline(hist, size)
        if not spiked:
            continue
        start, end = _schedule(slug)
        dur = fs.market_duration_minutes(start, end) if (start and end) else None
        # if we only have end, still compute minutes_left; window unknown → not endgame
        mins = fs.minutes_to_end(end, now_ts) if end else None
        window = fs.endgame_window_minutes(dur, frac=eg_frac, floor_min=eg_floor,
                                           cap_min=eg_cap, short_max=short_max)
        eg = fs.in_endgame(mins, window)
        if endgame_only and not eg:
            n_dropped += 1
            continue
        copy_ask = _clob_ask(t.get("asset"))
        new_rows.append(fs.paper_flow_record(
            t, copy_ask=copy_ask, spike_mult=(round(size / med, 2) if med else None),
            baseline_med=med, minutes_left=mins, today=today, endgame=eg,
            duration_min=dur, endgame_window=window))
        n_flagged += 1
        if eg:
            n_endgame += 1
    if new_rows:
        track.record_predictions(new_rows)
    if len(seen) > 8000:
        state["seen"] = set(list(seen)[-3000:])
    state["summary"] = {
        "n_slugs": len(baselines), "new_flags": n_flagged, "endgame_flags": n_endgame,
        "dropped_non_eg": n_dropped, "endgame_only": endgame_only,
        "mult": mult, "min_size": min_size,
        "eg_frac": eg_frac, "short_max": short_max,
        "ts": datetime.now(timezone.utc).strftime("%H:%MZ"),
    }
    log(f"flow-scout: scanned={len(raw)} flags=+{n_flagged} endgame={n_endgame} "
        f"dropped_non_eg={n_dropped} slugs={len(baselines)} mult={mult}x "
        f"short_max={short_max:.0f}m only={int(endgame_only)}")


def _scout_candidate_slugs(client, *, max_pages: int = 3) -> list[str]:
    """Catalog slugs for census (incentives + sports pages). Cheap — no books."""
    slugs: list[str] = []
    seen: set[str] = set()
    for tp in client.get_incentives() or []:
        s = tp.get("marketSlug") if isinstance(tp, dict) else None
        if s and s not in seen:
            seen.add(s)
            slugs.append(s)
    for m in client.get_markets(category="sports", max_pages=max_pages) or []:
        s = (m.get("slug") if isinstance(m, dict) else None) or ""
        if s and s not in seen:
            seen.add(s)
            slugs.append(s)
    return slugs


def _near_end_markets(client, *, max_min: float, max_pages: int = 20) -> list[dict]:
    """Markets with endDate in (0, max_min] — list payload includes endDate/question."""
    now_ts = time.time()
    out = []
    for page in range(max(1, max_pages)):
        try:
            st, d = client.public_get("/v1/markets", limit=100, offset=page * 100,
                                      active="true", closed="false")
        except Exception:
            break
        page_m = (d.get("markets") if isinstance(d, dict) else d) or []
        if st != 200 or not page_m:
            break
        for m in page_m:
            if not isinstance(m, dict):
                continue
            end_ts = iso_ts(m.get("endDate") or "")
            if not end_ts:
                continue
            mins = (end_ts - now_ts) / 60.0
            if 0 < mins <= max_min:
                out.append({"slug": m.get("slug"), "minutes_left": mins,
                            "title": (m.get("question") or m.get("title") or "")[:160],
                            "endDate": m.get("endDate")})
    out.sort(key=lambda r: r["minutes_left"])
    return out


def _env_on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def edge_scouts(client, log, arb_state, sweep_state):
    """Combined arb + sweep paper pass — budgeted crawl + rules census.

    Arb: only rules-complete families (full catalog census booked) count toward GO.
    Sweep: discover by endDate window first (venue has almost nothing in 48h —
    default window 30d), then book those slugs preferentially.
    Caps: EDGE_SCOUT_MAX_BOOKS (100), EDGE_SCOUT_BUDGET_SECS (35).
    """
    from datetime import date as _date
    from core import track, arbscan as a, arbrules as ar, sweepscout as ss

    do_arb = _env_on("ARB_SCAN")
    do_sweep = _env_on("SWEEP_SCOUT")
    if not do_arb and not do_sweep:
        return
    max_pages = int(os.getenv("EDGE_SCOUT_MAX_PAGES", "3"))
    max_books = int(os.getenv("EDGE_SCOUT_MAX_BOOKS", "100"))
    budget = float(os.getenv("EDGE_SCOUT_BUDGET_SECS", "35"))
    fee_bin = float(os.getenv("ARB_SCAN_FEE_BIN", "0.01"))
    fee_part = float(os.getenv("ARB_SCAN_FEE_PART", "0.02"))
    max_fam = int(os.getenv("ARB_MAX_FAMILY_SIZE", "12"))
    min_ask = float(os.getenv("SWEEP_SCOUT_MIN_ASK", "0.97"))
    max_ask = float(os.getenv("SWEEP_SCOUT_MAX_ASK", "0.995"))
    # Poly US active book is mostly weeks out — 30d default for paper discovery
    max_min = float(os.getenv("SWEEP_SCOUT_MAX_MIN", str(30 * 24 * 60)))
    sweep_pages = int(os.getenv("SWEEP_SCOUT_CATALOG_PAGES", "20"))
    paper_sz = float(os.getenv("SWEEP_SCOUT_SIZE", "10"))

    today = _date.today().isoformat()
    run_id = datetime.now(timezone.utc).strftime("%H%M%S")
    now_ts = time.time()
    deadline = now_ts + budget

    catalog = _scout_candidate_slugs(client, max_pages=max_pages)
    census = ar.census_families(catalog)
    off = int(arb_state.get("fam_off", 0))
    fetch_slugs: list[str] = []
    near_meta: dict[str, dict] = {}

    if do_sweep:
        near = _near_end_markets(client, max_min=max_min, max_pages=sweep_pages)
        for row in near:
            s = row.get("slug")
            if s and s not in near_meta:
                near_meta[s] = row
                fetch_slugs.append(s)
    if do_arb:
        fam_slugs = ar.prioritize_complete_families(
            census, max_books=max_books, max_family_size=max_fam, offset=off)
        arb_state["fam_off"] = off + 1
        for s in fam_slugs:
            if s not in fetch_slugs:
                fetch_slugs.append(s)

    log(f"edge-scouts: start arb={int(do_arb)} sweep={int(do_sweep)} "
        f"catalog={len(catalog)} census_fams={len(census)} near_end={len(near_meta)} "
        f"fetch={len(fetch_slugs)} max_books={max_books} budget={budget:.0f}s")

    books: dict[str, tuple] = {}
    n_books = 0
    truncated = False
    for slug in fetch_slugs:
        if n_books >= max_books or time.time() >= deadline:
            truncated = True
            break
        try:
            bids, offers = client.get_book(slug)
        except Exception:
            continue
        if not offers:
            continue
        ba, bas = offers[0][0], offers[0][1]
        bb = bbs = None
        if bids:
            bb, bbs = bids[0][0], bids[0][1]
        books[slug] = (bb, bbs, ba, bas)
        n_books += 1

    arb_rows: list[dict] = []
    sweep_rows: list[dict] = []

    if do_arb:
        n_bin = n_part = n_depth = n_suspect = n_incomplete = n_rules = 0
        raw_bin: list[float] = []
        raw_part: list[float] = []
        best_hits: list[dict] = []
        for slug, (bb, bbs, ba, bas) in books.items():
            if bb is None:
                continue
            e = a.binary_complement_edge(bb, ba, fee_buffer=fee_bin,
                                         yes_ask_size=bas, yes_bid_size=bbs)
            if not e:
                continue
            raw_bin.append(e["raw_edge"])
            if e["actionable"]:
                n_bin += 1
                n_rules += 1  # binary is self-complete
                if e.get("depth"):
                    n_depth += 1
                detail = dict(e, rules_ok=True, rules_note="binary")
                arb_rows.append(a.paper_arb_record(
                    "binary_complement", family=slug, legs=[slug],
                    edge=e["edge"], cost=e["cost"], today=today, detail=detail,
                    run_id=run_id))
                best_hits.append({"kind": "bin", "fam": slug[:40], "edge": e["edge"],
                                  "depth": e.get("depth")})
        # evaluate census families that we fully booked
        for fam, expected in census.items():
            if len(expected) > max_fam:
                continue
            complete, note = ar.family_complete(fam, list(books.keys()), census,
                                                max_family_size=max_fam)
            members = [m for m in expected if m in books]
            if len(members) < 2:
                continue
            asks = [books[m][2] for m in members]
            sizes = [books[m][3] for m in members]
            e = a.partition_edge(asks, fee_buffer=fee_part, sizes=sizes)
            if not e:
                continue
            raw_part.append(e["raw_edge"])
            detail = dict(e, rules_ok=complete, rules_note=note,
                          census_n=len(expected), booked_n=len(members))
            if e.get("suspect_incomplete") or e["actionable"] or not complete:
                arb_rows.append(a.paper_arb_record(
                    "partition", family=fam, legs=members,
                    edge=e["edge"], cost=e["cost"], today=today, detail=detail,
                    run_id=run_id))
            if not complete:
                n_incomplete += 1
                if e["raw_edge"] > 0:
                    best_hits.append({"kind": "INCOMPLETE", "fam": fam[:40],
                                      "edge": e["edge"], "depth": e.get("depth")})
                continue
            if e.get("suspect_incomplete"):
                n_suspect += 1
                best_hits.append({"kind": "SUSPECT", "fam": fam[:40],
                                  "edge": e["edge"], "depth": e.get("depth")})
            elif e["actionable"]:
                n_part += 1
                n_rules += 1
                if e.get("depth"):
                    n_depth += 1
                best_hits.append({"kind": "part", "fam": fam[:40], "edge": e["edge"],
                                  "depth": e.get("depth")})
        # reset legacy false-GO counters: only rules-complete accumulate
        cum = arb_state.setdefault("cum", {"rules_ok": 0, "with_depth": 0,
                                           "edges": [], "incomplete": 0, "suspect": 0})
        # one-time migrate away from pre-rules counters
        if "actionable" in cum and "rules_ok" not in cum:
            cum = {"rules_ok": 0, "with_depth": 0, "edges": [], "incomplete": 0,
                   "suspect": 0}
            arb_state["cum"] = cum
        cum["rules_ok"] += n_rules
        cum["with_depth"] += n_depth
        cum["incomplete"] += n_incomplete
        cum["suspect"] += n_suspect
        for h in best_hits:
            if h["kind"] in ("bin", "part"):
                cum["edges"].append(h["edge"])
        if len(cum["edges"]) > 500:
            cum["edges"] = cum["edges"][-500:]
        med = sorted(cum["edges"])[len(cum["edges"]) // 2] if cum["edges"] else None
        verdict, reason = ar.go_kill(cum["rules_ok"], cum["with_depth"], med)
        dist_bin, dist_part = a.summarize_edges(raw_bin), a.summarize_edges(raw_part)
        arb_state["summary"] = {
            "n_slugs": len(catalog), "n_books": n_books, "n_families": len(census),
            "actionable_bin": n_bin, "actionable_part": n_part,
            "rules_ok": n_rules, "incomplete": n_incomplete,
            "suspect_part": n_suspect, "with_depth": n_depth,
            "recorded": len(arb_rows), "truncated": truncated,
            "dist_bin": dist_bin, "dist_part": dist_part,
            "cum_actionable": cum["rules_ok"],  # app field: now rules-complete
            "cum_rules_ok": cum["rules_ok"], "cum_depth": cum["with_depth"],
            "cum_incomplete": cum["incomplete"], "cum_suspect": cum["suspect"],
            "cum_median_edge": med, "verdict": verdict, "verdict_note": reason,
            "budget_s": budget, "ts": datetime.now(timezone.utc).strftime("%H:%MZ"),
        }
        log(f"arb-scan: books={n_books}{'*' if truncated else ''} "
            f"census={len(census)} rules_ok={n_rules} incomplete={n_incomplete} "
            f"suspect={n_suspect} bin={n_bin} part={n_part} "
            f"cum_rules={cum['rules_ok']} verdict={verdict}")
        for h in sorted(best_hits, key=lambda x: -abs(x["edge"]))[:5]:
            log(f"  arb-hit {h['kind']} {h['fam']} edge={h['edge']:+.4f} "
                f"depth={h.get('depth')}")

    if do_sweep:
        seen = sweep_state.setdefault("seen", set())
        hour_tag = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        n_cand = 0
        all_asks = [row[2] for row in books.values()]
        near_end_asks: list[float] = []
        # prefer books we already have that are in the near-end set; also any high ask
        for slug, (bb, bbs, ba, bas) in books.items():
            meta = near_meta.get(slug) or {}
            mins = meta.get("minutes_left")
            if mins is None and slug in near_meta:
                mins = near_meta[slug]["minutes_left"]
            if mins is not None and 0 < mins <= max_min:
                near_end_asks.append(ba)
            if not ss.is_sweep_candidate(
                    ba, bb, minutes_left=mins, min_ask=min_ask, max_ask=max_ask,
                    max_minutes_left=max_min, require_near_end=True):
                continue
            key = f"{slug}|{hour_tag}"
            if key in seen:
                continue
            title = meta.get("title") or ""
            sz = min(paper_sz, bas) if bas else paper_sz
            sweep_rows.append(ss.paper_sweep_record(
                slug, ask=ba, bid=bb, minutes_left=mins, today=today, title=title,
                ask_size=sz, run_id=run_id))
            seen.add(key)
            n_cand += 1
        if len(seen) > 4000:
            sweep_state["seen"] = set(list(seen)[-1500:])
        cum_s = sweep_state.setdefault("cum", {"candidates": 0})
        cum_s["candidates"] += n_cand
        ask_dist = ss.summarize_asks(all_asks, min_ask=min_ask)
        near_dist = ss.summarize_asks(near_end_asks, min_ask=min_ask)
        # settled sample still empty → WATCH; surface discovery stats
        verdict, reason = ss.go_kill(0, None, None, source_gate=False)
        sweep_state["summary"] = {
            "n_slugs": len(near_meta), "n_books": n_books, "candidates": n_cand,
            "recorded": len(sweep_rows), "cum_candidates": cum_s["candidates"],
            "ask_dist": ask_dist, "near_end_dist": near_dist, "truncated": truncated,
            "near_end_catalog": len(near_meta),
            "min_ask": min_ask, "max_ask": max_ask, "max_min": max_min,
            "verdict": verdict, "verdict_note": reason,
            "budget_s": budget, "ts": datetime.now(timezone.utc).strftime("%H:%MZ"),
        }
        log(f"sweep-scout: near_end_catalog={len(near_meta)} books={n_books}"
            f"{'*' if truncated else ''} candidates=+{n_cand} "
            f"asks[max={ask_dist.get('max_ask')} ge{min_ask}={ask_dist.get('n_ge_min', 0)}] "
            f"cum={cum_s['candidates']} verdict={verdict}")

    rows = arb_rows + sweep_rows
    if rows:
        track.record_predictions(rows)
    log(f"edge-scouts: done books={n_books} elapsed={time.time() - now_ts:.1f}s "
        f"truncated={int(truncated)}")


def arb_scan(client, log, state):
    """CLI/compat wrapper — runs combined edge_scouts (arb only if SWEEP off)."""
    if not _env_on("ARB_SCAN"):
        os.environ["ARB_SCAN"] = "1"
    edge_scouts(client, log, state, {})


def sweep_scout(client, log, state):
    """CLI/compat wrapper — runs combined edge_scouts (sweep only if ARB off)."""
    if not _env_on("SWEEP_SCOUT"):
        os.environ["SWEEP_SCOUT"] = "1"
    edge_scouts(client, log, {}, state)


def copy_settle_pass(log, state):
    """Settle whale/flow paper rows against gamma closed markets; score GO/KILL.

    Runs in-process so Overview heartbeat shows settle progress without a manual
    CLI. Budgeted slug count keeps it off the critical path. Env: COPY_SETTLE=1
    (default on when WHALE_SCOUT or FLOW_SCOUT is on), COPY_SETTLE_LIMIT (800),
    COPY_SETTLE_MAX_SLUGS (120).
    """
    from core import copyscore, flowscout as fs
    if not (_env_on("COPY_SETTLE") or _env_on("WHALE_SCOUT") or _env_on("FLOW_SCOUT")):
        return
    # explicit COPY_SETTLE=0 disables
    if os.getenv("COPY_SETTLE", "").strip().lower() in ("0", "false", "no", "off"):
        return
    limit = int(os.getenv("COPY_SETTLE_LIMIT", "800"))
    max_slugs = int(os.getenv("COPY_SETTLE_MAX_SLUGS", "120"))
    summary = {}
    for model, gate in (("whale-scout", "WHALE_SCOUT"), ("flow-scout", "FLOW_SCOUT")):
        if not _env_on(gate) and not _env_on("COPY_SETTLE"):
            continue
        if not _env_on(gate):
            continue
        try:
            out = copyscore.score_model(model, limit=limit, settle=True, write=True,
                                        max_slugs=max_slugs)
        except Exception as e:
            log(f"copy-settle {model} error: {e}")
            continue
        scored = out.get("scored") or {}
        v, reason = fs.go_kill(scored.get("n_scored") or 0, scored.get("hit_rate"),
                               scored.get("paper_pnl"), min_n=100, min_hit=0.55)
        summary[model] = {
            "n_rows": out.get("n_rows"), "resolutions": out.get("resolutions"),
            "n_scored": scored.get("n_scored"), "hit_rate": scored.get("hit_rate"),
            "paper_pnl": scored.get("paper_pnl"), "wrote": out.get("wrote"),
            "verdict": v, "verdict_note": reason,
            "lag_median_bps": (out.get("lag") or {}).get("lag_median_bps"),
            "ts": datetime.now(timezone.utc).strftime("%H:%MZ"),
        }
        log(f"copy-settle {model}: rows={out.get('n_rows')} resolved={out.get('resolutions')} "
            f"scored={scored.get('n_scored')} pnl={scored.get('paper_pnl')} "
            f"wrote={out.get('wrote')} verdict={v}")
    state["summary"] = summary


def whale_scout(log, state):
    """READ-ONLY whale scout — find top wallets by OFFICIAL leaderboard PROFIT
    (lb-api/profit, not volume), mirror their recent public TRADE activity, and stamp
    a lagged copy_ask (CLOB best ask at observation time) so we can paper-score whether
    copy-trading survives latency. $0 / no orders / offshore .com observe-only — a US
    person still can't place these. Env: WHALE_SCOUT_N (10), WHALE_SCOUT_MIN_PROFIT (1e4),
    WHALE_SCOUT_WINDOW (30d), WHALE_SCOUT_TRADE_LIMIT (40)."""
    import json as _json
    import urllib.request as _u
    from datetime import date as _date
    from core import track, whalescout as ws
    def _get(url):
        try:
            with _u.urlopen(_u.Request(url, headers={"User-Agent": "prediction-mm/whale-scout",
                                                     "Accept": "application/json"}),
                            timeout=15) as r:
                return r.status, _json.loads(r.read())
        except Exception as e:
            return getattr(e, "code", None), {"_err": str(e)[:160]}
    def _clob_ask(token_id):
        if not token_id:
            return None
        st, d = _get(f"https://clob.polymarket.com/book?token_id={token_id}")
        if st != 200 or not isinstance(d, dict):
            return None
        try:
            asks = d.get("asks") or []
            if not asks:
                return None
            # CLOB book levels are {price, size}; best ask = lowest price
            return min(float(a["price"]) for a in asks if a.get("price") is not None)
        except Exception:
            return None
    n = int(os.getenv("WHALE_SCOUT_N", "10"))
    min_profit = float(os.getenv("WHALE_SCOUT_MIN_PROFIT", "10000"))
    window = os.getenv("WHALE_SCOUT_WINDOW", "30d").strip() or "30d"
    trade_lim = int(os.getenv("WHALE_SCOUT_TRADE_LIMIT", "40"))
    st, raw = _get(f"https://lb-api.polymarket.com/profit?window={window}&limit={max(n * 3, 30)}")
    if st != 200:
        log(f"whale-scout: profit lb http={st} {str(raw)[:120]}")
        return
    profit_rows = ws.parse_lb_rows(raw)
    # optional volume enrich (best-effort; never the rank key)
    vol_by = {}
    for r in profit_rows[: max(n * 2, 10)]:
        vst, vraw = _get(f"https://lb-api.polymarket.com/volume?window={window}"
                         f"&limit=1&address={r['addr']}")
        if vst == 200:
            vrows = ws.parse_lb_rows(vraw)
            if vrows:
                vol_by[r["addr"]] = vrows[0]["amount"]
    whales = ws.select_whales(profit_rows, vol_by, min_profit=min_profit, max_n=n)
    if not whales:
        state["summary"] = {"n": 0, "window": window, "note": "no whales cleared min_profit",
                            "ts": datetime.now(timezone.utc).strftime("%H:%MZ")}
        log(f"whale-scout: 0 whales cleared min_profit=${min_profit:.0f} ({window})")
        return
    today = _date.today().isoformat()
    seen = state.setdefault("seen", set())
    new_rows, n_trades = [], 0
    # rotate which whale we deep-fetch so a slow timer covers the set without bursting
    idx = int(state.get("rr", 0)) % len(whales)
    state["rr"] = idx + 1
    focus = whales[idx]
    st_a, acts = _get(f"https://data-api.polymarket.com/activity?user={focus['addr']}"
                      f"&limit={trade_lim}")
    if st_a == 200 and isinstance(acts, list):
        for a in acts:
            if not ws.is_trade(a):
                continue
            key = ws.trade_dedupe_key(a)
            if key in seen:
                continue
            seen.add(key)
            # lagged executable price — what WE would pay to copy when we first saw it
            copy_ask = _clob_ask(a.get("asset"))
            new_rows.append(ws.paper_copy_record(a, focus, copy_ask=copy_ask,
                                                 today=today, window=window))
            n_trades += 1
    if new_rows:
        track.record_predictions(new_rows)
    # keep seen-set bounded
    if len(seen) > 5000:
        state["seen"] = set(list(seen)[-2000:])
    state["summary"] = {
        "n": len(whales), "window": window, "min_profit": min_profit,
        "focus": focus["name"], "new_trades": n_trades,
        "top": [{"name": w["name"], "profit": round(w["amount"]),
                 "vol": round(w.get("volume") or 0), "addr": w["addr"][:10]}
                for w in whales[:5]],
        "ts": datetime.now(timezone.utc).strftime("%H:%MZ")}
    log(f"whale-scout: {len(whales)} whales ({window}) focus={focus['name']} "
        f"+{n_trades} trades | top_pnl={[ (w['name'], round(w['amount'])) for w in whales[:3] ]}")


def crypto_probe(log):
    """READ-ONLY reachability probe (env-gated, one-shot) for the short-term crypto
    Up/Down strategy: can this worker (US Render egress) reach (a) Polymarket's PUBLIC
    market-data API — where the hourly crypto markets live, on the offshore .com surface
    that's Cloudflare-geofenced — and (b) a US-accessible spot feed (Coinbase)? Logs HTTP
    codes + whether any crypto up/down markets are discoverable. No trading, no venue
    account touched. This just answers 'is a shadow test of his method even feasible from
    our infrastructure' before we build the harness."""
    import json as _json
    import urllib.request as _u
    def _get(url, timeout=12):
        try:
            req = _u.Request(url, headers={"User-Agent": "prediction-mm/probe"})
            with _u.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except Exception as e:
            return getattr(e, "code", -1), str(e)[:80].encode()
    # (a) Polymarket public data — Gamma + CLOB
    for name, url in (("gamma", "https://gamma-api.polymarket.com/markets?closed=false&limit=200"),
                      ("clob", "https://clob.polymarket.com/markets?next_cursor=")):
        st, body = _get(url)
        found = 0
        sample = []
        try:
            d = _json.loads(body)
            ms = d if isinstance(d, list) else (d.get("data") or d.get("markets") or [])
            for m in ms:
                q = (str(m.get("question", "")) + " " + str(m.get("slug", ""))).lower()
                if any(k in q for k in ("bitcoin", "btc", "ethereum", " eth", "up or down",
                                        "hourly", "higher", "et today")):
                    found += 1
                    if len(sample) < 4:
                        sample.append(str(m.get("question", m.get("slug", "")))[:60])
        except Exception:
            pass
        log(f"crypto-probe {name}: http={st} crypto_updown_markets={found} sample={sample}")
    # (b) US-accessible spot feeds
    for name, url in (("coinbase", "https://api.coinbase.com/v2/prices/BTC-USD/spot"),
                      ("kraken", "https://api.kraken.com/0/public/Ticker?pair=XBTUSD")):
        st, body = _get(url)
        log(f"crypto-probe {name}: http={st} body={body[:90]}")
    # (c) LOCATE the hourly Up/Down series. Hourly markets RESOLVE within the hour, so
    # order by endDate ASCENDING (soonest-resolving first) — that surfaces them at the top
    # where a newest-first query missed them. Also probe the /events endpoint (Polymarket
    # groups the recurring hourly markets under events) and the crypto tag list.
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc)
    def _ends_in_h(m):
        try:
            e = _dt.fromisoformat(str(m.get("endDate")).replace("Z", "+00:00"))
            return (e - now).total_seconds() / 3600
        except Exception:
            return None
    st, body = _get("https://gamma-api.polymarket.com/markets?closed=false&active=true"
                    "&order=endDate&ascending=true&limit=100")
    try:
        ms = _json.loads(body)
        ms = ms if isinstance(ms, list) else (ms.get("data") or [])
    except Exception:
        ms = []
    cry = []
    for m in ms:
        q = (str(m.get("question", "")) + " " + str(m.get("slug", ""))).lower()
        eh = _ends_in_h(m)
        if any(k in q for k in ("btc", "bitcoin", "eth", "ethereum", "solana", "up or down")) \
                and eh is not None and 0 <= eh <= 6:
            cry.append((round(eh, 2), m))
    log(f"crypto-probe soonest-resolving: {len(ms)} markets; crypto ending<=6h={len(cry)}; "
        f"nearest_ends={[round(_ends_in_h(m) or 9, 2) for m in ms[:3]]}h")
    for eh, m in cry[:6]:
        log(f"  hourly: ends_in={eh}h slug={m.get('slug')} q={str(m.get('question',''))[:52]}")
    if cry:
        m = cry[0][1]
        log(f"  hourly STRUCT keys={sorted(m.keys())}")
        for k in ("slug", "outcomes", "outcomePrices", "clobTokenIds", "startDate",
                  "endDate", "description"):
            log(f"    {k}={str(m.get(k))[:120]}")
    # events endpoint — recurring hourly markets are grouped here
    st, body = _get("https://gamma-api.polymarket.com/events?closed=false&order=endDate"
                    "&ascending=true&limit=60")
    try:
        evs = _json.loads(body)
        evs = evs if isinstance(evs, list) else (evs.get("data") or [])
    except Exception:
        evs = []
    ce = [e for e in evs if any(k in (str(e.get("title", "")) + str(e.get("slug", ""))).lower()
                                for k in ("bitcoin", "btc", "ethereum", "up or down", "up-or-down"))]
    log(f"crypto-probe events: {len(evs)} soonest events; crypto/updown={len(ce)} "
        f"samples={[str(e.get('slug'))[:40] for e in ce[:5]]}")


def catalog_census(client: PolyClient, log):
    """READ-ONLY one-shot census of the FULL market catalog — answers 'is there an angle
    we never looked at' with data instead of priors. Logs: (1) market-class inventory
    (slug-prefix histogram over every active market); (2) a structural-consistency sweep:
    per-outcome groups (same base slug, ≥3 legs) whose YES prints sum far from $1.00 —
    sum >> 1 across exclusive outcomes = sell-all richness candidate; sum << 1 is usually
    a NON-EXHAUSTIVE listing (not an arb) but worth eyes. Prints can be stale, so the most
    extreme groups are verified against live books (capped reads). ~1 catalog fetch +
    ≤12 book reads; no orders."""
    import json as _json
    from collections import Counter, defaultdict
    mks = client.get_markets(max_pages=150)
    pref = Counter()
    groups = defaultdict(list)
    n_binary = 0
    for m in mks:
        slug = m.get("slug", "")
        if not slug:
            continue
        pref["-".join(slug.split("-")[:2])] += 1
        try:
            outs = [str(x).strip().lower() for x in _json.loads(m.get("outcomes") or "[]")]
            prs = [float(x) for x in _json.loads(m.get("outcomePrices") or "[]")]
        except Exception:
            continue
        if len(outs) == 2 and "yes" in outs and len(prs) == len(outs):
            n_binary += 1
            yes = prs[outs.index("yes")]
            if 0.0 < yes < 1.0:
                groups["-".join(slug.split("-")[:-1])].append((slug, yes))
    log(f"census: {len(mks)} active markets, {n_binary} binary-YES | "
        f"classes: {pref.most_common(18)}")
    flags = []
    for base, legs in groups.items():
        if len(legs) < 3:
            continue
        ssum = sum(p for _, p in legs)
        if ssum < 0.94 or ssum > 1.08:
            flags.append((round(ssum, 3), len(legs), base))
    flags.sort()
    n3 = sum(1 for g in groups.values() if len(g) >= 3)
    log(f"census: outcome-groups(≥3 legs)={n3}; sum<0.94: "
        f"{sum(1 for f in flags if f[0] < 0.94)} (usually non-exhaustive), sum>1.08: "
        f"{sum(1 for f in flags if f[0] > 1.08)} (sell-all candidates)")
    for f in (flags[:5] + flags[-5:]):
        log(f"  census group sum={f[0]} legs={f[1]} {f[2][:64]}")
    # verify the extreme tails against LIVE books — prints lie, books don't
    seen = set()
    for ssum, nlegs, base in (flags[:2] + flags[-2:]):
        if base in seen:
            continue
        seen.add(base)
        tops = []
        for slug, pr in sorted(groups[base], key=lambda x: -x[1])[:3]:
            try:
                bids, offers = client.get_book(slug)
                tops.append((slug.split("-")[-1][:14], pr,
                             bids[0][0] if bids else None,
                             offers[0][0] if offers else None))
            except Exception:
                tops.append((slug.split("-")[-1][:14], pr, "err", "err"))
        log(f"  census verify sum={ssum} {base[:52]}: (leg, print, bid, ask)={tops}")


def scan_markets(client: PolyClient, budget: float):
    """READ-ONLY: rank every active reward market by the retail reward share a
    `budget`-sized resting order would capture. Pure public reads (no orders).
    share = my_contracts / (touch_depth + my_contracts); est = share * pool is an
    optimistic per-market ceiling for relative ranking."""
    now = datetime.now(timezone.utc).timestamp()
    progs: dict[str, list] = {}
    for tp in client.get_incentives():
        progs.setdefault(tp["marketSlug"], []).append(tp)
    rows = []
    for slug, tps in progs.items():
        mk = client.get_market(slug)
        if not mk or mk.get("closed"):
            continue
        name = (mk.get("question") or mk.get("title") or "")
        gstart = iso_ts(mk.get("gameStartTime"))
        settle = iso_ts(mk.get("endDate"))
        # is a reward window EARNING right now? (else rewards don't accrue yet)
        active = any(program_active(now, t.get("period"), iso_ts(t.get("start")),
                                    iso_ts(t.get("end")), gstart, settle) for t in tps)
        bids, offers = client.get_book(slug)
        bb = bids[0][0] if bids else None
        ba = offers[0][0] if offers else None
        bbq = bids[0][1] if bids else 0.0
        baq = offers[0][1] if offers else 0.0
        mycon = (budget / bb) if bb else 0.0
        share = mycon / (bbq + mycon) if bb else 0.0   # our share of the bid touch
        rows.append((active, share, slug, name, gstart, bb, ba, bbq, baq))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)   # earning first, then share
    log(f"=== REWARD SCAN (rest ${budget:.0f} at bid) — {len(rows)} markets ===")
    for active, share, slug, name, gstart, bb, ba, bbq, baq in rows[:18]:
        gs = (datetime.fromtimestamp(gstart, timezone.utc).strftime('%m-%d %H:%MZ')
              if gstart else '?')
        log(f"  [{'EARNING-NOW' if active else 'waiting   '}] share={share*100:>4.1f}% "
            f"bid={bb}x{bbq:.0f} ask={ba}x{baq:.0f} game={gs} | {str(name)[:54]} | {slug}")


_RY_HIST_CAP = 80        # rolling mid samples kept per watched market (~40 min @ 30s)


def reward_yield_sample(client, state):
    """READ-ONLY per-cycle vol sampler: append the current book mid for each WATCHED
    market (set by the last scan) to a bounded rolling history in `state`. Cheap (one
    book read per watched slug) and runs at the loop cadence, so `reward_yield_scan`
    can compute a real multi-minute volatility instead of a too-short blocking burst."""
    watch = state.get("watch") or []
    if not watch:
        return
    hist = state.setdefault("hist", {})
    now = time.time()
    for slug in watch:
        b, o = client.get_book(slug)
        if b and o:
            h = hist.setdefault(slug, [])
            h.append((now, (b[0][0] + o[0][0]) / 2.0))
            if len(h) > _RY_HIST_CAP:
                del h[:-_RY_HIST_CAP]


def reward_yield_scan(client, log, state, budget):
    """READ-ONLY reward-YIELD diagnostic (Stage 1) surfaced in the worker heartbeat.

    Ranks reward-eligible markets by modeled reward/hour (pool + competing book score)
    PER UNIT of realized volatility (the adverse-selection proxy), so the app can show
    which pools are worth farming WITHOUT a manual `scripts/reward_yield.py` run. Pure
    math lives in `core.rewardyield`; this wires the network reads + heartbeat summary.
    NO orders, NO writes. Env: REWARD_YIELD_TOPN (markets tracked for vol, default 12).

    Volatility comes from the ROLLING history that `reward_yield_sample` accumulates
    between scans (the watched set), so it reflects real multi-minute movement. The
    first scan after boot has no history yet -> `warming` true, vol shown as 0 with a
    small vol_n; it fills in over subsequent cycles. Stores a summary in `state`:
    `top` (by rank), `fattest` (by pool, so big pools aren't hidden), n, max_pool."""
    from core import rewardyield as ry
    topn = int(os.getenv("REWARD_YIELD_TOPN", "12"))
    by_market: dict[str, list] = {}
    for tp in client.get_incentives():
        by_market.setdefault(tp["marketSlug"], []).append(tp)
    rows = []
    for slug, tps in by_market.items():
        mk = client.get_market(slug)
        if not mk or mk.get("closed"):
            continue
        prog = max(tps, key=lambda t: float(t.get("rewardPool") or 0), default={})
        bids, offers = client.get_book(slug)
        if not bids or not offers:
            continue
        mid = (bids[0][0] + offers[0][0]) / 2.0
        disc = float(prog.get("discountFactor") or ry._DEFAULT_DISCOUNT)
        comp = ry.competing_score(bids, offers, disc)
        pool = float(prog.get("rewardPool") or 0)
        hrs = ry.period_hours(prog.get("period"), iso_ts(prog.get("start")),
                              iso_ts(prog.get("end")), iso_ts(mk.get("gameStartTime")),
                              iso_ts(mk.get("endDate")))
        r = ry.modeled_reward(budget, mid, comp, pool, hrs)
        rows.append({"slug": slug, "period": prog.get("period"), "pool": pool,
                     "mid": mid, **r})
    if not rows:
        state["summary"] = {"n": 0, "note": "no two-sided reward books",
                            "ts": datetime.now(timezone.utc).strftime("%H:%MZ")}
        state["watch"] = []
        log("reward-yield: no two-sided reward books this scan")
        return
    # watch the highest reward/hr markets for rolling vol sampling between scans; drop
    # history for markets that rotated out of the watch set.
    rows.sort(key=lambda r: -r["reward_per_hour"])
    watch = [r["slug"] for r in rows[:topn]]
    state["watch"] = watch
    hist = state.setdefault("hist", {})
    for slug in [s for s in hist if s not in watch]:
        hist.pop(slug, None)
    # rank the watched set by reward/hr PER UNIT of rolling volatility
    ranked = []
    for r in rows[:topn]:
        v = ry.realized_vol(hist.get(r["slug"]) or [(0.0, r["mid"])])
        ranked.append(dict(r, vol_min=v["vol_per_min"], vol_n=v["n"],
                           rank=ry.rank_key(r["reward_per_hour"], v["vol_per_min"])))
    ranked.sort(key=lambda r: -r["rank"])

    def _fmt(r):
        return {"slug": r["slug"][:46], "period": r["period"], "pool": round(r["pool"]),
                "share": round(r["share"], 4), "rwd_hr": round(r["reward_per_hour"], 3),
                "yld_hr": round(r["yield_per_hr"], 4), "vol_min": round(r["vol_min"], 5),
                "vol_n": r["vol_n"], "rank": round(r["rank"], 1)}
    max_pool = max(r["pool"] for r in rows)
    warming = all(r["vol_n"] < 3 for r in ranked)
    state["summary"] = {
        "n": len(rows), "budget": budget, "max_pool": round(max_pool),
        "warming": warming, "ts": datetime.now(timezone.utc).strftime("%H:%MZ"),
        "top": [_fmt(r) for r in ranked[:10]],
        "fattest": [{"slug": r["slug"][:46], "period": r["period"],
                     "pool": round(r["pool"]), "rwd_hr": round(r["reward_per_hour"], 3)}
                    for r in sorted(rows, key=lambda r: -r["pool"])[:5]]}
    b0 = ranked[0]
    log(f"reward-yield: {len(rows)} mkts max_pool=${max_pool:.0f}"
        f"{' [vol warming]' if warming else ''} | best {b0['slug'][:30]} "
        f"rwd=${b0['reward_per_hour']:.3f}/hr vol={b0['vol_min'] * 100:.2f}c/min "
        f"(n={b0['vol_n']}) rank={b0['rank']:.1f}")


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
    if MODE == "wxedge":
        # READ-ONLY weather edge-finder: forecast vs tc-temp book, records predictions.
        recorded: set[str] = set()
        log("START mode=WXEDGE (forecast vs tc-temp market, read-only)")
        while True:
            try:
                wx_pass(client, recorded)
            except Exception as e:
                log(f"wxedge error: {e}")
            time.sleep(600)
    if MODE == "soccer":
        # READ-ONLY soccer recorder: seed Elo from results, predict fixtures, record.
        recorded: set[str] = set()
        log("START mode=SOCCER (Elo predict, read-only, records to tracker)")
        while True:
            try:
                soccer_pass(client, recorded)
            except Exception as e:
                log(f"soccer error: {e}")
            time.sleep(3600)
    if MODE == "settle":
        # READ-ONLY settlement loop: resolve due predictions against realized outcomes
        # every ~30 min. Scoring only — no orders. (Also runs inside track mode.)
        log("START mode=SETTLE (resolve due predictions, read-only)")
        while True:
            try:
                settle_pass()
            except Exception as e:
                log(f"settle error: {e}")
            time.sleep(1800)
    if MODE == "track":
        # READ-ONLY combined tracker: ONE worker accumulates BOTH the weather and
        # soccer prediction tracks continuously (weather ~10min, soccer ~hourly) and
        # settles due predictions (~hourly), so a single Render service builds the full
        # calibration record AND scores it. No orders.
        from core import track as _track
        WX_EVERY, SOC_EVERY, SPORTS_EVERY, SETTLE_EVERY, HB_EVERY = 600, 3600, 3600, 3600, 60
        wx_rec: set[str] = set()
        soc_rec: set[str] = set()
        sports_rec: set[str] = set()
        golf_rec: set[str] = set()
        last_wx = last_soc = last_sports = last_settle = last_hb = last_pnl = 0.0
        last_wxchk = last_odds = last_mlb = last_users = last_crypto = 0.0
        last_mirror = last_ryield = last_whale = last_flow = 0.0
        last_arb = last_sweep = last_copy_settle = 0.0
        odds_state: dict = {}
        crypto_state: dict = {}
        mirror_state: dict = {}
        ryield_state: dict = {}
        whale_state: dict = {}
        flow_state: dict = {}
        arb_state: dict = {}
        sweep_state: dict = {}
        copy_settle_state: dict = {}
        PNL_EVERY = 300          # log an account P&L snapshot every 5 min while live
        pnl_summ: dict = {}
        # multi-user execution: ONE shared brain, one client per registered venue
        # account (poly_users). Each user's armed switch gates only THEIR order flow.
        accounts: dict[str, TradeAccount] = {}
        refresh_accounts(accounts, log)
        live_cache = RewardMarketCache(client)     # market data reads are account-agnostic
        was_live = False
        log(f"START mode=TRACK (control-driven; live-arm="
            f"{'ON (real orders)' if LIVE_ARMED else 'off (shadow)'}, "
            f"accounts={[a.name for a in accounts.values()]}, allow={sorted(ALLOW_TOKENS)})")
        try:
            catalog_census(client, log)        # one-shot, read-only, per process
        except Exception as e:
            log(f"census error: {e}")
        if os.getenv("CRYPTO_PROBE"):          # env-gated one-shot reachability probe
            try:
                crypto_probe(log)
            except Exception as e:
                log(f"crypto-probe error: {e}")
        halts_cleared = 0.0
        while True:
            now = time.time()
            ctrl = _track.get_control()
            desired = (ctrl.get("desired_mode") or "track").lower()
            budget = float(ctrl.get("budget") or BUDGET)
            cfg = effective_config(ctrl)
            # app-driven halt reset: one-shot per clear_halts value — un-trips every
            # strategy latch on every account (probe counters included) without a deploy.
            ch = iso_ts(str(ctrl.get("clear_halts") or ""))
            if ch and ch > halts_cleared:
                if halts_cleared:            # skip the boot-time backfill of an old value
                    for acct in accounts.values():
                        for st_ in (acct.wx_state, acct.mlb_state, acct.live_state):
                            st_["tripped"] = False
                            st_.pop("status", None)
                            st_["probe_fails"] = 0
                    log("app cleared strategy halts on all accounts")
                halts_cleared = ch
            # auto-revert: a "Go Live" request carries live_until; once it passes, flip
            # back to the read-only tracker (enforces "one day only" even if forgotten).
            lu = ctrl.get("live_until")
            if desired == "live" and lu:
                try:
                    if datetime.now(timezone.utc).timestamp() > datetime.fromisoformat(str(lu).replace("Z", "+00:00")).timestamp():
                        _track.set_desired_mode("track")
                        log("live window expired -> auto-reverted to track")
                        desired = "track"
                except Exception:
                    pass
            # keep the account set in sync with poly_users (each user's own kill switch)
            if now - last_users >= HB_EVERY:
                try:
                    refresh_accounts(accounts, log)
                except Exception as e:
                    log(f"accounts refresh error: {e}")
                last_users = now
            primary = primary_account(accounts, api_key)
            # leaving live -> cancel any resting orders so none are orphaned on the book
            if was_live and desired != "live":
                for acct in accounts.values():
                    if acct.live:
                        nx = cancel_all_orders(acct.client)
                        log(f"left live -> cancelled {nx} resting orders ({acct.name})")
                was_live = False
            if desired == "off":
                if now - last_hb >= HB_EVERY:
                    _track.heartbeat("off", "off", {"note": "idled by operator", "armed": LIVE_ARMED})
                    last_hb = now
                time.sleep(15)
                continue
            # LIVE farming runs alongside the read-only tracker (one worker does both):
            # the live reward-maker quotes every cycle, while the prediction tracker
            # records + settles on its own (much slower) timers.
            live_now = (desired == "live")
            res = {}
            if live_now and accounts:
                was_live = True
                # Stage 2: feed rolling volatility (from the REWARD_YIELD sampler) into
                # market selection so live quoting prefers low-adverse-selection pools.
                vol_by_slug = None
                if ryield_state.get("hist"):
                    from core import rewardyield as _ry
                    vol_by_slug = {s: _ry.realized_vol(h)["vol_per_min"]
                                   for s, h in ryield_state["hist"].items()}
                for acct in accounts.values():
                    try:
                        r_i = live_cycle(acct.client, live_cache, acct.live_state,
                                         budget, acct.live, vol_by_slug)
                    except Exception as e:
                        log(f"live error ({acct.name}): {e}")
                        r_i = {"status": f"error: {e}"}
                    if acct is primary:
                        res = r_i
                if now - last_pnl >= PNL_EVERY and primary:  # periodic account P&L snapshot
                    pnl_summ = pnl_snapshot(primary.client)
                    try:                               # clean per-strategy settled P&L
                        primary.wx_state.update(
                            wx_settlement_pnl(primary.client, log, primary.wx_state))
                    except Exception as e:
                        log(f"wx-pnl error: {e}")
                    try:
                        primary.mlb_state.update(
                            mlb_settlement_pnl(primary.client, log, primary.mlb_state))
                    except Exception as e:
                        log(f"mlb-pnl error: {e}")
                    try:                               # daily snapshot for the app recap
                        _track.record_daily({
                            "day": datetime.now(timezone.utc).date().isoformat(),
                            "balance": pnl_summ.get("balance"),
                            "buying_power": pnl_summ.get("buying_power"),
                            "open_contracts": pnl_summ.get("open_contracts"),
                            "wx_settled_pnl": primary.wx_state.get("wx_settled_pnl"),
                            "mlb_settled_pnl": primary.mlb_state.get("mlb_settled_pnl")})
                    except Exception as e:
                        log(f"daily snapshot error: {e}")
                    last_pnl = now
            # tracker passes ALWAYS run (record models + settle) — live or not
            if now - last_wx >= WX_EVERY:
                wx_buckets = []
                try:
                    wx_buckets = wx_pass(client, wx_rec) or []
                except Exception as e:
                    log(f"track/wx error: {e}")
                # LIVE weather sell-taker: real orders only when the app/env toggle is
                # live AND the account is armed (worker-armed AND user's own switch).
                # One cycle per account; books from wx_pass are shared.
                if cfg["wx_on"]:
                    for acct in accounts.values():
                        if not acct.live or acct.wx_state.get("tripped"):
                            continue
                        try:
                            acct.wx_status = wx_taker_cycle(acct.client, cfg["wx_budget"],
                                                            acct.wx_state, log,
                                                            buckets=wx_buckets)
                        except Exception as e:
                            log(f"wx-taker error ({acct.name}): {e}")
                last_wx = now
            if now - last_soc >= SOC_EVERY:
                try:
                    soccer_pass(client, soc_rec)
                except Exception as e:
                    log(f"track/soccer error: {e}")
                last_soc = now
            if now - last_sports >= SPORTS_EVERY:
                try:
                    sports_pass(client, sports_rec)
                except Exception as e:
                    log(f"track/sports error: {e}")
                try:
                    golf_pass(golf_rec)
                except Exception as e:
                    log(f"track/golf error: {e}")
                last_sports = now
            if now - last_settle >= SETTLE_EVERY:
                try:
                    settle_pass()
                except Exception as e:
                    log(f"track/settle error: {e}")
                last_settle = now
            # LIVE-DATA PAPER measurement of the crypto Up/Down snipe edge (read-only, no
            # orders). Fast timer — the snipe window is the market's final ~60s.
            if os.getenv("CRYPTO_SHADOW") and now - last_crypto >= 30:
                try:
                    crypto_shadow(log, crypto_state)
                except Exception as e:
                    log(f"crypto-shadow error: {e}")
                last_crypto = now
            # Mirror pspspsps5's PUBLIC account (his actual trades/positions) — read-only.
            if os.getenv("CRYPTO_SHADOW") and now - last_mirror >= 120:
                try:
                    mirror_pspspsps5(log, mirror_state)
                except Exception as e:
                    log(f"mirror error: {e}")
                last_mirror = now
            # Whale scout — top wallets by OFFICIAL profit; paper-copy their trades (read-only).
            if os.getenv("WHALE_SCOUT") and now - last_whale >= 300:
                try:
                    whale_scout(log, whale_state)
                except Exception as e:
                    log(f"whale-scout error: {e}")
                last_whale = now
            # Flow scout — unusually large tape prints near market end (informed-flow thesis).
            if os.getenv("FLOW_SCOUT") and now - last_flow >= 60:
                try:
                    flow_scout(log, flow_state)
                except Exception as e:
                    log(f"flow-scout error: {e}")
                last_flow = now
            # Arb + sweep paper instruments — ONE budgeted book crawl (never block HB long).
            if (_env_on("ARB_SCAN") or _env_on("SWEEP_SCOUT")) and now - last_arb >= 300:
                try:
                    edge_scouts(client, log, arb_state, sweep_state)
                except Exception as e:
                    log(f"edge-scouts error: {e}")
                last_arb = last_sweep = now
            # Whale/flow gamma settlement + GO/KILL (hourly; writes settled rows).
            if ((_env_on("WHALE_SCOUT") or _env_on("FLOW_SCOUT") or _env_on("COPY_SETTLE"))
                    and now - last_copy_settle >= 3600):
                try:
                    copy_settle_pass(log, copy_settle_state)
                except Exception as e:
                    log(f"copy-settle error: {e}")
                last_copy_settle = now
            # Reward-YIELD diagnostic (Stage 1) — read-only pool-yield vs adverse-selection
            # ranking, surfaced in the heartbeat. The per-cycle sampler accumulates a
            # rolling mid history for the watched markets; the scan re-ranks every 10 min
            # using that history (real multi-minute volatility). No orders, no writes.
            if os.getenv("REWARD_YIELD"):
                try:
                    reward_yield_sample(client, ryield_state)
                except Exception as e:
                    log(f"reward-yield sample error: {e}")
                if now - last_ryield >= 600:
                    try:
                        reward_yield_scan(client, log, ryield_state, budget)
                    except Exception as e:
                        log(f"reward-yield error: {e}")
                    last_ryield = now
            if now - last_wxchk >= 21600:          # settlement-source checks ~every 6h
                try:
                    wx_settle_check(client, log)
                except Exception as e:
                    log(f"track/wx-settle error: {e}")
                try:
                    sport_settle_check(client, log)
                except Exception as e:
                    log(f"track/sport-settle error: {e}")
                last_wxchk = now
            if now - last_odds >= 2400:            # near-game executable-odds refresh ~40min
                try:
                    odds_refresh_pass(client, log, odds_state)
                except Exception as e:
                    log(f"track/odds-refresh error: {e}")
                last_odds = now
            # LIVE MLB probe: real orders only when the app/env toggle is live AND the
            # account is armed. Fast timer — the kickoff-passed stale-order sweep must
            # run promptly (never rest in-play); it runs even when the account tripped.
            if cfg["mlb_on"] and now - last_mlb >= 600:
                for acct in accounts.values():
                    if not acct.live:
                        continue
                    try:
                        acct.mlb_status = mlb_taker_cycle(acct.client, cfg["mlb_budget"],
                                                          acct.mlb_state, log,
                                                          edge_min=cfg["mlb_edge"])
                    except Exception as e:
                        log(f"mlb-taker error ({acct.name}): {e}")
                last_mlb = now
            if now - last_hb >= HB_EVERY:
                pw = primary.wx_state if primary else {}
                wx_hb = {"wx_taker": primary.wx_status if primary else "",
                         "wx_tripped": pw.get("tripped", False),
                         "wx_settled_pnl": pw.get("wx_settled_pnl"),
                         "wx_settled_n": pw.get("wx_settled_n"),
                         "wx_settled_auth": pw.get("wx_settled_auth"),
                         "wx_settled_est": pw.get("wx_settled_est"),
                         "mlb_taker": primary.mlb_status if primary else "",
                         "mlb_tripped": primary.mlb_state.get("tripped", False) if primary else False,
                         "mlb_settled_pnl": primary.mlb_state.get("mlb_settled_pnl") if primary else None,
                         "mlb_settled_n": primary.mlb_state.get("mlb_settled_n") if primary else None,
                         # effective strategy config (app override or env default) so the
                         # app renders the real state, not what it last requested
                         "wx_on": cfg["wx_on"], "mlb_on": cfg["mlb_on"],
                         "wx_budget": cfg["wx_budget"], "mlb_budget": cfg["mlb_budget"],
                         "mlb_edge": cfg["mlb_edge"]}
                if accounts:
                    wx_hb["users"] = {a.name: {"armed": a.live,
                                               "wx": a.wx_status, "mlb": a.mlb_status}
                                      for a in accounts.values()}
                if ryield_state.get("summary"):
                    wx_hb["reward_yield"] = ryield_state["summary"]
                if whale_state.get("summary"):
                    wx_hb["whale_scout"] = whale_state["summary"]
                if flow_state.get("summary"):
                    wx_hb["flow_scout"] = flow_state["summary"]
                if arb_state.get("summary"):
                    wx_hb["arb_scan"] = arb_state["summary"]
                if sweep_state.get("summary"):
                    wx_hb["sweep_scout"] = sweep_state["summary"]
                if copy_settle_state.get("summary"):
                    wx_hb["copy_settle"] = copy_settle_state["summary"]
                if live_now:
                    _track.heartbeat("live" if LIVE_ARMED else "live-shadow",
                                     res.get("status", "?"),
                                     {"budget": budget, "armed": LIVE_ARMED, "tracking": True,
                                      **res, **pnl_summ, **wx_hb})
                else:
                    _track.heartbeat("track", "recording",
                                     {"weather": len(wx_rec), "soccer": len(soc_rec),
                                      "sports": len(sports_rec), "armed": LIVE_ARMED, **wx_hb})
                last_hb = now
            time.sleep(POLL if live_now else 30)
    if MODE == "research":
        # READ-ONLY: (1) the TRUTH on rewards — authed earnings endpoint; (2) the
        # non-esports venue map (weather/climate markets + their books). No orders.
        log("START mode=RESEARCH (earnings truth + full venue census)")
        es, ed = client.get_incentive_earnings()
        log(f"INCENTIVE EARNINGS: http={es} body={str(ed)[:600]}")
        mks = client.get_markets(max_pages=300)   # FULL catalog (no 3k sample cap)
        log(f"VENUE CENSUS: {len(mks)} active markets")
        # histogram by category segment (slug = '<prefix>-<category>-...'), and flag
        # weather/temperature markets by keyword so we know what's tradeable here.
        from collections import Counter
        cats = Counter()
        wx = []
        WX_KW = ("temperatur", "weather", "high temp", "rainfall", "snowfall",
                 "degrees", "°", "celsius", "fahrenheit", "climate")
        for m in mks:
            slug = m.get("slug", "")
            parts = slug.split("-")
            cats[parts[1] if len(parts) > 1 else slug] += 1
            q = (m.get("question") or m.get("title") or "")
            if any(k in q.lower() for k in WX_KW):
                wx.append((slug, q[:70]))
        log(f"TOTAL distinct categories: {len(cats)}")
        for cat, n in cats.most_common():
            log(f"  category[{cat}] = {n}")
        log(f"*** WEATHER/TEMP markets found: {len(wx)} ***")
        for slug, q in wx[:25]:
            log(f"  WX {slug[:44]:44} | {q}")
        log("=== research pass complete; idling ===")
        while True:
            time.sleep(300)
    log(f"START mode={'LIVE' if live else 'SHADOW'} budget=${BUDGET} size={SIZE} "
        f"max_inv={MAX_INV} daily_loss=${DAILY_LOSS} poll={POLL}s")
    if not live:
        log("SHADOW: orders are recorded, NONE reach the exchange. "
            "Flip BOT_MODE=live (operator action) only after validation.")

    log(f"breaker: max_inv={MAX_INV}/mkt, exposure_cap=${EXPOSURE_CAP:.0f}, "
        f"daily_loss=${DAILY_LOSS:.0f}")
    cache = RewardMarketCache(client)
    state = {"tripped": False}
    while True:
        try:
            live_cycle(client, cache, state, BUDGET, live)
        except Exception as e:
            log(f"loop error: {e}")
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
