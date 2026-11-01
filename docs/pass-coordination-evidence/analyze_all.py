import json,gzip,collections,hashlib
from pathlib import Path
root=Path('/Users/limingzhe/.codex/worktrees/pass-coordination/sim-3v3-simple-framework')
base=root/'docs/pass-coordination-evidence'
for name in ('run01','run02','run03'):
 run=base/name
 if not (run/'summary.json').exists():continue
 replay=json.load(gzip.open(run/'dashboard-team1.json.gz','rt'));frames=replay['frames'];events={};stages=collections.Counter();roles={i:collections.Counter() for i in (1,2,3)};rows=[]
 for f in frames:
  p=f['packet'];team=p.get('passing') or {};plan=team.get('active')
  stages[plan['stage'] if plan else 'none']+=1
  for e in team.get('events',[]):events[(e['id'],e['time'],e['event'])]=e
  for player in p['players']:roles[player['id']][player['action']]+=1
 referee=json.loads((run/'events.json').read_text()).get('events',[])
 selected_times=sorted(set([e['time'] for e in events.values()]+[e['simTime'] for e in referee if e.get('simTime') is not None and e['type'] in ('ball_touch','goal','player_penalized','robot_removed','match_finished')]))
 for t in selected_times:
  f=min(frames,key=lambda f:abs(f['packet']['context']['now']-t));rows.append({'frame':f['id'],'sim':f['packet']['context']['now'],'packet_location':'dashboard-team1.json.gz'})
 capture={'frames':len(frames),'first_id':frames[0]['id'],'last_id':frames[-1]['id'],'first_sim':frames[0]['packet']['context']['now'],'last_sim':frames[-1]['packet']['context']['now'],'receiver_span_s':frames[-1]['time']-frames[0]['time'],'max_sim_gap_s':max(b['packet']['context']['now']-a['packet']['context']['now'] for a,b in zip(frames,frames[1:])),'max_receiver_gap_s':max(b['time']-a['time'] for a,b in zip(frames,frames[1:])),'raw_bytes':len(gzip.decompress((run/'dashboard-team1.json.gz').read_bytes())),'gzip_bytes':(run/'dashboard-team1.json.gz').stat().st_size,'sha256':hashlib.sha256((run/'dashboard-team1.json.gz').read_bytes()).hexdigest(),'terminal_snapshot':frames[-1]['packet']['context']['game']['state']}
 result={'summary':json.loads((run/'summary.json').read_text()),'capture':capture,'passing_stage_frames':dict(stages),'pass_events':list(events.values()),'actions':{str(i):dict(c) for i,c in roles.items()},'referee_events':referee,'selected_frames':rows}
 (run/'passing-analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 exports=root/'results/pass-coordination'/name;exports.mkdir(parents=True,exist_ok=True);(exports/'dashboard-team1.json').write_bytes(gzip.decompress((run/'dashboard-team1.json.gz').read_bytes()))
 print(name,'capture',capture,'events',list(events.values()))
