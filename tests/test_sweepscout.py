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

    def test_go_kill(self):
        v, _ = ss.go_kill(10, 1.0, 1.0)
        self.assertEqual(v, "WATCH")
        v, _ = ss.go_kill(60, 0.995, 5.0)
        self.assertEqual(v, "GO")
        v, _ = ss.go_kill(60, 0.95, -10.0)
        self.assertEqual(v, "KILL")


if __name__ == "__main__":
    unittest.main()
