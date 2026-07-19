"""Tests for core.arbrules (no network)."""
import unittest
from core import arbrules as ar


class TestCensus(unittest.TestCase):
    def test_complete_small_family(self):
        slugs = [
            "atc-lmx-a-b-2026-07-24-a",
            "atc-lmx-a-b-2026-07-24-b",
            "atc-lmx-a-b-2026-07-24-draw",
        ]
        census = ar.census_families(slugs)
        fam = "atc-lmx-a-b-2026-07-24"
        self.assertEqual(len(census[fam]), 3)
        ok, note = ar.family_complete(fam, slugs, census)
        self.assertTrue(ok)
        self.assertIn("complete", note)

    def test_incomplete_refused(self):
        census = ar.census_families([
            "tec-mlb-nl-2026-11-27-mvp-aaa",
            "tec-mlb-nl-2026-11-27-mvp-bbb",
            "tec-mlb-nl-2026-11-27-mvp-ccc",
        ])
        fam = "tec-mlb-nl-2026-11-27-mvp"
        ok, note = ar.family_complete(fam, ["tec-mlb-nl-2026-11-27-mvp-aaa"], census)
        self.assertFalse(ok)
        self.assertIn("incomplete", note)

    def test_too_large_refused(self):
        slugs = [f"tec-mlb-nl-2026-11-27-mvp-p{i:03d}" for i in range(20)]
        census = ar.census_families(slugs)
        fam = "tec-mlb-nl-2026-11-27-mvp"
        ok, note = ar.family_complete(fam, slugs, census, max_family_size=12)
        self.assertFalse(ok)
        self.assertIn("family_too_large", note)

    def test_prioritize_keeps_families_whole(self):
        census = {
            "f-big": [f"f-big-{i}" for i in range(5)],
            "f-sm": ["f-sm-a", "f-sm-b"],
        }
        ordered = ar.prioritize_complete_families(census, max_books=4)
        # small family first, fully included
        self.assertEqual(ordered[:2], ["f-sm-a", "f-sm-b"])

    def test_go_kill_rules(self):
        v, _ = ar.go_kill(5, 5, 0.01)
        self.assertEqual(v, "WATCH")
        v, _ = ar.go_kill(40, 15, 0.01)
        self.assertEqual(v, "GO")


if __name__ == "__main__":
    unittest.main()
