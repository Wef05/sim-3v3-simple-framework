import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_fixed_goalkeeper import strategy
from src.framework.config import SoccerConfig
from src.framework.types import (
    ADULT_FIELD_DIMENSIONS, BallState, Context, GameControlState,
    GameState, Pose2D, RobotState, SetPlay,
)
from src.player import Player


class GoalAreaDistributionTests(unittest.TestCase):
    def setUp(self):
        self.players = [Player(i, SoccerConfig(), Mock(mode='walk', fall_down_state='normal'))
                        for i in (2, 3)]
        self.ctx = Context(now=0, dt=1/30, team_id=1, field=ADULT_FIELD_DIMENSIONS,
                           ball=BallState(-6, 0),
                           teammates={2: RobotState(2, Pose2D(-6.4, 0)),
                                      3: RobotState(3, Pose2D(-3, 0))},
                           opponents={1: RobotState(1, Pose2D(4, 3))})
        self.store = SimpleNamespace(normal_attacker=None, restart_plan=None)
        self.bind()

    def bind(self):
        for player in self.players:
            player.context = self.ctx

    def test_outfield_player_uses_same_safe_pass(self):
        self.assertTrue(self.players[0].distribute_goal_area_ball(self.players))
        self.assertEqual(self.players[0].action, 'goal_area:pass')

    def test_clearance_is_not_overwritten_by_attack_or_support(self):
        self.ctx = replace(self.ctx, opponents={1: RobotState(1, Pose2D(-5, 0))})
        self.bind()
        player = self.players[0]
        with patch.object(player, 'attack') as attack, patch.object(player, 'support') as support:
            strategy._act_normal(self.ctx, self.players, self.store)
        self.assertEqual(player.action, 'goal_area:clear')
        player._backend.kick.assert_called_once()
        attack.assert_not_called()
        support.assert_not_called()

    def test_area_bounds_use_player_position_and_include_lines(self):
        for x, y, expected in ((-7, 0, True), (-6, 2, True), (-6, -2, True),
                               (-5.99, 0, False), (-6.5, 2.01, False),
                               (-7.01, 0, False), (6.5, 0, False)):
            with self.subTest(x=x, y=y):
                self.ctx = replace(self.ctx, ball=BallState(x+0.2, y),
                                   teammates={2: RobotState(2, Pose2D(x, y))})
                self.bind()
                self.assertEqual(self.players[0].distribute_goal_area_ball([]), expected)

    def test_ball_in_area_does_not_include_player_outside(self):
        self.ctx = replace(self.ctx, ball=BallState(-6.1, 0),
                           teammates={2: RobotState(2, Pose2D(-5.9, 0))})
        self.bind()
        self.assertFalse(self.players[0].distribute_goal_area_ball([]))

    def test_no_nearby_ball_does_not_override_role(self):
        self.ctx = replace(self.ctx, ball=BallState(-4, 0))
        self.bind()
        self.assertFalse(self.players[0].distribute_goal_area_ball([]))

    def test_restart_and_stopped_states_block_distribution(self):
        for game in (GameControlState(state=GameState.READY),
                     GameControlState(state=GameState.PLAYING, set_play=SetPlay.GOAL_KICK),
                     GameControlState(state=GameState.PLAYING, secondary_time=5),
                     GameControlState(state=GameState.PLAYING, stopped=True)):
            with self.subTest(game=game):
                self.ctx = replace(self.ctx, game=game)
                self.bind()
                self.assertFalse(self.players[0].distribute_goal_area_ball(self.players))
                self.players[0]._backend.kick.assert_not_called()

    def test_all_outfield_players_can_clear_in_same_frame(self):
        self.ctx = replace(self.ctx, opponents={}, teammates={
            2: RobotState(2, Pose2D(-6.3, 0.1)),
            3: RobotState(3, Pose2D(-6.3, -0.1)),
        })
        self.bind()
        strategy._act_normal(self.ctx, self.players, self.store)
        self.assertIsNone(self.store.normal_attacker)
        for player in self.players:
            self.assertEqual(player.action, 'goal_area:clear')
            player._backend.kick.assert_called_once()
