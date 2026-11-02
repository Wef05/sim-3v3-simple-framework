"""用户确认的积极出球规则；独立夹具，不依赖缺失的历史回放模块。"""
import math
import unittest
from dataclasses import replace
from types import SimpleNamespace

from src.framework.types import (Context, GameControlState, GameState, BallState,
                                 RobotState, Pose2D, ADULT_FIELD_DIMENSIONS)
from src.intents import KickIntent, MoveIntent
from src.actions.kick_planner import plan_kick
from src.utils.goalkeeper import (decide_guard, own_goal_direction, adjustment_safe,
                                 prepare_outlet)


class Motion:
    def __init__(self, ball, velocity=(0., 0.), known=True):
        self.ball, self.velocity, self.known = ball, velocity, known

    def ball_prediction_details(self):
        return dict(velocity=self.velocity, velocity_estimated=self.known)

    def predict_ball_position(self, seconds):
        return tuple(self.ball[i]+self.velocity[i]*seconds for i in (0, 1))


def player(ball=(-6.3, 0.), pose=(-6.6, 0., 0.), opponents=(), teammates=(), velocity=(0., 0.), known=True):
    mates = {2: RobotState(2, Pose2D(*pose))}
    mates.update({i: RobotState(i, Pose2D(*r)) for i, r in teammates})
    ctx = Context(10., .05, 1, ADULT_FIELD_DIMENSIONS,
                  game=GameControlState(state=GameState.PLAYING, kicking_team=255),
                  ball=BallState(*ball, last_seen_at=10.), teammates=mates,
                  opponents={i: RobotState(i, Pose2D(*r)) for i, r in enumerate(opponents, 1)})
    return SimpleNamespace(id=2, context=ctx, pose=Pose2D(*pose),
                           motion=Motion(ball, velocity, known), threat={},
                           guard_receivers=frozenset(mates), _guard_memory=None)


def advance(p, seconds):
    p.context = replace(p.context, now=p.context.now+seconds,
                        ball=replace(p.context.ball, last_seen_at=p.context.now+seconds))


