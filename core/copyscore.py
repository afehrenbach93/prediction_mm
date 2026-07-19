"""
Shared paper-score + gamma settlement for whale-scout / flow-scout rows.

Both observe offshore .com tape; settlement reads gamma closed markets.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from core import track, whalescout as ws

UA = {"User-Agent": "prediction-mm/copy-score", "Accept": "application/json"}
GAMMA = "https://gamma-api.polymarket.com"


def supabase_creds() -> tuple[str, str]:
    return os.getenv("SUPABASE_URL", ""), (
        os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_SERVICE_KEY") or "")


def fetch_model_rows(model: str, limit: int = 2000,
                     *, oldest_first: bool = False) -> list[dict]:
    """Page through PostgREST (default max ~1000/req) until `limit` rows."""
    url, key = supabase_creds()
    if not url or not key:
        return []
    out: list[dict] = []
    page = 1000
    offset = 0
    order = "id.asc" if oldest_first else "id.desc"
    while len(out) < limit:
        n = min(page, limit - len(out))
        q = urllib.parse.urlencode({
            "select": "id,market_slug,outcome,market_ask,settled,realized_yes,pnl,"
                      "settle_date,meta",
            "model": f"eq.{model}",
            "order": order,
            "limit": str(n),
            "offset": str(offset),
        })
        req = urllib.request.Request(
            f"{url.rstrip('/')}/rest/v1/model_predictions?{q}",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Range": f"{offset}-{offset + n - 1}"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                batch = json.loads(r.read())
        except Exception:
            break
        if not batch:
            break
        out.extend(batch)
        if len(batch) < n:
            break
        offset += len(batch)
    return out[:limit]


def _http_json(url: str):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                    timeout=20) as r:
            return json.loads(r.read())
    except Exception:
        return None


def gamma_market(slug: str) -> dict | None:
    """Resolve a market dict from gamma (direct slug, else parent event)."""
    if not slug:
        return None
    raw = _http_json(f"{GAMMA}/markets?{urllib.parse.urlencode({'slug': slug})}")
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    if isinstance(raw, dict) and raw.get("slug"):
        return raw
    # Sports child slugs often 404 on /markets?slug= but live under the event.
    # Walk parent prefixes: a-b-c-d → try events?slug=a-b-c, a-b, …
    parts = slug.split("-")
    for cut in range(len(parts) - 1, 1, -1):
        parent = "-".join(parts[:cut])
        ev = _http_json(f"{GAMMA}/events?{urllib.parse.urlencode({'slug': parent})}")
        if not (isinstance(ev, list) and ev):
            continue
        for m in (ev[0].get("markets") or []):
            if isinstance(m, dict) and m.get("slug") == slug:
                return m
    return None


def gamma_market_by_token(token_id: str) -> dict | None:
    if not token_id:
        return None
    raw = _http_json(
        f"{GAMMA}/markets?{urllib.parse.urlencode({'clob_token_ids': token_id})}")
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    return None


def build_resolutions(rows: list[dict], *, max_slugs: int = 400) -> dict:
    slugs = []
    token_by_slug: dict[str, str] = {}
    for r in rows:
        meta = r.get("meta") or {}
        slug = meta.get("slug") or str(r.get("market_slug") or "").split("|", 1)[0]
        if slug and slug not in slugs:
            slugs.append(slug)
            if meta.get("asset"):
                token_by_slug[slug] = str(meta["asset"])
        if len(slugs) >= max_slugs:
            break
    out = {}
    for slug in slugs:
        m = gamma_market(slug) or gamma_market_by_token(token_by_slug.get(slug, ""))
        if not m:
            continue
        closed = bool(m.get("closed")) or str(
            m.get("umaResolutionStatus") or "").lower() == "resolved"
        if not closed:
            continue
        out[slug] = {"outcomes": m.get("outcomes"),
                     "outcomePrices": m.get("outcomePrices")}
    return out


def write_settled(rows: list[dict], resolutions: dict) -> int:
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


def score_model(model: str, *, limit: int = 2000, settle: bool = True,
                write: bool = False, max_slugs: int = 400) -> dict:
    """One-shot score dict for whale-scout or flow-scout.

    Lag summary uses newest rows; settlement prefers oldest ( likelier resolved ).
    """
    lag_rows = fetch_model_rows(model, min(limit, 2000), oldest_first=False)
    lag = ws.lag_cost_summary(lag_rows)
    out = {"model": model, "n_rows": len(lag_rows), "lag": lag,
           "resolutions": 0, "scored": {}, "wrote": 0}
    if not settle:
        return out
    # oldest first → markets that had time to resolve
    rows = fetch_model_rows(model, limit, oldest_first=True)
    out["n_rows_settle"] = len(rows)
    resolutions = build_resolutions(rows, max_slugs=max_slugs)
    out["resolutions"] = len(resolutions)
    out["scored"] = ws.score_settled_rows(rows, resolutions)
    if write:
        out["wrote"] = write_settled(rows, resolutions)
    return out
