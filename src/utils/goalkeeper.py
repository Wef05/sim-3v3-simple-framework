"""守门策略：近门直接出球、防乌龙、保留防守覆盖的调整及身体封堵。只产生意图，不执行 SDK。

有效步速/身体半径/踢球耗时均为保守初值，需实测标定；不把预测可达当扑救保证。
"""
from dataclasses import dataclass
import math

from ..param import *
from ..framework.types import Context, GameState, SetPlay, Penalty, Pose2D, KICKING_TEAM_NONE
from ..intents import MoveIntent, FaceIntent, KickIntent, KickTarget, StopIntent
from .geom import clamp, angle_to, normalize_angle, own_goal_area_center, behind_ball, goalkeeper_home
from .kick_lane import check_kick_lane, point_segment
from .ball_motion import BallMotion

Point = tuple[float, float]
OutletPlan = tuple[Point, str, int | None]


@dataclass
class GuardMemory:
    target: tuple | None = None
    plan: tuple | None = None  # (aim point, style, receiver id)
    kick_since: float | None = None
    retry_at: float = 0.0
    last_time: float | None = None
    last_shot_at: float | None = None
    close_clear: bool = False
    ray_active: bool = False


def travel_time(pose, target):
    """保持朝向全向移动的保守估计，侧移和后退单独限速。"""
    dx, dy = target[0] - pose.x, target[1] - pose.y
    c, s = math.cos(pose.theta), math.sin(pose.theta)
    forward, lateral = c*dx+s*dy, -s*dx+c*dy
    speed = GUARD_FORWARD_SPEED if forward >= 0 else GUARD_BACKWARD_SPEED
    distance = math.hypot(dx, dy)
    if distance <= GUARD_BLOCK_ARRIVE_M:
        return 0.0
    cruise_time = math.hypot(forward/speed, lateral/GUARD_LATERAL_SPEED)
    effective_speed = distance/cruise_time
    # walk_to 在接近目标时用 P 控制减速；常速到达估计会高估短距离侧移能力。
    transition = effective_speed/LINEAR_GAIN
    return (max(0, distance-transition)/effective_speed
            + max(0, math.log(min(distance, transition)/GUARD_BLOCK_ARRIVE_M))/LINEAR_GAIN)


def bounds(ctx):
    gx = -ctx.field.length/2
    return (gx+GUARD_GOAL_LINE_MARGIN_M, gx+ctx.field.goal_area_length+GUARD_CLEAR_EXTRA_M,
            max(0.0, ctx.field.goal_width/2-GUARD_POST_MARGIN_M))


def bounded(point, region):
    lo, hi, width = region
    return (clamp(point[0], lo, hi), clamp(point[1], -width, width))


def path_clear(ctx, pid, start, end):
    # 有限守门区域内仅接受直线可行候选，避免用绕障前的时间宣称可达。
    if check_kick_lane(ctx.field, start, end)['blocked']:
        return False
    for robot in list(ctx.opponents.values()) + [r for i, r in ctx.teammates.items() if i != pid]:
        if robot.pose is not None and point_segment((robot.pose.x, robot.pose.y), start, end) < GUARD_ROBOT_CLEARANCE:
            return False
    return True


def incoming_shot(ctx, motion, details):
    """在球首次离场前检测门线交点；二分过线时刻，避免快速球跳过门口。"""
    if not details or not details.get('velocity_estimated'):
        return None
    start = motion.predict_ball_position(0)
    gx, half_w = -ctx.field.length/2, ctx.field.width/2
    if start is None or start[0] < gx or abs(start[1]) > half_w:
        return None
    if details.get('velocity', (0, 0))[0] >= 0:
        return None
    end = motion.predict_ball_position(GUARD_SHOT_HORIZON_S)
    if end is None or end[0] > gx:
        return None
    lo, hi = 0.0, GUARD_SHOT_HORIZON_S
    for _ in range(32):
        mid = (lo+hi)/2
        if motion.predict_ball_position(mid)[0] > gx:
            lo = mid
        else:
            hi = mid
    crossing = motion.predict_ball_position(hi)
    # 包含擦柱附近的不确定性；不宣称此区域内每个球都会进门。
    if abs(crossing[1]) > ctx.field.goal_width/2 + BALL_OBSTACLE_RADIUS:
        return None
    return dict(time=hi, crossing=crossing)


