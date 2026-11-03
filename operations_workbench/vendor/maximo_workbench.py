"""Read-only Maximo workbench: work-order search, job-plan search and detail views."""

from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from time import monotonic
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from .downtime_catalog import ATTRACTIONS, attraction_for_location
from .downtime_search import DowntimeSearchError, _execute, database_status

SITEID = "SDLR"
ORGID = "WDPR"
TIMEZONE = "Asia/Shanghai"

MAX_DATE_RANGE_DAYS = 366
MAX_WO_RESULTS = 500
DEFAULT_WO_RESULTS = 200
MAX_JP_RESULTS = 200
DEFAULT_JP_RESULTS = 100
MAX_JP_WO_RESULTS = 100
DEFAULT_JP_WO_RESULTS = 50
MAX_KEYWORD_LEN = 40
MAX_CODE_LEN = 30
MIN_WILDCARD_LITERALS = 2

LANGS = ("CN", "EN")
LANG_TO_MAXIMO = {"CN": "ZH", "EN": "EN"}

_CODE_PATTERN = re.compile(r"^[A-Z0-9*][A-Z0-9_\-./*]*$")

WORKTYPE_OPTIONS = [
    {"value": "PM", "label": "PM · Preventive Maintenance"},
    {"value": "RC", "label": "RC · Ride Critical"},
    {"value": "EM", "label": "EM · Emergency Work"},
    {"value": "PC", "label": "PC · Planned Corrective"},
    {"value": "UCH", "label": "UCH · Unplanned Corrective High"},
    {"value": "UCL", "label": "UCL · Unplanned Corrective Low"},
    {"value": "FC", "label": "FC · Facility Critical"},
    {"value": "INS", "label": "INS · Inspection"},
    {"value": "INSPECTION", "label": "INSPECTION · Inspection WO"},
    {"value": "CAL", "label": "CAL · Calibration"},
    {"value": "PRJ", "label": "PRJ · Project Work"},
    {"value": "SERV", "label": "SERV · Service"},
    {"value": "REPAIR", "label": "REPAIR · Repair"},
    {"value": "OVERHAUL", "label": "OVERHAUL · Overhaul"},
    {"value": "SR", "label": "SR · Central Shops Routine"},
    {"value": "SC", "label": "SC · Central Shops Cycle"},
    {"value": "SE", "label": "SE · Central Shops Emergency"},
    {"value": "TR", "label": "TR · Training"},
    {"value": "MIS", "label": "MIS · Miscellaneous"},
    {"value": "ADM", "label": "ADM · Administrative"},
    {"value": "DELI", "label": "DELI · Delivery"},
]

WOSTATUS_OPTIONS = [
    {"value": "WAPPR", "label": "WAPPR · 待批准"},
    {"value": "APPR", "label": "APPR · 已批准"},
    {"value": "WSCH", "label": "WSCH · 待排程"},
    {"value": "WMATL", "label": "WMATL · 待物料"},
    {"value": "WPCOND", "label": "WPCOND · 待工况"},
    {"value": "INPRG", "label": "INPRG · 进行中"},
    {"value": "COMP", "label": "COMP · 已完成"},
    {"value": "AUDIT", "label": "AUDIT · 待审核"},
    {"value": "CCOMP", "label": "CCOMP · 强制完成"},
    {"value": "CLOSE", "label": "CLOSE · 已关闭"},
    {"value": "CAN", "label": "CAN · 已取消"},
]

PRIORITY_OPTIONS = [{"value": str(level), "label": f"P{level}"} for level in range(6)]

PERSONGROUP_OPTIONS = [
    "AES213", "HES211", "FES214", "CSBUDS", "ENTSDL", "CSSHOW", "CSF471", "HT0376",
    "CSFABR", "QA0475", "HT0372", "CSCYCL", "OFFSVC", "HT0375", "AUDIT1", "MPS215",
]

JP_STATUS_OPTIONS = ["ACTIVE", "INACTIVE", "DRAFT"]
JP_COMPLIANCE_OPTIONS = ["RIDELAW", "SAFETYRELATED", "REGULATORY", "WASH"]
JP_CRAFT_OPTIONS = [
    "TECH", "MECH", "ELEC", "STAGETCH", "QAINSP", "KITAM", "PLUMBAC", "DEC", "INSP",
    "DIVE", "PARADE", "WELD", "MPS", "MNGR", "IRRG", "PYRO", "FIRE", "QC", "PWS", "HVAC", "CARP",
]
JP_FREQ_OPTIONS = [
    "DAILY", "WEEKLY", "BI-WEEKLY", "03 WEEKS", "MONTHLY", "BI-MONTHLY", "QUARTERLY",
    "04 MONTHS", "06 MONTHS", "12 WEEKS", "24 WEEKS", "18 MONTHS", "ANNUAL", "YEARLY",
    "BI-ANNUAL", "BIENNIAL", "02 YEARS", "03 YEARS", "04 YEARS", "05 YEARS", "06 YEARS",
    "07 YEARS", "10 YEARS", "METER",
]
JP_TYPE_OPTIONS = [
    {"value": "PM", "label": "PM · 预防性维护"},
    {"value": "CM", "label": "CM · 纠正性维护"},
    {"value": "PC", "label": "PC · 计划维修"},
    {"value": "IN", "label": "IN · 检查"},
    {"value": "DI", "label": "DI"},
]
JP_ATTRACTION_OPTIONS = [
    "TRON", "DWAR", "PIRA", "PETE", "ZTHP", "MASP", "WOOD", "PRAD", "SOAR", "JEPA", "SLIN",
    "BUZZ", "CUPS", "DUBO", "POOH", "ROAR", "RCRA", "CARO", "CTRC", "WDTH", "ELTF", "ONCE",
    "ALIC", "TRPS",
]


