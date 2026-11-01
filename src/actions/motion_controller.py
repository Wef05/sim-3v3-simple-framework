"""Path guidance + predictive local velocity selection; never owns role decisions."""
from __future__ import annotations
import math
from .executor import Executor
from ..intents import MoveIntent
from ..param import (ARRIVE_DIST, OMNI_DIST, TURN_THRESHOLD, MAX_LINEAR, MAX_ANGULAR,
    LINEAR_GAIN, ANGULAR_GAIN, USE_GLOBAL_PATH_PLANNER, GLOBAL_PATH_LOOKAHEAD_M,
    NAV_SIDE_HOLD_S)
from ..utils.geom import clamp, normalize_angle
from ..utils.navigation import MotionTracker, NavigationFrame
from ..utils.path_planner import plan_global_path, segment_clear, clamp_target


class MotionController(Executor):
    def face_to(self, target_theta):
        if self.pose is None:
            self.stop()
            return
        self.set_velocity(0., 0., self._angular(normalize_angle(target_theta-self.pose.theta)))

    def navigation_frame(self):
        tracker = getattr(self, '_navigation_tracker', None)
        if tracker is None:
            tracker = self._navigation_tracker = MotionTracker()
        tracker.update(self.context)
        return NavigationFrame(self.context, tracker)

    def walk_to(self, target, *, face=None, avoid_ball=False, avoid_robots=False,
                arrive_dist=ARRIVE_DIST, maintain_face=False, direct_path=False,
                motion_priority=10, opponent_mode='avoid', ball_keepout=0.):
        """Standalone compatibility entry; team execution uses a shared planning frame."""
        intent = MoveIntent(target, face, avoid_ball, avoid_robots, arrive_dist,
                            maintain_face, direct_path, motion_priority, opponent_mode, ball_keepout)
        self.release_kick()
        command, arrived = self.plan_walk(intent, self.navigation_frame() if self.context else None)
        self.set_velocity(*command)
        return arrived

    def plan_walk(self, intent, frame):
        """Compute a body-frame command without sending it to the SDK."""
        self.tactical_target = intent.target
        info = self.navigation_status = dict(state='stopped', priority=intent.motion_priority,
            opponent_mode=intent.opponent_mode, avoid_ball=intent.avoid_ball,
            blocker=None, target=list(intent.target))
        pose = self.pose
        if pose is None or frame is None:
            info['state'] = 'no_pose'
            return (0., 0., 0.), False
        geometry = (*intent.target, pose.x, pose.y, pose.theta)
        if not all(math.isfinite(v) for v in geometry) or (intent.face is not None and not math.isfinite(intent.face)):
            info['state'] = 'invalid_geometry'
            return (0., 0., 0.), False
        target = clamp_target(self.context, intent.target)
        info['effective_target'] = list(target)
        if intent.direct_path and target != intent.target:
            info.update(state='blocked_direct', blocker='field_boundary')
            return (0., 0., 0.), False
        distance = math.hypot(target[0]-pose.x, target[1]-pose.y)
        if distance < intent.arrive_dist:
            yaw = self._angular(normalize_angle(intent.face-pose.theta)) if intent.face is not None else 0.
            info['state'] = 'arrived'
            return (0., 0., yaw if abs(yaw) > .2 else 0.), True

        start = pose.x, pose.y
        obstacles = frame.route_obstacles(self.id, intent)
        goal_heading = math.atan2(target[1]-pose.y, target[0]-pose.x)
        path, waypoint = None, target
        if intent.direct_path:
            if not segment_clear(start, target, obstacles, allow_escape=True):
                info.update(state='blocked_direct', blocker='direct_segment')
                return (0., 0., 0.), False
        elif USE_GLOBAL_PATH_PLANNER:
            path = plan_global_path(self.context, start, target, obstacles)
            if path is not None:
                waypoint = self._path_waypoint(pose, path, obstacles)
        heading = math.atan2(waypoint[1]-pose.y, waypoint[0]-pose.x)
        # If A* fails, locally try only checked commands. No "least bad" unsafe fallback.
        omni = distance <= OMNI_DIST or intent.maintain_face
        max_speed = min(MAX_LINEAR, LINEAR_GAIN*distance)
        now = self.context.now
        if now < getattr(self, '_nav_last_time', now):
            self._nav_side_until = 0.
        self._nav_last_time = now
        side = getattr(self, '_nav_side', 1. if self.id % 2 == 0 else -1.)
        held = now < getattr(self, '_nav_side_until', 0.)
        offsets = [0.] if intent.direct_path else [0.] + [sign*math.radians(deg)
            for deg in (15, 30, 45, 60, 90) for sign in (side, -side)]
        best, best_score, blocker, chosen_offset = None, -math.inf, None, 0.
        for offset in offsets:
            desired = heading+offset
            error = normalize_angle(desired-pose.theta)
            yaw = (self._angular(normalize_angle(intent.face-pose.theta))
                   if omni and intent.face is not None else 0. if omni else self._angular(error))
            # Rotation itself has no translational command, but momentum is still checked.
            speeds = (0.,) if not omni and abs(error) > TURN_THRESHOLD else (1., .65, .35, .15)
            for scale in speeds:
                speed = max_speed*scale
                if omni:
                    vx, vy = speed*math.cos(error), speed*math.sin(error)
                else:
                    vx, vy = speed*max(0., math.cos(error)), 0.
                wx = vx*math.cos(pose.theta)-vy*math.sin(pose.theta)
                wy = vx*math.sin(pose.theta)+vy*math.cos(pose.theta)
                reason = frame.check(self.id, intent, (wx, wy, yaw))
                if reason is not None:
                    blocker = blocker or reason
                    continue
                # Prefer progress along the safe route, then small turns and stable side.
                score = (wx*math.cos(heading)+wy*math.sin(heading)
                         -.22*abs(offset) - (.15 if held and offset*side < 0 else 0.))
                if score > best_score:
                    best, best_score, chosen_offset = (vx, vy, yaw), score, offset
                if offset == 0. and (scale == 1. or speeds == (0.,)):
                    break
            # Preferred full-speed movement / in-place turn is already safe.
            if best is not None and offset == 0. and (scale == 1. or speeds == (0.,)):
                break
        if best is None:
            info.update(state='blocked', blocker=blocker or 'no_safe_velocity')
            # Stop is fail-safe, not a claim an incoming robot cannot hit us.
            info['stop_conflict'] = frame.check(self.id, intent, (0., 0., 0.))
            return (0., 0., 0.), False
        if abs(chosen_offset) > 1e-6:
            self._nav_side = 1. if chosen_offset > 0 else -1.
            self._nav_side_until = now+NAV_SIDE_HOLD_S
        actual_speed = math.hypot(best[0], best[1])
        info.update(state='turning' if actual_speed < 1e-6 else
                    'yielding' if blocker and blocker.startswith('yield:') else
                    'avoiding' if blocker or abs(chosen_offset) > 1e-6 else 'moving',
                    blocker=blocker, speed=actual_speed, waypoint=list(waypoint),
                    path=path, command=list(best))
        from ..framework import debugdraw
        debugdraw.point(*target, rgb=(0., 1., 0.), scale=.15, ns='target')
        if path:
            debugdraw.line(path, rgb=(.2, .8, 1.), ns='global_path')
        debugdraw.point(*waypoint, rgb=(.2, .8, 1.), scale=.12, ns='global_waypoint')
        return best, False

    def _path_waypoint(self, pose, path, obstacles=()):
        """Accumulate lookahead length, then reject shortcuts across a corner."""
        if not path:
            return pose.x, pose.y
        start = pose.x, pose.y
        remaining, prev, visible = GLOBAL_PATH_LOOKAHEAD_M, start, start
        for point in path[1:]:
            length = math.dist(prev, point)
            if length <= 1e-9:
                continue
            fraction = min(1., remaining/length)
            candidate = prev[0]+fraction*(point[0]-prev[0]), prev[1]+fraction*(point[1]-prev[1])
            if not segment_clear(start, candidate, obstacles, allow_escape=True):
                return visible
            visible = candidate
            if length >= remaining:
                return candidate
            remaining -= length
            prev = point
        return visible

    @staticmethod
    def _angular(error):
        return clamp(ANGULAR_GAIN*error, -MAX_ANGULAR, MAX_ANGULAR)
