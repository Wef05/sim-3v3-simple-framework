import gzip,json,os,sys
sys.path.insert(0,os.getcwd())
from src.framework.types import Context,FieldDimensions,RobotState,Pose2D,BallState,GameControlState,GameState
from src.actions.motion_controller import MotionController
from src.framework.config import SoccerConfig
from src.utils.navigation import MotionTracker,NavigationFrame
from src.intents import MoveIntent
frames=json.load(gzip.open('/Users/limingzhe/.codex/worktrees/pass-coordination/sim-3v3-simple-framework/docs/pass-coordination-evidence/run01/dashboard-team1.json.gz','rt'))['frames']
tracker=MotionTracker();controller=MotionController(1,SoccerConfig(),None);controller._mode='walk';rows=[]
for frame in frames[:60]:
 c=frame['packet']['context']
 def robots(side):return {int(i):RobotState(**dict(r,pose=Pose2D(**r['pose']) if r['pose'] else None)) for i,r in c[side].items()}
 ctx=Context(c['now'],c['dt'],c['team_id'],FieldDimensions(**c['field']),ball=BallState(**c['ball']) if c['ball'] else None,teammates=robots('teammates'),opponents=robots('opponents'),game=GameControlState(state=GameState.READY,kicking_team=1))
 tracker.update(ctx);controller.context=ctx
 command,_=controller.plan_walk(MoveIntent((-1.5,0.),face=0.,avoid_ball=True,avoid_robots=True),NavigationFrame(ctx,tracker))
 rows.append({'frame':frame['id'],'sim':ctx.now,'velocity':tracker.velocity('team',1),'command':command,'state':controller.navigation_status.get('state'),'blocker':controller.navigation_status.get('blocker')})
print(json.dumps(rows))
