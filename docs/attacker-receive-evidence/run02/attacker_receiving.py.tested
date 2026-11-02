"""先拦球再自动踢球；只产生意图，不把近身低速误报为触球确认。"""
from __future__ import annotations

from dataclasses import dataclass
import math

from ..framework.types import BallState, Pose2D
from ..intents import FaceIntent, KickIntent, KickTarget, MoveIntent, StopIntent
from ..param import (BALL_MOTION_MAX_GAP_S,
                     CHASE_BEHIND_M, KICK_ENTER_M, KICK_EXIT_M,
                     RECEIVE_STABLE_S, RECEIVE_MAX_SPEED_MPS, RECEIVE_STANDOFF_M,
                     RECEIVE_ARRIVE_M, RECEIVE_HANDOFF_M, RECEIVE_REPLAN_M,
                     RECEIVE_DIRECTION_CHANGE_RAD, RECEIVE_ROBOT_STILL_MPS)
from .geom import angle_to, behind_ball


@dataclass
class ReceiveMemory:
    phase: str
    last_time: float
    stage: str = 'intercept'
    stable_since: float | None = None
    last_observed: float | None = None
    wait_point: tuple[float, float] | None = None
    ball_anchor: tuple[float, float] | None = None
    incoming: tuple[float, float] | None = None
    last_pose: tuple[float, float] | None = None
    robot_speed: float | None = None


