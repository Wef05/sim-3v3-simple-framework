import ast,hashlib,json,subprocess,zipfile,time
from pathlib import Path
r=Path.cwd();out=r/'docs/bounded-auto-kick-evidence/run02'
package=r/'build/com.example.lqinternalmatch-2.0.3.agent'
prefix='agent/com_example_lqinternalmatch/lib/python3.10/site-packages/com_example_lqinternalmatch/'
base='/opt/booster/booster_agent_data/data/agents/extract/com.example.lqinternalmatch/'
hashcode="import pathlib,hashlib,json;p=pathlib.Path("+repr(base+prefix)+");print(json.dumps({str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*.py')}))"
deployed=json.loads(subprocess.check_output(['docker','exec','4x2a0-k1','python3','-c',hashcode]))
records=[]
with zipfile.ZipFile(package) as z:
 for f in sorted((r/'src').rglob('*.py')):
  rel=str(f.relative_to(r/'src')); raw=f.read_bytes(); built=z.read(prefix+rel)
  doc_only=(rel=='__init__.py' and all(isinstance(n,ast.Expr) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str) for n in ast.parse(raw).body) and built==b'')
  sha=hashlib.sha256(raw).hexdigest();bsha=hashlib.sha256(built).hexdigest()
  assert raw==built or doc_only, rel+' build mismatch'
  assert deployed[rel]==bsha, rel+' deployment mismatch'
  records.append(dict(file='src/'+rel,source_sha256=sha,package_sha256=bsha,deployed_sha256=deployed[rel],docstring_only_build_difference=doc_only))
identity=dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),branch=subprocess.check_output(['git','branch','--show-current'],text=True).strip(),worktree=str(r),package=str(package),package_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),files=records,verified_at=time.time(),window_open_observed_wall_time=json.loads((out/'preparation.json').read_text())['window_open_observed_wall_time'])
(out/'identity.json').write_text(json.dumps(identity,indent=2));print('verified',len(records),'Python files')
(out/'source.patch').write_bytes(subprocess.check_output(['git','diff','ddf350a','HEAD','--','src']))
(out/'working-tree.patch').write_bytes(subprocess.check_output(['git','diff']))
