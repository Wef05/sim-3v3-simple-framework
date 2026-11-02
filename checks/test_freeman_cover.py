"""协防触发、状态生命周期、真实角色意图及协作回归。"""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from src.framework.config import SoccerConfig
from src.framework.types import (Context, ADULT_FIELD_DIMENSIONS, Pose2D, RobotState,
                                 BallState, GameControlState, GameState, SetPlay)
from src.intents import MoveIntent, Role, RoleTask
from src.player import Player, coordinate
from src.utils.freeman_cover import CoverMemory, cover_trigger, freeman_cover
from src.utils.threat import calculate_threat
from src.utils.kick_lane import point_segment


class Motion:
    def __init__(self, ball=(-2, 1), velocity=(0, 0), known=True):
        self.ball, self.velocity, self.known = ball, velocity, known
    def predict_ball_position(self, t):
        return tuple(b+v*t for b, v in zip(self.ball, self.velocity)) if self.ball else None
    def ball_prediction_details(self):
        return dict(velocity=self.velocity, velocity_estimated=self.known)
    def predict_ball_stop(self):
        return None


def context(now=1):
    return Context(now, .05, 1, ADULT_FIELD_DIMENSIONS, ball=BallState(-2, 1),
                   game=GameControlState(state=GameState.PLAYING),
                   teammates={1: RobotState(1, Pose2D(-3, 2)),
                              2: RobotState(2, Pose2D(-6, 0))},
                   opponents={1: RobotState(1, Pose2D(0, 0))})


class CoverTests(unittest.TestCase):
    def test_race_states(self):
        for race, expected in ((True, False), (False, True), (None, True)):
            self.assertEqual(cover_trigger(context(), Motion(), dict(can_beat=race))[0], expected)

    def test_fallback_position_direction_and_unknown(self):
        for ball, velocity, known, expected in (
            ((-5, 0), (2, 0), True, True),
            ((-2, 1), (0, 0), False, True),
            ((-2, 1), (2, 0), True, False),
            ((-2, 1), (0, 1), True, True),
            ((1, 0), (-1, 0), True, True),
            ((4, 0), (-1, 0), True, False),
            ((4, 0), (0, 0), False, False),
            (None, (0, 0), False, True)):
            with self.subTest(ball=ball, velocity=velocity):
                self.assertEqual(cover_trigger(context(), Motion(ball, velocity, known), {})[0], expected)

    def test_hold_pause_rewind(self):
        memory = CoverMemory()
        def run(t, race):
            return freeman_cover(context(t), 1, Pose2D(-3, 2), Motion(), dict(can_beat=race), memory)
        self.assertIsNotNone(run(1, False)[0])
        self.assertIsNotNone(run(2, True)[0])
        self.assertIsNotNone(run(2, True)[0])
        self.assertIsNotNone(run(2.59, True)[0])
        self.assertIsNone(run(2.61, True)[0])
        run(3, False)
        self.assertIsNone(run(.5, True)[0])

    def test_block_requires_path_timing_and_precedes_enemy(self):
        block = dict(status='reachable', target=(-3, 2.1), ball_time_s=2)
        threat = dict(can_beat=False, status='ok', threat_intercept=dict(
            status='intercept', intercept_time_s=3, ball_position=(-2, 1)))
        with patch('src.utils.freeman_cover.calculate_block_point', return_value=block):
            intent, info = freeman_cover(context(), 1, Pose2D(-3, 2), Motion(velocity=(-1, 0)), threat, CoverMemory())
            self.assertTrue(intent.direct_path)
            self.assertFalse(intent.avoid_ball)
            self.assertEqual(info['strategy'], 'ball_path')
            threat['threat_intercept']['intercept_time_s'] = 1
            intent, info = freeman_cover(context(), 1, Pose2D(-3, 2), Motion(velocity=(-1, 0)), threat, CoverMemory())
            self.assertFalse(intent.direct_path)
            self.assertEqual(info['strategy'], 'goal_side')
            threat['threat_intercept']['intercept_time_s'] = 3
            blocked = context()
            blocked.opponents[1] = RobotState(1, Pose2D(-3, 2.1))
            self.assertFalse(freeman_cover(blocked, 1, Pose2D(-3, 2), Motion(velocity=(-1, 0)), threat, CoverMemory())[0].direct_path)

    def test_real_player_phase_and_role_reset(self):
        p = Player(1, SoccerConfig(), None)
        p.context, p.motion, p.threat = context(), Motion(), dict(can_beat=False)
        p._mode = 'walk'
        intent = p.decide(RoleTask(Role.FREEMAN))
        self.assertIsInstance(intent, MoveIntent)
        self.assertTrue(p._cover_memory.active)
        self.assertEqual(p.freeman_cover['mode'], 'cover')
        p.decide(RoleTask(Role.STAY))
        self.assertFalse(p._cover_memory.active)
        self.assertIsNone(p.freeman_cover)
        p.context = replace(context(), game=GameControlState(state=GameState.PLAYING, set_play=SetPlay.DIRECT_FREE_KICK, kicking_team=1))
        p.decide(RoleTask(Role.FREEMAN))
        self.assertIsNone(p.freeman_cover)

    def test_guard_lane_yield(self):
        ctx = context()
        tasks = {1: RoleTask(Role.FREEMAN), 2: RoleTask(Role.GUARD)}
        intents = {1: MoveIntent((-5, 0), motion_priority=50, direct_path=True),
                   2: MoveIntent((-4, 0))}
        final = coordinate(ctx, tasks, intents)
        self.assertGreaterEqual(point_segment(final[1].target, (-6, 0), (-4, 0)), .9)
        self.assertFalse(final[1].direct_path)
        self.assertTrue(final[1].avoid_ball)
        self.assertEqual(final[2].target, intents[2].target)

    def test_race_excludes_unavailable_but_preserves_missing_pose_and_velocity(self):
        def player(pid, pose, fallen=False):
            return SimpleNamespace(id=pid, pose=pose, is_fallen=fallen, is_penalized=False, mode='walk')
        players = [player(1, Pose2D(-3, 0)), player(2, Pose2D(-6, 0), True)]
        def estimate(pose, motion, **kwargs):
            if pose is None or kwargs.get('fallen'):
                return dict(status='unavailable', score_s=None)
            return dict(status='intercept', score_s=1 if pose.x == 0 else 2,
                        intercept_time_s=1 if pose.x == 0 else 2)
        with patch('src.utils.threat.estimate_ball_intercept', side_effect=estimate):
            self.assertIs(calculate_threat(context(), players, Motion())['can_beat'], False)
            players[1] = player(2, None)
            self.assertIsNone(calculate_threat(context(), players, Motion())['can_beat'])
            self.assertIsNone(calculate_threat(context(), players[:1], Motion(known=False))['can_beat'])
            self.assertIsNone(calculate_threat(context(), [], Motion())['can_beat'])


if __name__ == '__main__':
    unittest.main()
