from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from kb.models import Document, KnowledgeBase, StructuredDataset
from kb.structured_data import (
    StructuredDataError, import_structured_dataset, lookup_drawing, lookup_related,
)


def csv_upload(name: str, text: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, text.encode("utf-8-sig"), content_type="text/csv")


class StructuredDataTests(TestCase):
    def test_csv_import_and_three_hop_drawing_lookup(self):
        import_structured_dataset(
            csv_upload(
                "drawing.csv",
                "图纸号,部件号,G-code,SCP等级\nDWG-1001,P-77,G-42,SCP-2\n",
            ),
            StructuredDataset.Kind.DRAWING,
        )
        import_structured_dataset(
            csv_upload(
                "parts.csv",
                "Part No,Part Name,Equipment\nP-77,主轴承,ROLLER-01\n",
            ),
            StructuredDataset.Kind.PARTS,
        )
        import_structured_dataset(
            csv_upload(
                "downtime.csv",
                "设备号,停机小时,故障原因\nROLLER-01,3.5,轴承温度高\n",
            ),
            StructuredDataset.Kind.DOWNTIME,
        )
        import_structured_dataset(
            csv_upload(
                "apex.csv",
                "Asset,APEX ID,状态\nROLLER-01,APEX-88,Open\n",
            ),
            StructuredDataset.Kind.APEX,
        )

        result = lookup_drawing("dwg 1001")
        self.assertEqual(len(result["records"]), 4)
        self.assertEqual(
            {group["kind"] for group in result["groups"]},
            {"drawing", "parts", "downtime", "apex"},
        )
        drawing = next(r for r in result["records"] if r.dataset.kind == "drawing")
        self.assertEqual(drawing.g_code, "G-42")
        self.assertEqual(drawing.scp_level, "SCP-2")

        by_part_name = lookup_related("主轴承")
        self.assertEqual(len(by_part_name["records"]), 4)
        self.assertEqual(by_part_name["direct_count"], 1)

    def test_xlsx_import_detects_english_headers(self):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "BOM"
        sheet.append(["Drawing master data export"])
        sheet.append(["Generated for maintenance lookup"])
        sheet.append(["Drawing Number", "Component No", "Component Name", "SCP Level"])
        sheet.append(["A-200", "C-9", "Brake Assembly", "3"])
        payload = BytesIO()
        workbook.save(payload)

        dataset = import_structured_dataset(
            SimpleUploadedFile(
                "drawing.xlsx", payload.getvalue(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            StructuredDataset.Kind.DRAWING,
        )
        record = dataset.records.get()
        self.assertEqual(record.drawing_no, "A-200")
        self.assertEqual(record.part_no, "C-9")
        self.assertEqual(record.part_name, "Brake Assembly")
        self.assertEqual(record.scp_level, "3")

    def test_duplicate_file_is_rejected(self):
        content = "图纸号,G-code\nD-1,G-1\n"
        import_structured_dataset(csv_upload("same.csv", content), "drawing")
        with self.assertRaises(StructuredDataError):
            import_structured_dataset(csv_upload("same.csv", content), "drawing")

    def test_staff_can_import_and_render_lookup_page(self):
        user = get_user_model().objects.create_user(
            username="operator", password="test-password", is_staff=True,
        )
        self.client.force_login(user)
        upload_page = self.client.get(reverse("kb:manage_list"))
        self.assertContains(upload_page, "上传设备手册")
        self.assertContains(upload_page, "上传业务数据")
        self.assertContains(upload_page, 'href="/kb/inspection/"', count=1)

        response = self.client.post(reverse("kb:manage_list"), {
            "action": "upload_structured",
            "kind": "drawing",
            "file": csv_upload(
                "drawing-ui.csv",
                "图纸号,部件名称,G-code,SCP等级\nUI-900,制动器,G-9,SCP-1\n",
            ),
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "已导入 drawing-ui.csv")

        response = self.client.get(reverse("kb:search"), {"q": "ui 900"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "制动器")
        self.assertContains(response, "G-9")
        self.assertContains(response, "SCP-1")
        self.assertContains(response, "不调用大模型")

        legacy = self.client.get(reverse("kb:asset_lookup"), {"q": "ui 900"})
        self.assertRedirects(
            legacy, reverse("kb:search") + "?q=ui+900",
            fetch_redirect_response=False,
        )

    @patch("kb.pipeline.process_document_async")
    def test_staff_can_upload_manual_from_same_upload_center(self, process_async):
        user = get_user_model().objects.create_user(
            username="manual-operator", password="test-password", is_staff=True,
        )
        library = KnowledgeBase.objects.create(
            name="设备手册", slug="manuals", is_folder=True, created_by=user,
        )
        self.client.force_login(user)
        response = self.client.post(reverse("kb:manage_list"), {
            "action": "upload_manual",
            "kb_slug": library.slug,
            "file": SimpleUploadedFile("brake-manual.txt", b"drawing UI-900", content_type="text/plain"),
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "已上传手册")
        document = Document.objects.get(original_name="brake-manual.txt")
        self.assertEqual(document.kb.parent, library)
        process_async.assert_called_once_with(document.id)
