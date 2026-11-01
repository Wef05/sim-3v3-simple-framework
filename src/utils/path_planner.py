"""Eight-neighbor A* with swept-body edge checks and visible-only smoothing."""
from __future__ import annotations
import heapq
import math
from functools import lru_cache
from ..param import (GLOBAL_FIELD_MARGIN_M, GLOBAL_GRID_RESOLUTION_M,
                     GLOBAL_OBSTACLE_MARGIN_M, NAV_BODY_RADIUS_M)
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


def plan_global_path(context, start, target, obstacles):
    """Return exact-start, exact-target collision-checked path, or None.

    Outside starts are not silently teleported into the field. A robot already
    touching an inflated obstacle can escape, but cannot move deeper into it.
    """
    target = clamp_target(context, target)
    if not segment_clear(target, target, obstacles):
        return None
    if segment_clear(start, target, obstacles, allow_escape=True):
        return [start, target]
    xmin, xmax, ymin, ymax = _bounds(context)
    res = GLOBAL_GRID_RESOLUTION_M
    nx, ny = int((xmax-xmin)/res), int((ymax-ymin)/res)

    def xy(node):
        return xmin+node[0]*res, ymin+node[1]*res

    @lru_cache(None)
    def free(node):
        return segment_clear(xy(node), xy(node), obstacles)

    def nearby(point):
        ix, iy = round((point[0]-xmin)/res), round((point[1]-ymin)/res)
        return [(i, j) for i in range(ix-2, ix+3)
                if 0 <= i <= nx for j in range(iy-2, iy+3)
                if 0 <= j <= ny]

    # Connect actual endpoints to nearby grid nodes with full segment checks.
    starts = {node: math.dist(start, xy(node)) for node in set(nearby(start))
              if free(node) and segment_clear(start, xy(node), obstacles, allow_escape=True)}
    goals = {node for node in set(nearby(target))
             if free(node) and segment_clear(xy(node), target, obstacles)}
    if not starts or not goals:
        return None
    queue = [(cost+math.dist(xy(node), target), node) for node, cost in starts.items()]
    heapq.heapify(queue)
    costs, previous, closed = dict(starts), {}, set()
    while queue:
        _, node = heapq.heappop(queue)
        if node in closed:
            continue
        closed.add(node)
        if node in goals:
            path = [target, xy(node)]
            while node in previous:
                node = previous[node]
                path.append(xy(node))
            path.append(start)
            path.reverse()
            return _smooth_path(path, obstacles)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if not (dx or dy):
                    continue
                nxt = node[0]+dx, node[1]+dy
                if not (0 <= nxt[0] <= nx and 0 <= nxt[1] <= ny) or nxt in closed:
                    continue
                if not free(nxt) or not segment_clear(xy(node), xy(nxt), obstacles):
                    continue
                cost = costs[node]+res*math.hypot(dx, dy)
                if cost < costs.get(nxt, math.inf):
                    costs[nxt], previous[nxt] = cost, node
                    heapq.heappush(queue, (cost+math.dist(xy(nxt), target), nxt))
    return None


def _smooth_path(path, obstacles):
    result, i = [path[0]], 0
    while i < len(path)-1:
        j = len(path)-1
        while j > i+1 and not segment_clear(path[i], path[j], obstacles, allow_escape=i == 0):
            j -= 1
        result.append(path[j])
        i = j
    return result
