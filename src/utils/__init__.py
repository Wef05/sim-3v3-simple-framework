"""Geometry, motion estimation, interception and path calculation utilities.

Estimators may retain observation history; utilities never issue robot commands.
"""

from .geom import (
    angle_to,
    clamp,
    clamp_inside_field,
    deg2rad,
    dist,
    normalize_angle,
    opponent_goal,
    own_goal,
    own_goal_area_center,
    rad2deg,
)
from .obstacles import Obstacle, collect_obstacles, detour

__all__ = [
    "Obstacle",
    "angle_to",
    "clamp",
    "clamp_inside_field",
    "collect_obstacles",
    "deg2rad",
    "detour",
    "dist",
    "normalize_angle",
    "opponent_goal",
    "own_goal",
    "own_goal_area_center",
    "rad2deg",
]