def block_target(ctx, pid, pose, motion, shot):
    """只搜进门前、守门区域内的接触点；不可达时最小化剩余漏球距离。"""
    region = bounds(ctx)
    start = (pose.x, pose.y)
    reachable, fallback = [], []
    radius = GUARD_BODY_RADIUS_M-GUARD_BLOCK_ARRIVE_M
    count = max(1, math.ceil(shot['time']/GUARD_BLOCK_STEP_S))
    for i in range(count+1):
        t = shot['time']*i/count
        ball = motion.predict_ball_position(t)
        d = math.dist(start, ball)
        ratio = max(0.0, d-radius)/d if d else 0.0
        ideal = bounded((start[0]+(ball[0]-start[0])*ratio,
                         start[1]+(ball[1]-start[1])*ratio), region)
        arrival = travel_time(pose, ideal)
        covered = math.dist(ideal, ball) <= radius+1e-8
        if covered and (arrival < 1e-8 or arrival+GUARD_REACTION_S <= t):
            if path_clear(ctx, pid, start, ideal):
                reachable.append((math.dist(start, ideal), t, ideal))
        # 用预计可达位置评估漏球距离，但导航仍朝接触点尽力移动。
        # 若把短时可达位置作为终点，反应预算耗尽时会退回原地并误停。
        budget = max(0.0, t-GUARD_REACTION_S)
        low, high = 0.0, 1.0
        for _ in range(16):
            fraction = (low+high)/2
            point = (start[0]+(ideal[0]-start[0])*fraction,
                     start[1]+(ideal[1]-start[1])*fraction)
            if travel_time(pose, point) <= budget:
                low = fraction
            else:
                high = fraction
        fraction = low if budget > 0 else 0.0
        possible = (start[0]+(ideal[0]-start[0])*fraction,
                    start[1]+(ideal[1]-start[1])*fraction)
        if bounded(possible, region) == possible and path_clear(ctx, pid, start, ideal):
            fallback.append((max(0, math.dist(possible, ball)-GUARD_BODY_RADIUS_M),
                             math.dist(start, ideal), ideal))
    if reachable:
        return min(reachable)[2], True
    return (min(fallback)[2] if fallback else start), False


def shooter_ray(ctx, pose, threat, details, goal_margin=GUARD_RAY_GOAL_MARGIN_M):
    """近球对手→球的正向射线。仅在射向门口时预判，不把所有远处对手当射手。

    几何遵循无误差射门假设；只负责出脚前占位，实测飞行轨迹始终优先。
    """
    if details and math.hypot(*details.get('velocity', (0, 0))) > GUARD_SHOOTER_BALL_SPEED:
        return None
    ball = (ctx.ball.x, ctx.ball.y)
    gx = -ctx.field.length/2
    if ball[0] <= gx:
        return None
    preferred = (threat or {}).get('threat_id')
    choices = []
    enemy_team = next((t.team_number for t in ctx.game.teams if t.team_number != ctx.team_id), None)
    for pid, robot in ctx.opponents.items():
        state = ctx.game.get_player_state(enemy_team, pid) if enemy_team is not None else None
        if robot.pose is None or state is not None and state.penalty != Penalty.NONE:
            continue
        dx, dy = ball[0]-robot.pose.x, ball[1]-robot.pose.y
        distance = math.hypot(dx, dy)
        if not GUARD_SHOOTER_MIN_GAP_M <= distance <= GUARD_SHOOTER_REACH_M or dx >= -1e-6:
            continue
        goal_y = ball[1]+(gx-ball[0])*dy/dx
        if abs(goal_y) > ctx.field.goal_width/2+goal_margin:
            continue
        # 保持靠近现有守门纵线，避免预判横移同时额外前冲。
        x = clamp(pose.x, gx+GUARD_GOAL_LINE_MARGIN_M, own_goal_area_center(ctx)[0])
        if ball[0] <= x:
            x = gx+GUARD_GOAL_LINE_MARGIN_M
        if ball[0] <= x:
            continue
        y = ball[1]+(x-ball[0])*dy/dx
        target = bounded((x, y), bounds(ctx))
        choices.append((distance, 0 if pid == preferred else 1,
                        dict(shooter_id=pid, target=target, goal_y=goal_y)))
    return min(choices, key=lambda item: item[:2])[2] if choices else None


