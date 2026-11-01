import json,subprocess,time,datetime
from pathlib import Path
out=Path(__file__).resolve().parent
expected_start=1793517384.3271716
initial=600.
def api(path,post=False):
 cmd=['docker','exec','4x2a0-k1','curl','--fail','--silent','--max-time','5']
 if post:cmd+=['-X','POST']
 return json.loads(subprocess.check_output(cmd+['http://127.0.0.1:38383'+path],timeout=8))
def own(s):return s.get('match',{}).get('startedAtWallTime')==expected_start and s['match'].get('matchId')=='local-match'
start=time.monotonic();last=initial;printed=-20;reason=None
with (out/'status-samples.jsonl').open('x') as f:
 while True:
  s=api('/status');wall=time.monotonic()-start
  f.write(json.dumps({'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':s})+'\n');f.flush()
  if not own(s):raise RuntimeError('Match identity changed; refusing to end another match')
  rem=s['game']['timeRemaining']
  if rem>last+.05:raise RuntimeError('Referee time moved backwards; preserve evidence')
  last=rem
  if wall-printed>=15:
   print(json.dumps({'wall_s':round(wall,1),'state':s['game']['state'],'elapsed_sim_s':round(initial-rem,3),'score':s['match']['score']}),flush=True);printed=wall
  if s['match'].get('isFinal'):
   reason='already_final';break
  if initial-rem>=60 or wall>480:
   reason='60_seconds' if initial-rem>=60 else 'wall_timeout'
   (out/'before-end.json').write_text(json.dumps(s))
   # Identity checked immediately before the single end request.
   (out/'end-response.json').write_text(json.dumps(api('/match/end',True)))
   break
  time.sleep(.2 if initial-rem>58 else .65)
for path,name in [('/status','final-status.json'),('/result','result.json'),('/events/history','events.json')]:
 try:(out/name).write_text(json.dumps(api(path)))
 except Exception as e:(out/(name+'.error.txt')).write_text(str(e))
end=json.loads((out/'final-status.json').read_text())
print(json.dumps({'stop_reason':reason,'elapsed_sim_s':initial-end['game']['timeRemaining'],'final':end['match'].get('isFinal'),'score':end['match']['score']}),flush=True)
