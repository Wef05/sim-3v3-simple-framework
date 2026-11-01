"""射门三角形排除：连续几何、原有约束及逐帧角色接线。"""
import math
import random
import unittest
from dataclasses import replace
from types import SimpleNamespace
from src.framework.types import Context, Pose2D, BallState, RobotState, ADULT_FIELD_DIMENSIONS
from src.intents import Role, RoleTask, StopIntent
from src.player import decide
from src.utils.free_position import nearest_free_position, shot_triangle, triangle_distance


class FreePositionTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context(1, 1/30, 1, ADULT_FIELD_DIMENSIONS, ball=BallState(0, 0))
        self.triangle = shot_triangle(self.ctx, Pose2D(0, 0))

    def target(self, pose, ctx=None, triangle=None):
        return nearest_free_position(ctx or self.ctx, pose, 7, 3, 1,
                                     self.triangle if triangle is None else triangle)

    def test_inside_and_boundary_move_to_nearest_safe_side(self):
        for pose in (Pose2D(3, 0), Pose2D(3, 3*1.3/7)):
            target = self.target(pose)
            self.assertGreaterEqual(triangle_distance(target, self.triangle), .38-1e-9)
            # 对称三角形中心到上/下边垂足的距离，加安全余量。
            if pose.y == 0:
                self.assertAlmostEqual(math.dist(target, (pose.x, pose.y)),
                                       3*1.3/math.hypot(7, 1.3)+.38)

    def test_outside_stays_and_absent_attacker_keeps_old_behavior(self):
        self.assertEqual(self.target(Pose2D(3, 2)), (3, 2))
        self.assertEqual(self.target(Pose2D(3, 0), triangle=()), (3, 0))
        self.assertEqual(shot_triangle(self.ctx, None), ())
        self.assertEqual(shot_triangle(self.ctx, Pose2D(float('nan'), 0)), ())

    def test_no_feasible_point_and_missing_ball(self):
        self.assertIsNone(nearest_free_position(self.ctx, Pose2D(), 0, 0, 1, self.triangle))
        self.assertIsNone(self.target(Pose2D(), ctx=replace(self.ctx, ball=None)))

    def test_degenerate_triangle_and_reversed_orientation(self):
        for triangle in (shot_triangle(self.ctx, Pose2D(7, 0)), self.triangle[::-1]):
            target = self.target(Pose2D(3, 0), triangle=triangle)
            self.assertIsNotNone(target)
            self.assertGreaterEqual(triangle_distance(target, triangle), .38-1e-9)

    def test_circle_intersections_preserve_original_constraints(self):
        ctx = replace(self.ctx, opponents={2: RobotState(2, Pose2D(3, 2))})
        target = self.target(Pose2D(3, 0), ctx=ctx)
        self.assertGreaterEqual(math.dist(target, (3, 2)), 3-1e-9)
        self.assertLessEqual(math.hypot(*target), 7+1e-9)
        self.assertGreaterEqual(triangle_distance(target, self.triangle), .38-1e-9)

    def test_solver_no_worse_than_independent_dense_candidates(self):
        rng = random.Random(19)
        for _ in range(15):
            pose = Pose2D(rng.uniform(0, 6), rng.uniform(-4, 4))
            triangle = shot_triangle(self.ctx, Pose2D(rng.uniform(-2, 4), rng.uniform(-3, 3)))
            target = self.target(pose, triangle=triangle)
            grid = [(x/10, y/10) for x in range(61) for y in range(-45, 46)
                    if math.hypot(x/10, y/10) <= 7 and triangle_distance((x/10, y/10), triangle) >= .38]
            if grid:
                self.assertIsNotNone(target)
                self.assertLessEqual(math.dist(target, (pose.x, pose.y)),
                                     min(math.dist(q, (pose.x, pose.y)) for q in grid)+1e-8)

    def test_role_change_and_absence_clear_previous_triangle(self):
        class Stub:
            def __init__(self, pid): self.id = pid
            def decide(self, task): return StopIntent()
        players = [Stub(1), Stub(3)]
        ctx = replace(self.ctx, teammates={1: RobotState(1, Pose2D(1, 1)), 3: RobotState(3, Pose2D(2, 2))})
        for attacker in (1, 3, None):
            tasks = {p.id: RoleTask(Role.ATTACKER if p.id == attacker else Role.FREEMAN) for p in players}
            decide(ctx, players, tasks, SimpleNamespace())
            expected = shot_triangle(ctx, ctx.teammates[attacker].pose) if attacker else ()
            for p in players: self.assertEqual(p.freeman_shot_triangle, expected)
