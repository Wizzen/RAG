import {clearSessions, deleteSession, listSessions, saveSession} from './ai-history.mjs';
import {cellText, csvContent, fieldLabel, recordsFrom, sourceNames, tableColumns} from './presentation.mjs';

const root = document.getElementById('ops-workbench');
const $ = id => document.getElementById('ops-' + id);
const sourceCopy = {
  maximo: '工单与作业计划',
  splunk: 'Apex 设备日志',
  downtime: '停机事件与趋势',
};
const priorityFields = {
  'maximo.workorders': ['wonum', 'jpnum', 'keyword', 'location', 'start_date', 'end_date'],
  'maximo.jobplans': ['jpnum', 'keyword', 'statuses'],
  'splunk.search': ['target', 'mnemonic', 'keyword', 'attraction', 'start_time', 'end_time'],
  'downtime.search': ['source', 'location', 'start_date', 'end_date', 'classification', 'codes'],
  'downtime.dashboard': ['source', 'location', 'start_date', 'end_date'],
  'downtime.history': ['source', 'assetnum', 'equipment_location', 'location', 'start_date', 'end_date'],
};

let catalog = {actions: [], sources: []};
let sourceId = 'maximo';
let action;
let response;
let resultParams = {};
let queryController;
let probeController;
let aiController;
let detailController;
let pdfDocument;
let pdfRender;
let filtered = [];
let page = 0;
const pageSize = 20;
const drafts = new Map();

