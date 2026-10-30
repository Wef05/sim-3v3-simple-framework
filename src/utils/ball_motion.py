"""整队共享的有状态观测工具：按分段摩擦拟合预测二维球轨迹。

状态仅用于观测估计，不持有角色或机器人控制权。update 推进观测，predict 只读，
保证一帧中角色分配、追球和看板多次查询时，不会重复更新速度或消耗观测。
未估计出速度时按静止外推，并通过 velocity_estimated 区分「未知」和「测得静止」。
"""
import math
from ..param import *


def ball_travel(initial_speed: float, seconds: float, mode: str) -> float:
    """截图速度公式的解析积分；t 从运动起点计，停止后位移不再增长。"""
    if mode not in ('sliding_then_rolling', 'pure_rolling'):
        raise ValueError('unsupported ball motion mode')
    if not all(math.isfinite(v) and v >= 0 for v in (initial_speed, seconds)):
        raise ValueError('speed and time must be finite and non-negative')
    v0 = initial_speed
    if mode == 'pure_rolling':
        plateau = BALL_PURE_ROLL_PLATEAU_S_PER_MPS * v0
        rolling = min(max(0.0, seconds - plateau), v0 / BALL_PURE_ROLL_DECEL_MPS2)
        return v0 * min(seconds, plateau) + v0 * rolling - 0.5 * BALL_PURE_ROLL_DECEL_MPS2 * rolling**2
    transition = BALL_SLIDE_TRANSITION_S_PER_MPS * v0
    sliding = min(seconds, transition)
    distance = v0 * sliding - 0.5 * BALL_SLIDE_DECEL_MPS2 * sliding**2
    # 第二段的 t 仍是全局时间，不能从切换点重新计时。
    rolling_speed = max(0.0, BALL_ROLL_SPEED_FACTOR * v0 - BALL_ROLL_DECEL_MPS2 * transition)
    rolling = min(max(0.0, seconds - transition), rolling_speed / BALL_ROLL_DECEL_MPS2)
    return distance + rolling_speed * rolling - 0.5 * BALL_ROLL_DECEL_MPS2 * rolling**2


