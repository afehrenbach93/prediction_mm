"""Shadow gate: ClobTrader must not call auth client when live=False."""
import unittest

from core.clobtrader import ClobTrader


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


if __name__ == "__main__":
    unittest.main()
