"""文档图片管线测试：落盘 / URL 重写 / 图片块构建 / 访问受控视图。"""
import base64
import shutil

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from kb.models import Document, KnowledgeBase
from kb.pipeline import (
    _image_chunks, doc_image_dir, rewrite_img_srcs, save_doc_images,
)

H = lambda c: c * 64  # 64 位 hex（MinerU sha256 命名）
FAKE_JPEG = base64.b64encode(b"\xff\xd8fakejpegdata").decode()


class DocImagePipelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(
            username="img-admin", password=None, is_staff=True,
        )
        cls.other = get_user_model().objects.create_user(
            username="img-other", password=None,
        )
        cls.kb = KnowledgeBase.objects.create(
            name="图片库", slug="img-kb", is_folder=True, created_by=cls.staff,
            department="机械",
        )
        cls.doc = Document.objects.create(
            kb=cls.kb, original_name="图册.pdf", file_type="pdf",
            md_content="# 图册\n\n## 轨道布置\n\n布置见下图\n\n![](images/%s.jpg)" % H("a"),
        )

    def tearDown(self):
        shutil.rmtree(doc_image_dir(self.doc.id), ignore_errors=True)

    def test_save_rejects_non_hash_names_and_dedupes(self):
        n = save_doc_images(self.doc.id, {
            "short.jpg": f"data:image/jpeg;base64,{FAKE_JPEG}",        # 非 32/64 位 hex → 拒
            "../evil.jpg": f"data:image/jpeg;base64,{FAKE_JPEG}",      # 路径注入 → 拒
            H("a") + ".jpg": f"data:image/jpeg;base64,{FAKE_JPEG}",    # 收
        })
        self.assertEqual(n, 1)
        files = [p.name for p in doc_image_dir(self.doc.id).iterdir()]
        self.assertEqual(files, [H("a") + ".jpg"])
        # 同内容重复写入 → 跳过
        self.assertEqual(save_doc_images(self.doc.id, {
            H("a") + ".jpg": f"data:image/jpeg;base64,{FAKE_JPEG}"}), 0)

    def test_rewrite_img_srcs(self):
        html = (
            f'<img src="images/{H("a")}.jpg" alt="x">'
            f'![](images/{H("b")[:32]}.png)'
            '<img src="https://ext.example/x.jpg">'
        )
        out = rewrite_img_srcs(html, self.doc.id)
        self.assertIn(f'src="/kb/doc/{self.doc.id}/img/{H("a")}.jpg"', out)
        self.assertIn(f'/img/{H("b")[:32]}.png', out)
        self.assertIn("https://ext.example/x.jpg", out)
        self.assertNotIn('src="images/', out)

    def test_image_chunks_context_and_small_image_filter(self):
        d = doc_image_dir(self.doc.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / (H("a") + ".jpg")).write_bytes(b"x" * 4096)   # ≥3KB → 收
        (d / (H("c") + ".jpg")).write_bytes(b"x" * 100)    # <3KB → 过滤
        md = (
            "# 图册\n\n## 轨道布置\n\n布置见下图：\n\n"
            f"![](images/{H('a')}.jpg)\n\n![](images/{H('c')}.jpg)"
        )
        items = _image_chunks(md, "图册.pdf", self.doc.id)
        self.assertEqual(len(items), 1)
        chunk = items[0][0]
        self.assertEqual(chunk.metadata["type"], "image")
        self.assertEqual(chunk.metadata["image"], H("a") + ".jpg")
        self.assertIn("轨道布置", chunk.page_content)
        self.assertNotIn("images/", chunk.page_content, "caption 不应残留 md 图片路径")

    def test_doc_image_view_enforces_department_access(self):
        d = doc_image_dir(self.doc.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / (H("a") + ".jpg")).write_bytes(b"\xff\xd8jpeg")
        url = reverse("kb:doc_image", args=[self.doc.id, H("a") + ".jpg"])

        # 机械部门库：staff 可看；普通「通用」部门用户 → 404（不泄露存在性）
        self.client.force_login(self.staff)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/jpeg")

        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)

        # 非法文件名（路径穿越 / 短 hex）→ 404
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get(reverse("kb:doc_image", args=[self.doc.id, "short.jpg"])).status_code, 404)
        self.assertEqual(
            self.client.get(f"/kb/doc/{self.doc.id}/img/..%2Fevil.jpg").status_code, 404)
