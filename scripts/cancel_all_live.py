"""
One-shot LIVE cancel of all resting Polymarket US orders. RISK-REDUCING ONLY —
it cancels resting orders and PLACES NO ORDERS, so it cannot open or add exposure.

Why this exists: during the 2026-06-20 migration cutover the worker briefly ran
live and rested real orders on COD esports reward markets (`aec-cod-*`). The
runner's `cancel_order` is shadow-gated, so once halted back to shadow the bot
can no longer reach those orders to cancel them — they orphan on the exchange.
This script reaches them with a live client and clears them in one shot.

It does NOT close positions: closing a position means BUYING/SELLING to flat,
i.e. placing an order, which this tool deliberately will not do. Any open
position is reported so the operator can close it manually in the Polymarket UI.

SAFETY: requires an explicit confirmation env var so it can never run by accident:

    POLYMARKET_API_KEY=... POLYMARKET_SECRET=... \
    CONFIRM_LIVE_CANCEL=yes python scripts/cancel_all_live.py

Without CONFIRM_LIVE_CANCEL=yes it runs in DRY-RUN: it lists the resting orders
and open positions but cancels nothing. Run the dry-run first.
"""
import os
import sys

from core.polyclient import PolyClient, load_env
from poly_runner import cancel_all_orders, positions_net


def main() -> int:
    env = load_env() if os.path.exists(".env") else {}
    api_key = os.getenv("POLYMARKET_API_KEY") or env.get("POLYMARKET_API_KEY", "")
    secret = os.getenv("POLYMARKET_SECRET") or env.get("POLYMARKET_SECRET", "")
    if not api_key or not secret:
        print("ERROR: POLYMARKET_API_KEY / POLYMARKET_SECRET not set.", file=sys.stderr)
        return 2

    confirm = os.getenv("CONFIRM_LIVE_CANCEL", "").strip().lower() == "yes"
    # live=True only when confirmed; otherwise a shadow client so even the cancel
    # path cannot reach the exchange (belt and suspenders on the dry-run).
    client = PolyClient(api_key_id=api_key, secret_b64=secret, live=confirm)

    # --- report current state first ---
    s, d = client.get_open_orders()
    orders = []
    if isinstance(d, dict):
        orders = d.get("orders") or d.get("openOrders") or []
    elif isinstance(d, list):
        orders = d
    print(f"open orders endpoint: HTTP {s}; {len(orders)} resting order(s)")
    for o in orders:
        if isinstance(o, dict):
            print(f"  - id={o.get('id') or o.get('orderId')} "
                  f"slug={o.get('marketSlug')} "
                  f"intent={o.get('intent')} px={o.get('price')} qty={o.get('quantity')}")

    positions = positions_net(client)
    open_pos = {k: v for k, v in positions.items() if abs(v.get("net", 0)) > 0}
    print(f"\nopen position(s): {len(open_pos)}")
    for slug, info in open_pos.items():
        print(f"  - {slug}: net={info['net']:+.0f} entry={info['entry']}")
    if open_pos:
        print("  NOTE: positions are NOT closed by this tool (that needs an order). "
              "Close them manually in the Polymarket UI.")

    if not confirm:
        print("\nDRY-RUN (CONFIRM_LIVE_CANCEL != yes): cancelled nothing. "
              "Re-run with CONFIRM_LIVE_CANCEL=yes to actually cancel the orders.")
        return 0

    print("\nCONFIRM_LIVE_CANCEL=yes — cancelling all resting orders...")
    n = cancel_all_orders(client)
    print(f"cancelled {n} order(s).")

    # verify
    s2, d2 = client.get_open_orders()
    remaining = []
    if isinstance(d2, dict):
        remaining = d2.get("orders") or d2.get("openOrders") or []
    elif isinstance(d2, list):
        remaining = d2
    print(f"remaining resting orders after cancel: {len(remaining)}")
    return 0 if not remaining else 1


if __name__ == "__main__":
    sys.exit(main())
