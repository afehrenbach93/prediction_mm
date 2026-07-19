"""
Polymarket GLOBAL CLOB reward-yield scan. PLACES NO ORDERS.

Pivoted edge search: pull rewards-eligible markets from
  GET https://clob.polymarket.com/sampling-markets
score competition with the official quadratic weight
  S = ((max_spread - distance)/max_spread)^2 * size
estimate capture
  est $/day = daily_rate * my_score / (my_score + book_score)
for a hypothetical $budget two-sided quote at half max_spread.

This is the deep-dive methodology. Polymarket US (`poly_scan.py`) found no
proven edge — this scanner is the active thesis.

    PYTHONPATH=. python3 scripts/clob_yield_scan.py
    PYTHONPATH=. python3 scripts/clob_yield_scan.py --budget 500 --top 250
    PYTHONPATH=. python3 scripts/clob_yield_scan.py --history
"""
from __future__ import annotations

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from core.clobclient import ClobClient
from core.clobscore import daily_rate, max_spread_cents, min_size, score_market

DEFAULT_SCAN_DIR = Path("data/clob_scans")
CSV_FIELDS = [
    "ts", "question", "slug", "condition_id", "token_id",
    "mid", "spread", "max_spread", "min_size", "daily_rate",
    "qual_notional", "book_score", "my_score", "est_daily", "yield_pct",
    "near_zero", "end_date",
]


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def yes_token(m: dict) -> dict | None:
    for t in m.get("tokens") or []:
        if (t.get("outcome") or "").lower() == "yes":
            return t
    tokens = m.get("tokens") or []
    return tokens[0] if tokens else None


def hours_to_end(end_iso: str, now: datetime) -> float | None:
    if not end_iso:
        return None
    try:
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        return (end - now).total_seconds() / 3600.0
    except Exception:
        return None


