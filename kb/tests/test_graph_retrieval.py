from __future__ import annotations

"""语义补查必须能跨措辞命中，并始终受文档和当前批次限制。"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from kb.graph_retrieval import search_document


class DocumentHybridSearchTests(SimpleTestCase):
    def setUp(self):
        self.document=SimpleNamespace(kb=SimpleNamespace(slug='doc-kb'),original_name='current.pdf')
        self.chunks=[{'chunk_id':'one','text':'表面不得存在裂纹。'},{'chunk_id':'two','text':'一级验收要求的允许缺欠深度为2毫米。'}]
        self.store=Mock(); self.store.embeddings.embed_query.return_value=[0.1,0.2]
        self.store._collection.query.return_value={'ids':[['two']]}

    @patch('kb.graph_retrieval.keyword_index.search',return_value=[])
    @patch('kb.graph_retrieval.get_kb_vectorstore')
    def test_semantic_hit_without_any_literal_keyword_match(self,get_store,keywords):
        get_store.return_value=self.store
        result=search_document(self.document,'acceptance level',self.chunks)
        self.assertEqual(result['rows'][0]['chunk_id'],'two')
        self.assertEqual(result['rows'][0]['via'],'vector')
        self.store.embeddings.embed_query.assert_called_once_with('acceptance level')
        args=self.store._collection.query.call_args.kwargs
        self.assertEqual(set(args['ids']),{'one','two'})
        self.assertEqual(args['where'],{'source':'current.pdf'})
        keywords.assert_called_once_with('doc-kb','acceptance level',limit=2,source='current.pdf')

    @patch('kb.graph_retrieval.keyword_index.search')
    @patch('kb.graph_retrieval.get_kb_vectorstore')
    def test_batch_scope_is_applied_before_vector_top_k_and_results_are_rechecked(self,get_store,keywords):
        get_store.return_value=self.store
        self.store._collection.query.return_value={'ids':[['foreign','two','one']]}
        keywords.return_value=[{'chunk_id':'two'},{'chunk_id':'foreign'},{'chunk_id':'one'}]
        result=search_document(self.document,'验收',self.chunks,allowed_ids={'one'})
        self.assertEqual([r['chunk_id'] for r in result['rows']],['one'])
        self.assertEqual(self.store._collection.query.call_args.kwargs['ids'],['one'])

    @patch('kb.graph_retrieval.keyword_index.search')
    @patch('kb.graph_retrieval.get_kb_vectorstore',side_effect=RuntimeError('embedding offline'))
    def test_unavailable_vector_service_is_reported_and_keyword_lane_remains_usable(self,get_store,keywords):
        keywords.return_value=[{'chunk_id':'one'}]
        result=search_document(self.document,'裂纹',self.chunks)
        self.assertEqual(result['method'],'keyword')
        self.assertEqual(result['vector_status'],'unavailable')
        self.assertEqual(result['rows'][0]['via'],'keyword')

    @patch('kb.graph_retrieval.get_kb_vectorstore')
    def test_empty_scope_does_not_query_another_document(self,get_store):
        result=search_document(self.document,'验收',self.chunks,allowed_ids={'foreign'})
        self.assertEqual(result['rows'],[]); get_store.assert_not_called()
