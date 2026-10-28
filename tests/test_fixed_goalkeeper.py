import importlib
import sys
import types
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from src.framework.types import (
    ADULT_FIELD_DIMENSIONS, BallState, Context, GameControlState, Pose2D, SetPlay,
)
from src.player import Player


sdk = types.ModuleType("booster_agent_framework")
sdk.AgentBase = type("AgentBase", (), {})
sdk.AgentFeatures = object
with patch.dict(sys.modules, {"booster_agent_framework": sdk}):
    strategy = importlib.import_module("src.main")


class FixedGoalkeeperTests(unittest.TestCase):
    def setUp(self):
        self.context = Context(
            now=0, dt=1 / 30, team_id=1, field=ADULT_FIELD_DIMENSIONS,
            ball=BallState(0, 0), game=GameControlState(kicking_team=1),
        )
        # 守门员最近球,2 号最近己方门,检验分工不会再由距离互换。
        self.players = [
            Mock(spec=Player, id=1, pose=Pose2D(0.1, 0)),
            Mock(spec=Player, id=2, pose=Pose2D(-6, 0)),
            Mock(spec=Player, id=3, pose=Pose2D(2, 0)),
        ]
        for player in self.players:
            player.distribute_goal_area_ball.return_value = False
        self.store = types.SimpleNamespace()
        strategy.SoccerSimAgent.init_store(None, self.store)

    def assert_goalkeeper_only_guards(self):
        self.players[0].guard.assert_called_once()
        self.players[0].attack.assert_not_called()
        self.players[0].kick.assert_not_called()
        self.players[0].support.assert_not_called()
        self.players[1].guard.assert_not_called()
        self.players[2].guard.assert_not_called()

    def test_normal_keeps_fixed_keeper_even_when_nearest_ball(self):
        self.store.normal_attacker = 1  # 旧的进攻角色记忆也必须被排除。
        strategy._act_normal(self.context, self.players, self.store)
        self.assert_goalkeeper_only_guards()
        self.assertEqual(self.store.normal_attacker, 3)
        self.players[2].attack.assert_called_once()
        self.players[1].support.assert_called_once()

    def test_our_kickoff_excludes_keeper_from_sticky_taker(self):
        self.store.prev_phase = strategy.Phase.OUR_KICKOFF
        self.store.kickoff_taker = 1
        strategy._act_our_kickoff(self.context, self.players, self.store)
        self.assert_goalkeeper_only_guards()
        self.assertEqual(self.store.kickoff_taker, 3)
        self.players[2].kick.assert_not_called()
        self.players[1].walk_to.assert_called_once()
        self.assertEqual(self.store.restart_plan.receiver, 2)

    def test_our_set_plays_exclude_keeper(self):
        for set_play in SetPlay:
            if set_play == SetPlay.NONE:
                continue
            with self.subTest(set_play=set_play):
                for p in self.players:
                    p.reset_mock()
                context = replace(self.context, game=replace(
                    self.context.game, set_play=set_play,
                ))
                strategy._act_our_set_play(context, self.players, self.store)
                self.assert_goalkeeper_only_guards()
                if set_play == SetPlay.PENALTY_KICK:
                    self.players[2].attack.assert_called_once()
                else:
                    self.players[2].attack.assert_not_called()
                    self.players[1].walk_to.assert_called_once()
                self.assertEqual(self.store.restart_plan.taker, 3)

    def test_only_goalkeeper_available_does_not_attack_or_take_kickoff(self):
        strategy._act_normal(self.context, self.players[:1], self.store)
        strategy._act_our_kickoff(self.context, self.players[:1], self.store)
        self.players[0].attack.assert_not_called()
        self.players[0].kick.assert_not_called()
        self.assertEqual(self.players[0].guard.call_count, 2)
        self.assertIsNone(self.store.normal_attacker)
        self.assertIsNone(self.store.kickoff_taker)

    def test_unavailable_keeper_does_not_replace_outfield_roles(self):
        for action in (strategy._act_normal, strategy._act_our_kickoff):
            with self.subTest(action=action.__name__):
                for p in self.players:
                    p.reset_mock()
                action(self.context, self.players[1:], self.store)
                self.players[1].guard.assert_not_called()
                self.players[2].guard.assert_not_called()
                self.players[0].guard.assert_not_called()
                self.assertEqual(
                    self.store.normal_attacker if action == strategy._act_normal
                    else self.store.kickoff_taker, 3,
                )

    def test_ready_sends_same_keeper_home_for_either_kicking_team(self):
        for team in (1, 2):
            with self.subTest(team=team):
                for p in self.players:
                    p.reset_mock()
                context = replace(self.context, game=replace(
                    self.context.game, kicking_team=team,
                ))
                strategy._act_ready(context, list(reversed(self.players)))
                self.players[0].walk_to.assert_called_once_with(
                    (-6.5, 0), face=0.0, avoid_ball=True, avoid_robots=True,
                )
                self.assertEqual(self.players[0].action, "ready:guard")
                self.assertEqual(self.players[1].walk_to.call_args.args[0][0],
                                 -1.5 if team == 1 else -2.0)

    def test_goalkeeper_id_parameter_changes_all_role_assignment(self):
        with patch.object(strategy, "GOALKEEPER_ID", 3):
            strategy._act_normal(self.context, self.players, self.store)
            self.assertEqual(self.store.normal_attacker, 1)
            self.players[2].guard.assert_called_once()
            self.players[0].attack.assert_called_once()
            strategy._act_our_kickoff(self.context, self.players, self.store)
            self.assertEqual(self.store.kickoff_taker, 1)
            self.players[2].kick.assert_not_called()
            strategy._act_ready(self.context, self.players)
            self.assertEqual(self.players[2].walk_to.call_args.args[0], (-6.5, 0))

    def test_no_active_players_clears_stale_attack_roles(self):
        self.store.normal_attacker = 2
        self.store.kickoff_taker = 3
        strategy._act_normal(self.context, [], self.store)
        strategy._act_our_kickoff(self.context, [], self.store)
        self.assertIsNone(self.store.normal_attacker)
        self.assertIsNone(self.store.kickoff_taker)


if __name__ == "__main__":
    unittest.main()
