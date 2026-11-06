"""MinerU 协议适配层：4.0.x File API 与 4.1.x Tasks API（全离线，httpx.MockTransport）。

覆盖版本探测与分叉、4.1 的「声明→上传→轮询→下载 result.zip」全流程，
以及两种协议产物归一后的 md / images / content_list 形状（chunk 溯源与
PDF 红圈定位都靠它）。
"""
import base64
import hashlib
import io
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx

from django.test import SimpleTestCase

from kb import pipeline

# 1×1 PNG：作为 4.1 结果包里的插图成员
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")
_PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(_PNG).decode()
_PNG_HASH = hashlib.sha256(_PNG).hexdigest() + ".png"

_BASE = "http://ocr.test"

# markdown：一张 4.1 zip 成员式的相对引用 + 一张内联 data URL
_MD = (
    "## 检验报告\n\n"
    "游乐设施为创极速光轮，工作单号 7336768。\n\n"
    "![示意图](images/rel.png)\n\n"
    f"![内联]({_PNG_DATA_URL})\n"
)

# structured_content：4.0/4.1 同构（pages → blocks，bbox 为 0-1 归一化）
_SC = {
    "pages": [
        {"page_idx": 0, "blocks": [
            {"type": "text", "bbox": [0.1, 0.2, 0.3, 0.4], "content": "检验报告"},
            {"type": "table", "bbox": [0.0, 0.05, 1.0, 0.5],
             "content": {"html": "<table><tr><td>扭矩</td><td>12</td></tr></table>",
                         "caption": "扭矩表"}},
            {"type": "image", "bbox": [0.2, 0.2, 0.8, 0.9], "image_source": "images/rel.png"},
        ]},
    ],
}


def _result_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("markdown.md", _MD)
        z.writestr("middle_json.json", json.dumps({"pdf_info": []}))
        z.writestr("structured_content.json", json.dumps(_SC, ensure_ascii=False))
        z.writestr("images/", b"")
        z.writestr("images/rel.png", _PNG)
    return buf.getvalue()


class _ServerBase:
    """假 MinerU：记录收到的请求，便于断言线上协议。"""

    def __init__(self, version: str):
        self.version = version
        self.posts: list[tuple[str, httpx.Request]] = []
        self.puts: list[httpx.Request] = []
        self.gets: list[str] = []
        self.headers_seen: list[str] = []

    def _json(self, payload, status=200):
        return httpx.Response(status, json=payload)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            self.posts.append((path, request))
        elif request.method == "PUT":
            self.puts.append(request)
        else:
            self.gets.append(path)
        return self.handle(request, path)

    # 子类实现
    def handle(self, request, path):
        raise NotImplementedError


class FakeTasksServer(_ServerBase):
    """MinerU 4.1.x：POST /v1/tasks → PUT upload.url → GET /v1/tasks/{id} → result_url。"""

    def __init__(self, version="4.1.7", tiers=("flash", "standard"), content=_result_zip):
        super().__init__(version)
        self.tiers = list(tiers)
        self._content = content() if callable(content) else content
        self.status_after_upload = "pending"
        self.polls = 0

    def _detail(self, status):
        files = [{"file_id": "f1", "file_name": "doc.pdf", "bytes": 12,
                  "status": "completed" if status == "completed" else "running"}]
        if status == "completed":
            files[0]["result_url"] = f"{_BASE}/v1/files/f1/content"
        return {"task_id": "t1", "status": status,
                "output_formats": ["default"],
                "progress": {"completed": 1 if status == "completed" else 0,
                             "failed": 0, "total": 1},
                "files": files}

    def handle(self, request, path):
        if path == "/v1/health":
            return self._json({"status": "ok", "version": self.version})
        if path == "/v1/tiers":
            return self._json({"object": "list",
                               "data": [{"id": t, "description": t} for t in self.tiers]})
        if path == "/v1/tasks" and request.method == "POST":
            self.created = json.loads(request.content)
            self.created_headers = dict(request.headers)
            return self._json({
                "task_id": "t1", "status": "waiting-file",
                "files": [{"file_id": "f1", "file_name": "doc.pdf", "status": "waiting-file",
                           "upload": {"url": f"{_BASE}/v1/tasks/t1/uploads/f1",
                                      "headers": {"Content-Type": "application/octet-stream"},
                                      "expires_at": 9999999999}}]}, 202)
        if path == "/v1/tasks/t1/uploads/f1" and request.method == "PUT":
            self.upload_ct = request.headers.get("content-type")
            self.uploaded = request.content
            return self._json({"task_id": "t1", "file_id": "f1", "status": "pending"})
        if path == "/v1/tasks/t1" and request.method == "GET":
            self.polls += 1
            # 首查 running、复查 completed：确保轮询循环真的在跑
            return self._json(self._detail("running" if self.polls == 1 else "completed"))
        if path == "/v1/files/f1/content":
            return httpx.Response(200, content=self._content,
                                  headers={"Content-Type": "application/zip"})
        raise AssertionError(f"unexpected request {request.method} {path}")


