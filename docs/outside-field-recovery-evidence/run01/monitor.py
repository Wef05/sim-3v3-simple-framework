import json,time,subprocess,urllib.request
from pathlib import Path
out=Path('/Users/limingzhe/.codex/worktrees/outside-field-recovery/sim-3v3-simple-framework/docs/outside-field-recovery-evidence/run01')
def save(name,data): (out/name).write_text(json.dumps(data,ensure_ascii=False))
def ref(path,post=False):
 cmd=['docker','exec','4x2a0-k1','curl','--fail','--silent','--max-time','8']
 if post: cmd+=['-X','POST']
 return json.loads(subprocess.check_output(cmd+['http://127.0.0.1:38383'+path],timeout=12))
def dash(path):
 with urllib.request.urlopen('http://127.0.0.1:8765'+path,timeout=5) as r:return json.load(r)
assert time.time()-json.loads((out/'reset-time.json').read_text())['recorded_wall_time']>=30
s=ref('/status');assert s['game']['state'].lower() in ('initial','finished') and s['gameControl']['ready']
d=dash('/api/state')['1'];assert d['age']<2
save('start-boundary.json',dash('/api/history?team=1&after=0&limit=1'))
save('start-response.json',ref('/match/start',True));start_wall=time.time();identity=None;initial=s['game']['regularDurationSeconds'];previous=initial;last_print=0;reason='time_limit';snapshots=[]
with (out/'status-samples.jsonl').open('w') as log:
 while time.time()-start_wall<360:
  s=ref('/status');now=time.time();log.write(json.dumps({'wall_time':now,'status':s})+'\n');log.flush()
  match=s['match'];key=match.get('startedAtWallTime')
  if identity is None:identity=key
  assert key==identity,'match identity changed'
  rem=s['game']['timeRemaining'];assert rem<=previous+.1,'clock reset';previous=rem
  if now-last_print>8:
   d=dash('/api/state')['1'];save('latest-snapshot.json',d);snapshots.append(d)
   poses=d['packet']['context']['teammates'];print('elapsed',round(initial-rem,3),'phase',s['game']['state'],'score',match['score'],'poses',[(k,round(v['pose']['x'],2),round(v['pose']['y'],2)) for k,v in poses.items()],flush=True);last_print=now
   if s['game']['state'].lower()=='playing' and initial-rem>5 and all(abs(v['pose']['y'])>4.5 for v in poses.values()):reason='invalid_outside_start';break
  if initial-rem>=60 or s['game']['state'].lower()=='finished':break
  time.sleep(.25 if initial-rem>58 else .75)
 else:reason='wall_timeout'
save('before-end.json',s);save('sampled-snapshots.json',snapshots)
if s['game']['state'].lower()!='finished':
 assert ref('/status')['match'].get('startedAtWallTime')==identity
 save('end-response.json',ref('/match/end',True))
time.sleep(.5);save('end-status.json',ref('/status'));save('end-snapshot.json',dash('/api/state'));save('end-boundary.json',dash('/api/history?team=1&after=0&limit=1'));save('result.json',ref('/result'));save('events.json',ref('/events/history'));save('run-summary.json',{'reason':reason,'elapsed_seconds':initial-rem,'match_started_at':identity,'wall_seconds':time.time()-start_wall});print('DONE',reason,initial-rem,flush=True)
