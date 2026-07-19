"""
Tests for CLOB quadratic reward scoring.
Run: PYTHONPATH=. python3 -m unittest tests.test_clobscore -v
"""
import unittest

from core.clobscore import (
    book_competition,
    estimate_capture,
    my_twosided_score,
    order_weight,
    q_min,
    score_market,
)


class TestOrderWeight(unittest.TestCase):
    def test_at_mid_full(self):
        self.assertAlmostEqual(order_weight(0.0, 4.5), 1.0)

    def test_half_max_is_quarter(self):
        # deep-dive: half max spread → weight 0.25
        self.assertAlmostEqual(order_weight(2.25, 4.5), 0.25)

    def test_outside_band_zero(self):
        self.assertEqual(order_weight(5.0, 4.5), 0.0)


class TestQMin(unittest.TestCase):
    def test_balanced_mid_band(self):
        self.assertAlmostEqual(q_min(100, 100, 0.5), 100)

    def test_one_sided_reduced(self):
        # max/c = 300/3 = 100; min=0 → max(0, 100) = 100
        self.assertAlmostEqual(q_min(300, 0, 0.5), 100)

    def test_tail_requires_both(self):
        self.assertAlmostEqual(q_min(300, 0, 0.05), 0)


class TestCapture(unittest.TestCase):
    def test_pro_rata(self):
        self.assertAlmostEqual(estimate_capture(100, 25, 75), 25.0)

    def test_empty_book_full_rate(self):
        self.assertAlmostEqual(estimate_capture(100, 50, 0), 100.0)


class TestMarketScore(unittest.TestCase):
    def test_half_spread_hypothetical(self):
        # mid 0.50, max_spread 4.5¢, budget $500 → 500 shares/side at weight 0.25
        # my_score = 0.25 * 500 = 125 (each side); Q_min = 125
        mine = my_twosided_score(500, 0.50, 4.5, 0.5)
        self.assertAlmostEqual(mine, 125.0)

    def test_competed_vs_empty(self):
        rewards = {"rates": [{"rewards_daily_rate": 100}], "min_size": 0, "max_spread": 4.5}
        # empty-ish book near mid
        empty = score_market([(0.49, 1)], [(0.51, 1)], rewards, budget=500,
                             near_zero_notional=50)
        deep = score_market([(0.50, 5000)], [(0.51, 5000)], rewards, budget=500,
                            near_zero_notional=50)
        self.assertTrue(empty.near_zero)
        self.assertFalse(deep.near_zero)
        self.assertGreater(empty.est_daily, deep.est_daily)

    def test_qualifying_band(self):
        # level outside max_spread should not count
        qmin, qraw, notion, mid = book_competition(
            [(0.50, 100), (0.40, 10000)],  # 0.40 is 10¢ away > 4.5¢
            [(0.51, 100)],
            max_spread_cents=4.5,
            min_sz=0,
        )
        self.assertAlmostEqual(mid, 0.505)
        # only the in-band sizes contribute to raw
        self.assertLess(qraw, 10000)


if __name__ == "__main__":
    unittest.main()
