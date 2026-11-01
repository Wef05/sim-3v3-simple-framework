"""传射路线估计与选择；所有耗时为仿真秒，概率未经实战标定。"""
from __future__ import annotations

import math
from types import SimpleNamespace

from ..framework.types import Pose2D
from ..param import (ATTACK_EDGE_PENALTY, ATTACK_KICK_PREPARE_S,
                     ATTACK_PASS_ADVANTAGE, ATTACK_PLAN_HORIZON_S,
                     ATTACK_TARGET_SAMPLES, CHASE_BEHIND_M,
                     KICK_RISK_BALL_SPEED_MPS, MAX_ANGULAR, MAX_LINEAR,
                     NAV_TURN_SPEED_RPS, OMNI_DIST, BALL_CONTROL_RADIUS_M)
from .attack_targets import receiver_target
from .geom import behind_ball, normalize_angle
from .interception_risk import estimate_interception_risk, predict_opponent_placeholder, _advance
from .obstacles import collect_obstacles
from .path_planner import clamp_target, path_time, plan_global_path


def success(candidate: dict) -> float | None:
    if candidate.get('status') == 'blocked':
        return 0.0
    if candidate.get('status') != 'ok' or candidate.get('probability') is None:
        return None
    return 1 - candidate['probability']


def best_shot(candidates: list[dict]) -> dict:
    """两侧最外点扣 10 个百分点；未知点不参与排名，部分敌方观测不当作零风险。"""
    shots = []
    for i, candidate in enumerate(candidates):
        raw = success(candidate)
        penalty = ATTACK_EDGE_PENALTY if i in (0, len(candidates)-1) else 0.0
        shots.append(dict(candidate, raw_probability=raw, edge_penalty=penalty,
                          probability=max(0.0, raw-penalty) if raw is not None else None))
    known = [s for s in shots if s['probability'] is not None]
    best = max(known, key=lambda s: (s['probability'], -abs(s['target'][1]))) if known else None
    return dict(probability=best['probability'] if best else None,
                target=best['target'] if best else None, shots=shots,
                plan=best.get('plan') if best else None)


def kick_preparation(context, pid: int, pose: Pose2D, origin: tuple,
                     target: tuple, earliest: float) -> dict | None:
    """沿现有避障折线到球后点，再调整朝向；行走和等待可重叠。"""
    stance = behind_ball(*origin, target, CHASE_BEHIND_M)
    if clamp_target(context, stance) != stance:
        return None
    obstacles = collect_obstacles(context, pid, ball=False, robots=True, goals=True)
    start = (pose.x, pose.y)
    omni = math.dist(start, stance) <= OMNI_DIST
    path = plan_global_path(context, start, stance, obstacles, heading=pose.theta, omni=omni)
    if path is None:
        return None
    travel = path_time(path, pose.theta, omni=omni)
    heading = pose.theta
    if not omni:
        for a, b in zip(path, path[1:]):
            if math.dist(a, b) > 1e-9:
                heading = math.atan2(b[1]-a[1], b[0]-a[0])
    aim = math.atan2(target[1]-origin[1], target[0]-origin[0])
    turn = abs(normalize_angle(aim-heading))/NAV_TURN_SPEED_RPS
    arrival = max(earliest, travel)
    preparation = turn + ATTACK_KICK_PREPARE_S
    if arrival + preparation > ATTACK_PLAN_HORIZON_S:
        return None
    return dict(path=path, stance=stance, arrival_s=arrival, travel_s=travel,
                turn_s=turn, preparation_s=preparation, kick_time_s=arrival+preparation,
                origin=origin)


def _risk(context, pose, motion, target, plan, predictor=predict_opponent_placeholder) -> dict:
    if plan is None:
        return dict(target=target, status='unavailable', probability=None, reason='no_preparation_path')
    risk = estimate_interception_risk(
        pose, motion, context.opponents, target,
        dict(status='intercept', intercept_time_s=plan['arrival_s']),
        ball_speed=KICK_RISK_BALL_SPEED_MPS, samples=ATTACK_TARGET_SAMPLES,
        predictor=predictor, preparation_s=plan['preparation_s'])
    return dict(target=target, status=risk['status'], probability=risk['probability'],
                reason=risk.get('reason'), plan=plan)


