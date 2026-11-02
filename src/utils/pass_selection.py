"""比较实际直射目标与传射路线；阈值是绝对概率差，不是相对降幅。"""
import math

from ..param import ATTACK_PASS_RISK_MARGIN, ATTACK_TARGET_REFRESH_S
from .kick_lane import check_kick_lane


def _probability(value):
    return (isinstance(value, (int, float)) and math.isfinite(value)
            and 0 <= value <= 1)


def select_pass(context, candidates: dict, scoring: dict, shot: dict,
                origins: tuple, receivers: tuple) -> dict:
    """保留射门选点，仅当完整传射路线改善至少指定百分点时替换第一脚。"""
    result = dict(shot, model='pass_risk_margin', pass_options=[],
                  required_margin=ATTACK_PASS_RISK_MARGIN, direct_risk=None,
                  best_pass_risk=None, risk_improvement=None)
    selected = shot['selected']
    target = selected['target'] if selected else shot['actual_target']
    direct = next((c for c in candidates.get('candidates', [])
                   if c.get('kind') == 'goal' and c.get('target') is not None
                   and tuple(c['target']) == tuple(target)), None)
    # 未知直射不能当成 100% 被截；结构不可用由原有安全替代流程处理。
    if (direct is None or direct.get('status') != 'ok'
            or not _probability(direct.get('probability'))
            or any(check_kick_lane(context.field, origin, target)['blocked'] for origin in origins)):
        return result
    result['direct_risk'] = direct['probability']
    if (scoring.get('evaluated_at') != candidates['evaluated_at']
            or not 0 <= context.now-candidates['evaluated_at'] < ATTACK_TARGET_REFRESH_S):
        return result
    for route in scoring.get('via_teammate', []):
        pid = route['player_id']
        passing, shooting = route.get('pass_probability'), route.get('shot_probability')
        receive, goal = route.get('receive_target'), route.get('target')
        robot = context.teammates.get(pid)
        reason = None
        if pid not in receivers or robot is None or robot.pose is None:
            reason = 'receiver_unavailable'
        elif receive is None or goal is None or not all(_probability(p) for p in (passing, shooting)):
            reason = 'unknown_or_blocked'
        elif not (abs(receive[0]) <= context.field.length/2 and abs(receive[1]) <= context.field.width/2):
            reason = 'outside_field'
        elif any(check_kick_lane(context.field, origin, receive)['blocked'] for origin in origins):
            reason = 'current_lane_blocked'
        probability = passing*shooting if all(_probability(p) for p in (passing, shooting)) else None
        if reason is None and probability <= 0:
            reason = 'zero_probability'
        option = dict(id=f'pass_{pid}', kind='pass', player_id=pid, target=receive,
                      shot_target=goal, pass_probability=passing, shot_probability=shooting,
                      probability=probability, excluded_reason=reason)
        result['pass_options'].append(option)
    available = sorted((o for o in result['pass_options'] if o['excluded_reason'] is None),
                       key=lambda o: (-round(o['probability'], 12), o['player_id']))
    if not available:
        return result
    best = available[0]
    risk = 1-best['probability']
    improvement = direct['probability']-risk
    result.update(best_pass_risk=risk, risk_improvement=improvement)
    # 仅去掉浮点尾差，让数学上恰好 30 个百分点的路线通过。
    if round(improvement, 12) >= ATTACK_PASS_RISK_MARGIN:
        result.update(selected=best, actual_target=best['target'], reason='pass_risk_margin',
                      runner_up=None, margin=None)
    return result
