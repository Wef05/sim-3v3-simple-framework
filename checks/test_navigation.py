"""Navigation geometry, prediction, priority and execution regression cases."""
import math
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from src.actions import execute
from src.actions.motion_controller import MotionController
from src.framework.config import SoccerConfig
from src.framework.types import (Context, ADULT_FIELD_DIMENSIONS, Pose2D, RobotState,
    BallState, GameControlState, GameState, SetPlay)
from src.intents import MoveIntent, Role, RoleTask, StopIntent, KickIntent, KickTarget
from src.player import Player, coordinate
from src.utils.navigation import MotionTracker, NavigationFrame, trajectories_conflict
from src.utils.obstacles import Obstacle, goal_obstacles
from src.utils.path_planner import plan_global_path, segment_clear


def context(positions=((0., 0.),), *, now=1., opponents=(), ball=None):
    return Context(now, .05, 1, ADULT_FIELD_DIMENSIONS, ball=ball,
        game=GameControlState(state=GameState.PLAYING),
        teammates={i: RobotState(i, Pose2D(*p), last_seen_at=now) for i, p in enumerate(positions, 1)},
        opponents={i: RobotState(i, Pose2D(*p), last_seen_at=now) for i, p in enumerate(opponents, 1)})


def frame(ctx):
    tracker = MotionTracker()
    tracker.update(ctx)
    return NavigationFrame(ctx, tracker)


def controller(ctx, pid=1):
    c = MotionController(pid, SoccerConfig(), None)
    c.context, c._mode = ctx, 'walk'
    return c


class GeometryTests(unittest.TestCase):
    def test_swept_body_rejects_clear_endpoints_with_blocked_middle(self):
        self.assertFalse(segment_clear((-1, 0), (1, 0), [Obstacle(0, 0, .01)]))

    def test_robot_diameter_is_added(self):
        self.assertFalse(segment_clear((-1, .49), (1, .49), [Obstacle(0, 0, .25)]))
        self.assertTrue(segment_clear((-1, .65), (1, .65), [Obstacle(0, 0, .25)]))

    def test_astar_detour_has_safe_edges_and_exact_start(self):
        ctx, obstacles = context(), [Obstacle(0, 0, .25)]
        path = plan_global_path(ctx, (-1.17, .03), (1.1, .02), obstacles)
        self.assertIsNotNone(path)
        self.assertEqual(path[0], (-1.17, .03))
        self.assertEqual(path[-1], (1.1, .02))
        self.assertTrue(all(segment_clear(a, b, obstacles) for a, b in zip(path, path[1:])))

    def test_blocked_endpoint_not_exempt(self):
        self.assertIsNone(plan_global_path(context(), (-1, 0), (0, 0), [Obstacle(0, 0, .25)]))

    def test_same_grid_cell_still_checks_segment(self):
        self.assertIsNone(plan_global_path(context(), (0, 0), (.02, 0), [Obstacle(.02, 0, .01)]))

    def test_overlapping_start_may_escape_but_not_cross_obstacle(self):
        obs = [Obstacle(0, 0, .25)]
        self.assertTrue(segment_clear((.4, 0), (1, 0), obs, allow_escape=True))
        self.assertFalse(segment_clear((.4, 0), (-1, 0), obs, allow_escape=True))
        self.assertIsNotNone(plan_global_path(context(), (.4, 0), (1, 0), obs))

    def test_goal_posts_sides_and_back_net(self):
        obs = goal_obstacles(context())
        for a, b in (((6.5, 1.3), (7.5, 1.3)), ((7.3, .5), (7.3, 1.7)),
                     ((7.3, 0), (7.9, 0)), ((-7.3, -.5), (-7.3, -1.7))):
            self.assertFalse(segment_clear(a, b, obs))
        self.assertTrue(segment_clear((6.2, 0), (6.6, 0), obs))

    def test_waypoint_does_not_skip_corner(self):
        c = controller(context(((-1., 0.),)))
        obs = [Obstacle(0, 0, .25)]
        path = plan_global_path(c.context, (-1, 0), (1, 0), obs)
        p = c._path_waypoint(c.pose, path, obs)
        self.assertTrue(segment_clear((-1, 0), p, obs))

    def test_waypoint_accumulates_distance(self):
        c = controller(context())
        p = c._path_waypoint(c.pose, [(0, 0), (.3, 0), (.6, 0), (1.2, 0)])
        self.assertAlmostEqual(p[0], .9)


