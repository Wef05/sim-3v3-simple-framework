"""Time-aware navigation: moving obstacles, task lifecycle and safe route changes."""
import math
import unittest
from dataclasses import replace
from checks.test_navigation import context, controller, frame
from src.actions import execute
from src.framework.config import SoccerConfig
from src.framework.types import GameState
from src.player import Player
from src.intents import MoveIntent, StopIntent
from src.param import NAV_SAFETY_MARGIN_M
from src.utils.navigation import MotionTracker, NavigationFrame
from src.utils.obstacles import Obstacle
from src.utils.path_planner import path_time, plan_global_path, segment_clear


class TimeCostTests(unittest.TestCase):
    def test_turn_and_lateral_cost_more_than_forward(self):
        straight = [(0, 0), (2, 0)]
        self.assertGreater(path_time(straight, math.pi), path_time(straight, 0))
        self.assertGreater(path_time([(0, 0), (0, 2)], 0, omni=True),
                           path_time(straight, 0, omni=True))
        self.assertGreater(path_time(straight, math.pi, omni=True),
                           path_time(straight, 0, omni=True))

    def test_heading_selects_detour_with_less_turning(self):
        obs = [Obstacle(0, 0, .25)]
        paths = [plan_global_path(context(), (-2, 0), (2, 0), obs, heading=h)
                 for h in (-1.2, 1.2)]
        self.assertLess(paths[0][1][1], 0)
        self.assertGreater(paths[1][1][1], 0)
        for h, chosen, other in ((-1.2, paths[0], paths[1]), (1.2, paths[1], paths[0])):
            self.assertLess(path_time(chosen, h), path_time(other, h))
            self.assertTrue(all(segment_clear(a, b, obs) for a, b in zip(chosen, chosen[1:])))


class RouteStabilityTests(unittest.TestCase):
    def test_moving_team_and_opponent_do_not_oscillate(self):
        for kind in ('team', 'opponent'):
            with self.subTest(kind=kind):
                tracker, c = MotionTracker(), controller(context(((-2., 0., 0.),)))
                sides = []
                for i in range(80):
                    moving = (0., .08*math.sin(i*.6), 0.)
                    ctx = context(((-2., 0., 0.), moving) if kind == 'team' else ((-2., 0., 0.),),
                                  opponents=(moving,) if kind == 'opponent' else (), now=1+i*.05)
                    c.context = ctx
                    tracker.update(ctx)
                    f = NavigationFrame(ctx, tracker)
                    cmd, _ = c.plan_walk(MoveIntent((2., 0.)), f)
                    info = c.navigation_status
                    sides.append(1 if info['waypoint'][1] > 0 else -1)
                    self.assertIsNone(f.check(1, MoveIntent((2., 0.)), cmd))  # body == world here
                    self.assertTrue(all(segment_clear(a, b, f.route_obstacles(1, MoveIntent((2., 0.))),
                                        margin=NAV_SAFETY_MARGIN_M, allow_escape=j == 0)
                                        for j, (a, b) in enumerate(zip(info['path'], info['path'][1:]))))
                self.assertLessEqual(sum(a != b for a, b in zip(sides, sides[1:])), 1)

    def test_new_obstacle_on_old_route_replans_safely(self):
        ctx = context(((-2., 0., 0.),), opponents=((0., 0., 0.),))
        c = controller(ctx)
        intent = MoveIntent((2., 0.))
        c.plan_walk(intent, frame(ctx))
        old = c.navigation_status['path']
        corner = old[1]
        c.context = context(((-2., 0., 0.),), opponents=((0., 0., 0.), (*corner, 0.)), now=1.05)
        f = frame(c.context)
        cmd, _ = c.plan_walk(intent, f)
        self.assertNotEqual(c.navigation_status['route_reason'], 'retained')
        self.assertIsNone(f.check(1, intent, cmd))
        self.assertNotEqual(c.navigation_status['path'], old)

    def test_clear_shortcut_replaces_large_detour(self):
        ctx = context(((-2., 0., 0.),), opponents=((0., 0., 0.),))
        c = controller(ctx)
        intent = MoveIntent((2., 0.))
        c.plan_walk(intent, frame(ctx))
        c.context = context(((-2., 0., 0.),), now=1.05)
        c.plan_walk(intent, frame(c.context))
        self.assertEqual(c.navigation_status['route_reason'], 'faster')
        self.assertEqual(c.navigation_status['path'], [(-2., 0.), (2., 0.)])

    def test_target_phase_clock_and_gap_reset(self):
        for change in ('target', 'phase', 'rewind', 'gap'):
            with self.subTest(change=change):
                ctx = context(((-2., 0., 0.),), opponents=((0., 0., 0.),))
                c = controller(ctx)
                c.plan_walk(MoveIntent((2., 0.)), frame(ctx))
                now = .5 if change == 'rewind' else 3. if change == 'gap' else 1.05
                ctx = replace(ctx, now=now)
                if change == 'phase':
                    ctx = replace(ctx, game=replace(ctx.game, state=GameState.READY))
                c.context = ctx
                c.plan_walk(MoveIntent((2., 1.) if change == 'target' else (2., 0.)), frame(ctx))
                self.assertEqual(c.navigation_status['route_reason'], 'new')

    def test_cumulative_target_drift_resets(self):
        ctx = context(((-2., 0., 0.),))
        c = controller(ctx)
        for i in range(7):
            c.context = replace(ctx, now=1+i*.05)
            c.plan_walk(MoveIntent((2., i*.1)), frame(c.context))
        self.assertEqual(c.navigation_status['route_reason'], 'new')

    def test_nonmovement_clears_route(self):
        ctx = context(((-2., 0., 0.),))
        p = Player(1, SoccerConfig(), None)
        p.controller._mode = 'walk'
        execute(ctx, [p], {1: MoveIntent((2., 0.))})
        self.assertIsNotNone(p.controller._nav_path)
        execute(ctx, [p], {1: StopIntent()})
        self.assertIsNone(p.controller._nav_path)

    def test_direct_path_never_reuses_detour(self):
        ctx = context(((-2., 0., 0.),), opponents=((0., 0., 0.),))
        c = controller(ctx)
        c.plan_walk(MoveIntent((2., 0.)), frame(ctx))
        command, _ = c.plan_walk(MoveIntent((2., 0.), direct_path=True), frame(ctx))
        self.assertEqual(command, (0., 0., 0.))
        self.assertEqual(c.navigation_status['state'], 'blocked_direct')
        self.assertIsNone(c._nav_path)


if __name__ == '__main__':
    unittest.main()
