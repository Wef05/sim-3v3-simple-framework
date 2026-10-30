"""Optional, best-effort UDP snapshots. No ROS or third-party dependencies."""
from dataclasses import asdict
import json
import logging
import os
import socket
import time

from . import telemetry_config

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
                self.sock.sendto(packet, self.address)
        except Exception:
            # Telemetry must never trigger the runtime's emergency stop path.
            pass


def make_snapshot(context, players, store):
    return {
        'version': 1,
        'context': asdict(context),
        'phase': getattr(getattr(store, 'cur_phase', None), 'value', None),
        'normal_attacker': getattr(store, 'normal_attacker', None),
        'kickoff_taker': (getattr(store, 'kickoff_taker', None)
                          if getattr(getattr(store, 'cur_phase', None), 'value', None) == 'our_kickoff' else None),
        'players': [dict(
            id=p.id, role=p.action.split(':')[0], action=p.action,
            target=getattr(p, 'tactical_target', None),
            freeman_region=getattr(p, 'freeman_region', None),
            mode=p.mode, fallen=p.is_fallen, penalized=p.is_penalized,
            penalty=p.penalty, kicking=p.is_kicking,
        ) for p in players],
    }


def publish(context, players, store):
    try:
        if not hasattr(store, '_tactics_publisher'):
            store._tactics_publisher = Publisher()
        store._tactics_publisher.publish(context, players, store)
    except Exception:
        pass
