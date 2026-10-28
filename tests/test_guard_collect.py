import math
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_fixed_goalkeeper import strategy
from src.framework.config import SoccerConfig
from src.framework.types import (
    ADULT_FIELD_DIMENSIONS, BallState, Context, GameControlState,
    GameState, Pose2D, RobotState, SetPlay,
)
from src.param import GUARD_BALL_ENTER_M, GUARD_CLEAR_POWER, GUARD_PASS_POWER
from src.player import Player
from src.utils.geom import ball_in_own_penalty_area


class GuardCollectTests(unittest.TestCase):
    def setUp(self):
        self.keeper = Player(1, SoccerConfig(), Mock(mode="walk", fall_down_state="normal"))
        self.ctx = Context(
            now=0, dt=1 / 30, team_id=1, field=ADULT_FIELD_DIMENSIONS,
            game=GameControlState(state=GameState.PLAYING),
            ball=BallState(-4.5, 0),
            teammates={1: RobotState(1, Pose2D(-6.4, 0))},
        )

    def guard(self, **kwargs):
        self.keeper.context = self.ctx
        self.keeper.guard(**kwargs)

    def test_penalty_area_bounds_include_lines_and_exclude_opponent_area(self):
        for x, y, expected in (
            (-7, 0, True), (-4, 0, True), (-5, 3, True), (-5, -3, True),
            (-3.999, 0, False), (-7.001, 0, False), (-5, 3.001, False),
            (5, 0, False),
        ):
            with self.subTest(x=x, y=y):
                self.assertEqual(ball_in_own_penalty_area(
                    replace(self.ctx, ball=BallState(x, y))), expected)
        self.assertFalse(ball_in_own_penalty_area(replace(self.ctx, ball=None)))

    def test_distant_ball_has_priority_over_shot_line_interception(self):
        with patch.object(self.keeper, "_guard_intercept_target") as intercept:
            self.guard()
        self.assertEqual(self.keeper.action, "guard:collect")
        intercept.assert_not_called()
        self.keeper._backend.kick.assert_not_called()
        vx, _, _ = self.keeper._backend.set_velocity.call_args.args
        self.assertGreater(vx, 0)

    def test_collects_from_side_of_penalty_area(self):
        self.ctx = replace(self.ctx, ball=BallState(-4.5, 2.5))
        with patch.object(self.keeper, "walk_to") as walk:
            self.guard()
        self.assertEqual(self.keeper.action, "guard:collect")
        target = walk.call_args.args[0]
        self.assertLess(target[0], self.ctx.ball.x)
        self.assertTrue(walk.call_args.kwargs["avoid_ball"])
        self.assertTrue(walk.call_args.kwargs["avoid_robots"])
        # 目标到球的距离 + 到达误差必须小于出脚半径。
        self.assertLess(math.dist(target, (self.ctx.ball.x, self.ctx.ball.y))
                        + walk.call_args.kwargs["arrive_dist"], GUARD_BALL_ENTER_M)

    def test_motion_commands_reach_kick_range_in_static_open_field(self):
        # 仅积分底盘指令验证无“到点但仍够不到球”的死区,不模拟物理踢球。
        for _ in range(300):
            self.guard()
            if self.keeper.is_kicking:
                break
            vx, vy, angular = self.keeper._backend.set_velocity.call_args.args
            pose = self.keeper.pose
            dt = self.ctx.dt
            pose = Pose2D(
                pose.x + (vx * math.cos(pose.theta) - vy * math.sin(pose.theta)) * dt,
                pose.y + (vx * math.sin(pose.theta) + vy * math.cos(pose.theta)) * dt,
                pose.theta + angular * dt,
            )
            self.ctx = replace(self.ctx, now=self.ctx.now + dt,
                               teammates={1: RobotState(1, pose)})
        self.assertTrue(self.keeper.is_kicking)
        self.assertEqual(self.keeper.action, "guard:clear")
        self.assertEqual(self.keeper._backend.kick.call_args.args[1], GUARD_CLEAR_POWER)

    def test_safe_teammate_is_passed_to_after_approach(self):
        receiver = Player(2, SoccerConfig(), Mock(mode="walk", fall_down_state="normal"))
        self.guard(receivers=[receiver])
        self.assertEqual(self.keeper.action, "guard:collect")
        self.ctx = replace(
            self.ctx, ball=BallState(-5.5, 0),
            teammates={1: RobotState(1, Pose2D(-6, 0)),
                       2: RobotState(2, Pose2D(-2.5, 0))},
            opponents={1: RobotState(1, Pose2D(4, 3))},
        )
        receiver.context = self.ctx
        self.guard(receivers=[receiver])
        self.assertEqual(self.keeper.action, "guard:pass")
        self.assertEqual(self.keeper._backend.kick.call_args.args[1], GUARD_PASS_POWER)

    def test_ball_leaving_penalty_area_cancels_collection(self):
        self.guard()
        self.ctx = replace(self.ctx, ball=BallState(-3.9, 0))
        self.guard()
        self.assertEqual(self.keeper.action, "guard:home")
        self.assertFalse(self.keeper.is_kicking)

    def test_referee_restrictions_and_explicit_disable_prevent_collection(self):
        games = [replace(self.ctx.game, state=GameState.SET),
                 replace(self.ctx.game, stopped=True),
                 replace(self.ctx.game, secondary_time=5)]
        games += [replace(self.ctx.game, set_play=s, kicking_team=2)
                  for s in SetPlay if s != SetPlay.NONE]
        for game in games:
            with self.subTest(game=game):
                self.ctx = replace(self.ctx, game=game)
                self.guard()
                self.assertNotEqual(self.keeper.action, "guard:collect")
                self.keeper._backend.kick.assert_not_called()
        self.ctx = replace(self.ctx, game=GameControlState(state=GameState.PLAYING))
        self.guard(allow_distribution=False)
        self.assertNotEqual(self.keeper.action, "guard:collect")

    def test_penalty_area_danger_overrides_old_restart_followup(self):
        store = SimpleNamespace(normal_attacker=None, restart_plan=object())
        self.keeper.context = self.ctx
        strategy._act_normal(self.ctx, [self.keeper], store)
        self.assertIsNone(store.restart_plan)
        self.assertEqual(self.keeper.action, "guard:collect")


if __name__ == "__main__":
    unittest.main()
