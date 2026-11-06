const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function fixture() {
  class Node {
    constructor(text='', value='') {this.textContent=text;this.value=value;this.children=[];this.dataset={};this.handlers={};this.classList={toggle(){},add(){},remove(){}};}
    addEventListener(name,callback) {this.handlers[name]=callback;}
    appendChild(node) {this.children.push(node);return node;}
    replaceChildren(...nodes) {this.children=nodes;}
    get options() {return this.children;}
    add(node) {this.children.push(node);}
    querySelectorAll() {return [];}
  }
  const nodes=new Map(), intervals=new Set(), sources=[];
  const get=id=>{if (!nodes.has(id)) nodes.set(id,new Node());return nodes.get(id);};
  get('graphLiveRun').add(new Node('请选择任务',''));
  get('graphLiveRun').add(new Node('任务23','23'));
  class EventSource {
    static CLOSED=2;
    constructor(url) {this.url=url;this.handlers={};this.readyState=1;sources.push(this);}
    addEventListener(name,callback) {this.handlers[name]=callback;}
    close() {this.closed=true;}
    emit(name,data) {this.handlers[name]?.({data:JSON.stringify(data)});}
  }
  const scope={window:{EventSource},EventSource,URLSearchParams,Date,Option:Node,
    setInterval:fn=>{intervals.add(fn);return fn;},clearInterval:id=>intervals.delete(id),
    document:{getElementById:get,createElement:()=>new Node(),createTextNode:text=>new Node(text)}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../static/js/graph_live.js'),'utf8'),scope);
  let completed=0,retried=[];
  const live=scope.window.createGraphLive({scope:()=> 'my-kb',run:()=>'',
    onComplete:()=>completed++,onRetry:async id=>retried.push(id)});
  return {get,sources,live,intervals,completed:()=>completed,retried,click:id=>get(id).handlers.click()};
}

test('failed model task exposes its error and a separate retry action outside the log details', async()=>{
  const f=fixture();
  f.sources[0].emit('snapshot',{id:23,status:'failed',document:'standard.pdf',focus:'等级',error:'模型连接中断'});
  assert.equal(f.get('graphLiveFailure').hidden,false);
  assert.equal(f.get('graphLiveError').textContent,'模型连接中断');
  await f.click('graphLiveRetry');
  assert.deepEqual(f.retried,['23']);
  assert.equal(f.get('graphLiveRetry').disabled,false);
});

test('reconnecting a terminal log never restarts extraction or repeats completion refreshes',()=>{
  const f=fixture(), terminal={id:23,status:'failed',error:'模型断开',created:0};
  f.sources[0].emit('done',terminal);
  f.live.updateJobs([{...terminal,label:'任务23'}]);
  f.click('graphLiveReconnect');f.sources[1].emit('done',terminal);
  assert.equal(f.completed(),1);
  assert.deepEqual(f.retried,[]);
  assert.equal(f.sources[1].closed,true);
});

test('polling can display backend failure while the event connection is broken',()=>{
  const f=fixture();f.sources[0].readyState=0;f.sources[0].onerror();
  f.live.updateJobs([{id:23,status:'failed',error:'断流，已停止',created:0,label:'任务23'}]);
  f.sources[0].onerror();
  assert.equal(f.get('graphLiveFailure').hidden,false);
  assert.equal(f.get('graphLiveError').textContent,'断流，已停止');
  assert.equal(f.get('graphLiveState').textContent,'抽取失败');
  assert.equal(f.live.active(),false);
});

test('refreshing jobs retains the selected history rather than switching to another job',()=>{
  const f=fixture();
  f.live.updateJobs([{id:24,status:'running',label:'任务24'},{id:23,status:'failed',label:'任务23'}]);
  assert.equal(f.get('graphLiveRun').value,'23');
  assert.equal(f.sources.length,1);
});

test('model wait display is driven by request timestamps and its timer is disposed',()=>{
  const f=fixture();
  f.sources[0].emit('progress',{id:1,kind:'model_request',at:new Date(Date.now()-5000).toISOString(),payload:{turn:2,message:'等待模型'}});
  assert.match(f.get('graphLiveWaiting').textContent,/第 2 轮模型请求 · 已等待 [56] 秒/);
  assert.equal(f.intervals.size,1);
  f.live.destroy();
  assert.equal(f.intervals.size,0);
  assert.equal(f.sources[0].closed,true);
});
