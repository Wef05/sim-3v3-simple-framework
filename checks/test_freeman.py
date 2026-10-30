"""自由人连续选点及控制指令的离线验证。"""

import math
import random
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from src.framework.config import SoccerConfig
from src.framework.types import (
    ADULT_FIELD_DIMENSIONS, BallState, Context, Pose2D, RobotState,
)
from src.player import Player
from src.utils.free_position import nearest_free_position


class FreemanTests(unittest.TestCase):
    def context(self, position=(-2, 2), ball=(0, 1), enemies=()):
        return Context(
            now=1, dt=1 / 30, team_id=1, field=ADULT_FIELD_DIMENSIONS,
            ball=BallState(*ball),
            teammates={1: RobotState(1, Pose2D(*position))},
            opponents={i: RobotState(i, Pose2D(*p)) for i, p in enumerate(enemies)},
        )

    def target(self, ctx, radius=7, clearance=3):
        return nearest_free_position(ctx, ctx.teammates[1].pose, radius, clearance, 1)

    def test_nearest_quadrant_projection_on_both_sides(self):
        for side in (-1, 1):
            ctx = self.context(position=(-2, 2 * side), ball=(0, side))
            self.assertEqual(self.target(ctx), (0, side))

    def test_ball_radius_projection(self):
        ctx = self.context(position=(5, -4), ball=(-6, 1))
        target = self.target(ctx)
        self.assertAlmostEqual(math.dist(target, (-6, 1)), 7)
        self.assertAlmostEqual(target[0], -6 + 7 * 11 / math.sqrt(146))

    def test_goal_line_and_field_edges(self):
        ctx = self.context(position=(8, -6), ball=(5, 1))
        self.assertEqual(self.target(ctx), (6, -4.5))
        self.assertIsNone(self.target(self.context(ball=(6.1, 0))))

    def test_enemy_circle_nearest_point(self):
        ctx = self.context(position=(2, -1), enemies=((3, -1),))
        self.assertEqual(self.target(ctx), (0, -1))

    def test_intersection_of_enemy_boundaries(self):
        ctx = self.context(position=(3, -1), ball=(0, 0),
                           enemies=((2, -1), (4, -1)))
        x, y = self.target(ctx, clearance=2)
        self.assertAlmostEqual(x, 3)
        self.assertAlmostEqual(y, -1 - math.sqrt(3))

    def test_empty_region_and_concentric_circles(self):
        ctx = self.context(enemies=((0, 1),))
        self.assertIsNone(self.target(ctx, radius=1, clearance=3))
        target = self.target(ctx, radius=3, clearance=3)
        self.assertAlmostEqual(math.dist(target, (0, 1)), 3)

    def test_goal_on_axis_selects_player_side(self):
        for side in (-1, 1):
            ctx = self.context(position=(2, side), ball=(0, 0))
            self.assertEqual(self.target(ctx), (2, side))

    def test_wait_faces_actual_goal_center_without_kicking(self):
        ctx = self.context(position=(2, -1))
        player = Player(1, SoccerConfig(), Mock())
        player.context = ctx
        with patch.object(player, 'walk_to') as walk, patch.object(player, 'face_to') as face:
            player.freeman()
        walk.assert_not_called()
        face.assert_called_once_with(math.atan2(1, 5))
        self.assertEqual(player.action, 'freeman:wait')
        self.assertFalse(player.is_kicking)

    def test_move_and_stop_on_unavailable_target_or_observations(self):
        player = Player(1, SoccerConfig(), Mock())
        player.context = self.context()
        with patch.object(player, 'walk_to') as walk:
            player.freeman(ball_radius=2, opponent_radius=1)
        self.assertEqual(walk.call_args.args[0], (0, 1))
        self.assertTrue(walk.call_args.kwargs['avoid_robots'])
        for ctx in (self.context(ball=(6.1, 0)), None,
                    replace(self.context(), ball=None),
                    replace(self.context(), teammates={})):
            player.context = ctx
            player.freeman()
            player._backend.set_velocity.assert_called_with(0, 0, 0)

    def test_matches_independent_grid_upper_bound(self):
        # 网格只作为独立上界：连续最优点应不比任一可行网格点更远。
        rng = random.Random(31)
        for _ in range(25):
            ball = (rng.uniform(-6, 5), rng.choice((-1, 1)) * rng.uniform(.1, 4))
            ctx = self.context(
                position=(rng.uniform(-7, 7), rng.uniform(-4.5, 4.5)), ball=ball,
                enemies=[(rng.uniform(-7, 7), rng.uniform(-4.5, 4.5)) for _ in range(3)],
            )
            target = self.target(ctx)
            pose = ctx.teammates[1].pose
            feasible_grid = []
            for ix in range(53):
                for iy in range(37):
                    x, y = -7 + ix * .25, -4.5 + iy * .25
                    if (x >= ball[0] and (y - ball[1]) * -ball[1] >= 0
                            and math.dist((x, y), ball) <= 7
                            and all(math.hypot(x-r.pose.x, y-r.pose.y) >= 3
                                    for r in ctx.opponents.values())):
                        feasible_grid.append((x, y))
            if feasible_grid:
                self.assertIsNotNone(target)
                self.assertLessEqual(math.dist(target, (pose.x, pose.y)),
                                     min(math.dist(p, (pose.x, pose.y)) for p in feasible_grid) + 1e-8)
            if target is not None:
                x, y = target
                self.assertTrue(-7-1e-8 <= x <= 6+1e-8 and abs(y) <= 4.5+1e-8)
                self.assertGreaterEqual(x, ball[0]-1e-8)
                self.assertGreaterEqual((y-ball[1]) * -ball[1], -1e-8)
                self.assertLessEqual(math.dist(target, ball), 7+1e-8)
                for r in ctx.opponents.values():
                    self.assertGreaterEqual(math.hypot(x-r.pose.x, y-r.pose.y), 3-1e-8)


if __name__ == '__main__':
    unittest.main()
