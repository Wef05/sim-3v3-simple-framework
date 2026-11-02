"""角区结构阻挡回归；检查真实行为意图和球路，不需要 SDK。"""
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.framework.config import SoccerConfig
from src.framework.types import Context, BallState, RobotState, Pose2D, ADULT_FIELD_DIMENSIONS
from src.intents import KickIntent, MoveIntent, StopIntent
from src.player import Player
from src.actions.kick_planner import plan_kick
from src.utils.blocked_attack import blocked_attack_target
from src.utils.kick_lane import check_kick_lane
from src.utils.geom import behind_ball
from src.param import CHASE_BEHIND_M


class BlockedAttackTests(unittest.TestCase):
    def player(self, ball=(6.95, 4.45), pid=3, pose=None):
        p = Player(pid, SoccerConfig(), None)
        p.context = Context(0, .1, 1, ADULT_FIELD_DIMENSIONS,
                            ball=BallState(*ball), teammates={
                                pid: RobotState(pid, Pose2D(*(pose or (ball[0]-.3, ball[1]-.3)), 0))})
        p.motion.update(p.context)
        p.ball_intercept = dict(status='intercept', target=ball, intercept_time_s=0)
        # 候选评分是只读遥测，与本次行为无关。
        for name in ('evaluate_attack_targets', 'evaluate_scoring', 'estimate_interception_risk'):
            mock = patch('src.player.' + name, return_value={'evaluated_at': 0})
            mock.start()
            self.addCleanup(mock.stop)
        return p

    def test_replay_positions_and_mirrors_keep_producing_safe_short_kicks(self):
        for ball, pid in (((6.95, 4.45), 3), ((6.46, 4.37), 1),
                          ((6.95, -4.45), 3), ((6.46, -4.37), 1)):
            with self.subTest(ball=ball):
                p = self.player(ball, pid)
                self.assertTrue(check_kick_lane(p.context.field, ball, (7, 0))['blocked'])
                targets = set()
                for tick in range(61):
                    p.context = replace(p.context, now=tick)
                    intent = p.attack()
                    self.assertIsInstance(intent, KickIntent)
                    self.assertEqual(intent.style, 'pass')
                    plan = plan_kick(p.context, intent)
                    self.assertFalse(check_kick_lane(p.context.field, ball, plan.target)['blocked'])
                    self.assertLess(plan.target[0], ball[0])
                    self.assertLess(abs(plan.target[1]), abs(ball[1]))
                    targets.add(plan.target)
                self.assertEqual(len(targets), 1)

    def test_open_teammate_is_preferred(self):
        p = self.player()
        p.context.teammates[1] = RobotState(1, Pose2D(4.5, 2, 0))
        intent = p.attack()
        self.assertEqual(intent.target.point, (4.5, 2))
        self.assertEqual(p.action, 'attack:fallback:pass:1')

    def test_marked_receiver_is_rejected(self):
        p = self.player()
        p.context.teammates[1] = RobotState(1, Pose2D(3, 3.8, 0))
        p.context.opponents[1] = RobotState(1, Pose2D(3, 3.8, 0))
        self.assertIsInstance(p.attack(), KickIntent)
        self.assertEqual(p.action, 'attack:fallback:infield')

    def test_unavailable_receiver_is_ignored(self):
        p = self.player()
        p.context.teammates[1] = RobotState(1)
        self.assertIsInstance(p.attack(), KickIntent)
        self.assertEqual(p.action, 'attack:fallback:infield')

    def test_chase_uses_fallback_direction(self):
        p = self.player(pose=(2, 2))
        intent = p.attack()
        self.assertIsInstance(intent, MoveIntent)
        expected = behind_ball(6.95, 4.45, p.tactical_target, CHASE_BEHIND_M)
        # 保留替代出球方向对应的真实球后点，角区允许场外站位。
        self.assertEqual(intent.target, expected)

    def test_fully_blocked_lanes_still_stop_safely(self):
        p = self.player()
        p.context.opponents[1] = RobotState(1, Pose2D(6.95, 4.45, 0))
        self.assertIsInstance(p.attack(), StopIntent)
        self.assertIn('no_safe_alternative', p.action)

    def test_clear_shot_is_unchanged(self):
        p = self.player(ball=(3, 0))
        intent = p.attack()
        self.assertEqual(intent.target.kind, 'goal')
        self.assertEqual(intent.style, 'shot')

    def test_both_ball_origins_must_be_safe(self):
        p = self.player()
        # 第二个起点位于门柱上，不能只用另一个起点放行。
        self.assertIsNone(blocked_attack_target(p.context, p.id, ((6.95, 4.45), (7, 1.3))))


if __name__ == '__main__':
    unittest.main()
