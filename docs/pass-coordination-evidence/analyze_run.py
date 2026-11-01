import gzip,json,collections,subprocess,hashlib,datetime
from pathlib import Path
root=Path('/Users/limingzhe/.codex/worktrees/pass-coordination/sim-3v3-simple-framework');out=root/'docs/pass-coordination-evidence';run=out/'run01'
f=json.load(gzip.open(run/'dashboard-team1.json.gz','rt'))['frames']
phase=collections.Counter(x['packet']['phase'] for x in f)
events={}
for x in f:
 for e in (x['packet'].get('passing') or {}).get('events',[]):events[(e['id'],e['time'],e['event'])]=e
analysis={'phase_frames':dict(phase),'pass_events':list(events.values()),'players':{},'timeline':[]}
for pid in (1,2,3):
 ps=[next(p for p in x['packet']['players'] if p['id']==pid) for x in f]
 analysis['players'][pid]={'actions':dict(collections.Counter(p['action'] for p in ps)),'navigation_blockers':dict(collections.Counter(str((p.get('navigation') or {}).get('blocker')) for p in ps))}
for t in (49.5,54.8,69.6,73,75,77,78.9,79.2,109,114.8,115):
 x=min(f,key=lambda x:abs(x['packet']['context']['now']-t));p=x['packet'];c=p['context']
 analysis['timeline'].append({'frame':x['id'],'sim':c['now'],'phase':p['phase'],'ball':c['ball'],'robots':c['teammates'],'players':p['players'],'passing':p.get('passing')})
analysis['capture']={'first_id':f[0]['id'],'last_id':f[-1]['id'],'frames':len(f),'simulation_range':[f[0]['packet']['context']['now'],f[-1]['packet']['context']['now']],'receiver_span_seconds':f[-1]['time']-f[0]['time'],'max_sim_gap':max(b['packet']['context']['now']-a['packet']['context']['now'] for a,b in zip(f,f[1:])),'max_wall_gap':max(b['time']-a['time'] for a,b in zip(f,f[1:])),'json_bytes':len(gzip.decompress((run/'dashboard-team1.json.gz').read_bytes())),'gzip_bytes':(run/'dashboard-team1.json.gz').stat().st_size,'sha256':hashlib.sha256((run/'dashboard-team1.json.gz').read_bytes()).hexdigest(),'terminal_snapshot':f[-1]['packet']['context']['game']['state'],'continuous_ids':all(b['id']==a['id']+1 for a,b in zip(f,f[1:]))}
(run/'analysis.json').write_text(json.dumps(analysis,ensure_ascii=False,indent=2))
a=json.loads(Path('/tmp/pass-current-boundary.json').read_text());b=json.loads(Path('/tmp/pass-baseline-boundary.json').read_text())
(out/'boundary-baseline-reproduction.json').write_text(json.dumps({'baseline':'e8b4d9d','task':'0e526d3','same_results':a==b,'blocked_frames':sum(x['blocker']=='field_boundary' for x in a),'total':len(a),'current':a,'baseline_results':b},indent=2))
(out/'replay_boundary.py').write_text(Path('/tmp/replay_pass_boundary.py').read_text())
(out/'run_monitor_initial.py').write_text(Path('/tmp/run_pass_match.py').read_text())
(out/'run_monitor_resumed.py').write_text(Path('/tmp/resume_pass_match.py').read_text())
(out/'verify_identity.py').write_text(Path('/tmp/pass_identity.py').read_text())
(out/'analyze_run.py').write_text(Path('/tmp/analyze_pass_run.py').read_text())
log=subprocess.check_output(['docker','exec','4x2a0-k1','cat','/run/3v3_runner/team_1/data/var/log/booster_agent/com.example.lqinternalmatch/agent_20261101_181708.log'])
(run/'agent-log.txt.gz').write_bytes(gzip.compress(log))
analysis['log_check']={'bytes':len(log),'python_traceback':b'Traceback (most recent call last)' in log,'loco_wait_messages':log.count(b'B1LocoClient not available')}
(run/'analysis.json').write_text(json.dumps(analysis,ensure_ascii=False,indent=2))
for name in ('samples.json','samples-resumed.json'):
 (run/(name+'.gz')).write_bytes(gzip.compress((run/name).read_bytes()))
(out/'project-after-studio.json').write_bytes((root/'.booster-studio/project.json').read_bytes())
(root/'.booster-studio/project.json').write_bytes(subprocess.check_output(['git','show','HEAD:.booster-studio/project.json'],cwd=root))
identity=json.loads((out/'identity.json').read_text());identity['package_marker_exception']='src/__init__.py contains only a docstring; Studio packs an empty marker. All other 44 Python files match source/package/deployed bytes.';(out/'identity.json').write_text(json.dumps(identity,indent=2))
print(json.dumps(analysis['capture'],indent=2));print(analysis['log_check'])
