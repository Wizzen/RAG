// Local PDF renderer: avoids reliance on a browser's built-in PDF plug-in.
export async function previewPdf(container,blob,dialog) {
  dialog.dispatchEvent(new Event('vt:preview-reset'));
  container.textContent='正在读取 PDF…';
  let task,pdf,closed=false,renderTask;
  const cleanup=()=>{closed=true;renderTask?.cancel();task?.destroy();dialog.removeEventListener('close',cleanup);dialog.removeEventListener('vt:preview-reset',cleanup);};
  dialog.addEventListener('close',cleanup,{once:true});
  dialog.addEventListener('vt:preview-reset',cleanup,{once:true});
  try {
    const lib=await import('/static/vehicle_tracker/vendor/pdfjs/pdf.min.mjs');
    lib.GlobalWorkerOptions.workerSrc='/static/vehicle_tracker/vendor/pdfjs/pdf.worker.min.mjs';
    if(closed||!container.isConnected)return;
    task=lib.getDocument({data:new Uint8Array(await blob.arrayBuffer()),isEvalSupported:false,enableXfa:false,useSystemFonts:true});
    pdf=await task.promise;if(closed)return;
    container.innerHTML='<div class="vt-pdf-controls"><button data-pdf-prev>上一页</button><span data-pdf-page></span><button data-pdf-next>下一页</button><button data-pdf-smaller aria-label="缩小 PDF">−</button><button data-pdf-larger aria-label="放大 PDF">＋</button></div><div class="vt-pdf-pages"><canvas aria-label="PDF 页内容"></canvas></div>';
    const canvas=container.querySelector('canvas'),label=container.querySelector('[data-pdf-page]'),buttons=[...container.querySelectorAll('button')];let page=1,zoom=1;
    const render=async()=>{
      buttons.forEach(b=>b.disabled=true);
      try {
        const sheet=await pdf.getPage(page);if(closed)return;
        const base=sheet.getViewport({scale:1}),width=container.clientWidth-24,scale=width/base.width*zoom,dpr=Math.min(window.devicePixelRatio||1,2),vp=sheet.getViewport({scale});
        canvas.width=Math.floor(vp.width*dpr);canvas.height=Math.floor(vp.height*dpr);canvas.style.width=`${vp.width}px`;canvas.style.height=`${vp.height}px`;
        renderTask=sheet.render({canvasContext:canvas.getContext('2d'),viewport:vp,transform:[dpr,0,0,dpr,0,0]});await renderTask.promise;
        label.textContent=`${page} / ${pdf.numPages} 页 · ${Math.round(zoom*100)}%`;
      } finally {
        if(!closed){buttons.forEach(b=>b.disabled=false);container.querySelector('[data-pdf-prev]').disabled=page===1;container.querySelector('[data-pdf-next]').disabled=page===pdf.numPages;}
      }
    };
    container.querySelector('[data-pdf-prev]').onclick=()=>{page--;render().catch(fail);};
    container.querySelector('[data-pdf-next]').onclick=()=>{page++;render().catch(fail);};
    container.querySelector('[data-pdf-smaller]').onclick=()=>{zoom=Math.max(.5,zoom/1.25);render().catch(fail);};
    container.querySelector('[data-pdf-larger]').onclick=()=>{zoom=Math.min(3,zoom*1.25);render().catch(fail);};
    await render();
  } catch(error) {fail(error);}
  function fail(error) {
    if(closed)return;
    container.textContent=error?.name==='PasswordException'?'此 PDF 已加密，当前无法显示内容。原始附件仍保存在本页。':'PDF 无法预览。原始附件仍保存在本页，请核对文件是否完整。';
  }
}
