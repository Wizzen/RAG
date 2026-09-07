"""WeMM 非对称查询格式（Instruct/Query 前缀）+ 语料块 embedding_input_version 测试。"""
import base64
import os
import shutil
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from kb.models import Document, KnowledgeBase
from kb.pipeline import (
    _WEMM_QUERY_INSTRUCT, _image_chunks, _section_aware_chunk,
    WeMMEmbeddings, doc_image_dir,
)

H = lambda c: c * 64  # 64 位 hex（MinerU sha256 命名）
FAKE_JPEG = base64.b64encode(b"\xff\xd8fakejpegdata").decode()


def _fake_post(vec=None):
    m = MagicMock()
    m.status_code = 200
    m.raise_for_status.return_value = None
    m.json.return_value = {"embeddings": vec or [[0.1, 0.2]]}
    return m


class WeMMQueryFormatTests(TestCase):
    def setUp(self):
        self.wemm = WeMMEmbeddings("http://192.168.1.10:8300", dimensions=64)

    def test_query_plain_by_default_ab_winner(self):
        # A/B 实测（2026-09-07）裸查询全面更优 → 默认裸查询（见 pipeline 注释）
        with patch("kb.pipeline.httpx.post", return_value=_fake_post()) as post:
            self.wemm.embed_query("轴承温度报警怎么处理")
        self.assertEqual(post.call_args.kwargs["json"]["inputs"][0]["text"],
                         "轴承温度报警怎么处理")

    def test_env_flag_enables_instruct_prefix(self):
        with patch.dict(os.environ, {"WEMM_QUERY_INSTRUCT": "1"}):
            with patch("kb.pipeline.httpx.post", return_value=_fake_post()) as post:
                self.wemm.embed_query("轴承温度报警怎么处理")
        sent = post.call_args.kwargs["json"]["inputs"][0]["text"]
        self.assertTrue(sent.startswith(f"Instruct: {_WEMM_QUERY_INSTRUCT}"))
        self.assertIn("\nQuery: 轴承温度报警怎么处理", sent)

    def test_corpus_documents_stay_plain(self):
        # 官方口径的非对称格式：前缀只加查询侧，语料侧必须保持裸文本
        with patch("kb.pipeline.httpx.post",
                   return_value=_fake_post([[0.1], [0.2]])) as post:
            self.wemm.embed_documents(["第一段", "第二段"])
        texts = [i["text"] for i in post.call_args.kwargs["json"]["inputs"]]
        self.assertEqual(texts, ["第一段", "第二段"])


class EmbeddingInputVersionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(
            username="wemm-admin", password=None, is_staff=True,
        )
        cls.kb = KnowledgeBase.objects.create(
            name="wemm 库", slug="wemm-kb", is_folder=True, created_by=cls.staff,
        )
        cls.doc = Document.objects.create(
            kb=cls.kb, original_name="图册.pdf", file_type="pdf",
            md_content="## 轨道布置\n\n![](images/%s.jpg)" % H("a"),
        )

    def tearDown(self):
        shutil.rmtree(doc_image_dir(self.doc.id), ignore_errors=True)

    def test_text_chunks_record_input_version(self):
        chunks = _section_aware_chunk("## 检修规范\n\n每日检查轴承。", "手册.pdf")
        self.assertRegex(chunks[0].metadata["embedding_input_version"],
                         r"^(wemm|openai)-text-user-v1$")

    def test_image_chunks_record_input_version(self):
        d = doc_image_dir(self.doc.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / (H("a") + ".jpg")).write_bytes(b"x" * 4096)
        chunk = _image_chunks(self.doc.md_content, "图册.pdf", self.doc.id)[0][0]
        self.assertRegex(chunk.metadata["embedding_input_version"],
                         r"^(wemm|openai)-media-user-v1$")
