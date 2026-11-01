"""自由人站位：在原有接球区域内求避开射门通道的最近可行点。"""

import math

from ..framework.types import Context, Pose2D


def shot_triangle(ctx: Context, attacker: Pose2D | None) -> tuple:
    """本帧 attacker 与敌方两门柱；缺失/无效观测不生成禁区。"""
    if attacker is None or not all(math.isfinite(v) for v in (attacker.x, attacker.y)):
        return ()
    x, y = ctx.field.length / 2, ctx.field.goal_width / 2
    return ((attacker.x, attacker.y), (x, -y), (x, y))


def triangle_distance(point: tuple[float, float], triangle: tuple) -> float:
    """点到闭三角形的距离（米）；退化三角形按三条线段处理。"""
    x, y = point
    crosses, distances = [], []
    for (ax, ay), (bx, by) in zip(triangle, triangle[1:] + triangle[:1]):
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        crosses.append(dx * (y - ay) - dy * (x - ax))
        t = min(1.0, max(0.0, ((x-ax)*dx + (y-ay)*dy) / length2)) if length2 else 0.0
        distances.append(math.hypot(x-ax-t*dx, y-ay-t*dy))
    a, b, c = triangle
    area = (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
    if abs(area) > 1e-12 and (min(crosses) >= 0 or max(crosses) <= 0):
        return 0.0
    return min(distances)


def nearest_free_position(
    ctx: Context, pose: Pose2D, ball_radius: float,
    opponent_radius: float, goal_line_margin: float,
    exclusion: tuple = (), shot_clearance: float = 0.38,
) -> tuple[float, float] | None:
    """坐标轴平行于场地；球水平线穿过敌方大禁区（含边界）时取朝球门半圆。

    距离边界包含在可行域内。枚举直线/圆边界的垂足和两两交点，
    包括圆心重合时的退化情况，求连续区域上的最近点，不做栅格采样。
    未观测到位置的对手不参与约束。
    """
    ball = ctx.ball
    if ball is None:
        return None
    if not all(math.isfinite(v) and v >= 0 for v in
               (ball_radius, opponent_radius, goal_line_margin, shot_clearance)):
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
    # 球的水平延长线穿过敌方大禁区时不限制球两侧的 y；否则保留门中心所在象限。
    if abs(ball.y) > ctx.field.penalty_area_width / 2.0:
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
    # 三角形外扩为边的平行线段及顶点圆弧。枚举整条直线/整圆后统一
    # 验证可行性，超出实际线段或圆弧的候选不会影响最近点的正确性。
    lines = [(1., 0., xmin), (1., 0., xmax), (0., 1., ymin), (0., 1., ymax)]
    if exclusion:
        circles.extend((x, y, shot_clearance) for x, y in exclusion)
        for (ax, ay), (bx, by) in zip(exclusion, exclusion[1:] + exclusion[:1]):
            length = math.hypot(bx-ax, by-ay)
            if length <= 1e-12:
                continue
            nx, ny = -(by-ay)/length, (bx-ax)/length
            base = nx*ax + ny*ay
            lines.extend((nx, ny, base + side*shot_clearance) for side in (-1, 1))
        for i, (nx, ny, offset) in enumerate(lines):
            delta = offset - nx*pose.x - ny*pose.y
            candidates.append((pose.x + delta*nx, pose.y + delta*ny))
            for mx, my, other in lines[i+1:]:
                determinant = nx*my - ny*mx
                if abs(determinant) > 1e-12:
                    candidates.append(((offset*my-ny*other)/determinant,
                                       (nx*other-offset*mx)/determinant))
            for cx, cy, radius in circles:
                delta = offset - nx*cx - ny*cy
                h2 = radius*radius - delta*delta
                if h2 >= -1e-10:
                    h = math.sqrt(max(0.0, h2))
                    candidates.extend((cx + delta*nx + side*h*ny,
                                       cy + delta*ny - side*h*nx) for side in (-1, 1))
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
                and (not exclusion or triangle_distance(point, exclusion) >= shot_clearance - eps)
                and all(math.hypot(x - ex, y - ey) >= er - eps
                        for ex, ey, er in enemies))

    return min((p for p in candidates if feasible(p)),
               key=lambda p: math.hypot(p[0] - pose.x, p[1] - pose.y),
               default=None)
