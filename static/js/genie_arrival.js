(()=>{
 'use strict';
 const states=new WeakMap(),nodes=new Set(),reduced=matchMedia('(prefers-reduced-motion: reduce)');
 let serial=0;
 const dark=()=>document.documentElement.dataset.theme==='dark';
 function play(node,animate=true){
  const previous=states.get(node);if(previous){clearTimeout(previous.timer);previous.image?.remove();}
  const theme=dark()?'dark':'light',idle=node.querySelector('[data-genie-idle]');
  idle.setAttribute('href',node.dataset[theme+'Still']);node.classList.remove('is-arriving');
  const state={timer:null,image:null};states.set(node,state);
  if(!animate||reduced.matches||!node.getClientRects().length)return;
  const image=document.createElementNS('http://www.w3.org/2000/svg','image');
  image.setAttribute('width','40');image.setAttribute('height','40');
  image.setAttribute('aria-hidden','true');state.image=image;
  const finish=()=>{if(states.get(node)!==state)return;clearTimeout(state.timer);image.remove();node.classList.remove('is-arriving');};
  image.addEventListener('load',()=>{if(states.get(node)===state)state.timer=setTimeout(finish,Number(node.dataset.duration)||2560);},{once:true});
  image.addEventListener('error',finish,{once:true});
  image.setAttribute('href',node.dataset[theme+'Intro']+'?play='+(++serial));
  node.classList.add('is-arriving');node.append(image);
 }
 function scan(){
  for(const node of nodes)if(!node.isConnected){clearTimeout(states.get(node)?.timer);nodes.delete(node);}
  document.querySelectorAll('[data-genie-arrival]').forEach(node=>{if(!nodes.has(node)){nodes.add(node);play(node);}});
 }
 function replay(scope=document){scope.querySelectorAll('[data-genie-arrival]').forEach(node=>play(node));}
 new MutationObserver(scan).observe(document.documentElement,{childList:true,subtree:true});
 new MutationObserver(()=>{for(const node of nodes)play(node,node.classList.contains('is-arriving'));}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
 reduced.addEventListener('change',()=>{for(const node of nodes)play(node,false);});
 window.fosGenieArrival={replay};scan();
})();
