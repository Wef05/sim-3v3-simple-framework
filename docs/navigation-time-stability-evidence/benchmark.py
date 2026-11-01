import json, math, statistics, time
from checks.test_navigation import context,controller
from src.intents import MoveIntent
from src.utils.navigation import MotionTracker,NavigationFrame
out={}
for kind in ('team','opponent'):
 tracker=MotionTracker(); c=controller(context(((-2.,0.,0.),))); records=[]
 for i in range(160):
  moving=(0.,.08*math.sin(i*.6),0.)
  ctx=context(((-2.,0.,0.),moving) if kind=='team' else ((-2.,0.,0.),),opponents=(moving,) if kind=='opponent' else (),now=1+i*.05)
  c.context=ctx;tracker.update(ctx);frame=NavigationFrame(ctx,tracker)
  tick=time.perf_counter();cmd,_=c.plan_walk(MoveIntent((2.,0.)),frame);ms=(time.perf_counter()-tick)*1000
  info=c.navigation_status; w=info.get('waypoint');
  records.append(dict(frame=i,time=ctx.now,obstacle=list(moving),waypoint=w,command=list(cmd),route_reason=info.get('route_reason'),cost_ms=ms,side=0 if not w else 1 if w[1]>0 else -1))
 sides=[r['side'] for r in records];ts=sorted(r['cost_ms'] for r in records)
 out[kind]=dict(switches=sum(a!=b for a,b in zip(sides,sides[1:])),mean_ms=statistics.mean(ts),p95_ms=ts[int(.95*len(ts))-1],max_ms=max(ts),records=records)
print(json.dumps(out,ensure_ascii=False,indent=2))
