"""全队单一传球计划：共享目标、观测确认、角色交接与传后支援。

不调用 SDK；所有坐标为场地米、角度为弧度、时限为仿真秒。
预测与球权判断均为操作性估计，不把动作请求或近球当作触球证据。
"""
from dataclasses import dataclass, field
import math
from typing import Any

from . import param as P
from .intents import (Role, RoleTask, MoveIntent, FaceIntent, StopIntent,
                      KickIntent, KickTarget)
from .utils.geom import angle_to, normalize_angle, behind_ball
from .utils.kick_lane import check_kick_lane, point_segment
from .utils.free_position import nearest_free_position
from .utils import goalkeeper
from . import receiving

Point = tuple[float, float]


@dataclass
class PassPlan:
    id: int
    passer: int
    receiver: int
    source: str
    target: Point
    origin: Point
    phase_key: tuple
    started: float
    stage: str = 'prepare'
    stage_since: float = 0.0
    ready_since: float | None = None
    release_since: float | None = None
    received_since: float | None = None
    requested_at: float | None = None
    released_at: float | None = None
    last_sample: float | None = None
    last_reselect: float = 0.0
    reselects: int = 0
    receiver_ready: bool = False
    passer_ready: bool = False
    flight_range: tuple[float, float] = (0.0, 0.0)
    contact: Point | None = None
    support_target: Point | None = None
    support_side: int = 0
    support_updated: float = 0.0
    evidence: str = 'not_requested'
    reception: receiving.Reception = field(default_factory=receiving.Reception)


@dataclass
class SupportTask:
    plan_id: int
    passer: int
    receiver: int
    target: Point | None
    side: int
    until: float
    ball_anchor: Point
    phase_key: tuple


@dataclass
class TeamPassing:
    active: PassPlan | None = None
    support: SupportTask | None = None
    kickoff_receiver: int | None = None
    sequence: int = 0
    cooldown_until: float = 0.0
    last_time: float | None = None
    events: list[dict] = field(default_factory=list)
    exit_protections: dict[int, receiving.ExitProtection] = field(default_factory=dict)


def _state(store):
    if not hasattr(store, 'passing'):
        store.passing = TeamPassing()
    return store.passing


def _xy(p):
    return (p.pose.x, p.pose.y)


def _ball(ctx):
    return (ctx.ball.x, ctx.ball.y)


def _available(p):
    return (p is not None and p.pose is not None and not p.is_fallen
            and not p.is_penalized and p.mode == 'walk')


def _phase_key(ctx):
    from .role_allocator import get_phase
    g = ctx.game
    return (get_phase(ctx).value, g.set_play.value if g else None,
            g.first_half if g else None)


def _allowed(ctx):
    return _phase_key(ctx)[0] in ('normal', 'our_kickoff', 'our_set_play')


def _fresh(ctx):
    return ctx.ball is not None and 0 <= ctx.now-ctx.ball.last_seen_at <= P.PASS_BALL_MAX_AGE_S


def _inside(ctx, point):
    return (all(math.isfinite(v) for v in point)
            and abs(point[0]) <= ctx.field.length/2-P.NAV_BODY_RADIUS_M
            and abs(point[1]) <= ctx.field.width/2-P.NAV_BODY_RADIUS_M)


def _event(state, plan, now, event, reason):
    state.events.append(dict(id=plan.id, time=now, event=event, reason=reason,
                             passer=plan.passer, receiver=plan.receiver,
                             target=plan.target, source=plan.source))
    del state.events[:-32]


def _finish(ctx, players, store, reason, success=False):
    state = _state(store)
    plan = state.active
    if plan is None:
        return
    _event(state, plan, ctx.now, 'received' if success else 'cancelled', reason)
    state.active = None
    protection = state.exit_protections.get(plan.receiver)
    if protection is not None:
        protection.recovery_until = ctx.now + (0. if success else P.RECEIVE_RECOVERY_S)
        protection.reason = reason
        protection.stable_since = None
    state.cooldown_until = ctx.now+P.PASS_COOLDOWN_S
    state.support = None
    for p in players:
        if p.id == plan.passer and p.id == P.GUARD_PLAYER_ID:
            p._guard_memory = None
    if success:
        if _phase_key(ctx)[0] == 'our_kickoff':
            state.kickoff_receiver = plan.receiver
        store.normal_attacker = plan.receiver
        if plan.passer != P.GUARD_PLAYER_ID:
            state.support = SupportTask(plan.id, plan.passer, plan.receiver,
                plan.support_target, plan.support_side, ctx.now+P.PASS_SUPPORT_HOLD_S,
                _ball(ctx), _phase_key(ctx))


