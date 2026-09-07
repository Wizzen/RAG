from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from kb.models import Document, KnowledgeBase, StructuredDataset, StructuredRecord
from kb.structured_data import normalize_key


class ApplicationSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="smoke-admin", password=None, is_staff=True, is_superuser=True,
        )
        cls.folder = KnowledgeBase.objects.create(
            name="测试手册", slug="smoke-manuals", is_folder=True, created_by=cls.user,
        )
        cls.library = KnowledgeBase.objects.create(
            name="制动器手册", slug="brake-manual", parent=cls.folder,
            is_folder=False, created_by=cls.user, doc_count=1, chunk_count=1,
        )
        cls.document = Document.objects.create(
            kb=cls.library,
            original_name="brake.md",
            file="documents/brake.md",
            file_type="md",
            md_content="# 制动器\n图纸号 DWG-700 的每周检查要求：检查磨损。",
            html_content="<h1>制动器</h1><p>图纸号 DWG-700 的每周检查要求：检查磨损。</p>",
            status=Document.Status.COMPLETED,
            chunk_count=1,
        )
        dataset = StructuredDataset.objects.create(
            name="drawing", kind="drawing", source_name="drawing.csv",
            checksum="smoke", row_count=1, imported_by=cls.user,
        )
        StructuredRecord.objects.create(
            dataset=dataset, row_number=2,
            drawing_no="DWG-700", drawing_no_norm=normalize_key("DWG-700"),
            part_no="P-700", part_no_norm=normalize_key("P-700"),
            part_name="制动器", part_name_norm=normalize_key("制动器"),
            g_code="G-700", g_code_norm=normalize_key("G-700"),
            scp_level="SCP-2",
            raw_data={"图纸号": "DWG-700", "部件名称": "制动器"},
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_primary_pages_render(self):
        urls = [
            reverse("kb:manage_list"),
            reverse("kb:search"),
            reverse("kb:inspection"),
            reverse("kb:ask"),
            reverse("kb:settings"),
            reverse("dashboard:index"),
            "/admin/",
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_unified_search_returns_structured_and_manual_results(self):
        response = self.client.get(reverse("kb:search"), {"q": "DWG-700"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "综合关联搜索")
        self.assertContains(response, "G-700")
        self.assertContains(response, "SCP-2")
        self.assertContains(response, "brake.md")
        self.assertContains(response, "手册原文 1")

    def test_document_inspection_and_status_endpoints(self):
        document_page = self.client.get(
            reverse("kb:document_html", kwargs={"doc_id": self.document.id}),
        )
        self.assertEqual(document_page.status_code, 200)
        self.assertContains(document_page, "DWG-700")

        inspection = self.client.get(
            reverse("kb:inspection"), {"doc": str(self.document.id)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(inspection.status_code, 200)
        self.assertEqual(inspection.json()["doc_name"], "brake.md")

        status = self.client.get(
            reverse("kb:doc_status", kwargs={"slug": self.folder.slug}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(status.status_code, 200)
