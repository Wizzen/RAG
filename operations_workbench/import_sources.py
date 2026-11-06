"""One-time local setup for independent RAG data sources.

Run with the project's Python: python -m operations_workbench.import_sources
--toolbox <engineering toolbox directory> --apex <configured Apex Portal directory>.
Paths are used only during import. Re-run to refresh the local downtime snapshot.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import ssl
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
# Set on this machine after import; a refresh from the source modules must not drop them.
LOCAL_KEYS = ("SPLUNK_CERT_SHA256", "SPLUNK_CA_BUNDLE")


def _read_config(destination):
    try:
        values = json.loads((destination / ".env.connections").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return values if isinstance(values, dict) else {}


def _write_config(destination, config):
    secret_file = destination / ".env.connections.tmp"
    secret_file.write_text(json.dumps(config), encoding="utf-8")
    secret_file.chmod(0o600)
    os.replace(secret_file, destination / ".env.connections")


def pin_splunk_certificate(destination):
    """Record the certificate the configured Splunk server presents now.

    Run only on the company network, and compare the printed fingerprint with
    the Splunk administrator's before relying on it.
    """
    config = _read_config(destination)
    host, port = config.get("SPLUNK_HOST", ""), config.get("SPLUNK_PORT", "")
    if not host or not port.isdigit():
        raise SystemExit("请先导入 Splunk 连接配置。")
    der = ssl.PEM_cert_to_DER_cert(ssl.get_server_certificate((host, int(port)), timeout=10))
    config["SPLUNK_CERT_SHA256"] = hashlib.sha256(der).hexdigest()
    _write_config(destination, config)
    return ":".join(config["SPLUNK_CERT_SHA256"][i:i + 2] for i in range(0, 64, 2)).upper()


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
    previous = _read_config(destination)
    if previous.get("SPLUNK_HOST") == config.get("SPLUNK_HOST"):
        config.update((k, previous[k]) for k in LOCAL_KEYS if previous.get(k))
    # Keep operator-owned paths private too. Copy the required runtime locally,
    # so neither launching the old service nor retaining its directory is needed.
    with tempfile.TemporaryDirectory(dir=destination) as temp:
        staging = Path(temp)
        for name, target in (("downtime_insights.sqlite3", "downtime.sqlite3"), ("qaqc.sqlite3", "qaqc.sqlite3")):
            src = toolbox / "data" / name
            if not src.is_file():
                continue
            snapshot = staging / target
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
    parser.add_argument("--toolbox", type=Path)
    parser.add_argument("--apex", type=Path)
    parser.add_argument(
        "--pin-splunk-cert",
        action="store_true",
        help="固定当前 Splunk 服务器证书（仅在公司网络中执行，并与管理员核对指纹）",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / ".runtime"
        / "operations-workbench",
    )
    args = parser.parse_args()
    if not args.pin_splunk_cert and not (args.toolbox and args.apex):
        parser.error("请同时指定 --toolbox 和 --apex，或使用 --pin-splunk-cert。")
    if args.toolbox and args.apex:
        install(args.toolbox, args.apex, args.destination)
        print("运维数据与本机连接配置已就绪。")
    if args.pin_splunk_cert:
        print("已固定 Splunk 服务器证书 SHA-256：" + pin_splunk_certificate(args.destination))
