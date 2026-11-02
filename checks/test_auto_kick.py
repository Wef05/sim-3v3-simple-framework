"""自动踢球交接、计时、取消和真实后端失败路径（不启动 SDK）。"""
import ast
import logging
import math
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.player import Player
from src.actions import execute
from src.framework.config import SoccerConfig
from src.framework.types import (Context, ADULT_FIELD_DIMENSIONS, Pose2D, RobotState,
                                 BallState, GameControlState, GameState)
from src.intents import KickIntent, MoveIntent, StopIntent, Role, RoleTask
from src.telemetry import make_snapshot


class AutoKickTests(unittest.TestCase):
    def setUp(self):
        self.backend = Mock(mode='walk', fall_down_state='normal')
        self.backend.kick.return_value = True
        self.backend.release_kick.return_value = True
        self.p = Player(1, SoccerConfig(), self.backend)
        self.ctx = Context(10., .02, 1, ADULT_FIELD_DIMENSIONS,
            game=GameControlState(state=GameState.PLAYING),
            ball=BallState(0., 0., last_seen_at=10.),
            teammates={1: RobotState(1, Pose2D(-.35, 0., 0.), last_seen_at=10.)})
        for name in ('evaluate_attack_targets', 'evaluate_scoring', 'estimate_interception_risk'):
            mocked = patch('src.player.'+name, return_value={'evaluated_at': 10.})
            mocked.start()
            self.addCleanup(mocked.stop)

    def tick(self, now=10., pose=None, ball=None, execute_intent=True):
        self.ctx = replace(self.ctx, now=now,
            ball=ball or replace(self.ctx.ball, last_seen_at=now),
            teammates={1: replace(self.ctx.teammates[1], last_seen_at=now,
                                 pose=pose or self.ctx.teammates[1].pose)})
        self.p.context = self.ctx
        self.p.motion = Mock()
        self.p.motion.predict_ball_position.return_value = (self.ctx.ball.x, self.ctx.ball.y)
        self.p.ball_intercept = dict(status='intercept', target=(self.ctx.ball.x,self.ctx.ball.y))
        intent = self.p.attack()
        if execute_intent:
            execute(self.ctx, [self.p], {1: intent})
        return intent

    def test_only_staged_aligned_player_starts(self):
        for pose, reason in [(Pose2D(-1,0,0),'too_far'), (Pose2D(-.35,0,.5),'misaligned'),
                             (Pose2D(.35,0,0),'ball_behind'),(Pose2D(-.35,.25,0),'not_staged')]:
            with self.subTest(pose=pose):
                self.assertNotIsInstance(self.tick(pose=pose), KickIntent)
                self.assertEqual(self.p.attack_kick.diagnostics['reason'],reason)
        self.assertIsInstance(self.tick(pose=Pose2D(-.35,0,0)),KickIntent)
        self.assertTrue(self.p.controller.kick_control['accepted'])

    def test_relaxed_entry_accepts_small_stage_and_angle_errors(self):
        self.assertIsInstance(self.tick(pose=Pose2D(-.53, 0, math.radians(24))), KickIntent)

    def test_distance_grace_recovers_and_restarts(self):
        self.tick()
        self.assertIsInstance(self.tick(10.1, Pose2D(-1.49,0,0)), KickIntent)
        self.assertIsInstance(self.tick(10.2, Pose2D(-1.51,0,0)), KickIntent)
        self.assertEqual(self.p.attack_kick.diagnostics['reason'], 'grace_pending')
        self.assertIsInstance(self.tick(10.49, Pose2D(-1.51,0,0)), KickIntent)
        self.assertIsInstance(self.tick(10.5, Pose2D(-1.4,0,0)), KickIntent)
        self.assertEqual(self.p.attack_kick.diagnostics['exit_grace'], [])
        self.assertIsInstance(self.tick(10.6, Pose2D(-1.51,0,0)), KickIntent)
        self.assertNotIsInstance(self.tick(10.9, Pose2D(-1.51,0,0)), KickIntent)
        self.assertFalse(self.p.is_kicking)
        self.assertEqual(self.p.attack_kick.last_exit,'too_far')

    def test_angle_grace_then_exit(self):
        self.tick()
        self.assertIsInstance(self.tick(10.1,Pose2D(-.35,0,math.radians(69))),KickIntent)
        self.assertIsInstance(self.tick(10.2,Pose2D(-.35,0,math.radians(71))),KickIntent)
        self.assertNotIsInstance(self.tick(10.5,Pose2D(-.35,0,math.radians(71))),KickIntent)
        self.assertEqual(self.p.attack_kick.last_exit,'misaligned')

    def test_ball_behind_has_longer_grace(self):
        self.tick()
        pose=Pose2D(.35,0,0)
        self.assertIsInstance(self.tick(10.1,pose),KickIntent)
        self.assertIsInstance(self.tick(10.59,pose),KickIntent)
        self.assertNotIsInstance(self.tick(10.6,pose),KickIntent)
        self.assertEqual(self.p.attack_kick.last_exit,'ball_behind')

    def test_multiple_grace_conditions_are_independent(self):
        self.tick()
        self.tick(10.1,Pose2D(-1.6,0,0))
        self.tick(10.2,Pose2D(-1.6,0,math.radians(71)))
        self.assertEqual(len(self.p.attack_kick.diagnostics['exit_grace']),2)
        self.assertIsInstance(self.tick(10.3,Pose2D(-1.4,0,math.radians(71))),KickIntent)
        self.assertNotIsInstance(self.tick(10.5,Pose2D(-1.4,0,math.radians(71))),KickIntent)
        self.assertEqual(self.p.attack_kick.last_exit,'misaligned')

    def test_safety_cancels_immediately_during_grace(self):
        self.tick()
        self.tick(10.1,Pose2D(-1.6,0,0))
        self.ctx=replace(self.ctx,opponents={2:RobotState(2,Pose2D(-.8,0))})
        self.assertNotIsInstance(self.tick(10.11),KickIntent)
        self.assertEqual(self.p.attack_kick.last_exit,'approach_blocked')
        self.assertFalse(self.p.is_kicking)
        self.assertEqual(self.p.attack_kick.diagnostics['exit_grace'],[])

    def test_phase_change_and_clock_reset_clear_grace(self):
        self.tick()
        self.tick(10.1,Pose2D(-1.6,0,0))
        self.ctx=replace(self.ctx,game=replace(self.ctx.game,secondary_time=10))
        self.assertNotIsInstance(self.tick(10.11),KickIntent)
        self.assertEqual(self.p.attack_kick._exceeded_since,{})
        self.tick(10.7,Pose2D(-.35,0,0))
        self.tick(10.8,Pose2D(-1.6,0,0))
        self.assertNotIsInstance(self.tick(1.),KickIntent)
        self.assertEqual(self.p.attack_kick._exceeded_since,{})

    def test_outside_stage_is_allowed_and_does_not_force_reentry(self):
        self.ctx=replace(self.ctx,ball=BallState(0,4.5,last_seen_at=10))
        self.tick(execute_intent=False)
        self.p.context=self.ctx=replace(self.ctx,teammates={1:RobotState(1,Pose2D(0,4.85,-math.pi/2))})
        self.assertIsInstance(self.p.attack((0.,0.)),KickIntent)

    def test_timeout_cooldown_and_pause_do_not_refresh_budget(self):
        self.tick()
        for _ in range(3):
            self.assertIsInstance(self.tick(14.8),KickIntent)
        self.assertAlmostEqual(self.p.attack_kick.diagnostics['remaining_s'],.2)
        self.assertNotIsInstance(self.tick(15.),KickIntent)
        self.assertEqual(self.p.attack_kick.last_exit,'timeout')
        self.assertFalse(self.p.is_kicking)
        self.assertNotIsInstance(self.tick(15.49),KickIntent)
        self.assertIsInstance(self.tick(15.51),KickIntent)
        self.assertAlmostEqual(self.p.attack_kick.diagnostics['elapsed_s'],0.)

    def test_clock_rollback_cancels_old_request(self):
        self.tick()
        self.assertNotIsInstance(self.tick(1.),KickIntent)
        self.assertFalse(self.p.is_kicking)
        self.assertEqual(self.p.attack_kick.last_exit,'clock_reset')

    def test_phase_change_cancels_even_if_still_attacker(self):
        self.tick()
        self.ctx=replace(self.ctx, game=replace(self.ctx.game,secondary_time=10))
        self.assertNotIsInstance(self.tick(10.1),KickIntent)
        self.assertEqual(self.p.attack_kick.last_exit,'phase_changed')

    def test_role_change_clears_session(self):
        self.tick()
        intent=self.p.decide(RoleTask(Role.STAY))
        execute(self.ctx,[self.p],{1:intent})
        self.assertFalse(self.p.is_kicking)
        self.assertIsNone(self.p.attack_kick.since)
        self.assertEqual(self.p.attack_kick.last_exit,'role_changed')

    def test_stale_or_missing_observation_exits(self):
        self.tick()
        self.assertIsInstance(self.tick(10.3,ball=BallState(0,0,last_seen_at=10)),StopIntent)
        self.assertFalse(self.p.is_kicking)
        self.assertEqual(self.p.attack_kick.last_exit,'observation_invalid')
        self.ctx=replace(self.ctx,ball=None)
        self.p.context=self.ctx
        self.assertIsInstance(self.p.attack(),StopIntent)

    def test_target_disappears_exits(self):
        self.tick()
        intent=self.p.attack(3)
        execute(self.ctx,[self.p],{1:intent})
        self.assertFalse(self.p.is_kicking)
        self.assertEqual(self.p.attack_kick.last_exit,'target_unavailable')

    def test_observed_and_compensated_ball_must_both_qualify(self):
        self.tick(execute_intent=False)
        self.p.attack_kick.since=None
        self.p.motion.predict_ball_position.return_value=(.5,0)
        self.assertNotIsInstance(self.p.attack(),KickIntent)

    def test_blocked_approach_does_not_gain_new_fallback(self):
        self.ctx=replace(self.ctx,ball=BallState(6.7,1.3,last_seen_at=10))
        intent=self.tick(pose=Pose2D(7.1,1.3,math.pi))
        self.assertNotIsInstance(intent,KickIntent)
        self.backend.kick.assert_not_called()

    def test_backend_rejection_is_not_reported_as_accepted(self):
        self.backend.kick.return_value=False
        self.backend.last_kick_error='backend_error'
        self.tick()
        self.assertFalse(self.p.is_kicking)
        self.assertIsNone(self.p.controller.kick_observation)
        self.assertEqual(self.p.controller.kick_control['state'],'failed')
        self.assertNotIsInstance(self.tick(10.1),KickIntent)
        self.assertEqual(self.p.attack_kick.last_exit,'backend_error')

    def test_telemetry_contains_budget_and_execution_separately(self):
        self.tick()
        from src.utils.ball_motion import BallMotion
        self.p.motion=BallMotion()
        self.p.motion.update(self.ctx)
        p=make_snapshot(self.ctx,[self.p],SimpleNamespace())['players'][0]
        self.assertTrue(p['kick_control']['accepted'])
        self.assertEqual(p['auto_kick']['remaining_s'],5.0)
        self.assertEqual(p['auto_kick']['state'],'requested')


