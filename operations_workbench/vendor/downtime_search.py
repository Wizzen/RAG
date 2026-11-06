"""Read-only, parameterized Oracle queries for attraction downtime."""

from __future__ import annotations

import os
import base64
import re
import subprocess
import shutil
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any

from ..runtime import option, runtime_dir

from .downtime_catalog import (
    ATTRACTIONS,
    CLASSIFICATION_CODES,
    attraction_for_location,
)

ALLOWED_CODES = frozenset({"100", "101", "105"})
MAX_DATE_RANGE_DAYS = 31
MAX_RESULTS = 500
ENRICHMENT_CACHE_TTL_SECONDS = 120
ENRICHMENT_CACHE_MAX_ITEMS = 2_000
_enrichment_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_enrichment_cache_lock = Lock()

_SQL = """
SELECT * FROM (
 SELECT q.*, ROWNUM AS row_number_ FROM (
  SELECT
    w.wonum,
    w.status,
    w.worktype,
    w.cust_downtimecode AS downtime_code,
    w.location,
    w.assetnum,
    w.failurecode,
    w.problemcode,
    w.cust_down_reason,
    w.cust_rootcause,
    w.cust_maint_down,
    (SELECT fc.description
       FROM maximo.failurecode fc
      WHERE fc.orgid = w.orgid
        AND fc.failurecode = w.failurecode
        AND fc.langcode = 'EN'
        AND ROWNUM = 1) AS failure_description,
    (SELECT fc.description
       FROM maximo.failurecode fc
      WHERE fc.orgid = w.orgid
        AND fc.failurecode = w.problemcode
        AND fc.langcode = 'EN'
        AND ROWNUM = 1) AS problem_description,
    cs.classificationid AS reason_code,
    NVL(
      (SELECT lcs.description
         FROM maximo.l_classstructure lcs
        WHERE lcs.ownerid = cs.classstructureuid
          AND lcs.langcode = 'ZH'
          AND ROWNUM = 1),
      cs.description
    ) AS reason_zh,
    NVL(
      (SELECT lw.description
         FROM maximo.l_workorder lw
        WHERE lw.ownerid = w.workorderid
          AND lw.langcode = 'ZH'
          AND ROWNUM = 1),
      w.description
    ) AS workorder_description,
    TO_CHAR(maximo.wdw_utctosite(w.reportdate, w.siteid),
            'YYYY-MM-DD HH24:MI:SS') AS report_local,
    TO_CHAR(
      (SELECT MIN(maximo.wdw_utctosite(ast.changedate, ast.siteid))
         FROM maximo.assetstatus ast
        WHERE ast.wonum = w.wonum
          AND ast.siteid = w.siteid
          AND ast.isrunning = 0),
      'YYYY-MM-DD HH24:MI:SS'
    ) AS downtime_start_local,
    TO_CHAR(
      (SELECT MAX(maximo.wdw_utctosite(ast.changedate, ast.siteid))
         FROM maximo.assetstatus ast
        WHERE ast.wonum = w.wonum
          AND ast.siteid = w.siteid
          AND ast.isrunning = 1),
      'YYYY-MM-DD HH24:MI:SS'
    ) AS downtime_end_local,
    (SELECT MAX(ast.downtime)
       FROM maximo.assetstatus ast
      WHERE ast.wonum = w.wonum
        AND ast.siteid = w.siteid
        AND ast.isrunning = 1) AS downtime_hours,
    (SELECT MAX(ast.operational)
       FROM maximo.assetstatus ast
      WHERE ast.wonum = w.wonum
        AND ast.siteid = w.siteid) AS operational
  FROM maximo.workorder w
  LEFT JOIN maximo.classstructure cs
    ON cs.classstructureid = w.classstructureid
  WHERE w.woclass = 'WORKORDER'
    AND w.historyflag = 0
    AND w.istask = 0
    AND w.siteid = :siteid
    AND w.orgid = :orgid
    AND w.status <> 'CAN'
    AND (
      (w.worktype = 'EM' AND w.cust_downtimecode IN ({code_binds}))
      {include_104}
    )
    AND maximo.wdw_utctosite(w.reportdate, w.siteid) >= TO_DATE(:start_date, 'YYYY-MM-DD')
    AND maximo.wdw_utctosite(w.reportdate, w.siteid) < TO_DATE(:end_date, 'YYYY-MM-DD')
    {attraction_filter}
    {classification_filter}
  ORDER BY maximo.wdw_utctosite(w.reportdate, w.siteid) DESC, w.wonum
 ) q WHERE ROWNUM <= TO_NUMBER(:row_end)
)
WHERE row_number_ > TO_NUMBER(:row_offset)
ORDER BY row_number_
"""