class BallMotion:
    def __init__(self, mode=BALL_MOTION_MODE):
        ball_travel(0.0, 0.0, mode)
        self.mode = mode
        self.context = None
        self._ball_sample = None
        self._ball_velocity = (0.0, 0.0)
        self._ball_velocity_estimated = False
        self._reset_fit()

    def _reset_fit(self):
        self._speed_samples = []
        self._current_speed = 0.0
        self._phase = 'unknown'
        self._slide_until = None

    def _fit_motion(self, previous, sample, vx, vy):
        """滑动窗口估计当前速度与阶段，不假定已知出球初速或出球时刻。"""
        speed = math.hypot(vx, vy)
        dt = sample[0] - previous[0]
        old_speed = math.hypot(*self._ball_velocity)
        turned = old_speed > 0 and speed > 0 and (
            vx*self._ball_velocity[0] + vy*self._ball_velocity[1]
        ) / (speed*old_speed) < 0.9
        accelerated = speed - old_speed > max(BALL_MOTION_SPEED_RESET_MPS, old_speed * .35)
        if turned or accelerated or speed == 0:
            self._reset_fit()
        if speed == 0:
            self._phase = 'stopped'
            return
        # 区间平均速度对应区间中点；不等间隔观测也可比较减速度。
        midpoint = (previous[0] + sample[0]) / 2
        self._speed_samples.append((midpoint, speed))
        self._speed_samples = self._speed_samples[-BALL_MOTION_FIT_SAMPLES:]
        samples = self._speed_samples
        mean_t = sum(t - sample[0] for t, _ in samples) / len(samples)
        mean_v = sum(v for _, v in samples) / len(samples)
        variance = sum((t - sample[0] - mean_t)**2 for t, _ in samples)
        measured_decel = -sum((t - sample[0] - mean_t)*(v - mean_v)
                             for t, v in samples) / variance if variance > 1e-12 else None
        rolling_decel = BALL_PURE_ROLL_DECEL_MPS2 if self.mode == 'pure_rolling' else BALL_ROLL_DECEL_MPS2
        sliding = (self.mode != 'pure_rolling' and measured_decel is not None
                   and measured_decel > (BALL_SLIDE_DECEL_MPS2 + rolling_decel) / 2)
        self._phase = 'sliding' if sliding else ('rolling' if measured_decel is not None else 'unknown')
        decel = BALL_SLIDE_DECEL_MPS2 if sliding else rolling_decel
        # 每次观测都更新当前速度；刚识别阶段变化时优先使用最新区间。
        if len(samples) > 1 and measured_decel is not None and abs(measured_decel-decel) < .5:
            self._current_speed = max(0.0, mean_v + decel*mean_t)
        else:
            self._current_speed = max(0.0, speed - decel*dt/2)
        if sliding:
            # 无法由滑动观测唯一恢复出球时刻。由截图关系给出剩余滑动时间上界，
            # 首次识别后只倒计时，后续滚动证据可立即提前终止它。
            remaining_bound = (BALL_SLIDE_TRANSITION_S_PER_MPS * self._current_speed /
                               (1 - BALL_SLIDE_DECEL_MPS2 * BALL_SLIDE_TRANSITION_S_PER_MPS))
            until = sample[0] + remaining_bound
            self._slide_until = min(self._slide_until, until) if self._slide_until is not None else until
        else:
            self._slide_until = None

    def _future_distance(self, seconds):
        """从最新观测时刻积分，当前速度和剩余阶段由观测不断校正。"""
        speed = self._current_speed
        sliding = min(seconds, max(0.0, self._slide_until - self._ball_sample[0])) if self._slide_until is not None else 0.0
        sliding = min(sliding, speed / BALL_SLIDE_DECEL_MPS2)
        distance = speed*sliding - .5*BALL_SLIDE_DECEL_MPS2*sliding**2
        speed = max(0.0, speed - BALL_SLIDE_DECEL_MPS2*sliding)
        decel = BALL_PURE_ROLL_DECEL_MPS2 if self.mode == 'pure_rolling' else BALL_ROLL_DECEL_MPS2
        rolling = min(max(0.0, seconds-sliding), speed/decel)
        return distance + speed*rolling - .5*decel*rolling**2

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
            self._reset_fit()
            return
        observed_at = ball.last_seen_at
        if ctx.now - observed_at > BALL_MOTION_MAX_GAP_S:
            self._ball_sample = None
            self._ball_velocity = (0.0, 0.0)
            self._ball_velocity_estimated = False
            self._reset_fit()
            return
        sample = (observed_at, ball.x, ball.y)
        previous = self._ball_sample
        if sample == previous:
            return
        self._ball_sample = sample
        self._ball_velocity_estimated = False
        if previous is None:
            self._ball_velocity = (0.0, 0.0)
            return
        dt = observed_at - previous[0]
        if 0.0 < dt <= BALL_MOTION_MAX_GAP_S:
            vx = (ball.x - previous[1]) / dt
            vy = (ball.y - previous[2]) / dt
            if math.hypot(vx, vy) <= BALL_MOTION_MAX_SPEED_MPS:
                self._fit_motion(previous, sample, vx, vy)
                self._ball_velocity = (vx, vy)
                self._ball_velocity_estimated = True
                return
        self._ball_velocity = (0.0, 0.0)
        self._reset_fit()

    def predict_ball_position(self, seconds: float) -> tuple[float, float] | None:
        """返回从当前帧起 ``seconds`` 秒后的球坐标（米）。

        对分段减速速度做解析积分，不考虑未来机器人触球、碰撞或反弹；
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
        speed = math.hypot(vx, vy)
        if not self._ball_velocity_estimated or speed == 0.0:
            return (x, y)
        distance = self._future_distance(elapsed)
        return (x + vx / speed * distance, y + vy / speed * distance)

    def predict_ball_stop(self) -> dict | None:
        """无后续触球/碰撞时的停止点；时间、剩余距离均相对当前帧。

        不受截球搜索时域或球场边界限制。速度未知不冒充已静止。
        """
        if self._ball_sample is None or not self._ball_velocity_estimated:
            return None
        speed = self._current_speed
        sliding = max(0.0, self._slide_until-self._ball_sample[0]) if self._slide_until is not None else 0.0
        sliding = min(sliding, speed/BALL_SLIDE_DECEL_MPS2)
        rolling_speed = max(0.0, speed-BALL_SLIDE_DECEL_MPS2*sliding)
        decel = BALL_PURE_ROLL_DECEL_MPS2 if self.mode == 'pure_rolling' else BALL_ROLL_DECEL_MPS2
        age = max(0.0, self.context.now-self._ball_sample[0])
        remaining = max(0.0, sliding+rolling_speed/decel-age)
        position = self.predict_ball_position(remaining)
        current = self.predict_ball_position(0.0)
        return dict(position=position, time_s=remaining,
                    distance_m=math.hypot(position[0]-current[0], position[1]-current[1]))

    def ball_prediction_details(self) -> dict | None:
        """看板轨迹，直接复用策略的预测函数；不从看板观测重新拟合。"""
        position = self.predict_ball_position(0.0)
        if position is None:
            return None
        count = math.ceil(BALL_INTERCEPT_HORIZON_S / 0.5)
        return dict(
            model=self.mode, position=position, velocity=self._ball_velocity,
            velocity_kind='observed_interval_average',
            estimation='online_observations', phase=self._phase,
            current_speed_mps=self._current_speed,
            initial_speed_mps=None, motion_age_s=None,
            fit_sample_count=len(self._speed_samples),
            sliding_remaining_s=max(0.0, self._slide_until-self._ball_sample[0]) if self._slide_until is not None else 0.0,
            velocity_estimated=self._ball_velocity_estimated,
            stop=self.predict_ball_stop(),
            observation_age_s=max(0.0, self.context.now - self._ball_sample[0]),
            horizon_s=BALL_INTERCEPT_HORIZON_S, step_s=BALL_INTERCEPT_STEP_S,
            points=[dict(t=min(i * 0.5, BALL_INTERCEPT_HORIZON_S),
                         position=self.predict_ball_position(min(i * 0.5, BALL_INTERCEPT_HORIZON_S)))
                    for i in range(count + 1)],
        )

