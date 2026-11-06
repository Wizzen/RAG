from __future__ import annotations

"""覆盖全篇扫描、查漏、续页、表头证据和部分失败，防止图谱退化为摘要。"""

import json
from unittest.mock import patch

from django.test import TestCase, SimpleTestCase
from langchain_core.messages import AIMessage

from kb.graph_build import build_document_graph, plan_batches, BATCH_CHARS
from kb.graph_extraction import extract_focused
from kb.graph_progress import GraphProgress
from kb.models import Document, GraphExtraction, GraphRelation, KnowledgeBase
from kb.publication import digest


class BatchPlanTests(SimpleTestCase):
    def test_long_chunks_have_no_gaps_and_no_same_id_overwrites_within_a_batch(self):
        for length in (2401,4481,4800,12000):
            text=''.join(chr(0x4e00+i%1000) for i in range(length))
            batches=plan_batches([{'chunk_id':'long','text':text}])
            self.assertTrue(all(len({c['chunk_id'] for c in b})==len(b) for b in batches))
            pieces=[c['text'] for batch in batches for c in batch]
            self.assertEqual(pieces[0]+''.join(p[160:] for p in pieces[1:]),text)
            self.assertTrue(all(len(p)<=BATCH_CHARS for p in pieces))


class DocumentGraphBuildTests(TestCase):
    def setUp(self):
        self.kb=KnowledgeBase.objects.create(name='扫描测试',slug='systematic-test')
        self.doc=Document.objects.create(kb=self.kb,original_name='standard.pdf',status='completed',md_content='')

    def prepare(self, count=1):
        self.chunks=[{'chunk_id':f'c{i}','section':f'第{i}节','text':f'部件{i}要求检查项目{i}，检查周期为每日。'} for i in range(count)]
        self.doc.md_content='\n'.join(c['text'] for c in self.chunks); self.doc.save(update_fields=['md_content'])
        self.job=GraphExtraction.objects.create(document=self.doc,focus='全部检查要求与分级',source_digest=digest(self.doc.md_content))
        return self.chunks

    def row(self, c):
        i=c['chunk_id'][1:]
        return {'subject':{'name':'部件'+i,'type':'component'},'predicate':'requires',
                'object':{'name':'检查项目'+i,'type':'inspection'},'quote':c['text'],
                'source_chunk_id':c['chunk_id'],'conditions':{'frequency':'每日'}}

    def model(self, callback):
        class Model:
            calls=[]
            def bind_tools(self,*args,**kwargs): return self
            def invoke(self,messages):
                data=json.loads(messages[1].content)
                audit='这是独立查漏轮' in messages[0].content
                self.calls.append((audit,data))
                result=callback(audit,data,messages)
                return result if isinstance(result,AIMessage) else AIMessage(content=json.dumps(result,ensure_ascii=False))
        return Model()

    def run_build(self,model):
        # 绕过流传输，仅模拟实际模型消息；持久化及所有校验仍走生产代码。
        with patch('kb.keyword_index.get_chunks',return_value=self.chunks),patch('kb.provenance.annotate_results'), \
             patch('kb.graph_extraction.call_model',side_effect=lambda m,msgs,*args:m.invoke(msgs)):
            build_document_graph(model,self.job,GraphProgress(self.job.pk))
        self.job.refresh_from_db()

    def test_every_chunk_is_processed_and_audited_including_unmatched_last_chapter(self):
        self.prepare(37)
        def callback(audit,data,messages):
            rows=[] if audit else [self.row(c) for c in data['chunks'] if c['chunk_id'] in data['primary_chunk_ids']]
            if audit:
                self.assertTrue(GraphRelation.objects.filter(extraction=self.job).exists())
            return {'relations':rows,'has_more':False}
        model=self.model(callback); self.run_build(model)
        self.assertEqual(self.job.status,'completed')
        self.assertEqual(self.job.created_relations,37)
        self.assertEqual(self.job.coverage['chunks_done'],37)
        self.assertEqual(self.job.coverage['audited_batches'],self.job.total)
        for audit in (True,False):
            covered={cid for flag,data in model.calls if flag==audit for cid in data['primary_chunk_ids']}
            self.assertEqual(covered,{c['chunk_id'] for c in self.chunks})
        self.assertEqual(set(GraphRelation.objects.values_list('status',flat=True)),{'candidate'})

    def test_audit_adds_a_missed_requirement_and_keeps_both_conditions(self):
        self.prepare()
        def callback(audit,data,messages):
            row=self.row(self.chunks[0])
            if audit:
                self.assertEqual(len(data['existing_relations']),1)
                row['conditions']={'frequency':'每日','scope':'运行前'}
            return {'relations':[row]}
        self.run_build(self.model(callback))
        self.assertEqual(self.job.created_relations,2)

    def test_duplicate_assertions_merge_but_keep_distinct_original_quotes(self):
        self.prepare(2)
        self.chunks[1]['text']='再次规定：'+self.chunks[0]['text']
        self.doc.md_content='\n'.join(c['text'] for c in self.chunks); self.doc.save(update_fields=['md_content'])
        self.job.source_digest=digest(self.doc.md_content); self.job.save(update_fields=['source_digest'])
        def callback(audit,data,messages):
            if audit: return {'relations':[]}
            first=self.row(self.chunks[0]); second={**first,'quote':self.chunks[1]['text'],'source_chunk_id':'c1'}
            return {'relations':[first,second]}
        self.run_build(self.model(callback))
        self.assertEqual(self.job.created_relations,1)
        self.assertEqual(GraphRelation.objects.get().evidence.count(),2)
        self.assertEqual(self.job.events.filter(kind='candidate').count(),1)

    def test_partial_failure_keeps_saved_candidates_and_reports_incomplete_coverage(self):
        self.prepare(9)
        def callback(audit,data,messages):
            if 'c8' in data['primary_chunk_ids']: raise RuntimeError('末章模型连接中断')
            return {'relations':[] if audit else [self.row(c) for c in data['chunks'] if c['chunk_id'] in data['primary_chunk_ids']]}
        self.run_build(self.model(callback))
        self.assertEqual(self.job.status,'failed')
        self.assertEqual(self.job.created_relations,8)
        self.assertEqual(self.job.coverage['chunks_done'],8)
        self.assertEqual(self.job.done,1); self.assertEqual(self.job.total,2)
        self.assertEqual(self.job.coverage['batches'][-1]['status'],'failed')
        self.assertFalse(self.job.events.filter(kind='complete').exists())

    def test_timeout_is_split_into_smaller_scopes_and_audited_again(self):
        self.prepare(8)
        def callback(audit,data,messages):
            if len(data['primary_chunk_ids'])>2: raise TimeoutError('timed out')
            return {'relations':[] if audit else [self.row(c) for c in data['chunks'] if c['chunk_id'] in data['primary_chunk_ids']]}
        self.run_build(self.model(callback))
        self.assertEqual(self.job.status,'completed')
        self.assertEqual(self.job.created_relations,8)
        self.assertEqual(self.job.coverage['chunks_done'],8)

    def test_header_and_row_can_use_separate_exact_evidence(self):
        self.prepare(2)
        self.chunks[0]['text']='钢板验收允许的缺欠尺寸及质量分级表头。'
        self.chunks[1]['text']='一级质量要求缺欠深度不大于2毫米。'
        self.doc.md_content='\n'.join(c['text'] for c in self.chunks); self.doc.save(update_fields=['md_content'])
        self.job.source_digest=digest(self.doc.md_content); self.job.save(update_fields=['source_digest'])
        row={'subject':{'name':'钢板','type':'component'},'predicate':'has_parameter','object':{'name':'缺欠深度','type':'parameter'},
             'quote':self.chunks[1]['text'],'source_chunk_id':'c1','conditions':{'parameter':'缺欠深度','operator':'<=','value':2,'unit':'毫米','scope':'一级质量'},
             'supporting_quotes':[{'chunk_id':'c0','quote':self.chunks[0]['text']}]}
        self.run_build(self.model(lambda audit,data,messages:{'relations':[] if audit else [row]}))
        self.assertEqual(self.job.created_relations,1)
        self.assertEqual(GraphRelation.objects.get().evidence.count(),2)

    def test_forged_supporting_quote_is_not_saved(self):
        self.prepare()
        row={**self.row(self.chunks[0]),'supporting_quotes':[{'chunk_id':'foreign','quote':self.chunks[0]['text']}]}
        self.run_build(self.model(lambda audit,data,messages:{'relations':[] if audit else [row]}))
        self.assertEqual(self.job.created_relations,0); self.assertEqual(self.job.rejected,1)

    def test_more_pages_continue_before_marking_the_batch_complete(self):
        self.prepare(2)
        class Model:
            def __init__(self,rows): self.rows=rows; self.calls=0
            def bind_tools(self,*args,**kwargs): return self
            def invoke(self,messages):
                self.calls+=1
                return AIMessage(content=json.dumps({'relations':[self.rows[self.calls-1]],'has_more':self.calls==1}))
        model=Model([self.row(c) for c in self.chunks])
        rows,rejected=extract_focused(model,self.doc,self.job.focus,document_chunks=self.chunks,initial_chunks=self.chunks)
        self.assertEqual(len(rows),2); self.assertEqual(rejected,0); self.assertEqual(model.calls,2)

    def test_perpetual_more_flag_keeps_partial_candidates_but_never_reports_complete(self):
        self.prepare()
        self.run_build(self.model(lambda audit,data,messages:{'relations':[self.row(self.chunks[0])],'has_more':True}))
        self.assertEqual(self.job.status,'failed')
        self.assertEqual(self.job.created_relations,1)
        self.assertEqual(self.job.coverage['chunks_done'],0)

    def test_model_cannot_jump_to_an_unrelated_batch(self):
        self.prepare(3)
        from unittest.mock import Mock
        model=Mock(); model.bind_tools.return_value=model
        model.invoke.side_effect=[AIMessage(content='',tool_calls=[{'name':'get_document_chunks','id':'outside','args':{'chunk_ids':['c2']}}]),
                                  AIMessage(content='{"relations":[],"has_more":false}')]
        rows,bad=extract_focused(model,self.doc,self.job.focus,document_chunks=self.chunks,
                                initial_chunks=self.chunks[:1],primary_chunk_ids=['c0'])
        self.assertEqual(rows,[])
        initial=json.loads(model.invoke.call_args_list[0].args[0][1].content)
        self.assertEqual([c['chunk_id'] for c in initial['outline']],['c0'])
        tools=[m.content for m in model.invoke.call_args.args[0] if m.type=='tool']
        self.assertIn('不可访问',tools[0])

    def test_version_change_stops_remaining_batches_without_claiming_complete(self):
        self.prepare(9)
        def callback(audit,data,messages):
            if audit:
                self.doc.md_content='文档已经改变'; self.doc.save(update_fields=['md_content'])
                return {'relations':[]}
            return {'relations':[self.row(c) for c in data['chunks'] if c['chunk_id'] in data['primary_chunk_ids']]}
        model=self.model(callback); self.run_build(model)
        self.assertEqual(self.job.status,'failed')
        self.assertEqual(self.job.coverage['chunks_done'],0)
        self.assertEqual(len(model.calls),2)
        self.assertEqual(self.job.coverage['batches'][-1]['status'],'pending')
