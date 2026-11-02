"""使用共享拦球计算结果；低速直通与补射只产生意图，不宣称触球。"""
from __future__ import annotations

from dataclasses import dataclass
import math

from ..framework.types import BallState, Pose2D
from ..intents import FaceIntent, KickIntent, KickTarget, MoveIntent, StopIntent
from ..param import (BALL_MOTION_MAX_GAP_S, CHASE_BEHIND_M, KICK_ENTER_M,
                     KICK_EXIT_M, RECEIVE_BYPASS_SPEED_MPS, RECEIVE_ARRIVE_M,
                     RECEIVE_ARRIVE_EXIT_M)
from .geom import angle_to, behind_ball


@dataclass
class ReceiveMemory:
    phase: str
    last_time: float
    stage: str = 'intercept'
    arrived: bool = False


def receive_intent(
    *, pose: Pose2D | None, ball: BallState | None,
    current_ball: tuple[float, float] | None, intercept: dict | None,
    target: tuple[float, float], aim: KickTarget, style: str,
    now: float, phase: str, speed: float | None, memory: ReceiveMemory | None,
    pose_observed_at: float | None,
) -> tuple[MoveIntent | FaceIntent | KickIntent | StopIntent, ReceiveMemory | None, dict]:
    """intercept 必须是 calculate_block_point 的同帧结果；speed 是当前估计速度。

    不自行偏移、冻结或重选站位。到位迟滞仅决定是否继续行走；
    当前球速严格低于阈值时直接交接，不再累计已被直通覆盖的稳定计时。
    """
    if (memory is None or memory.phase != phase or now < memory.last_time
            or now-memory.last_time > BALL_MOTION_MAX_GAP_S):
        memory = ReceiveMemory(phase, now)
    memory.last_time = now
    detail = dict(stage='unavailable', reason='missing_observation', angle_deg=None,
                  intercept_point=None, outgoing_target=target, contact_confirmed=False,
                  speed_mps=speed, speed_kind='estimated_current',
                  block_point=intercept)
    if (pose is None or ball is None or current_ball is None
            or not all(math.isfinite(v) for v in (now, pose.x, pose.y,
                         ball.x, ball.y, ball.last_seen_at, *current_ball, *target))
            or not 0 <= now-ball.last_seen_at <= BALL_MOTION_MAX_GAP_S):
        return StopIntent(), None, detail
    if (pose_observed_at is None or not math.isfinite(pose_observed_at)
            or not 0 <= now-pose_observed_at <= BALL_MOTION_MAX_GAP_S):
        detail['reason'] = 'invalid_robot_observation'
        return StopIntent(), None, detail
    position = pose.x, pose.y
    distance = max(math.hypot(pose.x-ball.x, pose.y-ball.y),
                   math.hypot(pose.x-current_ball[0], pose.y-current_ball[1]))
    detail['ball_distance_m'] = distance
    point = (intercept or {}).get('target')
    valid = ((intercept or {}).get('status') in ('reachable', 'stationary')
             and point is not None and len(point) == 2
             and all(math.isfinite(v) for v in point))
    rebound = False
    if valid:
        detail['intercept_point'] = point
        detail['intercept_status'] = intercept['status']
        u = (point[0]-current_ball[0], point[1]-current_ball[1])
        v = (target[0]-current_ball[0], target[1]-current_ball[1])
        lengths = math.hypot(*u), math.hypot(*v)
        if min(lengths) > 1e-6:
            dot = u[0]*v[0] + u[1]*v[1]
            detail['angle_deg'] = math.degrees(math.acos(max(-1., min(1., dot / math.prod(lengths)))))
            rebound = dot < 0
    if memory.stage == 'auto_kick' and distance <= KICK_EXIT_M:
        detail.update(stage='auto_kick', reason='kick_hysteresis')
        return KickIntent(aim, style=style), memory, detail
    if memory.stage == 'auto_kick':
        memory.stage = 'intercept'
        memory.arrived = False
    # 低速球无需拦停。使用当前补偿球位，远处仍经导航避球接近球后点。
    # 该判断不依赖预测拦球点；未知、负值或非有限球速不能获得直通资格。
    if speed is not None and math.isfinite(speed) and 0 <= speed < RECEIVE_BYPASS_SPEED_MPS:
        memory.arrived = False
        if distance <= KICK_ENTER_M:
            memory.stage = 'auto_kick'
            detail.update(stage='auto_kick', reason='low_speed_direct')
            return KickIntent(aim, style=style), memory, detail
        memory.stage = 'low_speed_chase'
        detail.update(stage='low_speed_chase', reason='low_speed_out_of_kick_range')
        return MoveIntent(behind_ball(*current_ball, target, CHASE_BEHIND_M), avoid_ball=True), memory, detail
    if not valid:
        detail['reason'] = 'no_feasible_block_point'
        return StopIntent(), None, detail
    if rebound:
        memory.arrived = False
        if distance <= KICK_ENTER_M:
            memory.stage = 'auto_kick'
            detail.update(stage='auto_kick', reason='rebound_over_90')
            return KickIntent(aim, style=style), memory, detail
        detail.update(stage='rebound_chase', reason='rebound_out_of_kick_range')
        return MoveIntent(behind_ball(*(intercept.get('ball_position') or current_ball), target, CHASE_BEHIND_M), avoid_ball=True), memory, detail

    # target 已是计算器给出的机器人站位，不能再当作球位叠加安全偏移。
    distance_to_point = math.dist(position, point)
    if memory.arrived and distance_to_point > RECEIVE_ARRIVE_EXIT_M:
        memory.arrived = False
    elif not memory.arrived and distance_to_point <= RECEIVE_ARRIVE_M:
        memory.arrived = True
    memory.stage = 'wait_ball' if memory.arrived else 'intercept'
    detail.update(stage=memory.stage,
                  reason='await_ball' if memory.arrived else 'go_to_block_point',
                  arrived=memory.arrived, target_distance_m=distance_to_point)
    face = angle_to(pose.x, pose.y, *current_ball)
    if math.dist(position, current_ball) < 1e-6:
        face = pose.theta
    if memory.arrived:
        return FaceIntent(face), memory, detail
    return MoveIntent(tuple(point), face=face, avoid_ball=True,
                      arrive_dist=RECEIVE_ARRIVE_M), memory, detail
