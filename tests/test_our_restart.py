import math
import types
import unittest
from dataclasses import replace
from unittest.mock import Mock

from test_fixed_goalkeeper import strategy
from src.framework.config import SoccerConfig
from src.framework.types import (
    Context, ADULT_FIELD_DIMENSIONS, BallState, GameControlState,
    GameState, SetPlay, RobotState, Pose2D,
)
from src.player import Player
from src.param import KICK_LEFT_BIAS_DEG
from src.restart import candidates


class OurRestartTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context(0, 1/30, 1, ADULT_FIELD_DIMENSIONS,
            game=GameControlState(state=GameState.PLAYING, kicking_team=1, secondary_time=10),
            ball=BallState(0, 0), teammates={
                1: RobotState(1, Pose2D(-6, 0)),
                2: RobotState(2, Pose2D(-0.8, 0, 0.3)),
                3: RobotState(3, Pose2D(-3, 2)),
            })
        self.players = [Player(pid, SoccerConfig(), Mock(mode="walk", fall_down_state="normal"))
                        for pid in (1, 2, 3)]
        self.store = types.SimpleNamespace()
        strategy.SoccerSimAgent.init_store(None, self.store)

    def step(self, **changes):
        self.ctx = replace(self.ctx, **changes)
        for p in self.players:
            p.context = self.ctx
            p._backend.reset_mock()
        strategy.SoccerSimAgent.play(self.ctx, self.players, self.store)

    def ready_receiver(self):
        target = self.store.restart_plan.target
        robots = dict(self.ctx.teammates)
        robots[3] = RobotState(3, Pose2D(*target))
        self.step(now=1, teammates=robots)

    def test_wait_run_pass_and_release(self):
        self.step()
        self.players[1]._backend.kick.assert_not_called()
        self.assertEqual(self.players[2].action, 'restart:run')
        self.ready_receiver()
        self.assertEqual(self.players[1]._backend.kick.call_args.args[1], 1.0)
        direction = self.players[1]._backend.kick.call_args.args[0]
        target = self.store.restart_plan.target
        self.assertAlmostEqual(direction, math.atan2(target[1], target[0])-0.3-math.radians(KICK_LEFT_BIAS_DEG))
        self.step(now=2, ball=BallState(0, 0.5))
        self.players[1]._backend.kick.assert_not_called()
        self.players[1]._backend.release_kick.assert_called()
        self.assertEqual(self.players[2].action, 'restart:receive')

    def test_blocked_targets_never_force_kick_even_after_timeout(self):
        opponents = {i: RobotState(i, Pose2D(*t)) for i, t in enumerate(candidates(self.ctx, 'kickoff'))}
        self.step(opponents=opponents)
        self.step(now=20)
        self.players[1]._backend.kick.assert_not_called()
        self.assertEqual(self.players[1].action, 'restart:blocked')

    def test_timeout_clear_lane_fallback(self):
        self.step()
        self.step(now=7)
        self.players[1]._backend.kick.assert_called_once()
        self.assertEqual(self.players[1].action, 'restart:fallback')
        self.assertEqual(self.players[1]._backend.kick.call_args.args[1], 1.0)

    def test_receiver_loss_cancels_old_plan_and_uses_single_player_fallback(self):
        self.step()
        self.players[2]._backend.fall_down_state = 'fallen'
        self.step(now=1)
        self.assertIsNone(self.store.restart_plan.receiver)
        self.players[2]._backend.release_kick.assert_called()
        self.step(now=8)
        self.players[1]._backend.kick.assert_called_once()

    def test_ball_missing_stops_both_outfield_players(self):
        self.step()
        self.ready_receiver()
        self.step(ball=None)
        for p in self.players[1:]:
            p._backend.kick.assert_not_called()
            p._backend.release_kick.assert_called()

    def test_normal_handoff_expires_and_ready_clears_plan(self):
        self.step()
        self.ready_receiver()
        self.step(now=2, game=replace(self.ctx.game, secondary_time=0), ball=BallState(0, 0.5))
        self.assertEqual(self.players[2].action, 'restart:receive')
        self.step(now=5)
        self.assertIsNotNone(self.store.restart_plan)
        self.step(now=11)
        self.assertIsNone(self.store.restart_plan)
        self.step(game=replace(self.ctx.game, secondary_time=10))
        self.assertIsNotNone(self.store.restart_plan)
        self.step(game=replace(self.ctx.game, state=GameState.READY))
        self.assertIsNone(self.store.restart_plan)

    def test_corner_and_goal_kick_points_inside_pitch(self):
        for key, ball in [(SetPlay.CORNER_KICK.value, BallState(7, 4.5)),
                          (SetPlay.CORNER_KICK.value, BallState(7, -4.5)),
                          (SetPlay.GOAL_KICK.value, BallState(-6, 0))]:
            points = list(candidates(replace(self.ctx, ball=ball), key))
            self.assertTrue(points)
            for x, y in points:
                self.assertLess(abs(x), 7)
                self.assertLess(abs(y), 4.5)
                self.assertLess(x, ball.x) if key == SetPlay.CORNER_KICK.value else self.assertGreater(x, ball.x)

    def test_same_phase_new_type_replans(self):
        self.step(game=replace(self.ctx.game, set_play=SetPlay.CORNER_KICK))
        first = self.store.restart_plan
        self.step(game=replace(self.ctx.game, set_play=SetPlay.GOAL_KICK))
        self.assertIsNot(first, self.store.restart_plan)
        self.assertEqual(self.store.restart_plan.key, SetPlay.GOAL_KICK.value)

    def test_far_taker_approaches_without_issuing_kick(self):
        self.step()
        self.players[1].context = replace(self.ctx, teammates={2: RobotState(2, Pose2D(-5, 0))})
        self.assertFalse(self.players[1].pass_to((0, 2), 3))
        self.players[1]._backend.kick.assert_not_called()

    def test_delayed_kick_starts_receive_clock_at_ball_motion(self):
        self.step()
        self.ready_receiver()
        self.step(now=12, ball=BallState(0, 0.5),
                  game=replace(self.ctx.game, secondary_time=0))
        self.assertEqual(self.store.restart_plan.moved_at, 12)
        self.assertEqual(self.players[2].action, 'restart:receive')
        self.players[2]._backend.kick.assert_not_called()
        self.assertEqual(self.players[1].action, 'restart:follow')
        self.players[1]._backend.kick.assert_not_called()

    def test_receiver_only_attacks_when_close_and_taker_keeps_running(self):
        self.step()
        self.ready_receiver()
        self.step(now=2, ball=BallState(0, 0.5))
        self.players[2]._backend.kick.assert_not_called()
        robots = dict(self.ctx.teammates)
        robots[3] = RobotState(3, Pose2D(-0.3, 0.5))
        self.step(now=3, teammates=robots)
        self.assertEqual(self.players[2].action, 'restart:attack')
        self.players[2]._backend.kick.assert_called_once()
        self.assertEqual(self.players[1].action, 'restart:follow')
        self.players[1]._backend.kick.assert_not_called()

    def test_receiver_lost_after_pass_does_not_reselect_original_taker(self):
        self.step()
        self.ready_receiver()
        self.step(now=2, ball=BallState(0, 0.5))
        self.players[2]._backend.fall_down_state = 'fallen'
        self.step(now=3)
        self.players[1]._backend.kick.assert_not_called()
        self.assertEqual(self.store.restart_plan.receiver, 3)

    def test_normal_without_ball_motion_does_not_force_receiver_role(self):
        self.step()
        self.step(now=2, game=replace(self.ctx.game, secondary_time=0))
        self.assertIsNone(self.store.restart_plan)
        self.assertNotEqual(self.players[2].action, 'restart:receive')

    def test_keeper_nearest_ball_never_takes_any_restart(self):
        robots = dict(self.ctx.teammates)
        robots[1] = RobotState(1, Pose2D(0.1, 0))
        for kind in SetPlay:
            with self.subTest(kind=kind):
                self.players[0]._kicking = True
                self.step(teammates=robots, game=replace(
                    self.ctx.game, set_play=kind, secondary_time=10))
                self.assertNotEqual(self.store.restart_plan.taker, 1)
                self.assertNotEqual(self.store.restart_plan.receiver, 1)
                self.players[0]._backend.kick.assert_not_called()
                self.players[0]._backend.release_kick.assert_called()

    def test_keeper_cannot_clear_during_normal_receive_handoff(self):
        self.step()
        self.ready_receiver()
        robots = dict(self.ctx.teammates)
        robots[1] = RobotState(1, Pose2D(0.1, 0.5))
        self.players[0]._kicking = True
        self.step(now=2, teammates=robots, ball=BallState(0, 0.5),
                  game=replace(self.ctx.game, secondary_time=0))
        self.assertEqual(self.players[2].action, 'restart:receive')
        self.players[0]._backend.kick.assert_not_called()
        self.players[0]._backend.release_kick.assert_called()

    def test_guard_default_call_cannot_bypass_restart_ban(self):
        robots = dict(self.ctx.teammates)
        robots[1] = RobotState(1, Pose2D(0.1, 0))
        self.step(teammates=robots)
        keeper = self.players[0]
        keeper._backend.reset_mock()
        keeper._kicking = True
        keeper.guard()
        keeper._backend.kick.assert_not_called()
        keeper._backend.release_kick.assert_called()

    def test_only_keeper_available_does_not_replace_taker(self):
        self.players[1]._backend.fall_down_state = 'fallen'
        self.players[2]._backend.fall_down_state = 'fallen'
        robots = dict(self.ctx.teammates)
        robots[1] = RobotState(1, Pose2D(0.1, 0))
        self.step(teammates=robots)
        self.assertIsNone(self.store.restart_plan)
        self.assertIsNone(self.store.kickoff_taker)
        self.players[0]._backend.kick.assert_not_called()
