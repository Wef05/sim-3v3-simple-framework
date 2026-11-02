
const $=id=>document.getElementById(id), c=$('pitch'),g=c.getContext('2d');let latest={};
const roleNames={attack:'进攻手',guard:'守门 / 防守',freeman:'自由人',support:'支援',kickoff:'开球',ready:'准备',opp_kickoff:'开球防守',stay:'等待',stopped:'停止',penalized:'被罚下',fallen:'倒地',switching_mode:'切换模式',no_pose:'位置未知'};
const roleLetters={attack:'A',guard:'G',freeman:'F',support:'S',kickoff:'K',ready:'R',opp_kickoff:'R',stay:'W',stopped:'X',penalized:'P',fallen:'D',switching_mode:'M',no_pose:'?'};
let displayedPacket=null;
const phases={normal:'正常比赛',our_kickoff:'我方开球',opp_kickoff:'对方开球',our_set_play:'我方定位球',opp_set_play:'对方定位球',ready:'准备',stopped:'停止'};
function pointText(p){return p?`(${Number(p[0]).toFixed(2)}, ${Number(p[1]).toFixed(2)}) m`:'无'}
function kickPowerLabel(p){
  if(!Object.hasOwn(p,'kick_power'))return '踢球力度：未记录（旧数据）';
  return Number.isFinite(p.kick_power)?`踢球力度：${p.kick_power} 档（指令）`:'踢球力度：—（本帧未下发）';
}
// Display only this frame's Agent decision; never recompute tactics in the browser.
function guardDecisionLines(p){
  const actions={
    'guard:set':['预站位，面向球','按球位或威胁者预计触球位置布防'],
    'guard:anticipate:ray':['提前封住射门方向','按近球对手 → 球的延长线提前站位'],
    'guard:block:reachable':['移动封堵来球','预测存在能在球进门前到达的封堵点'],
    'guard:block:best_effort':['尽力靠近球路','未找到可按时到达的封堵点，在可达范围内尽力补位'],
    'guard:block:hold':['保持位置挡球','模型判断当前位置已覆盖来球路径'],
    'guard:block:unreachable_hold':['原地保底，面向球','未找到可达封堵点；原地保持不表示已挡住球'],
    'guard:close_clear':['请求近身应急解围','低速球已在脚前，己方更近且存在可用出球通道'],
    'guard:direct_clear':['请求直接解围','近门沿当前朝向出脚；防乌龙时优先改选边线'],
    'guard:prepare':['调整脚位，准备出球','已选出传球或解围方向，正在接近球后位置并调整朝向'],
    'guard:pass':['请求传球','优先选择可执行的队友目标；需调整时才检查防守风险'],
    'guard:clear_kick':['请求向前场解围','已满足标准出球的脚位、朝向和时间条件'],
    'guard:kick_hold':['保持踢球请求','沿用已选出球目标，继续检查安全条件及超时'],
    'guard:escape':['优先撤离定位球保护区','先离开保护区，再恢复门将站位'],
    'guard:escape_outside':['向场外临时撤离保护区','没有可直达的场内出口，仍检查实体碰撞'],
    'guard:escape_blocked':['撤离受阻','没有找到安全出口，等待障碍变化'],
    'guard:set:restricted':['限制触球期间站位','对方开球或定位球期间不主动处理球'],
    'guard:home:no_ball':['返回门前待命点','球观测缺失或已过期'],
    'guard:stop':['停止','缺少上下文或自身位置'],
    'guard:stopped':['停止','比赛未开始、已暂停或缺少裁判信息']
  };
  const info=actions[p.action],d=p.guard_decision;
  const lines=[`正在执行：${info?.[0]||p.action||'未知'}`];
  if(info)lines.push(`依据：${info[1]}`);
  if(!d||typeof d!=='object'||Array.isArray(d)||!Object.keys(d).length){
    lines.push('本帧未记录详细依据（可能为旧版数据）；以上仅解释动作类型。');return lines;
  }
  const finite=Number.isFinite;
  if(finite(d.shooter_id))lines.push(`预判射手：敌方 ${d.shooter_id} 号${finite(d.ray_goal_y)?` · 射线到己方门线 Y=${d.ray_goal_y.toFixed(2)} m`:''}`);
  const sources={measured_prediction:'当前球轨迹预测',short_hold_envelope:'短期威胁保持（防止预测抖动）'};
  if(sources[d.shot_source])lines.push(`来球判断：${sources[d.shot_source]}`);
  if(finite(d.goal_time_s)&&d.goal_time_s>=0)lines.push(`预计到门线：${d.goal_time_s.toFixed(2)} s${Array.isArray(d.goal_crossing)&&d.goal_crossing.length===2&&d.goal_crossing.every(finite)?` · 交点 ${pointText(d.goal_crossing)}`:''}`);
  if(d.ball_fresh===false)lines.push('球观测：缺失或过期');
  else if(d.velocity_known===false)lines.push('球速：估计无效，不能据此主动出球');
  else if(finite(d.ball_speed_mps))lines.push(`球速：${d.ball_speed_mps.toFixed(2)} m/s`);
  const modes={near_facing:'近门立即沿当前朝向解围',own_goal_escape:'避开己方球门，改选边线',pass:'队友优先',sideline:'向边线解围',facing:'沿当前朝向出脚',facing_contested:'接受争球风险，沿当前朝向出脚',committed:'保持已开始的出脚，不因对手逼近取消',safe_adjustment:'调整前检查对手触球时间和球门覆盖'};
  if(modes[d.outlet_mode])lines.push(`出球方式：${modes[d.outlet_mode]}`);
  const waits={restricted:'对方重启限制主动触球',unknown_velocity:'球速未知',fast_ball:'球速超过主动处理阈值',retry_cooldown:'上次踢球请求超时，等待重试',no_safe_outlet:'未选出满足条件的出球方案',range:'球不在门前处理范围',own_goal_no_escape:'当前方向会打入己方球门，暂无可执行的边线逃逸',no_executable_kick:'当前没有可执行的出脚或安全调整方案'};
  if(waits[d.outlet_wait_reason])lines.push(`未出球：${waits[d.outlet_wait_reason]}`);
  const rejects={range:'球不在近身处理范围',bearing:'球不在可踢朝向',unknown_opponent:'对手位置未知',contested:'对手更近或领先余量不足',normal_outlet:'交由常规出球评估',no_safe_direction:'没有安全解围方向'};
  if(rejects[d.close_clear_rejection])lines.push(`近身解围未采用：${rejects[d.close_clear_rejection]}`);
  const targetStates={selected:'已选择安全方向',retained:'沿用安全目标',reselected:'旧目标失效，本帧改用安全目标（不重置超时）'};
  if(targetStates[d.close_clear_target_status])lines.push(`解围目标：${targetStates[d.close_clear_target_status]}`);
  const targetReasons={direction:'朝向或场地条件不满足',goal_structure:'球门结构阻挡',robot_lane:'机器人挡住球路'};
  if(targetReasons[d.close_clear_old_target_reason])lines.push(`旧目标失效：${targetReasons[d.close_clear_old_target_reason]}`);
  if(d.outlet_style)lines.push(`出球计划：${d.outlet_style==='pass'?'传球':'解围'}${finite(d.receiver_id)?` · 我方 ${d.receiver_id} 号`:''}`);
  if(['guard:direct_clear','guard:close_clear','guard:pass','guard:clear_kick','guard:kick_hold'].includes(p.action))lines.push('这是出球请求；是否触球成功需观察后续球轨迹。');
  return lines;
}
function line(parent,txt){let p=document.createElement('p');p.textContent=txt;parent.append(p)}
function draw(d){renderThreat(d);renderBallSpeed(d);renderKickVisibility();const w=d.context,f=w.field,s=Math.min(980/(f.length+2),640/(f.width+2));const xy=(x,y)=>[550+x*s,370-y*s];g.clearRect(0,0,1100,740);g.fillStyle='#112c2b';g.fillRect(0,0,1100,740);g.strokeStyle='#4f7972';g.lineWidth=2;
function path(points){g.beginPath();points.forEach((p,i)=>g[i?'lineTo':'moveTo'](...xy(...p)));g.stroke()}
function rect(x,y,a,b){g.strokeRect(...xy(x,y),a*s,-b*s)}
rect(-f.length/2,-f.width/2,f.length,f.width);path([[0,-f.width/2],[0,f.width/2]]);g.beginPath();g.arc(550,370,f.circle_radius*s,0,Math.PI*2);g.stroke();for(let sign of [-1,1]){let x=sign*f.length/2;rect(x,-f.goal_width/2,sign*.5,f.goal_width);rect(x,-f.penalty_area_width/2,-sign*f.penalty_area_length,f.penalty_area_width);rect(x,-f.goal_area_width/2,-sign*f.goal_area_length,f.goal_area_width)}
drawFreemanOverlay(d,xy,s);
drawInterceptionRisk(d,xy,s);
drawBallPrediction(d,xy);
drawBlockPoints(d,xy,s);
renderBlockPoints(d);
drawCandidateTarget(d,xy);
g.font='16px system-ui';g.fillStyle='#9ab7b2';g.fillText('−X',30,720);g.fillText('+X →',1000,720);
// Draw distances under robot markers; skip missing observations.
if($('distances').checked){
  const robots=[...Object.values(w.teammates),...Object.values(w.opponents)].filter(r=>r.pose).map(r=>r.pose);
  const edges=[];
  for(let i=0;i<robots.length;i++){
    for(let j=i+1;j<robots.length;j++)edges.push([robots[i],robots[j],false]);
    if(w.ball)edges.push([robots[i],w.ball,true]);
  }
  const boxes=robots.map(p=>{const [x,y]=xy(p.x,p.y);return [x-22,y-40,44,64]});
  if(w.ball){const [x,y]=xy(w.ball.x,w.ball.y);boxes.push([x-14,y-14,28,28])}
  g.save();g.lineWidth=1;g.setLineDash([3,5]);
  for(const [a,b,ball] of edges){g.strokeStyle=ball?'rgba(255,207,102,.45)':'rgba(173,193,209,.25)';path([[a.x,a.y],[b.x,b.y]])}
  g.setLineDash([]);g.font='13px system-ui';g.textAlign='center';g.textBaseline='middle';
  for(const [a,b,ball] of edges){
    const text=`${Math.hypot(a.x-b.x,a.y-b.y).toFixed(2)} m`, width=g.measureText(text).width+10;
    const [ax,ay]=xy(a.x,a.y),[bx,by]=xy(b.x,b.y);let box;
    for(const t of [.5,.35,.65,.2,.8]){
      const x=Math.max(width/2,Math.min(1100-width/2,ax+(bx-ax)*t)),y=Math.max(10,Math.min(730,ay+(by-ay)*t));
      box=[x-width/2,y-10,width,20];
      if(!boxes.some(q=>box[0]<q[0]+q[2]&&box[0]+box[2]>q[0]&&box[1]<q[1]+q[3]&&box[1]+box[3]>q[1]))break;
    }
    boxes.push(box);g.fillStyle='rgba(11,18,27,.9)';g.fillRect(...box);g.fillStyle=ball?'#ffcf66':'#becddd';g.fillText(text,box[0]+width/2,box[1]+10);
  }
  g.restore();
}
for(const p of d.players){const pose=w.teammates[p.id]?.pose;if(!pose||!p.target)continue;g.strokeStyle='#65ddbd';g.setLineDash([7,7]);path([[pose.x,pose.y],p.target]);g.setLineDash([]);let [x,y]=xy(...p.target);g.beginPath();g.moveTo(x,y-8);g.lineTo(x+8,y);g.lineTo(x,y+8);g.lineTo(x-8,y);g.closePath();g.stroke();g.fillStyle='#c3f6e6';g.fillText(`${p.id} 目标`,x+12,y-10)}
// Actual planned walking routes; target-only dashed lines above remain tactical intent.
for(const p of d.players){
  const route=p.navigation?.path;
  if(!Array.isArray(route)||route.length<2||route.length>200||route.some(q=>!Array.isArray(q)||q.length!==2||!q.every(Number.isFinite)))continue;
  g.save();g.strokeStyle='#71c9ff';g.lineWidth=3;g.setLineDash([]);path(route);g.restore();
}
function robot(r,color,tag,letter){if(!r.pose)return;let p=r.pose,[x,y]=xy(p.x,p.y);g.fillStyle=color;g.beginPath();g.arc(x,y,13,0,Math.PI*2);g.fill();g.strokeStyle=color;g.lineWidth=3;path([[p.x,p.y],[p.x+.45*Math.cos(p.theta),p.y+.45*Math.sin(p.theta)]]);g.lineWidth=2;g.fillStyle='#fff';g.font='bold 16px system-ui';g.fillText(tag,x-5,y-22);if(letter){g.save();g.fillStyle='#0b2422';g.font='bold 17px system-ui';g.textAlign='center';g.textBaseline='middle';g.fillText(letter,x,y+1);g.restore()}}
Object.values(w.opponents).forEach(r=>robot(r,'#699fff',String(r.player_id)));Object.values(w.teammates).forEach(r=>robot(r,'#62dfba',String(r.player_id),roleLetters[d.players.find(p=>String(p.id)===String(r.player_id))?.role]||'?'));
drawThreat(d,xy);
if(w.ball){g.fillStyle='#ffcf66';g.beginPath();g.arc(...xy(w.ball.x,w.ball.y),9,0,Math.PI*2);g.fill()}
if($('kick-error-visible').checked){
  const boxes=[];
  for(const event of kickEventsFor(d)){
    if(!Array.isArray(event.origin)||!event.origin.every(Number.isFinite))continue;
    const text=`#${event.id??'?'} ${kickErrorLabel({latest:event})}`,[bx,by]=xy(...event.origin);
    g.save();g.font='14px system-ui';g.textAlign='left';g.textBaseline='middle';
    const width=g.measureText(text).width+16,x=Math.max(8,Math.min(c.width-width-8,bx+24));
    let y=Math.max(16,Math.min(c.height-16,by+38));
    for(let attempt=0;attempt<24&&boxes.some(q=>x<q.x+q.width&&x+width>q.x&&Math.abs(y-q.y)<28);attempt++)y=16+(y+28-16)%(c.height-32);
    boxes.push({x,y,width});
    g.strokeStyle='#ffd17b';g.lineWidth=1;g.beginPath();g.moveTo(bx,by);g.lineTo(x,y);g.stroke();
    g.fillStyle='rgba(11,18,27,.94)';g.fillRect(x,y-13,width,26);g.fillStyle='#ffd17b';g.fillText(text,x+8,y);g.restore();
  }
}}

