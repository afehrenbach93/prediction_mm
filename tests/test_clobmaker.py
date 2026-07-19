"""Tests for CLOB quote price/size helpers."""
import unittest

from core.clobmaker import ClobQuoteParams, maker_quotes, quote_prices, size_per_side


class TestQuotes(unittest.TestCase):
    def test_half_max_spread_prices(self):
        # mid 0.50, max 4.5¢, fraction 0.5 → ~±2.25¢ from mid after tick snap
        bid, ask = quote_prices(0.50, 4.5, 0.001, 0.5)
        self.assertLess(bid, 0.50)
        self.assertGreater(ask, 0.50)
        self.assertAlmostEqual(0.50 - bid, 0.0225, delta=0.0015)
        self.assertAlmostEqual(ask - 0.50, 0.0225, delta=0.0015)

    def test_size_meets_min(self):
        self.assertGreaterEqual(size_per_side(75, 0.5, 30), 30)

    def test_both_sides_flat(self):
        q = maker_quotes(0.50, 4.5, 0.01, 0.0, 5.0,
                         ClobQuoteParams(budget_usd=75, spread_fraction=0.5))
        self.assertEqual(len(q), 2)
        self.assertEqual(q[0].side, "BUY")
        self.assertEqual(q[1].side, "SELL")
        self.assertLess(q[0].price, q[1].price)

    def test_inventory_blocks_buy(self):
        q = maker_quotes(0.50, 4.5, 0.01, 200.0, 5.0,
                         ClobQuoteParams(budget_usd=75, max_inventory=200))
        self.assertFalse(any(x.side == "BUY" for x in q))


if __name__ == "__main__":
    unittest.main()
