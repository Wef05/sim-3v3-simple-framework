"""有限接球姿态搜索；纯计算，时间是保守估计，不是触球或控球证明。"""
from dataclasses import dataclass, field
import math
from typing import Any, Callable

from .. import param as P
from .geom import angle_to, normalize_angle
from .kick_lane import point_segment, check_kick_lane
from .obstacles import collect_obstacles
from .path_planner import segment_clear, path_time
from ..framework.types import GameState, SetPlay

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
    attack_margin_s: float | None = None
    provisional: bool = False
    follow_up: str = 'direct_attack'


@dataclass
class Selection:
    evaluated_at: float
    anchor: Point
    kickoff: bool
    candidates: list[dict] = field(default_factory=list)
    selected: ReceivePose | None = None
    reason: str = 'no_safe_pose'
    preparation: ReceivePose | None = None


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


def kickoff_window(ctx: Any) -> float:
    """裁判开球保护的剩余下界；整数倒计时扣 1 s，不凭阶段名延长保护。"""
    g = ctx.game
    if (g is None or g.state != GameState.PLAYING or g.stopped
            or g.kicking_team != ctx.team_id or g.set_play != SetPlay.NONE
            or not 0 <= ctx.now-g.last_seen_at <= P.PASS_BALL_MAX_AGE_S):
        return 0.
    return max(0., g.secondary_time-1.-(ctx.now-g.last_seen_at))


def contest_time(ctx: Any, point: Point, prepare_s: float) -> float:
    """受限运动可达集合的保守距离下界；不假设对手在准备期静止。

    已核实裁判：保护解除前对方留在己方半场且远离中圈，首次本方触球即开放。
    用运动圆、半场和中圈三个距离下界的最大值，放宽交集而不高估安全性。
    一旦观测到对手违规或状态不新鲜，退回自由争抢估计。
    """
    window = kickoff_window(ctx)
    protected = min(max(0., prepare_s), window)
    times = []
    for robot in ctx.opponents.values():
        if robot.pose is None:
            return 0.
        point_enemy = (robot.pose.x, robot.pose.y)
        legal = point_enemy[0] >= -.05 and math.hypot(*point_enemy) > ctx.field.circle_radius+.05
        if not protected or not legal:
            times.append(max(0., math.dist(point_enemy, point)-P.BALL_CONTROL_RADIUS_M)/P.MAX_LINEAR)
            continue
        travel_bound = max(0., math.dist(point_enemy, point)-P.MAX_LINEAR*protected)
        half_bound = max(0., -.05-point[0])
        circle_bound = max(0., ctx.field.circle_radius+.05-math.hypot(*point))
        remaining = max(0., max(travel_bound, half_bound, circle_bound)-P.BALL_CONTROL_RADIUS_M)
        times.append(protected+remaining/P.MAX_LINEAR)
    return min(times, default=math.inf)


def race(ctx: Any, origin: Point, contact: Point, prepare_s: float,
         after_s: float) -> tuple[float, float, float]:
    distance = math.dist(origin, contact)
    lane = min(contest_time(ctx, (origin[0]+i/8*(contact[0]-origin[0]),
                                 origin[1]+i/8*(contact[1]-origin[1])), prepare_s)
               -prepare_s-i/8*distance/P.PASS_ASSUMED_SPEED_MIN_MPS for i in range(1, 9))
    protected = kickoff_window(ctx) > prepare_s+P.PASS_RACE_MARGIN_S and all(
        r.pose is not None and r.pose.x >= -.05
        and math.hypot(r.pose.x, r.pose.y) > ctx.field.circle_radius+.05
        for r in ctx.opponents.values())
    prepare_margin = math.inf if protected else enemy_time(ctx, origin)-prepare_s
    return (prepare_margin, lane,
            contest_time(ctx, contact, prepare_s)-prepare_s-distance/P.PASS_ASSUMED_SPEED_MIN_MPS-after_s)


