"""
Tests for US reward scoring (core.rewardscore).
Run: PYTHONPATH=. python -m unittest tests.test_rewardscore -v
"""
import unittest

from core.rewardscore import (
    book_competition_score,
    capture_share,
    estimate_reward,
    level_score,
    mid_in_band,
    my_touch_score,
    score_market,
    slug_denied,
    ticks_from_best,
)


class TestTicks(unittest.TestCase):
    def test_best_is_zero(self):
        self.assertEqual(ticks_from_best(0.50, 0.50, 0.001, "bid"), 0)
        self.assertEqual(ticks_from_best(0.51, 0.51, 0.001, "ask"), 0)

    def test_one_tick_off(self):
        self.assertEqual(ticks_from_best(0.499, 0.50, 0.001, "bid"), 1)
        self.assertEqual(ticks_from_best(0.511, 0.51, 0.001, "ask"), 1)


class TestLevelScore(unittest.TestCase):
    def test_touch_full(self):
        self.assertEqual(level_score(100, 0, 0.3), 100.0)

    def test_one_tick_discount(self):
        self.assertAlmostEqual(level_score(100, 1, 0.3), 30.0)


class TestBookScore(unittest.TestCase):
    def test_both_sides_touch(self):
        bids = [(0.50, 100)]
        asks = [(0.51, 200)]
        # touch only → 100 + 200
        self.assertAlmostEqual(book_competition_score(bids, asks, 0.3), 300.0)

    def test_off_touch_discounted(self):
        bids = [(0.50, 100), (0.499, 100)]  # second level 1 tick off
        asks = [(0.51, 100)]
        # 100 + 100*0.3 + 100 = 230
        self.assertAlmostEqual(book_competition_score(bids, asks, 0.3), 230.0)


class TestCapture(unittest.TestCase):
    def test_share_and_reward(self):
        mine = my_touch_score(50)  # 100
        book = 300
        self.assertAlmostEqual(capture_share(mine, book), 100 / 400)
        self.assertAlmostEqual(estimate_reward(1000, mine, book), 250.0)

    def test_empty_book_is_near_zero(self):
        bids = [(0.40, 1)]
        asks = [(0.42, 1)]
        sr = score_market(bids, asks, pool=100, budget=500, discount=0.3,
                          near_zero_threshold=50)
        self.assertTrue(sr.near_zero)
        self.assertGreater(sr.share, 0.9)

    def test_competed_not_near_zero(self):
        bids = [(0.50, 5000)]
        asks = [(0.51, 5000)]
        sr = score_market(bids, asks, pool=1000, budget=200, discount=0.3)
        self.assertFalse(sr.near_zero)
        self.assertLess(sr.share, 0.1)


class TestFilters(unittest.TestCase):
    def test_mid_band(self):
        self.assertTrue(mid_in_band(0.50))
        self.assertFalse(mid_in_band(0.05))
        self.assertFalse(mid_in_band(0.95))

    def test_deny_prefix(self):
        self.assertTrue(slug_denied("aec-cod-van-lat", ["aec-cod-"]))
        self.assertFalse(slug_denied("tec-f-wc-2026", ["aec-cod-"]))


if __name__ == "__main__":
    unittest.main()