def validate_plan(ctx: Any, players: list, store: Any) -> None:
    """角色分配前清理无效计划；只读取观测，不产生控制副作用。"""
    state = _state(store)
    if state.last_time is not None and ctx.now < state.last_time:
        store.passing = state = TeamPassing()
    state.last_time = ctx.now
    own = {p.id: p for p in players}
    if _phase_key(ctx)[0] != 'our_kickoff' or not _available(own.get(state.kickoff_receiver)):
        state.kickoff_receiver = None
    # 出球保护按球员持有，不随普通角色切换清除；新的比赛阶段与无效观测显式清理。
    phase = _phase_key(ctx)
    for pid, protection in list(state.exit_protections.items()):
        if protection.phase_key[0] in ('our_kickoff', 'our_set_play') and phase[0] == 'normal' and phase[2] == protection.phase_key[2]:
            protection.phase_key = phase
        if (phase != protection.phase_key or not _allowed(ctx)
                or not _available(own.get(pid)) or not _fresh(ctx)):
            del state.exit_protections[pid]
    plan = state.active
    if plan:
        reason = None
        current_phase = _phase_key(ctx)
        # 本方重开正常释放球权不是新一轮重开；出球/接球承诺必须延续。
        # 准备中重开超时、重新摆球、对方重开及上下半场切换仍取消。
        continuation = (plan.stage in ('release', 'receive')
                        and plan.phase_key[0] in ('our_kickoff', 'our_set_play')
                        and current_phase[0] == 'normal'
                        and current_phase[2] == plan.phase_key[2])
        if continuation:
            plan.phase_key = current_phase
            _event(state, plan, ctx.now, 'phase_continued', 'restart_released_to_normal')
        if not _allowed(ctx) or current_phase != plan.phase_key:
            reason = 'phase_changed'
        elif not all(_available(own.get(pid)) for pid in (plan.passer, plan.receiver)):
            reason = 'participant_unavailable'
        elif not _fresh(ctx):
            reason = 'ball_unavailable'
        elif abs(ctx.ball.x) > ctx.field.length/2 or abs(ctx.ball.y) > ctx.field.width/2:
            reason = 'ball_outside'
        else:
            motion = own[plan.passer].motion
            details = motion.ball_prediction_details()
            if goalkeeper.incoming_shot(ctx, motion, details) is not None:
                reason = 'goal_threat'
        if reason:
            _finish(ctx, players, store, reason)
    support = state.support
    if (support and support.phase_key[0] in ('our_kickoff', 'our_set_play')
            and _phase_key(ctx)[0] == 'normal' and _phase_key(ctx)[2] == support.phase_key[2]):
        support.phase_key = _phase_key(ctx)
    if support and (not _allowed(ctx) or _phase_key(ctx) != support.phase_key
                    or ctx.now >= support.until or not _fresh(ctx)
                    or not all(_available(own.get(pid)) for pid in (support.passer, support.receiver))
                    or math.dist(_ball(ctx), support.ball_anchor) > P.PASS_SUPPORT_BALL_SHIFT_M):
        state.support = None