def _ldtext(owner_table: str, key_expr: str, lang: str, alias: str) -> str:
    return f"""
       (SELECT ld.ldtext
          FROM maximo.longdescription ld
         WHERE ld.ldownertable = '{owner_table}'
           AND ld.ldkey = TO_CHAR({key_expr})
           AND ld.ldownercol = 'DESCRIPTION'
           AND ld.langcode = '{lang}'
           AND ROWNUM = 1) AS {alias}"""


def _l_desc(table: str, key_expr: str, alias: str) -> str:
    return f"""
       (SELECT l.description
          FROM maximo.{table} l
         WHERE l.ownerid = {key_expr}
           AND l.langcode = 'ZH'
           AND ROWNUM = 1) AS {alias}"""


def _local(column: str, alias: str, site_alias: str = "w") -> str:
    return f"TO_CHAR(maximo.wdw_utctosite({column}, {site_alias}.siteid), 'YYYY-MM-DD HH24:MI:SS') AS {alias}"


_SQL_WO_SEARCH = """
SELECT * FROM (
  SELECT w.wonum,
         w.workorderid,
         w.status,
         w.worktype,
         w.wopriority,
         w.location,
         w.assetnum,
         w.jpnum,
         w.pmnum,
         w.persongroup,
         w.description AS description_en,
         {desc_zh},
         {report_local},
         {actstart_local},
         {actfinish_local}
    FROM maximo.workorder w
   WHERE w.woclass = 'WORKORDER'
     AND w.istask = 0
     AND w.historyflag = 0
     AND w.siteid = :siteid
     AND w.orgid = :orgid
     AND maximo.wdw_utctosite(w.reportdate, w.siteid) >= TO_DATE(:start_date, 'YYYY-MM-DD')
     AND maximo.wdw_utctosite(w.reportdate, w.siteid) < TO_DATE(:end_date, 'YYYY-MM-DD')
     {filters}
   ORDER BY w.reportdate DESC, w.workorderid DESC
) WHERE ROWNUM <= :result_limit
"""

_SQL_WO_DETAIL = """
SELECT w.wonum,
       w.workorderid,
       w.status,
       w.worktype,
       w.wopriority,
       w.location,
       (SELECT l.description FROM maximo.locations l
         WHERE l.location = w.location AND l.siteid = w.siteid AND ROWNUM = 1) AS location_description,
       w.assetnum,
       (SELECT a.description FROM maximo.asset a
         WHERE a.assetnum = w.assetnum AND a.siteid = w.siteid AND ROWNUM = 1) AS asset_description,
       w.jpnum,
       w.pmnum,
       w.persongroup,
       w.supervisor,
       w.lead,
       w.crewid,
       w.cust_craft,
       w.cust_compliance,
       w.estdur,
       w.cust_wrenchtime,
       w.failurecode,
       w.problemcode,
       w.reportedby,
       w.parent,
       w.origrecordid,
       w.targstartdate,
       w.targcompdate,
       w.pmduedate,
       w.estlabhrs,
       w.cust_guestimpact,
       w.description AS description_en,
       {desc_zh},
       {ld_en},
       {ld_zh},
       {report_local},
       {schedstart_local},
       {schedfinish_local},
       {actstart_local},
       {actfinish_local},
       {targstart_local},
       {targcomp_local},
       {pmdue_local},
       {jp_desc_en},
       {jp_desc_zh}
  FROM maximo.workorder w
 WHERE w.wonum = :wonum
   AND w.siteid = :siteid
   AND w.orgid = :orgid
   AND w.istask = 0
"""

_SQL_WO_TASKS = """
SELECT t.taskid,
       t.wonum,
       t.status,
       t.estdur,
       t.jobtaskid,
       t.description AS description_en,
       {desc_zh},
       NVL({ld_en_wo}, {ld_en_task}) AS long_description_en,
       NVL({ld_zh_wo}, NVL({ld_zh_task}, {ld_en_wo})) AS long_description_zh,
       {actfinish_local}
  FROM maximo.wotask t
 WHERE t.parent = :wonum
   AND t.siteid = :siteid
   AND t.orgid = :orgid
 ORDER BY t.taskid
"""

