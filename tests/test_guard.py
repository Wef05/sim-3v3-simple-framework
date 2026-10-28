import math
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from src.framework.config import SoccerConfig
from src.framework.types import (
    ADULT_FIELD_DIMENSIONS, BallState, Context, Pose2D, RobotState,
)
from src.param import MAX_LINEAR
from src.player import Player
from src.utils.geom import closest_point_on_ray, closest_point_on_ray_in_rect


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.player = Player(1, SoccerConfig(), Mock())
        self.player.context = Context(
            now=0.0, dt=1 / 30, team_id=1, field=ADULT_FIELD_DIMENSIONS,
            ball=BallState(-2.0, 1.0),
            teammates={1: RobotState(1, Pose2D(-6.5, 0.0))},
            opponents={4: RobotState(4, Pose2D(0.0, 1.0, math.pi))},
        )

    def set_opponent(self, pose):
        self.player.context = replace(
            self.player.context, opponents={4: RobotState(4, pose)},
        )

    def test_extended_shot_line_projects_to_closest_point(self):
        # 对手到球的线段未进入禁区,向球方向延伸的射线进入禁区。
        self.assertEqual(self.player._guard_intercept_target(), (-6.5, 1.0))

    def test_distance_is_strictly_less_than_five(self):
        self.set_opponent(Pose2D(3.0, 1.0, math.pi))
        self.assertIsNone(self.player._guard_intercept_target())
        self.set_opponent(Pose2D(2.999, 1.0, math.pi))
        self.assertIsNotNone(self.player._guard_intercept_target())

    def test_ball_behind_opponent_does_not_trigger(self):
        self.set_opponent(Pose2D(0.0, 1.0, 0.0))
        self.assertIsNone(self.player._guard_intercept_target())

    def test_front_semicircle_includes_ninety_degree_boundary(self):
        self.set_opponent(Pose2D(0.0, 1.0, math.pi / 2))
        self.assertIsNotNone(self.player._guard_intercept_target())
        self.set_opponent(Pose2D(0.0, 1.0, math.pi / 2 - 0.001))
        self.assertIsNone(self.player._guard_intercept_target())

    def test_backward_extension_through_penalty_area_does_not_trigger(self):
        self.set_opponent(Pose2D(-3.0, 1.0, 0.0))
        self.assertIsNone(self.player._guard_intercept_target())

    def test_ray_missing_penalty_area_does_not_trigger(self):
        self.player.context = replace(self.player.context, ball=BallState(-2, 4))
        self.set_opponent(Pose2D(0, 4, math.pi))
        self.assertIsNone(self.player._guard_intercept_target())

    def test_diagonal_projection_is_not_clipped_to_penalty_boundary(self):
        self.player.context = replace(
            self.player.context, ball=BallState(-2, 0),
            teammates={1: RobotState(1, Pose2D(-6, 0))},
        )
        self.set_opponent(Pose2D(0, -2, 3 * math.pi / 4))
        self.assertEqual(self.player._guard_intercept_target(), (-4.0, 2.0))
        self.player.context = replace(
            self.player.context,
            teammates={1: RobotState(1, Pose2D(-7, 4))},
        )
        self.assertEqual(self.player._guard_intercept_target(), (-6.5, 4.5))

    def test_projection_before_penalty_area_remains_on_full_ray(self):
        self.player.context = replace(
            self.player.context,
            teammates={1: RobotState(1, Pose2D(-3, 0))},
        )
        self.assertEqual(self.player._guard_intercept_target(), (-3.0, 1.0))

    def test_projection_towards_enemy_uses_ball_as_ray_endpoint(self):
        self.player.context = replace(
            self.player.context,
            teammates={1: RobotState(1, Pose2D(-1, 0))},
        )
        self.assertEqual(self.player._guard_intercept_target(), (-2.0, 1.0))

    def test_ray_projection_is_collinear_and_points_away_from_enemy(self):
        ball, enemy = (-2.0, 1.0), (0.0, -1.0)
        direction = (ball[0] - enemy[0], ball[1] - enemy[1])
        for point in ((-6, 0), (-7, 4), (-1, 0), (-2, 1)):
            with self.subTest(point=point):
                target = closest_point_on_ray(ball, direction, point)
                rx, ry = target[0] - ball[0], target[1] - ball[1]
                self.assertAlmostEqual(rx * direction[1] - ry * direction[0], 0)
                self.assertGreaterEqual(rx * direction[0] + ry * direction[1], 0)
                if target != ball:
                    self.assertAlmostEqual(
                        (point[0] - target[0]) * direction[0]
                        + (point[1] - target[1]) * direction[1], 0,
                    )
        self.assertIsNone(closest_point_on_ray(ball, (0, 0), (-6, 0)))

    def test_ball_in_penalty_area_only_blocks_beyond_ball(self):
        self.player.context = replace(
            self.player.context, ball=BallState(-5, 1),
            teammates={1: RobotState(1, Pose2D(-4.5, 0))},
        )
        self.set_opponent(Pose2D(-3, 1, math.pi))
        self.assertEqual(self.player._guard_intercept_target(), (-5.0, 1.0))

    def test_closest_qualifying_opponent_has_priority(self):
        self.player.context = replace(
            self.player.context,
            opponents={
                4: RobotState(4, Pose2D(0, 1, math.pi)),
                5: RobotState(5, Pose2D(-1, 0.5, math.pi)),
                6: RobotState(6, Pose2D(-1.9, 1, 0)),
            },
        )
        self.assertEqual(self.player._guard_intercept_target(), (-5.2, 2.6))

    def test_unknown_or_coincident_opponent_is_skipped(self):
        for pose in (None, Pose2D(-2, 1)):
            with self.subTest(pose=pose):
                self.set_opponent(pose)
                self.assertIsNone(self.player._guard_intercept_target())

    def test_guard_uses_intercept_with_ball_facing(self):
        with patch.object(self.player, "walk_to") as walk:
            self.player.guard()
        self.assertEqual(self.player.action, "guard:intercept")
        walk.assert_called_once_with(
            (-6.5, 1.0), face=math.atan2(1, 4.5),
            avoid_ball=True, avoid_robots=True, keep_facing=True,
        )

    def test_no_threat_or_no_ball_returns_home(self):
        original = self.player.context
        for change in ({"opponents": {}}, {"ball": None}):
            with self.subTest(change=change):
                self.player.context = replace(original, **change)
                with patch.object(self.player, "walk_to") as walk:
                    self.player.guard()
                self.assertEqual(self.player.action, "guard:home")
                self.assertEqual(walk.call_args.args[0], (-6.5, 0.0))

    def test_unknown_keeper_pose_stops(self):
        self.player.context = replace(self.player.context, teammates={})
        self.player.guard()
        self.assertEqual(self.player.action, "guard:stop")
        self.player._backend.set_velocity.assert_called_with(0, 0, 0)

    def test_long_intercept_moves_sideways_while_facing_ball(self):
        # 在不穿过门柱安全圈的位置验证纯侧移；贴柱路线应当绕行。
        self.player.context = replace(
            self.player.context, teammates={1: RobotState(1, Pose2D(-6, 0))},
        )
        self.player.walk_to((-6, 3), face=0, keep_facing=True)
        vx, vy, turn = self.player._backend.set_velocity.call_args.args
        self.assertAlmostEqual(vx, 0)
        self.assertGreater(vy, 0)
        self.assertAlmostEqual(turn, 0)
        self.assertLessEqual(math.hypot(vx, vy), MAX_LINEAR)

    def test_at_intercept_turns_to_ball_without_translation(self):
        self.player.walk_to((-6.5, 0), face=math.pi / 2, keep_facing=True)
        vx, vy, turn = self.player._backend.set_velocity.call_args.args
        self.assertEqual((vx, vy), (0, 0))
        self.assertGreater(turn, 0)

    def test_tangent_and_degenerate_rays_do_not_cross_rectangle(self):
        bounds = (-7, -4, -3, 3)
        for origin, direction in (
            ((-2, 3), (-1, 0)),  # 沿边界
            ((-3, 2), (-1, 1)),  # 只碰角点
            ((-5, 0), (0, 0)),
            ((-2, 0), (0, 1)),  # 平行且在外侧
        ):
            with self.subTest(origin=origin, direction=direction):
                self.assertIsNone(closest_point_on_ray_in_rect(
                    origin, direction, (-6, 0), bounds,
                ))


if __name__ == "__main__":
    unittest.main()
