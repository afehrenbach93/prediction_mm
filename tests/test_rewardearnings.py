"""Tests for core.rewardearnings (no network)."""
import unittest

from core import rewardearnings as re_


class TestSummarize(unittest.TestCase):
    def test_live_rewards_shape(self):
        body = {"rewards": [
            {"reward": 0.08, "status": "PENDING", "marketSlug": "aec-x", "date": "2026-07-15"},
            {"reward": 1.30, "status": "PAID", "marketSlug": "tc-temp-x", "date": "2026-07-06"},
            {"reward": 0.22, "status": "SKIPPED", "marketSlug": "aec-mlb-x", "date": "2026-07-05"},
        ]}
        s = re_.summarize(body)
        self.assertEqual(s["n_rows"], 3)
        self.assertEqual(s["sum_amount_fields"], 1.6)
        self.assertEqual(s["paid"], 1.3)
        self.assertEqual(s["pending_credit"], 0.08)
        self.assertEqual(s["skipped"], 0.22)
        self.assertEqual(s["n_by_status"]["PAID"], 1)

    def test_legacy_amount_list(self):
        body = [{"amount": 1.5}, {"earned": {"value": 2.25}}]
        s = re_.summarize(body)
        self.assertEqual(s["n_rows"], 2)
        self.assertEqual(s["sum_amount_fields"], 3.75)

    def test_empty(self):
        s = re_.summarize({})
        self.assertEqual(s["n_rows"], 0)
        self.assertIsNone(s["sum_amount_fields"])
        self.assertEqual(s["pending_credit"], 0.0)


if __name__ == "__main__":
    unittest.main()
