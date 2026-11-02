"""传射优势阈值与实际出球语义的离线回归。"""
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.framework.config import SoccerConfig
from src.framework.types import (Context, BallState, RobotState, Pose2D,
                                 ADULT_FIELD_DIMENSIONS, GameControlState, GameState, SetPlay)
from src.player import Player
from src.intents import KickIntent
from src.utils.pass_selection import select_pass
from src.utils.shot_selection import select_shot
from src.utils.scoring_probability import evaluate_scoring


class PassSelectionTests(unittest.TestCase):
    def setUp(self):
        self.context = Context(1, .1, 1, ADULT_FIELD_DIMENSIONS,
            game=GameControlState(state=GameState.PLAYING), ball=BallState(0, 0, 1),
            teammates={1: RobotState(1, Pose2D(-.3, 0), 1),
                       2: RobotState(2, Pose2D(2, 1), 1)})
        self.candidates = dict(evaluated_at=1, candidates=[dict(
            id='goal_3', kind='goal', target=(7, 0), probability=.7,
            status='ok', goal_center_steps=0)])
        self.scoring = dict(evaluated_at=1, via_teammate=[dict(
            player_id=2, pass_probability=.8, shot_probability=.75,
            probability=.6, receive_target=(2, 1), target=(7, 0))])

    def choose(self, receivers=(2,)):
        shot = select_shot(self.context.field, self.candidates, ((0, 0),))
        return select_pass(self.context, self.candidates, self.scoring, shot, ((0, 0),), receivers)

    def test_threshold_boundaries(self):
        for risk, kind in ((.699, 'shot'), (.7, 'pass'), (.701, 'pass')):
            with self.subTest(risk=risk):
                self.candidates['candidates'][0]['probability'] = risk
                self.assertEqual(self.choose()['selected']['kind'], kind)

    def test_both_legs_count(self):
        self.scoring['via_teammate'][0]['pass_probability'] = .5
        self.assertEqual(self.choose()['selected']['kind'], 'shot')
        self.assertAlmostEqual(self.choose()['best_pass_risk'], .625)

    def test_invalid_and_unavailable_receivers(self):
        self.assertEqual(self.choose(())['selected']['kind'], 'shot')
        for value in (None, float('nan'), 1.1):
            self.scoring['via_teammate'][0]['shot_probability'] = value
            self.assertEqual(self.choose()['selected']['kind'], 'shot')

    def test_unknown_direct_and_stale_data_do_not_trigger_pass(self):
        self.candidates['candidates'][0]['status'] = 'partial'
        self.assertNotEqual((self.choose()['selected'] or {}).get('kind'), 'pass')
        self.candidates['candidates'][0]['status'] = 'ok'
        self.context = replace(self.context, now=1.6)
        self.assertEqual(self.choose()['selected']['kind'], 'shot')

    def test_penalty_does_not_inflate_risk_improvement(self):
        self.candidates['candidates'][0].update(probability=.6, goal_center_steps=3)
        self.assertEqual(self.choose()['selected']['kind'], 'shot')
        self.assertAlmostEqual(self.choose()['risk_improvement'], .2)

    def test_best_receiver_and_stable_tie(self):
        self.context.teammates[3] = RobotState(3, Pose2D(3, 1), 1)
        self.scoring['via_teammate'].insert(0, dict(self.scoring['via_teammate'][0], player_id=3))
        self.assertEqual(self.choose((2, 3))['selected']['player_id'], 2)
        self.scoring['via_teammate'][0]['shot_probability'] = .9
        self.assertEqual(self.choose((2, 3))['selected']['player_id'], 3)

    def test_real_attack_uses_pass_style_and_preserves_explicit_target(self):
        p = Player(1, SoccerConfig(), None)
        p.context = self.context
        p.motion.update(self.context)
        p.guard_receivers = (1, 2)
        p.ball_intercept = dict(status='intercept', intercept_time_s=0, target=(0, 0))
        with patch('src.player.evaluate_attack_targets', return_value=self.candidates), \
             patch('src.player.evaluate_scoring', return_value=self.scoring), \
             patch('src.player.estimate_interception_risk', return_value={}):
            intent = p.attack()
            self.assertIsInstance(intent, KickIntent)
            self.assertEqual(intent.style, 'pass')
            self.assertEqual(intent.target.point, (2, 1))
            self.assertEqual(p.attack((7, 0)).style, 'shot')
            self.assertFalse(p.scoring_probability['decision_enabled'])

    def test_special_phase_and_receiver_loss_preserve_shot(self):
        p = Player(1, SoccerConfig(), None)
        p.context = self.context
        p.motion.update(self.context)
        p.guard_receivers = (1, 2)
        p.ball_intercept = dict(status='intercept', intercept_time_s=0, target=(0, 0))
        with patch('src.player.evaluate_attack_targets', return_value=self.candidates), \
             patch('src.player.evaluate_scoring', return_value=self.scoring) as scoring, \
             patch('src.player.estimate_interception_risk', return_value={}):
            self.assertEqual(p.attack().style, 'pass')
            p.guard_receivers = (1,)
            self.assertEqual(p.attack().style, 'shot')
            self.assertEqual(scoring.call_count, 2)
            p.guard_receivers = (1, 2)
            p.context = replace(self.context, game=replace(self.context.game,
                set_play=SetPlay.PENALTY_KICK, kicking_team=1))
            self.assertEqual(p.attack().style, 'shot')
            self.assertFalse(p.scoring_probability['decision_enabled'])

    def test_scoring_includes_non_freeman_receiver(self):
        candidates = dict(self.candidates, origin=(0, 0), kick_delay_s=0,
            candidates=self.candidates['candidates']+[dict(player_id=2, kind='teammate',
                target=(2, 1), status='ok', probability=.2)])
        with patch('src.utils.scoring_probability.estimate_interception_risk',
                   return_value=dict(status='ok', probability=.25)):
            result = evaluate_scoring(self.context, Pose2D(), None, candidates, (), (2,))
        self.assertEqual(result['via_freeman'], [])
        self.assertAlmostEqual(result['via_teammate'][0]['probability'], .6)


if __name__ == '__main__':
    unittest.main()
