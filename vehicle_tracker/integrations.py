"""Optional, read-only bridge to the original Engineering ToolBox service.

VEHICLE_TRACKER_TOOLBOX_URL configures its base address. Optional
VEHICLE_TRACKER_TOOLBOX_USERNAME / PASSWORD authenticate the server-side QAQC
read. No Django cookies or project files are forwarded to that service.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlencode, urlsplit

import httpx
from django.conf import settings

CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{2,39}\Z")
DESTINATIONS = {
    "maximo-workorder": ("/tools/maximo", "wo"),
    "qaqc-workorder": ("/tools/qaqc", "workorders"),
    "workorder-pdf": ("/api/maximo/workorders/pdf", None),
    "qaqc-part": ("/tools/qaqc", "parts"),
    "job-plan": ("/tools/maximo", "jp"),
}


class ToolboxUnavailable(Exception):
    """The optional source service cannot currently be used."""


def option(name: str) -> str:
    return str(getattr(settings, name, os.environ.get(name, ""))).strip()


def validate_code(value) -> str:
    if not isinstance(value, str) or not CODE.fullmatch(value):
        raise ValueError("编号只能包含字母、数字和短横线（3–40 位）。")
    return value


def toolbox_url() -> str:
    value = option("VEHICLE_TRACKER_TOOLBOX_URL").rstrip("/")
    if not value:
        raise ToolboxUnavailable(
            "当前工作区尚未连接工程工具箱。三维、追踪表和本地项目仍可使用；"
            "QAQC / Maximo 查询需要先配置原工具箱地址。"
        )
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ToolboxUnavailable("工程工具箱连接地址配置无效。")
    return value


def destination_url(destination: str, code: str) -> str:
    path, tab = DESTINATIONS[destination]
    code = validate_code(code)
    if destination == "workorder-pdf":
        query = {"wonum": code, "lang": "CN"}
    else:
        prefix = {"parts": "part", "jp": "jp"}.get(tab, "wo")
        query = {"tab": tab, "open": f"{prefix}:{code}"}
    return f"{toolbox_url()}{path}?{urlencode(query)}"


def part_detail(code: str) -> tuple[dict, int]:
    url = toolbox_url() + "/api/qaqc/parts/detail"
    username = option("VEHICLE_TRACKER_TOOLBOX_USERNAME")
    password = option("VEHICLE_TRACKER_TOOLBOX_PASSWORD")
    try:
        response = httpx.post(
            url, json={"part_no": validate_code(code)},
            auth=(username, password) if username else None,
            timeout=8, follow_redirects=False, trust_env=False,
        )
    except httpx.RequestError as exc:
        raise ToolboxUnavailable("无法连接工程工具箱，请确认原服务已启动后重试。") from exc
    if response.status_code in {401, 403}:
        raise ToolboxUnavailable("工程工具箱要求身份验证，请检查此连接的访问配置。")
    if response.status_code not in {200, 400, 409}:
        raise ToolboxUnavailable("工程工具箱暂时无法提供 QAQC 数据，请稍后重试。")
    try:
        data = response.json()
    except ValueError as exc:
        raise ToolboxUnavailable("工程工具箱返回的 QAQC 数据格式无效。") from exc
    if not isinstance(data, dict):
        raise ToolboxUnavailable("工程工具箱返回的 QAQC 数据格式无效。")
    return data, response.status_code


def _record_json(path, payload):
    username = option("VEHICLE_TRACKER_TOOLBOX_USERNAME")
    password = option("VEHICLE_TRACKER_TOOLBOX_PASSWORD")
    try:
        response = httpx.post(toolbox_url() + path, json=payload,
                              auth=(username, password) if username else None,
                              timeout=8, follow_redirects=False, trust_env=False)
        if response.status_code != 200:
            raise ToolboxUnavailable("暂未取得该工单或作业计划，请连接公司网络后重试。")
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (httpx.HTTPError, ValueError) as exc:
        raise ToolboxUnavailable("暂未取得该工单或作业计划，请稍后重试。") from exc


def record_detail(kind, code):
    code = validate_code(code)
    routes = {
        "maximo-workorder": ("/api/maximo/workorders/detail", {"wonum": code}),
        "qaqc-workorder": ("/api/qaqc/workorders/detail", {"wonum": code}),
        "job-plan": ("/api/maximo/jobplans/detail", {"jpnum": code, "include_workorders": False}),
    }
    if kind not in routes:
        raise ValueError("不支持的资料类型。")
    try:
        data = _record_json(*routes[kind])
        return {"data": data, "source": "工具箱本地同步记录" if kind == "qaqc-workorder" else "Maximo只读查询", "notice": ""}
    except ToolboxUnavailable:
        if kind != "maximo-workorder":
            raise
        data = _record_json(*routes["qaqc-workorder"])
        return {"data": data, "source": "工具箱本地同步记录", "notice": "实时Maximo暂不可用，以下为已同步的工单资料。"}


def workorder_pdf(code):
    code = validate_code(code)
    username = option("VEHICLE_TRACKER_TOOLBOX_USERNAME")
    password = option("VEHICLE_TRACKER_TOOLBOX_PASSWORD")
    try:
        with httpx.stream("GET", toolbox_url() + "/api/maximo/workorders/pdf", params={"wonum": code, "lang": "CN"},
                          auth=(username, password) if username else None, timeout=15,
                          follow_redirects=False, trust_env=False) as response:
            if response.status_code != 200 or "application/pdf" not in response.headers.get("content-type", ""):
                raise ToolboxUnavailable("工单原PDF暂不可用，请连接公司网络后重试。当前项目的追踪记录仍可在本页查看。")
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 20 * 1024 * 1024:
                    raise ToolboxUnavailable("此PDF超过20MB页内预览上限。")
        if not content.startswith(b"%PDF-"):
            raise ToolboxUnavailable("返回资料不是有效PDF。")
        return bytes(content)
    except httpx.HTTPError as exc:
        raise ToolboxUnavailable("工单PDF连接失败，请稍后重试。") from exc