class FakeFileApiServer(_ServerBase):
    """MinerU 4.0.12：/v1/file_parse（inline 或 file_id）+ /v1/tasks/{id}/result。"""

    def __init__(self, version="4.0.12"):
        super().__init__(version)
        self.uploads: list[str] = []

    def _completed(self):
        return {"task_id": "t9", "status": "completed",
                "files": [{"status": "completed",
                           "content": {"markdown": _MD, "structured_content": _SC}}]}

    def handle(self, request, path):
        if path == "/v1/health":
            return self._json({"status": "ok", "version": self.version, "features": {}})
        if path == "/v1/uploads" and request.method == "POST":
            self.uploads.append("register")
            self.register_body = json.loads(request.content)
            return self._json({"id": "u1", "status": "pending"})
        if path == "/v1/uploads/u1/content" and request.method == "PUT":
            self.uploads.append("content")
            self.uploaded = request.content
            return self._json({"id": "u1", "status": "completing"})
        if path == "/v1/uploads/u1/complete" and request.method == "POST":
            self.uploads.append("complete")
            return self._json({"id": "u1", "status": "completed", "file": {"id": "file-77"}})
        if path == "/v1/file_parse" and request.method == "POST":
            self.parse_body = json.loads(request.content)
            return self._json(self._completed())
        if path == "/v1/tasks/t9/result":
            return self._json(self._completed())
        raise AssertionError(f"unexpected request {request.method} {path}")


class _MineruTestsMixin:
    def setUp(self):
        pipeline._MINERU_VERSION_CACHE.clear()
        self.addCleanup(pipeline._MINERU_VERSION_CACHE.clear)

    def _patch(self, server, backend="pipeline"):
        transport = server if isinstance(server, httpx.MockTransport) \
            else httpx.MockTransport(server)
        return [
            patch.object(pipeline, "_MINERU_TRANSPORT", transport),
            patch.object(pipeline, "mineru_settings", return_value={
                "api_base": _BASE, "api_key": "", "backend": backend, "lang": "ch"}),
            patch.object(pipeline, "_MINERU_POLL_SECONDS", 0),
        ]

    def _start(self, server, backend="pipeline"):
        """注入 transport、配置与轮询间隔（0 免等待）。"""
        for c in self._patch(server, backend):
            c.start()
            self.addCleanup(c.stop)

    def _ocr(self, server, raw=b"%PDF-1.7 fake", backend="pipeline"):
        self._start(server, backend)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "大型 报告.pdf"
            path.write_bytes(raw)
            return pipeline._ocr_pdf(path)


