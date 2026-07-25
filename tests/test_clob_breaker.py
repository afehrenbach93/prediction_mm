"""Inventory breaker: USD notional primary; maker-side trade netting."""
import os
import unittest
from unittest.mock import patch

# Import after env defaults are fine
import clob_runner as cr


class TestClobBreaker(unittest.TestCase):
    def test_low_mid_share_count_under_usd_cap_ok(self):
        # ~$75 of a 0.055 token ≈ 1364 shares — must NOT trip on share count alone
        positions = {"tok": 1360.7}
        mids = {"tok": 0.055}
        with patch.object(cr, "MAX_INV_USD", 75 * 1.5), patch.object(cr, "EXPOSURE_CAP", 500.0):
            trip, reason = cr.breaker(positions, mids)
        self.assertFalse(trip, reason)

    def test_usd_cap_trips(self):
        positions = {"tok": 2000.0}
        mids = {"tok": 0.10}  # $200
        with patch.object(cr, "MAX_INV_USD", 112.5), patch.object(cr, "EXPOSURE_CAP", 500.0):
            trip, reason = cr.breaker(positions, mids)
        self.assertTrue(trip)
        self.assertIn("inventory $", reason)

    def test_unknown_mid_does_not_assume_half(self):
        # 200 sh with no mark used to look like $100 @ 0.5 and false-trip
        positions = {"tok": 200.0}
        mids: dict = {}
        with patch.object(cr, "MAX_INV_USD", 94.0), patch.object(cr, "EXPOSURE_CAP", 500.0), \
             patch.object(cr, "MAX_INV", 200.0):
            trip, reason = cr.breaker(positions, mids)
        self.assertFalse(trip, reason)

    def test_maker_side_inverted_for_inventory(self):
        # Taker SELL into our bid → we BUY
        trades = [{
            "asset_id": "t1",
            "side": "SELL",
            "trader_side": "MAKER",
            "size": "10",
        }]
        self.assertAlmostEqual(cr.positions_from_trades(trades)["t1"], 10.0)

    def test_taker_buy_adds(self):
        trades = [{
            "token_id": "t1",
            "side": "BUY",
            "trader_side": "TAKER",
            "size": "5",
        }]
        self.assertAlmostEqual(cr.positions_from_trades(trades)["t1"], 5.0)

    def test_e6_size_normalized(self):
        trades = [{
            "asset_id": "t1",
            "side": "BUY",
            "trader_side": "TAKER",
            "size": "100000000",  # 100 shares in base units
        }]
        self.assertAlmostEqual(cr.positions_from_trades(trades)["t1"], 100.0)

    def test_share_room_grows_for_cheap_mids(self):
        with patch.object(cr, "MAX_INV", 200.0), patch.object(cr, "MAX_INV_USD", 93.75):
            self.assertGreater(cr.share_room_for_mid(0.05), 200.0)
            self.assertAlmostEqual(cr.share_room_for_mid(0.50), 200.0)


if __name__ == "__main__":
    unittest.main()
