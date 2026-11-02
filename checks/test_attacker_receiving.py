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
                    current_ball=(0, 0), intercept=dict(status='intercept', target=(1, 0)),
                    target=(7, 0), aim=KickTarget.goal(), style='shot', now=now,
                    phase='normal', speed=1., memory=self.memory, pose_observed_at=now)
        args.update(changes)
        intent, self.memory, detail = receive_intent(**args)
        return intent, detail

    def test_near_moving_ball_intercepts_instead_of_kicking(self):
        intent, info = self.step()
        self.assertIsInstance(intent, MoveIntent)
        self.assertEqual(intent.target, (1.6, 0))
        self.assertTrue(intent.avoid_ball)
        self.assertEqual(info['stage'], 'intercept')

    def test_rebound_strict_angle_boundary(self):
        for point, kick in (((-1, 0), True), ((0, 1), False), ((1e-6, 1), False),
                            ((-1e-6, 1), True), ((0, 0), False)):
            self.memory = None
            intent, info = self.step(intercept=dict(status='intercept', target=point))
            self.assertEqual(isinstance(intent, KickIntent), kick)
        self.assertIsNone(info['angle_deg'])

    def test_degenerate_outgoing_target_does_not_rebound(self):
        intent, info = self.step(target=(0, 0))
        self.assertIsInstance(intent, MoveIntent)
        self.assertIsNone(info['angle_deg'])

    def test_far_rebound_approaches_then_kicks_without_stability(self):
        intercept = dict(status='intercept', target=(-1, 0))
        intent, info = self.step(pose=Pose2D(-4, 0), intercept=intercept)
        self.assertEqual(intent.target, (-1.35, 0))
        self.assertEqual(info['stage'], 'rebound_chase')
        intent, _ = self.step(.1, intercept=intercept)
        self.assertIsInstance(intent, KickIntent)

    def test_stationary_ball_requires_fresh_stable_observations(self):
        for now in (0., .1, .2, .3, .4):
            self.assertIsInstance(self.step(now, speed=0.)[0], FaceIntent)
        intent, info = self.step(.45, speed=0.)
        self.assertIsInstance(intent, KickIntent)
        self.assertEqual(info['reason'], 'near_ball_low_speed')
        self.assertFalse(info['contact_confirmed'])

    def test_same_snapshot_cannot_accumulate_stability(self):
        self.step(speed=0.)
        intent, info = self.step(.2, speed=0., ball=BallState(0, 0, last_seen_at=0))
        self.assertIsInstance(intent, FaceIntent)
        self.assertEqual(info['stable_s'], 0)

    def test_unknown_speed_or_ball_departure_breaks_stability(self):
        for change in (dict(speed=None), dict(speed=2.), dict(pose=Pose2D(-.36, 0)),
                       dict(current_ball=(1, 0))):
            self.memory = None
            self.step(speed=0.)
            self.step(.1, **change)
            self.assertNotIsInstance(self.step(.2, speed=0.)[0], KickIntent)

    def test_arrival_waits_without_claiming_interception(self):
        intent, info = self.step(pose=Pose2D(1.6, 0))
        self.assertEqual(info['stage'], 'wait_ball')
        self.assertIsInstance(intent, FaceIntent)
        self.assertAlmostEqual(intent.theta, 3.141592653589793)

    def test_auto_kick_holds_across_angle_noise_and_exits_at_distance(self):
        self.step(intercept=dict(status='intercept', target=(-1, 0)))
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

    def test_fallback_is_chase_not_reachable_claim(self):
        intent, info = self.step(intercept=dict(status='fallback', target=(3, 0)))
        self.assertEqual(intent.target, (3.6, 0))
        self.assertEqual(info['intercept_status'], 'fallback')
        self.assertIsInstance(self.step(intercept=dict(status='unavailable'))[0], StopIntent)

    def test_player_integration_and_role_reset(self):
        p = player(pose=(2.4, 0))
        with patch('src.player.evaluate_attack_targets', return_value=candidates([.2]*7)), patch(
                'src.player.evaluate_scoring', return_value={'evaluated_at':0}):
            self.assertIsInstance(p.attack(), FaceIntent)  # 到位但球速未知，不启动踢球。
            for now in (.1, .2, .3, .4, .45):
                p.context = replace(p.context, now=now, ball=BallState(3, 0, last_seen_at=now),
                                    teammates={3: replace(p.context.teammates[3], last_seen_at=now)})
                p.motion.update(p.context)
                intent = p.attack()
            self.assertIsInstance(intent, KickIntent)
            self.assertEqual(p.attack_receive['reason'], 'near_ball_low_speed')
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

    def test_wait_point_does_not_follow_small_prediction_updates(self):
        self.step(pose=Pose2D(1.6, 0))
        intent, info = self.step(.1, pose=Pose2D(1.6, 0),
                                 intercept=dict(status='intercept', target=(1.2, 0)))
        self.assertIsInstance(intent, FaceIntent)
        self.assertEqual(info['wait_point'], (1.6, 0))
        self.assertFalse(info['replanned'])

    def test_direction_change_replans_wait_point(self):
        self.step()
        intent, info = self.step(.1, intercept=dict(status='intercept', target=(0, 1)))
        self.assertTrue(info['replanned'])
        self.assertEqual(intent.target, (0, 1.6))

    def test_stationary_ball_waits_on_robot_side_and_does_not_walk_into_ball(self):
        intent, info = self.step(pose=Pose2D(-2, 0), speed=0.)
        self.assertEqual(intent.target, (-.6, 0))
        self.assertTrue(intent.avoid_ball)
        self.assertEqual(info['intercept_point'], (1, 0))
        self.assertIsInstance(self.step(.1, pose=Pose2D(-.6, 0), speed=0.)[0], FaceIntent)
        self.assertGreater(self.memory.robot_speed, .15)
        self.assertIsInstance(self.step(.2, speed=0.)[0], FaceIntent)
        for now in (.3, .4, .5):
            self.assertIsInstance(self.step(now, speed=0.)[0], FaceIntent)
        self.assertIsInstance(self.step(.55, speed=0.)[0], KickIntent)

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

    def test_already_too_close_backs_out_before_handoff(self):
        intent, info = self.step(pose=Pose2D(-.2, 0), speed=0.)
        self.assertEqual(intent.target, (-.6, 0))
        self.assertFalse(info['arrived'])
        for now in (.1, .3):
            self.assertIsInstance(self.step(now, pose=Pose2D(-.2, 0), speed=0.)[0], MoveIntent)

    def test_arrival_holds_through_small_drift_and_hands_off(self):
        self.step(speed=0.)
        for i in range(1, 26):
            x = -.53 + (.002 if i % 2 else -.002)
            intent, info = self.step(i*.02, speed=0., pose=Pose2D(x, 0))
            self.assertTrue(self.memory.arrived)
            self.assertNotIsInstance(intent, MoveIntent)
        self.assertIsInstance(intent, KickIntent)

    def test_arrival_exit_clears_stability_and_resumes_safe_approach(self):
        self.step(speed=0.)
        intent, info = self.step(.1, speed=0., pose=Pose2D(-.44, 0))
        self.assertIsInstance(intent, MoveIntent)
        self.assertTrue(intent.avoid_ball)
        self.assertFalse(info['arrived'])
        self.assertIsNone(self.memory.stable_since)

    def test_duplicate_robot_observation_cannot_complete_window(self):
        for i in range(10):
            intent, info = self.step(i*.05, speed=0., pose_observed_at=0.)
            self.assertNotIsInstance(intent, KickIntent)
            self.assertEqual(info['settling']['sample_count'], 1)

    def test_window_rejects_continuous_and_back_forth_motion(self):
        from src.utils.attacker_receiving import _settle_metrics
        for positions in ([(-.6+i*.01, 0) for i in range(31)],
                          [(-.6+.06*math.cos(i*math.pi/15), 0) for i in range(31)]):
            memory = ReceiveMemory('normal', 0.)
            for i, position in enumerate(positions):
                metrics = _settle_metrics(memory, i*.01, position)
            self.assertTrue(metrics['ready'])
            self.assertFalse(metrics['settled'])

    def test_window_rewind_and_stale_pose_clear_evidence(self):
        for now in (0., .1, .2, .3):
            self.step(now, speed=0.)
        intent, info = self.step(.35, speed=0., pose_observed_at=.15)
        self.assertNotIsInstance(intent, KickIntent)
        self.assertEqual(info['settling']['sample_count'], 1)
        self.assertIsInstance(self.step(.8, speed=0., pose_observed_at=.1)[0], StopIntent)
        self.assertIsNone(self.memory)

    def test_recorded_opening_jitter_reaches_normal_handoff(self):
        import json
        from pathlib import Path
        samples = json.loads((Path(__file__).parent/'fixtures/attacker-settle-opening.json').read_text())
        for sample in samples:
            intent, info = self.step(
                sample['now'], pose=Pose2D(**sample['pose']),
                pose_observed_at=sample['pose_observed_at'],
                ball=BallState(**sample['ball']), current_ball=tuple(sample['current_ball']),
                intercept=sample['intercept'], target=tuple(sample['target']), speed=sample['speed'])
            if isinstance(intent, KickIntent):
                self.assertEqual(info['reason'], 'near_ball_low_speed')
                self.assertLess(sample['now'], 60.)
                self.assertTrue(info['settling']['settled'])
                break
        else:
            self.fail('Recorded opening never reaches normal handoff')


if __name__ == '__main__':
    unittest.main()