class AggressiveGuardTests(unittest.TestCase):
    def assert_safe_kick(self, p, intent):
        self.assertIsInstance(intent, KickIntent)
        plan = plan_kick(p.context, intent)
        self.assertFalse(own_goal_direction(p.context, (p.context.ball.x, p.context.ball.y), plan.direction))
        return plan

    def test_near_goal_facing_precedes_teammate_and_contest(self):
        p = player(opponents=((-6.2, 0., math.pi),), teammates=((3, (-3., 1., 0.)),))
        plan = self.assert_safe_kick(p, decide_guard(p, 3))
        self.assertAlmostEqual(plan.direction, 0.)
        self.assertEqual(p.guard_decision['outlet_mode'], 'near_facing')

    def test_fast_incoming_ball_within_foot_reach_is_kicked(self):
        p = player(velocity=(-3., 0.))
        self.assert_safe_kick(p, decide_guard(p))

    def test_fast_ball_outside_foot_reach_still_blocks(self):
        p = player(ball=(-4., 0.), velocity=(-4., 0.))
        self.assertNotIsInstance(decide_guard(p), KickIntent)
        self.assertTrue(p.action.startswith('guard:block:'))

    def test_fresh_unknown_velocity_allows_direct_contact(self):
        p = player(known=False)
        self.assert_safe_kick(p, decide_guard(p))

    def test_stale_ball_cancels_kick(self):
        p = player()
        decide_guard(p)
        p.context = replace(p.context, now=11.)
        self.assertNotIsInstance(decide_guard(p), KickIntent)
        self.assertEqual(p.action, 'guard:home:no_ball')
        self.assertIsNone(p._guard_memory.plan)

    def test_restricted_restart_never_kicks(self):
        p = player()
        p.context = replace(p.context, game=replace(p.context.game, kicking_team=2, secondary_time=5))
        self.assertNotIsInstance(decide_guard(p), KickIntent)

    def test_own_goal_ray_extends_past_short_target(self):
        p = player(ball=(-2., 0.), pose=(-1.7, 0., math.pi))
        self.assertTrue(own_goal_direction(p.context, (-2., 0.), math.pi))
        self.assertFalse(own_goal_direction(p.context, (-2., 0.), 0.))

    def test_own_goal_includes_command_correction(self):
        p = player()
        # 名义方向擦过下门柱，右脚补偿后仍可能进入门口。
        angle = math.atan2(-1.43, -.7)
        self.assertTrue(own_goal_direction(p.context, (-6.3, 0.), angle))

    def test_own_goal_facing_chooses_executable_side(self):
        theta = 2.1
        p = player(ball=(-6.3, .0), pose=(-6.3, -.3, theta))
        self.assertTrue(own_goal_direction(p.context, (-6.3, 0.), theta))
        plan = self.assert_safe_kick(p, decide_guard(p))
        self.assertGreater(plan.target[1], p.context.field.width/2)
        self.assertEqual(p.guard_decision['outlet_mode'], 'own_goal_escape')

    def test_no_executable_escape_does_not_kick_own_goal(self):
        p = player(ball=(-6.7, 0.), pose=(-6.4, 0., math.pi), opponents=((-6.8, .2, 0.),))
        self.assertNotIsInstance(decide_guard(p), KickIntent)

    def test_outside_area_prefers_ready_teammate(self):
        p = player(ball=(-5.7, 0.), pose=(-6., 0., 0.), teammates=((3, (-2., 0., 0.)),))
        intent = decide_guard(p, 3)
        self.assert_safe_kick(p, intent)
        self.assertEqual(intent.style, 'pass')

    def test_side_precedes_facing_without_teammate(self):
        p = player(ball=(-5.7, .3), pose=(-5.7, 0., math.pi/2))
        plan = self.assert_safe_kick(p, decide_guard(p))
        self.assertGreater(plan.target[1], 4.5)
        self.assertEqual(p.guard_decision['outlet_mode'], 'sideline')

    def test_pressure_rejects_adjustment_but_keeps_facing_fallback(self):
        p = player(ball=(-5.7, 0.), pose=(-6., 0., 0.),
                   opponents=((-5.5, .2, math.pi),), teammates=((3, (-3., 3., 0.)),))
        plan = self.assert_safe_kick(p, decide_guard(p))
        self.assertAlmostEqual(plan.direction, 0.)
        self.assertNotEqual(p.guard_decision['outlet_mode'], 'safe_adjustment')

    def test_kick_commit_survives_opponent_approach(self):
        p = player(ball=(-5.7, 0.), pose=(-6., 0., 0.), teammates=((3, (-2., 0., 0.)),))
        first = decide_guard(p)
        advance(p, .4)
        p.context.opponents[1] = RobotState(1, Pose2D(-5.6, 0., math.pi))
        second = decide_guard(p)
        self.assertEqual(first, second)
        self.assertEqual(p.guard_decision['outlet_mode'], 'committed')
        self.assertEqual(p._guard_memory.kick_since, 10.)

    def test_timeout_and_cooldown_not_reset_by_new_direction(self):
        p = player()
        decide_guard(p)
        advance(p, 1.21)
        self.assertNotIsInstance(decide_guard(p), KickIntent)
        advance(p, .3)
        self.assertNotIsInstance(decide_guard(p), KickIntent)
        advance(p, .21)
        self.assert_safe_kick(p, decide_guard(p))

    def test_time_rollback_discards_old_commit(self):
        p = player()
        decide_guard(p)
        advance(p, -5.)
        self.assert_safe_kick(p, decide_guard(p))
        self.assertEqual(p._guard_memory.kick_since, 5.)

    def test_unknown_opponent_rejects_adjustment(self):
        p = player(ball=(-5.7, 0.), pose=(-6.5, 0., 0.))
        p.context.opponents[1] = RobotState(1, None)
        self.assertFalse(adjustment_safe(p.context, 2, p.pose, p.motion, {}, (-6.05, 0.), 0., ((-6.05, 0.), .6)))

    def test_adjustment_cannot_abandon_covered_goal_ray(self):
        p = player(ball=(-5.7, 0.), pose=(-6.5, 0., 0.))
        self.assertFalse(adjustment_safe(p.context, 2, p.pose, p.motion, {}, (-6.5, 1.), 0., ((-6.5, 1.), 2.)))

    def test_safe_forward_adjustment_is_allowed(self):
        p = player(ball=(-5.7, 0.), pose=(-6.5, 0., 0.))
        intent = prepare_outlet(p.context, 2, p.pose, p.motion, {}, ((-2., 0.), 'pass', 3), False)
        self.assertIsInstance(intent, MoveIntent)
        self.assertTrue(intent.direct_path)
        self.assertFalse(intent.avoid_ball)

    def test_recorded_fourth_goal_contested_ball_now_requests_clearance(self):
        # 4:5 回放帧 31312（仿真435.874秒）：旧规则因 contested 取消请求。
        p = player(ball=(-5.902, .507), pose=(-6.227, .803, -.42),
                   opponents=((-5.396, .604, 0.),), teammates=((3, (-5.915, 1.413, 0.)),))
        plan = self.assert_safe_kick(p, decide_guard(p))
        self.assertEqual(p.guard_decision['outlet_mode'], 'sideline')
        self.assertLess(plan.target[1], -p.context.field.width/2)

    def test_recorded_fifth_goal_lane_change_no_longer_cancels_direct_clear(self):
        # 帧34374（542.234秒）：旧方向被robot_lane否决，球仍在脚前。
        p = player(ball=(-6.269, -.604), pose=(-6.455, -.401, -.52),
                   opponents=((-5.679, -.967, 0.),), teammates=((3, (-7.326, .121, 0.)),))
        plan = self.assert_safe_kick(p, decide_guard(p))
        self.assertAlmostEqual(plan.direction, -.52)

    def test_safe_turn_for_teammate_precedes_lower_priority_clearance(self):
        p = player(ball=(-5.7, .25), pose=(-6., 0., 0.),
                   teammates=((3, (-3., 2.7, 0.)),), opponents=((5., 3., 0.),))
        intent = decide_guard(p)
        self.assertIsInstance(intent, MoveIntent)
        self.assertEqual(p.guard_decision['outlet_mode'], 'safe_adjustment')
        self.assertEqual(p.guard_decision['receiver_id'], 3)
        self.assertEqual(intent.target, (-6., 0.))

    def test_receiver_unavailable_is_not_selected(self):
        p = player(ball=(-5.7, 0.), pose=(-6., 0., 0.), teammates=((3, (-2., 0., 0.)),),
                   opponents=((-5.4, .5, 0.),))
        p.guard_receivers = frozenset({2})
        intent = decide_guard(p)
        self.assert_safe_kick(p, intent)
        self.assertEqual(intent.style, 'clear')

    def test_ball_behind_goal_is_not_chased_for_clearance(self):
        p = player(ball=(-7.1, 0.), pose=(-6.8, 0., math.pi))
        self.assertNotIsInstance(decide_guard(p), KickIntent)


if __name__ == '__main__':
    unittest.main()