class ContinuingShot:
    """只用于短期威胁保持的匀速包络，不宣称是摩擦模型的精确落点。"""
    def __init__(self, position, velocity):
        self.position, self.velocity = position, velocity

    def predict_ball_position(self, seconds):
        return tuple(self.position[i]+self.velocity[i]*seconds for i in (0, 1))


def continuing_shot(ctx, motion, details, memory):
    if memory.last_shot_at is None or ctx.now-memory.last_shot_at > GUARD_SHOT_HOLD_S:
        return None
    vx, vy = details.get('velocity', (0, 0))
    start = motion.predict_ball_position(0)
    gx = -ctx.field.length/2
    if start is None or start[0] <= gx or vx >= -.1:
        return None
    seconds = (gx-start[0])/vx
    y = start[1]+vy*seconds
    if not 0 < seconds <= GUARD_SHOT_HORIZON_S or abs(y) > ctx.field.goal_width/2+GUARD_RAY_GOAL_MARGIN_M:
        return None
    return dict(time=seconds, crossing=(gx, y)), ContinuingShot(start, (vx, vy))


def preposition(ctx, pose, threat):
    """以预计出脚位置的球门角平分线预站位；近威胁略前移，移动收益不足保持。"""
    ball = ctx.ball
    source = (ball.x, ball.y)
    estimate = (threat or {}).get('threat_intercept') or {}
    if estimate.get('status') == 'intercept' and estimate.get('intercept_time_s', math.inf) <= GUARD_ANTICIPATE_S:
        future = estimate.get('ball_position')
        if future and -ctx.field.length/2 < future[0] < 0 and abs(future[1]) < ctx.field.width/2:
            source = tuple(future)
    home = goalkeeper_home(ctx)
    if source[0] > GUARD_SET_BALL_X:
        return home
    gx = -ctx.field.length/2
    # 仅小幅前移；默认离门线 0.5～0.8 米，不追到对手脚下。
    depth = ctx.field.goal_area_length/2
    if source[0] < -ctx.field.length/4:
        depth += GUARD_PREPOSITION_ADVANCE_M
    x = min(gx+depth, source[0]-GUARD_BODY_RADIUS_M)
    vectors = []
    for y in (-ctx.field.goal_width/2, ctx.field.goal_width/2):
        dx, dy = gx-source[0], y-source[1]
        norm = math.hypot(dx, dy)
        vectors.append((dx/max(norm, 1e-9), dy/max(norm, 1e-9)))
    vx, vy = (sum(v[j] for v in vectors) for j in (0, 1))
    y = source[1]+(x-source[0])*vy/vx if vx < -1e-6 else source[1]
    return bounded((x, y), bounds(ctx))


def receiver_ready(ctx, pid):
    robot = ctx.teammates.get(pid)
    state = ctx.game.get_player_state(ctx.team_id, pid) if ctx.game else None
    return robot is not None and robot.pose is not None and (state is None or state.penalty == Penalty.NONE)


def approach_route(ctx, pid, pose, ball, stage):
    """球后接近时不穿过球；必要时先走侧面中间点，时间包含绕球路程。"""
    start = (pose.x, pose.y)
    clearance = GUARD_APPROACH_BALL_MARGIN_M
    if point_segment(ball, start, stage) >= clearance and path_clear(ctx, pid, start, stage):
        return stage, travel_time(pose, stage)
    options = []
    gx = -ctx.field.length/2
    for i in range(16):
        angle = i*math.tau/16
        waypoint = (ball[0]+GUARD_APPROACH_RADIUS_M*math.cos(angle),
                    ball[1]+GUARD_APPROACH_RADIUS_M*math.sin(angle))
        if not (gx+GUARD_GOAL_LINE_MARGIN_M <= waypoint[0] <= bounds(ctx)[1]
                and abs(waypoint[1]) <= ctx.field.goal_area_width/2-GUARD_BODY_RADIUS_M):
            continue
        if (point_segment(ball, start, waypoint) < clearance
                or point_segment(ball, waypoint, stage) < clearance):
            continue
        if path_clear(ctx, pid, start, waypoint) and path_clear(ctx, pid, waypoint, stage):
            # 第二段按最慢平移速度估计，避免转向/斜移乐观。
            seconds = travel_time(pose, waypoint)+math.dist(waypoint, stage)/min(GUARD_LATERAL_SPEED, GUARD_BACKWARD_SPEED)
            options.append((seconds, waypoint))
    if not options:
        return None
    seconds, waypoint = min(options)
    return waypoint, seconds


