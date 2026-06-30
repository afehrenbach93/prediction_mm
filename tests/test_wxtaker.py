"""Tests for core/wxtaker pure selection/sizing/safety. No network.
Run: PYTHONPATH=. python -m unittest tests.test_wxtaker -v"""
import unittest

from core import wxtaker


class TestWxTaker(unittest.TestCase):
    def test_sell_candidates_overpriced_only(self):
        buckets = [
            {"slug": "a", "prob": 0.10, "bid": 0.40, "bid_qty": 50},   # edge 0.30 -> sell
            {"slug": "b", "prob": 0.30, "bid": 0.33, "bid_qty": 50},   # edge 0.03 -> skip
            {"slug": "c", "prob": 0.50, "bid": 0.95, "bid_qty": 50},   # bid>max -> skip
            {"slug": "d", "prob": 0.05, "bid": 0.20, "bid_qty": 50},   # edge 0.15 -> sell
        ]
        cands = wxtaker.sell_candidates(buckets, margin=0.10)
        self.assertEqual([c["slug"] for c in cands], ["a", "d"])  # best edge first

    def test_allocate_respects_budget(self):
        cands = [{"slug": "a", "sell_price": 0.40, "prob": 0.1, "edge": 0.3, "bid_qty": 100},
                 {"slug": "b", "sell_price": 0.50, "prob": 0.1, "edge": 0.4, "bid_qty": 100}]
        # budget $9: collateral/contract = 0.6 (a) and 0.5 (b)
        orders = wxtaker.allocate(cands, budget=9.0, per_bucket=10)
        total = sum(wxtaker.collateral(o["sell_price"], o["qty"]) for o in orders)
        self.assertLessEqual(total, 9.0 + 1e-9)
        self.assertTrue(orders)

    def test_allocate_probe_one_small_order(self):
        cands = [{"slug": "a", "sell_price": 0.40, "prob": 0.1, "edge": 0.3, "bid_qty": 100},
                 {"slug": "b", "sell_price": 0.50, "prob": 0.1, "edge": 0.4, "bid_qty": 100}]
        orders = wxtaker.allocate(cands, budget=75.0, probe=True)
        self.assertEqual(len(orders), 1)
        self.assertLessEqual(orders[0]["qty"], 2)

    def test_allocate_stops_when_budget_used(self):
        cands = [{"slug": "a", "sell_price": 0.40, "prob": 0.1, "edge": 0.3, "bid_qty": 100}]
        # already used 74.7 of 75 -> only ~0.3 collateral left -> 0 contracts
        orders = wxtaker.allocate(cands, budget=75.0, used=74.7, per_bucket=10)
        self.assertEqual(orders, [])

    def test_wrong_direction_flags_longs(self):
        positions = {"a": {"netPosition": "-5"}, "b": {"netPosition": "7"}, "c": {}}
        bad = wxtaker.wrong_direction(positions, {"a", "b", "c"})
        self.assertEqual(bad, ["b"])   # only the LONG is wrong


if __name__ == "__main__":
    unittest.main()
