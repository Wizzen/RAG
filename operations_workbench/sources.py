"""The operations workbench's closed catalog of read-only engineering operations."""

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import BoundedSemaphore

from fos_rag.isolated_model import child_environment

from . import sources_splunk as splunk
from .runtime import option, runtime_dir
from .vendor import downtime_cache as cache
from .vendor import downtime_insights as insights
from .vendor import downtime_search as db
from .vendor import maximo_workbench as maximo
from .vendor.downtime_catalog import ATTRACTIONS, CLASSIFICATIONS


EXPORT_CAP = 5000
EXPORTABLE = ("maximo.workorders", "maximo.jobplans", "splunk.search",
              "downtime.search", "downtime.dashboard", "downtime.history")
DRILL_KINDS = [{"value": kind, "label": label} for kind, label in (
    ("day", "日期"), ("attraction", "景点"), ("reason", "分类"), ("code", "停机代码"), ("asset", "资产"))]


class SourceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _field(name, label, kind="text", *, options=None, **attributes):
    item = {"name": name, "label": label, "type": kind, **attributes}
    if options is not None:
        item["options"] = [
            value if isinstance(value, dict) else {"value": value, "label": value}
            for value in options
        ]
    return item


def _action(name, label, fields=(), hidden=False):
    return {
        "id": name,
        "source": name.split(".")[0],
        "label": label,
        "fields": list(fields),
        "hidden": hidden,
    }


def _dates(last, days):
    return [
        _field(
            "start_date",
            "开始日期",
            "date",
            default=(last - timedelta(days=days - 1)).isoformat(),
        ),
        _field("end_date", "结束日期", "date", default=last.isoformat()),
    ]


