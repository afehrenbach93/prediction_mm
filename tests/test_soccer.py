"""Tests for the soccer Elo model core (lib/soccer.py). Pure, no network.
Run: PYTHONPATH=. python -m unittest tests.test_soccer -v"""
import unittest

from lib import soccer as sc


class TestElo(unittest.TestCase):
    def test_equal_teams_expected_half_plus_hfa(self):
        # equal ratings, home advantage -> home expectation > 0.5
        self.assertGreater(sc.expected_score(1500, 1500, hfa=60), 0.5)
        self.assertAlmostEqual(sc.expected_score(1500, 1500, hfa=0), 0.5, places=6)

    def test_update_moves_toward_result(self):
        # home wins when expected 0.5 -> rating rises
        new = sc.update_elo(1500, 0.5, 1.0, k=20)
        self.assertEqual(new, 1510.0)

    def test_zero_sum_update(self):
        t = sc.EloTable(base=1500)
        before = t.rating("A") + t.rating("B")
        t.observe("A", "B", 2, 0)
        after = t.rating("A") + t.rating("B")
        self.assertAlmostEqual(before, after, places=6)   # Elo is zero-sum
        self.assertGreater(t.rating("A"), t.rating("B"))


class TestProbabilities(unittest.TestCase):
    def test_sums_to_one(self):
        for rh, ra in [(1500, 1500), (1700, 1400), (1300, 1600)]:
            p = sc.match_probabilities(rh, ra)
            self.assertAlmostEqual(sum(p), 1.0, places=6)

    def test_favorite_gets_more(self):
        p_home, _, p_away = sc.match_probabilities(1750, 1450)
        self.assertGreater(p_home, p_away)

    def test_draw_peaks_when_even(self):
        _, d_even, _ = sc.match_probabilities(1500, 1500, hfa=0)
        _, d_mismatch, _ = sc.match_probabilities(1800, 1300, hfa=0)
        self.assertGreater(d_even, d_mismatch)

    def test_all_probs_nonneg(self):
        for p in sc.match_probabilities(2000, 1200):
            self.assertGreaterEqual(p, 0.0)


class TestEdge(unittest.TestCase):
    def test_edge_signs(self):
        self.assertAlmostEqual(sc.buy_edge(0.55, 0.40, 0.02), 0.13, places=6)
        self.assertLess(sc.buy_edge(0.30, 0.45), 0.0)
        self.assertIsNone(sc.buy_edge(0.5, None))


if __name__ == "__main__":
    unittest.main()
