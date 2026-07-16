"""
Tests for pure whale-scout helpers (no network).
Run: PYTHONPATH=. python -m unittest tests.test_whalescout -v
"""
import unittest

from core import whalescout as ws


class TestParseLb(unittest.TestCase):
    def test_normalises_rows(self):
        raw = [{"proxyWallet": "0x" + "ab" * 20, "name": "A", "amount": 1234.5},
               {"proxyWallet": "bad", "amount": 9},
               {"address": "0x" + "cd" * 20, "pseudonym": "B", "amount": "50"}]
        out = ws.parse_lb_rows(raw)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["name"], "A")
        self.assertEqual(out[0]["amount"], 1234.5)
        self.assertEqual(out[1]["addr"], "0x" + "cd" * 20)

    def test_empty_junk(self):
        self.assertEqual(ws.parse_lb_rows(None), [])
        self.assertEqual(ws.parse_lb_rows({"x": 1}), [])


class TestSelectWhales(unittest.TestCase):
    def test_ranks_by_profit_not_volume(self):
        rows = [{"addr": "0x" + "aa" * 20, "name": "bigvol", "amount": 500},
                {"addr": "0x" + "bb" * 20, "name": "bigpnl", "amount": 5000}]
        vol = {"0x" + "aa" * 20: 1e9, "0x" + "bb" * 20: 1e3}
        out = ws.select_whales(rows, vol, min_profit=1000, max_n=10)
        self.assertEqual([r["name"] for r in out], ["bigpnl"])  # bigvol filtered by min_profit

    def test_min_volume_filter(self):
        rows = [{"addr": "0x" + "aa" * 20, "name": "A", "amount": 5000}]
        out = ws.select_whales(rows, {"0x" + "aa" * 20: 10}, min_profit=1000,
                               min_volume=1000, max_n=5)
        self.assertEqual(out, [])

    def test_max_n(self):
        rows = [{"addr": "0x" + f"{i:02x}" * 20, "name": str(i), "amount": 10000 - i}
                for i in range(5)]
        out = ws.select_whales(rows, {}, min_profit=1, max_n=2)
        self.assertEqual([r["name"] for r in out], ["0", "1"])


class TestPaperCopy(unittest.TestCase):
    def test_trade_filter_and_key(self):
        self.assertTrue(ws.is_trade({"type": "TRADE"}))
        self.assertFalse(ws.is_trade({"type": "REDEEM"}))
        k = ws.trade_dedupe_key({"transactionHash": "0xabc", "asset": "1",
                                 "side": "BUY", "size": 2, "price": 0.5})
        self.assertIn("0xabc", k)

    def test_record_shape_and_lag(self):
        whale = {"addr": "0x" + "ab" * 20, "name": "W", "amount": 9000, "volume": 1e5}
        trade = {"type": "TRADE", "side": "BUY", "price": 0.50, "size": 10,
                 "usdcSize": 5, "slug": "foo-bar", "title": "Foo",
                 "outcome": "Yes", "timestamp": 1, "transactionHash": "0xdeadbeef01",
                 "asset": "tok"}
        rec = ws.paper_copy_record(trade, whale, copy_ask=0.55, today="2026-07-15")
        self.assertEqual(rec["model"], "whale-scout")
        self.assertEqual(rec["market_ask"], 0.50)
        self.assertEqual(rec["meta"]["copy_ask"], 0.55)
        self.assertEqual(rec["meta"]["lag_bps"], 1000.0)   # paid 10% more
        self.assertTrue(rec["market_slug"].startswith("foo-bar|"))


class TestPaperScore(unittest.TestCase):
    def test_lag_cost_summary(self):
        rows = [
            {"outcome": "buy", "market_ask": 0.50,
             "meta": {"copy_ask": 0.55, "lag_bps": 1000.0}},
            {"outcome": "buy", "market_ask": 0.40,
             "meta": {"copy_ask": 0.40, "lag_bps": 0.0}},
            {"outcome": "buy", "market_ask": 0.50, "meta": {}},  # no copy
        ]
        s = ws.lag_cost_summary(rows)
        self.assertEqual(s["n"], 3)
        self.assertEqual(s["n_with_copy_ask"], 2)
        self.assertEqual(s["lag_mean_bps"], 500.0)
        self.assertEqual(s["lag_median_bps"], 500.0)

    def test_paper_pnl_buy(self):
        self.assertEqual(ws.paper_pnl_at_copy("buy", 10, 0.40, True), 6.0)
        self.assertEqual(ws.paper_pnl_at_copy("buy", 10, 0.40, False), -4.0)

    def test_resolution_won(self):
        self.assertTrue(ws.resolution_won(["Yes", "No"], ["1", "0"], "Yes"))
        self.assertFalse(ws.resolution_won(["Yes", "No"], [1, 0], "No"))
        self.assertIsNone(ws.resolution_won(["Yes", "No"], [0.55, 0.45], "Yes"))

    def test_score_settled_rows(self):
        rows = [{
            "outcome": "buy", "market_ask": 0.5,
            "meta": {"slug": "m1", "outcome_name": "Yes", "size": 10, "copy_ask": 0.4},
        }]
        res = {"m1": {"outcomes": ["Yes", "No"], "outcomePrices": ["1", "0"]}}
        s = ws.score_settled_rows(rows, res)
        self.assertEqual(s["n_scored"], 1)
        self.assertEqual(s["paper_pnl"], 6.0)


if __name__ == "__main__":
    unittest.main()
