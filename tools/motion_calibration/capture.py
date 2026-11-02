"""Continuously preserve raw dashboard frames before its in-memory retention expires."""
import argparse,json,time,urllib.request,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--boundary',required=True);p.add_argument('--out',required=True);p.add_argument('--run',required=True);args=p.parse_args()
b=json.loads(Path(args.boundary).read_text());last=b['last_id'];out=Path(args.out);out.mkdir(exist_ok=True)
previous=None;started=time.monotonic()
with (out/'frames.jsonl').open('x') as f,(out/'status.jsonl').open('x') as sf:
 while time.monotonic()-started<7200:
  try:
   while True:
    with urllib.request.urlopen(f'http://127.0.0.1:8765/api/history?team=1&after={last}&limit=500',timeout=8) as r:d=json.load(r)
    if d['session']!=b['session']:raise RuntimeError('dashboard session changed')
    rows=d.get('frames',[])
    for row in rows:f.write(json.dumps(row,separators=(',',':'))+'\n');last=row['id']
    f.flush()
    if len(rows)<500:break
   s=json.loads(subprocess.check_output(['docker','exec','4x2a0-k1','cat','/tmp/soccer-motion-calibration-status.json'],timeout=8))
   sf.write(json.dumps(dict(wall=time.time(),status=s,last_id=last))+'\n');sf.flush()
   key=(s.get('run_id'),s.get('trial'),s['state'])
   if key!=previous:print(json.dumps(s),flush=True);previous=key
   if s.get('run_id')==args.run and s['state'] in ('done','aborted'):
    (out/'end-boundary.json').write_text(json.dumps(dict(session=b['session'],last_id=last,status=s)))
    break
  except Exception as e:
   print(type(e).__name__+': '+str(e),flush=True)
  time.sleep(10)
 else:raise RuntimeError('capture wall-time limit exceeded')