class DowntimeSearchError(ValueError):
    """A safe error suitable for returning to the UI."""


class ConnectionUnavailable(DowntimeSearchError):
    """The configured Oracle service could not complete the query."""


def _strip_env_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _required_env(name: str) -> str:
    value = _strip_env_quotes(option(name, ""))
    if not value:
        raise ConnectionUnavailable(
            "Downtime 数据库尚未配置。请在运维工作台连接配置 中设置数据库账号和密码。"
        )
    return value


def _jdbc_failure_message(stderr: str, stdout: str) -> str:
    """Map Oracle/JDBC output to a safe, actionable UI message (no secrets)."""
    text = f"{stderr}\n{stdout}"
    if re.search(r"UnknownHostException|Name or service not known|nodename nor servname provided", text, re.I):
        return "无法解析 Oracle 服务器地址，请连接公司网络或 VPN 后重试。"
    if re.search(r"ORA-01017", text, re.I):
        return (
            "Oracle 拒绝登录（ORA-01017）：用户名或密码不正确。"
            "请在运维工作台连接配置 中使用与报表库相同的账号，"
            "并确认账号未锁定或过期。"
        )
    if re.search(r"ORA-28000|ORA-28001|ORA-28002", text, re.I):
        return "Oracle 账号已锁定或密码已过期，请联系 DBA 解锁或重置密码。"
    if re.search(r"ORA-01031|ORA-01927", text, re.I):
        return "Oracle 账号权限不足，无法登录或执行查询（ORA-01031）。"
    if re.search(r"ORA-00942", text, re.I):
        return (
            "账号可连接，但无权访问 Maximo 表（ORA-00942）。"
            "请确认当前账号对 MAXIMO 模式有 SELECT 权限。"
        )
    if re.search(r"ORA-12541|ORA-12514|ORA-12545|ORA-12170|ORA-12609", text, re.I):
        return (
            "无法连到 Oracle 监听或服务名（网络/TNS）。"
            "请确认 VPN 已连接，且运维工作台连接配置 与 SHA.ReportingDB.PROD 一致。"
        )
    if re.search(r"Missing required database configuration", text):
        return "JDBC 缺少连接配置，请检查 运维工作台连接配置 中的 host/port/service 或 DOWNTIME_DB_TNS。"
    match = re.search(r"ORA-\d{5}", text, re.I)
    if match:
        return f"Oracle JDBC 失败（{match.group(0).upper()}），请联系管理员检查服务日志。"
    return "Oracle JDBC 未完成查询，请检查 Java、JDBC 驱动与数据库连接配置。"


def database_status() -> dict[str, Any]:
    """Return non-secret configuration status for the UI."""
    return {
        "configured": bool(
            option("DOWNTIME_DB_USER", "").strip()
            and option("DOWNTIME_DB_PASSWORD", "").strip()
        ),
        "tns_alias": option("DOWNTIME_DB_TNS_ALIAS", "SHA.ReportingDB.PROD"),
        "host": option(
            "DOWNTIME_DB_HOST",
            "lnxushdr001pra-scan.apac.wdpr.disney.com",
        ),
        "port": int(option("DOWNTIME_DB_PORT", "1541")),
        "service": option(
            "DOWNTIME_DB_SERVICE",
            "maxarshpsvc.apac.wdpr.disney.com",
        ),
        "driver": option("DOWNTIME_DB_DRIVER", "auto").strip().lower(),
        "tns_name": option("DOWNTIME_TNS_NAME", "SHA.ReportingDB.PROD").strip(),
        "read_only": True,
    }


