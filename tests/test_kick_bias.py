import math
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from src.framework.config import SoccerConfig
from src.framework.types import ADULT_FIELD_DIMENSIONS, BallState, Context, Pose2D, RobotState
from src.player import Player
from src.utils.geom import normalize_angle


class KickBiasTests(unittest.TestCase):
    def setUp(self):
        self.player = Player(2, SoccerConfig(), Mock(mode='walk', fall_down_state='normal'))
        self.ctx = Context(0, 1/30, 1, ADULT_FIELD_DIMENSIONS,
                           ball=BallState(0.5, 0.2),
                           teammates={2: RobotState(2, Pose2D(0, 0))})
        self.player.context = self.ctx

    def test_five_degree_right_correction_only_changes_angle(self):
        self.player.kick(0, 1)
        angle, power, bx, by = self.player._backend.kick.call_args.args
        self.assertAlmostEqual(angle, -math.pi/36)
        self.assertEqual((power, bx, by), (1, 0.5, 0.2))

    def test_heading_wrap_and_simulated_left_bias_cancel(self):
        for heading in (0, math.pi/2, -math.pi/2, math.pi-0.01):
            for target in (0, 1, -math.pi+0.01):
                self.player.context = replace(self.ctx, teammates={2: RobotState(2, Pose2D(0, 0, heading))})
                self.player.kick(target, 1)
                angle = self.player._backend.kick.call_args.args[0]
                self.assertLessEqual(abs(angle), math.pi)
                # 仅验证角度代数关系，不模拟真实踢球物理。
                actual = normalize_angle(angle + heading + math.radians(5))
                self.assertAlmostEqual(normalize_angle(actual-target), 0)

    def test_repeated_commands_do_not_accumulate_correction(self):
        self.player.kick(0, 1)
        first = self.player._backend.kick.call_args
        self.player.kick(0, 1)
        self.assertEqual(self.player._backend.kick.call_args, first)

    def test_zero_disables_and_negative_bias_reverses_correction(self):
        for bias in (0, -5):
            with patch('src.player.KICK_LEFT_BIAS_DEG', bias):
                self.player.kick(0, 1)
                self.assertAlmostEqual(self.player._backend.kick.call_args.args[0], -math.radians(bias))
