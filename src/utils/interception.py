"""Pure robot arrival/interception estimate; no robot commands."""
import math
from ..param import *
from .geom import normalize_angle

def estimate_ball_intercept(pose, motion, *, fallen=False, penalized=False) -> dict:
    """接球机会评分（秒，越小越好），用于 attacker / freeman 分配。

    在短期球轨迹上寻找最早可达点。机器人按最大步速估算，远距离加入
    转向时间；不模拟对手、避障和机器人加速度。窗口内无可达点时，以
    窗口末尾的球位估算剩余追赶时间作为排序后备值，不代表实际截球成功。
    """
    position = motion.predict_ball_position(0.0)
    result = dict(status='unavailable', score_s=None, intercept_time_s=None)
    if pose is None or position is None or fallen or penalized:
        result['reason'] = (
            'no_pose' if pose is None else 'no_ball' if position is None
            else 'fallen' if fallen else 'penalized'
        )
        return result

    def travel_details(target: tuple[float, float]) -> dict:
        dx, dy = target[0] - pose.x, target[1] - pose.y
        distance = math.hypot(dx, dy)
        angle = abs(normalize_angle(math.atan2(dy, dx) - pose.theta))
        turn_time = 0.0
        if distance > OMNI_DIST:
            turn_time = max(0.0, angle - TURN_THRESHOLD) / MAX_ANGULAR
        move_distance = max(0.0, distance - BALL_CONTROL_RADIUS_M)
        move_time = move_distance / MAX_LINEAR
        return dict(target=target, distance_m=distance, turn_angle_rad=angle,
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
        control_radius_m=BALL_CONTROL_RADIUS_M, linear_speed_mps=MAX_LINEAR,
        angular_speed_rps=MAX_ANGULAR, turn_threshold_rad=TURN_THRESHOLD,
        omni_distance_m=OMNI_DIST,
    )
    return result

