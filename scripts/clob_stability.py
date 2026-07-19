"""
Stability study filter (deep-dive §7.1).

Reads daily CSVs under data/clob_scans/, keeps markets whose competed yield
persists across days. Writes pilot_universe.csv for clob_runner.

Rows backed by fewer than 5 daily snapshots are marked provisional: true
(even when --min-days is lower during early accrual).

    PYTHONPATH=. python3 scripts/clob_stability.py
    PYTHONPATH=. python3 scripts/clob_stability.py --min-days 5 --min-yield 3
"""
from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path

DEFAULT_SCAN_DIR = Path("data/clob_scans")
PROVISIONAL_DAYS = 5
OUT_FIELDS = [
    "slug", "condition_id", "token_id", "question", "days",
    "avg_yield_pct", "min_yield_pct", "avg_est_daily", "avg_qual_notional",
    "avg_daily_rate", "near_zero_days", "end_date", "max_spread", "min_size",
    "provisional",
]


def load_series(scan_dir: Path) -> dict[str, list[dict]]:
    series: dict[str, list[dict]] = {}
    for fp in sorted(scan_dir.glob("????-??-??.csv")):
        with open(fp, newline="") as f:
            for row in csv.DictReader(f):
                key = row.get("condition_id") or row.get("slug")
                if not key:
                    continue
                series.setdefault(key, []).append({
                    "day": fp.stem,
                    "slug": row.get("slug") or "",
                    "condition_id": row.get("condition_id") or "",
                    "token_id": row.get("token_id") or "",
                    "question": row.get("question") or "",
                    "yld": float(row.get("yield_pct") or 0),
                    "est": float(row.get("est_daily") or 0),
                    "qual": float(row.get("qual_notional") or 0),
                    "rate": float(row.get("daily_rate") or 0),
                    "nz": str(row.get("near_zero", "")).lower() in ("true", "1", "yes"),
                    "end_date": row.get("end_date") or "",
                    "max_spread": row.get("max_spread") or "",
                    "min_size": row.get("min_size") or "",
                })
    return series


def select_persistent(series: dict[str, list[dict]], min_days: int,
                      min_yield: float, max_nz_days: int,
                      require_competed: bool,
                      provisional_days: int = PROVISIONAL_DAYS) -> list[dict]:
    out = []
    for key, pts in series.items():
        # one row per calendar day (last snapshot that day)
        by_day = {}
        for p in pts:
            by_day[p["day"]] = p
        days = sorted(by_day)
        if len(days) < min_days:
            continue
        recent = [by_day[d] for d in days[-min_days:]]
        nz_days = sum(1 for p in recent if p["nz"])
        if require_competed and nz_days > max_nz_days:
            continue
        ylds = [p["yld"] for p in recent]
        if min(ylds) < min_yield:
            continue
        last = recent[-1]
        n_days = len(days)
        out.append({
            "slug": last["slug"],
            "condition_id": last["condition_id"] or key,
            "token_id": last["token_id"],
            "question": last["question"],
            "days": n_days,
            "avg_yield_pct": round(statistics.mean(ylds), 4),
            "min_yield_pct": round(min(ylds), 4),
            "avg_est_daily": round(statistics.mean(p["est"] for p in recent), 4),
            "avg_qual_notional": round(statistics.mean(p["qual"] for p in recent), 2),
            "avg_daily_rate": round(statistics.mean(p["rate"] for p in recent), 2),
            "near_zero_days": nz_days,
            "end_date": last["end_date"],
            "max_spread": last["max_spread"],
            "min_size": last["min_size"],
            "provisional": n_days < provisional_days,
        })
    out.sort(key=lambda r: -r["avg_yield_pct"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-dir", default=str(DEFAULT_SCAN_DIR))
    ap.add_argument("--min-days", type=int, default=5,
                    help="Require this many daily snapshots (default 5)")
    ap.add_argument("--min-yield", type=float, default=3.0,
                    help="Min yield %%/day on every required day")
    ap.add_argument("--max-nz-days", type=int, default=0)
    ap.add_argument("--allow-near-zero", action="store_true",
                    help="Advanced tier — NOT for pilot")
    ap.add_argument("--out", default="",
                    help="Output path (default: scan-dir/pilot_universe.csv)")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--provisional-days", type=int, default=PROVISIONAL_DAYS,
                    help="Mark provisional:true when snapshot days < this")
    args = ap.parse_args()

    scan_dir = Path(args.scan_dir)
    series = load_series(scan_dir)
    if not series:
        print(f"no snapshots in {scan_dir}; run clob_yield_scan.py first")
        return 1
    rows = select_persistent(
        series, args.min_days, args.min_yield, args.max_nz_days,
        require_competed=not args.allow_near_zero,
        provisional_days=args.provisional_days,
    )[: args.top]

    out = Path(args.out) if args.out else scan_dir / "pilot_universe.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        for r in rows:
            row = dict(r)
            row["provisional"] = "true" if r["provisional"] else "false"
            w.writerow(row)

    prov_n = sum(1 for r in rows if r["provisional"])
    print(f"snapshots markets={len(series)}  persistent={len(rows)}  "
          f"provisional={prov_n}  wrote {out}")
    for i, r in enumerate(rows[:20], 1):
        tag = " [provisional]" if r["provisional"] else ""
        print(f"{i:2} {r['avg_yield_pct']:6.2f}% min={r['min_yield_pct']:5.2f}% "
              f"qual=${r['avg_qual_notional']:8,.0f}  {r['question'][:56]}{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
