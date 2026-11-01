from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from kb.models import Conversation,KnowledgeBase


class AssistantWidgetTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('assistant-widget',is_staff=True,is_superuser=True)
        self.client.force_login(self.user)

    def test_widget_available_once_on_business_and_admin_pages(self):
        for url in ['/',reverse('kb:search'),reverse('kb:graph'),reverse('kb:feeds'),reverse('kb:connector_builder'),
                    reverse('kb:manage_list'),reverse('kb:settings'),reverse('dashboard:index'),'/admin/']:
            with self.subTest(url=url):
                response=self.client.get(url)
                self.assertEqual(response.status_code,200)
                self.assertContains(response,'id="ragAssistant"',count=1)
                self.assertContains(response,'id="ragChatLauncher"',count=1)
                self.assertNotContains(response,'id="ragChatFrame"')

    def test_embedded_chat_keeps_original_features_without_nested_launcher_or_preload(self):
        response=self.client.get(reverse('kb:ask'),{'embed':'1'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.headers['X-Frame-Options'],'SAMEORIGIN')
        for identifier in ('messages','newChatBtn','convList','input','stopBtn','ragChatWelcomeTemplate'):
            self.assertContains(response,'id="'+identifier+'"')
        self.assertContains(response,'assistant_embedded.js');self.assertContains(response,'evidence_panel.js')
        self.assertNotContains(response,'id="ragAssistant"');self.assertNotContains(response,'requestIdleCallback(startPreload')
        self.assertContains(response,'window.FosAsk')

    def test_old_chat_links_open_widget_and_keep_valid_thread(self):
        response=self.client.get(reverse('kb:ask'),{'conv':'t_existing-thread'})
        self.assertContains(response,'data-auto-open="true"');self.assertContains(response,'data-thread="t_existing-thread"')
        self.assertNotContains(response,'id="messages"')
        malicious=self.client.get(reverse('kb:ask'),{'embed':'1','conv':'";</script><img onerror=alert(1)>'})
        self.assertEqual(malicious.status_code,200);self.assertNotContains(malicious,'<img onerror=')

    def test_login_required_and_existing_conversation_privacy_kept(self):
        kb=KnowledgeBase.objects.create(name='Private knowledge',slug='assistant-private',is_folder=True,created_by=self.user)
        Conversation.objects.create(user=self.user,kb=kb,thread_id='t_own',title='Own conversation')
        other=get_user_model().objects.create_user('assistant-other',is_staff=True)
        Conversation.objects.create(user=other,kb=kb,thread_id='t_other',title='Hidden conversation title')
        embedded=self.client.get(reverse('kb:ask'),{'embed':'1'})
        self.assertContains(embedded,'Own conversation');self.assertNotContains(embedded,'Hidden conversation title')
        self.assertEqual(self.client.get(reverse('kb:conv_messages',args=['t_other'])).status_code,404)
        self.client.logout();self.assertEqual(self.client.get(reverse('kb:ask'),{'embed':'1'}).status_code,302)
        self.assertNotContains(self.client.get('/'),'id="ragAssistant"')
