"""Shadow gate: ClobTrader must not call auth client when live=False."""
import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from core.clobtrader import ClobTrader, _as_dict, _jsonable, _use_secure_client
from core.supabase_clob import SupabaseClob


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

    def test_jsonable_converts_decimal(self):
        out = _jsonable({
            "price": Decimal("0.42"),
            "size": Decimal("10"),
            "nested": [Decimal("1.5"), {"x": Decimal("2")}],
        })
        self.assertEqual(out["price"], 0.42)
        self.assertEqual(out["size"], 10.0)
        self.assertEqual(out["nested"][0], 1.5)
        self.assertEqual(out["nested"][1]["x"], 2.0)
        json.dumps(out)  # must not raise

    def test_as_dict_decimal_payload(self):
        d = _as_dict({"order_id": "abc", "price": Decimal("0.17")})
        self.assertEqual(d["orderID"], "abc")
        self.assertEqual(d["price"], 0.17)
        json.dumps(d)

    def test_supabase_req_serializes_decimal(self):
        sb = SupabaseClob(url="https://example.supabase.co", key="k")
        captured = {}

        class FakeResp:
            status = 201

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=30):
            captured["body"] = req.data
            return FakeResp()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            st, _ = sb.insert("clob_fills", {
                "raw_json": {"price": Decimal("0.019"), "size": Decimal("5")},
            })
        self.assertEqual(st, 201)
        parsed = json.loads(captured["body"])
        self.assertEqual(parsed["raw_json"]["price"], "0.019")


if __name__ == "__main__":
    unittest.main()
