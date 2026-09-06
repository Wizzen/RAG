"""回填关键词检索索引（混合检索 P0 的存量数据迁移）。

从每个文档库的 Chroma collection 读出全部 chunk 文本，写入 keyword.db。
不重新向量化、不重新 OCR；幂等可重跑。

用法：
    python manage.py keyword_backfill            # 所有已完成的文档库
    python manage.py keyword_backfill --kb SLUG  # 只回填指定库
"""
from django.core.management.base import BaseCommand

from kb import keyword_index
from kb.models import Document
from kb.retriever import get_kb_vectorstore


class Command(BaseCommand):
    help = "从 Chroma 现有向量回填关键词检索索引（混合检索的文本路）"

    def add_arguments(self, parser):
        parser.add_argument("--kb", default="", help="只回填指定 slug 的文档库")

    def handle(self, *args, **options):
        only_slug = options["kb"]
        qs = Document.objects.filter(status=Document.Status.COMPLETED).exclude(md_content="")
        if only_slug:
            qs = qs.filter(kb__slug=only_slug)

        by_kb: dict[str, list[Document]] = {}
        for d in qs:
            by_kb.setdefault(d.kb.slug, []).append(d)

        if not by_kb:
            self.stdout.write(self.style.WARNING("没有已完成的文档可回填。"))
            return

        total_rows = 0
        for kb_slug, docs in by_kb.items():
            keyword_index.delete_kb(kb_slug)
            n = 0
            for doc in docs:
                try:
                    vs = get_kb_vectorstore(kb_slug)
                    res = vs._collection.get(  # noqa: SLF001  与 fetch_doc 同一模式
                        where={"source": doc.original_name},
                        include=["documents", "metadatas"],
                    )
                    texts = res.get("documents") or []
                    metas = res.get("metadatas") or []

                    class _C:  # 适配 keyword_index.add_chunks 的 LCDocument 形状
                        def __init__(self, text, meta):
                            self.page_content = text
                            self.metadata = meta

                    keyword_index.add_chunks(kb_slug, [_C(t, m or {}) for t, m in zip(texts, metas)])
                    n += len(texts)
                except Exception as e:
                    self.stderr.write(self.style.ERROR(f"  ✗ {doc.original_name}: {e}"))
            total_rows += n
            self.stdout.write(self.style.SUCCESS(f"  ✓ 库 {kb_slug}：回填 {n} 条（{len(docs)} 份文档）"))

        self.stdout.write(self.style.SUCCESS(f"\n完成：共回填 {total_rows} 条 chunk 到关键词索引。"))
