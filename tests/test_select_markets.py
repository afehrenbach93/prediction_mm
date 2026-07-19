"""
Tests for pilot market selection (est capture + filters).
Run: PYTHONPATH=. python -m unittest tests.test_select_markets -v
"""
import time
import unittest

import poly_runner as pr


class BookClient:
    def __init__(self, books: dict):
        # slug -> (bids, offers)
        self._books = books

    def get_book(self, slug):
        return self._books.get(slug, ([], []))


class TestSelectMarkets(unittest.TestCase):
    def setUp(self):
        pr.MIN_HOURS_TO_END = 72
        pr.MIN_MID, pr.MAX_MID = 0.10, 0.90
        pr.REQUIRE_COMPETED = True
        self.now = time.time()
        self.far = self.now + 10 * 86400
        self.soon = self.now + 24 * 3600  # 24h < 72h min

    def test_ranks_by_est_reward_and_skips_near_zero(self):
        # competed: deep book, modest pool
        # empty: near-zero book, huge pool — should be skipped when REQUIRE_COMPETED
        books = {
            "deep": ([(0.50, 5000)], [(0.51, 5000)]),
            # book_score 0.2 < NEAR_ZERO_BOOK_SCORE (1.0)
            "empty": ([(0.40, 0.1)], [(0.42, 0.1)]),
        }
        c = BookClient(books)
        windows = [
            ("empty", "daily_event", 5000.0, 0.3, self.far),
            ("deep", "daily_event", 200.0, 0.3, self.far),
        ]
        sel = pr.select_markets(c, windows, budget=200, now=self.now, max_markets=5)
        slugs = [s[0] for s in sel]
        self.assertIn("deep", slugs)
        self.assertNotIn("empty", slugs)

    def test_skips_near_expiry(self):
        books = {"m": ([(0.50, 5000)], [(0.51, 5000)])}
        c = BookClient(books)
        windows = [("m", "daily_event", 1000.0, 0.3, self.soon)]
        sel = pr.select_markets(c, windows, budget=200, now=self.now, max_markets=5)
        self.assertEqual(sel, [])

    def test_skips_extreme_mid(self):
        books = {"tail": ([(0.04, 5000)], [(0.05, 5000)])}
        c = BookClient(books)
        windows = [("tail", "daily_event", 1000.0, 0.3, self.far)]
        sel = pr.select_markets(c, windows, budget=200, now=self.now, max_markets=5)
        self.assertEqual(sel, [])

    def test_allows_near_zero_when_not_required(self):
        pr.REQUIRE_COMPETED = False
        books = {"empty": ([(0.40, 1)], [(0.42, 1)])}
        c = BookClient(books)
        windows = [("empty", "daily_event", 1000.0, 0.3, self.far)]
        sel = pr.select_markets(c, windows, budget=200, now=self.now, max_markets=5)
        self.assertEqual(len(sel), 1)
        self.assertEqual(sel[0][0], "empty")


class TestDenyList(unittest.TestCase):
    def test_slug_denied_helper(self):
        from core.rewardscore import slug_denied
        self.assertTrue(slug_denied("aec-cod-van-lat", ["aec-cod-"]))


if __name__ == "__main__":
    unittest.main()
