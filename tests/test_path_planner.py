import math
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from src.framework.config import SoccerConfig
from src.framework.types import ADULT_FIELD_DIMENSIONS, BallState, Context, Pose2D, RobotState
from src.param import GLOBAL_OBSTACLE_MARGIN_M, GOAL_ESCAPE_SPEED_M_S
from src.player import Player
from src.utils.obstacles import Obstacle, goal_obstacles
from src.utils.path_planner import navigation_target, plan_global_path, segment_is_clear


class PathPlannerTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context(now=0, dt=1/30, team_id=1, field=ADULT_FIELD_DIMENSIONS)
        self.backend = Mock()
        self.player = Player(2, SoccerConfig(), self.backend)

    def place(self, pose, ball=None):
        self.player.context = replace(self.ctx, ball=ball,
                                      teammates={2: RobotState(2, pose)})

    def assert_clear_path(self, path, obstacles, escape=False):
        self.assertIsNotNone(path)
        for a, b in zip(path, path[1:]):
            self.assertTrue(segment_is_clear(a, b, obstacles, allow_escape=escape), (a, b))

    def test_diagonal_grid_edge_cannot_clip_a_post(self):
        start, target = (.25, -.05), (.95, .65)
        obs = [Obstacle(.425-.15/math.sqrt(2), .125+.15/math.sqrt(2), .18)]
        self.assertFalse(segment_is_clear(start, target, obs))
        path = plan_global_path(self.ctx, start, target, obs)
        self.assert_clear_path(path, obs)
        self.assertGreater(len(path), 2)

    def test_follower_does_not_cut_multiple_short_corners(self):
        start = (1.451700467, .682514018)
        target = (-.814671169, -1.720104755)
        obs = [Obstacle(.656950740, -.509399892, .55)]
        path = plan_global_path(self.ctx, start, target, obs)
        self.assert_clear_path(path, obs)
        point = self.player._path_waypoint(Pose2D(*start), path)
        self.assertTrue(segment_is_clear(start, point, obs))
        self.assertLessEqual(math.dist(start, point), .9 + 1e-9)

    def test_boundary_target_does_not_round_outside_grid(self):
        path = plan_global_path(self.ctx, (0, 0), (6.75, 0), [])
        self.assertIsNotNone(path)
        self.assertEqual(path[-1], (6.75, 0))

    def test_occupied_target_is_moved_to_free_space(self):
        obs = [Obstacle(.65, -.05, .55)]
        path = plan_global_path(self.ctx, (-2, -.05), (.25, -.05), obs)
        self.assert_clear_path(path, obs)
        self.assertGreater(math.dist(path[-1], (.65, -.05)), .55+GLOBAL_OBSTACLE_MARGIN_M)

    def test_clipped_target_arrival_stops(self):
        self.place(Pose2D(-6.75, 0))
        self.assertTrue(self.player.walk_to((-7, 0)))
        self.backend.set_velocity.assert_called_with(0, 0, 0)

    def test_overlap_allows_only_outward_motion(self):
        obs = [Obstacle(0, 0, .5)]
        self.assertTrue(segment_is_clear((.2, 0), (1, 0), obs, allow_escape=True))
        self.assertFalse(segment_is_clear((.2, 0), (-1, 0), obs, allow_escape=True))
        self.assertFalse(segment_is_clear((.2, 0), (1, 0), obs))

    def test_all_four_front_posts_have_safe_routes(self):
        obs = goal_obstacles(self.ctx)
        for sx in (-1, 1):
            for sy in (-1, 1):
                with self.subTest(sx=sx, sy=sy):
                    start = (sx*6.5, sy*1.3)
                    path = plan_global_path(self.ctx, start, (sx*5, sy*2.5), obs)
                    self.assert_clear_path(path, obs, escape=True)
                    self.assertEqual(path[0], start)

    def test_default_chase_walk_avoids_goal_structure(self):
        self.place(Pose2D(6.5, 0))
        self.player.walk_to((6.5, 3), keep_facing=True)
        vx, vy, _ = self.backend.set_velocity.call_args.args
        self.assertLess(vx, 0)  # 先向场内让开门柱，再向上移动。
        self.assertGreater(vy, 0)
        self.assertTrue(segment_is_clear((6.5, 0), (6.5+vx/30, vy/30), goal_obstacles(self.ctx)))

    def test_surrounded_fallback_stops(self):
        self.place(Pose2D())
        obs = [Obstacle(.4*math.cos(i*math.pi/6), .4*math.sin(i*math.pi/6), .55)
               for i in range(12)]
        with patch('src.player.collect_obstacles', return_value=obs):
            self.assertFalse(self.player.walk_to((3, 0), keep_facing=True))
        self.backend.set_velocity.assert_called_with(0, 0, 0)

    def test_kick_cancels_sdk_approach_and_escapes_post(self):
        self.place(Pose2D(6.7, 1.3), BallState(6.6, 1.1))
        self.player._kicking = True
        self.player.kick(0, 5)
        self.backend.kick.assert_not_called()
        self.backend.release_kick.assert_called()
        self.assertFalse(self.player.is_kicking)
        vx, vy, _ = self.backend.set_velocity.call_args.args
        self.assertLess(vx, 0)
        self.assertLessEqual(math.hypot(vx, vy), GOAL_ESCAPE_SPEED_M_S + 1e-9)
        self.assertIsNotNone(self.player._goal_escape_target)
        # 脱离安全圈后仍完成退让，避免立即恢复踢球再次顶柱。
        self.place(Pose2D(6.2, 1.3), BallState(6.6, 1.1))
        self.player.kick(0, 5)
        self.backend.kick.assert_not_called()
        self.place(Pose2D(5.7, 1.3), BallState(6.6, 1.1))
        self.player.kick(0, 5)
        self.backend.kick.assert_called_once()
        self.assertIsNone(self.player._goal_escape_target)

    def test_escape_does_not_claim_a_pass_was_sent(self):
        self.place(Pose2D(-6.7, -1.3), BallState(-6.6, -1.1))
        self.assertFalse(self.player.pass_to((0, 0), 1))
        self.backend.kick.assert_not_called()

    def test_kinematic_walk_routes_around_posts_and_leaves_contact(self):
        # 理想底盘逐帧积分；用于查切角、方向/速度和脱离死循环，不代替物理仿真。
        for sx in (-1, 1):
            for sy in (-1, 1):
                for contact in (False, True):
                    with self.subTest(sx=sx, sy=sy, contact=contact):
                        pose = Pose2D(sx*6.7, sy*1.3) if contact else Pose2D(sx*6.5, 0)
                        target = (sx*5, sy*2.8) if contact else (sx*6.5, sy*3)
                        obs = goal_obstacles(self.ctx)
                        arrived = False
                        for _ in range(600):
                            self.place(pose)
                            arrived = self.player.walk_to(target, face=0, keep_facing=True)
                            vx, vy, _ = self.backend.set_velocity.call_args.args
                            next_pose = Pose2D(pose.x+vx/30, pose.y+vy/30)
                            if not arrived:
                                self.assertTrue(segment_is_clear((pose.x, pose.y),
                                    (next_pose.x, next_pose.y), obs, allow_escape=contact))
                            pose = next_pose
                            if arrived:
                                break
                        self.assertTrue(arrived, pose)

    def test_escape_beside_net_outside_touchline_margin(self):
        obs = goal_obstacles(self.ctx)
        for sx in (-1, 1):
            with self.subTest(sx=sx):
                pose = Pose2D(sx*7.3, sx*1.3)
                arrived = False
                for _ in range(600):
                    self.place(pose)
                    arrived = self.player.walk_to((sx*6.3, sx*1.3), keep_facing=True)
                    vx, vy, _ = self.backend.set_velocity.call_args.args
                    next_pose = Pose2D(pose.x+vx/30, pose.y+vy/30)
                    if not arrived:
                        self.assertTrue(segment_is_clear((pose.x, pose.y),
                            (next_pose.x, next_pose.y), obs, allow_escape=True))
                    pose = next_pose
                    if arrived:
                        break
                self.assertTrue(arrived, pose)

    def test_old_escape_target_is_discarded_after_moving_away(self):
        self.place(Pose2D(6.7, 1.3), BallState(6.6, 1.1))
        self.player.kick(0, 5)
        self.place(Pose2D(0, 0), BallState(.3, 0))
        self.player.kick(0, 5)
        self.backend.kick.assert_called_once()
        self.assertIsNone(self.player._goal_escape_target)


if __name__ == '__main__':
    unittest.main()
