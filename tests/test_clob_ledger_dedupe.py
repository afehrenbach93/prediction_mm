"""Fill / reward dedupe + earnings sum helpers."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.clob_ledger import ClobLedger, trade_id_of, unique_fills_by_trade_id
from core.clobtrader import ClobTrader


class TestTradeId(unittest.TestCase):
    def test_prefers_id(self):
        self.assertEqual(trade_id_of({"id": "abc", "trade_id": "x"}), "abc")

    def test_composite_fallback(self):
        tid = trade_id_of({
            "token_id": "tok1", "side": "BUY", "price": 0.4,
            "size": 5, "match_time": "t1",
        })
        self.assertIn("tok1", tid)
        self.assertIn("BUY", tid)


class TestLedgerDedupe(unittest.TestCase):
    def test_log_fill_skips_duplicates_in_process(self):
        with tempfile.TemporaryDirectory() as td:
            sb = MagicMock()
            sb.enabled = True
            sb.insert_ignore = MagicMock(return_value=(201, {}))
            led = ClobLedger(log_dir=td, sb=sb)
            t = {"id": "trade-1", "asset_id": "tok", "side": "BUY",
                 "price": 0.5, "size": 10}
            self.assertTrue(led.log_fill(t, simulated=False))
            self.assertFalse(led.log_fill(t, simulated=False))
            self.assertEqual(sb.insert_ignore.call_count, 1)
            self.assertTrue((Path(td) / "fills.csv").exists())

    def test_log_rewards_dedupe_key(self):
        with tempfile.TemporaryDirectory() as td:
            sb = MagicMock()
            sb.enabled = True
            sb.insert_ignore = MagicMock(return_value=(201, {}))
            led = ClobLedger(log_dir=td, sb=sb)
            self.assertTrue(led.log_rewards(
                {"earnings": 1.2}, source="actual", amount_usd=1.2,
                dedupe_key="actual:2026-08-02:total",
            ))
            self.assertFalse(led.log_rewards(
                {"earnings": 1.2}, source="actual", amount_usd=1.2,
                dedupe_key="actual:2026-08-02:total",
            ))
            self.assertEqual(sb.insert_ignore.call_count, 1)

    def test_update_runner_status_upserts(self):
        with tempfile.TemporaryDirectory() as td:
            sb = MagicMock()
            sb.enabled = True
            sb.upsert = MagicMock(return_value=(201, {}))
            led = ClobLedger(log_dir=td, sb=sb)
            row = led.update_runner_status(
                mode="live", host="ec2", collateral_usd=208.5, note="heartbeat",
            )
            self.assertEqual(row["collateral_usd"], 208.5)
            sb.upsert.assert_called_once()
            args = sb.upsert.call_args
            self.assertEqual(args[0][0], "clob_runner_status")
            self.assertEqual(args[1]["on_conflict"], "host")


class TestEarningsSum(unittest.TestCase):
    def test_sum_list(self):
        raw = [
            {"earnings": 0.25, "condition_id": "a"},
            {"earnings": 0.75, "condition_id": "b"},
        ]
        self.assertAlmostEqual(ClobTrader.sum_earnings_usd(raw), 1.0)

    def test_sum_err(self):
        self.assertEqual(ClobTrader.sum_earnings_usd({"_err": "nope"}), 0.0)


class TestUniqueFills(unittest.TestCase):
    def test_dedupes_by_trade_id(self):
        rows = [
            {"trade_id": "a", "ts": "2"},
            {"trade_id": "a", "ts": "1"},
            {"trade_id": "b", "ts": "3"},
            {"trade_id": "", "ts": "4"},
            {"trade_id": "", "ts": "5"},
        ]
        out = unique_fills_by_trade_id(rows)
        self.assertEqual(len(out), 4)  # a once, b, two empty ids kept
        self.assertEqual(out[0]["trade_id"], "a")
        ids = [r["trade_id"] for r in out if r["trade_id"]]
        self.assertEqual(ids, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
