"""Direct, structured Splunk Apex queries using the portal's fixed index and sourcetype."""

import json
import re
import ssl
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .runtime import option
from .vendor import downtime_apex as apex

TZ = ZoneInfo("Asia/Shanghai")
ATTRACTIONS = apex._load_apex_attractions()
KNOWN_ATTRACTIONS = {row["value"] for row in ATTRACTIONS}
EVENT_FIELDS = (
    "_time",
    "apexmssgdatetime",
    "attrname",
    "apexmssgtarget",
    "apexmssgmnemonic",
    "apexmssgtext",
)
RAW_FIELDS = re.compile(
    r'\b(apexmssgdatetime|attrname|apexmssgtarget|apexmssgmnemonic|apexmssgtext)="((?:\\.|[^"\\])*)"'
)
MAX_RESPONSE = 8 * 1024 * 1024


class SplunkUnavailable(Exception):
    pass


def configured():
    return all(
        option(key)
        for key in ("SPLUNK_HOST", "SPLUNK_PORT", "SPLUNK_USER", "SPLUNK_PASSWORD")
    )


def _request(method, path, **kwargs):
    if not configured():
        raise SplunkUnavailable("尚未配置 Splunk 只读连接。")
    host, port = option("SPLUNK_HOST"), option("SPLUNK_PORT")
    if (
        not re.fullmatch(r"[a-zA-Z0-9.-]+", host)
        or not port.isdigit()
        or not 1 <= int(port) <= 65535
    ):
        raise SplunkUnavailable("Splunk 服务器地址或端口配置无效。")
    try:
        tls = ssl.create_default_context(cafile=option("SPLUNK_CA_BUNDLE") or None)
        with httpx.Client(
            auth=(option("SPLUNK_USER"), option("SPLUNK_PASSWORD")),
            verify=tls,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(45, connect=5),
        ) as client, client.stream(
            method, f"https://{host}:{port}{path}", **kwargs
        ) as response:
            if response.status_code in (401, 403):
                raise SplunkUnavailable(
                    "Splunk 拒绝访问，请核对连接账号及查询权限。"
                )
            if response.status_code != 200 and response.status_code != 201:
                raise SplunkUnavailable(
                    f"Splunk 查询服务返回 HTTP {response.status_code}。"
                )
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE:
                    raise SplunkUnavailable(
                        "查询结果过大，请缩小时间范围或每页数量。"
                    )
        data = json.loads(body)
    except (httpx.HTTPError, OSError) as exc:
        raise SplunkUnavailable(
            "无法连接 Splunk，请检查公司网络、VPN 及服务器证书配置。"
        ) from exc
    except (UnicodeError, ValueError) as exc:
        raise SplunkUnavailable("Splunk 返回了无法读取的数据。") from exc
    if not isinstance(data, dict):
        raise SplunkUnavailable("Splunk 未返回预期的查询结果。")
    return data


def probe():
    data = _request("GET", "/services/server/info", params={"output_mode": "json"})
    if not isinstance(data.get("entry"), list) or not data["entry"]:
        raise SplunkUnavailable("未能确认 Splunk 服务端状态。")
    return {"ok": True, "message": "Splunk 服务已响应，账号验证成功。"}


def _quoted(value):
    if (
        not isinstance(value, str)
        or len(value) > 120
        or re.search(r"[\x00-\x1f\x7f`|\[\]$]", value)
    ):
        raise ValueError("日志筛选值最多 120 字符，不能包含查询命令或控制字符。")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _instant(value):
    try:
        instant = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("请输入有效的日志起止时间。") from exc
    return (
        instant.replace(tzinfo=TZ) if instant.tzinfo is None else instant.astimezone(TZ)
    )


def _number(value, minimum, maximum, label):
    if not re.fullmatch(r"\d{1,8}", str(value)) or not minimum <= int(value) <= maximum:
        raise ValueError(f"{label}须为 {minimum}–{maximum} 的整数。")
    return int(value)


