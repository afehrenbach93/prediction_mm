"""Tests for the generic multi-sport stack: espnfeed parser, lib/elo, sportstrack
row builder, settle.settle_sport. Pure, no network.
Run: PYTHONPATH=. python -m unittest tests.test_sports -v"""
import unittest

from core import espnfeed, sportstrack, settle
from lib import elo

# NBA-style payload (team), plus a tennis-style payload (athlete, no homeAway)
NBA = {"events": [
    {"id": "1", "date": "2026-06-20T23:00Z",
     "status": {"type": {"state": "post", "completed": True}},
     "competitions": [{"competitors": [
         {"homeAway": "home", "team": {"displayName": "Boston Celtics"}, "score": "110"},
         {"homeAway": "away", "team": {"displayName": "Miami Heat"}, "score": "98"}]}]},
    {"id": "2", "date": "2026-06-24T23:00Z",
     "status": {"type": {"state": "pre", "completed": False}},
     "competitions": [{"neutralSite": True, "competitors": [
         {"homeAway": "home", "team": {"displayName": "Boston Celtics"}, "score": None},
         {"homeAway": "away", "team": {"displayName": "Miami Heat"}, "score": None}]}]},
]}
TENNIS = {"events": [
    {"id": "9", "date": "2026-06-24T12:00Z",
     "status": {"type": {"state": "pre", "completed": False}},
     "competitions": [{"competitors": [
         {"athlete": {"displayName": "Carlos Alcaraz"}, "score": {"value": None}},
         {"athlete": {"displayName": "Jannik Sinner"}, "score": {"value": None}}]}]},
]}


class TestEspnFeed(unittest.TestCase):
    def test_team_parse(self):
        ms = espnfeed.parse_scoreboard(NBA)
        self.assertEqual(len(ms), 2)
        self.assertEqual(ms[0]["home"], "boston celtics")
        self.assertEqual((ms[0]["home_score"], ms[0]["away_score"]), (110, 98))
        self.assertTrue(ms[1]["neutral"])

    def test_tennis_athlete_parse(self):
        m = espnfeed.parse_scoreboard(TENNIS)[0]
        self.assertEqual(m["home"], "carlos alcaraz")     # first competitor
        self.assertEqual(m["away"], "jannik sinner")
        self.assertEqual(m["state"], "pre")

    def test_finals_map_completed_only(self):
        fm = {m["id"]: m for m in espnfeed.parse_scoreboard(NBA)
              if m["completed"]}
        self.assertEqual(list(fm), ["1"])


class TestElo(unittest.TestCase):
    def test_win_probs_sum_to_one(self):
        e = elo.Elo()
        self.assertAlmostEqual(sum(e.win_probs("a", "b")), 1.0, places=9)

    def test_home_advantage(self):
        e = elo.Elo()
        ph, pa = e.win_probs("a", "b")          # equal ratings, HFA -> home favored
        self.assertGreater(ph, pa)

    def test_neutral_no_advantage(self):
        e = elo.Elo(neutral=True)
        ph, pa = e.win_probs("a", "b")
        self.assertAlmostEqual(ph, 0.5, places=6)

    def test_seed_moves_winner_up(self):
        e = elo.Elo().seed(espnfeed.parse_scoreboard(NBA)[:1])
        self.assertGreater(e.rating("boston celtics"), e.rating("miami heat"))

    def test_tie_ignored(self):
        e = elo.Elo()
        e.observe("a", "b", 5, 5)
        self.assertEqual(e.rating("a"), elo.BASE_RATING)


class TestRowBuilder(unittest.TestCase):
    def test_builds_two_rows_per_fixture(self):
        ms = espnfeed.parse_scoreboard(NBA)
        recent = [m for m in ms if m["completed"]]
        fixtures = [m for m in ms if m["state"] == "pre"]
        rows = sportstrack.build_sport_rows("nba", "basketball/nba", False,
                                            recent, fixtures, "2026-06-23")
        self.assertEqual(len(rows), 2)          # home + away
        self.assertEqual({r["outcome"] for r in rows}, {"home", "away"})
        self.assertAlmostEqual(sum(r["model_prob"] for r in rows), 1.0, places=3)
        self.assertEqual(rows[0]["model"], "elo-nba")
        self.assertEqual(rows[0]["meta"]["espn_path"], "basketball/nba")

    def test_registry_has_six_minus_golf(self):
        self.assertEqual(set(sportstrack.SPORTS),
                         {"nba", "nfl", "ncaaf", "mlb", "atp", "wta"})


class TestSettleSport(unittest.TestCase):
    def _rows(self):
        base = {"settle_date": "2026-06-20", "market_ask": None,
                "meta": {"espn_path": "basketball/nba", "espn_id": "1"}}
        return [dict(base, id=i, outcome=o) for i, o in [(1, "home"), (2, "away")]]

    def test_home_win_resolves(self):
        finals = lambda path, dt: {"1": {"home_score": 110, "away_score": 98}}
        res = settle.settle_sport(self._rows(), finals)
        self.assertEqual((res[1][0], res[2][0]), (True, False))

    def test_not_final_skips(self):
        self.assertEqual(settle.settle_sport(self._rows(), lambda p, d: {}), {})

    def test_tie_skipped(self):
        finals = lambda path, dt: {"1": {"home_score": 100, "away_score": 100}}
        self.assertEqual(settle.settle_sport(self._rows(), finals), {})

    def test_date_formatted_for_espn(self):
        seen = []
        settle.settle_sport(self._rows(), lambda p, d: seen.append(d) or {})
        self.assertEqual(seen, ["20260620"])


if __name__ == "__main__":
    unittest.main()
