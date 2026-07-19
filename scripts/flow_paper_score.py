"""
Flow-scout paper score — lag + optional settlement PnL (shared copyscore path).

    python scripts/flow_paper_score.py
    python scripts/flow_paper_score.py --settle
    python scripts/flow_paper_score.py --settle --write
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import copyscore, whalescout as ws, flowscout as fs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--max-slugs", type=int, default=400)
    args = ap.parse_args()
    url, key = copyscore.supabase_creds()
    if not url or not key:
        sys.exit("set SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_KEY)")

    rows = copyscore.fetch_model_rows("flow-scout", args.limit)
    lag = ws.lag_cost_summary(rows)
    endgame_n = sum(1 for r in rows if (r.get("meta") or {}).get("endgame"))
    print("=== FLOW-SCOUT PAPER SCORE (lag) ===")
    print(f"  {'n_rows':20} {len(rows)}")
    for k, v in lag.items():
        print(f"  {k:20} {v}")
    print(f"  {'endgame_flags':20} {endgame_n}")

    if not args.settle:
        v, reason = fs.go_kill(0, None, None)
        print(f"\nverdict: {v} — {reason}")
        print("(pass --settle once markets resolve)")
        return

    resolutions = copyscore.build_resolutions(rows, max_slugs=args.max_slugs)
    scored = ws.score_settled_rows(rows, resolutions)
    print("\n=== FLOW-SCOUT PAPER SCORE (settlement @ copy_ask) ===")
    print(f"  gamma_resolved_slugs {len(resolutions)}")
    for k, v in scored.items():
        print(f"  {k:20} {v}")
    eg = [r for r in rows if (r.get("meta") or {}).get("endgame")]
    eg_s = ws.score_settled_rows(eg, resolutions)
    print("\n=== endgame subset ===")
    for k, v in eg_s.items():
        print(f"  {k:20} {v}")
    v, reason = fs.go_kill(scored["n_scored"], scored.get("hit_rate"),
                           scored.get("paper_pnl"))
    print(f"\nverdict: {v} — {reason}")
    if args.write:
        n = copyscore.write_settled(rows, resolutions)
        print(f"wrote_settled: {n}")


if __name__ == "__main__":
    main()
