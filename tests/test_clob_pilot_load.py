"""Pilot loader skips empty books and fills remaining slots."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import clob_runner as cr


class TestPilotLoad(unittest.TestCase):
    def test_skips_no_book_and_fills_slot(self):
        rows = [
            {
                "slug": "a-has-book",
                "token_id": "1",
                "near_zero_days": "0",
                "end_date": "2027-01-01T00:00:00Z",
                "provisional": "",
            },
            {
                "slug": "b-no-book",
                "token_id": "2",
                "near_zero_days": "0",
                "end_date": "2027-01-01T00:00:00Z",
                "provisional": "",
            },
            {
                "slug": "c-has-book",
                "token_id": "3",
                "near_zero_days": "0",
                "end_date": "2027-01-01T00:00:00Z",
                "provisional": "",
            },
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pilot.csv"
            with path.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)

            trader = MagicMock()

            def book(tid):
                if str(tid) == "2":
                    return [], []
                return [(0.4, 10)], [(0.5, 10)]

            trader.get_book.side_effect = book
            out = cr.load_pilot(path, max_n=2, trader=trader)
            self.assertEqual([r["slug"] for r in out], ["a-has-book", "c-has-book"])


if __name__ == "__main__":
    unittest.main()
