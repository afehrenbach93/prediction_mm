"""
Prediction tracker — durable record of model predictions for ALL models/sports.

Writes rows to Supabase `model_predictions` via PostgREST (stdlib only). The schema
is model-agnostic (model/sport columns + a `meta` jsonb), so weather and every sport
share one pipeline and new sports need no code change here. A later settlement pass
fills `settled`/`realized_yes`/`pnl`; calibration + net-of-fee edge are then computed
from the accumulated history — the data-backed go/no-go for going live.
"""
import json
import os
import urllib.parse
import urllib.request

TABLE = "model_predictions"
# unique snapshot key — must match the DB unique index. Naming it as the conflict
# target makes PostgREST emit ON CONFLICT (...) DO NOTHING (it otherwise defaults to
# the primary key and a unique-index clash 409s instead of being ignored).
CONFLICT = "model,market_slug,settle_date,run_date"


def _creds():
    return os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_ANON_KEY", "")


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def record_predictions(rows: list[dict]) -> tuple[int, str]:
    """Bulk-insert prediction rows. Each dict matches model_predictions columns
    (model, sport, market_slug, outcome, model_prob, market_bid, market_ask, edge,
    liquid, settle_date, meta). Returns (http_status, note). No-op without creds."""
    url, key = _creds()
    if not url or not key:
        return 0, "no supabase creds"
    if not rows:
        return 0, "no rows"
    req = urllib.request.Request(
        f"{url}/rest/v1/{TABLE}?on_conflict={CONFLICT}",
        data=json.dumps(rows).encode(),
        method="POST",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 # idempotent: a unique (model, market_slug, settle_date, run_date)
                 # index makes re-runs (e.g. deploy overlap) no-op instead of dupe.
                 "Prefer": "return=minimal,resolution=ignore-duplicates"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, f"inserted {len(rows)}"
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode()[:200]
        except Exception:
            return e.code, "(error)"
    except Exception as e:
        return -1, str(e)[:200]


def heartbeat(mode: str, status: str, detail: dict | None = None) -> int:
    """Write the worker's live status to poly_status (single row id=1) so the app's
    Overview shows it. Returns http status; best-effort (never raises)."""
    url, key = _creds()
    if not url or not key:
        return 0
    body = json.dumps({"mode": mode, "status": status,
                       "last_seen": _now(), "detail": detail or {}, "updated": _now()}).encode()
    req = urllib.request.Request(
        f"{url}/rest/v1/poly_status?id=eq.1", data=body, method="PATCH",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except Exception:
        return -1


def get_desired_mode() -> str | None:
    """Read the operator's desired mode from poly_control (the app's kill switch /
    mode control). Returns the string or None if unavailable."""
    c = get_control()
    return c.get("desired_mode") if c else None


def get_control() -> dict:
    """Read the full control row (desired_mode, budget, live_until) from poly_control.
    Returns {} if unavailable."""
    url, key = _creds()
    if not url or not key:
        return {}
    req = urllib.request.Request(
        f"{url}/rest/v1/poly_control?id=eq.1&select=desired_mode,budget,live_until,"
        f"wx_taker,mlb_taker,wx_budget,mlb_budget,mlb_edge,clear_halts",
        headers={"apikey": key, "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            rows = json.loads(r.read())
            return rows[0] if rows else {}
    except Exception:
        return {}


def set_desired_mode(mode: str) -> int:
    """Write desired_mode back to poly_control (e.g. the worker auto-reverting 'live'
    -> 'track' when the live window expires). Returns http status."""
    url, key = _creds()
    if not url or not key:
        return 0
    body = json.dumps({"desired_mode": mode, "updated": _now()}).encode()
    req = urllib.request.Request(
        f"{url}/rest/v1/poly_control?id=eq.1", data=body, method="PATCH",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except Exception:
        return -1


def fetch_users() -> list[dict]:
    """Rows from poly_users — the Polymarket accounts the shared worker trades for.
    Each row: email, name, key_env/secret_env (env-var NAMES holding that user's keys)
    and `armed` (that user's kill switch: false = no orders reach THEIR account).
    Returns [] without creds/on error (caller falls back to the base env account)."""
    url, key = _creds()
    if not url or not key:
        return []
    req = urllib.request.Request(
        f"{url}/rest/v1/poly_users?select=email,name,key_env,secret_env,armed,"
        f"pm_key_enc,pm_secret_enc",
        headers={"apikey": key, "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return []


def patch_meta(pred_id: int, meta: dict) -> int:
    """Overwrite one row's meta jsonb (caller merges first — PATCH replaces wholesale)."""
    url, key = _creds()
    if not url or not key:
        return 0
    body = json.dumps({"meta": meta}).encode()
    req = urllib.request.Request(
        f"{url}/rest/v1/{TABLE}?id=eq.{pred_id}", data=body, method="PATCH",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except Exception:
        return -1


def fetch_settled(sport: str, limit: int = 100) -> list[dict]:
    """Recently-settled rows for one sport (market_slug, outcome, realized_yes,
    settle_date) — used to cross-check OUR settlement against the venue's resolution."""
    url, key = _creds()
    if not url or not key:
        return []
    q = urllib.parse.urlencode({
        "select": "id,market_slug,outcome,realized_yes,settle_date,meta",
        "sport": f"eq.{sport}", "settled": "is.true",
        "order": "settle_date.desc", "limit": str(limit),
    })
    req = urllib.request.Request(f"{url}/rest/v1/{TABLE}?{q}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except Exception:
        return []


def set_realized(pred_id: int, realized_yes: bool) -> int:
    """Re-settle one row's realized_yes to the AUTHORITATIVE venue resolution (what
    actually pays). Used to correct weather rows that were settled from raw observations
    to PM's official Climatological-Report outcome. Only touches realized_yes."""
    url, key = _creds()
    if not url or not key:
        return 0
    body = json.dumps({"realized_yes": realized_yes}).encode()
    req = urllib.request.Request(
        f"{url}/rest/v1/{TABLE}?id=eq.{pred_id}", data=body, method="PATCH",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except Exception:
        return -1


def fetch_unsettled(before_date: str, limit: int = 2000) -> list[dict]:
    """Predictions whose settle_date is strictly before `before_date` (ISO) and not
    yet settled — the settlement pass's work queue. Returns [] without creds/on error."""
    url, key = _creds()
    if not url or not key:
        return []
    q = urllib.parse.urlencode({
        "select": "id,model,market_slug,outcome,settle_date,market_ask,meta",
        "settled": "is.null", "settle_date": f"lt.{before_date}",
        "limit": str(limit), "order": "settle_date.asc",
    })
    req = urllib.request.Request(f"{url}/rest/v1/{TABLE}?{q}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except Exception:
        return []


def fetch_rows_for_odds(sport: str, run_date: str, limit: int = 300) -> list[dict]:
    """Today's unsettled rows for one sport that matched a PM market (meta.pm_slug set)
    — the near-game executable-odds refresh queue. Returns [] without creds/on error."""
    url, key = _creds()
    if not url or not key:
        return []
    q = urllib.parse.urlencode({
        "select": "id,model,outcome,model_prob,market_ask,settle_date,run_date,market_slug,meta",
        "sport": f"eq.{sport}", "settled": "is.null",
        "run_date": f"eq.{run_date}", "meta->>pm_slug": "not.is.null",
        "limit": str(limit),
    })
    req = urllib.request.Request(f"{url}/rest/v1/{TABLE}?{q}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except Exception:
        return []


def update_market_odds(pred_id: int, bid, ask, edge, meta: dict) -> int:
    """Overwrite one row's market prices with EXECUTABLE near-game book quotes (the
    morning outcomePrices snapshot is often a stale pre-liquidity print — see the
    anti-informative market-Brier artifact). Caller passes the full merged meta (PATCH
    replaces the jsonb wholesale). Returns http status."""
    url, key = _creds()
    if not url or not key:
        return 0
    body = json.dumps({"market_bid": bid, "market_ask": ask, "edge": edge,
                       "liquid": ask is not None, "meta": meta}).encode()
    req = urllib.request.Request(
        f"{url}/rest/v1/{TABLE}?id=eq.{pred_id}", data=body, method="PATCH",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


def mark_settled(pred_id: int, realized_yes: bool, pnl: float | None) -> int:
    """Write the resolved outcome back to one prediction row. Returns http status."""
    url, key = _creds()
    if not url or not key:
        return 0
    body = json.dumps({"settled": True, "realized_yes": realized_yes, "pnl": pnl}).encode()
    req = urllib.request.Request(
        f"{url}/rest/v1/{TABLE}?id=eq.{pred_id}", data=body, method="PATCH",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1