def receive_intent(
    *, pose: Pose2D | None, ball: BallState | None,
    current_ball: tuple[float, float] | None, intercept: dict | None,
    target: tuple[float, float], aim: KickTarget, style: str,
    now: float, phase: str, speed: float | None, memory: ReceiveMemory | None,
) -> tuple[MoveIntent | FaceIntent | KickIntent | StopIntent, ReceiveMemory | None, dict]:
    """同帧 B→I 与 B→T 的点积为负才跳过拦球；退化向量不分类。

    稳定持续时间按新球观测的时间跨度确认，重复快照不能积累拦球证据。
    自动踢球一旦交接就保持到球超出退出距离，不因角度噪声反复撤销。
    """
    reset = (memory is None or memory.phase != phase or now < memory.last_time
             or now - memory.last_time > BALL_MOTION_MAX_GAP_S)
    if reset:
        memory = ReceiveMemory(phase, now)
    elapsed = now - memory.last_time
    memory.last_time = now
    detail = dict(stage='unavailable', reason='missing_observation', angle_deg=None,
                  intercept_point=None, outgoing_target=target, stable_s=0.0,
                  speed_mps=speed, contact_confirmed=False, wait_point=None,
                  standoff_m=RECEIVE_STANDOFF_M, robot_speed_mps=None)
    if (pose is None or ball is None or current_ball is None
            or not all(math.isfinite(v) for v in (now, pose.x, pose.y,
                         ball.x, ball.y, ball.last_seen_at, *current_ball, *target))
            or not 0 <= now - ball.last_seen_at <= BALL_MOTION_MAX_GAP_S):
        return StopIntent(), None, detail
    position = pose.x, pose.y
    if memory.last_pose is not None and elapsed > 0:
        memory.robot_speed = math.dist(position, memory.last_pose) / elapsed
    memory.last_pose = position
    detail['robot_speed_mps'] = memory.robot_speed
    distance = max(math.hypot(pose.x-ball.x, pose.y-ball.y),
                   math.hypot(pose.x-current_ball[0], pose.y-current_ball[1]))
    detail['ball_distance_m'] = distance
    point = (intercept or {}).get('target')
    valid = ((intercept or {}).get('status') in ('intercept', 'fallback')
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
        memory.stage, memory.stable_since = 'intercept', None
        memory.wait_point = None
    if not valid:
        detail['reason'] = 'no_intercept_estimate'
        return StopIntent(), None, detail
    if rebound:
        memory.stable_since = None
        memory.wait_point = None
        if distance <= KICK_ENTER_M:
            memory.stage = 'auto_kick'
            detail.update(stage='auto_kick', reason='rebound_over_90')
            return KickIntent(aim, style=style), memory, detail
        detail.update(stage='rebound_chase', reason='rebound_out_of_kick_range')
        return MoveIntent(behind_ball(*point, target, CHASE_BEHIND_M), avoid_ball=True), memory, detail

    # 等待点在来球方向的后方，机器人先避球到位，再让来球靠近。
    # 静止/未知速度或退化预测时，沿机器人所在侧留距，不穿过球去找球后点。
    dx, dy = point[0]-current_ball[0], point[1]-current_ball[1]
    length = math.hypot(dx, dy)
    moving = speed is not None and math.isfinite(speed) and speed > RECEIVE_MAX_SPEED_MPS
    incoming = (dx/length, dy/length) if moving and length > 1e-6 else None
    replan = memory.wait_point is None
    if not replan:
        if moving and incoming is not None:
            if memory.incoming is None:
                replan = True
            else:
                dot = sum(a*b for a, b in zip(incoming, memory.incoming))
                wx, wy = memory.wait_point[0]-current_ball[0], memory.wait_point[1]-current_ball[1]
                lateral = abs(wx*incoming[1]-wy*incoming[0])
                passed = wx*incoming[0]+wy*incoming[1] < -RECEIVE_HANDOFF_M
                replan = dot < math.cos(RECEIVE_DIRECTION_CHANGE_RAD) or lateral > RECEIVE_REPLAN_M or passed
        elif not moving:
            # 球已停在别处时重新接近；普通的小幅预测更新不拖着等待点跑。
            replan = (math.dist(current_ball, memory.wait_point) > RECEIVE_HANDOFF_M
                      and math.dist(current_ball, memory.ball_anchor) > RECEIVE_REPLAN_M)
    if replan:
        if incoming is not None:
            direction, anchor = incoming, tuple(point)
        else:
            anchor = current_ball
            dx, dy = pose.x-anchor[0], pose.y-anchor[1]
            length = math.hypot(dx, dy)
            direction = (dx/length, dy/length) if length > 1e-6 else (-math.cos(pose.theta), -math.sin(pose.theta))
        memory.wait_point = (anchor[0]+RECEIVE_STANDOFF_M*direction[0],
                             anchor[1]+RECEIVE_STANDOFF_M*direction[1])
        memory.ball_anchor, memory.incoming = anchor, incoming
        memory.stable_since = None
    wait = memory.wait_point
    arrived = math.dist(position, wait) <= RECEIVE_ARRIVE_M
    detail.update(wait_point=wait, replanned=replan, arrived=arrived)

    # 不再为满足旧 0.35 m 条件主动贴球。到安全等待点且机器人已减速，
    # 才积累近身低速观测；只到预测点或只发停车指令都不足以确认稳定。
    stable = (arrived and distance <= RECEIVE_HANDOFF_M and speed is not None
              and math.isfinite(speed) and 0 <= speed <= RECEIVE_MAX_SPEED_MPS
              and memory.robot_speed is not None and memory.robot_speed <= RECEIVE_ROBOT_STILL_MPS)
    observed = ball.last_seen_at
    if (memory.last_observed is not None
            and (observed < memory.last_observed
                 or observed - memory.last_observed > BALL_MOTION_MAX_GAP_S)):
        memory.stable_since = None
    memory.last_observed = observed
    if not stable:
        memory.stable_since = None
    elif memory.stable_since is None:
        memory.stable_since = observed
    duration = 0.0 if memory.stable_since is None else observed-memory.stable_since
    detail['stable_s'] = duration
    if stable and duration + 1e-9 >= RECEIVE_STABLE_S:
        memory.stage = 'auto_kick'
        detail.update(stage='auto_kick', reason='near_ball_low_speed')
        return KickIntent(aim, style=style), memory, detail
    detail.update(stage='wait_ball' if arrived else 'intercept',
                  reason='stabilizing' if stable else 'await_ball' if arrived else 'go_to_wait_point')
    face = angle_to(pose.x, pose.y, *current_ball)
    if math.dist(position, current_ball) < 1e-6:
        face = pose.theta
    if arrived:
        # 停止平移，仅面向来球；不让动态避球把已到位的接球者继续推走。
        return FaceIntent(face), memory, detail
    return MoveIntent(wait, face=face, avoid_ball=True,
                      arrive_dist=RECEIVE_ARRIVE_M), memory, detail