function renderPacket(d){displayedPacket=d;const w=d.context;$('empty').hidden=true;c.hidden=false;
const score=w.game?.teams?.map(t=>`${t.team_number} 队 ${t.score}`).join(' : ')||'比分未知';$('summary').textContent=`${phases[d.phase]||d.phase||'阶段未知'}　 |　${score}　 |　剩余 ${w.game?.secs_remaining??'—'} s　 |　球${w.ball?'可见':'未知'}　 |　帧 ${d.sequence}`;
$('cards').replaceChildren();for(const p of d.players){let card=document.createElement('article');card.className='card';let h=document.createElement('h3');h.textContent=`${p.id} 号`;let role=document.createElement('span');role.className='role';role.textContent=roleNames[p.role]||p.role;h.append(role);card.append(h);const pose=w.teammates[p.id]?.pose;if(p.role==='guard'){card.className+=' guard-card';for(const text of guardDecisionLines(p))line(card,text)}else line(card,`动作：${p.action}`);line(card,kickPowerLabel(p));if(p.navigation)line(card,navigationLabel(p.navigation));line(card,`位置：${pointText(pose?[pose.x,pose.y]:null)}`);line(card,`目的地：${pointText(p.target)}`);line(card,`距球：${pose&&w.ball?Math.hypot(pose.x-w.ball.x,pose.y-w.ball.y).toFixed(2)+' m':'未知'}`);line(card,`模式：${p.mode??'未知'}${p.fallen?' · 倒地':''}${p.kicking?' · 踢球中':''}${p.penalized?' · 判罚 '+p.penalty:''}`);if(['attack','freeman'].includes(p.role))line(card,interceptLabel(p.intercept));$('cards').append(card)}renderInterceptions(d);renderCandidateTargets(d);renderScoringProbability(d);renderAttackChoice(d);$('raw').textContent=JSON.stringify(d,null,2);draw(d)}

// Playback uses receiver monotonic timestamps, never the Agent's clock.
let liveFrames=[], archive=null, viewing='live', playing=false, cursor=0;
let historyTeam='', session='', after=0, syncing=false, lastAnimation=0, shown=null;
let clearing=false,clearGeneration=0;
let importName='', historyError=false;
const frames=()=>archive??liveFrames;
const elapsed=t=>`${Math.floor(Math.max(0,t)/60).toString().padStart(2,'0')}:${(Math.max(0,t)%60).toFixed(1).padStart(4,'0')}`;
function indexAt(list,t){let lo=0,hi=list.length;while(lo<hi){const mid=(lo+hi)>>1;if(list[mid].time<=t)lo=mid+1;else hi=mid}return Math.max(0,lo-1)}
function note(text){$('replay-note').textContent=text}
const eventKinds={
  our_goal:{label:'我方得分',color:'#65ddbd',symbol:'●'},
  opp_goal:{label:'敌方得分',color:'#ff8795',symbol:'●'},
  our_set_play:{label:'我方定位球',color:'#69d6ed',symbol:'◆'},
  opp_set_play:{label:'敌方定位球',color:'#ce9bff',symbol:'◆'},
  our_kickoff:{label:'我方开球',color:'#d0e97c',symbol:'▲'},
  opp_kickoff:{label:'敌方开球',color:'#ffb36b',symbol:'▲'},
  our_fall:{label:'我方摔倒',color:'#65c7bc',symbol:'↓'},
  opp_fall:{label:'敌方摔倒',color:'#f6a5cf',symbol:'↓'},
  our_penalty:{label:'我方罚下',color:'#ffdb7a',symbol:'!'},
  opp_penalty:{label:'敌方罚下',color:'#ff7878',symbol:'!'},
};
// Derived from recorded observations, so old exported files also gain markers.
function detectEvents(list){
  const events=[];let scores=null,restart=null,hadGame=false;
  const flags=new Map();
  function flag(f,side,id,kind,value,detail){
    if(typeof value!=='boolean')return;
    const key=side+':'+id+':'+kind,previous=flags.get(key);
    if(value&&previous!==true)events.push({time:f.time,kind:side+'_'+kind,robot:id,detail:`${id} 号 · ${detail}${previous===undefined?'（首次观测已处于此状态）':''}`});
    flags.set(key,value);
  }
  for(const f of list){const p=f.packet,w=p.context,game=w.game;
    // Falls do not depend on referee data. New packets contain both sides.
    const observations=p.robot_statuses||p.players?.map(r=>({...r,side:'our'}))||[];
    for(const r of observations)flag(f,r.side==='opp'?'opp':'our',r.id,'fall',r.fallen,'摔倒');
    if(!game){for(const r of p.players||[])flag(f,'our',r.id,'penalty',r.penalized,'被罚下');continue}
    for(const team of game.teams||[]){const side=String(team.team_number)===String(w.team_id)?'our':'opp';
      // Referee arrays may contain 20 slots; only actual match players count.
      const ids=new Set(Object.keys(side==='our'?w.teammates||{}:w.opponents||{}).map(Number));
      if(game.players_per_team>0)for(let id=1;id<=game.players_per_team;id++)ids.add(id);
      for(const id of ids){const penalty=team.players?.[id-1]?.penalty;
        if(typeof penalty==='string')flag(f,side,id,'penalty',!['NONE','SUBSTITUTE'].includes(penalty),penalty);
      }
    }
    const current=new Map((game.teams||[]).map(t=>[String(t.team_number),t.score]));
    const reset=scores&&[...current].some(([id,score])=>scores.has(id)&&score<scores.get(id));
    if(scores&&!reset){for(const [id,score] of current){const previous=scores.get(id);
      if(Number.isFinite(previous)&&Number.isFinite(score)&&score>previous){
        const ours=id===String(w.team_id),delta=score-previous;
        events.push({time:f.time,kind:ours?'our_goal':'opp_goal',detail:`比分 ${previous} → ${score}${delta>1?'（累计变化 +'+delta+'）':''}`});
      }
    }}
    scores=current;
    if(reset)restart=null;
    const kind=p.phase;
    if(['our_set_play','opp_set_play','our_kickoff','opp_kickoff'].includes(kind)){
      const key=kind+':'+(kind.endsWith('set_play')?game.set_play:'')+':'+game.kicking_team;
      if(key!==restart)events.push({time:f.time,kind,detail:(kind.endsWith('set_play')?({DIRECT_FREE_KICK:'直接任意球',INDIRECT_FREE_KICK:'间接任意球',PENALTY_KICK:'点球',THROW_IN:'界外球',GOAL_KICK:'球门球',CORNER_KICK:'角球'}[game.set_play]||String(game.set_play||'定位球')):'开球')+(!hadGame?'（记录起点已处于此阶段）':'')});
      restart=key;
    }else if(kind==='normal'||kind==='ready'||game.state==='INITIAL'||game.state==='FINISHED')restart=null;
    // Missing observations and a temporary STOPPED/SET do not start a new restart.
    hadGame=true;
  }
  return events;
}
let eventFirst=null,eventLast=null,eventsCollapsed=false,eventRenderedAt=-Infinity;
function renderEvents(list){
  if(eventFirst===list[0]&&eventLast===list.at(-1))return;
  if(eventFirst&&list.length&&performance.now()-eventRenderedAt<500)return;
  eventRenderedAt=performance.now();
  eventFirst=list[0];eventLast=list.at(-1);
  const events=detectEvents(list),start=list[0]?.time??0,duration=(list.at(-1)?.time??start)-start;
  const fragment=document.createDocumentFragment();
  for(const [kind,style] of (eventsCollapsed?[['all',{label:'全部事件',color:'#bed0df'}]]:Object.entries(eventKinds))){
    const row=document.createElement('div');row.className='event-row';
    const label=document.createElement('span');label.className='event-label';label.style.color=style.color;label.textContent=style.label;
    const track=document.createElement('div');track.className='event-track';
    const levels=[];let rows=1;
    for(const event of events.filter(e=>kind==='all'||e.kind===kind)){
      const eventStyle=eventKinds[event.kind];
      const marker=document.createElement('button');marker.className='event-marker';marker.style.background=eventStyle.color;
      marker.style.left=`${duration?100*(event.time-start)/duration:0}%`;marker.textContent=event.robot!==undefined?`${eventStyle.symbol}${event.robot}`:eventStyle.symbol;
      if(event.robot!==undefined)marker.style.width='28px';
      const description=`${eventStyle.label} · ${elapsed(event.time-start)} · ${event.detail}`;
      if(eventsCollapsed){const fraction=duration?(event.time-start)/duration:0;let level=levels.findIndex(v=>fraction-v>.035);if(level<0)level=levels.length;levels[level]=fraction;marker.style.top=`${2+level*20}px`;rows=Math.max(rows,level+1)}
      marker.title=description;marker.setAttribute('aria-label',description);
      marker.onclick=()=>{seek(event.time);note(description)};track.append(marker);
    }
    if(eventsCollapsed)track.style.height=`${rows*20+2}px`;
    row.append(label,track);fragment.append(row);
  }
  $('event-tracks').replaceChildren(fragment);
}
function controls(){const list=frames(),first=list[0],last=list.at(-1),has=!!first;
  renderPredictionAccuracy(list);
  renderEvents(list);
  $('play').disabled=!has;$('prev').disabled=!has;$('next').disabled=!has;$('timeline').disabled=!has;
  $('export').disabled=!has||(!archive&&syncing);
  $('team').disabled=!!archive||clearing;
  $('play').textContent=playing?'暂停回放':'播放回放';
  $('mode').textContent=archive?`文件回放 · 队伍 ${archive[0].packet.context.team_id}`:viewing==='live'?'实时观看':'实时回看';
  $('recording').textContent=archive?`已导入：${importName} · 实时录制继续`:
    historyError?'历史读取失败，正在重试':syncing?'正在同步录制历史…':`已记录 ${list.length} 帧 · 最近 30 分钟 / 最高 30 Hz`;
  if(!has){$('position').textContent='暂无历史';$('timeline').max=0;return}
  const pos=viewing==='live'?last.time:Math.min(last.time,Math.max(first.time,cursor));
  $('timeline').max=String(last.time-first.time);$('timeline').value=String(pos-first.time);
  const f=list[indexAt(list,pos)];
  $('position').textContent=`${elapsed(pos-first.time)} / ${elapsed(last.time-first.time)} · ${new Date(f.recorded_at*1000).toLocaleTimeString()}`;
}
function show(){controls();if(viewing==='live'){
    const entry=latest[$('team').value];if(!entry)return;
    $('status').textContent=entry.age>2?`数据陈旧 · ${entry.age.toFixed(1)} 秒未更新`:`实时 · ${entry.age.toFixed(1)} 秒前`;
    renderPacket(entry.packet);shown=null;
  }else{
    const list=frames();if(!list.length)return;
    cursor=Math.max(list[0].time,Math.min(cursor,list.at(-1).time));
    const f=list[indexAt(list,cursor)];
    $('status').textContent=`${archive?'文件回放':'历史回看'} · ${playing?'播放中':'已暂停'}`;
    if(shown!==f){renderPacket(f.packet);shown=f}
  }
}
function seek(t){const list=frames();if(!list.length)return;viewing='replay';playing=false;cursor=t;shown=null;show()}
function goLive(){archive=null;importName='';viewing='live';playing=false;shown=null;
  note('已返回实时。回放仅影响看板，比赛和录制继续进行。');show()}
