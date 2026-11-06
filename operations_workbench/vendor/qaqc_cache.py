"""Read-only QAQC / MRB lookups over the imported Engineering ToolBox snapshot.

Ported from the ToolBox ``qaqc.part_detail`` / ``qaqc.workorder_detail``. The
snapshot is refreshed by ``operations_workbench.import_sources``; SharePoint
report files are not part of the snapshot.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..runtime import runtime_dir
from .downtime_catalog import ATTRACTION_BY_LOCATION
from .qaqc_parts import DISPOSITION_LABELS, comment_meta, is_engineer_task, part_attraction

TZ = ZoneInfo("Asia/Shanghai")
SNAPSHOT = "qaqc.sqlite3"
_LIST_COLUMNS = ("workorderid, wonum, status, worktype, failurecode, origrecordid, description, description_en, "
                 "report_local, report_day, actfinish_local, part_no, part_nos, part_source, attraction_code, role, "
                 "is_mrb, has_mrb, case_root, disposition, comment_count, own_comment, report_count, location, assetnum")
_EVENT_RATE = "COUNT(*) AS inspections, SUM(mrb) AS mrb_inspections, SUM(case_count) AS mrb_cases"


class QaqcMiss(LookupError):
    """The snapshot has no matching record."""


class QaqcUnavailable(Exception):
    """The snapshot has not been imported or cannot be read."""


def _connect() -> sqlite3.Connection:
    path = runtime_dir() / SNAPSHOT
    if not path.is_file():
        raise QaqcUnavailable("尚未导入 QAQC 本地记录，请运行 operations_workbench.import_sources。")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _attraction_label(code: str | None) -> dict[str, str]:
    item = ATTRACTION_BY_LOCATION.get(code or "")
    if item:
        return {"code": code, "name_zh": item["name_zh"], "name_en": item["name_en"]}
    return {"code": code or "", "name_zh": code or "未识别", "name_en": code or "Unknown"}


def _finish(row: dict[str, Any]) -> dict[str, Any]:
    row["attraction"] = _attraction_label(row.pop("attraction_code", None))
    row["part_nos"] = json.loads(row["part_nos"]) if row.get("part_nos") else []
    row["disposition_label"] = DISPOSITION_LABELS.get(row.get("disposition") or "", "")
    return row


def _with_rate(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("inspections", "mrb_inspections", "mrb_cases"):
        row[key] = row.get(key) or 0
    row["mrb_rate"] = round(row["mrb_inspections"] / row["inspections"], 4) if row["inspections"] else None
    return row


def _data_through(connection: sqlite3.Connection) -> str | None:
    row = connection.execute("SELECT MAX(synced_through) FROM months").fetchone()
    return row[0] if row else None


def _task_list(connection: sqlite3.Connection, wonum: str) -> list[dict[str, Any]]:
    tasks = [dict(r) for r in connection.execute(
        "SELECT * FROM tasks WHERE parent = ? ORDER BY CAST(taskid AS INTEGER), wonum", (wonum,))]
    for task in tasks:
        task.update(comment_meta(task["long_text"]), engineer=is_engineer_task(task["description"]))
    return tasks


def validate_wonum(value: Any) -> str:
    wonum = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3,20}", wonum):
        raise ValueError("工单号格式无效。")
    return wonum


def part_detail(part_no: Any) -> dict[str, Any]:
    part = str(part_no or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{2,39}", part):
        raise ValueError("零件号格式无效。")
    exact = _like(f'"{part}"')
    try:
        with closing(_connect()) as connection:
            rows = [_finish(dict(r)) for r in connection.execute(
                f"""SELECT {_LIST_COLUMNS} FROM workorders WHERE part_nos LIKE ? ESCAPE '\\'
                       OR wonum IN (SELECT wonum FROM part_events WHERE part_no = ?)
                    ORDER BY report_local DESC LIMIT 500""", (exact, part))]
            if not rows:
                raise QaqcMiss(f"QAQC 本地记录中没有零件号 {part}。")
            years = [_with_rate(dict(r)) for r in connection.execute(
                f"""SELECT substr(report_day, 1, 4) AS bucket, {_EVENT_RATE} FROM part_events
                    WHERE part_no = ? GROUP BY bucket ORDER BY bucket""", (part,))]
            totals = _with_rate(dict(connection.execute(
                f"SELECT {_EVENT_RATE} FROM part_events WHERE part_no = ?", (part,)).fetchone()))
            through = _data_through(connection)
    except sqlite3.Error as exc:
        raise QaqcUnavailable("QAQC 本地记录无法读取，请重新导入。") from exc
    dispositions: dict[str, int] = {}
    for row in rows:
        if row["is_mrb"]:
            key = row["disposition"] or "none"
            dispositions[key] = dispositions.get(key, 0) + 1
    code = part_attraction(part)
    return {"mode": "part_detail", "queried_at": _now(), "data_through": through, "part_no": part,
            "attraction": _attraction_label(code) if code else rows[0]["attraction"],
            "totals": totals, "years": years, "workorders": rows,
            "dispositions": [{"disposition": k, "label": DISPOSITION_LABELS.get(k, "未识别" if k == "other" else "无意见"),
                              "count": v} for k, v in sorted(dispositions.items(), key=lambda item: -item[1])]}


def workorder_detail(wonum: Any) -> dict[str, Any]:
    wonum = validate_wonum(wonum)
    try:
        with closing(_connect()) as connection:
            row = connection.execute("SELECT * FROM workorders WHERE wonum = ?", (wonum,)).fetchone()
            if not row:
                raise QaqcMiss(f"QAQC 本地记录中没有工单 {wonum}。")
            record = _finish(dict(row))
            record.update(comment_meta(record.get("long_text")), tasks=_task_list(connection, wonum))
            origin = connection.execute(f"SELECT {_LIST_COLUMNS}, long_text FROM workorders WHERE wonum = ?",
                                        (record.get("origrecordid") or "",)).fetchone()
            related = [_finish(dict(r)) for r in connection.execute(
                f"SELECT {_LIST_COLUMNS}, long_text FROM workorders WHERE origrecordid = ? AND wonum <> ? "
                "ORDER BY report_local DESC", (wonum, wonum))]
            for item in related:
                item["tasks"] = _task_list(connection, item["wonum"])
            through = _data_through(connection)
    except sqlite3.Error as exc:
        raise QaqcUnavailable("QAQC 本地记录无法读取，请重新导入。") from exc
    return {"mode": "workorder_detail", "queried_at": _now(), "data_through": through, "workorder": record,
            "origin": _finish(dict(origin)) if origin else None, "followups": related}
