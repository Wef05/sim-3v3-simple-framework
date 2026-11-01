"""Navigation may cross pitch lines, while physical obstacles remain enforced."""
import math
import unittest

from checks.test_navigation import context, controller, frame
from src.intents import MoveIntent
from src.param import GLOBAL_MAX_GRID_CELLS
from src.utils.obstacles import Obstacle, goal_obstacles
from src.utils.path_planner import _search_axes, plan_global_path, segment_clear


class UnrestrictedNavigationTests(unittest.TestCase):
    def test_plain_and_direct_moves_preserve_outside_targets(self):
        for start, target in (((0., 4.), (0., 5.5)), ((0., -4.), (0., -5.5)),
                              ((6.5, 3.), (8., 3.)), ((-6.5, 3.), (-8., 3.))):
            for direct in (False, True):
                with self.subTest(start=start, direct=direct):
                    ctx = context((start,))
                    c = controller(ctx)
                    intent = MoveIntent(target, direct_path=direct, maintain_face=True)
                    cmd, arrived = c.plan_walk(intent, frame(ctx))
                    self.assertFalse(arrived)
                    self.assertEqual(c.navigation_status['effective_target'], list(target))
                    self.assertGreater(sum(v*(b-a) for v, a, b in zip(cmd[:2], start, target)), 0.)
                    if not direct:
                        self.assertEqual(c.navigation_status['path'][-1], target)

    def test_astar_can_leave_pitch_to_round_full_width_wall(self):
        obstacles = [Obstacle(0., -.4*i, .25) for i in range(-11, 12)]
        path = plan_global_path(context(), (-1., 0.), (1., 0.), obstacles, omni=True)
        self.assertIsNotNone(path)
        self.assertEqual(path[0], (-1., 0.))
        self.assertEqual(path[-1], (1., 0.))
        self.assertTrue(any(abs(p[1]) > 4.5 for p in path))
        self.assertTrue(all(segment_clear(a, b, obstacles) for a, b in zip(path, path[1:])))

    def test_outside_to_outside_detour_keeps_exact_endpoints(self):
        start, target = (-1., 6.), (1., 6.)
        obstacles = [Obstacle(0., 6., .25)]
        path = plan_global_path(context(), start, target, obstacles)
        self.assertIsNotNone(path)
        self.assertEqual((path[0], path[-1]), (start, target))
        self.assertTrue(all(segment_clear(a, b, obstacles) for a, b in zip(path, path[1:])))

    def test_outside_robot_bodies_still_block_even_with_avoid_flags_off(self):
        for kind in ('team', 'opponent'):
            ctx = (context(((0., 5.), (0., 5.8))) if kind == 'team'
                   else context(((0., 5.),), opponents=((0., 5.8),)))
            intent = MoveIntent((0., 7.), opponent_mode='contest', avoid_robots=False)
            f = frame(ctx)
            self.assertEqual(f.check(1, intent, (0., 2., 0.)),
                             'team:2' if kind == 'team' else 'opponent:1')
            c = controller(ctx)
            cmd, _ = c.plan_walk(intent, f)
            self.assertIsNone(f.check(1, intent, cmd))

    def test_posts_side_and_back_net_still_block_predicted_movement(self):
        for start, command in (((6.5, 1.3), (2., 0., 0.)),
                                ((7.3, .5), (0., 2., 0.)),
                                ((7.3, 0.), (2., 0., 0.)),
                                ((-7.3, -.5), (0., -2., 0.))):
            with self.subTest(start=start):
                ctx = context((start,))
                self.assertEqual(frame(ctx).check(1, MoveIntent((8.5, 0.)), command),
                                 'goal_structure')

    def test_direct_path_across_goal_net_still_stops(self):
        ctx = context(((6.5, 0.),))
        c = controller(ctx)
        command, _ = c.plan_walk(MoveIntent((8.5, 0.), direct_path=True), frame(ctx))
        self.assertEqual(command, (0., 0., 0.))
        self.assertEqual(c.navigation_status['blocker'], 'direct_segment')
        self.assertFalse(segment_clear((6.5, 0.), (8.5, 0.), goal_obstacles(ctx)))

    def test_large_distance_search_has_bounded_grid_and_preserves_endpoints(self):
        start, target = (-10000., 5.), (10000., 5.)
        xs, ys = _search_axes(context(), start, target)
        self.assertLessEqual(len(xs), GLOBAL_MAX_GRID_CELLS+1)
        self.assertLessEqual(len(ys), GLOBAL_MAX_GRID_CELLS+1)
        self.assertLess(xs[0], start[0])
        self.assertGreater(xs[-1], target[0])
        # Wide edges must still sweep-check the obstacle, even on a coarse grid.
        obstacles = [Obstacle(0., 5., .25)]
        path = plan_global_path(context(), start, target, obstacles, omni=True)
        self.assertIsNotNone(path)
        self.assertEqual((path[0], path[-1]), (start, target))
        self.assertTrue(all(segment_clear(a, b, obstacles) for a, b in zip(path, path[1:])))

    def test_nonfinite_target_still_stops(self):
        for value in (math.inf, math.nan):
            ctx = context()
            c = controller(ctx)
            command, _ = c.plan_walk(MoveIntent((value, 5.)), frame(ctx))
            self.assertEqual(command, (0., 0., 0.))
            self.assertEqual(c.navigation_status['state'], 'invalid_geometry')


if __name__ == '__main__':
    unittest.main()
