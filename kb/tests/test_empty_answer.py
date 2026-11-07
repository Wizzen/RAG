import asyncio
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.urls import reverse

from kb.models import KnowledgeBase, Message


class EmptyAnswerDiagnosticsTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('empty-answer-reader')
        self.kb = KnowledgeBase.objects.create(name='Diagnostic manual',slug='diagnostic-manual')
        self.client.force_login(self.user)
        self.async_client.cookies = self.client.cookies

    def run_events(self, events, thread):
        async def source(*args, **kwargs):
            for item in events:
                yield item
        async def consume():
            response = await self.async_client.post(reverse('kb:stream'), {
                'message':'compare the acceptance levels','thread_id':thread,'kb_slug':self.kb.slug})
            return b''.join([chunk async for chunk in response.streaming_content]).decode()
        with patch('kb.agent.run_agent_stream',side_effect=source), patch('kb.config.llm_settings',return_value={'qa_enhance':False}):
            wire = asyncio.run(consume())
        return wire, Message.objects.get(conversation__thread_id=thread,role='ai')

    def test_dynamic_verification_rejection_reports_the_real_reason(self):
        wire, answer = self.run_events([('verify',{'ok':False,'issues':[]})],'rejected-graph-draft')
        self.assertIn('来源核对',answer.failure_reason)
        self.assertNotIn('没有返回答案正文',answer.failure_reason)
        self.assertEqual(answer.completion_status,'incomplete')
        self.assertIn('event: error',wire)

    def test_rejected_verification_cannot_publish_a_tool_preface_as_a_complete_answer(self):
        wire, answer = self.run_events([('token',{'text':'I will compare the sources.'}),
            ('verify',{'ok':False,'issues':[]})],'rejected-preface')
        self.assertEqual(answer.completion_status,'incomplete')
        self.assertIn('来源核对',answer.failure_reason)

    def test_actual_empty_response_remains_distinct_from_verification_failure(self):
        wire, answer = self.run_events([],'actual-empty-answer')
        self.assertEqual(answer.completion_status,'incomplete')
        self.assertIn('没有返回答案正文',answer.failure_reason)
