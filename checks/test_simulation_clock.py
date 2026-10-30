"""仿真计时集成检查；不需要 ROS 或机器人 SDK。"""
import importlib
import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from src.framework.config import SoccerConfig
from src.framework.runtime import SoccerRuntime
from src.framework.types import BallState, Context, ADULT_FIELD_DIMENSIONS, WorldSnapshot
from src.utils.ball_motion import BallMotion


class SimulationClockTests(unittest.TestCase):
    def runtime(self):
        agent = NS(config=SoccerConfig(), player_class=lambda **kw: NS(
            controller=NS(stop=Mock(), kick_observation=None)), init_store=Mock(), play=Mock())
        source = NS(get_snapshot=Mock(return_value=WorldSnapshot(sim_time=0)))
        runtime = SoccerRuntime(agent, source)
        runtime._draw_world = Mock()
        self.prep = patch('src.framework.decision_frame.prepare_frame').start()
        patch('src.visualization.finish_frame').start()
        self.addCleanup(patch.stopall)
        return runtime, source, agent

    def test_no_clock_never_uses_wall_time(self):
        runtime, source, agent = self.runtime()
        source.get_snapshot.return_value = WorldSnapshot()
        runtime._tick(9999)
        agent.play.assert_not_called()
        runtime._players[0].controller.stop.assert_called_once()

    def test_pause_and_speed_use_only_simulation_interval(self):
        runtime, source, agent = self.runtime()
        for sim, wall, dt in [(0,100,0), (.1,110,.1), (.1,120,0), (.3,120.01,.2)]:
            source.get_snapshot.return_value = WorldSnapshot(sim_time=sim)
            runtime._tick(wall)
            ctx = agent.play.call_args.args[0]
            self.assertEqual(ctx.now, sim)
            self.assertAlmostEqual(ctx.dt, dt)

    def test_reset_epoch_clears_prediction_even_if_time_caught_up(self):
        runtime, source, agent = self.runtime()
        runtime._tick(100)
        runtime._store.ball_motion = object()
        runtime._store.teammate_motion = object()
        runtime._store.kick_error_tracker = object()
        runtime._players[0]._candidate_cache = {'evaluated_at': 0}
        source.get_snapshot.return_value = WorldSnapshot(sim_time=1, clock_epoch=1)
        runtime._tick(101)
        self.assertFalse(hasattr(runtime._store, 'ball_motion'))
        self.assertFalse(hasattr(runtime._players[0], '_candidate_cache'))
        self.assertEqual(agent.play.call_args.args[0].dt, 0)

    def test_transport_expiry_is_separate_from_simulation_age(self):
        runtime, source, agent = self.runtime()
        ball = BallState(0, 0, last_seen_at=0, received_at=100)
        snap = WorldSnapshot(ball=ball, sim_time=0)
        self.assertIsNotNone(runtime._build_context(0,0,snap,101).ball)
        self.assertIsNone(runtime._build_context(0,0,snap,102).ball)

    def test_velocity_uses_simulation_stamps_including_zero(self):
        results = []
        for wall_step in (.01, 10):
            motion = BallMotion()
            for i in range(3):
                ctx = Context(i*.1, .1, 1, ADULT_FIELD_DIMENSIONS,
                    ball=BallState(i*.1, 0, i*.1, received_at=100+i*wall_step))
                motion.update(ctx)
            self.assertAlmostEqual(motion._ball_velocity[0], 1)
            before = motion.predict_ball_position(0)
            motion.update(ctx)
            self.assertEqual(before, motion.predict_ball_position(0))
            results.append(motion.predict_ball_position(.2))
        self.assertEqual(results[0], results[1])

    def test_ros_callbacks_stamp_and_reset_in_simulation_domain(self):
        modules = {
            'rclpy': NS(), 'geometry_msgs.msg': NS(Pose2D=object),
            'rclpy.executors': NS(ExternalShutdownException=Exception, SingleThreadedExecutor=object),
            'rclpy.qos': NS(QoSDurabilityPolicy=object, QoSHistoryPolicy=object,
                           QoSProfile=object, QoSReliabilityPolicy=object),
            'std_msgs.msg': NS(String=object), 'rosgraph_msgs.msg': NS(Clock=object),
            'rclpy.parameter': NS(Parameter=object),
        }
        with patch.dict(sys.modules, modules):
            # Avoid leaving mocked ROS dependencies cached for other tests.
            name = 'src.framework.ros_source'
            old = sys.modules.pop(name, None)
            try:
                mod = importlib.import_module(name)
                source = mod.RosContextSource(SoccerConfig())
                msg = NS(x=1,y=2,theta=0)
                source._ball_cb(msg)
                self.assertIsNone(source.get_snapshot().ball)
                source._clock_cb(NS(clock=NS(sec=0,nanosec=0)))
                source._ball_cb(msg)
                source._make_pose_cb(source._teammates,1)(msg)
                self.assertEqual(source.get_snapshot().ball.last_seen_at, 0)
                self.assertIsNotNone(source.get_snapshot().ball.received_at)
                source._clock_cb(NS(clock=NS(sec=5,nanosec=0)))
                source._clock_cb(NS(clock=NS(sec=0,nanosec=0)))
                snap = source.get_snapshot()
                self.assertIsNone(snap.ball)
                self.assertIsNone(snap.teammates[1].pose)
                self.assertEqual(snap.clock_epoch, 1)
            finally:
                sys.modules.pop(name, None)
                if old is not None:
                    sys.modules[name] = old

if __name__ == '__main__':
    unittest.main()
