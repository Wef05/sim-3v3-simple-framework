import json,subprocess,time,urllib.request
from pathlib import Path
out=Path(__file__).resolve().parent

def referee(path,post=False):
    args=['docker','exec','4x2a0-k1','curl','-sf','--max-time','8']
    if post:args+=['-X','POST']
    return json.loads(subprocess.check_output(args+['http://127.0.0.1:38383'+path],text=True))
def dashboard(path):
    return json.load(urllib.request.urlopen('http://127.0.0.1:8765'+path,timeout=5))
def save(name,data):
    (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2))
before=referee('/status');save('before-start.json',before)
assert before['match'] is None and before['game']['state']=='initial', 'Another match exists'
assert before['gameControl']['ready']
state=dashboard('/api/state');save('before-snapshot.json',state)
assert state['1']['age']<3, 'Stale telemetry'
h=dashboard('/api/history?team=1&after=0&limit=1');save('start-boundary.json',{k:v for k,v in h.items() if k!='frames'})
log=subprocess.check_output(['docker','exec','4x2a0-k1','cat','/tmp/booster_agent_deploy/football3v3_runner/football3v3-run.log'],text=True)
(out/'runner-before.txt').write_text(log)
assert log.count('Inactive=>Active')>=2
save('start-response.json',referee('/match/start',True))
initial=before['game']['timeRemaining'];started=time.monotonic();match_id=None
with (out/'status-samples.jsonl').open('w') as f:
    while time.monotonic()-started<240:
        s=referee('/status');f.write(json.dumps(s)+'\n');f.flush()
        if match_id is None:match_id=s['match']
        elapsed=initial-s['game']['timeRemaining']
        print(s['game']['state'],round(elapsed,3),flush=True)
        if s['game']['state']=='finished':break
        if elapsed>=60:
            save('before-end.json',s);save('end-response.json',referee('/match/end',True));break
        time.sleep(.3 if elapsed>58 else .7)
    else:
        save('timeout-status.json',s)
        save('end-response.json',referee('/match/end',True))
time.sleep(.5)
save('final-status.json',referee('/status'))
h=dashboard('/api/history?team=1&after=0&limit=1');save('end-boundary.json',{k:v for k,v in h.items() if k!='frames'})
save('final-snapshot.json',dashboard('/api/state'))
save('result.json',referee('/result'));save('events.json',referee('/events/history'))
(out/'runner-after.txt').write_text(subprocess.check_output(['docker','exec','4x2a0-k1','cat','/tmp/booster_agent_deploy/football3v3_runner/football3v3-run.log'],text=True))
