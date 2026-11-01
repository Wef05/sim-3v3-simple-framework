"""Emergency keeper priority and captured close-clearance regressions."""
import json
import unittest
from pathlib import Path
from unittest.mock import Mock
from checks.test_navigation import context
from src.actions import execute
from src.framework.config import SoccerConfig
from src.framework.types import Context, FieldDimensions, BallState, RobotState, Pose2D
from src.intents import MoveIntent, KickIntent, KickTarget
from src.player import Player
from src.utils.navigation import MotionTracker, NavigationFrame
from src.utils.goalkeeper import GuardMemory, close_clearance


class KeeperPriorityTests(unittest.TestCase):
    def test_lower_reservation_removes_only_extra_margin(self):
        for separation, priority, expected in ((.70, 0, None), (.70, 110, 'yield:team:2'), (.59, 0, 'team:2')):
            with self.subTest(separation=separation, priority=priority):
                ctx = context(((0, 0), (0, separation)))
                tracker = MotionTracker(); tracker.update(ctx)
                f = NavigationFrame(ctx, tracker)
                f.priorities = {1: 100, 2: priority}
                f.reserve(2, (0, 0, 0))
                self.assertEqual(f.check(1, MoveIntent((1, 0), motion_priority=100), (0, .1, 0)), expected)

    def test_conflicting_kick_cancelled_before_guard_moves(self):
        for order in ((1, 2), (2, 1)):
            with self.subTest(order=order):
                ctx = context(((1, 1), (0, 0)), ball=BallState(1, -1, last_seen_at=1))
                ps = [Player(i, SoccerConfig(), Mock(mode='walk', fall_down_state='normal')) for i in order]
                by_id = {p.id:p for p in ps}
                by_id[1].controller._kicking = True
                calls=[]
                by_id[1]._backend.release_kick.side_effect=lambda: calls.append('cancel')
                by_id[2]._backend.set_velocity.side_effect=lambda *args: calls.append('move')
                intents={1:KickIntent(KickTarget.goal()), 2:MoveIntent((2, 0),direct_path=True,motion_priority=100)}
                execute(ctx, ps, intents)
                by_id[1]._backend.kick.assert_not_called()
                self.assertEqual(by_id[1].controller.navigation_status['state'], 'kick_yield')
                self.assertIsInstance(intents[1], KickIntent)
                self.assertEqual(calls[0], 'cancel')

    def test_non_emergency_does_not_cancel_kick(self):
        ctx = context(((1, 1), (0, 0)), ball=BallState(1, -1,last_seen_at=1))
        ps = [Player(i, SoccerConfig(), Mock(mode='walk',fall_down_state='normal')) for i in (1,2)]
        execute(ctx,ps,{1:KickIntent(KickTarget.goal()),2:MoveIntent((2,0),direct_path=True,motion_priority=60)})
        ps[0]._backend.kick.assert_called_once()


class ClearanceReplayTests(unittest.TestCase):
    def test_locked_lane_reselection_preserves_timeout(self):
        frames={r['id']:r['packet'] for r in json.loads(Path(__file__).with_name('keeper-clearance-frames.json').read_text())}
        for fid, succeeds in ((367396,True),(367399,True),(370165,True),(364445,False)):
            with self.subTest(frame=fid):
                c=frames[fid]['context']
                def robots(side):
                    return {int(k):RobotState(**dict(v,pose=Pose2D(**v['pose']) if v['pose'] else None)) for k,v in c[side].items()}
                ctx=Context(c['now'],c['dt'],c['team_id'],FieldDimensions(**c['field']),ball=BallState(**c['ball']),teammates=robots('teammates'),opponents=robots('opponents'))
                old=tuple(next(p for p in frames[fid-1]['players'] if p['id']==2)['target'])
                started=ctx.now-.4
                memory=GuardMemory(plan=(old,'clear',None),close_clear=True,kick_since=started)
                result=close_clearance(ctx,2,ctx.teammates[2].pose,memory)
                self.assertEqual(result is not None,succeeds)
                self.assertEqual(memory.kick_since,started)
                if succeeds:
                    self.assertNotEqual(memory.plan[0],old)
                    # A valid replacement remains locked on the next identical frame.
                    selected=memory.plan
                    self.assertIsNotNone(close_clearance(ctx,2,ctx.teammates[2].pose,memory))
                    self.assertEqual(memory.plan,selected)

if __name__=='__main__': unittest.main()