def _actions(now, cache_day):
    location = _field(
        "location",
        "景点",
        "select",
        options=[{"value": a["location"], "label": a["name_zh"]} for a in ATTRACTIONS],
    )
    codes = _field(
        "codes",
        "停机代码",
        "multiselect",
        options=["100", "101", "105"],
        default=["100", "101", "105"],
    )
    source = _field(
        "source",
        "数据来源",
        "select",
        options=[
            {"value": "cache", "label": "已导入停机记录"},
            {"value": "live", "label": "实时 Maximo"},
        ],
        default="cache",
    )
    classifications = _field(
        "classification",
        "分类",
        "select",
        options=[{"value": c["code"], "label": c["name_zh"]} for c in CLASSIFICATIONS],
    )
    incident_filters = [
        source,
        *_dates(cache_day, 7),
        location,
        classifications,
        codes,
        _field("include_104", "含 104 闭园后停机", "checkbox", default=False),
        _field("drill_kind", "图表筛选", "select", options=DRILL_KINDS, hidden=True),
        _field("drill_key", "图表筛选值", hidden=True),
    ]
    asset = _field("assetnum", "资产编号")
    equipment = _field("equipment_location", "精确设备位置")
    insight_filters = [
        *incident_filters,
        asset,
        equipment,
        _field(
            "maintenance",
            "维护标记",
            "select",
            options=["all", "Y", "N"],
            default="all",
        ),
        _field("include_history", "包含历史工单", "checkbox", default=True),
        _field("page", "页码", "number", min=1, default=1),
        _field("page_size", "每页数量", "number", min=1, max=100, default=25),
    ]
    wonum = _field("wonum", "工单号")
    jpnum = _field("jpnum", "作业计划号")
    keyword = _field("keyword", "描述关键词")
    wo_fields = [
        _field("wonum", "工单号"),
        jpnum,
        asset,
        keyword,
        *_dates(now.date(), 30),
        location,
        _field("pmnum", "预防维护编号"),
        _field("worktypes", "工单类型", "multiselect", options=maximo.WORKTYPE_OPTIONS),
        _field("statuses", "状态", "multiselect", options=maximo.WOSTATUS_OPTIONS),
        _field("priorities", "优先级", "multiselect", options=maximo.PRIORITY_OPTIONS),
        _field(
            "persongroups", "班组", "multiselect", options=maximo.PERSONGROUP_OPTIONS
        ),
        _field("has_jobplan", "已关联作业计划", "checkbox"),
        _field("include_cancelled", "包含取消工单", "checkbox"),
        _field("limit", "结果上限", "number", min=1, max=500, default=50),
        _field("offset", "记录偏移", "number", min=0, default=0),
    ]
    jp_fields = [
        jpnum,
        keyword,
        _field("statuses", "状态", "multiselect", options=maximo.JP_STATUS_OPTIONS),
        _field(
            "compliance",
            "遵从类型",
            "multiselect",
            options=maximo.JP_COMPLIANCE_OPTIONS,
        ),
        _field("crafts", "工艺", "multiselect", options=maximo.JP_CRAFT_OPTIONS),
        _field("frequencies", "频率", "multiselect", options=maximo.JP_FREQ_OPTIONS),
        _field("types", "计划类型", "multiselect", options=maximo.JP_TYPE_OPTIONS),
        _field(
            "attractions",
            "景点代码",
            "multiselect",
            options=maximo.JP_ATTRACTION_OPTIONS,
        ),
        _field("limit", "结果上限", "number", min=1, max=200, default=50),
        _field("offset", "记录偏移", "number", min=0, default=0),
    ]
    return [
        _action("maximo.workorders", "工单检索", wo_fields),
        _action("maximo.jobplans", "作业计划检索", jp_fields),
        _action("maximo.workorder", "工单详情", [{**wonum, "required": True}], True),
        _action(
            "maximo.jobplan",
            "作业计划详情",
            [
                {**jpnum, "required": True},
                _field("include_workorders", "查看关联工单", "checkbox", default=True),
                *_dates(now.date(), 30),
                _field(
                    "wo_limit", "关联工单上限", "number", min=1, max=100, default=50
                ),
            ],
            True,
        ),
        _action(
            "splunk.search",
            "Apex 日志检索",
            [
                _field(
                    "start_time",
                    "开始时间（上海）",
                    "datetime-local",
                    default=(now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                ),
                _field(
                    "end_time",
                    "结束时间（上海）",
                    "datetime-local",
                    default=now.strftime("%Y-%m-%dT%H:%M"),
                ),
                _field("attraction", "景点", "select", options=splunk.ATTRACTIONS),
                _field("target", "目标设备"),
                _field("mnemonic", "消息代码"),
                _field("keyword", "消息关键词"),
                _field(
                    "order",
                    "排序",
                    "select",
                    options=[
                        {"value": "desc", "label": "最新在前"},
                        {"value": "asc", "label": "最早在前"},
                    ],
                    default="desc",
                ),
                _field("operating_hours", "仅 07:00–22:00", "checkbox"),
                _field("limit", "每页数量", "number", min=1, max=200, default=50),
                _field("offset", "记录偏移", "number", min=0, default=0),
            ],
        ),
        _action(
            "downtime.search",
            "停机事件",
            [
                *incident_filters,
                _field("limit", "结果上限", "number", min=1, max=500, default=50),
                _field("offset", "记录偏移", "number", min=0, default=0),
            ],
        ),
        _action("downtime.dashboard", "停机统计", insight_filters),
        _action("downtime.history", "设备故障履历", insight_filters),
        _action(
            "downtime.detail",
            "停机工单详情",
            [_field("workorderid", "工单 ID", required=True), source],
            True,
        ),
        _action(
            "downtime.splunk",
            "停机时段日志",
            [
                _field("location", "设备位置"),
                _field("attraction_name_en", "景点英文名称"),
                _field("downtime_start_local", "停机时间"),
                _field("downtime_end_local", "恢复时间"),
                _field("report_local", "报告时间"),
                _field("snapshot_end_local", "截止时间"),
                _field(
                    "padding_minutes",
                    "前后缓冲分钟",
                    "number",
                    min=0,
                    max=240,
                    default=15,
                ),
                _field("limit", "每页数量", "number", min=1, max=200, default=50),
                _field("offset", "记录偏移", "number", min=0, default=0),
            ],
            True,
        ),
        *[
            _action(service + ".probe", "检查实时连接")
            for service in ("maximo", "splunk", "downtime")
        ],
    ]


def catalog():
    now = datetime.now(splunk.TZ)
    try:
        coverage = cache.coverage()
    except (cache.CacheMiss, sqlite3.Error, OSError):
        coverage = {"empty": True}
    last_day = (
        date.fromisoformat(coverage["data_through"])
        if coverage.get("data_through")
        else now.date()
    )
    oracle_ready = bool(option("DOWNTIME_DB_USER") and option("DOWNTIME_DB_PASSWORD"))
    return {
        "sources": [
            {"id": "maximo", "label": "Maximo", "configured": oracle_ready},
            {
                "id": "splunk",
                "label": "Splunk",
                "configured": bool(splunk.configured()),
            },
            {
                "id": "downtime",
                "label": "Downtime",
                "configured": oracle_ready,
                "coverage": coverage,
            },
        ],
        "actions": _actions(now, last_day),
    }


def _check_fields(action, params):
    now = datetime.now(splunk.TZ)
    spec = next(
        (item for item in _actions(now, now.date()) if item["id"] == action), None
    )
    if spec is None:
        raise SourceError("不支持此查询操作。")
    if not isinstance(params, dict):
        raise SourceError("查询参数必须是对象。")
    allowed = {f["name"]: f for f in spec["fields"]}
    if any(k not in allowed for k in params):
        raise SourceError("含有此操作不支持的参数。")
    values = {}
    for key, value in params.items():
        field = allowed[key]
        kind = field["type"]
        if value is None or value == "":
            continue
        if kind == "multiselect":
            if (
                not isinstance(value, list)
                or len(value) > 50
                or not all(isinstance(v, str) and len(v) <= 80 for v in value)
            ):
                raise SourceError(f"{field['label']} 的多选值无效。")
            if not value:
                continue
        elif kind == "checkbox":
            if not isinstance(value, bool):
                raise SourceError(f"{field['label']} 须为布尔值。")
        elif kind == "number":
            if (
                isinstance(value, bool)
                or not str(value).isascii()
                or not str(value).isdigit()
                or len(str(value)) > 10
            ):
                raise SourceError(f"{field['label']} 须为非负整数。")
            value = int(value)
        elif (
            not isinstance(value, str)
            or len(value) > 256
            or any(ord(char) < 32 for char in value)
        ):
            raise SourceError(f"{field['label']} 无效。")
        if "options" in field:
            options = {option["value"] for option in field["options"]}
            if any(
                v not in options
                for v in (value if isinstance(value, list) else [value])
            ):
                raise SourceError(f"请选择有效的{field['label']}。")
        values[key] = value
    for field in spec["fields"]:
        if field.get("required") and not values.get(field["name"]):
            raise SourceError(f"请填写{field['label']}。")
    return values


def _cache_detail(params):
    identifier = insights._integer(params.get("workorderid"), "工单 ID", 1, 10**18)
    with closing(cache._connect()) as conn:
        row = conn.execute(
            "SELECT * FROM workorders WHERE workorderid=?", (identifier,)
        ).fetchone()
    if not row:
        raise SourceError("此工单不在已导入的停机记录中。", 404)
    return {
        "workorder": insights.finish_items([dict(row)])[0],
        "source": "已导入停机记录",
        "warnings": ["任务、状态变更及关联工单请切换至实时 Maximo 查看。"],
    }


def _events_result(result, offset, limit):
    total = result["total"]
    for key in ("page", "page_size"):
        result.pop(key, None)
    count = len(result["items"])
    result.update(mode="events", count=count, offset=offset, limit=limit,
                  has_more=offset + count < total, truncated=total > count)
    return result


def _downtime(action, params, export=False):
    local = params.pop("source", "cache") == "cache"
    if action == "downtime.detail":
        return _cache_detail(params) if local else insights.detail(params)
    kind, key = params.pop("drill_kind", ""), params.pop("drill_key", "")
    if kind or key:
        params["drill"] = {"kind": kind, "key": key}
    if "end_date" not in params:
        last = cache.coverage().get("data_through") if local else None
        params["end_date"] = last or datetime.now(splunk.TZ).date().isoformat()
    if "start_date" not in params:
        params["start_date"] = (
            date.fromisoformat(params["end_date"]) - timedelta(days=6)
        ).isoformat()
    history = action == "downtime.history"
    if action == "downtime.search":
        limit, offset = params.pop("limit", 50), params.pop("offset", 0)
        if export:
            limit, offset = EXPORT_CAP, 0
        elif not 1 <= limit <= 500:
            raise SourceError("结果上限须在 1–500 以内。")
        if local:
            return _events_result(cache.search(params, event_limit=limit, event_offset=offset), offset, limit)
        if params.get("drill"):
            # The fixed live event query has no chart drill-down; the aggregate query does.
            page = dict(params, page=offset // limit + 1, page_size=limit)
            return _events_result(insights.search(page, max_page_size=max(limit, 100), compare=False), offset, limit)
        result = db.search_downtime(dict(params, limit=limit, offset=offset),
                                    max_results=EXPORT_CAP if export else db.MAX_RESULTS)
        # Same scope as the fixed event query (it always excludes history records).
        summary = insights.summary_only(dict(params, include_history=False))
        result.update(summary=summary, total=summary["totals"]["event_count"], complete_aggregation=True)
        return _events_result(result, offset, limit)
    if export:
        params.pop("page_size", None)
        params["page"] = 1
        if local:
            return cache.search(params, history, event_limit=EXPORT_CAP, event_offset=0)
        return insights.search(dict(params, page_size=EXPORT_CAP), history, max_page_size=EXPORT_CAP, compare=False)
    search = cache.search if local else insights.search
    return search(params, history=history)


def _export_values(action, values):
    if action not in EXPORTABLE:
        raise SourceError("此查询不支持导出。")
    if action.startswith("maximo.") or action == "splunk.search":
        return {**values, "limit": EXPORT_CAP, "offset": 0}
    return values


def _run_direct(action, params, export=False):
    try:
        values = _check_fields(action, params)
        if export:
            values = _export_values(action, values)
        if action.endswith(".probe"):
            if action == "splunk.probe":
                result = splunk.probe()
            else:
                result = db.probe_database()
                if not result["ok"]:
                    raise SourceError(result["message"], 503)
            return {
                "ok": True,
                "message": result["message"],
                "source": action.split(".")[0],
                "queried_at": datetime.now(splunk.TZ).isoformat(timespec="seconds"),
            }
        if action == "downtime.splunk":
            return splunk.linked_search(values)
        if action.startswith("downtime."):
            return _downtime(action, values, export)
        if export:
            caps = {"maximo.workorders": maximo.search_workorders, "maximo.jobplans": maximo.search_jobplans,
                    "splunk.search": splunk.search}
            return caps[action](values, max_limit=EXPORT_CAP)
        functions = {
            "maximo.workorders": maximo.search_workorders,
            "maximo.workorder": maximo.workorder_detail,
            "maximo.jobplans": maximo.search_jobplans,
            "maximo.jobplan": maximo.jobplan_detail,
            "splunk.search": splunk.search,
        }
        return functions[action](values)
    except cache.CacheMiss as exc:
        raise SourceError(str(exc), 409) from exc
    except (db.ConnectionUnavailable, splunk.SplunkUnavailable) as exc:
        raise SourceError(str(exc), 503) from exc
    except (OSError, sqlite3.Error) as exc:
        raise SourceError(
            "暂时无法读取工作台数据源，请检查本机连接配置。", 503
        ) from exc
    except ValueError as exc:
        raise SourceError(str(exc)) from exc
    except OverflowError as exc:
        raise SourceError("查询值超出范围。") from exc


_NETWORK_SLOTS = BoundedSemaphore(4)


def run_action(action, params, *, export=False):
    """Serve mirror pages locally. Run fixed live operations in a separate process.

    The RAG process restricts all socket connections. The isolated worker only
    accepts catalog operations and resolves endpoints from server configuration.
    """
    values = _check_fields(action, params)
    if export and action not in EXPORTABLE:
        raise SourceError("此查询不支持导出。")
    if (
        action
        in (
            "downtime.search",
            "downtime.dashboard",
            "downtime.history",
            "downtime.detail",
        )
        and values.get("source", "cache") == "cache"
    ):
        return _run_direct(action, values, export)
    if not _NETWORK_SLOTS.acquire(blocking=False):
        raise SourceError("实时查询正在处理中，请稍后重试。", 429)
    try:
        env = child_environment()
        env.update(
            (k, v)
            for k, v in os.environ.items()
            if k == "JAVA_HOME" or k.startswith("OPERATIONS_")
        )
        env["OPERATIONS_RUNTIME_DIR"] = str(runtime_dir().resolve())
        result = subprocess.run(
            [sys.executable, "-m", "operations_workbench.source_worker"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            input=json.dumps({"action": action, "params": values, **({"export": True} if export else {})}),
            text=True,
            capture_output=True,
            timeout=125,
            check=False,
        )
        if result.returncode or len(result.stdout) > 32 * 1024 * 1024:
            raise SourceError("数据源查询未完成，请缩小范围后重试。", 503)
        reply = json.loads(result.stdout)
        if not reply["ok"]:
            raise SourceError(reply["message"], reply["status"])
        return reply["data"]
    except subprocess.TimeoutExpired as exc:
        raise SourceError("数据源查询超时，请缩小范围后重试。", 504) from exc
    except (OSError, ValueError, KeyError) as exc:
        raise SourceError("当前数据源连接不可用，请稍后重试。", 503) from exc
    finally:
        _NETWORK_SLOTS.release()


def export(action, params):
    """Full result rows for CSV, up to EXPORT_CAP, with the exact match count."""
    if action not in EXPORTABLE:
        raise SourceError("此查询不支持导出。")
    data = run_action(action, params, export=True)
    items = data.get("items") if isinstance(data.get("items"), list) else []
    total = data.get("total")
    if not isinstance(total, int):
        total = len(items)
    return {"action": action, "items": items[:EXPORT_CAP], "exported": min(len(items), EXPORT_CAP),
            "total": total, "cap": EXPORT_CAP, "capped": total > EXPORT_CAP,
            "queried_at": data.get("queried_at")}


def render_pdf(kind, code, lang="CN"):
    from .vendor.maximo_pdf import jobplan_pdf, workorder_pdf

    try:
        lang = maximo.validate_lang(lang)
        if kind == "jobplan":
            detail = run_action(
                "maximo.jobplan", {"jpnum": code, "include_workorders": False}
            )
            return jobplan_pdf(detail, lang)
        if kind != "workorder":
            raise SourceError("不支持的报表类型。")
        detail = run_action("maximo.workorder", {"wonum": code})
        jp = None
        if not detail.get("tasks") and detail.get("workorder", {}).get("jpnum"):
            try:
                jp = run_action(
                    "maximo.jobplan",
                    {
                        "jpnum": detail["workorder"]["jpnum"],
                        "include_workorders": False,
                    },
                )
            except SourceError:
                pass
        return workorder_pdf(detail, lang, jp)
    except db.DowntimeSearchError as exc:
        raise SourceError(str(exc)) from exc
