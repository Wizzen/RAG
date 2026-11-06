import {PART_MAP} from './vehicle-tracker-core.mjs';
import {buildSnapshot,fitRequest} from './vehicle-tracker-ai-context.mjs';

const byId=id=>document.getElementById('vt-ai-'+id);
const prompts={
  explain:'解释所选部件的作用、位置、图号与预置检验要求，并指出需要原始资料核对的部分。',
  audit:'核对当前范围的追踪记录：缺少记录的位置、未绑定记录、当前序列号冲突和日期问题。区分历史记录，不把缺少追踪记录当作未检验。',
  qaqc:'结合当前部件的QAQC本地同步记录，解释检验与MRB统计，并与本车追踪记录核对。明确资料范围和不足。',
  help:'说明如何在这个工作台定位部件、导入并人工绑定记录，以及完整备份恢复。',
};

export function mountAssistant({getProject,getSelection,locate}) {
  const panel=byId('panel'),launcher=byId('open');
  if(!panel||!launcher)return {refresh(){}};
  let projectId=getProject().id,history=[],busy=false,controller=null,epoch=0;
  function status(message,error=false){byId('status').textContent=message;byId('status').classList.toggle('error',error);}
  function setBusy(value){busy=value;byId('send').disabled=value;byId('question').disabled=value;panel.setAttribute('aria-busy',String(value));byId('suggestions').querySelectorAll('button').forEach(button=>button.disabled=value);}
  function refresh(){
    const project=getProject();if(!project)return;
    if(project.id!==projectId){projectId=project.id;epoch++;controller?.abort();controller=null;history=[];byId('messages').replaceChildren();setBusy(false);status('已切换项目，车辆对话已清空。');}
    const id=getSelection(),part=PART_MAP.get(id);
    byId('context').textContent=part?`${project.parts[id]?.name||part.name} · ${id}`:'请先选择部件';
  }
  function open(){panel.hidden=false;launcher.setAttribute('aria-expanded','true');refresh();byId('question').focus();}
  function close(){panel.hidden=true;launcher.setAttribute('aria-expanded','false');launcher.focus();}
  function message(role,content,caption=''){
    const card=document.createElement('article');card.className='vt-ai-message '+role;
    const heading=document.createElement('strong');heading.textContent=role==='user'?'你':'车辆 AI';card.append(heading);
    if(caption){const meta=document.createElement('small');meta.textContent=caption;card.append(meta);}
    const body=document.createElement('div');body.className='vt-ai-text';body.textContent=content;card.append(body);
    byId('messages').append(card);card.scrollIntoView({block:'nearest'});return card;
  }
  async function ask(mode='explain',preset=''){
    if(busy)return;
    const question=(preset||byId('question').value).trim();if(!question){byId('question').focus();return;}
    const snapshot=buildSnapshot(getProject(),getSelection(),{scope:byId('scope').value,mode});
    const requestProject=getProject().id,requestEpoch=epoch,requestController=new AbortController();controller=requestController;
    const previous=history.slice(-6).map(row=>({role:row.role,content:row.content.slice(0,3000)}));
    const payload=fitRequest({question,mode,snapshot,history:previous,include_qaqc:mode!=='help'&&(mode==='qaqc'||byId('qaqc').checked)});
    message('user',question,`范围：${snapshot.scope==='train'?'全车':snapshot.selected}`);
    byId('question').value='';setBusy(true);status('正在结合当前部件和资料分析…');
    try{
      const response=await fetch('/tools/vehicle-tracker/api/ai/analyze',{
        method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]').content},
        body:JSON.stringify(payload),
        signal:requestController.signal,
      });
      const data=await response.json().catch(()=>({}));
      if(requestEpoch!==epoch||getProject().id!==requestProject)return;
      if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'本次分析未完成，请重试。');
      if(typeof data.answer!=='string')throw Error('未收到有效回答，请重试。');
      const total=data.records_total,caption=Number.isInteger(total)?`依据本次资料 · 追踪记录样本 ${data.records_included}/${total}`:'依据本次资料';
      const card=message('assistant',data.answer,caption);
      if(Array.isArray(data.parts)&&data.parts.length){
        const links=document.createElement('div');links.className='vt-ai-parts';
        for(const part of data.parts.slice(0,8))if(PART_MAP.has(part.id)){
          const button=document.createElement('button');button.type='button';button.dataset.aiPart=part.id;
          button.textContent=`定位 ${part.id}`;button.title=PART_MAP.get(part.id).position;links.append(button);
        }
        card.append(links);
      }
      const copy=document.createElement('button');copy.type='button';copy.className='vt-ai-copy';copy.textContent='复制回答';
      copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(data.answer);copy.textContent='已复制';}catch{status('浏览器未允许复制，可选择回答文字后复制。',true);}});card.append(copy);
      history=[...history,{role:'user',content:question},{role:'assistant',content:data.answer}].slice(-6);
      status(data.qaqc_status==='unavailable'?(data.qaqc_note||'本次未取得QAQC记录，回答已注明资料不足。'):'分析完成。可点击部件按钮在三维中定位。',data.qaqc_status==='unavailable');
      card.scrollIntoView({block:'nearest'});
    }catch(error){if(requestEpoch===epoch&&error.name!=='AbortError')status(error.message||'连接失败，请重试。',true);}
    finally{if(requestEpoch===epoch){setBusy(false);controller=null;}}
  }
  launcher.addEventListener('click',()=>panel.hidden?open():close());
  byId('close').addEventListener('click',close);
  byId('clear').addEventListener('click',()=>{epoch++;controller?.abort();controller=null;history=[];byId('messages').replaceChildren();setBusy(false);status('已清空当前车辆对话。');});
  byId('form').addEventListener('submit',event=>{event.preventDefault();ask().catch(error=>status(error.message,true));});
  byId('question').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();byId('form').requestSubmit();}});
  byId('suggestions').addEventListener('click',event=>{const button=event.target.closest('[data-ai-prompt]');if(button)ask(button.dataset.aiPrompt,prompts[button.dataset.aiPrompt]).catch(error=>status(error.message,true));});
  byId('messages').addEventListener('click',event=>{const button=event.target.closest('[data-ai-part]');if(button&&PART_MAP.has(button.dataset.aiPart))locate(button.dataset.aiPart);});
  panel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();}});
  refresh();return {refresh};
}