$('live').onclick=goLive;
$('timeline').oninput=()=>{const list=frames();if(list.length)seek(list[0].time+Number($('timeline').value))};
$('play').onclick=()=>{const list=frames();if(!list.length)return;
  if(viewing==='live'||cursor>=list.at(-1).time){cursor=list[0].time;viewing='replay'}
  playing=!playing;lastAnimation=performance.now();show()};
function step(delta){const list=frames();if(!list.length)return;const i=viewing==='live'?list.length-1:indexAt(list,cursor);seek(list[Math.max(0,Math.min(list.length-1,i+delta))].time)}
$('prev').onclick=()=>step(-1);$('next').onclick=()=>step(1);
$('team').onchange=()=>{liveFrames=[];historyTeam='';after=0;viewing='live';playing=false;shown=null;show()};
function animate(now){if(playing){const list=frames();if(list.length){cursor+=(now-lastAnimation)/1000*Number($('speed').value);if(cursor>=list.at(-1).time){cursor=list.at(-1).time;playing=false;note(archive?'已播放到文件末尾。':'已播放到已录制末尾，可点击“返回实时”继续观看。')}show()}}lastAnimation=now;requestAnimationFrame(animate)}
requestAnimationFrame(animate);
async function getJSON(url){const r=await fetch(url);if(!r.ok)throw Error(`HTTP ${r.status}`);return r.json()}
async function poll(){const started=performance.now(),generation=clearGeneration;try{
  const state=await getJSON('/api/state');if(clearing||generation!==clearGeneration)return;latest=state;const keys=Object.keys(latest),selected=$('team').value;
  if(keys.join(',')!==Array.from($('team').options).map(o=>o.value).join(',')){
    $('team').replaceChildren(...keys.map(k=>new Option(`观测队伍 ${k}`,k)));if(keys.includes(selected))$('team').value=selected}
  const team=$('team').value;
  if(keys.includes(team)){
    if(historyTeam!==team){historyTeam=team;liveFrames=[];after=0;session=''}
    try{
      const data=await getJSON(`/api/history?team=${encodeURIComponent(team)}&after=${after}`);
      if(team!==$('team').value||clearing||generation!==clearGeneration)return;
      if(session&&session!==data.session){liveFrames=[];after=0;session=data.session;note('录制服务已重启，开始新的历史记录。');return}
      session=data.session;historyError=false;
      liveFrames=liveFrames.filter(f=>data.first_id!==null&&f.id>=data.first_id);
      liveFrames.push(...data.frames);if(data.frames.length)after=data.frames.at(-1).id;
      syncing=data.last_id!==null&&after<data.last_id;
      if(!archive&&viewing==='replay'&&liveFrames.length&&cursor<liveFrames[0].time){cursor=liveFrames[0].time;shown=null;note('更早的历史已超出 30 分钟窗口，已移至最早可用帧。')}
    }catch(e){historyError=true;syncing=false}
  }
  show();
}catch(e){$('status').textContent='看板服务连接中断';if(viewing==='replay')show()}
finally{setTimeout(poll,Math.max(0,(syncing?20:1000/30)-(performance.now()-started)))}}
poll();
$('export').onclick=()=>{const list=frames();if(!list.length)return;
  const data={format:'soccer-tactics-replay',version:1,team_id:list[0].packet.context.team_id,
    exported_at:new Date().toISOString(),recording_hz:30,frames:list};
  const blob=new Blob([JSON.stringify(data)],{type:'application/json'}),url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download=`tactics-team${data.team_id}-${new Date().toISOString().replace(/[:.]/g,'-')}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  note(`已导出 ${list.length} 帧（${elapsed(list.at(-1).time-list[0].time)}）。`);
};
// Imported files are data only. No HTML injection and no server upload.
function validateReplay(data){
  if(data?.format!=='soccer-tactics-replay'||data.version!==1||!Array.isArray(data.frames)||!data.frames.length||data.frames.length>54000)throw Error('文件格式不正确，或帧数超出 54000');
  const finite=n=>typeof n==='number'&&Number.isFinite(n);let previous=-Infinity;
  for(const f of data.frames){const p=f?.packet,w=p?.context,field=w?.field;
    if(!finite(f.time)||f.time<previous||!finite(f.recorded_at)||p.version!==1||!w||w.team_id!==data.team_id||!Array.isArray(p.players)||p.players.length>40)throw Error('回放时间或队伍数据不完整');
    previous=f.time;
    for(const key of ['length','width','goal_width','circle_radius','penalty_area_width','penalty_area_length','goal_area_width','goal_area_length'])if(!field||!finite(field[key])||field[key]<=0||field[key]>1000)throw Error('球场数据不正确');
    for(const robots of [w.teammates,w.opponents]){if(!robots||typeof robots!=='object'||Array.isArray(robots)||Object.keys(robots).length>40)throw Error('球员观测数据不正确');for(const r of Object.values(robots))if(!r||r.pose!=null&&(!finite(r.pose.x)||!finite(r.pose.y)||!finite(r.pose.theta)))throw Error('球员位置不正确')}
    if(w.ball!=null&&(!finite(w.ball.x)||!finite(w.ball.y)))throw Error('球位置不正确');
    if(w.game!=null&&(!Array.isArray(w.game.teams)||w.game.teams.some(t=>!t||!finite(t.team_number)||!finite(t.score))))throw Error('比分数据不正确');
    validatePredictionData(p);
    for(const player of p.players)if(!player||!finite(player.id)||typeof player.action!=='string'||typeof player.role!=='string'||player.target!=null&&(!Array.isArray(player.target)||player.target.length!==2||!player.target.every(finite)))throw Error('球员决策数据不正确');
  }
  return data.frames;
}
$('import').onclick=()=>$('file').click();
$('file').onchange=async()=>{const file=$('file').files[0];if(!file)return;
  try{if(file.size>200*1024*1024)throw Error('文件超过 200 MB');const validated=validateReplay(JSON.parse(await file.text()));
    archive=validated;importName=file.name;viewing='replay';playing=false;cursor=archive[0].time;shown=null;note('正在观看导入文件。后台实时录制继续；点击“返回实时”可退出。');show();
  }catch(e){note(`无法导入：${e.message}。当前画面与录制未改变。`)}finally{$('file').value=''}
};

$('distances').onchange=()=>{if(displayedPacket)draw(displayedPacket)};

$('collapse-events').onclick=()=>{eventsCollapsed=!eventsCollapsed;$('collapse-events').textContent=eventsCollapsed?'展开标记':'折叠标记';$('collapse-events').setAttribute('aria-expanded',String(!eventsCollapsed));eventFirst=null;renderEvents(frames())};

$('clear-records').onclick=async()=>{
  if(archive){goLive();note('已清除导入回放视图，磁盘文件和实时录制不受影响。');return}
  if(clearing)return;const team=$('team').value;clearing=true;$('team').disabled=true;clearGeneration++;
  try{const response=await fetch('/api/history/clear',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({team})});if(!response.ok)throw Error();await response.json();
    liveFrames=[];after=0;session='';playing=false;viewing='live';eventFirst=null;shown=null;delete latest[team];displayedPacket=null;$('pitch').hidden=true;$('empty').hidden=false;$('summary').textContent='等待新帧';$('cards').replaceChildren();$('raw').textContent='暂无数据';show();note('已删除当前队伍的历史录制；继续录制新帧。');
  }catch(e){note('清除失败，记录未确认清除，请重试。')}finally{clearing=false;$('team').disabled=!!archive;clearGeneration++}
};


// Same semicircle/quadrant and rectangle rules as nearest_free_position; circular
// intersections are rendered with canvas clipping, not a sampled grid.
function freemanBounds(field,ball,pose,margin){
  const hl=field.length/2,hw=field.width/2;
  let xmin=-hl,xmax=hl-margin,ymin=-hw,ymax=hw;
  let sx=hl-ball.x;if(sx===0)sx=pose.x-ball.x;
  const semicircle=Math.abs(ball.y)<=field.penalty_area_width/2,sy=semicircle?0:ball.y<0?1:-1;
  if(sx>=0)xmin=Math.max(xmin,ball.x);else xmax=Math.min(xmax,ball.x);
  if(sy>0)ymin=Math.max(ymin,ball.y);else if(sy<0)ymax=Math.min(ymax,ball.y);
  return {xmin,xmax,ymin,ymax,sx:sx>=0?1:-1,sy,semicircle};
}
function drawFreemanOverlay(d,xy,s){
  const enabled=$('freeman-regions').checked,noteBox=$('region-note'),select=$('freeman-id');
  noteBox.hidden=!enabled;select.hidden=!enabled;
  if(!enabled)return;
  const candidates=d.players.filter(p=>p.role==='freeman'),ids=candidates.map(p=>String(p.id));
  if(ids.join(',')!==Array.from(select.options).map(o=>o.value).join(',')){
    const old=select.value;select.replaceChildren(...ids.map(id=>new Option(`${id} 号自由人`,id)));if(ids.includes(old))select.value=old;
  }
  const p=candidates.find(p=>String(p.id)===select.value),w=d.context,pose=p&&w.teammates[p.id]?.pose;
  select.disabled=!candidates.length;
  if(!p||!pose||!w.ball){noteBox.textContent=!p?'当前帧没有自由人，暂无选区。':'自由人位置或球位置未知，无法绘制选区。';return}
  const fallback=!p.freeman_region,r=p.freeman_region||{ball_radius:7,opponent_radius:3,goal_line_margin:1};
  if(!['ball_radius','opponent_radius','goal_line_margin'].every(k=>Number.isFinite(r[k])&&r[k]>=0&&r[k]<=1000)){noteBox.textContent='选区参数无效。';return}
  const f=w.field,b=w.ball,hl=f.length/2,hw=f.width/2,bounds=freemanBounds(f,b,pose,r.goal_line_margin);
  const enemies=Object.values(w.opponents).filter(o=>o.pose).map(o=>o.pose);
  noteBox.textContent=`${p.id} 号：青色球圆 ≤ ${r.ball_radius} m；蓝色为${bounds.semicircle?'朝向敌方球门的半平面（球的水平线穿过敌方大禁区，取 1/2 圆）':'敌方球门所在象限（取 1/4 圆）'}；红色圆内为对手排除区（半径 ${r.opponent_radius} m）；橙色为门线前 ${r.goal_line_margin} m 排除带；亮绿色为最终可选区。${fallback?' 本帧未记录参数，按 7 / 3 / 1 m 参考值绘制，不能确认为历史实际参数。':p.target?' ◇ 为 Agent 实际选定目的地。':' Agent 本帧未选出目的地。'}`;
  const triangle=Array.isArray(r.shot_triangle)&&r.shot_triangle.length===3&&r.shot_triangle.every(v=>Array.isArray(v)&&v.length===2&&v.every(Number.isFinite))?r.shot_triangle:null;
  const clearance=Number.isFinite(r.shot_clearance)&&r.shot_clearance>0?r.shot_clearance:0;
  if(triangle)noteBox.textContent+=` 紫色为 attacker 射门三角形及 ${clearance.toFixed(2)} m 安全余量。`;
  else if(!('shot_triangle' in r))noteBox.textContent+=' 本帧未记录射门排除区。';
  function trianglePath(){g.moveTo(...xy(...triangle[0]));for(const v of triangle.slice(1))g.lineTo(...xy(...v));g.closePath()}
  function excludePath(draw){g.beginPath();g.rect(0,0,c.width,c.height);draw();g.clip('evenodd')}
  function rectangle(x0,y0,x1,y1){const [x,y]=xy(x0,y1);g.beginPath();g.rect(x,y,(x1-x0)*s,(y1-y0)*s)}
  function circle(point,radius){g.beginPath();g.arc(...xy(point.x,point.y),radius*s,0,2*Math.PI)}
  g.save();rectangle(-hl,-hw,hl,hw);g.clip();
  // Each individual constraint remains visible, with the final intersection above it.
  circle(b,r.ball_radius);g.fillStyle='rgba(74,211,236,.10)';g.fill();g.strokeStyle='#4ad3ec';g.lineWidth=2;g.stroke();
  const qx0=bounds.sx>0?Math.max(-hl,b.x):-hl,qx1=bounds.sx>0?hl:Math.min(hl,b.x);
  const qy0=bounds.sy>0?Math.max(-hw,b.y):-hw,qy1=bounds.sy<0?Math.min(hw,b.y):hw;
  if(qx0<=qx1&&qy0<=qy1){rectangle(qx0,qy0,qx1,qy1);g.fillStyle='rgba(111,145,255,.12)';g.fill();g.strokeStyle='#8d9fff';g.setLineDash([8,5]);g.stroke();g.setLineDash([])}
  rectangle(Math.max(-hl,hl-r.goal_line_margin),-hw,hl,hw);g.fillStyle='rgba(255,171,67,.22)';g.fill();g.strokeStyle='#ffab43';g.stroke();
  for(const e of enemies){circle(e,r.opponent_radius);g.fillStyle='rgba(249,101,124,.12)';g.fill();g.strokeStyle='rgba(249,101,124,.75)';g.stroke()}
  if(triangle){g.beginPath();trianglePath();g.fillStyle='rgba(197,111,255,.22)';g.fill();g.strokeStyle='rgba(197,111,255,.45)';g.lineWidth=2*clearance*s;g.lineJoin='round';g.stroke();g.lineWidth=1}
  if(bounds.xmin<=bounds.xmax&&bounds.ymin<=bounds.ymax){
    g.save();rectangle(bounds.xmin,bounds.ymin,bounds.xmax,bounds.ymax);g.clip();circle(b,r.ball_radius);g.clip();
    for(const e of enemies){g.beginPath();g.rect(0,0,c.width,c.height);g.moveTo(xy(e.x,e.y)[0]+r.opponent_radius*s,xy(e.x,e.y)[1]);g.arc(...xy(e.x,e.y),r.opponent_radius*s,0,2*Math.PI);g.clip('evenodd')}
    if(triangle){
      excludePath(trianglePath);
      for(let i=0;i<3;i++){
        const a=triangle[i],b=triangle[(i+1)%3],dx=b[0]-a[0],dy=b[1]-a[1],len=Math.hypot(dx,dy);
        excludePath(()=>{const q=xy(...a);g.moveTo(q[0]+clearance*s,q[1]);g.arc(...q,clearance*s,0,2*Math.PI)});
        if(len>1e-12){const nx=-dy/len*clearance,ny=dx/len*clearance;
          excludePath(()=>{g.moveTo(...xy(a[0]+nx,a[1]+ny));g.lineTo(...xy(b[0]+nx,b[1]+ny));g.lineTo(...xy(b[0]-nx,b[1]-ny));g.lineTo(...xy(a[0]-nx,a[1]-ny));g.closePath()});}
      }
    }
    g.fillStyle='rgba(76,255,144,.46)';g.fillRect(0,0,c.width,c.height);
    g.strokeStyle='rgba(172,255,186,.65)';g.lineWidth=1;
    for(let x=-c.height;x<c.width;x+=14){g.beginPath();g.moveTo(x,0);g.lineTo(x+c.height,c.height);g.stroke()}
    g.restore();
  }
  g.restore();
}
$('freeman-regions').onchange=()=>{if(displayedPacket)draw(displayedPacket)};
$('freeman-id').onchange=()=>{if(displayedPacket)draw(displayedPacket)};

// All values below are recorded by the Agent in the decision frame, including
// replay frames. The browser never fits a trajectory or reruns role selection.
const predictionNumber=n=>typeof n==='number'&&Number.isFinite(n);
const predictionPoint=p=>Array.isArray(p)&&p.length===2&&p.every(predictionNumber);
const predictionFixed=n=>predictionNumber(n)?n.toFixed(2):'—';
function validBallPrediction(b){
  return b&&predictionPoint(b.position)&&predictionPoint(b.velocity)&&
    predictionNumber(b.horizon_s)&&predictionNumber(b.step_s)&&
    predictionNumber(b.observation_age_s)&&Array.isArray(b.points)&&b.points.length>0&&b.points.length<=100&&
    b.points.every(p=>predictionNumber(p.t)&&predictionPoint(p.position));
}
// Arithmetic mean of valid recorded speed samples in the preceding five seconds.
// Receiver time bounds the window; Agent time/run checks exclude future or other runs.
function ballSpeedStats(d,list,endTime){
  const speed=p=>validBallPrediction(p.ball_prediction)&&p.ball_prediction.velocity_estimated===true?
    Math.hypot(...p.ball_prediction.velocity):null;
  const current=speed(d),samples=[],seen=new Set();
  function add(p){
    if(p.context.team_id!==d.context.team_id||p.context.now>d.context.now||
      p.kick_error?.run_id!==d.kick_error?.run_id)return;
    const value=speed(p),key=p.context.now;
    if(value!==null&&!seen.has(key)){seen.add(key);samples.push(value)}
  }
  add(d);
  for(let i=list.length?indexAt(list,endTime):-1;i>=0;i--){
    const f=list[i];if(f.time<endTime-5)break;
    if(f.time<=endTime)add(f.packet);
  }
  return {current,average:samples.length?samples.reduce((a,b)=>a+b,0)/samples.length:null,count:samples.length};
}
function renderBallSpeed(d){
  const box=$('ball-speed');box.hidden=!$('prediction-path').checked;
  if(box.hidden)return;
  const list=frames(),endTime=viewing==='live'?(list.at(-1)?.time??0):cursor;
  const stats=ballSpeedStats(d,list,endTime),format=v=>v===null?'—':v.toFixed(2)+' m/s';
  box.textContent=`当前球速 ${format(stats.current)} · 平均球速 ${format(stats.average)}（近 5 秒有效记录均值，${stats.count} 个样本）`;
}
function renderKickVisibility(){
  const hidden=!$('kick-error-visible').checked;
  $('kick-error-note').hidden=hidden;
  $('kick-error-records').hidden=hidden;
}
$('kick-error-visible').onchange=()=>{renderKickVisibility();if(displayedPacket)draw(displayedPacket)};
function validIntercept(a){
  if(!a||!['unavailable','intercept','fallback'].includes(a.status))return false;
  if(a.status==='unavailable')return a.score_s===null;
  return predictionPoint(a.target)&&[
    'score_s','target_time_s','distance_m','turn_angle_rad','turn_time_s',
    'move_distance_m','move_time_s','arrival_time_s','slack_s','horizon_s','step_s',
    'control_radius_m','linear_speed_mps','angular_speed_rps','turn_threshold_rad','omni_distance_m',
  ].every(k=>predictionNumber(a[k]))&&(a.status!=='intercept'||predictionNumber(a.intercept_time_s));
}
function validInterceptionRisk(r){
  const prob=v=>predictionNumber(v)&&v>=0&&v<=1;
  if(!r||!['unavailable','ok','partial','blocked'].includes(r.status))return false;
  if(r.status==='unavailable')return r.probability===null;
  if(r.event==='intercept_kick_before_target'&&(!predictionPoint(r.target)||
    !predictionNumber(r.ball_speed_mps)||r.ball_speed_mps<=0||!predictionNumber(r.flight_time_s)||r.flight_time_s<=0||
    !Array.isArray(r.path)||r.path.length<2||r.path.length>161||r.path.some(p=>!predictionPoint(p.position)||
      !predictionNumber(p.t)||p.t<0||(p.probability!==null&&!prob(p.probability)))))return false;
  return (r.probability===null||prob(r.probability))&&predictionNumber(r.time_s)&&r.time_s>=0&&
    predictionPoint(r.ball_position)&&predictionPoint(r.robot_position)&&predictionNumber(r.control_radius_m)&&r.control_radius_m>0&&
    Array.isArray(r.opponents)&&r.opponents.length<=40&&r.opponents.every(e=>prob(e.probability)&&
      Array.isArray(e.density_components)&&e.density_components.length<=20&&e.density_components.every(b=>
        predictionPoint(b.position)&&predictionNumber(b.heading)&&predictionNumber(b.radius_m)&&b.radius_m>0&&
        prob(b.weight)&&predictionNumber(b.density_per_m2)&&b.density_per_m2>=0));
}
function validatePredictionData(d){
  if(d.ball_prediction!=null&&!validBallPrediction(d.ball_prediction))throw Error('球预测数据不正确');
  for(const p of d.players)if(p.intercept!=null&&!validIntercept(p.intercept))throw Error('接球计算数据不正确');
  for(const p of d.players)if(p.interception_risk!=null&&!validInterceptionRisk(p.interception_risk))throw Error('拦截风险数据不正确');
  for(const p of d.players)if(p.attack_candidates!=null&&!validAttackCandidates(p.attack_candidates))throw Error('候选目标评估数据不正确');
  for(const p of d.players)if(p.scoring_probability!=null&&!validScoringProbability(p.scoring_probability))throw Error('进球概率数据不正确');
  for(const p of d.players)if(p.attack_choice!=null&&!validAttackChoice(p.attack_choice))throw Error('进攻选择数据不正确');
  const a=d.attack_decision;
  if(a!=null&&(!predictionNumber(a.selected_id)||!predictionNumber(a.best_id)||!predictionNumber(a.margin_s)||
    !['fastest','hysteresis','no_estimate'].includes(a.reason)||!Array.isArray(a.candidates)||a.candidates.length>40||
    a.candidates.some(p=>!predictionNumber(p.id)||p.score_s!==null&&!predictionNumber(p.score_s))))throw Error('角色选择数据不正确');
}
function interceptLabel(a){
  if(!validIntercept(a))return '预计接球：本帧未记录';
  if(a.status==='unavailable')return '预计接球：无法估算';
  if(a.status==='fallback')return `${predictionFixed(a.horizon_s)} s 内不可达 · 追赶估值 ${predictionFixed(a.score_s)} s`;
  return `预计接球 ${predictionFixed(a.intercept_time_s)} s`;
}
// 每个出球编号保留独立记录；回放只读游标之前的数据，不泄漏未来结果。
function collectKickEvents(list, displayed){
  const data=displayed.kick_error, records=new Map();
  if(!data)return [];
  const merge=e=>{if(e&&Number.isFinite(e.id))records.set(e.id,e)};
  let lastId=null;
  for(const frame of list){
    const packet=frame.packet,k=packet?.kick_error;
    if(!k||k.run_id!==data.run_id||packet.context.now>displayed.context.now)continue;
    if(k.latest?.id!==lastId){for(const event of k.events??[])merge(event);lastId=k.latest?.id}
    merge(k.latest);
    if(packet===displayed)break;
  }
  for(const event of data.events??[])merge(event);
  merge(data.latest);
  if(!records.size&&data.latest)return [data.latest];
  return [...records.values()].sort((a,b)=>a.id-b.id);
}
let kickEventsCache={key:null,time:-Infinity,events:[]};
function kickEventsFor(d){
  const key=`${d.kick_error?.run_id}:${historyTeam}:${session}:${importName}:${viewing}:${viewing==='live'?'':d.context.now}`;
  const now=performance.now();
  if(key!==kickEventsCache.key||now-kickEventsCache.time>1000){
    kickEventsCache={key,time:now,events:collectKickEvents(frames(),d)};
  }
  // 实时新事件和状态每帧立即覆盖，不等下一次历史扫描。
  const records=new Map(kickEventsCache.events.map(e=>[e.id,e]));
  for(const e of d.kick_error?.events??[])records.set(e.id,e);
  if(d.kick_error?.latest)records.set(d.kick_error.latest.id,d.kick_error.latest);
  return [...records.values()].sort((a,b)=>(a.id??0)-(b.id??0));
}
function kickErrorLabel(data){
  const e=data?.latest;
  if(!e)return '踢球偏角：等待可识别出球';
  const status=e.status==='tracking'?'测量中':e.status==='complete'?'已固定':'样本不足';
  if(!Number.isFinite(e.error_deg))return `${e.player_id} 号踢球偏角：${status}`;
  const side=e.error_deg>0?'左偏':e.error_deg<0?'右偏':'无偏差';
  return `${e.player_id} 号 ${side} ${Math.abs(e.error_deg).toFixed(1)}° · ${status}（估计）`;
}
function renderKickError(data){
  const box=$('kick-error-note');
  if(!data){box.textContent='踢球偏角：本帧未记录；旧回放不补算。';return}
  const e=data.latest,angle=a=>Number.isFinite(a)?`${(a*180/Math.PI).toFixed(1)}°`:'—';
  const reason={first_turn:'首次转向',stopped:'球停止',timeout:'观测满 5 秒',observation_gap:'观测中断',observation_jump:'位置跳变'};
  box.textContent=kickErrorLabel(data)+(e?`。计划 ${angle(e.planned_direction)} / 实测 ${angle(e.actual_direction)}${e.end_reason?'；结束：'+(reason[e.end_reason]||e.end_reason):''}`:'')+
    `。本次 Agent 运行累计 ${data.count} 次有效出球，平均绝对偏角 ${Number.isFinite(data.mean_absolute_error_deg)?data.mean_absolute_error_deg.toFixed(1)+'°':'—'}。`+
    '以出球前计划方向为基准（正值左偏），仅取首次转向前球位；移动至少 0.15 米，转向阈值 10°。根据球速变化和附近唯一踢球者估计，非触球确认。';
}
function renderInterceptions(d){
  renderKickError(d.kick_error);
  const historyBox=$('kick-error-history');historyBox.replaceChildren();
  const kickEvents=kickEventsFor(d);
  if(!kickEvents.length)line(historyBox,'暂无踢球记录。');
  for(const e of kickEvents){
    const detail=Number.isFinite(e.planned_direction)&&Number.isFinite(e.actual_direction)?` · 计划 ${(e.planned_direction*180/Math.PI).toFixed(1)}° / 实测 ${(e.actual_direction*180/Math.PI).toFixed(1)}°`:'';
    line(historyBox,`#${e.id??'?'} · ${Number.isFinite(e.time)?e.time.toFixed(2)+' s':''} · ${kickErrorLabel({latest:e})}${detail}`);
  }
  const b=d.ball_prediction,box=$('intercept-details');box.replaceChildren();
  $('prediction-note').textContent=ballPredictionDescription(b, d.context.field);
  const a=d.attack_decision;
  let decision='本帧未记录 attacker / freeman 选择依据。';
  if(a){
    const scores=Array.isArray(a.candidates)?a.candidates.map(p=>`${p.id} 号 ${predictionNumber(p.score_s)?predictionFixed(p.score_s)+' s':'无法估算'}`).join('；'):'';
    const reason=a.reason==='hysteresis'?`保留原 attacker ${a.selected_id} 号：${a.best_id} 号虽更快，但优势不足 ${predictionFixed(a.margin_s)} s。`:
      a.reason==='no_estimate'?`所有候选均无法估算，${a.previous_id===a.selected_id?'保留原':'暂选'} attacker ${a.selected_id} 号。`:
      `选择 ${a.selected_id} 号为 attacker：接球评分最低（相同时保留原角色，否则按编号）。`;
    decision=`${reason} 切换缓冲 ${predictionFixed(a.margin_s)} s。候选评分：${scores}。`;
  }
  $('decision-note').textContent=decision;
  for(const p of d.players.filter(p=>['attack','freeman'].includes(p.role))){
    const card=document.createElement('article');card.className='card';
    const h=document.createElement('h3');h.textContent=`${p.id} 号 · ${p.role==='attack'?'attacker / 进攻手':'freeman / 自由人'}`;card.append(h);
    const metric=document.createElement('p');metric.className='metric';metric.textContent=interceptLabel(p.intercept);card.append(metric);
    const t=p.intercept;
    if(validIntercept(t)&&t.status!=='unavailable'){
      if(t.status==='fallback')metric.classList.add('fallback');
      line(card,`${t.status==='intercept'?'预计截球点':'追赶参考点'}：${pointText(t.target)} · 球在 ${predictionFixed(t.target_time_s)} s 后的位置`);
      line(card,`到达耗时 ${predictionFixed(t.arrival_time_s)} s = 转向 ${predictionFixed(t.turn_time_s)} s + 移动 ${predictionFixed(t.move_time_s)} s`);
      line(card,t.status==='intercept'?`到达耗时 ≤ 球到达时间，可提前 ${predictionFixed(t.slack_s)} s 到位。`:`窗口内没有可达点。评分 max(${predictionFixed(t.horizon_s)}, ${predictionFixed(t.arrival_time_s)}) = ${predictionFixed(t.score_s)} s，仅用于排序。`);
      const details=document.createElement('details');details.open=true;
      const summary=document.createElement('summary');summary.textContent='计算细节';details.append(summary);
      line(details,`直线距离 ${predictionFixed(t.distance_m)} m；控球半径 ${predictionFixed(t.control_radius_m)} m。`);
      line(details,`移动 = max(0, 距离 − 控球半径) ÷ ${predictionFixed(t.linear_speed_mps)} m/s = ${predictionFixed(t.move_distance_m)} ÷ ${predictionFixed(t.linear_speed_mps)} = ${predictionFixed(t.move_time_s)} s。`);
      line(details,`朝向差 ${predictionFixed(t.turn_angle_rad*180/Math.PI)}°（${predictionFixed(t.turn_angle_rad)} rad）；允许角差 ${predictionFixed(t.turn_threshold_rad*180/Math.PI)}°（${predictionFixed(t.turn_threshold_rad)} rad）。`);
      line(details,t.distance_m<=t.omni_distance_m?`距离 ≤ ${predictionFixed(t.omni_distance_m)} m，按全向移动，转向耗时 = 0 s。`:`转向 = max(0, ${predictionFixed(t.turn_angle_rad)} − ${predictionFixed(t.turn_threshold_rad)}) ÷ ${predictionFixed(t.angular_speed_rps)} rad/s = ${predictionFixed(t.turn_time_s)} s。`);
      line(details,`从 0 到 ${predictionFixed(t.horizon_s)} s，每 ${predictionFixed(t.step_s)} s 检查一次，取首个“转向 + 移动 ≤ 预测时间”的点。忽略避障、对手及机器人加速度。`);
      card.append(details);
    }else if(t?.status==='unavailable')line(card,`原因：${({no_pose:'机器人位置未知',no_ball:'球观测无效或过期',fallen:'机器人倒地',penalized:'机器人被罚下'})[t.reason]||'状态不可用'}`);
    box.append(card);
  }
}
function validBallStop(b){
  const s=b?.stop;
  return b?.velocity_estimated===true&&predictionPoint(s?.position)&&
    predictionNumber(s.time_s)&&s.time_s>=0&&predictionNumber(s.distance_m)&&s.distance_m>=0;
}
function ballPredictionDescription(b,field){
  if(!validBallPrediction(b))return '本帧没有有效球预测；旧回放若未记录预测数据，不补算历史轨迹。';
  const model=({sliding_then_rolling:'滑动／滚动减速预测',pure_rolling:'滚动减速预测'})[b.model]||'匀速直线预测（旧数据）';
  const phase=({sliding:'滑动',rolling:'滚动',stopped:'静止',unknown:'估计中'})[b.phase]||'未记录';
  const speed=predictionNumber(b.current_speed_mps)?b.current_speed_mps:Math.hypot(...b.velocity);
  let text=`${model} ${predictionFixed(b.horizon_s)} s · 当前估计球速 ${predictionFixed(speed)} m/s · 阶段：${phase} · 观测延迟 ${predictionFixed(b.observation_age_s)} s。`;
  if(validBallStop(b)){
    const s=b.stop,outside=field&&(Math.abs(s.position[0])>field.length/2||Math.abs(s.position[1])>field.width/2);
    text+=`无干扰停止点 ${pointText(s.position)} m · 剩余 ${predictionFixed(s.distance_m)} m · 预计 ${predictionFixed(s.time_s)} s 后停止${outside?'（球场外）':''}。`;
  }else text+='无干扰停止点：本帧观测不足或未记录。';
  return text+'不考虑后续触球、碰撞和反弹；停止点不受预测时域或球场边界限制。';
}
function drawBallPrediction(d,xy){
  if(!$('prediction-path').checked||!validBallPrediction(d.ball_prediction))return;
  const b=d.ball_prediction;
  g.save();g.beginPath();g.rect(0,0,c.width,c.height);g.clip();
  g.strokeStyle='#ffc16b';g.lineWidth=3;g.setLineDash([10,6]);g.beginPath();
  b.points.forEach((p,i)=>g[i?'lineTo':'moveTo'](...xy(...p.position)));g.stroke();g.setLineDash([]);
  g.font='18px system-ui';g.textAlign='left';g.textBaseline='bottom';
  const moving=Math.hypot(...b.velocity)>0.05;
  for(const p of b.points){
    if(p.t===0||!moving&&p!==b.points.at(-1))continue;
    const [x,y]=xy(...p.position);g.fillStyle='#ffc16b';g.beginPath();g.arc(x,y,4,0,Math.PI*2);g.fill();
    if(x>0&&x<1040&&y>20&&y<720){g.fillStyle='#ffe1b0';g.fillText(moving?`+${p.t.toFixed(1)}s`:`静止 +${p.t.toFixed(1)}s`,x+7,y-7)}
  }
  if(validBallStop(b)){
    const [x,y]=xy(...b.stop.position);
    if(x>=0&&x<=c.width&&y>=0&&y<=c.height){
      g.strokeStyle='#ff86ce';g.fillStyle='#ff86ce';g.lineWidth=2;
      g.beginPath();g.arc(x,y,9,0,Math.PI*2);g.moveTo(x-13,y);g.lineTo(x+13,y);g.moveTo(x,y-13);g.lineTo(x,y+13);g.stroke();
      g.fillText(`无干扰停止点 +${predictionFixed(b.stop.time_s)}s`,Math.max(8,Math.min(x+16,c.width-270)),Math.max(24,y-15));
    }else{
      g.fillStyle='#ff86ce';g.fillText('无干扰停止点在画面外，坐标见下方说明',20,30);
    }
  }
  let labelIndex=0;
  for(const p of d.players){
    const t=p.intercept,pose=d.context.teammates[p.id]?.pose;
    if(!['attack','freeman'].includes(p.role)||!pose||!validIntercept(t)||t.status==='unavailable')continue;
    const [x,y]=xy(...t.target);g.strokeStyle=p.role==='attack'?'#71edee':'#dda8ff';g.lineWidth=2;g.setLineDash([3,7]);
    g.beginPath();g.moveTo(...xy(pose.x,pose.y));g.lineTo(x,y);g.stroke();g.setLineDash([]);g.beginPath();
    if(t.status==='intercept')g.arc(x,y,11+labelIndex*4,0,Math.PI*2);
    else{g.moveTo(x-9,y-9);g.lineTo(x+9,y+9);g.moveTo(x+9,y-9);g.lineTo(x-9,y+9)}g.stroke();
    const label=`${p.id} ${p.role==='attack'?'A':'F'} ${t.status==='intercept'?predictionFixed(t.intercept_time_s)+'s':'追赶参考'}`;
    if(x>0&&x<1000&&y>40&&y<680){g.fillStyle='#0b121b';g.fillRect(x+14,y+12+labelIndex*24,g.measureText(label).width+12,22);g.fillStyle=g.strokeStyle;g.fillText(label,x+20,y+32+labelIndex*24)}
    labelIndex++;
  }
  g.restore();
}
$('prediction-path').onchange=()=>{if(displayedPacket)draw(displayedPacket)};

