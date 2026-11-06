import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import pymupdf
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, override_settings

from kb.models import KnowledgeBase, Document
from vehicle_tracker import documents


class DrawingDocumentsTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.tmp.name, DATA_DIR=Path(self.tmp.name)/"data")
        self.override.enable()
        self.addCleanup(self.override.disable)
        documents.scan_pdf.cache_clear()
        self.user = get_user_model().objects.create_user(username="drawing-reader")
        self.admin = get_user_model().objects.create_user(username="drawing-admin", is_staff=True)
        self.kb = KnowledgeBase.objects.create(name="授权手册", slug="drawing-public")
        self.private = KnowledgeBase.objects.create(name="其他部门资料", slug="drawing-private", department="Other")
        pdf = pymupdf.open()
        page = pdf.new_page(width=600, height=800)
        page.insert_text((80, 120), "SDLDWARMV0222 / SDLDWARMV02220", fontsize=14)
        page = pdf.new_page(width=600, height=800)
        page.insert_text((90, 130), "SDLDWARMV0222", fontsize=14)
        page.set_rotation(90)
        pdf.new_page(width=600, height=800)
        pdf.save(Path(self.tmp.name)/"drawing.pdf")
        pdf.close()
        self.doc = Document.objects.create(kb=self.kb, original_name="手册.pdf", file="drawing.pdf", file_type="pdf", status="completed")
        self.secret = Document.objects.create(kb=self.private, original_name="机密手册.pdf", file="drawing.pdf", file_type="pdf", status="completed")
        self.client.force_login(self.user)

    def url(self, doc=None, kind="location"):
        return f"/tools/vehicle-tracker/api/documents/{(doc or self.doc).id}/{kind}"

    def test_document_list_exact_text_pages_without_retrieval_or_config(self):
        with patch("kb.retriever.search", side_effect=AssertionError("retrieval forbidden")), patch("kb.config.get_config", side_effect=AssertionError("config writes forbidden")):
            result = documents.search(self.user, "sdldwarmv0222 Item 50")
        self.assertEqual(len(result["documents"]), 1)
        row = result["documents"][0]
        self.assertEqual(row["name"], "手册.pdf")
        self.assertEqual(row["hit_count"], 2)  # no false match of 02220
        self.assertEqual([item["page"] for item in row["occurrences"]], [0, 1])
        self.assertEqual(len(documents.search(self.admin,"SDLDWARMV0222")["documents"]), 2)

    def test_normal_and_rotated_boxes_match_rendered_page_coordinates(self):
        data = documents.locations(self.doc, "SDLDWARMV0222")
        for hit in data["occurrences"]:
            x0,y0,x1,y1 = hit["boxes"][0]
            self.assertTrue(0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000)
        normal, rotated = [item["boxes"][0] for item in data["occurrences"]]
        self.assertLess(normal[0], 200)
        self.assertGreater(rotated[0], 800)
        self.assertLess(rotated[1], 200)
        response = self.client.get(self.url(kind="page.png"), {"page": 1, "version": data["version"]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\x89PNG"))
        self.assertIn("no-store", response["Cache-Control"])

    def test_provenance_fallback_is_labeled_region_and_rejects_bad_boxes(self):
        rows = [{"page_idx":2,"text":"SDLDWARMV0222","bbox":[100,200,400,300]},
                {"page_idx":2,"text":"SDLDWARMV0222","bbox":[-1,0,200,300]},
                {"page_idx":0,"text":"SDLDWARMV0222","bbox":[0,0,1000,1000]}]
        with patch("kb.provenance.load_content_list",return_value=rows):
            data=documents.locations(self.doc,"SDLDWARMV0222")
        self.assertEqual([row["precision"] for row in data["occurrences"]], ["text","text","region"])
        self.assertEqual(data["occurrences"][2]["boxes"], [[100,200,400,300]])

    def test_permission_is_rechecked_for_metadata_and_images(self):
        for kind in ["location","page.png"]:
            self.assertEqual(self.client.get(self.url(self.secret,kind),{"drawing":"SDLDWARMV0222"}).status_code,404)
        self.kb.department="Other"
        self.kb.save(update_fields=["department"])
        self.assertEqual(self.client.get(self.url(),{"drawing":"SDLDWARMV0222"}).status_code,404)

    def test_invalid_page_stale_pdf_and_authentication_are_explicit(self):
        for query in [{"page":-1},{"page":999},{"page":"invalid"},{"page":0,"version":"old"}]:
            self.assertEqual(self.client.get(self.url(),{"drawing":"SDLDWARMV0222",**query}).status_code,400)
        response=self.client.get(self.url(),{"drawing":"SDLDWARMV0222","page":2})
        self.assertEqual(response.json()["boxes"],[])
        self.client.logout()
        for path in [self.url(),self.url(kind="page.png"),"/tools/vehicle-tracker/api/drawings/SDLDWARMV0222/documents"]:
            self.assertEqual(self.client.get(path).status_code,401)

    def test_missing_original_uses_existing_text_without_inventing_page(self):
        self.doc.file="missing.pdf"
        self.doc.md_content="检验 SDLDWARMV0222 相关说明"
        self.doc.save(update_fields=["file","md_content"])
        row=documents.search(self.user,"SDLDWARMV0222")["documents"][0]
        self.assertFalse(row["has_pdf"])
        self.assertEqual(row["occurrences"],[])
        self.assertIn("相关说明",row["excerpt"])
        for invalid in ["//other/path", "a", "abc<script>", 123]:
            with self.assertRaises(ValueError): documents.drawing_code(invalid)