def _jdbc_subprocess_env() -> dict[str, str]:
    status = database_status()
    tns_admin = _strip_env_quotes(option("DOWNTIME_TNS_ADMIN", ""))
    if not tns_admin and ((runtime_dir() / "oracle") / "tnsnames.ora").is_file():
        tns_admin = str((runtime_dir() / "oracle"))
    env: dict[str, str] = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "JAVA_HOME": option("JAVA_HOME", ""),
        "DOWNTIME_DB_HOST": str(status["host"]),
        "DOWNTIME_DB_PORT": str(status["port"]),
        "DOWNTIME_DB_SERVICE": str(status["service"]),
        "DOWNTIME_DB_TNS": _strip_env_quotes(option("DOWNTIME_DB_TNS", "")),
        "DOWNTIME_TNS_NAME": _strip_env_quotes(
            option("DOWNTIME_TNS_NAME", "SHA.ReportingDB.PROD")
        ),
    }
    if tns_admin:
        env["TNS_ADMIN"] = tns_admin
    wallet = _strip_env_quotes(option("DOWNTIME_WALLET_PATH", ""))
    if wallet:
        env["TNS_ADMIN"] = wallet
    return env


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _parse_date(value: Any, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise DowntimeSearchError(f"{name} 必须是 YYYY-MM-DD 日期。") from exc


def _validated_request(payload: dict[str, Any], max_results: int = MAX_RESULTS) -> dict[str, Any]:
    start = _parse_date(payload.get("start_date"), "开始日期")
    end_inclusive = _parse_date(payload.get("end_date"), "结束日期")
    end = end_inclusive + timedelta(days=1)
    if end <= start:
        raise DowntimeSearchError("结束日期不能早于开始日期。")
    if (end - start).days > MAX_DATE_RANGE_DAYS:
        raise DowntimeSearchError(f"单次查询最多 {MAX_DATE_RANGE_DAYS} 天。")

    raw_codes = payload.get("codes") or sorted(ALLOWED_CODES)
    if not isinstance(raw_codes, list):
        raise DowntimeSearchError("停机类型格式无效。")
    codes = [str(code) for code in raw_codes]
    if not codes or not set(codes).issubset(ALLOWED_CODES):
        raise DowntimeSearchError("停机类型仅允许 100、101、105。")

    location = str(payload.get("location") or "").strip().upper()
    valid_locations = {item["location"] for item in ATTRACTIONS}
    if location and location not in valid_locations:
        raise DowntimeSearchError("请选择目录中的有效景点。")

    classification = str(payload.get("classification") or "").strip()
    if classification and classification not in CLASSIFICATION_CODES:
        raise DowntimeSearchError("请选择有效的 Classification。")

    try:
        limit = int(payload.get("limit", 200))
        offset = int(payload.get("offset", 0))
    except (TypeError, ValueError) as exc:
        raise DowntimeSearchError("结果数量格式无效。") from exc
    if not 1 <= limit <= max_results:
        raise DowntimeSearchError(f"结果数量必须在 1 到 {max_results} 之间。")
    if not 0 <= offset <= 1_000_000:
        raise DowntimeSearchError("记录偏移须在 0 到 1000000 之间。")

    return {
        "start_date": start,
        "end_date": end,
        "codes": codes,
        "location": location,
        "classification": classification,
        "include_104": payload.get("include_104") is True,
        "limit": limit,
        "offset": offset,
    }


def _connection():
    try:
        import oracledb
    except ImportError as exc:
        raise ConnectionUnavailable("此连接需要 Oracle Python 驱动或 JDBC 运行组件。") from exc
    status = database_status()
    params = oracledb.ConnectParams(
        host=status["host"],
        port=status["port"],
        service_name=status["service"],
        tcp_connect_timeout=10,
    )
    try:
        connection = oracledb.connect(
            user=_required_env("DOWNTIME_DB_USER"),
            password=_required_env("DOWNTIME_DB_PASSWORD"),
            params=params,
        )
        connection.call_timeout = 60_000
        return connection
    except DowntimeSearchError:
        raise
    except oracledb.Error as exc:
        raise ConnectionUnavailable("无法连接 Downtime 数据库，请检查网络或账号配置。") from exc


def _execute_oracledb(sql: str, binds: dict[str, Any]) -> tuple[list[str], list[tuple]]:
    connection = _connection()
    import oracledb
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(sql, binds)
            columns = [item[0].lower() for item in cursor.description]
            return columns, cursor.fetchall()
    except oracledb.Error as exc:
        raise ConnectionUnavailable("Downtime 查询失败，请稍后重试或缩小日期范围。") from exc
    finally:
        connection.close()


def _jdbc_sql(sql: str, binds: dict[str, Any]) -> tuple[str, list[Any]]:
    """Convert internal named binds to JDBC positional parameters."""
    names: list[str] = []

    def replace(match: re.Match) -> str:
        names.append(match.group(1))
        return "?"

    statement = re.sub(r"(?<![A-Za-z0-9_]):([A-Za-z][A-Za-z0-9_]*)", replace, sql)
    return statement, [binds[name] for name in names]


def jdbc_runtime_paths() -> tuple[str | None, Path]:
    java_name = "java.exe" if os.name == "nt" else "java"
    candidates = [option("DOWNTIME_JAVA_BIN", "")]
    if option("JAVA_HOME"):
        candidates.append(str(Path(option("JAVA_HOME")) / "bin" / java_name))
    candidates.extend([str(runtime_dir() / "java" / "Contents" / "Home" / "bin" / java_name),
                       str(runtime_dir() / "java" / "bin" / java_name), shutil.which("java") or ""])
    java_bin = next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)
    jar = Path(option("DOWNTIME_JDBC_JAR", str(runtime_dir() / "oracle-jdbc" / "ojdbc8.jar"))).expanduser()
    if not jar.is_absolute():
        jar = runtime_dir() / jar
    return java_bin, jar


