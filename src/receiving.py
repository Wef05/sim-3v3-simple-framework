"""接球制动和首次出球保护；只产生意图，不假定零速度请求等于已停稳。"""
from dataclasses import dataclass
import math
from typing import Any
from . import param as P
from .intents import MoveIntent, KickIntent, StopIntent
from .actions.kick_planner import resolve_target
from .utils.geom import angle_to, normalize_angle, behind_ball


@dataclass
class Reception:
    phase: str = 'approach'
    buffer_since: float | None = None
    stable_since: float | None = None
    last_sample: float | None = None
    distance: float | None = None
    closing_speed: float | None = None
    relative_speed: float | None = None
    speed_limit: float = 0.0
    brake_distance: float | None = None
    reason: str = ''


@dataclass
class ExitProtection:
    player_id: int
    plan_id: int
    phase_key: tuple
    recovery_until: float
    reason: str
    phase: str = 'recovery'
    stable_since: float | None = None
    last_sample: float | None = None
    target: tuple | None = None


def motion_metrics(ctx: Any, player: Any) -> tuple[float, float, float, float] | None:
    pose, ball = player.pose, ctx.ball
    details = player.motion.ball_prediction_details() or {}
    bv = details.get('velocity') if details.get('velocity_estimated') else None
    tracker = getattr(player, 'teammate_motion', None)
    rv = tracker.velocities.get(player.id) if tracker is not None else None
    if bv is None or rv is None or not all(math.isfinite(v) for v in (*bv, *rv)):
        return None
    dx, dy = ball.x-pose.x, ball.y-pose.y
    distance = math.hypot(dx, dy)
    unit = (dx/max(distance, 1e-9), dy/max(distance, 1e-9))
    closing = max(0., sum((rv[i]-bv[i])*unit[i] for i in (0, 1)))
    own_approach = max(0., sum(rv[i]*unit[i] for i in (0, 1)))
    relative = math.hypot(rv[0]-bv[0], rv[1]-bv[1])
    return distance, closing, own_approach, relative


def hold(ctx: Any, player: Any) -> MoveIntent:
    """保持当前位置并缓慢转向；不追加向球心的平移。"""
    return MoveIntent((player.pose.x, player.pose.y),
        face=angle_to(player.pose.x, player.pose.y, ctx.ball.x, ctx.ball.y),
        maintain_face=True, arrive_dist=.02, avoid_ball=False,
        motion_priority=90, opponent_mode='contest', max_speed=0.,
        max_yaw_rate=P.RECEIVE_MAX_YAW_RADPS)


def receive(ctx: Any, player: Any, state: Reception) -> tuple[MoveIntent, str | None]:
    """返回 (意图, outcome)：近身观测优先于远处预测，缓冲期限不因抖动重置。"""
    ball, pose = ctx.ball, player.pose
    distance = math.hypot(ball.x-pose.x, ball.y-pose.y)
    metrics = motion_metrics(ctx, player)
    state.distance = distance
    state.closing_speed = metrics[1] if metrics else None
    state.relative_speed = metrics[3] if metrics else None
    sample = ball.last_seen_at
    fresh = state.last_sample is None or sample > state.last_sample
    state.last_sample = sample
    if distance <= P.RECEIVE_BUFFER_M and state.buffer_since is None:
        state.buffer_since = ctx.now
    if state.buffer_since is not None:
        state.phase = 'buffer'
        state.speed_limit = 0.
        if distance > P.RECEIVE_BUFFER_EXIT_M:
            return hold(ctx, player), 'ball_escaped'
        stable = (metrics is not None and distance <= P.BALL_CONTROL_RADIUS_M
                  and metrics[3] <= P.PASS_RECEIVE_RELATIVE_SPEED_MPS)
        if not stable:
            state.stable_since = None
        elif fresh and state.stable_since is None:
            state.stable_since = sample
        if stable:
            state.phase = 'settling'
        if fresh and state.stable_since is not None and sample-state.stable_since >= P.PASS_RECEIVE_HOLD_S:
            state.reason = 'stable_observations'
            return hold(ctx, player), 'received'
        if ctx.now-state.buffer_since >= P.RECEIVE_BUFFER_TIMEOUT_S:
            return hold(ctx, player), 'buffer_timeout'
        state.reason = 'waiting_for_stability'
        return hold(ctx, player), None
    intercept = player.ball_intercept or {}
    contact = intercept.get('target')
    if (contact is None or not all(math.isfinite(v) for v in contact)
            or abs(contact[0]) > ctx.field.length/2-P.NAV_BODY_RADIUS_M
            or abs(contact[1]) > ctx.field.width/2-P.NAV_BODY_RADIUS_M):
        return hold(ctx, player), 'no_receivable_contact'
    if metrics is None:
        state.speed_limit = 0.
        state.reason = 'velocity_unknown'
        return hold(ctx, player), None
    # 用实际接近速度决定制动区；逆向来球会进一步降低允许的主动迎球速度。
    remaining = max(0., distance-P.RECEIVE_BUFFER_M)
    approach = max(metrics[1], metrics[2])
    state.brake_distance = (P.RECEIVE_BUFFER_M + approach*P.RECEIVE_CONTROL_DELAY_S
                            + approach**2/(2*P.RECEIVE_DECEL_MPS2))
    safe = max(0., math.sqrt((P.RECEIVE_DECEL_MPS2*P.RECEIVE_CONTROL_DELAY_S)**2
               + 2*P.RECEIVE_DECEL_MPS2*remaining)-P.RECEIVE_DECEL_MPS2*P.RECEIVE_CONTROL_DELAY_S)
    incoming = max(0., metrics[1]-metrics[2])
    state.speed_limit = min(P.RECEIVE_MAX_SPEED_MPS, max(0., safe-incoming))
    state.phase = 'braking' if distance <= state.brake_distance else 'approach'
    state.reason = 'speed_limited_approach'
    dx, dy = contact[0]-pose.x, contact[1]-pose.y
    length = math.hypot(dx, dy)
    # 终点停在接触点前，不将行走目标放进球心。
    ratio = max(0., length-P.RECEIVE_STANDOFF_M)/max(length, 1e-9)
    target = (pose.x+dx*ratio, pose.y+dy*ratio)
    return MoveIntent(target, face=angle_to(pose.x, pose.y, ball.x, ball.y),
        maintain_face=True, arrive_dist=.03, avoid_ball=False, avoid_robots=True,
        motion_priority=90, opponent_mode='contest', max_speed=state.speed_limit,
        max_yaw_rate=P.RECEIVE_MAX_YAW_RADPS), None


