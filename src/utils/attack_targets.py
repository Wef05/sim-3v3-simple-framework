"""只读候选目标评估，不选择目标、不产生动作。"""
import math
from ..param import (MAX_LINEAR, KICK_RISK_BALL_SPEED_MPS,
                     ATTACK_TARGET_LEAD_M, ATTACK_GOAL_SAMPLES, ATTACK_TARGET_SAMPLES)
from .interception_risk import estimate_interception_risk, predict_opponent_placeholder


class TeammateMotion:
    """连续观测匀速预测；首次、丢失、超速或时间异常时采用静止占位。"""
    def __init__(self):
        self.previous = {}
        self.velocities = {}

    def update(self, context):
        current = {}
        velocities = {}
        for pid, robot in context.teammates.items():
            if robot.pose is None:
                continue
            t = robot.last_seen_at if robot.last_seen_at > 0 else context.now
            sample = (t, robot.pose.x, robot.pose.y)
            old = self.previous.get(pid)
            velocity = None
            if old == sample:
                velocity = self.velocities.get(pid)
            elif old is not None and 0 < t-old[0] <= 1:
                v = ((sample[1]-old[1])/(t-old[0]), (sample[2]-old[2])/(t-old[0]))
                if math.hypot(*v) <= MAX_LINEAR:
                    velocity = v
            if context.now-t > 1:
                velocity = None
            current[pid], velocities[pid] = sample, velocity
        self.previous, self.velocities = current, velocities

    def predict(self, context, pid, seconds):
        pose = context.teammates[pid].pose
        velocity = self.velocities.get(pid)
        age = max(0, context.now-self.previous.get(pid, (context.now,))[0])
        v = velocity or (0, 0)
        # 场内截断是预测假设，不是运动指令。
        point = (max(-context.field.length/2, min(context.field.length/2, pose.x+v[0]*(seconds+age))),
                 max(-context.field.width/2, min(context.field.width/2, pose.y+v[1]*(seconds+age))))
        return point, 'constant_velocity' if velocity is not None else 'stationary_fallback'


def receiver_target(position, goal, lead=ATTACK_TARGET_LEAD_M):
    """沿接球队员→球门中心的线段，在队员前方 lead 米处落球。"""
    distance = math.dist(position, goal)
    fraction = min(lead/distance, 1) if distance else 0
    return tuple(position[i]+fraction*(goal[i]-position[i]) for i in (0, 1))


def evaluate_attack_targets(context, player_id, pose, motion, intercept, teammate_motion):
    result = dict(evaluated_at=context.now, decision_enabled=False,
                  future_self='todo', samples_per_branch=ATTACK_TARGET_SAMPLES,
                  ball_speed_mps=KICK_RISK_BALL_SPEED_MPS, candidates=[])
    origin = motion.predict_ball_position(intercept.get('intercept_time_s') or 0)
    reachable = intercept.get('status') == 'intercept'
    wait = intercept.get('intercept_time_s') or 0
    result.update(origin=origin if reachable else None, kick_delay_s=wait if reachable else None)
    goal = (context.field.length/2, 0)
    # 多个球门内落点，离门柱 0.15 米；不包含球门外区域。
    half = max(0, context.field.goal_width/2-.15)
    specs = [dict(id=f'goal_{i}', kind='goal', label=f'球门 {i+1}',
                  target=(goal[0], -half+2*half*i/(ATTACK_GOAL_SAMPLES-1)))
             for i in range(ATTACK_GOAL_SAMPLES)]
    for pid, robot in sorted(context.teammates.items()):
        if pid == player_id:
            continue
        spec = dict(id=f'teammate_{pid}',kind='teammate',label=f'队友 {pid}',player_id=pid,target=None)
        if robot.pose is not None and origin is not None:
            # 联立接球时刻与球路长度；速度上限小于球速，迭代至毫米级。
            seconds = wait
            for _ in range(40):
                position, model = teammate_motion.predict(context, pid, seconds)
                target = receiver_target(position, goal)
                next_time = wait+math.dist(origin,target)/KICK_RISK_BALL_SPEED_MPS
                if abs(next_time-seconds) < .0001:
                    break
                seconds = next_time
            spec.update(target=target, receiver_position=position, receiver_model=model,
                        receive_time_s=seconds, lead_m=ATTACK_TARGET_LEAD_M)
        specs.append(spec)
    # 所有目标共享同一出球时刻的敌方分布，避免重复预测。
    distributions = {}
    def predictor(enemy_pose, ball_motion, seconds, **kwargs):
        key = (enemy_pose.x,enemy_pose.y,enemy_pose.theta,seconds)
        if key not in distributions:
            distributions[key] = predict_opponent_placeholder(enemy_pose,ball_motion,seconds,**kwargs)
        return distributions[key]
    for spec in specs:
        if spec['target'] is None:
            risk = dict(status='unavailable',probability=None,reason='no_receiver_pose')
        else:
            risk = estimate_interception_risk(pose,motion,context.opponents,spec['target'],intercept,
                ball_speed=KICK_RISK_BALL_SPEED_MPS,samples=ATTACK_TARGET_SAMPLES,predictor=predictor)
        # 仅发送摘要；避免为每个候选重复发送密度与整条路径，撑爆 UDP 快照。
        spec.update({k:risk.get(k) for k in ('status','probability','reason','flight_time_s')})
        result['candidates'].append(spec)
    return result
