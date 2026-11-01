"""从预计截球点向目标 T 出球后，整段球路被拦风险，纯计算。

占位敌方预测：直接追逐移动球 / 前往自身预计截球点，各占 1/2。
每个分支末端是均匀圆盘分布；误差半径是可替换的建模假设，不是实测。
风险为出球到目标之间首次被截事件；以成功到达截球点并完成出球为前提。
不同对手独立近似。未来可注入 predictor 替换站位分布，不改变风险积分接口。
"""
import math
from ..param import MAX_LINEAR, MAX_ANGULAR, BALL_CONTROL_RADIUS_M
from .geom import normalize_angle
from .kick_lane import check_kick_lane
from .interception import estimate_ball_intercept


def _advance(pose, target, seconds, speed, angular_speed):
    x, y, theta = pose
    dx, dy = target[0] - x, target[1] - y
    distance = math.hypot(dx, dy)
    if distance < 1e-9:
        return pose
    turn = normalize_angle(math.atan2(dy, dx) - theta)
    turned = min(abs(turn), angular_speed * seconds)
    theta += math.copysign(turned, turn)
    remaining = max(0.0, seconds - abs(turn) / angular_speed)
    travel = min(max(0.0, distance - BALL_CONTROL_RADIUS_M), speed * remaining)
    return x + dx / distance * travel, y + dy / distance * travel, theta


def predict_opponent_placeholder(pose, motion, seconds, *, speed=MAX_LINEAR,
                                 angular_speed=MAX_ANGULAR):
    """可替换接口：返回 weight/position/heading/radius_m 的归一化圆盘混合。"""
    intercept = estimate_ball_intercept(pose, motion, linear_speed=speed, angular_speed=angular_speed)
    target = intercept.get('target', motion.predict_ball_position(seconds))
    branches = []
    for strategy in ('chase_ball', 'intercept_point'):
        state = (pose.x, pose.y, pose.theta)
        steps = max(1, math.ceil(seconds / .05))
        dt = seconds / steps
        for i in range(steps):
            aim = motion.predict_ball_position((i + 1) * dt) if strategy == 'chase_ball' else target
            state = _advance(state, aim, dt, speed, angular_speed)
        branches.append(dict(strategy=strategy, weight=.5, position=state[:2],
                             heading=state[2], radius_m=.2 + .15 * seconds))
    return branches


def _segment_distance(a, b):
    """相对运动线段到原点最短距离，避免快球穿过控球圆时漏检。"""
    dx, dy = b[0]-a[0], b[1]-a[1]
    norm = dx*dx+dy*dy
    t = max(0, min(1, -(a[0]*dx+a[1]*dy)/norm)) if norm else 0
    return math.hypot(a[0]+t*dx, a[1]+t*dy)


def _flight_risk(branch, origin, target, flight_s, speed, angular_speed, samples, steps, preparation_s=0.0):
    """固定空间样本贯穿整条轨迹，首次命中只计一次，不将时间片当独立事件。"""
    cumulative = [0.0] * (steps+1)
    dt = flight_s / steps
    def ball(t):
        fraction = t / flight_s
        return tuple(origin[j]+(target[j]-origin[j])*fraction for j in (0, 1))
    hold_steps = math.ceil(preparation_s / .05)
    times = ([i * preparation_s / hold_steps for i in range(hold_steps)] if hold_steps else [])
    times += [preparation_s + i*dt for i in range(steps+1)]
    points = [origin if t <= preparation_s else ball(t-preparation_s) for t in times]
    cumulative = [0.0] * len(points)
    steps = len(points)-1
    for sample in range(samples):
        radius = branch['radius_m'] * math.sqrt((sample+.5)/samples)
        angle = sample * math.pi * (3-math.sqrt(5))
        state = (branch['position'][0]+radius*math.cos(angle),
                 branch['position'][1]+radius*math.sin(angle), branch['heading'])
        # 出球后重新估计敌方拦截点；只搜索球到达 T 之前的可达点。
        fixed = target
        if branch['strategy'] == 'intercept_point':
            for i in range(steps+1):
                t = times[i]
                point = points[i]
                turn = abs(normalize_angle(math.atan2(point[1]-state[1], point[0]-state[0])-state[2]))
                arrival = turn/angular_speed + max(0, math.dist(state[:2], point)-BALL_CONTROL_RADIUS_M)/speed
                if arrival <= t:
                    fixed = point
                    break
        hit = math.dist(state[:2], origin) <= BALL_CONTROL_RADIUS_M
        cumulative[0] += int(hit)/samples
        for i in range(1, steps+1):
            if hit:
                for j in range(i, steps+1):
                    cumulative[j] += 1/samples
                break
            previous_ball, current_ball = points[i-1], points[i]
            previous = state
            state = _advance(state, current_ball if branch['strategy']=='chase_ball' else fixed,
                             times[i]-times[i-1], speed, angular_speed)
            hit = hit or _segment_distance(
                (previous[0]-previous_ball[0], previous[1]-previous_ball[1]),
                (state[0]-current_ball[0], state[1]-current_ball[1])) <= BALL_CONTROL_RADIUS_M
            cumulative[i] += int(hit)/samples
    return cumulative[hold_steps:]