def moving_ready_time(path: list[Point], heading: float, face: float) -> float:
    # 执行器同步转向/全向移动；以起止两种身体朝向中的较慢移动估计，
    # 与转向取最大值，再由调用者加延迟，避免把并行动作全部串行相加。
    movement = max(path_time(path, heading, omni=True), path_time(path, face, omni=True))
    return max(movement, abs(normalize_angle(face-heading))/P.NAV_TURN_SPEED_RPS)


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
             diagnostics: dict | None = None, heading_override: float | None = None,
             preview: bool = False, cache: dict | None = None) -> tuple[ReceivePose | None, str]:
    origin = (ctx.ball.x, ctx.ball.y)
    receiver_xy = (receiver.pose.x, receiver.pose.y)
    heading = angle_to(*contact, *attack)
    ux, uy = math.cos(heading), math.sin(heading)
    behind = (contact[0]-P.RECEIVE_STANDOFF_M*ux, contact[1]-P.RECEIVE_STANDOFF_M*uy)
    if not inside(ctx, behind):
        return None, 'behind_outside_field'
    if not attack_clear(ctx, contact, attack, receiver.id):
        return None, 'attack_corridor_blocked'
    cache = {} if cache is None else cache
    if contact not in cache:
        pr = prepare_route(ctx, passer, contact)
        path = None if pr is None else route(ctx, passer.id, (passer.pose.x, passer.pose.y), pr[0], origin, pr[2])
        cache[contact] = None if path is None else moving_ready_time(path, passer.pose.theta, pr[2])
    def cached_route(pid, start, end, ball, face):
        key=('route',pid,start,end,ball,face)
        if key not in cache:
            cache[key]=route(ctx,pid,start,end,ball,face)
        return cache[key]
    passer_s = cache[contact]
    if passer_s is None:
        return None, 'passer_route_unavailable'
    waits = [behind]+[(behind[0]-side*uy*P.PASS_POSE_SIDE_M,
                       behind[1]+side*ux*P.PASS_POSE_SIDE_M) for side in (-1, 1)]
    if waiting_override is not None:
        waits = [waiting_override]
    options, failures = [], []
    best_failure = None
    for waiting in waits:
        if kickoff and waiting[0] > -.05:
            failures.append('kickoff_waiting_outside_own_half')
            continue
        if point_segment(waiting, origin, contact) < P.PASS_POSE_BALL_CLEARANCE_M:
            failures.append('waiting_blocks_ball')
            continue
        incoming = angle_to(*waiting, *origin)
        # 最多偏向进攻方向 30°，保持来球在前侧，不靠背向来球缩短转身时间。
        blend = incoming+max(-math.pi/6, min(math.pi/6, normalize_angle(heading-incoming)))
        faces = [heading_override] if heading_override is not None else [incoming, blend]
        for face in faces:
            approach = cached_route(receiver.id, receiver_xy, waiting, origin, face)
            adjustment = cached_route(receiver.id, waiting, behind, contact, face)
            if approach is None or adjustment is None:
                failures.append('receive_route_unavailable')
                continue
            receiver_s = moving_ready_time(approach, receiver.pose.theta, face)
            prepare_s = max(passer_s, receiver_s)+P.PASS_READY_HOLD_S+P.RECEIVE_CONTROL_DELAY_S
            if prepare_s > P.PASS_PREPARE_TIMEOUT_S:
                failures.append('prepare_timeout')
                continue
            adjust_s = sum(math.dist(a,b) for a,b in zip(adjustment,adjustment[1:]))/P.PASS_POSE_REPOSITION_SPEED_MPS
            turn_s = abs(normalize_angle(heading-face))/P.RECEIVE_MAX_YAW_RADPS
            after_s = P.PASS_RECEIVE_HOLD_S+adjust_s+turn_s+P.RECEIVE_KICK_STABLE_S
            if after_s-P.PASS_RECEIVE_HOLD_S > P.PASS_POSE_MAX_ADJUST_S:
                failures.append('attack_pose_timeout')
                continue
            budget = P.PASS_READY_HOLD_S+P.RECEIVE_CONTROL_DELAY_S if preview else prepare_s
            margins = race(ctx, origin, contact, budget, P.PASS_RECEIVE_HOLD_S)
            attack_margin = margins[2]-(after_s-P.PASS_RECEIVE_HOLD_S)
            diag = dict(prepare_s=prepare_s, after_receive_s=after_s,
                prepare_margin_s=_finite(margins[0]), lane_margin_s=_finite(margins[1]),
                receive_margin_s=_finite(margins[2]), attack_margin_s=_finite(attack_margin),
                protected_s=min(budget,kickoff_window(ctx)), provisional=preview)
            if min(margins) < P.PASS_RACE_MARGIN_S:
                reason = ('prepare_race','lane_race','receive_race')[min(range(3),key=lambda i:margins[i])]
                failures.append(reason)
                # 保存安全余量最好的失败姿态，不让最后一个方向覆盖真正的瓶颈。
                if best_failure is None or min(margins)>best_failure[0]:
                    best_failure=(min(margins),reason,diag)
                continue
            preferred=(kickoff and contact[0]<anchor[0]-.05 and contact[1]>anchor[1]+.05
                and (not ctx.opponents or nearest_enemy(ctx,contact)>nearest_enemy(ctx,anchor)+.05))
            ready_s=prepare_s+math.dist(origin,contact)/P.PASS_ASSUMED_SPEED_MIN_MPS+after_s
            # 稳定接触是硬门槛；已验证的球后路径逐帧执行，不把未完成转向当成已能射门。
            follow_up='direct_attack' if attack_margin>=P.PASS_RACE_MARGIN_S else 'checked_adjustment'
            pose=ReceivePose(contact,waiting,behind,attack,heading,face,approach,adjustment,
                preferred,_finite(nearest_enemy(ctx,contact)),prepare_s,ready_s,*map(_finite,margins),
                selected_at,_finite(attack_margin),preview,follow_up)
            options.append((0 if follow_up=='direct_attack' else 1,ready_s,pose,diag))
    if options:
        _,_,pose,diag=min(options,key=lambda item:item[:2])
        if diagnostics is not None:diagnostics.update(diag)
        return pose,'ok'
    if diagnostics is not None:
        if best_failure:diagnostics.update(best_failure[2])
        diagnostics['pose_rejections']={r:failures.count(r) for r in sorted(set(failures))}
    return None,best_failure[1] if best_failure else failures[0] if failures else 'receive_route_unavailable'


