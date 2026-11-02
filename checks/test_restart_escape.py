"""保护区撤离：角色入口、真实导航指令与闭环几何回归。"""
import math
import unittest
from dataclasses import replace
from types import SimpleNamespace

from src.actions.motion_controller import MotionController
from src.framework.config import SoccerConfig
from src.framework.types import (Context, ADULT_FIELD_DIMENSIONS, Pose2D, RobotState,
    BallState, GameControlState, GameState, SetPlay)
from src.intents import Role, RoleTask, MoveIntent, StopIntent, RecoverIntent
from src.player import Player, coordinate
from src.utils.navigation import MotionTracker, NavigationFrame
from src.utils.restart_defense import restart_intent
from src.utils.restart_escape import escape_intent
from src.utils.path_planner import segment_clear


def context(position=(.3, 0., math.pi), ball=(0., 0.), kind=SetPlay.DIRECT_FREE_KICK):
    return Context(1., .05, 1, ADULT_FIELD_DIMENSIONS,
        ball=BallState(*ball) if ball is not None else None,
        game=GameControlState(state=GameState.PLAYING, kicking_team=2, set_play=kind),
        teammates={1: RobotState(1, Pose2D(*position), last_seen_at=1.)})


def command(ctx, intent):
    tracker = MotionTracker(); tracker.update(ctx)
    frame = NavigationFrame(ctx, tracker)
    controller = MotionController(1, SoccerConfig(), None)
    controller.context = ctx; controller._mode = 'walk'
    velocity, _ = controller.plan_walk(intent, frame)
    return velocity, controller.navigation_status


