import json
import math
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from checks.test_navigation import context
from src.framework.types import GameState
from src.framework.config import SoccerConfig
from src.player import Player
from src.motion_calibration import CalibrationHost, trial_plan, timed_command


class CalibrationTests(unittest.TestCase):
    def test_near_home_lateral_pulse_and_continuous_longitudinal_command(self):
        p = Player(1, SoccerConfig(), None)
        p.controller._mode = 'walk'
        h = CalibrationHost(); h.run = 'test'; h.cfg = {'player_id': 1}
        h.trials = trial_plan('validation'); h.index = 0
        h.started = None; h.home_started = 4.
        h.status = {'state': 'homing'}
        h.next_poll = h.next_status = float('inf')
        with tempfile.TemporaryFile(mode='w+') as f, patch.object(p.controller, 'set_velocity') as send:
            h.file = f
            for now, expected in ((4.1, (-.5, -.5, 0.)), (4.5, (-.5, 0., 0.))):
                ctx = context(((-1.85, .15, 0.),), now=now)
                ctx = replace(ctx, game=replace(ctx.game, state=GameState.FINISHED))
                p.context = ctx
                h.tick(ctx, [p])
                self.assertEqual(send.call_args.args, expected)

    def test_homing_corrects_heading_inside_navigation_deadband(self):
        p = Player(1, SoccerConfig(), None)
        p.controller._mode = 'walk'
        ctx = context(((-2., 0., .09),), now=5.)
        ctx = replace(ctx, game=replace(ctx.game, state=GameState.FINISHED))
        p.context = ctx
        h = CalibrationHost()
        h.run = 'test'; h.cfg = {'player_id': 1}
        h.trials = trial_plan('validation'); h.index = 0
        h.started = None; h.home_started = 4.
        h.status = {'state': 'homing'}
        h.next_poll = h.next_status = float('inf')
        with tempfile.TemporaryFile(mode='w+') as f, patch.object(p.controller, 'set_velocity') as send:
            h.file = f
            h.tick(ctx, [p])
            self.assertEqual(send.call_args.args, (0., 0., -.18))

    def test_balanced_schedule_and_phase_edges(self):
        baseline = trial_plan('baseline')
        self.assertEqual(len(baseline), 42)
        counts = {}
        for t in baseline:
            c = tuple(t['segments'][0][1]); counts[c] = counts.get(c, 0)+1
        self.assertEqual(set(counts.values()), {3})
        self.assertEqual(len(trial_plan('validation')), 12)
        t = baseline[0]
        self.assertEqual([timed_command(t, s)[0] for s in [0, 1.99, 2, 7.99, 8, 9.99, 10]],
                         ['pre', 'pre', 'drive:0', 'drive:0', 'stop', 'stop', 'done'])
        with self.assertRaises(ValueError): trial_plan('validation', lateral=3)

    def test_referee_guard_stops_and_no_zero_injection_during_drive(self):
        p = Player(1, SoccerConfig(), None); p.controller._mode = 'walk'
        ctx = context(((-2., 0., 0.),), now=5)
        ctx = replace(ctx, game=replace(ctx.game, state=GameState.FINISHED))
        p.context = ctx
        h = CalibrationHost(); h.run='test'; h.cfg={'player_id':1}
        h.trials=trial_plan('baseline'); h.index=0; h.started=2.; h.home_started=0
        h.status={'state':'drive:0'}; h.next_poll=float('inf'); h.next_status=float('inf')
        with tempfile.TemporaryFile(mode='w+') as f, patch.object(p.controller, 'set_velocity') as send:
            h.file=f; h.tick(ctx,[p])
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args, (0., .2, 0.))
            ctx=replace(ctx, game=replace(ctx.game,state=GameState.PLAYING));p.context=ctx
            h.tick(ctx,[p]);self.assertEqual(h.status['state'],'aborted')
            self.assertEqual(send.call_args.args,(0.,0.,0.))
            f.seek(0); record=json.loads(f.readline())
            self.assertIsNone(record['sdk_acknowledged'])
            self.assertEqual(record['referee_state'],'finished')

    def test_entire_schedule_runs_all_trials_once(self):
        p=Player(1,SoccerConfig(),None);p.controller._mode='walk'
        h=CalibrationHost();h.run='test';h.cfg={'player_id':1};h.trials=trial_plan('baseline')
        h.index=0;h.started=None;h.home_started=1.;h.status={'state':'homing'}
        h.next_poll=float('inf');h.next_status=float('inf')
        with tempfile.TemporaryFile(mode='w+') as f:
            h.file=f
            for i in range(9000):
                ctx=context(((-2.,0.,0.),),now=1+i*.05)
                ctx=replace(ctx,game=replace(ctx.game,state=GameState.FINISHED));p.context=ctx
                h.tick(ctx,[p])
                if h.status['state']=='done':break
            self.assertEqual(h.status['state'],'done')
            f.seek(0);rows=[json.loads(line) for line in f]
            active={r['trial'] for r in rows if r['phase']=='drive:0'}
            self.assertEqual(active,set(range(1,43)))

    def test_next_trial_does_not_end_the_run(self):
        p=Player(1,SoccerConfig(),None);p.controller._mode='walk'
        ctx=context(((-2.,0.,0.),),now=12)
        ctx=replace(ctx,game=replace(ctx.game,state=GameState.FINISHED));p.context=ctx
        h=CalibrationHost();h.run='test';h.cfg={'player_id':1};h.trials=trial_plan('baseline')
        h.index=0;h.started=1.;h.home_started=0.;h.status={'state':'stop'}
        h.next_poll=float('inf');h.next_status=float('inf')
        with tempfile.TemporaryFile(mode='w+') as f:
            h.file=f;h.tick(ctx,[p])
            self.assertEqual(h.index,1);self.assertEqual(h.status['state'],'homing')
            self.assertIsNone(h.started)
            h.index=41;h.started=1.;h.status['state']='stop'
            h.tick(ctx,[p]);self.assertEqual(h.status['state'],'done')

    def test_trial_limit_and_disarm_are_fail_safe(self):
        p=Player(1,SoccerConfig(),None);p.controller._mode='walk'
        ctx=context(((-2.,3.5,0.),),now=5)
        ctx=replace(ctx,game=replace(ctx.game,state=GameState.FINISHED));p.context=ctx
        h=CalibrationHost();h.run='test';h.cfg={'player_id':1};h.trials=trial_plan('baseline')
        h.index=0;h.started=2;h.home_started=0;h.status={'state':'drive:0'}
        h.next_poll=float('inf');h.next_status=float('inf')
        with patch.object(p.controller,'set_velocity') as send:
            h.tick(ctx,[p]);self.assertEqual(h.status['reason'],'test_area_limit')
            self.assertEqual(send.call_args.args,(0.,0.,0.))
        h.status={'state':'drive:0'};h.next_poll=0
        with tempfile.TemporaryDirectory() as td, patch('src.motion_calibration.ARM',Path(td)/'missing'):
            h.tick(ctx,[p]);self.assertEqual(h.status['reason'],'disarmed')



class MeasurementTests(unittest.TestCase):
    def test_body_projection_and_angle_wrap(self):
        from tools.motion_calibration.analyze import velocities
        rows=[];x=y=0.;theta=3.
        for i in range(150):
            if i:
                mid=theta+.01;x+=-.4*math.sin(mid)*.02;y+=.4*math.cos(mid)*.02;theta+=.02
            rows.append(dict(observed_at=i*.02,pose=dict(x=x,y=y,theta=math.atan2(math.sin(theta),math.cos(theta)))))
        result=velocities(rows)[-1]['velocity']
        self.assertAlmostEqual(result[0],0.,places=8)
        self.assertAlmostEqual(result[1],.4,places=8)
        self.assertAlmostEqual(result[2],1.,places=8)

    def test_duplicate_observations_do_not_create_spikes(self):
        from tools.motion_calibration.analyze import velocities
        rows=[dict(observed_at=i*.02,pose=dict(x=0.,y=i*.01,theta=0.)) for i in range(60)]
        doubled=[r for r in rows for _ in range(2)]
        self.assertEqual(velocities(rows),velocities(doubled))

if __name__=='__main__': unittest.main()
