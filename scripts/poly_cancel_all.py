"""
One-shot LIVE cancel of all resting Polymarket US orders.

Use when the worker was flipped back to BOT_MODE=shadow but leftover live orders
remain (shadow cannot cancel exchange orders). Requires credentials in .env.

    PYTHONPATH=. python scripts/poly_cancel_all.py
    PYTHONPATH=. python scripts/poly_cancel_all.py --dry-run   # list only
"""
import argparse
import sys

from core.polyclient import from_env


def main():
    ap = argparse.ArgumentParser(description="Cancel all open Polymarket US orders (LIVE).")
    ap.add_argument("--dry-run", action="store_true",
                    help="List open orders without cancelling")
    args = ap.parse_args()

    client = from_env()
    if not client.api_key_id or not client._signer:
        print("ERROR: POLYMARKET_API_KEY / POLYMARKET_SECRET missing", file=sys.stderr)
        sys.exit(1)

    # Force live so cancel_order hits the exchange (shadow would only log).
    client.live = True

    s, d = client.get_open_orders()
    if s != 200:
        print(f"ERROR: get_open_orders status={s} body={d}", file=sys.stderr)
        sys.exit(1)

    orders = []
    if isinstance(d, dict):
        orders = d.get("orders") or d.get("openOrders") or []
    elif isinstance(d, list):
        orders = d

    print(f"open orders: {len(orders)}")
    if not orders:
        return

    for o in orders:
        if not isinstance(o, dict):
            continue
        oid = o.get("id") or o.get("orderId")
        slug = o.get("marketSlug", "")
        print(f"  {oid}  {slug}")
        if args.dry_run or not oid:
            continue
        cs, body = client.cancel_order(oid, slug)
        print(f"    cancel -> {cs} {body}")


if __name__ == "__main__":
    main()
