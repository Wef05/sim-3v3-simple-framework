"""自由人协防：三态抢球判断、位置方向回退与有限状态退出迟滞。"""
from dataclasses import dataclass
import math

from ..framework.types import Context, Pose2D
from ..intents import MoveIntent
from ..param import (FREEMAN_COVER_EXIT_S, FREEMAN_COVER_SPEED_MPS,
                     FREEMAN_COVER_LOOKAHEAD_S, FREEMAN_COVER_CLEARANCE_M,
                     SUPPORT_DIST_M, GUARD_PLAYER_ID, BLOCK_TIME_MARGIN_S)
from .ball_motion import BallMotion
from .block_point import calculate_block_point
from .geom import own_goal, clamp, angle_to
from .goalkeeper import travel_time
from .obstacles import collect_obstacles
from .path_planner import segment_clear
from .kick_lane import point_segment


@dataclass
class CoverMemory:
    active: bool = False
    safe_since: float | None = None
    last_time: float | None = None


def cover_trigger(ctx: Context, motion: BallMotion, threat: dict | None) -> tuple[bool, str]:
    """确定抢球优先；未知时使用有效球位及短期方向，不将未知速度当静止。"""
    ball = motion.predict_ball_position(0)
    if ball is None:
        return True, 'ball_unavailable'
    race = (threat or {}).get('can_beat')
    if race is not None:
        return not race, 'race_lost' if not race else 'teammate_first'
    gx, _ = own_goal(ctx)
    dangerous = ball[0] <= gx + ctx.field.penalty_area_length and abs(ball[1]) <= ctx.field.penalty_area_width / 2
    if dangerous:
        return True, 'danger_area'
    details = motion.ball_prediction_details() or {}
    known = details.get('velocity_estimated', False)
    vx, vy = details.get('velocity', (0, 0))
    speed = math.hypot(vx, vy)
    current = details.get('current_speed_mps', speed)
    if speed > 0 and current is not None:
        vx, vy = vx * current / speed, vy * current / speed
    distance = math.dist(ball, (gx, 0))
    toward = (vx * (gx-ball[0]) - vy*ball[1]) / max(distance, 1e-9)
    if ball[0] <= 0:
        if known and toward < -FREEMAN_COVER_SPEED_MPS:
            return False, 'ball_leaving'
        return True, 'own_half_direction' if known else 'own_half_unknown_velocity'
    future = motion.predict_ball_position(FREEMAN_COVER_LOOKAHEAD_S)
    incoming = known and toward > FREEMAN_COVER_SPEED_MPS and future[0] <= 0
    return incoming, 'incoming_own_half' if incoming else 'opponent_half'


def cover_position(ctx: Context, source: tuple[float, float] | None,
                   guard_lane: tuple | None = None) -> tuple[float, float]:
    """门侧前置点；与守门路径重叠时选择近侧空位，始终限制在场内。"""
    gx, gy = own_goal(ctx)
    margin = FREEMAN_COVER_CLEARANCE_M
    if source is None:
        target = (gx + ctx.field.penalty_area_length, ctx.field.goal_width / 2 + margin)
    else:
        distance = math.dist(source, (gx, gy))
        fraction = min(SUPPORT_DIST_M / max(distance, 1e-9), .65)
        target = (source[0] + (gx-source[0])*fraction, source[1]*(1-fraction))
    def bounded(p):
        return (clamp(p[0], gx + margin, ctx.field.length/2-margin),
                clamp(p[1], -ctx.field.width/2+margin, ctx.field.width/2-margin))
    target = bounded(target)
    guard = ctx.teammates.get(GUARD_PLAYER_ID)
    if guard_lane is None and guard is not None and guard.pose is not None:
        point = (guard.pose.x, guard.pose.y)
        guard_lane = (point, point)
    if guard_lane and point_segment(target, *guard_lane) < margin:
        # 不从球门后方绕行。有限候选中优先保持前置位置，其次选空侧。
        candidates = [bounded((target[0]+dx, target[1]+dy))
                      for dx, dy in ((margin, margin), (margin, -margin),
                                     (2*margin, 0), (0, 2*margin), (0, -2*margin))]
        target = max(candidates, key=lambda p: point_segment(p, *guard_lane))
    return target


def freeman_cover(ctx: Context, pid: int, pose: Pose2D, motion: BallMotion,
                  threat: dict | None, memory: CoverMemory) -> tuple[MoveIntent | None, dict]:
    """返回协防意图或 None（进攻接应）；状态只由调用者持有。"""
    if memory.last_time is not None and ctx.now < memory.last_time:
        memory.active, memory.safe_since = False, None
    memory.last_time = ctx.now
    required, reason = cover_trigger(ctx, motion, threat)
    if required:
        memory.active, memory.safe_since = True, None
    elif memory.active:
        if memory.safe_since is None:
            memory.safe_since = ctx.now
        if ctx.now-memory.safe_since >= FREEMAN_COVER_EXIT_S:
            memory.active, memory.safe_since = False, None
        else:
            reason = 'exit_hold:' + reason
    info = dict(mode='cover' if memory.active else 'support', reason=reason,
                race=(threat or {}).get('can_beat'), target=None,
                velocity_known=bool((motion.ball_prediction_details() or {}).get('velocity_estimated')))
    if not memory.active:
        return None, info
    ball = motion.predict_ball_position(0)
    source = ball
    enemy = (threat or {}).get('threat_intercept') or {}
    # 对手先触球后旧球路失效，不能将其触球后的旧预测称为可拦截球路。
    enemy_time = enemy.get('intercept_time_s') if (threat or {}).get('status') == 'ok' else None
    if (threat or {}).get('can_beat') is False and enemy.get('status') == 'intercept':
        source = enemy.get('ball_position', ball)
    details = motion.ball_prediction_details() or {}
    velocity = details.get('velocity', (0, 0))
    toward = ball is not None and sum(v*(g-b) for v, g, b in zip(velocity, own_goal(ctx), ball)) > 0
    block = calculate_block_point(pose, motion) if toward and details.get('velocity_estimated') else {}
    target = block.get('target')
    contact = False
    if block.get('status') == 'reachable':
        obstacles = collect_obstacles(ctx, pid, ball=False, robots=True, goals=True)
        arrival = travel_time(pose, target)
        before_touch = enemy_time is not None and block['ball_time_s'] < enemy_time
        # 只承诺验算过的直线路径；绕障后不能沿用原到达时间。
        contact = (before_touch and arrival + BLOCK_TIME_MARGIN_S <= block['ball_time_s']
                   and segment_clear((pose.x, pose.y), target, obstacles, allow_escape=True))
        info.update(block_time_s=block['ball_time_s'], arrival_time_s=arrival,
                    block_rejected=None if contact else 'timing_path_or_opponent_touch')
    if not contact:
        target = cover_position(ctx, source)
    info.update(target=target, source=source, strategy='ball_path' if contact else 'goal_side')
    face = angle_to(pose.x, pose.y, *source) if source is not None else 0.0
    return MoveIntent(target, face=face, avoid_ball=not contact, avoid_robots=True,
                      direct_path=contact, maintain_face=contact, arrive_dist=.03 if contact else .15,
                      motion_priority=50), info