def protect_exit(ctx: Any, player: Any, state: ExitProtection, intent: Any) -> tuple[Any, bool]:
    """恢复超时只允许追球，不解除第一次出球的姿态门槛。"""
    if ctx.now < state.recovery_until:
        state.phase, state.reason = 'recovery', 'receive_recovery'
        state.stable_since = None
        if math.dist((player.pose.x, player.pose.y), (ctx.ball.x, ctx.ball.y)) <= P.RECEIVE_BUFFER_EXIT_M:
            return hold(ctx, player), False
        return (StopIntent() if isinstance(intent, KickIntent) else intent), False
    state.phase = 'prepare_kick'
    if not isinstance(intent, KickIntent):
        state.stable_since = None
        state.reason = 'waiting_for_kick_proposal'
        return intent, False
    target = resolve_target(ctx, intent.target)
    current = player.motion.predict_ball_position(0.)
    metrics = motion_metrics(ctx, player)
    if target is None or current is None or metrics is None:
        state.stable_since, state.reason = None, 'observation_unavailable'
        return StopIntent(), False
    ball, pose = ctx.ball, player.pose
    direction = angle_to(ball.x, ball.y, *target)
    ux, uy = math.cos(direction), math.sin(direction)
    dx, dy = ball.x-pose.x, ball.y-pose.y
    ahead, lateral = dx*ux+dy*uy, abs(dx*uy-dy*ux)
    distance = max(metrics[0], math.dist((pose.x, pose.y), current))
    aligned = abs(normalize_angle(direction-pose.theta)) <= P.RECEIVE_KICK_ANGLE_RAD
    behind = .1 <= ahead <= P.RECEIVE_KICK_DISTANCE_M and lateral <= P.RECEIVE_KICK_LATERAL_M
    stable = metrics[3] <= P.PASS_RECEIVE_RELATIVE_SPEED_MPS
    reason = ('ball_too_far' if distance > P.RECEIVE_KICK_DISTANCE_M else
              'not_behind_ball' if not behind else 'heading_unaligned' if not aligned else
              'relative_motion' if not stable else 'stability_hold')
    sample = ball.last_seen_at
    fresh = state.last_sample is None or sample > state.last_sample
    state.last_sample = sample
    if state.target != tuple(target):
        state.stable_since = None
        state.target = tuple(target)
    if reason != 'stability_hold':
        state.stable_since = None
    elif fresh and state.stable_since is None:
        state.stable_since = sample
    state.reason = reason
    if fresh and state.stable_since is not None and sample-state.stable_since >= P.RECEIVE_KICK_STABLE_S:
        state.phase, state.reason = 'released', 'kick_pose_confirmed'
        return intent, True
    if behind:
        # 已在球后时只慢转，不能为准备射门继续挤向球。
        return MoveIntent((pose.x, pose.y), face=direction, maintain_face=True,
            arrive_dist=.02, max_speed=0., max_yaw_rate=P.RECEIVE_MAX_YAW_RADPS), False
    return MoveIntent(behind_ball(ball.x, ball.y, target, P.RECEIVE_STANDOFF_M),
        face=direction, maintain_face=True, avoid_ball=True, avoid_robots=True,
        motion_priority=85, max_speed=.4, max_yaw_rate=P.RECEIVE_MAX_YAW_RADPS), False