// 只用发出当时记录的预测点，对照之后的真实球观测，绝不用预测器自证准确。
// 以完整已加载录制为范围，支持实时历史与导入文件；不随回放游标改变。
function evaluatePredictionAccuracy(list){
  const horizons=[.5,1,2,3], minimumSpeed=.1, hitRadius=.5, maxGap=.2, interval=.5;
  const rows=horizons.map(h=>({horizon:h,valid:0,hits:0,errorSum:0,skipped:0,pending:0}));
  const finite=n=>typeof n==='number'&&Number.isFinite(n);
  let pending=[],previous=null,lastNow=null,lastSequence=null,lastSample=-Infinity,lastObservation=null;
  const skipPending=()=>{for(const q of pending)rows[q.index].skipped++;pending=[]};
  for(const frame of list){
    const packet=frame.packet,w=packet?.context,now=w?.now;
    if(!finite(now))continue;
    // 时间回退或发送序号重置时，不把前一次运行的预测与新比赛观测拼接。
    if(lastNow!==null&&(now<lastNow||(finite(packet.sequence)&&lastSequence!==null&&packet.sequence<lastSequence))){
      skipPending();previous=null;lastSample=-Infinity;lastObservation=null;
    }
    lastNow=now;if(finite(packet.sequence))lastSequence=packet.sequence;
    const b=w.ball,observedAt=b?.last_seen_at;
    // 没有明确观测时间的旧文件不拿接收时间冒充测量时间。
    const current=b&&finite(b.x)&&finite(b.y)&&finite(observedAt)&&observedAt>0&&observedAt<=now+1e-6
      ?{time:observedAt,x:b.x,y:b.y}:null;
    const remaining=[];
    for(const q of pending){
      let actual=null;
      if(current&&Math.abs(current.time-q.time)<1e-6)actual=current;
      else if(previous&&current&&previous.time<=q.time&&q.time<=current.time&&current.time>previous.time&&current.time-previous.time<=maxGap+1e-6){
        const t=(q.time-previous.time)/(current.time-previous.time);
        actual={x:previous.x+(current.x-previous.x)*t,y:previous.y+(current.y-previous.y)*t};
      }
      if(actual){const error=Math.hypot(q.point[0]-actual.x,q.point[1]-actual.y),row=rows[q.index];
        row.valid++;row.errorSum+=error;if(error<=hitRadius+1e-9)row.hits++;
      }else if((current&&current.time>q.time)||now>q.time+maxGap){rows[q.index].skipped++}
      else remaining.push(q);
    }
    pending=remaining;
    if(current&&(!previous||current.time>previous.time))previous=current;
    const prediction=packet.ball_prediction,v=prediction?.velocity;
    if(!current||now-current.time>maxGap||current.time===lastObservation||now-lastSample<interval-1e-6||
       prediction?.velocity_estimated!==true||!Array.isArray(v)||v.length!==2||!v.every(finite)||Math.hypot(...v)<minimumSpeed)continue;
    lastSample=now;lastObservation=current.time;
    horizons.forEach((h,index)=>{
      const point=prediction.points?.find(p=>finite(p.t)&&Math.abs(p.t-h)<1e-6)?.position;
      if(!Array.isArray(point)||point.length!==2||!point.every(finite)){rows[index].skipped++;return}
      pending.push({index,time:now+h,point});
    });
  }
  for(const q of pending)rows[q.index].pending++;
  return rows.map(r=>({...r,accuracy:r.valid?r.hits/r.valid:null,meanError:r.valid?r.errorSum/r.valid:null}));
}
let accuracyRenderedAt=-Infinity,accuracySource=null;
function renderPredictionAccuracy(list){
  const now=performance.now(),source=`${archive?'file:'+importName:'live:'+historyTeam+':'+session}:${list[0]?.id??list[0]?.time??'empty'}`;
  // 历史每帧追加，但统计最多每秒刷新一次，避免在 30 Hz 绘图中重复扫描录制。
  if(source===accuracySource&&now-accuracyRenderedAt<1000)return;
  accuracySource=source;accuracyRenderedAt=now;
  const results=evaluatePredictionAccuracy(list),scope=$('accuracy-scope');
  scope.textContent=list.length?`${archive?'导入文件':'当前队伍已加载录制'} · ${list.length} 帧 · ${elapsed(list.at(-1).time-list[0].time)}。统计整个已加载范围，不随回放位置变化${!archive&&syncing?'；历史同步中，结果尚不完整':''}。`:'暂无录制，等待带有效运动预测的数据。';
  $('accuracy-results').replaceChildren();
  for(const row of results){
    const card=document.createElement('article');card.className='card';
    const title=document.createElement('h3');title.textContent=`预测未来 ${row.horizon} 秒`;card.append(title);
    const rate=document.createElement('p');rate.className='metric';rate.textContent=row.accuracy===null?'暂无有效对照':`${(row.accuracy*100).toFixed(1)}%`;card.append(rate);
    line(card,`命中 / 有效：${row.hits} / ${row.valid}`);
    line(card,`平均位置误差：${row.meanError===null?'—':row.meanError.toFixed(3)+' 米'}`);
    line(card,`待验证：${row.pending} · 缺失对照或预测：${row.skipped}`);
    $('accuracy-results').append(card);
  }
}


