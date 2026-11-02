"""Pitch markings are traversable; physical obstacles remain blocking."""
import math
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from checks.test_navigation import context, controller, frame
from src.framework.config import SoccerConfig
from src.framework.decision_frame import prepare_frame
from src.framework.types import BallState, GameState, Penalty, PlayerState, SetPlay, TeamState
from src.intents import MoveIntent, RecoverIntent, Role, RoleTask
from src.player import Player
from src.role_allocator import assign, init_store
from src.utils.obstacles import Obstacle, goal_obstacles
from src.utils.path_planner import _search_axes, plan_global_path, segment_clear
from src.param import GLOBAL_MAX_GRID_CELLS


class FieldBoundaryTests(unittest.TestCase):
    def test_cross_lines_in_both_directions_with_exact_targets(self):
        for a, b in (((0., 4.), (0., 5.2)), ((0., -4.), (0., -5.2)),
                     ((6.5, 3.), (7.5, 4.8)), ((-6.5, -3.), (-7.5, -4.8)),
                     ((6.5, 0.), (7.1, 0.)), ((-6.5, 0.), (-7.1, 0.))):
            for start, target in ((a, b), (b, a)):
                for direct in (False, True):
                    with self.subTest(start=start, target=target, direct=direct):
                        ctx = context((start,))
                        obstacles = goal_obstacles(ctx)
                        path = plan_global_path(ctx, start, target, obstacles)
                        self.assertIsNotNone(path)
                        self.assertEqual(path[-1], target)
                        c = controller(ctx)
                        intent = MoveIntent(target, maintain_face=True, direct_path=direct)
                        command, arrived = c.plan_walk(intent, frame(ctx))
                        self.assertFalse(arrived)
                        self.assertGreater(math.hypot(*command[:2]), 0.)
                        self.assertEqual(c.navigation_status['effective_target'], list(target))
                        self.assertIsNone(frame(ctx).check(1, intent, command))

    def test_deep_goal_target_stays_blocked_but_can_escape_toward_pitch(self):
        # ±7.3 m 与后网膨胀范围重叠（规划净距约 -0.17 m），不能作为可达终点。
        # 保留此场景的阻挡断言，并验证已有重叠允许朝场内脱离。
        for sign in (-1., 1.):
            outside, inside = (sign*7.3, 0.), (sign*6.5, 0.)
            obs = goal_obstacles(context())
            self.assertIsNone(plan_global_path(context(), inside, outside, obs))
            path = plan_global_path(context(), outside, inside, obs)
            self.assertEqual(path, [outside, inside])

    def test_search_grid_is_bounded_for_distant_endpoints(self):
        xs, ys = _search_axes(context(), (-10000., 2000.), (10000., -3000.))
        self.assertLessEqual(len(xs), GLOBAL_MAX_GRID_CELLS+1)
        self.assertLessEqual(len(ys), GLOBAL_MAX_GRID_CELLS+1)
        self.assertLess(xs[0], -10000.)
        self.assertGreater(xs[-1], 10000.)
        self.assertLess(ys[0], -3000.)
        self.assertGreater(ys[-1], 2000.)

    def test_outside_face_request_is_not_suppressed(self):
        ctx = context(((0., 5.2),))
        intent = MoveIntent((1., 5.2), face=math.pi/2, maintain_face=True)
        cmd, _ = controller(ctx).plan_walk(intent, frame(ctx))
        self.assertGreater(cmd[2], 0.)

    def test_astar_can_detour_outside_and_return(self):
        for start, target, center in (((-1., 5.2), (1., 5.2), (0., 5.2)),
                                      ((0., 5.5), (0., 3.5), (0., 4.5)),
                                      ((7.4, 4.8), (6., 3.5), (6.7, 4.15))):
            obs = goal_obstacles(context()) + [Obstacle(*center, .3)]
            self.assertFalse(segment_clear(start, target, obs))
            path = plan_global_path(context(), start, target, obs, omni=True)
            self.assertIsNotNone(path)
            self.assertEqual((path[0], path[-1]), (start, target))
            self.assertTrue(all(segment_clear(a, b, obs) for a, b in zip(path, path[1:])))

    def test_throw_in_and_corner_keep_unclamped_intercept(self):
        for play, ball, start in ((SetPlay.THROW_IN, (0., 4.5), (0., 1.)),
                                  (SetPlay.CORNER_KICK, (7., 4.5), (4., 2.)),
                                  (SetPlay.NONE, (-7.2, 0.), (-4., 0.))):
            ctx = context((start,), ball=BallState(*ball, last_seen_at=1.))
            ctx = replace(ctx, game=replace(ctx.game, set_play=play, kicking_team=1))
            p = Player(1, SoccerConfig(), None)
            p._mode = 'walk'
            prepare_frame(ctx, [p], SimpleNamespace())
            intent = p.decide(RoleTask(Role.ATTACKER))
            self.assertIsInstance(intent, MoveIntent)
            self.assertAlmostEqual(math.dist(intent.target, ball), .6)
            self.assertTrue(intent.avoid_ball)
            self.assertEqual(p.attack_receive['stage'], 'intercept')
            self.assertEqual(p.controller.plan_walk(intent, frame(ctx))[1], False)
            self.assertEqual(p.controller.navigation_status['effective_target'], list(intent.target))

    def test_outside_does_not_override_assigned_behavior(self):
        for role, method in ((Role.ATTACKER, 'attack'), (Role.GUARD, 'guard'),
                             (Role.FREEMAN, 'freeman')):
            p = Player(1, SoccerConfig(), None)
            p.context, p._mode = context(((0., 5.2),)), 'walk'
            expected = MoveIntent((1., 5.3), face=1.)
            setattr(p, method, Mock(return_value=expected))
            self.assertIs(p.decide(RoleTask(role)), expected)

    def test_released_player_reaches_ready_target_without_special_reentry(self):
        p = Player(1, SoccerConfig(), Mock(mode='walk', fall_down_state='normal'))
        store = SimpleNamespace()
        init_store(store)
        ctx = context(((0., 5.5),))
        ctx = replace(ctx, game=replace(ctx.game, state=GameState.READY))
        penalized = replace(ctx, game=replace(ctx.game, teams=(TeamState(
            players=(PlayerState(penalty=Penalty.LEAVING_THE_FIELD),)),)))
        p.context = penalized
        self.assertIsInstance(p.decide(assign(penalized, [p], store)[1]), RecoverIntent)
        p.context = ctx
        intent = p.decide(assign(ctx, [p], store)[1])
        self.assertIsInstance(intent, MoveIntent)
        self.assertNotEqual(p.action, 'return_to_field')
        pose = [0., 5.5, 0.]
        for step in range(500):
            p.context = replace(context((tuple(pose),), now=1+step*.05), game=ctx.game)
            vx, vy, yaw = p.controller.plan_walk(intent, frame(p.context))[0]
            theta = pose[2]
            pose[0] += .05*(vx*math.cos(theta)-vy*math.sin(theta))
            pose[1] += .05*(vx*math.sin(theta)+vy*math.cos(theta))
            pose[2] += .05*yaw
            if math.dist(pose[:2], intent.target) < intent.arrive_dist:
                break
        self.assertLess(math.dist(pose[:2], intent.target), intent.arrive_dist)
        p.context = penalized
        p.controller.execute(intent)
        p._backend.set_velocity.assert_called_with(0., 0., 0.)

    def test_playing_penalty_release_resumes_real_chase_into_field(self):
        p = Player(1, SoccerConfig(), None)
        p._mode = 'walk'
        store = SimpleNamespace()
        init_store(store)
        ctx = context(((0., 5.5),), ball=BallState(-1., 0., last_seen_at=1.))
        penalized = replace(ctx, game=replace(ctx.game, teams=(TeamState(
            players=(PlayerState(penalty=Penalty.LEAVING_THE_FIELD),)),)))
        prepare_frame(penalized, [p], store)
        self.assertIsInstance(p.decide(assign(penalized, [p], store)[1]), RecoverIntent)
        prepare_frame(ctx, [p], store)
        intent = p.decide(assign(ctx, [p], store)[1])
        self.assertIsInstance(intent, MoveIntent)
        self.assertEqual(p.action.split(':')[0], Role.ATTACKER.value)
        self.assertEqual(p.attack_choice['reason'], 'center_fallback')
        self.assertLess(abs(intent.target[1]), ctx.field.width/2)
        pose = [0., 5.5, 0.]
        for step in range(250):
            p.context = replace(context((tuple(pose),), now=1+step*.05), game=ctx.game)
            vx, vy, yaw = p.controller.plan_walk(intent, frame(p.context))[0]
            theta = pose[2]
            pose[0] += .05*(vx*math.cos(theta)-vy*math.sin(theta))
            pose[1] += .05*(vx*math.sin(theta)+vy*math.cos(theta))
            pose[2] += .05*yaw
            if abs(pose[1]) < 4.4:
                break
        self.assertLess(abs(pose[1]), 4.4)

    def test_goal_net_and_post_still_block_direct_movement(self):
        for start, target in (((7.25, 0.), (8., 0.)), ((7.25, .4), (7.25, 1.6)),
                              ((6.4, 1.3), (7.4, 1.3))):
            ctx = context((start,))
            intent = MoveIntent(target, direct_path=True, maintain_face=True)
            c = controller(ctx)
            self.assertEqual(c.plan_walk(intent, frame(ctx))[0], (0., 0., 0.))


if __name__ == '__main__':
    unittest.main()
