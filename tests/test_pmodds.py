"""Tests for core/pmodds pure helpers (catalog index + slug matcher). No network.
Run: PYTHONPATH=. python -m unittest tests.test_pmodds -v"""
import unittest

from core import pmodds


class TestPmodds(unittest.TestCase):
    def test_norm_tokens(self):
        self.assertEqual(pmodds.norm_tokens("Boston Celtics"), {"boston", "celtics"})
        self.assertEqual(pmodds.norm_tokens(None), set())

    def test_build_index_extracts_date_and_tokens(self):
        idx = pmodds.build_index([{"slug": "aec-mlb-bos-nyy-2026-06-28"}, {"slug": ""}])
        self.assertEqual(len(idx), 1)
        slug, toks, date = idx[0]
        self.assertEqual(date, "2026-06-28")
        self.assertIn("bos", toks)
        self.assertIn("mlb", toks)

    def test_find_market_slug_matches_on_tokens_and_date(self):
        idx = pmodds.build_index([
            {"slug": "aec-nba-boston-miami-2026-06-28"},
            {"slug": "aec-nba-la-sf-2026-06-28"},
        ])
        # both team names share a token with the slug -> score 2 -> match
        hit = pmodds.find_market_slug(idx, "Boston Celtics", "Miami Heat", "2026-06-28")
        self.assertEqual(hit, "aec-nba-boston-miami-2026-06-28")

    def test_find_market_slug_date_filter(self):
        idx = pmodds.build_index([{"slug": "aec-mlb-boston-newyork-2026-06-27"}])
        # right teams, wrong date -> no match
        self.assertIsNone(pmodds.find_market_slug(idx, "Boston", "New York", "2026-06-28"))

    def test_find_market_slug_below_threshold(self):
        idx = pmodds.build_index([{"slug": "aec-mlb-xx-yy-2026-06-28"}])
        self.assertIsNone(pmodds.find_market_slug(idx, "Boston", "New York", "2026-06-28"))


if __name__ == "__main__":
    unittest.main()