def own_goal_direction(ctx: Context, origin: Point, direction: float) -> bool:
    """检查无限射线而非短目标线段，并计入统一右脚方向补偿。"""
    gx = -ctx.field.length/2
    for angle in (direction, direction+math.radians(RIGHT_FOOT_KICK_CORRECTION_DEG)):
        dx, dy = math.cos(angle), math.sin(angle)
        if dx >= -1e-8:
            continue
        t = (gx-origin[0])/dx
        if t >= 0 and abs(origin[1]+t*dy) <= ctx.field.goal_width/2+BALL_OBSTACLE_RADIUS:
            return True
    return False


def kickable(ctx: Context, pose: Pose2D, direction: float,
             radius: float = GUARD_DIRECT_REACH_M) -> bool:
    """只检查当前脚前接触几何；不以慢球、争抢优势或标准球后点作门槛。"""
    distance = math.hypot(ctx.ball.x-pose.x, ctx.ball.y-pose.y)
    bearing = angle_to(pose.x, pose.y, ctx.ball.x, ctx.ball.y)
    return (.06 <= distance <= radius
            and abs(normalize_angle(bearing-pose.theta)) <= GUARD_CLOSE_CLEAR_BEARING_RAD
            and abs(normalize_angle(direction-pose.theta)) <= GUARD_CLOSE_CLEAR_ANGLE_RAD)


def safe_kick_geometry(ctx: Context, aim: Point) -> bool:
    ball = (ctx.ball.x, ctx.ball.y)
    direction = angle_to(*ball, *aim)
    corrected = direction+math.radians(RIGHT_FOOT_KICK_CORRECTION_DEG)
    distance = math.dist(ball, aim)
    corrected_aim = (ball[0]+distance*math.cos(corrected), ball[1]+distance*math.sin(corrected))
    return (distance > .1 and not own_goal_direction(ctx, ball, direction)
            and not check_kick_lane(ctx.field, ball, aim)['blocked']
            and not check_kick_lane(ctx.field, ball, corrected_aim)['blocked'])


def sideline_targets(ctx: Context, pose: Pose2D) -> list[Point]:
    """选择两侧边线上的候选点，优先少转身；不将球门线误作边线。"""
    ball = (ctx.ball.x, ctx.ball.y)
    gx = -ctx.field.length/2
    aims = [(clamp(ball[0]+dx, gx+.35, -gx-.35), sign*(ctx.field.width/2+.2))
            for sign in (-1, 1) for dx in (0., 1.5, 3.)]
    return sorted(aims, key=lambda aim: abs(normalize_angle(angle_to(*ball, *aim)-pose.theta)))


def lane_clear(ctx, pid, plan):
    aim, _, receiver = plan
    if not safe_kick_geometry(ctx, aim):
        return False
    if receiver is not None:
        if not receiver_ready(ctx, receiver):
            return False
        r = ctx.teammates[receiver].pose
        if math.dist(aim, (r.x, r.y)) > GUARD_RECEIVER_DRIFT_M:
            return False
    ball = (ctx.ball.x, ctx.ball.y)
    robots = list(ctx.opponents.values())+[r for i, r in ctx.teammates.items() if i not in (pid, receiver)]
    return all(r.pose is not None and point_segment((r.pose.x, r.pose.y), ball, aim)
               > OPPONENT_RADIUS+BALL_OBSTACLE_RADIUS for r in robots)


