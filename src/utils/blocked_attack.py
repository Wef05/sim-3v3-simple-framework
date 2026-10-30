"""结构阻挡后的保守出球：队友短传优先，其次向场内空位移球。"""
import math

from ..framework.types import Penalty
from ..param import (ARRIVE_DIST, GUARD_PASS_MAX_DIST_M, GUARD_PASS_LANE_MARGIN_M,
                     OPPONENT_RADIUS, TEAMMATE_RADIUS)
from .kick_lane import check_kick_lane, point_segment


def blocked_attack_target(context, player_id, origins):
    """返回 (落点, 动作标签)；所有候选均检查观测和补偿后的球路。

    固定候选顺序避免静止球每帧换方向。空位短传只把球向场内送，
    不假定必有队友接应；全部通道被封时返回 None，保留安全停车。
    """
    field = context.field
    ball = origins[-1]
    margin = max(.5, ARRIVE_DIST)

    def safe(target, receiver=None):
        if not (abs(target[0]) <= field.length / 2 - margin
                and abs(target[1]) <= field.width / 2 - margin):
            return False
        blockers = [(r, OPPONENT_RADIUS) for r in context.opponents.values()]
        blockers += [(r, TEAMMATE_RADIUS) for pid, r in context.teammates.items()
                     if pid not in (player_id, receiver)]
        for origin in origins:
            if not 1.0 <= math.dist(origin, target) <= GUARD_PASS_MAX_DIST_M:
                return False
            if check_kick_lane(field, origin, target)['blocked']:
                return False
            if any(r.pose is not None and
                   point_segment((r.pose.x, r.pose.y), origin, target)
                   <= radius + GUARD_PASS_LANE_MARGIN_M for r, radius in blockers):
                return False
        return True

    receivers = sorted(context.teammates.items(), key=lambda item: (
        math.dist(ball, (item[1].pose.x, item[1].pose.y))
        if item[1].pose is not None else math.inf, item[0]))
    for pid, robot in receivers:
        if pid == player_id or robot.pose is None:
            continue
        state = context.game.get_player_state(context.team_id, pid) if context.game else None
        if state is not None and state.penalty != Penalty.NONE:
            continue
        target = (robot.pose.x, robot.pose.y)
        if safe(target, pid):
            return target, f'pass:{pid}'

    # 从两侧角区均向中间、远离端线送球；不把球朝己方球门方向穿网。
    inward_x = -1 if ball[0] >= 0 else 1
    inward_y = -1 if ball[1] >= 0 else 1
    for dx, dy in ((1.5, 1.5), (2.5, 1.0), (1.0, 2.5), (2.0, 0.0)):
        target = (max(-field.length/2 + margin, min(field.length/2 - margin,
                  ball[0] + inward_x * dx)),
                  max(-field.width/2 + margin, min(field.width/2 - margin,
                  ball[1] + inward_y * dy)))
        if safe(target):
            return target, 'infield'
    return None
