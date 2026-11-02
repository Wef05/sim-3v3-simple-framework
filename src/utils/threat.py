"""按抢球时间识别敌方威胁者，不执行动作或修改角色。

复用 attacker / freeman 的轨迹、速度、转身耗时和排序评分。
敌方不应用我方角色交接迟滞，也不排除某个固定编号的守门员。
敌方倒地/运动模式不可观测，按可行动估算；结果不是实际控球保证。
"""
import math
from ..framework.types import Penalty
from .interception import estimate_ball_intercept, intercept_score
from .geom import own_goal


def race_approach(context, motion):
    """我方允许半平面的法向量；对方取反，退化情形使用球→己方球门。"""
    ball = motion.predict_ball_position(0)
    if ball is None:
        return None, 'no_ball'
    goal = own_goal(context)
    gx, gy = goal[0] - ball[0], goal[1] - ball[1]
    details = motion.ball_prediction_details() or {}
    vx, vy = details.get('velocity', (0, 0))
    speed = math.hypot(vx, vy)
    if details.get('velocity_estimated') and speed > 1e-6:
        nx, ny = -vy / speed, vx / speed
        side = nx * gx + ny * gy
        if abs(side) > 1e-6:
            return (nx, ny) if side > 0 else (-nx, -ny), 'trajectory_line'
    distance = math.hypot(gx, gy)
    return ((gx / distance, gy / distance) if distance > 1e-6 else (-1.0, 0.0)), 'goal_direction_fallback'


def calculate_threat(context, players, motion):
    """返回最快敌人、每位我方球员的抢先判断及双方时间差。

can_beat: True 为严格更早，False 为同时或更晚，None 为无法判断。
fallback 仅供排名，不据此宣称能抢先；时间差为敌方减我方，正值领先。
"""
    own_normal, side_basis = race_approach(context, motion)
    enemy_normal = tuple(-v for v in own_normal) if own_normal else None
    enemy_team = next((t for t in context.game.teams if t.team_number != context.team_id),
                      None) if context.game else None
    candidates = []
    for pid, robot in sorted(context.opponents.items()):
        state = (enemy_team.players[pid - 1] if enemy_team and
                 1 <= pid <= len(enemy_team.players) else None)
        penalized = state is not None and state.penalty != Penalty.NONE
        estimate = estimate_ball_intercept(robot.pose, motion, penalized=penalized,
                                           approach_normal=enemy_normal)
        candidates.append(dict(id=pid, penalized=penalized, intercept=estimate))
    valid = [c for c in candidates if math.isfinite(intercept_score(c['intercept']))]
    threat = min(valid, key=lambda c: (intercept_score(c['intercept']), c['id'])) if valid else None
    partial = any(not c['penalized'] and c['intercept']['status'] == 'unavailable'
                  for c in candidates)
    velocity_known = bool((motion.ball_prediction_details() or {}).get("velocity_estimated"))
    comparisons = []
    for p in sorted(players, key=lambda p: p.id):
        own = estimate_ball_intercept(p.pose, motion, fallen=p.is_fallen,
                                      penalized=p.is_penalized, approach_normal=own_normal)
        ready = p.pose is not None and not p.is_fallen and not p.is_penalized and p.mode == 'walk'
        eligible = not p.is_fallen and not p.is_penalized and p.mode == 'walk'
        known = (velocity_known and ready and threat is not None and not partial and
                 own.get('status') == 'intercept' and threat['intercept']['status'] == 'intercept')
        margin = (threat['intercept']['intercept_time_s'] - own['intercept_time_s']) if known else None
        comparisons.append(dict(id=p.id, eligible=eligible, score_s=own.get('score_s'), intercept=own,
                                margin_s=margin, can_beat=margin > 0 if known else None))
    eligible_rows = [c for c in comparisons if c["eligible"]]
    winners = [c for c in eligible_rows if c['can_beat'] is True]
    best = min(winners, key=lambda c: (c['score_s'], c['id'])) if winners else None
    return dict(status='unavailable' if threat is None else 'partial' if partial else 'ok',
                threat_id=threat['id'] if threat else None,
                threat_intercept=threat['intercept'] if threat else None,
                opponents=candidates, teammates=comparisons,
                can_beat=True if winners else False if eligible_rows and all(
                    c['can_beat'] is False for c in eligible_rows) else None,
                first_player_id=best['id'] if best else None,
                opponent_motion_assumption='ready',
                approach_basis=side_basis, own_approach_normal=own_normal,
                model='shared_ball_intercept_half_plane')
