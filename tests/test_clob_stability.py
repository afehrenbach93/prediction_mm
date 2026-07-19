"""Stability filter unit tests (no network)."""
import csv
import tempfile
import unittest
from pathlib import Path

from scripts import clob_stability as stab


class TestStability(unittest.TestCase):
    def _write_days(self, d: Path, days: list[str], yld: float = 8.0):
        for day in days:
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
                    "near_zero": False, "end_date": "2026-12-31",
                    "max_spread": 0.045, "min_size": 30,
                })
                w.writerow({
                    "slug": "empty", "condition_id": "c2", "token_id": "t2",
                    "question": "Empty", "yield_pct": 20, "est_daily": 100,
                    "qual_notional": 0, "daily_rate": 100,
                    "near_zero": True, "end_date": "2026-12-31",
                    "max_spread": 0.045, "min_size": 30,
                })

    def test_persistent_competed(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self._write_days(d, ["2026-07-17", "2026-07-18", "2026-07-19"])
            series = stab.load_series(d)
            rows = stab.select_persistent(series, min_days=3, min_yield=3.0,
                                          max_nz_days=0, require_competed=True)
            ids = {r["condition_id"]: r for r in rows}
            self.assertIn("c1", ids)
            self.assertNotIn("c2", ids)
            self.assertTrue(ids["c1"]["provisional"])  # < 5 days

    def test_provisional_false_at_five_days(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            days = [f"2026-07-{i:02d}" for i in range(15, 20)]
            self._write_days(d, days)
            series = stab.load_series(d)
            rows = stab.select_persistent(series, min_days=5, min_yield=3.0,
                                          max_nz_days=0, require_competed=True)
            self.assertEqual(len(rows), 1)
            self.assertFalse(rows[0]["provisional"])

    def test_default_min_days_is_five(self):
        self.assertEqual(stab.PROVISIONAL_DAYS, 5)


if __name__ == "__main__":
    unittest.main()
