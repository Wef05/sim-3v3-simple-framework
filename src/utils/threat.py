"""按抢球时间识别敌方威胁者，不执行动作或修改角色。

复用 attacker / freeman 的轨迹、速度、转身耗时和排序评分。
敌方不应用我方角色交接迟滞，也不排除某个固定编号的守门员。
敌方倒地/运动模式不可观测，按可行动估算；结果不是实际控球保证。
"""
import math
from ..framework.types import Penalty
from .interception import estimate_ball_intercept, intercept_score


def calculate_threat(context, players, motion):
    """返回最快敌人、每位我方球员的抢先判断及双方时间差。

can_beat: True 为严格更早，False 为同时或更晚，None 为无法判断。
fallback 仅供排名，不据此宣称能抢先；时间差为敌方减我方，正值领先。
"""
    enemy_team = next((t for t in context.game.teams if t.team_number != context.team_id),
                      None) if context.game else None
    candidates = []
    for pid, robot in sorted(context.opponents.items()):
        state = (enemy_team.players[pid - 1] if enemy_team and
                 1 <= pid <= len(enemy_team.players) else None)
        penalized = state is not None and state.penalty != Penalty.NONE
        estimate = estimate_ball_intercept(robot.pose, motion, penalized=penalized)
        candidates.append(dict(id=pid, penalized=penalized, intercept=estimate))
    valid = [c for c in candidates if math.isfinite(intercept_score(c['intercept']))]
    threat = min(valid, key=lambda c: (intercept_score(c['intercept']), c['id'])) if valid else None
    partial = any(not c['penalized'] and c['intercept']['status'] == 'unavailable'
                  for c in candidates)
    comparisons = []
    for p in sorted(players, key=lambda p: p.id):
        own = p.ball_intercept or {}
        ready = p.pose is not None and not p.is_fallen and not p.is_penalized and p.mode == 'walk'
        known = (ready and threat is not None and not partial and
                 own.get('status') == 'intercept' and threat['intercept']['status'] == 'intercept')
        margin = (threat['intercept']['intercept_time_s'] - own['intercept_time_s']) if known else None
        comparisons.append(dict(id=p.id, score_s=own.get('score_s'),
                                margin_s=margin, can_beat=margin > 0 if known else None))
    winners = [c for c in comparisons if c['can_beat'] is True]
    best = min(winners, key=lambda c: (c['score_s'], c['id'])) if winners else None
    return dict(status='unavailable' if threat is None else 'partial' if partial else 'ok',
                threat_id=threat['id'] if threat else None,
                threat_intercept=threat['intercept'] if threat else None,
                opponents=candidates, teammates=comparisons,
                can_beat=True if winners else False if comparisons and all(
                    c['can_beat'] is False for c in comparisons) else None,
                first_player_id=best['id'] if best else None,
                opponent_motion_assumption='ready',
                model='shared_ball_intercept')
