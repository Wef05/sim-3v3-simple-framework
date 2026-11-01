"""Shared pass lifecycle and post-pass movement regression tests (no SDK)."""
import json
import math
import unittest
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock

from src import coordination as C, param as P, role_allocator
from src.framework.types import (Context, ADULT_FIELD_DIMENSIONS, GameControlState,
    GameState, SetPlay, BallState, RobotState, Pose2D)
from src.intents import (PassProposal, Role, RoleTask, MoveIntent, KickIntent,
                        KickTarget, StopIntent, FaceIntent)
from src.framework.config import SoccerConfig
from src.player import Player, decide
from src.framework.decision_frame import prepare_frame


class Motion:
    def __init__(self):
        self.ball = (0., 0.)
        self.velocity = (0., 0.)
    def ball_prediction_details(self):
        return dict(velocity_estimated=True, velocity=self.velocity)
    def predict_ball_position(self, t):
        return tuple(self.ball[i]+self.velocity[i]*t for i in (0, 1))


class PassingTests(unittest.TestCase):
    def setUp(self):
        self.motion = Motion()
        self.store = SimpleNamespace()
        role_allocator.init_store(self.store)
        self.players = [SimpleNamespace(id=i, pose=pose, mode='walk', is_fallen=False,
            is_penalized=False, motion=self.motion, action='attack' if i == 1 else 'freeman',
            ball_intercept={'target': (3., 0.), 'score_s': 0.1, 'status': 'intercept'},
            teammate_motion=SimpleNamespace(velocities={i: (0., 0.)}), threat=None,
            pass_proposal=None) for i, pose in ((1, Pose2D(-.35, 0., 0.)),
                                                (3, Pose2D(3., 0., math.pi)))]
        self.ctx = Context(1., .1, 1, ADULT_FIELD_DIMENSIONS,
            ball=BallState(0, 0, last_seen_at=1.),
            game=GameControlState(state=GameState.PLAYING))
        self.assignments = {1: RoleTask(Role.ATTACKER), 3: RoleTask(Role.FREEMAN)}
        self.preliminary = {1: KickIntent(KickTarget.teammate(3), 'pass'), 3: StopIntent()}
        self.players[0].pass_proposal = PassProposal(3, 'kickoff', (3., 0.))
        self.step(1.)

    def step(self, time, ball=None, velocity=None, sample=None):
        if ball is not None:
            self.motion.ball = ball
        if velocity is not None:
            self.motion.velocity = velocity
        self.ctx = replace(self.ctx, now=time, ball=BallState(*self.motion.ball,
            last_seen_at=time if sample is None else sample),
            teammates={p.id: RobotState(p.id, p.pose, last_seen_at=time) for p in self.players})
        C.validate_plan(self.ctx, self.players, self.store)
        return C.coordinate_pass(self.ctx, self.players, self.assignments, self.preliminary, self.store)

    def release(self):
        plan = self.store.passing.active
        self.assertIsNotNone(plan)
        self.players[1].pose = Pose2D(*plan.target, math.pi)
        self.players[0].pose = Pose2D(*C.behind_ball(0., 0., plan.target, P.CHASE_BEHIND_M),
                                    C.angle_to(0., 0., *plan.target))
        self.step(1.1)
        result = self.step(1.4)
        self.assertEqual(plan.stage, 'release')
        self.assertIsInstance(result[1], KickIntent)
        return plan

    def receive(self):
        plan = self.release()
        self.step(1.5, (.5, 0.), (2., 0.))
        result = self.step(1.6, (.7, 0.), (2., 0.))
        self.assertEqual(plan.stage, 'receive')
        return plan, result

    def test_wait_and_shared_frozen_target(self):
        self.assertNotIsInstance(self.step(1.05)[1], KickIntent)
        plan = self.release()
        self.assertEqual(self.step(1.41)[1].target.point, plan.target)
        self.assertEqual(self.preliminary[1].target.kind, 'teammate')

    def test_repeated_observation_does_not_prove_readiness(self):
        self.step(1.21, sample=1.)
        self.assertEqual(self.store.passing.active.stage, 'prepare')

    def test_request_without_motion_times_out(self):
        self.release()
        result = self.step(4.5)
        self.assertIsNone(self.store.passing.active)
        self.assertEqual(self.store.passing.events[-1]['reason'], 'release_timeout')
        self.assertIsInstance(result[1], StopIntent)

    def test_release_requires_multiple_observations_and_cancels_rekick(self):
        self.release()
        result = self.step(1.5, (.5, 0.), (2., 0.))
        self.assertEqual(self.store.passing.active.stage, 'release')
        self.assertNotIsInstance(result[1], KickIntent)
        self.step(1.7, sample=1.5)
        self.assertEqual(self.store.passing.active.stage, 'release')
        self.step(1.8, (.7, 0.), (2., 0.))
        self.assertEqual(self.store.passing.active.stage, 'receive')

    def test_ball_movement_before_request_cancels(self):
        self.step(1.1, (.4, 0.))
        self.assertIsNone(self.store.passing.active)
        self.assertEqual(self.store.passing.events[-1]['reason'], 'ball_moved_before_request')

    def test_wrong_direction_cancels(self):
        self.release()
        self.step(1.5, (0., .4), (0., 2.))
        self.assertEqual(self.store.passing.events[-1]['reason'], 'release_deviated')

    def test_roles_locked_then_receiver_handoff(self):
        plan, result = self.receive()
        roles = C.constrain_roles(self.ctx, self.players, self.store, self.assignments)
        self.assertEqual(roles[3].role, Role.ATTACKER)
        self.assertEqual(roles[1].role, Role.FREEMAN)
        self.assertIsInstance(result[3], MoveIntent)
        self.assertFalse(result[3].avoid_ball)
        self.assertNotIsInstance(result[1], KickIntent)
        target = plan.target
        self.players[1].pose = Pose2D(*target, math.pi)
        self.step(1.9, target, (0., 0.))
        self.step(2.2, target, (0., 0.))
        self.assertIsNone(self.store.passing.active)
        self.assertEqual(self.store.normal_attacker, 3)
        self.assertIsNotNone(self.store.passing.support)
        self.assertEqual(self.store.passing.events[-1]['event'], 'received')

    def test_unknown_relative_speed_not_success(self):
        plan, _ = self.receive()
        self.players[1].teammate_motion.velocities[3] = None
        self.step(1.9, plan.target, (0., 0.))
        self.step(2.3, plan.target, (0., 0.))
        self.assertIsNotNone(self.store.passing.active)

    def test_receiver_unavailable_releases_roles(self):
        self.players[1].is_fallen = True
        self.step(1.1)
        self.assertIsNone(self.store.passing.active)
        self.assertEqual(self.store.passing.events[-1]['reason'], 'participant_unavailable')

    def test_phase_and_rewind_clear(self):
        self.ctx = replace(self.ctx, game=replace(self.ctx.game, state=GameState.SET))
        self.step(1.1)
        self.assertIsNone(self.store.passing.active)
        self.ctx = replace(self.ctx, game=replace(self.ctx.game, state=GameState.PLAYING))
        self.players[0].pass_proposal = None
        self.step(.1)
        self.assertEqual(self.store.passing.events, [])

    def test_guard_never_receiver(self):
        self.store.passing.active = None
        self.players[1].id = 2
        self.players[0].pass_proposal = PassProposal(2, 'blocked_attack', (3., 0.))
        self.step(1.1)
        self.assertIsNone(self.store.passing.active)

    def test_blocked_lane_has_finite_reselection(self):
        self.ctx = replace(self.ctx, opponents={1: RobotState(1, Pose2D(1., 0.))})
        for time in (1.6, 2.2, 2.8):
            self.step(time)
        self.assertIsNone(self.store.passing.active)
        self.assertEqual(self.store.passing.events[-1]['reason'], 'no_safe_target')

    def test_support_is_behind_lateral_and_lower_priority(self):
        plan, _ = self.receive()
        result = self.step(2., (1.5, 0.), (1., 0.))
        self.assertIsInstance(result[1], MoveIntent)
        self.assertLess(result[1].target[0], plan.contact[0])
        self.assertGreater(abs(result[1].target[1]), 1.)
        self.assertTrue(result[1].avoid_ball)
        self.assertTrue(result[1].direct_path)
        self.assertLess(result[1].motion_priority, result[3].motion_priority)
        side = plan.support_side
        self.step(2.1, (1.6, 0.), (1., 0.))
        self.assertEqual(plan.support_side, side)

    def test_unsafe_support_route_holds(self):
        plan, _ = self.receive()
        self.players[0].pose = Pose2D(3., .2)
        # 起点贴近接球者，任何横穿其身体的支援路线不能发出。
        self.players[1].pose = Pose2D(3., 0.)
        result = self.step(2., (1.5, 0.), (1., 0.))
        self.assertNotIsInstance(result[1], KickIntent)
        if isinstance(result[1], MoveIntent):
            self.assertTrue(C._support_safe(self.ctx, *self.players, result[1].target, plan.contact))

    def test_emergency_cancels_without_leaking_receiver_shot(self):
        self.receive()
        guard = SimpleNamespace(id=2, pose=Pose2D(-6., 0.), mode='walk', is_fallen=False,
            is_penalized=False, motion=self.motion, action='guard:block:reachable', pass_proposal=None)
        self.players.append(guard)
        self.preliminary[2] = MoveIntent((-6, .3), motion_priority=100)
        self.preliminary[3] = KickIntent(KickTarget.goal())
        result = self.step(1.8)
        self.assertIsInstance(result[3], StopIntent)
        self.assertIsInstance(result[2], MoveIntent)
        self.assertIsNone(self.store.passing.active)
        self.assertIsNone(self.store.passing.support)
        self.assertEqual(self.store.passing.events[-1]['reason'], 'emergency_defence')

    def test_receive_timeout_and_unreachable_contact_release_lock(self):
        self.receive()
        self.players[1].ball_intercept = {'target': (3., 0.), 'score_s': 20.}
        self.step(1.8, (1., 0.), (1., 0.))
        self.assertIsNone(self.store.passing.active)
        self.assertEqual(self.store.passing.events[-1]['reason'], 'receiver_cannot_arrive')

    def test_repeated_sample_does_not_complete_receive(self):
        plan, _ = self.receive()
        self.step(2., plan.target, (0., 0.))
        self.step(2.3, sample=2.)
        self.assertIsNotNone(self.store.passing.active)
        self.step(2.4, plan.target, (0., 0.))
        self.assertIsNone(self.store.passing.active)

    def test_receiver_moves_during_release_no_retarget(self):
        plan = self.release()
        target = plan.target
        self.players[1].pose = Pose2D(4., 0., math.pi)
        result = self.step(1.45)
        self.assertIsNone(self.store.passing.active)
        self.assertEqual(plan.target, target)
        self.assertIsInstance(result[1], StopIntent)

    def test_unknown_opponent_prevents_plan(self):
        self.store.passing.active = None
        self.ctx = replace(self.ctx, opponents={1: RobotState(1)})
        self.step(1.1)
        self.assertIsNone(self.store.passing.active)

    def test_support_expires_and_ball_shift_clears(self):
        plan, _ = self.receive()
        self.step(2., plan.target, (0., 0.))
        self.step(2.3, plan.target, (0., 0.))
        self.players[0].pass_proposal = None
        self.step(3.6, plan.target, (0., 0.))
        self.assertIsNone(self.store.passing.support)

    def test_goal_threat_interrupts_before_role_assignment(self):
        self.release()
        self.step(1.5, (-.4, 0.), (-2., 0.))
        self.assertIsNone(self.store.passing.active)
        self.assertEqual(self.store.passing.events[-1]['reason'], 'goal_threat')

    def test_completed_kickoff_does_not_return_to_original_passer(self):
        self.ctx = replace(self.ctx, game=replace(self.ctx.game, kicking_team=1, secondary_time=10))
        self.store.passing.active.phase_key = C._phase_key(self.ctx)
        plan, _ = self.receive()
        self.step(2., plan.target, (0., 0.))
        self.step(2.3, plan.target, (0., 0.))
        self.players[0].pass_proposal = None
        self.step(3.6, plan.target, (0., 0.))
        roles = role_allocator.assign(self.ctx, self.players, self.store)
        self.assertEqual(roles[3].role, Role.ATTACKER)
        self.assertEqual(roles[1].role, Role.FREEMAN)

    def test_guard_pass_restores_guard_without_front_support(self):
        # 直接从已确认出球阶段验证守门员分支；准备阶段另由原有守门安全检查约束。
        plan, _ = self.receive()
        passer = self.players[0]
        passer.id = 2
        passer.pose = Pose2D(-6., 0.)
        passer.pass_proposal = PassProposal(3, 'guard', plan.target)
        plan.passer = 2
        self.preliminary = {2: KickIntent(KickTarget.position(plan.target), 'pass'), 3: StopIntent()}
        result = self.step(2., (1., 0.), (1., 0.))
        self.assertIsInstance(result[2], MoveIntent)
        self.assertLess(result[2].target[0], -5.)
        self.assertTrue(result[2].avoid_ball)
        self.assertEqual(passer.action, 'guard:post_pass_home')
        self.assertIsNone(passer._guard_memory)

    def test_json_has_no_nonfinite_values(self):
        json.dumps(asdict(self.store.passing), allow_nan=False)

    def test_input_order_independence(self):
        first = asdict(self.store.passing.active)
        self.store.passing = C.TeamPassing()
        self.players.reverse()
        self.step(1.)
        self.assertEqual(first, asdict(self.store.passing.active))


