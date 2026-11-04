"""Read-only downtime aggregates and equipment history; no row-limited statistics."""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from . import downtime_search as db
from .downtime_catalog import ATTRACTIONS, CLASSIFICATION_CODES

MAX_DAYS = 366
METHOD = ('按站点本地报告日期选择停机工单；包含 100/101/105，可选 104。'
          '时长沿用现有口径：每单恢复记录的最大 downtime × 60，仅计时间有效且已恢复的工单。'
          '工单时长合计未扣除重叠，105 为降低客载运行，不代表景点实际不可用时长。'
          '设备履历仅包含所选范围内的停机相关工单，不包含全部维修或 PM。')


def _integer(value, label, low, high):
    if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)):
        raise db.DowntimeSearchError(f'{label}必须是 {low}–{high} 的整数。')
    value = int(value)
    if not low <= value <= high:
        raise db.DowntimeSearchError(f'{label}必须在 {low}–{high} 之间。')
    return value


def _text(value, label):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value) > 120 or any(ord(c) < 32 for c in value):
        raise db.DowntimeSearchError(f'{label}格式无效，最多 120 字符。')
    return value.strip()


DRILL_COLUMNS = dict(day='report_day', attraction='attraction_code', reason='reason_code', code='downtime_code',
                     asset='assetnum')
TOP_ASSETS = 15


def validate(payload, history=False, max_days=MAX_DAYS, max_page_size=100):
    try:
        first = date.fromisoformat(str(payload.get('start_date', '')))
        last = date.fromisoformat(str(payload.get('end_date', '')))
        end = last + timedelta(days=1)
    except (ValueError, OverflowError) as exc:
        raise db.DowntimeSearchError('日期格式无效，请使用 YYYY-MM-DD。') from exc
    if not 1 <= (end - first).days <= max_days:
        raise db.DowntimeSearchError(f'日期范围必须为 1–{max_days} 天。')
    if payload.get('mode', 'dashboard') not in ('dashboard', 'history'):
        raise db.DowntimeSearchError('工作台视图无效。')
    codes = payload.get('codes', ['100', '101', '105'])
    if not isinstance(codes, list) or not codes or any(not isinstance(v, str) or v not in db.ALLOWED_CODES for v in codes):
        raise db.DowntimeSearchError('请至少选择一个有效停机类型（100/101/105）。')
    codes = sorted(set(codes))
    attraction = _text(payload.get('location'), '景点').upper()
    if attraction and attraction not in {a['location'] for a in ATTRACTIONS}:
        raise db.DowntimeSearchError('请选择目录中的有效景点。')
    classification = _text(payload.get('classification'), 'Classification')
    if classification and classification not in CLASSIFICATION_CODES:
        raise db.DowntimeSearchError('请选择有效的 Classification。')
    asset = _text(payload.get('assetnum'), '资产编号').upper()
    equipment_location = _text(payload.get('equipment_location'), '设备位置').upper()
    if history and not (asset or equipment_location):
        raise db.DowntimeSearchError('请填写资产编号或精确设备位置，或从明细中选择设备。')
    maintenance = payload.get('maintenance', 'all')
    if maintenance not in ('all', 'Y', 'N'):
        raise db.DowntimeSearchError('Maintenance 筛选无效。')
    for name in ('include_history', 'include_104'):
        if name in payload and not isinstance(payload[name], bool):
            raise db.DowntimeSearchError('历史工单及 104 开关必须为布尔值。')
    drill = payload.get('drill') or {}
    if not isinstance(drill, dict) or drill.get('kind', '') not in ('', *DRILL_COLUMNS):
        raise db.DowntimeSearchError('图表筛选无效。')
    kind = drill.get('kind', '')
    key = _text(drill.get('key'), '图表筛选值')
    if kind and not key:
        raise db.DowntimeSearchError('图表筛选缺少值。')
    if kind == 'day':
        try:
            selected = date.fromisoformat(key)
            if not first <= selected <= last:
                raise ValueError()
        except ValueError as exc:
            raise db.DowntimeSearchError('图表日期不在查询范围内。') from exc
    if kind == 'code' and key not in ('100', '101', '104', '105'):
        raise db.DowntimeSearchError('图表停机类型无效。')
    return dict(start_date=first.isoformat(), end_date=last.isoformat(), end_exclusive=end.isoformat(),
                codes=codes, location=attraction, classification=classification, assetnum=asset,
                equipment_location=equipment_location, maintenance=maintenance,
                include_history=payload.get('include_history', True), include_104=payload.get('include_104', False),
                page=_integer(payload.get('page', 1), '页码', 1, 1_000_000),
                page_size=_integer(payload.get('page_size', 25), '每页数量', 1, max_page_size),
                drill=dict(kind=kind, key=key))


