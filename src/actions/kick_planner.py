"""Resolve semantic kick targets, then calculate direction and power."""
from dataclasses import dataclass
from ..intents import KickTarget
from ..param import *
from ..utils.geom import angle_to, clamp, dist, opponent_goal

@dataclass(frozen=True)
class KickPlan:
    target: tuple[float, float]
    direction: float
    power: float

def resolve_target(context, target: KickTarget):
    if context is None:
        return None
    if target.kind == 'goal':
        return opponent_goal(context)
    if target.kind == 'point':
        return target.point
    if target.kind == 'teammate':
        teammate = context.teammates.get(target.player_id)
        if teammate is not None and teammate.pose is not None:
            return teammate.pose.x, teammate.pose.y
        return None
    raise ValueError(f'Unknown kick target: {target.kind}')

def plan_kick(context, intent):
    target = resolve_target(context, intent.target)
    ball = context.ball if context is not None else None
    if target is None or ball is None:
        return None
    if intent.style == 'kickoff':
        power = KICK_POWER_OUR_KICKOFF
    elif intent.style == 'pass':
        power = clamp(dist(ball.x, ball.y, *target) * GUARD_PASS_POWER_PER_M,
                      KICK_POWER_MIN, KICK_POWER_BACKFIELD)
    elif intent.style == 'clear':
        power = KICK_POWER_BACKFIELD
    else:
        power = KICK_POWER_BACKFIELD if ball.x < 0 else KICK_POWER_DEFAULT
    return KickPlan(target, angle_to(ball.x, ball.y, *target), power)
