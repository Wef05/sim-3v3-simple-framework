"""普通进攻的短时踢球交接。只产生意图与诊断，不调用 SDK。"""
from __future__ import annotations

import math
from ..framework.types import Context, Pose2D
from ..intents import ActionIntent, KickIntent, KickTarget, MoveIntent, StopIntent
from ..param import (
    KICK_ENTER_M, KICK_EXIT_M, CHASE_BEHIND_M,
    ATTACK_KICK_STAGE_TOLERANCE_M, ATTACK_KICK_ENTER_ANGLE_RAD,
    ATTACK_KICK_EXIT_ANGLE_RAD, ATTACK_KICK_TIMEOUT_S, ATTACK_KICK_RETRY_S,
    ATTACK_KICK_MAX_OBSERVATION_AGE_S, ATTACK_KICK_EXIT_GRACE_S,
    ATTACK_KICK_BEHIND_GRACE_S, NAV_SAFETY_MARGIN_M,
)
from .geom import angle_to, behind_ball, normalize_angle
from .obstacles import collect_obstacles
from .path_planner import segment_clear


class AttackKickControl:
    """从首次意图计时，预算到期后的首个控制帧取消；不是硬实时停止。

    宽限只适用于距离、朝向及球在身后，不延迟安全退出。三个计时器独立，
    条件恢复立即清零；取消、角色/阶段切换或时钟回退均清理旧计时。
    """
    def __init__(self) -> None:
        self.since = None
        self.retry_at = 0.0
        self.last_now = None
        self.phase = None
        self.last_exit = None
        self.diagnostics = {}
        self._exceeded_since: dict[str, float] = {}

    def _report(self, now: float, state: str, reason: str, **details) -> None:
        elapsed = max(0.0, now-self.since) if self.since is not None else 0.0
        self.diagnostics = dict(
            policy='bounded_attack', state=state, reason=reason,
            elapsed_s=elapsed,
            remaining_s=max(0.0, ATTACK_KICK_TIMEOUT_S-elapsed)
            if self.since is not None else None,
            cooldown_remaining_s=max(0.0, self.retry_at-now),
            last_exit_reason=self.last_exit, exit_grace=[], **details)

    def cancel(self, now: float, reason: str) -> None:
        if self.since is not None:
            self.since = None
            self.retry_at = now+ATTACK_KICK_RETRY_S
            self.last_exit = reason
        self._exceeded_since.clear()
        self._report(now, 'cooldown' if now < self.retry_at else 'idle', reason)

    def begin(self, context: Context) -> None:
        now, game = context.now, context.game
        phase = None if game is None else (
            game.state, game.set_play, game.game_phase, game.first_half,
            game.kicking_team, game.secondary_time > 0, game.stopped)
        if self.last_now is not None and now < self.last_now:
            self.since, self.retry_at = None, now+ATTACK_KICK_RETRY_S
            self.last_exit = 'clock_reset'
            self._exceeded_since.clear()
        elif self.phase != phase:
            self.cancel(now, 'phase_changed')
        self.last_now, self.phase = now, phase

    def _continuation(self, now, distance, error, front):
        grace = []
        for reason, exceeded, duration in (
            ('too_far', distance > KICK_EXIT_M, ATTACK_KICK_EXIT_GRACE_S),
            ('misaligned', error > ATTACK_KICK_EXIT_ANGLE_RAD, ATTACK_KICK_EXIT_GRACE_S),
            ('ball_behind', not front, ATTACK_KICK_BEHIND_GRACE_S),
        ):
            if not exceeded:
                self._exceeded_since.pop(reason, None)
                continue
            since = self._exceeded_since.setdefault(reason, now)
            elapsed = max(0.0, now-since)
            if elapsed >= duration-1e-9:
                return reason, []
            grace.append(dict(reason=reason, elapsed_s=elapsed,
                              remaining_s=max(0.0, duration-elapsed)))
        return None, grace

    def decide(
        self, context: Context, pid: int, pose: Pose2D | None,
        current_ball: tuple[float, float] | None, target: tuple[float, float],
        aim: KickTarget, style: str, intercept: dict | None,
        is_kicking: bool, execution_reason: str | None = None,
    ) -> ActionIntent:
        now, ball = context.now, context.ball
        if self.since is not None and not is_kicking:
            self.cancel(now, execution_reason or 'execution_rejected')
        if self.since is not None and now-self.since >= ATTACK_KICK_TIMEOUT_S-1e-9:
            self.cancel(now, 'timeout')
        if (ball is None or pose is None or current_ball is None or
                not all(math.isfinite(v) for v in (*current_ball, *target,
                    pose.x, pose.y, pose.theta, ball.x, ball.y, ball.last_seen_at)) or
                not 0 <= now-ball.last_seen_at <= ATTACK_KICK_MAX_OBSERVATION_AGE_S):
            self.cancel(now, 'observation_invalid')
            return StopIntent()
        position = pose.x, pose.y
        origins = ((ball.x, ball.y), current_ball)
        distance = max(math.dist(position, origin) for origin in origins)
        direction = angle_to(*current_ball, *target)
        error = max(abs(normalize_angle(angle_to(*origin, *target)-pose.theta)) for origin in origins)
        front = all((origin[0]-pose.x)*math.cos(pose.theta)+
                    (origin[1]-pose.y)*math.sin(pose.theta) > 0 for origin in origins)
        stage = behind_ball(*current_ball, target, CHASE_BEHIND_M)
        stage_error = max(math.dist(position, behind_ball(*origin, target, CHASE_BEHIND_M))
                          for origin in origins)
        obstacles = collect_obstacles(context, pid, ball=False, robots=True, goals=True)
        approach_clear = all(segment_clear(
            position, origin, obstacles, margin=NAV_SAFETY_MARGIN_M,
            allow_escape=True) for origin in origins)
        # #9 允许场外准备点。点位/接近是否安全仅取决于实体障碍，不按标线裁剪。
        stage_clear = segment_clear(stage, stage, obstacles, margin=NAV_SAFETY_MARGIN_M)
        ready_path = (stage_clear and segment_clear(
            position, stage, obstacles, margin=NAV_SAFETY_MARGIN_M, allow_escape=True))
        details = dict(distance_m=distance, angle_error_deg=math.degrees(error),
                       stage_error_m=stage_error, stage_target=list(stage))
        active = self.since is not None
        grace = []
        if active:
            # SDK 跨步后无须回到启动准备点；到球的实体安全检查仍每帧生效。
            reason = 'approach_blocked' if not approach_clear else None
            if reason is None:
                reason, grace = self._continuation(now, distance, error, front)
            if reason is not None:
                self.cancel(now, reason)
        else:
            reason = ('too_far' if distance > KICK_ENTER_M else
                      'ball_behind' if not front else
                      'misaligned' if error > ATTACK_KICK_ENTER_ANGLE_RAD else
                      'approach_blocked' if not approach_clear or not ready_path else
                      'not_staged' if stage_error > ATTACK_KICK_STAGE_TOLERANCE_M+1e-9 else None)
        if now < self.retry_at:
            reason = 'cooldown'
        if reason is None:
            if self.since is None:
                self.since = now
            self._report(now, 'requested', 'grace_pending' if grace else 'ready', **details)
            self.diagnostics['exit_grace'] = grace
            return KickIntent(aim, style=style)
        # 未接管时由已有导航接近/绕行；准备点被实体占据时停止，不新增脱困策略。
        if distance > KICK_ENTER_M:
            if intercept is None or intercept.get('status') == 'unavailable':
                self._report(now, 'blocked', 'no_intercept', **details)
                return StopIntent()
            stage = behind_ball(*intercept['target'], target, CHASE_BEHIND_M)
        if not segment_clear(stage, stage, obstacles, margin=NAV_SAFETY_MARGIN_M):
            self._report(now, 'blocked', 'stage_blocked', **details)
            return StopIntent()
        state = 'cooldown' if now < self.retry_at else 'aligning' if stage_error <= ATTACK_KICK_STAGE_TOLERANCE_M else 'approaching'
        self._report(now, state, reason, **dict(details, stage_target=list(stage)))
        return MoveIntent(stage, face=direction, maintain_face=True,
                          arrive_dist=0.05, avoid_robots=True)
