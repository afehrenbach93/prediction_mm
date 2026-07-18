"""
Sweep-scout paper score — settle near-certainty candidates vs venue resolution.

    python scripts/sweep_paper_score.py
    python scripts/sweep_paper_score.py --settle
    python scripts/sweep_paper_score.py --settle --write

Poly US markets may not appear on gamma; settlement uses Poly US get_market when
available, else gamma as fallback. source_gate is always False here — price-shape
alone never yields GO.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

from core import track, sweepscout as ss
from core.polyclient import from_env

URL = os.getenv("SUPABASE_URL", "")
KEY = (os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_SERVICE_KEY") or "")
UA = {"User-Agent": "prediction-mm/sweep-paper-score", "Accept": "application/json"}
GAMMA = "https://gamma-api.polymarket.com"
PAPER_SIZE = float(os.getenv("SWEEP_SCOUT_SIZE", "10"))


def fetch_rows(limit: int) -> list[dict]:
    q = urllib.parse.urlencode({
        "select": "id,market_slug,outcome,market_ask,settled,realized_yes,pnl,"
                  "settle_date,meta",
        "model": "eq.sweep-scout",
        "order": "id.desc",
        "limit": str(limit),
    })
    req = urllib.request.Request(
        f"{URL.rstrip('/')}/rest/v1/model_predictions?{q}",
        headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def gamma_resolved(slug: str) -> bool | None:
    """True/False if Yes won, None if unknown/unresolved."""
    url = f"{GAMMA}/markets?{urllib.parse.urlencode({'slug': slug})}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                    timeout=20) as r:
            raw = json.loads(r.read())
    except Exception:
        return None
    m = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, dict) else None)
    if not m:
        return None
    closed = bool(m.get("closed")) or str(
        m.get("umaResolutionStatus") or "").lower() == "resolved"
    if not closed:
        return None
    prices = m.get("outcomePrices")
    outcomes = m.get("outcomes")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except Exception:
            return None
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except Exception:
            return None
    if not prices or not outcomes:
        return None
    try:
        # Yes = first outcome conventionally
        idx = 0
        for i, o in enumerate(outcomes):
            if str(o).lower() in ("yes", "y"):
                idx = i
                break
        return float(prices[idx]) >= 0.5
    except Exception:
        return None


def poly_us_resolved(client, slug: str) -> bool | None:
    """Best-effort: closed market with last trade / outcome if present."""
    try:
        mk = client.get_market(slug)
    except Exception:
        return None
    if not mk:
        return None
    if not mk.get("closed"):
        return None
    # Polymarket US shapes vary; try common fields
    for key in ("winningOutcome", "outcome", "resolvedOutcome"):
        v = mk.get(key)
        if v is None:
            continue
        s = str(v).lower()
        if s in ("yes", "y", "1", "true"):
            return True
        if s in ("no", "n", "0", "false"):
            return False
    return None


def score(rows, resolutions: dict) -> dict:
    n, hits, pnl = 0, 0, 0.0
    for r in rows:
        meta = r.get("meta") or {}
        slug = meta.get("slug") or str(r.get("market_slug") or "").split("|", 1)[0]
        won = resolutions.get(slug)
        if won is None:
            continue
        ask = r.get("market_ask")
        if ask is None:
            ask = meta.get("ask")
        size = meta.get("ask_size") or PAPER_SIZE
        p = ss.paper_pnl_buy(ask, size, bool(won))
        if p is None:
            continue
        n += 1
        pnl += p
        if won:
            hits += 1
    return {
        "n_scored": n,
        "hits": hits,
        "hit_rate": (hits / n) if n else None,
        "paper_pnl": round(pnl, 4) if n else None,
    }


def maybe_write(rows, resolutions) -> int:
    n = 0
    for r in rows:
        if r.get("settled"):
            continue
        meta = r.get("meta") or {}
        slug = meta.get("slug") or str(r.get("market_slug") or "").split("|", 1)[0]
        won = resolutions.get(slug)
        if won is None:
            continue
        ask = r.get("market_ask")
        if ask is None:
            ask = meta.get("ask")
        size = meta.get("ask_size") or PAPER_SIZE
        p = ss.paper_pnl_buy(ask, size, bool(won))
        if p is None:
            continue
        st = track.mark_settled(int(r["id"]), bool(won), float(p), meta=meta)
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
    asks = []
    for r in rows:
        a = r.get("market_ask")
        if a is None:
            a = (r.get("meta") or {}).get("ask")
        if a is not None:
            asks.append(float(a))
    dist = ss.summarize_asks(asks, min_ask=0.97)
    print("=== SWEEP-SCOUT PAPER SCORE (candidates) ===")
    print(f"  rows                 {len(rows)}")
    for k, v in dist.items():
        print(f"  {k:20} {v}")

    if not args.settle:
        v, reason = ss.go_kill(0, None, None, source_gate=False)
        print(f"\nverdict: {v} — {reason}")
        print("(pass --settle once markets resolve)")
        return

    client = from_env()
    resolutions = {}
    slugs = []
    for r in rows:
        meta = r.get("meta") or {}
        slug = meta.get("slug") or str(r.get("market_slug") or "").split("|", 1)[0]
        if slug and slug not in slugs:
            slugs.append(slug)
    for slug in slugs:
        won = poly_us_resolved(client, slug)
        if won is None:
            won = gamma_resolved(slug)
        if won is not None:
            resolutions[slug] = won

    scored = score(rows, resolutions)
    print("\n=== SWEEP-SCOUT PAPER SCORE (settlement) ===")
    print(f"  resolved_slugs       {len(resolutions)}")
    for k, v in scored.items():
        print(f"  {k:20} {v}")
    v, reason = ss.go_kill(scored["n_scored"], scored.get("hit_rate"),
                           scored.get("paper_pnl"), source_gate=False)
    print(f"\nverdict: {v} — {reason}")
    if args.write:
        n = maybe_write(rows, resolutions)
        print(f"wrote_settled: {n}")


if __name__ == "__main__":
    main()
