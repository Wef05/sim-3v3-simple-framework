"""Read-only fall-state subscriptions, separate from robot control."""
import logging
import threading
import time


class FallObserver:
    def __init__(self, config):
        self.values = {}
        self.subscribers = []
        self.lock = threading.Lock()
        # SDK setup cannot block the strategy loop. Existing player SDKs have
        # already initialized the channel before play() begins.
        threading.Thread(target=self._start, args=(config,), daemon=True,
                         name='tactics_fall_observer').start()

    def _start(self, config):
        try:
            from booster_robotics_sdk_python import B1FallDownStateSubscriber
            from boosteros.robots.booster._subscription.converters import to_fall_down_state_data
            for side, names in [('our', config.robot_names), ('opp', config.opponent_robot_names)]:
                for pid, name in enumerate(names, 1):
                    def callback(msg, key=(side, pid)):
                        state = to_fall_down_state_data(msg).state
                        with self.lock:
                            self.values[key] = (state, time.monotonic())
                    sub = B1FallDownStateSubscriber(callback)
                    sub.InitChannelWithName(name)
                    self.subscribers.append(sub)
        except Exception as exc:
            logging.getLogger(__name__).warning('Fall telemetry unavailable: %s', exc)

    def snapshot(self, config):
        now = time.monotonic()
        with self.lock:
            values = dict(self.values)
        result = []
        for side, names in [('our', config.robot_names), ('opp', config.opponent_robot_names)]:
            for pid, _ in enumerate(names, 1):
                state, seen = values.get((side, pid), (None, 0))
                if now - seen > 2:
                    state = None
                fallen = (state != 'normal') if state in ('normal', 'falling', 'fallen', 'getting_up') else None
                result.append(dict(side=side, id=pid, fallen=fallen, fall_state=state))
        return result
