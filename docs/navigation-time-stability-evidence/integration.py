import math,json
from checks.test_navigation import context,controller
from src.intents import MoveIntent
from src.utils.navigation import MotionTracker,NavigationFrame
out={}
for kind in ('team','opponent'):
 pos=[-2.,0.,0.]; c=controller(context((tuple(pos),))); tracker=MotionTracker(); history=[]
 for i in range(800):
  obs=(0.,.08*math.sin(i*.12),0.)
  ctx=context((tuple(pos),obs) if kind=='team' else (tuple(pos),),opponents=(obs,) if kind=='opponent' else (),now=1+i*.05)
  c.context=ctx;tracker.update(ctx);f=NavigationFrame(ctx,tracker)
  cmd,arrived=c.plan_walk(MoveIntent((2.,0.)),f)
  history.append(dict(frame=i,time=ctx.now,pose=pos.copy(),command=cmd,state=c.navigation_status['state']))
  if arrived:break
  vx,vy,yaw=cmd;theta=pos[2]
  # Explicit surrogate only: assume commands achieve 37.5% translation and 50% rotation.
  pos[0]+=(vx*math.cos(theta)-vy*math.sin(theta))*.05*.375
  pos[1]+=(vx*math.sin(theta)+vy*math.cos(theta))*.05*.375
  pos[2]+=yaw*.05*.5
 out[kind]=dict(arrived=arrived,steps=i+1,final_pose=pos,history=history)
print(json.dumps(out,ensure_ascii=False,indent=2))
