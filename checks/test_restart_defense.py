"""敌方重启阵型、持位和角色切换回归。"""
import math
import unittest
from dataclasses import replace
from types import SimpleNamespace

from src.framework.types import ADULT_FIELD_DIMENSIONS, BallState, Context, GameControlState, GameState, Pose2D, SetPlay
from src.intents import Role, RoleTask, MoveIntent
from src.player import Player, coordinate
from src.role_allocator import assign, init_store
from src.utils.restart_defense import defensive_targets, restart_intent, kickoff_targets, _legal


class RestartDefenseTests(unittest.TestCase):
    def context(self, kind=SetPlay.DIRECT_FREE_KICK, ball=(-2., 1.)):
        return Context(now=1., dt=.03, team_id=1, field=ADULT_FIELD_DIMENSIONS,
                       ball=BallState(*ball) if ball is not None else None,
                       game=GameControlState(state=GameState.PLAYING, kicking_team=2, set_play=kind))

    def players(self):
        return [SimpleNamespace(id=i, pose=Pose2D(-i, 0.), mode='walk',
                                is_penalized=False, is_fallen=False,
                                ball_intercept={'score_s': float(i)}) for i in (1, 2, 3)]

    def test_restart_roles_and_release(self):
        store = SimpleNamespace(); init_store(store)
        players = self.players()
        for kind in SetPlay:
            ctx = self.context(kind)
            if kind == SetPlay.NONE:
                ctx = replace(ctx, game=replace(ctx.game, secondary_time=10))
            tasks = assign(ctx, list(reversed(players)), store)
            expected = Role.OPP_KICKOFF if kind == SetPlay.NONE else Role.OPP_SET_PLAY
            self.assertEqual((tasks[1].role, tasks[3].role), (expected, expected))
            self.assertEqual((tasks[1].slot, tasks[3].slot), (0, 1))
            self.assertEqual(tasks[2].role, Role.GUARD)
            self.assertIsNone(store.normal_attacker)
        ctx = replace(ctx, game=GameControlState(state=GameState.PLAYING))
        tasks = assign(ctx, players, store)
        self.assertEqual(tasks[1].role, Role.ATTACKER)
        self.assertEqual(tasks[3].role, Role.FREEMAN)

    def test_unavailable_does_not_shift_slots(self):
        store = SimpleNamespace(); init_store(store)
        players = self.players(); players[0].is_fallen = True; players[1].is_penalized = True
        tasks = assign(self.context(), players, store)
        self.assertEqual(tasks[1].role, Role.UNAVAILABLE)
        self.assertEqual(tasks[2].role, Role.UNAVAILABLE)
        self.assertEqual(tasks[3].slot, 1)
        self.assertEqual(tasks[3].role, Role.OPP_SET_PLAY)

    def test_all_set_plays_field_edges_and_missing_ball(self):
        for kind in SetPlay:
            for ball in ((-7.,-4.5),(-7.,4.5),(-6.,0.),(-2.,1.),(0.,0.),(7.,4.5),None):
                with self.subTest(kind=kind,ball=ball):
                    ctx=self.context(kind,ball); kickoff=kind==SetPlay.NONE
                    targets=defensive_targets(ctx,ball,kickoff)
                    self.assertTrue(all(p is not None for p in targets))
                    occupied=[]
                    for p in targets:
                        self.assertTrue(_legal(ctx,p,ball,kickoff,kind==SetPlay.PENALTY_KICK,occupied))
                        occupied.append(p)

    def test_side_restarts_mirror(self):
        for kind in (SetPlay.THROW_IN,SetPlay.CORNER_KICK):
            left=defensive_targets(self.context(kind,(-3.,4.)),(-3.,4.),False)
            right=defensive_targets(self.context(kind,(-3.,-4.)),(-3.,-4.),False)
            for a,b in zip(left,right):
                self.assertAlmostEqual(a[0],b[0]); self.assertAlmostEqual(a[1],-b[1])

    def test_small_noise_holds_and_large_motion_replans(self):
        ctx=self.context(); pose=Pose2D(-2.,0.)
        intent,mem,_=restart_intent(ctx,pose,0,False,None)
        noisy=replace(ctx,now=1.1,ball=BallState(-2.,1.02))
        again,mem2,_=restart_intent(noisy,pose,0,False,mem)
        self.assertEqual(intent.target,again.target)
        moved=replace(ctx,now=1.2,ball=BallState(2.,-2.))
        changed,_,_=restart_intent(moved,pose,0,False,mem2)
        self.assertNotEqual(changed.target,intent.target)
        _,reset,_=restart_intent(replace(noisy,now=.2),pose,0,False,mem2)
        self.assertEqual(reset.anchor,(-2.,1.02))

    def test_missing_ball_and_return_to_observation(self):
        ctx=self.context(ball=None)
        intent,mem,label=restart_intent(ctx,Pose2D(0.,0.),0,False,None)
        self.assertIn('no_ball',label); self.assertIsNone(mem.anchor)
        intent2,mem2,_=restart_intent(self.context(),Pose2D(0.,0.),0,False,mem)
        self.assertNotEqual(intent.target,intent2.target); self.assertEqual(mem2.anchor,(-2.,1.))

    def test_hold_faces_ball_and_retains_keepout(self):
        ctx=self.context(); targets=defensive_targets(ctx,(-2.,1.),False)
        pose=Pose2D(*targets[0])
        intent,_,label=restart_intent(ctx,pose,0,False,None)
        self.assertEqual(label,'hold')
        self.assertAlmostEqual(intent.face, math.atan2(1-pose.y,-2-pose.x))
        final=coordinate(ctx,{1:RoleTask(Role.OPP_SET_PLAY)},{1:intent})[1]
        self.assertTrue(final.avoid_ball);self.assertGreaterEqual(final.ball_keepout,ctx.field.circle_radius)

    def test_ready_kickoff_consistency_and_own_shift(self):
        ctx=self.context(SetPlay.NONE,(0.,0.))
        for slot,target in zip((0,2),kickoff_targets(ctx)):
            stub=SimpleNamespace(context=ctx)
            intent=Player.decide(stub,RoleTask(Role.READY,slot=slot,our_kickoff=False))
            self.assertEqual(intent.target,target)
        intent=Player.decide(SimpleNamespace(context=ctx),RoleTask(Role.READY,slot=2,our_kickoff=True))
        old=(-.5,ctx.field.circle_radius+2)
        self.assertAlmostEqual(math.dist(intent.target,old),1.)
        self.assertLess(intent.target[0],old[0]);self.assertGreater(intent.target[1],old[1])

    def test_player_clears_restart_memory_on_stop(self):
        ctx=self.context(); p=SimpleNamespace(context=ctx,pose=Pose2D(-2,0))
        intent=Player.decide(p,RoleTask(Role.OPP_SET_PLAY,slot=0))
        self.assertIsInstance(intent,MoveIntent);self.assertIsNotNone(p._restart_memory)
        Player.decide(p,RoleTask(Role.STAY))
        self.assertIsNone(p._restart_memory)


if __name__=='__main__': unittest.main()
