import importlib
import math
import sys
import types
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from src.framework.config import SoccerConfig
from src.framework.types import (
    ADULT_FIELD_DIMENSIONS, KICKING_TEAM_NONE, BallState, Context,
    GameControlState, GameState, Pose2D, RobotState, SetPlay,
)
from src.param import CIRCLE_MARGIN_M, OPPONENT_RESTART_AVOID_M
from src.player import Player


# 本机无 Booster SDK;只替换入口基类,仍执行真实策略、Player 和控制计算。
sdk = types.ModuleType("booster_agent_framework")
sdk.AgentBase = type("AgentBase", (), {})
sdk.AgentFeatures = object
with patch.dict(sys.modules, {"booster_agent_framework": sdk}):
    strategy = importlib.import_module("src.main")


class OpponentRestartTests(unittest.TestCase):
    def setUp(self):
        self.game = GameControlState(
            state=GameState.PLAYING, kicking_team=2, set_play=SetPlay.CORNER_KICK,
        )
        self.context = Context(
            now=0, dt=1 / 30, team_id=1, field=ADULT_FIELD_DIMENSIONS,
            game=self.game, ball=BallState(0, 0),
            teammates={
                1: RobotState(1, Pose2D(0.4, 0, math.pi)),
                2: RobotState(2, Pose2D(-1.7, 0)),
                3: RobotState(3, Pose2D(-3, 2)),
            },
        )
        self.players = []
        for pid in self.context.teammates:
            backend = Mock(mode="walk", fall_down_state="normal")
            self.players.append(Player(pid, SoccerConfig(), backend))
        self.store = types.SimpleNamespace()
        strategy.SoccerSimAgent.init_store(None, self.store)

    def step(self, context=None):
        self.context = context or self.context
        for player in self.players:
            player.context = self.context
        strategy.SoccerSimAgent.play(self.context, self.players, self.store)

    def assert_no_kicks(self):
        for player in self.players:
            player._backend.kick.assert_not_called()
            self.assertFalse(player.is_kicking)

    def test_every_opponent_set_play_cancels_existing_kicks(self):
        for set_play in SetPlay:
            if set_play == SetPlay.NONE:
                continue
            with self.subTest(set_play=set_play):
                for player in self.players:
                    player._kicking = True
                    player._backend.reset_mock()
                self.step(replace(
                    self.context, game=replace(self.game, set_play=set_play),
                ))
                self.assert_no_kicks()
                self.assertEqual(self.store.cur_phase, strategy.Phase.OPP_SET_PLAY)
                for player in self.players:
                    player._backend.release_kick.assert_called()
                    self.assertTrue(player.action.startswith("opp_restart:"))

    def test_far_players_hold_position_and_face_ball(self):
        self.step()
        for player in self.players[1:]:
            vx, vy, _ = player._backend.set_velocity.call_args.args
            self.assertEqual((vx, vy), (0, 0))
            self.assertEqual(player.action, "opp_restart:wait")

    def test_near_player_retreats_away_from_ball(self):
        self.step()
        player = self.players[0]
        vx, vy, _ = player._backend.set_velocity.call_args.args
        pose = player.pose
        world_vx = vx * math.cos(pose.theta) - vy * math.sin(pose.theta)
        self.assertGreater(world_vx, 0)
        self.assertEqual(player.action, "opp_restart:retreat")

    def test_waits_until_referee_clears_restart(self):
        self.step()
        # 球位置变化也不自行取消裁判仍在生效的限制。
        self.step(replace(self.context, ball=BallState(0.2, 0)))
        self.assert_no_kicks()
        self.step(replace(self.context, game=replace(
            self.game, set_play=SetPlay.NONE, secondary_time=0,
        )))
        self.assertEqual(self.store.cur_phase, strategy.Phase.NORMAL)
        # 裁判放行后，近身球允许守门员立即解围。
        self.players[0]._backend.kick.assert_called_once()
        self.assertEqual(self.players[0].action, "guard:clear")
        self.players[1]._backend.kick.assert_called_once()

    def test_our_restart_organizes_receiver_before_kicking(self):
        self.step(replace(self.context, game=replace(self.game, kicking_team=1)))
        self.assertEqual(self.store.cur_phase, strategy.Phase.OUR_SET_PLAY)
        self.players[0]._backend.kick.assert_not_called()
        self.players[1]._backend.kick.assert_not_called()
        self.assertEqual(self.players[2].action, "restart:run")

    def test_unknown_owner_does_not_fall_back_to_normal(self):
        for set_play, secondary_time in ((SetPlay.THROW_IN, 0), (SetPlay.NONE, 5)):
            with self.subTest(set_play=set_play):
                self.step(replace(self.context, game=replace(
                    self.game, set_play=set_play, secondary_time=secondary_time,
                    kicking_team=KICKING_TEAM_NONE,
                )))
                self.assert_no_kicks()
                self.assertIn(self.store.cur_phase, (
                    strategy.Phase.OPP_SET_PLAY, strategy.Phase.OPP_KICKOFF,
                ))

    def test_kickoff_excludes_center_circle_with_margin(self):
        self.step(replace(self.context, game=replace(
            self.game, set_play=SetPlay.NONE, secondary_time=10,
        )))
        self.assert_no_kicks()
        self.assertEqual(self.store.cur_phase, strategy.Phase.OPP_KICKOFF)
        radius = max(
            OPPONENT_RESTART_AVOID_M,
            self.context.field.circle_radius + CIRCLE_MARGIN_M,
        )
        with patch.object(self.players[0], "wait_for_opponent_restart") as wait:
            self.step()
        wait.assert_called_once_with(radius)

    def test_unknown_ball_or_pose_stops_without_kick(self):
        for changes in ({"ball": None}, {"teammates": {}}):
            with self.subTest(changes=changes):
                self.step(replace(self.context, **changes))
                self.assert_no_kicks()
                for player in self.players:
                    player._backend.set_velocity.assert_called_with(0, 0, 0)

    def test_unready_player_cancels_previous_movement_and_kick(self):
        player = self.players[0]
        player._backend.mode = "stand"
        player._kicking = True
        self.step()
        self.assert_no_kicks()
        player._backend.release_kick.assert_called()
        player._backend.set_velocity.assert_called_with(0, 0, 0)

    def test_corner_and_touchline_retreat_targets_stay_in_field(self):
        for bx, by, px, py in (
            (7, 4.5, 6.8, 4.3), (0, 4.5, 0, 4.4),
            (-7, -4.5, -6.8, -4.3), (0, -4.5, 0, -4.4),
            (0, 0, 0, 0), (0, 4.4, 0, 4.45),
        ):
            with self.subTest(ball=(bx, by), pose=(px, py)):
                player = self.players[0]
                player.context = replace(
                    self.context, ball=BallState(bx, by),
                    teammates={1: RobotState(1, Pose2D(px, py))},
                )
                with patch.object(player, "walk_to") as walk:
                    player.wait_for_opponent_restart(OPPONENT_RESTART_AVOID_M)
                tx, ty = walk.call_args.args[0]
                self.assertLessEqual(abs(tx), 7)
                self.assertLessEqual(abs(ty), 4.5)
                self.assertGreater(math.hypot(tx - bx, ty - by), OPPONENT_RESTART_AVOID_M)
                self.assertGreaterEqual((tx - px) * (px - bx) + (ty - py) * (py - by), -1e-12)


if __name__ == "__main__":
    unittest.main()
