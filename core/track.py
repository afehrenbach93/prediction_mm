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
