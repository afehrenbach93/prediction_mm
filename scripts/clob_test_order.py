#!/usr/bin/env python3
"""One-shot live CLOB post-only bid + cancel. Requires CLOB_MODE=live env.

Usage (on live host):
  set -a && source .env && set +a
  PYTHONPATH=. python scripts/clob_test_order.py
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.clobtrader import ClobTrader


def _best_bid(book) -> float:
    if isinstance(book, dict):
        bids = book.get("bids") or []
        if not bids:
            return 0.45
        top = bids[0]
        if isinstance(top, dict):
            return float(top.get("price") or top.get("p") or 0.45)
        return float(getattr(top, "price", 0.45))
    bids = getattr(book, "bids", None) or []
    if not bids:
        return 0.45
    return float(bids[0].price)


def _first_pilot_token() -> tuple[str, str]:
    path = Path(os.getenv("CLOB_PILOT_CSV", "data/clob_scans/pilot_universe.csv"))
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if int(float(r.get("near_zero_days") or 0)) > 0:
                continue
            tid = (r.get("token_id") or "").strip()
            if tid:
                return tid, (r.get("slug") or "")
    raise SystemExit(f"no pilot token in {path}")


def main() -> None:
    trader = ClobTrader.from_env()
    if not trader.live:
        raise SystemExit(
            "refusing: trader not live "
            f"(mode={os.getenv('CLOB_MODE')!r} reason={trader.refuse_reason!r})"
        )

    token, slug = _first_pilot_token()
    print(f"slug={slug}")
    print(f"token={token[:24]}...")

    book = trader.get_book(token)
    best = _best_bid(book)
    price = round(max(0.01, best - 0.01), 2)
    size = float(os.getenv("CLOB_TEST_SIZE", "5"))
    print(f"best_bid={best} test BUY {size}@{price}")

    resp = trader.place_limit(token, "BUY", price, size, post_only=True)
    print(f"resp={resp}")

    oid = None
    if isinstance(resp, dict):
        oid = resp.get("orderID") or resp.get("id") or resp.get("orderId")
    if oid and not str(oid).startswith("shadow-"):
        try:
            client = trader._auth_client()
            print(f"cancel={client.cancel(oid)}")
        except Exception as e:
            print(f"cancel_one_failed={e}; cancel_all={trader.cancel_all()}")
    else:
        print(f"cancel_all={trader.cancel_all()}")
    print("TEST_OK")


if __name__ == "__main__":
    main()
