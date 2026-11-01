"""Eight-neighbor A* with swept-body edge checks and visible-only smoothing."""
from __future__ import annotations
import heapq
import math
from functools import lru_cache
from collections.abc import Sequence
from ..param import (GLOBAL_FIELD_MARGIN_M, GLOBAL_GRID_RESOLUTION_M,
                     GLOBAL_OBSTACLE_MARGIN_M, NAV_BODY_RADIUS_M, NAV_FORWARD_SPEED_MPS,
                     NAV_LATERAL_SPEED_MPS, NAV_BACKWARD_SPEED_MPS,
                     NAV_TURN_SPEED_RPS, TURN_THRESHOLD)
from .geom import normalize_angle
from .kick_lane import point_segment


def segment_clear(start, end, obstacles, *, margin=GLOBAL_OBSTACLE_MARGIN_M,
                  allow_escape=False):
    """Check the entire swept disk; overlap may only be exited monotonically."""
    dx, dy = end[0]-start[0], end[1]-start[1]
    left, right = min(start[0], end[0]), max(start[0], end[0])
    bottom, top = min(start[1], end[1]), max(start[1], end[1])
    for o in obstacles:
        radius = o.radius + NAV_BODY_RADIUS_M + margin
        if o.x < left-radius or o.x > right+radius or o.y < bottom-radius or o.y > top+radius:
            continue
        ox, oy = start[0]-o.x, start[1]-o.y
        if allow_escape and math.hypot(ox, oy) <= radius:
            if ox*dx + oy*dy >= 0 and math.hypot(dx, dy) > 1e-9:
                continue
        if point_segment((o.x, o.y), start, end) <= radius:
            return False
    return True


def _bounds(context):
    x = context.field.length/2 - max(GLOBAL_FIELD_MARGIN_M, NAV_BODY_RADIUS_M)
    y = context.field.width/2 - max(GLOBAL_FIELD_MARGIN_M, NAV_BODY_RADIUS_M)
    return -x, x, -y, y


def clamp_target(context, point):
    xmin, xmax, ymin, ymax = _bounds(context)
    return max(xmin, min(xmax, point[0])), max(ymin, min(ymax, point[1]))


