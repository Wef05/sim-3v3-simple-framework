import json,subprocess,time,datetime,urllib.request,gzip,importlib.util,traceback
from pathlib import Path
root=Path('/Users/limingzhe/.codex/worktrees/pass-coordination/sim-3v3-simple-framework');out=root/'docs/receive-pose-evidence/run01'
def save(name,x): (out/name).write_text(json.dumps(x,ensure_ascii=False,indent=2))
def ref(path,post=False):
 cmd=['docker','exec','4x2a0-k1','curl','--fail','--silent','--max-time','6']
 if post:cmd+=['-X','POST']
 return json.loads(subprocess.check_output(cmd+['http://127.0.0.1:38383'+path],timeout=10))
def dash(path):return json.load(urllib.request.urlopen('http://127.0.0.1:8765'+path,timeout=8))
def boundary():return {k:v for k,v in dash('/api/history?team=1&after=0&limit=1').items() if k!='frames'}
def identity(match):return (match.get('matchId'),match.get('startedAtWallTime')) if match else None
reset=datetime.datetime.fromisoformat(json.loads((out/'window-open.json').read_text())['observed_at']).timestamp()
assert time.time()-reset>=60,'60 second settling period not reached'
before=ref('/status');save('before.json',before);assert before['game']['state'] in ('initial','finished') and before['gameControl']['ready']
state=dash('/api/state');save('dashboard-before.json',state);p=state['1']['packet']
assert state['1']['age']<3 and 'passing' in p
assert len(p['players'])==3 and all(r['mode']=='walk' and not r['fallen'] for r in p['players'])
start_bound=boundary();save('boundary-start.json',start_bound)
save('start.json',ref('/match/start',True))
samples=[];started=time.monotonic();first=ref('/status');initial=first['game']['timeRemaining'];last=initial;match_id=identity(first.get('match'));assert match_id
save('match-identity.json',{'match_id':match_id,'commit':'50bdc92','started_wall':datetime.datetime.now(datetime.timezone.utc).isoformat()})
valid_opening=None;reason=None
try:
 while time.monotonic()-started<900:
  status=ref('/status');game=status['game']
  assert identity(status.get('match'))==match_id,'match identity changed'
  remaining=game['timeRemaining'];assert remaining<=last+.1,'referee clock reset';last=remaining
  state=dash('/api/state');packet=state.get('1',{}).get('packet')
  sample={'wall':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':status,'snapshot':packet,'snapshot_age':state.get('1',{}).get('age')}
  samples.append(sample);save('samples.json',samples)
  if len(samples)%15==0:print(json.dumps({'state':game['state'],'elapsed':initial-remaining,'score':status['match']['score'],'valid_opening':valid_opening}),flush=True)
  if game['state']=='playing' and valid_opening is None:
   assert packet and state['1']['age']<3,'no fresh opening snapshot'
   poses={int(i):r['pose'] for i,r in packet['context']['teammates'].items()}
   field=packet['context']['field'];targets={1:(-field['circle_radius'],0),2:(-field['length']/2+field['goal_area_length'],0),3:(-.5,field['circle_radius']+2)}
   import math
   distances={i:math.dist((pose['x'],pose['y']),targets[i]) if pose else None for i,pose in poses.items()}
   valid_opening=(len(poses)==3 and all(pose and abs(pose['x'])<field['length']/2 and abs(pose['y'])<field['width']/2 and distances[i]<=.8 for i,pose in poses.items()) and all(r['mode']=='walk' and not r['fallen'] and not r['penalized'] for r in packet['players']))
   enemy_poses={i:r['pose'] for i,r in packet['context']['opponents'].items()}
   enemies_valid=len(enemy_poses)==3 and all(pose and abs(pose['x'])<field['length']/2 and abs(pose['y'])<field['width']/2 for pose in enemy_poses.values())
   valid_opening=valid_opening and enemies_valid
   save('opening-check.json',{'valid':valid_opening,'distance_to_ready_m':distances,'opponents_inside':enemies_valid,'sample':sample})
   if not valid_opening:
    reason='own_robots_not_ready_at_playing';save('invalid-opening.json',{'reason':reason});save('end-response.json',ref('/match/end',True));break
  if game['state']=='finished':reason='natural_or_external_finish';break
  if initial-remaining>=60:
   reason='requested_60_seconds';save('before-end.json',status);save('end-response.json',ref('/match/end',True));break
  time.sleep(.15 if initial-remaining>57 else .5)
 else:raise RuntimeError('wall timeout')
except Exception as exc:
 reason='monitor_failure';save('failure.json',{'error':str(exc),'traceback':traceback.format_exc()})
 current=ref('/status')
 if identity(current.get('match'))==match_id and current['game']['state']!='finished':save('failure-end.json',ref('/match/end',True))
 raise
finally:
 end=ref('/status');save('end.json',end)
 for _ in range(12):
  latest=dash('/api/state')
  if latest.get('1',{}).get('packet',{}).get('context',{}).get('game',{}).get('state')=='FINISHED':break
  time.sleep(.2)
 end_bound=boundary();save('boundary-end.json',end_bound);save('dashboard-end.json',latest)
 for name,path in [('result.json','/result'),('events.json','/events/history')]:
  try:save(name,ref(path))
  except Exception as e:save(name,{'error':str(e)})
 (out/'runner.txt').write_bytes(subprocess.check_output(['docker','exec','4x2a0-k1','cat','/tmp/booster_agent_deploy/football3v3_runner/football3v3-run.log']))
 spec=importlib.util.spec_from_file_location('exporter','/Users/limingzhe/.codex/skills/booster-sim-test/scripts/export_dashboard.py');exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
 replay=exporter.export('http://127.0.0.1:8765',1,start_bound.get('last_id') or 0,start_bound['session'],end_bound['last_id'])
 with gzip.open(out/'dashboard-team1.json.gz','wt') as f:json.dump(replay,f,separators=(',',':'))
 with gzip.open(out/'dashboard-team1.json.gz','rt') as f:check=json.load(f)
 save('summary.json',{'frames':len(check['frames']),'bytes':(out/'dashboard-team1.json.gz').stat().st_size,'elapsed':initial-end['game']['timeRemaining'],'score':end['match']['score'],'end_state':end['game']['state'],'finish_reason':end['game']['finishReason'],'valid_opening':valid_opening,'reason':reason,'first_sim':check['frames'][0]['packet']['context']['now'],'last_sim':check['frames'][-1]['packet']['context']['now'],'history_preserved':True})
 print((out/'summary.json').read_text(),flush=True)
