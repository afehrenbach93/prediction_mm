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
import urllib.request

TABLE = "model_predictions"


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
        f"{url}/rest/v1/{TABLE}",
        data=json.dumps(rows).encode(),
        method="POST",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"},
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
