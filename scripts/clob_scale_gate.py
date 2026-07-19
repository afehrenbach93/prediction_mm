"""
Scale gate (deep-dive §7.6).

Only increase size if realized net yield > 50% of estimated gross over the
window. Source of truth: Supabase clob_daily_pnl (CSV is a convenience dump).

  net = rewards_usd + trading_pnl
  gate PASS if mean(net / est_gross) > 0.50 over >= min_days

    PYTHONPATH=. python3 scripts/clob_scale_gate.py
    PYTHONPATH=. python3 scripts/clob_scale_gate.py --min-days 14 --threshold 0.5
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from core.supabase_clob import SupabaseClob

DEFAULT_LOG = Path("data/clob_logs/pnl_daily.csv")


def rows_from_supabase(limit: int = 60) -> list[dict]:
    sb = SupabaseClob()
    if not sb.enabled:
        return []
    return sb.fetch_daily_pnl(limit=limit)


def rows_from_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def evaluate(rows: list[dict], min_days: int, threshold: float) -> tuple[int, str]:
    by_day: dict[str, dict] = {}
    for r in rows:
        day = str(r.get("day") or "")
        if day:
            by_day[day] = r
    days = sorted(by_day)[-min_days:]
    if len(days) < min_days:
        return 2, f"FAIL: only {len(days)}/{min_days} days of pnl"

    ratios = []
    nets = []
    for d in days:
        r = by_day[d]
        net = float(r.get("net") or 0)
        est = float(r.get("est_gross") or 0)
        nets.append(net)
        if est > 0:
            ratios.append(net / est)
    if not ratios:
        return 2, "FAIL: no positive est_gross rows"
    avg = sum(ratios) / len(ratios)
    msg = (
        f"days={len(days)} avg_net_vs_gross={avg:.3f} "
        f"threshold={threshold} sum_net=${sum(nets):.2f}"
    )
    if avg > threshold:
        return 0, msg + "\nPASS: scale-up permitted under gate rule"
    return 1, msg + "\nFAIL: do not increase size"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pnl-csv", default=str(DEFAULT_LOG),
                    help="Fallback CSV if Supabase unavailable")
    ap.add_argument("--min-days", type=int, default=14)
    ap.add_argument("--threshold", type=float, default=0.50)
    ap.add_argument("--csv-only", action="store_true",
                    help="Skip Supabase (tests / offline)")
    args = ap.parse_args()

    source = "supabase"
    rows = [] if args.csv_only else rows_from_supabase(limit=max(60, args.min_days * 2))
    if not rows:
        source = "csv"
        rows = rows_from_csv(Path(args.pnl_csv))
    if not rows:
        print("FAIL: no pnl in Supabase clob_daily_pnl and no CSV fallback "
              f"at {args.pnl_csv}")
        return 2

    print(f"source={source} rows={len(rows)}")
    code, msg = evaluate(rows, args.min_days, args.threshold)
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main())
