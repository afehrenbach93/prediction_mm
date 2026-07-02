"""Tests for the multi-user execution filter + the tracked model variants
(elo-mlb-ctx rest adjustment, pitcher capture, blend/ctx rows never reach the
order path). No network.
Run: PYTHONPATH=. python -m unittest tests.test_multiuser_models -v"""
import datetime as dt
import unittest

from core import mlbtaker
from core import sportstrack as st
from core.espnfeed import parse_scoreboard

RECENT = [{"home": "a", "away": "b", "date": "2026-06-30T00:00Z"},
          {"home": "c", "away": "d", "date": "2026-06-28T00:00Z"}]
FX = {"id": "1", "date": "2026-07-02T17:00Z", "home": "a", "away": "b",
      "home_raw": "A", "away_raw": "B", "home_pitcher": "p1", "away_pitcher": "p2"}


class TestCtxVariant(unittest.TestCase):
    def test_rest_days(self):
        self.assertEqual(st.rest_days(RECENT, "a", "2026-07-02"), 2)
        self.assertEqual(st.rest_days(RECENT, "d", "2026-07-02"), 4)
        self.assertIsNone(st.rest_days(RECENT, "zz", "2026-07-02"))

    def test_ctx_adjust_is_small_and_guarded(self):
        p = st.ctx_adjust(0.5, 4, 1)
        self.assertGreater(p, 0.5)
        self.assertLess(p, 0.56)                    # capped, mild by design
        self.assertEqual(st.ctx_adjust(0.5, None, 1), 0.5)   # unknown rest -> no-op

    def test_mlb_emits_ctx_variant_with_pitchers(self):
        rows = st.build_sport_rows("mlb", "baseball/mlb", False, RECENT, [FX], "2026-07-02")
        self.assertEqual(sorted({r["model"] for r in rows}), ["elo-mlb", "elo-mlb-ctx"])
        ctx = next(r for r in rows if r["model"] == "elo-mlb-ctx")
        self.assertEqual(ctx["meta"]["sp_home"], "p1")
        self.assertEqual(ctx["meta"]["rest_home"], 2)

    def test_other_sports_stay_single_model(self):
        rows = st.build_sport_rows("nba", "basketball/nba", False, RECENT, [FX], "2026-07-02")
        self.assertEqual({r["model"] for r in rows}, {"elo-nba"})


class TestTakerModelFilter(unittest.TestCase):
    def test_variant_and_blend_rows_never_reach_the_order_path(self):
        now = dt.datetime.fromisoformat("2026-07-02T17:00+00:00").timestamp()
        row = {"model": "blend-mlb", "outcome": "home", "model_prob": 0.9,
               "market_ask": 0.5, "meta": {"pm_slug": "aec-mlb-x", "odds_at": "t",
                                           "book_side0": "home",
                                           "kickoff": "2026-07-02T17:30Z"}}
        self.assertEqual(mlbtaker.candidates([row], now), [])
        self.assertEqual(mlbtaker.candidates([dict(row, model="elo-mlb-ctx")], now), [])
        self.assertEqual(len(mlbtaker.candidates([dict(row, model="elo-mlb")], now)), 1)


class TestPitcherParse(unittest.TestCase):
    def test_probables_captured(self):
        raw = {"events": [{"id": "9", "date": "2026-07-02T17:00Z",
                           "status": {"type": {"state": "pre", "completed": False}},
                           "competitions": [{"id": "9", "competitors": [
                               {"homeAway": "home", "team": {"displayName": "A"},
                                "probables": [{"athlete": {"displayName": "Ace Starter"}}]},
                               {"homeAway": "away", "team": {"displayName": "B"}},
                           ]}]}]}
        m = parse_scoreboard(raw)[0]
        self.assertEqual(m["home_pitcher"], "ace starter")
        self.assertEqual(m["away_pitcher"], "")


if __name__ == "__main__":
    unittest.main()
