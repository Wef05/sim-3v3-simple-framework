"""动作执行入口：将最终意图转换为移动/踢球控制，并管理动作互斥。

角色层负责「是否需要踢」，本层负责「能否执行以及如何交接控制权」。
每名机器人持有独立控制器；全队决策完成后再逐个下发，SDK 调用并非原子同步。
"""
from dataclasses import asdict
import math
from .motion_controller import MotionController
from .kick_planner import plan_kick
from ..intents import (MoveIntent, FaceIntent, KickIntent, StopIntent, RecoverIntent)
from ..framework import debugdraw
from ..param import KICK_TARGET_MARK_SIZE_M

class ActionController(MotionController):
    def execute(self, intent, motion_command=None):
        """执行一名球员本帧最终意图；未知类型先停车，再报错。"""
        self.kick_observation = None
        if not isinstance(intent, MoveIntent):
            self.reset_navigation()
        # 分配之后 SDK 状态仍可能变化，协调层也可能替换意图，因此下发前再次检查。
        # 即便被罚下，也允许起身/切模式；所有战术移动和踢球请求则被停止覆盖。
        if self.is_penalized or self.pose is None:
            self.reset_navigation()
            self.stop()
            self.ensure_ready()
            return
        if self.is_fallen or self.mode != 'walk':
            self.reset_navigation()
            self.stop()
            self.ensure_ready()
            return
        # walk_to 内部先释放踢球；否则踢球控制器可能仍占用底盘，普通移动不生效。
        if isinstance(intent, MoveIntent):
            if motion_command is None:
                self.walk_to(**asdict(intent))
            else:
                self.release_kick()
                self.set_velocity(*motion_command)
        elif isinstance(intent, FaceIntent):
            self.release_kick()
            self.face_to(intent.theta)
        elif isinstance(intent, KickIntent):
            # 第一层把球门/队友等语义目标解析成场地目标，再算方向与力度。
            # 目标丢失时取消旧踢球，不能继续沿用上一帧的方向。
            plan = plan_kick(self.context, intent)
            if plan is None:
                self.stop()
                return
            # SDK kick owns its internal stepping. Reject an approach through
            # a post/net before handing over; the kick's actual stepping still
            # needs simulator validation because SDK exposes no planned path.
            from ..utils.obstacles import goal_obstacles
            from ..utils.path_planner import segment_clear
            from ..param import NAV_SAFETY_MARGIN_M
            ball = self.context.ball
            if ball is None or not segment_clear(
                (self.pose.x, self.pose.y), (ball.x, ball.y), goal_obstacles(self.context),
                margin=NAV_SAFETY_MARGIN_M, allow_escape=True,
            ):
                self.navigation_status = dict(state='kick_approach_blocked', blocker='goal_structure')
                self.stop()
                return
            self.tactical_target = plan.target
            x, y = plan.target
            s = KICK_TARGET_MARK_SIZE_M
            debugdraw.line([(x-s, y-s), (x+s, y+s)], rgb=(1, 0, 1), ns='kick_target')
            debugdraw.line([(x-s, y+s), (x+s, y-s)], rgb=(1, 0, 1), ns='kick_target')
            # 停止普通行走，再将球位置和计划交给底层；身体坐标转换只做一次。
            self.set_velocity(0.0, 0.0, 0.0)
            self.kick(plan.direction, plan.power)
        elif isinstance(intent, (StopIntent, RecoverIntent)):
            self.stop()
        else:
            self.stop()
            raise TypeError(f'Unsupported intent: {intent!r}')

def execute(context, players, intents):
    """整队执行入口。缺失意图按停车兜底，绝不默默保持上一帧动作。

    正常决策入口已检查编号完整性；这里仍保留兜底，支持独立调用执行层。
    """
    from ..utils.navigation import MotionTracker, NavigationFrame, trajectories_conflict
    from ..param import NAV_BODY_RADIUS_M, NAV_SAFETY_MARGIN_M
    players = list(players)
    if not players:
        return
    # Shared tracker survives order changes; a single update precedes all planning.
    tracker = next((p.controller._navigation_tracker for p in players
                    if hasattr(p.controller, '_navigation_tracker')), None)
    if tracker is None:
        tracker = MotionTracker()
    tracker.update(context)
    frame = NavigationFrame(context, tracker)
    commands = {}
    for p in players:
        p.controller.context = context
        p.controller._navigation_tracker = tracker
        p.controller.navigation_status = None
        intent = intents.get(p.id, StopIntent())
        if not isinstance(intent, MoveIntent):
            frame.reserve(p.id, (0., 0., 0.))
    # Complete the whole team's plan before any SDK call. Stable tie-break by ID.
    for p in sorted(players, key=lambda p: (-getattr(intents.get(p.id), 'motion_priority', 0), p.id)):
        controller, intent = p.controller, intents.get(p.id, StopIntent())
        if not isinstance(intent, MoveIntent):
            continue
        if controller.pose is None or controller.is_penalized or controller.is_fallen or controller.mode != 'walk':
            command = (0., 0., 0.)
        else:
            command, _ = controller.plan_walk(intent, frame)
        commands[p.id] = command
        theta = controller.pose.theta if controller.pose else 0.
        vx, vy, yaw = command
        frame.reserve(p.id, (vx*math.cos(theta)-vy*math.sin(theta),
                             vx*math.sin(theta)+vy*math.cos(theta), yaw))

    # Recheck the actual pair of selected commands, including braking. Testing
    # observed and stationary neighbors separately is not sufficient when a
    # lower-priority fallback lies between those two predicted trajectories.
    ranked = sorted(players, key=lambda p: (-getattr(intents.get(p.id), 'motion_priority', 0), p.id))
    def predicted(p):
        pose = p.controller.pose
        return frame.trajectory((pose.x, pose.y), tracker.velocity('team', p.id),
                                frame.reservations.get(p.id, (0., 0., 0.)))

    def halt(p, other_id):
        if p.id not in commands or commands[p.id] == (0., 0., 0.):
            return False
        commands[p.id] = (0., 0., 0.)
        frame.reserve(p.id, (0., 0., 0.))
        if p.controller.navigation_status is not None:
            p.controller.navigation_status.update(state='team_conflict_stop',
                blocker=f'team:{other_id}', command=[0., 0., 0.], speed=0.)
        return True

    for _ in range(len(players)+1):
        changed = False
        for i, high in enumerate(ranked):
            if high.controller.pose is None:
                continue
            for low in ranked[i+1:]:
                if low.controller.pose is None:
                    continue
                radius = 2*NAV_BODY_RADIUS_M+NAV_SAFETY_MARGIN_M
                if trajectories_conflict(predicted(high), predicted(low), radius):
                    changed = halt(low, high.id) or changed
                    if trajectories_conflict(predicted(high), predicted(low), radius):
                        changed = halt(high, low.id) or changed
        if not changed:
            break
    for p in players:
        p.controller.execute(intents.get(p.id, StopIntent()), commands.get(p.id))
