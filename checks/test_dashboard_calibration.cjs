const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('tools/tactics_dashboard/dashboard.js','utf8');const ctx={};vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function calibrationLines(')),ctx);
assert.match(ctx.calibrationLines({run_id:'r1',trial:2,phase:'drive:0',requested_command:[0,.5,0],measured_velocity:[0,.3,0]}).join('\n'),/0.500/);
assert.match(ctx.calibrationLines({}).join('\n'),/未取得/);
assert.ok(source.includes('if(p.calibration)for(const text of calibrationLines(p.calibration))line(card,text)'));
console.log('Calibration display and legacy payload fallback passed');