def _base(query):
    """Use only columns/tables already used by the existing working Maximo query."""
    binds = dict(siteid='SDLR', orgid='WDPR', start_date=query['start_date'], end_date=query['end_exclusive'])
    code_names = []
    for i, code in enumerate(query['codes']):
        binds[f'code_{i}'] = code
        code_names.append(f':code_{i}')
    where = ["w.woclass = 'WORKORDER'", 'w.istask = 0', 'w.siteid = :siteid', 'w.orgid = :orgid', "w.status <> 'CAN'",
             "maximo.wdw_utctosite(w.reportdate, w.siteid) >= TO_DATE(:start_date, 'YYYY-MM-DD')",
             "maximo.wdw_utctosite(w.reportdate, w.siteid) < TO_DATE(:end_date, 'YYYY-MM-DD')"]
    extra = " OR (w.worktype = 'PRJ' AND w.cust_downtimecode = '104')" if query['include_104'] else ''
    where.append(f"((w.worktype = 'EM' AND w.cust_downtimecode IN ({','.join(code_names)})){extra})")
    if not query['include_history']:
        where.append('w.historyflag = 0')
    if query['location']:
        binds.update(location=query['location'], location_prefix=query['location'] + '%')
        where.append('''(w.location = :location OR w.location LIKE :location_prefix OR EXISTS
            (SELECT 1 FROM maximo.locancestor la WHERE la.siteid = w.siteid
             AND la.ancestor = :location AND la.location = w.location))''')
    if query['classification']:
        binds['classification_prefix'] = query['classification'] + '%'
        where.append('cs.classificationid LIKE :classification_prefix')
    for field in ('assetnum',):
        if query[field]:
            binds[field] = query[field]
            where.append(f'UPPER(w.{field}) = :{field}')
    if query['equipment_location']:
        binds['equipment_location'] = query['equipment_location']
        where.append('UPPER(w.location) = :equipment_location')
    if query['maintenance'] != 'all':
        where.append("CASE WHEN w.cust_maint_down = 1 THEN 'Y' ELSE 'N' END = :maintenance")
        binds['maintenance'] = query['maintenance']
    cases = []
    for i, attraction in enumerate(sorted(ATTRACTIONS, key=lambda a: -len(a['location']))):
        binds[f'attr_{i}'] = attraction['location']
        binds[f'prefix_{i}'] = attraction['location'] + '%'
        cases.append(f'WHEN w.location LIKE :prefix_{i} THEN :attr_{i}')
    attraction_case = 'CASE ' + ' '.join(cases) + " ELSE '__UNKNOWN__' END"
    # Report-date grouping is intentional, independent of when the downtime started.
    cte = f'''WITH filtered AS (
      SELECT w.workorderid, w.wonum, w.siteid, w.orgid, w.status, w.worktype, w.historyflag,
        w.location, w.assetnum, w.description AS workorder_description, w.failurecode, w.problemcode,
        w.cust_down_reason, w.cust_rootcause, w.cust_maint_down, w.cust_downtimecode AS downtime_code,
        cs.classificationid AS reason_code, cs.description AS reason_description,
        TO_CHAR(maximo.wdw_utctosite(w.reportdate, w.siteid), 'YYYY-MM-DD HH24:MI:SS') AS report_local,
        TO_CHAR(maximo.wdw_utctosite(w.reportdate, w.siteid), 'YYYY-MM-DD') AS report_day,
        {attraction_case} AS attraction_code
      FROM maximo.workorder w LEFT JOIN maximo.classstructure cs ON cs.classstructureid = w.classstructureid
      WHERE {' AND '.join(where)}
    ), asset_times AS (
      SELECT ast.wonum, ast.siteid,
        MIN(CASE WHEN ast.isrunning = 0 THEN ast.changedate END) AS first_down,
        MAX(CASE WHEN ast.isrunning = 0 THEN ast.changedate END) AS last_down,
        MAX(CASE WHEN ast.isrunning = 1 THEN ast.changedate END) AS last_up,
        MAX(CASE WHEN ast.isrunning = 1 THEN ast.downtime END) AS downtime_hours
      FROM maximo.assetstatus ast
      WHERE EXISTS (SELECT 1 FROM filtered f WHERE f.wonum = ast.wonum AND f.siteid = ast.siteid)
      GROUP BY ast.wonum, ast.siteid
    ), base AS (
      SELECT f.*,
        TO_CHAR(maximo.wdw_utctosite(a.first_down, f.siteid), 'YYYY-MM-DD HH24:MI:SS') AS downtime_start_local,
        TO_CHAR(maximo.wdw_utctosite(a.last_up, f.siteid), 'YYYY-MM-DD HH24:MI:SS') AS downtime_end_local,
        CASE WHEN a.last_down IS NOT NULL AND (a.last_up IS NULL OR a.last_down > a.last_up) THEN 1 ELSE 0 END AS ongoing,
        CASE WHEN a.first_down IS NOT NULL AND a.last_up >= a.last_down AND a.downtime_hours >= 0
             THEN a.downtime_hours * 60 END AS downtime_minutes
      FROM filtered f LEFT JOIN asset_times a ON a.wonum = f.wonum AND a.siteid = f.siteid
    ), scoped AS (SELECT * FROM base WHERE { _drill_where(query, binds) })
    '''
    return cte, binds


