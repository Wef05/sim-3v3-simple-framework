"""传射门槛、时间积分和真实决策接入的回归检查。"""
import math
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from src.framework.config import SoccerConfig
from src.framework.types import (Context, BallState, RobotState, Pose2D, ADULT_FIELD_DIMENSIONS,
                                 GameControlState, GameState)
from src.framework.decision_frame import prepare_frame
from src.intents import KickIntent, KickTarget, MoveIntent, Role, RoleTask, StopIntent
from src.player import Player, decide
from src.utils.ball_motion import BallMotion
from src.utils.scoring_probability import (best_shot, choose_route, kick_preparation,
                                           evaluate_scoring, fallback_shot)
from src.utils.attack_targets import evaluate_attack_targets, TeammateMotion
from src.utils.interception_risk import estimate_interception_risk
from src.utils.pass_reception import coordinate_reception


def context():
    return Context(10, .03, 1, ADULT_FIELD_DIMENSIONS,
        game=GameControlState(state=GameState.PLAYING), ball=BallState(1, 0, 10),
        teammates={1: RobotState(1, Pose2D(.65, 0), 10),
                   2: RobotState(2, Pose2D(-6, 0), 10),
                   3: RobotState(3, Pose2D(3, 2), 10)})


def scoring(direct=.4, passing=.61):
    return dict(evaluated_at=10, decision_enabled=False,
        direct=dict(probability=direct, target=(7, 0), plan=None),
        via_freeman=[dict(player_id=3, probability=passing, pass_probability=.9,
                         shot_probability=passing/.9, receive_target=(3, 2), target=(7, .3),
                         pass_plan=None, receive_time_s=2)])


