"""
Scale gate (deep-dive §7.6).

Only increase size if realized net yield > 50% of estimated gross over the
window in data/clob_logs/pnl_daily.csv.

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

DEFAULT_LOG = Path("data/clob_logs/pnl_daily.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pnl-csv", default=str(DEFAULT_LOG))
    ap.add_argument("--min-days", type=int, default=14)
    ap.add_argument("--threshold", type=float, default=0.50)
    args = ap.parse_args()

    path = Path(args.pnl_csv)
    if not path.exists():
        print(f"FAIL: no pnl file at {path} (need pilot data first)")
        return 2

    rows = list(csv.DictReader(open(path)))
    # last N distinct days
    by_day = {}
    for r in rows:
        by_day[r["day"]] = r
    days = sorted(by_day)[-args.min_days:]
    if len(days) < args.min_days:
        print(f"FAIL: only {len(days)}/{args.min_days} days of pnl")
        return 2

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
        print("FAIL: no positive est_gross rows")
        return 2
    avg = sum(ratios) / len(ratios)
    print(f"days={len(days)} avg_net_vs_gross={avg:.3f} "
          f"threshold={args.threshold} sum_net=${sum(nets):.2f}")
    if avg > args.threshold:
        print("PASS: scale-up permitted under gate rule")
        return 0
    print("FAIL: do not increase size")
    return 1


if __name__ == "__main__":
    sys.exit(main())
