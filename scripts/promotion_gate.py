"""
Promotion gate — the standing rule for moving a shadow model to a live probe.

A model is ELIGIBLE for a small armed-gated live probe only when, on rows settled
against PM's AUTHORITATIVE resolution with EXECUTABLE (near-game book) odds:
  1. n >= MIN_N settled rows with executable odds (meta.odds_at set),
  2. model Brier < market Brier on those same rows, and
  3. the threshold sim is positive at executable prices: buy YES at the ask when
     model - ask >= EDGE_MIN; sell at the bid when bid - model >= EDGE_MIN.

`gate_report(rows)` is pure (unit-tested); `main` fetches from Supabase with the
worker's env creds and prints the per-model report. Read-only.

Run on the worker: python scripts/promotion_gate.py
"""
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MIN_N = 100
EDGE_MIN = 0.05


def gate_report(rows: list[dict]) -> dict:
    """Per-model gate metrics from settled rows. Only rows with meta.odds_at (executable
    book quotes) count toward the gate; snapshot-priced rows are reported separately.
    Returns {model: {n_exec, brier_model, brier_market, n_bets, sim_pnl, eligible, why}}."""
    by_model: dict[str, dict] = {}
    for r in rows:
        m = by_model.setdefault(r.get("model", "?"), {
            "n_exec": 0, "n_snap": 0, "se_model": 0.0, "se_market": 0.0,
            "n_bets": 0, "sim_pnl": 0.0})
        if not r.get("settled") or r.get("realized_yes") is None:
            continue
        ask, bid = r.get("market_ask"), r.get("market_bid")
        prob = r.get("model_prob")
        if ask is None or prob is None:
            continue
        if not (r.get("meta") or {}).get("odds_at"):
            m["n_snap"] += 1                      # snapshot print — not gate evidence
            continue
        y = 1.0 if r["realized_yes"] else 0.0
        m["n_exec"] += 1
        m["se_model"] += (prob - y) ** 2
        m["se_market"] += (ask - y) ** 2
        if prob - ask >= EDGE_MIN:                # buy YES at the ask
            m["n_bets"] += 1
            m["sim_pnl"] += y * (1 - ask) - (1 - y) * ask
        elif bid is not None and bid - prob >= EDGE_MIN:   # sell YES at the bid
            m["n_bets"] += 1
            m["sim_pnl"] += (1 - y) * bid - y * (1 - bid)
    out = {}
    for model, m in by_model.items():
        n = m["n_exec"]
        bm = round(m["se_model"] / n, 4) if n else None
        bk = round(m["se_market"] / n, 4) if n else None
        checks = {
            f"n>={MIN_N}": n >= MIN_N,
            "brier<market": (bm is not None and bk is not None and bm < bk),
            "sim>0": (m["n_bets"] > 0 and m["sim_pnl"] > 0),
        }
        out[model] = {
            "n_exec": n, "n_snap": m["n_snap"], "brier_model": bm, "brier_market": bk,
            "n_bets": m["n_bets"], "sim_pnl": round(m["sim_pnl"], 2),
            "eligible": all(checks.values()),
            "why": ", ".join(f"{k}={'Y' if v else 'n'}" for k, v in checks.items()),
        }
    return out


def _fetch_settled_rows(limit: int = 5000) -> list[dict]:
    url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_ANON_KEY", "")
    if not url or not key:
        print("no SUPABASE_URL/SUPABASE_ANON_KEY in env")
        return []
    q = urllib.parse.urlencode({
        "select": "model,model_prob,market_bid,market_ask,settled,realized_yes,meta",
        "settled": "is.true", "limit": str(limit), "order": "settle_date.desc",
    })
    req = urllib.request.Request(f"{url}/rest/v1/model_predictions?{q}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    rows = _fetch_settled_rows()
    print(f"promotion gate over {len(rows)} settled rows "
          f"(gate: n>={MIN_N} executable, brier<market, sim>0 at >={EDGE_MIN} edge)")
    for model, g in sorted(gate_report(rows).items()):
        flag = "ELIGIBLE " if g["eligible"] else "not yet  "
        print(f"  {flag} {model:12s} exec={g['n_exec']:4d} snap-only={g['n_snap']:4d} "
              f"brier={g['brier_model']} vs mkt={g['brier_market']} "
              f"bets={g['n_bets']} sim_pnl=${g['sim_pnl']:+.2f}  [{g['why']}]")


if __name__ == "__main__":
    main()
