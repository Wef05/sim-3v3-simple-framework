"""策略入口：只组织「分配角色 → 整队决定行动 → 执行」三个阶段。

main 不知道某个角色有哪些分支，也不计算站位、踢球方向或速度。
这样新增角色或加入队友意图协作时，只需修改对应层，不必修改主流程。
运行时在进入 play 前更新共享观测，执行结束后统一绘图和发布遥测。
"""
from booster_agent_framework import AgentBase
from .framework.agent import SoccerAgentMixin
from . import actions, player, role_allocator

class SoccerSimAgent(SoccerAgentMixin, AgentBase):
    player_class = player.Player

    def init_store(self, store):
        role_allocator.init_store(store)

    @staticmethod
    def play(context, players, store):
        # 角色是职责分工，不是动作指令；分配层不会让机器人开始移动。
        assignments = role_allocator.assign(context, players, store)
        # 统一入口先收齐整队初步意图，再协调，避免某人先执行后队友才决策。
        intents = player.decide(context, players, assignments, store)
        # 只有最终意图进入执行层；前两步都不能直接调用机器人 SDK。
        actions.execute(context, players, intents)
