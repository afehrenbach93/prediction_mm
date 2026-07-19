"""
Whale-scout paper score — lag + optional settlement PnL @ lagged copy_ask.

    python scripts/whale_paper_score.py
    python scripts/whale_paper_score.py --settle
    python scripts/whale_paper_score.py --settle --write
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import copyscore, whalescout as ws, flowscout as fs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=3000)
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--max-slugs", type=int, default=500)
    args = ap.parse_args()
    url, key = copyscore.supabase_creds()
    if not url or not key:
        sys.exit("set SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_KEY)")

    out = copyscore.score_model(
        "whale-scout", limit=args.limit, settle=args.settle,
        write=args.write, max_slugs=args.max_slugs)
    print("=== WHALE-SCOUT PAPER SCORE (lag) ===")
    print(f"  {'n_rows':20} {out['n_rows']}")
    for k, v in (out.get("lag") or {}).items():
        print(f"  {k:20} {v}")

    if not args.settle:
        v, reason = fs.go_kill(0, None, None, min_n=100, min_hit=0.55)
        print(f"\nverdict: {v} — {reason}")
        print("(pass --settle once markets resolve)")
        return

    scored = out.get("scored") or {}
    print("\n=== WHALE-SCOUT PAPER SCORE (settlement @ copy_ask) ===")
    print(f"  {'gamma_resolved_slugs':20} {out.get('resolutions')}")
    for k, v in scored.items():
        print(f"  {k:20} {v}")
    v, reason = fs.go_kill(scored.get("n_scored") or 0, scored.get("hit_rate"),
                           scored.get("paper_pnl"), min_n=100, min_hit=0.55)
    print(f"\nverdict: {v} — {reason}")
    if args.write:
        print(f"wrote_settled: {out.get('wrote')}")


if __name__ == "__main__":
    main()
