"""框架运行时:30Hz 主循环、Context 构造、Player 实例管理。

Context 数据来自注入的 ContextSource(Phase 2 的 ROS 数据源);未注入时(开发机 /
单测)每帧构造空 Context。新鲜度过滤(陈旧→None)在此层统一做,见 docs/new_design.md §9.3。
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Protocol

from .config import SoccerConfig
from .types import (
    ADULT_FIELD_DIMENSIONS,
    BallState,
    Context,
    GameControlState,
    RobotState,
    WorldSnapshot,
)

if TYPE_CHECKING:
    from ..player import Player
    from .agent import SoccerAgentMixin


__all__ = ["ContextSource", "SoccerRuntime"]


_log = logging.getLogger(__name__)


class ContextSource(Protocol):
    """数据源协议:runtime 每帧从它取原始快照,自身不依赖具体 ROS 实现。"""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def get_snapshot(self) -> WorldSnapshot: ...


class SoccerRuntime:
    """30Hz 控制循环 + Player 生命周期管理。

    对用户不可见:用户只接触 SoccerAgent / Player / Context / play(),不需要
    知道这个类的存在。

    ``context_source`` 为 None 时(开发机 / 单测)每帧构造空 Context;传入 ROS
    数据源时构造真实数据 + 新鲜度过滤后的 Context。
    """

    def __init__(
        self,
        agent: "SoccerAgentMixin",
        context_source: "ContextSource | None" = None,
    ) -> None:
        self._agent = agent
        self._config: SoccerConfig = agent.config
        self._source = context_source
        self._store = SimpleNamespace()
        self._players: list[Player] = [
            agent.player_class(player_id=pid, config=self._config, _backend=None)
            for pid in self._config.player_ids
        ]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._init_store_called = False
        self._last_now: float | None = None
        self._tick_id = 0
        self._clock_epoch: int | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            _log.info("runtime already running, ignore start")
            return
        if self._source is not None:
            self._source.start()
        if not self._init_store_called:
            self._agent.init_store(self._store)
            self._init_store_called = True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="soccer_runtime", daemon=True,
        )
        self._thread.start()
        _log.info(
            "SoccerRuntime started: team_id=%d control_hz=%.1f players=%d source=%s",
            self._config.team_id, self._config.control_hz, len(self._players),
            type(self._source).__name__ if self._source else "None",
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        if self._source is not None:
            self._source.stop()
        self._close_backends()
        _log.info("SoccerRuntime stopped")

    def _close_backends(self) -> None:
        """关闭所有 player 的 SDK backend。"""
        for player in self._players:
            if player._backend is not None:
                try:
                    player._backend.close()
                except Exception as exc:
                    _log.warning(
                        "player %d backend close failed: %s", player.id, exc,
                    )

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        period = 1.0 / max(1.0, self._config.control_hz)
        while not self._stop.is_set():
            started_at = time.monotonic()
            try:
                self._tick(started_at)
            except Exception as exc:
                _log.exception("control loop tick failed: %s", exc)
                # 异常时全员停车(直接调,不走 play 路径)
                for p in self._players:
                    try:
                        p.controller.stop()
                    except Exception:
                        pass

            elapsed = time.monotonic() - started_at
            self._stop.wait(max(0.0, period - elapsed))

    def _tick(self, wall_now: float) -> None:
        snap = self._source.get_snapshot() if self._source is not None else WorldSnapshot()
        if snap.sim_time is None:
            # 不允许无 /clock 时悄悄退回本地时间。
            for p in self._players:
                p.controller.stop()
            return
        now = snap.sim_time
        if (self._clock_epoch != snap.clock_epoch
                or (self._last_now is not None and now < self._last_now)):
            self._last_now = None
            self._clock_epoch = snap.clock_epoch
            for name in ('ball_motion', 'teammate_motion', 'kick_error_tracker'):
                if hasattr(self._store, name):
                    delattr(self._store, name)
            self._agent.init_store(self._store)
            for p in self._players:
                p.controller.stop()
                p.controller.kick_observation = None
                for name in ('_candidate_cache', '_candidate_signature', '_scoring_cache'):
                    if hasattr(p, name):
                        delattr(p, name)
        self._tick_id += 1
        dt = 0.0 if self._last_now is None else (now - self._last_now)
        self._last_now = now

        ctx = self._build_context(now, dt, snap, wall_now)
        for p in self._players:
            p.context = ctx

        # 调试可视化:开一帧,画常驻世界(球/队友/对手),再让 play() 追加策略 marker
        from . import debugdraw
        debugdraw.begin_frame()
        self._draw_world(ctx)

        from .decision_frame import prepare_frame
        from ..visualization import finish_frame
        prepare_frame(ctx, self._players, self._store)

        # Three-stage team strategy.
        self._agent.play(ctx, self._players, self._store)

        finish_frame(ctx, self._players, self._store)
        debugdraw.flush()

        # 每 ~2s 打一行 heartbeat,肉眼验证主循环 + 数据通路
        if self._tick_id % 60 == 0:
            self._log_heartbeat(ctx, dt)

    def _draw_world(self, ctx: Context) -> None:
        """常驻可视化:球场/球门(暗)、球(橙)、我方(红+编号+朝向)、对手(蓝+朝向)。"""
        from . import debugdraw
        import math

        self._draw_field(ctx)

        if ctx.ball is not None:
            debugdraw.point(
                ctx.ball.x, ctx.ball.y, rgb=(1.0, 0.5, 0.0), scale=0.2, ns="ball",
            )
        # 我方队员标记(颜色/形状随 kick 状态变化)由 visualization.py 画,
        # 它在执行后读取角色与控制器状态。这里只画朝向 + 对手。
        for r in ctx.teammates.values():
            if r.pose is not None:
                self._draw_facing(r.pose)
        for r in ctx.opponents.values():
            if r.pose is not None:
                debugdraw.point(r.pose.x, r.pose.y, rgb=(0.2, 0.4, 1.0),
                                scale=0.3, ns="opponent")
                self._draw_facing(r.pose)

    def _draw_facing(self, pose) -> None:
        """机器人朝向:白色短箭头(0.4m),ns=facing。与黄色速度 heading 区分。"""
        from . import debugdraw
        import math

        debugdraw.arrow(
            pose.x, pose.y,
            pose.x + math.cos(pose.theta) * 0.4,
            pose.y + math.sin(pose.theta) * 0.4,
            rgb=(1.0, 1.0, 1.0), ns="facing",
        )

    def _draw_field(self, ctx: Context) -> None:
        """静态场地:外边界、中线、中圈、两侧球门框。暗灰色。"""
        from . import debugdraw
        import math

        f = ctx.field
        hl, hw = f.length / 2.0, f.width / 2.0
        gray = (0.5, 0.5, 0.5)
        # 外边界
        debugdraw.line(
            [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw), (-hl, -hw)],
            rgb=gray, ns="field_bounds",
        )
        # 中线
        debugdraw.line([(0.0, -hw), (0.0, hw)], rgb=gray, ns="field_midline")
        # 中圈(多边形近似)
        r = f.circle_radius
        circle = [
            (r * math.cos(a), r * math.sin(a))
            for a in [i * math.pi / 12 for i in range(25)]
        ]
        debugdraw.line(circle, rgb=gray, ns="field_circle")
        # 两侧球门框(半个球门宽 × 进深 0.6)
        gw = f.goal_width / 2.0
        depth = 0.6
        for sx in (-1.0, 1.0):
            fx = sx * hl
            bx = sx * (hl + depth)
            debugdraw.line(
                [(fx, -gw), (bx, -gw), (bx, gw), (fx, gw)],
                rgb=gray, ns="field_goal",
            )

    def _log_heartbeat(self, ctx: Context, dt: float) -> None:
        ball_repr = (
            "None" if ctx.ball is None else f"({ctx.ball.x:.2f},{ctx.ball.y:.2f})"
        )
        seen = sum(1 for r in ctx.teammates.values() if r.pose is not None)
        opp_seen = sum(1 for r in ctx.opponents.values() if r.pose is not None)
        _log.info(
            "tick #%d dt=%.3f game=%s ball=%s teammates_seen=%d/%d opponents_seen=%d/%d",
            self._tick_id, dt,
            "None" if ctx.game is None else ctx.game.state.value,
            ball_repr,
            seen, len(ctx.teammates),
            opp_seen, len(ctx.opponents),
        )

    # ------------------------------------------------------------------
    # Context 构造 + 新鲜度过滤
    # ------------------------------------------------------------------

    def _build_context(self, now: float, dt: float, snap: WorldSnapshot, wall_now: float) -> Context:
        return Context(
            now=now,
            dt=dt,
            team_id=self._config.team_id,
            field=ADULT_FIELD_DIMENSIONS,
            game=self._fresh_game(snap.game, now, wall_now),
            ball=self._fresh_ball(snap.ball, now, wall_now),
            teammates={
                pid: self._fresh_robot(r, now, wall_now) for pid, r in snap.teammates.items()
            },
            opponents={
                pid: self._fresh_robot(r, now, wall_now) for pid, r in snap.opponents.items()
            },
        )

    def _fresh_game(
        self, game: GameControlState | None, now: float, wall_now: float,
    ) -> GameControlState | None:
        if game is None:
            return None
        if (wall_now - game.received_at if game.received_at is not None else now - game.last_seen_at) > self._config.game_state_max_age_sec:
            return None
        return game

    def _fresh_ball(self, ball: BallState | None, now: float, wall_now: float) -> BallState | None:
        if ball is None:
            return None
        if (wall_now - ball.received_at if ball.received_at is not None else now - ball.last_seen_at) > self._config.ball_max_age_sec:
            return None
        return ball

    def _fresh_robot(self, robot: RobotState, now: float, wall_now: float) -> RobotState:
        """位姿陈旧则清成 None(robot 对象保留),见 doc §9.3。"""
        if (
            robot.pose is not None
            and (wall_now - robot.received_at if robot.received_at is not None else now - robot.last_seen_at) > self._config.robot_pose_max_age_sec
        ):
            return dataclasses.replace(robot, pose=None)
        return robot
