import json,subprocess,time,urllib.request
from pathlib import Path
out=Path(__file__).resolve().parent

def save(name,data): (out/name).write_text(json.dumps(data,indent=2))
def api(path,method='GET'):
    return json.loads(subprocess.check_output(['docker','exec','4x2a0-k1','curl','--fail','--silent','--max-time','8','-X',method,'http://127.0.0.1:38383'+path]))
def http(path): return json.load(urllib.request.urlopen('http://127.0.0.1:8765'+path,timeout=6))
def identity(s):
    m=s.get('match') or {};return m.get('matchId'),m.get('startedAtWallTime')
# Wait after the official reset, then independently verify readiness.
reset=json.loads((out/'reset-observed.json').read_text())
time.sleep(max(0,30-(time.time()-reset['wall_time'])))
pre=api('/status');save('pre-start-status.json',pre)
assert pre['game']['state'] in ('initial','finished'), 'another test may be active'
state=http('/api/state');save('ready-state.json',state)
assert state['1']['age'] < 2
assert all(p['mode']=='walk' for p in state['1']['packet']['players'])
assert pre['gameControl']['ready'] and all(pre['gameControl']['checks'].values())
log=subprocess.check_output(['docker','exec','4x2a0-k1','cat','/tmp/booster_agent_deploy/football3v3_runner/football3v3-run.log'],text=True)
assert '[team_1]' in log and '[team_2]' in log and log.count('Inactive=>Active')>=2
boundary=http('/api/history?team=1&after=0&limit=1')
save('start-boundary.json',{k:v for k,v in boundary.items() if k!='frames'})
save('start-request.json',dict(wall_time=time.time(),wait_since_reset_s=time.time()-reset['wall_time']))
save('start-response.json',api('/match/start','POST'))
s=api('/status'); own=identity(s);save('first-status.json',s)
assert own[1] is not None and own!=identity(pre)
initial=s['game']['regularDurationSeconds']; last=initial;samples=[];states=[];begin=time.monotonic();next_print=0
while True:
    s=api('/status'); assert identity(s)==own,'match identity changed'
    remaining=s['game']['timeRemaining'];assert remaining<=last+.05,'match timer reset'
    last=remaining; elapsed=initial-remaining
    samples.append(dict(wall_time=time.time(),status=s));save('status-samples.json',samples)
    state=http('/api/state');states.append(dict(wall_time=time.time(),state=state));save('state-samples.json',states)
    if time.monotonic()-begin>=next_print:
        p=state['1']['packet'];poses=p['context']['teammates']
        print(json.dumps(dict(elapsed=round(elapsed,3),phase=s['game']['state'],score=[s['game']['teams'][x]['score'] for x in ('home','away')],positions={k:v['pose'] for k,v in poses.items()})),flush=True)
        next_print+=15
    if elapsed>=60:
        assert identity(api('/status'))==own
        save('end-request.json',dict(wall_time=time.time(),elapsed_s=elapsed,identity=own))
        save('end-response.json',api('/match/end','POST'));break
    assert s['game']['state']!='finished','premature finish'
    assert time.monotonic()-begin<420,'finite wait exceeded'
    time.sleep(.3 if elapsed>58 else .7)
end=api('/status');save('end-status.json',end);save('result.json',api('/result'));save('events.json',api('/events/history'))
time.sleep(1)
boundary=http('/api/history?team=1&after=0&limit=1');save('end-boundary.json',{k:v for k,v in boundary.items() if k!='frames'})
save('final-state.json',http('/api/state'))
(out/'runner.log').write_bytes(subprocess.check_output(['docker','exec','4x2a0-k1','cat','/tmp/booster_agent_deploy/football3v3_runner/football3v3-run.log']))
print('FINISHED',round(initial-end['game']['timeRemaining'],3),end['game']['finishReason'],flush=True)
