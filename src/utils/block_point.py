"""最近可行拦球站位，仅计算，不改变角色或动作。

沿现有减速预测轨迹取样，最小化球员移动距离（同距取较早时刻）。
球路旁有效接触圆内最近的站位为目标；空间误差从接触半径中扣除，
时间余量从球到达时间中扣除。可行仅指直线路径运动学，不含避障/碰撞。
"""
import math
from ..param import (MAX_LINEAR, MAX_ANGULAR, OMNI_DIST, TURN_THRESHOLD,
                     BALL_CONTROL_RADIUS_M, BLOCK_HORIZON_S, BLOCK_STEP_S,
                     BLOCK_POSITION_TOLERANCE_M, BLOCK_TIME_MARGIN_S)
from .geom import normalize_angle


def calculate_block_point(pose, motion, *, fallen=False, penalized=False,
                          linear_speed=MAX_LINEAR, angular_speed=MAX_ANGULAR,
                          horizon=BLOCK_HORIZON_S, step=BLOCK_STEP_S,
                          position_tolerance=BLOCK_POSITION_TOLERANCE_M,
                          time_margin=BLOCK_TIME_MARGIN_S):
    """返回 target（球员站位）与 ball_position（接触时球位）。

    搜索截至停止或 horizon；静止球单独返回 stationary，不称为拦住前进。
    均匀时间采样外加入几何垂足及可行性边界细化；最近为此离散模型下结果。
    """
    if not all(math.isfinite(v) and v > 0 for v in (linear_speed, angular_speed, horizon, step)):
        raise ValueError('speeds, horizon and step must be finite and positive')
    if not (0 <= position_tolerance < BALL_CONTROL_RADIUS_M and math.isfinite(time_margin) and time_margin >= 0):
        raise ValueError('invalid tolerance or time margin')
    radius = BALL_CONTROL_RADIUS_M-position_tolerance
    base = dict(status='unavailable', target=None, horizon_s=horizon, step_s=step,
                position_tolerance_m=position_tolerance, time_margin_s=time_margin,
                effective_radius_m=radius, model='nearest_block_sampled_v1')
    if pose is None or fallen or penalized:
        return dict(base, reason='no_pose' if pose is None else 'fallen' if fallen else 'penalized')
    if not all(math.isfinite(v) for v in (pose.x, pose.y, pose.theta)):
        return dict(base, reason='no_pose')
    start = motion.predict_ball_position(0)
    stop = motion.predict_ball_stop()
    if start is None:
        return dict(base, reason='no_ball')
    if stop is None:
        return dict(base, reason='unknown_velocity')
    field = getattr(getattr(motion, 'context', None), 'field', None)
    def inside(p):
        return field is None or (abs(p[0]) <= field.length/2 and abs(p[1]) <= field.width/2)
    def candidate(t):
        ball = motion.predict_ball_position(t)
        dx, dy = ball[0]-pose.x, ball[1]-pose.y
        distance = math.hypot(dx, dy)
        move = max(0, distance-radius)
        target = (pose.x+dx*move/distance, pose.y+dy*move/distance) if distance else (pose.x, pose.y)
        angle = abs(normalize_angle(math.atan2(dy, dx)-pose.theta))
        turn = max(0, angle-TURN_THRESHOLD)/angular_speed if move > OMNI_DIST else 0
        arrival = turn+move/linear_speed
        valid = inside(ball) and inside(target)
        # 已经覆盖球时无需等待准备余量。
        feasible = valid and (move <= 1e-9 or arrival+time_margin <= t)
        return dict(target=target, ball_position=ball, ball_time_s=t,
                    move_distance_m=move, turn_time_s=turn, move_time_s=move/linear_speed,
                    arrival_time_s=arrival, slack_s=t-arrival), feasible
    if not inside(start):
        return dict(base, reason='ball_outside_field')
    end = min(horizon, stop['time_s'])
    if stop['distance_m'] <= 1e-8:
        detail, _ = candidate(0)
        return dict(base, **detail, status='stationary', reason='ball_stopped') if inside(detail['target']) else dict(base, reason='outside_field')
    times = {0.0, end}
    times.update(min(i*step, end) for i in range(1, math.ceil(end/step)+1))
    # 精确加入球路上离机器人最近的垂足对应时刻，避免快球错过短窗口。
    finish = motion.predict_ball_position(end)
    vx, vy = finish[0]-start[0], finish[1]-start[1]
    norm = vx*vx+vy*vy
    if norm > 0:
        fraction = max(0, min(1, ((pose.x-start[0])*vx+(pose.y-start[1])*vy)/norm))
        desired = math.sqrt(norm)*fraction
        lo, hi = 0.0, end
        for _ in range(24):
            mid = (lo+hi)/2
            if math.dist(start, motion.predict_ball_position(mid)) < desired:
                lo = mid
            else:
                hi = mid
        times.add((lo+hi)/2)
    best = None
    previous = None
    for t in sorted(times):
        detail, feasible = candidate(t)
        choices = [(detail, feasible)]
        if previous is not None and previous[1] != feasible:
            lo, hi = previous[0], t
            for _ in range(16):
                mid = (lo+hi)/2
                if candidate(mid)[1] == previous[1]:
                    lo = mid
                else:
                    hi = mid
            choices.append(candidate(hi if feasible else lo))
        for value, valid in choices:
            if valid and (best is None or (value['move_distance_m'], value['ball_time_s']) < (best['move_distance_m'], best['ball_time_s'])):
                best = value
        previous = (t, feasible)
    if best is None:
        return dict(base, reason='no_feasible_point')
    return dict(base, **best, status='reachable')
