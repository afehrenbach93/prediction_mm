"""
Tests for the pure Polymarket maker quoting logic.
Run: PYTHONPATH=. python -m unittest tests.test_polymaker -v
"""
import unittest

from core.polymaker import MakerParams, maker_quotes, program_active


P = MakerParams(size=100, max_inventory=300, min_price=0.03, max_price=0.97)


class TestMakerQuotes(unittest.TestCase):
    def test_flat_quotes_both_sides_at_touch(self):
        q = maker_quotes(0.51, 0.53, 0, P)
        self.assertEqual(len(q), 2)
        (bi, bp, bq), (si, sp, sq) = q
        self.assertEqual((bi, bp, bq), ("ORDER_INTENT_BUY_LONG", 0.51, 100))
        self.assertEqual((si, sp), ("ORDER_INTENT_SELL_SHORT", 0.53))  # flat -> short
        self.assertEqual(sq, 100)

    def test_never_crosses_uses_book_prices(self):
        # joins the touch exactly — buy at bid, sell at ask, never inside/through
        q = maker_quotes(0.40, 0.60, 0, P)
        self.assertEqual(q[0][1], 0.40)
        self.assertEqual(q[1][1], 0.60)

    def test_long_inventory_shrinks_buy_and_sells_to_reduce(self):
        # near the long cap: little buy room, full sell room, sell reduces (LONG)
        q = maker_quotes(0.51, 0.53, 250, P)
        buy = next(o for o in q if o[0] == "ORDER_INTENT_BUY_LONG")
        sell = next(o for o in q if o[0].startswith("ORDER_INTENT_SELL"))
        self.assertEqual(buy[2], 50)                       # room = 300-250
        self.assertEqual(sell[0], "ORDER_INTENT_SELL_LONG")  # reducing a long
        self.assertEqual(sell[2], 100)

    def test_at_long_cap_no_buy(self):
        q = maker_quotes(0.51, 0.53, 300, P)
        self.assertFalse(any(o[0] == "ORDER_INTENT_BUY_LONG" for o in q))

    def test_short_inventory_shrinks_sell(self):
        q = maker_quotes(0.51, 0.53, -300, P)
        self.assertFalse(any(o[0].startswith("ORDER_INTENT_SELL") for o in q))
        self.assertTrue(any(o[0] == "ORDER_INTENT_BUY_LONG" for o in q))

    def test_skips_extreme_tails(self):
        self.assertEqual(maker_quotes(0.01, 0.02, 0, P), [])
        self.assertEqual(maker_quotes(0.98, 0.99, 0, P), [])

    def test_no_book(self):
        self.assertEqual(maker_quotes(None, None, 0, P), [])


class TestRewardWindow(unittest.TestCase):
    def test_live_and_day_of(self):
        ks, settle = 1000.0, 5000.0
        self.assertTrue(program_active(3000, "live", 0, 0, ks, settle))        # mid-game
        self.assertFalse(program_active(settle + 1, "live", 0, 0, ks, settle))  # settled
        self.assertTrue(program_active(ks - 3600, "day_of", 0, 0, ks, settle))  # 1h pre
        self.assertFalse(program_active(ks - 10 * 3600, "day_of", 0, 0, ks, settle))  # early

    def test_continuous_daily_event(self):
        # daily_event earns whenever the PROGRAM is running (no game tie)
        self.assertTrue(program_active(1500, "daily_event", 1000, 0, 0, 0))     # started, open-ended
        self.assertFalse(program_active(500, "daily_event", 1000, 0, 0, 0))     # before start
        self.assertFalse(program_active(2500, "daily_event", 1000, 2000, 0, 0))  # after end
        self.assertTrue(program_active(1500, "daily_event", 1000, 2000, 0, 0))   # within window


if __name__ == "__main__":
    unittest.main()
