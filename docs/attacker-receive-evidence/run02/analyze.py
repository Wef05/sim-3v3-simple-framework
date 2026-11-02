"""Replay inspection only; events confirm referee contact, not SDK kick success."""
from pathlib import Path
import collections
import gzip
import json
import math

root = Path(__file__).resolve().parent
path = root / 'dashboard-team1.json'
data = json.loads(path.read_bytes() if path.exists() else gzip.decompress((root / 'dashboard-team1.json.gz').read_bytes()))
frames = data['frames']
events = json.loads((root / 'events.json').read_text())['events']


def point(p):
    return (p['x'], p['y'])


def compact(f, pid):
    d = f['packet']; c = d['context']; p = next(p for p in d['players'] if p['id'] == pid)
    ball = c['ball']; pose = c['teammates'][str(pid)]['pose']; receive = p.get('attack_receive') or {}
    velocity = (d.get('ball_prediction') or {}).get('velocity')
    return dict(frame=f['id'], sim=c['now'], remaining=c['game']['secs_remaining'],
                player=pid, action=p['action'], stage=receive.get('stage'), reason=receive.get('reason'),
                distance=math.dist(point(ball), point(pose)) if ball and pose else None,
                ball=point(ball) if ball else None, robot=point(pose) if pose else None,
                ball_speed=math.hypot(*velocity) if velocity else None,
                kicking=p['kicking'], navigation=p.get('navigation'), receive=receive)


counts = collections.Counter(); reasons = collections.Counter(); transitions = []; previous = {}
approach = []; waits = []; near_jumps = []; last_speed = None; last_time = None
for f in frames:
    d = f['packet']; c = d['context']; now = c['now']
    velocity = (d.get('ball_prediction') or {}).get('velocity'); speed = math.hypot(*velocity) if velocity else None
    for p in d['players']:
        r = p.get('attack_receive') or {}; key = (p['role'], r.get('stage'), r.get('reason'))
        if key != previous.get(p['id']):
            transitions.append(compact(f, p['id']))
        previous[p['id']] = key
        if not r:
            continue
        counts[r['stage']] += 1; reasons[r['reason']] += 1
        if r['stage'] in ('intercept', 'rebound_chase'):
            row = compact(f, p['id']); approach.append(row)
            if (row['distance'] is not None and row['distance'] < .65
                    and speed is not None and last_speed is not None
                    and 0 < now-last_time <= .2 and speed-last_speed > .5):
                near_jumps.append(row)
        elif r['stage'] == 'wait_ball':
            waits.append(compact(f, p['id']))
    last_speed, last_time = speed, now

touches = []
for event in events:
    if event['type'] != 'ball_touch' or (event.get('actor') or {}).get('side') != 'home':
        continue
    t = event['simTime']; pid = event['actor']['playerNumber']
    nearby = [f for f in frames if t-.35 <= f['packet']['context']['now'] <= t+.35]
    before = [f for f in frames if f['packet']['context']['now'] <= t]
    after = [f for f in frames if f['packet']['context']['now'] >= t]
    touches.append(dict(event=event['eventId'], sim=t, remaining=event['timing']['timeRemaining'], player=pid,
                        before=compact(before[-1], pid) if before else None,
                        after=compact(after[0], pid) if after else None,
                        window=[compact(f, pid) for f in nearby]))

result = dict(frames=len(frames), stages=counts, reasons=reasons,
              first_id=frames[0]['id'], last_id=frames[-1]['id'],
              sim_start=frames[0]['packet']['context']['now'], sim_end=frames[-1]['packet']['context']['now'],
              max_sim_gap=max(b['packet']['context']['now']-a['packet']['context']['now'] for a,b in zip(frames,frames[1:])),
              approach_frames=len(approach), wait_frames=len(waits),
              approach_avoid_ball_false=sum(not (r['navigation'] or {}).get('avoid_ball') for r in approach),
              minimum_approach=min(approach,key=lambda r:r['distance']) if approach else None,
              minimum_wait=min(waits,key=lambda r:r['distance']) if waits else None,
              near_ball_speed_jumps=near_jumps, home_touch_events=touches, transitions=transitions)
(root / 'safety-analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('STAGES',counts,'REASONS',reasons)
print('APPROACH',len(approach),'WAIT',len(waits),'AVOID_FALSE',result['approach_avoid_ball_false'])
if approach:print('MIN_APPROACH',result['minimum_approach'])
print('NEAR_JUMPS',[(r['frame'],r['sim'],r['player'],r['distance'],r['ball_speed']) for r in near_jumps])
for t in touches:
    print('TOUCH',t['event'],t['sim'],t['player'],'before',t['before'],'after',t['after'])
for t in transitions:
    if t['reason']=='near_ball_low_speed' or t['reason']=='rebound_over_90':
        print('KICK_ENTRY',t)
