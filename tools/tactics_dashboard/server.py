#!/usr/bin/env python3
"""Standalone UDP receiver + local HTTP dashboard, Python standard library only."""
from collections import deque
import uuid
from urllib.parse import urlsplit, parse_qs
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import threading
import time


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.teams = {}
        self.history = {}
        self.last_recorded = {}
        self.next_id = 0
        self.session = uuid.uuid4().hex
        self.history_seconds = 1800
        self.max_frames = 18000

    def receive(self, raw):
        try:
            packet = json.loads(raw)
            ctx = packet['context']
            team = str(int(ctx['team_id']))
            if packet.get('version') != 1 or not isinstance(packet['players'], list):
                return False
            if not isinstance(ctx['field'], dict):
                return False
            # Round-trip rejects nonstandard NaN/Infinity JSON.
            json.dumps(packet, allow_nan=False)
        except (ValueError, TypeError, KeyError):
            return False
        with self.lock:
            if team not in self.teams and len(self.teams) >= 16:
                return False
            now = time.monotonic()
            self.teams[team] = {'packet': packet, 'received': now}
            # Record at up to 10 Hz; the live endpoint still gets every packet.
            if now - self.last_recorded.get(team, -float('inf')) >= 0.1:
                self.last_recorded[team] = now
                self.next_id += 1
                frames = self.history.setdefault(team, deque(maxlen=self.max_frames))
                frames.append({'id': self.next_id, 'time': now,
                               'recorded_at': time.time(), 'packet': packet})
                self._prune(frames, now)
        return True

    def _prune(self, frames, now):
        while frames and now - frames[0]['time'] > self.history_seconds:
            frames.popleft()

    def replay(self, team, after=0, limit=500):
        with self.lock:
            frames = self.history.get(team, deque())
            self._prune(frames, time.monotonic())
            selected = []
            for frame in frames:
                if frame['id'] > after:
                    selected.append(frame)
                    if len(selected) >= limit:
                        break
            return {'session': self.session, 'frames': selected,
                    'first_id': frames[0]['id'] if frames else None,
                    'last_id': frames[-1]['id'] if frames else None,
                    'recording_hz': 10, 'retention_seconds': self.history_seconds}

    def snapshot(self):
        with self.lock:
            return {team: {'packet': value['packet'],
                           'age': time.monotonic() - value['received']}
                    for team, value in self.teams.items()}


def handler_for(state):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == '/api/state':
                body = json.dumps(state.snapshot(), allow_nan=False).encode()
                mime = 'application/json'
            elif url.path == '/api/history':
                try:
                    query = parse_qs(url.query)
                    team = str(int(query.get('team', ['1'])[0]))
                    after = max(0, int(query.get('after', ['0'])[0]))
                    limit = min(500, max(1, int(query.get('limit', ['500'])[0])))
                except ValueError:
                    self.send_error(400)
                    return
                body = json.dumps(state.replay(team, after, limit), allow_nan=False).encode()
                mime = 'application/json'
            elif url.path == '/dashboard.js':
                body = Path(__file__).with_name('dashboard.js').read_bytes()
                mime = 'text/javascript; charset=utf-8'
            elif url.path in ('/', '/index.html'):
                body = Path(__file__).with_name('index.html').read_bytes()
                mime = 'text/html; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--udp-host', default='0.0.0.0')
    parser.add_argument('--udp-port', type=int, default=9871)
    parser.add_argument('--http-host', default='127.0.0.1')
    parser.add_argument('--http-port', type=int, default=8765)
    args = parser.parse_args()
    state = State()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.bind((args.udp_host, args.udp_port))
        def receive():
            while True:
                try:
                    raw, _ = udp.recvfrom(65535)
                except OSError:
                    return
                state.receive(raw)
        threading.Thread(target=receive, daemon=True).start()
        with ThreadingHTTPServer((args.http_host, args.http_port), handler_for(state)) as http:
            print(f'Tactics dashboard: http://{args.http_host}:{args.http_port} | UDP {args.udp_host}:{args.udp_port}', flush=True)
            try:
                http.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == '__main__':
    main()
