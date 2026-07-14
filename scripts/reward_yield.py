"""
Stage 1 — read-only reward-YIELD + volatility sweep for the Polymarket US farm.
PLACES NO ORDERS.

Ranks reward-eligible markets by modeled reward/hour (from the pool + the competing
book score) divided by realized volatility (the adverse-selection proxy), so we can
see whether ANY live pool offers a fat subsidy at low adverse selection BEFORE
risking capital. This is the cheapest possible read on the one unproven leg of the
whole Polymarket thesis; if even the best market can't plausibly cover adverse
selection, the venue has no retail income edge and we close it for $0.

Two passes bound the number of book reads:
  A. one book read per open reward market -> pool, competing score, modeled reward/hr
  B. sample the top-N by reward/hr every few seconds for a short window -> volatility

Run on the US worker (this sandbox is geo-blocked from api.polymarket.us):
    python scripts/reward_yield.py                 # $200 budget, top 8, 30s window
    python scripts/reward_yield.py 50 6 45 3       # budget=$50 topN=6 window=45s every=3s
"""
import sys
import time
from datetime import datetime, timezone

from core.polyclient import from_env
from core import rewardyield as ry


def iso_ts(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def best_program(tps):
    """The active program with the biggest pool — what we'd actually farm here."""
    return max(tps, key=lambda t: float(t.get("rewardPool") or 0), default={})


def mid_of(bids, offers):
    return (float(bids[0][0]) + float(offers[0][0])) / 2.0 if (bids and offers) else None


def snapshot(client, slug, prog, budget):
    """Pass A: single book read -> modeled reward for this market (None if no book)."""
    bids, offers = client.get_book(slug)
    mid = mid_of(bids, offers)
    if mid is None:
        return None
    disc = float(prog.get("discountFactor") or ry._DEFAULT_DISCOUNT)
    comp = ry.competing_score(bids, offers, disc)
    pool = float(prog.get("rewardPool") or 0)
    hrs = ry.period_hours(prog.get("period"),
                          iso_ts(prog.get("start")), iso_ts(prog.get("end")))
    r = ry.modeled_reward(budget, mid, comp, pool, hrs)
    return {"slug": slug, "mid": mid, "spread": round(float(offers[0][0]) - float(bids[0][0]), 4),
            "pool": pool, "period": prog.get("period"), "hours": hrs,
            "competing": comp, "vol": ry.realized_vol([]), "rank": 0.0, **r}


def main(budget, top_n, window, every):
    client = from_env()
    progs = client.get_incentives()
    by_market = {}
    for tp in progs:
        by_market.setdefault(tp["marketSlug"], []).append(tp)

    print(f"=== REWARD-YIELD SWEEP  budget=${budget:.0f}  topN={top_n}  "
          f"vol_window={window:.0f}s ===")
    print(f"active reward markets: {len(by_market)}  (programs: {len(progs)})\n")

    # Pass A — modeled reward per open reward market.
    rows = []
    for slug, tps in by_market.items():
        mk = client.get_market(slug)
        if not mk or mk.get("closed"):
            continue
        snap = snapshot(client, slug, best_program(tps), budget)
        if snap:
            rows.append(snap)
    rows.sort(key=lambda r: -r["reward_per_hour"])
    shortlist = rows[:top_n]

    # Pass B — sample the shortlist's mid over a short window to estimate volatility.
    series = {r["slug"]: [] for r in shortlist}
    t_end = time.time() + window
    while time.time() < t_end:
        now = time.time()
        for r in shortlist:
            bids, offers = client.get_book(r["slug"])
            mid = mid_of(bids, offers)
            if mid is not None:
                series[r["slug"]].append((now, mid))
        time.sleep(every)
    for r in shortlist:
        r["vol"] = ry.realized_vol(series[r["slug"]])
        r["rank"] = ry.rank_key(r["reward_per_hour"], r["vol"]["vol_per_min"])
    shortlist.sort(key=lambda r: -r["rank"])

    hdr = (f"{'market':44}{'per':>8}{'pool':>8}{'shr%':>7}{'rwd/hr':>8}"
           f"{'yld/hr':>8}{'vol/min':>9}{'rank':>9}")
    print(hdr)
    print("-" * len(hdr))
    for r in shortlist:
        print(f"{r['slug'][:44]:44}{str(r['period'])[:7]:>8}${r['pool']:>6,.0f}"
              f"{r['share'] * 100:>6.2f}%${r['reward_per_hour']:>6.3f}"
              f"{r['yield_per_hr'] * 100:>6.2f}%{r['vol']['vol_per_min'] * 100:>7.2f}c"
              f"{r['rank']:>9.1f}")

    print("\ninterpretation:")
    print("  rwd/hr  = modeled reward $/hr from resting the full budget at the touch")
    print("  yld/hr  = rwd/hr as % of budget    vol/min = avg |mid move| per minute (cents)")
    print("  rank    = rwd/hr per unit vol (fat subsidy + low movement ranks first)")
    if shortlist:
        b = shortlist[0]
        print(f"\nBEST: {b['slug'][:44]}  reward~${b['reward_per_hour']:.3f}/hr "
              f"({b['yield_per_hr'] * 100:.2f}%/hr)  vol={b['vol']['vol_per_min'] * 100:.2f}c/min "
              f"(n={b['vol']['n']})")
        print("KILL read: if even the top market's reward/hr can't plausibly cover")
        print("adverse selection at this vol, the venue has no retail income edge.")
    else:
        print("no two-sided reward books right now — re-run during an active window.")


if __name__ == "__main__":
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 200.0
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    window = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0
    every = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0
    main(budget, top_n, window, every)
