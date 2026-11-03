"""Execute a single fixed, read-only operation without importing Django site settings."""

import json
import sys

from .sources import SourceError, _run_direct


def main():
    try:
        request = json.loads(sys.stdin.read(65536))
        data = _run_direct(request["action"], request["params"])
        reply = {"ok": True, "data": data}
    except SourceError as exc:
        reply = {"ok": False, "message": str(exc), "status": exc.status}
    except Exception:  # noqa: BLE001 -- transport errors must not disclose private configuration.
        reply = {"ok": False, "message": "数据源暂未返回可用结果。", "status": 503}
    print(json.dumps(reply, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
