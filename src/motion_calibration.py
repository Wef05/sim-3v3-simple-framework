"""Isolated motion experiment. No production strategy or capability parameters.

An explicit arm file is required, and only INITIAL/FINISHED referee states are
accepted. Raw records distinguish requested commands from unconfirmed SDK calls.
"""
from dataclasses import asdict
import json
import math
from pathlib import Path
import re
import time

from .utils.geom import normalize_angle

ARM = Path('/tmp/soccer-motion-calibration-arm.json')
STATUS = Path('/tmp/soccer-motion-calibration-status.json')


def trial_plan(stage: str, lateral: float = .5, angular: float = 1.) -> list[dict]:
    """Return the predeclared balanced protocol, with seconds in simulation time."""
    trials = []
    if stage == 'baseline':
        for repeat in range(3):
            for axis, levels in ((1, [.2, .5, 1., 2.]), (2, [.5, 1., 2.])):
                levels = levels[repeat:] + levels[:repeat]
                for level in levels:
                    for sign in ((1, -1) if repeat % 2 == 0 else (-1, 1)):
                        command = [0., 0., 0.]
                        command[axis] = sign*level
                        trials.append(dict(kind='lateral' if axis == 1 else 'turn',
                            repeat=repeat, segments=[[6., command]]))
    elif stage == 'validation':
        if not (0 < lateral <= 2 and 0 < angular <= 2):
            raise ValueError('Validation amplitudes must be in (0, 2]')
        for axis, level in ((1, lateral), (2, angular)):
            for repeat in range(3):
                command = [0., 0., 0.]
                command[axis] = level * (1 if repeat % 2 == 0 else -1)
                trials.append(dict(kind='reversal', repeat=repeat,
                    segments=[[4., command], [4., [-v for v in command]]]))
        for command in ([.5, lateral, 0.], [.5, 0., angular], [0., lateral, angular]):
            for sign in (1, -1):
                trials.append(dict(kind='combined', repeat=0,
                    segments=[[6., [command[0], sign*command[1], sign*command[2]]]]))
    else:
        raise ValueError('Unknown calibration stage')
    for i, trial in enumerate(trials):
        trial['id'] = i+1
    return trials


def timed_command(trial: dict, elapsed: float) -> tuple[str, list[float]]:
    if elapsed < 2.:
        return 'pre', [0., 0., 0.]
    elapsed -= 2.
    for i, (duration, command) in enumerate(trial['segments']):
        if elapsed < duration:
            return f'drive:{i}', command
        elapsed -= duration
    return ('stop' if elapsed < 2. else 'done'), [0., 0., 0.]


