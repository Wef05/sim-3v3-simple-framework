import subprocess,json,hashlib,zipfile,datetime
from pathlib import Path
root=Path('/Users/limingzhe/.codex/worktrees/pass-coordination/sim-3v3-simple-framework')
out=root/'docs/pass-coordination-evidence'
archive=root/'build/com.example.lqinternalmatch-2.0.3.agent'
prefix='agent/com_example_lqinternalmatch/lib/python3.10/site-packages/com_example_lqinternalmatch/'
remote='/opt/booster/booster_agent_data/data/agents/extract/com.example.lqinternalmatch/'+prefix
script='''import pathlib,json,hashlib
p=pathlib.Path(%r)
print(json.dumps({str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*.py')}))
'''%remote
actual=json.loads(subprocess.check_output(['docker','exec','4x2a0-k1','python3','-c',script],text=True))
records=[]
with zipfile.ZipFile(archive) as z:
 for f in sorted((root/'src').rglob('*.py')):
  rel=str(f.relative_to(root/'src'));local=hashlib.sha256(f.read_bytes()).hexdigest()
  packed=hashlib.sha256(z.read(prefix+rel)).hexdigest()
  records.append(dict(path=rel,source=local,package=packed,deployed=actual.get(rel),match=local==packed==actual.get(rel)))
report=dict(checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),package=str(archive),package_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),deployed_path=remote,files=records,dirty=subprocess.check_output(['git','status','--short'],cwd=root,text=True))
(out/'identity.json').write_text(json.dumps(report,indent=2))
(out/'run01/runner-before.txt').write_bytes(subprocess.check_output(['docker','exec','4x2a0-k1','cat','/tmp/booster_agent_deploy/football3v3_runner/football3v3-run.log']))
print('files',len(records),'differences',[r['path'] for r in records if not r['match']])
