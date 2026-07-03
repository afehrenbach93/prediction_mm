"""Phase-2 app control: poly_control values override worker env defaults; NULL/garbage
falls back. Run: PYTHONPATH=. WX_TAKER=live MLB_TAKER=live python -m unittest
tests.test_effective_config -v"""
import unittest

import poly_runner as pr


class TestEffectiveConfig(unittest.TestCase):
    def test_null_control_uses_env_defaults(self):
        c = pr.effective_config({})
        self.assertEqual(c["wx_on"], pr.WX_TAKER == "live")
        self.assertEqual(c["mlb_on"], pr.MLB_TAKER == "live")
        self.assertEqual(c["mlb_budget"], pr.MLB_BUDGET)
        self.assertEqual(c["mlb_edge"], pr.MLB_EDGE)

    def test_app_values_win(self):
        c = pr.effective_config({"wx_taker": "off", "mlb_taker": "live",
                                 "mlb_budget": "120", "mlb_edge": 0.08})
        self.assertFalse(c["wx_on"])
        self.assertTrue(c["mlb_on"])
        self.assertEqual(c["mlb_budget"], 120.0)
        self.assertEqual(c["mlb_edge"], 0.08)

    def test_garbage_falls_back(self):
        c = pr.effective_config({"mlb_budget": "abc", "wx_budget": None})
        self.assertEqual(c["mlb_budget"], pr.MLB_BUDGET)
        self.assertEqual(c["wx_budget"], pr.WX_BUDGET)


if __name__ == "__main__":
    unittest.main()
