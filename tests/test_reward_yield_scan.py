"""
Wiring test for poly_runner.reward_yield_scan (the read-only heartbeat diagnostic).
No network, no orders. The volatility burst is disabled via REWARD_YIELD_WINDOW=0.
Run: PYTHONPATH=. python -m unittest tests.test_reward_yield_scan -v
"""
import os
import unittest

import poly_runner as pr


class FakeClient:
    """Read-only stand-in. Exposes ONLY the readers reward_yield_scan may call —
    if the scan ever tried to place/cancel an order the attribute wouldn't exist."""
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
    def setUp(self):
        os.environ["REWARD_YIELD_WINDOW"] = "0"   # skip the blocking vol burst
        self.logs = []

    def tearDown(self):
        os.environ.pop("REWARD_YIELD_WINDOW", None)

    def _log(self, msg):
        self.logs.append(msg)

    def test_populates_summary_ranked(self):
        state = {}
        pr.reward_yield_scan(FakeClient(), self._log, state, budget=200)
        s = state["summary"]
        self.assertEqual(s["n"], 2)
        self.assertEqual(s["budget"], 200)
        self.assertTrue(s["top"])
        row = s["top"][0]
        for k in ("slug", "period", "pool", "share", "rwd_hr", "yld_hr", "vol_min", "rank"):
            self.assertIn(k, row)
        # live pool (500 over 2h) out-earns the day_of pool (1000 over 6h) per hour
        self.assertEqual(row["slug"], "m2")
        self.assertGreater(row["rwd_hr"], 0.0)

    def test_no_book_reports_empty(self):
        state = {}
        pr.reward_yield_scan(FakeClient(book=([], [])), self._log, state, budget=200)
        self.assertEqual(state["summary"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