def choose(ctx: Any, passer: Any, receiver: Any, preferred: Point, anchor: Point,
           kickoff: bool, lane_safe: Callable, prepare_route: Callable,
           free: Point | None = None) -> Selection:
    """至多 34 个接球点 × 4 个进攻方向 × 3 个等待位置 × 2 个朝向（3v3）。"""
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
    # 上边线附近增加“多向左、少向上”的候选，不把出界点投影成同一点。
    if kickoff:
        room = ctx.field.width/2-P.PASS_POSE_FIELD_MARGIN_M-anchor[1]
        for dx in (.5,1.,1.5):
            for dy in (.15,.3):
                if room > .05:
                    points.add((anchor[0]-dx,anchor[1]+min(dy,room)))
    choices, preparations, cache = [], [], {}
    for contact in sorted(points):
        record = dict(contact=contact, upper_left=contact[0] < anchor[0]-.05 and contact[1] > anchor[1]+.05,
                      preferred_region=False,
                      enemy_distance=_finite(nearest_enemy(ctx, contact)), reason='unsafe_lane')
        result.candidates.append(record)
        if not inside(ctx, contact):
            record['reason']='contact_outside_field'
            continue
        if not lane_safe(ctx, passer.id, receiver.id, contact):
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
                                    anchor, kickoff, prepare_route, ctx.now, diagnostics=diagnostic, cache=cache)
            if 'receive_margin_s' in diagnostic and ('receive_margin_s' not in record or
                    (diagnostic['receive_margin_s'] or 0.) > (record['receive_margin_s'] or 0.)):
                record.update(diagnostic)
                record['primary_rejection']=reason
            if reason != 'ok':
                counts = record['direction_rejections']
                counts[reason] = counts.get(reason, 0)+1
            if pose:
                options.append(pose)
            else:
                record.setdefault('primary_rejection', reason)
                record['reason'] = record['primary_rejection']
                if kickoff and reason in ('prepare_race','lane_race','receive_race') and kickoff_window(ctx)>P.PASS_READY_HOLD_S:
                    preview_pose,_=evaluate(ctx,passer,receiver,contact,attack,anchor,kickoff,
                        prepare_route,ctx.now,preview=True,cache=cache)
                    if preview_pose:preparations.append(preview_pose)
        if not options:
            continue
        def rank(pose):
            margin = min(v for v in (pose.prepare_margin_s, pose.lane_margin_s,
                         pose.receive_margin_s, 60.) if v is not None)
            return (not pose.preferred_region if kickoff else False,
                    -math.floor(margin/P.PASS_POSE_TIME_BUCKET_S),
                    pose.follow_up != 'direct_attack',
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
    if result.selected is None and preparations:
        result.preparation=min(preparations,key=lambda p:(not p.preferred_region,
            p.follow_up!='direct_attack',p.prepare_s,p.contact,p.heading))
        result.reason='preposition_before_commit'
    return result
