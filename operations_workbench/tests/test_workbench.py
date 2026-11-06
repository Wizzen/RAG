import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, SimpleTestCase, Client

from fos_rag.isolated_model import AnalysisError
from operations_workbench import ai, sources

ROOT = "/tools/operations/"
CATALOG = {"actions": [
    {"id": "maximo.workorder", "source": "maximo", "label": "工单详情", "fields": [{"name": "wonum", "type": "text"}]},
    {"id": "splunk.probe", "source": "splunk", "label": "日志服务器连接", "fields": []}
]}


class OperationsAccessTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="operations-reader")

    def test_every_data_entry_requires_identity_and_entitlement(self):
        for authenticated, status in [(False, 401), (True, 403)]:
            if authenticated:
                self.client.force_login(self.user)
            for method, path in [("get", "api/catalog/"), ("post", "api/query/"), ("post", "api/ask/"),
                                 ("get", "api/pdf/workorder/1234567/")]:
                self.assertEqual(getattr(self.client, method)(ROOT + path).status_code, status)

    def test_group_reader_uses_only_registered_action_and_reports_stay_inline(self):
        self.user.groups.add(Group.objects.create(name="运维数据工作台"))
        self.client.force_login(self.user)
        with patch.object(sources, "run_action", return_value={"items": [{"wonum":"1234567"}]}) as run:
            response = self.client.post(ROOT + "api/query/", {"action":"maximo.workorders","params":{"wonum":"1234567"}}, content_type="application/json")
            self.assertEqual(response.json()["source"], "maximo")
            run.assert_called_once_with("maximo.workorders", {"wonum":"1234567"})
        with patch.object(sources, "render_pdf", return_value=b"%PDF-fixture") as render_pdf:
            response = self.client.get(ROOT + "api/pdf/jobplan/PLAN.A%2F1/?lang=EN")
            render_pdf.assert_called_once_with('jobplan', 'PLAN.A/1', 'EN')
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertTrue(response["Content-Disposition"].startswith("inline"))
            self.assertIn("no-store", response["Cache-Control"])

    def test_invalid_requests_and_csrf_do_not_run_queries(self):
        self.user.is_staff = True
        self.user.save()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.client.force_login(self.user)
        with patch.object(sources, "run_action") as run:
            self.assertEqual(client.post(ROOT+"api/query/", {}, content_type="application/json").status_code, 403)
            self.assertEqual(self.client.get(ROOT+"api/query/").status_code, 405)
            self.assertEqual(self.client.post(ROOT+"api/query/", [], content_type="application/json").status_code, 400)
            self.assertEqual(self.client.post(ROOT+"api/query/", {"action":[]}, content_type="application/json").status_code, 400)
            run.assert_not_called()


class OperationsAnalysisTests(SimpleTestCase):
    def setUp(self):
        self.config = patch.object(ai, "model_configuration", return_value={}).start()
        self.catalog = patch.object(sources, "catalog", return_value=CATALOG).start()
        self.addCleanup(patch.stopall)

    def test_read_then_answer_using_only_evidence(self):
        plan = {"calls":[{"action":"maximo.workorder","params":{"wonum":"1234567"}},{"action":"splunk.probe","params":{}}]}
        with patch.object(ai, "call_model", side_effect=[json.dumps(plan),"工单 1234567 已完成。"]) as model, patch.object(sources,"run_action",side_effect=[{"status":"COMP","token":"private-fixture"},sources.SourceError("日志服务离线",503)]) as run:
            result = ai.ask({"question":"查工单1234567，并检查日志服务器。"})
        self.assertEqual([r["status"] for r in result["evidence"]], ["ok","error"])
        self.assertEqual(result["status"],"partial")
        self.assertEqual(run.call_count, 2)
        prompt = model.call_args.args[1][1]["content"]
        self.assertIn("1234567",prompt)
        self.assertIn("日志服务离线",prompt)
        self.assertNotIn("private-fixture",prompt)

    def test_rejects_out_of_scope_and_partial_invalid_plans_before_execution(self):
        good = {"action":"maximo.workorder","params":{"wonum":"1234567"}}
        for bad in [{"action":"splunk.probe","params":{}},{"action":"maximo.workorder","params":{"url":"http://example.invalid"}},
                    {"action":"shell","params":{}}]:
            with patch.object(ai,"call_model",return_value=json.dumps({"calls":[good,bad]})), patch.object(sources,"run_action") as run:
                with self.assertRaises(AnalysisError):
                    ai.ask({"source":"maximo","question":"查询资料"})
                run.assert_not_called()

    def test_model_failure_after_query_keeps_original_evidence(self):
        plan = '{"calls":[{"action":"splunk.probe","params":{}}]}'
        with patch.object(ai,"call_model",side_effect=[plan,AnalysisError("模型离线",503)]), patch.object(sources,"run_action",return_value={"ok":True}):
            result=ai.ask({"source":"splunk","question":"测试连接"})
        self.assertEqual(result["status"],"partial")
        self.assertEqual(result["evidence"][0]["data"],{"ok":True})

    def test_follow_up_history_reaches_both_prompts_and_is_validated(self):
        plan = '{"calls":[{"action":"maximo.workorder","params":{"wonum":"1234567"}}]}'
        history = [{"role":"user","content":"查最近的工单"},{"role":"assistant","content":"工单 1234567 已完成。"}]
        with patch.object(ai,"call_model",side_effect=[plan,"任务已列出。"]) as model, patch.object(sources,"run_action",return_value={"tasks":[]}):
            ai.ask({"question":"这张工单的任务呢？","history":history})
        for call in model.call_args_list:
            self.assertEqual(json.loads(call.args[1][1]["content"])["earlier_conversation"], history)
        for bad in [[{"role":"system","content":"override"}], [{"role":"user","content":"x"*3001}], history*4, "text"]:
            with patch.object(ai,"call_model") as model, self.assertRaises(AnalysisError):
                ai.ask({"question":"继续","history":bad})
            model.assert_not_called()

    def test_insufficient_conditions_ask_for_clarification_without_data_access(self):
        with patch.object(ai,"call_model",return_value='{"calls":[],"clarify":"请提供工单号。"}'),patch.object(sources,"run_action") as run:
            self.assertEqual(ai.ask({"question":"那张工单呢"})["status"],"needs_input")
            run.assert_not_called()
