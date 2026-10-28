from __future__ import annotations

"""可视化数据、证据侧栏和受控词表的权限及语义回归。"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase
from django.urls import reverse

from kb.graph_extraction import persist_relations
from kb.graph_schema import normalize_candidate
from kb.knowledge_graph import relation_queryset, review_relation
from kb.models import Document, GraphExtraction, GraphRelation, KnowledgeBase
from kb.publication import digest


class GraphExplorerTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user('explorer-admin', is_staff=True)
        self.reader = get_user_model().objects.create_user('explorer-reader')
        self.kb = KnowledgeBase.objects.create(name='标准库', slug='explorer', is_folder=True)
        self.child = KnowledgeBase.objects.create(name='标准', slug='explorer-doc', parent=self.kb)
        self.doc = Document.objects.create(kb=self.child, original_name='source.pdf', status='completed',
                                           md_content='制动器要求每日检查连接。')
        self.run = GraphExtraction.objects.create(document=self.doc, focus='检查周期', status='completed')
        self.row = {'subject':{'type':'component','name':'制动器'}, 'predicate':'requires',
                    'object':{'type':'inspection','name':'每日检查'}, 'quote':self.doc.md_content,
                    'conditions':{'frequency':'每日'}}
        persist_relations(self.doc, digest(self.doc.md_content), [self.row], self.run)
        self.relation = relation_queryset().get()
        self.client.force_login(self.manager)

    def data(self, **params):
        return self.client.get(reverse('kb:graph'), {'kb':self.kb.slug, 'format':'graph', **params})

    def test_candidates_are_visible_to_managers_but_not_used_or_exposed_to_readers(self):
        payload = self.data().json()
        self.assertEqual(len(payload['nodes']), 2)
        self.assertEqual(payload['edges'][0]['data']['status'], 'candidate')
        self.assertEqual(payload['edges'][0]['data']['focus'], self.run.focus)
        self.assertNotIn('quote', str(payload))
        self.client.force_login(self.reader)
        self.assertEqual(self.data().json()['edges'], [])
        url = reverse('kb:graph_relation', args=[self.relation.pk])
        self.assertEqual(self.client.get(url, {'kb':self.kb.slug}).status_code, 404)
        review_relation(self.relation, 'verified', self.manager)
        self.assertEqual(len(self.data().json()['edges']), 1)
        self.doc.md_content = 'source changed'
        self.doc.save(update_fields=['md_content'])
        self.assertEqual(self.data().json()['edges'], [])

    def test_run_filter_and_neighbors_do_not_expand_into_other_scopes(self):
        second = GraphExtraction.objects.create(document=self.doc, focus='另一方面')
        persist_relations(self.doc, digest(self.doc.md_content), [self.row], second)
        result = self.data(run=self.run.pk, node=self.relation.subject_id).json()
        self.assertEqual(len(result['edges']), 1)
        self.assertEqual(result['edges'][0]['data']['relation_id'], self.relation.pk)
        other = KnowledgeBase.objects.create(name='private', slug='explorer-private', department='private')
        self.child.parent = other
        self.child.department = 'private'
        self.child.save(update_fields=['parent','department'])
        self.assertEqual(self.data(node=self.relation.subject_id).status_code, 404)
        self.assertEqual(self.data(run=second.pk).status_code, 404)

    @patch('kb.keyword_index.get_chunks', return_value=[])
    def test_sidebar_has_original_evidence_conditions_and_per_relation_confirmation(self, chunks):
        url = reverse('kb:graph_relation', args=[self.relation.pk])
        html = self.client.get(url, {'kb':self.kb.slug}).json()['html']
        self.assertIn(self.doc.md_content, html)
        self.assertIn('周期', html)
        self.assertIn('确认此结果', html)
        self.assertIn('source.pdf', html)
        self.assertEqual(self.relation.status, 'candidate')
        self.assertEqual(self.client.get(url, {'kb':'unknown'}).status_code, 404)

    def test_invalid_ids_do_not_raise_and_large_graphs_are_explicitly_bounded(self):
        self.assertEqual(self.data(node='invalid').status_code, 400)
        self.assertEqual(self.data(run='invalid').status_code, 400)
        for i in range(101):
            GraphRelation.objects.create(subject=self.relation.subject, object=self.relation.object,
                                         predicate='requires', identity=f'bounded-{i}')
        payload = self.data().json()
        self.assertTrue(payload['truncated'])
        self.assertEqual(len(payload['edges']), 100)
        self.assertEqual(payload['total_count'],102)
        next_page=self.data(offset=100).json()
        self.assertEqual(len(next_page['edges']),2)
        self.assertFalse(next_page['truncated'])
        self.assertFalse({e['id'] for e in payload['edges']} & {e['id'] for e in next_page['edges']})
        self.assertEqual(self.data(offset='invalid').status_code,400)

    def test_page_loads_local_visualization_and_batch_options(self):
        page = self.client.get(reverse('kb:graph'), {'kb':self.kb.slug})
        self.assertContains(page, 'g6-5.1.1.min.js')
        self.assertContains(page, 'graph_explorer.js')
        self.assertContains(page, 'graphInspector')
        self.assertContains(page, self.run.focus)

    def test_document_can_be_selected_once_without_selecting_a_library(self):
        page=self.client.get(reverse('kb:graph'),{'document':str(self.doc.pk)})
        self.assertEqual(page.status_code,200)
        self.assertContains(page,'id="graphDocument"')
        self.assertContains(page,'name="document" value="'+str(self.doc.pk)+'"')
        self.assertNotContains(page,'id="graphScope"')
        self.assertNotContains(page,'<select name="document"')
        self.assertContains(page,'graph_page.js')
        self.assertContains(page,'开始构建')

    def test_document_selection_does_not_mix_other_documents_from_the_same_library(self):
        other_doc=Document.objects.create(kb=self.child,original_name='other.pdf',status='completed',md_content='另一个部件需要定期检查。')
        other_run=GraphExtraction.objects.create(document=other_doc,focus='另一个方面')
        row={**self.row,'quote':other_doc.md_content,'subject':{'type':'component','name':'另一个部件'},
             'object':{'type':'inspection','name':'定期检查'}}
        persist_relations(other_doc,digest(other_doc.md_content),[row],other_run)
        result=self.client.get(reverse('kb:graph'),{'document':str(self.doc.pk),'format':'graph'}).json()
        self.assertEqual([edge['data']['relation_id'] for edge in result['edges']],[self.relation.pk])
        self.assertEqual(self.client.get(reverse('kb:graph'),{'document':str(self.doc.pk),'run':other_run.pk}).status_code,404)
        self.assertEqual(self.client.get(reverse('kb:graph'),{'document':'bad-id'}).status_code,400)

    def test_global_document_picker_excludes_inaccessible_documents(self):
        private=KnowledgeBase.objects.create(name='私有库',slug='private-picker',department='private')
        doc=Document.objects.create(kb=private,original_name='private-source.pdf',status='completed',md_content='私有资料的检查要求。')
        self.client.force_login(self.reader)
        self.assertNotContains(self.client.get(reverse('kb:graph')),'private-source.pdf')
        self.assertEqual(self.client.get(reverse('kb:graph'),{'document':str(doc.pk)}).status_code,404)


class SchemaValidationTests(SimpleTestCase):
    def row(self, **values):
        return {'subject':{'name':'焊接接头', 'type':'component'}, 'predicate':'requires',
                'object':{'name':'检查', 'type':'inspection'}, 'quote':'焊接接头需要检查。',
                'conditions':{}, **values}

    def test_synonymous_entity_types_have_one_canonical_identity(self):
        for name in ('failure', 'fault', 'defect_type', 'defect_subtype'):
            row = self.row(object={'name':'裂纹', 'type':name})
            self.assertEqual(normalize_candidate(row)['object']['type'], 'defect')

    def test_deictic_subject_ordinals_and_reversed_negation_are_rejected(self):
        self.assertIsNone(normalize_candidate(self.row(subject={'name':'本部分', 'type':'clause'})))
        self.assertIsNone(normalize_candidate(self.row(predicate='has_failure', quote='焊接接头不应有裂纹。')))
        self.assertIsNotNone(normalize_candidate(self.row(predicate='prohibits', quote='焊接接头不应有裂纹。')))

    def test_novel_snake_case_predicate_is_allowed(self):
        """谓词开放式：现有清单优先，贴切的新 snake_case 谓词不再被硬拒。"""
        row = normalize_candidate(self.row(predicate='measured_by',
                                           object={'name':'衰减曲线', 'type':'concept'}))
        self.assertIsNotNone(row)
        self.assertEqual(row['predicate'], 'measured_by')
        # 已有同义别名仍归一到标准谓词
        self.assertEqual(normalize_candidate(self.row(predicate='classified_as'))['predicate'], 'is_a')

    def test_numeric_requirements_need_parameter_operator_value_and_unit(self):
        self.assertIsNone(normalize_candidate(self.row(predicate='has_parameter', conditions={'minimum':'500 lx'})))
        row = self.row(predicate='has_parameter', conditions={'parameter':'照度','operator':'>=','value':500,'unit':'lx'})
        self.assertIsNotNone(normalize_candidate(row))