def _first_plan(context, pid, pose, motion, intercept, target):
    if intercept.get('status') != 'intercept':
        return None
    wait = intercept['intercept_time_s']
    # 球后站位依赖出球点，出球点又依赖到达时间；有限次求解且检查收敛。
    for _ in range(12):
        origin = motion.predict_ball_position(wait)
        if origin is None:
            return None
        plan = kick_preparation(context, pid, pose, origin, target, intercept['intercept_time_s'])
        if plan is None:
            return None
        if abs(plan['arrival_s']-wait) < .01:
            return plan
        wait = plan['arrival_s']
    return None


def receiver_distributions(context, motion, origin, target, wait, flight, preparation=0.0):
    """保留既有两种敌方策略，推进准备及传球，不能复用当前站位。"""
    distributions = {}
    duration = preparation+flight
    steps = max(1, math.ceil(duration/.05))
    dt = duration/steps
    points = [tuple(origin[j]+(target[j]-origin[j])*max(0, i*dt-preparation)/flight
                    for j in (0, 1)) for i in range(steps+1)]
    for robot in context.opponents.values():
        if robot.pose is None:
            continue
        branches = predict_opponent_placeholder(robot.pose, motion, wait)
        predicted = []
        for branch in branches:
            state = (*branch['position'], branch['heading'])
            fixed = target
            if branch['strategy'] == 'intercept_point':
                for i, point in enumerate(points):
                    turn = abs(normalize_angle(math.atan2(point[1]-state[1], point[0]-state[0])-state[2]))
                    arrival = turn/MAX_ANGULAR+max(0, math.dist(state[:2], point)-BALL_CONTROL_RADIUS_M)/MAX_LINEAR
                    if arrival <= i*dt:
                        fixed = point
                        break
            for point in points[1:]:
                state = _advance(state, point if branch['strategy'] == 'chase_ball' else fixed,
                                 dt, MAX_LINEAR, MAX_ANGULAR)
            predicted.append(dict(branch, position=state[:2], heading=state[2],
                                  radius_m=branch['radius_m']+.15*duration))
        distributions[robot.pose] = predicted
    return distributions


