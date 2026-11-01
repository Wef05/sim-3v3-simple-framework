const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('tools/tactics_dashboard/dashboard.js','utf8');
const box={text:[],replaceChildren(){this.text=[]}},ctx={
  $:()=>box,line:(parent,text)=>parent.text.push(text),pointText:JSON.stringify};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function renderAttackChoice('),source.indexOf('function validBlockPoint(')),ctx);
const selected={id:'selected_route',kind:'shot',target:[7,0],probability:null,
                shot_fallback:true,advantage:0};
const choice={enabled:true,evaluated_at:1,selected,options:[selected],runner_up:null,margin:0};
assert.equal(ctx.validAttackChoice(choice),true);
ctx.renderAttackChoice({context:{now:1},players:[{role:'attack',attack_choice:choice}]});
assert.match(box.text.join('\n'),/估计进球 未知/);
assert.match(box.text.join('\n'),/射门保底/);
assert.doesNotMatch(box.text.join('\n'),/估计进球 0\.0%/);
choice.selected=null;choice.options=[];
ctx.renderAttackChoice({context:{now:1},players:[{role:'attack',attack_choice:choice}]});
assert.match(box.text.join('\n'),/没有通过门柱\/球网检查的射门点/);
console.log('Shot fallback: unknown probability stays unknown, replay validates, blocked fallback is explained.');