// Each opponent is an independent, normalized mixture of uniform disks.
// Additive opacity shows density; circle outlines show possible control coverage.
function drawInterceptionRisk(d,xy,s){
  const p=d.players.find(p=>p.role==='attack'),r=p?.interception_risk;
  const note=$('risk-note');
  if(!r||r.status==='unavailable'||r.event!=='intercept_kick_before_target'){
    note.textContent='出球被截风险：暂无有效出球路径，或此回放未记录新版出球概率。';return;
  }
  const prob=r.status==='blocked'?`不可踢（${kickLaneReason(r.reason)}）`:r.probability==null?'未知':`${(100*r.probability).toFixed(1)}%`;
  note.textContent=`${p.id} 号预计 ${r.time_s.toFixed(2)} s 后从 ${pointText(r.ball_position)} 向 T ${pointText(r.target)} 出球 · 途中被截 ${prob}${r.status==='partial'?'（仅计已知对手）':''}。假设球速 ${r.ball_speed_mps.toFixed(1)} m/s，飞行 ${r.flight_time_s.toFixed(2)} s；红色为出球时敌方站位密度，虚线圆为出球时可能控球范围，球路颜色显示累计被截风险。以成功接球出球为前提；模型未校准。`;
  if(!$('interception-risk').checked)return;
  g.save();
  // Rasterize the exact analytic density rather than interpreting density as probability.
  const f=d.context.field,step=.12;
  for(let x=-f.length/2;x<f.length/2;x+=step)for(let y=-f.width/2;y<f.width/2;y+=step){
    let density=0;
    for(const enemy of r.opponents)for(const b of enemy.density_components)
      if(Math.hypot(x+step/2-b.position[0],y+step/2-b.position[1])<=b.radius_m)density+=b.density_per_m2;
    if(density){g.fillStyle=`rgba(255,80,95,${.7*(1-Math.exp(-density))})`;g.fillRect(...xy(x,y+step),step*s,step*s)}
  }
  g.lineWidth=4;
  for(let i=1;i<r.path.length;i++){
    const risk=r.path[i].probability??0;
    g.strokeStyle=`rgb(${Math.round(90+165*risk)},${Math.round(220-150*risk)},100)`;
    g.beginPath();g.moveTo(...xy(...r.path[i-1].position));g.lineTo(...xy(...r.path[i].position));g.stroke();
  }
  g.lineWidth=1;g.font='12px system-ui';
  const [tx,ty]=xy(...r.target);g.fillStyle='#ffffff';g.fillText('T',tx+10,ty-10);
  g.strokeStyle='#ffffff';g.strokeRect(tx-6,ty-6,12,12);
  for(const enemy of r.opponents)for(const b of enemy.density_components){
    const [x,y]=xy(...b.position);g.strokeStyle='#ff8f9c';g.setLineDash([4,5]);
    g.beginPath();g.arc(x,y,(b.radius_m+r.control_radius_m)*s,0,2*Math.PI);g.stroke();g.setLineDash([]);
    g.fillStyle='#ffd1d7';g.fillText(`${enemy.id} ${b.strategy==='chase_ball'?'追球':'截点'} ${(b.weight*100).toFixed(0)}% · ${(enemy.probability*100).toFixed(0)}%`,x+5,y-8);
    g.beginPath();g.moveTo(x,y);g.lineTo(x+15*Math.cos(b.heading),y-15*Math.sin(b.heading));g.stroke();
  }
  for(const [point,color,label] of [[r.robot_position,'#71edee','出球站位'],[r.ball_position,'#ffcf66','出球点']]){
    const [x,y]=xy(...point);g.strokeStyle=color;g.fillStyle=color;g.beginPath();g.arc(x,y,7,0,2*Math.PI);g.stroke();g.fillText(label,x+10,y+15);
  }
  g.restore();
}
$('interception-risk').onchange=()=>{if(displayedPacket)draw(displayedPacket)};


