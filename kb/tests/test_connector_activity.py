from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase,TestCase
from django.urls import reverse
from langchain_core.messages import AIMessage,AIMessageChunk,HumanMessage,SystemMessage,ToolMessage

from kb.connector_agent import Progress,run
from kb.connector_context import WorkingMemory,compact_context,message_size,usage_stats,normalize_arguments
from kb.models import ConnectorDraft,ConnectorEvent,KnowledgeFeed
from kb.tests.test_connector import FakeModel,call,sample,spec


class ContextTests(SimpleTestCase):
    def test_append_only_prefix_and_batched_compaction_keep_actual_knowledge(self):
        memory=WorkingMemory();memory.begin_turn()
        memory.observe('browser_snapshot',{}, {'url':'https://example.test','revision':'one','text':'Real field',
            'elements':[{'ref':'e1','selector':'.real','text':'Click'}]})
        memory.observe('browser_extract',{'rows':'.invented'},{'error':'not found'})
        memory.end_turn();self.assertEqual(memory.stalled_turns,0)
        messages=[SystemMessage(content='stable'),HumanMessage(content='requirement')]
        self.assertIs(compact_context(messages,memory)[0],messages)
        for index in range(8):
            messages.extend([AIMessage(content='',tool_calls=[{'name':'tool','args':{},'id':str(index)}]),ToolMessage(content='x'*10000,tool_call_id=str(index))])
        compacted,changed=compact_context(messages,memory)
        self.assertTrue(changed);self.assertEqual(compacted[:2],messages[:2]);self.assertIn('.real',compacted[2].content);self.assertIn('.invented',compacted[2].content)
        self.assertLess(sum(message_size(message) for message in compacted),35000)
        self.assertIs(compact_context(compacted,memory)[0],compacted)

    def test_real_cached_usage_distinguished_from_missing_and_images_not_counted_as_base64(self):
        missing=usage_stats(AIMessage(content='done',usage_metadata={'input_tokens':100,'output_tokens':10,'total_tokens':110}))
        self.assertFalse(missing['cache_reported'])
        result=usage_stats(AIMessage(content='done',response_metadata={'token_usage':{'prompt_tokens':100,'completion_tokens':10,'prompt_tokens_details':{'cached_tokens':80}}}))
        self.assertEqual(result['cached_tokens'],80);self.assertTrue(result['cache_reported'])
        self.assertLess(message_size(HumanMessage(content=[{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+'A'*100000}}])),2000)

    def test_only_new_data_resets_no_progress_counter(self):
        memory=WorkingMemory()
        for _ in range(7):
            memory.begin_turn();memory.observe('browser_extract',{}, {'records':[{'name':'Same'}]});memory.end_turn()
        self.assertEqual(memory.stalled_turns,6)

    def test_double_encoded_local_model_object_arguments_are_normalized(self):
        self.assertEqual(normalize_arguments({'spec':'{"url":"https://example.test"}'}),{'spec':{'url':'https://example.test'}})
        self.assertEqual(normalize_arguments({'spec':'{bad json'}),{'spec':'{bad json'})


class ActivityTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('activity-admin',is_staff=True)
        self.job=ConnectorDraft.objects.create(user=self.user,prompt='采集 https://api.example.test/reports 温度报告',sample=sample())
        self.client.force_login(self.user)

    def test_successful_preview_finishes_without_extra_model_turn_or_publication(self):
        model=FakeModel([AIMessageChunk(content='',tool_calls=[
            {'name':'show_data_preview','args':{'spec':spec()},'id':'call-1'},
            {'name':'inspect_connector_environment','args':{},'id':'unnecessary'}])])
        with patch('kb.agent._get_llm',return_value=model):result=run(self.job.pk)
        self.assertEqual(result.status,'ready',result.error);self.assertEqual(result.metrics['calls'],1);self.assertEqual(result.metrics['tools'],1)
        self.assertEqual(KnowledgeFeed.objects.count(),0)
        events=list(result.activity.values_list('payload',flat=True))
        tool=next(event for event in events if event['kind']=='tool_end')
        self.assertEqual(tool['records'],1);self.assertIn('seconds',tool);self.assertEqual(tool['call_id'],'call-1')

    def test_model_deadline_has_clear_failure_message_instead_of_empty_error(self):
        with patch('kb.agent._get_llm',return_value=FakeModel([])),patch('kb.graph_progress.call_model',side_effect=TimeoutError()):result=run(self.job.pk)
        self.assertEqual(result.status,'failed');self.assertIn('120秒',result.error)

    def test_activity_retained_incrementally_and_owned_by_user(self):
        progress=Progress(self.job)
        for index in range(205):progress.emit('notice',message=str(index))
        self.job.refresh_from_db();self.assertEqual(len(self.job.events),180);self.assertEqual(self.job.activity.count(),205)
        url=reverse('kb:connector_status',args=[self.job.pk])
        recent=self.client.get(url).json()['job'];self.assertEqual(len(recent['events']),120);self.assertTrue(recent['has_older_events'])
        old=self.client.get(url,{'before':recent['older_event_cursor']}).json()['job'];self.assertEqual(len(old['events']),85)
        empty=self.client.get(url,{'after':recent['event_cursor']}).json()['job'];self.assertEqual(empty['events'],[])
        progress.emit('notice',message='new')
        new=self.client.get(url,{'after':recent['event_cursor']}).json()['job'];self.assertEqual([row['message'] for row in new['events']],['new'])
        self.client.force_login(get_user_model().objects.create_user('other-activity-admin',is_staff=True))
        self.assertEqual(self.client.get(url).status_code,404)