class RestartEscapeTests(unittest.TestCase):
    def test_original_opposite_target_stalls_new_escape_translates_outward(self):
        ctx = context()
        original, _, _ = restart_intent(ctx, ctx.teammates[1].pose, 0, False, None)
        old, status = command(ctx, original)
        self.assertEqual(status['route_reason'], 'no_route')
        self.assertEqual(math.hypot(*old[:2]), 0.)
        intent, label = escape_intent(ctx, 1, ctx.teammates[1].pose)
        velocity, status = command(ctx, intent)
        self.assertEqual(label, 'escape')
        self.assertGreater(-velocity[0], .1)  # 朝向 pi 时后退即世界 +x
        self.assertEqual(velocity[2], 0.)
        self.assertNotEqual(status['state'], 'blocked')

    def test_all_headings_and_set_plays_have_outward_translation(self):
        for kind in SetPlay:
            for angle in (0., math.pi/2, math.pi, -math.pi/2):
                ctx = context(position=(.3, 0., angle), kind=kind)
                intent, _ = escape_intent(ctx, 1, ctx.teammates[1].pose)
                velocity, _ = command(ctx, intent)
                wx = velocity[0]*math.cos(angle)-velocity[1]*math.sin(angle)
                self.assertGreater(wx, 0.)

    def test_blocked_radial_exit_chooses_clear_alternative(self):
        ctx = context()
        ctx = replace(ctx, opponents={4: RobotState(4, Pose2D(1.3, 0.), last_seen_at=1.)})
        intent, _ = escape_intent(ctx, 1, ctx.teammates[1].pose)
        self.assertIsInstance(intent, MoveIntent)
        self.assertGreater(abs(intent.target[1]), .5)
        obstacles = NavigationFrame(ctx, MotionTracker()).route_obstacles(1, intent)
        self.assertTrue(segment_clear((.3, 0.), intent.target, obstacles, allow_escape=True))

    def test_no_exit_stops_without_crossing_physical_obstacles(self):
        ctx = context(position=(0., 0., 0.))
        robots = {i+2: RobotState(i+2, Pose2D(.65*math.cos(i*math.pi/6),
                  .65*math.sin(i*math.pi/6)), last_seen_at=1.) for i in range(12)}
        ctx = replace(ctx, opponents=robots)
        intent, label = escape_intent(ctx, 1, ctx.teammates[1].pose)
        self.assertIsInstance(intent, StopIntent)
        self.assertEqual(label, 'escape_blocked')

    def test_sideline_prefers_field_but_does_not_trap_outside_start(self):
        for y, expected in ((4.1, 'escape'), (4.8, 'escape_outside')):
            ctx = context(position=(0., y, 0.), ball=(0., 4.45))
            intent, label = escape_intent(ctx, 1, ctx.teammates[1].pose)
            self.assertEqual(label, expected)
            velocity, _ = command(ctx, intent)
            self.assertGreater(math.hypot(*velocity[:2]), 0.)
            self.assertGreater((y-4.45)*velocity[1], 0.)

    def test_missing_invalid_ball_and_outside_do_not_invent_escape(self):
        for ctx in (context(ball=None), context(ball=(math.nan, 0.)),
                    context(position=(3., 0., 0.))):
            self.assertIsNone(escape_intent(ctx, 1, ctx.teammates[1].pose)[0])

    def test_guard_and_outfield_override_then_release(self):
        for role in (Role.GUARD, Role.OPP_SET_PLAY, Role.OPP_KICKOFF):
            ctx = context()
            p = SimpleNamespace(id=1, context=ctx, pose=ctx.teammates[1].pose,
                                _guard_memory=object(), _restart_memory=object())
            intent = Player.decide(p, RoleTask(role))
            self.assertIsInstance(intent, MoveIntent)
            self.assertTrue(p.action.endswith(':escape'))
            self.assertIsNone(p._guard_memory)
            self.assertIsNone(p._restart_memory)
        p.context = replace(ctx, game=GameControlState(state=GameState.PLAYING))
        self.assertIsInstance(Player.decide(p, RoleTask(Role.STAY)), StopIntent)
        self.assertNotIn('escape', p.action)
        p.context = ctx
        self.assertIsInstance(Player.decide(p, RoleTask(Role.UNAVAILABLE)), RecoverIntent)

    def test_no_escape_for_our_restart_set_or_stopped(self):
        for game in (replace(context().game, kicking_team=1),
                     replace(context().game, state=GameState.SET),
                     replace(context().game, stopped=True)):
            ctx = replace(context(), game=game)
            p = SimpleNamespace(id=1, context=ctx, pose=ctx.teammates[1].pose,
                                guard=lambda pass_to: StopIntent())
            self.assertIsInstance(Player.decide(p, RoleTask(Role.GUARD)), StopIntent)
            self.assertNotIn('escape', p.action)

    def test_closed_loop_leaves_circle_without_distance_decrease(self):
        ctx = context()
        distances = []
        for step in range(200):
            pose = ctx.teammates[1].pose
            intent, _ = escape_intent(ctx, 1, pose)
            if intent is None:
                break
            velocity, _ = command(ctx, intent)
            wx = velocity[0]*math.cos(pose.theta)-velocity[1]*math.sin(pose.theta)
            wy = velocity[0]*math.sin(pose.theta)+velocity[1]*math.cos(pose.theta)
            pose = replace(pose, x=pose.x+wx*.05, y=pose.y+wy*.05)
            distances.append(math.hypot(pose.x, pose.y))
            ctx = replace(ctx, now=ctx.now+.05,
                teammates={1: replace(ctx.teammates[1], pose=pose, last_seen_at=ctx.now+.05)})
        self.assertLess(step, 199)
        self.assertGreaterEqual(distances[-1], 2.0)
        self.assertTrue(all(b >= a for a, b in zip(distances, distances[1:])))

    def test_legal_route_clearance_does_not_reenter_escape(self):
        # 全局路径使用 2.0 m 膨胀边界；圈外路线不得因额外阈值反复撤离。
        ctx = context(position=(2.001, 0., 0.))
        self.assertIsNone(escape_intent(ctx, 1, ctx.teammates[1].pose)[0])

    def test_exact_ball_center_and_goal_structure_remain_safe(self):
        for position, ball in (((0., 0., 0.), (0., 0.)),
                               ((-6.8, 1.4, 0.), (-6.6, 1.3))):
            ctx = context(position=position, ball=ball)
            intent, _ = escape_intent(ctx, 1, ctx.teammates[1].pose)
            self.assertIsInstance(intent, MoveIntent)
            obstacles = NavigationFrame(ctx, MotionTracker()).route_obstacles(1, intent)
            self.assertTrue(segment_clear(position[:2], intent.target, obstacles, allow_escape=True))

    def test_multiple_players_keep_physical_clearance_and_priority(self):
        ctx = context(position=(.3, .7, 0.))
        ctx = replace(ctx, teammates={1: ctx.teammates[1],
            3: RobotState(3, Pose2D(.3, -.7, 0.), last_seen_at=1.)})
        intents = {pid: escape_intent(ctx, pid, robot.pose)[0] for pid, robot in ctx.teammates.items()}
        final = coordinate(ctx, {pid: RoleTask(Role.OPP_SET_PLAY) for pid in intents}, intents)
        frame = NavigationFrame(ctx, MotionTracker())
        for pid, intent in final.items():
            self.assertGreaterEqual(intent.motion_priority, 110)
            start = ctx.teammates[pid].pose
            self.assertTrue(segment_clear((start.x, start.y), intent.target,
                frame.route_obstacles(pid, intent), allow_escape=True))
        self.assertGreater(math.dist(final[1].target, final[3].target), 1.)


if __name__ == '__main__':
    unittest.main()
