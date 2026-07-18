"""
Arb-scan paper score — edge distribution + GO/KILL from accumulated rows.

Does NOT prove executable arb by itself; scores what the detector recorded.
Rules-exhaustiveness verification is still required before any live capital.

    python scripts/arb_paper_score.py
    python scripts/arb_paper_score.py --limit 5000
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

from core import arbscan as a

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
    edges, n_depth = [], 0
    by_kind: dict[str, int] = {}
    for r in rows:
        meta = r.get("meta") or {}
        e = r.get("edge")
        if e is None:
            e = meta.get("edge")
        if e is not None:
            edges.append(float(e))
        d = meta.get("depth")
        if d is not None and float(d) > 0:
            n_depth += 1
        k = meta.get("kind") or r.get("outcome") or "?"
        by_kind[k] = by_kind.get(k, 0) + 1

    dist = a.summarize_edges(edges)
    med = dist.get("p50")
    v, reason = a.go_kill(len(edges), n_depth, med)

    print("=== ARB-SCAN PAPER SCORE ===")
    print(f"  rows                 {len(rows)}")
    print(f"  by_kind              {by_kind}")
    print(f"  with_depth>0         {n_depth}")
    for k, val in dist.items():
        print(f"  edge_{k:<16} {val}")
    print(f"\nverdict: {v} — {reason}")
    print("NOTE: GO still requires rules-exhaustiveness verification before live.")


if __name__ == "__main__":
    main()
