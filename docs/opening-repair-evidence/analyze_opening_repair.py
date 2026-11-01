import json,gzip,collections,hashlib
from pathlib import Path
base=Path('/Users/limingzhe/.codex/worktrees/pass-coordination/sim-3v3-simple-framework');out=base/'docs/opening-repair-evidence/run01'
data=json.load(gzip.open(out/'dashboard-team1.json.gz','rt'));fs=data['frames']
events={};selections={};stages=collections.Counter();actions=collections.Counter();kick_rows=[];timeline=[]
for f in fs:
 p=f['packet'];state=p.get('passing') or {};active=state.get('active')
 if active:stages[active.get('reception',{}).get('phase',active['stage']) if active['stage']=='receive' else active['stage']]+=1
 for e in state.get('events',[]):events[(e['id'],e['time'],e['event'])]=e
 selection=state.get('selection')
 if selection:selections[selection['evaluated_at']]=selection
 for r in p['players']:
  actions[str(r['id'])+':'+r['action']]+=1
  if r['kicking']:kick_rows.append({'frame':f['id'],'sim':p['context']['now'],'id':r['id'],'target':r['target'],'power':r['kick_power'],'passing':active})
times=sorted(set(e['time'] for e in events.values()))
for t in times:
 f=min(fs,key=lambda f:abs(f['packet']['context']['now']-t));p=f['packet'];timeline.append({'frame':f['id'],'sim':p['context']['now'],'ball':p['context']['ball'],'passing':p.get('passing'),'players':[{k:v for k,v in r.items() if k in ('id','action','kicking','kick_power','navigation','target')} for r in p['players']],'poses':{i:r['pose'] for i,r in p['context']['teammates'].items()}})
selected=[s for s in selections.values() if s.get('selected')]
capture={'frames':len(fs),'first_id':fs[0]['id'],'last_id':fs[-1]['id'],'first_sim':fs[0]['packet']['context']['now'],'last_sim':fs[-1]['packet']['context']['now'],'max_sim_gap':max(b['packet']['context']['now']-a['packet']['context']['now'] for a,b in zip(fs,fs[1:])),'max_receive_gap':max(b['time']-a['time'] for a,b in zip(fs,fs[1:])),'raw_bytes':len(gzip.decompress((out/'dashboard-team1.json.gz').read_bytes())),'sha256':hashlib.sha256((out/'dashboard-team1.json.gz').read_bytes()).hexdigest(),'max_packet_bytes':max(len(json.dumps(f['packet'],separators=(',',':')).encode()) for f in fs)}
analysis={'summary':json.loads((out/'summary.json').read_text()),'capture':capture,'events':list(events.values()),'phases':dict(stages),'actions':dict(actions),'kick_frames':kick_rows,'timeline':timeline,'selection_count':len(selections),'selected_count':len(selected),'selection_reasons':dict(collections.Counter(s['reason'] for s in selections.values())),'first_selection':next(iter(selections.values()),None),'selected':selected}
(out/'analysis.json').write_text(json.dumps(analysis,ensure_ascii=False,indent=2))
(out/'selections.json').write_text(json.dumps(list(selections.values()),ensure_ascii=False,indent=2))
for name in ('samples','selections'):
 raw=(out/(name+'.json')).read_bytes()
 with gzip.open(out/(name+'.json.gz'),'wb') as f:f.write(raw)
 # 原始信息无损压缩后删除同内容冗余文件，仅本次生成数据。
 (out/(name+'.json')).unlink()
dest=base/'results/opening-repair/run01';dest.mkdir(parents=True,exist_ok=True)
(dest/'dashboard-team1.json').write_bytes(gzip.decompress((out/'dashboard-team1.json.gz').read_bytes()))
print(json.dumps({k:v for k,v in analysis.items() if k in ('summary','capture','events','selection_count','selected_count','selection_reasons','phases')},ensure_ascii=False,indent=2))
