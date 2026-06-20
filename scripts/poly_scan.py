"""
Read-only Polymarket US liquidity-incentive assessment. PLACES NO ORDERS.

For each ACTIVE reward market, pull the live order book and quantify the
validate-first question: at our retail capital, what pro-rata share of the
reward pool could we realistically capture at the touch, and how much pro size
are we competing against? Also estimates the maker rebate per round trip.

The reward score is  discountFactor^(ticks_from_best) * size , snapshotted ~1/s
and split pro-rata. Posting one tick off best at discount 0.3 keeps only 30% of
the score, so meaningful earning means quoting AT the touch — exactly where
adverse selection is worst (this tool measures the share; the adverse-selection
cost needs a LIVE game, run during one).

    python scripts/poly_scan.py                 # one sweep, default $400 budget
    python scripts/poly_scan.py 489             # assume $489 of resting capital
"""
import sys

from core.polyclient import from_env

# maker rebate: payment of 0.0125 * C * p * (1-p) per contract traded (you get paid)
MAKER_REBATE_THETA = 0.0125


def maker_rebate(price: float, contracts: float) -> float:
    return MAKER_REBATE_THETA * contracts * price * (1.0 - price)


def main(budget: float):
    c = from_env()
    progs = c.get_incentives()
    # group programs by market; keep the biggest live pool + the day_of pool
    by_market = {}
    for tp in progs:
        by_market.setdefault(tp["marketSlug"], []).append(tp)

    print(f"=== POLYMARKET US REWARD-MARKET ASSESSMENT  (resting budget ${budget:.0f}) ===")
    print(f"active reward markets: {len(by_market)} "
          f"(programs: {len(progs)})\n")

    open_rows, total_live_pool = [], 0.0
    for slug, tps in by_market.items():
        mk = c.get_market(slug)
        if not mk or mk.get("closed"):
            continue
        live = next((t for t in tps if t.get("period") == "live"), None)
        day_of = next((t for t in tps if t.get("period") == "day_of"), None)
        pool_live = float((live or {}).get("rewardPool") or 0)
        disc = float((live or {}).get("discountFactor") or 0.3)
        tsize = float((live or {}).get("targetSize") or 0)
        total_live_pool += pool_live

        bids, offers = c.get_book(slug)
        if not bids or not offers:
            open_rows.append((slug, mk.get("gameStartTime"), None, None, None,
                              pool_live, tsize, disc, mk.get("volume24hr")))
            continue
        bb, bbq = bids[0]
        bo, boq = offers[0]
        spread = round(bo - bb, 4)
        touch = bbq + boq               # pro size already at the touch (both sides)
        mid = (bb + bo) / 2.0
        # contracts WE can rest with `budget`, split both sides at mid price
        my_contracts = budget / mid if mid > 0 else 0
        # pro-rata touch share: our tick-0 score vs (existing touch + ours)
        share = my_contracts / (touch + my_contracts) if touch else 0
        open_rows.append((slug, mk.get("gameStartTime"), spread, touch, share,
                          pool_live, tsize, disc, mk.get("volume24hr")))

    # report
    hdr = f"{'market':46} {'kickoff':17} {'spr':>5} {'touchQty':>9} {'ourShr':>7} {'livePool':>8}"
    print(hdr); print("-" * len(hdr))
    booked = [r for r in open_rows if r[2] is not None]
    for slug, ks, spr, touch, share, pool, tsize, disc, v24 in sorted(
            booked, key=lambda r: -(r[4] or 0))[:20]:
        ks = (ks or "")[5:16]
        print(f"{slug[:46]:46} {ks:17} {spr*100:>4.0f}¢ {touch:>9,.0f} "
              f"{(share or 0)*100:>6.1f}% ${pool:>7,.0f}")

    print(f"\nmarkets with a live two-sided book now: {len(booked)} / {len(open_rows)} open")
    if booked:
        avg_touch = sum(r[3] for r in booked) / len(booked)
        avg_share = sum(r[4] for r in booked) / len(booked)
        avg_spr = sum(r[2] for r in booked) / len(booked)
        print(f"avg touch depth (both sides): {avg_touch:,.0f} contracts")
        print(f"avg spread: {avg_spr*100:.1f}¢   target_size: 20,000")
        print(f"our avg pro-rata touch share at ${budget:.0f}: {avg_share*100:.2f}%")
        # reward range: pool is per-market; cadence (per-game vs per-day) unknown,
        # so bracket it. share * pool is the optimistic ceiling (we never beat it).
        ceil = avg_share * 9900
        print(f"\noptimistic reward CEILING per live market = share x $9,900 pool "
              f"≈ ${ceil:,.2f}")
        print(f"maker rebate on a 1,000-contract round trip at $0.50 ≈ "
              f"${maker_rebate(0.50, 1000):.2f}")
        print("\nNOTE: ceiling ignores (a) discount decay if we're not exactly at touch,")
        print("(b) ADVERSE SELECTION on in-play fills — measure that on a LIVE game.")


if __name__ == "__main__":
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 400.0
    main(budget)
