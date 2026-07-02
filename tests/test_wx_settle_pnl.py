"""Tests for the weather settlement-P&L helpers in poly_runner: authoritative `realized`
extraction and struct cleaning (icon-blob stripping). No network.
Run: PYTHONPATH=. python -m unittest tests.test_wx_settle_pnl -v"""
import json
import unittest

from poly_runner import _wx_clean_struct, _wx_find_realized, _wx_money


class TestWxSettlePnl(unittest.TestCase):
    def test_money_coerces_value_dict_and_scalars(self):
        self.assertEqual(_wx_money({"value": "-7.40"}), -7.4)
        self.assertEqual(_wx_money({"value": 3}), 3.0)
        self.assertEqual(_wx_money("1.25"), 1.25)
        self.assertIsNone(_wx_money(None))
        self.assertIsNone(_wx_money({"value": "abc"}))

    def test_find_realized_prefers_authoritative_pr_realized(self):
        pr = {"realized": {"value": "-7.40"}}
        val, src = _wx_find_realized(pr, {}, {})
        self.assertEqual(val, -7.4)
        self.assertEqual(src, "pr.realized")

    def test_find_realized_checks_alternate_locations(self):
        self.assertEqual(_wx_find_realized({"realizedPnl": 4.8}, {}, {})[0], 4.8)
        self.assertEqual(_wx_find_realized({}, {"realized": {"value": "1.08"}}, {})[0], 1.08)
        self.assertEqual(_wx_find_realized({}, {}, {})[0], None)  # nothing -> fallback

    def test_clean_struct_strips_icon_blob(self):
        pr = {
            "marketSlug": "tc-temp-nychigh-2026-07-01-gte87lt88",
            "realized": {"value": "-7.40"},
            "marketMetadata": {"icon": "data:image/png;base64," + "A" * 5000,
                               "question": "NYC high"},
        }
        cleaned = json.loads(json.dumps(_wx_clean_struct(pr)))
        self.assertEqual(cleaned["marketMetadata"]["icon"], "-")
        self.assertEqual(cleaned["realized"], {"value": "-7.40"})   # real field survives
        self.assertLess(len(json.dumps(cleaned)), 400)              # blob is gone


if __name__ == "__main__":
    unittest.main()
