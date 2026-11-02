import math, random, subprocess, time, json, sys
from types import SimpleNamespace as S
from collections import Counter
from src.utils.interception import estimate_ball_intercept as new
from src.utils.ball_motion import BallMotion
from src.framework.types import Pose2D
from src.framework.config import SoccerConfig
from src.player import Player
from src.framework import decision_frame
from src.utils import threat
sys.path.insert(0,'checks')
from test_navigation import context
from src.framework.types import BallState
ns={'__package__':'src.utils'}
exec(subprocess.check_output(['git','show','aaee819:src/utils/interception.py'],text=True),ns)
old=ns['estimate_ball_intercept']
rng=random.Random(42)
cases=[]
for i in range(100):
    m=BallMotion(); x,y=rng.uniform(-6,6),rng.uniform(-4,4); vx,vy=rng.uniform(-15,15),rng.uniform(-10,10)
    for t in (0.,.05):
        m.update(S(now=t,ball=S(x=x+vx*t,y=y+vy*t,last_seen_at=t)))
    cases.append((Pose2D(rng.uniform(-6,6),rng.uniform(-4,4),rng.uniform(-math.pi,math.pi)),m,(None,(1.,0.),(0.,-1.))[i%3]))
summary={}
for name,fn in [('old_algorithm_new_parameters',old),('new',new)]:
    samples=[]; counts=Counter(); ev=[]
    for repeat in range(10):
        for p,m,n in cases:
            t=time.perf_counter();r=fn(p,m,approach_normal=n);samples.append((time.perf_counter()-t)*1000)
            if repeat==0: counts[r['status']]+=1;ev.append(r.get('evaluations',0))
    samples.sort();summary[name]={'mean_ms':sum(samples)/len(samples),'p95_ms':samples[int(.95*len(samples))],'max_ms':max(samples),'statuses':dict(counts),'max_evaluations':max(ev)}
ctx=context(((-2.,0.),(-1.,1.),(0.,-1.)),opponents=((1.,0.),(2.,1.),(3.,-1.)),ball=BallState(.5,0.,last_seen_at=1.))
players=[Player(i,SoccerConfig(),None) for i in (1,2,3)]
for name,fn in [('old',old),('new',new)]:
    decision_frame.estimate_ball_intercept=fn;threat.estimate_ball_intercept=fn
    store=S();samples=[]
    for i in range(200):
        t=time.perf_counter();decision_frame.prepare_frame(ctx,players,store);samples.append((time.perf_counter()-t)*1000)
    summary[name+'_prepare_frame_mean_ms']=sum(samples)/len(samples)
print(json.dumps(summary,indent=2))
