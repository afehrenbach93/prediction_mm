"""Tests for core.sweepscout (no network)."""
import unittest
from core import sweepscout as ss


class TestSweep(unittest.TestCase):
    def test_return_and_candidate(self):
        self.assertAlmostEqual(ss.sweep_return(0.99), 0.010101, places=5)
        self.assertTrue(ss.is_sweep_candidate(
            0.99, 0.98, minutes_left=120, min_ask=0.97, max_ask=0.995))
        self.assertFalse(ss.is_sweep_candidate(
            0.90, minutes_left=120))  # too cheap / uncertain
        self.assertFalse(ss.is_sweep_candidate(
            0.99, minutes_left=None, require_near_end=True))

    def test_max_entry(self):
        self.assertEqual(ss.max_entry_from_error_rate(0.001, k=3), 0.997)

    def test_paper_pnl(self):
        self.assertAlmostEqual(ss.paper_pnl_buy(0.99, 100, True), 1.0, places=4)
        self.assertAlmostEqual(ss.paper_pnl_buy(0.99, 100, False), -99.0, places=4)

    def test_go_kill_requires_source_gate(self):
        v, _ = ss.go_kill(10, 1.0, 1.0)
        self.assertEqual(v, "WATCH")
        # good sample but no source gate → still WATCH
        v, reason = ss.go_kill(60, 0.995, 5.0, source_gate=False)
        self.assertEqual(v, "WATCH")
        self.assertIn("source-feed", reason)
        v, _ = ss.go_kill(60, 0.995, 5.0, source_gate=True)
        self.assertEqual(v, "GO")
        v, _ = ss.go_kill(60, 0.95, -10.0)
        self.assertEqual(v, "KILL")

    def test_summarize_asks(self):
        s = ss.summarize_asks([0.4, 0.5, 0.98, 0.99])
        self.assertEqual(s["n_ge_min"], 2)
        self.assertEqual(s["max_ask"], 0.99)


if __name__ == "__main__":
    unittest.main()
