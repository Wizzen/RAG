from __future__ import annotations

import json
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock,patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase,TestCase
from django.urls import reverse
from langchain_core.messages import AIMessage,AIMessageChunk,ToolMessage

from kb import connector_sessions as sessions
from kb.connector_agent import run
from kb.connector_runtime import build_batch,validate_spec
from kb.connector_tools import check_spec,sample_preview
from kb.feed_protocol import validate_batch
from kb.models import ConnectorDraft,KnowledgeBase,KnowledgeFeed
from kb.tests.test_connector import FakeModel,call,spec,sample
from kb.tests.test_connector_browser import web_spec


class FallbackTests(SimpleTestCase):
    def test_dates_are_generated_without_any_website_date_field(self):
        config=web_spec('https://website.example.test/#/spot/list?type=7')
        config['body_fields']=[{'label':'日期','source':'today'},{'label':'采集时间','source':'now'}]
        data={'records':[{'id':'ride','name':'Ride','source_url':config['url']}],
              '__fos_collected_at':'2026-10-31T23:30:00+00:00'}
        fields=validate_batch(build_batch(config,data))['items'][0]['fields']
        self.assertEqual(fields['日期'],'2026-11-01');self.assertEqual(fields['采集时间'],'2026-11-01T07:30:00+08:00')
        self.assertEqual(check_spec(SimpleNamespace(api_url=config['url'],prompt=config['url'],header_env={}),config),config)

    def test_changed_format_preserves_raw_content_and_does_not_invent_duration(self):
        config=web_spec();config.pop('on_error')
        config['body_fields']=[{'label':'开启','path':'start'},{'label':'关闭','path':'end'},
            {'label':'时长','computed':{'op':'duration','start':'start','end':'end','format':'hhmm','unit':'hours'}}]
        data={'records':[{'id':'ride','name':'Ride','start':None,'end':'晚间停止开放',
            '__fos_capture':{'warnings':['版式变化'],'raw':{'start':'今日开放时间待定'},'text':'今日开放时间待定'}}]}
        item=validate_batch(build_batch(config,data))['items'][0]
        self.assertEqual(item['fields']['开启'],'今日开放时间待定')
        self.assertIn('晚间停止开放',item['fields']['时长']);self.assertNotEqual(item['fields']['时长'],0)
        self.assertIn('采集提示',item['fields']);self.assertIn('原始内容',item['fields'])
        self.assertEqual(item['source_url'],config['url'])
        job=SimpleNamespace(api_url=config['url'],prompt=config['url'],header_env={})
        self.assertTrue(sample_preview(job,config,data)['valid']);self.assertIn('保留原文',sample_preview(job,config,data)['warning'])
        config['on_error']='fail'
        with self.assertRaises(ValueError):build_batch(config,data)

    def test_changed_optional_timestamp_and_table_columns_keep_evidence(self):
        config=spec(on_error='raw');data=sample()
        data['data'][0]['updated']='格式变更';data['data'][0]['alarms']=[{'new_description':'实际警报'}]
        item=validate_batch(build_batch(config,data))['items'][0]
        self.assertIn('实际警报',item['fields']['原始内容']);self.assertIn('更新时间',item['metadata']['collection_warning'])
        self.assertIsNotNone(datetime.fromisoformat(item['updated_at']).tzinfo)
        del data['data'][0]['device']
        with self.assertRaises(ValueError):build_batch(config,data)


class SessionStoreTests(SimpleTestCase):
    def test_ownership_expiry_touch_and_incomplete_parallel_tools(self):
        browser=Mock();key='store-continuity'
        with patch('kb.connector_sessions.time.monotonic',return_value=100):sessions.put(key,1,{'browser':browser})
        self.assertIsNone(sessions.take(key,2))
        with patch('kb.connector_sessions.time.monotonic',return_value=1800):sessions.touch(key,1)
        with patch('kb.connector_sessions.time.monotonic',return_value=2000):state=sessions.take(key,1)
        self.assertIs(state['browser'],browser);browser.close.assert_not_called()
        with patch('kb.connector_sessions.time.monotonic',return_value=0):sessions.put(key,1,state)
        with patch('kb.connector_sessions.time.monotonic',return_value=2000):self.assertIsNone(sessions.take(key,1))
        browser.close.assert_called_once()
        messages=[AIMessage(content='',tool_calls=[{'name':'one','args':{},'id':'a'},{'name':'two','args':{},'id':'b'}]),ToolMessage(content='data',tool_call_id='a')]
        completed=sessions.complete_tool_groups(messages)
        self.assertEqual([m.tool_call_id for m in completed if isinstance(m,ToolMessage)],['a','b'])
        self.assertIn('未执行',completed[-1].content)

    def test_resume_waits_for_worker_handoff_and_forget_prevents_resurrection(self):
        key='pending-continuity';sessions.begin(key)
        def finish():sessions.put(key,1,{'sample':'actual'});sessions.end(key)
        timer=threading.Timer(.05,finish);timer.start()
        self.assertEqual(sessions.take(key,1)['sample'],'actual');timer.join()
        key='forgotten-continuity';browser=Mock();closed=threading.Event();browser.close.side_effect=closed.set
        sessions.begin(key);sessions.discard([key],1);sessions.put(key,1,{'browser':browser});sessions.end(key)
        self.assertTrue(closed.wait(2));self.assertIsNone(sessions.take(key,1))


class DialogueContinuityTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('continuity-admin',is_staff=True);self.client.force_login(self.user)
        self.jobs=[];self.browser=Mock();self.browser.page.url=web_spec()['url'];self.browser.last_observation={}
        self.browser.call.return_value={'url':web_spec()['url'],'text':'已发现项目专属事实 observed-detail-42','html':'<h1>事实</h1>','elements':[]}
        self.addCleanup(lambda:sessions.discard(self.jobs,self.user.pk))

    def job(self,**values):
        job=ConnectorDraft.objects.create(user=self.user,prompt='打开 '+web_spec()['url']+' 采集运营时间',api_url=web_spec()['url'],**values)
        self.jobs.append(job.pk);return job

    def execute(self,job,responses):
        model=FakeModel(responses)
        with patch('kb.agent._get_llm',return_value=model),patch('kb.connector_browser.session',return_value=self.browser):result=run(job.pk)
        return result,model

    def test_answering_question_keeps_full_tool_messages_and_same_browser(self):
        parent=self.job()
        parent,_=self.execute(parent,[call('browser_open',{'url':web_spec()['url']}),AIMessageChunk(content='{"questions":["单位用小时吗？"]}')])
        self.assertEqual(parent.status,'needs_info');self.browser.close.assert_not_called()
        child=self.job(parent=parent,feedback='对，小时')
        child,model=self.execute(child,[call('browser_snapshot'),AIMessageChunk(content='{"questions":["是否保留关闭项目？"]}')])
        self.assertEqual(child.status,'needs_info',child.error);self.assertTrue(child.metrics['context_resumed'])
        self.assertIn('observed-detail-42',str(model.messages[0]));self.assertIn('单位用小时吗',str(model.messages[0]))
        self.assertEqual(self.browser.call.call_args_list[-1].args[0],'snapshot');self.browser.close.assert_not_called()

    def test_after_confirm_body_only_edit_reuses_validated_capture_and_same_feed(self):
        parent=self.job();data={'records':[{'id':'ride','name':'Ride','start':'09:00','end':'21:00','source_url':web_spec()['url']}]}
        with patch('kb.connector_browser.collect',return_value=data):
            parent,_=self.execute(parent,[call('browser_open',{'url':web_spec()['url']}),call('test_browser_connector',{'spec':web_spec()},'preview')])
        self.assertEqual(parent.status,'ready',parent.error)
        with patch('kb.connector_views.start_feed'):
            enabled=self.client.post(reverse('kb:connector_enable',args=[parent.pk]),'{}',content_type='application/json')
        self.assertEqual(enabled.status_code,200,enabled.content);feed_id=enabled.json()['feed_id']
        candidate=web_spec();candidate['body_fields'].append({'label':'duration','computed':{'op':'duration','start':'start','end':'end','format':'hhmm','unit':'hours'}})
        with patch('kb.connector_views.start_async'):
            reply=self.client.post(reverse('kb:connector_refine',args=[parent.pk]),json.dumps({'feedback':'增加duration字段，单位小时'}),content_type='application/json')
        child=ConnectorDraft.objects.get(pk=reply.json()['job']['id']);self.jobs.append(child.pk)
        with patch('kb.connector_browser.collect') as recollect:
            child,model=self.execute(child,[call('show_data_preview',{'spec':candidate})])
        self.assertEqual(child.status,'ready',child.error);recollect.assert_not_called()
        self.assertTrue(child.metrics['context_resumed']);self.assertEqual(child.preview['preview'][0]['fields']['duration'],12)
        self.assertIn('observed-detail-42',str(model.messages[0]));self.browser.close.assert_not_called()
        KnowledgeFeed.objects.filter(pk=feed_id).update(lease_token='',lease_until=None)
        with patch('kb.connector_views.start_feed'):
            result=self.client.post(reverse('kb:connector_enable',args=[child.pk]),'{}',content_type='application/json')
        self.assertEqual(result.status_code,200,result.content);self.assertEqual(result.json()['feed_id'],feed_id)
        self.assertEqual(KnowledgeFeed.objects.count(),1);self.assertEqual(KnowledgeBase.objects.count(),1)
