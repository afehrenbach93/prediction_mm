#!/usr/bin/env python3
"""One-shot live order test for deposit-wallet accounts.

Default path uses the unified ``polymarket`` SecureClient (recommended for
signature_type=3). Fallback: legacy ``ClobTrader`` / py-clob-client-v2.

Usage:
  set -a && source .env && set +a
  pip install -U 'polymarket>=0.1.0'
  PYTHONPATH=. python scripts/clob_test_order.py
  PYTHONPATH=. python scripts/clob_test_order.py --legacy
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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


def _best_bid_from_book(book) -> float:
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
    top = bids[0]
    if isinstance(top, dict):
        return float(top.get("price") or 0.45)
    return float(getattr(top, "price", 0.45))


def _builder_api_key():
    key = os.getenv("POLYMARKET_BUILDER_API_KEY", "")
    secret = os.getenv("POLYMARKET_BUILDER_SECRET", "")
    passphrase = os.getenv("POLYMARKET_BUILDER_PASSPHRASE", "")
    if not (key and secret and passphrase):
        return None
    from polymarket.auth import BuilderApiKey

    return BuilderApiKey(key=key, secret=secret, passphrase=passphrase)


def run_polymarket_sdk() -> None:
    from polymarket import SecureClient

    pk = os.environ["CLOB_PRIVATE_KEY"]
    wallet = os.environ["CLOB_FUNDER"]
    token, slug = _first_pilot_token()
    size = float(os.getenv("CLOB_TEST_SIZE", "5"))

    kwargs = {"private_key": pk, "wallet": wallet}
    builder = _builder_api_key()
    if builder is not None:
        kwargs["api_key"] = builder

    print(f"sdk=polymarket SecureClient wallet={wallet}")
    print(f"slug={slug}")
    client = SecureClient.create(**kwargs)
    try:
        identity = getattr(client, "account", None)
        print(f"account={identity}")
        book = client.get_order_book(token_id=token)
        # book shape varies by SDK version
        bids = None
        if hasattr(book, "bids"):
            bids = book.bids
        elif isinstance(book, dict):
            bids = book.get("bids")
        best = 0.45
        if bids:
            top = bids[0]
            best = float(getattr(top, "price", None) or (top.get("price") if isinstance(top, dict) else 0.45))
        price = round(max(0.01, best - 0.01), 2)
        print(f"best_bid={best} test BUY {size}@{price}")

        resp = client.place_limit_order(
            token_id=token,
            price=price,
            size=size,
            side="BUY",
            post_only=True,
        )
        print(f"resp={resp}")

        oid = None
        if hasattr(resp, "order_id"):
            oid = resp.order_id
        elif isinstance(resp, dict):
            oid = resp.get("orderID") or resp.get("order_id") or resp.get("id")
        else:
            oid = getattr(resp, "id", None)
        if oid:
            print(f"cancel={client.cancel_order(order_id=str(oid))}")
        else:
            print(f"cancel_all={client.cancel_all()}")
        print("TEST_OK")
    finally:
        try:
            client.close()
        except Exception:
            pass


def run_legacy() -> None:
    from core.clobtrader import ClobTrader

    trader = ClobTrader.from_env()
    if not trader.live:
        raise SystemExit(
            "refusing: trader not live "
            f"(mode={os.getenv('CLOB_MODE')!r} reason={trader.refuse_reason!r})"
        )

    token, slug = _first_pilot_token()
    print(f"sdk=legacy ClobTrader")
    print(f"slug={slug}")
    print(f"token={token[:24]}...")

    book = trader.get_book(token)
    best = _best_bid_from_book(book)
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
            print(f"cancel={trader._auth_client().cancel(oid)}")
        except Exception as e:
            print(f"cancel_one_failed={e}; cancel_all={trader.cancel_all()}")
    else:
        print(f"cancel_all={trader.cancel_all()}")
    print("TEST_OK")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--legacy",
        action="store_true",
        help="use py-clob-client-v2 via ClobTrader instead of polymarket SecureClient",
    )
    args = p.parse_args()
    if args.legacy:
        run_legacy()
    else:
        run_polymarket_sdk()


if __name__ == "__main__":
    main()
