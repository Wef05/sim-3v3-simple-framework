"""有限接球姿态搜索；纯计算，时间是保守估计，不是触球或控球证明。"""
from dataclasses import dataclass, field
import math
from typing import Any, Callable

from .. import param as P
from .geom import angle_to, normalize_angle
from .kick_lane import point_segment, check_kick_lane
from .obstacles import collect_obstacles
from .path_planner import segment_clear, path_time

Point = tuple[float, float]


@dataclass
class ReceivePose:
    contact: Point
    waiting: Point
    behind: Point
    attack_target: Point
    heading: float
    receive_heading: float
    approach_path: list[Point]
    adjustment_path: list[Point]
    preferred_region: bool
    enemy_distance: float | None
    prepare_s: float
    attack_ready_s: float
    prepare_margin_s: float | None
    lane_margin_s: float | None
    receive_margin_s: float | None
    selected_at: float


@dataclass
class Selection:
    evaluated_at: float
    anchor: Point
    kickoff: bool
    candidates: list[dict] = field(default_factory=list)
    selected: ReceivePose | None = None
    reason: str = 'no_safe_pose'


def inside(ctx: Any, point: Point) -> bool:
    return (all(math.isfinite(v) for v in point)
            and abs(point[0]) <= ctx.field.length/2-P.PASS_POSE_FIELD_MARGIN_M
            and abs(point[1]) <= ctx.field.width/2-P.PASS_POSE_FIELD_MARGIN_M)


def enemy_time(ctx: Any, point: Point) -> float:
    # 不因对手当前背向球而增加硬性安全预算；允许其立即以最大速度争抢。
    if any(r.pose is None for r in ctx.opponents.values()):
        return 0.
    return min((max(0., math.dist((r.pose.x, r.pose.y), point)
                    -P.BALL_CONTROL_RADIUS_M)/P.MAX_LINEAR
                for r in ctx.opponents.values()), default=math.inf)


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


def nearest_enemy(ctx: Any, point: Point) -> float:
    return min((math.dist((r.pose.x, r.pose.y), point)
                for r in ctx.opponents.values() if r.pose is not None), default=math.inf)


def route(ctx: Any, player_id: int, start: Point, end: Point,
          ball: Point, heading: float) -> list[Point] | None:
    """验证直线或有限侧向折线，控制搜索开销；没有证据的绕路不视为可达。

    球周围允许接近控制距离，但禁止穿球；普通机器人/结构使用导航扫掠半径。
    动作执行仍由原有动态导航复核，不假定这些静态路线保证实际到达。
    """
    if not inside(ctx, end):
        return None
    obstacles = collect_obstacles(ctx, player_id, ball=False, robots=True, goals=True)
    def clear(a, b):
        # 已贴球时只允许单调离开，不能因起点重叠永久卡死，也不能穿球抄近路。
        away = ((a[0]-ball[0])*(b[0]-a[0])+(a[1]-ball[1])*(b[1]-a[1]) >= 0
                and math.dist(a, b) > 1e-9 and math.dist(b, ball) >= P.PASS_POSE_BALL_CLEARANCE_M)
        ball_clear = (point_segment(ball, a, b) >= P.PASS_POSE_BALL_CLEARANCE_M
                      or math.dist(a, ball) < P.PASS_POSE_BALL_CLEARANCE_M and away)
        return inside(ctx, b) and segment_clear(a, b, obstacles, allow_escape=True) and ball_clear
    if clear(start, end):
        return [start, end]
    options = []
    # 两侧折线也能验证球后调整，不允许以到达目标替代整段路径检查。
    dx, dy = end[0]-start[0], end[1]-start[1]
    length = max(math.hypot(dx, dy), 1e-9)
    for offset in (.6, 1.):
        for side in (-1, 1):
            mid = ((start[0]+end[0])/2-side*dy/length*offset,
                   (start[1]+end[1])/2+side*dx/length*offset)
            lateral = (-side*dy/length*offset, side*dx/length*offset)
            corner_a = (start[0]+lateral[0], start[1]+lateral[1])
            corner_b = (end[0]+lateral[0], end[1]+lateral[1])
            for path in ([start, mid, end], [start, corner_a, corner_b, end]):
                if all(clear(a, b) for a, b in zip(path, path[1:])):
                    options.append((path_time(path, heading, omni=True), path))
    return min(options)[1] if options else None


def race(ctx: Any, origin: Point, contact: Point, prepare_s: float,
         after_s: float) -> tuple[float, float, float]:
    distance = math.dist(origin, contact)
    lane = min(enemy_time(ctx, (origin[0]+i/8*(contact[0]-origin[0]),
                               origin[1]+i/8*(contact[1]-origin[1])))
               -prepare_s-i/8*distance/P.PASS_ASSUMED_SPEED_MIN_MPS
               for i in range(1, 9))
    return (enemy_time(ctx, origin)-prepare_s, lane,
            enemy_time(ctx, contact)-prepare_s-distance/P.PASS_ASSUMED_SPEED_MIN_MPS-after_s)


