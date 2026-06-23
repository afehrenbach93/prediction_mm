"""Tests for the weather edge model (lib/weather.py). Pure, no network.
Run: PYTHONPATH=. python -m unittest tests.test_weather -v"""
import unittest

from lib import weather as wx


class TestParseSlug(unittest.TestCase):
    def test_between_bucket(self):
        d = wx.parse_temp_slug("tc-temp-nychigh-2026-06-23-gte84lt85f")
        self.assertEqual((d["station"], d["lo"], d["hi"]), ("nyc", 84.0, 85.0))
        self.assertEqual(d["date"], "2026-06-23")

    def test_upper_open(self):
        d = wx.parse_temp_slug("tc-temp-miahigh-2026-06-23-gte97f")
        self.assertEqual((d["lo"], d["hi"]), (97.0, None))

    def test_lower_open(self):
        d = wx.parse_temp_slug("tc-temp-nychigh-2026-06-23-lt78f")
        self.assertEqual((d["lo"], d["hi"]), (None, 78.0))

    def test_city_label(self):
        d = wx.parse_temp_slug("tc-temp-sfohigh-2026-06-23-gte70lt71f")
        self.assertIn("San Francisco", d["city"])

    def test_non_temp_slug(self):
        self.assertIsNone(wx.parse_temp_slug("aec-dota2-mouz-icxi-2026-06-24"))


class TestBucketProbability(unittest.TestCase):
    def test_centered_bucket_gets_most_mass(self):
        # forecast 84.5, tight sigma -> 84..85 bucket should hold most probability
        p = wx.bucket_probability(84.5, 1.0, 84.0, 85.0)
        self.assertGreater(p, 0.34)
        self.assertLessEqual(p, 1.0)

    def test_far_bucket_near_zero(self):
        p = wx.bucket_probability(84.5, 1.5, 95.0, 96.0)
        self.assertLess(p, 0.001)

    def test_open_ends_sum_to_one(self):
        # the full partition (<78, 78-95 in 1-wide, >=95) must sum to ~1
        total = wx.bucket_probability(86.0, 3.0, None, 78.0)
        total += sum(wx.bucket_probability(86.0, 3.0, t, t + 1) for t in range(78, 95))
        total += wx.bucket_probability(86.0, 3.0, 95.0, None)
        self.assertAlmostEqual(total, 1.0, places=6)

    def test_sigma_zero_is_point_mass(self):
        self.assertEqual(wx.bucket_probability(84.0, 0.0, 84.0, 85.0), 1.0)
        self.assertEqual(wx.bucket_probability(90.0, 0.0, 84.0, 85.0), 0.0)


class TestEdge(unittest.TestCase):
    def test_positive_edge_when_underpriced(self):
        e = wx.buy_edge(0.40, 0.25, fee=0.02)
        self.assertAlmostEqual(e, 0.13, places=6)

    def test_negative_edge_when_overpriced(self):
        self.assertLess(wx.buy_edge(0.20, 0.35), 0.0)

    def test_no_ask_no_edge(self):
        self.assertIsNone(wx.buy_edge(0.5, None))

    def test_taker_fee_peaks_midprice(self):
        self.assertGreater(wx.taker_fee(0.5), wx.taker_fee(0.9))


if __name__ == "__main__":
    unittest.main()
