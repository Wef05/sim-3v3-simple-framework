"""普通进攻的短时踢球交接。只产生意图与诊断，不调用 SDK。"""
from __future__ import annotations

import math
from ..intents import KickIntent, MoveIntent, StopIntent
from ..param import (KICK_ENTER_M, KICK_EXIT_M, CHASE_BEHIND_M,
    ATTACK_KICK_STAGE_TOLERANCE_M, ATTACK_KICK_ENTER_ANGLE_RAD,
    ATTACK_KICK_EXIT_ANGLE_RAD, ATTACK_KICK_TIMEOUT_S, ATTACK_KICK_RETRY_S,
    ATTACK_KICK_MAX_OBSERVATION_AGE_S, NAV_SAFETY_MARGIN_M)
from .geom import angle_to, behind_ball, normalize_angle
from .obstacles import collect_obstacles
from .path_planner import clamp_target, segment_clear


class AttackKickControl:
    """请求计时从首次意图开始，保证实际接管不会比预算更长。

    不声称请求已被 SDK 接受；下一帧结合执行状态处理拒绝。计时、冷却和
    退出原因由该对象独占，角色/比赛阶段变化以及时钟回退均取消旧请求。
    """
    def __init__(self) -> None:
        self.since = None
        self.retry_at = 0.0
        self.last_now = None
        self.phase = None
        self.last_exit = None
        self.diagnostics = {}

    def _report(self, now: float, state: str, reason: str, **details) -> None:
        elapsed = max(0.0, now-self.since) if self.since is not None else 0.0
        self.diagnostics = dict(policy='bounded_attack', state=state, reason=reason,
            elapsed_s=elapsed, remaining_s=max(0.0, ATTACK_KICK_TIMEOUT_S-elapsed)
            if self.since is not None else None,
            cooldown_remaining_s=max(0.0, self.retry_at-now),
            last_exit_reason=self.last_exit, **details)

    def cancel(self, now: float, reason: str) -> None:
        if self.since is not None:
            self.since = None
            self.retry_at = now+ATTACK_KICK_RETRY_S
            self.last_exit = reason
        self._report(now, 'cooldown' if now < self.retry_at else 'idle', reason)

    def begin(self, context) -> None:
        now, game = context.now, context.game
        phase = None if game is None else (game.state, game.set_play, game.game_phase,
                                           game.first_half, game.kicking_team, game.secondary_time > 0,
                                           game.stopped)
        if self.last_now is not None and now < self.last_now:
            self.since, self.retry_at = None, now+ATTACK_KICK_RETRY_S
            self.last_exit = 'clock_reset'
        elif self.phase != phase:
            self.cancel(now, 'phase_changed')
        self.last_now, self.phase = now, phase

    def decide(self, context, pid, pose, current_ball, target, aim, style,
               intercept, is_kicking, execution_reason=None):
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
        ready_path = (stage == clamp_target(context, stage) and
            segment_clear(stage, stage, obstacles, margin=NAV_SAFETY_MARGIN_M) and
            segment_clear(position, stage, obstacles, margin=NAV_SAFETY_MARGIN_M, allow_escape=True) and
            all(segment_clear(position, origin, obstacles, margin=NAV_SAFETY_MARGIN_M,
                              allow_escape=True) for origin in origins))
        details = dict(distance_m=distance, angle_error_deg=math.degrees(error),
                       stage_error_m=stage_error, stage_target=list(stage))
        active = self.since is not None
        reason = ('too_far' if distance > (KICK_EXIT_M if active else KICK_ENTER_M) else
                  'ball_behind' if not front else
                  'misaligned' if error > (ATTACK_KICK_EXIT_ANGLE_RAD if active else ATTACK_KICK_ENTER_ANGLE_RAD) else
                  'approach_blocked' if not ready_path else
                  'not_staged' if not active and stage_error > ATTACK_KICK_STAGE_TOLERANCE_M+1e-9 else None)
        if active and reason is not None:
            self.cancel(now, reason)
        if now < self.retry_at:
            reason = 'cooldown'
        if reason is None:
            if self.since is None:
                self.since = now
            self._report(now, 'requested', 'ready', **details)
            return KickIntent(aim, style=style)
        # 只负责自主接近与对齐。受阻时不新增出球兜底、脱困或碰撞豁免。
        if distance > KICK_ENTER_M:
            if intercept is None or intercept.get('status') == 'unavailable':
                self._report(now, 'blocked', 'no_intercept', **details)
                return StopIntent()
            stage = clamp_target(context, behind_ball(*intercept['target'], target, CHASE_BEHIND_M))
        if (stage != clamp_target(context, stage) or
                not segment_clear(stage, stage, obstacles, margin=NAV_SAFETY_MARGIN_M)):
            self._report(now, 'blocked', 'stage_blocked', **details)
            return StopIntent()
        state = 'cooldown' if now < self.retry_at else 'aligning' if stage_error <= ATTACK_KICK_STAGE_TOLERANCE_M else 'approaching'
        self._report(now, state, reason, **dict(details, stage_target=list(stage)))
        return MoveIntent(stage, face=direction, maintain_face=True,
                          arrive_dist=0.05, avoid_robots=True)
