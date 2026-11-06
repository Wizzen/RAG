from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.staticfiles import finders
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve, reverse

from operations_workbench.sources import SourceError
from operations_workbench.vendor import qaqc_cache
from vehicle_tracker import integrations, views

QAQC_SCHEMA = """
CREATE TABLE workorders (workorderid INTEGER PRIMARY KEY, wonum TEXT, status TEXT, worktype TEXT, failurecode TEXT,
  origrecordid TEXT, location TEXT, assetnum TEXT, description TEXT, description_en TEXT, long_text TEXT,
  report_local TEXT, report_day TEXT, actfinish_local TEXT, part_no TEXT, part_nos TEXT, part_source TEXT,
  attraction_code TEXT, role TEXT, is_mrb INTEGER, has_mrb INTEGER, case_root TEXT, disposition TEXT,
  comment_count INTEGER, report_count INTEGER, own_comment INTEGER);
CREATE TABLE tasks (workorderid INTEGER PRIMARY KEY, wonum TEXT, parent TEXT, taskid TEXT, status TEXT,
  failurecode TEXT, description TEXT, description_en TEXT, long_text TEXT, finish_local TEXT, changed_local TEXT,
  changeby TEXT);
CREATE TABLE part_events (part_no TEXT, wonum TEXT, report_day TEXT, attraction_code TEXT, status TEXT,
  mrb INTEGER, case_count INTEGER, last_mrb_day TEXT, orphan INTEGER);
CREATE TABLE months (month TEXT PRIMARY KEY, synced_through TEXT, synced_at TEXT, row_count INTEGER, task_count INTEGER);
INSERT INTO workorders VALUES (1, '7000001', 'COMP', 'QA', NULL, NULL, 'SDLFLSDMT', NULL, 'MT SDLDWARMV0222 检测',
  NULL, NULL, '2026-10-01 09:00:00', '2026-10-01', NULL, 'SDLDWARMV0222', '["SDLDWARMV0222"]', 'description',
  'SDLFLSDMT', 'inspection', 0, 1, NULL, NULL, 0, 0, 0);
INSERT INTO workorders VALUES (2, '7000002', 'COMP', 'QA', 'MRB', '7000001', 'SDLFLSDMT', NULL, 'MRB SDLDWARMV0222',
  NULL, '可以使用', '2026-10-02 09:00:00', '2026-10-02', NULL, 'SDLDWARMV0222', '["SDLDWARMV0222"]', 'description',
  'SDLFLSDMT', 'mrb', 1, 1, '7000001', 'accept', 1, 0, 1);
INSERT INTO tasks VALUES (3, '7000003', '7000002', '10', 'COMP', NULL, '工程师处置', NULL, '可以使用', NULL, NULL, NULL);
INSERT INTO part_events VALUES ('SDLDWARMV0222', '7000001', '2026-10-01', 'SDLFLSDMT', 'COMP', 1, 1, '2026-10-02', 0);
INSERT INTO months VALUES ('2026-10', '2026-10-31', '2026-11-01T00:00:00', 2, 1);
"""


class VehicleTrackerTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = SimpleNamespace(is_authenticated=True, is_staff=False, is_superuser=False)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.runtime = Path(folder.name)
        override = override_settings(OPERATIONS_RUNTIME_DIR=self.runtime)
        override.enable()
        self.addCleanup(override.disable)

    def make_qaqc_snapshot(self):
        with closing(sqlite3.connect(self.runtime / "qaqc.sqlite3")) as conn:
            conn.executescript(QAQC_SCHEMA)

    def request(self, method="get", path="/tools/vehicle-tracker", **kwargs):
        request = getattr(self.factory, method)(path, **kwargs)
        request.user = self.user
        return request

    def test_page_requires_existing_login_and_registers_only_new_routes(self):
        request = self.request()
        request.user = AnonymousUser()
        response = views.index(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, "/accounts/login/")
        self.assertEqual(reverse("vehicle_tracker:index"), "/tools/vehicle-tracker")
        self.assertEqual(resolve("/tools/vehicle-tracker").func, views.index)
        self.assertEqual(resolve("/kb/ask/").view_name, "kb:ask")
        self.assertEqual(resolve("/dashboard/").view_name, "dashboard:index")

    def test_page_has_complete_workspace_versioned_local_modules_and_no_external_requests(self):
        with patch("operations_workbench.sources.run_action") as live:
            response = views.index(self.request())
        live.assert_not_called()
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        self.assertIn("no-cache", response.headers["Cache-Control"])
        for element in ["vt-canvas", "vt-spec", "vt-records", "vt-drawing", "vt-boot-error"]:
            self.assertIn(f'id="{element}"', text)
        imports = json.loads(re.search(r'<script type="importmap">(.*?)</script>', text, re.S)[1])["imports"]
        version = re.search(r'vehicle-tracker\.mjs\?v=([0-9a-f]{10})"', text)[1]
        for name in ["core", "model", "pointer", "report", "pdf"]:
            path = f"/static/vehicle_tracker/js/vehicle-tracker-{name}.mjs"
            self.assertEqual(imports[path], f"{path}?v={version}")
        self.assertNotRegex(text, r'(?:src|href)="https?://')
        for path in re.findall(r'(?:src|href)="(/static/vehicle_tracker/[^"?]+)', text):
            self.assertIsNotNone(finders.find(path.removeprefix("/static/")), path)
        for path in ["vendor/three/three.module.min.js", "vendor/three/OrbitControls.js",
                     "vendor/three/LICENSE", "vendor/pdfjs/pdf.min.mjs", "vendor/pdfjs/pdf.worker.min.mjs",
                     "vendor/pdfjs/LICENSE", "vendor/xlsx-0.20.3.full.min.js", "vendor/xlsx-0.20.3.LICENSE"]:
            self.assertIsNotNone(finders.find("vehicle_tracker/" + path), path)

    def test_qaqc_without_snapshot_is_explicitly_unavailable_not_zero_results(self):
        response = views.qaqc_detail(self.request("post", data='{"part_no":"SDLDWARMV0222"}', content_type="application/json"))
        self.assertEqual(response.status_code, 503)
        self.assertIn("尚未导入", json.loads(response.content)["detail"])

    def test_api_authentication_method_and_invalid_codes_never_query_source(self):
        self.assertEqual(views.qaqc_detail(self.request()).status_code, 405)
        request = self.request("post", data='{"part_no":"SDLDWARMV0222"}', content_type="application/json")
        request.user = AnonymousUser()
        self.assertEqual(views.qaqc_detail(request).status_code, 401)
        with patch("operations_workbench.vendor.qaqc_cache.part_detail") as lookup:
            for body in ["bad json", "[]", '{"part_no":"https://evil.test"}', '{"part_no":123}', " " * 4097]:
                self.assertEqual(views.qaqc_detail(self.request("post", data=body, content_type="application/json")).status_code, 400)
        lookup.assert_not_called()

    def test_module_uses_csrf_protection_for_its_post_adapter(self):
        page_request = self.request()
        page = views.index(page_request)
        token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.content.decode())[1]
        middleware = CsrfViewMiddleware(lambda request: None)
        request = self.request("post", data='{"part_no":"SDLDWARMV0222"}', content_type="application/json")
        self.assertEqual(middleware.process_view(request, views.qaqc_detail, (), {}).status_code, 403)
        request.COOKIES[settings.CSRF_COOKIE_NAME] = page_request.META["CSRF_COOKIE"]
        request.META["HTTP_X_CSRFTOKEN"] = token
        self.assertIsNone(middleware.process_view(request, views.qaqc_detail, (), {}))

    def test_missing_external_connection_has_a_working_return_to_tracker(self):
        response = views.external(self.request(data={"code": "3023726"}), "maximo-workorder")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/tools/vehicle-tracker?inspect='))

    def test_external_links_are_fixed_destinations_with_validated_codes(self):
        for destination in integrations.DESTINATIONS:
            response = views.external(self.request(data={"code": "3023726"}), destination)
            self.assertEqual(response.status_code, 302)
            parsed = urlsplit(response.url)
            self.assertEqual(parsed.netloc, "")
            self.assertEqual(parsed.path, "/tools/vehicle-tracker")
            self.assertEqual(parse_qs(parsed.query), {"inspect": [destination], "code": ["3023726"]})
        response = views.external(self.request(data={"code": "//evil.test"}), "maximo-workorder")
        self.assertEqual(response.status_code, 400)

    def test_qaqc_part_detail_reads_the_local_snapshot(self):
        self.make_qaqc_snapshot()
        initial = (self.runtime / "qaqc.sqlite3").read_bytes()
        response = views.qaqc_detail(self.request("post", data='{"part_no":"SDLDWARMV0222","url":"https://evil.test"}', content_type="application/json"))
        data = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["totals"], {"inspections": 1, "mrb_inspections": 1, "mrb_cases": 1, "mrb_rate": 1.0})
        self.assertEqual([row["wonum"] for row in data["workorders"]], ["7000002", "7000001"])
        self.assertEqual(data["dispositions"][0]["count"], 1)
        self.assertEqual(data["data_through"], "2026-10-31")
        missing = views.qaqc_detail(self.request("post", data='{"part_no":"SDLDWARMV9999"}', content_type="application/json"))
        self.assertEqual(missing.status_code, 400)
        detail = integrations.record_detail("qaqc-workorder", "7000002")
        self.assertEqual(detail["data"]["workorder"]["tasks"][0]["taskid"], "10")
        self.assertEqual(detail["data"]["origin"]["wonum"], "7000001")
        self.assertEqual((self.runtime / "qaqc.sqlite3").read_bytes(), initial)

    def test_unreadable_snapshot_does_not_look_like_empty_qaqc(self):
        (self.runtime / "qaqc.sqlite3").write_text("not a database")
        with self.assertRaises(integrations.ToolboxUnavailable):
            integrations.part_detail("SDLDWARMV0222")

    def test_original_indexeddb_and_backup_contract_remains_unchanged(self):
        source = (Path(settings.BASE_DIR) / "static/vehicle_tracker/js/vehicle-tracker-core.mjs").read_text()
        self.assertIn("indexedDB.open('shdr-vehicle-tracker',2)", source)
        self.assertIn("export const FORMAT = 'shdr-vehicle-tracker'", source)
        self.assertIn("export const VERSION = 2", source)


