import json,gzip,collections,hashlib
from pathlib import Path
root=Path('/Users/limingzhe/.codex/worktrees/pass-coordination/sim-3v3-simple-framework');base=root/'docs/receive-safety-evidence'
for name in ('run01','run02'):
 run=base/name
 if not (run/'summary.json').exists():continue
 data=json.load(gzip.open(run/'dashboard-team1.json.gz','rt'));fs=data['frames'];events={};stages=collections.Counter();protections=collections.Counter();kick_rows=[];rows=[]
 for f in fs:
  p=f['packet'];state=p.get('passing',{});active=state.get('active')
  if active:stages[active.get('reception',{}).get('phase',active['stage']) if active['stage']=='receive' else active['stage']]+=1
  for e in state.get('events',[]):events[(e['id'],e['time'],e['event'])]=e
  for guard in state.get('exit_protections',{}).values():protections[guard['phase']+':'+guard['reason']]+=1
  for player in p['players']:
   if player['id']==3 and player['kicking']:kick_rows.append({'frame':f['id'],'sim':p['context']['now'],'target':player['target'],'power':player['kick_power'],'passing':active,'protection':state.get('exit_protections',{}).get('3')})
 times=sorted(set(e['time'] for e in events.values()))
 for t in times:
  f=min(fs,key=lambda f:abs(f['packet']['context']['now']-t));p=f['packet'];rows.append({'frame':f['id'],'sim':p['context']['now'],'ball':p['context']['ball'],'passing':p.get('passing'),'players':[{k:v for k,v in player.items() if k in ('id','action','kicking','kick_power','navigation','target')} for player in p['players']],'poses':{i:r['pose'] for i,r in p['context']['teammates'].items()}})
 capture={'frames':len(fs),'first_id':fs[0]['id'],'last_id':fs[-1]['id'],'first_sim':fs[0]['packet']['context']['now'],'last_sim':fs[-1]['packet']['context']['now'],'max_sim_gap':max(b['packet']['context']['now']-a['packet']['context']['now'] for a,b in zip(fs,fs[1:])),'max_receive_gap':max(b['time']-a['time'] for a,b in zip(fs,fs[1:])),'raw_bytes':len(gzip.decompress((run/'dashboard-team1.json.gz').read_bytes())),'sha256':hashlib.sha256((run/'dashboard-team1.json.gz').read_bytes()).hexdigest()}
 result={'summary':json.loads((run/'summary.json').read_text()),'capture':capture,'events':list(events.values()),'reception_frames':dict(stages),'protection_frames':dict(protections),'player3_kick_frames':kick_rows,'event_frames':rows}
 (run/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(name,result['summary'],list(events.values()),dict(stages),dict(protections),'3kickcount',len(kick_rows))
 dest=root/'results/receive-safety'/name;dest.mkdir(parents=True,exist_ok=True);(dest/'dashboard-team1.json').write_bytes(gzip.decompress((run/'dashboard-team1.json.gz').read_bytes()))
