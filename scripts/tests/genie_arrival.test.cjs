const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function setup(){
 const timers=new Map(),observers=[],revoked=[];let next=0,requests=0,queries=0,resolveAsset;
 const idle={setAttribute(k,v){this[k]=v;}};
 const classes=new Set();
 const node={nodeType:1,isConnected:true,childElementCount:0,dataset:{lightStill:'idle.png',lightIntro:'intro.gif',duration:'2560'},
  classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},
  querySelector:()=>idle,querySelectorAll:()=>[],matches:()=>true,getClientRects:()=>[{}],append(image){this.image=image;}};
 const document={documentElement:{dataset:{theme:'light'}},querySelectorAll(){queries++;return[node];},
  createElementNS(){return{listeners:{},setAttribute(k,v){this[k]=v;},addEventListener(k,fn){this.listeners[k]=fn;},remove(){this.removed=true;}};}};
 const window={addEventListener(){}};
 const context={document,window,matchMedia:()=>({matches:false,addEventListener(){}}),
  MutationObserver:class{constructor(fn){observers.push(fn);}observe(){}},queueMicrotask,
  AbortController,URL:{createObjectURL:()=>`blob:${++next}`,revokeObjectURL:url=>revoked.push(url)},
  setTimeout(fn,ms){const id=++next;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),
  fetch(){requests++;return new Promise(resolve=>resolveAsset=()=>resolve({ok:true,blob:async()=>({})}));}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../static/js/genie_arrival.js'),'utf8'),context);
 return{node,idle,window,timers,observers,revoked,resolve:()=>resolveAsset(),requests:()=>requests,queries:()=>queries};
}
const flush=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};

test('delayed entrance hides the final pose and resumes floating after playback',async()=>{
 const s=setup();assert.equal(s.node.classList.contains('is-arriving'),true);assert.equal(s.node.image,undefined);
 s.resolve();await flush();s.node.image.listeners.load();
 const completion=[...s.timers.values()].find(t=>t.ms===2560);assert.ok(completion);completion.fn();
 assert.equal(s.node.classList.contains('is-arriving'),false);assert.equal(s.node.image.removed,true);assert.equal(s.revoked.length,1);
});
test('a stalled entrance falls back and ignores a late response',async()=>{
 const s=setup();[...s.timers.values()].find(t=>t.ms===12000).fn();
 assert.equal(s.node.classList.contains('is-arriving'),false);s.resolve();await flush();assert.equal(s.node.image,undefined);
});
test('reopening reuses downloaded bytes and replaces the previous playback',async()=>{
 const s=setup();s.resolve();await flush();const first=s.node.image;
 s.window.fosGenieArrival.replay();await flush();
 assert.equal(s.requests(),1);assert.equal(first.removed,true);assert.notEqual(s.node.image,first);assert.equal(s.revoked.length,1);
});
test('answer text mutations do not rescan the document',async()=>{
 const s=setup();const initial=s.queries();
 for(let i=0;i<100;i++)s.observers[0]([{addedNodes:[{nodeType:3,isConnected:true}],removedNodes:[]}]);
 await flush();assert.equal(s.queries(),initial);
});
test('an always-dark chrome avatar keeps dark artwork in light theme',async()=>{
 const s=setup();
 s.node.dataset.darkStill='dark.png';s.node.dataset.darkIntro='dark-intro.gif';s.node.dataset.genieTheme='dark';
 s.window.fosGenieArrival.replay();await flush();
 assert.equal(s.idle.href,'dark.png');assert.equal(s.requests(),2);
});