def attack_clear(ctx: Any, contact: Point, target: Point, receiver_id: int) -> bool:
    if check_kick_lane(ctx.field, contact, target)['blocked']:
        return False
    # 这里只验证下一步前向走廊，不把空走廊当成射门或传球成功概率。
    return all(r.pose is not None and point_segment((r.pose.x, r.pose.y), contact, target)
               > P.OPPONENT_RADIUS+P.PASS_LANE_MARGIN_M for r in ctx.opponents.values()) and all(
               r.pose is not None and (math.dist((r.pose.x, r.pose.y), target) < .05 or
               point_segment((r.pose.x, r.pose.y), contact, target) > P.TEAMMATE_RADIUS+P.PASS_LANE_MARGIN_M)
               for pid, r in ctx.teammates.items() if pid != receiver_id)


def evaluate(ctx: Any, passer: Any, receiver: Any, contact: Point, attack: Point,
             anchor: Point, kickoff: bool, prepare_route: Callable,
             selected_at: float, waiting_override: Point | None = None,
             diagnostics: dict | None = None) -> tuple[ReceivePose | None, str]:
    origin = (ctx.ball.x, ctx.ball.y)
    receiver_xy = (receiver.pose.x, receiver.pose.y)
    heading = angle_to(*contact, *attack)
    ux, uy = math.cos(heading), math.sin(heading)
    behind = (contact[0]-P.RECEIVE_STANDOFF_M*ux,
              contact[1]-P.RECEIVE_STANDOFF_M*uy)
    if not inside(ctx, behind) or not attack_clear(ctx, contact, attack, receiver.id):
        return None, 'attack_corridor_or_behind_blocked'
    p_route = prepare_route(ctx, passer, contact)
    if p_route is None:
        return None, 'passer_route_unavailable'
    passer_path = route(ctx, passer.id, (passer.pose.x, passer.pose.y),
                        p_route[0], origin, p_route[2])
    if passer_path is None:
        return None, 'passer_route_unavailable'
    passer_s = (path_time(passer_path, p_route[2], omni=True)
                +abs(normalize_angle(p_route[2]-passer.pose.theta))/P.NAV_TURN_SPEED_RPS)
    # 优先直接站球后；会挡来球时必须侧向等待。到球线的距离含机器人身体。
    waits = [behind] + [(behind[0]-side*uy*P.PASS_POSE_SIDE_M,
                        behind[1]+side*ux*P.PASS_POSE_SIDE_M) for side in (-1, 1)]
    if waiting_override is not None:
        waits = [waiting_override]
    options, reason = [], 'receive_route_unavailable'
    for waiting in waits:
        if kickoff and waiting[0] > -.05:
            reason = 'kickoff_waiting_outside_own_half'
            continue
        if point_segment(waiting, origin, contact) < P.PASS_POSE_BALL_CLEARANCE_M:
            continue
        receive_heading = angle_to(*waiting, *origin)
        approach = route(ctx, receiver.id, receiver_xy, waiting, origin, receive_heading)
        adjustment = route(ctx, receiver.id, waiting, behind, contact, receive_heading)
        if approach is None or adjustment is None:
            continue
        receiver_s = (path_time(approach, receive_heading, omni=True)
                      +abs(normalize_angle(receive_heading-receiver.pose.theta))/P.NAV_TURN_SPEED_RPS)
        prepare_s = max(passer_s, receiver_s)+P.PASS_READY_HOLD_S+P.RECEIVE_CONTROL_DELAY_S
        if prepare_s > P.PASS_PREPARE_TIMEOUT_S:
            reason = 'prepare_timeout'
            continue
        adjust_s = sum(math.dist(a, b) for a, b in zip(adjustment, adjustment[1:]))/P.PASS_POSE_REPOSITION_SPEED_MPS
        turn_s = abs(normalize_angle(heading-receive_heading))/P.RECEIVE_MAX_YAW_RADPS
        after_s = P.PASS_POSE_SETTLE_S+adjust_s+turn_s+P.RECEIVE_KICK_STABLE_S
        if after_s-P.PASS_POSE_SETTLE_S > P.PASS_POSE_MAX_ADJUST_S:
            reason = 'attack_pose_timeout'
            continue
        margins = race(ctx, origin, contact, prepare_s, after_s)
        if diagnostics is not None:
            diagnostics.update(prepare_s=prepare_s, after_receive_s=after_s,
                prepare_margin_s=_finite(margins[0]), lane_margin_s=_finite(margins[1]),
                receive_margin_s=_finite(margins[2]))
        if min(margins) < P.PASS_RACE_MARGIN_S:
            reason = ('prepare_race', 'lane_race', 'receive_race')[min(range(3), key=lambda i: margins[i])]
            continue
        preferred = (kickoff and contact[0] < anchor[0]-.05 and contact[1] > anchor[1]+.05
                     and nearest_enemy(ctx, contact) > nearest_enemy(ctx, anchor)+.05)
        # 空对手集合的离线场景不要求无穷大距离严格增加。
        if kickoff and not ctx.opponents:
            preferred = contact[0] < anchor[0]-.05 and contact[1] > anchor[1]+.05
        ready_s = prepare_s+math.dist(origin, contact)/P.PASS_ASSUMED_SPEED_MIN_MPS+after_s
        pose = ReceivePose(contact, waiting, behind, attack, heading, receive_heading,
            approach, adjustment, preferred, _finite(nearest_enemy(ctx, contact)),
            prepare_s, ready_s, *map(_finite, margins), selected_at)
        options.append((ready_s, pose))
    return (min(options, key=lambda item: item[0])[1], 'ok') if options else (None, reason)


