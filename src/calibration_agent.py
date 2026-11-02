"""Explicit experimental entry; the production manifest never selects this class."""
from booster_agent_framework import AgentBase
from .framework.agent import SoccerAgentMixin
from .player import Player
from . import role_allocator
from .motion_calibration import CalibrationHost


class MotionCalibrationAgent(SoccerAgentMixin, AgentBase):
    player_class = Player

    def init_store(self, store):
        role_allocator.init_store(store)
        if hasattr(store, 'calibration_host'):
            store.calibration_host.abort('clock_reset')
        else:
            store.calibration_host = CalibrationHost()

    @staticmethod
    def play(context, players, store):
        store.calibration_host.tick(context, players)