class BackendFailureTests(unittest.TestCase):
    def backend(self):
        # 仅载入仓库中的真实适配类，跳过 Docker-only SDK 导入与构造。
        path=Path(__file__).parents[1]/'src/framework/robot_backend.py'
        tree=ast.parse(path.read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='RobotBackend')
        module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),cls],type_ignores=[])
        ns={'_log':logging.getLogger('backend_test')}
        exec(compile(ast.fix_missing_locations(module),str(path),'exec'),ns)
        b=ns['RobotBackend'].__new__(ns['RobotBackend'])
        b._mode='walk';b._kicking=False;b._player_id=1;b._kick_manager=Mock();b._robot=Mock()
        return b

    def test_update_failure_disables_started_manager(self):
        for method in ('start','update_command','update_ball'):
            with self.subTest(method=method):
                b=self.backend()
                getattr(b._kick_manager,method).side_effect=RuntimeError('injected')
                self.assertFalse(b.kick(0,5,.35,0))
                b._kick_manager.stop.assert_called_once()
                self.assertFalse(b._kicking)

    def test_failed_release_retries_and_does_not_allow_manual_velocity(self):
        b=self.backend();self.assertTrue(b.kick(0,5,.35,0))
        b._kick_manager.stop.side_effect=[RuntimeError('injected'),None]
        self.assertFalse(b.release_kick())
        self.assertTrue(b._kicking)
        b.set_velocity(1,0,0)
        b._robot.set_velocity.assert_not_called()
        self.assertTrue(b.release_kick())
        self.assertFalse(b._kicking)
        self.assertEqual(b._kick_manager.stop.call_count,2)