function el(tag, text, className) {
  const element = document.createElement(tag);
  if (text != null) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function button(label, onClick, className = 'ops-row-open') {
  const element = el('button', label, className);
  element.type = 'button';
  element.addEventListener('click', onClick);
  return element;
}

function status(element, message = '', error = false) {
  element.textContent = message;
  element.hidden = !message;
  element.classList.toggle('is-error', error);
}

async function api(path, body, controller) {
  const options = {method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
    redirect: 'error', signal: controller?.signal, headers: {Accept: 'application/json'}};
  if (body) {
    options.headers['X-CSRFToken'] = root.querySelector('[name=csrfmiddlewaretoken]').value;
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  let reply;
  try {
    reply = await fetch('/tools/operations/api/' + path + '/', options);
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('连接失败，请检查网络和登录状态后重试。');
  }
  const payload = await reply.json().catch(() => ({}));
  if (!reply.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '查询失败，请检查登录状态和访问权限。');
  return payload;
}

function searchCatalog(id) {
  return catalog.actions.find(item => item.id === id);
}

function actionLabel(id) {
  return searchCatalog(id)?.label || id;
}

async function loadCatalog() {
  $('reload').hidden = true;
  status($('catalog-status'), '正在载入数据源…');
  try {
    catalog = await api('catalog');
    if (!Array.isArray(catalog.actions) || !catalog.actions.length || !Array.isArray(catalog.sources)) {
      throw new Error('查询目录为空，请联系管理员检查运维配置。');
    }
    $('content').hidden = false;
    status($('catalog-status'));
    renderSources();
    chooseSource(catalog.sources.some(s => s.id === 'maximo') ? 'maximo' : catalog.sources[0].id);
  } catch (error) {
    status($('catalog-status'), error.message, true);
    $('reload').hidden = false;
  }
}

function renderSources() {
  const nodes = catalog.sources.map(source => {
    const item = button('', () => chooseSource(source.id), 'ops-source');
    item.dataset.source = source.id;
    const symbol = el('span', null, 'ops-symbol');
    symbol.append(icon(Object.hasOwn(sourceCopy, source.id) ? source.id : 'records'));
    const title = el('span');
    title.append(el('strong', sourceNames[source.id] || source.label), el('small', sourceCopy[source.id] || ''));
    item.append(symbol, title);
    return item;
  });
  $('sources').replaceChildren(...nodes);
}

function icon(name) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('class', 'ops-icon');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(ns, 'use');
  use.setAttribute('href', '#ops-i-' + name);
  svg.append(use);
  return svg;
}

function stopQuery() {
  queryController?.abort();
  queryController = null;
  $('cancel').hidden = true;
  $('submit').disabled = false;
  $('results-title').closest('section').removeAttribute('aria-busy');
}

function chooseSource(id) {
  if (action && id === sourceId) return;
  if (action) drafts.set(action.id, readFields(false));
  stopQuery();
  probeController?.abort();
  probeController = null;
  sourceId = id;
  for (const node of $('sources').children) node.setAttribute('aria-pressed', String(node.dataset.source === id));
  const actions = catalog.actions.filter(item => item.source === id && !item.hidden && !item.id.endsWith('.probe'));
  const select = $('action');
  select.replaceChildren(...actions.map(item => new Option(item.label, item.id)));
  $('action-field').hidden = actions.length < 2;
  $('query-source').textContent = sourceNames[id] || id;
  $('probe').hidden = !searchCatalog(id + '.probe');
  $('probe').disabled = false;
  const source = catalog.sources.find(item => item.id === id);
  const notes = [];
  if (source?.configured === false) notes.push('实时连接尚未配置');
  if (source?.coverage?.data_through) notes.push('停机记录截至 ' + source.coverage.data_through);
  status($('probe-status'), notes.join(' · '));
  const remembered = drafts.get('source:' + id);
  chooseAction(actions.find(item => item.id === remembered) || actions[0]);
}

function chooseAction(next) {
  stopQuery();
  action = next;
  if (!action) {
    $('action').disabled = true;
    $('submit').disabled = true;
    status($('query-status'), '此数据源没有可用查询。', true);
    return;
  }
  $('action').disabled = false;
  $('action').value = action.id;
  drafts.set('source:' + sourceId, action.id);
  $('query-title').textContent = $('action-field').hidden ? action.label : '筛选条件';
  renderFields(drafts.get(action.id) || {});
  clearResult();
}

function renderFields(draft) {
  $('fields').replaceChildren();
  $('extra-fields').replaceChildren();
  const fields = (action.fields || []).filter(field => !field.hidden);
  const order = priorityFields[action.id] || fields.map(field => field.name);
  const ordered = order.map(name => fields.find(field => field.name === name)).filter(Boolean);
  for (const field of [...ordered, ...fields.filter(field => !order.includes(field.name))]) {
    const group = order.includes(field.name) || field.required ? $('fields') : $('extra-fields');
    group.append(renderField(field, draft[field.name] ?? field.default));
  }
  $('more').hidden = !$('extra-fields').children.length;
  $('more').open = false;
}

function renderField(field, value) {
  const name = field.name;
  if (field.type === 'multiselect') {
    const fieldset = el('fieldset', null, 'ops-fieldset');
    fieldset.append(el('legend', field.label));
    const choices = el('div', null, 'ops-choice-list');
    const selected = Array.isArray(value) ? value.map(String) : [];
    for (const opt of field.options || []) {
      const input = el('input');
      input.type = 'checkbox';
      input.name = name;
      input.value = String(opt.value);
      input.checked = selected.includes(input.value);
      const label = el('label');
      label.append(input, document.createTextNode(opt.label));
      choices.append(label);
    }
    fieldset.append(choices);
    return fieldset;
  }
  const wrapper = el('label', field.label);
  let input;
  if (field.type === 'select') {
    input = el('select');
    if (!field.required && field.default == null && !(field.options || []).some(opt => opt.value === '')) input.append(new Option('全部', ''));
    for (const opt of field.options || []) input.append(new Option(opt.label, opt.value));
  } else {
    input = el('input');
    input.type = ['checkbox', 'date', 'datetime-local', 'number'].includes(field.type) ? field.type : 'text';
    if (input.type === 'checkbox') {
      wrapper.className = 'ops-check';
      wrapper.prepend(input);
      input.checked = value === true;
    }
  }
  input.name = name;
  input.required = Boolean(field.required);
  if (input.type === 'text' && name === 'keyword') {
    input.placeholder = sourceId === 'maximo' ? '例如，轴承' : '输入要查找的消息内容';
  }
  for (const key of ['min', 'max', 'step', 'placeholder', 'maxlength']) {
    if (field[key] != null) input.setAttribute(key, String(field[key]));
  }
  if (input.type !== 'checkbox') {
    if (value != null) input.value = String(value);
    wrapper.append(input);
  }
  return wrapper;
}

function readFields(forRequest = true) {
  if (!action) return {};
  const form = new FormData($('query-form'));
  const values = {};
  for (const field of action.fields || []) {
    if (field.hidden) continue;
    const value = form.get(field.name);
    if (field.type === 'multiselect') values[field.name] = form.getAll(field.name);
    else if (field.type === 'checkbox') values[field.name] = value !== null;
    else if (value !== null && String(value).trim() !== '') values[field.name] = field.type === 'number' ? Number(value) : String(value).trim();
    else if (!forRequest) values[field.name] = '';
  }
  return values;
}

function clearResult() {
  response = null;
  filtered = [];
  page = 0;
  $('summary').replaceChildren();
  const empty = el('div', null, 'ops-idle');
  empty.append(icon('records'), el('p', '选择筛选条件并查询，即可在这里浏览记录。'));
  $('result-view').replaceChildren(empty);
  $('results-title').textContent = '查询结果';
  $('export').disabled = true;
  status($('query-status'));
}

async function runQuery(params = readFields()) {
  if (!action) return;
  stopQuery();
  clearResult();
  const controller = new AbortController();
  queryController = controller;
  const id = action.id;
  $('result-view').replaceChildren();
  $('submit').disabled = true;
  $('cancel').hidden = false;
  $('results-title').closest('section').setAttribute('aria-busy', 'true');
  status($('query-status'), '正在查询 ' + sourceNames[sourceId] + '…');
  try {
    const payload = await api('query', {action: id, params}, controller);
    if (queryController !== controller) return;
    response = payload.data;
    resultParams = {...params};
    $('results-title').textContent = action.label + '结果';
    renderResults();
  } catch (error) {
    if (queryController === controller && error.name !== 'AbortError') {
      status($('query-status'), error.message, true);
      $('result-view').replaceChildren(button('重新查询', () => runQuery(params), 'btn'));
    }
  } finally {
    if (queryController === controller) stopQuery();
  }
}

function renderResults() {
  const rows = recordsFrom(response);
  const messages = ['返回 ' + rows.length + ' 条'];
  if (response.queried_at) messages.push('查询时间 ' + response.queried_at);
  if (resultParams.source === 'cache') messages.push('本地停机记录');
  if (response.cache?.data_through) messages.push('记录截至 ' + response.cache.data_through);
  if (response.cache?.stale) messages.push('查询结束日期超出已同步范围');
  if (response.truncated) messages.push('已达返回上限，可缩小查询范围');
  if (response.warning) messages.push(String(response.warning));
  if (Array.isArray(response.warnings)) messages.push(...response.warnings);
  status($('query-status'), messages.join(' · '));
  $('summary').replaceChildren();
  if (response.summary) showSummary(response.summary);
  const box = $('result-view');
  box.replaceChildren();
  $('export').disabled = !rows.length;
  if (!rows.length) {
    box.append(el('p', '当前条件下没有匹配的记录。', 'ops-empty'));
    addServerPager(box);
    return;
  }
  const head = el('div', null, 'ops-list-head');
  const count = el('strong', '本次返回 ' + rows.length + ' 条');
  const filter = el('input');
  filter.type = 'search';
  filter.setAttribute('aria-label', '在本次结果中过滤');
  filter.placeholder = '在本次结果中过滤…';
  head.append(count, filter);
  const list = el('div');
  box.append(head, list);
  const columns = tableColumns(rows, sourceId);
  const update = () => {
    const word = filter.value.trim().toLowerCase();
    filtered = word ? rows.filter(row => Object.values(row).some(value =>
      value != null && typeof value !== 'object' && String(value).toLowerCase().includes(word))) : rows;
    page = 0;
    $('export').disabled = !filtered.length;
    paint();
  };
  function paint() {
    list.replaceChildren();
    if (!filtered.length) {
      list.append(el('p', '本次结果中没有符合筛选条件的记录。', 'ops-empty'));
      return;
    }
    const start = page * pageSize;
    const slice = filtered.slice(start, start + pageSize);
    list.append(makeTable(slice, columns, (row, cell) => {
      const actions = el('div', null, 'ops-row-actions');
      actions.append(button('详情', () => showRow(row)));
      if (sourceId === 'downtime' && row.location && (row.downtime_start_local || row.report_local) && searchCatalog('downtime.splunk')) {
        actions.append(button('同段日志', () => showCorrelation(row)));
      }
      cell.append(actions);
    }));
    const pager = el('div', null, 'ops-pager');
    pager.append(el('span', (start + 1) + '–' + (start + slice.length) + ' / ' + filtered.length + ' 条'));
    if (filtered.length > pageSize) {
      const previous = button('上一页', () => {page--; paint();});
      const next = button('下一页', () => {page++; paint();});
      previous.disabled = page === 0;
      next.disabled = start + pageSize >= filtered.length;
      pager.append(previous, next);
    }
    list.append(pager);
  }
  filter.addEventListener('input', update);
  update();
  addServerPager(box);
}

function makeTable(rows, columns, renderAction) {
  const wrap = el('div', null, 'ops-table-wrap');
  const table = el('table', null, 'ops-table');
  const tr = table.createTHead().insertRow();
  for (const column of columns) tr.append(el('th', fieldLabel(column)));
  if (renderAction) tr.append(el('th', '查看'));
  const tbody = table.createTBody();
  for (const row of rows) {
    const entry = tbody.insertRow();
    for (const key of columns) {
      const value = cellText(row[key]);
      const cell = entry.insertCell();
      cell.dataset.key = key;
      cell.append(el('span', value.length > 240 ? value.slice(0, 240) + '…' : value, 'ops-cell-value'));
    }
    if (renderAction) renderAction(row, entry.insertCell());
  }
  wrap.append(table);
  return wrap;
}

function addServerPager(box) {
  if (!['downtime.dashboard', 'downtime.history', 'splunk.search'].includes(action.id)) return;
  const remote = el('div', null, 'ops-pager');
  const num = Number(response.page || resultParams.page || 1);
  const offset = Number(resultParams.offset || 0);
  if (!response.has_more && num <= 1 && offset <= 0) return;
  const limit = Number(response.page_size || resultParams.limit || recordsFrom(response).length || 100);
  const isSplunk = action.id === 'splunk.search';
  if (num > 1 || offset > 0) remote.append(button('读取上一批', () => runQuery(
    {...resultParams, ...(isSplunk ? {offset: Math.max(0, offset - limit)} : {page: num - 1})})));
  if (response.has_more) remote.append(button('读取下一批', () => runQuery(
    {...resultParams, ...(isSplunk ? {offset: offset + limit} : {page: num + 1})})));
  box.append(remote);
}

function showSummary(summary, target = $('summary')) {
  const stats = el('div', null, 'ops-stats');
  for (const key of ['event_count', 'minutes', 'average_minutes', 'ongoing_count']) {
    if (summary.totals?.[key] == null) continue;
    const metric = el('span', null, 'ops-stat');
    metric.append(el('b', cellText(summary.totals[key])), document.createTextNode(fieldLabel(key)));
    stats.append(metric);
  }
  target.append(stats);
  for (const key of ['daily', 'attractions', 'reasons', 'codes']) {
    if (!Array.isArray(summary[key]) || !summary[key].length) continue;
    const title = key === 'codes' ? '停机类型分布' : fieldLabel(key);
    const disclosure = el('details', null, 'ops-breakdown');
    disclosure.append(el('summary', title + '（' + summary[key].length + '）'));
    disclosure.addEventListener('toggle', () => {
      if (disclosure.open && disclosure.children.length === 1) {
        pagedDetails(disclosure, summary[key], ['label', 'event_count', 'minutes', 'ongoing_count']);
      }
    });
    target.append(disclosure);
  }
}

function pagedDetails(container, rows, columns) {
  let index = 0;
  const content = el('div');
  function render() {
    const slice = rows.slice(index, index + pageSize);
    content.replaceChildren(makeTable(slice, columns));
    if (rows.length <= pageSize) return;
    const pager = el('div', null, 'ops-pager');
    const prev = button('前一组', () => {index -= pageSize; render();});
    const next = button('后一组', () => {index += pageSize; render();});
    prev.disabled = !index;
    next.disabled = index + pageSize >= rows.length;
    pager.append(el('span', (index + 1) + '–' + (index + slice.length) + ' / ' + rows.length), prev, next);
    content.append(pager);
  }
  container.append(content);
  render();
}

function objectView(record) {
  const wrap = el('div');
  if (record == null || typeof record !== 'object') {
    wrap.textContent = cellText(record);
    return wrap;
  }
  const facts = el('dl', null, 'ops-facts');
  const sections = [];
  for (const [key, value] of Object.entries(record)) {
    if (value == null || value === '') continue;
    if (typeof value !== 'object') {
      facts.append(el('dt', fieldLabel(key)), el('dd', cellText(value)));
      continue;
    }
    if (Array.isArray(value) && !value.length) continue;
    const section = el('details');
    section.append(el('summary', fieldLabel(key) + (Array.isArray(value) ? '（' + value.length + '）' : '')));
    section.addEventListener('toggle', () => {
      if (!section.open || section.children.length > 1) return;
      if (Array.isArray(value) && value.every(item => item && typeof item === 'object')) {
        const list = el('div');
        let shown = 0;
        const load = button('显示更多', add);
        function add() {
          for (const item of value.slice(shown, shown + pageSize)) list.append(objectView(item));
          shown += pageSize;
          load.hidden = shown >= value.length;
        }
        section.append(list, load);
        add();
      } else if (Array.isArray(value)) {
        section.append(el('p', value.join('、'), 'ops-notice'));
      } else {
        section.append(objectView(value));
      }
    });
    sections.push(section);
  }
  if (facts.children.length) wrap.append(facts);
  wrap.append(...sections);
  return wrap;
}

function stopDetail() {
  detailController?.abort();
  detailController = null;
  pdfRender?.cancel();
  pdfRender = null;
  pdfDocument?.destroy();
  pdfDocument = null;
}

const modalTarget = {
  source: $('detail-source'), title: $('detail-title'), body: $('detail-body'),
  show() { if (!$('detail').open) $('detail').showModal(); },
};
const paneTarget = {
  source: $('ai-pane-source'), title: $('ai-pane-title'), body: $('ai-pane-body'),
  show() {
    $('ai-pane').hidden = false;
    $('ai-drawer').classList.add('has-pane');
  },
};
// Follow-up links inside a detail (job plan, PDF) stay in the view that opened it.
let detailTarget = modalTarget;

function openDetail(source, title) {
  stopDetail();
  detailTarget.source.textContent = source;
  detailTarget.title.textContent = title;
  detailTarget.body.replaceChildren();
  detailTarget.show();
  detailTarget.body.scrollTop = 0;
  return detailTarget.body;
}

function showRow(row) {
  detailTarget = modalTarget;
  const title = row.wonum || row.jpnum || row.apexmssgmnemonic || '记录详情';
  let id;
  let params;
  if (sourceId === 'maximo' && row.wonum) {
    id = 'maximo.workorder'; params = {wonum: row.wonum};
  } else if (sourceId === 'maximo' && row.jpnum) {
    id = 'maximo.jobplan'; params = {jpnum: row.jpnum};
  } else if (sourceId === 'downtime' && row.workorderid) {
    id = 'downtime.detail'; params = {workorderid: String(row.workorderid), source: resultParams.source || 'cache'};
  }
  if (id && searchCatalog(id)) {
    queryDetail(id, params, title, row);
  } else {
    const body = openDetail(sourceNames[sourceId], title);
    body.append(objectView(row));
  }
}

function showCorrelation(row) {
  detailTarget = modalTarget;
  const params = {location: row.location, padding_minutes: 15};
  for (const key of ['downtime_start_local', 'downtime_end_local', 'report_local', 'attraction_name_en', 'snapshot_end_local']) {
    if (row[key] != null && row[key] !== '') params[key] = row[key];
  }
  if (!params.downtime_end_local && response?.cache?.data_through) {
    params.snapshot_end_local = response.cache.data_through + ' 23:59:59';
  }
  queryDetail('downtime.splunk', params, '停机时段的 Apex 日志', row);
}

async function queryDetail(id, params, title, row) {
  const body = openDetail(actionLabel(id), title);
  const remote = el('div');
  const note = el('p', '正在获取记录…', 'ops-inline-status');
  note.setAttribute('role', 'status');
  body.append(note, remote);
  if (row) {
    const local = el('details');
    local.append(el('summary', '本次查询中的原始记录'), objectView(row));
    body.append(local);
  }
  const controller = new AbortController();
  detailController = controller;
  try {
    const {data} = await api('query', {action: id, params}, controller);
    if (detailController !== controller) return;
    status(note, data.queried_at ? '查询时间 ' + data.queried_at : '资料已载入');
    const header = data.workorder || data.jobplan;
    if (header) {
      remote.append(objectView(header));
      const links = el('div', null, 'ops-row-actions');
      if (id === 'maximo.workorder' || id === 'maximo.jobplan') {
        const kind = id.split('.')[1];
        const code = kind === 'workorder' ? params.wonum : params.jpnum;
        for (const lang of ['CN', 'EN']) {
          links.append(button(lang === 'CN' ? '中文 PDF' : '英文 PDF', () => showPdf(kind, code, lang, () => queryDetail(id, params, title, row))));
        }
      }
      if (id !== 'maximo.jobplan' && header.jpnum) links.append(button('查看作业计划', () => queryDetail('maximo.jobplan', {jpnum: header.jpnum}, header.jpnum)));
      if (id === 'downtime.detail' && header.wonum) links.append(button('查看 Maximo 工单', () => queryDetail('maximo.workorder', {wonum: header.wonum}, header.wonum)));
      remote.append(links);
      const rest = {};
      for (const key of ['tasks', 'status_history', 'status_events', 'related', 'workorders', 'warnings']) {
        if (data[key]) rest[key] = data[key];
      }
      remote.append(objectView(rest));
    } else if (id === 'downtime.splunk') {
      const rows = recordsFrom(data);
      if (rows.length) pagedDetails(remote, rows, tableColumns(rows, 'splunk'));
      else remote.append(el('p', '该停机时段没有匹配的日志。', 'ops-empty'));
      const limit = Number(data.page_size || params.limit || rows.length || 100);
      const offset = Number(params.offset || 0);
      if (offset > 0) remote.append(button('读取上一批日志', () => queryDetail(id, {...params, offset: Math.max(0, offset - limit)}, title, row)));
      if (data.has_more) remote.append(button('读取下一批日志', () => queryDetail(id, {...params, offset: offset + limit}, title, row)));
    } else remote.append(objectView(data));
  } catch (error) {
    if (detailController !== controller || error.name === 'AbortError') return;
    status(note, error.message, true);
    remote.append(button('重试', () => queryDetail(id, params, title, row)));
  }
}

async function showPdf(kind, code, lang, back) {
  const body = openDetail('Maximo 报表', code + (lang === 'CN' ? ' · 中文 PDF' : ' · 英文 PDF'));
  const controller = new AbortController();
  detailController = controller;
  const note = el('p', '正在生成 PDF…', 'ops-inline-status');
  note.setAttribute('role', 'status');
  body.append(button('返回记录详情', back, 'ops-link'), note);
  let loading;
  try {
    const path = '/tools/operations/api/pdf/' + kind + '/' + encodeURIComponent(code) + '/?lang=' + lang;
    const res = await fetch(path, {credentials: 'same-origin', redirect: 'error', signal: controller.signal});
    if (!res.ok || !res.headers.get('Content-Type')?.includes('application/pdf')) {
      const error = await res.json().catch(() => ({}));
      throw new Error(error.detail || '原始 PDF 暂时不可用，请稍后重试。');
    }
    const bytes = await res.arrayBuffer();
    const lib = await import('/static/vehicle_tracker/vendor/pdfjs/pdf.min.mjs');
    if (detailController !== controller) return;
    lib.GlobalWorkerOptions.workerSrc = '/static/vehicle_tracker/vendor/pdfjs/pdf.worker.min.mjs';
    loading = lib.getDocument({data: new Uint8Array(bytes), isEvalSupported: false, enableXfa: false, useSystemFonts: true});
    controller.signal.addEventListener('abort', () => { if (!pdfDocument) loading.destroy(); }, {once: true});
    const doc = await loading.promise;
    if (detailController !== controller) {doc.destroy(); return;}
    pdfDocument = doc;
    let index = 1;
    let zoom = 1;
    const tools = el('div', null, 'ops-pager');
    const count = el('span');
    const canvas = el('canvas');
    canvas.setAttribute('aria-label', 'PDF 原始页面');
    const scroll = el('div', null, 'ops-pdf-scroll');
    scroll.append(canvas);
    async function render() {
      if (detailController !== controller) return;
      for (const b of tools.querySelectorAll('button')) b.disabled = true;
      try {
        const sheet = await doc.getPage(index);
        if (detailController !== controller) return;
        const width = Math.max(150, scroll.clientWidth - 12);
        const scale = width / sheet.getViewport({scale: 1}).width * zoom;
        const vp = sheet.getViewport({scale});
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.ceil(vp.width * dpr);
        canvas.height = Math.ceil(vp.height * dpr);
        canvas.style.width = vp.width + 'px';
        canvas.style.height = vp.height + 'px';
        pdfRender = sheet.render({canvasContext: canvas.getContext('2d'), viewport: vp, transform: [dpr,0,0,dpr,0,0]});
        await pdfRender.promise;
        count.textContent = '第 ' + index + ' / ' + doc.numPages + ' 页 · ' + Math.round(100 * zoom) + '%';
        status(note);
      } catch (error) {
        if (detailController === controller) status(note, '此页无法显示，请重新打开报表。', true);
      } finally {
        if (detailController === controller) {
          prev.disabled = index <= 1;
          next.disabled = index >= doc.numPages;
          smaller.disabled = zoom <= 0.75;
          larger.disabled = zoom >= 2;
        }
      }
    }
    const prev = button('上一页', () => {index--; render();});
    const next = button('下一页', () => {index++; render();});
    const smaller = button('缩小', () => {zoom -= 0.25; render();});
    const larger = button('放大', () => {zoom += 0.25; render();});
    tools.append(count, prev, next, smaller, larger);
    body.append(tools, scroll);
    await render();
  } catch (error) {
    if (detailController === controller) {
      status(note, error.message || 'PDF 加载失败。', true);
      body.append(button('重试', () => showPdf(kind, code, lang, back)));
    }
  }
}

async function probe() {
  probeController?.abort();
  const controller = new AbortController();
  probeController = controller;
  $('probe').disabled = true;
  status($('probe-status'), '正在检查 ' + sourceNames[sourceId] + ' 连接…');
  try {
    const {data} = await api('query', {action: sourceId + '.probe', params: {}}, controller);
    if (probeController === controller) status($('probe-status'), data.message || '连接正常。', data.ok === false);
  } catch (error) {
    if (probeController === controller && error.name !== 'AbortError') status($('probe-status'), error.message, true);
  } finally {
    if (probeController === controller) {probeController = null; $('probe').disabled = false;}
  }
}

function openEvidence(evidence, index) {
  const source = sourceNames[evidence.source] || evidence.source;
  const body = openDetail(source + ' · AI 查询依据', '依据 ' + index + ' · ' + actionLabel(evidence.action));
  body.append(el('h3', '实际查询条件'), objectView(evidence.params || {}));
  if (evidence.status === 'error') {
    body.append(el('p', evidence.detail || '查询未完成。', 'ops-notice'));
    return;
  }
  const data = evidence.data || {};
  if (data.queried_at) body.append(el('p', '查询时间 ' + data.queried_at, 'ops-inline-status'));
  if (data.cache?.data_through) body.append(el('p', '停机记录截至 ' + data.cache.data_through + (data.cache.stale ? '。查询结束日期超出已同步范围。' : ''), 'ops-notice'));
  if (data.summary?.totals) {
    body.append(el('h3', '本次查询统计'));
    showSummary(data.summary, body);
  }
  const rows = recordsFrom(data);
  if (rows.length) {
    body.append(el('h3', '返回记录'));
    pagedDetails(body, rows, tableColumns(rows, evidence.source));
  } else if (data.workorder || data.jobplan) body.append(objectView(data.workorder || data.jobplan));
  const details = el('details');
  details.append(el('summary', '查看查询依据的全部字段'));
  details.addEventListener('toggle', () => {
    if (details.open && details.children.length === 1) details.append(objectView(data));
  });
  body.append(details);
}

const REF_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._\/-]{2,39}$/;

