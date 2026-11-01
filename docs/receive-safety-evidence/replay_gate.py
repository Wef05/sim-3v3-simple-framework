"""原始轨迹固定的门槛回放，不模拟新控制动作对物理轨迹的影响。"""
import gzip,json,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from src import receiving as R
from src.framework.types import Context,FieldDimensions,RobotState,Pose2D,BallState
from src.intents import KickIntent,KickTarget
root=Path(__file__).resolve().parents[2]
frames=json.load(gzip.open(root/'docs/pass-coordination-evidence/run03/dashboard-team1.json.gz','rt'))['frames']
rows=[];previous=None;guard=None;reception=R.Reception()
for f in frames:
 p=f['packet'];c=p['context'];t=c['now'];pose=Pose2D(**c['teammates']['3']['pose'])
 if t<119:previous=(t,pose);continue
 if t>122.4:break
 velocity=(0.,0.) if previous is None else ((pose.x-previous[1].x)/(t-previous[0]),(pose.y-previous[1].y)/(t-previous[0]))
 previous=(t,pose)
 ctx=Context(t,c['dt'],c['team_id'],FieldDimensions(**c['field']),ball=BallState(**c['ball']))
 motion=SimpleNamespace(ball_prediction_details=lambda:p['ball_prediction'],predict_ball_position=lambda dt:(ctx.ball.x,ctx.ball.y))
 player=SimpleNamespace(id=3,pose=pose,motion=motion,teammate_motion=SimpleNamespace(velocities={3:velocity}),ball_intercept=p['players'][2]['intercept'])
 if t>=119.622 and t<121.93:
  intent,outcome=R.receive(ctx,player,reception)
  rows.append(dict(frame=f['id'],sim=t,phase=reception.phase,limit=reception.speed_limit,outcome=outcome))
 if t>=121.93:
  if guard is None:guard=R.ExitProtection(3,1,('normal','NONE',True),123.13,'historical_cancel')
  intent,allowed=R.protect_exit(ctx,player,guard,KickIntent(KickTarget.goal()))
  assert not allowed and not isinstance(intent,KickIntent)
  rows.append(dict(frame=f['id'],sim=t,original_kicking=p['players'][2]['kicking'],new_intent=type(intent).__name__,reason=guard.reason))
result={'source':'run03 fixed historical trajectory','limitation':'Cannot establish collision avoidance or success of changed dynamics; robot velocity estimated from exported pose differences.','rows':rows}
(root/'docs/receive-safety-evidence/replay-gate.json').write_text(json.dumps(result,indent=2))
print('Historical kick frames blocked:',sum(r.get('original_kicking',False) for r in rows))
