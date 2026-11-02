"""接球、补射边界与跨帧生命周期回归。"""
import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.framework.types import BallState, Pose2D
from src.intents import FaceIntent, KickIntent, KickTarget, MoveIntent, StopIntent, RoleTask, Role
from src.utils.attacker_receiving import receive_intent, ReceiveMemory
from checks.test_shot_selection import player, candidates


class AttackerReceivingTests(unittest.TestCase):
    def setUp(self):
        self.memory = None

    def step(self, now=0., **changes):
        args = dict(pose=Pose2D(-.6, 0, 0), ball=BallState(0, 0, last_seen_at=now),
                    current_ball=(0, 0), intercept=dict(status='reachable', target=(1, 0)),
                    target=(7, 0), aim=KickTarget.goal(), style='shot', now=now,
                    phase='normal', speed=1., memory=self.memory, pose_observed_at=now)
        args.update(changes)
        intent, self.memory, detail = receive_intent(**args)
        return intent, detail

    def test_low_speed_strict_boundary_and_invalid_speed(self):
        for speed, expected in [(0., True), (.499999, True), (.5, False),
                                (.500001, False), (None, False), (-1., False),
                                (float('nan'), False), (float('inf'), False)]:
            self.memory = None
            intent, info = self.step(speed=speed)
            self.assertEqual(isinstance(intent, KickIntent), expected, speed)
            if expected:
                self.assertEqual(info['reason'], 'low_speed_direct')
                self.assertEqual(info['speed_kind'], 'estimated_current')

    def test_far_low_speed_uses_current_ball_and_avoids_ball(self):
        intent, info = self.step(speed=.4, pose=Pose2D(-3, 0), intercept=None)
        self.assertIsInstance(intent, MoveIntent)
        self.assertEqual(intent.target, (-.35, 0))
        self.assertTrue(intent.avoid_ball)
        self.assertEqual(info['stage'], 'low_speed_chase')
        intent, info = self.step(.1, speed=.4, pose=Pose2D(-2, 0), intercept=None)
        self.assertIsInstance(intent, KickIntent)
        self.assertEqual(info['reason'], 'low_speed_direct')

    def test_decelerating_ball_exits_intercept_without_wait(self):
        self.step()
        intent, info = self.step(.1, speed=.49)
        self.assertIsInstance(intent, KickIntent)
        self.assertFalse(self.memory.arrived)
        self.assertEqual(info['reason'], 'low_speed_direct')
        self.assertIsInstance(self.step(.2, speed=.51)[0], KickIntent)

    def test_far_ball_acceleration_returns_to_safe_intercept(self):
        self.step(speed=.49, pose=Pose2D(-3, 0))
        intent, info = self.step(.1, speed=.5, pose=Pose2D(-3, 0))
        self.assertEqual(info['stage'], 'intercept')
        self.assertTrue(intent.avoid_ball)
        self.assertEqual(intent.target, (1, 0))

    def test_low_speed_does_not_bypass_invalid_observation(self):
        for changes in [dict(ball=BallState(0, 0, last_seen_at=-1.)),
                        dict(pose_observed_at=-1.), dict(current_ball=None)]:
            self.memory = None
            self.assertIsInstance(self.step(speed=.1, **changes)[0], StopIntent)

    def test_station_uses_calculator_target_and_updates_without_offset(self):
        intent, info = self.step(intercept=dict(status='reachable', target=(1, .2), ball_position=(1.25, .2)))
        self.assertEqual(intent.target, (1, .2))
        self.assertTrue(intent.avoid_ball)
        intent, info = self.step(.1, intercept=dict(status='reachable', target=(1.1, .2)))
        self.assertEqual(intent.target, (1.1, .2))
        self.assertNotIn('wait_point', info)

    def test_arrival_hysteresis_tracks_same_frame_target(self):
        self.step(pose=Pose2D(1, 0))
        intent, _ = self.step(.1, pose=Pose2D(1.08, 0))
        self.assertIsInstance(intent, FaceIntent)
        intent, _ = self.step(.2, pose=Pose2D(1.16, 0))
        self.assertIsInstance(intent, MoveIntent)
        self.assertEqual(intent.target, (1, 0))

    def test_frame_and_telemetry_share_calculator_result(self):
        from types import SimpleNamespace
        from src.framework.decision_frame import prepare_frame
        from src.telemetry import make_snapshot
        p = player()
        store = SimpleNamespace()
        expected = dict(status='reachable', target=(4., 0), ball_position=(4.25, 0))
        with patch('src.framework.decision_frame.calculate_block_point', return_value=expected) as calc:
            prepare_frame(p.context, [p], store)
            self.assertEqual(calc.call_count, 1)
        with patch('src.telemetry.calculate_block_point', side_effect=AssertionError('own result recalculated')):
            snapshot = make_snapshot(p.context, [p], store)
        self.assertEqual(snapshot['block_points'][0]['target'], expected['target'])
        with patch('src.player.evaluate_attack_targets', return_value=candidates([.2]*7)), patch(
                'src.player.evaluate_scoring', return_value={'evaluated_at':0}):
            intent = p.attack()
        self.assertIsInstance(intent, MoveIntent)
        self.assertEqual(intent.target, expected['target'])
        self.assertIs(p.attack_receive['block_point'], expected)

    def test_player_uses_current_speed_instead_of_interval_average(self):
        from src.utils.ball_motion import BallMotion
        p = player(pose=(2.4, 0))
        p.motion = BallMotion(mode='pure_rolling')
        p.motion.update(p.context)
        p.context = replace(p.context, now=.1, ball=BallState(3.051, 0, last_seen_at=.1),
                            teammates={3: replace(p.context.teammates[3], last_seen_at=.1)})
        p.motion.update(p.context)
        self.assertGreater(p.motion.observed_speed(), .5)
        self.assertLess(p.motion.current_speed(), .5)
        with patch('src.player.evaluate_attack_targets', return_value=candidates([.2]*7)), patch(
                'src.player.evaluate_scoring', return_value={'evaluated_at':0}):
            self.assertIsInstance(p.attack(), KickIntent)
        self.assertEqual(p.attack_receive['reason'], 'low_speed_direct')
        self.assertEqual(p.attack_receive['speed_mps'], p.motion.current_speed())
        self.assertEqual(p.attack_receive['observed_interval_speed_mps'], p.motion.observed_speed())

    def test_near_moving_ball_intercepts_instead_of_kicking(self):
        intent, info = self.step()
        self.assertIsInstance(intent, MoveIntent)
        self.assertEqual(intent.target, (1, 0))
        self.assertTrue(intent.avoid_ball)
        self.assertEqual(info['stage'], 'intercept')

    def test_rebound_strict_angle_boundary(self):
        for point, kick in (((-1, 0), True), ((0, 1), False), ((1e-6, 1), False),
                            ((-1e-6, 1), True), ((0, 0), False)):
            self.memory = None
            intent, info = self.step(intercept=dict(status='reachable', target=point))
            self.assertEqual(isinstance(intent, KickIntent), kick)
        self.assertIsNone(info['angle_deg'])

    def test_degenerate_outgoing_target_does_not_rebound(self):
        intent, info = self.step(target=(0, 0))
        self.assertIsInstance(intent, MoveIntent)
        self.assertIsNone(info['angle_deg'])

    def test_far_rebound_approaches_then_kicks_without_stability(self):
        intercept = dict(status='reachable', target=(-1, 0), ball_position=(-1.2, 0))
        intent, info = self.step(pose=Pose2D(-4, 0), intercept=intercept)
        self.assertAlmostEqual(intent.target[0], -1.55)
        self.assertEqual(intent.target[1], 0)
        self.assertEqual(info['stage'], 'rebound_chase')
        intent, _ = self.step(.1, intercept=intercept)
        self.assertIsInstance(intent, KickIntent)




    def test_arrival_waits_without_claiming_interception(self):
        intent, info = self.step(pose=Pose2D(1, 0))
        self.assertEqual(info['stage'], 'wait_ball')
        self.assertIsInstance(intent, FaceIntent)
        self.assertAlmostEqual(intent.theta, 3.141592653589793)

    def test_auto_kick_holds_across_angle_noise_and_exits_at_distance(self):
        self.step(intercept=dict(status='reachable', target=(-1, 0)))
        intent, info = self.step(.1, pose=Pose2D(-2.4, 0))
        self.assertIsInstance(intent, KickIntent)
        self.assertEqual(info['reason'], 'kick_hysteresis')
        intent, _ = self.step(.2, pose=Pose2D(-2.6, 0))
        self.assertIsInstance(intent, MoveIntent)

    def test_reset_on_phase_clock_and_gap(self):
        for changes in (dict(phase='kickoff'), dict(now=-.1), dict(now=1.)):
            self.memory = ReceiveMemory('normal', 0., stage='auto_kick')
            self.assertIsInstance(self.step(**changes)[0], MoveIntent)

    def test_missing_stale_or_invalid_observation_stops_and_clears(self):
        for changes in (dict(ball=None), dict(current_ball=None),
                        dict(ball=BallState(0, 0, last_seen_at=-1)),
                        dict(current_ball=(float('nan'), 0))):
            self.memory = ReceiveMemory('normal', 0., stage='auto_kick')
            self.assertIsInstance(self.step(**changes)[0], StopIntent)
            self.assertIsNone(self.memory)

    def test_old_interception_fallback_is_not_a_block_point(self):
        intent, info = self.step(intercept=dict(status='fallback', target=(3, 0)))
        self.assertIsInstance(intent, StopIntent)
        self.assertEqual(info['reason'], 'no_feasible_block_point')
        self.assertIsInstance(self.step(intercept=dict(status='unavailable'))[0], StopIntent)

    def test_player_integration_and_role_reset(self):
        p = player(pose=(2.4, 0))
        with patch('src.player.evaluate_attack_targets', return_value=candidates([.2]*7)), patch(
                'src.player.evaluate_scoring', return_value={'evaluated_at':0}):
            self.assertIsInstance(p.attack(), StopIntent)  # 球速未知且无计算器结果，不追旧预测点。
            for now in (.1, .2, .3, .4, .45):
                p.context = replace(p.context, now=now, ball=BallState(3, 0, last_seen_at=now),
                                    teammates={3: replace(p.context.teammates[3], last_seen_at=now)})
                p.motion.update(p.context)
                intent = p.attack()
            self.assertIsInstance(intent, KickIntent)
            self.assertIn(p.attack_receive['reason'], ('low_speed_direct', 'kick_hysteresis'))
            p.decide(RoleTask(Role.STAY))
            self.assertIsNone(p._receive_memory)
            self.assertIsNone(p.attack_receive)

    def test_observed_speed_invalid_after_gap(self):
        p = player()
        self.assertIsNone(p.motion.observed_speed())
        p.context = replace(p.context, now=.1, ball=BallState(3, 0, last_seen_at=.1))
        p.motion.update(p.context)
        self.assertEqual(p.motion.observed_speed(), 0.)
        p.context = replace(p.context, now=1.)
        p.motion.update(p.context)
        self.assertIsNone(p.motion.observed_speed())




    def test_navigation_blocks_crossing_ball_on_way_to_wait_point(self):
        from checks.test_navigation import context, controller, frame
        from src.utils.path_planner import segment_clear
        intent, _ = self.step(pose=Pose2D(-1, 0))
        ctx = context(((-1., 0.),), now=0., ball=BallState(0, 0))
        nav = frame(ctx)
        self.assertFalse(segment_clear((-1, 0), intent.target, nav.route_obstacles(1, intent)))
        self.assertEqual(nav.check(1, intent, (2., 0., 0.)), 'ball')
        cmd, _ = controller(ctx).plan_walk(intent, nav)
        self.assertIsNone(nav.check(1, intent, cmd))









if __name__ == '__main__':
    unittest.main()
