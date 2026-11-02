"""敌方重启的优先撤离；只计算意图，不修改观测或发送动作。"""
from __future__ import annotations

import math

from ..framework.types import Context, Pose2D
from ..intents import MoveIntent, StopIntent
from ..param import (GLOBAL_OBSTACLE_MARGIN_M, NAV_BODY_RADIUS_M,
                     NAV_SAFETY_MARGIN_M, OPPONENT_RESTART_AVOID_M)
from .navigation import MotionTracker, NavigationFrame
from .path_planner import segment_clear

# 进入/退出阈值与路径规划膨胀边界一致，避免合法绕圆路径反复触发撤离。
# 目标再远 15 cm，避免到达误差使其留在圈内。
ESCAPE_TARGET_EXTRA_M = .15
ESCAPE_ARRIVE_M = .03
ESCAPE_PRIORITY = 110
ESCAPE_DIRECTIONS = 72


def escape_intent(context: Context, pid: int, pose: Pose2D | None
                  ) -> tuple[MoveIntent | StopIntent | None, str]:
    """调用方确认敌方重启且可行动；None 表示不需撤离或观测不足。

    不继承旧球位、旧目标或跨阶段记忆。每帧按当前几何重验，实际动态
    碰撞与队友预约仍交给执行层。离开后立即恢复原角色。
    """
    ball = context.ball
    if pose is None or ball is None or not all(math.isfinite(v) for v in
            (pose.x, pose.y, pose.theta, ball.x, ball.y)):
        return None, 'no_observation'
    start = pose.x, pose.y
    center = ball.x, ball.y
    keepout = max(context.field.circle_radius, OPPONENT_RESTART_AVOID_M)
    release = keepout + NAV_BODY_RADIUS_M + max(
        GLOBAL_OBSTACLE_MARGIN_M, NAV_SAFETY_MARGIN_M)
    distance = math.dist(start, center)
    if distance >= release:
        return None, 'outside'
    radius = release + ESCAPE_TARGET_EXTRA_M
    policy = MoveIntent(start, avoid_ball=True, avoid_robots=True, ball_keepout=keepout)
    # 复用导航的实体/球障碍构造；此处只使用位置，速度估计在执行层维护。
    obstacles = NavigationFrame(context, MotionTracker()).route_obstacles(pid, policy)
    radial = math.atan2(pose.y-ball.y, pose.x-ball.x) if distance > 1e-8 else math.pi
    angles = [radial] + [2*math.pi*i/ESCAPE_DIRECTIONS for i in range(ESCAPE_DIRECTIONS)]
    candidates = []
    for angle in angles:
        target = ball.x+radius*math.cos(angle), ball.y+radius*math.sin(angle)
        # 必须全段不向球靠近；不能为了战术站位穿过保护圈。
        if not segment_clear(start, target, obstacles, allow_escape=True):
            continue
        if not segment_clear(target, target, obstacles):
            continue
        in_field = (abs(target[0]) <= context.field.length/2-NAV_BODY_RADIUS_M and
                    abs(target[1]) <= context.field.width/2-NAV_BODY_RADIUS_M)
        candidates.append((not in_field, math.dist(start, target), target))
    if not candidates:
        return StopIntent(), 'escape_blocked'
    # 优先场内最短可行出口；边线外已陷入圈内时允许临时向外撤离，
    # 避免硬性场界与禁止靠近球互相锁死。球门实体始终不可穿越。
    outside, _, target = min(candidates)
    return MoveIntent(target, face=pose.theta, avoid_ball=True, avoid_robots=True,
                      arrive_dist=ESCAPE_ARRIVE_M, maintain_face=True, direct_path=True,
                      motion_priority=ESCAPE_PRIORITY, ball_keepout=keepout), (
                          'escape_outside' if outside else 'escape')
