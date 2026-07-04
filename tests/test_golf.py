"""Tests for the golf field/winner model: lib/golf, golffeed parser, settle_golf.
Pure, no network. Run: PYTHONPATH=. python -m unittest tests.test_golf -v"""
import unittest

from core import golffeed, settle
from lib import golf

# ESPN golf scoreboard: one competition whose competitors are the whole field.
FINISHED = {"events": [{
    "id": "G1", "name": "Test Open", "date": "2026-06-21T12:00Z",
    "status": {"type": {"state": "post", "completed": True}},
    "competitions": [{"competitors": [
        {"athlete": {"displayName": "Scottie Scheffler"}, "status": {"position": {"displayName": "1"}}},
        {"athlete": {"displayName": "Rory McIlroy"}, "status": {"position": {"displayName": "T2"}}},
        {"athlete": {"displayName": "Jon Rahm"}, "status": {"position": {"displayName": "T2"}}},
        {"athlete": {"displayName": "Cut Player"}, "status": {"position": {"displayName": "CUT"}}},
    ]}],
}]}
UPCOMING = {"events": [{
    "id": "G2", "name": "Next Open", "date": "2026-06-26T12:00Z",
    "status": {"type": {"state": "pre", "completed": False}},
    "competitions": [{"competitors": [
        {"athlete": {"displayName": "Scottie Scheffler"}, "status": {"position": {}}},
        {"athlete": {"displayName": "Rory McIlroy"}, "status": {"position": {}}},
    ]}],
}]}


class TestParse(unittest.TestCase):
    def test_field_and_positions(self):
        t = golffeed.parse_golf(FINISHED)[0]
        self.assertEqual(t["id"], "G1")
        self.assertTrue(t["completed"])
        pos = {p["player"]: p["position"] for p in t["field"]}
        self.assertEqual(pos["scottie scheffler"], 1)
        self.assertEqual(pos["rory mcilroy"], 2)      # 'T2' -> 2
        self.assertIsNone(pos["cut player"])          # 'CUT' -> None

    def test_winners_map(self):
        wm = {}
        for t in golffeed.parse_golf(FINISHED):
            if t["completed"]:
                wm[t["id"]] = next((p["player"] for p in t["field"] if p["position"] == 1), None)
        self.assertEqual(wm, {"G1": "scottie scheffler"})

    def test_position_reads_order_field(self):
        # PROD SHAPE (from worker DIAG): golf competitors have NO status; the finish rank
        # is in `order` (1 = winner). This is the actual fix.
        self.assertEqual(golffeed._position({"order": 1, "score": -4, "status": None}), 1)
        self.assertEqual(golffeed._position({"order": 12}), 12)

    def test_position_falls_back_to_status_position(self):
        # other sports/shapes may still use status.position.displayName
        self.assertEqual(golffeed._position({"status": {"position": {"displayName": "T5"}}}), 5)

    def test_winners_map_from_order(self):
        raw = {"events": [{
            "id": "GO", "name": "Order Open", "date": "2026-07-05T12:00Z",
            "status": {"type": {"state": "post", "completed": True}},
            "competitions": [{"competitors": [
                {"athlete": {"displayName": "J.J. Spaun"}, "order": 1, "score": -4, "status": None},
                {"athlete": {"displayName": "Robert MacIntyre"}, "order": 2, "score": -3, "status": None},
            ]}]}]}
        orig = golffeed.fetch
        golffeed.fetch = lambda path=golffeed.DEFAULT_TOUR, dates=None: golffeed.parse_golf(raw)
        try:
            self.assertEqual(golffeed.winners_map(), {"GO": "j.j. spaun"})
        finally:
            golffeed.fetch = orig

    def test_winners_map_via_winner_flag(self):
        # winner identified by ESPN's `won` flag even if the position field is unusable
        raw = {"events": [{
            "id": "GW", "name": "Flag Open", "date": "2026-07-05T12:00Z",
            "status": {"type": {"state": "post", "completed": True}},
            "competitions": [{"competitors": [
                {"athlete": {"displayName": "Hayden Springer"},
                 "winner": True, "status": {"position": {"id": "42"}}},
                {"athlete": {"displayName": "Lucas Glover"},
                 "status": {"position": {"id": "43", "displayName": "2"}}},
            ]}]}]}
        orig = golffeed.fetch
        golffeed.fetch = lambda path=golffeed.DEFAULT_TOUR, dates=None: golffeed.parse_golf(raw)
        try:
            self.assertEqual(golffeed.winners_map(), {"GW": "hayden springer"})
        finally:
            golffeed.fetch = orig


class TestModel(unittest.TestCase):
    def test_performance_bounds(self):
        self.assertEqual(golf.performance(1, 100), 1.0)     # winner
        self.assertEqual(golf.performance(100, 100), 0.0)   # last
        self.assertEqual(golf.performance(1, 1), 0.5)       # degenerate

    def test_seed_lifts_winner_skill(self):
        t = golf.SkillTable()
        field = [{"player": p["player"], "position": p["position"]}
                 for p in golffeed.parse_golf(FINISHED)[0]["field"]]
        t.observe_event(field)
        self.assertGreater(t.skill("scottie scheffler"), t.skill("rory mcilroy"))
        self.assertGreater(t.skill("rory mcilroy"), golf.BASE_SKILL - 0.01)

    def test_win_probs_sum_to_one_favorite_leads(self):
        t = golf.SkillTable(skills={"a": 0.9, "b": 0.5, "c": 0.3})
        p = t.win_probs(["a", "b", "c"])
        self.assertAlmostEqual(sum(p.values()), 1.0, places=6)
        self.assertGreater(p["a"], p["b"])
        self.assertGreater(p["b"], p["c"])

    def test_empty_field(self):
        self.assertEqual(golf.SkillTable().win_probs([]), {})


class TestSettle(unittest.TestCase):
    def _rows(self):
        base = {"settle_date": "2026-06-21", "market_ask": None}
        return [
            dict(base, id=1, outcome="win", meta={"tourney_id": "G1", "player": "scottie scheffler"}),
            dict(base, id=2, outcome="win", meta={"tourney_id": "G1", "player": "rory mcilroy"}),
        ]

    def test_winner_resolves_yes(self):
        res = settle.settle_golf(self._rows(), lambda d: {"G1": "scottie scheffler"})
        self.assertEqual(res[1][0], True)
        self.assertEqual(res[2][0], False)

    def test_not_final_skips(self):
        self.assertEqual(settle.settle_golf(self._rows(), lambda d: {}), {})

    def test_date_passed_yyyymmdd(self):
        # window must reach back to the tournament START — ESPN files events under their
        # start date, and golf settles on the END date (the old ±1-day window missed
        # every event and settled 0 rows).
        seen = []
        settle.settle_golf(self._rows(), lambda d: seen.append(d) or {})
        self.assertEqual(seen, ["20260615-20260622"])   # end−6d .. end+1d


if __name__ == "__main__":
    unittest.main()