def evaluate_scoring(context, pose, motion, candidates, receiver_ids,
                     *, player_id=0, intercept=None, teammate_motion=None) -> dict:
    goals = [c for c in candidates['candidates'] if c['kind'] == 'goal']
    if intercept is None:
        wait = candidates.get('kick_delay_s')
        intercept = dict(status='intercept' if wait is not None else 'unavailable', intercept_time_s=wait)
    direct = [_risk(context, pose, motion, g['target'],
                    _first_plan(context, player_id, pose, motion, intercept, g['target'])) for g in goals]
    result = dict(evaluated_at=candidates['evaluated_at'], decision_enabled=False,
                  model='prepared_one_minus_interception', direct=best_shot(direct), via_freeman=[])
    for pid in receiver_ids:
        robot = context.teammates.get(pid)
        c = next((c for c in candidates['candidates'] if c.get('player_id') == pid), None)
        row = dict(player_id=pid, pass_probability=None, shot_probability=None,
                   probability=None, target=None, shots=[], receive_target=None)
        result['via_freeman'].append(row)
        if robot is None or robot.pose is None or c is None or c['target'] is None:
            continue
        target = c['target']
        plan = None
        for _ in range(12):
            plan = _first_plan(context, player_id, pose, motion, intercept, target)
            if plan is None:
                break
            flight = math.dist(plan['origin'], target)/KICK_RISK_BALL_SPEED_MPS
            receive_time = plan['kick_time_s']+flight
            if teammate_motion is None:
                break
            position, _ = teammate_motion.predict(context, pid, receive_time)
            updated = receiver_target(position, (context.field.length/2, 0))
            if math.dist(updated, target) < .01:
                break
            target = updated
        else:
            plan = None
        if plan is None:
            continue
        # 最后一次迭代的 target 与 plan 必须配对，禁止拼接不同时间的球路。
        if math.dist(plan['origin'], target) < .75:
            continue
        passing = _risk(context, pose, motion, target, plan)
        row.update(pass_probability=success(passing), receive_target=target, pass_plan=plan,
                   receive_time_s=receive_time)
        if row['pass_probability'] is None:
            continue
        if row['pass_probability'] == 0:
            row['probability'] = 0.0
            continue
        distributions = receiver_distributions(context, motion, plan['origin'], target,
            plan['arrival_s'], flight, plan['preparation_s'])
        future_motion = SimpleNamespace(context=context, predict_ball_position=lambda seconds: target)
        def predictor(enemy_pose, ball_motion, seconds, **kwargs):
            advanced = []
            for branch in distributions[enemy_pose]:
                state = (*branch['position'], branch['heading'])
                steps = max(1, math.ceil(seconds/.05))
                for _ in range(steps):
                    state = _advance(state, target, seconds/steps, MAX_LINEAR, MAX_ANGULAR)
                advanced.append(dict(branch, position=state[:2], heading=state[2],
                                     radius_m=branch['radius_m']+.15*seconds))
            return advanced
        shots = []
        for goal in goals:
            second = kick_preparation(context, pid, robot.pose, target, goal['target'], receive_time)
            if second is not None:
                late = max(0, second['arrival_s']-receive_time)
                second = dict(second, arrival_s=0.0, receive_late_s=late,
                              preparation_s=late+second['preparation_s'],
                              kick_time_s=second['kick_time_s']-receive_time)
            shots.append(_risk(context, Pose2D(*target), future_motion, goal['target'], second, predictor))
        shot = best_shot(shots)
        row.update(shot_probability=shot['probability'], target=shot['target'], shots=shot['shots'],
                   shot_plan=shot['plan'], probability=(row['pass_probability']*shot['probability']
                   if shot['probability'] is not None else None))
    return result


def choose_route(scoring: dict, receiver_ids: tuple) -> dict | None:
    """严格超过 20 个百分点才传；未知直射概率不能用零代替。"""
    direct = scoring['direct']
    if direct['probability'] is None:
        return None
    choice = dict(kind='shot', target=direct['target'], probability=direct['probability'],
                  plan=direct.get('plan'), advantage=0.0)
    routes = sorted((r for r in scoring['via_freeman'] if r['player_id'] in receiver_ids
                     and r['probability'] is not None), key=lambda r: (-r['probability'], r['player_id']))
    if routes:
        best = routes[0]
        advantage = round(best['probability']-direct['probability'], 12)
        if advantage > ATTACK_PASS_ADVANTAGE:
            choice = dict(kind='pass', player_id=best['player_id'], target=best['receive_target'],
                          shot_target=best['target'], probability=best['probability'],
                          plan=best.get('pass_plan'), receive_time_s=best.get('receive_time_s'),
                          advantage=advantage)
    return choice if choice['probability'] > 0 else None


def fallback_shot(scoring: dict, field, origins: tuple) -> dict | None:
    """无正分路线时仍射门；保留未知概率，并按当前球位检查球门结构。"""
    from .kick_lane import check_kick_lane

    shots = list(scoring['direct'].get('shots', ()))
    center = (field.length/2, 0.0)
    if not any(tuple(shot['target']) == center for shot in shots):
        shots.append(dict(target=center, probability=None))
    safe = [shot for shot in shots if not any(
        check_kick_lane(field, origin, shot['target'])['blocked'] for origin in origins)]
    if not safe:
        return None
    # 已知的调整后分数（即使为零）优先；全部未知时按距中心远近排序。
    best = max(safe, key=lambda shot: (shot.get('probability') is not None,
        shot.get('probability') if shot.get('probability') is not None else -1.0,
        -abs(shot['target'][1])))
    return dict(kind='shot', target=best['target'], probability=best.get('probability'),
                plan=best.get('plan'), advantage=0.0, shot_fallback=True,
                reason='no_suitable_route')
