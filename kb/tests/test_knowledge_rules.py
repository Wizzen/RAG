from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock,patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase,TestCase
from langchain_core.messages import AIMessageChunk

from kb.knowledge_rules import build_matrix_rules
from kb.knowledge_tables import catalog,read_tables,build_candidates
from kb.pipeline import _md_for_embedding


ACTIVITY='''## 10.3 声发射源的活性等级
对于连续加载且最大载荷可持续保持的，按以下规则判定。
快速增加且事件数 E >= 10 个/min 时，声发射源为超强活性。
表1 声发射源活性判定
| 序号 | 在两次保载阶段出现的定位源个数 | 活性等级 |
|---|---|---|
| 1 | E <= 3 | 弱活性 |
| 2 | 3 < E <= 10 | 中活性 |
| 3 | E > 10 | 强活性 |
'''
STRENGTH='''## 10.4 声发射源强度等级
表2 声发射源强度判定
<table><tr><td>源的强度级别</td><td>源强度</td></tr><tr><td>低强度</td><td>Q &lt; a</td></tr><tr><td>中强度</td><td>a ≤ Q ≤ b</td></tr><tr><td>高强度</td><td>Q &gt; b</td></tr><tr><td colspan="2">注：a、b 应由试验确定，不能套用其它材料的阈值。</td></tr></table>
'''
MATRIX='''## 10.5 声发射源的综合等级评定
声发射源按强度和活性等级进行综合等级评定。
表4 声发射源综合等级
<table><tr><td rowspan="2">声发射源强度等级</td><td colspan="4">声发射源活性等级</td></tr><tr><td>超强活性</td><td>强活性</td><td>中活性</td><td>弱活性</td></tr><tr><td>高强度</td><td>IV</td><td>IV</td><td>III</td><td>II</td></tr><tr><td>中强度</td><td>IV</td><td>III</td><td>III</td><td>I</td></tr><tr><td>低强度</td><td>III</td><td>III</td><td>II</td><td>I</td></tr></table>
'''


def fixture():
    raw=ACTIVITY+'\n'+STRENGTH+'\n'+MATRIX
    doc=SimpleNamespace(pk='rule-fixture',original_name='规则示例.pdf',md_content=raw)
    chunks=[{'chunk_id':cid,'text':_md_for_embedding(text)} for cid,text in [('activity',ACTIVITY),('strength',STRENGTH),('matrix',MATRIX)]]
    return doc,{c['chunk_id']:c for c in chunks}


def selection(doc,fetched):
    tables=read_tables(fetched,list(fetched),doc)
    activity=next(t for t in tables if t.caption and '表1 ' in t.caption.text)
    strength=next(t for t in tables if t.caption and '表2 ' in t.caption.text)
    matrix=next(t for t in tables if t.caption and '表4 ' in t.caption.text)
    return matrix,{'object_name':'声发射源','property_name':'综合等级','object_type':'concept','header_rows':2,
        'bindings':[{'parameter':'声发射源强度等级','table_id':strength.id,'match_column':0,'value_columns':[1]},
                    {'parameter':'声发射源活性等级','table_id':activity.id,'match_column':2,'value_columns':[1],
                     'context_quotes':[{'chunk_id':'activity','quote':'对于连续加载且最大载荷可持续保持的，按以下规则判定。'}]},
                    {'parameter':'声发射源活性等级','entry':'超强活性','source_chunk_id':'activity',
                     'quote':'快速增加且事件数 E >= 10 个/min 时，声发射源为超强活性。',
                     'context_quotes':[{'chunk_id':'activity','quote':'对于连续加载且最大载荷可持续保持的，按以下规则判定。'}]}]}


