const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('tools/tactics_dashboard/dashboard.js','utf8');
const ctx={};vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function kickPowerLabel('),source.indexOf('function line(')),ctx);
for(const power of [1,3,5])assert.equal(ctx.kickPowerLabel({kick_power:power}),`踢球力度：${power} 档（指令）`);
assert.match(ctx.kickPowerLabel({kick_power:null}),/本帧未下发/);
assert.match(ctx.kickPowerLabel({kicking:true}),/未记录（旧数据）/);
assert.match(ctx.kickPowerLabel({kick_power:NaN}),/本帧未下发/);
// Verify the actual card renderer uses the per-frame field, including replay/idle frames.
const element=()=>({children:[],append(x){this.children.push(x)},replaceChildren(){this.children=[]}});
const elements={},cards=element();
Object.assign(ctx,{$:id=>id==='cards'?cards:(elements[id]??=element()),c:{},phases:{},
  roleNames:{},document:{createElement:element},pointText:JSON.stringify,
  line:(parent,text)=>parent.append(text),renderInterceptions(){},renderCandidateTargets(){},
  renderScoringProbability(){},renderAttackChoice(){},draw(){}});
vm.runInContext(source.slice(source.indexOf('function passingLines('),source.indexOf('function drawPassing(')),ctx);
vm.runInContext(source.slice(source.indexOf('function renderPacket('),source.indexOf('// Playback uses')),ctx);

function renderGuard(action,decision){
  ctx.renderPacket({context:{teammates:{}},players:[{id:2,role:'guard',action,guard_decision:decision}]});
  return cards.children[0].children.filter(x=>typeof x==='string').join('\n');
}
assert.match(renderGuard('guard:anticipate:ray',{shooter_id:2,ray_goal_y:.935}),/敌方 2 号/);
assert.match(renderGuard('guard:block:reachable',{shot_source:'short_hold_envelope',goal_time_s:.45,goal_crossing:[-7,1]}),/0.45 s/);
assert.match(renderGuard('guard:block:unreachable_hold',{}),/不表示已挡住球/);
assert.match(renderGuard('guard:close_clear',{outlet_style:'clear'}),/是否触球成功/);
assert.match(renderGuard('guard:set',{outlet_wait_reason:'fast_ball',ball_speed_mps:2}),/球速超过/);
const legacy=renderGuard('guard:set',undefined);
assert.match(legacy,/旧版数据/);
assert.doesNotMatch(legacy,/敌方 2 号|0.45 s/);
const malformed=renderGuard('guard:block:hold',{goal_time_s:NaN,goal_crossing:[null,Infinity],ray_goal_y:'bad'});
assert.doesNotMatch(malformed,/NaN|Infinity|bad/);
console.log('Guard cards: reasons, quantitative evidence, clearance requests, old frames and stale-state isolation passed.');
assert.match(renderGuard('guard:close_clear',{close_clear_target_status:'reselected',close_clear_old_target_reason:'robot_lane'}),/不重置超时/);
assert.match(renderGuard('guard:set',{close_clear_rejection:'contested'}),/争抢|更近|距离/);

assert.deepEqual(Array.from(ctx.passingLines({},1)),[]);
const pass={id:7,passer:1,receiver:3,stage:'release',target:[2,1],contact:null,receiver_ready:true,passer_ready:true,evidence:'kick_requested_only'};
assert.match(ctx.passingLines({passing:{active:pass}},1).join(' '),/1 → 3.*请求出球/);
assert.match(ctx.passingLines({passing:{active:pass}},3).join(' '),/kick_requested_only/);
assert.equal(ctx.passingLines({passing:{active:pass}},2).length,0);
assert.match(ctx.passingLines({passing:{support:{plan_id:7,passer:1,target:[0,2]}}},1).join(' '),/传后支援/);
assert.match(ctx.passingLines({passing:{events:[{id:7,passer:1,receiver:3,event:'cancelled',reason:'release_timeout'}]}},3).join(' '),/release_timeout/);
console.log('Passing cards: shared target, evidence, support, cancellation and legacy replay passed.');
