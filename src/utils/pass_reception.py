"""有界的传球请求记忆；触球只由球的实际位移推断，不能把请求当成功。"""
from __future__ import annotations

import math
from ..intents import KickIntent, MoveIntent, Role
from ..param import KICK_ENTER_M, ATTACK_PLAN_HORIZON_S
from ..role_allocator import get_phase, Phase
from .kick_lane import point_segment


def coordinate_reception(context, players, assignments, intents, store) -> dict:
    final = dict(intents)
    by_id = {p.id: p for p in players}
    pending = getattr(store, 'pending_pass', None)
    ball = None if context.ball is None else (context.ball.x, context.ball.y)
    if (get_phase(context) != Phase.NORMAL or ball is None or
            pending and not 0 <= context.now-pending['created_at'] <= ATTACK_PLAN_HORIZON_S):
        store.pending_pass = None
        return final
    if pending is not None:
        receiver = by_id.get(pending['receiver'])
        if (receiver is None or assignments[receiver.id].role in (Role.UNAVAILABLE, Role.GUARD)
                or receiver.pose is None or receiver.is_fallen or receiver.is_penalized
                or receiver.mode != 'walk'
                or point_segment(ball, pending['origin'], pending['target']) > .8):
            pending = None
    # 尚未观察到球离开发起点时，只接受本帧仍有效的同一传球请求。
    moving = pending is not None and math.dist(ball, pending['origin']) > .35
    if not moving:
        pending = None
        for passer in players:
            selected = (getattr(passer, 'attack_choice', None) or {}).get('selected')
            if isinstance(intents[passer.id], KickIntent) and selected and selected['kind'] == 'pass':
                previous = getattr(store, 'pending_pass', None)
                same = previous and previous['passer'] == passer.id and previous['receiver'] == selected['player_id']
                created = previous['created_at'] if same else context.now
                pending = dict(passer=passer.id, receiver=selected['player_id'],
                               origin=previous['origin'] if same else ball,
                               target=selected['target'], created_at=created)
                break
    store.pending_pass = pending
    if pending is None:
        return final
    receiver = by_id[pending['receiver']]
    if assignments[receiver.id].role == Role.ATTACKER:
        # 正常角色分配已经接管，不用旧传球点覆盖新的追球决策。
        store.pending_pass = None
        return final
    if moving and math.dist((receiver.pose.x, receiver.pose.y), ball) <= KICK_ENTER_M:
        receiver.action = 'receive:attack'
        final[receiver.id] = receiver.attack()
        store.pending_pass = None
    else:
        receiver.action = 'receive:move'
        face = math.atan2(-pending['target'][1], context.field.length/2-pending['target'][0])
        final[receiver.id] = MoveIntent(pending['target'], face=face, avoid_robots=True, motion_priority=75)
    return final
