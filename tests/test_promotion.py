"""Tests for the Option-1 promotion pipeline pure logic: executable book-side mapping
(pmodds.executable_sides), the golf settlement window fix, and the promotion gate.
Run: PYTHONPATH=. python -m unittest tests.test_promotion -v"""
import json
import unittest

from core import pmodds
from core.settle import _golf_window
from scripts.promotion_gate import gate_report


def _mkt(outcomes, prices):
    return {"outcomes": json.dumps(outcomes), "outcomePrices": json.dumps(prices)}


class TestExecutableSides(unittest.TestCase):
    def test_book_maps_to_first_outcome_and_complements(self):
        m = _mkt(["New York Yankees", "Detroit Tigers"], ["0.57", "0.43"])
        quotes, side0, drift = pmodds.executable_sides(
            m, [(0.56, 100)], [(0.58, 80)], "New York Yankees", "Detroit Tigers")
        self.assertEqual(side0, "home")
        self.assertEqual(quotes["home"], {"bid": 0.56, "ask": 0.58})
        self.assertEqual(quotes["away"], {"bid": 0.42, "ask": 0.44})   # complement
        self.assertAlmostEqual(drift, 0.0, places=4)

    def test_away_first_outcome(self):
        m = _mkt(["Detroit Tigers", "New York Yankees"], ["0.43", "0.57"])
        quotes, side0, _ = pmodds.executable_sides(
            m, [(0.42, 50)], [(0.44, 50)], "New York Yankees", "Detroit Tigers")
        self.assertEqual(side0, "away")
        self.assertEqual(quotes["home"], {"bid": 0.56, "ask": 0.58})

    def test_unmappable_outcomes_or_no_book(self):
        m = _mkt(["Yes", "No"], ["0.5", "0.5"])   # no team names to map
        self.assertEqual(pmodds.executable_sides(m, [(0.5, 1)], [(0.51, 1)], "A", "B")[0],
                         None)
        m2 = _mkt(["New York Yankees", "Detroit Tigers"], ["0.5", "0.5"])
        self.assertEqual(pmodds.executable_sides(m2, [], [], "New York Yankees",
                                                 "Detroit Tigers")[0], None)

    def test_one_sided_book_gives_partial_quotes(self):
        # pre-game books are often one-sided — the present side maps, the missing side
        # is None and callers skip rows they can't price (was: whole market unmappable,
        # which starved the MLB taker of candidates all day).
        m = _mkt(["New York Yankees", "Detroit Tigers"], ["0.5", "0.5"])
        quotes, side0, _ = pmodds.executable_sides(m, [], [(0.5, 1)],
                                                   "New York Yankees", "Detroit Tigers")
        self.assertEqual(side0, "home")
        self.assertEqual(quotes["home"], {"bid": None, "ask": 0.5})
        self.assertEqual(quotes["away"], {"bid": 0.5, "ask": None})


class TestExactDatePreference(unittest.TestCase):
    def test_exact_date_market_beats_yesterdays_resolved_one(self):
        # the ±1-day tolerance (UTC vs ET) let today's game match YESTERDAY'S resolved
        # market when both dates were in the catalog — exact date must win the sort.
        idx = pmodds.build_index([
            {"slug": "aec-mlb-cin-mil-2026-07-01", "outcome": ""},
            {"slug": "aec-mlb-cin-mil-2026-07-02", "outcome": ""},
        ])
        hits = pmodds.find_market_slugs(idx, "Milwaukee Brewers", "Cincinnati Reds",
                                        "2026-07-02", "mil", "cin")
        self.assertEqual(hits[0][0], "aec-mlb-cin-mil-2026-07-02")


class TestGolfWindow(unittest.TestCase):
    def test_window_spans_back_to_tournament_start(self):
        # settle_date = Sunday 2026-06-28; a Thu 06-25 start must be inside the window
        self.assertEqual(_golf_window("2026-06-28"), "20260622-20260629")

    def test_bad_date_falls_through(self):
        self.assertEqual(_golf_window("garbage"), "garbage")


class TestGateReport(unittest.TestCase):
    def _row(self, prob, ask, y, bid=None, execu=True, model="m"):
        return {"model": model, "model_prob": prob, "market_ask": ask, "market_bid": bid,
                "settled": True, "realized_yes": y,
                "meta": {"odds_at": "t"} if execu else {}}

    def test_snapshot_rows_do_not_count(self):
        rows = [self._row(0.6, 0.5, True, execu=False)] * 5
        g = gate_report(rows)["m"]
        self.assertEqual(g["n_exec"], 0)
        self.assertEqual(g["n_snap"], 5)
        self.assertFalse(g["eligible"])

    def test_gate_requires_all_three_checks(self):
        # 100 executable rows where the model is sharp (prob=1 on winners) and the
        # market asks 0.5 -> buys win, brier_model < brier_market, sim positive
        rows = [self._row(0.9, 0.5, True) for _ in range(100)]
        g = gate_report(rows)["m"]
        self.assertEqual(g["n_exec"], 100)
        self.assertTrue(g["eligible"])
        self.assertGreater(g["sim_pnl"], 0)
        # same but only 99 rows -> n check fails
        g99 = gate_report(rows[:99])["m"]
        self.assertFalse(g99["eligible"])

    def test_sell_side_uses_bid(self):
        # model says 0.2, bid 0.4 -> sell at bid; outcome NO -> collect 0.4 each
        rows = [self._row(0.2, 0.42, False, bid=0.4) for _ in range(3)]
        g = gate_report(rows)["m"]
        self.assertEqual(g["n_bets"], 3)
        self.assertAlmostEqual(g["sim_pnl"], 1.2, places=2)


if __name__ == "__main__":
    unittest.main()