// Work-order and job-plan numbers the server actually returned for this answer.
function evidenceLinks(evidence) {
  const links = new Map();
  const add = (code, id, params) => {
    const text = code == null ? '' : String(code).trim();
    if (REF_PATTERN.test(text) && !links.has(text) && searchCatalog(id)) links.set(text, {id, params});
  };
  const visit = row => {
    if (!row || typeof row !== 'object') return;
    add(row.wonum, 'maximo.workorder', {wonum: String(row.wonum ?? '')});
    add(row.jpnum, 'maximo.jobplan', {jpnum: String(row.jpnum ?? ''), include_workorders: false});
  };
  for (const item of evidence || []) {
    if (item.status !== 'ok') continue;
    visit(item.params);
    visit(item.data?.workorder);
    visit(item.data?.jobplan);
    for (const row of recordsFrom(item.data)) visit(row);
  }
  return links;
}

function linkMatcher(links) {
  if (!links.size) return null;
  const escaped = [...links.keys()].sort((a, b) => b.length - a.length).map(code => code.replace(/[.*+?^${}()|[\]\\\/]/g, '\\$&'));
  return new RegExp('(?<![A-Za-z0-9])(' + escaped.join('|') + ')(?![A-Za-z0-9])', 'g');
}

function inlineText(node, text, links, matcher) {
  if (!matcher) {node.append(document.createTextNode(text)); return;}
  let last = 0;
  for (const match of text.matchAll(matcher)) {
    if (match.index > last) node.append(document.createTextNode(text.slice(last, match.index)));
    const code = match[1];
    const link = links.get(code);
    const ref = button(code, () => openFromAi(() => queryDetail(link.id, link.params, code)), 'ops-ai-ref');
    ref.title = link.id === 'maximo.jobplan' ? '查看作业计划 ' + code : '查看工单 ' + code;
    node.append(ref);
    last = match.index + code.length;
  }
  if (last < text.length) node.append(document.createTextNode(text.slice(last)));
}

