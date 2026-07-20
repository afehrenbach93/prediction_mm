"""Shadow-fill simulator unit tests (no network)."""
import unittest
from unittest.mock import patch

from core.clob_shadowfills import (
    ShadowFillState,
    ShadowQuote,
    mark_to_mid,
    process_tape,
    trade_crosses_quote,
)


class TestCross(unittest.TestCase):
    def test_trade_at_bid_fills_buy(self):
        q = ShadowQuote("t", bid=0.40, ask=0.60, bid_size=10, ask_size=10, mid=0.50)
        self.assertEqual(trade_crosses_quote(0.40, "SELL", q), "BUY")
        self.assertEqual(trade_crosses_quote(0.39, "SELL", q), "BUY")

    def test_trade_at_ask_fills_sell(self):
        q = ShadowQuote("t", bid=0.40, ask=0.60, bid_size=10, ask_size=10, mid=0.50)
        self.assertEqual(trade_crosses_quote(0.60, "BUY", q), "SELL")

    def test_inside_spread_no_fill(self):
        q = ShadowQuote("t", bid=0.40, ask=0.60, bid_size=10, ask_size=10, mid=0.50)
        self.assertIsNone(trade_crosses_quote(0.50, "BUY", q))


class TestProcessTape(unittest.TestCase):
    def test_warmup_skips_historical_fills(self):
        q = ShadowQuote("tok1", bid=0.45, ask=0.55, bid_size=20, ask_size=20,
                        mid=0.50, slug="m")
        state = ShadowFillState()
        trades = [{"id": "tr1", "price": 0.44, "size": 5, "side": "SELL"}]
        with patch("core.clob_shadowfills.fetch_trades", return_value=trades):
            fills = process_tape([q], state)
        self.assertEqual(fills, [])
        self.assertEqual(state.fills_today, 0)
        self.assertIn("tok1", state.warmed_tokens)

    def test_simulated_fill_after_warmup(self):
        q = ShadowQuote("tok1", bid=0.45, ask=0.55, bid_size=20, ask_size=20,
                        mid=0.50, slug="m")
        state = ShadowFillState()
        state.warmed_tokens.add("tok1")
        trades = [{"id": "tr1", "price": 0.44, "size": 5, "side": "SELL"}]
        with patch("core.clob_shadowfills.fetch_trades", return_value=trades):
            fills = process_tape([q], state)
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["side"], "BUY")
        self.assertEqual(fills[0]["price"], 0.45)
        self.assertTrue(fills[0]["simulated"])
        self.assertEqual(state.fills_today, 1)
        self.assertAlmostEqual(state.inventory["tok1"], 5.0)

    def test_mark_to_mid(self):
        state = ShadowFillState()
        state.inventory["tok1"] = 10.0
        state.avg_entry["tok1"] = 0.40
        pnl = mark_to_mid(state, {"tok1": 0.42})
        self.assertAlmostEqual(pnl, 0.20)


if __name__ == "__main__":
    unittest.main()