def constrain_roles(ctx: Any, players: list, store: Any, assignments: dict) -> dict:
    """临时任务叠加在基础角色上；固定守门员永远不被接球任务替换。"""
    result = dict(assignments)
    state = _state(store)
    plan = state.active
    if plan:
        receiving = plan.stage == 'receive'
        for pid, role in ((plan.passer, Role.FREEMAN if receiving else Role.ATTACKER),
                          (plan.receiver, Role.ATTACKER if receiving else Role.FREEMAN)):
            if pid != P.GUARD_PLAYER_ID and result[pid].role != Role.UNAVAILABLE:
                result[pid] = RoleTask(role, reason='pass_plan')
        store.normal_attacker = plan.receiver if receiving else (
            plan.passer if plan.passer != P.GUARD_PLAYER_ID else plan.receiver)
    elif state.support:
        # 交接后短期固定接球者进攻，防止传球者立即抢回角色。
        result[state.support.receiver] = RoleTask(Role.ATTACKER, reason='pass_handoff')
        result[state.support.passer] = RoleTask(Role.FREEMAN, reason='post_pass')
        store.normal_attacker = state.support.receiver
    elif state.kickoff_receiver is not None:
        for pid in result:
            if pid != P.GUARD_PLAYER_ID and result[pid].role != Role.UNAVAILABLE:
                result[pid] = RoleTask(Role.ATTACKER if pid == state.kickoff_receiver else Role.FREEMAN)
        store.normal_attacker = state.kickoff_receiver
    return result


def _lane_safe(ctx, passer, receiver, target):
    origin = _ball(ctx)
    distance = math.dist(origin, target)
    if not _inside(ctx, target) or not P.PASS_MIN_DISTANCE_M <= distance <= P.PASS_MAX_DISTANCE_M:
        return False
    if check_kick_lane(ctx.field, origin, target)['blocked']:
        return False
    for pid, robot in ctx.teammates.items():
        if pid not in (passer, receiver) and (robot.pose is None or point_segment(
                (robot.pose.x, robot.pose.y), origin, target) <= P.TEAMMATE_RADIUS+P.PASS_LANE_MARGIN_M):
            return False
    for robot in ctx.opponents.values():
        if robot.pose is None:
            return False
        point = (robot.pose.x, robot.pose.y)
        if point_segment(point, origin, target) <= P.OPPONENT_RADIUS+P.PASS_LANE_MARGIN_M:
            return False
        # 最慢假设球速做保守争抢筛选；不把力度映射成已知初速。
        for i in range(1, 9):
            part = i/8
            contact = tuple(origin[j]+part*(target[j]-origin[j]) for j in (0, 1))
            enemy = max(0, math.dist(point, contact)-P.BALL_CONTROL_RADIUS_M)/P.MAX_LINEAR
            if enemy <= part*distance/P.PASS_ASSUMED_SPEED_MIN_MPS+P.PASS_RACE_MARGIN_S:
                return False
    return True


def _prepare_route(ctx, passer, target):
    ball = _ball(ctx)
    stage = behind_ball(*ball, target, P.CHASE_BEHIND_M)
    if not _inside(ctx, stage):
        return None
    direction = angle_to(*ball, *target)
    if passer.id == P.GUARD_PLAYER_ID:
        region = goalkeeper.bounds(ctx)
        if (not region[0] <= ball[0] <= region[1]
                or abs(ball[1]) > ctx.field.goal_area_width/2
                or math.dist(_xy(passer), ball) > P.GUARD_CLEAR_REACH_M
                or not region[0] <= stage[0] <= region[1]
                or abs(stage[1]) > ctx.field.goal_area_width/2-P.GUARD_BODY_RADIUS_M
                or target[0]-ball[0] < P.GUARD_PASS_MIN_FORWARD_M):
            return None
        route = goalkeeper.approach_route(ctx, passer.id, passer.pose, ball, stage)
        details = passer.motion.ball_prediction_details()
        if route is None or not details or not details.get('velocity_estimated'):
            return None
        if math.hypot(*details['velocity']) > P.GUARD_CLEAR_MAX_BALL_SPEED:
            return None
        finish = route[1]+abs(normalize_angle(direction-passer.pose.theta))/P.GUARD_TURN_SPEED+P.GUARD_KICK_PREPARE_S
        future = passer.motion.predict_ball_position(finish)
        if (future is None or math.dist(ball, future) > P.GUARD_CONTROL_DRIFT_M
                or not goalkeeper.safe_race(ctx, passer.id, passer.threat, future, finish)):
            return None
        return stage, route[0], direction
    # 普通传球仍由安全导航规划接近；到位前避球，防止穿球抄近路。
    return stage, stage, direction


