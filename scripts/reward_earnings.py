"""
Reward-credit reader — authenticated `/v1/incentives/earnings`.

Polymarket US credits LP rewards ~5+2 business days after a period ends. This
script is the confirmation leg of the economics pilot (PILOT.md): compare the
credited amount to the same-day modeled reward/hr from the heartbeat.

READ-ONLY. Places no orders. Must run where Polymarket US is reachable (Render
one-off / US egress) — this sandbox is often geo-blocked (403).

    python scripts/reward_earnings.py
    python scripts/reward_earnings.py --raw     # full JSON dump (truncated)
    python scripts/reward_earnings.py --slug lmx
"""
import argparse
import json
import os
import sys

from core.polyclient import from_env, load_env
from core import rewardearnings as re_


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", action="store_true", help="print truncated raw body")
    ap.add_argument("--slug", default="",
                    help="substring filter on marketSlug (e.g. lmx, aec-czech)")
    args = ap.parse_args()
    env = load_env()
    if not env.get("POLYMARKET_API_KEY"):
        env["POLYMARKET_API_KEY"] = os.getenv("POLYMARKET_API_KEY", "")
        env["POLYMARKET_SECRET"] = os.getenv("POLYMARKET_SECRET", "")
    if not env.get("POLYMARKET_API_KEY") or not env.get("POLYMARKET_SECRET"):
        sys.exit("need POLYMARKET_API_KEY + POLYMARKET_SECRET")

    client = from_env(env)
    st, body = client.get_incentive_earnings()
    print("=== REWARD EARNINGS (/v1/incentives/earnings) ===")
    print(f"http        : {st}")
    if st != 200:
        print(f"body        : {str(body)[:500]}")
        if st == 403:
            print("hint: geo-blocked — run on the Render polymarket-mm shell (US egress)")
        sys.exit(1)
    summ = re_.summarize(body)
    print(f"n_rows      : {summ['n_rows']}")
    print(f"sum_all     : {summ['sum_amount_fields']}")
    print(f"paid        : {summ['paid']}")
    print(f"pending     : {summ['pending_credit']}")
    print(f"skipped     : {summ['skipped']}")
    print(f"n_by_status : {summ['n_by_status']}")
    print(f"body_keys   : {summ['keys']}")
    rows = re_.flatten_earnings(body)
    if args.slug:
        needle = args.slug.lower()
        rows = [r for r in rows if needle in str(r.get("marketSlug") or "").lower()]
        print(f"\n-- filtered slug~={args.slug!r} (n={len(rows)}) --")
    else:
        rows = summ["sample"]
        print("\n-- recent sample --")
    for r in rows[:25]:
        print(f"  {r.get('date')}  {str(r.get('status')):8}  "
              f"${re_.row_amount(r) or 0:7.2f}  {str(r.get('marketSlug') or '')[:56]}")
    if args.raw:
        print("\n--- raw (truncated) ---")
        print(json.dumps(body, indent=2, default=str)[:4000])
    print("\nCompare pending/paid $ to heartbeat reward_yield top rwd_hr × hours.")
    print("Credits land ~5+2 business days after the reward period ends.")


if __name__ == "__main__":
    main()
