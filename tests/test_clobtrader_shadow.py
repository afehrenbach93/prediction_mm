"""Shadow gate: ClobTrader must not call auth client when live=False."""
import os
import unittest
from unittest.mock import patch

from core.clobtrader import ClobTrader, _use_secure_client


class TestClobShadow(unittest.TestCase):
    def test_default_shadow_place(self):
        t = ClobTrader(live=False)
        resp = t.place_limit("tok", "BUY", 0.4, 10)
        self.assertTrue(resp.get("shadow"))
        self.assertEqual(len(t.shadow_orders), 1)

    def test_shadow_cancel_all(self):
        t = ClobTrader(live=False)
        r = t.cancel_all()
        self.assertTrue(r.get("shadow"))

    def test_shadow_get_trades_empty(self):
        t = ClobTrader(live=False)
        self.assertEqual(t.get_trades(), [])

    def test_use_secure_client_defaults_for_sig_type_3(self):
        with patch.dict(os.environ, {"CLOB_SIGNATURE_TYPE": "3"}, clear=False):
            os.environ.pop("CLOB_USE_SECURE_CLIENT", None)
            self.assertTrue(_use_secure_client())
        with patch.dict(os.environ, {
            "CLOB_SIGNATURE_TYPE": "3",
            "CLOB_USE_SECURE_CLIENT": "0",
        }, clear=False):
            self.assertFalse(_use_secure_client())
        with patch.dict(os.environ, {"CLOB_SIGNATURE_TYPE": "1"}, clear=False):
            os.environ.pop("CLOB_USE_SECURE_CLIENT", None)
            self.assertFalse(_use_secure_client())


if __name__ == "__main__":
    unittest.main()
