"""整队共享的有状态观测工具：保留球历史并进行二维匀速外推。

状态仅用于观测估计，不持有角色或机器人控制权。update 推进观测，predict 只读，
保证一帧中角色分配、追球和看板多次查询时，不会重复更新速度或消耗观测。
未估计出速度时按静止外推，并通过 velocity_estimated 区分「未知」和「测得静止」。
"""
import math
from ..param import *

class BallMotion:
    def __init__(self):
        self.context = None
        self._ball_sample = None
        self._ball_velocity = (0.0, 0.0)
        self._ball_velocity_estimated = False

    def update(self, context):
        self.context = context
        self.update_ball_motion()

    def update_ball_motion(self) -> None:
        """记录共享球观测；每帧更新一次，角色改变时也保有连续轨迹。

        用观测时间而非策略帧间隔求速度，重复读取同一观测不改变速度。
        丢球、过期、时间回退或不合理跳变后重新积累观测。
        """
        ctx = self.context
        ball = ctx.ball if ctx is not None else None
        if ball is None or not all(math.isfinite(v) for v in (
            ball.x, ball.y, ball.last_seen_at, ctx.now,
        )):
            self._ball_sample = None
            self._ball_velocity = (0.0, 0.0)
            self._ball_velocity_estimated = False
            return
        observed_at = ball.last_seen_at if ball.last_seen_at > 0.0 else ctx.now
        if ctx.now - observed_at > BALL_MOTION_MAX_GAP_S:
            self._ball_sample = None
            self._ball_velocity = (0.0, 0.0)
            self._ball_velocity_estimated = False
            return
        sample = (observed_at, ball.x, ball.y)
        previous = self._ball_sample
        if sample == previous:
            return
        self._ball_sample = sample
        self._ball_velocity = (0.0, 0.0)
        self._ball_velocity_estimated = False
        if previous is None:
            return
        dt = observed_at - previous[0]
        if 0.0 < dt <= BALL_MOTION_MAX_GAP_S:
            vx = (ball.x - previous[1]) / dt
            vy = (ball.y - previous[2]) / dt
            if math.hypot(vx, vy) <= BALL_MOTION_MAX_SPEED_MPS:
                self._ball_velocity = (vx, vy)
                self._ball_velocity_estimated = True

    def predict_ball_position(self, seconds: float) -> tuple[float, float] | None:
        """返回从当前帧起 ``seconds`` 秒后的球坐标（米）。

        只按球的连续观测做二维匀速外推，不考虑机器人、碰撞、摩擦或反弹；
        不将结果裁剪到球场。只有一个观测时按静止处理，无有效球观测返回 None。
        预测时间必须有限且非负。预测只读取最近一次 update() 的快照，不更新观测。
        """
        if not math.isfinite(seconds) or seconds < 0.0:
            raise ValueError("prediction time must be finite and non-negative")
        if self._ball_sample is None:
            return None
        observed_at, x, y = self._ball_sample
        elapsed = max(0.0, self.context.now - observed_at) + seconds
        vx, vy = self._ball_velocity
        return (x + vx * elapsed, y + vy * elapsed)

    def ball_prediction_details(self) -> dict | None:
        """看板轨迹，直接复用策略的预测函数；不从看板观测重新拟合。"""
        position = self.predict_ball_position(0.0)
        if position is None:
            return None
        count = math.ceil(BALL_INTERCEPT_HORIZON_S / 0.5)
        return dict(
            model='constant_velocity', position=position, velocity=self._ball_velocity,
            velocity_estimated=self._ball_velocity_estimated,
            observation_age_s=max(0.0, self.context.now - self._ball_sample[0]),
            horizon_s=BALL_INTERCEPT_HORIZON_S, step_s=BALL_INTERCEPT_STEP_S,
            points=[dict(t=min(i * 0.5, BALL_INTERCEPT_HORIZON_S),
                         position=self.predict_ball_position(min(i * 0.5, BALL_INTERCEPT_HORIZON_S)))
                    for i in range(count + 1)],
        )

