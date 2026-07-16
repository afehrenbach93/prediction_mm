"""Tests for core.pilotreadout (no network)."""
import unittest
from datetime import datetime, timezone, timedelta

from core import pilotreadout as pr


class TestSummarize(unittest.TestCase):
    def test_flattens_heartbeat(self):
        status = {
            "mode": "live", "status": "quoting", "last_seen": "2026-07-16T14:00:00Z",
            "detail": {
                "budget": 50, "markets": 1, "size": 25, "placed_ok": 4, "rej": 0,
                "balance": 465.0, "realized_pnl": 0.0, "open_contracts": 5,
                "reward_yield": {
                    "n": 100, "max_pool": 2400, "warming": True,
                    "top": [{"slug": "atc-lmx-x", "rwd_hr": 0.2, "yld_hr": 0.004,
                             "vol_min": 0.0, "share": 0.03}],
                    "fattest": [{"slug": "atc-lmx-x", "pool": 2400}],
                },
            },
        }
        control = {"desired_mode": "live", "budget": 50,
                   "live_until": "2026-07-16T19:56:18+00:00"}
        now = datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc)
        s = pr.summarize(status, control, now=now)
        self.assertEqual(s["status"], "quoting")
        self.assertEqual(s["max_pool"], 2400)
        self.assertAlmostEqual(s["hours_left"], 5.94, places=1)
        self.assertTrue(s["ry_warming"])


class TestVerdict(unittest.TestCase):
    def test_tripped_is_kill(self):
        v, _ = pr.verdict({"status": "tripped", "mode": "live"})
        self.assertEqual(v, "KILL")

    def test_negative_realized_kill(self):
        v, _ = pr.verdict({"status": "quoting", "mode": "live", "realized_pnl": -3.5})
        self.assertEqual(v, "KILL")

    def test_quoting_warming_is_watch(self):
        v, reason = pr.verdict({
            "status": "quoting", "mode": "live", "desired_mode": "live",
            "hours_left": 5, "max_pool": 2400, "ry_warming": True,
            "realized_pnl": 0, "placed_ok": 4, "rej": 0, "top_vol_min": 0.0,
        })
        self.assertEqual(v, "WATCH")
        self.assertIn("warming", reason)

    def test_provisional_go(self):
        v, _ = pr.verdict({
            "status": "quoting", "mode": "live", "desired_mode": "live",
            "hours_left": 5, "max_pool": 2400, "ry_warming": False,
            "realized_pnl": 0, "placed_ok": 4, "rej": 0, "top_vol_min": 0.01,
        })
        self.assertEqual(v, "GO")

    def test_thin_pool_watch(self):
        v, _ = pr.verdict({
            "status": "quoting", "mode": "live", "desired_mode": "live",
            "hours_left": 5, "max_pool": 100, "ry_warming": False,
            "realized_pnl": 0, "placed_ok": 4, "rej": 0, "top_vol_min": 0.01,
        })
        self.assertEqual(v, "WATCH")


if __name__ == "__main__":
    unittest.main()
