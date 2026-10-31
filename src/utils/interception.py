"""截球工具：比较机器人到达时间与球的未来轨迹，不修改观测或执行动作。

输出的 score_s 既用于全队进攻者排序，也与 target 一起用于个人追球决策。
intercept 表示在简化模型中窗口内可达；fallback 只是窗口外追赶的排序后备值，
不能把它当成已找到可实现的拦截。该模型不包含对手争抢、避障耗时或加速度。
"""
import math
from ..param import *
from .geom import normalize_angle

def intercept_score(estimate) -> float:
    """角色分配与威胁排序共用的时间评分；未知值不参与最快竞争。"""
    score = (estimate or {}).get('score_s')
    return score if score is not None and math.isfinite(score) else math.inf

def estimate_ball_intercept(pose, motion, *, fallen=False, penalized=False,
                            linear_speed=MAX_LINEAR, angular_speed=MAX_ANGULAR,
                            approach_normal=None) -> dict:
    """接球机会评分（秒，越小越好），用于 attacker / freeman 分配。

    在短期球轨迹上寻找最早可达点。机器人按最大步速估算，远距离加入
    转向时间；不模拟对手、避障和机器人加速度。窗口内无可达点时，以
    窗口末尾的球位估算剩余追赶时间作为排序后备值，不代表实际截球成功。
    """
    # 可选单位法向量：只允许进入 dot(robot-ball, normal) >= 0 的控球半圆。
    # 不传时维持角色分配原有的完整控球圆；边界属于双方。
    if approach_normal is not None:
        length = math.hypot(*approach_normal)
        if not math.isfinite(length) or length <= 0:
            raise ValueError('approach_normal must be finite and nonzero')
        approach_normal = tuple(v / length for v in approach_normal)
    position = motion.predict_ball_position(0.0)
    result = dict(status='unavailable', score_s=None, intercept_time_s=None)
    if pose is None or position is None or fallen or penalized:
        result['reason'] = (
            'no_pose' if pose is None else 'no_ball' if position is None
            else 'fallen' if fallen else 'penalized'
        )
        return result

    def travel_details(target: tuple[float, float]) -> dict:
        ball_position = target
        if approach_normal is not None:
            # 到闭半圆盘的最近点：先投影到半平面，再限制到控球半径。
            nx, ny = approach_normal
            rx, ry = pose.x - target[0], pose.y - target[1]
            side = rx * nx + ry * ny
            if side < 0:
                rx, ry = rx - side * nx, ry - side * ny
            radius = math.hypot(rx, ry)
            if radius > BALL_CONTROL_RADIUS_M:
                rx, ry = rx * BALL_CONTROL_RADIUS_M / radius, ry * BALL_CONTROL_RADIUS_M / radius
            target = (target[0] + rx, target[1] + ry)
        dx, dy = target[0] - pose.x, target[1] - pose.y
        distance = math.hypot(dx, dy)
        angle = abs(normalize_angle(math.atan2(dy, dx) - pose.theta))
        turn_time = 0.0
        if distance > OMNI_DIST:
            turn_time = max(0.0, angle - TURN_THRESHOLD) / angular_speed
        move_distance = distance if approach_normal is not None else max(0.0, distance - BALL_CONTROL_RADIUS_M)
        move_time = move_distance / linear_speed
        return dict(target=target, ball_position=ball_position,
                    approach_normal=approach_normal, distance_m=distance, turn_angle_rad=angle,
                    turn_time_s=turn_time, move_distance_m=move_distance,
                    move_time_s=move_time, arrival_time_s=turn_time + move_time)

    steps = math.ceil(BALL_INTERCEPT_HORIZON_S / BALL_INTERCEPT_STEP_S)
    for step in range(steps + 1):
        seconds = min(step * BALL_INTERCEPT_STEP_S, BALL_INTERCEPT_HORIZON_S)
        position = motion.predict_ball_position(seconds)
        detail = travel_details(position)
        if detail['arrival_time_s'] <= seconds:
            break
    reachable = detail['arrival_time_s'] <= seconds
    score = seconds if reachable else max(BALL_INTERCEPT_HORIZON_S, detail['arrival_time_s'])
    result = dict(
        **detail, status='intercept' if reachable else 'fallback', score_s=score,
        intercept_time_s=seconds if reachable else None, target_time_s=seconds,
        slack_s=seconds - detail['arrival_time_s'],
        horizon_s=BALL_INTERCEPT_HORIZON_S, step_s=BALL_INTERCEPT_STEP_S,
        control_radius_m=BALL_CONTROL_RADIUS_M, linear_speed_mps=linear_speed,
        angular_speed_rps=angular_speed, turn_threshold_rad=TURN_THRESHOLD,
        omni_distance_m=OMNI_DIST,
    )
    return result

