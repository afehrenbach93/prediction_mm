"""Tests for core/settle.py — pure scoring with injected fetchers (no network).
Run: PYTHONPATH=. python -m unittest tests.test_settle -v"""
import unittest

from core import settle


class TestWeather(unittest.TestCase):
    def _rows(self):
        return [
            {"id": 1, "market_slug": "tc-temp-nychigh-2026-06-22-gte84lt85f",
             "market_ask": 0.30, "settle_date": "2026-06-22"},   # 84<=h<85
            {"id": 2, "market_slug": "tc-temp-nychigh-2026-06-22-gte90f",
             "market_ask": 0.10, "settle_date": "2026-06-22"},   # h>=90
        ]

    def test_bucket_hit_and_miss(self):
        res = settle.settle_weather(self._rows(), lambda s, d: 84.0)
        self.assertEqual(res[1][0], True)    # 84 in [84,85)
        self.assertEqual(res[2][0], False)   # 84 < 90
        # pnl = (1 if yes else 0) - ask
        self.assertAlmostEqual(res[1][1], 1.0 - 0.30, places=6)
        self.assertAlmostEqual(res[2][1], 0.0 - 0.10, places=6)

    def test_upper_open_bucket(self):
        res = settle.settle_weather(self._rows(), lambda s, d: 93.0)
        self.assertEqual(res[2][0], True)    # 93 >= 90
        self.assertEqual(res[1][0], False)   # 93 not in [84,85)

    def test_no_data_skips(self):
        res = settle.settle_weather(self._rows(), lambda s, d: None)
        self.assertEqual(res, {})            # unresolved -> left unsettled

    def test_fetch_cached_per_station_date(self):
        calls = []
        settle.settle_weather(self._rows(), lambda s, d: calls.append((s, d)) or 84.0)
        self.assertEqual(len(calls), 1)      # both rows share (nyc, date)

    def test_no_ask_pnl_none(self):
        rows = [{"id": 9, "market_slug": "tc-temp-miahigh-2026-06-22-gte97f",
                 "market_ask": None, "settle_date": "2026-06-22"}]
        res = settle.settle_weather(rows, lambda s, d: 99.0)
        self.assertEqual(res[9], (True, None))


class TestSoccer(unittest.TestCase):
    def _rows(self):
        base = {"settle_date": "2026-06-22", "market_ask": None,
                "meta": {"league": "wc", "espn_id": "555"}}
        return [dict(base, id=i, outcome=o)
                for i, o in [(1, "home"), (2, "draw"), (3, "away")]]

    def _finals(self, win):
        score = {"home": (2, 0), "draw": (1, 1), "away": (0, 3)}[win]
        return lambda lg, dt: {"555": {"home_score": score[0], "away_score": score[1]}}

    def test_home_win(self):
        res = settle.settle_soccer(self._rows(), self._finals("home"))
        self.assertEqual((res[1][0], res[2][0], res[3][0]), (True, False, False))

    def test_draw(self):
        res = settle.settle_soccer(self._rows(), self._finals("draw"))
        self.assertEqual((res[1][0], res[2][0], res[3][0]), (False, True, False))

    def test_away_win(self):
        res = settle.settle_soccer(self._rows(), self._finals("away"))
        self.assertEqual((res[1][0], res[2][0], res[3][0]), (False, False, True))

    def test_not_final_skips(self):
        res = settle.settle_soccer(self._rows(), lambda lg, dt: {})
        self.assertEqual(res, {})

    def test_date_passed_as_window(self):
        seen = []
        settle.settle_soccer(self._rows(), lambda lg, dt: seen.append(dt) or {})
        self.assertEqual(seen, ["20260621-20260623"])   # ±1-day window (TZ-robust)


if __name__ == "__main__":
    unittest.main()