def segment_time(start: tuple[float, float], end: tuple[float, float],
                 heading: float, *, omni: bool = False) -> float:
    """Estimated seconds: fixed-body omni travel, or turn then forward travel."""
    dx, dy = end[0]-start[0], end[1]-start[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return 0.
    error = normalize_angle(math.atan2(dy, dx)-heading)
    if omni:
        forward, lateral = length*math.cos(error), length*math.sin(error)
        speed = NAV_FORWARD_SPEED_MPS if forward >= 0 else NAV_BACKWARD_SPEED_MPS
        return math.hypot(forward/speed, lateral/NAV_LATERAL_SPEED_MPS)
    # 与远距控制的先转后走阈值一致；小角度边走边转不另收停车时间。
    return length/NAV_FORWARD_SPEED_MPS + max(0., abs(error)-TURN_THRESHOLD)/NAV_TURN_SPEED_RPS


def path_time(path: Sequence[tuple[float, float]], heading: float, *, omni: bool = False) -> float:
    """Estimate remaining travel time; this is not a measured arrival time."""
    total = 0.
    for start, end in zip(path, path[1:]):
        total += segment_time(start, end, heading, omni=omni)
        if not omni and math.dist(start, end) > 1e-9:
            heading = math.atan2(end[1]-start[1], end[0]-start[0])
    return total


def plan_global_path(context, start, target, obstacles, *, heading=0., omni=False):
    """Eight-direction A* by estimated seconds, with incoming heading in state.

    Keep exact endpoints and swept-body checks. Unknown obstacle velocity is
    handled by the downstream predictive safety layer, not invented here.
    """
    target = clamp_target(context, target)
    if not segment_clear(target, target, obstacles):
        return None
    if segment_clear(start, target, obstacles, allow_escape=True):
        return [start, target]
    xmin, xmax, ymin, ymax = _bounds(context)
    res = GLOBAL_GRID_RESOLUTION_M
    nx, ny = int((xmax-xmin)/res), int((ymax-ymin)/res)
    xs = [xmin+i*res for i in range(nx+1)]
    ys = [ymin+j*res for j in range(ny+1)]
    outside = start != clamp_target(context, start)
    if outside:
        # 仅增加真实场外起点所在的行/列，允许沿边线绕开入口障碍。
        # 不按场外距离扩展整片网格，避免异常定位导致搜索规模无界增长。
        xs = sorted(set(xs + [xmax, min(xmin, start[0]), max(xmax, start[0])]))
        ys = sorted(set(ys + [ymax, min(ymin, start[1]), max(ymax, start[1])]))
        nx, ny = len(xs)-1, len(ys)-1

    def inward(a, b):
        # 每条边只允许保持或减少越界量；入场后不能再次走出安全边界。
        return all(min(lo, old)-1e-8 <= value <= max(hi, old)+1e-8
                   for old, value, lo, hi in zip(a, b, (xmin, ymin), (xmax, ymax)))

    def xy(node):
        return xs[node[0]], ys[node[1]]

    @lru_cache(None)
    def free(node):
        return segment_clear(xy(node), xy(node), obstacles)

    @lru_cache(None)
    def edge_clear(a, b):
        return inward(xy(a), xy(b)) and segment_clear(xy(a), xy(b), obstacles)

    def nearby(point):
        ix = min(range(len(xs)), key=lambda i: abs(xs[i]-point[0]))
        iy = min(range(len(ys)), key=lambda i: abs(ys[i]-point[1]))
        return [(i, j) for i in range(ix-2, ix+3)
                if 0 <= i <= nx for j in range(iy-2, iy+3)
                if 0 <= j <= ny]

    def arrival_heading(a, b):
        return heading if omni or math.dist(a, b) < 1e-9 else math.atan2(b[1]-a[1], b[0]-a[0])

    # 入射方向必须是状态的一部分，否则同一格内更少转弯的路线会被提前剪掉。
    starts = {(node, arrival_heading(start, xy(node))):
              segment_time(start, xy(node), heading, omni=omni)
              for node in nearby(start) if free(node) and
              inward(start, xy(node)) and
              segment_clear(start, xy(node), obstacles, allow_escape=True)}
    goals = {node for node in nearby(target)
             if free(node) and segment_clear(xy(node), target, obstacles)}
    if not starts or not goals:
        return None
    # 不计转弯、以最快平移速度估算的下界，保证启发函数不高估。
    fastest = max(NAV_FORWARD_SPEED_MPS, NAV_LATERAL_SPEED_MPS, NAV_BACKWARD_SPEED_MPS)
    queue = [(cost+math.dist(xy(state[0]), target)/fastest, state)
             for state, cost in starts.items()]
    heapq.heapify(queue)
    costs, previous, closed = dict(starts), {}, set()
    best_goal, best_total = None, math.inf
    while queue:
        bound, state = heapq.heappop(queue)
        if bound >= best_total:
            break
        if state in closed:
            continue
        closed.add(state)
        node, angle = state
        if node in goals:
            total = costs[state]+segment_time(xy(node), target, angle, omni=omni)
            if total < best_total:
                best_goal, best_total = state, total
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if not (dx or dy):
                    continue
                nxt = node[0]+dx, node[1]+dy
                if not (0 <= nxt[0] <= nx and 0 <= nxt[1] <= ny):
                    continue
                nxt_state = (nxt, arrival_heading(xy(node), xy(nxt)))
                if nxt_state in closed or not free(nxt) or not edge_clear(node, nxt):
                    continue
                cost = costs[state]+segment_time(xy(node), xy(nxt), angle, omni=omni)
                if cost < costs.get(nxt_state, math.inf):
                    costs[nxt_state], previous[nxt_state] = cost, state
                    heapq.heappush(queue, (cost+math.dist(xy(nxt), target)/fastest, nxt_state))
    if best_goal is None:
        return None
    state, path = best_goal, [target]
    while True:
        path.append(xy(state[0]))
        if state not in previous:
            break
        state = previous[state]
    path.append(start)
    path.reverse()
    return _smooth_path(path, obstacles, heading=heading, omni=omni)


def _smooth_path(path, obstacles, *, heading=0., omni=False):
    # 快捷连线除了安全，还不能把转弯/侧移耗时重新加回来。
    result, i = [path[0]], 0
    while i < len(path)-1:
        j = len(path)-1
        while j > i+1:
            candidate = result + path[j:]
            original = result + path[i+1:]
            if (segment_clear(path[i], path[j], obstacles, allow_escape=i == 0) and
                    path_time(candidate, heading, omni=omni) <= path_time(original, heading, omni=omni)+1e-9):
                break
            j -= 1
        result.append(path[j])
        i = j
    return result
