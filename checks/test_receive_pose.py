"""接球姿态和整段争抢预算回归：几何、时间及生命周期，不模拟触球结果。"""
import json
import math
import unittest
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import patch

from src import coordination as C, param as P, receiving
from src.framework.types import (Context, ADULT_FIELD_DIMENSIONS, BallState,
    Pose2D, RobotState, GameControlState, GameState)
from src.utils import receive_pose as R
from src.utils.kick_lane import point_segment
from src.intents import StopIntent, KickIntent, MoveIntent
from checks import test_passing as fixtures


class ReceivePoseTests(unittest.TestCase):
    def setUp(self):
        self.passer = SimpleNamespace(id=1, pose=Pose2D(-.35, 0., math.pi/2))
        self.receiver = SimpleNamespace(id=3, pose=Pose2D(-.6, 2., -math.pi/2))
        self.anchor = (-.6, 2.)
        self.ctx = Context(1., .03, 1, ADULT_FIELD_DIMENSIONS,
            ball=BallState(0., 0., last_seen_at=1.),
            game=GameControlState(state=GameState.PLAYING, kicking_team=1, secondary_time=10),
            teammates={p.id: RobotState(p.id, p.pose, last_seen_at=1.) for p in (self.passer,self.receiver)})

    def choose(self, kickoff=True):
        return R.choose(self.ctx, self.passer, self.receiver, self.anchor, self.anchor,
                        kickoff, C._lane_safe, C._prepare_route)

    def test_kickoff_prefers_upper_left_and_separates_ball_and_robot(self):
        result=self.choose();p=result.selected
        self.assertIsNotNone(p)
        self.assertLess(p.contact[0], self.anchor[0])
        self.assertGreater(p.contact[1], self.anchor[1])
        self.assertTrue(p.preferred_region)
        self.assertGreaterEqual(math.dist(p.contact,p.waiting),.29)
        self.assertGreaterEqual(point_segment(p.waiting,(0.,0.),p.contact),P.PASS_POSE_BALL_CLEARANCE_M)
        self.assertLess(p.behind[0],p.contact[0])
        self.assertLessEqual(len(result.candidates),28)
        json.dumps(asdict(result),allow_nan=False)

    def test_upper_left_blocked_falls_back_with_reason(self):
        lane=lambda ctx,p,r,t: not (t[0]<self.anchor[0] and t[1]>self.anchor[1]) and C._lane_safe(ctx,p,r,t)
        result=R.choose(self.ctx,self.passer,self.receiver,self.anchor,self.anchor,True,lane,C._prepare_route)
        self.assertIsNotNone(result.selected)
        self.assertFalse(result.selected.preferred_region)
        self.assertEqual(result.reason,'upper_left_unavailable')

    def test_all_unsafe_never_fabricates_plan(self):
        self.ctx=replace(self.ctx,opponents={1:RobotState(1,Pose2D(.1,.1),last_seen_at=1.)})
        result=self.choose()
        self.assertIsNone(result.selected)
        self.assertEqual(result.reason,'no_safe_pose')

    def test_unknown_or_stale_robot_rejects(self):
        for robot in (RobotState(1),RobotState(1,Pose2D(6.,0.),last_seen_at=0.)):
            self.ctx=replace(self.ctx,opponents={1:robot})
            self.assertEqual(self.choose().reason,'robot_observation_unavailable')

    def test_waiting_route_does_not_cross_ball(self):
        self.ctx=replace(self.ctx,teammates={3:self.ctx.teammates[3]})
        path=R.route(self.ctx,3,(.5,0.),(-.3,0.),(0.,0.),0.)
        self.assertIsNotNone(path)
        self.assertGreater(len(path),2)
        for a,b in zip(path,path[1:]):
            self.assertGreaterEqual(point_segment((0.,0.),a,b),P.PASS_POSE_BALL_CLEARANCE_M)

    def test_close_ball_overlap_can_only_escape(self):
        path=R.route(self.ctx,3,(.1,0.),(.3,0.),(0.,0.),0.)
        self.assertEqual(path,[(.1,0.),(.3,0.)])
        path=R.route(self.ctx,3,(.1,0.),(-.3,0.),(0.,0.),0.)
        self.assertNotEqual(path,[(.1,0.),(-.3,0.)])

    def test_field_margin_and_blocked_behind_point(self):
        self.assertFalse(R.inside(self.ctx,(0.,self.ctx.field.width/2-.1)))
        contact=(-1.,2.5);attack=(1.,2.5)
        behind=(contact[0]-.3,contact[1])
        self.ctx=replace(self.ctx,opponents={1:RobotState(1,Pose2D(*behind),last_seen_at=1.)})
        pose,_=R.evaluate(self.ctx,self.passer,self.receiver,contact,attack,self.anchor,True,C._prepare_route,1.)
        self.assertIsNone(pose)

    def test_prepare_delay_reduces_all_race_margins(self):
        self.ctx=replace(self.ctx,opponents={1:RobotState(1,Pose2D(6.,4.),last_seen_at=1.)})
        fast=R.race(self.ctx,(0.,0.),(-1.,3.),.5,.5)
        slow=R.race(self.ctx,(0.,0.),(-1.,3.),2.5,.5)
        for a,b in zip(fast,slow):self.assertAlmostEqual(a-b,2.)

    def test_post_receive_adjustment_consumes_contest_window(self):
        self.ctx=replace(self.ctx,opponents={1:RobotState(1,Pose2D(6.,4.),last_seen_at=1.)})
        fast=R.race(self.ctx,(0.,0.),(-1.,3.),.1,.1)
        slow=R.race(self.ctx,(0.,0.),(-1.,3.),.1,4.)
        self.assertGreater(fast[2],P.PASS_RACE_MARGIN_S)
        self.assertLess(slow[2],P.PASS_RACE_MARGIN_S)
        self.assertEqual(fast[:2],slow[:2])

    def test_opponent_heading_does_not_create_false_time(self):
        robot=RobotState(1,Pose2D(3.,3.,0.),last_seen_at=1.)
        self.ctx=replace(self.ctx,opponents={1:robot});first=R.enemy_time(self.ctx,(0.,0.))
        self.ctx=replace(self.ctx,opponents={1:replace(robot,pose=Pose2D(3.,3.,math.pi))})
        self.assertEqual(first,R.enemy_time(self.ctx,(0.,0.)))

    def test_anchor_is_fixed_until_phase_changes(self):
        state=C.TeamPassing()
        C._choose_pose(self.ctx,self.passer,self.receiver,self.anchor,state)
        anchor=state.kickoff_anchor
        self.receiver.pose=Pose2D(-1.,2.5,-math.pi/2)
        C._choose_pose(self.ctx,self.passer,self.receiver,self.anchor,state)
        self.assertEqual(anchor,state.kickoff_anchor)
        self.assertEqual(anchor,state.selection.anchor)

    def test_goal_heading_changes_behind_position(self):
        pose=self.choose().selected
        vx,vy=pose.contact[0]-pose.behind[0],pose.contact[1]-pose.behind[1]
        self.assertAlmostEqual(math.atan2(vy,vx),pose.heading)

    def test_old_target_position_is_not_ready_position(self):
        f=fixtures.PassingTests();f.setUp();plan=f.store.passing.active
        f.players[1].pose=Pose2D(*plan.target,plan.pose_plan.receive_heading)
        f.step(1.1);f.step(1.4)
        self.assertFalse(plan.receiver_ready)
        self.assertEqual(plan.stage,'prepare')

    def test_exit_plan_prepares_without_synthesizing_kick(self):
        f=fixtures.PassingTests();f.setUp();plan,_=f.receive();player=f.players[1]
        player.pose=Pose2D(2.7,0.,0.)
        f.motion.ball=(3.,0.);f.motion.velocity=(0.,0.)
        ctx=replace(f.ctx,now=2.,ball=BallState(3.,0.,last_seen_at=2.))
        state=receiving.ExitProtection(3,1,C._phase_key(ctx),0.,'received',planned_attack=(5.,0.))
        intent,allowed=receiving.protect_exit(ctx,player,state,StopIntent())
        self.assertFalse(allowed);self.assertNotIsInstance(intent,KickIntent)
        self.assertIsInstance(intent,MoveIntent)
        ctx=replace(ctx,now=2.3,ball=replace(ctx.ball,last_seen_at=2.3))
        intent,allowed=receiving.protect_exit(ctx,player,state,StopIntent())
        self.assertFalse(allowed);self.assertIsInstance(intent,StopIntent)
        self.assertEqual(state.phase,'attack_ready')

    def test_waiting_side_is_frozen_on_revalidation(self):
        selected=self.choose().selected
        pose,_=R.evaluate(self.ctx,self.passer,self.receiver,selected.contact,selected.attack_target,
            self.anchor,True,C._prepare_route,1.1,waiting_override=selected.waiting)
        self.assertIsNotNone(pose)
        self.assertEqual(pose.waiting,selected.waiting)


if __name__=='__main__':unittest.main()