_SQL_WO_STATUS = """
SELECT s.status,
       TO_CHAR(maximo.wdw_utctosite(s.changedate, s.siteid), 'YYYY-MM-DD HH24:MI:SS') AS changed_local,
       s.changeby,
       s.memo
  FROM maximo.wostatus s
 WHERE s.wonum = :wonum
   AND s.siteid = :siteid
   AND s.orgid = :orgid
 ORDER BY s.changedate
"""

_JP_PICK_REVISION = """
  (SELECT jobplanid FROM (
     SELECT p.jobplanid
       FROM maximo.jobplan p
      WHERE p.jpnum = :jpnum AND p.siteid = :siteid AND p.orgid = :orgid
      ORDER BY CASE WHEN p.status = 'ACTIVE' THEN 0 ELSE 1 END, p.pluscrevnum DESC, p.jobplanid DESC
   ) WHERE ROWNUM = 1)
"""

_SQL_JP_SEARCH = """
SELECT * FROM (
  SELECT ranked.*
    FROM (
      SELECT jp.jpnum,
             jp.jobplanid,
             jp.status,
             jp.pluscrevnum,
             jp.description,
             {jp_desc_zh},
             jp.cust_compliance,
             jp.cust_craft,
             jp.cust_jpfreq,
             jp.cust_jptype,
             jp.cust_attraction,
             jp.cust_wrenchtime,
             TO_CHAR(jp.pluscchangedate, 'YYYY-MM-DD HH24:MI') AS changedate_utc,
             ROW_NUMBER() OVER (
               PARTITION BY jp.jpnum
               ORDER BY CASE WHEN jp.status = 'ACTIVE' THEN 0 ELSE 1 END, jp.pluscrevnum DESC, jp.jobplanid DESC
             ) AS rev_rank
        FROM maximo.jobplan jp
       WHERE jp.siteid = :siteid
         AND jp.orgid = :orgid
         {filters}
    ) ranked
   WHERE ranked.rev_rank = 1
   ORDER BY ranked.jpnum
) WHERE ROWNUM <= :result_limit
"""

_SQL_JP_HEADER = """
SELECT jp.jpnum,
       jp.jobplanid,
       jp.status,
       jp.pluscrevnum,
       jp.description,
       {jp_desc_zh},
       {ld_en},
       {ld_zh},
       jp.priority,
       jp.jpduration,
       jp.cust_wrenchtime,
       jp.interruptible,
       jp.supervisor,
       jp.laborcode,
       jp.cust_craft,
       (SELECT c.description FROM maximo.craft c
         WHERE c.craft = jp.cust_craft AND c.orgid = jp.orgid AND ROWNUM = 1) AS craft_description_en,
       (SELECT l.description FROM maximo.craft c
          JOIN maximo.l_craft l ON l.ownerid = c.craftid AND l.langcode = 'ZH'
         WHERE c.craft = jp.cust_craft AND c.orgid = jp.orgid AND ROWNUM = 1) AS craft_description_zh,
       jp.cust_qual_req,
       jp.cust_qual_labor,
       jp.cust_compliance,
       jp.cust_drawing,
       jp.cust_partdesc,
       jp.cust_partrev,
       jp.cust_jpfreq,
       jp.cust_jptype,
       jp.cust_attraction,
       jp.cust_assetclass,
       jp.persongroup,
       TO_CHAR(jp.pluscchangedate, 'YYYY-MM-DD HH24:MI:SS') AS changedate_utc
  FROM maximo.jobplan jp
 WHERE jp.jobplanid = {pick}
"""

_SQL_JP_TASKS = """
SELECT j.jptask,
       j.jobtaskid,
       j.taskduration,
       j.nestedjpnum,
       j.description,
       {jt_desc_zh},
       {ld_en},
       {ld_zh}
  FROM maximo.jobtask j
 WHERE j.jobplanid = {pick}
 ORDER BY j.jptask
"""

_SQL_JP_WORKORDERS = """
SELECT * FROM (
  SELECT w.wonum,
         w.status,
         w.worktype,
         w.location,
         w.assetnum,
         w.description AS description_en,
         {desc_zh},
         {report_local},
         {actfinish_local}
    FROM maximo.workorder w
   WHERE w.jpnum = :jpnum
     AND w.siteid = :siteid
     AND w.orgid = :orgid
     AND w.woclass = 'WORKORDER'
     AND w.istask = 0
     AND w.historyflag = 0
     AND w.status <> 'CAN'
     AND maximo.wdw_utctosite(w.reportdate, w.siteid) >= TO_DATE(:start_date, 'YYYY-MM-DD')
     AND maximo.wdw_utctosite(w.reportdate, w.siteid) < TO_DATE(:end_date, 'YYYY-MM-DD')
   ORDER BY w.reportdate DESC, w.workorderid DESC
) WHERE ROWNUM <= :wo_limit
"""

_WO_DESC_ZH = _l_desc("l_workorder", "w.workorderid", "description_zh")

