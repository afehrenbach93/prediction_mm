"""
Whale-scout paper score — lag cost now; settlement PnL when gamma resolves.

Read-only by default. Pulls model='whale-scout' rows from Supabase and reports:
  1) lag_bps distribution (copy_ask vs their fill) — available immediately
  2) optional --settle: try gamma-api resolution per slug and score paper PnL
     at the lagged copy_ask (does NOT write back unless --write)

    python scripts/whale_paper_score.py
    python scripts/whale_paper_score.py --settle
    python scripts/whale_paper_score.py --settle --write   # mark_settled + pnl
    python scripts/whale_paper_score.py --limit 500
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

from core import track, whalescout as ws

URL = os.getenv("SUPABASE_URL", "")
KEY = (os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_SERVICE_KEY") or "")
UA = {"User-Agent": "prediction-mm/whale-paper-score", "Accept": "application/json"}
GAMMA = "https://gamma-api.polymarket.com"


def fetch_whale_rows(limit: int) -> list[dict]:
    q = urllib.parse.urlencode({
        "select": "id,market_slug,outcome,market_ask,settled,realized_yes,pnl,"
                  "settle_date,meta",
        "model": "eq.whale-scout",
        "order": "id.desc",
        "limit": str(limit),
    })
    req = urllib.request.Request(
        f"{URL.rstrip('/')}/rest/v1/model_predictions?{q}",
        headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def gamma_market(slug: str) -> dict | None:
    if not slug:
        return None
    url = f"{GAMMA}/markets?{urllib.parse.urlencode({'slug': slug})}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                    timeout=20) as r:
            raw = json.loads(r.read())
    except Exception:
        return None
    if isinstance(raw, list) and raw:
        return raw[0] if isinstance(raw[0], dict) else None
    return raw if isinstance(raw, dict) else None


def build_resolutions(rows: list[dict]) -> dict:
    """slug -> {outcomes, outcomePrices} for markets gamma can resolve."""
    slugs = []
    for r in rows:
        meta = r.get("meta") or {}
        slug = meta.get("slug") or str(r.get("market_slug") or "").split("|", 1)[0]
        if slug and slug not in slugs:
            slugs.append(slug)
    out = {}
    for slug in slugs:
        m = gamma_market(slug)
        if not m:
            continue
        closed = bool(m.get("closed")) or str(m.get("umaResolutionStatus") or "").lower() == "resolved"
        if not closed:
            continue
        out[slug] = {"outcomes": m.get("outcomes"),
                     "outcomePrices": m.get("outcomePrices")}
    return out


def maybe_write(rows: list[dict], resolutions: dict) -> int:
    """Persist paper PnL onto resolved rows (settled=true). Returns write count."""
    n = 0
    for r in rows:
        if r.get("settled"):
            continue
        meta = r.get("meta") or {}
        ca = meta.get("copy_ask")
        if ca is None:
            continue
        slug = meta.get("slug") or str(r.get("market_slug") or "").split("|", 1)[0]
        res = resolutions.get(slug)
        if not res:
            continue
        won = ws.resolution_won(res.get("outcomes"), res.get("outcomePrices"),
                                meta.get("outcome_name"))
        if won is None:
            continue
        pnl = ws.paper_pnl_at_copy(r.get("outcome"), meta.get("size") or 0, ca, won)
        if pnl is None:
            continue
        # realized_yes semantics for copy rows: did the NAMED outcome win?
        st = track.mark_settled(int(r["id"]), bool(won), float(pnl), meta=meta)
        if st and st < 300:
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--settle", action="store_true",
                    help="resolve via gamma-api and score paper PnL at copy_ask")
    ap.add_argument("--write", action="store_true",
                    help="with --settle, write settled/pnl back to Supabase")
    args = ap.parse_args()
    if not URL or not KEY:
        sys.exit("set SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_KEY)")

    rows = fetch_whale_rows(args.limit)
    lag = ws.lag_cost_summary(rows)
    print("=== WHALE-SCOUT PAPER SCORE (lag) ===")
    for k, v in lag.items():
        print(f"  {k:20} {v}")

    if not args.settle:
        print("\n(pass --settle to attempt gamma resolution + paper PnL)")
        return

    resolutions = build_resolutions(rows)
    scored = ws.score_settled_rows(rows, resolutions)
    print("\n=== WHALE-SCOUT PAPER SCORE (settlement @ copy_ask) ===")
    print(f"  gamma_resolved_slugs {len(resolutions)}")
    for k, v in scored.items():
        print(f"  {k:20} {v}")
    if args.write:
        n = maybe_write(rows, resolutions)
        print(f"  wrote_settled        {n}")
    else:
        print("  (pass --write to persist settled/pnl)")


if __name__ == "__main__":
    main()
