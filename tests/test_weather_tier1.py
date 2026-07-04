"""Tests for the weather Tier-1 changes (intraday-floor bucket probability, non-boundary
sell filter) and the tennis TBD-fixture skip. No network.
Run: PYTHONPATH=. python -m unittest tests.test_weather_tier1 -v"""
import unittest

import core.espnfeed as espnfeed
from core import wxtaker
from lib import weather as wx


class TestIntradayFloor(unittest.TestCase):
    def test_bucket_below_observed_max_is_impossible(self):
        # observed max-so-far 88; a bucket capped at 85 can't be the daily high
        self.assertEqual(wx.bucket_probability(90, 3, 84, 85, floor=88), 0.0)

    def test_floor_renormalizes_within_unit_interval(self):
        for lo, hi in [(87, 88), (89, 90), (None, 80), (95, None)]:
            p = wx.bucket_probability(88, 3, lo, hi, floor=88)
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)

    def test_no_floor_matches_plain_normal(self):
        self.assertAlmostEqual(wx.bucket_probability(88, 3, 87, 88),
                               wx.bucket_probability(88, 3, 87, 88, floor=None))

    def test_degenerate_sigma_respects_floor(self):
        # sigma 0, forecast 84 but already observed 89 -> high is 89, lands in [89,90)
        self.assertEqual(wx.bucket_probability(84, 0, 89, 90, floor=89), 1.0)
        self.assertEqual(wx.bucket_probability(84, 0, 84, 85, floor=89), 0.0)


class TestNonBoundaryFilter(unittest.TestCase):
    def test_boundary_bucket_excluded_even_with_edge(self):
        buckets = [
            {"slug": "boundary", "prob": 0.35, "bid": 0.55, "bid_qty": 50},  # edge .20 but risky
            {"slug": "deep", "prob": 0.03, "bid": 0.20, "bid_qty": 50},      # edge .17, safe
        ]
        got = wxtaker.sell_candidates(buckets, margin=0.10)
        self.assertEqual([c["slug"] for c in got], ["deep"])

    def test_max_prob_threshold_configurable(self):
        buckets = [{"slug": "m", "prob": 0.20, "bid": 0.40, "bid_qty": 10}]
        self.assertEqual(wxtaker.sell_candidates(buckets, margin=0.10, max_prob=0.15), [])
        self.assertEqual(len(wxtaker.sell_candidates(buckets, margin=0.10, max_prob=0.25)), 1)


class TestTbdFixtureSkip(unittest.TestCase):
    def _raw(self, hp, ap, cid):
        return {"id": cid, "date": "2026-07-06T04:00Z",
                "status": {"type": {"state": "pre", "completed": False}},
                "competitions": [{"id": cid, "competitors": [
                    {"homeAway": "home", "athlete": {"displayName": hp}},
                    {"homeAway": "away", "athlete": {"displayName": ap}}]}]}

    def test_tbd_placeholders_are_skipped(self):
        raw = {"events": [self._raw("TBD", "TBD", "1"),
                          self._raw("Carlos Alcaraz", "Jannik Sinner", "2")]}
        parsed = espnfeed.parse_scoreboard(raw)
        orig = espnfeed.fetch
        espnfeed.fetch = lambda path, dates=None: parsed
        try:
            fx = espnfeed.upcoming_fixtures("tennis/atp")
        finally:
            espnfeed.fetch = orig
        self.assertEqual([m["home_raw"] for m in fx], ["Carlos Alcaraz"])

    def test_is_tbd_variants(self):
        for n in ("TBD", "tbd", "", "  ", "To Be Determined", "Bye"):
            self.assertTrue(espnfeed._is_tbd(n))
        self.assertFalse(espnfeed._is_tbd("Coco Gauff"))


if __name__ == "__main__":
    unittest.main()