function inline(node, text, links, matcher) {
  for (const piece of text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
    if (!piece) continue;
    if (piece.startsWith('**') && piece.endsWith('**') && piece.length > 4) {
      const strong = el('strong');
      inlineText(strong, piece.slice(2, -2), links, matcher);
      node.append(strong);
    } else if (piece.startsWith('`') && piece.endsWith('`') && piece.length > 2) {
      const code = el('code');
      inlineText(code, piece.slice(1, -1), links, matcher);
      node.append(code);
    } else inlineText(node, piece, links, matcher);
  }
}

function tableCells(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
}

function markdownTable(lines, links, matcher) {
  const rows = lines.map(tableCells);
  const separator = row => row.length && row.every(cell => /^:?-{2,}:?$/.test(cell));
  const head = rows.length > 1 && separator(rows[1]) ? rows[0] : null;
  const body = rows.filter((row, index) => !separator(row) && !(head && index === 0));
  const wrap = el('div', null, 'ops-table-wrap ops-ai-table');
  const table = el('table', null, 'ops-table');
  const fill = (row, cells, tag) => {
    for (const cell of cells) {
      const node = el(tag);
      inline(node, /^(null|none|undefined)$/i.test(cell) ? '—' : cell, links, matcher);
      row.append(node);
    }
  };
  if (head) fill(table.createTHead().insertRow(), head, 'th');
  const tbody = table.createTBody();
  for (const row of body) fill(tbody.insertRow(), row, 'td');
  wrap.append(table);
  return wrap;
}

