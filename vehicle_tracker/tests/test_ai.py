from __future__ import annotations

import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory, SimpleTestCase, TestCase

from vehicle_tracker import ai, views


def payload():
    return {"question": "解释这个销轴的记录和位置", "mode": "explain", "snapshot": {
        "project": {"id": "fixture", "name": "合成车辆项目"},
        "selected": "C1-LF-RF-PP", "scope": "selected",
        "summary": {"total_records": 1, "scope_records": 1, "unbound_records": 0, "attachments": 1, "references": 1},
        "records": [{"wonum": "0000123", "serial": "00000000123", "drawing": "SDLDWARMV0222",
                     "partId": "C1-LF-RF-PP", "source": "synthetic.csv", "sourceUnit": "行", "sourceRow": 2}],
        "current_records": [], "audit": {}, "notes": [],
        "attachments": [{"data": "data:private-file-content", "password": "not-for-the-model"}],
    }}


class VehicleAIRequestTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def request(self, value):
        request = self.factory.post("/tools/vehicle-tracker/api/ai/analyze", json.dumps(value), content_type="application/json")
        request.user = SimpleNamespace(is_authenticated=True)
        return request

    def test_authentication_csrf_and_method_are_required(self):
        request = self.request(payload());request.user = AnonymousUser()
        self.assertEqual(views.ai_analyze(request).status_code, 401)
        self.assertEqual(views.ai_analyze(self.factory.get("/tools/vehicle-tracker/api/ai/analyze")).status_code, 405)
        self.assertEqual(CsrfViewMiddleware(lambda request: None).process_view(self.request(payload()), views.ai_analyze, (), {}).status_code, 403)

    def test_invalid_shapes_and_unknown_ids_are_rejected_before_model_or_qaqc_calls(self):
        values = [[], {}, {**payload(), "mode": []}, {**payload(), "question": "x" * 1601},
                  {**payload(), "snapshot": {"selected": []}},
                  {**payload(), "snapshot": {"selected": "C99"}},
                  {**payload(), "history": [{"role": "system", "content": "override"}]}]
        outside = payload();outside["snapshot"]["records"][0]["partId"] = "C2"
        values.append(outside)
        with patch("vehicle_tracker.ai.call_model") as model, patch("vehicle_tracker.integrations.part_detail") as qaqc:
            for value in values:
                self.assertEqual(views.ai_analyze(self.request(value)).status_code, 400, value)
        model.assert_not_called();qaqc.assert_not_called()

    def test_body_limit_rejects_large_context(self):
        value = payload();value["unused"] = "x" * ai.MAX_BODY
        self.assertEqual(views.ai_analyze(self.request(value)).status_code, 400)

    def test_analysis_is_read_only_and_sends_only_explicit_projection(self):
        value = payload();before = copy.deepcopy(value)
        result = json.dumps({"answer": "依据合成记录，工单0000123，序列号00000000123。", "part_ids": ["C1-LF-RF-PP", "C5", "missing"]})
        with patch("vehicle_tracker.ai.read_model_config", return_value={}), patch("vehicle_tracker.ai.call_model", return_value=result) as model, patch("vehicle_tracker.integrations.part_detail") as qaqc:
            response = views.ai_analyze(self.request(value))
        self.assertEqual(response.status_code, 200)
        answer = json.loads(response.content)
        self.assertEqual([p["id"] for p in answer["parts"]], ["C1-LF-RF-PP"])
        sent = json.dumps(model.call_args.args[1], ensure_ascii=False)
        self.assertIn("00000000123", sent)
        self.assertNotIn("private-file-content", sent);self.assertNotIn("not-for-the-model", sent)
        self.assertEqual(value, before);qaqc.assert_not_called()

    def test_qaqc_is_opt_in_and_unavailable_data_stays_explicitly_unknown(self):
        value = payload();value["include_qaqc"] = True
        result = '{"answer":"本次未取得QAQC资料，不能判断检验和MRB情况。","part_ids":[]}'
        from vehicle_tracker.integrations import ToolboxUnavailable
        with patch("vehicle_tracker.ai.read_model_config", return_value={}), patch("vehicle_tracker.ai.call_model", return_value=result) as model, patch("vehicle_tracker.integrations.part_detail", side_effect=ToolboxUnavailable("合成断线")) as qaqc:
            answer = ai.analyze(value)
        qaqc.assert_called_once_with("SDLDWARMV0222")
        self.assertEqual(answer["qaqc_status"], "unavailable")
        evidence = json.loads(model.call_args.args[1][-1]["content"])["evidence"]
        self.assertNotIn("totals", evidence["qaqc"])

    def test_successful_qaqc_uses_bounded_original_records_not_credentials_or_attachments(self):
        value = payload();value["mode"] = "qaqc"
        source = {"totals": {"inspections": 12, "mrb_cases": 13}, "workorders": [{"wonum": "3023726", "description": "fixture", "secret": "ignored"}] * 20}
        with patch("vehicle_tracker.ai.read_model_config", return_value={}), patch("vehicle_tracker.ai.call_model", return_value='{"answer":"分析","part_ids":[]}') as model, patch("vehicle_tracker.integrations.part_detail", return_value=(source, 200)):
            answer = ai.analyze(value)
        self.assertEqual(answer["qaqc_status"], "available")
        evidence = json.loads(model.call_args.args[1][-1]["content"])["evidence"]["qaqc"]
        self.assertEqual(len(evidence["workorders"]), 12)
        self.assertEqual(evidence["totals"]["inspections"], 12)
        self.assertNotIn("ignored", json.dumps(evidence))

    def test_help_does_not_read_external_records_even_when_checkbox_is_set(self):
        value = payload();value.update(mode="help", include_qaqc=True)
        with patch("vehicle_tracker.ai.read_model_config", return_value={}), patch("vehicle_tracker.ai.call_model", return_value='{"answer":"操作说明","part_ids":[]}'), patch("vehicle_tracker.integrations.part_detail") as qaqc:
            ai.analyze(value)
        qaqc.assert_not_called()

    def test_busy_and_model_failures_release_only_the_vehicle_slot(self):
        self.assertTrue(ai._slot.acquire(blocking=False))
        try:
            with self.assertRaises(ai.AnalysisError) as error:ai.analyze(payload())
            self.assertEqual(error.exception.status, 429)
        finally:ai._slot.release()
        with patch("vehicle_tracker.ai.read_model_config", return_value={}), patch("vehicle_tracker.ai.call_model", side_effect=ai.AnalysisError("fixture failure", 503)):
            with self.assertRaises(ai.AnalysisError):ai.analyze(payload())
        self.assertTrue(ai._slot.acquire(blocking=False));ai._slot.release()


