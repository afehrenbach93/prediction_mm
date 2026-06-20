"""
Shadow-gate safety tests for the Polymarket order layer.

The whole point: in shadow (default), NO order mutation can reach the exchange.
These assert the gate holds and that the order body is built correctly (post-only
maker flag, automated indicator, price formatting).
Run: PYTHONPATH=. python -m unittest tests.test_polyclient_shadow -v
"""
import unittest

from core.polyclient import PolyClient


class TestShadowGate(unittest.TestCase):
    def setUp(self):
        # no creds needed for the shadow path; default live=False
        self.c = PolyClient()

    def test_default_is_shadow(self):
        self.assertFalse(self.c.live)

    def test_shadow_place_records_and_acks(self):
        s, d = self.c.place_order("asc-fwc-can-qat-2026-06-18-pos-2pt5",
                                  "ORDER_INTENT_BUY_LONG", 0.51, 100)
        self.assertEqual(s, 200)
        self.assertTrue(d["shadow"])
        self.assertEqual(len(self.c.shadow_orders), 1)

    def test_shadow_never_hits_network(self):
        # if the gate leaked, this would be invoked and blow up
        def boom(*a, **k):
            raise AssertionError("signed_post called in shadow — gate leaked!")
        self.c.signed_post = boom
        self.c.place_order("m", "ORDER_INTENT_BUY_LONG", 0.5, 10)
        self.c.cancel_order("order-123")
        self.assertEqual(len(self.c.shadow_orders), 2)

    def test_order_body_is_correct(self):
        s, d = self.c.place_order("m", "ORDER_INTENT_SELL_SHORT", 0.5325, 7,
                                  post_only=True)
        body = d["order"]
        self.assertEqual(body["price"]["value"], "0.5325")       # 4dp string
        self.assertTrue(body["participateDontInitiate"])         # maker-only
        self.assertEqual(body["manualOrderIndicator"],
                         "MANUAL_ORDER_INDICATOR_AUTOMATED")      # declares bot
        self.assertEqual(body["intent"], "ORDER_INTENT_SELL_SHORT")

    def test_live_flag_routes_to_signed_post(self):
        live = PlaceCapture()
        live.live = True
        s, d = PolyClient.place_order(live, "m", "ORDER_INTENT_BUY_LONG", 0.5, 5)
        self.assertEqual(live.posted_path, "/v1/orders")
        self.assertEqual(live.posted_body["marketSlug"], "m")
        self.assertFalse(getattr(live, "shadow_orders", []))  # not recorded as shadow


class PlaceCapture:
    """Minimal stand-in: live=True, capture the signed_post call instead of sending."""
    live = True
    shadow_orders: list = []

    def signed_post(self, path, body):
        self.posted_path = path
        self.posted_body = body
        return 200, {"orderId": "real-1"}


if __name__ == "__main__":
    unittest.main()
