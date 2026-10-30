"""Reusable tactical geometry, independent of role state and robot control."""
import math
from .geom import clamp, dist

def kick_can_score(ctx, kick_direction: float) -> bool:
    """判断从当前球位按 ``kick_direction`` 踢,直线轨迹是否能进对方球门。
    """
    ball = ctx.ball if ctx is not None else None
    if ctx is None or ball is None:
        return False

    goal_x = ctx.field.length / 2.0
    dx = math.cos(kick_direction)
    dy = math.sin(kick_direction)
    if dx <= 1e-6:
        return False

    half_goal = ctx.field.goal_width / 2.0
    if half_goal <= 0.0:
        return False
    if ball.x >= goal_x:
        y_at_goal = ball.y
    else:
        t = (goal_x - ball.x) / dx
        if t < 0.0:
            return False
        y_at_goal = ball.y + dy * t
    return -half_goal <= y_at_goal <= half_goal

def block_path_projection(
    ctx, pose, opponent_id: int,
) -> tuple[float, float, float, float] | None:
    """自己到 对手→球 线段的垂足:返回 (x, y, 垂距, 线段参数 t)。"""
    ball = ctx.ball if ctx is not None else None
    opponent = ctx.opponents.get(opponent_id) if ctx is not None else None
    if ctx is None or pose is None or ball is None or opponent is None:
        return None
    if opponent.pose is None:
        return None

    ax, ay = opponent.pose.x, opponent.pose.y
    bx, by = ball.x, ball.y
    vx, vy = bx - ax, by - ay
    length2 = vx * vx + vy * vy
    if length2 < 1e-6:
        return None

    raw_t = ((pose.x - ax) * vx + (pose.y - ay) * vy) / length2
    t = clamp(raw_t, 0.0, 1.0)
    tx = ax + vx * t
    ty = ay + vy * t
    return tx, ty, dist(pose.x, pose.y, tx, ty), raw_t