def _drill_where(query, binds):
    kind, key = query['drill']['kind'], query['drill']['key']
    if not kind:
        return '1 = 1'
    column = DRILL_COLUMNS[kind]
    if key == '__UNKNOWN__':
        return f'{column} IS NULL' if kind in ('reason', 'asset') else f"{column} = '__UNKNOWN__'"
    binds['drill_key'] = key
    return f'{column} = :drill_key'


def _rows(sql, binds):
    # Both Oracle drivers reject superfluous binds; summary and page have different binds.
    used = set(re.findall(r'(?<![A-Za-z0-9_]):([A-Za-z][A-Za-z0-9_]*)', sql))
    columns, rows = db._execute(sql, {k: v for k, v in binds.items() if k in used})
    return [dict(zip(columns, row)) for row in rows]


def _number(value):
    if value is None:
        return 0
    value = float(value)
    if not math.isfinite(value):
        raise db.DowntimeSearchError('数据库返回了无效统计数值。')
    return value


def _metrics(row):
    count, known = int(_number(row.get('event_count'))), int(_number(row.get('duration_count')))
    minutes = _number(row.get('minutes'))
    return dict(event_count=count, duration_count=known, minutes=round(minutes, 2),
                average_minutes=round(minutes / known, 2) if known else None,
                ongoing_count=int(_number(row.get('ongoing_count'))),
                missing_duration_count=count - known - int(_number(row.get('ongoing_count'))))


NAMES = {a['location']: a['name_zh'] for a in ATTRACTIONS}