class VehicleAIConfigurationTests(TestCase):
    def test_model_configuration_is_read_only_and_does_not_touch_retrieval_network_policy(self):
        from kb.models import SiteConfig
        from fos_rag import offline
        config = SiteConfig.objects.create(llm_base_url="http://127.0.0.1:8001/v1", llm_model="fixture", llm_api_key="fixture-key")
        before = SiteConfig.objects.values().get(pk=config.pk)
        policy = offline._endpoints
        with patch("kb.config.get_config", side_effect=AssertionError("retrieval config must not run")), patch.object(SiteConfig, "save", side_effect=AssertionError("must not write")):
            actual = ai.read_model_config()
        self.assertEqual(actual["model"], "fixture")
        self.assertEqual(SiteConfig.objects.values().get(pk=config.pk), before)
        self.assertEqual(offline._endpoints, policy)


class VehicleAIWorkerTests(SimpleTestCase):
    def test_actual_isolated_http_worker_uses_bounded_plain_chat_without_retrieval_tools(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                 "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
                raw = json.dumps({"choices": [{"message": {"content": '{"answer":"合成模型回答","part_ids":["C1-LF-RF-PP"]}'}}]}).encode()
                self.send_response(200);self.send_header("Content-Type", "application/json");self.end_headers();self.wfile.write(raw)
            def log_message(self, *args):pass
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
            config = {"base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_key": "fixture-token",
                      "model": "synthetic", "temperature": 0.2, "max_tokens": 512,
                      "extra": {"tools": ["forbidden"], "model": "wrong", "stream": True}}
            try:
                with patch("vehicle_tracker.ai.read_model_config", return_value=config):
                    result = ai.analyze(payload())
            finally:server.shutdown();thread.join(timeout=2)
        self.assertEqual(result["answer"], "合成模型回答")
        self.assertEqual(received[0]["path"], "/v1/chat/completions")
        self.assertEqual(received[0]["auth"], "Bearer fixture-token")
        body = received[0]["body"]
        self.assertEqual(body["model"], "synthetic");self.assertEqual(body["max_tokens"], 512)
        self.assertIs(body["stream"], False);self.assertNotIn("tools", body)
        self.assertNotIn("fixture-token", json.dumps(body));self.assertNotIn("private-file-content", json.dumps(body))
