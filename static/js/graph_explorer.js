/* G6 图谱探索：只读数据由服务器按权限过滤，审核复用原有 POST 接口。 */
(function () {
  'use strict';
  const kindNames = {equipment:'设备',component:'部件',part:'部件',inspection:'检查项目',
    action:'操作',standard:'标准',clause:'条款',parameter:'参数',defect:'缺陷',material:'材料',
    drawing:'图纸',person:'人员',requirement:'要求',concept:'概念'};
  const statusNames = {candidate:'待确认',verified:'已确认',rejected:'已拒绝'};
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  window.createGraphExplorer = function ({form, url}) {
    const canvas = document.getElementById('graphCanvas');
    const inspector = document.getElementById('graphInspector');
    const notice = document.getElementById('graphCanvasStatus');
    const counts = document.getElementById('graphCounts');
    let graph, request, detailRequest, sequence = 0, detailSequence = 0, disposed = false;
    let data = {nodes:[],edges:[]}, selectedRelation = null, selectedNode = null, fingerprint = '', totalAvailable = null;
    const token = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    function parameters() { return new URLSearchParams(new FormData(form)); }
    async function read(target, signal) {
      const response = await fetch(target, {signal, headers:{'X-Requested-With':'XMLHttpRequest'}});
      if (!response.ok) throw new Error(response.status === 404 ? '关系已移除或当前范围无权访问。' : '加载失败，请重试。');
      if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('会话已失效，请重新登录。');
      return response.json();
    }
    function stats() { counts.textContent = data.nodes.length + ' 个实体 · ' + data.edges.length + ' 条关系' +
      (totalAvailable > data.edges.length ? '（当前筛选共有 ' + totalAvailable + ' 条）' : ''); }
    async function showRelation(id) {
      selectedRelation = id; selectedNode = null;
      const ticket = ++detailSequence;
      if (detailRequest) detailRequest.abort();
      detailRequest = new AbortController();
      inspector.replaceChildren(element('p', '正在加载关系与原文…', 'graph-inspector-hint'));
      try {
        const query = new URLSearchParams({kb:parameters().get('kb') || ''});
        const result = await read('/kb/graph/relation/' + encodeURIComponent(id) + '/?' + query, detailRequest.signal);
        if (!disposed && ticket === detailSequence) inspector.innerHTML = result.html;
      } catch (error) {
        if (!disposed && ticket === detailSequence && error.name !== 'AbortError') inspector.replaceChildren(element('p', error.message));
      }
    }
    function showNode(id) {
      ++detailSequence;
      if (detailRequest) detailRequest.abort();
      selectedNode = id; selectedRelation = null;
      const node = data.nodes.find(n => n.id === id);
      if (!node) return;
      inspector.replaceChildren(element('h3', node.data.label, 'graph-inspector-heading'),
        element('p', kindNames[node.data.kind] || node.data.kind, 'graph-inspector-meta'));
      const expand = element('button', '展开相邻关系', 'btn outline graph-expand');
      expand.type = 'button'; expand.addEventListener('click', () => expandNode(id));
      inspector.appendChild(expand);
      const edges = data.edges.filter(e => e.source === id || e.target === id);
      inspector.appendChild(element('p', '当前显示 ' + edges.length + ' 条关联。点击下方关系查看原文并确认。'));
      edges.forEach(edge => {
        const source = data.nodes.find(n => n.id === edge.source);
        const target = data.nodes.find(n => n.id === edge.target);
        const button = element('button', source.data.label + ' → ' + edge.data.label + ' → ' + target.data.label +
          ' · ' + (edge.data.current ? statusNames[edge.data.status] : '来源失效'), 'graph-edge-choice');
        button.type = 'button'; button.addEventListener('click', () => showRelation(edge.data.relation_id));
        inspector.appendChild(button);
      });
    }
    async function draw() {
      if (graph) { graph.destroy(); graph = null; }
      canvas.replaceChildren(); canvas.classList.toggle('graph-empty', !data.nodes.length);
      stats();
      if (!data.nodes.length) {
        canvas.textContent = '暂无可显示的关系。选择资料并输入一个关注方面，生成待确认结果。';
        return;
      }
      if (!window.G6?.Graph) {
        canvas.textContent = '图形组件未加载，可使用下方文字列表查看与确认。';
        return;
      }
      const ink = token('--text'), surface = token('--surface');
      const color = edge => !edge.data.current ? token('--mute') :
        token(edge.data.status === 'verified' ? '--ok' : edge.data.status === 'rejected' ? '--danger' : '--warn');
      const instance = new G6.Graph({container:canvas, width:canvas.clientWidth, height:canvas.clientHeight,
        autoFit:'view', padding:45, animation:false, data,
        layout:{type:'d3-force', manyBody:{strength:-250}, link:{distance:150}, collide:{radius:70},
          x:{x:canvas.clientWidth / 2,strength:0.06}, y:{y:canvas.clientHeight / 2,strength:0.06}},
        node:{style:{size:42, fill:token('--brand-soft'), stroke:token('--brand'), lineWidth:2,
          labelText:d => d.data.label, labelFill:ink, labelFontSize:14, labelPlacement:'bottom',
          labelWordWrap:true, labelMaxWidth:150, labelMaxLines:3}},
        edge:{style:{stroke:color, lineWidth:2, endArrow:true,
          lineDash:d => d.data.status === 'candidate' ? [6,4] : [],
          labelText:d => d.data.label, labelFill:ink, labelFontSize:12,
          labelBackground:true, labelBackgroundFill:surface, labelPadding:[3,5]}},
        transforms:[{type:'process-parallel-edges',mode:'bundle',distance:24}],
        behaviors:['drag-canvas','zoom-canvas','drag-element'],
      });
      graph = instance;
      instance.on('node:click', event => showNode(String(event.target.id)));
      instance.on('edge:click', event => {
        const edge = data.edges.find(e => e.id === String(event.target.id));
        if (edge) showRelation(edge.data.relation_id);
      });
      await instance.render();
    }
    async function expandNode(id) {
      const ticket = ++sequence;
      if (request) request.abort();
      request = new AbortController();
      notice.textContent = '正在读取相邻关系…';
      try {
        const query = parameters(); query.set('format','graph'); query.set('node',id); query.delete('q'); query.delete('offset');
        const result = await read(url + '?' + query, request.signal);
        if (disposed || ticket !== sequence) return;
        const nodes = new Map(data.nodes.map(n => [n.id,n]));
        const edges = new Map(data.edges.map(e => [e.id,e]));
        result.nodes.forEach(n => nodes.set(n.id,n)); result.edges.forEach(e => edges.set(e.id,e));
        if (edges.size > 300) { notice.textContent = '当前视图关系较多，请按批次或关键词缩小范围。'; return; }
        data = {nodes:[...nodes.values()],edges:[...edges.values()]}; fingerprint = '';
        await draw(); showNode(id);
        notice.textContent = result.truncated ? '该实体的关联超过显示上限，请缩小范围。' : '已展开当前范围内的相邻关系。';
      } catch (error) { if (!disposed && ticket === sequence && error.name !== 'AbortError') notice.textContent = error.message; }
    }
    async function reload() {
      const ticket = ++sequence;
      if (request) request.abort();
      request = new AbortController();
      try {
        const query = parameters(); query.set('format','graph');
        const result = await read(url + '?' + query, request.signal);
        if (disposed || ticket !== sequence) return;
        const previous = document.getElementById('graphPrevious'), next = document.getElementById('graphNext');
        if (previous && next) {
          const offset = result.offset || 0;
          previous.disabled = offset===0; previous.dataset.graphOffset = String(Math.max(0,offset-100));
          next.disabled = !result.truncated; next.dataset.graphOffset = String(offset+100);
          previous.hidden = next.hidden = offset===0 && !result.truncated;
        }
        const nextFingerprint = JSON.stringify(result);
        if (nextFingerprint !== fingerprint) {
          fingerprint = nextFingerprint; data = {nodes:result.nodes,edges:result.edges}; totalAvailable = result.total_count;
          await draw();
        }
        if (disposed || ticket !== sequence) return;
        notice.textContent = result.truncated ? '当前页显示 100 条关系，可点击「下一页关系」继续查看；显示分页不限制生成总量。' :
          '拖动画布、滚轮缩放。点击实体展开关联，点击连线查看原文并逐条确认。';
        if (selectedRelation && data.edges.some(e => e.data.relation_id === selectedRelation)) await showRelation(selectedRelation);
        else if (selectedNode && data.nodes.some(n => n.id === selectedNode)) showNode(selectedNode);
        else { ++detailSequence; detailRequest?.abort(); selectedRelation = selectedNode = null; inspector.replaceChildren(element('p', '点击实体或连线查看关系及来源。', 'graph-inspector-hint')); }
      } catch (error) { if (!disposed && ticket === sequence && error.name !== 'AbortError') notice.textContent = error.message; }
    }
    const resize = new ResizeObserver(() => {
      if (graph && canvas.clientWidth) {
        graph.setSize(canvas.clientWidth,canvas.clientHeight);
        graph.fitView();
      }
    });
    resize.observe(canvas);
    const theme = new MutationObserver(() => { if (!disposed && data.nodes.length) draw().catch(() => {}); });
    theme.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    document.getElementById('graphFit').addEventListener('click', () => { if (graph) graph.fitView(); });
    document.getElementById('graphReset').addEventListener('click', () => { fingerprint = ''; selectedNode = selectedRelation = null; reload(); });
    return {reload, destroy() { if (disposed) return; disposed = true; ++sequence; ++detailSequence; request?.abort(); detailRequest?.abort(); resize.disconnect(); theme.disconnect(); if (graph) graph.destroy(); }};
  };
})();