def summarize(groups, query):
    """Turn (group_kind, key columns, metric) rows into the dashboard summary."""
    summary = dict(totals=_metrics({}), daily=[], attractions=[], reasons=[], codes=[], assets=[], heatmap=[])
    names = NAMES
    for row in groups:
        metrics = _metrics(row)
        kind = row['group_kind']
        if kind == 'total':
            summary['totals'] = metrics
            continue
        if kind == 'day_attraction':
            code = str(row.get('attraction_code') or '__UNKNOWN__')
            summary['heatmap'].append(dict(day=str(row.get('report_day')), key=code,
                                           label=names.get(code, '未映射景点'), **metrics))
            continue
        if kind == 'asset':
            key = str(row.get('assetnum') or '__UNKNOWN__')
            summary['assets'].append(dict(key=key, label='未关联资产' if key == '__UNKNOWN__' else key,
                                          location=row.get('sample_location'), **metrics))
            continue
        column, target = dict(day=('report_day', 'daily'), attraction=('attraction_code', 'attractions'),
                              reason=('reason_code', 'reasons'), code=('downtime_code', 'codes'))[kind]
        key = str(row.get(column) or '__UNKNOWN__')
        label = names.get(key, '未映射景点') if kind == 'attraction' else ('未分类' if key == '__UNKNOWN__' else key)
        if kind == 'reason' and key != '__UNKNOWN__' and row.get('reason_description'):
            label = f"{key} · {row['reason_description']}"
        summary[target].append(dict(key=key, label=label, **metrics))
    daily = {row['key']: row for row in summary['daily']}
    day = date.fromisoformat(query['start_date'])
    summary['daily'] = []
    while day.isoformat() <= query['end_date']:
        key = day.isoformat()
        summary['daily'].append(daily.get(key, dict(key=key, label=key, **_metrics({}))))
        day += timedelta(days=1)
    for target in ('attractions', 'reasons', 'codes'):
        summary[target].sort(key=lambda r: (-r['event_count'], r['key']))
    summary['asset_count'] = len(summary['assets'])
    summary['assets'] = sorted(summary['assets'], key=lambda r: (-r['minutes'], -r['event_count'], r['key']))[:TOP_ASSETS]
    summary['heatmap'].sort(key=lambda r: (r['day'], r['key']))
    return summary


def previous_window(query):
    """The equal-length window immediately before the query, for period-over-period cards."""
    first, last = date.fromisoformat(query['start_date']), date.fromisoformat(query['end_date'])
    days = (last - first).days + 1
    return (first - timedelta(days=days)).isoformat(), (first - timedelta(days=1)).isoformat()


def finish_items(items):
    for row in items:
        row['workorderid'] = str(row['workorderid'])
        row['ongoing'] = bool(_number(row.get('ongoing')))
        row['downtime_minutes'] = round(_number(row['downtime_minutes']), 2) if row.get('downtime_minutes') is not None else None
        row['attraction_name_zh'] = NAMES.get(row.get('attraction_code'), '未映射景点')
        row['maintenance_yn'] = 'Y' if str(row.get('cust_maint_down')) == '1' else 'N'
    return items


def result(query, summary, items, started, history, source):
    total = summary['totals']['event_count']
    return dict(query_id=uuid4().hex, queried_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),
                timezone='Asia/Shanghai', elapsed_seconds=round(monotonic() - started, 3),
                mode='history' if history else 'dashboard', filters=query, method=METHOD,
                summary=summary, items=items, total=total, page=query['page'], page_size=query['page_size'],
                has_more=query['page'] * query['page_size'] < total, complete_aggregation=True, source=source)


_SUMMARY_SQL = '''SELECT
      CASE WHEN GROUPING(report_day) = 0 AND GROUPING(attraction_code) = 0 THEN 'day_attraction'
           WHEN GROUPING(report_day) = 0 THEN 'day'
           WHEN GROUPING(attraction_code) = 0 THEN 'attraction'
           WHEN GROUPING(reason_code) = 0 THEN 'reason'
           WHEN GROUPING(downtime_code) = 0 THEN 'code'
           WHEN GROUPING(assetnum) = 0 THEN 'asset' ELSE 'total' END AS group_kind,
      report_day, attraction_code, reason_code, downtime_code, assetnum,
      COUNT(*) AS event_count, COUNT(downtime_minutes) AS duration_count,
      SUM(downtime_minutes) AS minutes, SUM(ongoing) AS ongoing_count,
      MAX(reason_description) AS reason_description, MAX(location) AS sample_location
      FROM scoped GROUP BY GROUPING SETS ((), (report_day), (attraction_code), (reason_code), (downtime_code),
                                          (assetnum), (report_day, attraction_code))'''
_TOTAL_SQL = '''SELECT 'total' AS group_kind, COUNT(*) AS event_count, COUNT(downtime_minutes) AS duration_count,
      SUM(downtime_minutes) AS minutes, SUM(ongoing) AS ongoing_count FROM scoped'''


