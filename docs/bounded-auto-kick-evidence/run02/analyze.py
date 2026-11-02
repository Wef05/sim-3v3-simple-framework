"""Recompute bounded-kick evidence from the unmodified exported replay."""
import collections
import gzip
import hashlib
import json
import math
from pathlib import Path

root = Path(__file__).parent
with gzip.open(root / 'dashboard-team1.json.gz', 'rt') as stream:
    replay = json.load(stream)
frames = replay['frames']
result = json.loads((root / 'result.json').read_text())
summary = dict(frames=len(frames), first_id=frames[0]['id'], last_id=frames[-1]['id'],
    recording_span_s=frames[-1]['time']-frames[0]['time'],
    simulation_clock_range=[frames[0]['packet']['context']['now'], frames[-1]['packet']['context']['now']],
    max_recording_gap_s=max(b['time']-a['time'] for a,b in zip(frames,frames[1:])),
    max_simulation_gap_s=max(b['packet']['context']['now']-a['packet']['context']['now'] for a,b in zip(frames,frames[1:])),
    referee_elapsed_s=600-result['timing']['timeRemaining'], score=result['score'],
    end_reason=result['endReason'], is_final=result['isFinal'], bursts=[])
for pid in (1,2,3):
    active = None
    for frame in frames:
        ctx=frame['packet']['context']
        player=next(p for p in frame['packet']['players'] if p['id']==pid)
        auto=player.get('auto_kick') or {}
        on=player['role']=='attack' and player['kick_control']['accepted']
        if on and active is None:
            active=dict(player=pid,first_id=frame['id'],first_now=ctx['now'],
                        inferred_request_since=ctx['now']-auto['elapsed_s'],entry_observed=auto)
        if on:
            active.update(last_id=frame['id'],last_now=ctx['now'],last_elapsed=auto['elapsed_s'])
        if not on and active:
            active.update(exit_id=frame['id'],exit_now=ctx['now'],exit_reason=auto['last_exit_reason'],
                observed_release_after_s=ctx['now']-active['inferred_request_since'],
                control_at_exit=player['kick_control'])
            summary['bursts'].append(active)
            active=None
    if active:
        summary['bursts'].append(active)
first=next(f for f in frames if f['packet']['context']['game']['state']=='PLAYING')
ctx=first['packet']['context']
summary['first_playing']=dict(id=first['id'],now=ctx['now'],teammates=ctx['teammates'],opponents=ctx['opponents'])
summary['minimum_post_distance_m']={}
for pid in (1,2,3):
    distances=[]
    for f in frames:
        ctx=f['packet']['context']
        if ctx['game']['state']!='PLAYING':continue
        pose=ctx['teammates'][str(pid)]['pose']
        distances.extend(math.dist((pose['x'],pose['y']),(x,y)) for x in (-7,7) for y in (-1.3,1.3))
    summary['minimum_post_distance_m'][pid]=min(distances)
summary['grace_episodes'] = []
for pid in (1, 2, 3):
    active = None
    for f in frames:
        p = next(p for p in f['packet']['players'] if p['id'] == pid)
        c, auto = f['packet']['context'], p.get('auto_kick') or {}
        grace = auto.get('exit_grace') or []
        if grace and active is None:
            active = dict(player=pid, first_id=f['id'], first_now=c['now'],
                          conditions=grace, request_elapsed_s=auto['elapsed_s'])
        if grace:
            active.update(last_id=f['id'],last_now=c['now'],last_conditions=grace)
        if not grace and active:
            active.update(end_id=f['id'],end_now=c['now'],end_state=auto.get('state'),
                          end_reason=auto.get('reason'),last_exit_reason=auto.get('last_exit_reason'))
            summary['grace_episodes'].append(active)
            active = None
    if active:
        summary['grace_episodes'].append(active)
summary['auto_kick_state_counts'] = {str(pid):dict(collections.Counter(
    (next(p for p in f['packet']['players'] if p['id']==pid).get('auto_kick') or {}).get('state','missing')
    for f in frames)) for pid in (1,2,3)}
key_ids = {first['id'], frames[-1]['id']}
for event in summary['bursts']+summary['grace_episodes']:
    key_ids.update(v for k,v in event.items() if k.endswith('_id'))
last_bucket = None
for f in frames:
    bucket = int(f['packet']['context']['now']/3)
    if bucket != last_bucket:
        key_ids.add(f['id']);last_bucket = bucket
keyframes=[]
for f in frames:
    if f['id'] not in key_ids:continue
    c=f['packet']['context']
    keyframes.append(dict(id=f['id'],sequence=f['packet']['sequence'],now=c['now'],
        remaining=c['game']['secs_remaining'],state=c['game']['state'],ball=c['ball'],
        teammates=c['teammates'],opponents=c['opponents'],
        players=[{k:p.get(k) for k in ('id','role','action','auto_kick','kick_control','guard_decision','navigation','target','kick_power')} for p in f['packet']['players']]))
phase_seconds = collections.Counter()
for a,b in zip(frames,frames[1:]):
    c = a['packet']['context']; game = c['game']
    phase_seconds[game['state']+(':stopped' if game['stopped'] else '')] += max(0., b['packet']['context']['now']-c['now'])
summary['observed_phase_simulation_seconds'] = dict(phase_seconds)
(root/'analysis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
(root/'keyframes.json').write_text(json.dumps(keyframes,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k not in ('bursts','first_playing','grace_episodes')},indent=2))
