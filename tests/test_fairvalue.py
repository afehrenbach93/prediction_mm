"""Tests for lib/fairvalue.py (salvaged from kalshi-mm tests/test_engine.py).

The module is dormant in prediction-mm but kept tested so it stays a reliable
building block. Imports point at lib.fairvalue (was core.fairvalue in kalshi-mm).
"""

import unittest


class TestFairValue(unittest.TestCase):
    def test_norm_cdf(self):
        from lib.fairvalue import norm_cdf
        self.assertAlmostEqual(norm_cdf(0.0), 0.5)
        self.assertGreater(norm_cdf(3.0), 0.99)
        self.assertLess(norm_cdf(-3.0), 0.01)

    def test_vol_estimate(self):
        from lib.fairvalue import vol_per_sqrt_sec
        # 1s-spaced walk with ±10 steps → variance rate ≈ 100 → sigma ≈ 10
        samples = [(float(i), 64000.0 + (10 if i % 2 else -10) * (i % 2))
                   for i in range(40)]
        v = vol_per_sqrt_sec(samples)
        self.assertIsNotNone(v)
        self.assertGreater(v, 0)
        # too little history → None (caller must fall back to the book)
        self.assertIsNone(vol_per_sqrt_sec(samples[:3]))
        self.assertIsNone(vol_per_sqrt_sec([]))

    def test_fair_yes_above(self):
        from lib.fairvalue import fair_yes_above
        # at the strike with any positive vol → coin flip
        self.assertAlmostEqual(fair_yes_above(64000, 64000, 600, 10.0), 0.5, places=6)
        # spot well above strike → YES near 1; well below → near 0
        self.assertGreater(fair_yes_above(64500, 64000, 300, 10.0), 0.9)
        self.assertLess(fair_yes_above(63500, 64000, 300, 10.0), 0.1)
        # missing/degenerate inputs → None (fall back to book)
        self.assertIsNone(fair_yes_above(64000, 64000, 600, None))
        self.assertIsNone(fair_yes_above(64000, 64000, 0, 10.0))
        self.assertIsNone(fair_yes_above(0, 64000, 600, 10.0))

    def test_stale_side(self):
        from lib.fairvalue import stale_side
        # YES = "above": spot up makes our ASK stale, spot down our BID
        self.assertEqual(stale_side(0.001, 0.0006), "ask")
        self.assertEqual(stale_side(-0.001, 0.0006), "bid")
        self.assertIsNone(stale_side(0.0002, 0.0006))
        self.assertIsNone(stale_side(None, 0.0006))

    def test_quote_gate(self):
        from lib.fairvalue import quote_gate
        # THE bleed case: fair is 0.70 but the book mid is ~0.50, so a
        # book-anchored 0.46/0.54 quote would SELL YES at 0.54 (far under
        # fair) — the gate must refuse the ask and keep only the cheap bid.
        allow_bid, allow_ask = quote_gate(0.70, 0.46, 0.54, 0.03)
        self.assertTrue(allow_bid)           # 0.46 ≤ 0.70 − 0.03 → buying cheap
        self.assertFalse(allow_ask)          # 0.54 < 0.70 + 0.03 → underpriced
        # mirror: fair low (0.30) → refuse the overpriced bid, keep the ask
        allow_bid, allow_ask = quote_gate(0.30, 0.46, 0.54, 0.03)
        self.assertFalse(allow_bid)
        self.assertTrue(allow_ask)
        # a quote that straddles fair with margin on both sides → both show
        allow_bid, allow_ask = quote_gate(0.70, 0.60, 0.74, 0.03)
        self.assertTrue(allow_bid and allow_ask)
        # fair at the money with a tight margin → both sides fine
        ab, aa = quote_gate(0.50, 0.46, 0.54, 0.03)
        self.assertTrue(ab and aa)


if __name__ == "__main__":
    unittest.main()