def _choose_target(ctx, passer, receiver, preferred):
    free = nearest_free_position(ctx, receiver.pose, P.FREEMAN_BALL_RADIUS_M,
                                 P.FREEMAN_OPPONENT_RADIUS_M, P.FREEMAN_GOAL_LINE_MARGIN_M)
    seeds = [preferred, _xy(receiver)] + ([free] if free else [])
    candidates = sorted({(point[0]+dx, point[1]+dy) for point in seeds
                         for dx, dy in ((0, 0), (.5, 0), (-.5, 0), (0, .5), (0, -.5))})
    choices = []
    for target in candidates:
        receiver_time = math.dist(_xy(receiver), target)/P.NAV_LATERAL_SPEED_MPS
        if receiver_time > P.PASS_PREPARE_TIMEOUT_S/2 or not _lane_safe(ctx, passer.id, receiver.id, target):
            continue
        route = _prepare_route(ctx, passer, target)
        if route is None:
            continue
        passer_time = math.dist(_xy(passer), route[0])/P.NAV_FORWARD_SPEED_MPS
        if passer_time > P.PASS_PREPARE_TIMEOUT_S:
            continue
        choices.append((max(receiver_time, passer_time)+.1*math.dist(_ball(ctx), target), target))
    return min(choices)[1] if choices else None


def _hold(p, ball):
    return FaceIntent(angle_to(*_xy(p), *ball))


def _support_safe(ctx, passer, receiver, target, contact):
    if not _inside(ctx, target):
        return False
    start, ball = _xy(passer), _ball(ctx)
    home = (-ctx.field.length/2-contact[0], -contact[1])
    if (target[0]-contact[0])*home[0]+(target[1]-contact[1])*home[1] <= 0:
        return False
    if check_kick_lane(ctx.field, start, target)['blocked']:
        return False
    # 只接受已验算直线，执行层 direct_path 禁止绕行后横穿接球走廊。
    # 起点已在球路附近时允许单调离开，不能向球路更深处移动。
    corridor = 2*P.NAV_BODY_RADIUS_M+P.NAV_SAFETY_MARGIN_M
    segments = ((ball, contact), (_xy(receiver), contact))
    for a, b in segments:
        previous = point_segment(start, a, b)
        for i in range(1, 17):
            point = tuple(start[j]+i/16*(target[j]-start[j]) for j in (0, 1))
            distance = point_segment(point, a, b)
            if distance < corridor and distance < previous-1e-6:
                return False
            previous = distance
        if previous < corridor:
            return False
    for pid, robot in ctx.teammates.items():
        if pid != passer.id and robot.pose is not None and point_segment(
                (robot.pose.x, robot.pose.y), start, target) < corridor:
            return False
    return all(r.pose is not None and point_segment((r.pose.x, r.pose.y), start, target)
               > corridor+P.NAV_OPPONENT_EXTRA_M for r in ctx.opponents.values())


def _support_intent(ctx, passer, receiver, plan):
    contact = plan.contact or plan.target
    if ctx.now-plan.support_updated >= P.PASS_SUPPORT_REFRESH_S:
        plan.support_updated = ctx.now
        if plan.support_target is None or not _support_safe(ctx, passer, receiver, plan.support_target, contact):
            dx, dy = -ctx.field.length/2-contact[0], -contact[1]
            length = math.hypot(dx, dy)
            ux, uy = (dx/length, dy/length) if length > 1e-6 else (-1.0, 0.0)
            choices = []
            for side in (-1, 1):
                target = (contact[0]+P.PASS_SUPPORT_BACK_M*ux-side*P.PASS_SUPPORT_SIDE_M*uy,
                          contact[1]+P.PASS_SUPPORT_BACK_M*uy+side*P.PASS_SUPPORT_SIDE_M*ux)
                if _support_safe(ctx, passer, receiver, target, contact):
                    choices.append((0 if side == plan.support_side else 1, math.dist(_xy(passer), target), side, target))
            if choices:
                _, _, plan.support_side, plan.support_target = min(choices)
            else:
                plan.support_target = None
    if plan.support_target is None or not _support_safe(ctx, passer, receiver, plan.support_target, contact):
        return _hold(passer, _ball(ctx))
    return MoveIntent(plan.support_target, face=angle_to(*_xy(passer), *_ball(ctx)),
                      avoid_ball=True, avoid_robots=True, maintain_face=True,
                      direct_path=True, motion_priority=20, arrive_dist=.15)


