"""几何 helper —— 纯函数,无状态,吃 Context / 坐标。

【utils】框架预置的工具样例,用户可读可改可 fork,也可在本目录加自己的 util
(如"球是否出界")。放这里是因为这些计算换打法不变,且被 nav / 策略复用。

坐标系:队伍场地视角,+x 朝对方球门,-x 朝己方球门,场地中心 (0,0)。
"""

from __future__ import annotations

import math

from ..framework.types import Context
from ..param import GOAL_TARGET_DEPTH_M


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def normalize_angle(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


def deg2rad(deg: float) -> float:
    """角度制转弧度制。"""
    return math.radians(deg)


def rad2deg(rad: float) -> float:
    """弧度制转角度制。"""
    return math.degrees(rad)


def dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def angle_to(fx: float, fy: float, tx: float, ty: float) -> float:
    """从 (fx,fy) 指向 (tx,ty) 的场地角度。"""
    return math.atan2(ty - fy, tx - fx)


def opponent_goal(ctx: Context) -> tuple[float, float]:
    """对方球门中心(进攻目标),取门线后方一点避免球压线时反向瞄准。"""
    return (ctx.field.length / 2.0 + GOAL_TARGET_DEPTH_M, 0.0)


def own_goal(ctx: Context) -> tuple[float, float]:
    """己方球门中心(防守核心)。"""
    return (-ctx.field.length / 2.0, 0.0)


def own_goal_area_center(ctx: Context) -> tuple[float, float]:
    """己方小禁区(球门区)中心 —— 守门员无威胁时的默认站位。

    小禁区从己方门线沿 +x 伸进 ``goal_area_length``,中心在门线内侧半个进深处。
    """
    return (-ctx.field.length / 2.0 + ctx.field.goal_area_length / 2.0, 0.0)


def clamp_inside_field(
    ctx: Context, x: float, y: float, margin: float = 2.0,
) -> tuple[float, float]:
    """把 (x,y) 夹进场地矩形内(留 margin 余量)。"""
    half_l = ctx.field.length / 2.0 - margin
    half_w = ctx.field.width / 2.0 - margin
    return (clamp(x, -half_l, half_l), clamp(y, -half_w, half_w))

def behind_ball(
    ball_x: float, ball_y: float, aim: tuple[float, float], offset: float,
) -> tuple[float, float]:
    """用于计算站位。球在 球→``aim`` 连线上的"后方"点:从球沿【背离 aim】方向退 ``offset`` 米。

    追球走位目标用它:球落在机器人与 ``aim`` 之间,到位时天然对准 aim 方向。
    aim 与球重合(退化)时,方向不定,直接返回球位。
    """
    dx, dy = aim[0] - ball_x, aim[1] - ball_y
    d = math.hypot(dx, dy)
    if d < 1e-6:
        return (ball_x, ball_y)
    ux, uy = dx / d, dy / d              # 球指向 aim 的单位向量
    return (ball_x - ux * offset, ball_y - uy * offset)   # 背离 aim 退 offset
