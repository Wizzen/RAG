"""Private, module-scoped source configuration. Never modify process environment."""

import json
import os
from functools import lru_cache
from pathlib import Path

from django.conf import settings


def runtime_dir():
    directory = os.getenv("OPERATIONS_RUNTIME_DIR")
    root = Path(__file__).resolve().parents[1]
    if settings.configured:
        directory = getattr(settings, "OPERATIONS_RUNTIME_DIR", None) or directory
        root = Path(settings.BASE_DIR)
    return Path(directory or root / ".runtime" / "operations-workbench")


@lru_cache(maxsize=2)
def _load(path, version):
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return values if isinstance(values, dict) else {}


def option(name, default=""):
    override = os.getenv("OPERATIONS_" + name)
    if override is not None:
        return override
    if name == "JAVA_HOME":
        return os.getenv(name, default)
    path = runtime_dir() / ".env.connections"
    try:
        stat = path.stat()
    except OSError:
        return default
    return _load(path, (stat.st_ino, stat.st_mtime_ns)).get(name, default)