function answerView(text, links = new Map()) {
  const matcher = linkMatcher(links);
  const content = el('div', null, 'ops-ai-answer');
  const lines = text.split('\n');
  let list;
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index].trim();
    if (!line) {list = null; continue;}
    if (line.startsWith('|')) {
      const block = [];
      while (index < lines.length && lines[index].trim().startsWith('|')) block.push(lines[index++].trim());
      index--;
      list = null;
      content.append(markdownTable(block, links, matcher));
      continue;
    }
    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    if (heading) {
      list = null;
      const node = el('h4');
      inline(node, heading[1], links, matcher);
      content.append(node);
      continue;
    }
    const marker = /^([*+-]|\d+[.)])\s+/.exec(line);
    let node;
    if (marker) {
      const tag = /^\d/.test(marker[1]) ? 'ol' : 'ul';
      if (!list || list.localName !== tag) {
        list = el(tag);
        content.append(list);
      }
      node = el('li');
      list.append(node);
    } else {
      list = null;
      node = el('p');
      content.append(node);
    }
    inline(node, line.slice(marker?.[0].length || 0), links, matcher);
  }
  return content;
}

const account = String(root.dataset.account || 'anonymous');
const outcome = {complete: '分析完成', partial: '部分数据源未返回资料', needs_input: '请补充查询条件', unavailable: '此时无法取得完整资料'};
let session = null;
let historyNote = '';

