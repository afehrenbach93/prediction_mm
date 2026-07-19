"""Stability filter unit tests (no network)."""
import csv
import tempfile
import unittest
from pathlib import Path

from scripts import clob_stability as stab


class TestStability(unittest.TestCase):
    def test_persistent_competed(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for day, yld, nz in [("2026-07-17", 8.0, False),
                                 ("2026-07-18", 7.0, False),
                                 ("2026-07-19", 9.0, False)]:
                with open(d / f"{day}.csv", "w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=[
                        "slug", "condition_id", "token_id", "question",
                        "yield_pct", "est_daily", "qual_notional", "daily_rate",
                        "near_zero", "end_date", "max_spread", "min_size",
                    ])
                    w.writeheader()
                    w.writerow({
                        "slug": "m1", "condition_id": "c1", "token_id": "t1",
                        "question": "Q", "yield_pct": yld, "est_daily": 40,
                        "qual_notional": 500, "daily_rate": 100,
                        "near_zero": nz, "end_date": "2026-12-31",
                        "max_spread": 0.045, "min_size": 30,
                    })
                    w.writerow({
                        "slug": "empty", "condition_id": "c2", "token_id": "t2",
                        "question": "Empty", "yield_pct": 20, "est_daily": 100,
                        "qual_notional": 0, "daily_rate": 100,
                        "near_zero": True, "end_date": "2026-12-31",
                        "max_spread": 0.045, "min_size": 30,
                    })
            series = stab.load_series(d)
            rows = stab.select_persistent(series, min_days=3, min_yield=3.0,
                                          max_nz_days=0, require_competed=True)
            ids = {r["condition_id"] for r in rows}
            self.assertIn("c1", ids)
            self.assertNotIn("c2", ids)


if __name__ == "__main__":
    unittest.main()
