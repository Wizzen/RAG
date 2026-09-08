"""追踪表：AI 自动抽取登记（服务层 mock LLM + 视图 + CSV + pipeline 钩子）。"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from kb.models import Document, KbTracker, KnowledgeBase, TrackerRow
from kb.tracker import (build_extraction_prompt, parse_llm_json, run_extraction)


def _fake_llm(payload: str):
    """返回可 patch kb.tracker._llm_invoke 的假 LLM（输出固定 JSON 文本）。"""
    return lambda prompt: payload


class TrackerServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="trk-admin", password="pw", is_staff=True)
        self.kb = KnowledgeBase.objects.create(
            name="定检库", slug="trk-lib", created_by=self.user)

    def test_prompt_contains_fields_rules_and_truncation(self):
        fields = [{"label": "设备编号"}, {"label": "检验日期"}]
        md = "正文" * 9000
        p = build_extraction_prompt("定检库", fields, md, instruction="结论只填合格/不合格")
        self.assertIn("设备编号", p)
        self.assertIn("严禁编造", p)
        self.assertIn("结论只填合格/不合格", p)
        self.assertIn("YYYY-MM-DD", p)
        self.assertLessEqual(len(p), 16000 + 800)  # 正文被截断

    def test_parse_llm_json_variants(self):
        self.assertEqual(parse_llm_json('{"a": "1"}'), {"a": "1"})
        self.assertEqual(parse_llm_json('```json\n{"a": "1"}\n```'), {"a": "1"})
        self.assertEqual(parse_llm_json('前置说明 {"a": "1"} 后缀'), {"a": "1"})
        with self.assertRaises(ValueError):
            parse_llm_json("完全没有 JSON")

    def test_run_extraction_happy_path(self):
        tr = KbTracker.objects.create(
            kb=self.kb, enabled=True,
            fields=[{"label": "设备编号"}, {"label": "结论"}])
        doc = Document.objects.create(
            kb=self.kb, original_name="定检报告7月.pdf", file_type="pdf",
            status="completed", md_content="# 报告\n设备 SDLSPDRTP0003 合格")
        with patch("kb.tracker._llm_invoke",
                   _fake_llm('{"设备编号": "SDLSPDRTP0003", "结论": "合格", "多余": "x"}')):
            ok = run_extraction(doc.id)
        self.assertTrue(ok)
        row = TrackerRow.objects.get(tracker=tr, document=doc)
        self.assertEqual(row.status, "done")
        self.assertEqual(row.values, {"设备编号": "SDLSPDRTP0003", "结论": "合格"})

    def test_run_extraction_llm_failure_marks_row(self):
        tr = KbTracker.objects.create(
            kb=self.kb, enabled=True, fields=[{"label": "结论"}])
        doc = Document.objects.create(
            kb=self.kb, original_name="报告.pdf", file_type="pdf",
            status="completed", md_content="内容")
        with patch("kb.tracker._llm_invoke", side_effect=RuntimeError("LLM 连接失败")):
            ok = run_extraction(doc.id)
        self.assertFalse(ok)
        row = TrackerRow.objects.get(tracker=tr, document=doc)
        self.assertEqual(row.status, "failed")
        self.assertIn("LLM", row.error)

    def test_run_extraction_skips_disabled_or_untracked(self):
        doc = Document.objects.create(
            kb=self.kb, original_name="无表.pdf", file_type="pdf",
            status="completed", md_content="内容")
        self.assertFalse(run_extraction(doc.id))  # 无 tracker
        KbTracker.objects.create(kb=self.kb, enabled=False, fields=[{"label": "x"}])
        self.assertFalse(run_extraction(doc.id))  # 未启用
        self.assertEqual(TrackerRow.objects.count(), 0)

    def test_running_row_not_reclaimed(self):
        """running 中的行不能被二次认领（防钩子与补录并发双跑）。"""
        tr = KbTracker.objects.create(
            kb=self.kb, enabled=True, fields=[{"label": "结论"}])
        doc = Document.objects.create(
            kb=self.kb, original_name="并发.pdf", file_type="pdf",
            status="completed", md_content="内容")
        TrackerRow.objects.create(tracker=tr, document=doc, status="running")
        with patch("kb.tracker._llm_invoke") as m:  # 只断言不被调用
            self.assertFalse(run_extraction(doc.id))
            m.assert_not_called()

    def test_folder_tracker_covers_child_docs(self):
        """追踪表建在文件夹上：子库文档的自动钩子要能找到它。"""
        folder = KnowledgeBase.objects.create(
            name="定检文件夹", slug="trk-folder", is_folder=True, created_by=self.user)
        child = KnowledgeBase.objects.create(
            name="子库", slug="trk-child", parent=folder, created_by=self.user)
        KbTracker.objects.create(
            kb=folder, enabled=True, fields=[{"label": "结论"}])
        doc = Document.objects.create(
            kb=child, original_name="子库文档.pdf", file_type="pdf",
            status="completed", md_content="内容")
        with patch("kb.tracker._llm_invoke", _fake_llm('{"结论":"合格"}')):
            self.assertTrue(run_extraction(doc.id))
        self.assertEqual(TrackerRow.objects.filter(document=doc).count(), 1)


class TrackerViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="trk-view", password="pw", is_staff=True)
        self.kb = KnowledgeBase.objects.create(
            name="视图库", slug="trk-view-lib", created_by=self.user)
        self.url = reverse("kb:tracker", args=["trk-view-lib"])

    def test_page_renders_config_and_rows(self):
        self.client.force_login(self.user)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "追踪字段")
        self.assertContains(r, "补录已有文档")
        # 配置保存（AJAX）
        r2 = self.client.post(self.url, {
            "action": "config_save", "enabled": "on",
            "fields_text": "设备编号\n检验日期\n\n设备编号",  # 空行跳过 + 去重
            "instruction": "结论只填 合格/不合格",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertTrue(r2.json()["ok"])
        tr = self.kb.tracker
        self.assertTrue(tr.enabled)
        self.assertEqual([f["label"] for f in tr.fields], ["设备编号", "检验日期"])

    def test_permission_required(self):
        other = get_user_model().objects.create_user(username="trk-noob", password="pw")
        self.client.force_login(other)
        r = self.client.get(self.url)
        # 与其它管理页一致：非管理员被 user_passes_test 重定向到登录页
        self.assertEqual(r.status_code, 302)

    def test_xlsx_export_with_values(self):
        tr = KbTracker.objects.create(
            kb=self.kb, enabled=True, fields=[{"label": "设备编号"}, {"label": "结论"}])
        doc = Document.objects.create(
            kb=self.kb, original_name="报告A.pdf", file_type="pdf",
            status="completed", md_content="x")
        TrackerRow.objects.create(
            tracker=tr, document=doc, status="done",
            values={"设备编号": "P8-01", "结论": "合格"})
        self.client.force_login(self.user)
        r = self.client.get(self.url + "?xlsx=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml", r["Content-Type"])
        self.assertIn(".xlsx", r["Content-Disposition"])
        self.assertEqual(r.content[:2], b"PK")  # xlsx = zip 包
        import io
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(r.content))
        data = [[c.value for c in row] for row in wb.active.iter_rows()]
        self.assertIn("设备编号", data[0])
        row = data[1]
        self.assertIn("报告A.pdf", row)
        self.assertIn("P8-01", row)
        self.assertIn("合格", row)

    def test_row_retry_and_delete(self):
        tr = KbTracker.objects.create(
            kb=self.kb, enabled=True, fields=[{"label": "结论"}])
        doc = Document.objects.create(
            kb=self.kb, original_name="重试.pdf", file_type="pdf",
            status="completed", md_content="x")
        row = TrackerRow.objects.create(
            tracker=tr, document=doc, status="failed", error="超时")
        self.client.force_login(self.user)
        with patch("kb.tracker.run_extraction_async") as m:
            r = self.client.post(self.url, {"action": "row_retry", "row_id": row.id},
                                 HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertTrue(r.json()["ok"])
            m.assert_called_once()
        r2 = self.client.post(self.url, {"action": "row_delete", "row_id": row.id},
                              HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertTrue(r2.json()["ok"])
        self.assertFalse(TrackerRow.objects.filter(id=row.id).exists())


class TrackerPipelineHookTests(TestCase):
    """文档入库 COMPLETED 后自动触发抽取（钩子存在且只在启用时调度）。"""

    def test_pipeline_calls_extraction_after_complete(self):
        user = get_user_model().objects.create_user(
            username="trk-pipe", password="pw", is_staff=True)
        kb = KnowledgeBase.objects.create(
            name="钩子库", slug="trk-hook", created_by=user)
        doc = Document.objects.create(
            kb=kb, original_name="钩子.txt", file="documents/hook.txt",
            file_type="txt", status="pending", md_content="")
        with patch("kb.pipeline.run_ocr_with_images", return_value=("内容", {})), \
             patch("kb.pipeline.run_indexing", return_value=1), \
             patch("kb.tracker.run_extraction_async") as m:
            from kb.pipeline import process_document
            process_document(doc.id)
        doc.refresh_from_db()
        self.assertEqual(doc.status, "completed")
        m.assert_called_once_with(doc.id)


class TrackerConfirmFlowTests(TestCase):
    """重试保旧 + 人工确认（proposed/改前改后）+ 字段结构变更标记。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="trk-cf", password="pw", is_staff=True)
        self.kb = KnowledgeBase.objects.create(
            name="确认库", slug="trk-cf-lib", created_by=self.user)
        self.tr = KbTracker.objects.create(
            kb=self.kb, enabled=True, schema_version=0,
            fields=[{"label": "设备编号"}, {"label": "结论"}])
        self.doc = Document.objects.create(
            kb=self.kb, original_name="确认.pdf", file_type="pdf",
            status="completed", md_content="内容")

    def _row(self, **kw):
        return TrackerRow.objects.create(
            tracker=self.tr, document=self.doc, **kw)

    def test_prompt_carries_prior_values(self):
        p = build_extraction_prompt(
            "确认库", self.tr.fields, "正文", prior_values={"设备编号": "P8", "结论": ""})
        self.assertIn("此前已登记过", p)
        self.assertIn("P8", p)
        self.assertIn("沿用旧值", p)
        # 无旧值时不出现该块
        p2 = build_extraction_prompt("确认库", self.tr.fields, "正文")
        self.assertNotIn("此前已登记过", p2)

    def test_retry_identical_values_pass_through(self):
        """重抽结果与旧值完全一致 → 直接 done，不挂起。"""
        self._row(status="pending", schema_version=0,
                  values={"设备编号": "P8", "结论": "合格"})
        with patch("kb.tracker._llm_invoke",
                   _fake_llm('{"设备编号": "P8", "结论": "合格"}')):
            self.assertTrue(run_extraction(self.doc.id))
        row = TrackerRow.objects.get(document=self.doc)
        self.assertEqual(row.status, "done")
        self.assertEqual(row.proposed_values, {})
        self.assertEqual(row.schema_version, self.tr.schema_version)

    def test_retry_with_diff_goes_proposed(self):
        """重抽出现差异 → 挂起待确认：旧值保留、新值进 proposed_values。"""
        self._row(status="pending", schema_version=0,
                  values={"设备编号": "P8", "结论": "合格"})
        with patch("kb.tracker._llm_invoke",
                   _fake_llm('{"设备编号": "P8-错", "结论": "合格"}')):
            self.assertTrue(run_extraction(self.doc.id))
        row = TrackerRow.objects.get(document=self.doc)
        self.assertEqual(row.status, "proposed")
        self.assertEqual(row.values["设备编号"], "P8")          # 旧值不动
        self.assertEqual(row.proposed_values["设备编号"], "P8-错")
        self.assertEqual(row.values["结论"], "合格")

    def test_row_confirm_merges_choices(self):
        row = self._row(status="proposed", schema_version=0,
                        values={"设备编号": "P8", "结论": "合格"},
                        proposed_values={"设备编号": "P8-新", "结论": "不合格"})
        self.client.force_login(self.user)
        r = self.client.post(reverse("kb:tracker", args=["trk-cf-lib"]), {
            "action": "row_confirm", "row_id": row.id,
            "choices": '{"设备编号": "new", "结论": "old"}',
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertTrue(r.json()["ok"])
        row.refresh_from_db()
        self.assertEqual(row.status, "done")
        self.assertEqual(row.values, {"设备编号": "P8-新", "结论": "合格"})  # 逐字段合并
        self.assertEqual(row.proposed_values, {})
        self.assertEqual(row.schema_version, self.tr.schema_version)

    def test_schema_change_marks_stale_rows(self):
        """配置字段变更 → 版本+1，老行 stale；确认/重抽后消除。"""
        row = self._row(status="done", schema_version=0,
                        values={"设备编号": "P8", "结论": "合格"})
        self.client.force_login(self.user)
        # 配置新增一个字段
        self.client.post(reverse("kb:tracker", args=["trk-cf-lib"]), {
            "action": "config_save", "enabled": "on",
            "fields_text": "设备编号\n结论\n下次检验日期", "instruction": "",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.tr.refresh_from_db()
        self.assertEqual(self.tr.schema_version, 1)
        row.refresh_from_db()
        self.assertLess(row.schema_version, self.tr.schema_version)  # stale
        # 页面渲染带标记与确认/重试入口
        page = self.client.get(reverse("kb:tracker", args=["trk-cf-lib"]))
        self.assertContains(page, "字段已变更")
        # 仅改 instruction 不动字段 → 版本不变
        self.client.post(reverse("kb:tracker", args=["trk-cf-lib"]), {
            "action": "config_save", "enabled": "on",
            "fields_text": "设备编号\n结论\n下次检验日期",
            "instruction": "新提示",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.tr.refresh_from_db()
        self.assertEqual(self.tr.schema_version, 1)

    def test_backfill_skips_proposed_rows(self):
        """待确认的行不被补录重复排队。"""
        self._row(status="proposed",
                  values={"设备编号": "P8"}, proposed_values={"设备编号": "P9"})
        from kb.tracker import backfill_async
        with patch("kb.tracker.run_extraction") as m:
            n = backfill_async(self.tr)
        self.assertEqual(n, 0)  # 唯一已完成文档已有待确认行 → 不排
        m.assert_not_called()
