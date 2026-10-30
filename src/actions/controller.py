"""The sole action dispatcher. Role decisions never issue robot commands."""
from dataclasses import asdict
from .motion_controller import MotionController
from .kick_planner import plan_kick
from ..intents import (MoveIntent, FaceIntent, KickIntent, StopIntent, RecoverIntent)
from ..framework import debugdraw
from ..param import KICK_TARGET_MARK_SIZE_M

class ActionController(MotionController):
    def execute(self, intent):
        # Recheck readiness at the command boundary, even for coordinated intents.
        if self.is_penalized or self.pose is None:
            self.stop()
            self.ensure_ready()
            return
        if self.is_fallen or self.mode != 'walk':
            self.stop()
            self.ensure_ready()
            return
        if isinstance(intent, MoveIntent):
            self.walk_to(**asdict(intent))
        elif isinstance(intent, FaceIntent):
            self.release_kick()
            self.face_to(intent.theta)
        elif isinstance(intent, KickIntent):
            plan = plan_kick(self.context, intent)
            if plan is None:
                self.stop()
                return
            self.tactical_target = plan.target
            x, y = plan.target
            s = KICK_TARGET_MARK_SIZE_M
            debugdraw.line([(x-s, y-s), (x+s, y+s)], rgb=(1, 0, 1), ns='kick_target')
            debugdraw.line([(x-s, y+s), (x+s, y-s)], rgb=(1, 0, 1), ns='kick_target')
            self.set_velocity(0.0, 0.0, 0.0)
            self.kick(plan.direction, plan.power)
        elif isinstance(intent, (StopIntent, RecoverIntent)):
            self.stop()
        else:
            self.stop()
            raise TypeError(f'Unsupported intent: {intent!r}')

def execute(context, players, intents):
    for player in players:
        player.controller.context = context
        player.controller.execute(intents.get(player.id, StopIntent()))
