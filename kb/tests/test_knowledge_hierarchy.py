from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import Mock,patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from langchain_core.messages import AIMessageChunk

from accounts.models import UserProfile
from kb.graph_extraction import run_extraction
from kb.knowledge_graph import lookup_facts,validate_fact
from kb.knowledge_hierarchy import (assertion_current,assertion_queryset,fact_snapshot,
    persist_assertions,review_assertion,retrieve_passages,validate_assertions,visible_assertions)
from kb.models import (Document,GraphExtraction,KnowledgeAssertion,KnowledgeBase,
                       KnowledgeNode,KnowledgeObject)
from kb.publication import digest


class KnowledgeHierarchyTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_user('hierarchy-admin',is_staff=True)
        self.reader=get_user_model().objects.create_user('hierarchy-reader')
        self.folder=KnowledgeBase.objects.create(name='材料标准',slug='hierarchy-folder',is_folder=True)
        self.kb=KnowledgeBase.objects.create(name='标准A',slug='hierarchy-a',parent=self.folder)
        self.text='标准A规定锻件超声检测质量分级：I级底波降低量应不大于6 dB，II级底波降低量应不大于12 dB。'
        self.doc=Document.objects.create(kb=self.kb,original_name='标准A.pdf',status='completed',md_content=self.text)
        self.chunk={'chunk_id':'hierarchy-c1','text':self.text,'page_label':'第 1 页'}
        self.row={'object':{'name':'锻件','type':'material'},'path':[
            {'name':'质量分级','kind':'property'},{'name':'I级','kind':'entry'},
            {'name':'判定条件','kind':'group'},{'name':'底波降低量','kind':'property'}],
            'value':{'type':'number','content':6,'operator':'<=','unit':'dB'},
            'scope':{'standard':'标准A','method':'超声检测'},'conditions':{},
            'quote':self.text,'source_chunk_id':'hierarchy-c1'}
        self.job=GraphExtraction.objects.create(document=self.doc,focus='锻件质量分级',schema_version=2,
                                                source_digest=digest(self.text))
        self.client.force_login(self.admin)

    def save(self,row=None,doc=None,job=None):
        row=deepcopy(row or self.row);doc=doc or self.doc;job=job or self.job
        rows,rejected=validate_assertions({'assertions':[row]},{self.chunk['chunk_id']:self.chunk},document=doc)
        self.assertEqual(rejected,0)
        persist_assertions(doc,digest(doc.md_content),rows,job)
        return assertion_queryset().order_by('-pk').first()

    def structure(self,user=None,**params):
        if user:self.client.force_login(user)
        return self.client.get(reverse('kb:knowledge_structure'),{'kb':self.folder.slug,**params})

    def test_validation_checks_objects_paths_complete_values_and_read_source_membership(self):
        rows,rejected=validate_assertions({'assertions':[self.row]},{'hierarchy-c1':self.chunk},document=self.doc)
        self.assertEqual((len(rows),rejected),(1,0))
        for change in [{'source_chunk_id':'foreign'},{'value':{'type':'number','content':600}},
                       {'object':{'name':'铸件','type':'material'}},{'scope':{'standard':'未读标准'}},
                       {'path':[{'name':'I级','kind':'entry'}]}, {'value':{'type':'text','content':'编造的要求'}}]:
            row={**deepcopy(self.row),**change}
            with self.subTest(change=change):
                accepted,rejected=validate_assertions({'assertions':[row]},{'hierarchy-c1':self.chunk},document=self.doc)
                self.assertEqual((accepted,rejected),([],1))

    def test_value_lists_and_subparameters_share_a_node_without_overwriting_other_values(self):
        first=self.save()
        row=deepcopy(self.row);row['value']={'type':'list','content':[6,12],'unit':'dB'}
        second=self.save(row)
        self.assertEqual(first.node_id,second.node_id)
        self.assertEqual(KnowledgeAssertion.objects.count(),2)
        self.assertEqual(KnowledgeNode.objects.count(),4)
        self.assertEqual(KnowledgeObject.objects.count(),1)

    def test_incremental_documents_add_properties_to_the_same_object_and_keep_the_same_standard_branch(self):
        first=self.save()
        kb=KnowledgeBase.objects.create(name='补充资料',slug='hierarchy-extra',parent=self.folder)
        doc=Document.objects.create(kb=kb,original_name='补充资料.pdf',status='completed',md_content=self.text)
        job=GraphExtraction.objects.create(document=doc,focus='补充定义',schema_version=2,source_digest=digest(doc.md_content))
        row=deepcopy(self.row);row['path']=row['path'][:2]+[{'name':'等级定义','kind':'group'}]
        row['value']={'type':'text','content':self.text}
        item=self.save(row,doc,job)
        self.assertEqual(first.node.object_id,item.node.object_id)
        self.assertEqual(first.node.parent.parent_id,item.node.parent_id)
        self.assertEqual(KnowledgeAssertion.objects.count(),2)
        self.assertEqual(KnowledgeObject.objects.count(),1)

    def test_other_objects_grades_and_standard_versions_have_independent_paths(self):
        first=self.save()
        row=deepcopy(self.row);row['object']['name']='标准A';row['object']['type']='standard'
        other=self.save(row)
        self.assertNotEqual(first.node.object_id,other.node.object_id)
        row=deepcopy(self.row);row['scope']={'standard':'标准A','version':'6'}
        variant=self.save(row)
        self.assertEqual(first.node.object_id,variant.node.object_id)
        self.assertNotEqual(first.node_id,variant.node_id)

    def test_each_new_extraction_creates_candidates_without_inheriting_review(self):
        first=self.save();review_assertion(first,'verified',self.admin)
        job=GraphExtraction.objects.create(document=self.doc,focus='再次抽取',schema_version=2,source_digest=digest(self.text))
        second=self.save(job=job)
        self.assertNotEqual(first.pk,second.pk)
        self.assertEqual(second.status,'candidate')
        self.assertEqual(first.node_id,second.node_id)

    def test_candidates_are_not_returned_to_readers_or_answers(self):
        self.save()
        self.assertEqual(lookup_facts([self.kb.slug],'锻件质量分级')['facts'],[])
        self.assertEqual(self.structure(user=self.reader).json()['objects'],[])
        data=self.structure(user=self.admin).json()
        self.assertEqual(data['objects'][0]['label'],'锻件')
        self.assertEqual(data['tree'][0]['label'],'质量分级')
        self.assertEqual(data['tree'][0]['children'][0]['label'],'I级')

    def test_verified_values_retrieve_current_sources_and_snapshots_are_revoked_after_edits(self):
        item=self.save();review_assertion(item,'verified',self.admin)
        result=lookup_facts([self.kb.slug],'锻件质量分级I级')
        self.assertEqual(len(result['facts']),1)
        snapshot=result['facts'][0]
        from kb.publication import validate_sources,visible_citations
        citation={'kind':'graph','relation_id':snapshot['relation_id'],'fact_digest':snapshot['fact_digest'],'graph_snapshot':snapshot}
        self.assertTrue(validate_sources([snapshot],self.reader.pk))
        self.assertTrue(visible_citations([citation],self.reader))
        self.assertEqual(snapshot['relation_id'],f'k{item.pk}')
        self.assertTrue(validate_fact(snapshot,self.reader))
        with patch('kb.keyword_index.get_chunks',return_value=[self.chunk]):
            rows=retrieve_passages([self.kb.slug],[self.kb.slug],'锻件')
        self.assertEqual(rows[0]['chunk_id'],'hierarchy-c1')
        self.assertIn('质量分级',rows[0]['graph_paths'][0])
        self.assertEqual(self.client.get(reverse('kb:graph_fact',args=[f'k{item.pk}']),{'v':snapshot['fact_digest']}).status_code,200)
        self.doc.md_content+='新版本';self.doc.save(update_fields=['md_content'])
        self.assertFalse(validate_fact(snapshot,self.admin))
        self.assertFalse(validate_sources([snapshot],self.reader.pk))
        self.assertFalse(visible_citations([citation],self.reader))
        self.assertEqual(lookup_facts([self.kb.slug],'锻件')['facts'],[])
        self.assertEqual(self.client.get(reverse('kb:graph_fact',args=[f'k{item.pk}'])).status_code,410)

    def test_rejected_and_source_changed_values_cannot_be_approved_or_used(self):
        item=self.save();review_assertion(item,'rejected',self.admin)
        self.assertIsNone(fact_snapshot(item))
        self.doc.md_content+='changed';self.doc.save(update_fields=['md_content'])
        item=assertion_queryset().get(pk=item.pk)
        self.assertFalse(assertion_current(item))
        with self.assertRaises(ValueError):review_assertion(item,'verified',self.admin)

    def test_explicit_method_and_grade_select_the_right_branch_without_losing_subparameters(self):
        from kb.knowledge_hierarchy import approved_matches
        self.text+='另有射线检测要求。'
        self.doc.md_content=self.text;self.doc.save(update_fields=['md_content'])
        self.chunk['text']=self.text;self.row['quote']=self.text
        first=self.save();review_assertion(first,'verified',self.admin)
        row=deepcopy(self.row);row['path'][1]['name']='II级';row['value']['content']=12
        second=self.save(row);review_assertion(second,'verified',self.admin)
        row=deepcopy(self.row);row['scope']['method']='射线检测'
        other=self.save(row);review_assertion(other,'verified',self.admin)
        matches=approved_matches([self.kb.slug],'锻件超声检测 I 级的判定条件')
        self.assertEqual([item.pk for item in matches],[first.pk])
        self.assertEqual({item.pk for item in approved_matches([self.kb.slug],'锻件质量分级')},
                         {first.pk,second.pk,other.pk})

    def test_source_permissions_prevent_shared_objects_exposing_other_departments(self):
        self.save()
        hidden=KnowledgeBase.objects.create(name='私有标准',slug='hierarchy-private',department='机密',parent=self.folder)
        doc=Document.objects.create(kb=hidden,original_name='秘密.pdf',status='completed',md_content=self.text)
        job=GraphExtraction.objects.create(document=doc,focus='机密',schema_version=2,source_digest=digest(self.text))
        row=deepcopy(self.row);row['path'][-1]={'name':'秘密属性','source_name':'底波降低量','kind':'property'}
        item=self.save(row,doc,job);review_assertion(item,'verified',self.admin)
        allowed=self.structure(user=self.reader).json()
        self.assertEqual(allowed['objects'],[])
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(reverse('kb:graph_fact',args=[f'k{item.pk}'])).status_code,404)
        self.assertEqual(self.client.post(reverse('kb:knowledge_action'),{'kb':self.folder.slug,'assertion':item.pk,'status':'verified'}).status_code,404)

    def test_department_manager_cannot_approve_another_sources_general_library_values(self):
        user=get_user_model().objects.create_user('hierarchy-dept')
        UserProfile.objects.update_or_create(user=user,defaults={'department':'机械','role':UserProfile.Role.DEPT_ADMIN})
        item=self.save()
        self.client.force_login(user)
        response=self.client.post(reverse('kb:knowledge_action'),{'kb':self.folder.slug,'assertion':item.pk,'status':'verified'})
        self.assertEqual(response.status_code,404)

    def test_node_detail_shows_nested_conditions_and_escapes_model_text(self):
        row=deepcopy(self.row);row['conditions']={'all_of':[{'适用条件':'I级'},{'any_of':['6 dB','12 dB']}],'<script>':'test'}
        item=self.save(row)
        with patch('kb.keyword_index.get_chunks',return_value=[self.chunk]):
            response=self.structure(node=item.node_id)
        html=response.json()['html']
        self.assertIn('同时满足',html);self.assertIn('满足任一项',html)
        self.assertIn('查看原文圈注',html)
        self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>',html)
        self.assertIn('确认此条内容',html)

    def test_filtering_all_values_out_yields_an_empty_tree_without_a_false_permission_error(self):
        item=self.save()
        response=self.structure(object=item.node.object_id,status='verified')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['tree'],[])
        self.assertEqual(self.structure(object=999999).status_code,404)

    def test_source_links_keep_saved_chunk_identity_when_identical_text_occurs_on_another_page(self):
        item=self.save()
        other={**self.chunk,'chunk_id':'unrelated-identical-table-header','page_label':'第 99 页'}
        with patch('kb.keyword_index.get_chunks',return_value=[other,self.chunk]),patch('kb.provenance.annotate_results'):
            html=self.structure(node=item.node_id).json()['html']
        self.assertIn('data-graph-chunk="hierarchy-c1"',html)
        self.assertNotIn('unrelated-identical-table-header',html)
        item.evidence.update(chunks=[])
        with patch('kb.keyword_index.get_chunks',return_value=[other,self.chunk]),patch('kb.provenance.annotate_results'):
            html=self.structure(node=item.node_id).json()['html']
        self.assertNotIn('data-graph-chunk',html)

    def test_deleting_the_original_collection_preserves_cross_collection_assertions(self):
        first=self.save()
        kb=KnowledgeBase.objects.create(name='其他集合',slug='hierarchy-elsewhere')
        doc=Document.objects.create(kb=kb,original_name='other.pdf',status='completed',md_content=self.text)
        job=GraphExtraction.objects.create(document=doc,schema_version=2,target_object=first.node.object,source_digest=digest(self.text))
        other=self.save(doc=doc,job=job)
        self.folder.delete()
        self.assertTrue(KnowledgeAssertion.objects.filter(pk=other.pk).exists())
        other=assertion_queryset().get(pk=other.pk)
        self.assertTrue(assertion_current(other))

    def test_default_page_submission_selects_hierarchical_extraction(self):
        with patch('kb.graph_extraction.schedule_document',return_value=True) as schedule:
            response=self.client.post(reverse('kb:graph_action'),{'kb':self.folder.slug,'action':'extract','document':self.doc.pk,'focus':'质量分级'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(schedule.call_args.kwargs['schema_version'],2)

    def test_worker_uses_hierarchical_schema_and_persists_only_unreviewed_assertions(self):
        model=Mock();model.bind.return_value=model;model.bind_tools.return_value=model
        model.stream.return_value=iter([AIMessageChunk(content=json.dumps({'assertions':[self.row],'has_more':False},ensure_ascii=False))])
        with patch('kb.agent._get_llm',return_value=model),patch('kb.config.llm_settings',return_value={}), \
             patch('kb.keyword_index.get_chunks',return_value=[self.chunk]),patch('kb.provenance.annotate_results'), \
             patch('kb.graph_retrieval.search_document',return_value={'rows':[self.chunk],'method':'hybrid','vector_status':'ok'}):
            run_extraction(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status,'completed')
        self.assertEqual(KnowledgeAssertion.objects.count(),1)
        self.assertEqual(KnowledgeAssertion.objects.get().status,'candidate')
        self.assertIn('"assertions"',model.stream.call_args.args[0][0].content)
        self.assertEqual(model.bind_tools.call_args.kwargs['max_tokens'],32768)
        event=self.job.events.get(kind='candidate').payload
        self.assertIn('质量分级',event['predicate'])
        self.assertEqual(event['schema_version'],2)

    def test_truncated_hierarchical_arrays_save_complete_assertions_before_continuing(self):
        first='{"assertions":['+json.dumps(self.row,ensure_ascii=False)+',{"object":'
        model=Mock();model.bind.return_value=model;model.bind_tools.return_value=model
        model.stream.side_effect=[iter([AIMessageChunk(content=first,response_metadata={'finish_reason':'length'})]),
                                  iter([AIMessageChunk(content='{"assertions":[],"has_more":false}')])]
        with patch('kb.agent._get_llm',return_value=model),patch('kb.config.llm_settings',return_value={}), \
             patch('kb.keyword_index.get_chunks',return_value=[self.chunk]),patch('kb.provenance.annotate_results'), \
             patch('kb.graph_retrieval.search_document',return_value={'rows':[self.chunk],'method':'hybrid','vector_status':'ok'}):
            run_extraction(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status,'completed')
        self.assertEqual(self.job.created_relations,1)
        self.assertEqual(KnowledgeAssertion.objects.count(),1)
        self.assertEqual(model.stream.call_count,2)

    def test_roman_grade_names_cannot_be_sliced_from_a_different_grade(self):
        row=deepcopy(self.row)
        row['quote']='标准A规定锻件超声检测质量分级：II级底波降低量应不大于12 dB。'
        row['value']['content']=12
        rows,rejected=validate_assertions({'assertions':[row]}, {'hierarchy-c1':{'chunk_id':'hierarchy-c1','text':row['quote']}},document=self.doc)
        self.assertEqual((rows,rejected),([],1))

    def test_legacy_conversion_is_idempotent_keeps_the_old_data_and_requires_new_confirmation(self):
        from kb.graph_extraction import persist_relations
        from kb.knowledge_graph import relation_queryset,review_relation
        from kb.hierarchy_conversion import convert_relations
        row={'subject':{'name':'锻件','type':'material'},'predicate':'has_parameter',
             'object':{'name':'底波降低量','type':'parameter'},'quote':self.text,'_chunks':[{'chunk_id':'hierarchy-c1'}],
             'conditions':{'parameter':'底波降低量','operator':'<=','value':6,'unit':'dB','quality_grade':'I'}}
        persist_relations(self.doc,digest(self.text),[row],self.job)
        relation=relation_queryset().get();review_relation(relation,'verified',self.admin)
        with patch('kb.keyword_index.get_chunks',return_value=[self.chunk]):
            preview=convert_relations([self.kb.slug])
            self.assertEqual(preview['eligible'],1);self.assertEqual(KnowledgeAssertion.objects.count(),0)
            converted=convert_relations([self.kb.slug],apply=True)
            repeated=convert_relations([self.kb.slug],apply=True)
        self.assertEqual(converted['created'],1);self.assertEqual(repeated['created'],0)
        self.assertEqual(KnowledgeAssertion.objects.get().status,'candidate')
        self.assertEqual(KnowledgeAssertion.objects.get().path[1]['name'],'I')
        relation.refresh_from_db();self.assertEqual(relation.status,'verified')

    def test_binding_another_collections_object_is_explicit_and_respects_current_visibility(self):
        item=self.save()
        user=get_user_model().objects.create_user('hierarchy-appender')
        UserProfile.objects.update_or_create(user=user,defaults={'department':'机械','role':UserProfile.Role.DEPT_ADMIN})
        kb=KnowledgeBase.objects.create(name='机械资料',slug='hierarchy-managed',department='机械')
        doc=Document.objects.create(kb=kb,original_name='机械.pdf',status='completed',md_content=self.text)
        data={'kb':kb.slug,'action':'extract','document':doc.pk,'focus':'补充参数','target_object':item.node.object_id}
        self.client.force_login(user)
        with patch('kb.graph_extraction.schedule_document',return_value=True) as schedule:
            self.assertEqual(self.client.post(reverse('kb:graph_action'),data).status_code,404)
            schedule.assert_not_called()
            review_assertion(item,'verified',self.admin)
            self.assertEqual(self.client.post(reverse('kb:graph_action'),data).status_code,200)
            self.assertEqual(schedule.call_args.kwargs['target_object'].pk,item.node.object_id)
