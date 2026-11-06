from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase

from scripts.graph_benchmark.compare import flatten_cognee, recover_unique_primary_ids, score
from scripts.graph_benchmark.run import BudgetProxy, ROOT, main


class GraphBenchmarkTests(SimpleTestCase):
    def graph(self):
        return {'objects': [{'name': '锻件', 'parameters': [
            {'key': '锻件/质量分级', 'name': '质量分级', 'role': 'property'},
            {'key': '锻件/质量分级/I', 'name': 'I级', 'role': 'entry', 'parent': {'key': '锻件/质量分级'}},
            {'key': '锻件/质量分级/I/底波', 'name': '底波降低量', 'parent': {'key': '锻件/质量分级/I'},
             'values': [{'text': '6', 'value_type': 'number', 'operator': '<=', 'unit': 'dB',
                         'conditions': '声程大于近场区长度',
                         'evidence': [{'chunk_id': 'test-source', 'quote': '锻件I级底波降低量不大于6 dB。'}]}]},
        ]}]}

    def test_parent_references_rebuild_the_entire_path_and_keep_conditions(self):
        rows, rejected = flatten_cognee(self.graph())
        self.assertEqual(rejected, 0)
        self.assertEqual([part['name'] for part in rows[0]['path']], ['质量分级', 'I级', '底波降低量'])
        self.assertEqual(rows[0]['value']['content'], 6)
        self.assertIn('近场', rows[0]['conditions']['原文条件'])
        self.assertEqual(score(rows)['matched_table_cells'], 1)

    def test_cycles_and_unresolved_references_are_not_silently_treated_as_root_nodes(self):
        for parent in ('missing', '锻件/质量分级/I/底波'):
            graph = self.graph(); graph['objects'][0]['parameters'][-1]['parent'] = {'key': parent}
            self.assertEqual(flatten_cognee(graph), ([], 1))

    def test_duplicate_keys_are_rejected_instead_of_overwriting_a_parameter(self):
        graph = self.graph()
        graph['objects'][0]['parameters'].append({'key': '锻件/质量分级/I', 'name': 'II级'})
        self.assertEqual(flatten_cognee(graph), ([], 1))

    def test_table_score_does_not_credit_the_right_value_under_the_wrong_grade_or_object(self):
        graph = self.graph(); graph['objects'][0]['parameters'][1]['name'] = 'II级'
        rows, _ = flatten_cognee(graph)
        self.assertEqual(score(rows)['matched_table_cells'], 0)
        graph = self.graph(); graph['objects'][0]['name'] = '棒材'
        rows, _ = flatten_cognee(graph)
        self.assertEqual(score(rows)['other_object_assertions'], 1)
        self.assertEqual(score(rows)['matched_table_cells'], 0)

    def test_formula_is_not_credited_as_a_simple_numeric_diameter(self):
        graph = self.graph(); node = graph['objects'][0]['parameters'][-1]
        node['name'] = '单个缺陷当量平底孔直径'
        graph['objects'][0]['parameters'][1]['name'] = 'II级'
        node['values'][0].update(text='6', unit='mm')
        rows, _ = flatten_cognee(graph)
        self.assertEqual(score(rows)['matched_table_cells'], 0)

    def test_malformed_raw_output_is_reported_without_breaking_the_comparison(self):
        result = score([None, {'object': {'name': '锻件'}, 'path': [None]}])
        self.assertEqual(result['malformed_assertions'], 2)
        self.assertEqual(result['matched_table_cells'], 0)

    def test_conditions_are_not_credited_just_because_the_quote_contains_them(self):
        graph = self.graph(); value = graph['objects'][0]['parameters'][-1]['values'][0]
        value['conditions'] = ''
        value['evidence'][0]['quote'] += '近场适用条件，不同种类独立分级，危害性缺陷。'
        rows, _ = flatten_cognee(graph); result = score(rows)
        self.assertFalse(result['near_field_condition_present'])
        self.assertFalse(result['independent_grading_present'])
        self.assertFalse(result['hazardous_defect_present'])

    def test_embedded_values_count_as_content_but_not_explicit_parameter_nodes(self):
        rows, _ = flatten_cognee(self.graph())
        rows[0]['path'] = rows[0]['path'][:2]
        rows[0]['value'] = {'type': 'object', 'content': {'底波降低量': '<=6 dB'}}
        result = score(rows)
        self.assertEqual(result['matched_table_cells'], 1)
        self.assertEqual(result['explicit_parameter_cells'], 0)
        self.assertEqual(result['embedded_parameter_cells'], 1)

    def test_parameter_then_grade_does_not_count_as_the_requested_grade_then_subparameter(self):
        rows,_=flatten_cognee(self.graph())
        self.assertEqual(score(rows)['requested_grade_then_parameter_cells'],1)
        rows[0]['path']=[rows[0]['path'][0],rows[0]['path'][2],rows[0]['path'][1]]
        result=score(rows)
        self.assertEqual(result['matched_table_cells'],1)
        self.assertEqual(result['explicit_parameter_cells'],1)
        self.assertEqual(result['requested_grade_then_parameter_cells'],0)

    def test_cli_finalizes_its_run_metadata_without_a_model_call(self):
        with TemporaryDirectory(dir=ROOT/'output') as temp:
            folder = Path(temp); fixture = folder/'fixture.json'
            fixture.write_text(json.dumps({'focus': 'synthetic', 'chunks': [{'text': 'synthetic'}]}), encoding='utf8')
            argv = ['run.py', '--backend', 'smoke', '--fixture', str(fixture), '--output', str(folder)]
            with patch('sys.argv', argv), patch('scripts.graph_benchmark.run.cognee') as worker, \
                 patch('kb.config.llm_settings', return_value={'model': 'synthetic', 'remote_enabled': False}):
                self.assertEqual(main(), 0)
                worker.assert_called_once()
            record = json.loads((folder/'run-smoke.json').read_text(encoding='utf8'))
            self.assertEqual(record['outcomes']['smoke']['status'], 'completed')
            self.assertFalse(record['production_writes'])
            self.assertEqual(record['requests'], [])

    def test_primary_id_diagnostic_requires_a_unique_read_source_and_preserves_supplied_ids(self):
        rows=[{'quote':'unique quote'}, {'quote':'shared quote'}, {'quote':'unread quote'},
              {'quote':'unique quote','source_chunk_id':'provided-id'}]
        repaired, count=recover_unique_primary_ids(rows, {'a':{'text':'unique quote shared quote'},'b':{'text':'shared quote'}})
        self.assertEqual(count,1)
        self.assertEqual(repaired[0]['source_chunk_id'],'a')
        self.assertNotIn('source_chunk_id',repaired[1])
        self.assertNotIn('source_chunk_id',repaired[2])
        self.assertEqual(repaired[3]['source_chunk_id'],'provided-id')
        self.assertNotIn('source_chunk_id',rows[0])

    def test_report_does_not_execute_model_supplied_html(self):
        from scripts.graph_benchmark.view import tree
        rows,_=flatten_cognee(self.graph())
        rows[0]['value']['content']='<img src=x onerror=alert(1)>'
        result=tree(rows)
        self.assertNotIn('<img',result)
        self.assertIn('&lt;img',result)

    def test_table_geometry_keeps_column_grades_and_skips_undefined_cells(self):
        from scripts.graph_benchmark.table_geometry import extract_table
        text='10.1 其他缺陷独立评定。\n表9 锻件质量分级\n| 等级 | I | II |\n|---|---|---|\n| 底波降低量 | <=6 dB | <=12 dB |\n| 密集区缺陷 | — | <=4 mm |'
        fixture={'document':'synthetic.pdf','chunks':[{'chunk_id':'table-only','text':text}]}
        rows=extract_table(fixture,'table-only','锻件','质量分级')
        leaves=[row for row in rows if row['path'][-1]['kind']=='property']
        self.assertEqual(len(leaves),3)
        self.assertEqual(leaves[0]['path'][1]['name'],'I')
        self.assertEqual(leaves[1]['path'][1]['name'],'II')
        self.assertEqual(leaves[1]['value']['content'],'<=12 dB')
        self.assertTrue(all(row['quote'] in text for row in rows))
        self.assertFalse(any(row['value']['content']=='0' for row in rows))

    def test_table_geometry_refuses_ambiguous_or_incomplete_tables(self):
        from scripts.graph_benchmark.table_geometry import extract_table
        table='表9 锻件质量分级\n| 等级 | I | II |\n|---|---|---|\n| 底波降低量 | <=6 dB | <=12 dB |'
        for text in (table+'\n\n'+table,table.replace(' | <=12 dB','')):
            fixture={'document':'synthetic.pdf','chunks':[{'chunk_id':'table-only','text':text}]}
            with self.assertRaises(ValueError):extract_table(fixture,'table-only','锻件','质量分级')

    def test_proxy_caps_real_requests_and_never_saves_credentials(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            proxy = BudgetProxy({'model': 'synthetic', 'base_url': 'http://127.0.0.1:1/v1',
                                 'api_key': 'do-not-log-this-secret'}, folder)
            thread = threading.Thread(target=proxy.serve_forever, daemon=True); thread.start()
            client = HTTPConnection(*proxy.server_address, timeout=5)
            response = Mock(status_code=200)
            response.json.return_value = {'choices': [{'finish_reason': 'stop'}],
                                          'usage': {'prompt_tokens': 10, 'completion_tokens': 20}}
            upstream = Mock(); upstream.__aenter__ = AsyncMock(return_value=upstream)
            upstream.__aexit__ = AsyncMock(return_value=False); upstream.post = AsyncMock(return_value=response)
            try:
                with patch('httpx.AsyncClient', return_value=upstream):
                    statuses = []
                    for _ in range(3):
                        client.request('POST', '/cognee/v1/chat/completions',
                            body=json.dumps({'messages': [{'role': 'user', 'content': 'synthetic test'}], 'max_tokens': 100000}),
                            headers={'Content-Type': 'application/json'})
                        result = client.getresponse(); statuses.append(result.status); result.read()
                    self.assertEqual(statuses, [200, 200, 402])
                    self.assertEqual(upstream.post.call_count, 2)
                    self.assertEqual(upstream.post.call_args.kwargs['json']['max_tokens'], 32768)
            finally:
                client.close(); proxy.shutdown(); proxy.server_close(); thread.join(timeout=2)
            for file in folder.glob('*.json'):
                self.assertNotIn('do-not-log-this-secret', file.read_text(encoding='utf8'))
            restarted = BudgetProxy(proxy.config, folder)
            try:
                self.assertEqual(restarted.counts['cognee'], 2)
            finally:
                restarted.server_close()