class ProbabilityAttackTests(unittest.TestCase):
    def test_strict_absolute_advantage(self):
        for p, kind in ((.59, 'shot'), (.6, 'shot'), (.6000001, 'pass')):
            self.assertEqual(choose_route(scoring(passing=p), (3,))['kind'], kind)

    def test_unknown_direct_and_receiver_unavailable(self):
        self.assertIsNone(choose_route(scoring(direct=None), (3,)))
        self.assertEqual(choose_route(scoring(), ())['kind'], 'shot')

    def test_edge_penalty_and_best_point(self):
        shots = [dict(target=(7, y), probability=risk, status='ok')
                 for y, risk in ((-1, 0), (0, .05), (1, 0))]
        result = best_shot(shots)
        self.assertEqual(result['target'], (7, 0))
        self.assertEqual(result['shots'][0]['probability'], .9)
        self.assertEqual(result['shots'][1]['edge_penalty'], 0)
        shots[1]['probability'] = .2
        self.assertEqual(best_shot(shots)['target'], (7, -1))

    def test_unknown_and_blocked_are_distinct(self):
        shots = [dict(target=(7, y), probability=None, status='partial') for y in (-1, 0, 1)]
        self.assertIsNone(best_shot(shots)['probability'])
        for shot in shots:
            shot['status'] = 'blocked'
        self.assertEqual(best_shot(shots)['probability'], 0)

    def test_turn_and_detour_time(self):
        ctx = context()
        aligned = kick_preparation(ctx, 1, Pose2D(.65, 0, 0), (1, 0), (7, 0), 0)
        reverse = kick_preparation(ctx, 1, Pose2D(.65, 0, math.pi), (1, 0), (7, 0), 0)
        self.assertGreater(reverse['kick_time_s'], aligned['kick_time_s']+3)
        ctx = replace(ctx, opponents={1: RobotState(1, Pose2D(-1, 0))})
        detour = kick_preparation(ctx, 1, Pose2D(-3, 0), (1, 0), (7, 0), 0)
        self.assertGreater(len(detour['path']), 2)
        self.assertGreater(detour['travel_s'], aligned['travel_s'])

    def test_preparation_exposes_ball_to_interception(self):
        ctx = replace(context(), opponents={1: RobotState(1, Pose2D(1, 1, -math.pi/2))})
        motion = BallMotion(); motion.update(ctx)
        def predictor(*args, **kwargs):
            return [dict(position=(1, 1), heading=-math.pi/2, radius_m=.001,
                         weight=1., strategy='chase_ball')]
        args = (Pose2D(.65, 0), motion, ctx.opponents, (7, 0),
                dict(status='intercept', intercept_time_s=0))
        instant = estimate_interception_risk(*args, predictor=predictor, samples=8)
        prepared = estimate_interception_risk(*args, predictor=predictor, samples=8, preparation_s=1)
        self.assertGreater(prepared['probability'], instant['probability'])
        self.assertEqual(prepared['path'][0]['probability'], 1)
        self.assertEqual(len(prepared['path']), len(instant['path']))

    def test_full_route_has_preparation_on_both_kicks(self):
        ctx = context(); motion = BallMotion(); motion.update(ctx)
        tracker = TeammateMotion(); tracker.update(ctx)
        intercept = dict(status='intercept', intercept_time_s=0)
        candidates = evaluate_attack_targets(ctx, 1, ctx.teammates[1].pose, motion, intercept, tracker)
        result = evaluate_scoring(ctx, ctx.teammates[1].pose, motion, candidates, (3,),
                                 player_id=1, intercept=intercept, teammate_motion=tracker)
        row = result['via_freeman'][0]
        self.assertIsNotNone(row['probability'])
        self.assertGreaterEqual(row['pass_plan']['preparation_s'], .6)
        self.assertGreaterEqual(row['shot_plan']['preparation_s'], .6)
        self.assertEqual(row['probability'], row['pass_probability']*row['shot_probability'])
        self.assertEqual(result['direct']['target'], (7, 0))

    def player(self):
        p = Player(1, SoccerConfig(), SimpleNamespace(mode='walk', fall_down_state='normal'))
        prepare_frame(context(), [p], SimpleNamespace())
        p.evaluation_receiver_ids = (3,)
        return p

    def test_pass_and_shot_reach_real_intents(self):
        for probability, style, target in ((.6, 'shot', (7, 0)), (.61, 'pass', (3, 2))):
            p = self.player()
            with patch('src.player.evaluate_scoring', return_value=scoring(passing=probability)):
                intent = p.attack()
            self.assertIsInstance(intent, KickIntent)
            self.assertEqual(intent.style, style)
            self.assertEqual(intent.target.point, target)
            self.assertTrue(p.attack_choice['enabled'])

    def test_unknown_shoots_and_explicit_kickoff_keeps_semantics(self):
        p = self.player()
        with patch('src.player.evaluate_scoring', return_value=scoring(direct=None)):
            fallback = p.attack()
            self.assertIsInstance(fallback, KickIntent)
            self.assertEqual(fallback.target.point, (7, 0))
            self.assertEqual(fallback.style, 'shot')
            self.assertIsNone(p.attack_choice['selected']['probability'])
            intent = p.attack(3)
        self.assertEqual(intent.target, KickTarget.teammate(3))
        self.assertEqual(intent.style, 'pass')

    def test_zero_score_still_shoots_best_known_point(self):
        p = self.player()
        scores = scoring(direct=0, passing=0)
        scores['direct']['shots'] = [dict(target=(7, y), probability=value)
            for y, value in ((-1, None), (0, None), (.4, 0))]
        with patch('src.player.evaluate_scoring', return_value=scores):
            intent = p.attack()
        self.assertEqual(intent.target.point, (7, .4))
        self.assertEqual(intent.style, 'shot')
        self.assertEqual(p.action, 'attack:shot_fallback')

    def test_unknown_fallback_prefers_center_and_checks_both_origins(self):
        scores = scoring(direct=None)
        scores['direct']['shots'] = [dict(target=(7, y), probability=None) for y in (-.7, 0, .7)]
        self.assertEqual(fallback_shot(scores, context().field, ((1, 0),))['target'], (7, 0))
        with patch('src.utils.kick_lane.check_kick_lane',
                   side_effect=lambda field, origin, target: dict(blocked=origin == (1, 1) and target[1] == 0)):
            choice = fallback_shot(scores, context().field, ((1, 0), (1, 1)))
        self.assertEqual(choice['target'], (7, -.7))

    def test_fully_blocked_shot_still_stops(self):
        p = self.player()
        with patch('src.player.evaluate_scoring', return_value=scoring(direct=None)), \
             patch('src.utils.kick_lane.check_kick_lane', return_value=dict(blocked=True)):
            self.assertIsInstance(p.attack(), StopIntent)
        self.assertEqual(p.action, 'attack:no_safe_shot')

    def test_far_unknown_route_keeps_chasing_for_shot(self):
        p = self.player()
        p.context = replace(p.context, ball=BallState(4, 0, 10))
        p.motion.update(p.context)
        p.ball_intercept = dict(status='intercept', target=(4, 0), intercept_time_s=1)
        with patch('src.player.evaluate_scoring', return_value=scoring(direct=None)):
            intent = p.attack()
        self.assertIsInstance(intent, MoveIntent)
        self.assertEqual(p.action, 'attack:shot_fallback')
        self.assertEqual(p.attack_choice['selected']['kind'], 'shot')

    def test_current_blocked_pass_uses_shot_fallback(self):
        p = self.player()
        with patch('src.player.evaluate_scoring', return_value=scoring()), \
             patch('src.player.check_kick_lane',
                   side_effect=lambda field, origin, target: dict(blocked=target == (3, 2))):
            intent = p.attack()
        self.assertEqual(intent.style, 'shot')
        self.assertEqual(intent.target.point, (7, 0))

    def test_changed_receiver_set_invalidates_cache(self):
        p = self.player()
        with patch('src.player.evaluate_scoring', return_value=scoring()) as evaluate:
            self.assertEqual(p.attack().style, 'pass')
            p.evaluation_receiver_ids = ()
            self.assertEqual(p.attack().style, 'shot')
            self.assertEqual(evaluate.call_count, 2)

    def test_missing_opponent_never_becomes_zero_risk(self):
        ctx = replace(context(), opponents={1: RobotState(1)})
        motion = BallMotion(); motion.update(ctx)
        tracker = TeammateMotion(); tracker.update(ctx)
        intercept = dict(status='intercept', intercept_time_s=0)
        candidates = evaluate_attack_targets(ctx, 1, ctx.teammates[1].pose, motion, intercept, tracker)
        result = evaluate_scoring(ctx, ctx.teammates[1].pose, motion, candidates, (3,),
                                 player_id=1, intercept=intercept)
        self.assertIsNone(choose_route(result, (3,)))

    def test_receiving_request_memory_clears_on_phase_and_clock_change(self):
        ctx = context()
        p = SimpleNamespace(id=1, attack_choice=dict(selected=dict(kind='pass', player_id=3, target=(3, 2))))
        receiver = SimpleNamespace(id=3, pose=ctx.teammates[3].pose, is_fallen=False,
                                   is_penalized=False, mode='walk')
        tasks = {1: RoleTask(Role.ATTACKER), 3: RoleTask(Role.FREEMAN)}
        intents = {1: KickIntent(KickTarget.position((3, 2)), style='pass'), 3: StopIntent()}
        store = SimpleNamespace()
        out = coordinate_reception(ctx, [p, receiver], tasks, intents, store)
        self.assertIsInstance(out[3], MoveIntent)
        self.assertIsInstance(intents[3], StopIntent)
        coordinate_reception(replace(ctx, now=9), [p, receiver], tasks, intents, store)
        self.assertIsNone(store.pending_pass)
        coordinate_reception(ctx, [p, receiver], tasks, intents, store)
        coordinate_reception(replace(ctx, game=replace(ctx.game, state=GameState.FINISHED)),
                             [p, receiver], tasks, intents, store)
        self.assertIsNone(store.pending_pass)


if __name__ == '__main__':
    unittest.main()