function validAttackCandidates(a){
  const finite=Number.isFinite,point=p=>Array.isArray(p)&&p.length===2&&p.every(finite);
  return a&&finite(a.evaluated_at)&&a.decision_enabled===false&&
    (a.origin===null||point(a.origin))&&Array.isArray(a.candidates)&&a.candidates.length<=40&&
    a.candidates.every(c=>typeof c.id==='string'&&typeof c.label==='string'&&['goal','teammate'].includes(c.kind)&&
      ['ok','partial','unavailable','blocked'].includes(c.status)&&(c.target===null||point(c.target))&&
      (c.probability===null||finite(c.probability)&&c.probability>=0&&c.probability<=1)&&
      (c.receiver_position==null||point(c.receiver_position)));
}
function renderCandidateTargets(d){
  const a=d.players.find(p=>p.role==='attack')?.attack_candidates;
  const box=$('candidate-details'),select=$('candidate-select'),note=$('candidate-note');
  const selected=select.value;box.replaceChildren();select.replaceChildren();
  const actual=document.createElement('option');actual.value='';actual.textContent='实际目标';select.append(actual);
  if(!validAttackCandidates(a)){note.textContent='本帧暂无候选评估。未来的自己：TODO。';return}
  note.textContent=`仅展示，未用于决策 · 评估距本帧 ${Math.max(0,d.context.now-a.evaluated_at).toFixed(2)} s · 球门 7 个采样点；队友按预计接球位置朝球门中心偏移 ${a.candidates.find(c=>c.kind==='teammate'&&c.lead_m!=null)?.lead_m??'未知'} m · 球速 ${a.ball_speed_mps} m/s · 每分支 ${a.samples_per_branch} 个样本。未来的自己：TODO。`;
  for(const c of a.candidates){
    const option=document.createElement('option');option.value=c.id;option.textContent=c.label;select.append(option);
    const card=document.createElement('article');card.className='card';
    const h=document.createElement('h3');h.textContent=c.label;card.append(h);
    line(card,`被截概率：${c.status==='blocked'?'不可踢 · '+kickLaneReason(c.reason):c.probability===null?'未知':(100*c.probability).toFixed(1)+'%'}${c.status==='partial'?'（观测不完整）':''}`);
    line(card,`T：${pointText(c.target)}`);
    if(c.receiver_position){line(card,`预计接球位置：${pointText(c.receiver_position)}`);line(card,`预测：${c.receiver_model==='constant_velocity'?'连续观测匀速外推':'速度未知，静止占位'} · ${c.receive_time_s.toFixed(2)} s 后`)}
    if(c.status==='unavailable')line(card,`无法评估：${c.reason==='no_receiver_pose'?'队友位置缺失':c.reason==='no_reachable_intercept'?'没有可达截球点':'没有有效球路'}`);
    box.append(card);
  }
  select.value=a.candidates.some(c=>c.id===selected)?selected:'';
}
function drawCandidateTarget(d,xy){
  const a=d.players.find(p=>p.role==='attack')?.attack_candidates;
  if(!validAttackCandidates(a)||!a.origin)return;
  const selected=a.candidates.find(c=>c.id===$('candidate-select').value);
  g.save();g.font='12px system-ui';
  for(const c of a.candidates){
    if(!c.target)continue;
    const [x,y]=xy(...c.target);g.fillStyle=c.probability===null?'#9caec3':`hsl(${120*(1-c.probability)},80%,65%)`;
    g.beginPath();g.arc(x,y,c===selected?7:4,0,2*Math.PI);g.fill();
    g.fillText(`${c.label} ${c.probability===null?'?':(100*c.probability).toFixed(0)+'%'}`,x+10,y);
  }
  if(selected?.target){
    g.strokeStyle='#ecb8ff';g.lineWidth=3;g.setLineDash([8,5]);g.beginPath();g.moveTo(...xy(...a.origin));g.lineTo(...xy(...selected.target));g.stroke();
    if(selected.receiver_position){const [x,y]=xy(...selected.receiver_position);g.setLineDash([]);g.beginPath();g.arc(x,y,9,0,2*Math.PI);g.stroke();g.fillStyle='#ecb8ff';g.fillText('队友预计位置',x+12,y+18)}
  }
  g.restore();
}
$('candidate-select').onchange=()=>{if(displayedPacket)draw(displayedPacket)};

