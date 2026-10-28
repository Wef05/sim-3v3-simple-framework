import math
import unittest
from dataclasses import replace
from unittest.mock import Mock

from src.framework.config import SoccerConfig
from src.framework.types import ADULT_FIELD_DIMENSIONS, BallState, Context, Pose2D, RobotState
from src.player import Player
from src.param import GUARD_CLEAR_POWER, GUARD_PASS_POWER, KICK_LEFT_BIAS_DEG


class GuardDistributionTests(unittest.TestCase):
    def setUp(self):
        self.keeper = Player(1, SoccerConfig(), Mock(mode='walk', fall_down_state='normal'))
        self.receiver = Player(2, SoccerConfig(), Mock(mode='walk', fall_down_state='normal'))
        self.ctx = Context(now=0, dt=1/30, team_id=1, field=ADULT_FIELD_DIMENSIONS,
                           ball=BallState(-6, 0),
                           teammates={1: RobotState(1, Pose2D(-6.4, 0)),
                                      2: RobotState(2, Pose2D(-3, 0))},
                           opponents={1: RobotState(1, Pose2D(4, 3))})

    def run_guard(self, **kwargs):
        self.keeper.context = self.receiver.context = self.ctx
        self.keeper.guard(receivers=[self.receiver], **kwargs)
        return self.keeper.action

    def test_safe_forward_pass(self):
        self.assertEqual(self.run_guard(), 'guard:pass')
        self.assertEqual(self.keeper._backend.kick.call_args.args[:2], (-math.radians(KICK_LEFT_BIAS_DEG), GUARD_PASS_POWER))

    def test_pressure_and_potential_interception_clear(self):
        # 第二个对手不在当前球路上，但能在慢动作期间跑到球路。
        for pose in (Pose2D(-5.5, 0), Pose2D(-4, 2), Pose2D(-3, 0.5)):
            with self.subTest(pose=pose):
                self.ctx = replace(self.ctx, opponents={1: RobotState(1, pose)})
                self.assertEqual(self.run_guard(), 'guard:clear')
                direction, power, *_ = self.keeper._backend.kick.call_args.args
                self.assertGreater(math.cos(direction), 0)
                self.assertEqual(power, GUARD_CLEAR_POWER)

    def test_unknown_and_stale_opponents_clear(self):
        for opponents in ({}, {1: RobotState(1)}, {1: RobotState(1, Pose2D(4, 3), -2)}):
            self.ctx = replace(self.ctx, opponents=opponents)
            self.assertEqual(self.run_guard(), 'guard:clear')

    def test_backward_long_and_large_turn_passes_rejected(self):
        for pose in (Pose2D(-6.8, 1), Pose2D(0, 0), Pose2D(-4, 3)):
            self.ctx = replace(self.ctx, teammates={**self.ctx.teammates, 2: RobotState(2, pose)})
            self.assertEqual(self.run_guard(), 'guard:clear')

    def test_unready_receiver_rejected(self):
        self.receiver._backend.fall_down_state = 'fallen'
        self.assertEqual(self.run_guard(), 'guard:clear')

    def test_teammate_blocks_pass(self):
        self.ctx = replace(self.ctx, teammates={**self.ctx.teammates,
                                               3: RobotState(3, Pose2D(-4.5, 0))})
        self.assertEqual(self.run_guard(), 'guard:clear')

    def test_rechecks_safety_during_kick(self):
        self.assertEqual(self.run_guard(), 'guard:pass')
        self.ctx = replace(self.ctx, opponents={1: RobotState(1, Pose2D(-4, 0))})
        self.assertEqual(self.run_guard(), 'guard:clear')

    def test_restart_never_distributes(self):
        self.run_guard(allow_distribution=False)
        self.keeper._backend.kick.assert_not_called()

    def test_ball_leaves_releases_kick_and_returns_home(self):
        self.run_guard()
        self.ctx = replace(self.ctx, ball=BallState(-3.9, 0), opponents={})
        self.assertEqual(self.run_guard(), 'guard:home')
        self.assertFalse(self.keeper.is_kicking)
        self.keeper._backend.release_kick.assert_called()

    def test_no_ball_releases_kick(self):
        self.run_guard()
        self.ctx = replace(self.ctx, ball=None)
        self.run_guard()
        self.assertFalse(self.keeper.is_kicking)

    def test_distant_penalty_area_ball_is_collected_before_disposal(self):
        self.ctx = replace(self.ctx, ball=BallState(-5.4, 0), opponents={})
        self.assertEqual(self.run_guard(), 'guard:collect')
        self.keeper._backend.kick.assert_not_called()
