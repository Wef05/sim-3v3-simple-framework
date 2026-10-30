#!/usr/bin/env python3
"""Standalone UDP receiver + local HTTP dashboard, Python standard library only."""
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
            self.teams[team] = {'packet': packet, 'received': time.monotonic()}
        return True

    def snapshot(self):
        with self.lock:
            return {team: {'packet': value['packet'],
                           'age': time.monotonic() - value['received']}
                    for team, value in self.teams.items()}


def handler_for(state):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/api/state':
                body = json.dumps(state.snapshot(), allow_nan=False).encode()
                mime = 'application/json'
            elif self.path in ('/', '/index.html'):
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
