"""Circular-obstacle A* with continuous collision checks and overlap escape."""

from __future__ import annotations

import heapq
import math

from ..framework.types import Context
from ..param import (
    GLOBAL_FIELD_MARGIN_M,
    GLOBAL_GRID_RESOLUTION_M,
    GLOBAL_OBSTACLE_MARGIN_M,
)
from .obstacles import Obstacle

Point = tuple[float, float]


def segment_is_clear(
    start: Point, end: Point, obstacles: list[Obstacle], *, allow_escape: bool = False,
) -> bool:
    """Check the whole segment; an overlapping start may only move outward.

    The outward dot product makes distance to each overlapping obstacle
    monotonically increase, including when several goal-net circles overlap.
    """
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_sq = dx * dx + dy * dy
    for obs in obstacles:
        rx, ry = start[0] - obs.x, start[1] - obs.y
        radius = obs.radius + GLOBAL_OBSTACLE_MARGIN_M
        start_sq = rx * rx + ry * ry
        if allow_escape and start_sq <= radius * radius + 1e-12:
            end_sq = (end[0] - obs.x) ** 2 + (end[1] - obs.y) ** 2
            if rx * dx + ry * dy < -1e-12 or end_sq <= start_sq + 1e-12:
                return False
            continue
        t = max(0.0, min(1.0, -(rx * dx + ry * dy) / max(length_sq, 1e-20)))
        if (rx + t * dx) ** 2 + (ry + t * dy) ** 2 <= radius * radius + 1e-12:
            return False
    return True


def navigation_target(context: Context, target: Point, obstacles: list[Obstacle]) -> Point | None:
    """Use a field-interior, unoccupied destination instead of aiming into a post."""
    min_x, max_x, min_y, max_y = _bounds(context)
    target = (max(min_x, min(max_x, target[0])), max(min_y, min(max_y, target[1])))
    if not _blocked(*target, obstacles):
        return target
    # Only needed for an occupied target. Deterministic nearest free grid point.
    nx = int((max_x - min_x) / GLOBAL_GRID_RESOLUTION_M)
    ny = int((max_y - min_y) / GLOBAL_GRID_RESOLUTION_M)
    candidates = (_to_xy((i, j), min_x, min_y)
                  for i in range(nx + 1) for j in range(ny + 1))
    for point in sorted(candidates, key=lambda p: math.dist(p, target)):
        if not _blocked(*point, obstacles):
            return point
    return None


def plan_global_path(
    context: Context, start: Point, target: Point, obstacles: list[Obstacle],
) -> list[Point] | None:
    """Plan from the actual robot position, including a monotone overlap escape."""
    target = navigation_target(context, target, obstacles)
    if target is None:
        return None
    if math.dist(start, target) < 1e-9:
        return [target]
    if segment_is_clear(start, target, obstacles, allow_escape=True):
        return [start, target]

    min_x, max_x, min_y, max_y = _bounds(context)
    nx = int((max_x - min_x) / GLOBAL_GRID_RESOLUTION_M)
    ny = int((max_y - min_y) / GLOBAL_GRID_RESOLUTION_M)
    start_idx = (-1, -1)  # Virtual node preserves the actual (unsnapped) pose.
    cx = max(0, min(nx, round((start[0] - min_x) / GLOBAL_GRID_RESOLUTION_M)))
    cy = max(0, min(ny, round((start[1] - min_y) / GLOBAL_GRID_RESOLUTION_M)))
    initial = [(i, j) for i in range(max(0, cx - 1), min(nx, cx + 1) + 1)
               for j in range(max(0, cy - 1), min(ny, cy + 1) + 1)]
    open_heap = [(math.dist(start, target), 0, start_idx)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score = {start_idx: 0.0}
    closed: set[tuple[int, int]] = set()
    counter = 0
    while open_heap:
        _, _, current = heapq.heappop(open_heap)
        if current in closed:
            continue
        point = start if current == start_idx else _to_xy(current, min_x, min_y)
        if (current != start_idx and math.dist(point, target) <= GLOBAL_GRID_RESOLUTION_M * 2
                and segment_is_clear(point, target, obstacles, allow_escape=True)):
            path = [target, point]
            while current in came_from:
                current = came_from[current]
                path.append(start if current == start_idx else _to_xy(current, min_x, min_y))
            path.reverse()
            return _smooth_path(path, obstacles)
        closed.add(current)
        neighbors = initial if current == start_idx else [
            (current[0] + dx, current[1] + dy)
            for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy
        ]
        for neighbor in neighbors:
            if not (0 <= neighbor[0] <= nx and 0 <= neighbor[1] <= ny):
                continue
            other = _to_xy(neighbor, min_x, min_y)
            if not segment_is_clear(point, other, obstacles, allow_escape=True):
                continue
            tentative = g_score[current] + math.dist(point, other)
            if tentative >= g_score.get(neighbor, math.inf):
                continue
            came_from[neighbor] = current
            g_score[neighbor] = tentative
            counter += 1
            heapq.heappush(open_heap, (tentative + math.dist(other, target), counter, neighbor))
    return None


def _bounds(context: Context) -> tuple[float, float, float, float]:
    half_l = context.field.length / 2.0 - GLOBAL_FIELD_MARGIN_M
    half_w = context.field.width / 2.0 - GLOBAL_FIELD_MARGIN_M
    return -half_l, half_l, -half_w, half_w


def _to_xy(idx: tuple[int, int], min_x: float, min_y: float) -> Point:
    return (min_x + idx[0] * GLOBAL_GRID_RESOLUTION_M,
            min_y + idx[1] * GLOBAL_GRID_RESOLUTION_M)


def _blocked(x: float, y: float, obstacles: list[Obstacle]) -> bool:
    return any(math.hypot(x - obs.x, y - obs.y) <= obs.radius + GLOBAL_OBSTACLE_MARGIN_M
               for obs in obstacles)


def _smooth_path(path: list[Point], obstacles: list[Obstacle]) -> list[Point]:
    """Remove corners only when the complete shortcut is collision-free."""
    smoothed = [path[0]]
    i = 0
    while i < len(path) - 1:
        j = len(path) - 1
        while j > i + 1 and not segment_is_clear(path[i], path[j], obstacles, allow_escape=True):
            j -= 1
        smoothed.append(path[j])
        i = j
    return smoothed
