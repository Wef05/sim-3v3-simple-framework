"""真实看板观测回归与威胁状态边界。"""
import unittest
from dataclasses import replace
from checks.goalkeeper_replay_cases import CASES
from checks.test_goalkeeper import Motion
from checks import test_goalkeeper as helpers
from src.framework.types import (Context, GameControlState, GameState, SetPlay, BallState, RobotState,
                                 Pose2D, ADULT_FIELD_DIMENSIONS)
from src.framework.config import SoccerConfig
from src.player import Player
from src.intents import KickIntent, MoveIntent
from src.utils.ball_motion import BallMotion
from src.utils.goalkeeper import shooter_ray, incoming_shot


def recorded_player(name, existing=None):
    return recorded_player_data(CASES[name], existing)


def recorded_player_data(data, existing=None):
    p = existing or Player(2, SoccerConfig(), None)
    game = dict(data['game'], state=GameState(data['game']['state']), set_play=SetPlay(data['game']['set_play']))
    robots = lambda side: {int(i):RobotState(int(i), Pose2D(**r['pose'])) for i,r in data[side].items()}
    p.context = Context(data['now'], 1/30, 1, ADULT_FIELD_DIMENSIONS,
                        game=GameControlState(**game), ball=BallState(**data['ball']),
                        teammates=robots('teammates'), opponents=robots('opponents'))
    pred = data['prediction']
    p.motion = BallMotion()
    p.motion.context = p.context
    p.motion._ball_sample = (p.context.ball.last_seen_at, p.context.ball.x, p.context.ball.y)
    p.motion._ball_velocity = tuple(pred['velocity'])
    p.motion._ball_velocity_estimated = pred['velocity_estimated']
    p.motion._current_speed = pred['current_speed_mps']
    if pred['sliding_remaining_s'] > 0:
        p.motion._slide_until = p.context.ball.last_seen_at+pred['sliding_remaining_s']
    p.threat = data['threat']
    p.guard_receivers = frozenset({1,2,3})
    return p


class RecordedGuardTests(unittest.TestCase):
    def test_moves_to_shooter_ray_before_first_shot(self):
        p = None
        for name in ('before_shot','before_contact'):
            p = recorded_player(name, p)
            intent = p.guard(3)
            self.assertEqual(p.action, 'guard:anticipate:ray')
            self.assertGreater(intent.target[1], .5)
            self.assertLess(CASES[name]['old_guard']['target'][1], 0)

    def test_deceleration_flip_does_not_immediately_drop_shot(self):
        p = recorded_player('shot_confirmed')
        p.guard()
        self.assertTrue(p.action.startswith('guard:block:'))
        recorded_player('shot_flicker', p)
        self.assertIsNone(incoming_shot(p.context, p.motion, p.motion.ball_prediction_details()))
        p.guard()
        self.assertTrue(p.action.startswith('guard:block:'))
        self.assertEqual(CASES['shot_flicker']['old_guard']['action'], 'guard:set')

    def test_close_ball_can_be_cleared_without_backing_away(self):
        p = recorded_player('close_ball')
        intent = p.guard()
        self.assertIsInstance(intent, KickIntent)
        self.assertEqual(p.action, 'guard:close_clear')
        self.assertEqual(intent.style, 'clear')
        self.assertGreater(intent.target.point[0], p.context.ball.x)
        self.assertEqual(CASES['close_ball']['old_guard']['action'], 'guard:set')

    def test_actual_contested_rebound_still_prioritizes_block(self):
        p = recorded_player('contested_rebound')
        self.assertNotIsInstance(p.guard(), KickIntent)
        self.assertTrue(p.action.startswith('guard:block:'))


class GuardBoundaryTests(unittest.TestCase):
    def player(self, **kwargs):
        return helpers.GoalkeeperTests().player(**kwargs)

    def test_remote_opponent_does_not_control_preposition(self):
        p=self.player(ball=(-3,0))
        p.context.opponents[1]=RobotState(1,Pose2D(0,0))
        self.assertIsNone(shooter_ray(p.context,p.pose,p.threat,p.motion.ball_prediction_details()))

    def test_wrong_ray_and_coincident_opponent_are_ignored(self):
        for x,y in ((-3,0),(-3,.2),(-4,0),(-2.8,1)):
            p=self.player(ball=(-3,0))
            p.context.opponents[1]=RobotState(1,Pose2D(x,y))
            self.assertIsNone(shooter_ray(p.context,p.pose,p.threat,p.motion.ball_prediction_details()))

    def test_outgoing_bounce_cancels_held_shot(self):
        p=self.player(ball=(-4,0),velocity=(-5,0))
        p.guard()
        p.context=replace(p.context,now=1.1,ball=replace(p.context.ball,last_seen_at=1.1))
        p.motion=Motion((-4,0),(1,0))
        p.guard()
        self.assertFalse(p.action.startswith('guard:block:'))

    def test_hold_expires_without_new_confirmation(self):
        p=recorded_player('shot_confirmed')
        p.guard()
        recorded_player('shot_flicker',p)
        p.context=replace(p.context,now=p.context.now+1,ball=replace(p.context.ball,last_seen_at=p.context.now+1))
        # 明确不足以滚到门线的慢球，不能无限延续上一次射门。
        p.motion=Motion((-3,0),(-.1,0))
        p.guard()
        self.assertFalse(p.action.startswith('guard:block:'))

    def test_close_clear_is_disabled_during_restart_and_for_fast_ball(self):
        p=recorded_player('close_ball')
        p.context=replace(p.context,game=GameControlState(state=GameState.PLAYING,kicking_team=2,secondary_time=10))
        self.assertNotIsInstance(p.guard(),KickIntent)
        p=recorded_player('close_ball')
        p.motion=Motion((p.context.ball.x,p.context.ball.y),(-6,0))
        self.assertNotIsInstance(p.guard(),KickIntent)

    def test_close_clear_timeout_still_applies(self):
        p=recorded_player('close_ball')
        self.assertIsInstance(p.guard(),KickIntent)
        p.context=replace(p.context,now=p.context.now+1.3,ball=replace(p.context.ball,last_seen_at=p.context.now+1.3))
        p.motion=Motion((p.context.ball.x,p.context.ball.y))
        self.assertNotIsInstance(p.guard(),KickIntent)


if __name__ == '__main__':
    unittest.main()
