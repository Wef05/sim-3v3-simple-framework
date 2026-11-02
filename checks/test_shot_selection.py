"""射门排序及真实攻击意图回归，不依赖 SDK。"""
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.framework.config import SoccerConfig
from src.framework.types import Context, BallState, RobotState, Pose2D, ADULT_FIELD_DIMENSIONS
from src.intents import KickIntent, KickTarget, MoveIntent
from src.player import Player
from src.utils.shot_selection import select_shot
from src.utils.attack_targets import evaluate_attack_targets, TeammateMotion
from src.utils.geom import behind_ball
from src.param import CHASE_BEHIND_M


def candidates(risks, statuses=None):
    return dict(evaluated_at=0, candidates=[dict(
        id=f'goal_{i}', kind='goal', target=(7, (i-3)*.35),
        goal_edge=i in (0, 6), status=(statuses or {}).get(i, 'ok'), probability=risk)
        for i, risk in enumerate(risks)])


def player(pose=(2.7, 0), ball=(3, 0)):
    p = Player(3, SoccerConfig(), None)
    p.context = Context(0, .1, 1, ADULT_FIELD_DIMENSIONS, ball=BallState(*ball),
                        teammates={3: RobotState(3, Pose2D(*pose))})
    p.motion.update(p.context)
    p.ball_intercept = dict(status='intercept', target=ball, intercept_time_s=0)
    return p


class ShotSelectionTests(unittest.TestCase):
    def choose(self, data):
        return select_shot(ADULT_FIELD_DIMENSIONS, data, ((3, 0), (3, 0)))

    def test_edge_penalty_changes_winner(self):
        result = self.choose(candidates([.2, .9, .9, .25, .9, .9, .9]))
        self.assertEqual(result['selected']['id'], 'goal_3')
        self.assertAlmostEqual(result['options'][0]['probability'], .7)
        self.assertEqual(result['options'][3]['penalty'], 0)

    def test_edge_still_wins_when_better_after_penalty(self):
        result = self.choose(candidates([.05, .9, .9, .3, .9, .9, .9]))
        self.assertEqual(result['selected']['id'], 'goal_0')
        self.assertAlmostEqual(result['selected']['probability'], .85)

    def test_tie_prefers_center_including_float_tail(self):
        result = self.choose(candidates([.2, .9, .9, .3, .9, .9, .9]))
        self.assertEqual(result['selected']['id'], 'goal_3')

    def test_partial_and_blocked_do_not_hide_known_candidate(self):
        data = candidates([0, 0, .8, .4, .8, .8, .8], {0:'partial', 1:'blocked'})
        self.assertEqual(self.choose(data)['selected']['id'], 'goal_3')

    def test_invalid_and_zero_scores_use_center(self):
        for risks in ([None]*7, [1]*7, [float('nan')]*7, [-1]*7):
            result = self.choose(candidates(risks))
            self.assertIsNone(result['selected'])
            self.assertEqual(result['actual_target'], (7, 0))
        self.assertEqual(self.choose(candidates([.95]*7))['options'][0]['probability'], 0)

    def test_both_current_origins_checked(self):
        result = select_shot(ADULT_FIELD_DIMENSIONS, candidates([0]*7), ((3,0),(7,1.3)))
        self.assertIsNone(result['selected'])

    def test_generated_candidates_mark_only_endpoints(self):
        p = player(); tracker = TeammateMotion(); tracker.update(p.context)
        data = evaluate_attack_targets(p.context, p.id, p.pose, p.motion, p.ball_intercept, tracker)
        goals = [c for c in data['candidates'] if c['kind']=='goal']
        self.assertEqual([c['id'] for c in goals if c['goal_edge']], ['goal_0','goal_6'])

    def attack(self, p, data, target=0):
        with patch('src.player.evaluate_attack_targets', return_value=data), patch(
                'src.player.evaluate_scoring', return_value={'evaluated_at':0}):
            return p.attack(target)

    def test_selected_point_reaches_kick_and_chase(self):
        data = candidates([.05,.9,.9,.3,.9,.9,.9])
        for pose in ((2.7,0), (0,0)):
            p = player(pose); intent = self.attack(p, data)
            if isinstance(intent, KickIntent):
                self.assertEqual(intent.target, KickTarget.position(data['candidates'][0]['target']))
                self.assertEqual(intent.style, 'shot')
            else:
                self.assertIsInstance(intent, MoveIntent)
                self.assertEqual(intent.target, behind_ball(3,0,data['candidates'][0]['target'],CHASE_BEHIND_M))
            self.assertEqual(p.attack_choice['actual_target'], data['candidates'][0]['target'])

    def test_no_route_kicks_center(self):
        p = player(); intent = self.attack(p, candidates([1]*7))
        self.assertEqual(intent.target, KickTarget.goal())
        self.assertEqual(p.action, 'attack:center_fallback')

    def test_explicit_targets_bypass_selection(self):
        p = player(); intent = self.attack(p, candidates([0]*7), (5,1))
        self.assertEqual(intent.target, KickTarget.position((5,1)))
        self.assertIsNone(p.attack_choice)

    def test_cache_reused_and_clock_rollback_refreshes(self):
        p=player(); data=candidates([.05,.9,.9,.3,.9,.9,.9])
        with patch('src.player.evaluate_attack_targets', return_value=data) as evaluate, patch(
                'src.player.evaluate_scoring', return_value={'evaluated_at':0}):
            p.attack(); p.attack()
            self.assertEqual(evaluate.call_count, 1)
            p.context=replace(p.context, now=-1)
            p.attack()
            self.assertEqual(evaluate.call_count, 2)

if __name__ == '__main__':
    unittest.main()
