"""Tests for core.arbscan (no network)."""
import unittest
from core import arbscan as a


class TestBinary(unittest.TestCase):
    def test_no_edge_on_normal_book(self):
        e = a.binary_complement_edge(0.40, 0.42, fee_buffer=0.01)
        self.assertIsNotNone(e)
        self.assertFalse(e["actionable"])

    def test_edge_on_crossed_book(self):
        # ask 0.40, bid 0.45 → no_ask=0.55, cost=0.95, edge=0.04 after 0.01 buffer
        e = a.binary_complement_edge(0.45, 0.40, fee_buffer=0.01)
        self.assertTrue(e["actionable"])
        self.assertAlmostEqual(e["cost"], 0.95, places=4)


class TestPartition(unittest.TestCase):
    def test_no_arb_when_overround(self):
        e = a.partition_edge([0.40, 0.35, 0.30], fee_buffer=0.02)
        self.assertFalse(e["actionable"])

    def test_arb_when_underround(self):
        e = a.partition_edge([0.30, 0.30, 0.30], fee_buffer=0.02)
        self.assertTrue(e["actionable"])
        self.assertAlmostEqual(e["cost"], 0.90, places=4)

    def test_families(self):
        slugs = [
            "atc-lmx-aft-ame-2026-07-24-aft",
            "atc-lmx-aft-ame-2026-07-24-ame",
            "atc-lmx-aft-ame-2026-07-24-draw",
            "lonely-slug",
        ]
        fam = a.group_families(slugs)
        self.assertIn("atc-lmx-aft-ame-2026-07-24", fam)
        self.assertEqual(len(fam["atc-lmx-aft-ame-2026-07-24"]), 3)


if __name__ == "__main__":
    unittest.main()
