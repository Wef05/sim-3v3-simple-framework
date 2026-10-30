"""Agent entry point: allocate roles, decide as a team, execute."""
from booster_agent_framework import AgentBase
from .framework.agent import SoccerAgentMixin
from . import actions, player, role_allocator

class SoccerSimAgent(SoccerAgentMixin, AgentBase):
    player_class = player.Player

    def init_store(self, store):
        role_allocator.init_store(store)

    @staticmethod
    def play(context, players, store):
        assignments = role_allocator.assign(context, players, store)
        intents = player.decide(context, players, assignments, store)
        actions.execute(context, players, intents)
