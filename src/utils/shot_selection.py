"""按未被截概率选择射门点；从中央逐点向外的被截率惩罚是策略假设。"""
import math

from ..param import SHOT_OUTWARD_PENALTY_STEP
from .kick_lane import check_kick_lane


def select_shot(field, candidates: dict, origins: tuple) -> dict:
    """返回可录制的选择依据；未知/阻挡/零分均不参与排名。"""
    options = []
    for candidate in candidates.get('candidates', []):
        if candidate.get('kind') != 'goal':
            continue
        target = candidate.get('target')
        risk = candidate.get('probability')
        valid = (candidate.get('status') == 'ok' and isinstance(risk, (int, float))
                 and math.isfinite(risk) and 0 <= risk <= 1)
        raw = 1 - risk if valid else None
        penalty = SHOT_OUTWARD_PENALTY_STEP * candidate['goal_center_steps']
        score = max(0.0, raw - penalty) if raw is not None else None
        reason = None
        if target is None:
            reason = 'no_target'
        elif not valid:
            reason = 'unknown_or_blocked'
        elif any(check_kick_lane(field, origin, target)['blocked'] for origin in origins):
            reason = 'current_lane_blocked'
        elif score <= 0:
            reason = 'zero_probability'
        options.append(dict(id=candidate['id'], kind='shot', target=target,
                            interception_probability=risk, raw_probability=raw,
                            penalty=penalty, probability=score, excluded_reason=reason))
    # 仅消除浮点尾差；同分优先中央，再以稳定编号决定。
    available = sorted((o for o in options if o['excluded_reason'] is None),
                       key=lambda o: (-round(o['probability'], 12), abs(o['target'][1]), o['id']))
    selected = available[0] if available else None
    runner_up = available[1] if len(available) > 1 else None
    return dict(enabled=True, model='shot_outward_penalty', evaluated_at=candidates['evaluated_at'],
                options=options, selected=selected, runner_up=runner_up,
                margin=max(0.0, selected['probability'] - runner_up['probability']) if runner_up else None,
                reason='best_shot' if selected else 'center_fallback',
                actual_target=selected['target'] if selected else (field.length / 2, 0))
