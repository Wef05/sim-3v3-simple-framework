"""Opening passes must retain pass semantics from decision to dashboard."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from src.actions import execute
from src.framework.config import SoccerConfig
from src.framework.types import (ADULT_FIELD_DIMENSIONS, BallState, Context,
                                 GameControlState, GameState, Pose2D, RobotState)
from src.intents import KickIntent, KickTarget, Role, RoleTask
from src.player import Player
from src.telemetry import make_snapshot


class KickoffPowerTests(unittest.TestCase):
    def test_opening_pass_and_normal_shot_reach_backend_and_dashboard(self):
        # Receiver coordinates from the observed opening, then longer passes.
        for receiver, expected in [((-.4344753693, 3.5112123454), 3),
                                   ((0, 2.9), 1), ((0, 3), 3), ((6, 0), 3), ((6.1, 0), 5)]:
            with self.subTest(receiver=receiver):
                backend = Mock(mode='walk', fall_down_state='normal')
                player = Player(1, SoccerConfig(), backend)
                ctx = Context(10, .03, 1, ADULT_FIELD_DIMENSIONS,
                              ball=BallState(0, 0, last_seen_at=10),
                              teammates={1: RobotState(1, Pose2D(-.5, 0)),
                                         3: RobotState(3, Pose2D(*receiver))},
                              game=GameControlState(state=GameState.PLAYING,
                                                    kicking_team=1, secondary_time=10))
                for kickoff in (True, False):
                    current = ctx if kickoff else replace(ctx, game=replace(ctx.game, secondary_time=0))
                    player.context = current
                    player.motion.update(current)
                    intent = player.decide(RoleTask(Role.ATTACKER))
                    self.assertIsInstance(intent, KickIntent)
                    self.assertEqual(intent.style, 'pass' if kickoff else 'shot')
                    self.assertEqual(intent.target, KickTarget.teammate(3) if kickoff else KickTarget.goal())
                    execute(current, [player], {1: intent})
                    power = expected if kickoff else 5
                    self.assertEqual(backend.kick.call_args.args[1], power)
                    snapshot = make_snapshot(current, [player], SimpleNamespace())
                    self.assertEqual(snapshot['players'][0]['kick_power'], power)


if __name__ == '__main__':
    unittest.main()
