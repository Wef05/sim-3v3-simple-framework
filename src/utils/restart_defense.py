"""敌方重启站位：纯几何选点与个体持位记忆，不发控制指令。

队伍坐标 x 正向为进攻方向、y 正向为看板上方，单位米。
避球沿用项目保护半径；这是策略约束，不是独立的赛事规则实现。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..framework.types import Context, Pose2D, SetPlay
from ..intents import MoveIntent, StopIntent
from ..param import (ARRIVE_DIST, NAV_BODY_RADIUS_M, NAV_SAFETY_MARGIN_M,
                     OPPONENT_RESTART_AVOID_M, RESTART_TARGET_MARGIN_M,
                     RESTART_PAIR_GAP_M, RESTART_ANCHOR_DRIFT_M)

Point = tuple[float, float]


@dataclass(frozen=True)
class RestartMemory:
    key: tuple
    anchor: Point | None
    targets: tuple[Point | None, Point | None]
    now: float


def kickoff_targets(context: Context) -> tuple[Point, Point]:
    """READY 与敌方开球共用前后错位的两道防线。"""
    r = max(context.field.circle_radius, OPPONENT_RESTART_AVOID_M)
    return ((-r - .6, .65), (-r - 2.0, -.8))


def _ball(context: Context) -> Point | None:
    # Context 已由 runtime 做新鲜度过滤；不把非法坐标作为真实球位。
    b = context.ball
    return (b.x, b.y) if b is not None and all(math.isfinite(v) for v in (b.x, b.y)) else None


def _legal(context: Context, point: Point, ball: Point | None,
           kickoff: bool, penalty: bool, occupied: list[Point]) -> bool:
    f = context.field
    x, y = point
    margin = RESTART_TARGET_MARGIN_M
    # 给门将保留整个小禁区纵深，点球时外场队员退到大禁区线外。
    min_x = -f.length / 2 + (f.penalty_area_length if penalty else f.goal_area_length) + margin
    if not (min_x <= x <= -margin and abs(y) <= f.width / 2 - margin):
        return False
    radius = max(f.circle_radius, OPPONENT_RESTART_AVOID_M) + margin
    if kickoff and math.hypot(x, y) < radius:
        return False
    if ball is not None and math.dist(point, ball) < radius:
        return False
    if any(math.dist(point, other) < RESTART_PAIR_GAP_M for other in occupied):
        return False
    guard = context.teammates.get(2)
    if guard is not None and guard.pose is not None:
        if math.dist(point, (guard.pose.x, guard.pose.y)) < 2 * NAV_BODY_RADIUS_M + NAV_SAFETY_MARGIN_M:
            return False
    return True


def defensive_targets(context: Context, ball: Point | None,
                      kickoff: bool) -> tuple[Point | None, Point | None]:
    """先算战术目标；受边界、保护距离约束时在己方半场找最近合法点。"""
    f = context.field
    kind = context.game.set_play if context.game is not None else SetPlay.NONE
    penalty = not kickoff and kind == SetPlay.PENALTY_KICK
    goal_x = -f.length / 2
    side = 1 if ball is None or ball[1] >= 0 else -1
    if kickoff or ball is None:
        desired = kickoff_targets(context)
    elif penalty:
        desired = ((goal_x + f.penalty_area_length + .6, 1.5),
                   (goal_x + f.penalty_area_length + .6, -1.5))
    elif kind == SetPlay.GOAL_KICK:
        desired = ((-1.5, side * .8), (goal_x + f.penalty_area_length + .5, -side * .8))
    else:
        # 第一人沿球→己方球门线退到保护圈外；边线球偏向场内封堵传中。
        bx, by = ball
        distance = math.hypot(goal_x - bx, by)
        radius = max(f.circle_radius, OPPONENT_RESTART_AVOID_M) + RESTART_TARGET_MARGIN_M + .15
        first = (bx + radius * (goal_x - bx) / max(distance, .01),
                 by - radius * by / max(distance, .01))
        if kind in (SetPlay.THROW_IN, SetPlay.CORNER_KICK):
            first = (min(bx - .5, -.5), by - side * radius)
        desired = (first, (goal_x + f.penalty_area_length - .5, -side * .8))
    selected: list[Point] = []
    result = []
    for target in desired:
        if not _legal(context, target, ball, kickoff, penalty, selected):
            # 仅几何冲突时扫描固定有界网格，避免场边投影后重新落入禁入圆。
            candidates = ((-f.length / 2 + .4 + i * .5, -f.width / 2 + .4 + j * .5)
                          for i in range(math.ceil(f.length))
                          for j in range(math.ceil(f.width * 2)))
            target = min((p for p in candidates if _legal(context, p, ball, kickoff, penalty, selected)),
                         key=lambda p: math.dist(p, target), default=None)
        result.append(target)
        if target is not None:
            selected.append(target)
    return tuple(result)


def restart_intent(context: Context, pose: Pose2D, slot: int, kickoff: bool,
                   memory: RestartMemory | None) -> tuple[MoveIntent | StopIntent, RestartMemory, str]:
    """持位时只更新朝向；球明显移动、时钟回退或重启类型变化时重选。"""
    ball = _ball(context)
    g = context.game
    key = (kickoff, g.set_play if g else None, g.kicking_team if g else None,
           g.first_half if g else None)
    penalty = not kickoff and g is not None and g.set_play == SetPlay.PENALTY_KICK
    reuse = memory is not None and memory.key == key and context.now >= memory.now
    if reuse:
        reuse = ((ball is None and memory.anchor is None) or
                 (ball is not None and memory.anchor is not None and
                  math.dist(ball, memory.anchor) < RESTART_ANCHOR_DRIFT_M))
    if reuse:
        occupied = []
        for target in memory.targets:
            if target is None or not _legal(context, target, ball, kickoff, penalty, occupied):
                reuse = False
                break
            occupied.append(target)
    targets = memory.targets if reuse else defensive_targets(context, ball, kickoff)
    memory = RestartMemory(key, memory.anchor if reuse else ball, targets, context.now)
    target = targets[slot]
    if target is None:
        return StopIntent(), memory, 'no_safe_target'
    face = math.atan2(ball[1] - pose.y, ball[0] - pose.x) if ball is not None else 0.0
    label = 'hold' if math.dist((pose.x, pose.y), target) <= ARRIVE_DIST else 'position'
    if ball is None:
        label += ':no_ball'
    return MoveIntent(target, face=face, avoid_ball=True, avoid_robots=True,
                      ball_keepout=max(context.field.circle_radius, OPPONENT_RESTART_AVOID_M)), memory, label
