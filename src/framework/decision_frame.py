"""Refresh common observations before the three-stage strategy pipeline."""
from ..utils.ball_motion import BallMotion
from ..utils.interception import estimate_ball_intercept

def prepare_frame(context, players, store):
    if not hasattr(store, 'ball_motion'):
        store.ball_motion = BallMotion()
    store.ball_motion.update(context)
    for p in players:
        p.context = context
        p.motion = store.ball_motion
        p.ball_intercept = estimate_ball_intercept(
            p.pose, store.ball_motion, fallen=p.is_fallen, penalized=p.is_penalized)
        p.freeman_region = None
        p.tactical_target = None
        p.controller.tactical_target = None