SQL_WO_SEARCH = _SQL_WO_SEARCH.replace("{desc_zh}", _WO_DESC_ZH).replace(
    "{report_local}", _local("w.reportdate", "report_local")
).replace("{actstart_local}", _local("w.actstart", "actstart_local")).replace(
    "{actfinish_local}", _local("w.actfinish", "actfinish_local")
)

# Reporting DB rejects correlated inline views with ORDER BY (ORA-00904); pick revision by MAX(jobplanid).
_JP_PICK_ID_FOR_WO = """
       COALESCE(
         (SELECT MAX(p.jobplanid)
            FROM maximo.jobplan p
           WHERE p.jpnum = w.jpnum
             AND p.siteid = w.siteid
             AND p.orgid = w.orgid
             AND p.status = 'ACTIVE'),
         (SELECT MAX(p.jobplanid)
            FROM maximo.jobplan p
           WHERE p.jpnum = w.jpnum
             AND p.siteid = w.siteid
             AND p.orgid = w.orgid))"""

_JP_DESC_EN = f"""
       (SELECT jp.description
          FROM maximo.jobplan jp
         WHERE jp.jobplanid = {_JP_PICK_ID_FOR_WO}) AS jp_description_en"""

_JP_DESC_ZH = f"""
       (SELECT lj.description
          FROM maximo.jobplan jp
          JOIN maximo.l_jobplan lj ON lj.ownerid = jp.jobplanid AND lj.langcode = 'ZH'
         WHERE jp.jobplanid = {_JP_PICK_ID_FOR_WO}) AS jp_description_zh"""

SQL_WO_DETAIL = _SQL_WO_DETAIL.format(
    desc_zh=_WO_DESC_ZH,
    ld_en=_ldtext("WORKORDER", "w.workorderid", "EN", "long_description_en"),
    ld_zh=_ldtext("WORKORDER", "w.workorderid", "ZH", "long_description_zh"),
    report_local=_local("w.reportdate", "report_local"),
    schedstart_local=_local("w.schedstart", "schedstart_local"),
    schedfinish_local=_local("w.schedfinish", "schedfinish_local"),
    actstart_local=_local("w.actstart", "actstart_local"),
    actfinish_local=_local("w.actfinish", "actfinish_local"),
    targstart_local=_local("w.targstartdate", "targstart_local"),
    targcomp_local=_local("w.targcompdate", "targcomp_local"),
    pmdue_local=_local("w.pmduedate", "pmdue_local"),
    jp_desc_en=_JP_DESC_EN,
    jp_desc_zh=_JP_DESC_ZH,
)

SQL_WO_STATUS = _SQL_WO_STATUS


def _ld_scalar(owner_table: str, lang: str, key_expr: str = "w.workorderid") -> str:
    return _ldtext(owner_table, key_expr, lang, "x").rsplit(" AS ", 1)[0]


_TASK_DESC_ZH = _l_desc("l_workorder", "t.workorderid", "description_zh")

SQL_WO_TASKS = _SQL_WO_TASKS.format(
    desc_zh=_TASK_DESC_ZH,
    ld_en_task=_ld_scalar("WOTASK", "EN", "t.workorderid"),
    ld_en_wo=_ld_scalar("WORKORDER", "EN", "t.workorderid"),
    ld_zh_task=_ld_scalar("WOTASK", "ZH", "t.workorderid"),
    ld_zh_wo=_ld_scalar("WORKORDER", "ZH", "t.workorderid"),
    actfinish_local=_local("t.actfinish", "actfinish_local", "t"),
)

SQL_JP_HEADER = _SQL_JP_HEADER.format(
    jp_desc_zh=_l_desc("l_jobplan", "jp.jobplanid", "description_zh"),
    ld_en=_ldtext("JOBPLAN", "jp.jobplanid", "EN", "long_description_en"),
    ld_zh=_ldtext("JOBPLAN", "jp.jobplanid", "ZH", "long_description_zh"),
    pick=_JP_PICK_REVISION,
)

SQL_JP_TASKS = _SQL_JP_TASKS.format(
    jt_desc_zh=_l_desc("l_jobtask", "j.jobtaskid", "description_zh"),
    ld_en=_ldtext("JOBTASK", "j.jobtaskid", "EN", "long_description_en"),
    ld_zh=_ldtext("JOBTASK", "j.jobtaskid", "ZH", "long_description_zh"),
    pick=_JP_PICK_REVISION,
)

SQL_JP_WORKORDERS = _SQL_JP_WORKORDERS.format(
    desc_zh=_WO_DESC_ZH,
    report_local=_local("w.reportdate", "report_local"),
    actfinish_local=_local("w.actfinish", "actfinish_local"),
)

_SQL_JP_SEARCH_BASE = _SQL_JP_SEARCH.replace(
    "{jp_desc_zh}", _l_desc("l_jobplan", "jp.jobplanid", "description_zh")
)


# ---------------------------------------------------------------- helpers


def _now_iso() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).isoformat(timespec="seconds")


