// Portable equipment manifests contain data only. A viewer is selected by the host.
export const FORMAT = 'shdr-equipment-model';
export const FILE_LIMIT = 2 * 1024 * 1024;
const identifier = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$/;
const drawingNumber = /^[A-Z0-9][A-Z0-9-]{2,39}$/;

function field(value, label, max, required = false) {
  if (value === undefined && !required) return '';
  if (typeof value !== 'string' || value.length > max || (required && !value.trim())) {
    throw new Error(label + ' 格式不正确或超过 ' + max + ' 字。');
  }
  return value.trim();
}

function key(value, label) {
  if (typeof value !== 'string' || !identifier.test(value)) {
    throw new Error(label + ' 需为 1 至 80 位字母、数字、点、横线或下划线，并以字母或数字开头。');
  }
  return value;
}

export function normalizeManifest(input) {
  if (!input || input.format !== FORMAT || input.version !== 1) {
    throw new Error('请选择版本 1 的设备模型 JSON 文件。');
  }
  const model = {
    format: FORMAT, version: 1, id: key(input.id, '模型编号'),
    name: field(input.name, '设备名称', 160, true),
    category: field(input.category, '设备分类', 80) || '通用设备',
    description: field(input.description, '设备说明', 8000),
  };
  if (!Array.isArray(input.components) || !input.components.length || input.components.length > 2000) {
    throw new Error('每个设备模型需包含 1 至 2000 个部件。');
  }
  const parents = new Map();
  model.components = input.components.map(part => {
    if (!part || typeof part !== 'object') throw new Error('部件资料格式不正确。');
    const id = key(part.id, '部件编号');
    if (parents.has(id)) throw new Error('设备内的部件编号重复：' + id);
    const parent = part.parent == null || part.parent === '' ? '' : key(part.parent, '上级编号');
    parents.set(id, parent);
    const drawing = field(part.drawing, '图号', 40).toUpperCase();
    if (drawing && !drawingNumber.test(drawing)) throw new Error('图号需为 3 至 40 位字母、数字或短横线。');
    return {id, parent, name: field(part.name, '部件名称', 160, true), drawing,
      description: field(part.description, '部件说明', 8000)};
  });
  const resolved = new Set();
  for (const id of parents.keys()) {
    const path = new Set();
    let cursor = id;
    while (cursor && !resolved.has(cursor)) {
      if (!parents.has(cursor)) throw new Error('找不到上级部件：' + cursor);
      if (path.has(cursor)) throw new Error('部件层级形成循环：' + cursor);
      path.add(cursor);
      cursor = parents.get(cursor);
    }
    for (const partId of path) resolved.add(partId);
  }
  const entries = input.entries ?? [];
  if (!Array.isArray(entries) || entries.length > 4000) throw new Error('单个模型最多可保存 4000 条笔记与记录。');
  model.entries = entries.map(entry => {
    if (!entry || !['tracking', 'learning'].includes(entry.kind) || !parents.has(entry.component)) {
      throw new Error('记录类型或关联的部件编号无效。');
    }
    if (typeof entry.at !== 'string' || !Number.isFinite(Date.parse(entry.at))) throw new Error('记录时间无效。');
    return {kind: entry.kind, component: entry.component, at: new Date(entry.at).toISOString(),
      text: field(entry.text, '记录内容', 8000, true)};
  });
  if (new Blob([JSON.stringify(model)]).size > FILE_LIMIT) throw new Error('模型已超出 2 MB 容量。');
  return model;
}

export function componentOrder(components) {
  const children = new Map();
  for (const part of components) {
    if (!children.has(part.parent)) children.set(part.parent, []);
    children.get(part.parent).push(part);
  }
  const stack = (children.get('') || []).map(part => ({part, depth: 0})).reverse();
  const result = [];
  while (stack.length) {
    const row = stack.pop();
    result.push(row);
    const parts = children.get(row.part.id) || [];
    for (let i = parts.length - 1; i >= 0; i--) stack.push({part: parts[i], depth: row.depth + 1});
  }
  return result;
}

export function openModelStore(accountId) {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('shdr-equipment-library:' + accountId, 1);
    request.onupgradeneeded = () => request.result.createObjectStore('models', {keyPath: 'id'});
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error('请先关闭较旧的设备目录标签页，再刷新。'));
  });
}

export function readModels(database) {
  return new Promise((resolve, reject) => {
    const request = database.transaction('models').objectStore('models').getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

// Read and write in the same transaction so independent tabs append to the latest version.
export function saveModel(database, id, transform) {
  if (!database) return Promise.reject(new Error('本机存储不可用，请检查浏览器的网站数据权限。'));
  return new Promise((resolve, reject) => {
    const transaction = database.transaction('models', 'readwrite');
    const store = transaction.objectStore('models');
    const request = store.get(id);
    let output;
    let failure;
    request.onsuccess = () => {
      try {
        output = normalizeManifest(transform(request.result));
        if (output.id !== id) throw new Error('模型编号已改变。');
        store.put(output);
      } catch (error) {
        failure = error;
        transaction.abort();
      }
    };
    transaction.onabort = () => reject(failure || transaction.error || new Error('保存失败，请检查本机存储空间。'));
    transaction.oncomplete = () => resolve(output);
  });
}
