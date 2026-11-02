"""Analyze raw calibration observations; never fit against dashboard wall time."""
import argparse
import collections
import gzip
import json
import math
import statistics
from pathlib import Path


def slope(xs, ys):
    xm, ym = statistics.mean(xs), statistics.mean(ys)
    denom = sum((x-xm)**2 for x in xs)
    return sum((x-xm)*(y-ym) for x,y in zip(xs,ys))/denom if denom else 0.


def velocities(records):
    """Integrate each displacement in midpoint body frame, then fit 0.3 s windows."""
    out, history = [], []
    distance = [0., 0., 0.]
    old = None
    for r in records:
        t, p = r['observed_at'], r['pose']
        if old is not None:
            ot, op = old
            if t <= ot:
                continue
            if t-ot > .15:
                history = []
            dh = math.atan2(math.sin(p['theta']-op['theta']),math.cos(p['theta']-op['theta']))
            angle = op['theta']+dh/2
            dx,dy = p['x']-op['x'],p['y']-op['y']
            distance[0] += dx*math.cos(angle)+dy*math.sin(angle)
            distance[1] += -dx*math.sin(angle)+dy*math.cos(angle)
            distance[2] += dh
        old = t,p
        history.append((t,distance.copy()))
        history = [x for x in history if t-x[0] <= .32]
        if len(history) < 4 or t-history[0][0] < .20:
            continue
        out.append(dict(r, velocity=[slope([x[0] for x in history],[x[1][i] for x in history]) for i in range(3)]))
    return out


def summarize(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt') as stream:
        lines = [json.loads(x) for x in stream]
    manifest=lines[0]; raw=[r for r in lines[1:] if 'pose' in r]
    vs=velocities(raw); grouped=collections.defaultdict(list)
    for r in vs:grouped[r['trial']].append(r)
    results=[]
    for plan in manifest['trials']:
        rows=grouped[plan['id']]; segs=[]
        for i,(duration,command) in enumerate(plan['segments']):
            phase=f'drive:{i}';data=[r for r in rows if r['phase']==phase]
            if not data:
                segs.append(dict(phase=phase,valid=False,reason='no_samples'));continue
            end=max(r['observed_at'] for r in data)
            tail=[r for r in data if r['observed_at']>=end-2]
            times=[r['observed_at'] for r in tail]
            med=[statistics.median(r['velocity'][axis] for r in tail) for axis in range(3)]
            active=[axis for axis,v in enumerate(command) if v]
            stable=(len(tail)>=15 and times[-1]-times[0]>=1 and
                all(abs(slope(times,[r['velocity'][a] for r in tail]))<=max(.04,abs(med[a])*.15)
                    and statistics.pstdev(r['velocity'][a] for r in tail)<=max(.04,abs(med[a])*.25) for a in active))
            raw_seg=[r for r in raw if r['trial']==plan['id'] and r['phase']==phase]
            complete=bool(raw_seg and raw_seg[-1]['sim_time']-raw_seg[0]['sim_time'] >= duration-.2)
            valid=stable and complete and all(not(r['fallen'] or r['penalized'] or r['kicking']) for r in data)
            dynamics = {}
            for axis in active:
                sign=1 if command[axis]>0 else -1
                onset=raw_seg[0]['sim_time'] if raw_seg else data[0]['sim_time']
                threshold=.03 if axis<2 else .05
                def reached(level):
                    for j,r in enumerate(data):
                        if sign*r['velocity'][axis] < level: continue
                        following=[q for q in data[j:] if q['observed_at']-r['observed_at']<=.20]
                        if (following[-1]['observed_at']-r['observed_at']>=.14 and
                            all(sign*q['velocity'][axis]>=level for q in following)):
                            return max(0., r['observed_at']-onset)
                    return None
                dynamics[str(axis)] = dict(response_delay_s=reached(threshold),
                    t90_s=reached(.9*abs(med[axis])) if stable and sign*med[axis]>threshold else None,
                    response_filter_window_s=.3)
            segs.append(dict(phase=phase,command=command,valid=valid,steady_detected=stable,
                dynamics=dynamics,
                complete=complete,measured_median=med,measured_span_s=times[-1]-times[0],
                velocity_std=[statistics.pstdev(r['velocity'][a] for r in tail) for a in range(3)],
                measured_mean=[statistics.mean(r['velocity'][a] for r in tail) for a in range(3)]))
        stops=[r for r in raw if r['trial']==plan['id'] and r['phase']=='stop']
        braking=None
        if len(stops)>=2:
            a,b=stops[0]['pose'],stops[-1]['pose']
            braking=dict(observation_duration_s=stops[-1]['observed_at']-stops[0]['observed_at'],
                net_displacement_m=math.hypot(b['x']-a['x'],b['y']-a['y']),
                net_rotation_rad=math.atan2(math.sin(b['theta']-a['theta']),math.cos(b['theta']-a['theta'])))
        results.append(dict(trial=plan['id'],kind=plan['kind'],repeat=plan['repeat'],segments=segs,braking=braking))
    groups=collections.defaultdict(list)
    for tr in results:
        for seg in tr['segments']:
            if seg.get('valid') and tr['kind'] in ('lateral','turn'):
                groups[tuple(seg['command'])].append(seg['measured_median'])
    response=[]
    for cmd,values in sorted(groups.items()):
        axis=1 if cmd[1] else 2; vals=[v[axis] for v in values]
        mean=statistics.mean(vals)
        response.append(dict(command=list(cmd),n=len(vals),median=statistics.median(vals),
            min=min(vals),max=max(vals),relative_std=statistics.pstdev(vals)/abs(mean) if mean else None,
            repeatability_passed=(len(vals)==3 and abs(mean)>0 and statistics.pstdev(vals)/abs(mean)<=.15)))
    timestamps=sorted(set(r['observed_at'] for r in raw));gaps=[b-a for a,b in zip(timestamps,timestamps[1:])]
    return dict(source=str(path),manifest=manifest,raw_records=len(raw),distinct_observations=len(timestamps),
        observed_duration_s=timestamps[-1]-timestamps[0] if timestamps else 0,
        maximum_gap_s=max(gaps,default=0),trials=results,response=response),vs


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('raw');p.add_argument('--output',required=True);args=p.parse_args()
    result,vs=summarize(args.raw);Path(args.output).write_text(json.dumps(result,indent=2))
    Path(args.output).with_suffix('.velocities.json').write_text(json.dumps(vs))
    print(json.dumps({k:v for k,v in result.items() if k not in ('manifest','trials')},indent=2))
