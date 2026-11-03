import {PART_MAP,KINDS} from './vehicle-tracker-core.mjs';
const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const titles={'maximo-workorder':'工单资料','qaqc-workorder':'QAQC / MRB 工单','job-plan':'作业计划','workorder-pdf':'工单 PDF'};

export function createInlineRecords({getProject,locate,openDrawing}){
  const dialog=document.getElementById('vt-inspector-dialog'),$=id=>document.getElementById('vt-inspector-'+id);
  let current=null,epoch=0;
  function local(code,kind){
    if(kind==='job-plan'){
      const entries=Object.values(KINDS).filter(item=>item.req?.jobPlan===code);
      return `<section class="vt-inline-section"><h3>预置检验清单</h3><p class="vt-subtle">来自部件清单，不替代实时作业计划原文。</p>${entries.map(item=>`<article><strong>${escape(item.zh)}</strong><p>${escape(item.req.method)}</p><small>${escape(item.req.frequency)}</small></article>`).join('')||'<p>当前项目没有引用此作业计划。</p>'}</section>`;
    }
    const records=getProject().records.filter(row=>row.wonum===code||row.reportWo===code);
    return `<section class="vt-inline-section"><h3>当前车辆追踪记录 <span>${records.length}</span></h3><p class="vt-subtle">来自当前浏览器项目，保留原始文件与页码。</p>${records.map(row=>`<article><div class="vt-inline-record-heading"><strong>${escape(row.serial||'未登记序列号')}</strong><span>${escape([row.inspector,row.date].filter(Boolean).join(' · '))}</span></div><p>${row.partId?escape(PART_MAP.get(row.partId)?.position||row.partId):'待人工绑定'} · ${escape(row.drawing)}</p><small>${escape(row.source)}${row.sourceRow?` · ${row.sourceUnit==='页'?'第 '+row.sourceRow+' 页':'行 '+row.sourceRow}`:''}</small><div class="vt-inline-actions">${row.partId?`<button type="button" data-inline-locate="${escape(row.partId)}">定位部件</button>`:''}${row.drawing?`<button type="button" data-inline-drawing="${escape(row.drawing)}">查看图号出处</button>`:''}</div></article>`).join('')||'<p>当前车辆项目尚未关联这张工单。</p>'}</section>`;
  }
  function detail(payload){
    const data=payload.data||{},header=data.workorder||data.jobplan||{},labels={wonum:'工单号',jpnum:'作业计划',description:'说明',description_en:'英文说明',description_zh:'中文说明',status:'状态',worktype:'类型',location:'位置',assetnum:'资产',report_local:'报告时间',actfinish_local:'实际完成',failurecode:'故障代码',disposition_label:'处置',long_text:'工程备注',long_description_zh:'详细说明'};
    const fields=Object.entries(labels).filter(([key])=>header[key]!==undefined&&header[key]!==null&&header[key]!=='');
    const tasks=Array.isArray(data.tasks)?data.tasks:Array.isArray(header.tasks)?header.tasks:[];
    return `<section class="vt-inline-section"><div class="vt-inline-source">${escape(payload.source||'只读资料')}</div>${payload.notice?`<p class="vt-inline-notice">${escape(payload.notice)}</p>`:''}<dl class="vt-inline-fields">${fields.map(([key,label])=>`<dt>${label}</dt><dd>${escape(header[key])}</dd>`).join('')}</dl>${tasks.length?`<h3>任务与检查步骤</h3>${tasks.slice(0,100).map(task=>`<article><strong>${escape(task.taskid??task.jobtaskid??'步骤')}</strong><p>${escape(task.description_zh||task.description||task.description_en||'')}</p><p>${escape(task.long_description_zh||task.long_description||task.long_text||'')}</p></article>`).join('')}`:''}</section>`;
  }
  async function open(kind,code){
    if(!titles[kind])return;
    current={kind,code};const marker=++epoch;dialog.dispatchEvent(new Event('vt:preview-reset'));
    $('title').textContent=`${titles[kind]} · ${code}`;$('local').innerHTML=local(code,kind);$('remote').replaceChildren();$('status').textContent='正在读取资料…';
    if(!dialog.open)dialog.showModal();
    try{
      const response=await fetch(`/tools/vehicle-tracker/api/records/${encodeURIComponent(kind)}/${encodeURIComponent(code)}`,{cache:'no-store'});
      if(marker!==epoch||!dialog.open)return;
      if(kind==='workorder-pdf'&&response.ok){
        const blob=await response.blob();if(marker!==epoch||!dialog.open)return;
        const {previewPdf}=await import('./vehicle-tracker-pdf.mjs');if(marker!==epoch||!dialog.open)return;
        await previewPdf($('remote'),blob,dialog);$('status').textContent='工单 PDF · 在当前页面预览';return;
      }
      const data=await response.json().catch(()=>({}));if(marker!==epoch||!dialog.open)return;
      if(!response.ok)throw Error(data.detail||'资料暂不可用。');
      $('remote').innerHTML=detail(data);$('status').textContent='资料已载入 · 保留在当前车辆工作台';
    }catch(error){if(marker===epoch)$('status').textContent=error.message||'资料暂不可用，请重试。';}
  }
  $('close').addEventListener('click',()=>dialog.close());$('retry').addEventListener('click',()=>{if(current)open(current.kind,current.code);});
  dialog.addEventListener('close',()=>{epoch++;dialog.dispatchEvent(new Event('vt:preview-reset'));});
  dialog.addEventListener('click',event=>{const part=event.target.closest('[data-inline-locate]'),drawing=event.target.closest('[data-inline-drawing]');
    if(part&&PART_MAP.has(part.dataset.inlineLocate)){dialog.close();locate(part.dataset.inlineLocate);}
    if(drawing){dialog.close();openDrawing(drawing.dataset.inlineDrawing);}
  });
  return {open};
}
