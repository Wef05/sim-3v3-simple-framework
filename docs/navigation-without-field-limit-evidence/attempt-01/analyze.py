from pathlib import Path
import json,collections,math,hashlib
out=Path(__file__).resolve().parent
path=out/'dashboard-team1.json';raw=path.read_bytes();d=json.loads(raw);frames=d['frames']
assert d['format']=='soccer-tactics-replay' and d['version']==1 and d['team_id']==1
assert all(a['id']<b['id'] and a['packet']['context']['now']<=b['packet']['context']['now'] for a,b in zip(frames,frames[1:]))
counts=collections.Counter();states=collections.Counter();outside_playing=[]
for f in frames:
 p=f['packet'];ctx=p['context']
 for r in p['players']:
  n=r.get('navigation') or {};counts[n.get('blocker') or 'none']+=1;states[n.get('state') or 'none']+=1
  pose=ctx['teammates'].get(str(r['id']),{}).get('pose')
  if pose and (ctx.get('game') or {}).get('state')=='PLAYING' and (abs(pose['x'])>7 or abs(pose['y'])>4.5):outside_playing.append(dict(frame=f['id'],player=r['id'],pose=pose))
def snapshot(f):
 p=f['packet'];ctx=p['context']
 return dict(frame=f['id'],context_now=ctx['now'],recorded_at=f['recorded_at'],phase=p.get('phase'),game=ctx.get('game'),ball=ctx.get('ball'),players=[dict(id=r['id'],role=r['role'],action=r['action'],pose=ctx['teammates'].get(str(r['id']),{}).get('pose'),target=r.get('target'),kicking=r.get('kicking'),navigation=r.get('navigation')) for r in p['players']])
selected=[]
for event in json.loads((out/'events.json').read_text())['events']:
 if event['type'] in ('ball_touch','goal','match_finished'):
  f=min(frames,key=lambda f:abs(f['recorded_at']-event['wallTime']))
  selected.append(dict(event=event,wall_alignment_error_s=f['recorded_at']-event['wallTime'],snapshot=snapshot(f)))
for label,predicate in [('first_ready',lambda f:(f['packet']['context'].get('game') or {}).get('state')=='READY'),('first_playing',lambda f:(f['packet']['context'].get('game') or {}).get('state')=='PLAYING'),('goal_structure',lambda f:any((r.get('navigation') or {}).get('blocker')=='goal_structure' for r in f['packet']['players'])),('kick_approach_blocked',lambda f:any((r.get('navigation') or {}).get('state')=='kick_approach_blocked' for r in f['packet']['players']))]:
 f=next((f for f in frames if predicate(f)),None)
 if f:selected.append(dict(label=label,snapshot=snapshot(f)))
summary=dict(frames=len(frames),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),frame_range=[frames[0]['id'],frames[-1]['id']],context_now_range=[frames[0]['packet']['context']['now'],frames[-1]['packet']['context']['now']],received_time_range=[frames[0]['recorded_at'],frames[-1]['recorded_at']],max_received_gap_s=max(b['recorded_at']-a['recorded_at'] for a,b in zip(frames,frames[1:])),max_context_gap_s=max(b['packet']['context']['now']-a['packet']['context']['now'] for a,b in zip(frames,frames[1:])),received_gaps_over_02_s=sum(b['recorded_at']-a['recorded_at']>.2 for a,b in zip(frames,frames[1:])),blocker_counts=dict(counts),navigation_state_counts=dict(states),outside_playing=outside_playing,selected=selected)
(out/'analysis.json').write_text(json.dumps(summary,indent=2))
print({k:v for k,v in summary.items() if k not in ('selected','outside_playing')})
for row in selected:
 s=row['snapshot'];print(row.get('label') or row['event']['type'],s['frame'],s['context_now'],[(r['id'],r['action'],r['pose'],r.get('target'),(r['navigation'] or {}).get('state'),(r['navigation'] or {}).get('blocker')) for r in s['players']],s['ball'])
