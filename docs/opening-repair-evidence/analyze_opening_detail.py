import json,gzip,collections,math
from pathlib import Path
out=Path('/Users/limingzhe/.codex/worktrees/pass-coordination/sim-3v3-simple-framework/docs/opening-repair-evidence/run01')
fs=json.load(gzip.open(out/'dashboard-team1.json.gz','rt'))['frames'];events=json.loads((out/'events.json').read_text())['events']
rows=[]
for t in [37.,39.,50.,59.738,60.778,61.996,67.88,69.486,73.794,78.,80.5,81.236,81.744,110.53,114.,119.5]:
 f=min(fs,key=lambda f:abs(f['packet']['context']['now']-t));p=f['packet'];q=p.get('passing') or {}
 rows.append({'frame':f['id'],'sim':p['context']['now'],'game':p['context']['game']['state'],'ball':p['context']['ball'],
 'teammates':p['context']['teammates'],'opponents':p['context']['opponents'],
 'players':[{k:r.get(k) for k in ('id','action','navigation','kicking','target')} for r in p['players']],
 'selection_reason':(q.get('selection') or {}).get('reason'),'preparation':q.get('preparation'),'active':q.get('active')})
pre=[]
for f in fs:
 p=f['packet'];r=next(r for r in p['players'] if r['id']==3)
 if r['action']=='freeman:receive_preposition':pre.append((f,r))
stages=collections.defaultdict(float)
for a,b in zip(events,events[1:]):
 if a.get('simTime') is not None and b.get('simTime') is not None:stages[a['game']['state']]+=b['simTime']-a['simTime']
prestats={'frames':len(pre),'max_y':max(f['packet']['context']['teammates']['3']['pose']['y'] for f,r in pre),'blockers':dict(collections.Counter((r.get('navigation') or {}).get('blocker') for f,r in pre)), 'returning_frames':sum(bool((r.get('navigation') or {}).get('returning')) for f,r in pre)}
result={'timeline':rows,'preposition_3':prestats,'event_interval_state_seconds':dict(stages),'referee_events':[{'type':e['type'],'sim':e['simTime'],'elapsed':600-e['timing']['timeRemaining'],'actor':e.get('actor'),'outcome':e.get('outcome')} for e in events]}
(out/'detail-analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('preposition3',prestats,'stages',dict(stages))
for row in rows:
 print(row['frame'],row['sim'],row['game'],'ball',(round(row['ball']['x'],2),round(row['ball']['y'],2)),row['selection_reason'],[(r['id'],r['action'],r['target']) for r in row['players']])
print([(e['type'],e['simTime'],e.get('outcome')) for e in events[-8:]])