def adjustment_safe(ctx: Context, pid: int, pose: Pose2D, motion: BallMotion,
                    threat: dict | None, stage: Point, direction: float,
                    route: tuple[Point, float]) -> bool:
    """仅调整动作评估射门窗口；直达对手时间是保守下界，不宣称绝对安全。

    所有分段保持当前已覆盖的采样球门射线，不假定队友会持续替补；另要求在对手
    最早触球前完成挪步、转向和出脚。缺失对手观测时不尝试调整。
    """
    waypoint, move_time = route
    finish = move_time+abs(normalize_angle(direction-pose.theta))/GUARD_TURN_SPEED+GUARD_KICK_PREPARE_S
    enemy_time = math.inf
    ball = (ctx.ball.x, ctx.ball.y)
    for robot in ctx.opponents.values():
        if robot.pose is None:
            return False
        enemy_time = min(enemy_time, max(0., math.dist((robot.pose.x, robot.pose.y), ball)
                                        -BALL_CONTROL_RADIUS_M)/MAX_LINEAR)
    estimate = (threat or {}).get('threat_intercept') or {}
    if estimate.get('status') == 'intercept':
        enemy_time = min(enemy_time, estimate['intercept_time_s'])
    if finish+GUARD_REACTION_S >= enemy_time:
        return False
    future = motion.predict_ball_position(finish)
    if future is None or math.dist(ball, future) > GUARD_CONTROL_DRIFT_M:
        return False
    start = (pose.x, pose.y)
    points = [start, waypoint, stage]
    gx = -ctx.field.length/2
    for i in range(9):
        goal = (gx, ctx.field.goal_width*(i/8-.5))
        if point_segment(start, ball, goal) > GUARD_BODY_RADIUS_M:
            continue
        # 到线段的距离是凸函数；每段两端都覆盖即可覆盖整段平移。
        if any(point_segment(p, ball, goal) > GUARD_BODY_RADIUS_M for p in points):
            return False
    return True


def prepare_outlet(ctx: Context, pid: int, pose: Pose2D, motion: BallMotion,
                   threat: dict | None, plan: OutletPlan, near_goal: bool) -> MoveIntent | None:
    """原地转身优先；只有射门窗口允许时才挪到球后。近门逃逸限制位移。"""
    ball = (ctx.ball.x, ctx.ball.y)
    direction = angle_to(*ball, *plan[0])
    # 原地转向后球仍须在可踢扇区内，避免面对边线后球落在背后的死循环。
    turned = Pose2D(pose.x, pose.y, direction)
    if kickable(ctx, turned, direction):
        route = ((pose.x, pose.y), 0.)
        if adjustment_safe(ctx, pid, pose, motion, threat, route[0], direction, route):
            return MoveIntent(route[0], face=direction, maintain_face=True, arrive_dist=.02,
                              avoid_ball=False, avoid_robots=True, direct_path=True,
                              motion_priority=80, opponent_mode='contest')
    stage = behind_ball(*ball, plan[0], CHASE_BEHIND_M)
    if near_goal and math.dist((pose.x, pose.y), stage) > GUARD_ESCAPE_MOVE_M:
        return None
    if not (bounds(ctx)[0] <= stage[0] <= bounds(ctx)[1]
            and abs(stage[1]) <= ctx.field.goal_area_width/2-GUARD_BODY_RADIUS_M):
        return None
    route = approach_route(ctx, pid, pose, ball, stage)
    if near_goal and route is not None and math.dist((pose.x, pose.y), route[0]) > GUARD_ESCAPE_MOVE_M:
        return None
    if route is None or not adjustment_safe(ctx, pid, pose, motion, threat, stage, direction, route):
        return None
    return MoveIntent(route[0], face=direction, avoid_ball=False, avoid_robots=True,
                      maintain_face=True, arrive_dist=GUARD_STAGE_TOLERANCE_M, direct_path=True,
                      motion_priority=80, opponent_mode='contest')


