from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from kb.agent import _get_llm
from kb.config import embedding_settings, normalize_openai_base_url
from kb.models import SiteConfig
from kb.pipeline import _embeddings


class LocalAIConfigurationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="local-ai-admin", password=None, is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_ollama_url_gets_scheme_and_openai_prefix(self):
        self.assertEqual(
            normalize_openai_base_url("127.0.0.1:11434", ollama=True),
            "http://127.0.0.1:11434/v1",
        )

    # .env 里有真实云端 key（回退链第二层），置空后才能测「全空 → 占位符」逻辑
    @override_settings(EMBEDDING_API_KEY="")
    def test_empty_local_keys_can_construct_runtime_clients(self):
        llm = _get_llm({
            "model": "local-chat", "api_key": "",
            "base_url": "http://localhost:1234/v1", "temperature": 0.2,
        })
        self.assertEqual(llm.openai_api_key.get_secret_value(), "local-no-key")

        cfg = SiteConfig.get()
        cfg.embedding_base_url = "127.0.0.1:11434"
        cfg.embedding_model = "bge-m3:latest"
        cfg.embedding_api_key = ""
        cfg.save()
        self.assertEqual(embedding_settings()["base_url"], "http://127.0.0.1:11434/v1")
        embeddings = _embeddings()
        self.assertEqual(embeddings.openai_api_key.get_secret_value(), "local-no-key")

    def test_settings_page_has_embedded_field_definitions(self):
        """设置页预设化后字段由 JS CFG_FIELDS 定义（无服务端渲染表单）。"""
        response = self.client.get(reverse("kb:settings"))
        self.assertContains(response, "embedding_base_url")
        self.assertContains(response, "cfgDialog")

    def test_chat_uses_automatically_selected_kb_slug(self):
        """The stream must not receive an empty default scope."""
        from unittest.mock import AsyncMock
        from kb.models import KnowledgeBase

        kb = KnowledgeBase.objects.create(
            name="Indexed manual", slug="indexed-manual", is_folder=False,
            chunk_count=3, created_by=self.user,
        )

        async def no_events(*args, **kwargs):
            if False:
                yield None

        with patch("kb.agent.run_agent_stream", side_effect=no_events) as stream:
            response = self.client.post(reverse("kb:stream"), {
                "message": "test", "thread_id": "auto-kb-thread",
            })
            # Consume StreamingHttpResponse so the async generator executes.
            async def consume():
                return [chunk async for chunk in response.streaming_content]
            import asyncio
            asyncio.run(consume())

        self.assertEqual(stream.call_args.args[2], kb.slug)

    # 预设化后「测试」按钮只传 target，端点按【已保存配置】实际调用
    # （UI 上明确标注「使用已保存配置」）。
    @override_settings(EMBEDDING_API_KEY="")
    @patch("httpx.post")
    def test_connection_test_uses_saved_config(self, post):
        cfg = SiteConfig.get()
        cfg.embedding_base_url = "127.0.0.1:11434"
        cfg.embedding_model = "bge-m3:latest"
        cfg.embedding_api_key = ""
        cfg.save()

        post.return_value.status_code = 200
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {"data": [{"embedding": [0.1, 0.2]}]}

        response = self.client.post(reverse("kb:settings_test"), {"target": "embedding"})

        self.assertTrue(response.json()["results"]["embedding"]["ok"])
        called_url = post.call_args.args[0]
        self.assertEqual(called_url, "http://127.0.0.1:11434/v1/embeddings")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer local-no-key")
