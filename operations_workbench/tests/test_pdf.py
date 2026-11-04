"""Maximo reports must reach the browser intact and render with the locally served PDF.js assets."""

import hashlib
import re
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase

from operations_workbench import sources

ROOT = "/tools/operations/"
STATIC = Path(settings.BASE_DIR) / "static" / "operations_workbench"
PDFJS = STATIC / "vendor" / "pdfjs-4.10.38"
DETAIL = {
    "workorder": {"wonum": "7826101", "status": "WSCH", "description_en": "MRM SDMT RV UNDERSIDE PM",
                  "description_zh": "七个小矮人矿山车底部检查", "jpnum": "JPL4072004"},
    "jobplan": {"jpnum": "JPL4072004", "description": "MRM SDMT RV UNDERSIDE PM",
                "description_zh": "七个小矮人矿山车底部检查"},
    "tasks": [{"taskid": 10, "jptask": 10, "description_en": "Training Certification",
               "description": "Training Certification", "description_zh": "培训及认证",
               "long_description_zh": "在使用我的员工代码完成此步骤时，我声明我已经接受了培训。"}],
    "status_history": [],
}


def report(kind, lang):
    with patch.object(sources, "run_action", return_value=DETAIL):
        return sources.render_pdf(kind, "7826101" if kind == "workorder" else "JPL4072004", lang)


class PdfEndpointTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="pdf-reader", is_staff=True)
        self.client.force_login(user)

    def test_report_bytes_arrive_complete_with_matching_length(self):
        for kind, code in [("workorder", "7826101"), ("jobplan", "JPL4072004")]:
            for lang in ("CN", "EN"):
                expected = report(kind, lang)
                with patch.object(sources, "render_pdf", return_value=expected):
                    response = self.client.get(f"{ROOT}api/pdf/{kind}/{code}/?lang={lang}")
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.streaming)
                self.assertEqual(response["Content-Type"], "application/pdf")
                self.assertEqual(response.content, expected)
                self.assertEqual(int(response["Content-Length"]), len(expected))
                self.assertTrue(response.content.startswith(b"%PDF-"))
                self.assertTrue(response.content.rstrip().endswith(b"%%EOF"))
                self.assertTrue(response["Content-Disposition"].startswith("inline"))

    def test_download_mode_uses_attachment_with_a_safe_filename(self):
        with patch.object(sources, "render_pdf", return_value=b"%PDF-1.4\n%%EOF\n"):
            response = self.client.get(ROOT + 'api/pdf/jobplan/A%22B%2F1/?lang=EN&download=1')
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="jobplan-A_B_1-EN.pdf"')


class PdfAssetTests(SimpleTestCase):
    def test_every_cmap_the_reports_use_is_served_locally(self):
        cmaps = PDFJS / "cmaps"
        for kind in ("workorder", "jobplan"):
            for lang in ("CN", "EN"):
                data = report(kind, lang)
                # Non-embedded CID text is undecodable in PDF.js without its CMap.
                self.assertNotIn(b"/FontFile", data)
                encodings = set(re.findall(rb"/Encoding\s*/([A-Za-z0-9-]+)", data)) - {b"WinAnsiEncoding"}
                self.assertTrue(encodings, "reports are expected to use CID text")
                for name in encodings:
                    self.assertTrue((cmaps / f"{name.decode()}.bcmap").is_file(), name)

    def test_viewer_configures_local_cmaps_fonts_and_worker(self):
        script = (STATIC / "workbench.mjs").read_text(encoding="utf-8")
        base = re.search(r"const PDFJS_BASE = '([^']+)'", script).group(1)
        self.assertEqual(base, "/static/operations_workbench/vendor/pdfjs-4.10.38/")
        for option in ("cMapUrl: base + 'cmaps/'", "cMapPacked: true", "standardFontDataUrl: base + 'standard_fonts/'"):
            self.assertIn(option, script)
        self.assertNotIn("vehicle_tracker/vendor/pdfjs", script)
        self.assertNotRegex(script, r"https?://(?!www\.w3\.org)")
        relative = base.removeprefix(settings.STATIC_URL)
        for name in ("pdf.min.mjs", "pdf.worker.min.mjs", "cmaps/UniGB-UCS2-H.bcmap",
                     "standard_fonts/LiberationSans-Regular.ttf", "standard_fonts/FoxitSerif.pfb", "LICENSE"):
            self.assertTrue(finders.find(relative + name), name)

    def test_vendored_pdfjs_matches_its_recorded_release(self):
        library = (PDFJS / "pdf.min.mjs").read_bytes()
        self.assertIn(b'"4.10.38"', library)
        self.assertIn(b'"4.10.38"', (PDFJS / "pdf.worker.min.mjs").read_bytes())
        attribution = (STATIC / "vendor" / "ATTRIBUTION.txt").read_text(encoding="utf-8")
        self.assertIn(hashlib.sha256(library).hexdigest(), attribution)
        self.assertGreater(len(list((PDFJS / "cmaps").glob("*.bcmap"))), 150)