def estimate_interception_risk(pose, motion, opponents, target, intercept=None, *,
                               ball_speed=4.0, speed=MAX_LINEAR, angular_speed=MAX_ANGULAR,
                               predictor=predict_opponent_placeholder, samples=48, preparation_s=0.0):
    """条件于我方在预计截球点成功出球，估算到 T 途中至少一次被截的概率。

    球直线匀速，ball_speed 单位 m/s，不可用 SDK power 代替。
    先预测出球时刻的敌人圆盘分布，再以确定性空间采样积分整段事件。
    每个敌人内部保留轨迹相关性，敌人之间使用独立近似。
    """
    if not all(math.isfinite(v) and v > 0 for v in (ball_speed, speed, angular_speed)):
        raise ValueError('speeds must be finite and positive')
    if not isinstance(samples, int) or not 1 <= samples <= 512:
        raise ValueError('samples must be an integer in 1..512')
    if target is None or len(target)!=2 or not all(math.isfinite(v) for v in target):
        raise ValueError('target must be a finite field point')
    if not math.isfinite(preparation_s) or not 0 <= preparation_s <= 8:
        raise ValueError('preparation_s must be finite in 0..8')
    own = intercept if intercept is not None else estimate_ball_intercept(pose, motion)
    base = dict(model='kick_path_equal_chase_intercept_v2', calibrated=False,
                event='intercept_kick_before_target', probability=None, target=target,
                opponents=[], control_radius_m=BALL_CONTROL_RADIUS_M,
                ball_speed_mps=ball_speed, samples_per_branch=samples,
                linear_speed_mps=speed, angular_speed_rps=angular_speed)
    if pose is None or own.get('status') != 'intercept':
        return dict(base, status='unavailable', reason='no_reachable_intercept')
    seconds = own['intercept_time_s']
    origin = motion.predict_ball_position(seconds)
    if origin is None:
        return dict(base, status='unavailable', reason='no_ball')
    flight = math.dist(origin, target)/ball_speed
    if flight <= 1e-9 or flight > 8:
        return dict(base, status='unavailable', reason='zero_length_or_flight_over_8s')
    # 我方站在球后，面向 T；这是出球所需站位，不证明已经能完成转身。
    distance = math.dist(origin, target)
    base.update(time_s=seconds+preparation_s, preparation_s=preparation_s, ball_position=origin, flight_time_s=flight,
                robot_position=tuple(origin[j]-(target[j]-origin[j])/distance*BALL_CONTROL_RADIUS_M for j in (0,1)))
    field = getattr(getattr(motion, 'context', None), 'field', None)
    if field is not None:
        lane = check_kick_lane(field, origin, target)
        base['lane'] = lane
        if lane['blocked']:
            return dict(base, status='blocked', reason=lane['reason'], probability=1.0,
                        path=[dict(t=0,position=origin,probability=1.0),
                              dict(t=flight,position=target,probability=1.0)])
    steps = max(1, math.ceil(flight/.05))
    survival = [1.0]*(steps+1)
    missing = []
    for robot in opponents.values():
        if robot.pose is None:
            missing.append(robot.player_id)
            continue
        branches = predictor(robot.pose, motion, seconds, speed=speed, angular_speed=angular_speed)
        if not branches or abs(sum(b['weight'] for b in branches)-1) > 1e-6:
            raise ValueError('predictor weights must sum to one')
        risks = [0.0]*(steps+1)
        components = []
        for branch in branches:
            radius, weight = branch['radius_m'], branch['weight']
            if (not all(math.isfinite(v) for v in (*branch['position'], radius, weight, branch['heading']))
                    or radius <= 0 or weight < 0 or branch['strategy'] not in ('chase_ball', 'intercept_point')):
                raise ValueError('invalid prediction component')
            branch_risks = _flight_risk(branch, origin, target, flight, speed, angular_speed, samples, steps, preparation_s)
            for i, risk in enumerate(branch_risks):
                risks[i] += weight*risk
            components.append(dict(branch, density_per_m2=weight/(math.pi*radius**2)))
        for i, risk in enumerate(risks):
            survival[i] *= 1-max(0, min(1, risk))
        base['opponents'].append(dict(id=robot.player_id, probability=min(1,risks[-1]), density_components=components))
    available = bool(base['opponents']) or not missing
    base['path'] = [dict(t=i*flight/steps,
                        position=tuple(origin[j]+(target[j]-origin[j])*i/steps for j in (0,1)),
                        probability=1-survival[i] if available else None) for i in range(steps+1)]
    return dict(base, status='partial' if missing else 'ok', missing_opponents=missing,
                probability=1-survival[-1] if available else None)
