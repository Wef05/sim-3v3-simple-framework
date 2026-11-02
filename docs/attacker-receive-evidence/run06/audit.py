"""逐帧核对实际接球目标、当前球速来源与严格低速分支。"""
import gzip,json,math
from pathlib import Path
p=Path(__file__).resolve().parent
frames=json.loads(gzip.decompress((p/'dashboard-team1.json.gz').read_bytes()))['frames']
counts=dict(receive_frames=0,block_comparisons=0,target_comparisons=0,speed_comparisons=0,intercept_frames=0,wait_frames=0,low_speed_frames=0)
issues=[];entries=[];threshold_disagreements=[];examples=[]
def equal(a,b):
    return a==b or (isinstance(a,(int,float)) and isinstance(b,(int,float)) and abs(a-b)<1e-9)
for f in frames:
 d=f['packet'];now=d['context']['now'];pred=d.get('ball_prediction') or {}
 for player in d['players']:
  r=player.get('attack_receive') or {}
  if not r:continue
  counts['receive_frames']+=1
  row=dict(frame=f['id'],sim=now,player=player['id'],stage=r['stage'],reason=r['reason'])
  own=next((b for b in d.get('block_points',[]) if b['side']=='teammates' and b['id']==player['id']),None)
  block=r.get('block_point')
  if block is not None and own is not None:
   counts['block_comparisons']+=1
   if any(own.get(k)!=v for k,v in block.items()):issues.append(dict(row,issue='calculator_snapshot_mismatch'))
  speed=r.get('speed_mps');average=r.get('observed_interval_speed_mps')
  counts['speed_comparisons']+=1
  if not equal(speed,pred.get('current_speed_mps')) or r.get('speed_kind')!='estimated_current':issues.append(dict(row,issue='current_speed_source_mismatch'))
  if isinstance(speed,(int,float)) and 0<=speed<.5:
   counts['low_speed_frames']+=1
   if r['stage'] in ('intercept','wait_ball'):issues.append(dict(row,issue='low_speed_intercept'))
  if r['reason']=='low_speed_direct':
   entries.append(dict(row,speed=speed,average=average,distance=r.get('ball_distance_m')))
   if not isinstance(speed,(int,float)) or not 0<=speed<.5:issues.append(dict(row,issue='invalid_low_speed_entry'))
  if isinstance(speed,(int,float)) and isinstance(average,(int,float)) and (speed<.5)!=(average<.5):
   threshold_disagreements.append(dict(row,speed=speed,average=average))
  if r['stage'] in ('intercept','wait_ball'):
   counts['intercept_frames' if r['stage']=='intercept' else 'wait_frames']+=1
   if block is None or r.get('intercept_point')!=block.get('target'):issues.append(dict(row,issue='receive_target_mismatch'))
   if r['stage']=='intercept':
    nav=player.get('navigation') or {};counts['target_comparisons']+=1
    if nav.get('target')!=block.get('target') or nav.get('effective_target')!=block.get('target'):issues.append(dict(row,issue='navigation_target_mismatch',navigation=nav,block=block))
   if len(examples)<15:examples.append(dict(row,block=block,navigation=player.get('navigation'),speed=speed))
result=dict(counts=counts,issues=issues,low_speed_entries=entries,threshold_disagreements=threshold_disagreements,intercept_examples=examples)
(p/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(dict(counts=counts,issues=issues,entries=entries,disagreement_count=len(threshold_disagreements)),ensure_ascii=False))
