"""Tests for CLOB quote price/size helpers."""
import unittest

from core.clobmaker import (
    ClobQuoteParams,
    clamp_price,
    maker_quotes,
    quote_prices,
    round_tick,
    size_per_side,
)


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

    def test_prices_exclusive_unit_interval(self):
        bid, ask = quote_prices(0.50, 4.5, 0.01, 0.5)
        self.assertGreater(bid, 0.0)
        self.assertLess(ask, 1.0)
        for q in maker_quotes(0.50, 4.5, 0.01, 0.0, 1.0, ClobQuoteParams()):
            self.assertGreater(q.price, 0.0)
            self.assertLess(q.price, 1.0)

    def test_rounded_to_tick(self):
        tick = 0.01
        bid, ask = quote_prices(0.503, 4.5, tick, 0.5)
        self.assertAlmostEqual(bid, round_tick(bid, tick))
        self.assertAlmostEqual(ask, round_tick(ask, tick))
        # prices are exact integer multiples of tick (float-safe)
        self.assertAlmostEqual(round(bid / tick), bid / tick, places=8)
        self.assertAlmostEqual(round(ask / tick), ask / tick, places=8)

    def test_mid_near_zero_clamps(self):
        # mid ± half-spread would go ≤0 — clamp stays in (0,1)
        bid, ask = quote_prices(0.02, 10.0, 0.01, 0.5)
        self.assertGreater(bid, 0.0)
        self.assertLess(ask, 1.0)
        self.assertLess(bid, ask)
        q = maker_quotes(0.02, 10.0, 0.01, 0.0, 1.0, ClobQuoteParams())
        for x in q:
            self.assertGreater(x.price, 0.0)
            self.assertLess(x.price, 1.0)

    def test_mid_near_one_clamps(self):
        bid, ask = quote_prices(0.98, 10.0, 0.01, 0.5)
        self.assertGreater(bid, 0.0)
        self.assertLess(ask, 1.0)
        self.assertLess(bid, ask)
        self.assertEqual(clamp_price(1.5, 0.01), 0.99)
        self.assertEqual(clamp_price(-0.5, 0.01), 0.01)


if __name__ == "__main__":
    unittest.main()
