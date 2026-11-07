(()=>{
 'use strict';if(window.parent===window)return;
 const user=document.currentScript.dataset.user,input=document.getElementById('input'),send=document.getElementById('sendBtn'),stop=document.getElementById('stopBtn');
 const draftKey='rag-assistant-draft-'+user,post=data=>parent.postMessage(data,location.origin);
 const visible=node=>node.getClientRects().length&&!node.disabled&&!node.closest('[inert]');
 const focusables=()=>[...document.querySelectorAll('button,input,textarea,a[href],[tabindex="0"]')].filter(visible);
 const icon='<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 16V4m-5 5 5-5 5 5"/></svg>';
 send.innerHTML=icon;send.setAttribute('aria-label','发送');send.title='发送';
 stop.innerHTML='<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="4" y="4" width="12" height="12" rx="2"/></svg>';stop.setAttribute('aria-label','停止回答');stop.title='停止回答';
 stop.classList.remove('outline');stop.classList.add('primary');
 input.placeholder='向 EnGenie 提问…';input.setAttribute('aria-label','向 EnGenie 提问');
 try{if(!input.value)input.value=sessionStorage.getItem(draftKey)||'';}catch(_){}
 input.dispatchEvent(new Event('input'));
 input.addEventListener('input',()=>{try{sessionStorage.setItem(draftKey,input.value);}catch(_){}});
 const drawer=document.getElementById('convDrawer');
 function drawerState(){drawer.inert=matchMedia('(max-width:859px)').matches&&!drawer.classList.contains('open');}
 new MutationObserver(drawerState).observe(drawer,{attributes:true,attributeFilter:['class']});window.addEventListener('resize',drawerState);drawerState();
 let previous,modal=false;
 function notify(){const busy=!stop.hidden&&stop.style.display!=='none';if(previous!==busy){previous=busy;post({type:'rag-chat:state',busy});}}
 new MutationObserver(notify).observe(stop,{attributes:true,attributeFilter:['hidden','style']});notify();
 const localEvidence=window.fosEvidencePanel;
 window.fosEvidencePanel=(chunkId,options={})=>post({type:'rag-chat:evidence',chunkId:String(chunkId),chunks:(options.chunks||[]).map(row=>({chunk_id:String(row.chunk_id),page:String(row.page||'')}))});
 document.addEventListener('click',event=>{
  const prompt=event.target.closest('[data-rag-prompt]');if(prompt){input.value=prompt.dataset.ragPrompt;input.dispatchEvent(new Event('input'));input.focus();return;}
  const link=event.target.closest('a[href]');if(!link||event.defaultPrevented||link.hasAttribute('download')||link.getAttribute('href').startsWith('#')||link.href.startsWith('blob:'))return;
  link.target='_blank';link.rel='noopener noreferrer';
 });
 document.addEventListener('keydown',event=>{
  if(document.querySelector('dialog[open]'))return;
  const drawer=document.getElementById('convDrawer');
  if(event.key==='Escape'){
   if(drawer.classList.contains('open')&&matchMedia('(max-width:859px)').matches)return;
   post({type:'rag-chat:close'});event.preventDefault();return;
  }
  if(event.key==='Tab'&&modal){
   const nodes=focusables();
   if((event.shiftKey&&event.target===nodes[0])||(!event.shiftKey&&event.target===nodes.at(-1))){event.preventDefault();post({type:'rag-chat:focus-header',edge:event.shiftKey?'last':'first'});}
  }
 },true);
 window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==parent)return;const data=event.data||{};
  if(data.type==='rag-chat:theme'&&['light','dark'].includes(data.theme))document.documentElement.dataset.theme=data.theme;
  else if(data.type==='rag-chat:mode')modal=data.modal===true;
  else if(data.type==='rag-chat:evidence-local'&&typeof data.chunkId==='string')localEvidence(data.chunkId,{chunks:data.chunks||[]});
  else if(data.type==='rag-chat:new')window.FosAsk.newConversation();
  else if(data.type==='rag-chat:thread'&&/^[\w-]{1,100}$/.test(data.thread))window.FosAsk.openConversation(data.thread);
  else if(data.type==='rag-chat:focus'){
   if(data.edge==='last')focusables().at(-1)?.focus();else input.focus();
  }
 });
 post({type:'rag-chat:ready'});
})();
