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
    dist,
    normalize_angle,
    opponent_goal,
    own_goal,
    own_goal_area_center,
)
from .utils.obstacles import collect_obstacles
from .utils.path_planner import plan_global_path
from .utils.free_position import nearest_free_position

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
        self.tactical_target: tuple[float, float] | None = None

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
        # 右脚出球向左偏，统一在下发前向右补偿；战术目标方向保持不变。
        direction_body = normalize_angle(
            kick_direction - pose.theta + math.radians(RIGHT_FOOT_KICK_CORRECTION_DEG)
        )
        power_clamped = max(KICK_POWER_MIN, min(KICK_POWER_MAX, power))
        self._kicking = True
        self._backend.kick(direction_body, power_clamped, ball_x_body, ball_y_body)

    def plan_kick(
        self, kick_target: tuple[float, float] | None = None,
    ) -> tuple[float, float] | None:
        """计算踢球方向和力度。

        从当前球位踢向指定目标，默认对方球门中心；力度沿用前后场配置。
        返回 ``(kick_direction, kick_power)``;球或上下文不可用时返回 None。
        """
        ctx = self.context
        ball = ctx.ball if ctx is not None else None
        if ctx is None or ball is None:
            return None

        default_target = kick_target is None
        if default_target:
            kick_target = opponent_goal(ctx)
        kick_direction = angle_to(ball.x, ball.y, *kick_target)
        if default_target:
            kick_target = self._goal_target_for_direction(kick_direction)
        kick_power = (
            KICK_POWER_BACKFIELD if self._in_backfield()
            else KICK_POWER_DEFAULT
        )

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
        maintain_face: bool = False,
    ) -> bool:
        """走向目标点。返回是否已到达。

        避障(局部规划器,简化版 VFH):``avoid_ball`` / ``avoid_robots`` 开启时,把
        球 / 机器人 / 球门结构收集成圆形障碍,在机器人周围扫一圈候选方向,选"最朝
        目标 + 前方 ``PLAN_LOOKAHEAD`` 内不撞障碍"的方向前进。能处理多障碍、凹形
        结构和对称冲突,比单障碍绕行 / 势场排斥稳。

        行走两种模式:近距离全向,远距离转身-走。``face`` 指定到达/近距离时的朝向。
        ``maintain_face`` 使远距离也采用全向移动，用于守门时持续面向球。
        """
        self.tactical_target = target
        self.release_kick() # 踢球状态下，走位会被覆盖，先取消踢球状态
        pose = self.pose
        if pose is None:
            self.stop()
            return False

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
        if (avoid_ball or avoid_robots) and self.context is not None:
            obstacles = collect_obstacles(
                self.context, self.id,
                ball=avoid_ball, robots=avoid_robots,
                goals=(avoid_ball or avoid_robots),
            )
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

        if distance <= OMNI_DIST or maintain_face:
            # 近距离:全向行走,沿 heading 平移,同时转向 face
            wdx, wdy = math.cos(heading) * distance, math.sin(heading) * distance
            cos_t, sin_t = math.cos(pose.theta), math.sin(pose.theta)
            vx = LINEAR_GAIN * (wdx * cos_t + wdy * sin_t)
            vy = LINEAR_GAIN * (-wdx * sin_t + wdy * cos_t)
            speed = math.hypot(vx, vy)
            if speed > MAX_LINEAR:
                vx *= MAX_LINEAR / speed
                vy *= MAX_LINEAR / speed
            vyaw = (
                self._angular(normalize_angle(face - pose.theta))
                if face is not None else 0.0
            )
            self.set_velocity(vx, vy, vyaw)
        else:
            # 远距离:转身-走-转身,朝 heading
            angle_err = normalize_angle(heading - pose.theta)
            if abs(angle_err) > TURN_THRESHOLD:
                self.set_velocity(0.0, 0.0, self._angular(angle_err))
            else:
                vx = clamp(
                    LINEAR_GAIN * distance * math.cos(angle_err), 0.0, MAX_LINEAR,
                )
                self.set_velocity(vx, 0.0, self._angular(angle_err))
        return False

    def _path_waypoint(
        self, pose: Pose2D, path: list[tuple[float, float]],
    ) -> tuple[float, float]:
        """Pick a short lookahead waypoint from a planned global path."""
        if not path:
            return (pose.x, pose.y)
        prev = (pose.x, pose.y)
        points = path[1:] if len(path) > 1 else path
        for point in points:
            seg_len = dist(prev[0], prev[1], point[0], point[1])
            if seg_len >= GLOBAL_PATH_LOOKAHEAD_M:
                ratio = GLOBAL_PATH_LOOKAHEAD_M / max(seg_len, 1e-6)
                return (
                    prev[0] + (point[0] - prev[0]) * ratio,
                    prev[1] + (point[1] - prev[1]) * ratio,
                )
            prev = point
        return path[-1]

    def _plan_heading(
        self, pose: Pose2D, goal_dir: float, obstacles: list,
    ) -> float:
        """扫候选方向,选最朝目标且前方够空的方向;都不够空时返回最空的那个。

        候选按偏离目标方向 ``|offset|`` 从小到大试;先试哪一侧由 player_id 奇偶决定,
        用来打破两人同侧避让的对称。
        """
        sign_first = 1.0 if self.id % 2 == 0 else -1.0
        best_h = goal_dir
        best_clear = -math.inf

        offsets = [0.0]
        k = 1
        while k * PLAN_STEP <= PLAN_MAX_OFFSET + 1e-9:
            offsets.append(sign_first * k * PLAN_STEP)
            offsets.append(-sign_first * k * PLAN_STEP)
            k += 1

        for off in offsets:
            h = goal_dir + off
            clear = _heading_clearance(pose.x, pose.y, h, obstacles)
            if clear >= PLAN_CLEARANCE:
                return h
            if clear > best_clear:
                best_clear, best_h = clear, h
        return best_h

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

    def attack(self, kick_target: int | tuple[float, float] = 0) -> None:
        """追球并踢球：0 为敌方球门，1–3 为对应编号队友的当前位置。

        保留坐标目标兼容原有开球调用。队友位置未知时停止。
        踢球迟滞:距球 ≤ ENTER 进入踢球;进入后距球 > EXIT 才退出(EXIT > ENTER),
        防边界抖动。不避障——正常拼抢要直取球。追球目标设在【球后方】(球→目标连线
        上、背离目标退 ``CHASE_BEHIND_M``),到位时天然对准踢球方向。
        """
        # 获取当前球的位置，如果球或机器人位置未知，则停止动作并返回
        ball = self.context.ball if self.context is not None else None
        if ball is None or self.pose is None:
            self.stop()
            return

        if kick_target == 0:
            target = opponent_goal(self.context)
        else:
            teammate = self.context.teammates.get(kick_target)
            if teammate is None or teammate.pose is None:
                self.stop()
                return
            target = (teammate.pose.x, teammate.pose.y)

        # 计算机器人与球的距离，并判断是否进入踢球状态
        d = dist(self.pose.x, self.pose.y, ball.x, ball.y)
        self._kicking = d <= (KICK_EXIT_M if self._kicking else KICK_ENTER_M)

        if self._kicking:
            # 如果处于踢球状态，规划踢球方向和力度
            kick_plan = self.plan_kick(target)
            if kick_plan is None:
                # 如果规划失败，停止动作并返回
                self.stop()
                return
            kick_direction, kick_power = kick_plan
            # 执行踢球动作
            self.kick(kick_direction, kick_power)
        else:
            # 如果未进入踢球状态，释放踢球状态并走向球的后方
            self.release_kick()
            self.walk_to(
                _behind_ball(ball.x, ball.y, target, CHASE_BEHIND_M)
            )


    def _guard_pass_target(self, teammate_id: int | None) -> tuple[float, float] | None:
        """选择可向前短传的指定队友；通道或接球点被占据时放弃。"""
        ctx = self.context
        if ctx is None or ctx.ball is None or teammate_id is None or teammate_id == self.id:
            return None
        receiver = ctx.teammates.get(teammate_id)
        if receiver is None or receiver.pose is None:
            return None
        if ctx.game is not None:
            state = ctx.game.get_player_state(ctx.team_id, teammate_id)
            if state is not None and state.penalty != Penalty.NONE:
                return None
        ball, pose = ctx.ball, receiver.pose
        dx, dy = pose.x - ball.x, pose.y - ball.y
        length2 = dx * dx + dy * dy
        if (
            dx < GUARD_PASS_MIN_FORWARD_M
            or length2 > GUARD_PASS_MAX_DIST_M ** 2
            or abs(pose.x) >= ctx.field.length / 2.0
            or abs(pose.y) >= ctx.field.width / 2.0
        ):
            return None

        # 点到球→队友线段的距离，同时覆盖传球线路和接球点附近的防守者。
        blockers = [(r, OPPONENT_RADIUS) for r in ctx.opponents.values()]
        blockers.extend(
            (r, TEAMMATE_RADIUS) for pid, r in ctx.teammates.items()
            if pid not in (self.id, teammate_id)
        )
        for robot, radius in blockers:
            if robot.pose is None:
                continue
            rx, ry = robot.pose.x - ball.x, robot.pose.y - ball.y
            t = clamp((rx * dx + ry * dy) / length2, 0.0, 1.0)
            if math.hypot(rx - t * dx, ry - t * dy) <= radius + GUARD_PASS_LANE_MARGIN_M:
                return None
        return pose.x, pose.y

    def guard(self, pass_to: int | None = None) -> None:
        """每帧执行一次守门决策：门前出球优先，其次封堵，最后回中待命。

        参数：
            pass_to: 希望优先传给的队友编号，例如 guard(3) 表示寻找传给
                3 号的机会；None 表示不考虑传球，门前球直接向前场解围。
                传入编号并不强制传球，仍须通过 _guard_pass_target 的检查。

        坐标约定：
            原点为球场中心，+x 朝对方球门，-x 朝己方球门，y 为横向坐标。
            默认 14 米场地中，己方门线 x=-7，小禁区中央 home=(-6.5, 0)。
            球 x<=-1 表示进入己方半场至少 1 米，并非距己方门线 1 米。

        执行顺序：
            1. 缺少自身定位时停止；没有球观测时回小禁区中央。
            2. 根据球的位置及跨帧迟滞状态，计算横向封堵目标。
            3. 比赛允许主动触球且球在门前近处时，优先接近并传球/解围；
               此分支发出指令后立即返回，避免被后面的回位指令覆盖。
            4. 不需要出球时，执行先前算好的封堵或回中移动。

        本方法只根据当前球位计算几何封堵线，不预测球速或射门落点。
        action 用于显示当前动作；_guard_threatened 和 _kicking 跨帧保留。
        """
        # 1. 安全入口：没有上下文或自身位置就无法计算移动和出球方向。
        # stop() 同时取消踢球并下发零速度，避免继续执行上一帧的命令。
        ctx, pose = self.context, self.pose
        if ctx is None or pose is None:
            self._guard_threatened = False
            self.action = "guard:stop"
            self.stop()
            return

        # 默认先选择小禁区中央；下面只在需要封堵或出球时覆盖 target。
        home = own_goal_area_center(ctx)
        ball = ctx.ball
        goal_x, _ = own_goal(ctx)
        target = home
        can_clear = False
        self.action = "guard:home"
        if ball is None:
            # 丢球后清除威胁记忆；本帧仍会走到末尾执行回中、面向前场。
            self._guard_threatened = False
        else:
            # 2. 威胁状态采用两个阈值，避免球在边界附近时反复回中/封堵：
            # 未进入封堵：球 x<=-1.0 才进入；已在封堵：直到 x>-0.7 才退出。
            # 这只是球所处区域的判断，不表示已检测到对方射门。
            threshold = (
                GUARD_THREAT_EXIT_X if self._guard_threatened
                else GUARD_THREAT_ENTER_X
            )
            self._guard_threatened = ball.x <= threshold
            if self._guard_threatened:
                # 固定纵向站位 x=home.x，只调整横向 y。
                # 球在站位线前方时，利用相似三角形求“球→己方门中心”
                # 与 x=home.x 的交点：
                # target_y = ball.y * (home.x-goal_x) / (ball.x-goal_x)。
                # 球已到站位线或其后方时直接取 ball.y，避免反向投影或除零。
                y = ball.y
                if ball.x > home[0]:
                    y *= (home[0] - goal_x) / (ball.x - goal_x)
                # 将横向目标限制在门柱内侧；默认门宽 2.6 米，留 0.25 米
                # 余量后 y 限于 [-1.05, 1.05]。这是目标点约束，不是路径约束。
                limit = max(0.0, ctx.field.goal_width / 2.0 - GUARD_POST_MARGIN_M)
                target = (home[0], clamp(y, -limit, limit))
                self.action = "guard:block"

            # 3. 判断本帧是否允许主动处理球：必须有裁判数据、处于 PLAYING
            # 且比赛未暂停；对方定位球/开球倒计时期间不主动接近出球。
            # can_clear 也供末尾移动使用，决定是否把球当作需要避让的障碍。
            game = ctx.game
            can_clear = (
                game is not None and game.state == GameState.PLAYING
                and not game.stopped
                and not (
                    game.kicking_team != ctx.team_id
                    and (game.set_play != SetPlay.NONE or game.secondary_time > 0)
                )
            )
            # 出球范围同时要求球在门前矩形内、守门员也离球足够近。
            # 默认场地：x∈[-7, -5.4]、|y|<=2，并且人球距离<=1.8 米。
            # 球越过门线后不在此范围，守门员不会为了追球继续进入球门深处。
            near_goal = (
                goal_x <= ball.x <= goal_x + ctx.field.goal_area_length + GUARD_CLEAR_EXTRA_M
                and abs(ball.y) <= ctx.field.goal_area_width / 2.0
                and dist(pose.x, pose.y, ball.x, ball.y) <= GUARD_CLEAR_REACH_M
            )
            if can_clear and near_goal:
                # 先准备默认解围方案：踢向前场中心，默认目标为 (3.5, 0)。
                # 仅在此门前处理分支考虑传球，不会为接应队友离开守门区域。
                clear_target = (ctx.field.length / 4.0, 0.0)
                # helper 检查队友位置/罚下状态、是否在球前方至少 0.75 米、
                # 距球是否在 6 米以内，以及传球线段和接球点附近有无机器人。
                # None、自己编号或任一检查不通过，都保留上面的解围方案。
                pass_target = self._guard_pass_target(pass_to)
                kick_power = KICK_POWER_BACKFIELD
                if pass_target is not None:
                    clear_target = pass_target
                    # 传球力度按距离线性估算，并限制在最小力度和后场解围
                    # 力度之间；该系数是初始配置，尚需完整仿真校准。
                    kick_power = clamp(
                        dist(ball.x, ball.y, *pass_target) * GUARD_PASS_POWER_PER_M,
                        KICK_POWER_MIN, KICK_POWER_BACKFIELD,
                    )
                # 出球方向是“球→目标”的场地角度；kick() 内部会转换为
                # 机器人身体坐标并应用现有右脚方向补偿。
                kick_dir = angle_to(ball.x, ball.y, *clear_target)
                # 守门独立使用近距离踢球阈值，不复用 attack 的 2 米阈值。
                # 初次<=0.65 米进入；已在踢球时允许保持到 0.85 米，减少抖动。
                kick_dist = GUARD_KICK_EXIT_M if self._kicking else GUARD_KICK_ENTER_M
                if dist(pose.x, pose.y, ball.x, ball.y) <= kick_dist:
                    self.action = f"guard:pass:{pass_to}" if pass_target is not None else "guard:clear_kick"
                    # 首次启动前停止普通行走，随后由踢球控制器接管底盘；
                    # 已在踢球时 backend 会忽略普通速度命令，保持踢球独占。
                    self.set_velocity(0.0, 0.0, 0.0)
                    self.kick(kick_dir, kick_power)
                    return
                # 尚未到踢球距离：先走向“球→出球目标”反方向 0.35 米处，
                # 让球尽量位于自己与目标之间，面向预定出球方向接近。
                stage = _behind_ball(ball.x, ball.y, clear_target, CHASE_BEHIND_M)
                # 球后点可能落入球门或偏出小禁区侧面，因此限制接近目标：
                # x 至少在门线前 0.25 米，y 不超过小禁区左右边界。
                target = (
                    max(goal_x + GUARD_GOAL_LINE_MARGIN_M, stage[0]),
                    clamp(stage[1], -ctx.field.goal_area_width / 2.0,
                          ctx.field.goal_area_width / 2.0),
                )
                self.action = f"guard:pass_approach:{pass_to}" if pass_target is not None else "guard:clear_approach"
                # 触球是本分支的目的，所以不避球；仍避开机器人及球门结构。
                # maintain_face=True 使远距离也全向移动并保持出球朝向。
                # walk_to() 会先取消上一帧踢球；本帧发出接近指令后直接返回。
                self.walk_to(target, face=kick_dir, avoid_ball=False,
                             avoid_robots=True, maintain_face=True)
                return

        # 4. 未进入门前出球分支：执行封堵或回中，默认面向球；
        # 球不可见或关闭 GUARD_FACE_BALL 时面向 +x（角度 0，即前场）。
        face = 0.0
        if GUARD_FACE_BALL and ball is not None:
            face = angle_to(
                pose.x, pose.y, ball.x, ball.y,
            )

        # 蓝色调试点标记当前最终站位目标，可能是中央，也可能是横移封堵点。
        debugdraw.point(
            target[0], target[1], rgb=(0.0, 0.6, 1.0), scale=0.2, ns="guard_home",
        )
        # 只有“存在威胁且允许主动触球”时才关闭避球；其余情况保持避球。
        # 始终避机器人并保持指定朝向。walk_to() 会取消残留踢球状态，
        # 因此球离开处理范围后会自然结束传球/解围并恢复站位。
        self.walk_to(target, face=face,
                     avoid_ball=ball is None or not self._guard_threatened or not can_clear,
                     avoid_robots=True, maintain_face=True)

    def freeman(
        self, ball_radius: float = FREEMAN_BALL_RADIUS_M,
        opponent_radius: float = FREEMAN_OPPONENT_RADIUS_M,
    ) -> None:
        """自由人：到最近接球位置等待，就位后面朝敌方门线中心。

        站位须在场内、距球不超过 ball_radius、距每个已知对手至少
        opponent_radius，且在敌方门线前至少 1 米；只取以球为原点、
        坐标轴平行场地坐标轴、包含敌方门中心的四分之一圆。
        门中心落在轴上时取自身所在侧，自身也在轴上则取正侧。
        每帧重算；无可行点或观测不足时停止，不主动踢球。
        """
        ctx, pose = self.context, self.pose
        self.freeman_region = dict(ball_radius=ball_radius, opponent_radius=opponent_radius,
                                   goal_line_margin=FREEMAN_GOAL_LINE_MARGIN_M)
        target = None if ctx is None or pose is None else nearest_free_position(
            ctx, pose, ball_radius, opponent_radius, FREEMAN_GOAL_LINE_MARGIN_M,
        )
        self.tactical_target = target
        if target is None:
            self.action = "freeman:stop"
            self.stop()
            return
        face = angle_to(pose.x, pose.y, ctx.field.length / 2.0, 0.0)
        distance = dist(pose.x, pose.y, *target)
        if distance <= 1e-9:
            self.action = "freeman:wait"
            self.release_kick()
            self.face_to(face)
            return
        self.action = "freeman:move"
        # 不使用普通站位的到达容差提前停在可行域外。
        self.walk_to(target, face=face, avoid_ball=True, avoid_robots=True,
                     arrive_dist=min(0.02, distance / 2.0))

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
