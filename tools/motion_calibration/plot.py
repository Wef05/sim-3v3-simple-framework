"""Render measured responses, preserving failed steady-state classifications."""
import argparse,json,collections
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from analyze import summarize
p=argparse.ArgumentParser();p.add_argument('raw',nargs='+');p.add_argument('--output',required=True);a=p.parse_args()
out=Path(a.output);out.mkdir(exist_ok=True);results=[];curves=[];offset=0.
for path in a.raw:
 r,v=summarize(path);results.append(r)
 if v:
  origin=v[0]['observed_at']
  for row in v: row['plot_time']=offset+row['observed_at']-origin
  offset=v[-1]['plot_time']+5.
  curves.extend(v)
  curves.append(dict(plot_time=offset-2.5,phase='homing',velocity=[0.,0.,0.],requested_command=[0.,0.,0.]))
 (out/(Path(path).stem+'-analysis.json')).write_text(json.dumps(r,indent=2))
rows=[]
for r in results:
 for trial in r['trials']:
  for s in trial['segments']:
   if 'measured_mean' in s:
    rows.append(dict(source=r['source'],trial=trial['trial'],kind=trial['kind'],repeat=trial['repeat'],**s))
(out/'measured-segments.json').write_text(json.dumps(rows,indent=2))
fig,axes=plt.subplots(1,2,figsize=(11,4.5))
for ax,kind,axis,unit in zip(axes,['lateral','turn'],[1,2],['m/s','rad/s']):
 data=[r for r in rows if r['kind']==kind and r.get('complete')]
 for valid,marker,color in [(True,'o','#167d8d'),(False,'x','#b76319')]:
  ds=[r for r in data if r['valid']==valid]
  ax.scatter([r['command'][axis] for r in ds],[r['measured_mean'][axis] for r in ds],marker=marker,c=color,label='Steady criterion passed' if valid else 'Steady criterion failed')
 ax.plot([-2,2],[-2,2],'--',color='#aaa',label='Requested = measured')
 ax.axhline(0,color='#ccc',lw=.5);ax.axvline(0,color='#ccc',lw=.5)
 ax.set(title=kind.capitalize()+' response',xlabel=f'Requested ({unit})',ylabel=f'Mean observed, last 2 s ({unit})');ax.legend(fontsize=8);ax.grid(alpha=.15)
fig.tight_layout();fig.savefig(out/'response.png',dpi=180);plt.close(fig)
fig,axes=plt.subplots(2,1,figsize=(12,6),sharex=True)
for axis,ax in [(1,axes[0]),(2,axes[1])]:
 ax.plot([r['plot_time'] for r in curves],[r['velocity'][axis] if r['phase']!='homing' else float('nan') for r in curves],lw=.6,label='Measured (0.3 s fit)')
 ax.plot([r['plot_time'] for r in curves],[r['requested_command'][axis] if r['phase']!='homing' else float('nan') for r in curves],lw=.6,alpha=.6,label='Requested')
 ax.set_ylabel('Lateral m/s' if axis==1 else 'Angular rad/s');ax.legend(loc='upper right');ax.grid(alpha=.15)
axes[-1].set_xlabel('Concatenated observation time (s; 5 s separators between runs)');fig.tight_layout();fig.savefig(out/'timeline.png',dpi=160)
print('Rendered',len(rows),'measured segments')