def filter_markets(markets: list[dict], min_daily: float, min_days: float,
                   mid_lo: float, mid_hi: float, now: datetime) -> list[dict]:
    out = []
    for m in markets:
        if not m.get("active") or m.get("closed") or not m.get("accepting_orders"):
            continue
        rate = daily_rate(m.get("rewards"))
        if rate < min_daily:
            continue
        hrs = hours_to_end(m.get("end_date_iso") or "", now)
        if hrs is not None and hrs < min_days * 24:
            continue
        tok = yes_token(m)
        if not tok:
            continue
        try:
            mid = float(tok.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if not (mid_lo <= mid <= mid_hi):
            continue
        if max_spread_cents(m.get("rewards")) <= 0:
            continue
        out.append(m)
    out.sort(key=lambda m: -daily_rate(m.get("rewards")))
    return out


def fetch_and_score(client: ClobClient, m: dict, budget: float) -> dict | None:
    tok = yes_token(m)
    if not tok:
        return None
    tid = tok.get("token_id")
    bids, asks = client.get_book(str(tid))
    rewards = m.get("rewards") or {}
    sr = score_market(bids, asks, rewards, budget=budget)
    if sr is None:
        return {
            "ts": _iso_now(),
            "question": (m.get("question") or "")[:120],
            "slug": m.get("market_slug") or "",
            "condition_id": m.get("condition_id") or "",
            "token_id": tid,
            "mid": tok.get("price"),
            "spread": "",
            "max_spread": max_spread_cents(rewards) / 100.0,
            "min_size": min_size(rewards),
            "daily_rate": daily_rate(rewards),
            "qual_notional": 0.0,
            "book_score": 0.0,
            "my_score": 0.0,
            "est_daily": daily_rate(rewards),  # no book → theoretical 100% if you alone
            "yield_pct": daily_rate(rewards) / budget * 100 if budget else 0,
            "near_zero": True,
            "end_date": m.get("end_date_iso") or "",
        }
    return {
        "ts": _iso_now(),
        "question": (m.get("question") or "")[:120],
        "slug": m.get("market_slug") or "",
        "condition_id": m.get("condition_id") or "",
        "token_id": tid,
        "mid": round(sr.mid, 4),
        "spread": sr.spread,
        "max_spread": sr.max_spread_price,
        "min_size": min_size(rewards),
        "daily_rate": daily_rate(rewards),
        "qual_notional": round(sr.qualifying_notional, 2),
        "book_score": round(sr.book_score, 4),
        "my_score": round(sr.my_score, 4),
        "est_daily": round(sr.est_daily, 4),
        "yield_pct": round(sr.yield_pct, 4),
        "near_zero": sr.near_zero,
        "end_date": m.get("end_date_iso") or "",
    }


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def append_daily(scan_dir: Path, rows: list[dict]) -> Path:
    path = scan_dir / f"{_day_stamp()}.csv"
    scan_dir.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def print_tables(rows: list[dict], budget: float, top_n: int):
    competed = [r for r in rows if not r["near_zero"]]
    near = [r for r in rows if r["near_zero"]]
    print(f"=== CLOB REWARD YIELD SCAN  (budget ${budget:.0f}) ===")
    print(f"scored: {len(rows)}  competed: {len(competed)}  near-zero: {len(near)}\n")

    hdr = (f"{'#':>3} {'mid':>5} {'rate':>6} {'qual$':>8} {'est$/d':>8} "
           f"{'yld%':>7} {'spr':>5} {'max':>5}  question")
    print("--- competed (by est $/day) ---")
    print(hdr)
    for i, r in enumerate(sorted(competed, key=lambda x: -x["est_daily"])[:top_n], 1):
        q = (r["question"] or r["slug"])[:56]
        print(f"{i:3} {r['mid']:5.3f} ${r['daily_rate']:5.0f} "
              f"${r['qual_notional']:7,.0f} ${r['est_daily']:7.2f} "
              f"{r['yield_pct']:6.2f}% {r['spread']*100:4.1f}¢ "
              f"{r['max_spread']*100:4.1f}¢  {q}")

    print("\n--- near-zero competition (high theoretical yield / high AS risk) ---")
    print(hdr)
    for i, r in enumerate(sorted(near, key=lambda x: -x["est_daily"])[:30], 1):
        q = (r["question"] or r["slug"])[:56]
        mid = float(r["mid"] or 0)
        spr = float(r["spread"] or 0) if r["spread"] != "" else 0.0
        print(f"{i:3} {mid:5.3f} ${r['daily_rate']:5.0f} "
              f"${r['qual_notional']:7,.0f} ${r['est_daily']:7.2f} "
              f"{r['yield_pct']:6.2f}% {spr*100:4.1f}¢ "
              f"{r['max_spread']*100:4.1f}¢  {q}")

    if competed:
        top = sorted(competed, key=lambda x: -x["est_daily"])[:20]
        avg_y = sum(r["yield_pct"] for r in top) / len(top)
        print(f"\ntop-20 competed avg yield: {avg_y:.2f}%/day on ${budget:.0f} "
              f"(GROSS reward capture only — no adverse selection)")
    print("NOTE: estimates are gross. Empty books price the cost of being run over.")


def history_report(scan_dir: Path):
    files = sorted(scan_dir.glob("????-??-??.csv"))
    if not files:
        print(f"no daily snapshots in {scan_dir}")
        return
    series: dict[str, list] = {}
    for fp in files:
        with open(fp, newline="") as f:
            for row in csv.DictReader(f):
                key = row.get("slug") or row.get("condition_id")
                series.setdefault(key, []).append({
                    "day": fp.stem,
                    "est": float(row.get("est_daily") or 0),
                    "yld": float(row.get("yield_pct") or 0),
                    "qual": float(row.get("qual_notional") or 0),
                    "nz": str(row.get("near_zero", "")).lower() in ("true", "1", "yes"),
                })
    print(f"=== CLOB STABILITY  ({len(files)} days, {len(series)} markets) ===")
    hdr = f"{'slug/question':48} {'d':>3} {'avg_yld':>8} {'min_yld':>8} {'avg_qual':>9} {'nz':>3}"
    print(hdr)
    ranked = []
    for k, pts in series.items():
        ylds = [p["yld"] for p in pts]
        quals = [p["qual"] for p in pts]
        ranked.append((k, len(pts), sum(ylds)/len(ylds), min(ylds),
                       sum(quals)/len(quals), sum(1 for p in pts if p["nz"])))
    for k, n, ay, my, aq, nz in sorted(ranked, key=lambda x: -x[2])[:40]:
        print(f"{k[:48]:48} {n:3} {ay:7.2f}% {my:7.2f}% ${aq:8,.0f} {nz:3}")


def main():
    ap = argparse.ArgumentParser(description="CLOB reward yield scan (global Polymarket)")
    ap.add_argument("--budget", type=float, default=500.0)
    ap.add_argument("--top", type=int, default=250,
                    help="Score top-N by daily_rate after filters")
    ap.add_argument("--min-daily", type=float, default=1.0)
    ap.add_argument("--min-days", type=float, default=3.0)
    ap.add_argument("--mid-lo", type=float, default=0.10)
    ap.add_argument("--mid-hi", type=float, default=0.90)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--scan-dir", type=str, default=str(DEFAULT_SCAN_DIR))
    ap.add_argument("--no-snapshot", action="store_true")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--print-top", type=int, default=50)
    args = ap.parse_args()

    scan_dir = Path(args.scan_dir)
    if args.history:
        history_report(scan_dir)
        return

    now = datetime.now(timezone.utc)
    client = ClobClient()
    print("fetching sampling-markets…", flush=True)
    markets = list(client.iter_sampling_markets())
    print(f"  {len(markets)} markets pulled", flush=True)

    eligible = filter_markets(
        markets, args.min_daily, args.min_days, args.mid_lo, args.mid_hi, now
    )
    print(f"  {len(eligible)} after filters (active, mid band, ≥{args.min_days}d, "
          f"rate≥${args.min_daily})", flush=True)

    targets = eligible[: args.top]
    print(f"scoring top {len(targets)} by daily_rate ({args.workers} workers)…",
          flush=True)

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_and_score, client, m, args.budget): m for m in targets}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                r = fut.result()
            except Exception as e:
                print(f"  book error: {e}", flush=True)
                continue
            if r:
                rows.append(r)
            if done % 50 == 0:
                print(f"  {done}/{len(targets)}", flush=True)

    rows.sort(key=lambda r: -r["est_daily"])
    print_tables(rows, args.budget, args.print_top)

    latest = scan_dir / "latest.csv"
    write_csv(latest, rows)
    print(f"\nwrote {latest} ({len(rows)} rows)")
    if not args.no_snapshot:
        day = append_daily(scan_dir, rows)
        print(f"appended {day}")


if __name__ == "__main__":
    main()
