import json,subprocess,time,datetime,urllib.request,gzip,importlib.util,traceback
from pathlib import Path
root=Path('/Users/limingzhe/.codex/worktrees/pass-coordination/sim-3v3-simple-framework')
out=root/'docs/pass-coordination-evidence/run01'
def save(name,x): (out/name).write_text(json.dumps(x,ensure_ascii=False,indent=2))
def ref(path,post=False):
 cmd=['docker','exec','4x2a0-k1','curl','--fail','--silent','--max-time','6']
 if post: cmd+=['-X','POST']
 return json.loads(subprocess.check_output(cmd+['http://127.0.0.1:38383'+path],timeout=10))
def dash(path):
 return json.load(urllib.request.urlopen('http://127.0.0.1:8765'+path,timeout=8))
def boundary(): return {k:v for k,v in dash('/api/history?team=1&after=0&limit=1').items() if k!='frames'}
opened=datetime.datetime.fromisoformat(json.loads((out.parent/'preflight.json').read_text())['window_open_observed_at']).timestamp()
assert time.time()-opened>=60,'must wait at least 60 seconds after window opened'
before=ref('/status');save('before.json',before)
assert before['game']['state'] in ('initial','finished') and before['gameControl']['ready']
state=dash('/api/state');save('dashboard-before.json',state)
p=state['1']['packet'];assert state['1']['age']<3 and 'passing' in p
assert len(p['players'])==3 and all(p['context']['teammates'][str(i)]['pose'] for i in (1,2,3))
start_bound=boundary();save('boundary-start.json',start_bound)
start=ref('/match/start',True);save('start.json',start)
started=time.monotonic();samples=[];initial=before['game']['timeRemaining'];match_id=None;last=initial;finished=False;playing_at=None
try:
 while time.monotonic()-started<900:
  status=ref('/status');game=status['game'];match=status.get('match')
  if match_id is None and match: match_id=match
  if match_id and match and match!=match_id: raise RuntimeError('match identity changed')
  remaining=game['timeRemaining']
  if remaining>last+.1: raise RuntimeError('referee time reset')
  last=remaining
  sample={'wall':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':status}
  state=dash('/api/state');packet=state.get('1',{}).get('packet')
  if packet: sample['snapshot']=packet
  samples.append(sample)
  save('samples.json',samples)
  if len(samples)%15==0:
   print(json.dumps({'state':game['state'],'elapsed':initial-remaining,'score':[game['teams'][t]['score'] for t in ('home','away')],'passing':packet.get('passing',{}).get('active') if packet else None}),flush=True)
  if game['state']=='playing':
   if playing_at is None:
    playing_at=time.monotonic();save('opening-check.json',sample)
   # 场外出生点只作为无效启动诊断，不把受罚或正常短暂出界当失败。
   if time.monotonic()-playing_at>12 and packet and initial-remaining<15:
    points=[r['pose'] for side in ('teammates','opponents') for r in packet['context'][side].values() if r['pose']]
    if len(points)==6 and all(abs(r['y'])>packet['context']['field']['width']/2 for r in points):
     raise RuntimeError('all robots remained outside after PLAYING')
  if game['state']=='finished': finished=True;break
  if initial-remaining>=60:
   save('before-end.json',status)
   save('end-response.json',ref('/match/end',True));finished=True;break
  time.sleep(.15 if initial-remaining>57 else .5)
 if not finished: raise RuntimeError('wall timeout before required referee duration')
except Exception as exc:
 save('failure.json',{'error':str(exc),'traceback':traceback.format_exc()})
 # 只结束本脚本启动且身份未变化的比赛。
 current=ref('/status')
 if current.get('match')==match_id and current['game']['state']!='finished':
  save('failure-end.json',ref('/match/end',True))
 raise
finally:
 end=ref('/status');save('end.json',end)
 for _ in range(12):
  latest=dash('/api/state')
  if latest.get('1',{}).get('packet',{}).get('context',{}).get('game',{}).get('state')=='FINISHED':break
  time.sleep(.2)
 end_bound=boundary();save('boundary-end.json',end_bound)
 save('dashboard-end.json',latest)
 for name,path in [('result.json','/result'),('events.json','/events/history')]:
  try: save(name,ref(path))
  except Exception as e: save(name,{'error':str(e)})
 (out/'runner-after.txt').write_bytes(subprocess.check_output(['docker','exec','4x2a0-k1','cat','/tmp/booster_agent_deploy/football3v3_runner/football3v3-run.log']))
 spec=importlib.util.spec_from_file_location('exporter','/Users/limingzhe/.codex/skills/booster-sim-test/scripts/export_dashboard.py')
 exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
 replay=exporter.export('http://127.0.0.1:8765',1,start_bound.get('last_id') or 0,start_bound['session'],end_bound['last_id'])
 with gzip.open(out/'dashboard-team1.json.gz','wt') as f:json.dump(replay,f,separators=(',',':'))
 with gzip.open(out/'dashboard-team1.json.gz','rt') as f:check=json.load(f)
 save('summary.json',{'frames':len(check['frames']),'bytes':(out/'dashboard-team1.json.gz').stat().st_size,'initial':initial,'elapsed':initial-end['game']['timeRemaining'],'score':[end['game']['teams'][t]['score'] for t in ('home','away')],'end_state':end['game']['state'],'finish_reason':end['game']['finishReason'],'first_sim':check['frames'][0]['packet']['context']['now'],'last_sim':check['frames'][-1]['packet']['context']['now'],'history_preserved':True})
 print((out/'summary.json').read_text(),flush=True)
