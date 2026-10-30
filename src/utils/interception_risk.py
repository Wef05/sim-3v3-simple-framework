"""截球时刻的竞争风险，纯计算、不发送动作。

占位敌方预测：直接追逐移动球 / 前往自身预计截球点，各占 1/2。
每个分支末端是均匀圆盘分布；误差半径是可替换的建模假设，不是实测。
风险指在我方预计截球时刻对手控球圆覆盖球位，不表示整段路径被截概率。
不同对手独立近似。未来可注入 predictor 替换站位分布，不改变风险积分接口。
"""
import math
from ..param import MAX_LINEAR, MAX_ANGULAR, BALL_CONTROL_RADIUS_M
from .geom import normalize_angle
from .interception import estimate_ball_intercept


def _advance(pose, target, seconds, speed, angular_speed):
    x, y, theta = pose
    dx, dy = target[0] - x, target[1] - y
    distance = math.hypot(dx, dy)
    if distance < 1e-9:
        return pose
    turn = normalize_angle(math.atan2(dy, dx) - theta)
    turned = min(abs(turn), angular_speed * seconds)
    theta += math.copysign(turned, turn)
    remaining = max(0.0, seconds - abs(turn) / angular_speed)
    travel = min(max(0.0, distance - BALL_CONTROL_RADIUS_M), speed * remaining)
    return x + dx / distance * travel, y + dy / distance * travel, theta


def predict_opponent_placeholder(pose, motion, seconds, *, speed=MAX_LINEAR,
                                 angular_speed=MAX_ANGULAR):
    """可替换接口：返回 weight/position/heading/radius_m 的归一化圆盘混合。"""
    intercept = estimate_ball_intercept(pose, motion, linear_speed=speed, angular_speed=angular_speed)
    target = intercept.get('target', motion.predict_ball_position(seconds))
    branches = []
    for strategy in ('chase_ball', 'intercept_point'):
        state = (pose.x, pose.y, pose.theta)
        steps = max(1, math.ceil(seconds / .05))
        dt = seconds / steps
        for i in range(steps):
            aim = motion.predict_ball_position((i + 1) * dt) if strategy == 'chase_ball' else target
            state = _advance(state, aim, dt, speed, angular_speed)
        branches.append(dict(strategy=strategy, weight=.5, position=state[:2],
                             heading=state[2], radius_m=.2 + .15 * seconds))
    return branches


def _overlap_probability(distance, radius, control):
    """均匀误差圆盘与控球圆的精确交面积积分。"""
    if distance >= radius + control:
        return 0.0
    if distance <= abs(radius - control):
        return min(1.0, control ** 2 / radius ** 2)
    a = math.acos(max(-1, min(1, (distance**2 + radius**2 - control**2) / (2*distance*radius))))
    b = math.acos(max(-1, min(1, (distance**2 + control**2 - radius**2) / (2*distance*control))))
    lens = radius**2*a + control**2*b - .5*math.sqrt(max(0,
        (-distance+radius+control)*(distance+radius-control)*(distance-radius+control)*(distance+radius+control)))
    return max(0.0, min(1.0, lens / (math.pi*radius**2)))


def estimate_interception_risk(pose, motion, opponents, intercept=None, *,
                               speed=MAX_LINEAR, angular_speed=MAX_ANGULAR,
                               predictor=predict_opponent_placeholder):
    """返回 probability（未知为 None）及可直接绘制的圆盘概率密度混合。

    每名敌人 density_components 在整个平面积分为 1，密度单位 m^-2。
    未知敌人不会伪造为安全；已知敌人的概率仍输出并标记 partial。
    """
    if not all(math.isfinite(v) and v > 0 for v in (speed, angular_speed)):
        raise ValueError('speeds must be finite and positive')
    own = intercept if intercept is not None else estimate_ball_intercept(pose, motion)
    base = dict(model='equal_chase_intercept_disks_v1', calibrated=False,
                event='opponent_controls_ball_at_own_intercept', probability=None,
                opponents=[], control_radius_m=BALL_CONTROL_RADIUS_M,
                linear_speed_mps=speed, angular_speed_rps=angular_speed)
    if pose is None or own.get('status') != 'intercept':
        return dict(base, status='unavailable', reason='no_reachable_intercept')
    seconds = own['intercept_time_s']
    ball = motion.predict_ball_position(seconds)
    if ball is None:
        return dict(base, status='unavailable', reason='no_ball')
    dx, dy = pose.x-ball[0], pose.y-ball[1]
    distance = math.hypot(dx, dy)
    ratio = min(1, BALL_CONTROL_RADIUS_M / distance) if distance else 0
    base.update(time_s=seconds, ball_position=ball,
                robot_position=(ball[0]+dx*ratio, ball[1]+dy*ratio))
    survival = 1.0
    missing = []
    for robot in opponents.values():
        if robot.pose is None:
            missing.append(robot.player_id)
            continue
        branches = predictor(robot.pose, motion, seconds, speed=speed, angular_speed=angular_speed)
        if not branches or abs(sum(b['weight'] for b in branches)-1) > 1e-6:
            raise ValueError('predictor weights must sum to one')
        risk = 0.0
        components = []
        for branch in branches:
            radius, weight = branch['radius_m'], branch['weight']
            if not all(math.isfinite(v) for v in (*branch['position'], radius, weight)) or radius <= 0 or weight < 0:
                raise ValueError('invalid prediction component')
            risk += weight * _overlap_probability(math.dist(branch['position'], ball), radius, BALL_CONTROL_RADIUS_M)
            components.append(dict(branch, density_per_m2=weight/(math.pi*radius**2)))
        survival *= 1-risk
        base['opponents'].append(dict(id=robot.player_id, probability=risk, density_components=components))
    return dict(base, status='partial' if missing else 'ok', missing_opponents=missing,
                probability=1-survival if base['opponents'] or not missing else None)
