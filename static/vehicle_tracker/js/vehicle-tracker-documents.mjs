const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export const drawingCode=value=>String(value??'').trim().replace(/\s+Item\s*\d+\s*$/i,'');
const base='/tools/vehicle-tracker/api';

export function createDocumentExplorer({onUpdate, emptyMessage='请核对图号，或在本页附件中关联原件。'}){
  const cache=new Map(),dialog=document.getElementById('vt-source-dialog'),$=id=>document.getElementById('vt-source-'+id);
  let active=null,epoch=0,zoomed=false;
  async function load(code,force=false){
    code=drawingCode(code);const old=cache.get(code);if(old&&!force&&!old.error)return;
    cache.set(code,{loading:true});onUpdate(code);
    try{const response=await fetch(`${base}/drawings/${encodeURIComponent(code)}/documents`,{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.detail||'出处读取失败');cache.set(code,{data});}
    catch(error){cache.set(code,{error:error.message||'出处读取失败，请重试。'});}
    onUpdate(code);
  }
  function html(code){
    const row=cache.get(code);
    if(!row||row.loading)return '<div class="vt-source-empty"><span class="vt-source-spinner"></span><h3>正在核对文档出处</h3><p>只查找当前账号可见的原始资料。</p></div>';
    if(row.error)return `<div class="vt-source-empty"><h3>暂未取得文档出处</h3><p>${escape(row.error)}</p><button type="button" data-source-refresh="${escape(code)}">重新查找</button></div>`;
    const docs=row.data.documents||[];
    if(!docs.length)return `<div class="vt-source-empty"><h3>暂无已入库出处</h3><p>当前可见文档中未找到 ${escape(code)}。${escape(emptyMessage)}</p><button type="button" data-source-refresh="${escape(code)}">重新查找</button></div>`;
    return `<div class="vt-source-summary"><span>${docs.length} 份文档 · ${docs.reduce((sum,doc)=>sum+doc.hit_count,0)} 处定位</span><button type="button" data-source-refresh="${escape(code)}" aria-label="刷新文档出处">刷新</button></div>${docs.map(doc=>`<article class="vt-source-card"><span class="vt-source-filetype">${doc.has_pdf?'PDF':'文档'}</span><h4>${escape(doc.name)}</h4><small>${escape(doc.knowledge_base)}</small>${doc.occurrences.length?`<p>图号出现于 ${doc.occurrences.map(hit=>hit.page+1).join('、')} 页</p><div class="vt-source-pages">${doc.occurrences.map(hit=>`<button type="button" data-source-open="${escape(doc.id)}" data-source-code="${escape(code)}" data-source-page="${hit.page}">第 ${hit.page+1} 页 <span>定位原文</span></button>`).join('')}</div>`:`<p class="vt-subtle">${escape(doc.excerpt||'已匹配文档名称，暂无页内文字框。')}</p><button type="button" data-source-open="${escape(doc.id)}" data-source-code="${escape(code)}" data-source-page="0">查看原文</button>`}</article>`).join('')}${row.data.limited?'<p class="vt-subtle">本次检查的文档数量已达上限，以上不是完整资料范围。</p>':''}`;
  }
  function list(code){
    const docs=cache.get(code)?.data?.documents||[];
    $('list').innerHTML=docs.map(doc=>`<section><h3>${escape(doc.name)}</h3><p>${escape(doc.knowledge_base)}</p>${doc.occurrences.map(hit=>`<button type="button" class="${active?.id===doc.id&&active?.page===hit.page?'active':''}" data-source-open="${escape(doc.id)}" data-source-code="${escape(code)}" data-source-page="${hit.page}"><span>第 ${hit.page+1} 页</span><small>${hit.precision==='text'?'图号精确定位':'解析区域定位'}</small></button>`).join('')||`<button type="button" data-source-open="${escape(doc.id)}" data-source-code="${escape(code)}" data-source-page="0">原文预览</button>`}</section>`).join('');
  }
  async function show(id,code,page){
    const doc=cache.get(code)?.data?.documents?.find(item=>item.id===id);
    if(!doc){epoch++;active=null;$('image').hidden=true;$('image').removeAttribute('src');$('boxes').replaceChildren();$('list').replaceChildren();$('quote').textContent='';$('status').textContent='文档已移除或当前账号无权查看，请重新查找出处。';$('prev').disabled=true;$('next').disabled=true;return;}
    page=Math.max(0,Math.min(page,Math.max(0,doc.page_count-1)));
    active={id,code,page,version:doc.version,page_count:doc.page_count};const version=++epoch;
    if(!dialog.open)dialog.showModal();$('name').textContent=doc.name;$('drawing').textContent=code;$('status').textContent='正在打开原始页面…';$('boxes').replaceChildren();$('image').hidden=true;list(code);
    if(!doc.has_pdf){$('status').textContent='原始资料没有可用PDF页图，以下为文档内匹配文字。';$('quote').textContent=doc.excerpt||'暂无页内定位数据。';$('pages').textContent='/ —';$('page').value='1';$('prev').disabled=true;$('next').disabled=true;return;}
    $('prev').disabled=true;$('next').disabled=true;
    try{
      const query=new URLSearchParams({drawing:code,page:String(page),version:doc.version});
      const response=await fetch(`${base}/documents/${id}/location?${query}`,{cache:'no-store'});const data=await response.json();
      if(version!==epoch||!dialog.open)return;if(!response.ok)throw Error(data.detail||'原文定位不可用，请刷新出处后重试');
      active.page_count=data.page_count;active.page=data.page;active.version=data.version;
      $('page').value=String(data.page+1);$('page').max=String(data.page_count);$('pages').textContent=`/ ${data.page_count}`;
      $('quote').textContent=data.quote||'';
      $('image').onload=()=>{if(version!==epoch||!dialog.open)return;$('image').hidden=false;
        for(const bbox of data.boxes||[]){if(!Array.isArray(bbox)||bbox.length!==4||!bbox.every(Number.isFinite))continue;const box=document.createElement('span');box.className='vt-evidence-box';box.style.cssText=`left:${bbox[0]/10}%;top:${bbox[1]/10}%;width:${(bbox[2]-bbox[0])/10}%;height:${(bbox[3]-bbox[1])/10}%;`;box.title=data.precision==='text'?'图号原文精确位置':'解析区域';$('boxes').append(box);}
        $('status').textContent=data.precision==='text'?'红框标出图号在原PDF中的精确文字位置':data.precision==='region'?'红框标出图号所在解析区域':'上下文页 · 本页未匹配到此图号';
        $('prev').disabled=data.page===0;$('next').disabled=data.page===data.page_count-1;
        const first=data.boxes?.[0],scroll=$('scroll')||dialog.querySelector('.vt-source-scroll');
        if(scroll){scroll.scrollLeft=0;scroll.scrollTop=first?Math.max(0,first[1]/1000*$('image').clientHeight-scroll.clientHeight*.3):0;}
      };
      $('image').onerror=()=>{if(version===epoch)$('status').textContent='原页暂时无法渲染，请刷新文档出处后重试。';};
      $('image').src=`${base}/documents/${id}/page.png?${new URLSearchParams({page:String(data.page),version:data.version})}`;
      list(code);
    }catch(error){if(version===epoch)$('status').textContent=error.message||'原文读取失败';}
  }
  document.getElementById('vt-drawing').addEventListener('click',event=>{
    const refresh=event.target.closest('[data-source-refresh]');if(refresh){load(refresh.dataset.sourceRefresh,true);return;}
    const button=event.target.closest('[data-source-open]');if(button)show(button.dataset.sourceOpen,button.dataset.sourceCode,Number(button.dataset.sourcePage));
  });
  dialog.addEventListener('click',event=>{const button=event.target.closest('[data-source-open]');if(button)show(button.dataset.sourceOpen,button.dataset.sourceCode,Number(button.dataset.sourcePage));});
  $('close').addEventListener('click',()=>dialog.close());dialog.addEventListener('close',()=>{epoch++;$('image').removeAttribute('src');$('boxes').replaceChildren();});
  $('prev').addEventListener('click',()=>{if(active?.page>0)show(active.id,active.code,active.page-1);});
  $('next').addEventListener('click',()=>{if(active&&active.page+1<active.page_count)show(active.id,active.code,active.page+1);});
  $('go').addEventListener('submit',event=>{event.preventDefault();const page=Number($('page').value)-1;if(active&&Number.isInteger(page)&&page>=0&&page<active.page_count)show(active.id,active.code,page);else $('status').textContent='请输入原文范围内的页码。';});
  $('zoom').addEventListener('click',()=>{zoomed=!zoomed;$('paper').classList.toggle('zoomed',zoomed);$('zoom').textContent=zoomed?'适宽':'放大';const box=$('boxes').firstElementChild,scroll=$('scroll')||dialog.querySelector('.vt-source-scroll');if(box&&scroll)scroll.scrollTop=Math.max(0,box.offsetTop-scroll.clientHeight*.3);});
  $('refresh').addEventListener('click',async()=>{if(!active)return;const old={...active};await load(old.code,true);show(old.id,old.code,old.page);});
  return {load,html,count:code=>cache.get(code)?.error?'—':cache.get(code)?.data?.documents?.length??'…',show};
}
