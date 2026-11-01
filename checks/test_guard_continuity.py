"""不可达射门的连续封堵回归；固定观测，不代表真实比赛。"""
import unittest
from dataclasses import replace
from unittest.mock import patch
from src.framework.config import SoccerConfig
from src.framework.types import (Context, BallState, RobotState, Pose2D,
                                 ADULT_FIELD_DIMENSIONS, GameControlState, GameState)
from src.intents import MoveIntent, FaceIntent
from src.player import Player


class Motion:
    def __init__(self, ball: tuple[float, float], velocity: tuple[float, float]):
        self.ball, self.velocity = ball, velocity

    def predict_ball_position(self, t: float) -> tuple[float, float]:
        return tuple(self.ball[i] + self.velocity[i]*t for i in (0, 1))

    def ball_prediction_details(self) -> dict:
        return dict(velocity=self.velocity, velocity_estimated=True)


def player(ball: tuple[float, float] = (-5, .8)) -> Player:
    p = Player(2, SoccerConfig(), None)
    p.context = Context(1, 1/30, 1, ADULT_FIELD_DIMENSIONS,
                        game=GameControlState(state=GameState.PLAYING),
                        ball=BallState(*ball, last_seen_at=1),
                        teammates={2: RobotState(2, Pose2D(-6.5, 0, 0))})
    p.motion = Motion(ball, (-6, 0))
    p._mode = 'walk'
    return p


class GuardContinuityTests(unittest.TestCase):
    def test_unreachable_shot_keeps_moving_as_ball_approaches(self):
        p = player()
        # 同一来球逼近时，时间预算变短不能把移动终点缩回脚下。
        for elapsed, x in ((0, -5), (1/6, -6), (.25, -6.5)):
            with self.subTest(ball_x=x):
                p.context = replace(p.context, now=1+elapsed,
                                    ball=BallState(x, .8, last_seen_at=1+elapsed))
                p.motion = Motion((x, .8), (-6, 0))
                intent = p.guard()
                self.assertIsInstance(intent, MoveIntent)
                self.assertEqual(p.action, 'guard:block:best_effort')
                self.assertGreater(intent.target[1], .3)
                with patch.object(p.controller, 'set_velocity') as velocity:
                    p.controller.execute(intent)
                    self.assertTrue(any(call.args[1] > 0 for call in velocity.call_args_list))

    def test_blocked_path_still_holds(self):
        p = player((-6, .8))
        p.context.opponents[1] = RobotState(1, Pose2D(-6.5, .25))
        self.assertIsInstance(p.guard(), FaceIntent)
        self.assertEqual(p.action, 'guard:block:unreachable_hold')

    def test_already_covering_shot_still_holds(self):
        p = player((-5, 0))
        self.assertIsInstance(p.guard(), FaceIntent)
        self.assertEqual(p.action, 'guard:block:hold')


if __name__ == '__main__':
    unittest.main()
