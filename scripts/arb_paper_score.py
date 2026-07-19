"""
Arb-scan paper score — rules-complete edges + GO/KILL.

    python scripts/arb_paper_score.py
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import arbrules as ar

URL = os.getenv("SUPABASE_URL", "")
KEY = (os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_SERVICE_KEY") or "")


def fetch_rows(limit: int) -> list[dict]:
    q = urllib.parse.urlencode({
        "select": "id,market_slug,outcome,market_ask,edge,settled,meta,run_date",
        "model": "eq.arb-scan",
        "order": "id.desc",
        "limit": str(limit),
    })
    req = urllib.request.Request(
        f"{URL.rstrip('/')}/rest/v1/model_predictions?{q}",
        headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=5000)
    args = ap.parse_args()
    if not URL or not KEY:
        sys.exit("set SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_KEY)")

    rows = fetch_rows(args.limit)
    rules_edges, n_depth, n_incomplete, n_suspect = [], 0, 0, 0
    by_kind: dict[str, int] = {}
    for r in rows:
        meta = r.get("meta") or {}
        k = meta.get("kind") or r.get("outcome") or "?"
        by_kind[k] = by_kind.get(k, 0) + 1
        if meta.get("suspect_incomplete"):
            n_suspect += 1
            continue
        if meta.get("rules_ok") is False or (
                meta.get("rules_note") or "").startswith("incomplete"):
            n_incomplete += 1
            continue
        # legacy rows without rules_ok: exclude partitions (likely incomplete)
        if meta.get("rules_ok") is None and k == "partition":
            n_incomplete += 1
            continue
        e = r.get("edge")
        if e is None:
            e = meta.get("edge")
        if e is not None:
            rules_edges.append(float(e))
        d = meta.get("depth")
        if d is not None and float(d) > 0:
            n_depth += 1

    from core import arbscan as a
    dist = a.summarize_edges(rules_edges)
    med = dist.get("p50")
    v, reason = ar.go_kill(len(rules_edges), n_depth, med)

    print("=== ARB-SCAN PAPER SCORE (rules-complete only) ===")
    print(f"  rows                 {len(rows)}")
    print(f"  by_kind              {by_kind}")
    print(f"  rules_complete       {len(rules_edges)}")
    print(f"  incomplete/excluded  {n_incomplete}")
    print(f"  suspect              {n_suspect}")
    print(f"  with_depth>0         {n_depth}")
    for k, val in dist.items():
        print(f"  edge_{k:<16} {val}")
    print(f"\nverdict: {v} — {reason}")


if __name__ == "__main__":
    main()