function wideLayout() {
  return $('ai-drawer').classList.contains('is-wide') && window.matchMedia('(min-width: 900px)').matches;
}

function openFromAi(show) {
  detailTarget = wideLayout() ? paneTarget : modalTarget;
  show();
}

function closePane() {
  if (detailTarget === paneTarget) stopDetail();
  $('ai-pane').hidden = true;
  $('ai-pane-body').replaceChildren();
  $('ai-drawer').classList.remove('has-pane');
}

function timeLabel(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}).format(date);
}

function turnView(turn) {
  const article = el('article', null, 'ops-ai-turn');
  const asked = el('div', null, 'ops-ai-question');
  asked.append(el('p', turn.question));
  const sourceLabel = turn.source === 'auto' ? '自动选择' : (sourceNames[turn.source] || turn.source);
  asked.append(el('small', [sourceLabel, timeLabel(turn.asked_at)].filter(Boolean).join(' · ')));
  const reply = el('div', null, 'ops-ai-reply');
  if (turn.pending) {
    const wait = el('p', '正在选择数据源并查询记录…', 'ops-inline-status ops-ai-pending');
    wait.setAttribute('role', 'status');
    reply.append(wait);
  } else if (turn.error) {
    reply.append(el('p', turn.error, 'ops-inline-status is-error'));
  } else {
    const evidence = Array.isArray(turn.evidence) ? turn.evidence : [];
    reply.append(el('span', outcome[turn.status] || '查询完成', 'ops-ai-outcome' + (turn.status === 'complete' ? '' : ' is-warn')));
    reply.append(answerView(turn.answer || '未取得回答。', evidenceLinks(evidence)));
    if (evidence.length) {
      const list = el('div', null, 'ops-ai-evidence-list');
      list.append(el('h4', '本次查询依据'));
      evidence.forEach((item, i) => {
        const itemButton = button('', () => openFromAi(() => openEvidence(item, i + 1)), 'ops-evidence');
        itemButton.append(el('strong', (i + 1) + ' · ' + actionLabel(item.action)),
          el('small', (sourceNames[item.source] || item.source) + ' · ' + (item.status === 'ok' ? '查看记录' : '查看未取得资料的原因')));
        list.append(itemButton);
      });
      reply.append(list);
    }
  }
  article.append(asked, reply);
  return article;
}

