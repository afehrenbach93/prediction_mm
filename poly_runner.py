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
    fc, rows, taker_buckets = {}, [], []
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
        prob = wx.bucket_probability(high, sigma, p["lo"], p["hi"])
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
    sport_rows = [r for r in rows if (r["model"] or "").startswith("elo-")]
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


def cancel_all_orders(client: PolyClient, exclude_prefixes=("tc-temp",)) -> int:
    """Cancel our resting orders (best-effort) — used by the cricket reconcile + breaker.
    EXCLUDES tc-temp (weather sell-taker) orders by default so the cricket farm's
    cancel-all reconcile doesn't nuke the separately-managed weather offers every cycle.
    Pass exclude_prefixes=() to cancel literally everything."""
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
        # ignore held-legacy (deny) positions AND the weather sell-taker's tc-temp shorts,
        # which have their OWN budget/breaker — otherwise weather exposure trips the cricket
        # farm (the two strategies must be accounted independently).
        if slug in DENY_SLUGS or slug.startswith("tc-temp"):
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
               budget: float, live: bool) -> dict:
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
    sel = sorted(windows, key=lambda w: -w[2])[:MAX_MARKETS]
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
        try:
            sb, bal = client.signed_get("/v1/account/balances")
            log(f"PNL balance http={sb} {str(bal)[:300]}")
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
    """Extract the authoritative settled realized dollars from a positionResolution, checking
    every location PM has been observed to carry it. Returns (value, source) or (None, '')."""
    candidates = [
        ("pr.realized", pr.get("realized")),
        ("pr.realizedPnl", pr.get("realizedPnl")),
        ("pr.pnl", pr.get("pnl")),
        ("ap.realized", ap.get("realized")),
        ("ap.realizedPnl", ap.get("realizedPnl")),
        ("bp.realized", bp.get("realized")),
    ]
    for src, cand in candidates:
        v = _wx_money(cand)
        if v is not None:
            return v, src
    return None, ""


