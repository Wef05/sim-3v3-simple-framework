import math
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from src.framework.config import SoccerConfig
from src.framework.types import ADULT_FIELD_DIMENSIONS, BallState, Context, Pose2D, RobotState
from src.player import Player
from src.param import KICK_LEFT_BIAS_DEG


class ShotSelectionTests(unittest.TestCase):
    def setUp(self):
        self.player = Player(2, SoccerConfig(), Mock(mode='walk', fall_down_state='normal'))
        self.ctx = Context(0, 1/30, 1, ADULT_FIELD_DIMENSIONS,
                           ball=BallState(3, 0), teammates={2: RobotState(2, Pose2D(2.5, 0, 0.2))})
        self.player.context = self.ctx

    def opponents(self, *points):
        self.player.context = replace(self.ctx, opponents={
            i: RobotState(i, Pose2D(*point)) for i, point in enumerate(points)})

    def test_clear_goal_prefers_center(self):
        self.assertEqual(self.player.shot_target(), (7, 0))

    def test_center_keeper_changes_angle_inside_posts(self):
        self.opponents((6.5, 0))
        target = self.player.shot_target()
        self.assertGreater(abs(target[1]), 0.5)
        self.assertLess(abs(target[1]), 1.3)
        self.assertTrue(self.player.kick_can_score(self.player.plan_kick()[0]))

    def test_defender_switching_sides_changes_shot(self):
        self.opponents((6.5, 0.6))
        self.assertLess(self.player.shot_target()[1], 0)
        self.opponents((6.5, -0.6))
        self.assertGreater(self.player.shot_target()[1], 0)

    def test_behind_ball_and_goal_do_not_block(self):
        self.opponents((2, 0), (8, 0))
        self.assertEqual(self.player.shot_target(), (7, 0))

    def test_teammate_blocking_center_also_changes_angle(self):
        self.player.context = replace(self.ctx, teammates={**self.ctx.teammates,
            3: RobotState(3, Pose2D(6, 0))})
        self.assertGreater(abs(self.player.shot_target()[1]), 0.5)

    def test_small_noise_preserves_target(self):
        self.opponents((6.5, 0))
        target = self.player.shot_target()
        self.opponents((6.5, -0.001))
        self.assertEqual(self.player.shot_target(), target)

    def test_attack_uses_selected_angle_in_body_coordinates(self):
        self.opponents((6.5, 0.6))
        target = self.player.shot_target()
        self.player.attack()
        direction = self.player._backend.kick.call_args.args[0]
        self.assertAlmostEqual(direction, math.atan2(target[1], 4)-0.2-math.radians(KICK_LEFT_BIAS_DEG))

    def test_far_approach_aligns_with_selected_target(self):
        self.opponents((6.5, 0.6))
        self.player.context = replace(self.player.context, teammates={2: RobotState(2, Pose2D(0, 0))})
        with patch.object(self.player, 'walk_to') as walk:
            self.player.attack()
        self.assertGreater(walk.call_args.args[0][1], 0)
        self.player._backend.kick.assert_not_called()

    def test_explicit_target_and_pass_stay_exact(self):
        self.opponents((6.5, 0.6))
        self.player.attack((3, 2))
        self.assertAlmostEqual(self.player._backend.kick.call_args.args[0], math.pi/2-0.2-math.radians(KICK_LEFT_BIAS_DEG))
        self.player.pass_to((3, -2), 1)
        direction, power, *_ = self.player._backend.kick.call_args.args
        self.assertAlmostEqual(direction, -math.pi/2-0.2-math.radians(KICK_LEFT_BIAS_DEG))
        self.assertEqual(power, 1)

    def test_missing_ball_and_ball_beyond_goal_do_not_shoot(self):
        for ball in (None, BallState(7.1, 0)):
            self.player.context = replace(self.ctx, ball=ball)
            self.player.attack()
            self.player._backend.kick.assert_not_called()

    def test_fully_blocked_goal_still_selects_inside_goal(self):
        self.opponents((6, -0.8), (6, 0), (6, 0.8))
        self.assertLessEqual(abs(self.player.shot_target()[1]), 1.05)
