const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function fixture(fetchImpl) {
  let active=true,nextTimer=0;
  const nodes=new Map(),timers=new Map();
  const get=id=>{
    if (!nodes.has(id)) nodes.set(id,{isConnected:true,value:'',dataset:{},elements:{},handlers:{},
      addEventListener(name,fn){this.handlers[name]=fn;},replaceChildren(){},add(){}});
    return nodes.get(id);
  };
  get('graphPage').dataset={graphUrl:'/kb/graph/',actionUrl:'/kb/graph/action/'};
  get('graphSearch').elements={kb:{value:'test'},run:{value:'',options:[]},offset:{value:'0'}};
  Object.defineProperty(get('graphResults'),'innerHTML',{set(value){active=value.includes('data-graph-active');},get(){return '';}});
  class FormData extends Map {constructor(form){super();if (form) Object.entries(form.elements).forEach(([key,input])=>this.set(key,input.value));}}
  const scope={AbortController,FormData,URLSearchParams,Option:class{},fetch:fetchImpl,
    setTimeout:(fn,ms)=>{timers.set(++nextTimer,{fn,ms});return nextTimer;},clearTimeout:id=>timers.delete(id),
    window:{createGraphExplorer:()=>({reload:async()=>{},destroy(){}}),createGraphLive:()=>({updateJobs(){},destroy(){}})},
    document:{getElementById:get,addEventListener(){},querySelector:selector=>selector.includes('data-graph-active') ? (active?{}:null) : null}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../static/js/graph_page.js'),'utf8'),scope);
  return {get,scope,timers,fire(ms){const pair=[...timers].find(([,timer])=>timer.ms===ms);assert.ok(pair,'missing '+ms+'ms timer');timers.delete(pair[0]);return pair[1].fn();}};
}
const response=()=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>({html:'done',runs:[],live_jobs:[]})});

test('a failed progress read schedules another poll instead of freezing permanently',async()=>{
  let calls=0;
  const f=fixture(async()=>{if (++calls===1) throw new Error('offline');return response();});
  await f.fire(3000);
  assert.match(f.get('graphStatus').textContent,/offline/);
  await f.fire(3000);
  assert.equal(calls,2);
  assert.equal(f.timers.size,0);
});

test('a stalled progress read is aborted and polling resumes',async()=>{
  const f=fixture((_url,{signal})=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(Object.assign(new Error(),{name:'AbortError'})))));
  const pending=f.fire(3000);
  f.fire(15000);await pending;
  assert.match(f.get('graphStatus').textContent,/超时/);
  assert.ok([...f.timers.values()].some(timer=>timer.ms===3000));
});

test('leaving the graph page aborts pending reads and ignores late responses',async()=>{
  let resolve,signal;
  const f=fixture((_url,options)=>{signal=options.signal;return new Promise(r=>resolve=r);});
  const pending=f.fire(3000);
  f.scope.window.disposeGraphPage();
  assert.equal(signal.aborted,true);
  resolve(response());await pending;
  assert.equal(f.timers.size,0);
});