def summary_only(payload, history=False):
    """Full-match aggregates without a record page (pairs with the fixed live event query)."""
    query = validate(payload, history)
    cte, binds = _base(query)
    return summarize(_rows(cte + _SUMMARY_SQL, binds), query)


def previous_totals(query):
    start, end = previous_window(query)
    earlier = dict(query, start_date=start, end_date=end,
                   end_exclusive=(date.fromisoformat(end) + timedelta(days=1)).isoformat(), drill=dict(kind='', key=''))
    cte, binds = _base(earlier)
    rows = _rows(cte + _TOTAL_SQL, binds)
    return dict(start_date=start, end_date=end, totals=_metrics(rows[0] if rows else {}))


def search(payload, history=False, *, max_page_size=100, compare=True):
    started = monotonic()
    query = validate(payload, history, max_page_size=max_page_size)
    cte, binds = _base(query)
    summary = summarize(_rows(cte + _SUMMARY_SQL, binds), query)
    comparison = None
    if compare and not history and not query['drill']['kind']:
        try:
            comparison = previous_totals(query)
        except db.DowntimeSearchError:
            comparison = None
    binds.update(start_row=(query['page'] - 1) * query['page_size'], end_row=query['page'] * query['page_size'])
    page_sql = cte + '''SELECT * FROM (
      SELECT scoped.*, ROW_NUMBER() OVER (ORDER BY report_local DESC, workorderid DESC) AS row_number_ FROM scoped
      ) WHERE row_number_ > TO_NUMBER(:start_row) AND row_number_ <= TO_NUMBER(:end_row) ORDER BY row_number_'''
    items = finish_items(_rows(page_sql, binds))
    data = result(query, summary, items, started, history, 'Maximo · SDLR / WDPR · read only')
    data['comparison'] = comparison
    return data


def detail(payload):
    identifier = str(_integer(payload.get('workorderid'), '工单 ID', 1, 10**18))
    binds = dict(workorderid=identifier, siteid='SDLR', orgid='WDPR')
    records = _rows('''SELECT workorderid, wonum, assetnum, location, status, worktype, description,
      failurecode, problemcode, cust_rootcause, cust_down_reason FROM maximo.workorder
      WHERE workorderid = TO_NUMBER(:workorderid) AND siteid = :siteid AND orgid = :orgid
        AND woclass = 'WORKORDER' AND istask = 0''', binds)
    if not records:
        raise db.DowntimeSearchError('工单不存在或不属于当前站点。')
    record = records[0]
    record['workorderid'] = str(record['workorderid'])
    binds['wonum'] = record['wonum']
    result = dict(workorder=record, tasks=[], status_events=[], related=[], warnings=[])
    queries = {
        'tasks': '''SELECT taskid, description FROM maximo.wotask WHERE parent = :wonum
          AND siteid = :siteid AND orgid = :orgid AND istask = 1 ORDER BY taskid''',
        'status_events': '''SELECT TO_CHAR(maximo.wdw_utctosite(changedate, siteid), 'YYYY-MM-DD HH24:MI:SS') AS changed_local,
          isrunning, code, downtime FROM maximo.assetstatus WHERE wonum = :wonum AND siteid = :siteid ORDER BY changedate''',
        'related': '''SELECT 'follow_up' AS relation, w.wonum, w.status, w.description FROM maximo.workorder w
          WHERE w.origrecordid = :wonum AND w.origrecordclass = 'WORKORDER' AND w.siteid = :siteid AND w.orgid = :orgid
          UNION ALL
          SELECT 'related' AS relation, w.wonum, w.status, w.description FROM maximo.workorder w
          WHERE w.siteid = :siteid AND w.orgid = :orgid AND EXISTS
          (SELECT 1 FROM maximo.relatedrecord rr WHERE rr.relatedrecclass = 'WORKORDER'
           AND rr.relatedreckey = :wonum AND rr.recordkey = w.wonum AND rr.recordkey <> :wonum)''',
    }
    for name, sql in queries.items():
        try:
            result[name] = _rows(sql, binds)
        except db.DowntimeSearchError as exc:
            result['warnings'].append(f'{name}: {exc}')
    return result
