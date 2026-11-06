from __future__ import annotations

"""验证真正的增量输出、工具分片、日志重放与未审核数据的权限边界。"""

import json
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import Runnable

from kb.graph_extraction import extract_focused, run_extraction
from kb.graph_progress import GraphProgress, call_model, visible_text
from kb.models import Document, GraphExtraction, GraphExtractionEvent, GraphRelation, KnowledgeBase
from kb.publication import digest


async def stream_text(response) -> str:
    return ''.join([part.decode() async for part in response.streaming_content])


class GraphProgressTests(TestCase):
    def setUp(self):
        vector=patch('kb.graph_retrieval.get_kb_vectorstore',side_effect=RuntimeError('offline'))
        vector.start(); self.addCleanup(vector.stop)
        self.admin = get_user_model().objects.create_user('live-admin', is_staff=True)
        self.reader = get_user_model().objects.create_user('live-reader')
        self.folder = KnowledgeBase.objects.create(name='实时标准库', slug='live-folder', is_folder=True)
        self.kb = KnowledgeBase.objects.create(name='实时标准', slug='live-doc', parent=self.folder)
        self.doc = Document.objects.create(kb=self.kb, original_name='标准.pdf', status='completed',
                                           md_content='制动装置要求每日检查连接是否牢固。')
        self.job = GraphExtraction.objects.create(document=self.doc, focus='每日检查', source_digest=digest(self.doc.md_content),total=1)
        self.row = {'subject':{'name':'制动装置','type':'component'}, 'predicate':'requires',
                    'object':{'name':'每日检查','type':'inspection'}, 'quote':self.doc.md_content,
                    'conditions':{},'source_chunk_id':'neighbor'}
        self.client.force_login(self.admin)

    def events(self):
        return list(self.job.events.values_list('kind','payload'))

    def test_incremental_text_is_saved_before_the_model_finishes_and_internal_metadata_is_ignored(self):
        def chunks(messages):
            yield AIMessageChunk(content='第一段', additional_kwargs={'reasoning_content':'private reasoning'})
            self.assertEqual(self.events(), [('token',{'text':'第一段','turn':1})])
            self.assertFalse(GraphRelation.objects.exists())
            yield AIMessageChunk(content='第二段', response_metadata={'finish_reason':'stop'})
        model = Mock(); model.stream.side_effect = chunks
        response = call_model(model, [], GraphProgress(self.job.pk))
        self.assertEqual(response.content,'第一段第二段')
        self.assertEqual(response.response_metadata['finish_reason'],'stop')
        self.assertNotIn('private reasoning',str(self.events()))
        model.invoke.assert_not_called()
        self.assertEqual(visible_text([{'type':'reasoning','text':'hidden'},{'type':'text','text':'visible'}]),'visible')

    @patch('kb.provenance.annotate_results')
    @patch('kb.keyword_index.search')
    @patch('kb.keyword_index.get_chunks')
    def test_split_tool_arguments_are_reassembled_and_neighbor_reads_and_validation_are_visible(self,chunks,search,annotate):
        chunks.return_value = [{'chunk_id':'seed','text':'本节介绍检查要求。'}, {'chunk_id':'neighbor','text':self.doc.md_content}]
        search.return_value = [chunks.return_value[0]]
        model = Mock(); model.bind_tools.return_value = model
        model.stream.side_effect = [iter([
            AIMessageChunk(content='',tool_call_chunks=[{'name':'get_neighbor_chunks','args':'{"chunk_id":"seed",','id':'call1','index':0}]),
            AIMessageChunk(content='',tool_call_chunks=[{'name':None,'args':'"direction":"next"}','id':None,'index':0}]),
        ]), iter([AIMessageChunk(content=json.dumps({'relations':[self.row]}))])]
        rows, rejected = extract_focused(model,self.doc,'每日检查',GraphProgress(self.job.pk))
        self.assertEqual(len(rows),1); self.assertEqual(rejected,0)
        events = self.events(); kinds = [kind for kind,data in events]
        self.assertIn('tool_start',kinds); self.assertIn('tool_result',kinds)
        neighbor = next(data for kind,data in events if kind=='tool_result')
        self.assertEqual(neighbor['chunks'][0]['chunk_id'],'neighbor')
        validation = next(data for kind,data in events if kind=='validation')
        self.assertEqual(validation['accepted'],1)
        self.assertEqual(validation['rejected'],0)
        self.assertFalse(GraphRelation.objects.exists())

    @patch('kb.config.llm_settings',return_value={})
    @patch('kb.agent._get_llm')
    @patch('kb.provenance.annotate_results')
    @patch('kb.keyword_index.search')
    @patch('kb.keyword_index.get_chunks')
    def test_partial_model_failure_preserves_output_and_never_publishes_a_relation(self,chunks,search,annotate,llm,config):
        chunks.return_value = [{'chunk_id':'neighbor','text':self.doc.md_content}]
        search.return_value = chunks.return_value
        def broken(messages):
            yield AIMessageChunk(content='{"relations":[')
            raise RuntimeError('模型连接中断')
        model = Mock(); model.bind_tools.return_value = model; model.stream.side_effect = broken
        llm.return_value.bind.return_value = model
        run_extraction(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status,'failed')
        self.assertTrue(self.job.events.filter(kind='token').exists())
        self.assertEqual(self.events()[-1],('failed',{'message':'模型连接中断'}))
        self.assertFalse(GraphRelation.objects.exists())

    @patch('kb.config.llm_settings',return_value={})
    @patch('kb.agent._get_llm')
    @patch('kb.provenance.annotate_results')
    @patch('kb.keyword_index.search')
    @patch('kb.keyword_index.get_chunks')
    def test_completed_stream_saves_only_candidates_and_logs_rejected_quotes(self,chunks,search,annotate,llm,config):
        chunks.return_value = [{'chunk_id':'neighbor','text':self.doc.md_content}]; search.return_value=chunks.return_value
        invalid = {**self.row,'quote':'没有在原文中出现的关系'}
        payload = json.dumps({'relations':[self.row,invalid]},ensure_ascii=False)
        model=Mock(); model.bind_tools.return_value=model
        model.stream.side_effect=[iter([AIMessageChunk(content=payload[:20]),AIMessageChunk(content=payload[20:])]),
                                  iter([AIMessageChunk(content='{"relations":[],"has_more":false}')])]
        llm.return_value.bind.return_value=model
        run_extraction(self.job.pk); self.job.refresh_from_db()
        self.assertEqual(self.job.status,'completed')
        self.assertEqual(self.job.rejected,1)
        self.assertEqual(GraphRelation.objects.get().status,'candidate')
        self.assertEqual(self.events()[-1][0],'complete')

    @patch('kb.graph_progress.MAX_OUTPUT_CHARS',4)
    def test_display_limit_does_not_truncate_the_actual_model_response(self):
        model=Mock(); model.stream.return_value=iter([AIMessageChunk(content='abcdefgh')])
        response=call_model(model,[],GraphProgress(self.job.pk))
        self.assertEqual(response.content,'abcdefgh')
        self.assertEqual(next(data['text'] for kind,data in self.events() if kind=='token'),'abcd')
        self.assertIn('notice',[kind for kind,data in self.events()])

    def test_silent_async_model_is_cancelled_at_deadline_and_its_stream_is_closed(self):
        closed=[]
        class SilentModel(Runnable):
            def invoke(self, *_args, **_kwargs):
                raise AssertionError('必须使用可取消的异步流')
            async def astream(self, *_args, **_kwargs):
                try:
                    await asyncio.sleep(60)
                    yield AIMessageChunk(content='late')
                finally:
                    closed.append(True)
        with self.assertRaises(TimeoutError):
            call_model(SilentModel(), [], GraphProgress(self.job.pk), request_timeout=0.03)
        self.assertEqual(closed,[True])
        self.assertFalse(self.job.events.filter(kind='token').exists())

    def test_real_sdk_can_stream_two_successive_requests_on_the_bounded_worker(self):
        from kb.agent import _get_llm
        from langchain_core.messages import HumanMessage
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args):pass
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                payload={'id':'test','object':'chat.completion.chunk','created':1,'model':'test',
                         'choices':[{'index':0,'delta':{'role':'assistant','content':'{"relations":[]}'},'finish_reason':None}]}
                tail={**payload,'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]}
                body=('data: '+json.dumps(payload)+'\n\ndata: '+json.dumps(tail)+'\n\ndata: [DONE]\n\n').encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream')
                self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        with ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            model=_get_llm({'base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'test',
                            'api_key':'','temperature':0,'single_request_connections':True})
            def read_source(chunk_id: str) -> str:
                """Read one source chunk."""
                return chunk_id
            agent=model.bind_tools([read_source])
            try:
                for turn,request_model in ((1,agent),(2,agent.bind(tool_choice='none'))):
                    response=call_model(request_model,[HumanMessage(content='synthetic test')],GraphProgress(self.job.pk),
                                         turn,request_timeout=2)
                    self.assertEqual(response.content,'{"relations":[]}')
            finally:
                server.shutdown();thread.join(timeout=3)
        self.assertEqual(len(requests),2)
        self.assertTrue(all(request['stream'] for request in requests))
        self.assertEqual(requests[0]['tools'],requests[1]['tools'])
        self.assertEqual(requests[1]['tool_choice'],'none')

    def terminal(self):
        self.job.status='completed'; self.job.save(update_fields=['status'])
        self.first=GraphExtractionEvent.objects.create(extraction=self.job,kind='token',payload={'text':'first\nline','turn':1})
        self.last=GraphExtractionEvent.objects.create(extraction=self.job,kind='complete',payload={'message':'待确认'})

    def endpoint(self, **params):
        return self.client.get(reverse('kb:graph_events',args=[self.job.pk]),{'kb':self.folder.slug,**params})

    @patch('kb.agent._get_llm')
    def test_sse_history_replays_from_cursor_without_starting_another_model(self,llm):
        self.terminal()
        other=GraphExtraction.objects.create(document=self.doc)
        GraphExtractionEvent.objects.create(extraction=other,kind='token',payload={'text':'other task'})
        response=self.endpoint(after=self.first.pk)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Content-Type'],'text/event-stream')
        self.assertEqual(response['X-Accel-Buffering'],'no')
        text=async_to_sync(stream_text)(response)
        self.assertIn(f'id: {self.last.pk}\nevent: progress',text)
        self.assertNotIn('first\\nline',text)
        self.assertNotIn('other task',text)
        self.assertIn('event: done',text)
        llm.assert_not_called()
        response=self.client.get(reverse('kb:graph_events',args=[self.job.pk]),{'kb':self.folder.slug},HTTP_LAST_EVENT_ID=str(self.last.pk))
        self.assertNotIn('event: progress',async_to_sync(stream_text)(response))

    def test_event_access_requires_management_and_current_library_scope(self):
        self.terminal()
        self.client.force_login(self.reader)
        self.assertEqual(self.endpoint().status_code,404)
        self.client.force_login(self.admin)
        other=KnowledgeBase.objects.create(name='other',slug='live-other')
        self.assertEqual(self.client.get(reverse('kb:graph_events',args=[self.job.pk]),{'kb':other.slug}).status_code,404)
        self.assertEqual(self.endpoint(after='invalid').status_code,400)
        self.assertEqual(self.endpoint(after='9'*30).status_code,400)
        self.client.logout()
        self.assertEqual(self.endpoint().status_code,302)

    def test_stream_stops_after_management_permission_is_removed(self):
        response=self.endpoint()
        self.admin.is_staff=False; self.admin.save(update_fields=['is_staff'])
        text=async_to_sync(stream_text)(response)
        self.assertIn('event: unavailable',text)

    def test_live_panel_is_only_shown_to_managers_and_extraction_response_selects_the_job(self):
        page=self.client.get(reverse('kb:graph'),{'kb':self.folder.slug})
        self.assertContains(page,'id="graphLive"')
        self.assertContains(page,'graph_live.js')
        self.client.force_login(self.reader)
        self.assertNotContains(self.client.get(reverse('kb:graph'),{'kb':self.folder.slug}),'id="graphLive"')
        self.client.force_login(self.admin)
        with patch('kb.graph_extraction.schedule_document',return_value=False):
            response=self.client.post(reverse('kb:graph_action'),{'kb':self.folder.slug,'action':'extract','document':str(self.doc.pk),'focus':'每日检查'})
        self.assertEqual(response.json()['job_id'],self.job.pk)

    def test_retry_uses_the_failed_jobs_focus_and_keeps_existing_history(self):
        self.job.status = 'failed'; self.job.save(update_fields=['status'])
        def schedule(document, **kwargs):
            self.assertEqual(document.pk, self.doc.pk)
            self.assertEqual(kwargs['focus'], self.job.focus)
            GraphExtraction.objects.create(document=document, focus=kwargs['focus'], source_digest=digest(document.md_content))
            return True
        with patch('kb.graph_extraction.schedule_document', side_effect=schedule):
            response = self.client.post(reverse('kb:graph_action'), {'kb':self.folder.slug,
                                       'action':'retry_extract', 'extraction':self.job.pk, 'focus':'ignored'})
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()['job_id'], self.job.pk)
        self.job.refresh_from_db(); self.assertEqual(self.job.status, 'failed')

    def test_retry_rejects_active_jobs_changed_documents_and_non_managers(self):
        url=reverse('kb:graph_action')
        data={'kb':self.folder.slug, 'action':'retry_extract', 'extraction':self.job.pk}
        with patch('kb.graph_extraction.schedule_document') as schedule:
            self.assertEqual(self.client.post(url,data).status_code,400)
            self.job.status='failed';self.job.save(update_fields=['status'])
            self.doc.md_content+='changed';self.doc.save(update_fields=['md_content'])
            self.assertEqual(self.client.post(url,data).status_code,400)
            self.client.force_login(self.reader)
            self.assertEqual(self.client.post(url,data).status_code,404)
            schedule.assert_not_called()

    def test_retry_cannot_use_a_job_from_a_different_library_scope(self):
        other=KnowledgeBase.objects.create(name='Other',slug='retry-other')
        doc=Document.objects.create(kb=other,original_name='other.pdf',status='completed',md_content='其他资料原文')
        job=GraphExtraction.objects.create(document=doc,status='failed',focus='其他方面',source_digest=digest(doc.md_content))
        with patch('kb.graph_extraction.schedule_document') as schedule:
            response=self.client.post(reverse('kb:graph_action'),{'kb':self.folder.slug,'action':'retry_extract','extraction':job.pk})
        self.assertEqual(response.status_code,404)
        schedule.assert_not_called()