def _rows(columns: list[str], raw_rows: list[tuple]) -> list[dict[str, Any]]:
    return [dict(zip(columns, row)) for row in raw_rows]


def _query(sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
    columns, raw_rows = _execute(sql, binds)
    return _rows(columns, raw_rows)


def _parallel(jobs: dict[str, Callable[[], Any]]) -> dict[str, Any]:
    if len(jobs) == 1:
        key, job = next(iter(jobs.items()))
        return {key: job()}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {key: pool.submit(job) for key, job in jobs.items()}
        return {key: future.result() for key, future in futures.items()}


def _parse_date(value: Any, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise DowntimeSearchError(f"{name} 必须是 YYYY-MM-DD 日期。") from exc


def _date_window(payload: dict[str, Any], default_days: int) -> tuple[date, date, date]:
    end_inclusive = _parse_date(payload.get("end_date") or date.today().isoformat(), "结束日期")
    start = _parse_date(
        payload.get("start_date") or (end_inclusive - timedelta(days=default_days - 1)).isoformat(),
        "开始日期",
    )
    end = end_inclusive + timedelta(days=1)
    if end <= start:
        raise DowntimeSearchError("结束日期不能早于开始日期。")
    if (end - start).days > MAX_DATE_RANGE_DAYS:
        raise DowntimeSearchError(f"单次查询最多 {MAX_DATE_RANGE_DAYS} 天。")
    return start, end, end_inclusive


def _int_in_range(value: Any, default: int, low: int, high: int, name: str) -> int:
    try:
        number = int(value if value not in (None, "") else default)
    except (TypeError, ValueError) as exc:
        raise DowntimeSearchError(f"{name}格式无效。") from exc
    if not low <= number <= high:
        raise DowntimeSearchError(f"{name}须在 {low} 到 {high} 之间。")
    return number


def validate_lang(value: Any) -> str:
    lang = str(value or "CN").strip().upper()
    if lang in {"CN", "ZH", "CH", "CHINESE"}:
        return "CN"
    if lang in {"EN", "ENGLISH"}:
        return "EN"
    raise DowntimeSearchError("语言仅支持 CN（中文）或 EN（英文）。")


def escape_like_pattern(keyword: str) -> str:
    """Escape LIKE metacharacters and wrap with wildcards for a contains match; ``*`` matches anything."""
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped.replace('*', '%')}%"


def code_to_like(code: str) -> str:
    """Translate a user code pattern (``*`` wildcard) into an escaped LIKE pattern."""
    escaped = code.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped.replace("*", "%")


def validate_code(value: Any, name: str, required: bool = False) -> str:
    code = str(value or "").strip().upper()
    if not code:
        if required:
            raise DowntimeSearchError(f"请输入{name}。")
        return ""
    if len(code) > MAX_CODE_LEN:
        raise DowntimeSearchError(f"{name}最多 {MAX_CODE_LEN} 个字符。")
    if not _CODE_PATTERN.match(code):
        raise DowntimeSearchError(f"{name}格式无效（字母、数字及 - _ . /，可用 * 通配）。")
    if "*" in code and sum(ch.isalnum() for ch in code) < MIN_WILDCARD_LITERALS:
        raise DowntimeSearchError(f"{name}通配搜索至少需要 {MIN_WILDCARD_LITERALS} 个字母或数字。")
    return code


def validate_exact_code(value: Any, name: str) -> str:
    code = validate_code(value, name, required=True)
    if "*" in code:
        raise DowntimeSearchError(f"{name}不能包含通配符。")
    return code


def _validate_keyword(value: Any) -> str:
    keyword = str(value or "").strip()
    if len(keyword) > MAX_KEYWORD_LEN:
        raise DowntimeSearchError(f"关键词最多 {MAX_KEYWORD_LEN} 个字符。")
    return keyword if keyword.replace("*", "").strip() else ""


def _validate_choices(value: Any, allowed: set[str], name: str) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise DowntimeSearchError(f"{name}格式无效。")
    picked = [str(item).strip().upper() for item in value if str(item).strip()]
    invalid = sorted(set(picked) - allowed)
    if invalid:
        raise DowntimeSearchError(f"不支持的{name}：{', '.join(invalid)}")
    return list(dict.fromkeys(picked))


def _code_filter(column: str, code: str, bind: str, binds: dict[str, Any]) -> str:
    if "*" in code:
        binds[bind] = code_to_like(code)
        return f"AND UPPER({column}) LIKE :{bind} ESCAPE '\\'"
    binds[bind] = code
    return f"AND {column} = :{bind}"


def _in_filter(column: str, values: list[str], prefix: str, binds: dict[str, Any]) -> str:
    if not values:
        return ""
    names = []
    for index, value in enumerate(values):
        name = f"{prefix}_{index}"
        binds[name] = value
        names.append(f":{name}")
    return f"AND {column} IN ({', '.join(names)})"


def ldtext_to_plain(value: Any) -> str:
    """Convert Maximo rich-text long descriptions to plain multi-line text."""
    if not value:
        return ""
    text = str(value)
    if re.search(r"<\s*[a-zA-Z/!]", text):
        text = re.sub(r"[ \t]*\r?\n[ \t]*", " ", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<\s*li[^>]*>", "\n• ", text, flags=re.I)
    text = re.sub(r"</\s*(p|div|li|tr|h[1-6]|table|ul|ol)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ").replace("\r", "")
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n ")


def pick_lang(row: dict[str, Any], lang: str, en_key: str, zh_key: str) -> str:
    en = row.get(en_key) or ""
    zh = row.get(zh_key) or ""
    return (zh or en) if lang == "CN" else (en or zh)


def _normalize_long(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        row["long_description_en"] = ldtext_to_plain(row.get("long_description_en"))
        row["long_description_zh"] = ldtext_to_plain(row.get("long_description_zh"))


def _annotate_attraction(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        attraction = attraction_for_location(row.get("location"))
        row["attraction_code"] = attraction["location"] if attraction else ""
        row["attraction_name_zh"] = attraction["name_zh"] if attraction else ""
        row["attraction_name_en"] = attraction["name_en"] if attraction else ""


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        label = str(row.get(key) or "—")
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


# ---------------------------------------------------------------- config


def module_config() -> dict[str, Any]:
    status = database_status()
    return {
        "module": "maximo_workbench",
        "database": {
            key: status[key]
            for key in ("configured", "tns_alias", "tns_name", "driver", "read_only")
        },
        "site": {"siteid": SITEID, "orgid": ORGID, "timezone": TIMEZONE},
        "attractions": ATTRACTIONS,
        "workorder": {
            "worktypes": WORKTYPE_OPTIONS,
            "statuses": WOSTATUS_OPTIONS,
            "priorities": PRIORITY_OPTIONS,
            "persongroups": PERSONGROUP_OPTIONS,
        },
        "jobplan": {
            "statuses": JP_STATUS_OPTIONS,
            "compliance": JP_COMPLIANCE_OPTIONS,
            "crafts": JP_CRAFT_OPTIONS,
            "frequencies": JP_FREQ_OPTIONS,
            "types": JP_TYPE_OPTIONS,
            "attractions": JP_ATTRACTION_OPTIONS,
        },
        "limits": {
            "max_days": MAX_DATE_RANGE_DAYS,
            "max_wo_results": MAX_WO_RESULTS,
            "max_jp_results": MAX_JP_RESULTS,
            "max_jp_wo_results": MAX_JP_WO_RESULTS,
            "max_keyword_len": MAX_KEYWORD_LEN,
        },
        "defaults": {
            "lang": "CN",
            "wo_days": 30,
            "wo_limit": DEFAULT_WO_RESULTS,
            "jp_limit": DEFAULT_JP_RESULTS,
            "jp_statuses": ["ACTIVE"],
            "jp_wo_limit": DEFAULT_JP_WO_RESULTS,
        },
    }


# ---------------------------------------------------------------- work orders


def _validated_wo_search(payload: dict[str, Any]) -> dict[str, Any]:
    start, end, end_inclusive = _date_window(payload, 30)
    location = str(payload.get("location") or "").strip().upper()
    if location and location not in {item["location"] for item in ATTRACTIONS}:
        raise DowntimeSearchError("请选择目录中的有效景点。")
    return {
        "start_date": start,
        "end_date": end,
        "end_inclusive": end_inclusive,
        "keyword": _validate_keyword(payload.get("keyword")),
        "wonum": validate_code(payload.get("wonum"), "工单号"),
        "jpnum": validate_code(payload.get("jpnum"), "Job Plan 编号"),
        "assetnum": validate_code(payload.get("assetnum"), "资产编号"),
        "pmnum": validate_code(payload.get("pmnum"), "PM 编号"),
        "location": location,
        "worktypes": _validate_choices(
            payload.get("worktypes"), {item["value"] for item in WORKTYPE_OPTIONS}, "工单类型"
        ),
        "statuses": _validate_choices(
            payload.get("statuses"), {item["value"] for item in WOSTATUS_OPTIONS}, "工单状态"
        ),
        "priorities": _validate_choices(
            payload.get("priorities"), {item["value"] for item in PRIORITY_OPTIONS}, "优先级"
        ),
        "persongroups": _validate_choices(
            payload.get("persongroups"), set(PERSONGROUP_OPTIONS), "班组"
        ),
        "has_jobplan": payload.get("has_jobplan") is True,
        "include_cancelled": payload.get("include_cancelled") is True,
        "limit": _int_in_range(payload.get("limit"), DEFAULT_WO_RESULTS, 1, MAX_WO_RESULTS, "结果数量"),
    }


def search_workorders(payload: dict[str, Any]) -> dict[str, Any]:
    started = monotonic()
    query = _validated_wo_search(payload)
    binds: dict[str, Any] = {
        "siteid": SITEID,
        "orgid": ORGID,
        "start_date": query["start_date"].isoformat(),
        "end_date": query["end_date"].isoformat(),
        "result_limit": query["limit"],
    }
    filters: list[str] = []
    if query["keyword"]:
        binds["keyword_pattern"] = escape_like_pattern(query["keyword"])
        filters.append(
            """AND (
          UPPER(NVL(w.description, ' ')) LIKE UPPER(:keyword_pattern) ESCAPE '\\'
          OR EXISTS (SELECT 1 FROM maximo.l_workorder lw
                      WHERE lw.ownerid = w.workorderid AND lw.langcode = 'ZH'
                        AND lw.description LIKE :keyword_pattern ESCAPE '\\')
        )"""
        )
    for column, key in (("w.wonum", "wonum"), ("w.jpnum", "jpnum"), ("w.assetnum", "assetnum"), ("w.pmnum", "pmnum")):
        if query[key]:
            filters.append(_code_filter(column, query[key], key, binds))
    if query["location"]:
        binds["location"] = query["location"]
        binds["location_prefix"] = f"{query['location']}%"
        filters.append(
            """AND (
          w.location = :location
          OR w.location LIKE :location_prefix
          OR EXISTS (SELECT 1 FROM maximo.locancestor la
                      WHERE la.siteid = w.siteid AND la.ancestor = :location AND la.location = w.location)
        )"""
        )
    filters.append(_in_filter("w.worktype", query["worktypes"], "wt", binds))
    filters.append(_in_filter("w.status", query["statuses"], "st", binds))
    filters.append(_in_filter("TO_CHAR(w.wopriority)", query["priorities"], "pr", binds))
    filters.append(_in_filter("w.persongroup", query["persongroups"], "pg", binds))
    if query["has_jobplan"]:
        filters.append("AND w.jpnum IS NOT NULL")
    if not query["include_cancelled"] and "CAN" not in query["statuses"]:
        filters.append("AND w.status <> 'CAN'")

    sql = SQL_WO_SEARCH.replace("{filters}", "\n     ".join(item for item in filters if item))
    rows = _query(sql, binds)
    _annotate_attraction(rows)
    return {
        "mode": "workorders",
        "query_id": uuid4().hex,
        "queried_at": _now_iso(),
        "elapsed_seconds": round(monotonic() - started, 3),
        "timezone": TIMEZONE,
        "items": rows,
        "count": len(rows),
        "truncated": len(rows) >= query["limit"],
        "facets": {
            "status": _count_by(rows, "status"),
            "worktype": _count_by(rows, "worktype"),
            "attraction": _count_by(rows, "attraction_name_zh"),
        },
        "filters": {
            **{key: query[key] for key in (
                "keyword", "wonum", "jpnum", "assetnum", "pmnum", "location", "worktypes",
                "statuses", "priorities", "persongroups", "has_jobplan", "include_cancelled", "limit",
            )},
            "start_date": query["start_date"].isoformat(),
            "end_date": query["end_inclusive"].isoformat(),
        },
    }


def workorder_detail(payload: dict[str, Any]) -> dict[str, Any]:
    started = monotonic()
    wonum = validate_exact_code(payload.get("wonum"), "工单号")
    binds = {"wonum": wonum, "siteid": SITEID, "orgid": ORGID}
    results = _parallel({
        "header": lambda: _query(SQL_WO_DETAIL, binds),
        "tasks": lambda: _query(SQL_WO_TASKS, binds),
        "status_history": lambda: _query(SQL_WO_STATUS, binds),
    })
    if not results["header"]:
        raise DowntimeSearchError(f"未找到工单：{wonum}（{SITEID} / {ORGID}）。")
    header = results["header"][0]
    _normalize_long([header])
    _annotate_attraction([header])
    tasks = results["tasks"]
    _normalize_long(tasks)
    return {
        "mode": "workorder_detail",
        "query_id": uuid4().hex,
        "queried_at": _now_iso(),
        "elapsed_seconds": round(monotonic() - started, 3),
        "timezone": TIMEZONE,
        "workorder": header,
        "tasks": tasks,
        "task_count": len(tasks),
        "status_history": results.get("status_history", []),
    }


# ---------------------------------------------------------------- job plans


def _validated_jp_search(payload: dict[str, Any]) -> dict[str, Any]:
    query = {
        "jpnum": validate_code(payload.get("jpnum"), "Job Plan 编号"),
        "keyword": _validate_keyword(payload.get("keyword")),
        "statuses": _validate_choices(payload.get("statuses"), set(JP_STATUS_OPTIONS), "计划状态"),
        "compliance": _validate_choices(payload.get("compliance"), set(JP_COMPLIANCE_OPTIONS), "遵从类型"),
        "crafts": _validate_choices(payload.get("crafts"), set(JP_CRAFT_OPTIONS), "工艺"),
        "frequencies": _validate_choices(payload.get("frequencies"), set(JP_FREQ_OPTIONS), "频率"),
        "types": _validate_choices(
            payload.get("types"), {item["value"] for item in JP_TYPE_OPTIONS}, "计划类型"
        ),
        "attractions": _validate_choices(payload.get("attractions"), set(JP_ATTRACTION_OPTIONS), "景点代码"),
        "limit": _int_in_range(payload.get("limit"), DEFAULT_JP_RESULTS, 1, MAX_JP_RESULTS, "结果数量"),
    }
    if not query["jpnum"] and not query["keyword"] and not any(
        query[key] for key in ("compliance", "crafts", "frequencies", "types", "attractions")
    ):
        raise DowntimeSearchError("请至少输入 Job Plan 编号（可用 *）、描述关键词，或选择一个筛选条件。")
    return query


def search_jobplans(payload: dict[str, Any]) -> dict[str, Any]:
    started = monotonic()
    query = _validated_jp_search(payload)
    binds: dict[str, Any] = {"siteid": SITEID, "orgid": ORGID, "result_limit": query["limit"]}
    filters: list[str] = []
    if query["jpnum"]:
        filters.append(_code_filter("jp.jpnum", query["jpnum"], "jpnum", binds))
    if query["keyword"]:
        binds["keyword_pattern"] = escape_like_pattern(query["keyword"])
        filters.append(
            """AND (
          UPPER(NVL(jp.description, ' ')) LIKE UPPER(:keyword_pattern) ESCAPE '\\'
          OR EXISTS (SELECT 1 FROM maximo.l_jobplan lj
                      WHERE lj.ownerid = jp.jobplanid AND lj.langcode = 'ZH'
                        AND lj.description LIKE :keyword_pattern ESCAPE '\\')
        )"""
        )
    filters.append(_in_filter("jp.status", query["statuses"], "st", binds))
    filters.append(_in_filter("jp.cust_compliance", query["compliance"], "cp", binds))
    filters.append(_in_filter("jp.cust_craft", query["crafts"], "cr", binds))
    filters.append(_in_filter("jp.cust_jpfreq", query["frequencies"], "fq", binds))
    filters.append(_in_filter("jp.cust_jptype", query["types"], "ty", binds))
    filters.append(_in_filter("jp.cust_attraction", query["attractions"], "at", binds))

    sql = _SQL_JP_SEARCH_BASE.replace("{filters}", "\n         ".join(item for item in filters if item))
    rows = _query(sql, binds)
    for row in rows:
        row.pop("rev_rank", None)
    return {
        "mode": "jobplans",
        "query_id": uuid4().hex,
        "queried_at": _now_iso(),
        "elapsed_seconds": round(monotonic() - started, 3),
        "timezone": TIMEZONE,
        "items": rows,
        "count": len(rows),
        "truncated": len(rows) >= query["limit"],
        "facets": {
            "status": _count_by(rows, "status"),
            "craft": _count_by(rows, "cust_craft"),
            "frequency": _count_by(rows, "cust_jpfreq"),
        },
        "filters": query,
    }


def jobplan_detail(payload: dict[str, Any]) -> dict[str, Any]:
    started = monotonic()
    jpnum = validate_exact_code(payload.get("jpnum"), "Job Plan 编号")
    include_wos = payload.get("include_workorders") is not False
    binds = {"jpnum": jpnum, "siteid": SITEID, "orgid": ORGID}
    jobs: dict[str, Callable[[], Any]] = {
        "header": lambda: _query(SQL_JP_HEADER, binds),
        "tasks": lambda: _query(SQL_JP_TASKS, binds),
    }
    wo_filters: dict[str, Any] = {}
    if include_wos:
        start, end, end_inclusive = _date_window(payload, MAX_DATE_RANGE_DAYS)
        wo_limit = _int_in_range(
            payload.get("wo_limit"), DEFAULT_JP_WO_RESULTS, 1, MAX_JP_WO_RESULTS, "工单条数"
        )
        wo_binds = {**binds, "start_date": start.isoformat(), "end_date": end.isoformat(), "wo_limit": wo_limit}
        wo_filters = {"start_date": start.isoformat(), "end_date": end_inclusive.isoformat(), "limit": wo_limit}
        jobs["workorders"] = lambda: _query(SQL_JP_WORKORDERS, wo_binds)
    results = _parallel(jobs)
    if not results["header"]:
        raise DowntimeSearchError(f"未找到 Job Plan：{jpnum}（{SITEID} / {ORGID}）。")
    header = results["header"][0]
    _normalize_long([header])
    tasks = results["tasks"]
    _normalize_long(tasks)
    workorders = results.get("workorders", [])
    _annotate_attraction(workorders)
    return {
        "mode": "jobplan_detail",
        "query_id": uuid4().hex,
        "queried_at": _now_iso(),
        "elapsed_seconds": round(monotonic() - started, 3),
        "timezone": TIMEZONE,
        "jobplan": header,
        "tasks": tasks,
        "task_count": len(tasks),
        "workorders": {
            "included": include_wos,
            "items": workorders,
            "count": len(workorders),
            "truncated": include_wos and len(workorders) >= wo_filters.get("limit", 0),
            "status_summary": _count_by(workorders, "status"),
            "filters": wo_filters,
        },
    }
