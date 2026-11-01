"""Field reentry across READY, penalty release and blocked sideline starts."""
import math
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from checks.test_navigation import context, controller, frame
from src.framework.config import SoccerConfig
from src.framework.types import GameState, Penalty, PlayerState, TeamState
from src.intents import MoveIntent, RecoverIntent, Role, RoleTask, StopIntent
from src.player import Player, coordinate
from src.role_allocator import assign, init_store
from src.utils.obstacles import Obstacle
from src.utils.path_planner import plan_global_path, segment_clear


class ReentryTests(unittest.TestCase):
    def test_boundary_does_not_reject_inward_outward_or_braking_motion(self):
        for start, observed, command in (
                ((0., 5.), (0., .1), (0., -1., 0.)),
                ((0., 5.), (0., .1), (0., 1., 0.)),
                ((0., -5.), (0., -.1), (0., -1., 0.)),
                ((8., 3.), (.1, 0.), (1., 0., 0.)),
                ((-8., 3.), (-.1, 0.), (-1., 0., 0.)),
                ((0., 4.24), (0., 1.), (0., -1., 0.))):
            f = frame(context((start,)))
            f.tracker.velocities['team', 1] = observed
            self.assertIsNone(f.check(1, MoveIntent((0., 0.)), command))

    def player(self, ctx, pid=1):
        p = Player(pid, SoccerConfig(), None)
        p.context, p._mode = ctx, 'walk'
        return p

    def task(self, p):
        store = SimpleNamespace()
        init_store(store)
        return assign(p.context, [p], store)[p.id]

    def test_ready_all_slots_keep_formation_from_outside(self):
        for pid in (1, 2, 3):
            ctx = context(((0., 5.5),)*3)
            ctx = replace(ctx, game=replace(ctx.game, state=GameState.READY))
            p = self.player(ctx, pid)
            task = self.task(p)
            self.assertEqual(task.role, Role.READY)
            intent = p.decide(task)
            self.assertIsInstance(intent, MoveIntent)
            self.assertLess(abs(intent.target[1]), ctx.field.width/2)

    def test_penalty_release_returns_without_ball(self):
        ctx = context(((0., 5.5),))
        penalized = replace(ctx, game=replace(ctx.game, teams=(TeamState(
            players=(PlayerState(penalty=Penalty.LEAVING_THE_FIELD),)),)))
        p = self.player(penalized)
        self.assertIsInstance(p.decide(self.task(p)), RecoverIntent)
        p.context = ctx
        intent = p.decide(self.task(p))
        self.assertIsInstance(intent, MoveIntent)
        self.assertEqual(p.action, 'return_to_field')
        backend = Mock(mode='walk', fall_down_state='normal')
        p._backend = backend
        p.context = penalized  # 决策后重新受罚，下发前仍必须停车。
        p.controller.execute(intent)
        backend.set_velocity.assert_called_with(0., 0., 0.)

    def test_stopped_states_never_request_reentry(self):
        for state, stopped in ((GameState.INITIAL, False), (GameState.SET, False),
                               (GameState.FINISHED, False), (GameState.READY, True),
                               (GameState.PLAYING, True)):
            ctx = context(((0., 5.5),))
            p = self.player(replace(ctx, game=replace(ctx.game, state=state, stopped=stopped)))
            self.assertIsInstance(p.decide(self.task(p)), StopIntent)

    def test_waiting_kickoff_player_returns_and_resumes_waiting_inside(self):
        ctx = context(((0., 0.), (0., 0.), (0., 5.5)))
        ctx = replace(ctx, game=replace(ctx.game, kicking_team=1, secondary_time=10))
        p = self.player(ctx, 3)
        self.assertIsInstance(p.decide(self.task(p)), MoveIntent)
        p.context = replace(ctx, teammates=context(((0., 0.),)*3).teammates)
        self.assertIsInstance(p.decide(self.task(p)), StopIntent)

    def test_restart_keepout_survives_reentry(self):
        ctx = context(((0., 5.5),))
        ctx = replace(ctx, game=replace(ctx.game, kicking_team=2, secondary_time=10))
        p = self.player(ctx)
        task = self.task(p)
        intent = coordinate(ctx, {1: task}, {1: p.decide(task)})[1]
        self.assertTrue(intent.avoid_ball)
        self.assertEqual(intent.ball_keepout, ctx.field.circle_radius)
        self.assertEqual(intent.opponent_mode, 'avoid')

    def test_outside_routes_round_obstacle_with_safe_edges(self):
        for start, obstacle in (((0., 5.5), (0., 4.7)), ((0., -5.5), (0., -4.7)),
                                ((8., 3.), (7.2, 3.)), ((-8., 3.), (-7.2, 3.))):
            ctx = context((start,))
            obstacles = [Obstacle(*obstacle, .3)]
            path = plan_global_path(ctx, start, (0., 0.), obstacles, omni=True)
            self.assertIsNotNone(path)
            self.assertEqual(path[0], start)
            for i, (a, b) in enumerate(zip(path, path[1:])):
                self.assertTrue(segment_clear(a, b, obstacles, allow_escape=i == 0))

    def test_blocked_ready_entry_makes_progress_for_all_headings(self):
        for heading in (0., math.pi/2, math.pi, -math.pi/2):
            p = [0., 5.5, heading]
            c = controller(context((tuple(p),)))
            for n in range(160):
                c.context = context((tuple(p),), now=1+n*.05, opponents=((0., 4.7),))
                f = frame(c.context)
                intent = MoveIntent((0., 0.), face=0., avoid_robots=True)
                command, _ = c.plan_walk(intent, f)
                vx, vy, yaw = command
                wx, wy = vx*math.cos(p[2])-vy*math.sin(p[2]), vx*math.sin(p[2])+vy*math.cos(p[2])
                self.assertIsNone(f.check(1, intent, (wx, wy, yaw)))
                p = [p[0]+.05*wx, p[1]+.05*wy, p[2]+.05*yaw]
                if abs(p[1]) < 4.:
                    break
            self.assertLess(abs(p[1]), 4., (heading, p, c.navigation_status))


if __name__ == '__main__':
    unittest.main()
