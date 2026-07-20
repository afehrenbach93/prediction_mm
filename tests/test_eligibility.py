"""P0: live mode without ELIGIBILITY_CONFIRMED → shadow, zero exchange mutations."""
import os
import unittest
from unittest.mock import patch

from core.eligibility import eligibility_confirmed, resolve_live_mode
from core.clobtrader import ClobTrader


class TestEligibilityGate(unittest.TestCase):
    def test_confirmed_true(self):
        with patch.dict(os.environ, {"ELIGIBILITY_CONFIRMED": "true"}, clear=False):
            self.assertTrue(eligibility_confirmed())

    def test_confirmed_false_default(self):
        env = {k: v for k, v in os.environ.items() if k != "ELIGIBILITY_CONFIRMED"}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(eligibility_confirmed())

    def test_live_without_flag_refused(self):
        with patch.dict(os.environ, {
            "CLOB_MODE": "live",
            "ELIGIBILITY_CONFIRMED": "",
        }, clear=False):
            live, reason = resolve_live_mode("live")
            self.assertFalse(live)
            self.assertIn("ELIGIBILITY_CONFIRMED", reason)

    def test_live_with_flag_allowed(self):
        with patch.dict(os.environ, {
            "CLOB_MODE": "live",
            "ELIGIBILITY_CONFIRMED": "true",
        }, clear=False):
            live, reason = resolve_live_mode("live")
            self.assertTrue(live)
            self.assertEqual(reason, "")

    def test_trader_live_env_without_flag_stays_shadow(self):
        with patch.dict(os.environ, {
            "CLOB_MODE": "live",
            "ELIGIBILITY_CONFIRMED": "false",
        }, clear=False):
            t = ClobTrader.from_env()
            self.assertFalse(t.live)
            resp = t.place_limit("tok", "BUY", 0.4, 10)
            self.assertTrue(resp.get("shadow"))
            cancel = t.cancel_all()
            self.assertTrue(cancel.get("shadow"))
            # never constructs auth client
            self.assertIsNone(t._client)

    def test_construct_live_true_without_env_flag_forced_shadow(self):
        with patch.dict(os.environ, {"ELIGIBILITY_CONFIRMED": ""}, clear=False):
            t = ClobTrader(live=True)
            self.assertFalse(t.live)
            r = t.place_limit("tok", "SELL", 0.6, 5)
            self.assertTrue(r.get("shadow"))
            self.assertEqual(len(t.shadow_orders), 1)


if __name__ == "__main__":
    unittest.main()