def outlet(ctx, pid, pose, motion, threat, memory, preferred, available_receivers=None,
           diagnostics=None, allow_adjustment=True):
    """小禁区直接沿朝向出脚；其余按队友、边线、当前朝向严格分级。"""
    diagnostics = diagnostics if diagnostics is not None else {}
    ball = (ctx.ball.x, ctx.ball.y)
    gx = -ctx.field.length/2
    if not (gx <= ball[0] <= bounds(ctx)[1] and abs(ball[1]) <= ctx.field.goal_area_width/2
            and math.dist((pose.x, pose.y), ball) <= GUARD_CLEAR_REACH_M):
        diagnostics['outlet_wait_reason'] = 'range'
        return None
    near = ball[0] <= gx+ctx.field.goal_area_length
    facing = (ball[0]+GUARD_CLOSE_CLEAR_LENGTH_M*math.cos(pose.theta),
              ball[1]+GUARD_CLOSE_CLEAR_LENGTH_M*math.sin(pose.theta))
    facing_plan = (facing, 'clear', None)
    sides = [(aim, 'clear', None) for aim in sideline_targets(ctx, pose)]
    own_goal = own_goal_direction(ctx, ball, pose.theta)
    diagnostics.update(near_goal=near, own_goal_facing=own_goal)

    def kick(plan, action):
        memory.plan, memory.close_clear = plan, True
        if memory.kick_since is None:
            memory.kick_since = ctx.now
        return KickIntent(KickTarget.position(plan[0]), style=plan[1]), action

    # 近门最高优先级每帧重查：朝向球门时绝不延续之前的危险目标。
    if near and not own_goal and safe_kick_geometry(ctx, facing) and kickable(ctx, pose, pose.theta):
        diagnostics['outlet_mode'] = 'near_facing'
        return kick(facing_plan, 'direct_clear')
    # 出脚中的目标承诺不因争抢优势消失/远处球路变差而取消，仍验防乌龙和脚位。
    if not near and memory.kick_since is not None and memory.plan is not None:
        plan = memory.plan
        receiver = plan[2]
        receiver_ok = receiver is None or (receiver_ready(ctx, receiver)
            and (available_receivers is None or receiver in available_receivers)
            and math.dist(plan[0], (ctx.teammates[receiver].pose.x, ctx.teammates[receiver].pose.y)) <= GUARD_RECEIVER_DRIFT_M)
        if receiver_ok and safe_kick_geometry(ctx, plan[0]) and kickable(
                ctx, pose, angle_to(*ball, *plan[0]), GUARD_KICK_EXIT_M):
            diagnostics['outlet_mode'] = 'committed'
            return kick(plan, 'kick_hold')

    passes = []
    for other, robot in sorted(ctx.teammates.items()):
        if other == pid or not receiver_ready(ctx, other) or (available_receivers is not None and other not in available_receivers):
            continue
        aim = (robot.pose.x, robot.pose.y)
        if (abs(aim[0]) < ctx.field.length/2 and abs(aim[1]) < ctx.field.width/2
                and .5 <= math.dist(ball, aim) <= GUARD_PASS_MAX_DIST_M):
            passes.append((aim, 'pass', other))
    passes.sort(key=lambda p: (abs(normalize_angle(angle_to(*ball, *p[0])-pose.theta)), p[2] != preferred))
    groups = [sides] if near else [passes, sides, [facing_plan]]
    for group in groups:
        usable = [plan for plan in group if (safe_kick_geometry(ctx, plan[0])
                  if near else lane_clear(ctx, pid, plan))]
        for plan in usable:
            if kickable(ctx, pose, angle_to(*ball, *plan[0])):
                diagnostics['outlet_mode'] = 'own_goal_escape' if near else 'pass' if plan[1] == 'pass' else 'sideline' if group is sides else 'facing'
                return kick(plan, 'pass' if plan[1] == 'pass' else 'direct_clear')
        if allow_adjustment:
            for plan in usable:
                intent = prepare_outlet(ctx, pid, pose, motion, threat, plan, near)
                if intent is not None:
                    memory.plan, memory.kick_since, memory.close_clear = plan, None, False
                    diagnostics['outlet_mode'] = 'safe_adjustment'
                    return intent, 'prepare'
    # 当前朝向兜底接受争球风险，不再要求整条球路无人；门结构/防乌龙仍检查。
    if not near and safe_kick_geometry(ctx, facing) and kickable(ctx, pose, pose.theta):
        diagnostics['outlet_mode'] = 'facing_contested'
        return kick(facing_plan, 'direct_clear')
    diagnostics['outlet_wait_reason'] = 'own_goal_no_escape' if own_goal else 'no_executable_kick'
    return None


