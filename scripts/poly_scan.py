"""
Read-only Polymarket US liquidity-incentive assessment. PLACES NO ORDERS.

Scores competition with the US formula:
    Score = discountFactor^(ticks_from_best) * size
(summed over both sides of the book), then estimates capture as
    est_reward = rewardPool * my_score / (my_score + book_score)
for a hypothetical two-sided touch quote sized to `budget`.

This is NOT the global CLOB quadratic / sampling-markets scan — US venue only.

    PYTHONPATH=. python scripts/poly_scan.py
    PYTHONPATH=. python scripts/poly_scan.py 500
    PYTHONPATH=. python scripts/poly_scan.py 500 --csv data/reward_scans/latest.csv
    PYTHONPATH=. python scripts/poly_scan.py --history   # stability report from snapshots
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.polyclient import from_env
from core.rewardscore import score_market

# maker rebate: payment of 0.0125 * C * p * (1-p) per contract traded (you get paid)
MAKER_REBATE_THETA = 0.0125
DEFAULT_SCAN_DIR = Path("data/reward_scans")


def maker_rebate(price: float, contracts: float) -> float:
    return MAKER_REBATE_THETA * contracts * price * (1.0 - price)


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def scan_markets(client, budget: float) -> list[dict]:
    progs = client.get_incentives()
    by_market: dict[str, list] = {}
    for tp in progs:
        by_market.setdefault(tp["marketSlug"], []).append(tp)

    rows = []
    for slug, tps in by_market.items():
        mk = client.get_market(slug)
        if not mk or mk.get("closed"):
            continue
        # Prefer live-period pool/discount; else largest pool among programs
        live = next((t for t in tps if t.get("period") == "live"), None)
        chosen = live or max(tps, key=lambda t: float(t.get("rewardPool") or 0))
        pool = float(chosen.get("rewardPool") or 0)
        disc = float(chosen.get("discountFactor") or 0.3)
        period = chosen.get("period") or ""
        end_date = mk.get("endDate") or ""

        bids, offers = client.get_book(slug)
        if not bids or not offers:
            rows.append({
                "ts": _iso_now(), "slug": slug, "period": period, "pool": pool,
                "discount": disc, "mid": "", "spread": "", "book_score": 0.0,
                "my_score": 0.0, "share": 0.0, "est_reward": 0.0,
                "near_zero": True, "end_date": end_date, "no_book": True,
            })
            continue
        sr = score_market(bids, offers, pool=pool, budget=budget, discount=disc)
        if sr is None:
            continue
        rows.append({
            "ts": _iso_now(), "slug": slug, "period": period, "pool": pool,
            "discount": disc, "mid": round(sr.mid, 4), "spread": sr.spread,
            "book_score": round(sr.book_score, 4), "my_score": round(sr.my_score, 4),
            "share": round(sr.share, 6), "est_reward": round(sr.est_reward, 4),
            "near_zero": sr.near_zero, "end_date": end_date, "no_book": False,
        })
    return rows


CSV_FIELDS = [
    "ts", "slug", "period", "pool", "discount", "mid", "spread",
    "book_score", "my_score", "share", "est_reward", "near_zero",
    "end_date", "no_book",
]


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def append_daily_snapshot(scan_dir: Path, rows: list[dict]):
    """Append today's ranked rows for multi-day stability study."""
    day_path = scan_dir / f"{_day_stamp()}.csv"
    scan_dir.mkdir(parents=True, exist_ok=True)
    write_header = not day_path.exists()
    with open(day_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for r in rows:
            w.writerow(r)
    return day_path


def print_report(rows: list[dict], budget: float):
    booked = [r for r in rows if not r.get("no_book")]
    competed = [r for r in booked if not r["near_zero"]]
    near_zero = [r for r in booked if r["near_zero"]]

    print(f"=== POLYMARKET US REWARD YIELD SCAN  (budget ${budget:.0f}) ===")
    print(f"open reward markets: {len(rows)}  with book: {len(booked)}  "
          f"competed: {len(competed)}  near-zero: {len(near_zero)}\n")

    hdr = (f"{'market':42} {'mid':>5} {'spr':>5} {'pool':>8} {'bookScr':>9} "
           f"{'est$':>8} {'shr%':>6} {'nz':>3}")
    print("--- competed (ranked by est_reward) ---")
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(competed, key=lambda x: -x["est_reward"])[:50]:
        print(f"{r['slug'][:42]:42} {r['mid']:5.3f} {r['spread']*100:4.1f}¢ "
              f"${r['pool']:7,.0f} {r['book_score']:9,.1f} "
              f"${r['est_reward']:7.2f} {r['share']*100:5.1f}%   ")

    if near_zero:
        print("\n--- near-zero competition (advanced tier / high AS risk) ---")
        print(hdr)
        print("-" * len(hdr))
        for r in sorted(near_zero, key=lambda x: -float(x["pool"] or 0))[:30]:
            mid = r["mid"] if r["mid"] != "" else 0.0
            spr = r["spread"] if r["spread"] != "" else 0.0
            print(f"{r['slug'][:42]:42} {mid:5.3f} {spr*100:4.1f}¢ "
                  f"${r['pool']:7,.0f} {r['book_score']:9,.1f} "
                  f"${r['est_reward']:7.2f} {r['share']*100:5.1f}%  NZ")

    if competed:
        top = sorted(competed, key=lambda x: -x["est_reward"])[:20]
        avg_share = sum(r["share"] for r in top) / len(top)
        avg_est = sum(r["est_reward"] for r in top) / len(top)
        print(f"\ntop-20 competed: avg share {avg_share*100:.2f}%  "
              f"avg est_reward ${avg_est:.2f} (pool ceiling × share)")
        print(f"maker rebate on 1,000-ct round trip @ $0.50 ≈ "
              f"${maker_rebate(0.50, 1000):.2f}")
    print("\nNOTE: est_reward uses rewardPool as a ceiling proxy — cadence unknown.")
    print("Adverse selection is NOT subtracted; prefer competed markets for pilots.")


def history_report(scan_dir: Path):
    """Per-slug stability across daily CSV snapshots."""
    files = sorted(scan_dir.glob("????-??-??.csv"))
    if not files:
        print(f"no daily snapshots in {scan_dir}")
        return
    # slug -> list of (day, est_reward, book_score, near_zero)
    series: dict[str, list] = {}
    for fp in files:
        day = fp.stem
        with open(fp, newline="") as f:
            for row in csv.DictReader(f):
                slug = row["slug"]
                series.setdefault(slug, []).append({
                    "day": day,
                    "est": float(row.get("est_reward") or 0),
                    "book": float(row.get("book_score") or 0),
                    "nz": row.get("near_zero", "").lower() in ("true", "1", "yes"),
                })

    print(f"=== STABILITY REPORT  ({len(files)} days, {len(series)} markets) ===")
    hdr = f"{'market':42} {'days':>4} {'avg_est':>8} {'min_est':>8} {'avg_book':>9} {'nz_days':>7}"
    print(hdr)
    print("-" * len(hdr))
    ranked = []
    for slug, pts in series.items():
        ests = [p["est"] for p in pts]
        books = [p["book"] for p in pts]
        ranked.append((
            slug, len(pts),
            sum(ests) / len(ests), min(ests),
            sum(books) / len(books),
            sum(1 for p in pts if p["nz"]),
        ))
    for slug, n, avg_e, min_e, avg_b, nz in sorted(ranked, key=lambda x: -x[2])[:40]:
        print(f"{slug[:42]:42} {n:4} ${avg_e:7.2f} ${min_e:7.2f} "
              f"{avg_b:9,.1f} {nz:7}")


def main():
    ap = argparse.ArgumentParser(description="Polymarket US reward yield scan")
    ap.add_argument("budget", nargs="?", type=float, default=400.0)
    ap.add_argument("--csv", type=str, default="",
                    help="Write ranked CSV to this path")
    ap.add_argument("--snapshot", action="store_true", default=True,
                    help="Append daily snapshot under data/reward_scans/ (default)")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="Skip daily snapshot append")
    ap.add_argument("--history", action="store_true",
                    help="Print stability report from daily snapshots and exit")
    ap.add_argument("--scan-dir", type=str, default=str(DEFAULT_SCAN_DIR))
    args = ap.parse_args()

    scan_dir = Path(args.scan_dir)
    if args.history:
        history_report(scan_dir)
        return

    client = from_env()
    rows = scan_markets(client, args.budget)
    print_report(rows, args.budget)

    # always write latest.csv for tooling
    latest = scan_dir / "latest.csv"
    write_csv(latest, sorted(rows, key=lambda r: -float(r.get("est_reward") or 0)))
    print(f"\nwrote {latest}")

    if args.csv:
        write_csv(Path(args.csv), rows)
        print(f"wrote {args.csv}")

    if not args.no_snapshot:
        day_path = append_daily_snapshot(scan_dir, rows)
        print(f"appended daily snapshot {day_path}")


if __name__ == "__main__":
    # allow legacy: `python scripts/poly_scan.py 489`
    if len(sys.argv) == 2 and sys.argv[1].replace(".", "", 1).isdigit():
        sys.argv = [sys.argv[0], sys.argv[1]]
    main()
