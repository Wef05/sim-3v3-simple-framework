"""核对实际低速直通，保留全部违反候选而非只选成功片段。"""
from pathlib import Path
import json, gzip, collections
p=Path(__file__).resolve().parent
frames=json.loads(gzip.decompress((p/'dashboard-team1.json.gz').read_bytes()))['frames']
entries=[]; violations=[]; low_frames=0; low_chase=0
for f in frames:
    for player in f['packet']['players']:
        r=player.get('attack_receive') or {}; speed=r.get('speed_mps')
        if isinstance(speed,(int,float)) and 0 <= speed < .5:
            low_frames+=1
            if r.get('stage') in ('intercept','wait_ball'):
                violations.append(dict(frame=f['id'],sim=f['packet']['context']['now'],player=player['id'],receive=r))
        if r.get('stage')=='low_speed_chase':low_chase+=1
        if r.get('reason')=='low_speed_direct':
            entries.append(dict(frame=f['id'],sim=f['packet']['context']['now'],player=player['id'],receive=r))
result=dict(low_speed_frames=low_frames,low_chase_frames=low_chase,direct_entries=entries,low_speed_intercept_violations=violations)
(p/'low-speed-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False))