class PipelineTests(unittest.TestCase):
    def test_real_kickoff_pipeline_never_sends_before_receiver_ready(self):
        ctx = Context(1., .1, 1, ADULT_FIELD_DIMENSIONS,
            ball=BallState(0, 0, last_seen_at=1.),
            game=GameControlState(state=GameState.PLAYING, kicking_team=1, secondary_time=10),
            teammates={1: RobotState(1, Pose2D(-.35, 0.), 1.),
                       2: RobotState(2, Pose2D(-6., 0.), 1.),
                       3: RobotState(3, Pose2D(3., 0., 0.), 1.)})
        players = [Player(i, SoccerConfig(), Mock(mode='walk', fall_down_state='normal')) for i in (1, 2, 3)]
        store = SimpleNamespace()
        role_allocator.init_store(store)
        prepare_frame(ctx, players, store)
        assignments = role_allocator.assign(ctx, players, store)
        intents = decide(ctx, players, assignments, store)
        self.assertEqual(set(intents), {1, 2, 3})
        self.assertNotIsInstance(intents[1], KickIntent)
        self.assertIsNotNone(store.passing.active)
        self.assertEqual(store.passing.active.receiver, 3)
        for p in players:
            p._backend.kick.assert_not_called()
        self.assertEqual(assignments[2].role, Role.GUARD)


if __name__ == '__main__':
    unittest.main()
