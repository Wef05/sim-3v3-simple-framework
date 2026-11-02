"""Continuous-window interception regression tests."""
import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.framework.types import Pose2D
from src.utils.ball_motion import BallMotion
from src.utils.interception import estimate_ball_intercept
from src.actions.motion_controller import MotionController
from src.param import BALL_CONTROL_RADIUS_M, MAX_ANGULAR


def linear(origin, velocity=(0., 0.)):
    return SimpleNamespace(predict_ball_position=lambda t: tuple(
        origin[i] + velocity[i] * t for i in (0, 1)))


class InterceptionTests(unittest.TestCase):
    def test_real_fast_ball_between_original_samples(self):
        motion = BallMotion()
        for t, x in ((0., -1.5), (.05, -.5)):
            motion.update(SimpleNamespace(now=t, ball=SimpleNamespace(
                x=x, y=0., last_seen_at=t)))
        for normal in (None, (0., 1.), (0., -1.)):
            result = estimate_ball_intercept(Pose2D(), motion, approach_normal=normal)
            self.assertEqual(result['status'], 'intercept')
            self.assertLess(result['intercept_time_s'], .025)
            self.assertLessEqual(result['arrival_time_s'], result['intercept_time_s'])
            self.assertEqual(result['ball_position'], motion.predict_ball_position(result['intercept_time_s']))

    def test_stationary_earliest_time(self):
        result = estimate_ball_intercept(Pose2D(), linear((2., 0.)))
        self.assertEqual(result['status'], 'intercept')
        self.assertAlmostEqual(result['intercept_time_s'], .9, delta=.001)

    def test_stationary_inside_and_boundary(self):
        for distance in (0., .2):
            result = estimate_ball_intercept(Pose2D(), linear((distance, 0.)))
            self.assertEqual(result['intercept_time_s'], 0.)

    def test_window_endpoint(self):
        result = estimate_ball_intercept(Pose2D(), linear((6.2, 0.)))
        self.assertEqual(result['intercept_time_s'], 3.)

    def test_unreachable_fallback(self):
        result = estimate_ball_intercept(Pose2D(), linear((10., 0.)))
        self.assertEqual(result['status'], 'fallback')
        self.assertIsNone(result['intercept_time_s'])
        self.assertAlmostEqual(result['score_s'], 4.9)

    def test_invalid_speeds_even_without_observations(self):
        for name in ('linear_speed', 'angular_speed'):
            for value in (0, -1, float('nan'), float('inf'), -float('inf'), None, '2', [], True):
                with self.subTest(name=name, value=value):
                    with self.assertRaisesRegex(ValueError, name):
                        estimate_ball_intercept(None, linear((0., 0.)), **{name: value})

    def test_budget_is_unknown(self):
        with patch('src.utils.interception.BALL_INTERCEPT_MAX_EVALUATIONS', 1):
            result = estimate_ball_intercept(Pose2D(), linear((2., 0.)))
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], 'search_budget_exhausted')
        self.assertIsNone(result['score_s'])

    def test_unresolved_window_is_unknown(self):
        # 故意放大数值精度，制造内部短窗口不能被采样证明的情况。
        with patch('src.utils.interception.BALL_INTERCEPT_TIME_TOLERANCE_S', .05):
            result = estimate_ball_intercept(Pose2D(), linear((-.5, 0.), (100., 0.)))
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], 'search_resolution_limit')

    def test_turn_and_omni_boundary(self):
        for distance in (1., 1.00001):
            result = estimate_ball_intercept(Pose2D(0., 0., math.pi), linear((distance, 0.)))
            expected = (distance-.2)/2 + (math.pi-.5 if distance > 1 else 0)
            if expected <= 3:
                self.assertEqual(result['status'], 'intercept')
                self.assertAlmostEqual(result['intercept_time_s'], expected, delta=.001)
            else:
                self.assertEqual(result['status'], 'fallback')

    def test_half_disc_disallows_wrong_side_immediate_control(self):
        result = estimate_ball_intercept(Pose2D(-.1, 0.), linear((0., 0.)), approach_normal=(1., 0.))
        self.assertAlmostEqual(result['intercept_time_s'], .05, delta=.001)
        self.assertGreaterEqual(result['target'][0], 0.)

    def test_stopping_ball(self):
        motion = BallMotion()
        for t, x in ((0., 1.), (.1, 1.01), (.2, 1.01)):
            motion.update(SimpleNamespace(now=t, ball=SimpleNamespace(x=x, y=0., last_seen_at=t)))
        result = estimate_ball_intercept(Pose2D(), motion)
        self.assertEqual(result['status'], 'intercept')
        self.assertAlmostEqual(result['intercept_time_s'], .405, delta=.001)

    def test_dense_reference_and_half_plane_boundaries(self):
        # 独立细网格核验：允许明确未知，不允许漏判为 fallback 或提前宣称可达。
        for origin, velocity, normal in (
                ((-.5, .2), (20., 0.), None),
                ((-.5, .201), (20., 0.), (0., 1.)),
                ((-2., .1), (8., 0.), (1., 0.)),
                ((1.2, 0.), (-3., 0.), None),
                ((.5, -2.), (0., 8.), (0., -1.))):
            pose = Pose2D(0., 0., math.pi)
            motion = linear(origin, velocity)
            def arrival(t):
                x, y = motion.predict_ball_position(t)
                if normal is not None:
                    rx, ry = -x, -y
                    side = rx*normal[0]+ry*normal[1]
                    if side < 0:
                        rx, ry = rx-side*normal[0], ry-side*normal[1]
                    length = math.hypot(rx, ry)
                    if length > .2:
                        rx, ry = rx*.2/length, ry*.2/length
                    x, y = x+rx, y+ry
                distance = math.hypot(x, y)
                angle = abs(math.atan2(math.sin(math.atan2(y,x)-pose.theta), math.cos(math.atan2(y,x)-pose.theta)))
                turn = max(0., angle-.5) if distance > 1 else 0.
                return turn + (distance if normal else max(0., distance-.2))/2
            dense = next((i*.0001 for i in range(30001) if arrival(i*.0001) <= i*.0001), None)
            result = estimate_ball_intercept(pose, motion, approach_normal=normal)
            if result['status'] == 'intercept':
                t = result['intercept_time_s']
                self.assertLessEqual(arrival(t), t+1e-12)
                if dense is not None:
                    self.assertLessEqual(t, dense+.001)
            elif dense is not None:
                self.assertEqual(result['status'], 'unavailable')

    def test_shared_parameters_and_controller(self):
        self.assertEqual(MAX_ANGULAR, 1.)
        self.assertEqual(BALL_CONTROL_RADIUS_M, .2)
        self.assertEqual(MotionController._angular(2.), 1.)
        self.assertEqual(MotionController._angular(-2.), -1.)

    def test_finite_fast_miss(self):
        result = estimate_ball_intercept(Pose2D(), linear((-.5, 1.), (20., 0.)))
        self.assertEqual(result['status'], 'fallback')


if __name__ == '__main__':
    unittest.main()
