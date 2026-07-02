"""Tests for core/mlbtaker pure selection/order/safety. No network.
Run: PYTHONPATH=. python -m unittest tests.test_mlbtaker -v"""
import unittest

from core import mlbtaker

NOW = 1_700_000_000.0


def _row(slug, outcome, prob, ask, ko_offset=3600, odds_at="t", side0="home"):
    from datetime import datetime, timezone
    ko = datetime.fromtimestamp(NOW + ko_offset, timezone.utc).isoformat()
    return {"outcome": outcome, "model_prob": prob, "market_ask": ask,
            "meta": {"pm_slug": slug, "odds_at": odds_at, "book_side0": side0,
                     "kickoff": ko}}


class TestCandidates(unittest.TestCase):
    def test_edge_window_and_one_per_game(self):
        rows = [
            _row("aec-mlb-a-b-1", "home", 0.60, 0.50),          # edge .10 -> in
            _row("aec-mlb-a-b-1", "away", 0.56, 0.50),          # same game, worse edge
            _row("aec-mlb-c-d-1", "home", 0.52, 0.50),          # edge .02 -> out
            _row("aec-mlb-e-f-1", "home", 0.70, 0.50, ko_offset=20000),  # too far out
            _row("aec-mlb-g-h-1", "home", 0.70, 0.50, ko_offset=-60),    # started
            _row("aec-mlb-i-j-1", "home", 0.70, 0.50, odds_at=None),     # no exec odds
        ]
        cands = mlbtaker.candidates(rows, NOW)
        self.assertEqual([c["slug"] for c in cands], ["aec-mlb-a-b-1"])
        self.assertEqual(cands[0]["outcome"], "home")


class TestOrderFor(unittest.TestCase):
    def test_long_book_side_rests_inside_spread(self):
        intent, px, cpc = mlbtaker.order_for("home", "home", 0.55, 0.58)
        self.assertEqual(intent, "ORDER_INTENT_BUY_LONG")
        self.assertEqual(px, 0.57)                 # ask-0.01
        self.assertEqual(cpc, 0.57)
        # 1-tick spread: floors to the bid (post-only can never cross)
        _, px2, _ = mlbtaker.order_for("home", "home", 0.55, 0.56)
        self.assertEqual(px2, 0.55)

    def test_fade_book_side_is_buy_short(self):
        intent, px, cpc = mlbtaker.order_for("away", "home", 0.55, 0.58)
        self.assertEqual(intent, "ORDER_INTENT_BUY_SHORT")
        self.assertEqual(px, 0.56)                 # bid+0.01, capped at ask
        self.assertAlmostEqual(cpc, 0.44, places=4)

    def test_bad_inputs(self):
        self.assertIsNone(mlbtaker.order_for("home", "", 0.5, 0.6))
        self.assertIsNone(mlbtaker.order_for("home", "home", None, 0.6))
        self.assertIsNone(mlbtaker.order_for("home", "home", 0.7, 0.6))  # crossed book


class TestSafety(unittest.TestCase):
    def test_stale_orders_at_or_past_kickoff(self):
        oo = [{"id": "1", "marketSlug": "aec-mlb-a-b"},
              {"id": "2", "marketSlug": "aec-mlb-c-d"},
              {"id": "3", "marketSlug": "aec-mlb-e-f"},
              {"id": "4", "marketSlug": "tc-temp-x"}]        # not ours
        kos = {"aec-mlb-a-b": NOW - 10,      # started -> stale
               "aec-mlb-c-d": NOW + 600}     # pre-game -> keep
        # e-f unknown kickoff -> stale (fail safe)
        stale = mlbtaker.stale_order_ids(oo, kos, NOW)
        self.assertEqual(sorted(i for i, _ in stale), ["1", "3"])

    def test_wrong_direction_uses_expected_sign(self):
        pos = {"a": {"netPosition": "5"}, "b": {"netPosition": "-3"}, "c": {"netPosition": 0}}
        bad = mlbtaker.wrong_direction(pos, {"a": 1, "b": 1, "c": -1})
        self.assertEqual(bad, ["b"])   # we opened b LONG but it shows short


if __name__ == "__main__":
    unittest.main()
