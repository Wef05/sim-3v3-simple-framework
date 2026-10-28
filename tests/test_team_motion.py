import math
import unittest
from unittest.mock import Mock

from src.framework.config import SoccerConfig
from src.framework.types import Context, ADULT_FIELD_DIMENSIONS, RobotState, Pose2D, BallState
from src.player import Player
from src.utils.team_motion import compatible, coordinated_motion, priority_order


class TeamMotionTests(unittest.TestCase):
    def setup_team(self, positions, ball=(5, 0)):
        ctx = Context(now=1, dt=1/30, team_id=1, field=ADULT_FIELD_DIMENSIONS,
                      ball=BallState(*ball), teammates={
                          pid: RobotState(pid, Pose2D(*xy)) for pid, xy in positions.items()})
        players = [Player(pid, SoccerConfig(), Mock()) for pid in positions]
        for p in players:
            p.context = ctx
        return ctx, players

    def velocity(self, p):
        return p._backend.set_velocity.call_args.args[:2]

    def test_crossing_same_time_collides(self):
        self.assertFalse(compatible((-1, 0), (2, 0), (0, -1), (0, 2)))

    def test_same_spatial_crossing_different_times_is_allowed(self):
        self.assertTrue(compatible((-1, 0), (2, 0), (0, -3), (0, 2), horizon=2))

    def test_body_volume_not_just_centerlines(self):
        self.assertFalse(compatible((0, 0), (1, 0), (0, .9), (1, 0)))

    def test_head_on_swept_collision(self):
        self.assertFalse(compatible((-2, 0), (4, 0), (2, 0), (-4, 0)))

    def test_overlap_must_separate(self):
        self.assertTrue(compatible((0, 0), (-.5, 0), (.8, 0), (.5, 0)))
        self.assertFalse(compatible((0, 0), (.5, 0), (.8, 0), (-.5, 0)))

    def test_priority_is_keeper_then_ball_distance(self):
        ctx, ps = self.setup_team({3: (0, 3), 2: (0, 0), 1: (-6, 0)}, ball=(0, 3))
        self.assertEqual([p.id for p in priority_order(ctx, ps)], [1, 3, 2])

    def test_joint_search_preserves_keeper_and_yields_other(self):
        ctx, ps = self.setup_team({2: (0, -2), 1: (-2, 0)})
        with coordinated_motion(ctx, ps):
            ps[0].set_velocity(0, 2, 0)
            ps[1].set_velocity(2, 0, 0)
        self.assertEqual(self.velocity(ps[1]), (2, 0))
        self.assertNotEqual(self.velocity(ps[0]), (0, 2))
        self.assertTrue(compatible((0, -2), self.velocity(ps[0]), (-2, 0), self.velocity(ps[1])))

    def test_safe_crossing_retains_both_commands(self):
        ctx, ps = self.setup_team({1: (-1, 0), 2: (0, -3)})
        with coordinated_motion(ctx, ps):
            ps[0].set_velocity(2, 0, 0)
            ps[1].set_velocity(0, 2, 0)
        self.assertEqual(self.velocity(ps[0]), (2, 0))
        self.assertEqual(self.velocity(ps[1]), (0, 2))

    def test_stationary_player_is_not_assumed_to_yield(self):
        ctx, ps = self.setup_team({1: (-2, 0), 2: (0, 0)})
        with coordinated_motion(ctx, ps):
            ps[0].set_velocity(2, 0, 0)
            ps[1].stop()
        self.assertEqual(self.velocity(ps[1]), (0, 0))
        self.assertTrue(compatible((-2, 0), self.velocity(ps[0]), (0, 0), (0, 0)))

    def test_exception_restores_backends_and_stops(self):
        ctx, ps = self.setup_team({1: (-2, 0)})
        backend = ps[0]._backend
        with self.assertRaises(RuntimeError):
            with coordinated_motion(ctx, ps):
                ps[0].set_velocity(2, 0, 0)
                raise RuntimeError('test')
        self.assertIs(ps[0]._backend, backend)
        backend.set_velocity.assert_called_with(0, 0, 0)

    def test_unsafe_sdk_approach_is_cancelled(self):
        ctx, ps = self.setup_team({1: (-2, 0), 2: (0, 0)}, ball=(0, 0))
        with coordinated_motion(ctx, ps):
            ps[0].kick(0, 1)
            ps[1].stop()
        ps[0]._backend.kick.assert_not_called()
        self.assertEqual(self.velocity(ps[0]), (0, 0))

    def test_rotated_body_command_uses_world_coordinates(self):
        ctx, ps = self.setup_team({1: (-2, 0, math.pi/2), 2: (0, -2)})
        with coordinated_motion(ctx, ps):
            ps[0].set_velocity(0, -2, 0)
            ps[1].set_velocity(0, 2, 0)
        vx, vy = self.velocity(ps[0])
        self.assertAlmostEqual(vx, 0)
        self.assertAlmostEqual(vy, -2)
        self.assertNotEqual(self.velocity(ps[1]), (0, 2))