class TasksApi41Tests(_MineruTestsMixin, SimpleTestCase):
    def test_protocol_detected_from_health_version(self):
        self._start(FakeTasksServer())
        with pipeline._mineru_client() as client:
            self.assertEqual(pipeline._mineru_protocol(client, _BASE), ("tasks", "4.1.7"))

    def test_end_to_end_returns_md_images_and_content_list(self):
        server = FakeTasksServer()
        md, images, content_list = self._ocr(server)

        # 4.1 请求体：没有 inline，档位是 default
        self.assertEqual(server.created["output_formats"], ["default"])
        self.assertNotIn("source", server.created["files"][0])
        self.assertEqual(server.created["files"][0]["bytes"], len(b"%PDF-1.7 fake"))
        self.assertEqual(server.created["files"][0]["sha256sum"],
                         hashlib.sha256(b"%PDF-1.7 fake").hexdigest())
        self.assertEqual(server.created["tier"], "standard")   # backend=pipeline → standard
        self.assertEqual(server.created_headers.get("x-mineru-task-lease-seconds"), "3600")
        self.assertEqual(server.uploaded, b"%PDF-1.7 fake")
        self.assertEqual(server.upload_ct, "application/octet-stream")
        self.assertGreaterEqual(server.polls, 2)               # 真的轮询过

        # 插图：相对引用与 data URL 都归一成 images/<内容哈希>.png
        self.assertNotIn("data:image", md)
        self.assertEqual(md.count(f"](images/{_PNG_HASH})"), 2)
        self.assertEqual(images, {_PNG_HASH: _PNG_DATA_URL})

        # content_list：bbox 放大到 0-1000，表格 html → table_body
        text, table, image = content_list
        self.assertEqual(text["text"], "检验报告")
        self.assertEqual(text["bbox"], [100.0, 200.0, 300.0, 400.0])
        self.assertEqual(text["page_idx"], 0)
        self.assertIn("<table>", table["table_body"])
        self.assertEqual(table["table_caption"], ["扭矩表"])
        self.assertEqual(image["type"], "image")
        self.assertEqual(image["img_path"], f"images/{_PNG_HASH}")   # zip 成员也接上了

    def test_zip_without_structured_content_still_returns_text(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("markdown.md", "只有正文")
        server = FakeTasksServer(content=buf.getvalue())
        md, images, content_list = self._ocr(server)
        self.assertEqual(md, "只有正文")
        self.assertIsNone(content_list)

    def test_failed_task_raises_server_error(self):
        class Failed(FakeTasksServer):
            def handle(self, request, path):
                if path == "/v1/tasks/t1" and request.method == "GET":
                    return self._json({"task_id": "t1", "status": "failed", "files": [],
                                       "error": {"code": "bad_pdf", "message": "PDF 已加密"}})
                return super().handle(request, path)

        with self.assertRaises(RuntimeError) as ctx:
            self._ocr(Failed())
        self.assertIn("bad_pdf", str(ctx.exception))

    def test_missing_result_url_is_reported(self):
        class NoResult(FakeTasksServer):
            def _detail(self, status):
                return {"task_id": "t1", "status": "completed",
                        "files": [{"file_id": "f1", "status": "completed"}]}

        with self.assertRaises(RuntimeError) as ctx:
            self._ocr(NoResult())
        self.assertIn("result_url", str(ctx.exception))

    def test_corrupt_result_package_is_reported(self):
        server = FakeTasksServer(content=b"not a zip at all")
        with self.assertRaises(RuntimeError) as ctx:
            self._ocr(server)
        self.assertIn("不是可解压", str(ctx.exception))

    def test_tier_falls_back_to_one_the_server_offers(self):
        server = FakeTasksServer(tiers=("flash", "basic"))
        self._ocr(server)
        self.assertEqual(server.created["tier"], "basic")     # standard 没开 → 按优先级退到 basic

    def test_backend_tier_passed_through(self):
        server = FakeTasksServer()
        self._ocr(server, backend="vlm")
        self.assertEqual(server.created["tier"], "flash")     # 旧 vlm → flash


class FileApi40Tests(_MineruTestsMixin, SimpleTestCase):
    def test_small_pdf_is_inlined(self):
        server = FakeFileApiServer()
        md, images, content_list = self._ocr(server)

        self.assertEqual(server.parse_body["output_formats"], ["markdown", "structured_content"])
        source = server.parse_body["files"][0]["source"]
        self.assertEqual(source["type"], "inline")
        # 中文名被压成 MinerU 接受的 ASCII 名，内容仍以 base64 随请求发出
        self.assertRegex(source["name"], r"^doc_[0-9a-f]{6}\.pdf$")
        self.assertEqual(base64.b64decode(source["data"]), b"%PDF-1.7 fake")
        self.assertEqual(content_list[0]["bbox"], [100.0, 200.0, 300.0, 400.0])
        self.assertIn(_PNG_HASH, images)
        self.assertNotIn("data:image", md)

    def test_large_pdf_uses_uploads_flow(self):
        server = FakeFileApiServer()
        raw = b"%PDF" + b"x" * (pipeline._MINERU_INLINE_MAX + 10)
        self._ocr(server, raw=raw)
        self.assertEqual(server.uploads, ["register", "content", "complete"])
        self.assertEqual(server.register_body["purpose"], "parse")
        self.assertEqual(server.register_body["sha256sum"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(server.uploaded, raw)
        self.assertEqual(server.parse_body["files"][0]["source"],
                         {"type": "file_id", "file_id": "file-77"})

    def test_protocol_is_file_for_4_0(self):
        self._start(FakeFileApiServer())
        with pipeline._mineru_client() as client:
            self.assertEqual(pipeline._mineru_protocol(client, _BASE), ("file", "4.0.12"))


class _Server3x:
    """MinerU 3.x：没有 /v1，只有根 /health。"""

    def __init__(self):
        self.probed: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.probed.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "3.1.5"})
        return httpx.Response(404, json={"error": "not found"})


class LegacyRejectionTests(_MineruTestsMixin, SimpleTestCase):
    def test_3x_is_rejected_with_actionable_message(self):
        server = _Server3x()
        with self.assertRaises(RuntimeError) as ctx:
            self._ocr(server)
        self.assertIn("不受支持", str(ctx.exception))
        self.assertIn("4.1.x", str(ctx.exception))
        self.assertEqual(server.probed, ["/v1/health", "/v1/models", "/health"])

    def test_probe_reports_unsupported_without_raising(self):
        self._start(_Server3x())
        result = pipeline.mineru_probe(_BASE)
        self.assertFalse(result["ok"])
        self.assertIn("不受支持", result["detail"])

    def test_probe_reports_version_and_protocol(self):
        self._start(FakeTasksServer())
        result = pipeline.mineru_probe(_BASE)
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], "4.1.7")
        self.assertEqual(result["protocol"], "tasks")
        self.assertIn("Tasks API", result["detail"])

    def test_probe_reports_unsupported_when_version_unreadable(self):
        """报不出版本号（非 4.x 服务）：算连通但给 warning，不谎报就绪。"""
        def handler(request):
            if request.url.path == "/v1/health":
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(404, json={"error": "not found"})
        self._start(handler)
        result = pipeline.mineru_probe(_BASE)
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("warning"))

    def test_unreachable_service_is_reported_as_connection_failure(self):
        def refuse(request):
            raise httpx.ConnectError("connection refused")
        self._start(refuse)
        result = pipeline.mineru_probe(_BASE)
        self.assertFalse(result["ok"])
        self.assertIn("连接失败", result["detail"])