def wx_settlement_pnl(client: PolyClient, log, state) -> dict:
    """Account-level WEATHER-ONLY realized P&L: sum the settled P&L of tc-temp positions
    from portfolio activities (the shared balance mixes cricket + open marks, so isolate
    weather here). Prefers the resolution's AUTHORITATIVE settled `realized`; falls back to
    computing from net/cost/outcome (short: settles NO -> +(Q-cost), YES -> -cost) and marks
    the row estimated. Logs the cleaned resolution structure ONCE so the realized field can be
    verified. Read-only; never raises."""
    import json as _json
    try:
        _, act = client.signed_get("/v1/portfolio/activities")
    except Exception as e:
        log(f"wx-pnl activities err={str(e)[:70]}")
        return {}
    items = act.get("activities") if isinstance(act, dict) else act
    items = items if isinstance(items, list) else []
    seen, rows, total = set(), [], 0.0
    struct_logged = state.get("wx_pnl_logged", False)
    n_auth = n_est = 0
    outcome_cache = {}
    for a in items:
        if not isinstance(a, dict) or (a.get("type") or "") != "ACTIVITY_TYPE_POSITION_RESOLUTION":
            continue
        pr = a.get("positionResolution") or {}
        slug = pr.get("marketSlug", "")
        if not slug.startswith("tc-temp"):
            continue
        bp = pr.get("beforePosition") or {}
        ap = pr.get("afterPosition") or {}
        key = (slug, bp.get("updateTime", ""))
        if key in seen:
            continue
        seen.add(key)
        if not struct_logged:
            log(f"wx-pnl STRUCT: {_json.dumps(_wx_clean_struct(pr))[:900]}")
            struct_logged = True
        # authoritative settled realized, if the resolution carries it
        realized, src = _wx_find_realized(pr, ap, bp)
        est = False
        if realized is None:
            # fallback: compute from net + cost + PM outcome (assume cost = collateral)
            est = True
            try:
                net = float(bp.get("qtyBought", 0)) - float(bp.get("qtySold", 0))
                cost = float((bp.get("cost") or {}).get("value", 0))
            except Exception:
                continue
            if net == 0:
                continue
            if slug not in outcome_cache:
                m = client.get_market(slug)
                try:
                    prs = [float(x) for x in _json.loads((m or {}).get("outcomePrices") or "[]")]
                    outcome_cache[slug] = (prs[0] > 0.9) if prs else None
                except Exception:
                    outcome_cache[slug] = None
            yes = outcome_cache[slug]
            if yes is None:
                continue
            won = (net < 0 and not yes) or (net > 0 and yes)     # short wins on NO
            realized = (abs(net) - cost) if won else -cost
        total += realized
        n_est += int(est)
        n_auth += int(not est)
        rows.append((round(realized, 2), ("~" if est else " ") + slug[-30:]))
    state["wx_pnl_logged"] = struct_logged
    rows.sort()
    tag = f"{n_auth} authoritative + {n_est} estimated" if n_est else f"{n_auth} authoritative"
    log(f"wx-pnl: {len(rows)} settled weather positions ({tag}), total realized ${total:+.2f}")
    for r in rows[:12]:
        log(f"  wx-pnl {r}")
    return {"wx_settled_pnl": round(total, 2), "wx_settled_n": len(rows),
            "wx_settled_auth": n_auth, "wx_settled_est": n_est}


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
        last_wxchk = 0.0
        PNL_EVERY = 300          # log an account P&L snapshot every 5 min while live
        pnl_summ: dict = {}
        # live trading client (REAL only when POLY_LIVE_ARMED; else shadow) + cache +
        # breaker state for the app-driven "Go Live" path.
        live_client = client
        if LIVE_ARMED:
            live_client = PolyClient(api_key_id=api_key, secret_b64=secret, live=True)
        live_cache = RewardMarketCache(live_client)
        live_state = {"tripped": False}
        wx_state = {"tripped": False}
        wx_status = ""
        was_live = False
        log(f"START mode=TRACK (control-driven; live-arm="
            f"{'ON (real orders)' if LIVE_ARMED else 'off (shadow)'}, allow={sorted(ALLOW_TOKENS)})")
        while True:
            now = time.time()
            ctrl = _track.get_control()
            desired = (ctrl.get("desired_mode") or "track").lower()
            budget = float(ctrl.get("budget") or BUDGET)
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
            # leaving live -> cancel any resting orders so none are orphaned on the book
            if was_live and desired != "live":
                nx = cancel_all_orders(live_client)
                log(f"left live -> cancelled {nx} resting orders")
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
            if live_now:
                was_live = True
                try:
                    res = live_cycle(live_client, live_cache, live_state, budget, LIVE_ARMED)
                except Exception as e:
                    log(f"live error: {e}")
                    res = {"status": f"error: {e}"}
                if now - last_pnl >= PNL_EVERY:        # periodic account P&L snapshot
                    pnl_summ = pnl_snapshot(live_client)
                    if WX_TAKER == "live":             # clean weather-only settled P&L
                        try:
                            wx_state.update(wx_settlement_pnl(live_client, log, wx_state))
                        except Exception as e:
                            log(f"wx-pnl error: {e}")
                    last_pnl = now
            # tracker passes ALWAYS run (record models + settle) — live or not
            if now - last_wx >= WX_EVERY:
                wx_buckets = []
                try:
                    wx_buckets = wx_pass(client, wx_rec) or []
                except Exception as e:
                    log(f"track/wx error: {e}")
                # LIVE weather sell-taker: real orders only when WX_TAKER=live AND armed.
                # reuse wx_pass's freshly-read books (no duplicate ~60-book read).
                if WX_TAKER == "live" and LIVE_ARMED and not wx_state.get("tripped"):
                    try:
                        wx_status = wx_taker_cycle(live_client, WX_BUDGET, wx_state, log,
                                                   buckets=wx_buckets)
                    except Exception as e:
                        log(f"wx-taker error: {e}")
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
            if now - last_wxchk >= 21600:          # settlement-source check ~every 6h
                try:
                    wx_settle_check(client, log)
                except Exception as e:
                    log(f"track/wx-settle error: {e}")
                last_wxchk = now
            if now - last_hb >= HB_EVERY:
                wx_hb = {"wx_taker": wx_status, "wx_tripped": wx_state.get("tripped", False),
                         "wx_settled_pnl": wx_state.get("wx_settled_pnl"),
                         "wx_settled_n": wx_state.get("wx_settled_n"),
                         "wx_settled_auth": wx_state.get("wx_settled_auth"),
                         "wx_settled_est": wx_state.get("wx_settled_est")} \
                    if WX_TAKER == "live" else {}
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
