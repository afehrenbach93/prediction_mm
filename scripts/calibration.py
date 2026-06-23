"""
Calibration read — how good are the recorded predictions, per model?

Read-only. Pulls SETTLED rows from Supabase and reports, per model:
  - N, hit rate, mean predicted prob (base rate sanity)
  - Brier score (mean (prob - outcome)^2; lower is better, 0.25 = coin flip)
  - a reliability table (predicted-prob bins vs realized frequency)
  - realized P&L where a market price was recorded (buying YES at ask)

Run on the worker (US egress) or anywhere with SUPABASE_URL/SUPABASE_ANON_KEY:
  SUPABASE_URL=... SUPABASE_ANON_KEY=... python scripts/calibration.py [model]
This is the mid-week / end-of-week instrument behind the go/no-go decision.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

URL = os.getenv("SUPABASE_URL", "")
KEY = os.getenv("SUPABASE_ANON_KEY", "")


def fetch_settled(model: str | None) -> list[dict]:
    q = {"select": "model,sport,model_prob,realized_yes,pnl,market_ask",
         "settled": "is.true", "limit": "10000"}
    if model:
        q["model"] = f"eq.{model}"
    req = urllib.request.Request(
        f"{URL}/rest/v1/model_predictions?{urllib.parse.urlencode(q)}",
        headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def brier(rows: list[dict]) -> float:
    pts = [(r["model_prob"], 1.0 if r["realized_yes"] else 0.0)
           for r in rows if r.get("model_prob") is not None]
    return sum((p - o) ** 2 for p, o in pts) / len(pts) if pts else float("nan")


def reliability(rows: list[dict], bins: int = 5) -> list[tuple]:
    """(bin_lo, bin_hi, n, mean_pred, realized_freq) per probability bin."""
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        sel = [r for r in rows if r.get("model_prob") is not None
               and (lo <= r["model_prob"] < hi or (i == bins - 1 and r["model_prob"] == 1.0))]
        if not sel:
            out.append((lo, hi, 0, None, None))
            continue
        mp = sum(r["model_prob"] for r in sel) / len(sel)
        rf = sum(1 for r in sel if r["realized_yes"]) / len(sel)
        out.append((lo, hi, len(sel), mp, rf))
    return out


def report(model: str | None):
    rows = fetch_settled(model)
    if not rows:
        print("no settled rows yet — settlement pass hasn't resolved anything.")
        return
    models = sorted({r["model"] for r in rows})
    for m in models:
        mr = [r for r in rows if r["model"] == m]
        hits = sum(1 for r in mr if r["realized_yes"])
        mean_p = sum(r["model_prob"] for r in mr if r["model_prob"] is not None) / len(mr)
        priced = [r for r in mr if r.get("pnl") is not None]
        pnl = sum(r["pnl"] for r in priced)
        print(f"\n=== {m}  (n={len(mr)}) ===")
        print(f"  hit rate     : {hits/len(mr):.3f}  ({hits}/{len(mr)})")
        print(f"  mean pred p  : {mean_p:.3f}")
        print(f"  Brier        : {brier(mr):.4f}   (0.25 = coin flip; lower better)")
        if priced:
            print(f"  realized P&L : {pnl:+.3f}  over {len(priced)} priced rows "
                  f"(buy-YES-at-ask)")
        print("  reliability (pred bin -> realized freq):")
        for lo, hi, n, mp, rf in reliability(mr):
            if n:
                print(f"    [{lo:.1f},{hi:.1f})  n={n:<4} pred={mp:.2f}  realized={rf:.2f}")


if __name__ == "__main__":
    if not URL or not KEY:
        sys.exit("set SUPABASE_URL and SUPABASE_ANON_KEY")
    report(sys.argv[1] if len(sys.argv) > 1 else None)
