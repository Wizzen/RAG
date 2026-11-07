const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const scope={};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../static/js/assistant_activity.js'),'utf8'),scope);
const State=scope.FosToolActivity.ActivityState;

test('parallel tool calls keep individual durations and do not finish on an answer token',()=>{
  let now=0;const state=new State(()=>now);
  state.update('tool_start',{run_id:'a',tool:'kb_search',label:'检索知识库…'});
  now=500;state.update('tool_start',{run_id:'b',tool:'tracker_lookup'});
  now=1500;state.update('tool_end',{run_id:'a',tool:'kb_search'});
  state.update('token',{text:'answer'});
  assert.equal(state.rows.get('a').state,'done');assert.equal(state.rows.get('a').seconds,1.5);
  assert.equal(state.rows.get('b').state,'running');
  now=2500;state.update('tool_end',{run_id:'b',tool:'tracker_lookup'});
  assert.equal(state.rows.get('b').seconds,2);
});

test('duplicate completion events keep the existing state instead of restarting completion',()=>{
  let now=0;const state=new State(()=>now);
  state.update('tool_start',{run_id:'a',tool:'kb_search'});
  now=250;state.update('tool_end',{run_id:'a'});
  const row=state.rows.get('a');
  now=1000;state.update('tool_end',{run_id:'a'});
  assert.equal(state.rows.size,1);assert.equal(state.rows.get('a'),row);assert.equal(row.seconds,.25);
});

test('a missing tool-end event becomes a warning rather than a false success',()=>{
  const state=new State(()=>0);state.update('tool_start',{run_id:'pending',tool:'kb_search'});
  state.finish('done');assert.equal(state.rows.get('pending').state,'warning');
  assert.match(state.rows.get('pending').note,/未返回完成结果/);
});

test('failed validation or interrupted tools cannot later be displayed as successful',()=>{
  const state=new State(()=>0);state.update('tool_start',{run_id:'a',tool:'kb_search'});
  state.update('verify',{ok:false});state.finish('done');
  assert.equal(state.status,'error');assert.equal(state.rows.get('a').state,'error');
  const stopped=new State(()=>0);stopped.update('tool_start',{run_id:'b'});stopped.finish('stopped');
  assert.equal(stopped.rows.get('b').state,'warning');
});

test('heartbeat elapsed time never moves backward and phase events do not invent tools',()=>{
  let now=100;const state=new State(()=>now);state.update('heartbeat',{stage:'检索资料',elapsed:30});
  state.update('heartbeat',{stage:'检索资料',elapsed:5});state.update('step',{stage:'answer_generation',status:'start'});
  assert.equal(state.elapsed(),30);assert.equal(state.rows.size,0);assert.equal(state.label,'生成回答');
  now=2100;assert.equal(state.elapsed(),32,'a restored background answer keeps counting between updates');
});

test('zero elapsed time from a completed tool is preserved',()=>{
  const state=new State(()=>10000);state.update('tool_end',{run_id:'fast',tool:'kb_search',seconds:0,warning:true});
  assert.equal(state.rows.get('fast').seconds,0);assert.equal(state.rows.get('fast').state,'warning');
});
