import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from django.test import SimpleTestCase

from .isolated_model import AnalysisError, call_model


class IsolatedModelEncodingTests(SimpleTestCase):
    def test_real_worker_round_trips_unicode_under_legacy_parent_encoding(self):
        received = []
        question = '采集日期、运营时长？🕒'
        answer = '今日共 3 场，持续 8 小时。✅'

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                raw = json.dumps({'choices':[{'message':{'content':answer}}]}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            config = {'base_url':f'http://127.0.0.1:{server.server_port}/v1', 'api_key':'fixture',
                      'model':'fixture', 'temperature':0, 'max_tokens':100}
            try:
                with patch.dict('os.environ', {'PYTHONUTF8':'0', 'PYTHONIOENCODING':'gbk'}):
                    result = call_model(config, [{'role':'user', 'content':question}])
            finally:
                server.shutdown()
                thread.join(timeout=2)
        self.assertEqual(result, answer)
        self.assertEqual(received[0]['messages'][0]['content'], question)

    def test_unicode_worker_errors_are_reported_as_analysis_errors(self):
        config = {'base_url':'http://127.0.0.1:1/v1', 'api_key':'fixture',
                  'model':'fixture', 'temperature':0, 'max_tokens':100}
        with self.assertRaises(AnalysisError) as caught:
            call_model(config, [{'role':'user','content':'中文问题'}])
        self.assertEqual(caught.exception.status, 503)
        self.assertIn('未能从当前回答模型', str(caught.exception))
