"""
Tests for the pure reward-yield math (Stage 1). No network.
Run: PYTHONPATH=. python -m unittest tests.test_rewardyield -v
"""
import unittest

from core import rewardyield as ry


class TestInferTick(unittest.TestCase):
    def test_game_tick(self):
        self.assertAlmostEqual(ry.infer_tick([0.50, 0.49, 0.48]), 0.01)

    def test_futures_tick(self):
        self.assertAlmostEqual(ry.infer_tick([0.500, 0.499, 0.498]), 0.001)

    def test_default_when_indeterminate(self):
        self.assertEqual(ry.infer_tick([]), ry._DEFAULT_TICK)
        self.assertEqual(ry.infer_tick([0.5]), ry._DEFAULT_TICK)
        self.assertEqual(ry.infer_tick([0.5, 0.5]), ry._DEFAULT_TICK)


class TestSideScore(unittest.TestCase):
    def test_touch_full_weight_deep_decays(self):
        # best at full weight (disc^0=1); one tick off at disc^1.
        s = ry.side_score([(0.50, 100), (0.49, 50)], 0.50, disc=0.3, tick=0.01)
        self.assertAlmostEqual(s, 100 + 50 * 0.3)

    def test_beyond_max_ticks_ignored(self):
        s = ry.side_score([(0.50, 100), (0.40, 999)], 0.50, disc=0.3, tick=0.01,
                          max_ticks=8)  # 0.40 is 10 ticks off -> dropped
        self.assertAlmostEqual(s, 100.0)

    def test_empty_or_bad_tick(self):
        self.assertEqual(ry.side_score([], 0.5, 0.3, 0.01), 0.0)
        self.assertEqual(ry.side_score([(0.5, 10)], 0.5, 0.3, 0.0), 0.0)


class TestCompetingScore(unittest.TestCase):
    def test_two_sided_sums_both_touches(self):
        s = ry.competing_score([(0.50, 100)], [(0.52, 80)], disc=0.3)
        self.assertAlmostEqual(s, 180.0)

    def test_one_sided_earns_nothing(self):
        self.assertEqual(ry.competing_score([(0.50, 100)], [], disc=0.3), 0.0)
        self.assertEqual(ry.competing_score([], [(0.52, 80)], disc=0.3), 0.0)

    def test_depth_discounted_with_explicit_tick(self):
        s = ry.competing_score([(0.50, 100), (0.49, 100)],
                               [(0.51, 100), (0.52, 100)], disc=0.5, tick=0.01)
        # each side: 100 + 100*0.5 = 150; both sides -> 300
        self.assertAlmostEqual(s, 300.0)


class TestPeriodHours(unittest.TestCase):
    def test_explicit_span_wins(self):
        self.assertAlmostEqual(ry.period_hours("live", 1000.0, 1000.0 + 7200), 2.0)

    def test_live_game_span_fallback(self):
        self.assertAlmostEqual(
            ry.period_hours("live", 0, 0, game_start=1000.0, settle=1000.0 + 3600), 1.0)

    def test_period_type_fallback(self):
        self.assertEqual(ry.period_hours("day_of"), 6.0)
        self.assertEqual(ry.period_hours("daily_event"), 24.0)

    def test_unknown_defaults_24(self):
        self.assertEqual(ry.period_hours("who_knows"), ry._DEFAULT_PERIOD_HOURS)


class TestModeledReward(unittest.TestCase):
    def test_share_and_yield(self):
        # budget 200 @ mid 0.5 -> 400 contracts; competing 400 -> share 0.5;
        # pool 1000 over 2h -> $500/period, $250/hr, yield 1.25 (/hr per $).
        r = ry.modeled_reward(budget=200, mid=0.5, competing=400, pool=1000, hours=2)
        self.assertAlmostEqual(r["my_contracts"], 400.0)
        self.assertAlmostEqual(r["share"], 0.5)
        self.assertAlmostEqual(r["reward_per_period"], 500.0)
        self.assertAlmostEqual(r["reward_per_hour"], 250.0)
        self.assertAlmostEqual(r["yield_per_hr"], 1.25)

    def test_thin_competition_higher_share(self):
        thin = ry.modeled_reward(200, 0.5, competing=40, pool=1000, hours=2)
        fat = ry.modeled_reward(200, 0.5, competing=4000, pool=1000, hours=2)
        self.assertGreater(thin["share"], fat["share"])

    def test_degenerate_inputs_zero(self):
        for r in (ry.modeled_reward(0, 0.5, 100, 1000, 2),
                  ry.modeled_reward(200, 0, 100, 1000, 2),
                  ry.modeled_reward(200, 0.5, 100, 0, 2),
                  ry.modeled_reward(200, 0.5, 100, 1000, 0)):
            self.assertEqual(r["reward_per_hour"], 0.0)


class TestRealizedVol(unittest.TestCase):
    def test_drift_per_minute(self):
        v = ry.realized_vol([(0, 0.50), (60, 0.52), (120, 0.51)])
        self.assertEqual(v["n"], 3)
        self.assertAlmostEqual(v["span_min"], 2.0)
        self.assertAlmostEqual(v["vol_per_min"], 0.03 / 2.0)   # moves 0.02 + 0.01
        self.assertAlmostEqual(v["max_move"], 0.02)

    def test_unordered_and_none_dropped(self):
        v = ry.realized_vol([(120, 0.51), (0, 0.50), (60, None), (60, 0.52)])
        self.assertEqual(v["n"], 3)
        self.assertAlmostEqual(v["vol_per_min"], 0.015)

    def test_too_few_points_zero(self):
        self.assertEqual(ry.realized_vol([])["vol_per_min"], 0.0)
        self.assertEqual(ry.realized_vol([(0, 0.5)])["vol_per_min"], 0.0)


class TestRankKey(unittest.TestCase):
    def test_higher_reward_ranks_higher(self):
        self.assertGreater(ry.rank_key(250, 0.01), ry.rank_key(100, 0.01))

    def test_higher_vol_ranks_lower(self):
        self.assertGreater(ry.rank_key(250, 0.001), ry.rank_key(250, 0.05))

    def test_zero_vol_finite(self):
        self.assertTrue(ry.rank_key(250, 0.0) < float("inf"))


if __name__ == "__main__":
    unittest.main()
