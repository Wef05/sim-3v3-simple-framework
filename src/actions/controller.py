"""动作执行入口：将最终意图转换为移动/踢球控制，并管理动作互斥。

角色层负责「是否需要踢」，本层负责「能否执行以及如何交接控制权」。
每名机器人持有独立控制器；全队决策完成后再逐个下发，SDK 调用并非原子同步。
"""
from dataclasses import asdict
from .motion_controller import MotionController
from .kick_planner import plan_kick
from ..intents import (MoveIntent, FaceIntent, KickIntent, StopIntent, RecoverIntent)
from ..framework import debugdraw
from ..param import KICK_TARGET_MARK_SIZE_M

class ActionController(MotionController):
    def execute(self, intent):
        """执行一名球员本帧最终意图；未知类型先停车，再报错。"""
        self.kick_observation = None
        # 分配之后 SDK 状态仍可能变化，协调层也可能替换意图，因此下发前再次检查。
        # 即便被罚下，也允许起身/切模式；所有战术移动和踢球请求则被停止覆盖。
        if self.is_penalized or self.pose is None:
            self.stop()
            self.ensure_ready()
            return
        if self.is_fallen or self.mode != 'walk':
            self.stop()
            self.ensure_ready()
            return
        # walk_to 内部先释放踢球；否则踢球控制器可能仍占用底盘，普通移动不生效。
        if isinstance(intent, MoveIntent):
            self.walk_to(**asdict(intent))
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
    for player in players:
        player.controller.context = context
        player.controller.execute(intents.get(player.id, StopIntent()))
