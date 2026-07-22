"""Shadow-fill simulator unit tests (no network)."""
import unittest
from unittest.mock import patch

from core.clob_shadowfills import (
    ShadowFillState,
    ShadowQuote,
    flatten_token,
    mark_to_mid,
    process_tape,
    rollover_utc_day,
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
            fills = process_tape([q], state, max_fill_size=5)
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["side"], "BUY")
        self.assertEqual(fills[0]["price"], 0.45)
        self.assertTrue(fills[0]["simulated"])
        self.assertEqual(state.fills_today, 1)
        self.assertAlmostEqual(state.inventory["tok1"], 5.0)

    def test_cap_flattens_then_allows_new_fills(self):
        """Stuck-at-cap used to skip forever; must flatten and keep sampling."""
        q = ShadowQuote("tok1", bid=0.45, ask=0.55, bid_size=20, ask_size=20,
                        mid=0.50, slug="m")
        state = ShadowFillState()
        state.warmed_tokens.add("tok1")
        state.inventory["tok1"] = 150.0
        state.avg_entry["tok1"] = 0.40
        trades = [{"id": "new1", "price": 0.44, "size": 5, "side": "SELL"}]
        with patch("core.clob_shadowfills.fetch_trades", return_value=trades):
            fills = process_tape([q], state, max_inventory=150, max_fill_size=5)
        self.assertEqual(len(fills), 1)
        self.assertGreater(state.realized_pnl_today, 0.0)  # flattened long at mid 0.50
        self.assertAlmostEqual(state.inventory.get("tok1", 0.0), 5.0)

    def test_reducing_fill_at_cap_without_prior_flatten_path(self):
        q = ShadowQuote("tok1", bid=0.45, ask=0.55, bid_size=20, ask_size=20,
                        mid=0.50, slug="m")
        state = ShadowFillState()
        state.warmed_tokens.add("tok1")
        state.inventory["tok1"] = 149.0
        state.avg_entry["tok1"] = 0.40
        # sell crosses ask → reduces long
        trades = [{"id": "red1", "price": 0.56, "size": 5, "side": "BUY"}]
        with patch("core.clob_shadowfills.fetch_trades", return_value=trades):
            fills = process_tape([q], state, max_inventory=150, max_fill_size=5)
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["side"], "SELL")
        self.assertAlmostEqual(state.inventory["tok1"], 144.0)

    def test_mark_to_mid(self):
        state = ShadowFillState()
        state.inventory["tok1"] = 10.0
        state.avg_entry["tok1"] = 0.40
        pnl = mark_to_mid(state, {"tok1": 0.42})
        self.assertAlmostEqual(pnl, 0.20)

    def test_rollover_resets_day_keeps_warmup(self):
        state = ShadowFillState()
        state.day = "2026-07-20"
        state.warmed_tokens.add("tok1")
        state.fills_today = 10
        state.inventory["tok1"] = 8.0
        state.avg_entry["tok1"] = 0.4
        state.adverse_moves = [0.01]
        with patch("core.clob_shadowfills.utc_day", return_value="2026-07-21"):
            rolled, realized, prior = rollover_utc_day(state, {"tok1": 0.5})
        self.assertTrue(rolled)
        self.assertEqual(prior["day"], "2026-07-20")
        self.assertEqual(prior["fills_today"], 10)
        self.assertEqual(state.day, "2026-07-21")
        self.assertEqual(state.fills_today, 0)
        self.assertEqual(state.inventory, {})
        self.assertIn("tok1", state.warmed_tokens)
        self.assertAlmostEqual(realized, 8.0 * (0.5 - 0.4))

    def test_flatten_token(self):
        state = ShadowFillState()
        state.inventory["tok1"] = -10.0
        state.avg_entry["tok1"] = 0.6
        r = flatten_token(state, "tok1", 0.5)
        self.assertAlmostEqual(r, -10.0 * (0.5 - 0.6))
        self.assertNotIn("tok1", state.inventory)


if __name__ == "__main__":
    unittest.main()