class VersionParsingTests(_MineruTestsMixin, SimpleTestCase):
    def test_version_tuple(self):
        self.assertEqual(pipeline._mineru_version_tuple("4.1.7"), (4, 1))
        self.assertEqual(pipeline._mineru_version_tuple("mineru-4.0"), (4, 0))
        self.assertIsNone(pipeline._mineru_version_tuple(""))
        self.assertIsNone(pipeline._mineru_version_tuple("latest"))

    def test_future_version_uses_tasks_protocol(self):
        # 4.2/5.x 这类还没适配的新版本先按 Tasks API 试，而不是退回必定 404 的 File API
        for version in ("4.1.7", "4.2.0", "5.0.1"):
            with self.subTest(version=version):
                pipeline._MINERU_VERSION_CACHE.clear()
                self._start(FakeTasksServer(version=version))
                with pipeline._mineru_client() as client:
                    self.assertEqual(pipeline._mineru_protocol(client, _BASE),
                                     ("tasks", version))

    def test_version_probe_is_cached_per_base(self):
        server = FakeTasksServer()
        self._start(server)
        with pipeline._mineru_client() as client:
            pipeline._mineru_protocol(client, _BASE)
            probes = len(server.gets)
            pipeline._mineru_protocol(client, _BASE)   # 命中缓存，不再发探测请求
        self.assertEqual((probes, len(server.gets)), (1, 1))   # 只探 /v1/health 一次
        self.assertEqual(pipeline._MINERU_VERSION_CACHE[_BASE][1], (4, 1))
