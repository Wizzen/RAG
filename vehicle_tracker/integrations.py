"""Read-only QAQC and Maximo records for the tracker, served by the RAG's own modules.

QAQC / MRB records come from the snapshot imported into the operations
workbench runtime. Work orders, job plans and work-order PDFs use the
workbench's read-only Maximo connection. No external ToolBox service is used.
"""
from __future__ import annotations

import re

from operations_workbench import sources
from operations_workbench.vendor import qaqc_cache

CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{2,39}\Z")
DESTINATIONS = ("maximo-workorder", "qaqc-workorder", "workorder-pdf", "qaqc-part", "job-plan")


class ToolboxUnavailable(Exception):
    """The requested record source cannot currently be used."""


def validate_code(value) -> str:
    if not isinstance(value, str) or not CODE.fullmatch(value):
        raise ValueError("编号只能包含字母、数字和短横线（3–40 位）。")
    return value


def part_detail(code: str) -> tuple[dict, int]:
    try:
        return qaqc_cache.part_detail(validate_code(code)), 200
    except qaqc_cache.QaqcMiss as exc:
        return {"detail": str(exc)}, 400
    except qaqc_cache.QaqcUnavailable as exc:
        raise ToolboxUnavailable(str(exc)) from exc


def _qaqc_workorder(code):
    try:
        return qaqc_cache.workorder_detail(code)
    except qaqc_cache.QaqcMiss as exc:
        raise ToolboxUnavailable(str(exc)) from exc
    except qaqc_cache.QaqcUnavailable as exc:
        raise ToolboxUnavailable(str(exc)) from exc


def _maximo(action, params):
    try:
        return sources.run_action(action, params)
    except sources.SourceError as exc:
        raise ToolboxUnavailable(str(exc)) from exc


def record_detail(kind, code):
    code = validate_code(code)
    if kind == "qaqc-workorder":
        return {"data": _qaqc_workorder(code), "source": "QAQC 本地同步记录", "notice": ""}
    if kind == "job-plan":
        return {"data": _maximo("maximo.jobplan", {"jpnum": code, "include_workorders": False}),
                "source": "Maximo只读查询", "notice": ""}
    if kind != "maximo-workorder":
        raise ValueError("不支持的资料类型。")
    try:
        return {"data": _maximo("maximo.workorder", {"wonum": code}), "source": "Maximo只读查询", "notice": ""}
    except ToolboxUnavailable as live:
        try:
            data = _qaqc_workorder(code)
        except ToolboxUnavailable:
            raise live from None
        return {"data": data, "source": "QAQC 本地同步记录", "notice": "实时Maximo暂不可用，以下为已同步的工单资料。"}


def workorder_pdf(code):
    code = validate_code(code)
    try:
        content = sources.render_pdf("workorder", code, "CN")
    except sources.SourceError as exc:
        raise ToolboxUnavailable(f"工单PDF暂不可用：{exc}") from exc
    if len(content) > 20 * 1024 * 1024:
        raise ToolboxUnavailable("此PDF超过20MB页内预览上限。")
    return content
