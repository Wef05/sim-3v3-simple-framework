"""Joint, receding-horizon velocity planning in strict team priority order.

Paths may cross at different times. Swept discs are checked analytically over
continuous time, not by comparing spatial polylines or discrete samples.
"""
from __future__ import annotations

from contextlib import contextmanager
import math

from ..param import (GOALKEEPER_ID, TEAMMATE_RADIUS, TEAM_MOTION_MARGIN_M,
                     TEAM_MOTION_HORIZON_S, TEAM_YIELD_SPEED_M_S,
                     GLOBAL_FIELD_MARGIN_M, MAX_LINEAR)
from .obstacles import goal_obstacles
from .path_planner import segment_is_clear


def priority_order(context, players):
    def key(p):
        pose, ball = p.pose, context.ball
        distance = math.hypot(pose.x-ball.x, pose.y-ball.y) if pose and ball else math.inf
        return (p.id != GOALKEEPER_ID, distance, p.id)
    return sorted(players, key=key)


def compatible(a, va, b, vb, horizon=TEAM_MOTION_HORIZON_S):
    """Minimum relative distance of two moving bodies over [0, horizon]."""
    rx, ry = a[0]-b[0], a[1]-b[1]
    vx, vy = va[0]-vb[0], va[1]-vb[1]
    radius = 2*TEAMMATE_RADIUS + TEAM_MOTION_MARGIN_M
    start2 = rx*rx + ry*ry
    dot = rx*vx + ry*vy
    speed2 = vx*vx + vy*vy
    if start2 < radius*radius:
        # An already overlapping pair must separate, never deepen the overlap.
        return dot >= -1e-10 and start2 + 2*dot*horizon + speed2*horizon*horizon > start2 + 1e-10
    t = max(0.0, min(horizon, -dot / max(speed2, 1e-20)))
    return (rx+vx*t)**2 + (ry+vy*t)**2 >= radius*radius


class _Intent:
    def __init__(self, backend):
        self.backend = backend
        self.command = ('set_velocity', (0., 0., 0.))

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def set_velocity(self, *args):
        self.command = ('set_velocity', args)

    def kick(self, *args):
        self.command = ('kick', args)

    def release_kick(self):
        if self.command[0] == 'kick':
            self.command = ('set_velocity', (0., 0., 0.))
        if self.backend is not None:
            self.backend.release_kick()


def _preferred(p, intent, context):
    if p.pose is None:
        return (0., 0.)
    if intent.command[0] == 'kick':
        if context.ball is None:
            return (0., 0.)
        dx, dy = context.ball.x-p.pose.x, context.ball.y-p.pose.y
        d = math.hypot(dx, dy)
        speed = min(MAX_LINEAR, d / TEAM_MOTION_HORIZON_S)
        return (speed*dx/max(d, 1e-9), speed*dy/max(d, 1e-9))
    vx, vy, _ = intent.command[1]
    c, s = math.cos(p.pose.theta), math.sin(p.pose.theta)
    return (c*vx-s*vy, s*vx+c*vy)


def resolve(context, players, intents):
    ordered = priority_order(context, [p for p in players if p.pose is not None])
    options = {}
    preferred = {p.id: _preferred(p, intents[p.id], context) for p in ordered}
    for p in ordered:
        v = preferred[p.id]
        candidates = [v]
        # Only a walking intent may sidestep. Stops, penalties and SDK kicks
        # are not silently converted into new tactical movement.
        if intents[p.id].command[0] == 'set_velocity' and math.hypot(*v) > 1e-9:
            candidates += [(v[0]*s, v[1]*s) for s in (.65, .3)]
            candidates.append((0., 0.))
            for angle in (45, -45, 90, -90, 135, -135, 180):
                h = math.atan2(v[1], v[0]) + math.radians(angle)
                speed = min(math.hypot(*v), TEAM_YIELD_SPEED_M_S)
                candidates.append((speed*math.cos(h), speed*math.sin(h)))
        else:
            candidates.append((0., 0.))
        start = (p.pose.x, p.pose.y)
        obstacles = getattr(p, '_motion_obstacles', goal_obstacles(context))
        def clear(v):
            if v == (0., 0.):
                return True
            end = (start[0]+v[0]*TEAM_MOTION_HORIZON_S,
                   start[1]+v[1]*TEAM_MOTION_HORIZON_S)
            half_l = context.field.length/2-GLOBAL_FIELD_MARGIN_M
            half_w = context.field.width/2-GLOBAL_FIELD_MARGIN_M
            if abs(end[0]) > max(half_l, abs(start[0])) or abs(end[1]) > max(half_w, abs(start[1])):
                return False
            return segment_is_clear(start, end, obstacles, allow_escape=True)
        unknown_players = [r.pose for pid, r in context.teammates.items()
                           if pid not in intents and r.pose is not None]
        options[p.id] = [v for v in candidates if clear(v) and all(
            compatible(start, v, (other.x, other.y), (0., 0.))
            for other in unknown_players)]

    # Lexicographic search: preserve the keeper's preferred motion whenever
    # any safe combination for the rest exists, then preserve nearest-to-ball.
    chosen = {}
    def search(i):
        if i == len(ordered):
            return True
        p = ordered[i]
        for v in options[p.id]:
            if all(compatible((p.pose.x, p.pose.y), v, (q.pose.x, q.pose.y), chosen[q.id])
                   for q in ordered[:i]):
                chosen[p.id] = v
                if search(i+1):
                    return True
        chosen.pop(p.id, None)
        return False
    solved = search(0)
    if not solved:
        chosen = {p.id: (0., 0.) for p in ordered}
    for p in players:
        intent = intents[p.id]
        backend = intent.backend
        if backend is None:
            continue
        v = chosen.get(p.id, (0., 0.))
        if solved and intent.command[0] == 'kick' and p.pose is not None and v == preferred[p.id]:
            backend.kick(*intent.command[1])
        else:
            backend.release_kick()
            if intent.command[0] == "kick":
                p._kicking = False
            yaw = intent.command[1][2] if intent.command[0] == 'set_velocity' else 0.
            c, s = (math.cos(p.pose.theta), math.sin(p.pose.theta)) if p.pose else (1., 0.)
            backend.set_velocity(c*v[0]+s*v[1], -s*v[0]+c*v[1], yaw)
            if p.id in preferred and v != preferred[p.id]:
                p.action += ':yield'


@contextmanager
def coordinated_motion(context, players):
    """Collect all intents before issuing movement; restore backends on errors."""
    intents = {p.id: _Intent(p._backend) for p in players}
    for p in players:
        p._backend = intents[p.id]
        p._team_motion_active = True
        p._motion_obstacles = goal_obstacles(context)
    try:
        yield
        resolve(context, players, intents)
    except Exception:
        for intent in intents.values():
            if intent.backend is not None:
                intent.backend.release_kick()
                intent.backend.set_velocity(0., 0., 0.)
        raise
    finally:
        for p in players:
            p._backend = intents[p.id].backend
            p._team_motion_active = False
