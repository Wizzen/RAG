from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock,patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase,TestCase
from langchain_core.messages import AIMessageChunk
from langchain_core.utils.function_calling import convert_to_openai_tool

from kb.knowledge_tables import build_candidates,catalog,read_tables


TEXT='''## 10.3.8 质量分级
10.3.8.1 底波分级仅适用于声程大于近场区长度；不同种类缺陷独立分级。
表9 锻件超声检测质量分级
| 等级 | I | II | III |
| --- | --- | --- | --- |
| 单个缺陷当量直径 | \\leqslant \\phi 4 | \\leqslant \\phi 4 + 6 \\text{ dB} | >\\phi 4 + 6 \\text{ dB} |
| 底波降低量 | <=6 dB | <=12 dB | >12 dB |
| 密集缺陷 | — | — | <=4 mm |
'''


class KnowledgeTableParserTests(SimpleTestCase):
    def setUp(self):
        self.chunk={'chunk_id':'table-c1','text':TEXT}
        self.document=SimpleNamespace(original_name='标准A.pdf')

    def build(self,fetched=None,order=None,**kwargs):
        fetched=fetched or {'table-c1':self.chunk};order=order or list(fetched)
        table=read_tables(fetched,order)[0]
        return build_candidates(table,fetched,self.document,object_name='锻件',property_name='质量分级',object_type='material',**kwargs)

    def test_real_cells_form_grade_then_parameter_paths_with_source_notes(self):
        rows=self.build()
        self.assertEqual(len(rows),7)
        self.assertEqual([part['name'] for part in rows[1]['path']],['质量分级','II','单个缺陷当量直径'])
        self.assertEqual(rows[1]['value']['content'],r'\leqslant \phi 4 + 6 \text{ dB}')
        for row in rows:
            self.assertIn(row['quote'],TEXT)
            self.assertIn('近场',row['conditions']['source_notes'][0])
            self.assertNotIn('all_of',row['conditions'])
            self.assertEqual(row['_chunks'][0]['chunk_id'],'table-c1')

    def test_only_requested_parameters_are_generated(self):
        rows=self.build(parameter_names=['底波降低量'])
        self.assertEqual(len(rows),3)
        self.assertTrue(all(row['path'][-1]['name']=='底波降低量' for row in rows))
        with self.assertRaises(ValueError):self.build(parameter_names=['猜测参数'])

    def test_classes_in_rows_are_transposed_without_swapping_values(self):
        self.chunk['text']='表1 锻件质量分级\n| 等级 | 底波降低量 | 直径 |\n|---|---|---|\n| I | <=6 dB | <=4 mm |\n| II | <=12 dB | <=6 mm |'
        rows=self.build(entries_axis='rows')
        self.assertEqual(len(rows),4)
        self.assertEqual([part['name'] for part in rows[2]['path']],['质量分级','II','底波降低量'])
        self.assertEqual(rows[2]['value']['content'],'<=12 dB')

    def test_boundary_requires_reading_next_chunk_and_supports_unambiguous_continuation(self):
        fetched={'table-c1':self.chunk};order=['table-c1','c2','c3']
        self.assertEqual(catalog(fetched,order)['tables'][0]['needs_chunk'],'c2')
        with self.assertRaisesMessage(ValueError,'补读'):self.build(fetched,order)
        fetched['c2']={'chunk_id':'c2','text':'| 附加参数 | 1 mm | 2 mm | 3 mm |'}
        fetched['c3']={'chunk_id':'c3','text':'## 10.4 其它材料'}
        rows=self.build(fetched,order)
        self.assertEqual(len(rows),10)
        self.assertEqual(rows[-1]['source_chunk_id'],'c2')
        self.assertTrue(any(part['chunk_id']=='table-c1' for part in rows[-1]['supporting_quotes']))

    def test_duplicate_or_incomplete_geometry_and_unsupported_html_are_explicit(self):
        for text in [TEXT.replace('| 底波降低量 | <=6 dB | <=12 dB | >12 dB |','| 底波降低量 | <=6 dB |'),
                     TEXT.replace('| I | II | III |','| I | I | III |')]:
            self.chunk['text']=text
            with self.assertRaises(ValueError):self.build()
        self.chunk['text']='<table><tr><td rowspan="2">锻件</td></tr></table>'
        result=catalog({'table-c1':self.chunk},['table-c1'])
        self.assertEqual(result['tables'],[])
        self.assertIn('HTML',result['unsupported'][0]['issue'])

    def test_another_material_cannot_borrow_unrelated_context_to_pass_validation(self):
        self.chunk['text']=TEXT.replace('10.3.8','10.2.8').replace('锻件','棒材')
        fetched={'table-c1':self.chunk,'forging':{'chunk_id':'forging','text':'## 10.3 锻件超声检测质量分级'}}
        with self.assertRaisesMessage(ValueError,'主体'):
            self.build(fetched,context_chunk_ids=['forging'])

    def test_numbered_ancestor_can_supply_the_actual_object_name(self):
        self.chunk['text']=TEXT.replace('表9 锻件超声检测质量分级','表9 超声检测质量分级')
        fetched={'table-c1':self.chunk,'title':{'chunk_id':'title','text':'## 10.3 锻件超声检测要求'}}
        rows=self.build(fetched,context_chunk_ids=['title'])
        self.assertEqual(len(rows),7)
        with self.assertRaises(ValueError):self.build(fetched,context_chunk_ids=['unread'])

    def test_trailing_footnotes_and_wrapped_text_are_retained_in_every_candidate(self):
        self.chunk['text']=TEXT+'\n注：这里另有适用条件，\n不得用于其它材料。\n'
        rows=self.build()
        self.assertTrue(all(any('不得用于其它材料' in note for note in row['conditions']['source_notes']) for row in rows))
        self.assertTrue(all(any('注：这里另有适用条件，\n不得用于其它材料。'==part['quote'] for part in row['supporting_quotes']) for row in rows))

    def test_footnote_in_the_next_read_chunk_is_not_lost_at_a_boundary(self):
        fetched={'table-c1':self.chunk,'next':{'chunk_id':'next','text':'注：本表仅适用于演示条件。\n\n## 10.4 下一节'}}
        rows=self.build(fetched,['table-c1','next'])
        self.assertIn('注：本表仅适用于演示条件。',rows[0]['conditions']['source_notes'])
        self.assertTrue(any(part['chunk_id']=='next' for part in rows[0]['supporting_quotes']))