def choose(ctx: Any, passer: Any, receiver: Any, preferred: Point, anchor: Point,
           kickoff: bool, lane_safe: Callable, prepare_route: Callable,
           free: Point | None = None) -> Selection:
    """至多 28 个接球点 × 4 个进攻方向 × 3 个等待位置（3v3）。"""
    result = Selection(ctx.now, anchor, kickoff)
    if any(r.pose is None or not all(math.isfinite(v) for v in (r.pose.x, r.pose.y, r.pose.theta))
           or not 0 <= ctx.now-r.last_seen_at <= P.PASS_BALL_MAX_AGE_S
           for r in list(ctx.opponents.values())+list(ctx.teammates.values())):
        result.reason = 'robot_observation_unavailable'
        return result
    origin = (ctx.ball.x, ctx.ball.y)
    points = {tuple(preferred), anchor, (receiver.pose.x, receiver.pose.y)}
    if free is not None:
        points.add(tuple(free))
    for radius in P.PASS_POSE_RADII_M:
        for i in range(8):
            angle = i*math.pi/4
            points.add((anchor[0]+radius*math.cos(angle), anchor[1]+radius*math.sin(angle)))
    choices = []
    for contact in sorted(points):
        record = dict(contact=contact, upper_left=contact[0] < anchor[0]-.05 and contact[1] > anchor[1]+.05,
                      preferred_region=False,
                      enemy_distance=_finite(nearest_enemy(ctx, contact)), reason='unsafe_lane')
        result.candidates.append(record)
        if not inside(ctx, contact) or not lane_safe(ctx, passer.id, receiver.id, contact):
            continue
        base = angle_to(*contact, ctx.field.length/2, 0.)
        options, attacks = [], []
        for offset in (0., -.45, .45):
            angle = base+offset
            attack = (contact[0]+P.PASS_POSE_ATTACK_LENGTH_M*math.cos(angle),
                      contact[1]+P.PASS_POSE_ATTACK_LENGTH_M*math.sin(angle))
            if not inside(ctx, attack):
                attack = (ctx.field.length/2, 0.)
            attacks.append(attack)
        attacks.extend((r.pose.x, r.pose.y) for pid, r in ctx.teammates.items()
                       if pid not in (receiver.id, P.GUARD_PLAYER_ID) and r.pose is not None
                       and math.dist(contact, (r.pose.x, r.pose.y)) >= P.PASS_MIN_DISTANCE_M)
        record['direction_rejections'] = {}
        for attack in attacks:
            diagnostic = dict(attack_target=attack)
            pose, reason = evaluate(ctx, passer, receiver, contact, attack,
                                    anchor, kickoff, prepare_route, ctx.now, diagnostics=diagnostic)
            record.update(diagnostic)
            if reason != 'ok':
                counts = record['direction_rejections']
                counts[reason] = counts.get(reason, 0)+1
            if pose:
                options.append(pose)
            else:
                record['reason'] = reason
        if not options:
            continue
        def rank(pose):
            margin = min(v for v in (pose.prepare_margin_s, pose.lane_margin_s,
                         pose.receive_margin_s, 60.) if v is not None)
            return (not pose.preferred_region if kickoff else False,
                    -math.floor(margin/P.PASS_POSE_TIME_BUCKET_S),
                    math.floor(pose.attack_ready_s/P.PASS_POSE_TIME_BUCKET_S),
                    -math.floor((pose.enemy_distance or 0.)/P.PASS_POSE_CLEARANCE_BUCKET_M),
                    math.dist(origin, pose.contact), pose.contact, pose.heading)
        pose = min(options, key=rank)
        record.update(reason='ok', attack_target=pose.attack_target, preferred_region=pose.preferred_region,
                      prepare_s=pose.prepare_s, attack_ready_s=pose.attack_ready_s,
                      prepare_margin_s=pose.prepare_margin_s, lane_margin_s=pose.lane_margin_s,
                      receive_margin_s=pose.receive_margin_s)
        choices.append((rank(pose), pose))
    if choices:
        result.selected = min(choices, key=lambda item: item[0])[1]
        result.reason = ('preferred_upper_left' if result.selected.preferred_region else
                         'upper_left_unavailable' if kickoff else 'safe_attack_pose')
    return result
