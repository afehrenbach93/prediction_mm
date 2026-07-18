"""
One-shot settlement-sweep paper scout (Polymarket US). READ-ONLY — places no orders.

Flags near-certainty asks near endDate. Does NOT confirm resolution sources.
Never auto-buy from this output.

    python scripts/sweep_scout.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.polyclient import from_env
from poly_runner import sweep_scout, log


def main():
    client = from_env()
    state: dict = {}
    sweep_scout(client, log, state)
    s = state.get("summary") or {}
    print("--- summary ---")
    for k, v in s.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
