"""Path guidance + predictive local velocity selection; never owns role decisions."""
from __future__ import annotations
import math
from .executor import Executor
from ..intents import MoveIntent
from ..param import (ARRIVE_DIST, OMNI_DIST, TURN_THRESHOLD, MAX_LINEAR, MAX_ANGULAR,
    LINEAR_GAIN, ANGULAR_GAIN, USE_GLOBAL_PATH_PLANNER, GLOBAL_PATH_LOOKAHEAD_M,
    NAV_SIDE_HOLD_S, NAV_ROUTE_SWITCH_S, NAV_TARGET_RESET_M, NAV_SAFETY_MARGIN_M)
from ..utils.geom import clamp, normalize_angle
from ..utils.navigation import MotionTracker, NavigationFrame
from ..utils.path_planner import plan_global_path, segment_clear, clamp_target, path_time, segment_time


class MotionController(Executor):
    def reset_navigation(self) -> None:
        """Discard route/direction memory when the movement task is interrupted."""
        self._nav_path = None
        self._nav_key = None
        self._nav_heading = None
        self._nav_side_until = 0.
        self._nav_side = 1. if self.id % 2 == 0 else -1.

    def _remaining_route(self, start, target, obstacles, *, omni=False):
        path = getattr(self, '_nav_path', None)
        if not path:
            return None
        # 从旧折线最近投影处继续，不能重新走向已经通过的拐点。
        projections = []
        for i, (a, b) in enumerate(zip(path, path[1:])):
            dx, dy = b[0]-a[0], b[1]-a[1]
            norm = dx*dx+dy*dy
            t = max(0., min(1., ((start[0]-a[0])*dx+(start[1]-a[1])*dy)/norm)) if norm else 1.
            point = a[0]+t*dx, a[1]+t*dy
            projections.append((math.dist(start, point), -i, point))
        _, neg_i, point = min(projections)
        remaining = [start, point] + path[-neg_i+1:-1] + [target]
        remaining = [p for i, p in enumerate(remaining) if i == 0 or math.dist(p, remaining[i-1]) > 1e-8]
        # 用最终安全层余量复核旧路线；新路线保留更宽的规划余量，避免余量边缘抖动。
        if all(segment_clear(a, b, obstacles, margin=NAV_SAFETY_MARGIN_M, allow_escape=i == 0)
               for i, (a, b) in enumerate(zip(remaining, remaining[1:]))):
            return remaining
        # 移动障碍只碰到旧路线边缘时，先绕开它连接原拐点，保留绕行侧。
        # 不能连接原拐点则放弃；不把保持路线作为碰撞豁免。
        repaired, heading = [start], self.pose.theta
        for end in remaining[1:]:
            begin = repaired[-1]
            if segment_clear(begin, end, obstacles, margin=NAV_SAFETY_MARGIN_M,
                             allow_escape=len(repaired) == 1):
                segment = [begin, end]
            else:
                segment = plan_global_path(self.context, begin, end, obstacles, heading=heading, omni=omni)
            if segment is None:
                return None
            repaired.extend(segment[1:])
            # 修复只保留有限拐点；达到上限时重新选路，防止长期运行积累状态。
            if len(repaired) > 64:
                return None
            if not omni and len(segment) > 1:
                a, b = segment[-2:]
                heading = math.atan2(b[1]-a[1], b[0]-a[0])
        return repaired

    def face_to(self, target_theta):
        self.reset_navigation()
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
            self.reset_navigation()
            info['state'] = 'no_pose'
            return (0., 0., 0.), False
        geometry = (*intent.target, pose.x, pose.y, pose.theta)
        if not all(math.isfinite(v) for v in geometry) or (intent.face is not None and not math.isfinite(intent.face)):
            self.reset_navigation()
            info['state'] = 'invalid_geometry'
            return (0., 0., 0.), False
        target = clamp_target(self.context, intent.target)
        info['effective_target'] = list(target)
        if intent.direct_path and target != intent.target:
            self.reset_navigation()
            info.update(state='blocked_direct', blocker='field_boundary')
            return (0., 0., 0.), False
        distance = math.hypot(target[0]-pose.x, target[1]-pose.y)
        if distance < intent.arrive_dist:
            self.reset_navigation()
            yaw = self._angular(normalize_angle(intent.face-pose.theta)) if intent.face is not None else 0.
            info['state'] = 'arrived'
            return (0., 0., yaw if abs(yaw) > .2 else 0.), True

        now = self.context.now
        outside = (pose.x, pose.y) != clamp_target(self.context, (pose.x, pose.y))
        omni = distance <= OMNI_DIST or intent.maintain_face or outside
        game = self.context.game
        phase = None if game is None else (game.state, game.set_play, game.game_phase, game.first_half, game.kicking_team, game.stopped)
        key = (phase, omni, outside, intent.direct_path, intent.avoid_ball, intent.opponent_mode, intent.ball_keepout, intent.motion_priority)
        last = getattr(self, '_nav_last_time', now)
        anchor = getattr(self, '_nav_target', target)
        face_anchor = getattr(self, '_nav_face', intent.face)
        face_changed = ((face_anchor is None) != (intent.face is None) or
                        (face_anchor is not None and intent.face is not None and
                         abs(normalize_angle(face_anchor-intent.face)) > TURN_THRESHOLD))
        if (key != getattr(self, '_nav_key', None) or now < last or now-last > NAV_SIDE_HOLD_S or
                math.dist(anchor, target) > NAV_TARGET_RESET_M or face_changed):
            self.reset_navigation()
            self._nav_target, self._nav_face = target, intent.face
        self._nav_key, self._nav_last_time = key, now
        start = pose.x, pose.y
        obstacles = frame.route_obstacles(self.id, intent)
        goal_heading = math.atan2(target[1]-pose.y, target[0]-pose.x)
        path, waypoint = None, target
        if intent.direct_path:
            if not segment_clear(start, target, obstacles, allow_escape=True):
                info.update(state='blocked_direct', blocker='direct_segment')
                return (0., 0., 0.), False
        elif USE_GLOBAL_PATH_PLANNER:
            previous = self._remaining_route(start, target, obstacles, omni=omni)
            path = plan_global_path(self.context, start, target, obstacles, heading=pose.theta, omni=omni)
            candidate_time = path_time(path, pose.theta, omni=omni) if path else None
            previous_time = path_time(previous, pose.theta, omni=omni) if previous else None
            reason = 'new' if not getattr(self, '_nav_path', None) else 'unsafe'
            if previous is not None:
                if path is None or previous_time <= candidate_time+NAV_ROUTE_SWITCH_S:
                    path, reason = previous, 'retained'
                else:
                    reason = 'faster'
            self._nav_path = path
            info.update(route_reason=reason if path else 'no_route',
                        estimated_time_s=path_time(path, pose.theta, omni=omni) if path else None,
                        candidate_time_s=candidate_time, previous_time_s=previous_time,
                        switch_margin_s=NAV_ROUTE_SWITCH_S)
            if path is not None:
                waypoint = self._path_waypoint(pose, path, obstacles)
        heading = math.atan2(waypoint[1]-pose.y, waypoint[0]-pose.x)
        # If A* fails, locally try only checked commands. No "least bad" unsafe fallback.
        max_speed = min(MAX_LINEAR, LINEAR_GAIN*distance)
        side = getattr(self, '_nav_side', 1. if self.id % 2 == 0 else -1.)
        held = now < getattr(self, '_nav_side_until', 0.) and self._nav_heading is not None
        offsets = [0.] if intent.direct_path else [0.] + [sign*math.radians(deg)
            for deg in (15, 30, 45, 60, 90) for sign in (side, -side)]
        if held and not intent.direct_path:
            previous_offset = normalize_angle(self._nav_heading-heading)
            if abs(previous_offset) <= math.pi/2:
                offsets.append(previous_offset)
        best, best_score, blocker, chosen_offset = None, -math.inf, None, 0.
        for offset in offsets:
            desired = heading+offset
            error = normalize_angle(desired-pose.theta)
            yaw = (self._angular(normalize_angle(intent.face-pose.theta))
                   if omni and intent.face is not None else 0. if omni else self._angular(error))
            if outside:
                # 场外用保持朝向的全向平移沿入口路径移动，避免先转后走反复选侧。
                # 入场后恢复原有朝向控制；速度仍接受完整预测碰撞检查。
                yaw = 0.
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
                # 用同一运动能力模型比较局部耗时；原地转向按转完后的移动计价。
                progress = max(.05, math.cos(offset))
                step = (GLOBAL_PATH_LOOKAHEAD_M*math.cos(desired), GLOBAL_PATH_LOOKAHEAD_M*math.sin(desired))
                estimate = segment_time((0., 0.), step, pose.theta, omni=omni)
                estimate /= progress * (scale if scale > 0 else 1.)
                if held:
                    change = abs(normalize_angle(desired-self._nav_heading))
                    estimate += NAV_ROUTE_SWITCH_S*min(1., change/TURN_THRESHOLD)
                score = -estimate
                if score > best_score:
                    best, best_score, chosen_offset = (vx, vy, yaw), score, offset
                if offset == 0. and not held and (scale == 1. or speeds == (0.,)):
                    break
            # Preferred full-speed movement / in-place turn is already safe.
            if best is not None and offset == 0. and not held and (scale == 1. or speeds == (0.,)):
                break
        if best is None:
            info.update(state='blocked', blocker=blocker or 'no_safe_velocity',
                        blocker_priority=frame.blocker_priority(blocker))
            # Stop is fail-safe, not a claim an incoming robot cannot hit us.
            info['stop_conflict'] = frame.check(self.id, intent, (0., 0., 0.))
            return (0., 0., 0.), False
        self._nav_heading = heading+chosen_offset
        # 绕行侧定义在机器人到最终目标的方向上，不随 A* 引导线改变正负。
        side_error = normalize_angle(self._nav_heading-goal_heading)
        if abs(side_error) > 1e-6:
            self._nav_side = 1. if side_error > 0 else -1.
        self._nav_side_until = now+NAV_SIDE_HOLD_S
        info['direction_held'] = held and abs(normalize_angle(chosen_offset)) > 1e-6
        actual_speed = math.hypot(best[0], best[1])
        info.update(state='turning' if actual_speed < 1e-6 else
                    'yielding' if blocker and blocker.startswith('yield:') else
                    'avoiding' if blocker or abs(chosen_offset) > 1e-6 else 'moving',
                    blocker=blocker, blocker_priority=frame.blocker_priority(blocker),
                    speed=actual_speed, waypoint=list(waypoint),
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
            if not segment_clear(start, candidate, obstacles, margin=NAV_SAFETY_MARGIN_M, allow_escape=True):
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
