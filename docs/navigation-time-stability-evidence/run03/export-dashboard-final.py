#!/usr/bin/env python3
"""Export a bounded team replay without mutating dashboard state."""
import argparse
import datetime
import json
import math
from pathlib import Path
import urllib.parse
import urllib.request


def export(base_url, team, after_id, session=None, until_id=None):
    def get(after, limit):
        query = urllib.parse.urlencode({'team': team, 'after': after, 'limit': limit})
        with urllib.request.urlopen(base_url.rstrip('/') + '/api/history?' + query, timeout=20) as response:
            return json.load(response)

    boundary = get(after_id, 1)
    expected = session if session is not None else boundary['session']
    if boundary['session'] != expected:
        raise ValueError('Dashboard session changed; do not mix runs')
    end_id = boundary.get('last_id')
    if until_id is not None:
        if until_id <= after_id or end_id is None or until_id > end_id:
            raise ValueError('Invalid end boundary; preserve the recorded start/end IDs')
        end_id = until_id
    if end_id is None or end_id <= after_id:
        raise ValueError('No recorded frames after the requested boundary')
    frames = []
    cursor = after_id
    previous_time = -math.inf
    previous_sim_time = -math.inf
    while cursor < end_id:
        page = get(cursor, 500)
        if page['session'] != expected:
            raise ValueError('Dashboard session changed during export')
        batch = page['frames']
        if not batch:
            raise ValueError('History disappeared before the fixed end boundary')
        progressed = False
        for frame in batch:
            identifier = frame['id']
            if identifier > end_id:
                break
            packet = frame['packet']
            if identifier <= cursor:
                raise ValueError('History IDs did not increase')
            if (not math.isfinite(frame['time']) or frame['time'] < previous_time
                    or not math.isfinite(frame['recorded_at'])):
                raise ValueError('Invalid or non-monotonic replay time')
            if (packet.get('version') != 1 or packet['context']['team_id'] != team
                    or not isinstance(packet.get('players'), list)):
                raise ValueError('Invalid snapshot or wrong team')
            sim_time = packet['context'].get('now')
            if sim_time is not None:
                if not isinstance(sim_time, (int, float)) or not math.isfinite(sim_time):
                    raise ValueError('Invalid simulation time')
                if sim_time < previous_sim_time:
                    raise ValueError('Simulation clock reset inside export; use this match end ID, do not mix runs')
                previous_sim_time = sim_time
            frames.append(frame)
            if len(frames) > 54000:
                raise ValueError('Replay exceeds the supported 54000-frame limit')
            cursor = identifier
            previous_time = frame['time']
            progressed = True
        if not progressed:
            raise ValueError('Unable to reach fixed end boundary')
    return {
        'format': 'soccer-tactics-replay', 'version': 1, 'team_id': team,
        'exported_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'recording_hz': boundary.get('recording_hz', 30), 'frames': frames,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8765')
    parser.add_argument('--team', type=int, default=1)
    parser.add_argument('--after-id', type=int, default=0)
    parser.add_argument('--until-id', type=int, help='Fixed last frame ID recorded at match end')
    parser.add_argument('--session', help='Expected dashboard session recorded before this test')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.after_id < 0:
        parser.error('--after-id must be nonnegative')
    if args.output.exists():
        parser.error('Output already exists; choose a new run path')
    replay = export(args.base_url, args.team, args.after_id, args.session, args.until_id)
    payload = json.dumps(replay, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        handle.write(payload)
    print(json.dumps({'path': str(args.output.resolve()), 'frames': len(replay['frames']),
                      'bytes': args.output.stat().st_size}, ensure_ascii=False))


if __name__ == '__main__':
    main()
