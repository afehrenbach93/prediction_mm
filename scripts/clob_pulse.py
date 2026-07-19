"""
Regular CLOB market pulse (deep-dive §7.1).

Runs against clob.polymarket.com:
  1) yield scan (sampling-markets + books + quadratic capture)
  2) stability filter → pilot_universe.csv
  3) pulse summary → pulse.json + pulse.md
  4) reward recon section (empty until live)
  5) optional Supabase snapshot (preferred over committing to deploy branch)

    PYTHONPATH=. python3 scripts/clob_pulse.py
    PYTHONPATH=. python3 scripts/clob_pulse.py --budget 500 --top 250
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCAN_DIR = Path("data/clob_scans")


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _run(cmd: list[str]) -> int:
    print("+", " ".join(cmd), flush=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    return subprocess.call(cmd, cwd=str(ROOT), env=env)


def summarize(scan_dir: Path, budget: float) -> dict:
    latest = scan_dir / "latest.csv"
    pilot = scan_dir / "pilot_universe.csv"
    day_files = sorted(scan_dir.glob("????-??-??.csv"))

    rows = []
    if latest.exists():
        with open(latest, newline="") as f:
            rows = list(csv.DictReader(f))

    competed = [r for r in rows if str(r.get("near_zero", "")).lower() not in
                ("true", "1", "yes")]
    near = [r for r in rows if str(r.get("near_zero", "")).lower() in
            ("true", "1", "yes")]

    def top_yield(xs, n=10):
        xs = sorted(xs, key=lambda r: -float(r.get("est_daily") or 0))[:n]
        out = []
        for r in xs:
            out.append({
                "question": (r.get("question") or r.get("slug") or "")[:80],
                "slug": r.get("slug") or "",
                "daily_rate": float(r.get("daily_rate") or 0),
                "qual_notional": float(r.get("qual_notional") or 0),
                "est_daily": float(r.get("est_daily") or 0),
                "yield_pct": float(r.get("yield_pct") or 0),
                "mid": r.get("mid") or "",
                "near_zero": str(r.get("near_zero", "")).lower() in ("true", "1", "yes"),
            })
        return out

    pilot_n = 0
    provisional_n = 0
    if pilot.exists():
        with open(pilot, newline="") as f:
            for r in csv.DictReader(f):
                pilot_n += 1
                if str(r.get("provisional", "")).lower() in ("true", "1", "yes"):
                    provisional_n += 1

    top20 = sorted(competed, key=lambda r: -float(r.get("est_daily") or 0))[:20]
    avg_top20 = (
        sum(float(r.get("yield_pct") or 0) for r in top20) / len(top20)
        if top20 else 0.0
    )

    return {
        "ts": _iso(),
        "domain": "https://clob.polymarket.com",
        "budget_usd": budget,
        "snapshot_days": len(day_files),
        "scored_markets": len(rows),
        "competed": len(competed),
        "near_zero": len(near),
        "pilot_universe": pilot_n,
        "pilot_provisional": provisional_n,
        "top20_competed_avg_yield_pct": round(avg_top20, 4),
        "top10_competed": top_yield(competed, 10),
        "top10_near_zero": top_yield(near, 10),
        "files": {
            "latest": str(latest),
            "pilot_universe": str(pilot),
            "day_csvs": [p.name for p in day_files[-7:]],
        },
    }


def write_pulse_md(path: Path, pulse: dict):
    lines = [
        f"# CLOB pulse — {pulse['ts']}",
        "",
        f"- Domain: `{pulse['domain']}`",
        f"- Budget: ${pulse['budget_usd']:.0f}",
        f"- Snapshot days on disk: {pulse['snapshot_days']}",
        f"- Scored: {pulse['scored_markets']}  competed: {pulse['competed']}  "
        f"near-zero: {pulse['near_zero']}",
        f"- Pilot universe: {pulse['pilot_universe']} "
        f"(provisional: {pulse.get('pilot_provisional', 0)})",
        f"- Top-20 competed avg yield: {pulse['top20_competed_avg_yield_pct']}%/day "
        f"(gross)",
        "",
        "## Top competed",
        "",
        "| # | yield%/d | est$/d | rate | qual$ | market |",
        "|---|----------|--------|------|-------|--------|",
    ]
    for i, r in enumerate(pulse["top10_competed"], 1):
        lines.append(
            f"| {i} | {r['yield_pct']:.2f} | {r['est_daily']:.2f} | "
            f"{r['daily_rate']:.0f} | {r['qual_notional']:.0f} | "
            f"{r['question'][:56]} |"
        )
    lines += ["", "## Near-zero (excluded from pilot)", ""]
    for i, r in enumerate(pulse["top10_near_zero"], 1):
        lines.append(
            f"| {i} | {r['yield_pct']:.2f} | {r['est_daily']:.2f} | "
            f"{r['question'][:56]} |"
        )
    path.write_text("\n".join(lines) + "\n")


def push_supabase(scan_dir: Path, pulse: dict) -> bool:
    """Persist pulse + pilot to Supabase so deploy-branch commits are unnecessary."""
    try:
        from core.supabase_clob import SupabaseClob
    except Exception:
        return False
    sb = SupabaseClob()
    if not sb.enabled:
        print("supabase not configured — skip pulse snapshot push", flush=True)
        return False
    pilot_rows = []
    pilot = scan_dir / "pilot_universe.csv"
    if pilot.exists():
        with open(pilot, newline="") as f:
            pilot_rows = list(csv.DictReader(f))
    payload = {
        "pulse": pulse,
        "pilot_universe": pilot_rows,
    }
    st, _ = sb.insert("clob_pulse_snapshots", {
        "day": _day(),
        "payload_json": payload,
    })
    ok = st in (200, 201)
    print(f"supabase clob_pulse_snapshots insert status={st}", flush=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="CLOB regular market pulse")
    ap.add_argument("--budget", type=float, default=500.0)
    ap.add_argument("--top", type=int, default=250)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--min-days", type=int, default=5)
    ap.add_argument("--min-yield", type=float, default=3.0)
    ap.add_argument("--scan-dir", default=str(DEFAULT_SCAN_DIR))
    ap.add_argument("--skip-scan", action="store_true",
                    help="Only re-summarize existing CSVs")
    ap.add_argument("--skip-recon", action="store_true")
    ap.add_argument("--no-supabase", action="store_true")
    args = ap.parse_args()

    scan_dir = Path(args.scan_dir)
    scan_dir.mkdir(parents=True, exist_ok=True)
    py = sys.executable

    if not args.skip_scan:
        rc = _run([
            py, "scripts/clob_yield_scan.py",
            "--budget", str(args.budget),
            "--top", str(args.top),
            "--workers", str(args.workers),
            "--scan-dir", str(scan_dir),
        ])
        if rc != 0:
            print(f"scan failed rc={rc}", file=sys.stderr)
            return rc

    rc = _run([
        py, "scripts/clob_stability.py",
        "--scan-dir", str(scan_dir),
        "--min-days", str(args.min_days),
        "--min-yield", str(args.min_yield),
        "--top", "50",
    ])
    if rc != 0:
        print(f"stability failed rc={rc}", file=sys.stderr)
        return rc

    pulse = summarize(scan_dir, args.budget)
    (scan_dir / "pulse.json").write_text(json.dumps(pulse, indent=2) + "\n")
    write_pulse_md(scan_dir / "pulse.md", pulse)

    if not args.skip_recon:
        _run([
            py, "scripts/clob_reward_recon.py",
            "--out-md", str(scan_dir / "pulse.md"),
            "--json-out", str(scan_dir / "reward_recon.json"),
        ])

    if not args.no_supabase:
        push_supabase(scan_dir, pulse)

    print(json.dumps({
        "ts": pulse["ts"],
        "scored": pulse["scored_markets"],
        "competed": pulse["competed"],
        "near_zero": pulse["near_zero"],
        "pilot": pulse["pilot_universe"],
        "provisional": pulse.get("pilot_provisional", 0),
        "top20_avg_yield_pct": pulse["top20_competed_avg_yield_pct"],
    }, indent=2))
    print(f"wrote {scan_dir / 'pulse.json'}")
    print(f"wrote {scan_dir / 'pulse.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
