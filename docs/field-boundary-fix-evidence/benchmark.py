import sys,time,json,statistics
sys.path.insert(0,sys.argv[1])
from src.framework.types import Context,ADULT_FIELD_DIMENSIONS
from src.utils.obstacles import Obstacle,goal_obstacles
from src.utils.path_planner import plan_global_path
ctx=Context(1,.05,1,ADULT_FIELD_DIMENSIONS)
base=goal_obstacles(ctx)
cases=[('clear',(-2.,0.),(2.,0.),base),('central_obstacle',(-2.,0.),(2.,0.),base+[Obstacle(0,0,.3)]),('outside_detour',(-1.,5.2),(1.,5.2),base+[Obstacle(0,5.2,.3)])]
rows=[]
for name,a,b,obs in cases:
 values=[]
 for _ in range(7):
  t=time.perf_counter();p=plan_global_path(ctx,a,b,obs);values.append(1000*(time.perf_counter()-t))
 rows.append(dict(case=name,median_ms=statistics.median(values),max_ms=max(values),target_kept=p is not None and p[-1]==b,path_nodes=len(p) if p else None))
print(json.dumps(rows,indent=2))
