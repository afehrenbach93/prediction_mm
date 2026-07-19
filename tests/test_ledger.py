"""
Tests for OpsLedger append-only logging.
Run: PYTHONPATH=. python -m unittest tests.test_ledger -v
"""
import tempfile
import unittest
from pathlib import Path

from core.ledger import OpsLedger


class TestOpsLedger(unittest.TestCase):
    def test_writes_quotes_rewards_separately(self):
        with tempfile.TemporaryDirectory() as td:
            led = OpsLedger(td)
            led.log_quote("m1", "ORDER_INTENT_BUY_LONG", 0.5, 10, 0.51,
                          mode="shadow", shadow=True)
            led.log_rewards({"earnings": 1.23}, note="unit-test")
            led.log_fill_placeholder("m1", "buy", 0.5, 5, 0.51, note="sim")

            self.assertTrue((Path(td) / "quotes.csv").exists())
            self.assertTrue((Path(td) / "rewards.csv").exists())
            self.assertTrue((Path(td) / "fills.csv").exists())
            q = (Path(td) / "quotes.csv").read_text()
            r = (Path(td) / "rewards.csv").read_text()
            self.assertIn("m1", q)
            self.assertIn("earnings", r)
            # rewards file must not mix trading fill rows
            self.assertNotIn("ORDER_INTENT", r)


if __name__ == "__main__":
    unittest.main()
