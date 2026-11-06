/* 单次选择资料即可构建、查看和审核；日志与筛选默认折叠。 */
(function () {
  'use strict';
  function initGraph() {
    window.disposeGraphPage?.();
    const page=document.getElementById('graphPage'), form=document.getElementById('graphSearch');
    if (!page || !form) return;
    const status=document.getElementById('graphStatus'), picker=document.getElementById('graphDocument');
    const url=page.dataset.graphUrl, actionUrl=page.dataset.actionUrl;
    let sequence=0, pollTimer, switching=false, disposed=false;
    const requests=new Set();
    const events=new AbortController();
    let explorer=null;
    const hierarchy=window.createKnowledgeHierarchy?.({form,page});
    const legacy=document.getElementById('graphLegacyView');
    if(!legacy || legacy.open)explorer=window.createGraphExplorer?.({form,url});
    legacy?.addEventListener('toggle',()=>{if(legacy.open && !explorer){explorer=window.createGraphExplorer?.({form,url});explorer?.reload();}});
    const live=window.createGraphLive?.({scope:()=>form.elements.kb.value,run:()=>form.elements.run.value,onComplete:()=>refresh(true),onRetry:id=>{
      const data=new FormData();data.set('action','retry_extract');data.set('extraction',id);return mutate(data);
    }});
    window.disposeGraphPage=()=>{disposed=true;++sequence;clearTimeout(pollTimer);events.abort();requests.forEach(request=>request.abort());explorer?.destroy();hierarchy?.destroy();live?.destroy();};
    function poll() {
      clearTimeout(pollTimer);
      if (!disposed && form.isConnected && (document.querySelector('#graphResults [data-graph-active]') || live?.active?.())) pollTimer=setTimeout(()=>refresh(true),3000);
    }
    async function refresh(quiet=false) {
      if (disposed || !form.isConnected || switching) return;
      const ticket=++sequence;
      const controller=new AbortController();requests.add(controller);
      const timeout=setTimeout(()=>controller.abort(),15000);
      if (!quiet) status.textContent='正在加载…';
      try {
        const response=await fetch(url+'?'+new URLSearchParams(new FormData(form)),{signal:controller.signal,headers:{'X-Requested-With':'XMLHttpRequest'}});
        if (!response.ok) throw new Error('加载失败，请重试。');
        if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('会话已失效，请重新登录。');
        const result=await response.json();
        if (!form.isConnected || ticket!==sequence) return;
        document.getElementById('graphResults').innerHTML=result.html;
        if (result.runs) {
          const select=form.elements.run, previous=select.value;
          select.replaceChildren(new Option('全部批次',''));
          result.runs.forEach(run=>select.add(new Option(run.label,String(run.id))));
          select.value=previous;
        }
        if (live && result.live_jobs) live.updateJobs(result.live_jobs);
        await Promise.all([explorer?.reload(),hierarchy?.reload()]);
        if (form.isConnected && ticket===sequence) status.textContent='';
      } catch(error) {if (form.isConnected && ticket===sequence) status.textContent=error.name==='AbortError' ? '读取进度超时，稍后自动重试；后台抽取不受影响。' : error.message;}
      finally {clearTimeout(timeout);requests.delete(controller);if (form.isConnected && ticket===sequence && !switching) poll();}
    }
    async function mutate(data,button) {
      if (data.get('action')==='attach' && !data.get('relation')) {status.textContent='请先选择一条关系。';return;}
      data.set('kb',form.elements.kb.value);
      if (button) button.disabled=true;
      const controller=new AbortController();requests.add(controller);
      const timeout=setTimeout(()=>controller.abort(),30000);
      try {
        const response=await fetch(actionUrl,{method:'POST',body:data,signal:controller.signal,headers:{'X-Requested-With':'XMLHttpRequest','X-CSRFToken':document.querySelector('[name=csrfmiddlewaretoken]').value}});
        if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('保存失败，请检查登录状态或权限。');
        const result=await response.json();
        if (!response.ok) throw new Error(result.error || '保存失败。');
        if (disposed || !form.isConnected) return;
        window.fosToast?.(result.message,'ok');
        if (data.get('action')==='bind') document.querySelector('#bindForm option:checked')?.remove();
        if (result.job_id) {
          form.elements.offset.value='0';
          const select=form.elements.run;
          if (![...select.options].some(option=>option.value===String(result.job_id))) select.add(new Option('#'+result.job_id,String(result.job_id)));
          select.value=String(result.job_id);live?.select(result.job_id);
        }
        await refresh(true);
      } catch(error) {
        if (!disposed && form.isConnected) {
          status.textContent=error.name==='AbortError' ? '提交结果暂未确认，正在重新读取任务状态。' : error.message;
          if (error.name==='AbortError' || error.name==='TypeError') await refresh(true);
        }
      } finally {clearTimeout(timeout);requests.delete(controller);if (button) button.disabled=false;}
    }
    picker.addEventListener('change',async()=>{
      if (!picker.value) {picker.value=page.dataset.document;return;}
      switching=true;clearTimeout(pollTimer);
      const ticket=++sequence, target=url+'?document='+encodeURIComponent(picker.value);
      const focus=document.querySelector('#extractForm [name=focus]')?.value || '';
      status.textContent='正在加载资料…';
      try {
        const response=await fetch(target);
        if (!response.ok) throw new Error('资料加载失败。');
        const parsed=new DOMParser().parseFromString(await response.text(),'text/html');
        if (!form.isConnected || ticket!==sequence) return;
        const content=parsed.querySelector('.fos-container');
        if (!content || !parsed.querySelector('#graphPage')) throw new Error('会话已失效，请重新登录。');
        window.disposeGraphPage();
        document.querySelector('.fos-container').innerHTML=content.innerHTML;
        const textarea=document.querySelector('#extractForm [name=focus]');
        if (textarea) textarea.value=focus;
        history.replaceState(null,'',target);document.dispatchEvent(new Event('graph:init'));
      } catch(error) {if (form.isConnected && ticket===sequence) {switching=false;picker.value=page.dataset.document;status.textContent=error.message;poll();}}
    });
    form.addEventListener('submit',event=>{event.preventDefault();form.elements.offset.value='0';refresh();});
    page.addEventListener('click',event=>{
      const pageButton=event.target.closest('[data-graph-offset]');
      if (pageButton) {form.elements.offset.value=pageButton.dataset.graphOffset;refresh(true);return;}
      const evidence=event.target.closest('[data-graph-chunk]');
      if (evidence) {window.fosEvidencePanel?.(evidence.dataset.graphChunk);return;}
      const button=event.target.closest('button[data-relation]');
      if (!button) return;
      if (button.dataset.action==='attach') {
        document.getElementById('attachRelation').value=button.dataset.relation;
        document.getElementById('attachSave').disabled=false;
        for (const details of document.querySelectorAll('.graph-advanced,#graphAttach')) details.open=true;
        document.getElementById('attachLabel').textContent='关系 #'+button.dataset.relation;
        document.querySelector('#attachForm textarea').focus();return;
      }
      const data=new FormData();data.set('action','review');data.set('relation',button.dataset.relation);data.set('status',button.dataset.status);mutate(data,button);
    },{signal:events.signal});
    ['attachForm','bindForm','extractForm'].forEach(id=>document.getElementById(id)?.addEventListener('submit',event=>{event.preventDefault();mutate(new FormData(event.target),event.submitter);}));
    document.getElementById('backfill')?.addEventListener('click',event=>{const data=new FormData();data.set('action','backfill');mutate(data,event.target);});
    poll();explorer?.reload();hierarchy?.reload();
  }
  document.addEventListener('graph:init',initGraph);initGraph();
})();
