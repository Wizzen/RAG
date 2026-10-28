from __future__ import annotations

"""验证主题召回、按需邻文、整次预算和候选保存，防止重新退化成全篇重做。"""

import json
from unittest.mock import Mock, patch

from django.test import TestCase
from langchain_core.messages import AIMessageChunk

from kb.graph_extraction import run_extraction
from kb.models import Document, GraphExtraction, GraphRelation, KnowledgeBase
from kb.publication import digest


class FocusedGraphTests(TestCase):
    def setUp(self):
        kb = KnowledgeBase.objects.create(name='主题检索测试', slug='focused-build-test')
        self.chunks = [{'chunk_id': f'c{i}', 'section': f'第{i}节',
                        'text': f'部件{i}要求检查项目{i}，检查周期为每日。'} for i in range(40)]
        self.doc = Document.objects.create(kb=kb, original_name='standard.pdf', status='completed',
                                           md_content='\n'.join(c['text'] for c in self.chunks))
        self.job = GraphExtraction.objects.create(document=self.doc, focus='检查频率',
                                                   source_digest=digest(self.doc.md_content))
        for name, kwargs in [('kb.keyword_index.get_chunks', {'return_value': self.chunks}),
                             ('kb.provenance.annotate_results', {})]:
            p = patch(name, **kwargs); p.start(); self.addCleanup(p.stop)
        self.retrieval = patch('kb.graph_retrieval.search_document', return_value={
            'rows': [self.chunks[10], self.chunks[30]], 'method': 'hybrid', 'vector_status': 'ok'})
        self.search = self.retrieval.start(); self.addCleanup(self.retrieval.stop)

    def row(self, i: int) -> dict:
        return {'subject': {'name': f'部件{i}', 'type': 'component'}, 'predicate': 'requires',
                'object': {'name': f'检查项目{i}', 'type': 'inspection'}, 'quote': self.chunks[i]['text'],
                'conditions': {'frequency': '每日'}, 'source_chunk_id': f'c{i}'}

    def page(self, rows: list[dict], more: bool = False, **kwargs) -> AIMessageChunk:
        return AIMessageChunk(content=json.dumps({'relations': rows, 'has_more': more}, ensure_ascii=False), **kwargs)

    def tool(self, name: str, args: dict, id: str = 't1') -> AIMessageChunk:
        return AIMessageChunk(content='', tool_call_chunks=[{'index': 0, 'id': id, 'name': name,
                                                             'args': json.dumps(args)}])

    def run_model(self, responses: list) -> Mock:
        model = Mock(); model.bind.return_value = model; model.bind_tools.return_value = model
        model.stream.side_effect = [iter([r]) for r in responses]
        with patch('kb.agent._get_llm', return_value=model), patch('kb.config.llm_settings', return_value={}):
            run_extraction(self.job.pk)
        self.job.refresh_from_db()
        return model

    def test_default_worker_only_sends_retrieved_text_and_does_not_scan_or_audit(self):
        with patch('kb.graph_build.build_document_graph') as whole_book:
            model = self.run_model([self.page([self.row(10)], usage_metadata={
                'input_tokens': 100, 'output_tokens': 50, 'total_tokens': 150})])
        self.assertEqual(self.job.status, 'completed')
        whole_book.assert_not_called()
        self.assertEqual(model.stream.call_count, 1)
        self.search.assert_called_once()
        self.assertEqual(self.search.call_args.kwargs['limit'], 6)
        messages = model.stream.call_args.args[0]
        self.assertEqual([c['chunk_id'] for c in json.loads(messages[1].content)['chunks']], ['c10', 'c30'])
        self.assertNotIn('这是独立查漏轮', messages[0].content)
        self.assertEqual(self.job.coverage['mode'], 'focused')
        self.assertEqual(self.job.coverage['chunks_read'], 2)
        self.assertEqual(self.job.coverage['chunks_total'], 40)
        self.assertEqual(self.job.coverage['input_tokens'], 100)
        self.assertEqual(self.job.coverage['output_tokens'], 50)
        self.assertEqual(self.job.coverage['usage_calls'], 1)
        relation = GraphRelation.objects.get(extraction=self.job)
        self.assertEqual(relation.status, 'candidate')

    def test_model_decides_to_read_neighbor_and_can_cite_only_the_actually_read_text(self):
        model = self.run_model([self.tool('get_neighbor_chunks', {'chunk_id': 'c10', 'direction': 'next'}),
                                self.page([self.row(11), self.row(12)])])
        self.assertEqual(self.job.status, 'completed')
        self.assertEqual(self.job.created_relations, 1)
        self.assertEqual(self.job.rejected, 1)
        self.assertEqual(self.job.coverage['read_ids'], ['c10', 'c11', 'c30'])
        self.assertEqual(self.job.coverage['tool_calls'], 1)
        self.assertEqual(model.stream.call_count, 2)
        self.assertEqual(GraphRelation.objects.get().evidence.get().chunks[0]['chunk_id'], 'c11')

    def test_repeated_search_is_cached_and_does_not_send_the_same_body_again(self):
        first = self.tool('search_document_chunks', {'query': self.job.focus}, 'a')
        first.tool_call_chunks.append({'index': 1, 'id': 'b', 'name': 'search_document_chunks',
                                        'args': json.dumps({'query': self.job.focus})})
        # 从工具分片重新构造，确保第二个调用也经历 LangChain 原生组装。
        first = AIMessageChunk(content='', tool_call_chunks=first.tool_call_chunks)
        model = self.run_model([first, self.page([])])
        self.assertEqual(self.job.status, 'completed')
        self.assertEqual(self.job.coverage['tool_calls'], 2)
        self.assertEqual(self.job.coverage['chunks_read'], 2)
        self.search.assert_called_once()
        tool_messages = [m.content for m in model.stream.call_args.args[0] if m.type == 'tool']
        self.assertEqual(len(tool_messages), 2)
        self.assertTrue(all('没有新增正文' in text for text in tool_messages))

    def test_output_truncation_stops_once_without_recursive_retries(self):
        model = self.run_model([AIMessageChunk(content='{"relations":[', response_metadata={'finish_reason': 'length'})])
        self.assertEqual(self.job.status, 'failed')
        self.assertIn('截断', self.job.error)
        self.assertEqual(model.stream.call_count, 1)
        self.assertFalse(GraphRelation.objects.exists())

    @patch('kb.graph_focus.MAX_CALLS', 1)
    def test_task_call_budget_keeps_a_saved_page_and_never_approves_it(self):
        model = self.run_model([self.page([self.row(10)], more=True)])
        self.assertEqual(model.stream.call_count, 1)
        self.assertEqual(self.job.status, 'failed')
        self.assertEqual(self.job.coverage['stage'], 'limited')
        self.assertEqual(self.job.created_relations, 1)
        self.assertEqual(GraphRelation.objects.get().status, 'candidate')

    @patch('kb.graph_focus.MAX_TOOLS', 1)
    def test_tool_budget_counts_invalid_and_duplicate_requests_across_rounds(self):
        model = self.run_model([self.tool('get_document_chunks', {'chunk_ids': ['foreign']}, 'a'),
                                self.tool('get_document_chunks', {'chunk_ids': ['foreign']}, 'b')])
        self.assertEqual(self.job.coverage['tool_calls'], 1)
        self.assertEqual(self.job.coverage['stage'], 'limited')
        self.assertIn('补查次数', self.job.error)
        self.assertEqual(model.stream.call_count, 2)

    def test_continuation_without_new_facts_stops_and_preserves_existing_candidates(self):
        model = self.run_model([self.page([self.row(10)], more=True), self.page([self.row(10)], more=True)])
        self.assertEqual(self.job.status, 'failed')
        self.assertIn('没有新增', self.job.error)
        self.assertEqual(model.stream.call_count, 2)
        self.assertEqual(GraphRelation.objects.count(), 1)
        self.assertEqual(self.job.created_relations, 1)

    def test_source_change_after_tool_request_prevents_the_next_model_call(self):
        def changed():
            yield self.tool('get_neighbor_chunks', {'chunk_id': 'c10', 'direction': 'next'})
            self.doc.md_content += '已修改'
            self.doc.save(update_fields=['md_content'])
        model = Mock(); model.bind.return_value = model; model.bind_tools.return_value = model
        model.stream.side_effect = [changed()]
        with patch('kb.agent._get_llm', return_value=model), patch('kb.config.llm_settings', return_value={}):
            run_extraction(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'failed')
        self.assertIn('资料已变化', self.job.error)
        self.assertEqual(model.stream.call_count, 1)
        self.assertEqual(self.job.coverage['chunks_read'], 2)

    @patch('kb.graph_focus.MAX_OUTPUT_CHARS', 10)
    def test_stream_budget_closes_the_response_and_does_not_keep_reading(self):
        closed = []
        def chunks(messages):
            try:
                yield AIMessageChunk(content='x' * 11)
                self.fail('输出预算达到后仍在读取模型流')
            finally:
                closed.append(True)
        model = Mock(); model.bind.return_value = model; model.bind_tools.return_value = model
        model.stream.side_effect = chunks
        with patch('kb.agent._get_llm', return_value=model), patch('kb.config.llm_settings', return_value={}):
            run_extraction(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(closed, [True])
        self.assertEqual(self.job.coverage['stage'], 'limited')
        self.assertIn('累计输出', self.job.error)

    @patch('kb.graph_focus.MAX_INPUT_CHARS', 1)
    def test_input_budget_stops_before_any_model_request(self):
        model = self.run_model([])
        model.stream.assert_not_called()
        self.assertEqual(self.job.coverage['stage'], 'limited')

    @patch('kb.graph_focus.MAX_READ_CHUNKS', 2)
    def test_neighbor_reads_cannot_expand_the_context_without_a_global_limit(self):
        model = self.run_model([self.tool('get_neighbor_chunks', {'chunk_id': 'c10', 'direction': 'next'})])
        self.assertEqual(model.stream.call_count, 1)
        self.assertEqual(self.job.coverage['chunks_read'], 2)
        self.assertEqual(self.job.coverage['stage'], 'limited')
        self.assertIn('原文读取预算', self.job.error)