class InlineRecordsTests(SimpleTestCase):
    def test_record_kind_uses_only_fixed_read_only_operations(self):
        for kind,action,params in [("maximo-workorder","maximo.workorder",{"wonum":"3023726"}),
                ("job-plan","maximo.jobplan",{"jpnum":"3023726","include_workorders":False})]:
            with patch("operations_workbench.sources.run_action",return_value={"tasks":[]}) as live:
                result=integrations.record_detail(kind,"3023726")
            self.assertEqual(result["data"],{"tasks":[]})
            live.assert_called_once_with(action,params)
        with patch("operations_workbench.vendor.qaqc_cache.workorder_detail",return_value={"tasks":[]}) as local:
            self.assertEqual(integrations.record_detail("qaqc-workorder","3023726")["data"],{"tasks":[]})
        local.assert_called_once_with("3023726")
        with self.assertRaises(ValueError): integrations.record_detail("unknown","3023726")

    def test_maximo_outage_uses_labeled_cached_qaqc_and_no_fake_success(self):
        offline=SourceError("Oracle 不可用",503)
        with patch("operations_workbench.sources.run_action",side_effect=offline), \
                patch("operations_workbench.vendor.qaqc_cache.workorder_detail",return_value={"workorder":{"wonum":"3023726"}}):
            self.assertIn("暂不可用",integrations.record_detail("maximo-workorder","3023726")["notice"])
        with patch("operations_workbench.sources.run_action",side_effect=offline), \
                patch("operations_workbench.vendor.qaqc_cache.workorder_detail",side_effect=qaqc_cache.QaqcMiss("无记录")):
            with self.assertRaisesRegex(integrations.ToolboxUnavailable,"Oracle"): integrations.record_detail("maximo-workorder","3023726")
