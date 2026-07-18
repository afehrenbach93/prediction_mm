"""
One-shot same-venue arb scan (Polymarket US). READ-ONLY — places no orders.

    python scripts/arb_scan.py
"""
import os
import sys

# repo root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.polyclient import from_env


def main():
    # Import after path setup; arb_scan lives on the worker module.
    from poly_runner import arb_scan, log
    client = from_env()
    state: dict = {}
    arb_scan(client, log, state)
    s = state.get("summary") or {}
    print("--- summary ---")
    for k, v in s.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