def _execute_jdbc(sql: str, binds: dict[str, Any]) -> tuple[list[str], list[tuple]]:
    # SECURITY-REVIEW: fixed local JVM/JAR/class paths load the approved Oracle
    # driver in an isolated subprocess. SQL is fixed by this module and all
    # browser values remain positional bind parameters.
    java_bin, jdbc_jar = jdbc_runtime_paths()
    bridge_dir = runtime_dir() / "bridge"
    bridge_class = bridge_dir / "DowntimeJdbcBridge.class"
    if (
        not java_bin or not Path(java_bin).exists()
        or not jdbc_jar.exists()
        or not bridge_class.exists()
    ):
        raise ConnectionUnavailable(
            "Oracle JDBC 兼容组件未安装，请重新运行 Downtime 模块安装步骤。"
        )

    statement, parameters = _jdbc_sql(sql, binds)
    encoded = [
        _b64(statement),
        str(len(parameters)),
        *[_b64(str(value)) for value in parameters],
        _b64(_required_env("DOWNTIME_DB_USER")),
        _b64(_required_env("DOWNTIME_DB_PASSWORD")),
    ]
    env = _jdbc_subprocess_env()
    classpath = f"{jdbc_jar}{os.pathsep}{bridge_dir}"
    try:
        completed = subprocess.run(
            [
                str(java_bin),
                "-cp",
                classpath,
                "DowntimeJdbcBridge",
            ],
            input="\n".join(encoded) + "\n",
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConnectionUnavailable(
            "Oracle JDBC 查询进程未能完成，请稍后重试。"
        ) from exc
    if completed.returncode != 0:
        raise ConnectionUnavailable(
            _jdbc_failure_message(completed.stderr or "", completed.stdout or "")
        )

    lines = completed.stdout.splitlines()
    if not lines:
        raise ConnectionUnavailable("Oracle JDBC 未返回查询结果。")

    def decode(value: str) -> str | None:
        if value == "~":
            return None
        return base64.b64decode(value).decode("utf-8")

    columns = [str(decode(item)).lower() for item in lines[0].split("\t")]
    rows = [
        tuple(decode(item) for item in line.split("\t"))
        for line in lines[1:]
        if line
    ]
    return columns, rows


def _execute(sql: str, binds: dict[str, Any]) -> tuple[list[str], list[tuple]]:
    driver = database_status()["driver"]
    if driver == "jdbc":
        return _execute_jdbc(sql, binds)
    return _execute_oracledb(sql, binds)


def _maintenance_yn(cust_maint_down: Any) -> str:
    """Maximo WORKORDER.CUST_MAINT_DOWN: 1 = maintenance downtime (Y), else N."""
    if cust_maint_down is None:
        return "N"
    text = str(cust_maint_down).strip()
    if not text:
        return "N"
    try:
        return "Y" if int(float(text)) == 1 else "N"
    except (TypeError, ValueError):
        return "N"


def _wonum_in_clause(wonums: list[str]) -> tuple[str, dict[str, Any]]:
    binds: dict[str, Any] = {}
    parts: list[str] = []
    for index, wonum in enumerate(wonums):
        key = f"wonum_{index}"
        binds[key] = wonum
        parts.append(f":{key}")
    return ", ".join(parts), binds


def _enrich_downtime_rows(
    rows: list[dict[str, Any]], siteid: str, orgid: str
) -> str | None:
    """Fetch all operation and work-order relationships in one JDBC call."""
    requested_wonums = [str(row.get("wonum")) for row in rows if row.get("wonum")]
    now = monotonic()
    cached: dict[str, dict[str, Any]] = {}
    with _enrichment_cache_lock:
        for wonum in requested_wonums:
            entry = _enrichment_cache.get(wonum)
            if entry and now - entry[0] <= ENRICHMENT_CACHE_TTL_SECONDS:
                cached[wonum] = entry[1]

    for row in rows:
        row.update(cached.get(str(row.get("wonum") or ""), {}))

    wonums = [wonum for wonum in requested_wonums if wonum not in cached]
    if not wonums:
        return None

    in_sql, in_binds = _wonum_in_clause(wonums)
    sql = f"""
    SELECT 'operation_steps' AS kind,
           cw.parent AS wonum,
           LISTAGG(
             TO_CHAR(cw.taskid) || ' - ' ||
             NVL(cw.description, '(无描述)'),
             ' | '
           ) WITHIN GROUP (ORDER BY cw.taskid) AS value
      FROM maximo.wotask cw
     WHERE cw.siteid = :siteid
       AND cw.orgid = :orgid
       AND cw.parent IN ({in_sql})
       AND cw.historyflag = 0
       AND cw.istask = 1
     GROUP BY cw.parent
    UNION ALL
    SELECT 'follow_up_wonums' AS kind,
           cw.origrecordid AS wonum,
           LISTAGG(cw.wonum || ' [' || cw.status || ']', ', ')
             WITHIN GROUP (ORDER BY cw.wonum) AS value
      FROM maximo.workorder cw
     WHERE cw.siteid = :siteid
       AND cw.orgid = :orgid
       AND cw.origrecordid IN ({in_sql})
       AND cw.historyflag = 0
       AND cw.origrecordclass = 'WORKORDER'
     GROUP BY cw.origrecordid
    UNION ALL
    SELECT 'related_wonums' AS kind,
           rr.relatedreckey AS wonum,
           LISTAGG(
             rr.recordkey ||
             CASE WHEN rr.relatetype IS NOT NULL
                  THEN ' (' || rr.relatetype || ')' END,
             ', '
           ) WITHIN GROUP (ORDER BY rr.recordkey) AS value
      FROM maximo.relatedrecord rr
     WHERE rr.relatedrecclass = 'WORKORDER'
       AND rr.relatedreckey IN ({in_sql})
       AND rr.recordkey <> rr.relatedreckey
     GROUP BY rr.relatedreckey
    UNION ALL
    SELECT 'asset_status_codes' AS kind,
           ast.wonum AS wonum,
           LISTAGG(TRIM(ast.code), ', ')
             WITHIN GROUP (ORDER BY ast.changedate) AS value
      FROM maximo.assetstatus ast
     WHERE ast.siteid = :siteid
       AND ast.wonum IN ({in_sql})
       AND ast.code IS NOT NULL
       AND TRIM(ast.code) IS NOT NULL
     GROUP BY ast.wonum
    """
    binds = {"siteid": siteid, "orgid": orgid, **in_binds}
    try:
        columns, raw_rows = _execute(sql, binds)
    except DowntimeSearchError as exc:
        return str(exc)

    enrichment: dict[str, dict[str, Any]] = {}
    for raw in raw_rows:
        result = dict(zip(columns, raw))
        wonum = str(result.get("wonum") or "")
        kind = str(result.get("kind") or "")
        value = result.get("value")
        if wonum and kind and value is not None:
            enrichment.setdefault(wonum, {})[kind] = value

    for row in rows:
        row.update(enrichment.get(str(row.get("wonum") or ""), {}))

    with _enrichment_cache_lock:
        for wonum in wonums:
            _enrichment_cache[wonum] = (now, enrichment.get(wonum, {}))
        if len(_enrichment_cache) > ENRICHMENT_CACHE_MAX_ITEMS:
            oldest = sorted(
                _enrichment_cache, key=lambda key: _enrichment_cache[key][0]
            )
            for key in oldest[: len(_enrichment_cache) - ENRICHMENT_CACHE_MAX_ITEMS]:
                _enrichment_cache.pop(key, None)
    return None


def search_downtime(payload: dict[str, Any], *, max_results: int = MAX_RESULTS) -> dict[str, Any]:
    """Execute the fixed downtime query with validated bind variables only."""
    started = monotonic()
    query = _validated_request(payload, max_results)
    code_binds = ", ".join(f":code_{index}" for index in range(len(query["codes"])))
    include_104 = (
        "OR (w.worktype = 'PRJ' AND w.cust_downtimecode = '104')"
        if query["include_104"]
        else ""
    )
    attraction_filter = ""
    if query["location"]:
        attraction_filter = """
        AND (
          w.location = :location
          OR w.location LIKE :location_prefix
          OR EXISTS (
            SELECT 1
              FROM maximo.locancestor la
             WHERE la.siteid = w.siteid
               AND la.ancestor = :location
               AND la.location = w.location
          )
        )
        """

    classification_filter = (
        "AND cs.classificationid LIKE :classification_prefix"
        if query["classification"]
        else ""
    )

    sql = _SQL.format(
        code_binds=code_binds,
        include_104=include_104,
        attraction_filter=attraction_filter,
        classification_filter=classification_filter,
    )
    binds: dict[str, Any] = {
        "siteid": "SDLR",
        "orgid": "WDPR",
        "start_date": query["start_date"].isoformat(),
        "end_date": query["end_date"].isoformat(),
        "row_offset": query["offset"],
        "row_end": query["offset"] + query["limit"],
    }
    binds.update({f"code_{index}": code for index, code in enumerate(query["codes"])})
    if query["location"]:
        binds["location"] = query["location"]
        binds["location_prefix"] = f"{query['location']}%"
    if query["classification"]:
        binds["classification_prefix"] = f"{query['classification']}%"

    columns, raw_rows = _execute(sql, binds)
    rows = [dict(zip(columns, row)) for row in raw_rows]

    for row in rows:
        row.pop("row_number_", None)
        attraction = attraction_for_location(row.get("location"))
        row["attraction_code"] = attraction["location"] if attraction else ""
        row["attraction_name_zh"] = attraction["name_zh"] if attraction else ""
        row["attraction_name_en"] = attraction["name_en"] if attraction else ""
        hours = row.get("downtime_hours")
        row["downtime_hours"] = float(hours) if hours is not None else None
        row["downtime_minutes"] = round(float(hours) * 60, 2) if hours is not None else None

    enrichment_warning = _enrich_downtime_rows(
        rows, binds["siteid"], binds["orgid"]
    )
    for row in rows:
        row["maintenance_yn"] = _maintenance_yn(row.get("cust_maint_down"))

    return {
        "query_id": uuid4().hex,
        "queried_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "elapsed_seconds": round(monotonic() - started, 3),
        "timezone": "Asia/Shanghai",
        "source": "Maximo reportdate / assetstatus; business rule review pending",
        "items": rows,
        "count": len(rows),
        "offset": query["offset"],
        "truncated": len(rows) == query["limit"],
        "warning": enrichment_warning,
        "filters": {
            "start_date": query["start_date"].isoformat(),
            "end_date": (query["end_date"] - timedelta(days=1)).isoformat(),
            "codes": query["codes"],
            "location": query["location"],
            "classification": query["classification"],
            "include_104": query["include_104"],
            "limit": query["limit"],
            "offset": query["offset"],
        },
    }


def probe_database() -> dict[str, Any]:
    """Lightweight login check (SELECT 1 FROM DUAL). Safe to expose message to UI."""
    status = database_status()
    meta = {
        "tns_name": status.get("tns_name") or status.get("tns_alias"),
    }
    try:
        columns, rows = _execute("SELECT 1 AS ok FROM DUAL", {})
        return {
            "ok": True,
            "message": "Oracle 连接正常。",
            "sample": rows[0][0] if rows else None,
            **meta,
        }
    except DowntimeSearchError as exc:
        message = str(exc)
        return {"ok": False, "message": message, **meta}
