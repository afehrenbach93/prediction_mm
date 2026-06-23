"""Tests for core/soccerfeed.py — pure parser on a sample ESPN payload (no network).
Run: PYTHONPATH=. python -m unittest tests.test_soccerfeed -v"""
import unittest

from core import soccerfeed as sf

SAMPLE = {
    "events": [
        {"id": "401", "date": "2026-06-01T19:00Z",
         "status": {"type": {"state": "post", "completed": True}},
         "competitions": [{"competitors": [
             {"homeAway": "home", "team": {"displayName": "Arsenal"}, "score": "2"},
             {"homeAway": "away", "team": {"displayName": "Chelsea  FC"}, "score": "1"},
         ]}]},
        {"id": "402", "date": "2026-06-02T19:00Z",
         "status": {"type": {"state": "pre", "completed": False}},
         "competitions": [{"competitors": [
             {"homeAway": "home", "team": {"displayName": "Liverpool"}, "score": None},
             {"homeAway": "away", "team": {"displayName": "Everton"}},
         ]}]},
        {"id": "403", "date": "2026-06-02T21:00Z",
         "status": {"type": {"state": "in", "completed": False}},
         "competitions": [{"competitors": [
             {"homeAway": "home", "team": {"displayName": "Spurs"}, "score": "0"},
             {"homeAway": "away", "team": {"displayName": "Leeds"}, "score": "0"},
         ]}]},
    ]
}


class TestParse(unittest.TestCase):
    def test_parses_all_events(self):
        ms = sf.parse_scoreboard(SAMPLE)
        self.assertEqual(len(ms), 3)

    def test_finished_match_fields(self):
        m = sf.parse_scoreboard(SAMPLE)[0]
        self.assertEqual(m["home"], "arsenal")
        self.assertEqual(m["away"], "chelsea fc")   # whitespace normalized
        self.assertEqual((m["home_score"], m["away_score"]), (2, 1))
        self.assertTrue(m["completed"])

    def test_missing_score_is_none(self):
        m = sf.parse_scoreboard(SAMPLE)[1]
        self.assertIsNone(m["home_score"])
        self.assertIsNone(m["away_score"])

    def test_empty_and_malformed_safe(self):
        self.assertEqual(sf.parse_scoreboard({}), [])
        self.assertEqual(sf.parse_scoreboard({"events": [{"id": "x"}]}), [])

    def test_normalize_team(self):
        self.assertEqual(sf.normalize_team("  Real   Madrid "), "real madrid")
        self.assertEqual(sf.normalize_team(None), "")


class TestFilters(unittest.TestCase):
    def setUp(self):
        # exercise the filters directly against the parsed sample
        self.ms = sf.parse_scoreboard(SAMPLE)

    def test_recent_results_filter(self):
        done = [m for m in self.ms if m["completed"]
                and m["home_score"] is not None and m["away_score"] is not None]
        self.assertEqual([m["id"] for m in done], ["401"])

    def test_upcoming_filter(self):
        pre = [m for m in self.ms if m["state"] == "pre"]
        self.assertEqual([m["id"] for m in pre], ["402"])

    def test_elo_seed_from_results(self):
        from lib import soccer as sc
        t = sc.EloTable()
        done = [m for m in self.ms if m["completed"]]
        for m in done:
            t.observe(m["home"], m["away"], m["home_score"], m["away_score"])
        self.assertGreater(t.rating("arsenal"), t.rating("chelsea fc"))


if __name__ == "__main__":
    unittest.main()
