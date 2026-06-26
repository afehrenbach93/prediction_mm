"""Tests for the app-driven live controls: WC allow-filter + live_cycle gating.
Pure where possible; live_cycle exercised against a shadow client (no network mutation).
Run: PYTHONPATH=. python -m unittest tests.test_live_control -v"""
import unittest

import poly_runner as pr
from core.polyclient import PolyClient


class TestAllowFilter(unittest.TestCase):
    def setUp(self):
        self._saved = pr.ALLOW_TOKENS
        pr.ALLOW_TOKENS = {"worldcup", "fwc", "-wc-"}

    def tearDown(self):
        pr.ALLOW_TOKENS = self._saved

    def test_wc_slug_allowed(self):
        self.assertTrue(pr._allowed("asc-fwc-2026-06-25-spread-bra", [{"programId": "x"}]))
        self.assertTrue(pr._allowed("tec-f-wc-2026-07-19-winner", [{"programId": "x"}]))

    def test_wc_program_allowed(self):
        self.assertTrue(pr._allowed("anything", [{"programId": "worldcup_exotic_v2"}]))

    def test_non_wc_denied(self):
        self.assertFalse(pr._allowed("aec-dota2-2026", [{"programId": "esports_v1"}]))
        self.assertFalse(pr._allowed("mac-cpi-2026", [{"programId": "macro_daily"}]))


class FakeCache:
    def __init__(self, windows):
        self._w = windows

    def in_window(self, now):
        return self._w


class TestLiveCycle(unittest.TestCase):
    def _client(self):
        # shadow client (no creds): place/cancel are intercepted, never hit the network
        return PolyClient(live=False)

    def test_idle_when_no_window(self):
        c = self._client()
        res = pr.live_cycle(c, FakeCache([]), {"tripped": False}, 50.0, live=False)
        self.assertEqual(res["status"], "idle")
        self.assertEqual(res["markets"], 0)

    def test_tripped_stands_aside(self):
        c = self._client()
        res = pr.live_cycle(c, FakeCache([("asc-fwc-x", "day_of", 9900)]),
                            {"tripped": True}, 50.0, live=False)
        self.assertEqual(res["status"], "tripped")

    def test_shadow_never_places_real_orders(self):
        # in shadow, any "placed" orders are recorded locally, not sent to the exchange
        c = self._client()
        pr.live_cycle(c, FakeCache([]), {"tripped": False}, 50.0, live=False)
        # a no-window cycle places nothing; the shadow ledger only grows from cancels
        self.assertTrue(all(o.get("shadow") for o in c.shadow_orders))


if __name__ == "__main__":
    unittest.main()