function kickLaneReason(reason){
  return {goal_post:'球路碰门柱',goal_net:'球路穿球网',goal_side_entry:'无法从正面门口入球'}[reason]||'球路被结构阻挡';
}

function renderScoringProbability(d){
  const box=$('scoring-details');box.replaceChildren();
  const a=d.players.find(p=>p.role==='attack')?.scoring_probability;
  if(!a){line(box,'本帧没有进球概率评估。');return}
  const pct=p=>typeof p==='number'&&Number.isFinite(p)?(100*p).toFixed(1)+'%':'未知';
  line(box,`进球率 = 1 − 被拦概率；球网/门柱阻挡为 0。以下为未扣两侧惩罚的原始路线估计；实际射门扣分与选点见选择依据。传自由人路线仅展示。数据年龄 ${Math.max(0,d.context.now-a.evaluated_at).toFixed(2)} s。`);
  const card=(title)=>{const e=document.createElement('article');e.className='card';const h=document.createElement('h3');h.textContent=title;e.append(h);box.append(e);return e};
  const own=card('自己直接射门');line(own,`估计进球概率：${pct(a.direct.probability)}`);line(own,`评估最佳落点：${pointText(a.direct.target)}`);
  for(const row of a.via_freeman){const e=card(`传给自由人 ${row.player_id} 号后射门`);
    line(e,`估计进球概率：${pct(row.probability)}`);
    line(e,`传球未被截 ${pct(row.pass_probability)} × 接球后射门未被截 ${pct(row.shot_probability)}`);
    line(e,`接球点：${pointText(row.receive_target)} · 射门落点：${pointText(row.target)}`);
  }
  if(!a.via_freeman.length)line(box,'当前没有分配自由人角色。');
  line(box,'占位模型：假设自由人接球后立即射门，更新敌方到接球时刻的站位；两阶段乘积为近似，不含接球失误/转身耗时，也未按传球成功筛选敌方轨迹。');
}

function validScoringProbability(a){
  const prob=v=>v===null||typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<=1;
  const point=v=>v===null||Array.isArray(v)&&v.length===2&&v.every(Number.isFinite);
  return a&&Number.isFinite(a.evaluated_at)&&typeof a.decision_enabled==='boolean'&&a.direct&&
    prob(a.direct.probability)&&point(a.direct.target)&&Array.isArray(a.via_freeman)&&a.via_freeman.length<=40&&
    a.via_freeman.every(r=>Number.isFinite(r.player_id)&&prob(r.probability)&&prob(r.pass_probability)&&
      prob(r.shot_probability)&&point(r.target)&&point(r.receive_target));
}

function renderAttackChoice(d){
  const a=d.players.find(p=>p.role==='attack')?.attack_choice,box=$('attack-choice');if(!box)return;box.replaceChildren();
  const receiving=d.players.find(p=>p.role==='attack')?.attack_receive;
  if(receiving){
    const stages={intercept:'前往拦球点',wait_ball:'等待来球',low_speed_chase:'低速球接近',rebound_chase:'补射接近',auto_kick:'自动踢球',unavailable:'接球数据不足'};
    line(box,`接球阶段：${stages[receiving.stage]||receiving.stage} · ${receiving.reason}`);
    if(receiving.speed_kind==='estimated_current'){
      const fmt=v=>Number.isFinite(v)?v.toFixed(3):'未知';
      line(box,`当前估计球速 ${fmt(receiving.speed_mps)} m/s · 区间平均 ${fmt(receiving.observed_interval_speed_mps)} m/s · 低速直通条件 < 0.500 m/s`);
    }
    if(receiving.settling){
      const s=receiving.settling,fmt=v=>Number.isFinite(v)?v.toFixed(3):'未知';
      line(box,`停稳窗口：${s.sample_count} 个观测 / ${fmt(s.span_s)} s · 趋势 ${fmt(s.trend_mps)} m/s · 跨度 ${fmt(s.spread_m)} m · ${s.settled?'已停稳':'尚未停稳'}`);
    }
    if(receiving.wait_point)line(box,`等待点 ${pointText(receiving.wait_point)} · 安全间距 ${Number(receiving.standoff_m).toFixed(2)} m`);
    line(box,`拦球点 ${pointText(receiving.intercept_point)} · 出球点 ${pointText(receiving.outgoing_target)} · 夹角 ${Number.isFinite(receiving.angle_deg)?receiving.angle_deg.toFixed(1)+'°':'未定义'}`);
    if(receiving.stable_s!==undefined)line(box,`近身低速持续 ${Number.isFinite(receiving.stable_s)?receiving.stable_s.toFixed(2):'0.00'} s；不代表已确认触球。`);
  }
  if(!a){line(box,'本帧未记录选择依据。');return}
  if(!a.enabled){line(box,'使用指定目标或特殊阶段策略，本帧未自动比较选点。');return}
  if(a.model==='pass_risk_margin'){
    const pct=p=>p==null?'未知':(100*p).toFixed(1)+'%';
    line(box,`直射原始被截率 ${pct(a.direct_risk)} · 最佳传射综合被截率 ${pct(a.best_pass_risk)}`);
    line(box,`降低 ${a.risk_improvement==null?'未知':(100*a.risk_improvement).toFixed(1)} 个百分点；至少 ${(100*a.required_margin).toFixed(0)} 个百分点才传球。射门中央偏好不计入该差值。`);
    line(box,a.selected?.kind==='pass'?`传给队友 ${a.selected.player_id} 号，预计接球点 ${pointText(a.selected.target)}`:`保留直接射门 ${pointText(a.actual_target)}`);
    line(box,`实际目标 ${pointText(a.actual_target)} · 执行 ${a.execution||'待定'} · 数据年龄 ${Math.max(0,d.context.now-a.evaluated_at).toFixed(2)} s`);
    for(const o of a.pass_options||[])line(box,`队友 ${o.player_id}：传球成功 ${pct(o.pass_probability)} × 后续射门成功 ${pct(o.shot_probability)} = ${pct(o.probability)}${o.excluded_reason?' · 排除：'+o.excluded_reason:''}`);
    line(box,'接球后立即射门为模型假设，不代表实际接球或进球已确认。');
    return;
  }
  if(['shot_edge_penalty','shot_outward_penalty'].includes(a.model)){
    const pct=p=>p==null?'未知':(100*p).toFixed(1)+'%';
    line(box,a.model==='shot_outward_penalty'?'被截率惩罚：中央 +0，每向外一点 +5 个百分点；射门评分 = max(0, 1 − 被截率 − 惩罚)。同分优先中央。模型分数不是实测进球率。':'射门评分 = 1 − 被截概率 − 两端点惩罚（10 个百分点）；同分优先中央。模型分数不是实测进球率。');
    line(box,a.selected?`选择射门点：${pointText(a.selected.target)} · 扣分后 ${pct(a.selected.probability)}`:'没有合适方案，使用球门中央射门兜底。');
    line(box,`实际目标：${pointText(a.actual_target)} · ${a.execution==='blocked_stop'?'结构阻挡，停止':a.execution==='pass'?'结构阻挡，沿用安全传球替代':'射门'} · 数据年龄 ${Math.max(0,d.context.now-a.evaluated_at).toFixed(2)} s`);
    for(const o of a.options)line(box,`${pointText(o.target)}：被截 ${pct(o.interception_probability)} · 原始成功 ${pct(o.raw_probability)} − ${(100*o.penalty).toFixed(0)} 个百分点 = ${pct(o.probability)}${o.excluded_reason?' · 排除：'+({unknown_or_blocked:'未知或结构阻挡',zero_probability:'扣分后为零',current_lane_blocked:'当前球路结构阻挡',no_target:'无目标'}[o.excluded_reason]||o.excluded_reason):''}`);
    return;
  }
  const name=o=>o.fallback?`保球传给 ${o.player_id} 号 ${pointText(o.target)}`:o.kind==='pass'?`传自由人 ${o.player_id} 号，再射 ${pointText(o.shot_target)}`:`直接射门 ${pointText(o.target)}`;
  const pct=p=>(100*p).toFixed(1)+'%';
  if(a.restart_policy)line(box,`重启类型：${a.restart_policy.restart} · ${a.restart_policy.allow_shot?'允许比较射门':'需先传球'} · ${a.restart_policy.allow_pass?'允许传球':'仅考虑射门'}`);
  if(!a.selected){line(box,'暂无已知且进球概率大于 0 的可用方案：近球时停止出脚，远球时继续追球。');}
  if(a.selected){
  line(box,`选择：${name(a.selected)} · ${a.selected.fallback?'估计传球成功':'估计进球'} ${pct(a.selected.probability)} · 实际出球目标 ${pointText(a.selected.target)}`);
  if(a.selected.fallback)line(box,'依据：没有可用进球路线，选择模型估计传球成功率至少 80% 的横传/回传；优先成功率，相同时选较短球路。此数值不是进球概率。');
  if(a.selected.kind==='pass'&&!a.selected.fallback)line(box,`依据：传球未被截 ${pct(a.selected.pass_probability)} × 第二脚射门未被截 ${pct(a.selected.shot_probability)}`);
  if(a.runner_up)line(box,`次选：${name(a.runner_up)} · ${pct(a.runner_up.probability)}；优势 ${(100*a.margin).toFixed(1)} 个百分点。`);
  line(box,`评估距本帧 ${Math.max(0,d.context.now-a.evaluated_at).toFixed(2)} s；${a.selected.fallback?'保球传球同分选较短球路。':'完全同分时保留上次方案，其次直射、靠近球门中心。'}`);
  }
  for(const option of a.options)line(box,`${name(option)}：${option.probability==null?'未知':pct(option.probability)}${option.excluded_reason?' · 排除：'+({restart_requires_pass:'该重启先传球',penalty_requires_shot:'点球仅射门',outside_field:'目标出界',not_lateral_or_back:'不是横传或回传',pass_too_short:'传球距离过短',unsafe_pass:'传球成功率未知或不足 80%',unknown_or_blocked:'未知或被结构阻挡',zero_probability:'进球概率为 0',receiver_unavailable:'接球队员不可用',current_lane_blocked:'当前球路被门柱/球网阻挡',no_target:'无目标'}[option.excluded_reason]||option.excluded_reason):''}`);
}

