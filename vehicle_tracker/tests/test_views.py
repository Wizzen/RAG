from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.staticfiles import finders
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve, reverse

from vehicle_tracker import integrations, views


@override_settings(VEHICLE_TRACKER_TOOLBOX_URL="")
class VehicleTrackerTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = SimpleNamespace(is_authenticated=True, is_staff=False, is_superuser=False)

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
        with patch("vehicle_tracker.integrations.httpx.post") as post:
            response = views.index(self.request())
        post.assert_not_called()
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

    def test_qaqc_without_source_is_explicitly_unavailable_not_zero_results(self):
        with patch("vehicle_tracker.integrations.httpx.post") as post:
            response = views.qaqc_detail(self.request("post", data='{"part_no":"SDLDWARMV0222"}', content_type="application/json"))
        post.assert_not_called()
        self.assertEqual(response.status_code, 503)
        self.assertIn("尚未连接", json.loads(response.content)["detail"])

    def test_api_authentication_method_and_invalid_codes_never_query_source(self):
        self.assertEqual(views.qaqc_detail(self.request()).status_code, 405)
        request = self.request("post", data='{"part_no":"SDLDWARMV0222"}', content_type="application/json")
        request.user = AnonymousUser()
        self.assertEqual(views.qaqc_detail(request).status_code, 401)
        with patch("vehicle_tracker.integrations.httpx.post") as post:
            for body in ["bad json", "[]", '{"part_no":"https://evil.test"}', '{"part_no":123}', " " * 4097]:
                self.assertEqual(views.qaqc_detail(self.request("post", data=body, content_type="application/json")).status_code, 400)
        post.assert_not_called()

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

    @override_settings(VEHICLE_TRACKER_TOOLBOX_URL="https://toolbox.example.test")
    def test_external_links_are_fixed_destinations_with_validated_codes(self):
        for destination, (path, _) in integrations.DESTINATIONS.items():
            response = views.external(self.request(data={"code": "3023726"}), destination)
            self.assertEqual(response.status_code, 302)
            parsed = urlsplit(response.url)
            self.assertEqual(parsed.netloc, "")
            self.assertEqual(parsed.path, "/tools/vehicle-tracker")
            self.assertEqual(parse_qs(parsed.query), {"inspect": [destination], "code": ["3023726"]})
        target = urlsplit(integrations.destination_url("job-plan", "QAL4070051"))
        self.assertEqual(parse_qs(target.query), {"tab": ["jp"], "open": ["jp:QAL4070051"]})
        response = views.external(self.request(data={"code": "//evil.test"}), "maximo-workorder")
        self.assertEqual(response.status_code, 400)

    @override_settings(VEHICLE_TRACKER_TOOLBOX_URL="https://toolbox.example.test")
    def test_qaqc_proxy_only_forwards_the_validated_part_number(self):
        data = {"totals": {"inspections": 3}, "workorders": []}
        with patch("vehicle_tracker.integrations.httpx.post", return_value=httpx.Response(200, json=data)) as post:
            response = views.qaqc_detail(self.request("post", data='{"part_no":"SDLDWARMV0222","url":"https://evil.test"}', content_type="application/json", HTTP_COOKIE="sessionid=private"))
        self.assertEqual(json.loads(response.content), data)
        self.assertEqual(post.call_args.args, ("https://toolbox.example.test/api/qaqc/parts/detail",))
        self.assertEqual(post.call_args.kwargs["json"], {"part_no": "SDLDWARMV0222"})
        self.assertNotIn("cookies", post.call_args.kwargs)
        self.assertNotIn("headers", post.call_args.kwargs)
        self.assertFalse(post.call_args.kwargs["follow_redirects"])

    @override_settings(VEHICLE_TRACKER_TOOLBOX_URL="https://toolbox.example.test")
    def test_source_outages_authentication_and_bad_data_do_not_look_like_empty_qaqc(self):
        for response in [httpx.Response(401), httpx.Response(500), httpx.Response(200, text="login"), httpx.Response(200, json=[])]:
            with patch("vehicle_tracker.integrations.httpx.post", return_value=response):
                with self.assertRaises(integrations.ToolboxUnavailable):
                    integrations.part_detail("SDLDWARMV0222")
        with patch("vehicle_tracker.integrations.httpx.post", side_effect=httpx.ConnectError("offline")):
            with self.assertRaises(integrations.ToolboxUnavailable):
                integrations.part_detail("SDLDWARMV0222")

    def test_original_indexeddb_and_backup_contract_remains_unchanged(self):
        source = (Path(settings.BASE_DIR) / "static/vehicle_tracker/js/vehicle-tracker-core.mjs").read_text()
        self.assertIn("indexedDB.open('shdr-vehicle-tracker',2)", source)
        self.assertIn("export const FORMAT = 'shdr-vehicle-tracker'", source)
        self.assertIn("export const VERSION = 2", source)
