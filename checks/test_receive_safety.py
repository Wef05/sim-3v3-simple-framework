"""接球制动、跨帧恢复与执行限速回归。"""
import math
import unittest
from dataclasses import replace
from src import receiving as R, coordination as C
from src.intents import MoveIntent, KickIntent, KickTarget, StopIntent
from checks import test_passing as passing_fixture
from checks.test_navigation import context, controller
from src.utils.navigation import NavigationFrame, MotionTracker


class ReceiveSafetyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = passing_fixture.PassingTests()
        self.fixture.setUp()
        self.fixture.receive()
        self.ctx = self.fixture.ctx
        self.player = self.fixture.players[1]
        self.player.pose = replace(self.player.pose, x=1., y=0., theta=math.pi)
        self.player.teammate_motion.velocities[3] = (0., 0.)
        self.state = R.Reception()

    def ball(self, x, time=2., velocity=(0., 0.)):
        self.fixture.motion.ball = (x, 0.)
        self.fixture.motion.velocity = velocity
        self.ctx = replace(self.ctx, now=time, ball=replace(self.ctx.ball,x=x,y=0.,last_seen_at=time))

    def test_braking_grows_with_observed_speed(self):
        self.ball(0.)
        self.player.ball_intercept = dict(target=(0.,0.),score_s=.1)
        R.receive(self.ctx,self.player,self.state)
        slow=self.state.brake_distance
        self.player.teammate_motion.velocities[3]=(-1.5,0.)
        intent,_=R.receive(self.ctx,self.player,self.state)
        self.assertGreater(self.state.brake_distance,slow)
        self.assertLessEqual(intent.max_speed,1.)
        self.assertGreater(intent.target[0],0.)

    def test_near_invalid_prediction_buffers_then_times_out(self):
        self.ball(.7)
        self.player.ball_intercept=dict(target=(0.,-9.),score_s=9.)
        intent,outcome=R.receive(self.ctx,self.player,self.state)
        self.assertIsNone(outcome)
        self.assertEqual(intent.max_speed,0.)
        self.player.teammate_motion.velocities[3]=(2.,0.)
        self.ball(.7,3.3)
        self.assertEqual(R.receive(self.ctx,self.player,self.state)[1],'buffer_timeout')

    def test_repeated_observation_cannot_confirm(self):
        self.ball(.7)
        R.receive(self.ctx,self.player,self.state)
        self.ctx=replace(self.ctx,now=2.5)
        self.assertIsNone(R.receive(self.ctx,self.player,self.state)[1])

    def test_stable_receive_with_invalid_far_prediction(self):
        self.ball(.7)
        self.player.ball_intercept=dict(target=None)
        R.receive(self.ctx,self.player,self.state)
        self.ball(.7,2.25)
        self.assertEqual(R.receive(self.ctx,self.player,self.state)[1],'received')

    def test_ball_escaping_near_buffer_cancels(self):
        self.ball(.7)
        R.receive(self.ctx,self.player,self.state)
        self.ball(-.1,2.1,(-3.,0.))
        self.assertEqual(R.receive(self.ctx,self.player,self.state)[1],'ball_escaped')

    def protection(self,until=0.):
        return R.ExitProtection(3,1,C._phase_key(self.ctx),until,'cancelled')

    def test_expired_recovery_does_not_release_misaligned_shot(self):
        self.ball(1.3)
        p=self.protection()
        shot=KickIntent(KickTarget.goal())
        for time in (2.,3.,5.):
            self.ball(1.3,time)
            intent,allowed=R.protect_exit(self.ctx,self.player,p,shot)
            self.assertFalse(allowed)
            self.assertNotIsInstance(intent,KickIntent)
        self.assertEqual(p.reason,'heading_unaligned')

    def test_good_pose_requires_fresh_continuous_hold(self):
        self.ball(1.3)
        self.player.pose=replace(self.player.pose,theta=0.)
        p=self.protection();shot=KickIntent(KickTarget.goal())
        self.assertFalse(R.protect_exit(self.ctx,self.player,p,shot)[1])
        self.ctx=replace(self.ctx,now=2.5)
        self.assertFalse(R.protect_exit(self.ctx,self.player,p,shot)[1])
        self.ball(1.3,2.6)
        self.assertTrue(R.protect_exit(self.ctx,self.player,p,shot)[1])

    def test_recovery_blocks_pass_as_well_as_shot(self):
        self.ball(.7)
        p=self.protection(4.)
        for shot in (KickIntent(KickTarget.goal()),KickIntent(KickTarget.position((3.,1.)),'pass')):
            self.assertNotIsInstance(R.protect_exit(self.ctx,self.player,p,shot)[0],KickIntent)

    def test_cancel_protection_survives_next_frame_role_change(self):
        f=self.fixture
        f.players[1].ball_intercept=dict(target=None)
        f.players[1].pose=replace(f.players[1].pose,x=3.)
        f.step(1.8)
        self.assertIsNone(f.store.passing.active)
        self.assertIn(3,f.store.passing.exit_protections)
        f.preliminary[3]=KickIntent(KickTarget.goal())
        f.players[1].action='attack'
        self.assertNotIsInstance(f.step(1.9)[3],KickIntent)
        self.assertNotIsInstance(f.step(4.)[3],KickIntent)

    def test_rewind_clears_protection(self):
        self.fixture.step(.5)
        self.assertFalse(self.fixture.store.passing.exit_protections)

    def test_new_restart_clears_protection(self):
        from src.framework.types import GameState
        f=self.fixture
        f.ctx=replace(f.ctx,game=replace(f.ctx.game,state=GameState.READY))
        f.step(2.)
        self.assertFalse(f.store.passing.exit_protections)

    def test_execution_caps_translation_and_arrival_rotation(self):
        ctx=context();c=controller(ctx);frame=NavigationFrame(ctx,MotionTracker())
        cmd,_=c.plan_walk(MoveIntent((2.,0.),max_speed=.12,max_yaw_rate=.3),frame)
        self.assertLessEqual(math.hypot(*cmd[:2]),.120001)
        pos=(c.pose.x,c.pose.y)
        cmd,_=c.plan_walk(MoveIntent(pos,face=math.pi/2,max_speed=0.,max_yaw_rate=.3),frame)
        self.assertLessEqual(abs(cmd[2]),.300001)
        self.assertEqual(cmd[:2],(0.,0.))

    def test_invalid_speed_limit_stops(self):
        ctx=context();c=controller(ctx)
        cmd,_=c.plan_walk(MoveIntent((2.,0.),max_speed=float('nan')),NavigationFrame(ctx,MotionTracker()))
        self.assertEqual(cmd,(0.,0.,0.))

if __name__=='__main__':unittest.main()