class KnowledgeRuleBuilderTests(SimpleTestCase):
    def setUp(self):self.doc,self.fetched=fixture()

    def build(self,**changes):
        table,args=selection(self.doc,self.fetched);args.update(changes)
        return build_matrix_rules(table,self.fetched,self.doc,**args)

    def test_html_merges_are_restored_without_reindexing_or_hidden_chunk_reads(self):
        result=catalog(self.fetched,list(self.fetched),self.doc)
        self.assertEqual(len(result['tables']),3)
        matrix=next(t for t in result['tables'] if '表4 ' in t['caption'])
        self.assertEqual(matrix['suggested_header_rows'],2)
        only={'matrix':self.fetched['matrix']}
        self.assertEqual(len(catalog(only,list(self.fetched),self.doc)['tables']),1)

    def test_each_grade_gets_or_of_and_groups_and_bound_thresholds(self):
        rows=self.build()
        self.assertEqual(len(rows),4)
        by_grade={r['path'][1]['name']:r for r in rows}
        self.assertEqual({k:len(v['value']['content']['any_of']) for k,v in by_grade.items()},{'I':2,'II':2,'III':5,'IV':3})
        combos=by_grade['III']['value']['content']['any_of']
        self.assertTrue(all(len(c['all_of'])==2 for c in combos))
        middle_strong=next(c for c in combos if [x['value'] for x in c['all_of']]==['中强度','强活性'])
        self.assertEqual(middle_strong['all_of'][1]['definitions'][0]['value'],'E > 10')
        self.assertIn('最大载荷',middle_strong['all_of'][1]['definitions'][0]['source_notes'][0])
        self.assertIn('试验确定',middle_strong['all_of'][0]['definitions'][0]['source_notes'][0])
        self.assertFalse(any([x['value'] for x in c['all_of']]==['高强度','强活性'] for c in combos))
        self.assertIn('E >= 10',json.dumps(combos,ensure_ascii=False))

    def test_simple_html_parameters_keep_table_notes_and_values(self):
        table=next(t for t in read_tables(self.fetched,list(self.fetched),self.doc) if t.caption and '表2 ' in t.caption.text)
        rows=build_candidates(table,self.fetched,self.doc,object_name='声发射源',property_name='强度',entries_axis='rows')
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[0]['value']['content'],'Q < a')
        self.assertIn('试验确定',rows[0]['conditions']['source_notes'][0])

    def test_unit_line_between_caption_and_table_is_required_and_retained(self):
        self.doc.md_content=self.doc.md_content.replace('表2 声发射源强度判定\n','表2 声发射源强度判定\n\n单位为毫米\n\n')
        self.fetched['strength']['text']=_md_for_embedding(STRENGTH.replace('表2 声发射源强度判定\n','表2 声发射源强度判定\n\n单位为毫米\n\n'))
        table=next(t for t in read_tables(self.fetched,list(self.fetched),self.doc) if t.caption and '表2 ' in t.caption.text)
        self.assertFalse(table.issue)
        rows=build_candidates(table,self.fetched,self.doc,object_name='声发射源',property_name='强度',entries_axis='rows')
        self.assertTrue(any('单位为毫米' in note for note in rows[0]['conditions']['source_notes']))

    def test_matrix_without_bindings_is_explicitly_a_category_rule(self):
        rows=self.build(bindings=[])
        self.assertNotIn('definitions',rows[0]['value']['content']['any_of'][0]['all_of'][0])

    def test_missing_prose_definition_is_not_fabricated_from_other_categories(self):
        _,args=selection(self.doc,self.fetched)
        with self.assertRaisesMessage(ValueError,'超强活性'):self.build(bindings=args['bindings'][:2])

    def test_wrong_table_header_depth_and_foreign_object_are_rejected(self):
        with self.assertRaisesMessage(ValueError,'合并表头'):self.build(header_rows=1)
        with self.assertRaisesMessage(ValueError,'主体'):self.build(object_name='钢板')

    def test_definitions_must_be_read_and_class_names_exact(self):
        _,args=selection(self.doc,self.fetched);bindings=deepcopy(args['bindings']);bindings[0]['table_id']='unread'
        with self.assertRaises(ValueError):self.build(bindings=bindings)
        bindings=deepcopy(args['bindings']);bindings[2]['quote']='猜测超强活性的阈值为 E > 999'
        with self.assertRaisesMessage(ValueError,'逐字'):self.build(bindings=bindings)

    def test_rule_validator_rejects_invented_thresholds_and_mixed_operators(self):
        from kb.knowledge_hierarchy import validate_value
        with self.assertRaises(ValueError):validate_value({'type':'rule','content':{'all_of':[],'any_of':[]}},ACTIVITY)
        with self.assertRaises(ValueError):validate_value({'type':'rule','content':{'parameter':'活性等级','value':'E > 999'}},ACTIVITY)


