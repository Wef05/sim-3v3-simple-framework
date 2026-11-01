import json,gzip,time
from types import SimpleNamespace as NS
from dataclasses import asdict
from src import coordination as C
from src.utils import receive_pose as R
from src.framework.types import *
frames=json.load(gzip.open('docs/receive-safety-evidence/run02/dashboard-team1.json.gz','rt'))['frames']
f=next(f for f in frames if f['id']==494401);d=f['packet']['context']
robots=lambda key:{int(i):RobotState(int(i),Pose2D(**v['pose']) if v['pose'] else None,last_seen_at=d['now']) for i,v in d[key].items()}
ctx=Context(d['now'],.03,1,ADULT_FIELD_DIMENSIONS,ball=BallState(d['ball']['x'],d['ball']['y'],last_seen_at=d['now']),teammates=robots('teammates'),opponents=robots('opponents'),game=GameControlState(state=GameState.PLAYING,kicking_team=1,secondary_time=10))
ps=[NS(id=i,pose=ctx.teammates[i].pose) for i in (1,3)]
state=C.TeamPassing();t=time.perf_counter();result=C._choose_pose(ctx,*ps,(ps[1].pose.x,ps[1].pose.y),state);elapsed=time.perf_counter()-t
out={'source_frame':494401,'baseline':'6b41db7','runtime_s':elapsed,'selection':asdict(state.selection)}
open('docs/receive-pose-evidence/opening-reconstruction.json','w').write(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
print(elapsed,result)
from collections import Counter
print(Counter(r['reason'] for r in state.selection.candidates))
