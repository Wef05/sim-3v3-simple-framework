"""自由人站位：在矩形、球圆及对手圆组成的区域中求最近可行点。"""

import math

from ..framework.types import Context, Pose2D


def nearest_free_position(
    ctx: Context, pose: Pose2D, ball_radius: float,
    opponent_radius: float, goal_line_margin: float,
) -> tuple[float, float] | None:
    """坐标轴平行于场地；球水平线穿过门口（含门柱）时取朝球门半圆。

    距离边界包含在可行域内。枚举直线/圆边界的垂足和两两交点，
    包括圆心重合时的退化情况，求连续区域上的最近点，不做栅格采样。
    未观测到位置的对手不参与约束。
    """
    ball = ctx.ball
    if ball is None:
        return None
    if not all(math.isfinite(v) and v >= 0 for v in
               (ball_radius, opponent_radius, goal_line_margin)):
        raise ValueError("自由人距离参数必须为有限非负数")
    half_l, half_w = ctx.field.length / 2, ctx.field.width / 2
    xmin, xmax = -half_l, half_l - goal_line_margin
    ymin, ymax = -half_w, half_w
    # 敌方球门中心为 (half_l, 0)，按相对球的位置确定象限。
    sx = half_l - ball.x
    if sx == 0:
        sx = pose.x - ball.x
    if sx >= 0:
        xmin = max(xmin, ball.x)
    else:
        xmax = min(xmax, ball.x)
    # 球的水平延长线穿过门口时不限制球两侧的 y；否则保留门中心所在象限。
    if abs(ball.y) > ctx.field.goal_width / 2.0:
        if ball.y < 0:
            ymin = max(ymin, ball.y)
        else:
            ymax = min(ymax, ball.y)
    if xmin > xmax or ymin > ymax:
        return None

    enemies = [(r.pose.x, r.pose.y, opponent_radius)
               for r in ctx.opponents.values() if r.pose is not None]
    circles = [(ball.x, ball.y, ball_radius), *enemies]
    candidates = [(pose.x, pose.y)]
    # 矩形边界上的最近点，以及所有角点。
    for x in (xmin, xmax):
        candidates.append((x, min(ymax, max(ymin, pose.y))))
        for y in (ymin, ymax):
            candidates.append((x, y))
    for y in (ymin, ymax):
        candidates.append((min(xmax, max(xmin, pose.x)), y))

    for cx, cy, radius in circles:
        dx, dy = pose.x - cx, pose.y - cy
        d = math.hypot(dx, dy)
        if d > 0:
            candidates.append((cx + radius * dx / d, cy + radius * dy / d))
        # 圆心等于当前位置时，所有圆周点距离相等，选基准点或交点即可。
        candidates.extend([(cx + radius, cy), (cx - radius, cy),
                           (cx, cy + radius), (cx, cy - radius)])
        for x in (xmin, xmax):
            h2 = radius ** 2 - (x - cx) ** 2
            if h2 >= -1e-10:
                h = math.sqrt(max(0.0, h2))
                candidates.extend([(x, cy + h), (x, cy - h)])
        for y in (ymin, ymax):
            h2 = radius ** 2 - (y - cy) ** 2
            if h2 >= -1e-10:
                h = math.sqrt(max(0.0, h2))
                candidates.extend([(cx + h, y), (cx - h, y)])

    for i, (ax, ay, ar) in enumerate(circles):
        for bx, by, br in circles[i + 1:]:
            dx, dy = bx - ax, by - ay
            d = math.hypot(dx, dy)
            if d == 0 or d > ar + br + 1e-10 or d < abs(ar - br) - 1e-10:
                continue
            along = (ar * ar - br * br + d * d) / (2 * d)
            h = math.sqrt(max(0.0, ar * ar - along * along))
            mx, my = ax + along * dx / d, ay + along * dy / d
            candidates.extend([(mx - h * dy / d, my + h * dx / d),
                               (mx + h * dy / d, my - h * dx / d)])

    def feasible(point):
        x, y = point
        eps = 1e-9  # 仅吸收边界交点计算的浮点误差。
        return (xmin - eps <= x <= xmax + eps
                and ymin - eps <= y <= ymax + eps
                and math.hypot(x - ball.x, y - ball.y) <= ball_radius + eps
                and all(math.hypot(x - ex, y - ey) >= er - eps
                        for ex, ey, er in enemies))

    return min((p for p in candidates if feasible(p)),
               key=lambda p: math.hypot(p[0] - pose.x, p[1] - pose.y),
               default=None)
