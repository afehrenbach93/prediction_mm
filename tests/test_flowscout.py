"""Tests for core.flowscout (no network)."""
import unittest

from core import flowscout as fs


class TestSpike(unittest.TestCase):
    def test_needs_warm_baseline(self):
        self.assertFalse(fs.is_spike(500, [10, 10, 10], min_size=100, mult=5,
                                     min_baseline_n=8))

    def test_flags_large_vs_median(self):
        base = [20.0] * 10
        self.assertTrue(fs.is_spike(200, base, mult=5, min_size=100))
        self.assertFalse(fs.is_spike(50, base, mult=5, min_size=100))  # below floor
        self.assertFalse(fs.is_spike(80, base, mult=5, min_size=50))   # < 5×20

    def test_push_baseline_trims(self):
        h = []
        for i in range(5):
            fs.push_baseline(h, float(i + 1), maxlen=3)
        self.assertEqual(h, [3.0, 4.0, 5.0])


class TestEndgame(unittest.TestCase):
    def test_minutes_and_window(self):
        self.assertAlmostEqual(fs.minutes_to_end(3600, 0), 60.0)
        self.assertTrue(fs.in_endgame(30, 120))
        self.assertFalse(fs.in_endgame(200, 120))
        self.assertTrue(fs.in_endgame(None, 0))       # any-time mode
        self.assertFalse(fs.in_endgame(None, 120))    # need end when gated


class TestRecordAndVerdict(unittest.TestCase):
    def test_record_shape(self):
        trade = {"side": "BUY", "price": 0.4, "size": 250, "slug": "ufc-x",
                 "title": "UFC", "outcome": "A", "timestamp": 1,
                 "transactionHash": "0xdeadbeef01", "asset": "tok",
                 "conditionId": "0xc", "proxyWallet": "0x" + "ab" * 20}
        rec = fs.paper_flow_record(trade, copy_ask=0.45, spike_mult=8.0,
                                   baseline_med=30.0, minutes_left=25.0,
                                   today="2026-07-18", endgame=True)
        self.assertEqual(rec["model"], "flow-scout")
        self.assertEqual(rec["meta"]["endgame"], True)
        self.assertEqual(rec["meta"]["size"], 250)
        self.assertGreater(rec["meta"]["lag_bps"], 0)

    def test_go_kill(self):
        v, _ = fs.go_kill(50, 0.7, 10.0)
        self.assertEqual(v, "WATCH")
        v, _ = fs.go_kill(120, 0.60, 25.0)
        self.assertEqual(v, "GO")
        v, _ = fs.go_kill(120, 0.48, -5.0)
        self.assertEqual(v, "KILL")


if __name__ == "__main__":
    unittest.main()
