"""历史开局几何的确定性回归；不将离线姿态替换视为实机到位。"""
import math
import unittest
from dataclasses import replace
from types import SimpleNamespace as NS
from src import coordination as C, param as P
from src.utils import receive_pose as R
from src.framework.types import Context, ADULT_FIELD_DIMENSIONS, BallState, RobotState, Pose2D, GameControlState, GameState
from src.intents import MoveIntent, KickIntent, KickTarget, StopIntent, PassProposal, Role, RoleTask
from checks.test_navigation import context, frame, controller
from checks.test_passing import Motion


def opening():
    poses={1:Pose2D(-1.490565,-.114687,.093576),3:Pose2D(-.582299,3.40467,.081699)}
    players=[NS(id=i,pose=p,mode='walk',is_fallen=False,is_penalized=False,action='attack',
        motion=Motion(),pass_proposal=PassProposal(3,'kickoff',(-.582299,3.40467)) if i==1 else None) for i,p in poses.items()]
    ctx=Context(55.,.03,1,ADULT_FIELD_DIMENSIONS,ball=BallState(0.,0.,last_seen_at=55.),
        teammates={i:RobotState(i,p,last_seen_at=55.) for i,p in poses.items()},
        opponents={i:RobotState(i,Pose2D(*p),last_seen_at=55.) for i,p in enumerate(((2.751059,-.00914),(2.73882,-2.65149),(5.74485,.0083)),1)},
        game=GameControlState(state=GameState.PLAYING,kicking_team=1,secondary_time=10,last_seen_at=55.))
    return ctx,players


class ReentryTests(unittest.TestCase):
    def test_history_noise_allows_inward_not_outward(self):
        for y,v in ((4.9836,.000026145),(4.9851,.000027877),(5.0047,.000022798), (5.,.00033)):
            ctx=context(((0.,y),));f=frame(ctx);f.tracker.velocities['team',1]=(0.,v)
            intent=MoveIntent((0.,0.))
            self.assertIsNone(f.check(1,intent,(0.,-.4,0.)))
            self.assertEqual(f.check(1,intent,(0.,.01,0.)),'field_boundary')
            c=controller(ctx);command,_=c.plan_walk(intent,f)
            self.assertLess(command[1],0.)
            self.assertTrue(c.navigation_status['returning'])

    def test_excessive_inertia_cannot_be_hidden(self):
        ctx=context(((0.,5.),));f=frame(ctx);f.tracker.velocities['team',1]=(0.,2.)
        self.assertEqual(f.check(1,MoveIntent((0.,0.)),(0.,-.4,0.)),'field_boundary')

    def test_corner_requires_both_axes_inward(self):
        ctx=context(((7.,5.),));f=frame(ctx)
        self.assertEqual(f.check(1,MoveIntent((0.,0.)),(-.4,0.,0.)),'field_boundary')
        self.assertIsNone(f.check(1,MoveIntent((0.,0.)),(-.4,-.4,0.)))

    def test_reentry_retains_robot_collision_checks(self):
        ctx=context(((0.,5.),),opponents=((0.,4.4),));f=frame(ctx)
        self.assertIsNotNone(f.check(1,MoveIntent((0.,0.)),(0.,-.6,0.)))

    def test_stop_clears_reentry(self):
        ctx=context(((0.,5.),));c=controller(ctx);c.plan_walk(MoveIntent((0.,0.)),frame(ctx))
        c.context=replace(ctx,game=replace(ctx.game,stopped=True))
        command,_=c.plan_walk(MoveIntent((0.,0.)),frame(c.context))
        self.assertEqual(command,(0.,0.,0.));self.assertFalse(c._nav_reentry)


class PreparationTests(unittest.TestCase):
    def test_history_preposition_then_commit_same_left_upper_point(self):
        ctx,players=opening();s=C.TeamPassing()
        self.assertIsNone(C._choose_pose(ctx,*players,(-.582299,3.40467),s))
        p=s.preparation;self.assertIsNotNone(p);self.assertTrue(p.preferred_region)
        self.assertLess(p.contact[0],-.582299);self.assertGreater(p.contact[1],3.40467)
        self.assertGreater(p.enemy_distance,R.nearest_enemy(ctx,(-.582299,3.40467)))
        stage=C._prepare_route(ctx,players[0],p.contact)
        players[0].pose=Pose2D(*stage[0],stage[2]);players[1].pose=Pose2D(*p.waiting,p.receive_heading)
        ctx=replace(ctx,teammates={p.id:replace(ctx.teammates[p.id],pose=p.pose) for p in players})
        committed=C._choose_pose(ctx,*players,p.contact,s)
        self.assertIsNotNone(committed);self.assertFalse(committed.provisional)
        self.assertEqual(committed.contact,p.contact);self.assertEqual(committed.waiting,p.waiting)
        self.assertGreaterEqual(committed.receive_margin_s,P.PASS_RACE_MARGIN_S)

    def test_preposition_is_movement_never_kick(self):
        ctx,players=opening();store=NS(passing=C.TeamPassing())
        result=C.coordinate_pass(ctx,players,{1:RoleTask(Role.ATTACKER),3:RoleTask(Role.FREEMAN)},
            {1:KickIntent(KickTarget.teammate(3)),3:StopIntent()},store)
        self.assertIsNone(store.passing.active)
        self.assertTrue(all(isinstance(result[i],MoveIntent) for i in (1,3)))
        self.assertTrue(store.passing.preparation.provisional)

    def test_preparation_expires(self):
        ctx,players=opening();s=C.TeamPassing();C._choose_pose(ctx,*players,(-.582299,3.40467),s)
        ctx=replace(ctx,now=ctx.now+P.PASS_PREPARE_TIMEOUT_S+.01)
        self.assertIsNone(C._choose_pose(ctx,*players,(-.582299,3.40467),s))
        self.assertTrue(s.preparation_expired);self.assertIsNone(s.preparation)

    def test_protection_requires_fresh_legal_state(self):
        ctx,_=opening();self.assertEqual(R.kickoff_window(ctx),9.)
        self.assertEqual(R.kickoff_window(replace(ctx,game=replace(ctx.game,last_seen_at=54.))),0.)
        self.assertEqual(R.kickoff_window(replace(ctx,game=replace(ctx.game,secondary_time=1))),0.)
        bad=replace(ctx,opponents={1:RobotState(1,Pose2D(-1.,0.),last_seen_at=55.)})
        self.assertEqual(R.contest_time(bad,(-2.,3.),2.),R.enemy_time(bad,(-2.,3.)))

    def test_opponents_move_during_preparation(self):
        ctx,_=opening();point=(-2.,3.5)
        remaining=R.contest_time(ctx,point,2.)-2.
        self.assertLess(remaining,R.enemy_time(ctx,point))
        self.assertGreaterEqual(remaining,0.)

    def test_inside_and_outside_rejection_distinct(self):
        ctx,players=opening();s=C.TeamPassing();C._choose_pose(ctx,*players,(-.582299,3.40467),s)
        outside=[c for c in s.selection.candidates if not R.inside(ctx,c['contact'])]
        self.assertTrue(outside);self.assertTrue(all(c['reason']=='contact_outside_field' for c in outside))


if __name__=='__main__':unittest.main()