def _events(filters, earliest, latest, attractions):
    if latest <= earliest or latest - earliest > timedelta(days=31):
        raise ValueError("日志结束时间需晚于开始时间，最多可查 31 天。")
    limit = _number(filters.get("limit", 50), 1, 200, "每页数量")
    offset = _number(filters.get("offset", 0), 0, 1000000, "记录偏移")
    if any(name not in KNOWN_ATTRACTIONS for name in attractions):
        raise ValueError("请选择目录中的有效 Splunk 景点。")
    terms = [apex.BASE_SEARCH]
    if attractions:
        terms.append(
            "(" + " OR ".join("attrname=" + _quoted(a) for a in attractions) + ")"
        )
    for field, name in [("apexmssgtarget", "target"), ("apexmssgmnemonic", "mnemonic")]:
        value = filters.get(name)
        if value:
            if not isinstance(value, str) or len(value) > 240:
                raise ValueError("日志过滤条件格式无效。")
            values = [v.strip() for v in value.split(",") if v.strip()]
            if not 1 <= len(values) <= 12:
                raise ValueError("每项筛选最多 12 个值，以逗号分隔。")
            terms.append(
                "(" + " OR ".join(field + "=" + _quoted(v) for v in values) + ")"
            )
    keyword = filters.get("keyword", "")
    if keyword:
        _quoted(keyword)
        terms.append(
            "apexmssgtext="
            + _quoted(keyword if "*" in keyword else "*" + keyword + "*")
        )
    order = filters.get("order", "desc")
    if order not in ("asc", "desc"):
        raise ValueError("日志排序方式无效。")
    direction = "+" if order == "asc" else "-"
    terms.append(
        '| eval _apex_sort=coalesce(strptime(apexmssgdatetime,"%m-%d-%Y %H:%M:%S.%3N"),_time)'
    )
    if filters.get("operating_hours") is True:
        # Unix epoch projected into Shanghai local day, independent of the account timezone.
        terms.append(
            "| eval _op_sec=(_apex_sort+28800)%86400 | where _op_sec>=25200 AND _op_sec<79200"
        )
    terms.append(
        f"| sort 0 {direction}_apex_sort {direction}_time | head {offset + limit + 1}"
    )
    if offset:
        terms.append(f"| streamstats count as _ops_row | where _ops_row>{offset}")
    terms.append("| fields " + " ".join((*EVENT_FIELDS, "_raw")))
    data = _request(
        "POST",
        "/services/search/jobs",
        data={
            "search": " ".join(terms),
            "earliest_time": str(int(earliest.timestamp())),
            "latest_time": str(int(latest.timestamp())),
            "exec_mode": "oneshot",
            "output_mode": "json",
            "count": str(limit + 1),
        },
    )
    rows = data.get("results")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise SplunkUnavailable("Splunk 返回结果格式异常。")
    if any(
        isinstance(message, dict)
        and str(message.get("type")).upper() in ("ERROR", "FATAL")
        for message in (data.get("messages") or [])
    ):
        raise SplunkUnavailable("Splunk 未能完成本次查询，请核对账号权限及查询条件。")
    items = []
    for event in rows[:limit]:
        raw = str(event.get("_raw") or "")
        fields = {
            key: re.sub(r"\\(.)", r"\1", value)
            for key, value in RAW_FIELDS.findall(raw)
        }
        row = {
            key: str(event.get(key) or fields.get(key) or "")[:16000]
            for key in EVENT_FIELDS
        }
        if not row["apexmssgtext"]:
            row["apexmssgtext"] = raw[:16000]
        items.append(row)
    return {
        "items": items,
        "count": len(items),
        "limit": limit,
        "offset": offset,
        "has_more": len(rows) > limit,
        "source": "Splunk Apex · 实时",
        "queried_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "start_time": earliest.isoformat(),
        "end_time": latest.isoformat(),
    }


def search(params):
    now = datetime.now(TZ)
    start = _instant(params.get("start_time") or (now - timedelta(hours=1)).isoformat())
    end = _instant(params.get("end_time") or now.isoformat())
    attraction = params.get("attraction")
    return _events(params, start, end, [attraction] if attraction else [])


def linked_search(params):
    start, end, window = apex.build_apex_window(params)
    attractions = apex.resolve_apex_attrnames(
        location=params.get("location"),
        attraction_name_en=params.get("attraction_name_en"),
    )
    if not attractions:
        raise ValueError("此停机记录未匹配到日志景点。")
    result = _events(
        params,
        datetime.fromtimestamp(start, TZ),
        datetime.fromtimestamp(end, TZ),
        attractions,
    )
    result.update(window=window, attractions=attractions)
    return result
