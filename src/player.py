"""Player:单个机器人的控制 handle —— 用户可直接编辑这个类。

平台原语(set_velocity / kick / release_kick / request_mode / get_up)委托给注入的
``_backend``(framework 层);状态 property(pose / mode / is_fallen / penalty)从
``self.context`` 或 backend 读。

走位行为(walk_to / face_to / ensure_ready)也是 Player 方法:它们本质是"对本
球员下命令的动词",且未来加迟滞/避障需要的跨帧状态可直接挂 ``self``。纯坐标计算
(dist / angle_to / 球门坐标等)才放 utils/geom。

实例跨整场比赛存活;``self.context`` 每帧被框架覆写。用户想加自己的拐棍方法直接
在这里加。
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from .framework.types import Context, GameState, Penalty, Pose2D, SetPlay
from .framework import debugdraw

from .param import *
from .utils.geom import (
    angle_to,
    clamp,
    closest_point_on_ray_in_rect,
    dist,
    normalize_angle,
    opponent_goal,
    own_goal,
    own_goal_area_center,
)
from .utils.obstacles import collect_obstacles, goal_obstacles
from .utils.path_planner import navigation_target, plan_global_path, segment_is_clear

if TYPE_CHECKING:
    from .framework.config import SoccerConfig


__all__ = ["Player"]


_log = logging.getLogger(__name__)

def _heading_clearance(
    px: float, py: float, heading: float, obstacles: list,
) -> float:
    """用于局部避障。沿 (px,py) 朝 ``heading`` 的 lookahead 射线,到最近障碍的余量(dist - radius)。

    只考虑**前方**(投影 t>0)的障碍;身后 / 侧后的障碍不挡这个方向,跳过——否则贴近
    任何障碍(哪怕正后方)都会把所有方向的余量拉低,误判"全堵"。
    无障碍返回 inf;越大越空,负值表示会撞。
    """
    ux, uy = math.cos(heading), math.sin(heading)
    min_clear = math.inf
    for obs in obstacles:
        t = (obs.x - px) * ux + (obs.y - py) * uy
        if t <= 0.0:
            continue                      # 障碍在身后/侧后,不挡此方向
        if t > PLAN_LOOKAHEAD:
            t = PLAN_LOOKAHEAD
        nx, ny = px + ux * t, py + uy * t
        clear = math.hypot(obs.x - nx, obs.y - ny) - obs.radius
        if clear < min_clear:
            min_clear = clear
    return min_clear


def _behind_ball(
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


class Player:
    """单个球员的 handle。可以在这里加入新的技术动作。
    """

    def __init__(
        self,
        player_id: int,
        config: "SoccerConfig",
        _backend: object | None,
    ) -> None:
        self.id: int = player_id
        self.config: "SoccerConfig" = config
        self._backend = _backend           # 机器人控制接口的包装，通常不用改
        self.context: Context | None = None

        # 当前高层动作名(仅供可视化/调试)。策略分派层每帧写入;有子状态的动作
        # (如 guard)在方法内部细化。可视化 pass 读它标注文字,见 main.py。
        self.action: str = "init"

        # SDK 缓存字段,框架后台会自动更新
        self._mode: str | None = None
        self._fall_down_state: str | None = None

        # 避障绕行侧记忆(跨帧;None=当前无绕行)
        self._avoid_side: float | None = None

        # 踢球迟滞状态(跨帧)
        self._kicking: bool = False

        # block/guard 的跨帧迟滞状态
        self._block_pressing: bool = False
        self._guard_threatened: bool = False
        # 门线撞球进门迟滞状态
        self._goal_line_push: bool = False
        self._goal_escape_target: tuple[float, float] | None = None
        # support 卡住后临时转 attack 的跨帧状态
        self._support_last_pos: tuple[float, float] | None = None
        self._support_stationary_since: float | None = None
        self._support_last_update_at: float | None = None

    # ------------------------------------------------------------------
    # 状态读取
    # ------------------------------------------------------------------

    @property
    def is_kicking(self) -> bool:
        """当前是否处于踢球状态。"""
        return self._kicking

    @property
    def pose(self) -> Pose2D | None:
        ctx = self.context
        if ctx is None:
            return None
        robot = ctx.teammates.get(self.id)
        return None if robot is None else robot.pose

    @property
    def mode(self) -> str | None:
        if self._backend is not None:
            return self._backend.mode
        return self._mode

    @property
    def is_fallen(self) -> bool:
        return self.fall_down_state not in (None, "normal")

    @property
    def fall_down_state(self) -> str | None:
        if self._backend is not None:
            return getattr(self._backend, "fall_down_state", None)
        return self._fall_down_state

    @property
    def penalty(self) -> Penalty:
        ctx = self.context
        if ctx is None or ctx.game is None:
            return Penalty.NONE
        state = ctx.game.get_player_state(self.config.team_id, self.id)
        return Penalty.NONE if state is None else state.penalty

    @property
    def is_penalized(self) -> bool:
        return self.penalty != Penalty.NONE

    # ------------------------------------------------------------------
    # 底盘控制
    # ------------------------------------------------------------------

    def set_velocity(self, vx: float, vy: float, vyaw: float) -> None:
        if self._backend is None:
            _log.debug(
                "player %d set_velocity vx=%.3f vy=%.3f vyaw=%.3f (no backend)",
                self.id, vx, vy, vyaw,
            )
            return
        self._backend.set_velocity(vx, vy, vyaw)

    def stop(self) -> None:
        self.release_kick()
        self.set_velocity(0.0, 0.0, 0.0)

    # ------------------------------------------------------------------
    # 踢球
    # ------------------------------------------------------------------

    def kick(
        self,
        kick_direction: float | None = None,
        power: float = KICK_POWER_DEFAULT,
    ) -> None:
        pose = self.pose
        if pose is None:
            _log.warning("player %d kick skipped: pose unknown", self.id)
            return

        # SDK 踢球含自动接近动作；贴柱时先取消它并完整退离，再恢复踢球。
        if self.context is not None:
            structures = goal_obstacles(self.context)
            touching_goal = any(
                dist(pose.x, pose.y, obs.x, obs.y) <= obs.radius + GLOBAL_OBSTACLE_MARGIN_M
                for obs in structures
            )
            # 若期间被其他策略带离球门，丢弃旧退让目标，避免再跑回门柱附近。
            if self._goal_escape_target is not None and all(
                dist(pose.x, pose.y, obs.x, obs.y) > obs.radius + GLOBAL_OBSTACLE_MARGIN_M + 0.5
                for obs in structures
            ):
                self._goal_escape_target = None
            if touching_goal and self._goal_escape_target is None:
                inward = -1.0 if pose.x >= 0 else 1.0
                self._goal_escape_target = (pose.x + inward, pose.y)
            if self._goal_escape_target is not None:
                self.action = "goal:escape"
                if not self.walk_to(self._goal_escape_target, face=pose.theta,
                                    keep_facing=True, avoid_robots=True):
                    return
                self._goal_escape_target = None

        ball = self.context.ball if self.context is not None else None
        if ball is None:
            _log.warning("player %d kick skipped: ball unknown", self.id)
            return

        if kick_direction is None:
            kick_plan = self.plan_kick()
            if kick_plan is None:
                _log.warning("player %d kick skipped: kick plan unavailable", self.id)
                return
            kick_direction, power = kick_plan

        if self._backend is None:
            _log.debug(
                "player %d kick ball=(%.3f, %.3f) dir=%.3f (no backend)",
                self.id, ball.x, ball.y, kick_direction,
            )
            return
        # 场地坐标 → 体坐标(用当前 pose)
        dx = ball.x - pose.x
        dy = ball.y - pose.y
        cos_t = math.cos(pose.theta)
        sin_t = math.sin(pose.theta)
        ball_x_body = dx * cos_t + dy * sin_t
        ball_y_body = -dx * sin_t + dy * cos_t
        kick_direction = normalize_angle(kick_direction)
        # 只在最终控制接口补偿一次；战术目标、球坐标和力度保持不变。
        direction_body = normalize_angle(
            kick_direction - pose.theta - math.radians(KICK_LEFT_BIAS_DEG)
        )
        power_clamped = max(KICK_POWER_MIN, min(KICK_POWER_MAX, power))
        self._kicking = True
        self._backend.kick(direction_body, power_clamped, ball_x_body, ball_y_body)

    def shot_target(self) -> tuple[float, float] | None:
        """在门柱内侧采样，用球路净空选择射门角度，保留近似等价旧目标。"""
        ctx = self.context
        ball = ctx.ball if ctx is not None else None
        if ball is None or ctx is None:
            return None
        goal_x = ctx.field.length / 2
        if ball.x >= goal_x:
            return None
        half = max(0.0, ctx.field.goal_width / 2 - SHOT_POST_MARGIN_M)
        obstacles = [(r.pose, OPPONENT_RADIUS) for r in ctx.opponents.values()
                     if r.pose is not None]
        obstacles += [(r.pose, TEAMMATE_RADIUS) for pid, r in ctx.teammates.items()
                      if pid != self.id and r.pose is not None]

        def score(y):
            dx, dy = goal_x-ball.x, y-ball.y
            length2 = dx*dx + dy*dy
            clearance = SHOT_CLEARANCE_CAP_M
            for pose, radius in obstacles:
                # 仅考虑从球到门线前方的障碍，不受身后或门后球员干扰。
                if not ball.x < pose.x <= goal_x:
                    continue
                t = max(0.0, min(1.0, ((pose.x-ball.x)*dx + (pose.y-ball.y)*dy)/length2))
                gap = math.hypot(pose.x-ball.x-t*dx, pose.y-ball.y-t*dy) - radius
                clearance = min(clearance, gap)
            return clearance - SHOT_CENTER_WEIGHT * abs(y)

        targets = [-half + 2*half*i/(SHOT_SAMPLE_COUNT-1) for i in range(SHOT_SAMPLE_COUNT)]
        best = max(targets, key=lambda y: (score(y), -abs(y)))
        previous = getattr(self, "_shot_target_y", None)
        if previous is not None and abs(previous) <= half:
            if score(previous) >= score(best) - SHOT_KEEP_MARGIN_M:
                best = previous
        self._shot_target_y = best
        return goal_x, best

    def plan_kick(self, target: tuple[float, float] | None = None) -> tuple[float, float] | None:
        """默认按球门球路净空选角度；显式传入目标时严格朝该点踢。"""
        ctx = self.context
        ball = ctx.ball if ctx is not None else None
        if ctx is None or ball is None:
            return None
        kick_target = target if target is not None else self.shot_target()
        if kick_target is None:
            return None
        kick_direction = angle_to(ball.x, ball.y, *kick_target)
        kick_power = KICK_POWER_BACKFIELD if self._in_backfield() else KICK_POWER_DEFAULT
        self._draw_kick_target(kick_target)
        return kick_direction, kick_power

    def _goal_target_for_direction(
        self, kick_direction: float,
    ) -> tuple[float, float]:
        """把射门方向投到对方门线上,用于可视化踢球目标。"""
        ctx = self.context
        ball = ctx.ball if ctx is not None else None
        if ctx is None or ball is None:
            return (0.0, 0.0)

        dx = math.cos(kick_direction)
        if dx <= 1e-6:
            return opponent_goal(ctx)
        goal_x = ctx.field.length / 2.0
        t = max(0.0, (goal_x - ball.x) / dx)
        return (goal_x, ball.y + math.sin(kick_direction) * t)

    def _in_backfield(self) -> bool:
        """球在我方后场时,默认踢球加大力度。"""
        ctx = self.context
        ball = ctx.ball if ctx is not None else None
        if ctx is None or ball is None:
            return False
        # own_penalty_edge_x = -ctx.field.length / 2.0 + ctx.field.penalty_area_length
        # return ball.x < own_penalty_edge_x
        return ball.x < 0

    def _draw_kick_target(self, target: tuple[float, float]) -> None:
        """以 X 标出 plan_kick 选择的踢球目标。"""
        from .framework import debugdraw

        x, y = target
        s = KICK_TARGET_MARK_SIZE_M
        debugdraw.line(
            [(x - s, y - s), (x + s, y + s)],
            rgb=(1.0, 0.0, 1.0), ns="kick_target",
        )
        debugdraw.line(
            [(x - s, y + s), (x + s, y - s)],
            rgb=(1.0, 0.0, 1.0), ns="kick_target",
        )

    def release_kick(self) -> None:
        self._kicking = False  # 清除踢球迟滞标志(取消方块显示)
        if self._backend is None:
            _log.debug("player %d release_kick (no backend)", self.id)
            return
        self._backend.release_kick()

    def kick_can_score(self, kick_direction: float) -> bool:
        """判断从当前球位按 ``kick_direction`` 踢,直线轨迹是否能进对方球门。
        """
        ctx = self.context
        ball = ctx.ball if ctx is not None else None
        if ctx is None or ball is None:
            return False

        goal_x = ctx.field.length / 2.0
        dx = math.cos(kick_direction)
        dy = math.sin(kick_direction)
        if dx <= 1e-6:
            return False

        half_goal = ctx.field.goal_width / 2.0
        if half_goal <= 0.0:
            return False
        if ball.x >= goal_x:
            y_at_goal = ball.y
        else:
            t = (goal_x - ball.x) / dx
            if t < 0.0:
                return False
            y_at_goal = ball.y + dy * t
        return -half_goal <= y_at_goal <= half_goal

    # ------------------------------------------------------------------
    # 慢操作（同步接口异步调取，使得不阻塞）
    # ------------------------------------------------------------------

    def request_mode(self, mode: str) -> None:
        if self._backend is None:
            _log.debug("player %d request_mode -> %s (no backend)", self.id, mode)
            return
        self._backend.request_mode(mode)

    def get_up(self) -> None:
        if self._backend is None:
            _log.debug("player %d get_up (no backend)", self.id)
            return
        self._backend.get_up()

    # ------------------------------------------------------------------
    # 走位
    # ------------------------------------------------------------------

    def ensure_ready(self) -> bool:
        """活性自理:摔倒→起身,非 walk→切模式(都异步、不产生移动)。

        返回本帧是否可执行动作(True=已就绪)。
        """
        if self.is_fallen:
            self.get_up()
            return False
        if self.mode != "walk":
            self.request_mode("walk")
            return False
        return True

    def face_to(self, target_theta: float) -> None:
        """原地转向到目标朝向。"""
        if self.pose is None:
            self.stop()
            return
        err = normalize_angle(target_theta - self.pose.theta)
        self.set_velocity(0.0, 0.0, self._angular(err))

    def walk_to(
        self,
        target: tuple[float, float],
        *,
        face: float | None = None,
        avoid_ball: bool = False,
        avoid_robots: bool = False,
        arrive_dist: float = ARRIVE_DIST,
        keep_facing: bool = False,
    ) -> bool:
        """走向目标点。返回是否已到达。

        球门结构始终避让；``avoid_ball`` / ``avoid_robots`` 控制其他障碍。
        优先全局 A*，无路时仅采用经过碰撞检查的局部方向，否则停车。
        已处于障碍安全圈内时，只允许向外脱离。

        行走两种模式:近距离全向,远距离转身-走。``face`` 指定到达/近距离时的朝向。
        ``keep_facing`` 启用全程全向移动,持续朝向 ``face``。
        """
        self.release_kick() # 踢球状态下，走位会被覆盖，先取消踢球状态
        pose = self.pose
        if pose is None:
            self.stop()
            return False

        obstacles = []
        escaping_goal = False
        if self.context is not None:
            obstacles = collect_obstacles(
                self.context, self.id, ball=avoid_ball, robots=avoid_robots, goals=True,
                teammates=not getattr(self, "_team_motion_active", False),
            )
            self._motion_obstacles = obstacles
            escaping_goal = any(
                dist(pose.x, pose.y, obs.x, obs.y) <= obs.radius + GLOBAL_OBSTACLE_MARGIN_M
                for obs in goal_obstacles(self.context)
            )
            safe_target = navigation_target(self.context, target, obstacles)
            if safe_target is None:
                self.stop()
                return False
            target = safe_target
        tx, ty = target
        dx = tx - pose.x
        dy = ty - pose.y
        distance = math.hypot(dx, dy)

        if distance < arrive_dist:
            # 已到达:按需转到目标朝向
            if face is not None:
                err = normalize_angle(face - pose.theta)
                if abs(err) > 0.1:
                    self.set_velocity(0.0, 0.0, self._angular(err))
                else:
                    self.stop()
            else:
                self.stop()
            return True

        # 规划:默认全局 A*,找不到路时退回旧局部规划。
        goal_dir = math.atan2(dy, dx)
        planned_path: list[tuple[float, float]] | None = None
        waypoint: tuple[float, float] | None = None
        if self.context is not None:
            if USE_GLOBAL_PATH_PLANNER:
                planned_path = plan_global_path(
                    self.context,
                    (pose.x, pose.y),
                    (tx, ty),
                    obstacles,
                )
                if planned_path is not None:
                    waypoint = self._path_waypoint(pose, planned_path)
                    heading = angle_to(pose.x, pose.y, waypoint[0], waypoint[1])
                else:
                    heading = self._plan_heading(pose, goal_dir, obstacles)
            else:
                heading = self._plan_heading(pose, goal_dir, obstacles)
        else:
            heading = goal_dir

        if heading is None:
            self.stop()
            return False
        move_distance = distance if waypoint is None else dist(pose.x, pose.y, *waypoint)
        recovering_goal = escaping_goal or self._goal_escape_target is not None
        speed_limit = GOAL_ESCAPE_SPEED_M_S if recovering_goal else MAX_LINEAR

        # 可视化:目标点(绿)、到目标连线(灰)、规划朝向(黄箭头)、
        # 前方探测射线(青,长度=lookahead;规划器"看"的范围,无 path/途径点概念)
        from .framework import debugdraw
        debugdraw.point(tx, ty, rgb=(0.0, 1.0, 0.0), scale=0.15, ns="target")
        debugdraw.line([(pose.x, pose.y), (tx, ty)], rgb=(0.4, 0.4, 0.4), ns="to_target")
        if planned_path is not None and len(planned_path) >= 2:
            debugdraw.line(planned_path, rgb=(0.2, 0.8, 1.0), ns="global_path")
        if waypoint is not None:
            debugdraw.point(
                waypoint[0], waypoint[1],
                rgb=(0.2, 0.8, 1.0), scale=0.12, ns="global_waypoint",
            )
        debugdraw.arrow(
            pose.x, pose.y,
            pose.x + math.cos(heading) * 0.6, pose.y + math.sin(heading) * 0.6,
            rgb=(1.0, 1.0, 0.0), ns="heading",
        )
        debugdraw.line(
            [(pose.x, pose.y),
             (pose.x + math.cos(heading) * PLAN_LOOKAHEAD,
              pose.y + math.sin(heading) * PLAN_LOOKAHEAD)],
            rgb=(0.0, 0.8, 0.8), ns="lookahead",
        )

        if keep_facing or distance <= OMNI_DIST or recovering_goal:
            # 近距离或保持朝向:全向行走,沿 heading 平移,同时转向 face
            wdx, wdy = math.cos(heading) * move_distance, math.sin(heading) * move_distance
            cos_t, sin_t = math.cos(pose.theta), math.sin(pose.theta)
            vx = LINEAR_GAIN * (wdx * cos_t + wdy * sin_t)
            vy = LINEAR_GAIN * (-wdx * sin_t + wdy * cos_t)
            speed = math.hypot(vx, vy)
            if speed > speed_limit:
                vx *= speed_limit / speed
                vy *= speed_limit / speed
            vyaw = (
                self._angular(normalize_angle(face - pose.theta))
                if face is not None else 0.0
            )
            self.set_velocity(vx, vy, vyaw)
        else:
            # 远距离:转身-走-转身,朝 heading
            angle_err = normalize_angle(heading - pose.theta)
            # Heading must align before forward-only movement near a planned corner.
            if abs(angle_err) > min(TURN_THRESHOLD, 0.1):
                self.set_velocity(0.0, 0.0, self._angular(angle_err))
            else:
                vx = clamp(
                    LINEAR_GAIN * move_distance * math.cos(angle_err), 0.0, speed_limit,
                )
                self.set_velocity(vx, 0.0, self._angular(angle_err))
        return False

    def _path_waypoint(
        self, pose: Pose2D, path: list[tuple[float, float]],
    ) -> tuple[float, float]:
        """Follow the first segment; never skip an unvalidated corner."""
        if not path:
            return (pose.x, pose.y)
        prev = (pose.x, pose.y)
        points = path[1:] if len(path) > 1 else path
        for point in points:
            seg_len = dist(prev[0], prev[1], point[0], point[1])
            if seg_len > 1e-9:
                ratio = min(1.0, GLOBAL_PATH_LOOKAHEAD_M / seg_len)
                return (
                    prev[0] + (point[0] - prev[0]) * ratio,
                    prev[1] + (point[1] - prev[1]) * ratio,
                )
            prev = point
        return path[-1]

    def _plan_heading(
        self, pose: Pose2D, goal_dir: float, obstacles: list,
    ) -> float | None:
        """扫安全方向；无法安全移动时返回 None。

        候选按偏离目标方向 ``|offset|`` 从小到大试;先试哪一侧由 player_id 奇偶决定,
        用来打破两人同侧避让的对称。
        """
        sign_first = 1.0 if self.id % 2 == 0 else -1.0
        escaping = any(dist(pose.x, pose.y, o.x, o.y) <= o.radius + GLOBAL_OBSTACLE_MARGIN_M
                       for o in obstacles)

        offsets = [0.0]
        k = 1
        max_offset = math.pi if escaping else PLAN_MAX_OFFSET
        while k * PLAN_STEP <= max_offset + 1e-9:
            offsets.append(sign_first * k * PLAN_STEP)
            offsets.append(-sign_first * k * PLAN_STEP)
            k += 1

        for off in offsets:
            h = goal_dir + off
            end = (pose.x + math.cos(h) * PLAN_LOOKAHEAD,
                   pose.y + math.sin(h) * PLAN_LOOKAHEAD)
            if self.context is not None:
                half_l = self.context.field.length / 2 - GLOBAL_FIELD_MARGIN_M
                half_w = self.context.field.width / 2 - GLOBAL_FIELD_MARGIN_M
                outside_start = (max(0.0, abs(pose.x) - half_l) ** 2
                                 + max(0.0, abs(pose.y) - half_w) ** 2)
                outside_end = (max(0.0, abs(end[0]) - half_l) ** 2
                               + max(0.0, abs(end[1]) - half_w) ** 2)
                # 已在门线外时允许沿网侧退离，不能进一步远离场内。
                if outside_end > outside_start + 1e-12:
                    continue
            if not segment_is_clear((pose.x, pose.y), end, obstacles, allow_escape=True):
                continue
            clear = _heading_clearance(pose.x, pose.y, h, obstacles)
            if escaping or clear >= PLAN_CLEARANCE:
                return h
        return None

    # ------------------------------------------------------------------
    # 高层动作(策略在 main.py 里直接调这些)
    # ------------------------------------------------------------------


    def block_path_projection(
        self, opponent_id: int,
    ) -> tuple[float, float, float, float] | None:
        """自己到 对手→球 线段的垂足:返回 (x, y, 垂距, 线段参数 t)。"""
        ctx = self.context
        pose = self.pose
        ball = ctx.ball if ctx is not None else None
        opponent = ctx.opponents.get(opponent_id) if ctx is not None else None
        if ctx is None or pose is None or ball is None or opponent is None:
            return None
        if opponent.pose is None:
            return None

        ax, ay = opponent.pose.x, opponent.pose.y
        bx, by = ball.x, ball.y
        vx, vy = bx - ax, by - ay
        length2 = vx * vx + vy * vy
        if length2 < 1e-6:
            return None

        raw_t = ((pose.x - ax) * vx + (pose.y - ay) * vy) / length2
        t = clamp(raw_t, 0.0, 1.0)
        tx = ax + vx * t
        ty = ay + vy * t
        return tx, ty, dist(pose.x, pose.y, tx, ty), raw_t

    def attack(self, kick_target: tuple[float, float] | None = None) -> None:
        """追球射门。踢向 ``kick_target``，默认按对手站位选择球门内的空当。

        踢球迟滞:距球 ≤ ENTER 进入踢球;进入后距球 > EXIT 才退出(EXIT > ENTER),
        防边界抖动。不避障——正常拼抢要直取球。追球目标设在【球后方】(球→球门连线
        上、背离球门退 ``CHASE_BEHIND_M``),到位时天然对准射门方向。
        """
        ball = self.context.ball if self.context is not None else None
        if ball is None or self.pose is None:
            self.stop()
            return
        if kick_target is None:
            kick_target = self.shot_target()
        if kick_target is None:
            self.stop()
            return

        d = dist(self.pose.x, self.pose.y, ball.x, ball.y)
        self._kicking = d <= (KICK_EXIT_M if self._kicking else KICK_ENTER_M)
        if self._kicking:
            kick_plan = self.plan_kick(kick_target)
            if kick_plan is None:
                self.stop()
                return
            kick_direction, kick_power = kick_plan
            self.kick(kick_direction, kick_power)
        else:
            self.release_kick()
            self.walk_to(
                _behind_ball(ball.x, ball.y, kick_target, CHASE_BEHIND_M)
            )

    def pass_to(self, target: tuple[float, float], power: float) -> bool:
        """追到球后向指定场地坐标出球；返回是否已提交踢球指令。"""
        ball = self.context.ball if self.context is not None else None
        if ball is None or self.pose is None:
            self.stop()
            return False
        direction = angle_to(ball.x, ball.y, *target)
        self._draw_kick_target(target)
        if dist(self.pose.x, self.pose.y, ball.x, ball.y) <= KICK_ENTER_M:
            self.kick(direction, power)
            return self.is_kicking
        self.walk_to(_behind_ball(ball.x, ball.y, target, CHASE_BEHIND_M),
                     face=direction, avoid_ball=True, avoid_robots=True)
        return False

    def _guard_intercept_target(self) -> tuple[float, float] | None:
        """选最近球的威胁对手,求其射门射线在己方禁区内的最短拦截点。"""
        ctx, pose = self.context, self.pose
        if ctx is None or pose is None or ctx.ball is None:
            return None
        ball = ctx.ball
        goal_x = -ctx.field.length / 2.0
        half_width = ctx.field.penalty_area_width / 2.0
        bounds = (
            goal_x, goal_x + ctx.field.penalty_area_length,
            -half_width, half_width,
        )
        candidates = []
        for opponent in ctx.opponents.values():
            opp = opponent.pose
            if opp is None:
                continue
            dx, dy = ball.x - opp.x, ball.y - opp.y
            ball_dist = math.hypot(dx, dy)
            if not 1e-6 < ball_dist < GUARD_OPPONENT_BALL_DIST_M:
                continue
            # 球在对手前方半平面(左右各 90°),包含正侧方边界。
            if dx * math.cos(opp.theta) + dy * math.sin(opp.theta) < -1e-12:
                continue
            target = closest_point_on_ray_in_rect(
                (ball.x, ball.y), (dx, dy), (pose.x, pose.y), bounds,
            )
            if target is not None:
                candidates.append((ball_dist, opponent.player_id, target))
        return min(candidates)[2] if candidates else None

    def _guard_distribution(self, receivers: list[Player]) -> tuple[tuple[float, float], float, str]:
        """只向已就绪队友传球；按慢动作期间对手可达范围扩大整条路线。"""
        ctx, pose, ball = self.context, self.pose, self.context.ball

        def fresh(robot):
            return (robot.pose is not None
                    and 0 <= ctx.now - robot.last_seen_at <= GUARD_OBSERVATION_MAX_AGE_S)

        def lane_distance(robot, target):
            dx, dy = target[0] - ball.x, target[1] - ball.y
            t = clamp(((robot.pose.x-ball.x)*dx + (robot.pose.y-ball.y)*dy)
                      / (dx*dx + dy*dy), 0.0, 1.0)
            return dist(robot.pose.x, robot.pose.y, ball.x+t*dx, ball.y+t*dy)

        # 缺失/陈旧观测不能作为“无人逼抢”的证据。
        observations_ok = (bool(ctx.opponents)
                           and all(fresh(r) for r in ctx.opponents.values())
                           and 0 <= ctx.now-ball.last_seen_at <= GUARD_OBSERVATION_MAX_AGE_S
                           and ball.confidence >= 0.9)
        choices = []
        if observations_ok:
            for receiver in receivers:
                robot = ctx.teammates.get(receiver.id)
                if (receiver.id == self.id or receiver.is_fallen or receiver.is_penalized
                        or receiver.mode != "walk" or robot is None or not fresh(robot)):
                    continue
                target = (robot.pose.x, robot.pose.y)
                length = dist(ball.x, ball.y, *target)
                direction = angle_to(ball.x, ball.y, *target)
                if (not GUARD_PASS_MIN_M <= length <= GUARD_PASS_MAX_M
                        or target[0]-ball.x < GUARD_PASS_FORWARD_M
                        or abs(target[0]) > ctx.field.length/2-0.65
                        or abs(target[1]) > ctx.field.width/2-0.65
                        or abs(normalize_angle(direction-pose.theta)) > GUARD_PASS_MAX_TURN):
                    continue
                danger = GUARD_PASS_LANE_M + GUARD_OPPONENT_SPEED * (
                    GUARD_PASS_PREPARE_S + length/GUARD_PASS_BALL_SPEED)
                if any(lane_distance(r, target) <= danger for r in ctx.opponents.values()):
                    continue
                blockers = [r for pid, r in ctx.teammates.items()
                            if pid not in (self.id, receiver.id)]
                if any(not fresh(r) or lane_distance(r, target) <= GUARD_PASS_LANE_M
                       for r in blockers):
                    continue
                choices.append((length, receiver.id, target))
        if choices:
            return min(choices)[2], GUARD_PASS_POWER, "guard:pass"

        # 只选向前的解围方向；兼顾前方空间和转身耗时，绝不向己方门踢。
        obstacles = [r for r in ctx.opponents.values() if r.pose is not None]
        obstacles += [r for pid, r in ctx.teammates.items()
                      if pid != self.id and r.pose is not None]
        options = []
        for degrees in (0, -30, 30, -60, 60):
            heading = math.radians(degrees)
            target = (ball.x+5*math.cos(heading), ball.y+5*math.sin(heading))
            space = min((lane_distance(r, target) for r in obstacles), default=5.0)
            turn = abs(normalize_angle(heading-pose.theta))
            options.append((min(space, 3.0)-0.5*turn, -abs(degrees), target))
        return max(options)[2], GUARD_CLEAR_POWER, "guard:clear"

    def distribute_goal_area_ball(self, receivers: list[Player]) -> bool:
        """己方球门区内任何球员，近身球都采用保守出球，返回是否接管动作。"""
        ctx, pose = self.context, self.pose
        if ctx is None or pose is None:
            return False
        goal_x = -ctx.field.length / 2
        if not (goal_x <= pose.x <= goal_x + ctx.field.goal_area_length
                and abs(pose.y) <= ctx.field.goal_area_width / 2):
            return False
        return self._distribute_near_ball(receivers, "goal_area")

    def _distribute_near_ball(self, receivers: list[Player], label: str) -> bool:
        ctx, pose = self.context, self.pose
        if ctx is None or pose is None or ctx.ball is None:
            return False
        game = ctx.game
        if game is not None and (
                game.state != GameState.PLAYING or game.stopped
                or game.set_play != SetPlay.NONE or game.secondary_time > 0):
            self.release_kick()
            return False
        ball = ctx.ball
        radius = GUARD_BALL_EXIT_M if self._kicking else GUARD_BALL_ENTER_M
        if dist(pose.x, pose.y, ball.x, ball.y) > radius:
            return False
        target, power, action = self._guard_distribution(receivers)
        self.action = label + ":" + action.split(":")[-1]
        self._draw_kick_target(target)
        self.kick(angle_to(ball.x, ball.y, *target), power)
        return True

    def guard(self, *, allow_distribution: bool = True,
              receivers: list[Player] | None = None) -> None:
        """近身球优先解围，严格安全才传球；其余时间拦截或回位。"""
        home = own_goal_area_center(self.context) if self.context is not None else None
        if home is None or self.pose is None:
            self.action = "guard:stop"
            self.stop()
            return

        ball = self.context.ball if self.context is not None else None

        # 防止调用者忘记禁用出球：任何裁判定位球/开球窗口都不能解围。
        game = self.context.game
        if game is not None and (
                game.state != GameState.PLAYING or game.stopped
                or game.set_play != SetPlay.NONE or game.secondary_time > 0):
            allow_distribution = False
        if not allow_distribution:
            self.release_kick()

        if allow_distribution and self._distribute_near_ball(receivers or [], "guard"):
            return

        intercept = self._guard_intercept_target()
        if intercept is not None:
            self.action = "guard:intercept"
            self.walk_to(
                intercept,
                face=angle_to(self.pose.x, self.pose.y, ball.x, ball.y),
                avoid_ball=True, avoid_robots=True, keep_facing=True,
            )
            return

        # 待命朝向:朝球(便于快速反应);球不可见则朝对方门方向(默认 0)。
        face = 0.0
        if GUARD_FACE_BALL and ball is not None:
            face = angle_to(
                self.pose.x, self.pose.y, ball.x, ball.y,
            )

        self.action = "guard:home"
        debugdraw.point(
            home[0], home[1], rgb=(0.0, 0.6, 1.0), scale=0.2, ns="guard_home",
        )
        self.walk_to(home, face=face, avoid_ball=True, avoid_robots=True)

    def support(self) -> None:
        """支援:站在 球→己方门中心 连线上距球 ``SUPPORT_DIST_M`` 处补防。

        站位点在球与己方门之间的封堵线上。
        """
        ctx = self.context
        if ctx is None:
            self.stop()
            return

        ball = self.context.ball
        gx, gy = own_goal(ctx)
        bx, by = (ball.x, ball.y)
        dx, dy = gx - bx, gy - by
        d = math.hypot(dx, dy)
        if d < 1e-6:
            ux, uy = -1.0, 0.0
        else:
            ux, uy = dx / d, dy / d
        along = min(SUPPORT_DIST_M, d)
        tx = bx + ux * along
        ty = by + uy * along

        # 不越过己方底线(x >= 门线 + 0.3),横向夹进场内
        half_l = ctx.field.length / 2.0
        half_w = ctx.field.width / 2.0 - 0.3
        tx = clamp(tx, -half_l + 0.3, half_l)
        ty = clamp(ty, -half_w, half_w)
        self.move_to_position((tx, ty))

    def wait_for_opponent_restart(self, clearance: float) -> None:
        """对手开球前取消踢球,在球周围让出 clearance 米并面朝球等待。"""
        self.release_kick()
        ctx, pose = self.context, self.pose
        if ctx is None or pose is None or ctx.ball is None:
            self.action = "opp_restart:stop"
            self.stop()
            return
        ball = ctx.ball
        dx, dy = pose.x - ball.x, pose.y - ball.y
        distance = math.hypot(dx, dy)
        face = angle_to(pose.x, pose.y, ball.x, ball.y)
        if distance >= clearance:
            self.action = "opp_restart:wait"
            self.face_to(face)
            return

        # 留出到达误差,避免 walk_to 在安全距离以内提前停下。
        radius = clearance + ARRIVE_DIST + 0.05
        candidates = []
        if distance > 1e-6:
            candidates.append((
                ball.x + dx / distance * radius,
                ball.y + dy / distance * radius,
            ))
        # 贴边/角球时径向退点可能在场外,补充水平/垂直退点。
        # 只选择整段直行不会更靠近球、且终点仍在场内的方向。
        if abs(dy) <= radius:
            offset = math.sqrt(max(0.0, radius * radius - dy * dy))
            candidates.extend((ball.x + sign * offset, pose.y) for sign in (-1, 1))
        if abs(dx) <= radius:
            offset = math.sqrt(max(0.0, radius * radius - dx * dx))
            candidates.extend((pose.x, ball.y + sign * offset) for sign in (-1, 1))
        half_l, half_w = ctx.field.length / 2, ctx.field.width / 2
        candidates = [
            (x, y) for x, y in candidates
            if -half_l <= x <= half_l and -half_w <= y <= half_w
            and (x - pose.x) * dx + (y - pose.y) * dy >= -1e-12
        ]
        if not candidates:
            self.action = "opp_restart:wait"
            self.face_to(face)
            return
        target = min(candidates, key=lambda p: dist(pose.x, pose.y, *p))
        self.action = "opp_restart:retreat"
        # 直接向外全向退让,避免路径规划在球附近绕行时反而靠近球。
        self.walk_to(target, face=face, keep_facing=True)

    def take_kickoff(self, kick_target: tuple[float, float] | None = None) -> None:
        """我方开球/重开:未就位则绕到球后待命(避球不碰),就位后接近并踢。"""
        ball = self.context.ball if self.context is not None else None
        if ball is None or self.pose is None:
            self.stop()
            return
        if kick_target is None:
            kick_target = opponent_goal(self.context)
        kick_dir = angle_to(ball.x, ball.y, *kick_target)
        cos_k, sin_k = math.cos(kick_dir), math.sin(kick_dir)
        rel_x, rel_y = self.pose.x - ball.x, self.pose.y - ball.y
        behind = rel_x * cos_k + rel_y * sin_k          # <0 在球后方(己方侧)
        lateral = abs(-rel_x * sin_k + rel_y * cos_k)
        if behind > KICKOFF_FRONT_MARGIN or lateral > KICKOFF_LATERAL_TOL:
            stage = (
                ball.x - cos_k * KICKOFF_STAGE_M,
                ball.y - sin_k * KICKOFF_STAGE_M,
            )
            self.release_kick()
            self.walk_to(stage, face=kick_dir, avoid_ball=True)
        else:
            self.attack(kick_target)

    def move_to_position(self, target: tuple[float, float] | None) -> None:
        """走到站位点(支援/防守/避让),面向球,避球+避机器人。"""
        if target is None:
            self.stop()
            return
        face = None
        ball = self.context.ball if self.context is not None else None
        if ball is not None and self.pose is not None:
            face = angle_to(self.pose.x, self.pose.y, ball.x, ball.y)
        self.release_kick()
        self.walk_to(target, face=face, avoid_ball=True, avoid_robots=True)

    @staticmethod
    def _angular(err: float) -> float:
        return clamp(ANGULAR_GAIN * err, -MAX_ANGULAR, MAX_ANGULAR)