function renderThread(scroll = true) {
  const thread = $('ai-thread');
  const turns = session?.turns || [];
  thread.replaceChildren($('ai-welcome'), ...turns.map(turnView));
  $('ai-welcome').hidden = turns.length > 0;
  if (historyNote) thread.append(el('p', historyNote, 'ops-notice'));
  if (scroll) thread.lastElementChild?.scrollIntoView({block: turns.length ? 'start' : 'end'});
}

async function renderSessions() {
  const list = $('ai-sessions');
  let rows = [];
  try {
    rows = await listSessions(account);
  } catch {
    list.replaceChildren(el('li', '此浏览器无法保存历史对话。', 'ops-ai-history-empty'));
    return;
  }
  if (!rows.length) {
    list.replaceChildren(el('li', '暂无历史对话。', 'ops-ai-history-empty'));
    $('ai-clear').hidden = true;
    return;
  }
  $('ai-clear').hidden = false;
  list.replaceChildren(...rows.map(row => {
    const item = el('li', null, 'ops-ai-session' + (row.id === session?.id ? ' is-current' : ''));
    const open = button('', () => loadSession(row), 'ops-ai-session-open');
    if (row.id === session?.id) open.setAttribute('aria-current', 'true');
    open.append(el('strong', row.title || '未命名对话'), el('small', timeLabel(row.updated) + ' · ' + (row.turns?.length || 0) + ' 轮'));
    const remove = button('', async () => {
      await deleteSession(row.id).catch(() => {});
      if (session?.id === row.id) startSession();
      renderSessions();
    }, 'ops-ai-session-delete');
    remove.setAttribute('aria-label', '删除对话：' + (row.title || '未命名对话'));
    remove.textContent = '×';
    item.append(open, remove);
    return item;
  }));
}

function showHistory(visible) {
  $('ai-drawer').classList.toggle('show-history', visible);
  $('ai-history-toggle').setAttribute('aria-expanded', String(visible));
}

function startSession() {
  session = null;
  historyNote = '';
  closePane();
  renderThread(false);
  renderSessions();
  $('question').focus({preventScroll: true});
}

function loadSession(row) {
  session = row;
  historyNote = '';
  closePane();
  renderThread();
  renderSessions();
  if (!wideLayout()) showHistory(false);
}

async function persist(owner) {
  const stored = {...owner, turns: owner.turns.filter(turn => !turn.pending)};
  try {
    await saveSession(stored);
    if (owner === session) historyNote = '';
  } catch {
    if (owner === session) historyNote = '本次对话未能保存到浏览器历史，刷新页面后将不保留。';
  }
  renderSessions();
}

function aiBusy(busy) {
  $('ask').disabled = busy;
  $('ai-cancel').hidden = !busy;
  $('ai-thread').setAttribute('aria-busy', String(busy));
  $('ai-launcher').classList.toggle('is-busy', busy);
  $('ai-launcher').setAttribute('aria-label', busy ? '运维 AI 正在分析，点击查看' : '打开运维 AI');
}

function recentHistory(turns) {
  const messages = [];
  for (const turn of turns.filter(row => !row.pending && !row.error && row.answer).slice(-3)) {
    messages.push({role: 'user', content: turn.question.slice(0, 1200)}, {role: 'assistant', content: turn.answer.slice(0, 1500)});
  }
  return messages;
}

