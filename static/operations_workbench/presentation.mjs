const labels = {
  wonum: '工单号', workorderid: '记录编号', jpnum: '作业计划', description: '说明',
  description_zh: '中文说明', description_en: '英文说明', long_description: '详细说明',
  long_description_zh: '中文详述', long_description_en: '英文详述', long_text: '工程备注',
  assetnum: '资产编号', location: '设备位置', status: '状态', worktype: '工单类型',
  wopriority: '优先级', persongroup: '班组', origrecordid: '来源记录', taskid: '任务编号',
  jobtaskid: '任务编号', jptask: '作业步骤', tasks: '任务步骤', task_count: '任务数',
  revnum: '版本', pluscrevnum: '版本', pmnum: '预防性维护编号', status_history: '状态履历',
  status_events: '状态履历', changed_local: '变更时间', related: '关联记录', workorders: '关联工单',
  workorder: '工单信息', jobplan: '作业计划', report_local: '报告时间', report_day: '报告日期',
  attraction_name_zh: '景点', attraction_name_en: '景点英文名', attraction_code: '景点代码',
  downtime_minutes: '停机分钟', downtime_hours: '停机小时', downtime_code: '停机代码',
  downtime_start_local: '停机开始', downtime_end_local: '设备恢复', ongoing: '尚未恢复',
  failurecode: '故障代码', failure_description: '故障说明', problem_description: '问题说明',
  _time: '时间', _raw: '日志原文', apexmssgdatetime: '日志时间', attrname: '景点',
  apexmssgtarget: '目标设备', apexmssgmnemonic: '助记码', apexmssgtext: '消息',
  apexmssgid: '消息编号', host: '主机', target: '目标设备', mnemonic: '助记码', message: '消息',
  cust_craft: '工艺', cust_jpfreq: '频率', cust_jptype: '计划类型', cust_compliance: '遵从类型',
  start_date: '开始日期', end_date: '结束日期', start_time: '开始时间', end_time: '结束时间',
  earliest_time: '起始时间', latest_time: '截止时间', limit: '最多返回', count: '返回条数',
  keyword: '关键词', source: '数据来源', queried_at: '查询时间', classification: '设备分类',
  equipment_location: '设备位置', codes: '停机代码', event_count: '事件数', minutes: '停机分钟',
  average_minutes: '平均分钟', ongoing_count: '尚未恢复', duration_count: '有时长的事件',
  missing_duration_count: '缺少时长的事件', label: '类别', daily: '每日趋势', reasons: '原因分布',
  attractions: '景点分布', key: '标识', totals: '合计', warnings: '数据提示',
};
export const sourceNames = {maximo: 'Maximo', splunk: 'Splunk', downtime: 'Downtime'};

export function fieldLabel(key) {
  return labels[key] || key;
}

export function cellText(value) {
  if (value === false) return '否';
  if (value === true) return '是';
  return value == null || value === '' ? '—' : String(value);
}

export function recordsFrom(data) {
  if (Array.isArray(data)) return data;
  return Array.isArray(data?.items) ? data.items : [];
}

const preferredColumns = {
  maximo: ['wonum', 'jpnum', 'description_zh', 'description', 'status', 'assetnum', 'location', 'report_local', 'cust_craft'],
  downtime: ['wonum', 'attraction_name_zh', 'description', 'downtime_start_local', 'downtime_end_local', 'downtime_minutes', 'location', 'downtime_code'],
  splunk: ['_time', 'apexmssgdatetime', 'attrname', 'apexmssgtarget', 'apexmssgmnemonic', 'apexmssgtext', 'message', '_raw'],
};

export function tableColumns(rows, source, max = 5) {
  const keys = new Set();
  for (const row of rows) {
    for (const [key, value] of Object.entries(row)) {
      if (value != null && value !== '' && typeof value !== 'object') keys.add(key);
    }
  }
  const order = [...new Set([...(preferredColumns[source] || []), ...keys])];
  return order.filter(key => keys.has(key)).slice(0, max);
}

export function csvContent(rows, source) {
  const columns = tableColumns(rows, source, Infinity);
  const quote = value => {
    let s = value == null ? '' : String(value);
    if (/^[\s]*[=+@-]/.test(s) || /^[\t\r\n]/.test(s)) s = "'" + s;
    return '"' + s.replaceAll('"', '""') + '"';
  };
  const lines = [columns.map(key => quote(fieldLabel(key))).join(',')];
  for (const row of rows) lines.push(columns.map(key => quote(row[key])).join(','));
  return '\uFEFF' + lines.join('\r\n');
}