def decide_guard(player, preferred=None):
    ctx, pose = player.context, player.pose
    player.guard_decision = {}
    memory = getattr(player, '_guard_memory', None)
    if memory is None or (ctx and memory.last_time is not None and ctx.now < memory.last_time):
        memory = GuardMemory()
        player._guard_memory = memory
    def result(intent, action):
        if isinstance(intent, KickIntent) or action == 'prepare':
            plan = memory.plan
            if plan is not None:
                player.guard_decision.update(outlet_style=plan[1], receiver_id=plan[2])
        player.action = 'guard:'+action
        player.tactical_target = getattr(intent, 'target', None) if isinstance(intent, MoveIntent) else (intent.target.point if isinstance(intent, KickIntent) else None)
        return intent
    if ctx is None or pose is None:
        player._guard_memory = GuardMemory()
        return result(StopIntent(), 'stop')
    memory.last_time = ctx.now
    game = ctx.game
    if game is None or game.state != GameState.PLAYING or game.stopped:
        player._guard_memory = GuardMemory()
        return result(StopIntent(), 'stopped')
    restricted = game.kicking_team not in (ctx.team_id, KICKING_TEAM_NONE) and (game.set_play != SetPlay.NONE or game.secondary_time > 0)
    details = player.motion.ball_prediction_details()
    fresh = (ctx.ball is not None and 0 <= ctx.now-ctx.ball.last_seen_at <= GUARD_OBSERVATION_MAX_AGE_S)
    known = fresh and details is not None and details.get('velocity_estimated', False)
    player.guard_decision.update(ball_fresh=fresh, velocity_known=known)
    if not fresh:
        memory.plan, memory.kick_since, memory.target = None, None, None
        memory.close_clear, memory.ray_active = False, False
        return result(MoveIntent(goalkeeper_home(ctx), face=0, avoid_ball=True,
                                 avoid_robots=True, maintain_face=True), 'home:no_ball')
    face = angle_to(pose.x, pose.y, ctx.ball.x, ctx.ball.y)
    shot_motion = player.motion
    shot = incoming_shot(ctx, shot_motion, details) if known else None
    if shot is not None:
        memory.last_shot_at = ctx.now
    elif known and not restricted:
        held = continuing_shot(ctx, player.motion, details, memory)
        if held is not None:
            shot, shot_motion = held
        else:
            memory.last_shot_at = None
    else:
        memory.last_shot_at = None
    if memory.kick_since is not None and ctx.now-memory.kick_since >= GUARD_KICK_TIMEOUT_S:
        memory.plan, memory.kick_since, memory.close_clear = None, None, False
        memory.retry_at = ctx.now+GUARD_RETRY_S
    speed = math.hypot(*details.get('velocity', (0, 0))) if known else None
    player.guard_decision['ball_speed_mps'] = speed
    # 可触及的近门球先解围；球速不是拒绝出脚的门槛。射门期间不允许为出球挪位。
    if not restricted and ctx.now >= memory.retry_at:
        near = ctx.ball.x <= -ctx.field.length/2+ctx.field.goal_area_length
        if shot is None or near:
            choice = outlet(ctx, player.id, pose, player.motion, player.threat, memory, preferred,
                            getattr(player, 'guard_receivers', None), player.guard_decision,
                            allow_adjustment=known and shot is None)
            if choice:
                memory.target = None
                return result(*choice)
    if shot is not None and not restricted:
        player.guard_decision.update(shot_source='measured_prediction' if shot_motion is player.motion else 'short_hold_envelope',
                                     goal_time_s=shot['time'], goal_crossing=shot['crossing'])
        memory.plan, memory.kick_since, memory.target = None, None, None
        memory.close_clear, memory.ray_active = False, False
        target, reachable = block_target(ctx, player.id, pose, shot_motion, shot)
        if math.dist((pose.x, pose.y), target) < GUARD_BLOCK_ARRIVE_M:
            return result(FaceIntent(face), 'block:hold' if reachable else 'block:unreachable_hold')
        return result(MoveIntent(target, face=face, avoid_ball=False, avoid_robots=True,
                                 maintain_face=True, arrive_dist=GUARD_BLOCK_ARRIVE_M, direct_path=True,
                                 motion_priority=100, opponent_mode='contest'), 'block:reachable' if reachable else 'block:best_effort')
    player.guard_decision.setdefault('outlet_wait_reason',
        'restricted' if restricted else 'retry_cooldown' if ctx.now < memory.retry_at else 'no_executable_kick')
    memory.plan, memory.kick_since, memory.close_clear = None, None, False
    margin = GUARD_RAY_EXIT_MARGIN_M if memory.ray_active else GUARD_RAY_GOAL_MARGIN_M
    ray = shooter_ray(ctx, pose, player.threat, details, margin)
    memory.ray_active = ray is not None
    if ray is not None:
        player.guard_decision.update(shooter_id=ray['shooter_id'], ray_goal_y=ray['goal_y'])
    target = ray['target'] if ray is not None else preposition(ctx, pose, player.threat)
    if memory.target is not None and math.dist(memory.target, target) < GUARD_TARGET_DEADBAND_M:
        target = memory.target
    memory.target = target
    return result(MoveIntent(target, face=face, avoid_ball=True, avoid_robots=True,
                             maintain_face=True, arrive_dist=.06), 'set:restricted' if restricted else 'anticipate:ray' if ray is not None else 'set')
