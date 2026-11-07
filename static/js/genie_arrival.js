(()=>{
 'use strict';
 const selector='[data-genie-arrival]',states=new WeakMap(),nodes=new Set(),assets=new Map();
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 function asset(url){
  if(!assets.has(url)){
   const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),10000);
   const promise=fetch(url,{signal:controller.signal}).then(response=>{
    if(!response.ok)throw new Error('Mascot unavailable');return response.blob();
   }).finally(()=>clearTimeout(timeout));
   assets.set(url,promise);promise.catch(()=>assets.delete(url));
  }
  return assets.get(url);
 }
 function release(state){
  if(!state)return;clearTimeout(state.timer);state.image?.remove();
  if(state.url)URL.revokeObjectURL(state.url);state.url=null;
 }
 function play(node,animate=true){
  release(states.get(node));
  // 常黑标题栏（运维抽屉/设备追踪面板）上的头像无论页面主题都用暗色素材
  const theme=node.dataset.genieTheme==='dark'||document.documentElement.dataset.theme==='dark'?'dark':'light';
  const idle=node.querySelector('[data-genie-idle]');if(!idle)return;
  idle.setAttribute('href',node.dataset[theme+'Still']);node.classList.remove('is-arriving');
  const state={timer:null,image:null,url:null};states.set(node,state);
  if(!animate||reduced.matches||!node.getClientRects().length)return;
  // Hide the final pose before loading: the first visible pose must be the dust.
  node.classList.add('is-arriving');
  const finish=()=>{if(states.get(node)!==state)return;release(state);node.classList.remove('is-arriving');};
  state.timer=setTimeout(finish,12000);
  asset(node.dataset[theme+'Intro']).then(blob=>{
   if(states.get(node)!==state||!node.isConnected||!node.classList.contains('is-arriving'))return;
   state.url=URL.createObjectURL(blob);
   const image=document.createElementNS('http://www.w3.org/2000/svg','image');state.image=image;
   image.setAttribute('width','40');image.setAttribute('height','40');image.setAttribute('aria-hidden','true');
   image.addEventListener('load',()=>{
    if(states.get(node)!==state)return;clearTimeout(state.timer);
    state.timer=setTimeout(finish,Number(node.dataset.duration)||2560);
   },{once:true});
   image.addEventListener('error',finish,{once:true});
   image.setAttribute('href',state.url);node.append(image);
  }).catch(finish);
 }
 function visit(root,callback){
  if(root.nodeType!==1&&root.nodeType!==11)return;
  if(root.matches?.(selector))callback(root);
  if(root.childElementCount)root.querySelectorAll(selector).forEach(callback);
 }
 function register(node){if(!nodes.has(node)){nodes.add(node);play(node);}}
 function replay(scope=document){
  if(scope.matches?.(selector)){nodes.add(scope);play(scope);}
  scope.querySelectorAll(selector).forEach(node=>{nodes.add(node);play(node);});
 }
 const added=new Set(),removed=new Set();let scheduled=false;
 new MutationObserver(records=>{
  for(const record of records){record.addedNodes.forEach(node=>added.add(node));record.removedNodes.forEach(node=>removed.add(node));}
  if(scheduled)return;scheduled=true;
  queueMicrotask(()=>{
   scheduled=false;
   for(const root of removed)visit(root,node=>{if(!node.isConnected){release(states.get(node));nodes.delete(node);states.delete(node);}});
   for(const root of added)if(root.isConnected)visit(root,register);
   removed.clear();added.clear();
  });
 }).observe(document.documentElement,{childList:true,subtree:true});
 new MutationObserver(()=>{for(const node of nodes)play(node,node.classList.contains('is-arriving'));}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
 reduced.addEventListener('change',()=>{for(const node of nodes)play(node,false);});
 window.addEventListener('pagehide',()=>{for(const node of nodes){release(states.get(node));node.classList.remove('is-arriving');}});
 window.fosGenieArrival={replay};document.querySelectorAll(selector).forEach(register);
})();
