const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('tools/tactics_dashboard/dashboard.js','utf8');
let clips=0;
const elements={'freeman-regions':{checked:true},'region-note':{},'freeman-id':{value:'3',options:[{value:'3'}]}};
const ctx={$:id=>elements[id],c:{width:800,height:500},g:new Proxy({}, {get:(o,k)=>k==='clip'?()=>clips++:()=>{}})};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function freemanBounds('),source.indexOf("$('freeman-regions').onchange")),ctx);
const frame={context:{field:{length:14,width:9,penalty_area_width:6},ball:{x:0,y:0},teammates:{3:{pose:{x:3,y:0}}},opponents:{}},players:[{id:3,role:'freeman',target:[3,1],freeman_region:{ball_radius:7,opponent_radius:3,goal_line_margin:1,shot_triangle:[[0,0],[7,-1.3],[7,1.3]],shot_clearance:.38}}]};
ctx.drawFreemanOverlay(frame,(x,y)=>[400+x*40,250-y*40],40);
assert.match(elements['region-note'].textContent,/0.38 m/);
assert.equal(clips,10); // field, rectangle, ball, triangle, three rounded edge strips and vertex circles
frame.players[0].freeman_region.shot_triangle=[];
clips=0;ctx.drawFreemanOverlay(frame,(x,y)=>[x,y],1);assert.equal(clips,3);
delete frame.players[0].freeman_region.shot_triangle;
ctx.drawFreemanOverlay(frame,(x,y)=>[x,y],1);assert.match(elements['region-note'].textContent,/未记录射门排除区/);
console.log('Freeman overlay: buffered triangle clipping, absent attacker and legacy replay passed.');