async function ask() {
  const question = $('question').value.trim();
  if (!question || aiController) {$('question').focus(); return;}
  const now = new Date().toISOString();
  const id = crypto.randomUUID?.() || Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  session ||= {id, account, title: question.slice(0, 40), created: now, updated: now, turns: []};
  const owner = session;
  const history = recentHistory(owner.turns);
  const turn = {question, source: $('ai-source').value, asked_at: now, pending: true};
  owner.turns.push(turn);
  owner.updated = now;
  $('question').value = '';
  renderThread();
  const controller = new AbortController();
  aiController = controller;
  aiBusy(true);
  try {
    const result = await api('ask', {question, source: turn.source, history}, controller);
    Object.assign(turn, {status: result.status, answer: result.answer || '',
      evidence: Array.isArray(result.evidence) ? result.evidence : []});
  } catch (error) {
    turn.error = error.name === 'AbortError' ? '已取消本次分析。' : error.message;
  } finally {
    delete turn.pending;
    if (aiController === controller) {aiController = null; aiBusy(false);}
  }
  owner.updated = new Date().toISOString();
  if (owner === session) renderThread();
  await persist(owner);
}

$('query-form').addEventListener('submit', event => {event.preventDefault(); runQuery();});
$('action').addEventListener('change', () => {
  if (action) drafts.set(action.id, readFields(false));
  chooseAction(searchCatalog($('action').value));
});
$('reset').addEventListener('click', () => {
  stopQuery();
  drafts.delete(action.id);
  renderFields({});
  clearResult();
});
$('cancel').addEventListener('click', () => {
  stopQuery();
  status($('query-status'), '已取消本次查询。');
});
$('probe').addEventListener('click', probe);
$('reload').addEventListener('click', loadCatalog);
$('detail-close').addEventListener('click', () => $('detail').close());
$('detail').addEventListener('close', () => {
  if ($('detail').open) return;
  if (detailTarget === modalTarget) stopDetail();
  $('detail-body').replaceChildren();
});
$('ai-form').addEventListener('submit', event => {event.preventDefault(); ask();});
$('ai-cancel').addEventListener('click', () => aiController?.abort());
$('question').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $('ai-form').requestSubmit();
  }
});
$('ai-new').addEventListener('click', startSession);
$('ai-clear').addEventListener('click', async () => {
  if (!window.confirm('清空本浏览器中保存的全部运维 AI 历史对话？')) return;
  await clearSessions(account).catch(() => {});
  startSession();
});
$('ai-history-toggle').addEventListener('click', () => showHistory(!$('ai-drawer').classList.contains('show-history')));
$('ai-pane-close').addEventListener('click', closePane);
const WIDE_KEY = 'shdr-operations-ai-wide';
function setWide(wide) {
  $('ai-drawer').classList.toggle('is-wide', wide);
  $('ai-size').setAttribute('aria-pressed', String(wide));
  $('ai-size').setAttribute('aria-label', wide ? '切换为侧边栏' : '展开为大窗口');
  $('ai-size').querySelector('span').textContent = wide ? '侧边栏' : '大窗口';
  showHistory(wide);
  if (!wide) closePane();
  try { localStorage.setItem(WIDE_KEY, wide ? '1' : '0'); } catch { /* preference only */ }
}
$('ai-size').addEventListener('click', () => setWide(!$('ai-drawer').classList.contains('is-wide')));
try { setWide(localStorage.getItem(WIDE_KEY) !== '0'); } catch { setWide(true); }
renderThread(false);
renderSessions();
for (const node of root.querySelectorAll('[data-example]')) {
  node.addEventListener('click', () => {
    $('question').value = node.dataset.example;
    $('question').focus();
  });
}
const aiDrawer = $('ai-drawer');
const aiLauncher = $('ai-launcher');
const aiTriggers = [aiLauncher, ...document.querySelectorAll('[data-rag-chat-open]')];
let aiOpener = aiLauncher;
function sizeAiDrawer() {
  const view = window.visualViewport;
  if (!aiDrawer.open || !view) return;
  aiDrawer.style.setProperty('--ops-vv-height', view.height + 'px');
  aiDrawer.style.setProperty('--ops-vv-top', view.offsetTop + 'px');
}
for (const trigger of aiTriggers) {
  trigger.setAttribute('aria-controls', aiDrawer.id);
  trigger.setAttribute('aria-haspopup', 'dialog');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.addEventListener('click', () => {
    if (aiDrawer.open) return;
    aiOpener = trigger;
    aiDrawer.showModal();
    for (const entry of aiTriggers) entry.setAttribute('aria-expanded', 'true');
    sizeAiDrawer();
    if (!window.matchMedia('(max-width: 620px)').matches) $('question').focus({preventScroll: true});
  });
}
window.visualViewport?.addEventListener('resize', sizeAiDrawer);
window.visualViewport?.addEventListener('scroll', sizeAiDrawer);
$('ai-close').addEventListener('click', () => aiDrawer.close());
function outsideDrawer(event) {
  if (event.target !== aiDrawer) return false;
  const rect = aiDrawer.getBoundingClientRect();
  return event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
}
let pressedBackdrop = false;
aiDrawer.addEventListener('pointerdown', event => { pressedBackdrop = outsideDrawer(event); });
aiDrawer.addEventListener('click', event => {
  if (pressedBackdrop && outsideDrawer(event)) aiDrawer.close();
  pressedBackdrop = false;
});
aiDrawer.addEventListener('close', () => {
  if (aiDrawer.open) return;
  for (const entry of aiTriggers) entry.setAttribute('aria-expanded', 'false');
  const sidebarHidden = aiOpener.closest('.fos-sidebar') && document.getElementById('navToggle')?.getClientRects().length;
  (sidebarHidden ? aiLauncher : aiOpener).focus({preventScroll: true});
});
$('export').addEventListener('click', () => {
  if (!filtered.length) return;
  const blob = new Blob([csvContent(filtered, sourceId)], {type: 'text/csv;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const link = el('a');
  link.href = url;
  const exportDate = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Asia/Shanghai'}).format(new Date());
  link.download = action.label + '-' + exportDate + '.csv';
  root.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
});
loadCatalog();
