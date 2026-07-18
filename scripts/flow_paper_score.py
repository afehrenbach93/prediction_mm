"""
Flow-scout paper score — lag + optional settlement PnL (same path as whale-scout).

    python scripts/flow_paper_score.py
    python scripts/flow_paper_score.py --settle
    python scripts/flow_paper_score.py --settle --write
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

from core import track, whalescout as ws, flowscout as fs

URL = os.getenv("SUPABASE_URL", "")
KEY = (os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_SERVICE_KEY") or "")
UA = {"User-Agent": "prediction-mm/flow-paper-score", "Accept": "application/json"}
GAMMA = "https://gamma-api.polymarket.com"


def fetch_rows(limit: int) -> list[dict]:
    q = urllib.parse.urlencode({
        "select": "id,market_slug,outcome,market_ask,settled,realized_yes,pnl,"
                  "settle_date,meta",
        "model": "eq.flow-scout",
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
        closed = bool(m.get("closed")) or str(
            m.get("umaResolutionStatus") or "").lower() == "resolved"
        if not closed:
            continue
        out[slug] = {"outcomes": m.get("outcomes"),
                     "outcomePrices": m.get("outcomePrices")}
    return out


def maybe_write(rows, resolutions) -> int:
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
        st = track.mark_settled(int(r["id"]), bool(won), float(pnl), meta=meta)
        if st and st < 300:
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if not URL or not KEY:
        sys.exit("set SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_KEY)")

    rows = fetch_rows(args.limit)
    lag = ws.lag_cost_summary(rows)
    endgame_n = sum(1 for r in rows if (r.get("meta") or {}).get("endgame"))
    print("=== FLOW-SCOUT PAPER SCORE (lag) ===")
    for k, v in lag.items():
        print(f"  {k:20} {v}")
    print(f"  {'endgame_flags':20} {endgame_n}")

    if not args.settle:
        v, reason = fs.go_kill(0, None, None)
        print(f"\nverdict: {v} — {reason}")
        print("(pass --settle once markets resolve)")
        return

    resolutions = build_resolutions(rows)
    scored = ws.score_settled_rows(rows, resolutions)
    print("\n=== FLOW-SCOUT PAPER SCORE (settlement @ copy_ask) ===")
    print(f"  gamma_resolved_slugs {len(resolutions)}")
    for k, v in scored.items():
        print(f"  {k:20} {v}")
    # endgame subset
    eg = [r for r in rows if (r.get("meta") or {}).get("endgame")]
    eg_s = ws.score_settled_rows(eg, resolutions)
    print("\n=== endgame subset ===")
    for k, v in eg_s.items():
        print(f"  {k:20} {v}")
    v, reason = fs.go_kill(scored["n_scored"], scored.get("hit_rate"),
                           scored.get("paper_pnl"))
    print(f"\nverdict: {v} — {reason}")
    if args.write:
        n = maybe_write(rows, resolutions)
        print(f"wrote_settled: {n}")


if __name__ == "__main__":
    main()
