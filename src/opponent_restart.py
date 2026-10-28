"""对方定位球：稳定分工与纯走位防守；沿用己方球门在 -x 的坐标约定。"""
import math
from types import SimpleNamespace

from .framework.types import SetPlay
from .param import (
    GOALKEEPER_ID, OPP_SET_WALL_DIST_M, OPPONENT_RESTART_AVOID_M,
    CIRCLE_MARGIN_M, GLOBAL_FIELD_MARGIN_M, GLOBAL_OBSTACLE_MARGIN_M,
    ARRIVE_DIST,
)


def defense_targets(ctx, key, count, receiver=None):
    """返回门将点和场上队员点；路径层负责进一步避障和合法点投影。"""
    f, b = ctx.field, ctx.ball
    gx = -f.length / 2
    dx, dy = gx-b.x, -b.y
    length = math.hypot(dx, dy)
    ux, uy = (dx/length, dy/length) if length > 1e-9 else (-1, 0)
    nx, ny = -uy, ux
    keeper = (gx + 0.5, max(-f.goal_width/2+0.6, min(f.goal_width/2-0.6, b.y*0.25)))
    d = max(OPP_SET_WALL_DIST_M,
            OPPONENT_RESTART_AVOID_M + GLOBAL_OBSTACLE_MARGIN_M + ARRIVE_DIST + 0.05)
    if key == SetPlay.DIRECT_FREE_KICK.value and b.x < 0:
        slots = [(b.x+d*ux+(i-(count-1)/2)*0.6*nx,
                  b.y+d*uy+(i-(count-1)/2)*0.6*ny) for i in range(count)]
    elif key == SetPlay.CORNER_KICK.value:
        side = 1 if b.y >= 0 else -1
        slots = [(gx+0.8, side*(f.goal_width/2-0.4)),
                 (gx+f.goal_area_length+0.7, side*0.5)]
    elif key in (SetPlay.THROW_IN.value, SetPlay.INDIRECT_FREE_KICK.value) and receiver:
        rx, ry = receiver.pose.x, receiver.pose.y
        r = max(math.hypot(b.x-rx, b.y-ry), 1e-9)
        slots = [(rx+0.8*(b.x-rx)/r, ry+0.8*(b.y-ry)/r),
                 (gx+f.penalty_area_length, 0)]
    else:
        slots = [(b.x+(d+i*1.5)*ux+(-0.35 if i == 0 else 0.35)*nx,
                  b.y+(d+i*1.5)*uy+(-0.35 if i == 0 else 0.35)*ny)
                 for i in range(count)]
    if key == 'kickoff':
        r = max(OPPONENT_RESTART_AVOID_M, f.circle_radius+CIRCLE_MARGIN_M)
        slots = [(min(x, -r-0.3), y) for x, y in slots]
    return keeper, slots[:count]


def defend_restart(ctx, players, store, key):
    if store is None:
        store = SimpleNamespace()
    if not players:
        store.opponent_defense = None
        return
    if ctx.ball is None:
        for p in players:
            p.action = 'opp_restart:stop'
            p.stop()
        return
    if key == SetPlay.PENALTY_KICK.value:
        # 点球有专门站位约束，未核对裁判规则前保留原退让策略。
        store.opponent_defense = None
        for p in players:
            p.wait_for_opponent_restart(OPPONENT_RESTART_AVOID_M)
        return
    ids = tuple(sorted(p.id for p in players))
    plan = getattr(store, 'opponent_defense', None)
    signature = (key, ids)
    if plan is None or plan.signature != signature:
        # 固定门将优先；不可行动时由最近球门者临时保护门前。
        keeper = next((p for p in players if p.id == GOALKEEPER_ID), None)
        if keeper is None:
            keeper = min(players, key=lambda p: (math.hypot(
                p.pose.x+ctx.field.length/2, p.pose.y), p.id))
        others = sorted((p for p in players if p.id != keeper.id), key=lambda p: p.id)
        opponents = [r for r in ctx.opponents.values() if r.pose is not None]
        taker = min(opponents, key=lambda r: math.hypot(
            r.pose.x-ctx.ball.x, r.pose.y-ctx.ball.y), default=None)
        receiver = min((r for r in opponents if r is not taker), key=lambda r: (
            math.hypot(r.pose.x+ctx.field.length/2, r.pose.y), r.player_id), default=None)
        if receiver and key in (SetPlay.THROW_IN.value, SetPlay.INDIRECT_FREE_KICK.value):
            others.sort(key=lambda p: (math.hypot(p.pose.x-receiver.pose.x,
                                                p.pose.y-receiver.pose.y), p.id))
        plan = SimpleNamespace(signature=signature, keeper=keeper.id,
                               outfield=[p.id for p in others],
                               receiver=receiver.player_id if receiver else None)
        store.opponent_defense = plan
    receiver = ctx.opponents.get(plan.receiver)
    if receiver is not None and receiver.pose is None:
        receiver = None
    keeper_target, slots = defense_targets(ctx, key, len(plan.outfield), receiver)
    targets = {plan.keeper: keeper_target, **dict(zip(plan.outfield, slots))}
    clearance = OPPONENT_RESTART_AVOID_M
    if key == 'kickoff':
        clearance = max(clearance, ctx.field.circle_radius+CIRCLE_MARGIN_M)
    for p in players:
        # 先脱离违规距离，再组织阵型；路径层仍保留完整禁入圆。
        if math.hypot(p.pose.x-ctx.ball.x, p.pose.y-ctx.ball.y) < clearance:
            p.wait_for_opponent_restart(clearance)
            continue
        target = targets.get(p.id)
        if target is None:
            p.stop()
            continue
        p.action = 'opp_restart:keeper' if p.id == plan.keeper else 'opp_restart:'+key
        p.walk_to(target, face=math.atan2(ctx.ball.y-p.pose.y, ctx.ball.x-p.pose.x),
                  avoid_ball=True, avoid_robots=True, keep_facing=True)