function validAttackChoice(a){
  const point=p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite);
  const prob=p=>p===null||typeof p==='number'&&Number.isFinite(p)&&p>=0&&p<=1;
  const option=o=>o&&typeof o.id==='string'&&['shot','pass'].includes(o.kind)&&
    (o.target===null||point(o.target))&&prob(o.probability)&&
    (o.kind!=='pass'||Number.isFinite(o.player_id)&&(o.fallback?o.shot_target===null:point(o.shot_target))&&prob(o.pass_probability)&&prob(o.shot_probability));
  return a&&typeof a.enabled==='boolean'&&Number.isFinite(a.evaluated_at)&&Array.isArray(a.options)&&
    a.options.length<=320&&a.options.every(option)&&(a.selected===null||option(a.selected))&&
    (a.runner_up==null||option(a.runner_up))&&(a.margin==null||prob(a.margin));
}


function validBlockPoint(p){
  return p&&['reachable','stationary'].includes(p.status)&&
    ['teammates','opponents'].includes(p.side)&&predictionPoint(p.target)&&
    predictionPoint(p.ball_position)&&['effective_radius_m','ball_time_s','arrival_time_s',
      'move_distance_m','turn_time_s','move_time_s','slack_s'].every(k=>predictionNumber(p[k]));
}
function blockPointLabel(p){
  const reasons={no_pose:'位置未知',fallen:'倒地',penalized:'被罚下',no_ball:'球观测无效',
    unknown_velocity:'球速尚未估计',ball_outside_field:'球已出界',outside_field:'站位在场外',
    no_feasible_point:'预测窗口内无可行拦球点'};
  if(!validBlockPoint(p))return reasons[p?.reason]||'无有效拦球数据';
  return `${p.status==='stationary'?'静止球接近点':'最近拦球站位'} ${pointText(p.target)} · 移动 ${predictionFixed(p.move_distance_m)} m`;
}
function renderBlockPoints(d){
  const box=$('block-details');box.replaceChildren();
  if(!Array.isArray(d.block_points)){line(box,'本帧未记录最近拦球点（旧数据）。');return}
  for(const p of d.block_points){
    const card=document.createElement('article');card.className='card';
    const h=document.createElement('h3');h.textContent=`${p.side==='teammates'?'我方':'对方'} ${p.id} 号`;card.append(h);
    line(card,blockPointLabel(p));
    if(validBlockPoint(p)){
      line(card,`到位 ${predictionFixed(p.arrival_time_s)} s = 转向 ${predictionFixed(p.turn_time_s)} + 移动 ${predictionFixed(p.move_time_s)} s`);
      if(p.status==='reachable')line(card,`球到达 ${predictionFixed(p.ball_time_s)} s · 提前 ${predictionFixed(p.slack_s)} s · 球位 ${pointText(p.ball_position)}`);
      else line(card,'球已静止，此点仅表示接近位置。');
    }
    if(p.state_known===false)line(card,'未获得倒地/受罚状态，按正常移动估计。');
    box.append(card);
  }
}
function drawBlockPoints(d,xy,scale){
  if(!$('block-points').checked)return;
  g.save();g.font='14px system-ui';g.textBaseline='bottom';
  let index=0;
  for(const p of d.block_points||[]){
    const pose=d.context[p.side]?.[p.id]?.pose;
    if(!validBlockPoint(p)||!pose)continue;
    const [x,y]=xy(...p.target),[bx,by]=xy(...p.ball_position);
    g.strokeStyle=p.side==='teammates'?'#69ffb4':'#ffad79';g.fillStyle=g.strokeStyle;g.lineWidth=2;
    g.setLineDash([5,5]);g.beginPath();g.moveTo(...xy(pose.x,pose.y));g.lineTo(x,y);g.stroke();g.setLineDash([]);
    g.strokeRect(x-5,y-5,10,10);g.beginPath();g.arc(x,y,p.effective_radius_m*scale,0,Math.PI*2);g.stroke();
    g.beginPath();g.arc(bx,by,3,0,Math.PI*2);g.fill();
    const label=`${p.side==='teammates'?'我':'敌'}${p.id} ${p.status==='stationary'?'静止':'拦球'} ${predictionFixed(p.move_distance_m)}m`;
    const lx=Math.max(8,Math.min(x+12,c.width-190)),ly=Math.max(22,Math.min(y-16+(index++%3)*20,c.height-8));
    g.fillStyle='#0b121b';g.fillRect(lx-3,ly-17,g.measureText(label).width+6,20);g.fillStyle=g.strokeStyle;g.fillText(label,lx,ly);
  }
  g.restore();
}
$('block-points').onchange=()=>{if(displayedPacket)draw(displayedPacket)};


// Threat data belongs to the displayed frame, including historical replay.
function threatRaceLabel(p){
  if(typeof p?.can_beat!=='boolean'||!Number.isFinite(p.margin_s))return '抢先判断不确定';
  if(p.margin_s===0)return '预计同时到球，不能抢先';
  return `${p.can_beat?'预计可抢先':'预计无法抢先'} · ${p.margin_s>0?'领先':'落后'} ${Math.abs(p.margin_s).toFixed(2)} s`;
}
function threatLabel(t){
  if(!t)return '威胁者：本帧未记录（旧数据或 Agent 尚未更新）';
  if(t.threat_id==null)return '威胁者：无法确定 · 缺少有效敌方截球预测';
  const estimate=t.threat_intercept;
  const time=Number.isFinite(estimate?.score_s)?` · ${estimate.score_s.toFixed(2)} s${estimate.status==='fallback'?'（窗口外排序估计）':''}`:'';
  const race=t.can_beat===true?`我方 ${t.first_player_id} 号预计可抢先`:t.can_beat===false?'我方预计无法抢先':'我方能否抢先：不确定';
  return `敌方威胁者：${t.threat_id} 号${time} · ${race}${t.status==='partial'?' · 敌方观测不完整':''}`;
}
function renderThreat(d){
  const basis=d.threat?.approach_basis;
  $('threat-note').textContent=threatLabel(d.threat)+(basis==='trajectory_line'?' · 按球运动直线分侧':basis==='goal_direction_fallback'?' · 按球门方向分侧（运动直线退化）':'');
  const box=$('threat-details');box.replaceChildren();
  for(const p of d.threat?.teammates||[])line(box,`我方 ${p.id} 号：${threatRaceLabel(p)}`);
}
function drawThreat(d,xy){
  const t=d.threat;
  if(t?.threat_id==null)return;
  const pose=d.context.opponents?.[t.threat_id]?.pose;
  if(!pose||!Number.isFinite(pose.x)||!Number.isFinite(pose.y))return;
  const [x,y]=xy(pose.x,pose.y);
  g.save();g.strokeStyle='#ff7185';g.lineWidth=3;g.setLineDash([]);
  g.beginPath();g.arc(x,y,22,0,Math.PI*2);g.stroke();
  const label=`威胁者 · 敌 ${t.threat_id}${t.status==='partial'?'（暂定）':''}`;
  g.font='bold 15px system-ui';g.textAlign='left';g.textBaseline='middle';
  const width=g.measureText(label).width+14;
  const lx=Math.max(6,Math.min(c.width-width-6,x+28)),ly=Math.max(16,Math.min(c.height-16,y+16));
  g.fillStyle='#311a28';g.fillRect(lx,ly-13,width,26);g.fillStyle='#ff99a7';g.fillText(label,lx+7,ly);
  g.restore();
}

function navigationLabel(n){
  const states={kick_yield:'取消踢球，为紧急封堵让行',moving:'行走',turning:'转向',avoiding:'绕行',yielding:'让行',blocked:'受阻停车',blocked_direct:'直线受阻',team_conflict_stop:'队内冲突停车',kick_approach_blocked:'踢球接近受阻',arrived:'已到位',no_pose:'位置未知',invalid_geometry:'位置无效',stopped:'停止',yield:'让行等待',emergency_stop:'安全停车',team_safety_stop:'队内冲突停车',direct_blocked:'直线受阻',no_route:'暂无可行路线'};
  const speed=Number.isFinite(n.speed)?n.speed.toFixed(2)+' m/s':Array.isArray(n.velocity)&&n.velocity.length===2&&n.velocity.every(Number.isFinite)?Math.hypot(...n.velocity).toFixed(2)+' m/s':'未知';
  const routes={new:'新路线',retained:'保留路线',faster:'耗时优势换路',unsafe:'旧路线受阻重规划',no_route:'暂无全局路线'};
  const eta=Number.isFinite(n.estimated_time_s)?` · 预计 ${n.estimated_time_s.toFixed(2)} s（模型估计）`:'';
  return `导航：${states[n.state||n.reason]||'未知'} · 优先级 ${Number.isFinite(n.priority)?n.priority:'—'} · 指令 ${speed}${n.route_reason?' · '+(routes[n.route_reason]||'路线状态未知'):''}${eta}${typeof n.blocker==='string'?' · 阻挡/让行对象 '+n.blocker:''}${Number.isFinite(n.blocker_priority)?'（优先级 '+n.blocker_priority+'）':''}${n.direction_held?' · 保持绕行方向':''}`;
}
