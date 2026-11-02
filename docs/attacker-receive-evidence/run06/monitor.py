import subprocess,json,time,urllib.request,datetime
from pathlib import Path
out=Path('/private/tmp/attacker-receive-first/docs/attacker-receive-evidence/run06');out.mkdir(exist_ok=True)
def save(name,value):
    (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
def ref(path,post=False):
    cmd=['docker','exec','4x2a0-k1','curl','--fail','--silent','--max-time','8']
    if post:cmd+=['-X','POST']
    return json.loads(subprocess.check_output(cmd+['http://127.0.0.1:38383/'+path]))
def dash(path):
    return json.load(urllib.request.urlopen('http://127.0.0.1:8765/'+path,timeout=8))
def boundary():
    h=dash('api/history?team=1&after=0&limit=1');return {k:v for k,v in h.items() if k!='frames'}
prep=json.loads((out/'preparation.json').read_text())
assert time.time()-prep['reset_observed_wall'] >= 30, '30-second readiness wait not completed'
s=ref('status');save('before.json',s)
assert s['game']['state'] in ('initial', 'finished')
assert s['match'] is None or s['match']['status']=='finished'
old_start = (s['match'] or {}).get('startedAtWallTime')
assert s['gameControl']['ready'] and all(s['gameControl']['checks'].values())
d=dash('api/state');assert d['1']['age']<2;save('dashboard-before.json',d)
save('start-boundary.json',boundary());start=ref('match/start',True);save('start-response.json',start)
s=ref('status');assert s['match']['startedAtWallTime'] != old_start
assert s['game']['timeRemaining'] == s['game']['regularDurationSeconds']
initial=s['game']['timeRemaining'];deadline=time.monotonic()+600;ident=None;previous=initial;last_print=0;playing_recorded=False
with (out/'status-samples.jsonl').open('w') as f:
    while time.monotonic()<deadline:
        s=ref('status');m=s['match'];remaining=s['game']['timeRemaining'];state=s['game']['state']
        row={'wall_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':s};f.write(json.dumps(row)+'\n');f.flush()
        if ident is None and m is not None:
            ident=m;save('match-identity.json',m);print('identity',json.dumps(m),flush=True)
        if remaining>previous+.1:raise RuntimeError('clock rollback; no end issued')
        previous=remaining
        if time.monotonic()-last_print>10:
            d=dash('api/state');p=d['1']['packet'];save('dashboard-latest.json',d)
            print(json.dumps({'elapsed':initial-remaining,'state':state,'score':[s['game']['teams'][k]['score'] for k in ('home','away')],'robots':{side:{k:[round(v['pose']['x'],2),round(v['pose']['y'],2)] for k,v in p['context'][side].items() if v['pose']} for side in ('teammates','opponents')},'actions':[(v['id'],v['action'],(v.get('attack_receive') or {}).get('stage')) for v in p['players']]}),flush=True)
            last_print=time.monotonic()
            if state=='playing' and not playing_recorded:save('playing-entry.json',d);playing_recorded=True
        if state=='finished':break
        if initial-remaining>=60:
            # Identity fields remain constant; compare id keys if exposed, retain all raw samples.
            if ident is None:raise RuntimeError('no match identity')
            for key in ('id','matchId','match_id','startedAtWallTime'):
                if key in ident and m.get(key)!=ident[key]:raise RuntimeError('different match; no end issued')
            save('end-response.json',ref('match/end',True));break
        time.sleep(.25 if initial-remaining>58 else .5)
    else:
        save('timeout-status.json',s)
        raise RuntimeError('time limit reached; manual ownership check required')
time.sleep(1)
save('end-status.json',ref('status'));save('end-boundary.json',boundary())
for ep,name in [('result','result.json'),('events/history','events.json')]:
    try:save(name,ref(ep))
    except Exception as e:save(name,{'error':str(e)})
print('DONE',flush=True)