class KnowledgeTableWorkerTests(TestCase):
    def setUp(self):
        from kb.models import Document,GraphExtraction,KnowledgeBase
        from kb.publication import digest
        self.kb=KnowledgeBase.objects.create(name='表格测试',slug='knowledge-table-build')
        self.chunks=[{'chunk_id':'table-c1','text':TEXT},{'chunk_id':'next','text':'## 10.4 下一章'}]
        self.doc=Document.objects.create(kb=self.kb,original_name='标准A.pdf',md_content=TEXT+'\n## 10.4 下一章',status='completed')
        self.job=GraphExtraction.objects.create(document=self.doc,focus='锻件质量分级',schema_version=2,source_digest=digest(self.doc.md_content))
        self.selection={'table_id':read_tables({'table-c1':self.chunks[0]},['table-c1','next'])[0].id,
            'object_name':'锻件','property_name':'质量分级','object_type':'material'}

    def run_model(self,replies):
        from kb.graph_extraction import run_extraction
        model=Mock();model.bind.return_value=model;model.bind_tools.return_value=model
        model.stream.side_effect=[iter([reply]) for reply in replies]
        with patch('kb.agent._get_llm',return_value=model),patch('kb.config.llm_settings',return_value={}), \
             patch('kb.keyword_index.get_chunks',return_value=self.chunks),patch('kb.provenance.annotate_results'), \
             patch('kb.graph_retrieval.search_document',return_value={'rows':self.chunks,'method':'hybrid','vector_status':'ok'}):
            run_extraction(self.job.pk)
        self.job.refresh_from_db()
        return model

    def tool(self,name,args,id='tool1'):
        return AIMessageChunk(content='',tool_call_chunks=[{'index':0,'id':id,'name':name,'args':json.dumps(args)}])

    def test_compact_table_selection_saves_cells_as_unreviewed_candidates_in_one_call(self):
        from kb.models import KnowledgeAssertion
        from kb.knowledge_graph import lookup_facts
        model=self.run_model([AIMessageChunk(content=json.dumps({'table_requests':[self.selection],'assertions':[],'has_more':False}))])
        self.assertEqual(self.job.status,'completed')
        self.assertEqual(self.job.created_relations,7)
        self.assertEqual(self.job.coverage['tables_built'],1)
        self.assertEqual(self.job.coverage['table_candidates'],7)
        self.assertEqual(self.job.coverage['model_calls'],1)
        self.assertEqual(self.job.coverage['tool_calls'],1)
        self.assertFalse(KnowledgeAssertion.objects.exclude(status='candidate').exists())
        self.assertEqual(lookup_facts([self.kb.slug],'锻件')['facts'],[])
        tools=model.bind_tools.call_args.args[0]
        self.assertIn('build_table_knowledge',[tool.__name__ for tool in tools])
        for tool in tools:convert_to_openai_tool(tool)

    def test_native_tool_persists_once_and_final_empty_json_does_not_drop_candidates(self):
        model=self.run_model([self.tool('build_table_knowledge',self.selection),
            self.tool('build_table_knowledge',self.selection,'again'),
            AIMessageChunk(content='{"assertions":[],"has_more":false}')])
        self.assertEqual(self.job.status,'completed')
        self.assertEqual(self.job.created_relations,7)
        self.assertEqual(model.stream.call_count,3)
        self.assertEqual(self.job.coverage['tables_built'],1)

    def test_wrong_scope_or_malformed_table_selection_fails_without_approving_or_fabricating(self):
        selection={**self.selection,'table_id':'not-read'}
        self.run_model([AIMessageChunk(content=json.dumps({'table_requests':[selection],'assertions':[],'has_more':False}))])
        self.assertEqual(self.job.status,'failed')
        self.assertIn('已读目录',self.job.error)
        self.assertEqual(self.job.created_relations,0)

    def test_confirmed_fact_keeps_qualifying_notes_and_formula_display_does_not_change_sources(self):
        from kb.knowledge_hierarchy import assertion_queryset,review_assertion,fact_snapshot,display_text
        self.run_model([AIMessageChunk(content=json.dumps({'table_requests':[self.selection],'assertions':[],'has_more':False}))])
        admin=get_user_model().objects.create_user('table-reviewer',is_staff=True)
        item=assertion_queryset().filter(extraction=self.job).first()
        raw=item.value['content']
        review_assertion(item,'verified',admin)
        snapshot=fact_snapshot(item)
        self.assertIn('近场',snapshot['text'])
        self.assertIn('独立分级',snapshot['text'])
        self.assertIn('≤',display_text(raw))
        item.refresh_from_db()
        self.assertEqual(item.value['content'],raw)
