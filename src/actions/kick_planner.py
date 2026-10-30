"""踢球规划：语义目标 → 场地目标点 → 场地方向与力度。

规划不下发动作、不修改右脚补偿；同一目标解析函数也可供角色层选择球后站位。
这种复用保证追球时的出球方向与执行时的目标含义一致。
"""
from dataclasses import dataclass
from ..intents import KickTarget
from ..param import *
from ..utils.geom import angle_to, clamp, dist, opponent_goal

@dataclass(frozen=True)
class KickPlan:
    """执行器输入：target 用于显示，direction 为场地角度，power 为 SDK 力度值。

    power 不是速度或距离；最终范围限制由 executor 统一处理。
    """
    target: tuple[float, float]
    direction: float
    power: float

def resolve_target(context, target: KickTarget):
    """从同一帧观测解析目标；缺失上下文或队友定位时返回 None。

    当前没有接球提前量模型，也不在这里检查传球通道；传球是否值得做由角色决定。
    未知 kind 属于编程错误，直接报错，避免悄悄把传球变成射门。
    """
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
    """按出球用途选择力度策略，方向始终由「球 → 目标点」计算。

    传球系数沿用现有配置，需要仿真校准；并非精确的球运动反解模型。
    现有 else 分支对 shot 及其他 style 使用前后场默认力度，新增用途时应显式扩展。
    """
    target = resolve_target(context, intent.target)
    ball = context.ball if context is not None else None
    if target is None or ball is None:
        return None
    if intent.style == 'pass':
        power = clamp(dist(ball.x, ball.y, *target) * GUARD_PASS_POWER_PER_M,
                      KICK_POWER_MIN, KICK_POWER_BACKFIELD)
    elif intent.style == 'clear':
        power = KICK_POWER_BACKFIELD
    else:
        power = KICK_POWER_BACKFIELD if ball.x < 0 else KICK_POWER_DEFAULT
    return KickPlan(target, angle_to(ball.x, ball.y, *target), power)
