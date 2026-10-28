from __future__ import annotations

"""验证自动抽取覆盖原文、证据约束、幂等和异步调度。"""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from kb.graph_extraction import parse_relations, persist_relations, schedule_document, windows, run_extraction
from kb.knowledge_graph import relation_current, relation_queryset
from kb.models import Document, GraphExtraction, GraphRelation, KnowledgeBase
from kb.publication import digest


class AutomaticGraphTests(TestCase):
    @patch('kb.provenance.annotate_results')
    @patch('kb.keyword_index.search')
    @patch('kb.keyword_index.get_chunks')
    def test_english_focus_can_search_chinese_document_without_initial_literal_matches(self,chunks,search,annotate):
        from unittest.mock import Mock
        from langchain_core.messages import AIMessage
        from kb.graph_extraction import extract_focused
        chunks.return_value=[{'chunk_id':'cover','text':'无损检测标准简介。','section':'简介'},
                             {'chunk_id':'neighbor','text':self.doc.md_content,'section':'检测要求'}]
        search.side_effect=[[],[chunks.return_value[1]]]
        model=Mock(); model.bind_tools.return_value=model
        model.invoke.side_effect=[AIMessage(content='',tool_calls=[{'id':'search1','name':'search_document_chunks',
                                                                  'args':{'query':'检查要求'}}]),
                                  AIMessage(content=json.dumps({'relations':[{**self.row,'source_chunk_id':'neighbor'}]}))]
        rows,rejected=extract_focused(model,self.doc,'acceptance level')
        self.assertEqual(len(rows),1); self.assertEqual(rejected,0)
        self.assertEqual(rows[0]['_chunks'][0]['chunk_id'],'neighbor')
        self.assertEqual(search.call_args_list[0].kwargs['source'],self.doc.original_name)
        self.assertEqual(search.call_args_list[1].args[1],'检查要求')
        self.assertEqual(search.call_args_list[1].kwargs['source'],self.doc.original_name)
        initial=json.loads(model.invoke.call_args_list[0].args[0][1].content)
        self.assertEqual(initial['focus'],'acceptance level')
        self.assertEqual(initial['outline'][1]['chunk_id'],'neighbor')
        self.assertEqual([c['chunk_id'] for c in initial['chunks']],['cover'])

    @patch('kb.provenance.annotate_results')
    @patch('kb.keyword_index.search',return_value=[])
    @patch('kb.keyword_index.get_chunks')
    def test_outline_is_navigation_and_unread_chunks_cannot_supply_evidence(self,chunks,search,annotate):
        from unittest.mock import Mock
        from langchain_core.messages import AIMessage
        from kb.graph_extraction import extract_focused
        chunks.return_value=[{'chunk_id':'cover','text':'资料概述。'}, {'chunk_id':'neighbor','text':self.doc.md_content}]
        model=Mock(); model.bind_tools.return_value=model
        model.invoke.return_value=AIMessage(content=json.dumps({'relations':[{**self.row,'source_chunk_id':'neighbor'}]}))
        rows,rejected=extract_focused(model,self.doc,'acceptance level')
        self.assertEqual(rows,[]); self.assertEqual(rejected,1)

    @patch('kb.provenance.annotate_results')
    @patch('kb.keyword_index.search',return_value=[])
    @patch('kb.keyword_index.get_chunks')
    def test_model_can_jump_to_a_section_but_not_read_another_documents_chunk(self,chunks,search,annotate):
        from unittest.mock import Mock
        from langchain_core.messages import AIMessage
        from kb.graph_extraction import extract_focused
        chunks.return_value=[{'chunk_id':'cover','text':'资料概述。'}, {'chunk_id':'neighbor','text':self.doc.md_content}]
        model=Mock(); model.bind_tools.return_value=model
        model.invoke.side_effect=[
            AIMessage(content='',tool_calls=[{'id':'foreign','name':'get_document_chunks','args':{'chunk_ids':['foreign-doc-id']}}]),
            AIMessage(content='',tool_calls=[{'id':'bad','name':'search_document_chunks','args':{'query':[]}}]),
            AIMessage(content='',tool_calls=[{'id':'local','name':'get_document_chunks','args':{'chunk_ids':['neighbor']}}]),
            AIMessage(content=json.dumps({'relations':[{**self.row,'source_chunk_id':'neighbor'}]}))]
        rows,rejected=extract_focused(model,self.doc,'acceptance level')
        self.assertEqual(len(rows),1); self.assertEqual(rejected,0)
        messages=model.invoke.call_args_list[-1].args[0]
        results=[m.content for m in messages if m.type=='tool']
        self.assertIn('不可访问',results[0]); self.assertIn('无效',results[1])
        self.assertEqual(json.loads(results[2])[0]['chunk_id'],'neighbor')

    @patch('kb.keyword_index.search')
    @patch('kb.keyword_index.get_chunks')
    def test_focused_extraction_can_read_neighbor_and_binds_its_evidence(self, chunks, search):
        from unittest.mock import Mock
        from langchain_core.messages import AIMessage
        from kb.graph_extraction import extract_focused
        chunks.return_value = [{'chunk_id':'seed','text':'本节介绍人员要求。'},
                               {'chunk_id':'neighbor','text':self.doc.md_content}]
        search.return_value = [chunks.return_value[0]]
        model = Mock()
        model.bind_tools.return_value.invoke.side_effect = [
            AIMessage(content='', tool_calls=[{'id':'call1','name':'get_neighbor_chunks',
                                               'args':{'chunk_id':'seed','direction':'next'}}]),
            AIMessage(content=json.dumps({'relations':[{**self.row,'source_chunk_id':'neighbor'}]})),
        ]
        rows, rejected = extract_focused(model, self.doc, '每日检查要求')
        self.assertEqual(rejected, 0)
        self.assertEqual(rows[0]['_chunks'][0]['chunk_id'], 'neighbor')
        self.assertIn('每日检查要求', model.bind_tools.return_value.invoke.call_args.args[0][1].content)
        self.assertIn(self.doc.md_content, model.bind_tools.return_value.invoke.call_args.args[0][-2].content)

    def test_focus_is_required_by_page_and_new_runs_need_independent_confirmation(self):
        from kb.knowledge_graph import review_relation
        self.client.force_login(self.user)
        response = self.client.post(reverse('kb:graph_action'), {'kb':self.folder.slug,'action':'extract'})
        self.assertEqual(response.status_code, 400)
        first = GraphExtraction.objects.create(document=self.doc, focus='检查频率')
        second = GraphExtraction.objects.create(document=self.doc, focus='检查频率')
        persist_relations(self.doc, digest(self.doc.md_content), [self.row], first)
        relation = relation_queryset().get(extraction=first)
        review_relation(relation, 'verified', self.user)
        persist_relations(self.doc, digest(self.doc.md_content), [self.row], second)
        self.assertEqual(GraphRelation.objects.get(extraction=second).status, 'candidate')

    @patch('kb.keyword_index.get_chunks')
    def test_graph_page_reuses_page_circle_evidence_panel(self, chunks):
        from kb.models import ChunkProvenance
        row = {**self.row, '_chunks':[{'chunk_id':'located','page':'第 3 页'}]}
        persist_relations(self.doc, digest(self.doc.md_content), [row])
        ChunkProvenance.objects.create(document=self.doc, chunk_id='located', page_start=2, page_end=2,
                                       blocks=[{'page':2,'bbox':[10,10,80,80],'kind':'text'}])
        chunks.return_value = [{'chunk_id':'located','text':self.doc.md_content}]
        self.client.force_login(self.user)
        page = self.client.get(reverse('kb:graph'), {'kb':self.folder.slug})
        self.assertContains(page, 'data-graph-chunk="located"')
        self.assertContains(page, '第 3 页 · 查看原文圈注')
        self.assertContains(page, 'js/evidence_panel.js')

    def test_truncated_response_splits_source_without_accepting_partial_json(self):
        from unittest.mock import Mock
        from kb.graph_extraction import extract_section
        model = Mock()
        model.invoke.side_effect = [
            SimpleNamespace(content='incomplete', response_metadata={'finish_reason': 'length'}),
            SimpleNamespace(content='{"relations":[]}', response_metadata={}),
            SimpleNamespace(content='{"relations":[]}', response_metadata={}),
        ]
        source = '原始资料' * 300
        self.assertEqual(extract_section(model, source), ([], 0))
        calls = model.invoke.call_args_list
        left = calls[1].args[0][1]['content']
        right = calls[2].args[0][1]['content']
        self.assertEqual(left + right[200:], source)

    def setUp(self):
        vector = patch('kb.graph_retrieval.get_kb_vectorstore',side_effect=RuntimeError('offline'))
        vector.start(); self.addCleanup(vector.stop)
        self.user = get_user_model().objects.create_user('auto-graph', is_staff=True)
        self.folder = KnowledgeBase.objects.create(name='标准库', slug='auto-folder', is_folder=True)
        self.kb = KnowledgeBase.objects.create(name='标准文档', slug='auto-standard', parent=self.folder)
        self.doc = Document.objects.create(kb=self.kb, original_name='标准.pdf', status='completed',
            md_content='制动装置要求每日检查连接是否牢固。')
        self.row = {'subject': {'name': '制动装置', 'type': 'component'}, 'predicate': 'requires',
                    'object': {'name': '每日检查', 'type': 'inspection'}, 'quote': self.doc.md_content,
                    'conditions': {'频率': '每日'}}

    def test_exact_evidence_required_and_hallucinated_quotes_rejected(self):
        rows, bad = parse_relations(json.dumps({'relations': [self.row]}, ensure_ascii=False), self.doc.md_content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(bad, 0)
        _, bad = parse_relations(json.dumps({'relations': [{**self.row, 'quote': '杜撰的检查要求原文'}]}), self.doc.md_content)
        self.assertEqual(bad, 1)

    def test_candidates_have_automatic_source_and_folder_review_works(self):
        version = digest(self.doc.md_content)
        self.assertEqual(persist_relations(self.doc, version, [self.row]), 1)
        self.assertEqual(persist_relations(self.doc, version, [self.row]), 0)
        relation = relation_queryset().get()
        self.assertEqual(relation.status, 'candidate')
        self.assertTrue(relation_current(relation))
        self.assertEqual(relation.evidence.get().document, self.doc)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('kb:graph'), {'kb': self.folder.slug}), '制动装置')
        response = self.client.post(reverse('kb:graph_action'), {'kb': self.folder.slug, 'action': 'review',
                                   'relation': relation.pk, 'status': 'verified'})
        self.assertEqual(response.status_code, 200)
        self.doc.md_content = '新版本'
        self.doc.save(update_fields=['md_content'])
        self.assertFalse(relation_current(relation_queryset().get()))
        with self.assertRaises(ValueError):
            persist_relations(self.doc, version, [self.row])

    def test_all_document_text_is_covered(self):
        text = 'x' * 23001
        parts = list(windows(text))
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0] + parts[1][500:] + parts[2][500:], text)

    @patch('kb.graph_extraction._worker.submit')
    def test_schedule_runs_after_commit_and_prevents_duplicate_jobs(self, submit):
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(schedule_document(self.doc, focus='检查频率'))
            self.assertFalse(schedule_document(self.doc, retry=True, focus='检查频率'))
        submit.assert_called_once()

    @patch('kb.config.llm_settings', return_value={})
    @patch('kb.agent._get_llm')
    def test_worker_extracts_without_manual_graph_input(self, llm, config):
        from langchain_core.messages import AIMessageChunk
        llm.return_value.bind.return_value.stream.return_value = iter([AIMessageChunk(
            content=json.dumps({'relations': [self.row]}), response_metadata={'finish_reason': 'stop'})])
        job = GraphExtraction.objects.create(document=self.doc, source_digest=digest(self.doc.md_content), total=1)
        run_extraction(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.created_relations, 1)
        self.assertEqual(GraphRelation.objects.get().status, 'candidate')

    @patch('kb.agent._get_llm', side_effect=RuntimeError('模型不可用'))
    def test_failure_is_visible_and_does_not_break_document(self, llm):
        job = GraphExtraction.objects.create(document=self.doc, source_digest=digest(self.doc.md_content), total=1)
        run_extraction(job.pk)
        job.refresh_from_db(); self.doc.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertIn('模型不可用', job.error)
        self.assertEqual(self.doc.status, 'completed')


