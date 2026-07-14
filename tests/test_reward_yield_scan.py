"""
Wiring tests for the read-only reward-yield diagnostic in poly_runner:
reward_yield_scan (ranking + heartbeat summary) and reward_yield_sample (rolling
volatility history). No network, no orders.
Run: PYTHONPATH=. python -m unittest tests.test_reward_yield_scan -v
"""
import unittest

import poly_runner as pr


class FakeClient:
    """Read-only stand-in exposing ONLY the readers the diagnostic may call — if it
    ever tried to place/cancel an order the attribute wouldn't exist and the test
    would fail loudly."""
    def __init__(self, book=([(0.50, 100)], [(0.52, 100)])):
        self._book = book

    def get_incentives(self):
        return [{"marketSlug": "m1", "period": "day_of", "rewardPool": "1000",
                 "discountFactor": "0.3", "start": "", "end": ""},
                {"marketSlug": "m2", "period": "live", "rewardPool": "500",
                 "discountFactor": "0.3", "start": "", "end": ""}]

    def get_market(self, slug):
        return {"closed": False, "gameStartTime": "", "endDate": ""}

    def get_book(self, slug):
        return self._book


class TestRewardYieldScan(unittest.TestCase):
    def _log(self, msg):
        pass

    def test_populates_summary_and_watch(self):
        state = {}
        pr.reward_yield_scan(FakeClient(), self._log, state, budget=200)
        s = state["summary"]
        self.assertEqual(s["n"], 2)
        self.assertEqual(s["budget"], 200)
        self.assertIn("max_pool", s)
        self.assertIn("warming", s)
        self.assertTrue(s["top"])
        self.assertTrue(s["fattest"])
        for k in ("slug", "period", "pool", "share", "rwd_hr", "yld_hr",
                  "vol_min", "vol_n", "rank"):
            self.assertIn(k, s["top"][0])
        # fattest is by pool -> day_of m1 ($1000) leads; top is by rank -> live m2
        # out-earns per hour (short period), and with no history yet it's warming.
        self.assertEqual(s["fattest"][0]["slug"], "m1")
        self.assertEqual(s["top"][0]["slug"], "m2")
        self.assertTrue(s["warming"])
        self.assertEqual(set(state["watch"]), {"m1", "m2"})

    def test_no_book_reports_empty(self):
        state = {}
        pr.reward_yield_scan(FakeClient(book=([], [])), self._log, state, budget=200)
        self.assertEqual(state["summary"]["n"], 0)
        self.assertEqual(state["watch"], [])


class TestRewardYieldSample(unittest.TestCase):
    def test_sampler_accumulates_history_for_watched(self):
        state = {"watch": ["m2"]}
        c = FakeClient()
        for _ in range(3):
            pr.reward_yield_sample(c, state)
        self.assertEqual(len(state["hist"]["m2"]), 3)
        self.assertNotIn("m1", state.get("hist", {}))   # only watched slugs sampled

    def test_sampler_noop_without_watch(self):
        state = {}
        pr.reward_yield_sample(FakeClient(), state)
        self.assertEqual(state.get("hist", {}), {})

    def test_scan_uses_rolling_history_for_vol(self):
        # pre-seed a MOVING history for m2; the scan should read non-zero volatility
        # from it (not the degenerate single-point 0.0).
        state = {"hist": {"m2": [(0.0, 0.50), (30.0, 0.55), (60.0, 0.52)]}}
        pr.reward_yield_scan(FakeClient(), self._log, state, budget=200)
        m2 = next(r for r in state["summary"]["top"] if r["slug"] == "m2")
        self.assertGreater(m2["vol_min"], 0.0)
        self.assertEqual(m2["vol_n"], 3)
        self.assertFalse(state["summary"]["warming"])   # m2 has >=3 samples

    def _log(self, msg):
        pass


if __name__ == "__main__":
    unittest.main()
