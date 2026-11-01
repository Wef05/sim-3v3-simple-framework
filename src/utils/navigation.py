"""Shared observation prediction and priority reservations, without SDK calls.

All coordinates/velocities in this module are world-frame. Prediction is a
short-horizon estimate, not a guarantee about other robots' future actions.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from ..param import (NAV_BODY_RADIUS_M, NAV_SAFETY_MARGIN_M, NAV_OPPONENT_EXTRA_M,
    NAV_HORIZON_S, NAV_REACTION_S, NAV_BRAKE_S, NAV_PREDICTION_STEP_S,
    NAV_TRACK_MAX_AGE_S, NAV_TRACK_MAX_SPEED_MPS, NAV_YIELD_MARGIN_M,
    TEAMMATE_RADIUS, OPPONENT_RADIUS, BALL_OBSTACLE_RADIUS)
from .obstacles import Obstacle, goal_obstacles
from .ball_motion import BallMotion
from .path_planner import _bounds


class MotionTracker:
    """Difference distinct simulation-time observations; reject resets/teleports."""
    def __init__(self):
        self.samples = {}
        self.velocities = {}
        self.now = None
        self.ball = BallMotion()

    def update(self, context):
        if self.now is not None and context.now < self.now:
            self.samples.clear()
            self.velocities.clear()
            self.ball = BallMotion()
        self.now = context.now
        present = set()
        for kind, robots in (('team', context.teammates), ('opponent', context.opponents)):
            for pid, robot in robots.items():
                if robot.pose is None:
                    continue
                sample = robot.last_seen_at, robot.pose.x, robot.pose.y
                if not all(math.isfinite(x) for x in sample):
                    continue
                key = kind, pid
                present.add(key)
                old = self.samples.get(key)
                velocity = self.velocities.get(key, (0., 0.))
                age = context.now-sample[0]
                if not 0 <= age <= NAV_TRACK_MAX_AGE_S:
                    velocity = (0., 0.)
                elif old is not None and sample[0] > old[0]:
                    dt = sample[0]-old[0]
                    measured = (sample[1]-old[1])/dt, (sample[2]-old[2])/dt
                    velocity = measured if (dt <= NAV_TRACK_MAX_AGE_S and
                        math.hypot(*measured) <= NAV_TRACK_MAX_SPEED_MPS) else (0., 0.)
                elif old is None or sample != old:
                    velocity = (0., 0.)
                self.samples[key], self.velocities[key] = sample, velocity
        self.samples = {key: value for key, value in self.samples.items() if key in present}
        self.velocities = {key: value for key, value in self.velocities.items() if key in present}
        self.ball.update(context)

    def velocity(self, kind, pid):
        return self.velocities.get((kind, pid), (0., 0.))


@dataclass(frozen=True)
class MovingObstacle:
    label: str
    position: tuple[float, float]
    velocity: tuple[float, float]
    radius: float
    # Higher-priority planned velocity is tested in addition to observed motion.
    reserved: tuple[float, float] | None = None


class NavigationFrame:
    """One observation snapshot and reservations of already planned team commands."""
    def __init__(self, context, tracker):
        self.context, self.tracker = context, tracker
        self.static = goal_obstacles(context)
        self.reservations = {}

    def reserve(self, pid, velocity):
        self.reservations[pid] = velocity

    def obstacles(self, pid, intent):
        dynamic = []
        for kind, robots, radius in (('team', self.context.teammates, TEAMMATE_RADIUS),
                                    ('opponent', self.context.opponents, OPPONENT_RADIUS)):
            for other_id, robot in robots.items():
                if robot.pose is None or (kind == 'team' and other_id == pid):
                    continue
                x, y = robot.pose.x, robot.pose.y
                if not all(math.isfinite(v) for v in (x, y)):
                    continue
                extra = NAV_OPPONENT_EXTRA_M if kind == 'opponent' and intent.opponent_mode != 'contest' else 0.
                dynamic.append(MovingObstacle(f'{kind}:{other_id}', (x, y),
                    self.tracker.velocity(kind, other_id), radius+extra,
                    self.reservations.get(other_id) if kind == 'team' else None))
        return dynamic

    def route_obstacles(self, pid, intent):
        result = list(self.static)
        result.extend(Obstacle(*o.position, o.radius) for o in self.obstacles(pid, intent))
        if intent.avoid_ball and self.context.ball is not None:
            b = self.context.ball
            result.append(Obstacle(b.x, b.y, max(BALL_OBSTACLE_RADIUS, intent.ball_keepout)))
        return result

    def trajectory(self, position, observed, command):
        """Piecewise linear trajectory including reaction and braking allowance."""
        points = [(0., position)]
        n = math.ceil(NAV_HORIZON_S/NAV_PREDICTION_STEP_S)
        x, y = position
        for i in range(1, n+1):
            t = min(NAV_HORIZON_S, i*NAV_PREDICTION_STEP_S)
            old_t = points[-1][0]
            # Midpoint integration of velocity transition; segments checked continuously.
            alpha = max(0., min(1., ((old_t+t)/2-NAV_REACTION_S)/NAV_BRAKE_S))
            angle = (command[2] if len(command) > 2 else 0.)*(old_t+t)/2
            cx = command[0]*math.cos(angle)-command[1]*math.sin(angle)
            cy = command[0]*math.sin(angle)+command[1]*math.cos(angle)
            vx = observed[0]+(cx-observed[0])*alpha
            vy = observed[1]+(cy-observed[1])*alpha
            x, y = x+vx*(t-old_t), y+vy*(t-old_t)
            points.append((t, (x, y)))
        return points

    def check(self, pid, intent, command):
        """Return blocker label or None, using swept relative segments (no tunneling)."""
        pose = self.context.teammates[pid].pose
        start = pose.x, pose.y
        trajectory = self.trajectory(start, self.tracker.velocity('team', pid), command)
        xmin, xmax, ymin, ymax = _bounds(self.context)
        previous = start
        for _, point in trajectory[1:]:
            # Outside robots may return, but not move farther out.
            for value, old, lo, hi in zip(point, previous, (xmin, ymin), (xmax, ymax)):
                if value < min(lo, old)-1e-8 or value > max(hi, old)+1e-8:
                    return 'field_boundary'
            previous = point
        xs, ys = zip(*(point for _, point in trajectory))
        left, right, bottom, top = min(xs), max(xs), min(ys), max(ys)
        for o in self.static:
            # Most goal circles are far away. Reject by the trajectory bounding
            # box before allocating/checking all segment pairs.
            radius = o.radius+NAV_BODY_RADIUS_M+NAV_SAFETY_MARGIN_M
            if (o.x < left-radius or
                o.x > right+radius or
                o.y < bottom-radius or
                o.y > top+radius):
                continue
            other = [(t, (o.x, o.y)) for t, _ in trajectory]
            if trajectories_conflict(trajectory, other, radius):
                return 'goal_structure'
        for o in self.obstacles(pid, intent):
            radius = o.radius+NAV_BODY_RADIUS_M+NAV_SAFETY_MARGIN_M
            other = [(t, (o.position[0]+o.velocity[0]*t, o.position[1]+o.velocity[1]*t))
                     for t, _ in trajectory]
            if trajectories_conflict(trajectory, other, radius):
                return o.label
            # Everyone also respects a teammate that stops/yields. This makes the
            # zero-velocity fallback compatible with earlier high-priority plans.
            if o.label.startswith('team:'):
                stationary = [(t, o.position) for t, _ in trajectory]
                if trajectories_conflict(trajectory, stationary, radius):
                    return o.label
            if o.reserved is not None:
                other = self.trajectory(o.position, o.velocity, o.reserved)
                if trajectories_conflict(trajectory, other, radius+NAV_YIELD_MARGIN_M):
                    return 'yield:'+o.label
        if intent.avoid_ball and self.context.ball is not None:
            ball = self.context.ball
            other = [(t, self.tracker.ball.predict_ball_position(t) or (ball.x, ball.y))
                     for t, _ in trajectory]
            radius = max(BALL_OBSTACLE_RADIUS, intent.ball_keepout)+NAV_BODY_RADIUS_M+NAV_SAFETY_MARGIN_M
            if trajectories_conflict(trajectory, other, radius):
                return 'ball'
        return None


def trajectories_conflict(first, second, radius):
    """Minimum relative distance on each segment; permit monotonic overlap escape.

    A robot inside an inflated margin can stand still or leave, never approach
    more closely. This avoids permanently trapping robots after noisy contacts.
    """
    for (_, a0), (_, a1), (_, b0), (_, b1) in zip(first, first[1:], second, second[1:]):
        rx, ry = a0[0]-b0[0], a0[1]-b0[1]
        dx, dy = (a1[0]-b1[0])-rx, (a1[1]-b1[1])-ry
        dot = rx*dx+ry*dy
        if rx*rx+ry*ry <= radius*radius:
            if dot < -1e-10:
                return True
            continue
        norm = dx*dx+dy*dy
        t = max(0., min(1., -dot/norm)) if norm > 1e-12 else 0.
        if math.hypot(rx+t*dx, ry+t*dy) < radius-1e-8:
            return True
    return False
