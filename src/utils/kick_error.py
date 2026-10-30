"""只读出球方向估计：冻结出球前计划，用首次转向前的真实球位估计偏角。

无接触传感器，事件由球速变化和附近唯一踢球者推断，不代表确认触球。
"""
import math
import uuid
from collections import deque
from copy import deepcopy


def angle_difference(a, b):
    return math.atan2(math.sin(a-b), math.cos(a-b))


class KickErrorTracker:
    MAX_GAP = .25
    MIN_SPEED = .4
    IMPULSE = .5
    CONTACT_RADIUS = .8
    MIN_SEGMENT = .08
    MIN_TRAVEL = .15
    TURN_RAD = math.radians(10)

    def __init__(self):
        self.run_id = uuid.uuid4().hex
        self.events = deque(maxlen=16)
        self.next_id = 0
        self.previous = None
        self.commands = []
        self.velocity = None
        self.active = None
        self.latest = None
        self.count = 0
        self.absolute_sum = 0.

    def finish(self, reason):
        if self.active is None:
            return
        event = self.active
        event['status'] = 'complete' if event['error_deg'] is not None else 'insufficient'
        event['end_reason'] = reason
        if event['error_deg'] is not None:
            self.count += 1
            self.absolute_sum += abs(event['error_deg'])
        self.latest = event
        self.active = None

    def update(self, context, players):
        ball = context.ball
        commands = [dict(p.controller.kick_observation) for p in players
                    if p.controller.kick_observation is not None]
        if ball is None or not all(math.isfinite(v) for v in
                (ball.x, ball.y, ball.last_seen_at, context.now)):
            self.finish('observation_gap')
            self.previous = self.velocity = None
            self.commands = []
            return
        t = ball.last_seen_at
        if t < 0 or context.now-t > self.MAX_GAP or t > context.now:
            self.finish('observation_gap')
            self.previous = self.velocity = None
            self.commands = []
            return
        sample = (t, ball.x, ball.y)
        old = self.previous
        if old and t < old[0]:
            self.__init__()
            old = None
        if old and t == old[0]:
            return  # 重复观测不能推进统计或覆盖该观测前的指令。
        self.previous = sample
        previous_commands, self.commands = self.commands, commands
        if not old:
            return
        dt = t-old[0]
        if dt > self.MAX_GAP:
            self.finish('observation_gap')
            self.velocity = None
            return
        vx, vy = (ball.x-old[1])/dt, (ball.y-old[2])/dt
        speed = math.hypot(vx, vy)
        if speed > 20:
            self.finish('observation_jump')
            self.velocity = None
            return
        previous_velocity, self.velocity = self.velocity, (vx, vy)
        if self.active:
            event = self.active
            anchor = event['segment_anchor']
            dx, dy = ball.x-anchor[0], ball.y-anchor[1]
            if speed < .1:
                self.finish('stopped')
            elif math.hypot(dx, dy) >= self.MIN_SEGMENT:
                direction = math.atan2(dy, dx)
                if abs(angle_difference(direction, event['reference_direction'])) > self.TURN_RAD:
                    self.finish('first_turn')  # 不把转向后的点计入出球方向。
                else:
                    event['segment_anchor'] = [ball.x, ball.y]
                    dx, dy = ball.x-event['origin'][0], ball.y-event['origin'][1]
                    if math.hypot(dx, dy) >= self.MIN_TRAVEL:
                        actual = math.atan2(dy, dx)
                        event['actual_direction'] = actual
                        event['reference_direction'] = actual
                        event['error_deg'] = math.degrees(angle_difference(actual, event['planned_direction']))
            if self.active and t-event['time'] > 5:
                self.finish('timeout')
            return
        if previous_velocity is None or speed < self.MIN_SPEED:
            return
        if math.hypot(vx-previous_velocity[0], vy-previous_velocity[1]) < self.IMPULSE:
            return
        candidates = [cmd for cmd in previous_commands
                      if 0 <= t-cmd['time'] <= self.MAX_GAP
                      and math.hypot(cmd['robot'][0]-old[1], cmd['robot'][1]-old[2]) <= self.CONTACT_RADIUS]
        # 近处有其他机器人时无法可靠归因，不计为该球员的踢球。
        if len(candidates) != 1:
            return
        cmd = candidates[0]
        others = [r for pid, r in context.teammates.items() if pid != cmd['player_id']]
        others += list(context.opponents.values())
        if any(r.pose and math.hypot(r.pose.x-old[1], r.pose.y-old[2]) <= self.CONTACT_RADIUS for r in others):
            return
        self.next_id += 1
        self.active = dict(id=self.next_id, player_id=cmd['player_id'], time=t, origin=[old[1], old[2]],
                           segment_anchor=[old[1], old[2]], planned_direction=cmd['planned_direction'],
                           command_direction=cmd['command_direction'], reference_direction=math.atan2(vy, vx), actual_direction=None,
                           error_deg=None, status='tracking', end_reason=None)
        self.latest = self.active
        self.events.append(self.active)

    def snapshot(self):
        return dict(method='observed_first_segment', run_id=self.run_id,
                    events=deepcopy(list(self.events)), latest=deepcopy(self.latest), count=self.count,
                    mean_absolute_error_deg=self.absolute_sum/self.count if self.count else None,
                    turn_threshold_deg=math.degrees(self.TURN_RAD), minimum_travel_m=self.MIN_TRAVEL)
