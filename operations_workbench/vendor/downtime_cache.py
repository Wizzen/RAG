"""Server-side record of downtime work orders, synced from Maximo by calendar month.

The dashboard and equipment history filter this record locally; work-order detail
remains a live Maximo query.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, timedelta
from time import monotonic

from . import downtime_insights as live
from . import downtime_search as db
from ..runtime import runtime_dir

MAX_DAYS = 1827

class CacheMiss(db.DowntimeSearchError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def _connect():
    path = runtime_dir() / 'downtime.sqlite3'
    if not path.is_file():
        raise CacheMiss('尚未导入本机停机记录，可选择实时 Maximo 查询。', 'empty')
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    connection.execute('PRAGMA case_sensitive_like=ON')
    return connection


def _month_start(month):
    return date.fromisoformat(f'{month}-01')


def _next_month(month):
    first = _month_start(month)
    return (first + timedelta(days=32)).strftime("%Y-%m")


def _months_between(first, last):
    months, month = [], first
    while month <= last:
        months.append(month)
        month = _next_month(month)
    return months


def coverage():
    with closing(_connect()) as connection:
        return _coverage(connection)


def _coverage(connection):
    months = [dict(row) for row in connection.execute('SELECT * FROM months ORDER BY month')]
    total = connection.execute('SELECT COUNT(*) FROM workorders').fetchone()[0]
    if not months:
        return dict(empty=True, months=0, rows=0, first_day=None, data_through=None, synced_at=None, gaps=[])
    names = [m['month'] for m in months]
    gaps = [m for m in _months_between(names[0], names[-1]) if m not in set(names)]
    return dict(empty=False, months=len(months), rows=total, first_day=f'{names[0]}-01',
                data_through=months[-1]['synced_through'], synced_at=max(m['synced_at'] for m in months),
                oldest_sync=min(m['synced_at'] for m in months), gaps=gaps)


def _check_covered(query, connection):
    info = _coverage(connection)
    if info['empty']:
        raise CacheMiss('本机停机记录为空，请选用实时查询。', 'empty')
    if query['start_date'] < info['first_day'] or query['start_date'] > info['data_through']:
        raise CacheMiss(f"已导入的停机记录覆盖 {info['first_day']} 至 {info['data_through']}，请选择覆盖期间或改用实时查询。", 'not_covered')
    if any(query['start_date'][:7] <= gap <= query['end_date'][:7] for gap in info['gaps']):
        raise CacheMiss('所选期间有尚未导入的月份，请调整日期或改用实时查询。', 'not_covered')
    return info


def _where(query, binds):
    binds.update(start_date=query['start_date'], end_date=query['end_date'])
    where = ['report_day >= :start_date', 'report_day <= :end_date']
    codes = []
    for i, code in enumerate(query['codes']):
        binds[f'code_{i}'] = code
        codes.append(f':code_{i}')
    extra = " OR (worktype = 'PRJ' AND downtime_code = '104')" if query['include_104'] else ''
    where.append(f"((worktype = 'EM' AND downtime_code IN ({', '.join(codes)})){extra})")
    if not query['include_history']:
        where.append('historyflag = 0')
    if query['location']:
        binds.update(location=query['location'], location_prefix=query['location'] + '%')
        where.append('''(location = :location OR location LIKE :location_prefix OR EXISTS
          (SELECT 1 FROM location_ancestors la WHERE la.ancestor = :location AND la.location = workorders.location))''')
    if query['classification']:
        binds['classification_prefix'] = query['classification'] + '%'
        where.append('reason_code LIKE :classification_prefix')
    if query['assetnum']:
        binds['assetnum'] = query['assetnum']
        where.append('UPPER(assetnum) = :assetnum')
    if query['equipment_location']:
        binds['equipment_location'] = query['equipment_location']
        where.append('UPPER(location) = :equipment_location')
    if query['maintenance'] != 'all':
        binds['maintenance'] = query['maintenance']
        where.append("CASE WHEN cust_maint_down = 1 THEN 'Y' ELSE 'N' END = :maintenance")
    return ' AND '.join(where)


def search(payload, history=False, *, event_limit=None):
    started = monotonic()
    query = live.validate(payload, history, MAX_DAYS)
    if event_limit is not None:
        query['page_size'] = event_limit
    binds = {}
    cte = f'''WITH scoped AS (SELECT * FROM workorders WHERE {_where(query, binds)}
      AND {live._drill_where(query, binds)}) '''
    keys = ['report_day', 'attraction_code', 'reason_code', 'downtime_code']
    selects = []
    for kind, column in [('total', None), ('day', 'report_day'), ('attraction', 'attraction_code'),
                         ('reason', 'reason_code'), ('code', 'downtime_code')]:
        fields = ', '.join(k if k == column else f'NULL AS {k}' for k in keys)
        selects.append(f'''SELECT '{kind}' AS group_kind, {fields}, COUNT(*) AS event_count,
          COUNT(downtime_minutes) AS duration_count, SUM(downtime_minutes) AS minutes,
          SUM(ongoing) AS ongoing_count, MAX(reason_description) AS reason_description
          FROM scoped''' + (f' GROUP BY {column}' if column else ''))
    with closing(_connect()) as connection:
        info = _check_covered(query, connection)
        groups = [dict(row) for row in connection.execute(cte + ' UNION ALL '.join(selects), binds)]
        page = dict(binds, limit=query['page_size'], offset=(query['page'] - 1) * query['page_size'])
        items = [dict(row) for row in connection.execute(
            cte + 'SELECT * FROM scoped ORDER BY report_local DESC, workorderid DESC LIMIT :limit OFFSET :offset', page)]
    summary = live.summarize(groups, query)
    data_through = info['data_through']
    result = live.result(query, summary, live.finish_items(items), started, history,
                         f'服务器记录 · 数据截至 {data_through}')
    result['cache'] = dict(data_through=data_through, synced_at=info['synced_at'],
                           stale=query['end_date'] > data_through)
    if result['cache']['stale']:
        result['warning'] = f'所选结束日期晚于快照截止日 {data_through}，指标仅涵盖已导入的期间。'
    return result
