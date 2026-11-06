from __future__ import annotations

import base64
import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from langchain_core.messages import AIMessageChunk

from kb.connector_agent import run
from kb.feed_credentials import bindings, parse_auth, request_headers, secret_values, store
from kb.feed_http import fetch_batch
from kb.models import ConnectorDraft, FeedCredential, FeedRun, KnowledgeBase, KnowledgeFeed
from kb.tests.test_connector import FakeModel, answer, call, sample, spec

TOKEN='synthetic-user-secret-ABC123'


class CredentialTests(TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        settings=self.settings(DATA_DIR=Path(self.directory.name));settings.enable();self.addCleanup(settings.disable)
        self.user=get_user_model().objects.create_user('credential-admin',is_staff=True)
        self.client.force_login(self.user)
        start=patch('kb.collector_api.start_async');self.start=start.start();self.addCleanup(start.stop)
        publish=patch('kb.collector_api.start_feed');self.publish=publish.start();self.addCleanup(publish.stop)

    def post(self,name,data,job=None):
        return self.client.post(reverse('kb:'+name,args=[job.pk] if job else []),json.dumps(data),content_type='application/json',HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def draft(self,**extra):
        response=self.post('connector_create',{'prompt':'从设备接口采集温度和报警，按设备与日期更新','api_url':spec()['url'],
            'authentication':{'type':'bearer','token':TOKEN},**extra})
        self.assertEqual(response.status_code,202,response.content)
        return ConnectorDraft.objects.get(pk=response.json()['job']['id']),response

    def make_ready(self,job):
        job.spec=spec(header_env=job.header_env);job.preview={'valid':True,'items':1,'preview':[]};job.status='ready'
        job.save(update_fields=['spec','preview','status'])

    def test_all_form_formats_and_forbidden_headers(self):
        self.assertEqual(parse_auth({'type':'bearer','token':'Bearer '+TOKEN})[1],{'Authorization':'Bearer '+TOKEN})
        self.assertEqual(parse_auth({'type':'api_key','header_name':'api_key','value':TOKEN})[1],{'api_key':TOKEN})
        self.assertEqual(parse_auth({'type':'basic','username':'device-user','password':TOKEN})[1],
                         {'Authorization':'Basic '+base64.b64encode(('device-user:'+TOKEN).encode()).decode()})
        self.assertEqual(len(parse_auth({'type':'headers','headers':[{'name':'X-App-Id','value':'app-7'},{'name':'X-App-Key','value':TOKEN}]})[1]),2)
        for auth in [{'type':'bearer','token':'bad\nvalue'},{'type':'api_key','header_name':'Host','value':TOKEN},
            {'type':'basic','username':'bad:user','password':TOKEN},{'type':'headers','headers':[{'name':'X-Key','value':TOKEN},{'name':'x-key','value':TOKEN}]}]:
            with self.assertRaises(ValueError):parse_auth(auth)

    def test_auto_encrypted_storage_and_bound_origin_across_key_reloads(self):
        job,response=self.draft()
        credential=job.credential
        self.assertNotIn(TOKEN,credential.encrypted_value);self.assertNotIn(TOKEN,response.content.decode())
        self.assertTrue((Path(self.directory.name)/'secrets/feed-credentials.key').is_file())
        self.assertEqual(request_headers(job,spec()['url'])['Authorization'],'Bearer '+TOKEN)
        self.assertEqual(request_headers(job,spec()['url']+'?page=2')['Authorization'],'Bearer '+TOKEN)
        with self.assertRaises(ValueError):request_headers(job,'https://different.example.test/reports')
        self.assertIn(TOKEN,secret_values(job))
        credential.encrypted_value=credential.encrypted_value[:-10]+'broken'
        credential.save(update_fields=['encrypted_value']);job.refresh_from_db()
        with self.assertRaises(ValueError):request_headers(job,spec()['url'])

    def test_form_values_are_redacted_from_prompt_sample_status_and_model(self):
        job,response=self.draft(prompt='获取温度日报，意外附带 '+TOKEN,sample={'secret':TOKEN,**sample()})
        self.assertNotIn(TOKEN,job.prompt);self.assertNotIn(TOKEN,json.dumps(job.sample))
        data=sample();data['data'][0]['echo']=TOKEN
        config=spec(header_env=job.header_env)
        job.sample=None;job.save(update_fields=['sample'])
        model=FakeModel([call('probe_api',{'url':job.api_url}),call('show_data_preview',{'spec':config},'preview'),answer(config)])
        with patch('kb.agent._get_llm',return_value=model),patch('kb.connector_agent.read_url',return_value=(200,{'content-type':'application/json'},json.dumps(data).encode())) as read:
            job=run(job.pk)
        self.assertEqual(job.status,'ready',job.error);self.assertEqual(read.call_args.kwargs['headers']['Authorization'],'Bearer '+TOKEN)
        self.assertNotIn(TOKEN,str(model.messages));self.assertNotIn(TOKEN,json.dumps(job.events));self.assertNotIn(TOKEN,json.dumps(job.spec))
        status=self.client.get(reverse('kb:connector_status',args=[job.pk]));self.assertNotIn(TOKEN,status.content.decode())
        self.assertEqual(status.json()['job']['limits']['model_calls'],50)
        archive=self.client.get(reverse('kb:connector_download',args=[job.pk]));self.assertEqual(archive.status_code,200)
        with zipfile.ZipFile(io.BytesIO(b''.join(archive.streaming_content))) as package:
            self.assertFalse(any(TOKEN.encode() in package.read(name) for name in package.namelist()))

    def test_department_manager_can_enter_own_credentials_without_server_env_access(self):
        from accounts.models import UserProfile
        user=get_user_model().objects.create_user('credential-department')
        UserProfile.objects.update_or_create(user=user,defaults={'department':'机械','role':UserProfile.Role.DEPT_ADMIN})
        self.client.force_login(user);job,_=self.draft();self.assertEqual(job.credential.owner,user)
        self.make_ready(job)
        response=self.post('connector_enable',{},job);self.assertEqual(response.status_code,200,response.content)
        feed=KnowledgeFeed.objects.get();self.assertEqual(feed.kb.department,'机械');self.assertEqual(feed.credential_id,job.credential_id)
        self.assertEqual(self.publish.call_count,1)
        self.assertEqual(self.post('connector_enable',{},job).status_code,200);self.assertEqual(self.publish.call_count,1)
        self.assertEqual(FeedRun.objects.count(),1)

    def test_test_auth_uses_secret_without_model_call_and_never_echoes_body(self):
        with patch('kb.feed_http.read_url',return_value=(200,{'content-type':'application/json'},b'never echo this body')) as read:
            response=self.post('connector_test_auth',{'api_url':spec()['url'],'authentication':{'type':'bearer','token':TOKEN}})
        self.assertTrue(response.json()['valid']);self.assertNotIn(TOKEN,response.content.decode());self.assertNotIn('echo',response.content.decode())
        self.assertEqual(read.call_args.kwargs['headers']['Authorization'],'Bearer '+TOKEN);self.start.assert_not_called()
        with patch('kb.feed_http.read_url',return_value=(401,{},TOKEN.encode())):
            response=self.post('connector_test_auth',{'api_url':spec()['url'],'authentication':{'type':'bearer','token':TOKEN}})
        self.assertFalse(response.json()['valid']);self.assertEqual(response.json()['http_status'],401);self.assertNotIn(TOKEN,response.content.decode())

    def test_rotate_in_frontend_updates_scheduled_feed_immediately(self):
        job,_=self.draft();self.make_ready(job);self.post('connector_enable',{},job);job.refresh_from_db()
        replacement='new-synthetic-credential-456'
        response=self.post('connector_credentials',{'api_url':job.api_url,'authentication':{'type':'api_key','header_name':'X-API-Key','value':replacement}},job)
        self.assertEqual(response.status_code,200,response.content);job.refresh_from_db();feed=KnowledgeFeed.objects.get()
        self.assertEqual(feed.credential_id,job.credential_id)
        with patch('kb.feed_http.read_url',return_value=(200,{'content-type':'application/json'},json.dumps(sample()).encode())) as read:
            payload,_=fetch_batch(feed)
        self.assertEqual(read.call_args.kwargs['headers'],{'X-API-Key':replacement});self.assertEqual(payload['items'][0]['title'],'A1日报')
        self.assertNotIn(replacement,response.content.decode());self.assertEqual(FeedCredential.objects.count(),1)

    def test_other_user_cannot_reuse_modify_or_probe_saved_credentials(self):
        job,_=self.draft();other=get_user_model().objects.create_user('other-credential-admin',is_staff=True);self.client.force_login(other)
        for name,data,args in [('connector_credentials',{'api_url':job.api_url,'authentication':{'type':'none'}},job),
            ('connector_test_auth',{'api_url':job.api_url,'authentication':{'type':'saved'},'credential_job':str(job.pk)},None),
            ('connector_create',{'prompt':'获取设备日报接口的温度信息','api_url':job.api_url,'authentication':{'type':'saved'},'credential_job':str(job.pk)},None)]:
            self.assertEqual(self.post(name,data,args).status_code,404)

    def test_refine_keeps_credentials_and_requires_new_approval_before_first_import(self):
        job,_=self.draft();self.make_ready(job)
        response=self.post('connector_refine',{'feedback':'只保留温度，并用设备名称作为标题'},job)
        self.assertEqual(response.status_code,202,response.content)
        revision=ConnectorDraft.objects.get(pk=response.json()['job']['id']);self.assertEqual(revision.credential_id,job.credential_id)
        self.assertEqual(revision.parent_id,job.pk);self.assertIn('只保留温度',revision.feedback)
        self.assertEqual(KnowledgeFeed.objects.count(),0);self.publish.assert_not_called()
        self.assertEqual(self.post('connector_enable',{},job).status_code,400)
        config=spec(header_env=revision.header_env,body_fields=[{'label':'温度','path':'temperature','unit':'℃'}])
        model=FakeModel([call('probe_api',{'url':job.api_url}),call('show_data_preview',{'spec':config},'p'),answer(config)])
        with patch('kb.agent._get_llm',return_value=model),patch('kb.connector_agent.read_url',return_value=(200,{'content-type':'application/json'},json.dumps(sample()).encode())):
            revision=run(revision.pk)
        self.assertEqual(revision.status,'ready',revision.error);self.assertIn('只保留温度',str(model.messages));self.assertNotIn('报警',revision.preview['preview'][0]['content'])
        self.assertEqual(KnowledgeFeed.objects.count(),0)
        self.assertEqual(self.post('connector_enable',{},revision).status_code,200);self.assertEqual(KnowledgeFeed.objects.count(),1);self.publish.assert_called_once()

    def test_cancel_fences_late_worker_publication(self):
        job,_=self.draft()
        self.assertEqual(self.post('connector_cancel',{},job).json()['job']['status'],'cancelled')
        with patch('kb.agent._get_llm') as model: result=run(job.pk)
        self.assertEqual(result.status,'cancelled');model.assert_not_called();self.assertEqual(KnowledgeFeed.objects.count(),0)

    def test_user_can_answer_missing_api_url_in_plain_language_feedback(self):
        previous=ConnectorDraft.objects.create(user=self.user,prompt='请采集设备温度日报',status='needs_info',questions=['请提供实际接口地址'])
        response=self.post('connector_refine',{'feedback':'实际接口是 https://api.example.test/reports，请继续获取日报。'},previous)
        self.assertEqual(response.status_code,202,response.content);job=ConnectorDraft.objects.get(pk=response.json()['job']['id'])
        model=FakeModel([call('probe_api',{'url':spec()['url']}),answer()])
        with patch('kb.agent._get_llm',return_value=model),patch('kb.connector_agent.read_url',return_value=(200,{'content-type':'application/json'},json.dumps(sample()).encode())) as read:
            job=run(job.pk)
        self.assertEqual(job.status,'ready',job.error);read.assert_called_once();self.assertEqual(KnowledgeFeed.objects.count(),0)

    def test_manual_feed_credential_form_can_save_reuse_rotate_and_clear(self):
        payload={'action':'create','name':'Manual source','kb_name':'Reports','mode':'pull','url':spec()['url'],
            'authentication':{'type':'bearer','token':TOKEN}}
        response=self.post('feed_action',payload);self.assertEqual(response.status_code,200,response.content)
        feed=KnowledgeFeed.objects.get();self.assertTrue(feed.credential_id)
        payload.update(action='update',id=str(feed.pk),authentication={'type':'saved'})
        self.assertEqual(self.post('feed_action',payload).status_code,200)
        payload['authentication']={'type':'none'};self.assertEqual(self.post('feed_action',payload).status_code,200)
        feed.refresh_from_db();self.assertIsNone(feed.credential_id);self.assertEqual(feed.header_env,{})
