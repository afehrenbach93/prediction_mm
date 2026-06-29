"""Tests for core/pmodds pure helpers (catalog index + slug matcher). No network.
Run: PYTHONPATH=. python -m unittest tests.test_pmodds -v"""
import unittest

from core import pmodds


class TestPmodds(unittest.TestCase):
    def test_norm_tokens(self):
        self.assertEqual(pmodds.norm_tokens("Boston Celtics"), {"boston", "celtics"})
        self.assertEqual(pmodds.norm_tokens(None), set())

    def test_team_tokens_includes_abbr(self):
        toks = pmodds.team_tokens("New York Yankees", "NYY")
        self.assertIn("nyy", toks)
        self.assertIn("yankees", toks)

    def test_build_index_extracts_date_tokens_outcome(self):
        idx = pmodds.build_index([
            {"slug": "aec-mlb-bos-nyy-2026-06-28", "outcome": "New York Yankees"},
            {"slug": ""},
        ])
        self.assertEqual(len(idx), 1)
        slug, toks, date, outcome = idx[0]
        self.assertEqual(date, "2026-06-28")
        self.assertIn("bos", toks)
        self.assertEqual(outcome, "New York Yankees")

    def test_find_market_slug_matches_on_name_tokens(self):
        idx = pmodds.build_index([
            {"slug": "aec-nba-boston-miami-2026-06-28"},
            {"slug": "aec-nba-la-sf-2026-06-28"},
        ])
        hit = pmodds.find_market_slug(idx, "Boston Celtics", "Miami Heat", "2026-06-28")
        self.assertEqual(hit, "aec-nba-boston-miami-2026-06-28")

    def test_find_market_slug_matches_on_abbrev(self):
        # PM uses short codes; matching must work off ESPN abbreviations too.
        idx = pmodds.build_index([{"slug": "tec-mlb-bos-nyy-2026-06-28"}])
        hit = pmodds.find_market_slug(idx, "Boston Red Sox", "New York Yankees",
                                      "2026-06-28", home_abbr="BOS", away_abbr="NYY")
        self.assertEqual(hit, "tec-mlb-bos-nyy-2026-06-28")

    def test_find_market_slug_requires_both_teams(self):
        # only the away team is present -> not a head-to-head match for this game
        idx = pmodds.build_index([{"slug": "tec-mlb-nyy-champ-2026-06-28"}])
        self.assertIsNone(pmodds.find_market_slug(
            idx, "Boston Red Sox", "New York Yankees", "2026-06-28",
            home_abbr="BOS", away_abbr="NYY"))

    def test_find_market_slug_abbrev_alias(self):
        # ESPN 'chw' (White Sox) vs PM 'cws' — alias must bridge the gap
        idx = pmodds.build_index([{"slug": "aec-mlb-cws-bal-2026-06-29"}])
        hit = pmodds.find_market_slug(idx, "Chicago White Sox", "Baltimore Orioles",
                                      "2026-06-29", home_abbr="BAL", away_abbr="CHW")
        self.assertEqual(hit, "aec-mlb-cws-bal-2026-06-29")

    def test_build_index_label_from_alt_field(self):
        # PM populates the YES label under groupItemTitle (not 'outcome') for game markets
        idx = pmodds.build_index([{"slug": "aec-mlb-pit-phi-2026-06-29",
                                   "groupItemTitle": "Philadelphia Phillies"}])
        self.assertEqual(idx[0][3], "Philadelphia Phillies")

    def test_find_market_slug_date_filter_far(self):
        # >1 day off -> filtered out (date tolerance is ±1 day for TZ)
        idx = pmodds.build_index([{"slug": "aec-mlb-boston-newyork-2026-06-25"}])
        self.assertIsNone(pmodds.find_market_slug(idx, "Boston", "New York", "2026-06-28"))

    def test_find_market_slug_date_within_one_day(self):
        # ESPN UTC date vs PM ET date can differ by a day -> still matches
        idx = pmodds.build_index([{"slug": "aec-mlb-boston-newyork-2026-06-29"}])
        self.assertEqual(
            pmodds.find_market_slug(idx, "Boston", "New York", "2026-06-28"),
            "aec-mlb-boston-newyork-2026-06-29")

    def test_find_market_slug_below_threshold(self):
        idx = pmodds.build_index([{"slug": "aec-mlb-xx-yy-2026-06-28"}])
        self.assertIsNone(pmodds.find_market_slug(idx, "Boston", "New York", "2026-06-28"))

    def test_find_market_slugs_returns_all_sorted(self):
        idx = pmodds.build_index([
            {"slug": "tec-mlb-bos-nyy-2026-06-28-spread-extra"},
            {"slug": "tec-mlb-bos-nyy-2026-06-28"},
        ])
        hits = pmodds.find_market_slugs(idx, "Boston Red Sox", "New York Yankees",
                                        "2026-06-28", "BOS", "NYY")
        self.assertEqual(len(hits), 2)
        # most specific (fewest tokens) first
        self.assertEqual(hits[0][0], "tec-mlb-bos-nyy-2026-06-28")

    def test_side_of_maps_outcome_to_team(self):
        self.assertEqual(pmodds._side_of("New York Yankees", "Boston Red Sox",
                                         "New York Yankees"), "away")
        self.assertEqual(pmodds._side_of("Boston Red Sox", "Boston Red Sox",
                                         "New York Yankees"), "home")
        self.assertEqual(pmodds._side_of("", "Boston", "New York"), "")


if __name__ == "__main__":
    unittest.main()
