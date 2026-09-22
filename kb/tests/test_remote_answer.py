from unittest.mock import patch, Mock
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from kb.config import llm_settings, validate_answer_endpoint, answer_extra_body
from kb.models import SiteConfig, PRESET_CATEGORIES
from fos_rag.offline import configure_answer_endpoint, check_connection


class RemoteAnswerTests(TestCase):
    def tearDown(self):
        configure_answer_endpoint("", False)

    def config(self, enabled=True):
        c = SiteConfig.get()
        c.llm_base_url = "https://api.example.com/v1"
        c.llm_model = "remote-model"
        c.llm_api_key = "test-key"
        c.llm_remote_enabled = enabled
        c.save()
        return llm_settings()

    def test_opt_in_and_revocation_are_scoped(self):
        self.config()
        check_connection(("api.example.com", 443))
        for address in [("api.example.com", 80), ("other.example.com", 443)]:
            with self.assertRaises(PermissionError):
                check_connection(address)
        cfg = self.config(False)
        with self.assertRaises(ValueError):
            validate_answer_endpoint(cfg)
        with self.assertRaises(PermissionError):
            check_connection(("api.example.com", 443))

    def test_legacy_preset_does_not_inherit_remote_permission(self):
        self.config()
        c = SiteConfig.get()
        c.apply({"llm_model": "old"}, PRESET_CATEGORIES["llm"])
        self.assertFalse(c.llm_remote_enabled)

    @override_settings(LLM_EXTRA_BODY={"enable_thinking": False})
    def test_runtime_uses_portable_remote_parameters(self):
        from kb.agent import _get_llm
        cfg = self.config()
        with patch("kb.agent.ChatOpenAI") as constructor:
            _get_llm(cfg)
        kwargs = constructor.call_args.kwargs
        self.assertEqual(kwargs["extra_body"], {})
        self.assertFalse(kwargs["stream_usage"])
        self.assertEqual(kwargs["base_url"], cfg["base_url"])
        kwargs["http_client"].close()
        self.assertEqual(answer_extra_body({}), {"enable_thinking": False})

    @override_settings(LLM_EXTRA_BODY={"enable_thinking": False})
    def test_connection_test_calls_saved_remote_model(self):
        self.config()
        user = get_user_model().objects.create_user("remote-admin", is_staff=True)
        self.client.force_login(user)
        response = Mock(status_code=200)
        response.json.return_value = {"model": "remote-model", "choices": [{"finish_reason": "stop", "message": {"content": "OK"}}]}
        with patch("httpx.post", return_value=response) as post:
            result = self.client.post(reverse("kb:settings_test"), {"target": "llm"})
        self.assertTrue(result.json()["results"]["llm"]["ok"])
        self.assertEqual(post.call_args.args[0], "https://api.example.com/v1/chat/completions")
        self.assertNotIn("enable_thinking", post.call_args.kwargs["json"])
        self.assertFalse(post.call_args.kwargs["trust_env"])
        page = self.client.get(reverse("kb:settings"))
        self.assertContains(page, "允许远程回答 API")

    def test_rejects_credentials_query_and_unsupported_protocol(self):
        for url in ["https://key@api.example.com/v1", "ftp://api.example.com/v1", "https://api.example.com/v1?key=secret"]:
            with self.assertRaises(ValueError):
                validate_answer_endpoint({"base_url": url, "remote_enabled": True})

    def test_http_and_https_are_both_allowed_with_explicit_opt_in(self):
        for url in ["http://192.168.1.100:1234/v1", "https://192.168.1.100:8443/v1", "http://api.example.com/v1", "https://api.example.com/v1"]:
            with self.subTest(url=url):
                validate_answer_endpoint({"base_url": url, "remote_enabled": True})
                with self.assertRaises(ValueError):
                    validate_answer_endpoint({"base_url": url, "remote_enabled": False})

    def test_lan_http_endpoint_configuration_and_port_boundary(self):
        c = SiteConfig.get()
        c.llm_base_url = "http://192.168.1.100:1234/v1"
        c.llm_remote_enabled = True
        c.save()
        cfg = llm_settings()
        validate_answer_endpoint(cfg)
        self.assertEqual(cfg["base_url"], c.llm_base_url)
        check_connection(("192.168.1.100", 1234))
        with self.assertRaises(PermissionError):
            check_connection(("192.168.1.100", 1235))