class CalibrationHost:
    def __init__(self):
        self.run = None
        self.seen = set()
        self.next_poll = 0.
        self.next_status = 0.
        self.file = None
        self.status = dict(state='unarmed')
        self.history = []

    def abort(self, reason: str) -> None:
        self.status.update(state='aborted', reason=reason)
        if self.file:
            self.file.flush()

    def _arm(self, cfg, now):
        run = cfg['run_id']
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', run):
            raise ValueError('Invalid run_id')
        if cfg.get('player_id', 1) not in (1, 2, 3):
            raise ValueError('Invalid player_id')
        trials = trial_plan(cfg['stage'], cfg.get('lateral', .5), cfg.get('angular', 1.))
        start_trial = cfg.get('start_trial', 1)
        if not isinstance(start_trial, int) or not 1 <= start_trial <= len(trials):
            raise ValueError('Invalid start_trial')
        trials = trials[start_trial-1:]
        if self.file:
            self.file.close()
        # Exclusive creation prevents accidental overwriting or automatic reruns.
        self.file = open(f'/tmp/motion-calibration-{run}.jsonl', 'x', buffering=1)
        self.file.write(json.dumps(dict(event='manifest', config=cfg, trials=trials))+'\n')
        self.run, self.cfg, self.trials = run, cfg, trials
        self.index, self.started, self.home_started = 0, None, now
        self.history = []
        self.status = dict(run_id=run, state='homing', trial=trials[0]['id'], total=trials[-1]['id'])

    def tick(self, ctx, players):
        wall = time.monotonic()
        if wall >= self.next_poll:
            self.next_poll = wall+.5
            try:
                cfg = json.loads(ARM.read_text()) if ARM.exists() else None
                if cfg and cfg.get('run_id') not in self.seen:
                    self.seen.add(cfg.get('run_id'))
                    if self.status['state'] in ('unarmed', 'done', 'aborted'):
                        self._arm(cfg, ctx.now)
                elif not cfg and self.status['state'] not in ('unarmed', 'done', 'aborted'):
                    self.abort('disarmed')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.abort(f'arm_error:{exc}')
        for p in players:
            p.action = 'calibration:idle'
            if (self.status['state'] in ('unarmed', 'done', 'aborted') or
                    p.id != self.cfg.get('player_id', 1)):
                p.controller.stop()
            p.calibration = None
        if self.status['state'] not in ('unarmed', 'done', 'aborted'):
            self._step(ctx, players)
            if self.status['state'] == 'aborted':
                for p in players:
                    p.controller.stop()
        if wall >= self.next_status:
            self.next_status = wall+.5
            tmp = STATUS.with_suffix('.tmp')
            tmp.write_text(json.dumps(dict(self.status, sim_time=ctx.now)))
            tmp.replace(STATUS)

    def _step(self, ctx, players):
        p = next(p for p in players if p.id == self.cfg.get('player_id', 1))
        pose = p.pose
        state = None if ctx.game is None else ctx.game.state.value.lower()
        if state not in ('initial', 'finished'):
            self.abort(f'referee_state:{state}')
            return
        if pose is None or p.is_penalized or p.is_fallen:
            self.abort('missing_pose_penalty_or_fall')
            return
        if ctx.now-ctx.teammates[p.id].last_seen_at > .15:
            self.abort('stale_pose')
            return
        if p.mode != 'walk':
            p.controller.ensure_ready()
            if ctx.now-self.home_started > 45:
                self.abort('walk_mode_timeout')
            return
        nearest = min((math.hypot(r.pose.x-pose.x, r.pose.y-pose.y)
            for side, rs in (('team', ctx.teammates), ('opp', ctx.opponents))
            for pid, r in rs.items() if r.pose and (side != 'team' or pid != p.id)), default=99.)
        if nearest < .8:
            self.abort('nearby_robot')
            return
        home = (-2., 0.)
        self.history.append((ctx.now, pose.x, pose.y, pose.theta))
        self.history = [x for x in self.history if ctx.now-x[0] <= .6]
        measured = None
        if len(self.history) >= 2:
            old = self.history[0]
            dt = ctx.now-old[0]
            if dt > .1:
                dx, dy = pose.x-old[1], pose.y-old[2]
                dh = normalize_angle(pose.theta-old[3])
                angle = old[3]+dh/2
                measured = [(dx*math.cos(angle)+dy*math.sin(angle))/dt,
                            (-dx*math.sin(angle)+dy*math.cos(angle))/dt, dh/dt]
        trial = self.trials[self.index]
        command = [0., 0., 0.]
        if self.started is None:
            self.status.update(state='homing', trial=trial['id'])
            if ctx.now-self.home_started > 60:
                self.abort('homing_timeout')
                return
            distance = math.dist((pose.x, pose.y), home)
            if .12 < distance < .6:
                # 小幅前后请求可能无法推进，前后轴维持可执行幅度；
                # 横移使用短脉冲，位置达标后停车，验收阈值不放宽。
                dx, dy = home[0]-pose.x, home[1]-pose.y
                c, s = math.cos(pose.theta), math.sin(pose.theta)
                body = (dx*c+dy*s, -dx*s+dy*c)
                on = (ctx.now-self.home_started) % 2. < .4
                command = [math.copysign(.5, e) if (axis == 0 or on) and abs(e) > .06 else 0.
                           for axis, e in enumerate(body)]
                command.append(max(-1., min(1., -2.*pose.theta)))
                p.controller.release_kick()
                p.controller.set_velocity(*command)
            elif distance > .12:
                p.controller.walk_to(home, face=0., maintain_face=True, arrive_dist=.08)
                command = p.controller.navigation_status.get('command', [0., 0., 0.])
            elif abs(pose.theta) > .08:
                # 标定朝向容差比普通导航的到位死区小，近点单独校正。
                command = [0., 0., max(-1., min(1., -2.*pose.theta))]
                p.controller.release_kick()
                p.controller.set_velocity(*command)
            elif measured and math.hypot(*measured[:2]) < .05 and abs(measured[2]) < .08:
                self.started = ctx.now
                p.controller.stop()
            else:
                p.controller.stop()
        else:
            phase, command = timed_command(trial, ctx.now-self.started)
            self.status.update(state=phase, trial=trial['id'])
            if abs(pose.y) > 3.4 or abs(pose.x) > 5.5:
                self.abort('test_area_limit')
                return
            if phase == 'done':
                self.index += 1
                self.started, self.home_started = None, ctx.now
                if self.index == len(self.trials):
                    self.status['state'] = 'done'
                else:
                    self.status['state'] = 'homing'
            p.controller.release_kick()
            p.controller.set_velocity(*command)
        record = dict(run_id=self.run, trial=trial['id'], kind=trial['kind'],
            phase=self.status['state'], sim_time=ctx.now,
            observed_at=ctx.teammates[p.id].last_seen_at, pose=asdict(pose),
            requested_command=command, sdk_acknowledged=None, mode=p.mode,
            fallen=p.is_fallen, penalized=p.is_penalized, kicking=p.is_kicking,
            nearest_robot_m=nearest, referee_state=state, measured_velocity=measured)
        self.file.write(json.dumps(record, allow_nan=False)+'\n')
        p.action = 'calibration:'+self.status['state']
        p.calibration = record
        p.controller.navigation_status = dict(state='calibration', target=list(home),
            command=command, speed=math.hypot(*command[:2]))
