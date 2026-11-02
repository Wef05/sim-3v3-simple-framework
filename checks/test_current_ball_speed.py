"""当前球速应为轨迹在决策时刻的导数，不能直接使用观测区间均速。"""
import unittest
from dataclasses import replace
from src.framework.types import BallState, Context, ADULT_FIELD_DIMENSIONS
from src.utils.ball_motion import BallMotion
from src.param import BALL_PURE_ROLL_DECEL_MPS2


class CurrentBallSpeedTests(unittest.TestCase):
    def sample(self, motion, t, x, now=None):
        ctx = Context(t if now is None else now, .1, 1, ADULT_FIELD_DIMENSIONS,
                      ball=BallState(x, 0, last_seen_at=t))
        motion.update(ctx)

    def test_deceleration_crosses_threshold_before_average(self):
        m = BallMotion(mode='pure_rolling')
        self.sample(m, 0., 0.)
        self.sample(m, .1, .051)
        self.assertAlmostEqual(m.observed_speed(), .51)
        self.assertAlmostEqual(m.current_speed(), .51-BALL_PURE_ROLL_DECEL_MPS2*.05)
        self.assertLess(m.current_speed(), .5)

    def test_latency_advances_speed_and_matches_position_derivative(self):
        for mode in ('pure_rolling', 'sliding_then_rolling'):
            m = BallMotion(mode=mode)
            self.sample(m, 0., 0.)
            self.sample(m, .1, .2)
            at_sample = m.current_speed()
            self.sample(m, .1, .2, now=.3)
            self.assertLess(m.current_speed(), at_sample)
            epsilon = 1e-6
            p0, p1 = m.predict_ball_position(0), m.predict_ball_position(epsilon)
            self.assertAlmostEqual(m.current_speed(), (p1[0]-p0[0])/epsilon, places=4)
            self.assertEqual(m.ball_prediction_details()['current_speed_mps'], m.current_speed())

    def test_acceleration_replaces_old_slow_fit(self):
        m = BallMotion()
        self.sample(m, 0., 0.)
        self.sample(m, .1, .01)
        self.assertLess(m.current_speed(), .5)
        self.sample(m, .2, .21)
        self.assertGreater(m.current_speed(), .5)

    def test_unknown_stale_rewind_and_missing_are_not_zero(self):
        m = BallMotion()
        self.sample(m, 0., 0.)
        self.assertIsNone(m.current_speed())
        self.sample(m, .1, .1)
        self.sample(m, .1, .1, now=.7)
        self.assertIsNone(m.current_speed())
        self.sample(m, .2, .2)
        self.sample(m, .1, .1)
        self.assertIsNone(m.current_speed())
        m.update(replace(m.context, ball=None))
        self.assertIsNone(m.current_speed())

    def test_stop_clamps_to_zero_and_duplicate_does_not_refit(self):
        m = BallMotion(mode='pure_rolling')
        self.sample(m, 0., 0.)
        self.sample(m, .1, .01)
        samples = list(m._speed_samples)
        self.sample(m, .1, .01, now=.5)
        self.assertEqual(m.current_speed(), 0.)
        self.assertEqual(m._speed_samples, samples)
