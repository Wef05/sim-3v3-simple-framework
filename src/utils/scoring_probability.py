"""两条进球路线的展示估计，不参与动作；进球率仅取 1-被拦概率。"""
import math
from dataclasses import replace
from types import SimpleNamespace
from ..framework.types import Pose2D
from ..param import MAX_LINEAR, MAX_ANGULAR, BALL_CONTROL_RADIUS_M, ATTACK_TARGET_SAMPLES, KICK_RISK_BALL_SPEED_MPS
from .interception_risk import estimate_interception_risk, predict_opponent_placeholder, _advance
from .geom import normalize_angle


def success(candidate):
    if candidate.get('status') == 'blocked':
        return 0.0
    if candidate.get('status') != 'ok' or candidate.get('probability') is None:
        return None
    return 1-candidate['probability']


def best_shot(candidates):
    shots = [dict(target=c['target'], probability=success(c),status=c['status']) for c in candidates]
    known = [s for s in shots if s['probability'] is not None]
    best = max(known,key=lambda s:s['probability']) if known and len(known)==len(shots) else None
    return dict(probability=best['probability'] if best else None,
                target=best['target'] if best else None, shots=shots)


def receiver_distributions(context, motion, origin, target, wait, flight):
    """敌人先到出球时刻，再沿传球轨迹追球/预判；保留两种分支密度。"""
    distributions = {}
    steps=max(1,math.ceil(flight/.05));dt=flight/steps
    points=[tuple(origin[j]+(target[j]-origin[j])*i/steps for j in (0,1)) for i in range(steps+1)]
    for pid,robot in context.opponents.items():
        if robot.pose is None:continue
        branches=predict_opponent_placeholder(robot.pose,motion,wait)
        predicted=[]
        for branch in branches:
            state=(*branch['position'],branch['heading'])
            fixed=target
            if branch['strategy']=='intercept_point':
                for i,point in enumerate(points):
                    turn=abs(normalize_angle(math.atan2(point[1]-state[1],point[0]-state[0])-state[2]))
                    if turn/MAX_ANGULAR+max(0,math.dist(state[:2],point)-BALL_CONTROL_RADIUS_M)/MAX_LINEAR<=i*dt:
                        fixed=point;break
            for point in points[1:]:
                state=_advance(state,point if branch['strategy']=='chase_ball' else fixed,dt,MAX_LINEAR,MAX_ANGULAR)
            predicted.append(dict(branch,position=state[:2],heading=state[2],radius_m=branch['radius_m']+.15*flight))
        distributions[pid]=predicted
    return distributions


def evaluate_scoring(context, pose, motion, candidates, freeman_ids):
    goals=[c for c in candidates['candidates'] if c['kind']=='goal']
    result=dict(evaluated_at=candidates['evaluated_at'],decision_enabled=False,
                model='one_minus_interception',direct=best_shot(goals),via_freeman=[])
    for pid in freeman_ids:
        c=next((c for c in candidates['candidates'] if c.get('player_id')==pid),None)
        passing=success(c) if c else None
        row=dict(player_id=pid,pass_probability=passing,shot_probability=None,
                 probability=None,target=None,shots=[],receive_target=c.get('target') if c else None)
        result['via_freeman'].append(row)
        if passing is None:continue
        if passing==0:
            row['probability']=0.0;continue
        origin,target=candidates['origin'],c['target']
        wait=candidates['kick_delay_s'];flight=math.dist(origin,target)/KICK_RISK_BALL_SPEED_MPS
        distributions=receiver_distributions(context,motion,origin,target,wait,flight)
        # 唯一占位 pose 仅作 predictor 索引；真正分布由上面的两阶段预测提供。
        opponents={pid:replace(robot,pose=Pose2D(float(pid),0,0)) if robot.pose else robot
                   for pid,robot in context.opponents.items()}
        future_motion=SimpleNamespace(context=context,predict_ball_position=lambda seconds:target)
        def predictor(enemy_pose,*args,**kwargs):return distributions[int(enemy_pose.x)]
        shots=[]
        for goal in goals:
            risk=estimate_interception_risk(Pose2D(*target),future_motion,opponents,goal['target'],
                dict(status='intercept',intercept_time_s=0),predictor=predictor,samples=ATTACK_TARGET_SAMPLES,
                ball_speed=KICK_RISK_BALL_SPEED_MPS)
            shots.append(dict(target=goal['target'],status=risk['status'],probability=risk['probability']))
        shot=best_shot(shots)
        row.update(shot_probability=shot['probability'],target=shot['target'],shots=shot['shots'],
                   probability=passing*shot['probability'] if shot['probability'] is not None else None)
    return result
