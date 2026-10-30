"""机器人状态与最终 SDK 指令边界。

本层不选择角色或踢球对象，只接收已经规划好的方向/力度或速度。
场地坐标转身体坐标、右脚方向补偿和力度限幅集中在这里，避免各角色重复实现。
_kicking 记录是否已发出踢球请求，不是 SDK 确认完成或球已踢出的证明。
"""
from __future__ import annotations
import logging
import math
from ..framework.types import Context, Pose2D, Penalty
from ..param import *
from ..utils.geom import angle_to, clamp, dist, normalize_angle, opponent_goal
_log = logging.getLogger(__name__)

class Executor:
    def __init__(self, player_id, config, backend):
        self.id = player_id
        self.config = config
        self._backend = backend
        self.context = None
        self._mode = None
        self._fall_down_state = None
        self._kicking = False
        self._avoid_side = None
        self.tactical_target = None
        self.kick_observation = None

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
            raise ValueError("executor requires a planned kick direction")

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
        self.kick_observation = dict(
            player_id=self.id, time=self.context.now, robot=[pose.x, pose.y],
            planned_direction=kick_direction, power=power_clamped,
            command_direction=normalize_angle(direction_body + pose.theta),
        )

    def release_kick(self) -> None:
        self.kick_observation = None
        self._kicking = False  # 清除踢球迟滞标志(取消方块显示)
        if self._backend is None:
            _log.debug("player %d release_kick (no backend)", self.id)
            return
        self._backend.release_kick()

    def request_mode(self, mode: str) -> None:
        if self._backend is None:
            _log.debug("player %d request_mode -> %s (no backend)", self.id, mode)
            return
        self._backend.request_mode(mode)

    def get_up(self) -> None:
        if self._backend is None:
            _log.debug("player %d get_up (no backend)", self.id)
            return
        if self.context is not None:
            self._backend.get_up(self.context.now)

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