class PredictionTests(unittest.TestCase):
    def test_tracker_distinct_samples_pause_rewind_teleport_and_expiry(self):
        t = MotionTracker()
        t.update(context(now=1))
        t.update(context(((.1, 0),), now=1.1))
        self.assertAlmostEqual(t.velocity('team', 1)[0], 1.)
        t.update(context(((.1, 0),), now=1.1))
        self.assertAlmostEqual(t.velocity('team', 1)[0], 1.)
        t.update(replace(context(((.1, 0),), now=1.1), now=2.))
        self.assertEqual(t.velocity('team', 1), (0., 0.))
        t.update(context(now=.1))
        self.assertEqual(t.velocity('team', 1), (0., 0.))
        t.update(context(((6, 0),), now=.2))
        self.assertEqual(t.velocity('team', 1), (0., 0.))
        t.update(replace(context(now=.3), teammates={}))
        self.assertEqual(t.samples, {})

    def test_crossing_opponent_predicted_before_contact(self):
        ctx = context(opponents=((0, -1.3),))
        f = frame(ctx)
        f.tracker.velocities['opponent', 1] = (0, 2.)
        self.assertIsNotNone(f.check(1, MoveIntent((0, 1)), (0, 0, 0)))
        f.tracker.velocities['opponent', 1] = (0, -2.)
        self.assertIsNone(f.check(1, MoveIntent((0, 1)), (0, 0, 0)))

    def test_swept_relative_segments_do_not_tunnel(self):
        a = [(0, (-1, 0)), (.1, (1, 0))]
        b = [(0, (0, -1)), (.1, (0, 1))]
        self.assertTrue(trajectories_conflict(a, b, .5))

    def test_reservation_forces_lower_priority_to_yield(self):
        ctx = context(((-1, 0), (0, -1)))
        f = frame(ctx)
        intent = MoveIntent((1, 0), maintain_face=True)
        self.assertIsNone(f.check(1, intent, (2, 0, 0)))
        f.reserve(2, (0, 2, 0))
        self.assertEqual(f.check(1, intent, (2, 0, 0)), 'yield:team:2')

    def test_existing_overlap_can_separate(self):
        a = [(0, (.4, 0)), (1, (.8, 0))]
        b = [(0, (0, 0)), (1, (0, 0))]
        self.assertFalse(trajectories_conflict(a, b, .54))
        self.assertTrue(trajectories_conflict(list(reversed(a)), b, .54))

    def test_braking_drift_cannot_be_hidden_by_zero_command(self):
        ctx = context(((6.65, 1.3),))
        f = frame(ctx)
        f.tracker.velocities['team', 1] = (2., 0.)
        self.assertEqual(f.check(1, MoveIntent((6., 1.3)), (0, 0, 0)), 'field_boundary')

    def test_ball_avoidance_is_scenario_specific(self):
        ctx = context(ball=BallState(.6, 0, last_seen_at=1))
        f = frame(ctx)
        self.assertIsNone(f.check(1, MoveIntent((1, 0)), (1., 0, 0)))
        self.assertEqual(f.check(1, MoveIntent((1, 0), avoid_ball=True), (1., 0, 0)), 'ball')

    def test_contest_removes_extra_margin_not_robot_body(self):
        ctx = context(opponents=((.8, .65),))
        f = frame(ctx)
        self.assertIsNone(f.check(1, MoveIntent((2, 0), opponent_mode='contest'), (2, 0, 0)))
        self.assertIsNotNone(f.check(1, MoveIntent((2, 0), opponent_mode='avoid'), (2, 0, 0)))
        ctx = context(opponents=((.8, .3),))
        self.assertIsNotNone(frame(ctx).check(1, MoveIntent((2, 0), opponent_mode='contest'), (2, 0, 0)))

    def test_moving_ball_is_avoided_before_reaching_robot(self):
        tracker = MotionTracker()
        tracker.update(context(now=.9, ball=BallState(-1.4, 0, last_seen_at=.9)))
        ctx = context(ball=BallState(-1.2, 0, last_seen_at=1.))
        tracker.update(ctx)
        f = NavigationFrame(ctx, tracker)
        self.assertEqual(f.check(1, MoveIntent((0, 1), avoid_ball=True), (0, 0, 0)), 'ball')
        self.assertIsNone(f.check(1, MoveIntent((0, 1), avoid_ball=False), (0, 0, 0)))