from django.test import SimpleTestCase


class PointerEntityRejectionTests(SimpleTestCase):
    """表号/图号/条款号只是知识的位置，作为实体或关系终点必须被拒绝。"""

    def _row(self, subject="缺陷", obj="表7"):
        return {"subject": {"name": subject, "type": "defect"},
                "predicate": "documented_by",
                "object": {"name": obj, "type": "clause"},
                "quote": "缺陷的质量分级见表7。", "conditions": {}}

    def test_table_number_as_object_rejected(self):
        from kb.graph_schema import normalize_candidate
        self.assertIsNone(normalize_candidate(self._row()))

    def test_pointer_variants_rejected(self):
        from kb.graph_schema import normalize_candidate, is_pointer_name
        for name in ("表7", "表 22", "表 G.5～表 G.7", "图3", "图K.7", "附录K", "10.2.8.1", "第5条"):
            self.assertTrue(is_pointer_name(name), name)
            self.assertIsNone(normalize_candidate(self._row(obj=name)), name)

    def test_real_entity_still_accepted(self):
        from kb.graph_schema import normalize_candidate
        row = {"subject": {"name": "密集型缺陷", "type": "defect"},
               "predicate": "has_parameter",
               "object": {"name": "V级", "type": "concept"},
               "quote": "密集型缺陷评为V级。",
               "conditions": {"parameter": "当量值", "operator": ">", "value": "2", "unit": "mm"}}
        self.assertIsNotNone(normalize_candidate(row))