class KnowledgeRuleWorkerTests(TestCase):
    def setUp(self):
        from kb.models import Document,GraphExtraction,KnowledgeBase
        from kb.publication import digest
        fixture_doc,self.fetched=fixture()
        kb=KnowledgeBase.objects.create(name='判定规则',slug='decision-rule-test')
        self.doc=Document.objects.create(kb=kb,original_name=fixture_doc.original_name,md_content=fixture_doc.md_content,status='completed')
        self.job=GraphExtraction.objects.create(document=self.doc,focus='综合等级以及各级判定依据',schema_version=2,source_digest=digest(self.doc.md_content))

    def run_model(self,replies):
        from kb.graph_extraction import run_extraction
        model=Mock();model.bind.return_value=model;model.bind_tools.return_value=model
        model.stream.side_effect=[iter([AIMessageChunk(content=json.dumps(reply,ensure_ascii=False))]) for reply in replies]
        with patch('kb.agent._get_llm',return_value=model),patch('kb.config.llm_settings',return_value={}), \
             patch('kb.keyword_index.get_chunks',return_value=list(self.fetched.values())),patch('kb.provenance.annotate_results'), \
             patch('kb.graph_retrieval.search_document',return_value={'rows':list(self.fetched.values()),'method':'hybrid','vector_status':'ok'}):
            run_extraction(self.job.pk)
        self.job.refresh_from_db();return model

    def test_matrix_selection_persists_reviewable_rules_and_full_snapshot_logic(self):
        from kb.knowledge_hierarchy import assertion_queryset,review_assertion,fact_snapshot
        table,args=selection(self.doc,self.fetched)
        model=self.run_model([{'table_requests':[{'table_id':table.id,'layout':'decision_matrix',**args}],'has_more':False}])
        self.assertEqual(self.job.status,'completed',self.job.error)
        self.assertEqual(self.job.created_relations,4)
        item=assertion_queryset().filter(extraction=self.job,path__1__name='III').first()
        self.assertEqual(item.status,'candidate')
        self.assertIsNone(fact_snapshot(item))
        user=get_user_model().objects.create_user('rule-reviewer',is_staff=True)
        with patch('kb.keyword_index.get_chunks',return_value=list(self.fetched.values())):
            review_assertion(item,'verified',user)
            snapshot=fact_snapshot(item)
        self.assertIn('E > 10',snapshot['text']);self.assertIn('同时满足',snapshot['text']);self.assertIn('满足任一组合',snapshot['text'])
        self.assertEqual(model.stream.call_count,1)

    def test_rejected_scope_gets_one_bounded_repair_without_relaxing_quotes(self):
        row=build_matrix_rules(*selection(self.doc,self.fetched)[:1],self.fetched,self.doc,**selection(self.doc,self.fetched)[1])[0]
        bad=deepcopy(row);bad['scope']={'method':'模型自行改写的加载方式'}
        model=self.run_model([{'assertions':[bad],'has_more':False},{'assertions':[row],'has_more':False}])
        self.assertEqual(self.job.status,'completed',self.job.error)
        self.assertEqual(self.job.created_relations,1)
        self.assertEqual(model.stream.call_count,2)
        self.assertTrue(any('scope 必须逐字' in str(message.content) for message in model.stream.call_args_list[1].args[0]))

    def test_summary_echo_is_repaired_as_protocol_error_not_accepted_as_a_fact(self):
        row=build_matrix_rules(*selection(self.doc,self.fetched)[:1],self.fetched,self.doc,**selection(self.doc,self.fetched)[1])[0]
        model=self.run_model([{'saved_relations':[{'value':row['value']}],'has_more':False},{'assertions':[row],'has_more':False}])
        self.assertEqual(self.job.status,'completed',self.job.error)
        self.assertEqual(self.job.created_relations,1)
        self.assertEqual(model.stream.call_count,2)

    def test_repair_does_not_loop_when_the_same_invalid_output_repeats(self):
        model=self.run_model([{'saved_relations':[],'has_more':False},{'saved_relations':[],'has_more':False}])
        self.assertEqual(self.job.status,'failed')
        self.assertEqual(model.stream.call_count,2)
        self.assertEqual(self.job.created_relations,0)