class PolicyAndExecutionTests(unittest.TestCase):
    def test_priorities_and_restart_policy(self):
        ctx = context(((0, 0), (1, 0), (2, 0)))
        tasks = {1: RoleTask(Role.ATTACKER), 2: RoleTask(Role.GUARD), 3: RoleTask(Role.FREEMAN)}
        initial = {i: MoveIntent((3, 0)) for i in tasks}
        final = coordinate(ctx, tasks, initial)
        self.assertEqual([final[i].motion_priority for i in tasks], [80, 60, 20])
        self.assertEqual(final[1].opponent_mode, 'contest')
        self.assertEqual(initial[1].motion_priority, 10)
        ctx = replace(ctx, game=replace(ctx.game, kicking_team=2, set_play=SetPlay.CORNER_KICK))
        final = coordinate(ctx, tasks, initial)
        self.assertTrue(all(i.avoid_ball and i.opponent_mode == 'avoid' and i.ball_keepout >= 1.5 for i in final.values()))

    def test_default_move_cannot_disable_goal_or_team_safety(self):
        ctx = context(((6.5, 1.3), (6.3, 1.3)))
        c = controller(ctx)
        command, _ = c.plan_walk(MoveIntent((7.2, 1.3), direct_path=True), frame(ctx))
        self.assertEqual(command, (0., 0., 0.))

    def test_no_safe_direct_path_stops(self):
        ctx = context(opponents=((.4, 0),))
        c = controller(ctx)
        command, _ = c.plan_walk(MoveIntent((1, 0), direct_path=True), frame(ctx))
        self.assertEqual(command, (0., 0., 0.))

    def test_deadline_target_is_not_silently_clamped(self):
        ctx = context(((6., 0.),))
        c = controller(ctx)
        command, _ = c.plan_walk(MoveIntent((7.1, 0), direct_path=True), frame(ctx))
        self.assertEqual(command, (0., 0., 0.))
        self.assertEqual(c.navigation_status['blocker'], 'field_boundary')

    def test_open_field_makes_progress(self):
        ctx = context()
        c = controller(ctx)
        cmd, arrived = c.plan_walk(MoveIntent((3, 0)), frame(ctx))
        self.assertGreater(cmd[0], 0)
        self.assertFalse(arrived)

    def test_all_directions_blocked_stops(self):
        ctx = context(opponents=tuple((.6*math.cos(i*math.pi/4), .6*math.sin(i*math.pi/4)) for i in range(8)))
        c = controller(ctx)
        command, _ = c.plan_walk(MoveIntent((2, 0), maintain_face=True), frame(ctx))
        self.assertEqual(command, (0., 0., 0.))
        self.assertEqual(c.navigation_status['state'], 'blocked')

    def test_kick_handoff_does_not_approach_through_post(self):
        ctx = context(((6.5, 1.3),), ball=BallState(7.2, 1.3, last_seen_at=1))
        p = Player(1, SoccerConfig(), Mock(mode='walk', fall_down_state='normal'))
        execute(ctx, [p], {1: KickIntent(KickTarget.goal())})
        p._backend.kick.assert_not_called()
        self.assertEqual(p.controller.navigation_status['state'], 'kick_approach_blocked')

    def test_joint_command_check_catches_inconsistent_plans(self):
        ctx = context(((-1., 0.), (0., -1.)))
        ps = [Player(i, SoccerConfig(), Mock(mode='walk', fall_down_state='normal')) for i in (1, 2)]
        # Simulate a bad planner result to verify the independent final pair check.
        ps[0].controller.plan_walk = Mock(return_value=((2., 0., 0.), False))
        ps[1].controller.plan_walk = Mock(return_value=((0., 2., 0.), False))
        intents = {1: MoveIntent((1, 0), motion_priority=80), 2: MoveIntent((0, 1), motion_priority=20)}
        execute(ctx, ps, intents)
        self.assertEqual(ps[0]._backend.set_velocity.call_args.args, (2., 0., 0.))
        self.assertEqual(ps[1]._backend.set_velocity.call_args.args, (0., 0., 0.))

    def test_order_independence_and_plan_before_backend_calls(self):
        ctx = context(((-1, 0), (0, -1)))
        intents = {1: MoveIntent((1, 0), maintain_face=True, face=0, motion_priority=80),
                   2: MoveIntent((0, 1), maintain_face=True, face=0, motion_priority=20)}
        results = []
        for ids in ((1, 2), (2, 1)):
            ps = [Player(i, SoccerConfig(), Mock(mode='walk', fall_down_state='normal')) for i in ids]
            def check_planning(*args):
                self.assertTrue(all(p.controller.navigation_status is not None for p in ps))
            for p in ps:
                p._backend.set_velocity.side_effect = check_planning
            execute(ctx, ps, intents)
            results.append({p.id: p._backend.set_velocity.call_args.args for p in ps})
        self.assertEqual(*results)
        self.assertNotEqual(results[0][2], (0, 1.5, 0))

    def test_crossing_kinematic_rollout_keeps_separation_and_arrives(self):
        ps = [Player(i, SoccerConfig(), Mock(mode='walk', fall_down_state='normal')) for i in (1, 2)]
        positions = [(-1.5, 0.), (0., -1.5)]
        targets = [(1.5, 0.), (0., 1.5)]
        intents = {i: MoveIntent(targets[i-1], face=0, maintain_face=True, motion_priority=80 if i == 1 else 20)
                   for i in (1, 2)}
        minimum = math.inf
        for k in range(180):
            ctx = context(tuple(positions), now=1+k*.05)
            execute(ctx, ps, intents)
            commands = [p._backend.set_velocity.call_args.args for p in ps]
            positions = [(xy[0]+v[0]*.05, xy[1]+v[1]*.05) for xy, v in zip(positions, commands)]
            minimum = min(minimum, math.dist(*positions))
        self.assertGreaterEqual(minimum, .5)
        self.assertTrue(all(math.dist(p, t) < .16 for p, t in zip(positions, targets)), positions)

    def test_head_on_and_three_way_kinematic_rollouts(self):
        cases = [([(-2., 0.), (2., 0.)], [(2., 0.), (-2., 0.)]),
                 ([(-2., 0.), (0., -2.), (2., 0.)], [(2., 0.), (0., 2.), (-2., 0.)])]
        for positions, targets in cases:
            with self.subTest(count=len(positions)):
                ps = [Player(i+1, SoccerConfig(), Mock(mode='walk', fall_down_state='normal')) for i in range(len(positions))]
                intents = {i+1: MoveIntent(t, face=0, maintain_face=True, motion_priority=80-i*20)
                           for i, t in enumerate(targets)}
                minimum = math.inf
                for k in range(250):
                    execute(context(tuple(positions), now=1+k*.05), ps, intents)
                    positions = [(xy[0]+p._backend.set_velocity.call_args.args[0]*.05,
                                  xy[1]+p._backend.set_velocity.call_args.args[1]*.05) for xy, p in zip(positions, ps)]
                    minimum = min(minimum, *(math.dist(positions[i], positions[j])
                        for i in range(len(positions)) for j in range(i)))
                self.assertGreaterEqual(minimum, .5)
                self.assertTrue(all(math.dist(p, t) < .16 for p, t in zip(positions, targets)), positions)


if __name__ == '__main__':
    unittest.main()
