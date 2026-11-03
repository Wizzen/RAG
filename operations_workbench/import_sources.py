"""One-time local setup for independent RAG data sources.

Run with the project's Python: python -m operations_workbench.import_sources
--toolbox <engineering toolbox directory> --apex <configured Apex Portal directory>.
Paths are used only during import. Re-run to refresh the local downtime snapshot.
"""

import argparse
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from dotenv import dotenv_values

ORACLE_KEYS = (
    "DOWNTIME_DB_USER",
    "DOWNTIME_DB_PASSWORD",
    "DOWNTIME_DB_TNS",
    "DOWNTIME_DB_TNS_ALIAS",
    "DOWNTIME_TNS_NAME",
    "DOWNTIME_DB_HOST",
    "DOWNTIME_DB_PORT",
    "DOWNTIME_DB_SERVICE",
    "DOWNTIME_DB_DRIVER",
)
SPLUNK_KEYS = ("SPLUNK_HOST", "SPLUNK_PORT", "SPLUNK_USER", "SPLUNK_PASSWORD")


def install(toolbox, apex, destination):
    if not (toolbox / ".env.downtime").is_file() or not (apex / ".env").is_file():
        raise FileNotFoundError("请指定已配置的原始 Oracle 和 Splunk 模块目录。")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.chmod(0o700)
    (destination / ".gitignore").write_text("*\n")
    oracle = dotenv_values(toolbox / ".env.downtime", interpolate=False)
    splunk = dotenv_values(apex / ".env", interpolate=False)
    config = {
        k: v
        for values, keys in ((oracle, ORACLE_KEYS), (splunk, SPLUNK_KEYS))
        for k in keys
        if (v := values.get(k))
    }
    # Keep operator-owned paths private too. Copy the required runtime locally,
    # so neither launching the old service nor retaining its directory is needed.
    with tempfile.TemporaryDirectory(dir=destination) as temp:
        staging = Path(temp)
        src = toolbox / "data" / "downtime_insights.sqlite3"
        if src.is_file():
            snapshot = staging / "downtime.sqlite3"
            with (
                closing(
                    sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True)
                ) as original,
                closing(sqlite3.connect(snapshot)) as copy,
            ):
                original.backup(copy)
                copy.execute("PRAGMA journal_mode=DELETE")
            snapshot.chmod(0o600)
            os.replace(snapshot, destination / snapshot.name)
        for source, name in (
            (toolbox / ".tools" / "java", "java"),
            (toolbox / ".tools" / "oracle-jdbc", "oracle-jdbc"),
            (toolbox / "config" / "oracle", "oracle"),
        ):
            if source.is_dir() and not (destination / name).exists():
                shutil.copytree(source, staging / name, symlinks=True)
                os.replace(staging / name, destination / name)
        bridge = toolbox / "web/services/java/DowntimeJdbcBridge.class"
        if bridge.is_file():
            (destination / "bridge").mkdir(exist_ok=True)
            shutil.copy2(bridge, staging / bridge.name)
            os.replace(staging / bridge.name, destination / "bridge" / bridge.name)
        secret_file = staging / ".env.connections"
        secret_file.write_text(json.dumps(config), encoding="utf-8")
        secret_file.chmod(0o600)
        os.replace(secret_file, destination / secret_file.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolbox", required=True, type=Path)
    parser.add_argument("--apex", required=True, type=Path)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / ".runtime"
        / "operations-workbench",
    )
    args = parser.parse_args()
    install(args.toolbox, args.apex, args.destination)
    print("运维数据与本机连接配置已就绪。")
