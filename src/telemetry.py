"""Optional, best-effort UDP snapshots. No ROS or third-party dependencies."""
from dataclasses import asdict
import json
import logging
import os
import socket
import struct
import secrets
import time

from . import telemetry_config
from .utils.block_point import calculate_block_point

_log = logging.getLogger(__name__)


class Publisher:
    def __init__(self):
        self.sock = None
        self.next_send = 0.0
        self.sequence = 0
        self.fall_observer = None
        try:
            host = os.getenv('SOCCER_DASHBOARD_HOST', telemetry_config.HOST)
            if not host:
                return
            # Numeric IPv4 only: never perform DNS in the control loop.
            socket.inet_pton(socket.AF_INET, host)
            port = int(os.getenv('SOCCER_DASHBOARD_PORT', telemetry_config.PORT))
            hz = float(os.getenv('SOCCER_DASHBOARD_HZ', telemetry_config.HZ))
            if not 1 <= port <= 65535 or not 0 < hz <= 30:
                raise ValueError('port must be 1..65535 and hz must be (0, 30]')
            self.address = (host, port)
            self.interval = 1 / hz
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setblocking(False)
        except Exception as exc:
            self.close()
            _log.warning('Tactics telemetry disabled: %s', exc)

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def publish(self, context, players, store):
        if self.sock is None:
            return
        now = time.monotonic()
        if now < self.next_send:
            return
        # Keep a fixed cadence instead of adding loop jitter to each interval.
        self.next_send += self.interval
        if self.next_send <= now:
            self.next_send = now + self.interval
        try:
            if self.fall_observer is None and players and players[0]._backend is not None:
                from .telemetry_status import FallObserver
                self.fall_observer = FallObserver(players[0].config)
            self.sequence += 1
            payload = make_snapshot(context, players, store)
            if self.fall_observer is not None:
                payload['robot_statuses'] = self.fall_observer.snapshot(players[0].config)
            payload.update(sequence=self.sequence, sent_at=time.time())
            packet = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
            if len(packet) <= 60000:
                # Docker Desktop 会丢弃较大的 UDP 数据报；每包不超过 1200 字节。
                message_id = secrets.randbits(64)
                chunks = [packet[i:i+1184] for i in range(0, len(packet), 1184)]
                for index, chunk in enumerate(chunks):
                    header = struct.pack("!4sQHH", b"STD1", message_id, index, len(chunks))
                    self.sock.sendto(header + chunk, self.address)
        except Exception:
            # Telemetry must never trigger the runtime's emergency stop path.
            pass


def make_snapshot(context, players, store):
    own = {p.id: p for p in players}
    blocks = []
    if players:
        for side, robots in (('teammates', context.teammates), ('opponents', context.opponents)):
            for ident, robot in robots.items():
                player = own.get(ident) if side == 'teammates' else None
                blocks.append(dict(id=ident, side=side,
                    state_known=player is not None,
                    **calculate_block_point(robot.pose, players[0].motion,
                        fallen=player.is_fallen if player else False,
                        penalized=player.is_penalized if player else False)))
    return {
        'version': 1,
        'block_points': blocks,
        'context': asdict(context),
        'phase': getattr(getattr(store, 'cur_phase', None), 'value', None),
        'normal_attacker': getattr(store, 'normal_attacker', None),
        'attack_decision': getattr(store, 'normal_attack_decision', None),
        'threat': getattr(store, 'threat', None),
        'passing': asdict(store.passing) if hasattr(store, 'passing') else None,
        'kick_error': store.kick_error_tracker.snapshot() if hasattr(store, 'kick_error_tracker') else None,
        'ball_prediction': players[0].motion.ball_prediction_details() if players else None,
        'kickoff_taker': (getattr(store, 'kickoff_taker', None)
                          if getattr(getattr(store, 'cur_phase', None), 'value', None) == 'our_kickoff' else None),
        'players': [dict(
            id=p.id, role=p.action.split(':')[0], action=p.action,
            target=getattr(p, 'tactical_target', None),
            guard_decision=getattr(p, 'guard_decision', None),
            navigation=getattr(getattr(p, 'controller', None), 'navigation_status', None),
            freeman_region=getattr(p, 'freeman_region', None),
            intercept=getattr(p, 'ball_intercept', None),
            interception_risk=getattr(p, 'interception_risk', None),
            attack_candidates=getattr(p, 'attack_candidates', None),
            scoring_probability=getattr(p, 'scoring_probability', None),
            mode=p.mode, fallen=p.is_fallen, penalized=p.is_penalized,
            penalty=p.penalty, kicking=p.is_kicking,
            kick_power=(getattr(getattr(p, "controller", None), "kick_observation", None) or {}).get("power"),
        ) for p in players],
    }


def publish(context, players, store):
    try:
        if not hasattr(store, '_tactics_publisher'):
            store._tactics_publisher = Publisher()
        store._tactics_publisher.publish(context, players, store)
    except Exception:
        pass