def _coordinate_pass(ctx: Any, players: list, assignments: dict,
                    preliminary: Any, store: Any) -> dict:
    """收齐提议后统一生成双方意图，保持输入不变、编号完整。"""
    state = _state(store)
    own = {p.id: p for p in players}
    final = dict(preliminary)
    # 守门紧急动作优先；即便不产生进门预测，也不能等接球者就绪。
    emergency = any(p.action.startswith(('guard:block', 'guard:close_clear')) for p in players)
    interrupted = state.active if emergency else None
    if emergency:
        _finish(ctx, players, store, 'emergency_defence')
        state.support = None
    if interrupted:
        for pid in (interrupted.passer, interrupted.receiver):
            if pid != P.GUARD_PLAYER_ID:
                final[pid] = StopIntent()
    proposals = [(p, getattr(p, 'pass_proposal', None)) for p in sorted(players, key=lambda p: p.id)]
    # 未获团队许可的队友传球一律不发；解围/空位移球不受此限制。
    for p, proposal in proposals:
        if proposal is not None and isinstance(final[p.id], KickIntent):
            final[p.id] = StopIntent()
    if state.active is None and not emergency and _allowed(ctx) and _fresh(ctx) and ctx.now >= state.cooldown_until:
        for passer, proposal in proposals:
            if proposal is None or not _available(passer):
                continue
            receiver = own.get(proposal.receiver_id)
            if (not _available(receiver) or receiver.id in (passer.id, P.GUARD_PLAYER_ID)):
                continue
            target = _choose_target(ctx, passer, receiver, proposal.target)
            if target is None:
                state.cooldown_until = ctx.now+P.PASS_RESELECT_INTERVAL_S
                continue
            state.sequence += 1
            distance = math.dist(_ball(ctx), target)
            state.active = PassPlan(state.sequence, passer.id, receiver.id, proposal.source,
                target, _ball(ctx), _phase_key(ctx), ctx.now, stage_since=ctx.now,
                last_reselect=ctx.now, support_updated=ctx.now-P.PASS_SUPPORT_REFRESH_S,
                flight_range=(distance/P.PASS_ASSUMED_SPEED_MAX_MPS, distance/P.PASS_ASSUMED_SPEED_MIN_MPS))
            state.support = None
            _event(state, state.active, ctx.now, 'prepare', 'proposal_accepted')
            break
    plan = state.active
    if plan is None:
        support = state.support
        if support and not emergency:
            passer, receiver = own[support.passer], own[support.receiver]
            if support.target and _support_safe(ctx, passer, receiver, support.target, _xy(receiver)):
                final[passer.id] = MoveIntent(support.target, face=angle_to(*_xy(passer), *_ball(ctx)),
                    avoid_ball=True, avoid_robots=True, maintain_face=True, direct_path=True, motion_priority=20)
            else:
                final[passer.id] = _hold(passer, _ball(ctx))
            passer.action = 'freeman:post_pass_support'
        return final
    passer, receiver = own[plan.passer], own[plan.receiver]
    # 所有失败分支先停止双方本帧旧意图，下帧正常分配，绝不泄漏预先生成的射门。
    def cancel(reason):
        _finish(ctx, players, store, reason)
        final[receiver.id] = StopIntent()
        final[passer.id] = StopIntent()
        receiver.action = 'freeman:pass_cancelled:'+reason
        passer.action = ('guard' if passer.id == P.GUARD_PLAYER_ID else 'attack')+':pass_cancelled:'+reason
        return final
    ball = _ball(ctx)
    sample = ctx.ball.last_seen_at
    fresh_sample = plan.last_sample is None or sample > plan.last_sample
    details = passer.motion.ball_prediction_details() or {}
    velocity = details.get('velocity', (0.0, 0.0))
    speed = math.hypot(*velocity) if details.get('velocity_estimated') else None
    if plan.stage == 'release' and ctx.now-plan.stage_since > P.PASS_RELEASE_TIMEOUT_S:
        return cancel('release_timeout')
    if plan.stage in ('prepare', 'release'):
        dx, dy = plan.target[0]-plan.origin[0], plan.target[1]-plan.origin[1]
        length = math.hypot(dx, dy)
        displacement = math.dist(ball, plan.origin)
        aligned_motion = (speed is not None and speed >= P.PASS_RELEASE_SPEED_MPS
                          and (velocity[0]*dx+velocity[1]*dy)/max(speed*length, 1e-9) >= .7
                          and point_segment(ball, plan.origin, plan.target) <= P.PASS_CONTACT_DEVIATION_M)
        if plan.stage == 'prepare' and displacement > P.PASS_BALL_DRIFT_M:
            return cancel('ball_moved_before_request')
        if plan.stage == 'release' and displacement >= P.PASS_RELEASE_DISTANCE_M:
            if not aligned_motion:
                return cancel('release_deviated')
            if fresh_sample:
                plan.release_since = sample if plan.release_since is None else plan.release_since
                if sample-plan.release_since >= P.PASS_RELEASE_CONFIRM_S:
                    plan.stage, plan.stage_since, plan.released_at = 'receive', ctx.now, ctx.now
                    plan.evidence = 'observed_motion_after_request_not_touch_confirmation'
                    _event(state, plan, ctx.now, 'receive', plan.evidence)
                    state.exit_protections[receiver.id] = receiving.ExitProtection(
                        receiver.id, plan.id, plan.phase_key, ctx.now, 'active_receive')
            # 证据积累时也立即退出踢球，禁止连续追踢刚离脚的球。
            if plan.stage == 'release':
                plan.last_sample = sample
                final[passer.id], final[receiver.id] = _hold(passer, ball), _hold(receiver, ball)
                return final
        elif fresh_sample:
            plan.release_since = None
    plan.last_sample = sample
    if plan.stage in ('prepare', 'release'):
        deadline = P.PASS_PREPARE_TIMEOUT_S if plan.stage == 'prepare' else P.PASS_RELEASE_TIMEOUT_S
        if ctx.now-plan.stage_since > deadline:
            return cancel(plan.stage+'_timeout')
        safe = _lane_safe(ctx, passer.id, receiver.id, plan.target)
        route = _prepare_route(ctx, passer, plan.target)
        if not safe or route is None:
            if plan.stage == 'release':
                return cancel('release_route_unsafe')
            plan.ready_since = None
            plan.passer_ready = plan.receiver_ready = False
            if ctx.now-plan.last_reselect >= P.PASS_RESELECT_INTERVAL_S:
                plan.last_reselect = ctx.now
                plan.reselects += 1
                if plan.reselects > P.PASS_MAX_RESELECTS:
                    return cancel('no_safe_target')
                target = _choose_target(ctx, passer, receiver, plan.target)
                if target is not None:
                    plan.target, plan.origin = target, ball
                    distance = math.dist(ball, target)
                    plan.flight_range = (distance/P.PASS_ASSUMED_SPEED_MAX_MPS, distance/P.PASS_ASSUMED_SPEED_MIN_MPS)
            final[passer.id], final[receiver.id] = StopIntent(), _hold(receiver, ball)
            return final
        stage, waypoint, direction = route
        receiver_angle = angle_to(*_xy(receiver), *ball)
        plan.receiver_ready = (math.dist(_xy(receiver), plan.target) <= P.PASS_READY_DISTANCE_M
            and abs(normalize_angle(receiver_angle-receiver.pose.theta)) <= P.PASS_READY_ANGLE_RAD)
        plan.passer_ready = (math.dist(_xy(passer), stage) <= P.GUARD_STAGE_TOLERANCE_M
            and abs(normalize_angle(direction-passer.pose.theta)) <= P.PASS_READY_ANGLE_RAD)
        if plan.stage == 'prepare':
            if plan.receiver_ready and plan.passer_ready:
                if fresh_sample:
                    plan.ready_since = sample if plan.ready_since is None else plan.ready_since
                if fresh_sample and plan.ready_since is not None and sample-plan.ready_since >= P.PASS_READY_HOLD_S:
                    plan.stage, plan.stage_since, plan.requested_at = 'release', ctx.now, ctx.now
                    plan.origin, plan.evidence = ball, 'kick_requested_only'
                    _event(state, plan, ctx.now, 'release', plan.evidence)
            else:
                plan.ready_since = None
        elif not plan.receiver_ready:
            return cancel('receiver_left_before_release')
        final[receiver.id] = (FaceIntent(receiver_angle) if plan.receiver_ready else
            MoveIntent(plan.target, face=receiver_angle, avoid_ball=True, avoid_robots=True,
                       maintain_face=True, arrive_dist=.08, motion_priority=85))
        if plan.stage == 'release':
            final[passer.id] = KickIntent(KickTarget.position(plan.target), style='pass')
        else:
            final[passer.id] = (FaceIntent(direction) if plan.passer_ready else
                MoveIntent(waypoint, face=direction, avoid_ball=passer.id != P.GUARD_PLAYER_ID,
                    avoid_robots=True, maintain_face=True, arrive_dist=.06,
                    direct_path=passer.id == P.GUARD_PLAYER_ID, motion_priority=80))
        passer.action = ('guard' if passer.id == P.GUARD_PLAYER_ID else 'attack')+':pass_'+plan.stage
        receiver.action = 'freeman:receive_prepare'
        return final
    if ctx.now-plan.stage_since > P.PASS_RECEIVE_TIMEOUT_S:
        return cancel('receive_timeout')
    if point_segment(ball, plan.origin, plan.target) > P.PASS_CONTACT_DEVIATION_M:
        return cancel('ball_deviated')
    if any(r.pose is not None and math.dist((r.pose.x, r.pose.y), ball) < P.BALL_CONTROL_RADIUS_M
           and math.dist((r.pose.x, r.pose.y), ball)+.15 < math.dist(_xy(receiver), ball)
           for r in ctx.opponents.values()):
        return cancel('opponent_near_ball')
    reception_intent, outcome = receiving.receive(ctx, receiver, plan.reception)
    if outcome not in (None, 'received'):
        return cancel(outcome)
    intercept = receiver.ball_intercept or {}
    contact = intercept.get('target')
    if plan.reception.buffer_since is None and (
            intercept.get('score_s') is None or intercept['score_s'] >
            P.PASS_RECEIVE_TIMEOUT_S-(ctx.now-plan.stage_since)):
        return cancel('receiver_cannot_arrive')
    if contact is not None and _inside(ctx, contact):
        plan.contact = tuple(contact)
    plan.received_since = plan.reception.stable_since
    final[receiver.id] = reception_intent
    receiver.action = 'attack:receiving'
    if passer.id == P.GUARD_PLAYER_ID:
        # 恢复现有守门策略，但本帧不允许对已传出的球再次组织出球。
        if isinstance(final[passer.id], KickIntent) or getattr(passer, 'pass_proposal', None) is not None:
            final[passer.id] = MoveIntent(goalkeeper.preposition(ctx, passer.pose, passer.threat),
                face=angle_to(*_xy(passer), *ball), avoid_ball=True, maintain_face=True, motion_priority=60)
        passer._guard_memory = None
        passer.action = 'guard:post_pass_home'
    elif ctx.now-plan.released_at < P.PASS_SUPPORT_YIELD_S:
        final[passer.id] = _hold(passer, ball)
        passer.action = 'freeman:post_pass_yield'
    else:
        final[passer.id] = _support_intent(ctx, passer, receiver, plan)
        passer.action = 'freeman:post_pass_support'
    if outcome == 'received':
        _finish(ctx, players, store, 'observed_receive_proximity_and_relative_speed', success=True)
    return final


def coordinate_pass(ctx: Any, players: list, assignments: dict,
                    preliminary: Any, store: Any) -> dict:
    """在所有传球返回路径后统一施加首次出球保护，避免下一帧普通射门绕过。"""
    final = _coordinate_pass(ctx, players, assignments, preliminary, store)
    state = _state(store)
    for player in players:
        protection = state.exit_protections.get(player.id)
        if protection is None or (state.active and state.active.receiver == player.id):
            continue
        if not _available(player) or not _fresh(ctx):
            final[player.id] = StopIntent()
            continue
        final[player.id], released = receiving.protect_exit(ctx, player, protection, final[player.id])
        if released:
            del state.exit_protections[player.id]
        else:
            player.action = 'attack:receive_' + protection.phase + ':' + protection.reason
    return final
