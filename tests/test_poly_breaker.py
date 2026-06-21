"""
Tests for the poly_runner live risk breaker + position parsing.
Run: PYTHONPATH=. python -m unittest tests.test_poly_breaker -v
"""
import unittest

import poly_runner as pr


class FakeClient:
    def __init__(self, positions=None, book=(0.50, 0.50), open_orders=None):
        self._pos = positions or {}
        self._book = book
        self._open = open_orders or []
        self.cancelled = []

    def get_positions(self):
        return 200, {"positions": self._pos}

    def get_book(self, slug):
        bb, ba = self._book
        return [(bb, 100)], [(ba, 100)]

    def get_open_orders(self):
        return 200, {"orders": self._open}

    def cancel_order(self, oid, market_slug=""):
        self.cancelled.append((oid, market_slug))
        return 200, {}


class TestPositionsParse(unittest.TestCase):
    def test_defensive_field_parsing(self):
        c = FakeClient(positions={
            "m1": {"net": "120", "avgPrice": {"value": "0.55"}},
            "m2": {"quantity": -50, "costBasis": 0.40},
        })
        out = pr.positions_net(c)
        self.assertEqual(out["m1"]["net"], 120.0)
        self.assertEqual(out["m1"]["entry"], 0.55)
        self.assertEqual(out["m2"]["net"], -50.0)

    def test_live_netposition_field(self):
        # CONFIRMED live raw shape (2026-06-20). The old code parsed net=0 here
        # (no `netPosition` key) so the breaker did NOT trip on this 332-contract
        # position. Regression guard: net must read 332 from `netPosition`.
        c = FakeClient(positions={
            "tec-f-wc-2026-07-19-groupb-winner-bih":
                {"netPosition": "332", "qtyBought": "332", "qtySold": "0"},
        })
        out = pr.positions_net(c)
        self.assertEqual(
            out["tec-f-wc-2026-07-19-groupb-winner-bih"]["net"], 332.0)

    def test_net_derived_from_qty_when_no_netposition(self):
        c = FakeClient(positions={"m": {"qtyBought": "200", "qtySold": "120"}})
        out = pr.positions_net(c)
        self.assertEqual(out["m"]["net"], 80.0)

    def test_live_position_trips_inventory_cap(self):
        # End-to-end: the confirmed live shape must now trip the inventory cap.
        c = FakeClient(positions={"m": {"netPosition": "332"}})
        positions = pr.positions_net(c)
        trip, reason = pr.breaker_check(c, positions)
        self.assertTrue(trip)
        self.assertIn("inventory", reason)


class TestBreaker(unittest.TestCase):
    def setUp(self):
        pr.MAX_INV, pr.EXPOSURE_CAP, pr.DAILY_LOSS = 300.0, 300.0, 15.0
        pr.DENY_SLUGS = set()

    def tearDown(self):
        pr.DENY_SLUGS = set()

    def test_denied_legacy_position_does_not_trip(self):
        # A held legacy WC-futures bet over the cap must NOT stand the bot down
        # when its slug is denied (we keep it, but the bot doesn't manage it).
        pr.MAX_INV = 50.0
        pr.DENY_SLUGS = {"tec-f-wc-2026-07-19-groupb-winner-bih"}
        trip, _ = pr.breaker_check(
            FakeClient(), {"tec-f-wc-2026-07-19-groupb-winner-bih":
                           {"net": 332, "entry": 0.08}})
        self.assertFalse(trip)

    def test_denied_slug_does_not_mask_other_market_trip(self):
        pr.MAX_INV = 50.0
        pr.DENY_SLUGS = {"legacy"}
        trip, reason = pr.breaker_check(
            FakeClient(), {"legacy": {"net": 332, "entry": 0.08},
                           "pilot": {"net": 80, "entry": 0.5}})
        self.assertTrue(trip)
        self.assertIn("pilot", reason)

    def test_no_trip_when_flat(self):
        trip, _ = pr.breaker_check(FakeClient(), {})
        self.assertFalse(trip)

    def test_inventory_cap_trips(self):
        trip, reason = pr.breaker_check(FakeClient(), {"m": {"net": 400, "entry": 0.5}})
        self.assertTrue(trip)
        self.assertIn("inventory", reason)

    def test_exposure_cap_trips(self):
        c = FakeClient(book=(0.89, 0.91))  # mark 0.90
        pos = {"a": {"net": 200, "entry": 0.9}, "b": {"net": 200, "entry": 0.9}}
        trip, reason = pr.breaker_check(c, pos)  # 200*.9 + 200*.9 = 360 > 300
        self.assertTrue(trip)
        self.assertIn("exposure", reason)

    def test_unrealized_loss_trips(self):
        c = FakeClient(book=(0.39, 0.41))  # mark 0.40
        # long 100 @ 0.60, now 0.40 -> unreal 100*(0.40-0.60) = -20 <= -15
        trip, reason = pr.breaker_check(c, {"m": {"net": 100, "entry": 0.60}})
        self.assertTrue(trip)
        self.assertIn("unrealized", reason)

    def test_small_position_no_trip(self):
        c = FakeClient(book=(0.49, 0.51))
        trip, _ = pr.breaker_check(c, {"m": {"net": 20, "entry": 0.50}})
        self.assertFalse(trip)


class TestCancelAll(unittest.TestCase):
    def test_cancels_every_open_order_with_marketslug(self):
        c = FakeClient(open_orders=[{"id": "a", "marketSlug": "m1"},
                                    {"id": "b", "marketSlug": "m2"}])
        n = pr.cancel_all_orders(c)
        self.assertEqual(n, 2)
        self.assertEqual(c.cancelled, [("a", "m1"), ("b", "m2")])


if __name__ == "__main__":
    unittest.main()
