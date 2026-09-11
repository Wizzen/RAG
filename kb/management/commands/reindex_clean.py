"""重建已处理文档的向量索引（不重新 OCR）。

用途：
1. 嵌入文本清洗规则变更后（HTML 表格 → 结构化文本）重灌向量；
2. **更换 embedding 模型后重建**（如 Qwen3-Embedding → WeMM）。

换模型必须整目录重建：Chroma collection 的向量维度在建库时固定
（Qwen3-Embedding-4B=2560，WeMM-2B=2048），collection 内部 delete(where=)
清空数据不改变维度，重灌不同维度的向量会直接报维度不匹配。因此本命令
对每个文档库删除 data/chroma/<slug>/ 整个目录后从 md_content 重建。

层级结构下每个文档库（slug）只承载一份文档，整目录删除不影响其它文档。

注意：重建期间，长驻 Django 进程（开发服务器）缓存的 Chroma 句柄指向
已删除的文件，重建完成后需重启 Django 恢复检索。

用法：
    python manage.py reindex_clean             # 所有已完成的文档
    python manage.py reindex_clean --kb <slug> # 只重建某个文档库
    python manage.py reindex_clean --dry-run   # 只列出将处理的文档
"""
import shutil

from django.core.management.base import BaseCommand

from kb.models import Document
from kb.pipeline import _kb_persist_dir, run_indexing
from kb.retriever import _VS_CACHE


class Command(BaseCommand):
    help = "重建文档向量索引：整目录删除 data/chroma/<slug> 后从 md_content 重新切块+embedding（换 embedding 模型后必用）"

    def add_arguments(self, parser):
        parser.add_argument("--kb", default="", help="只重建指定 slug 的文档库")
        parser.add_argument("--dry-run", action="store_true", help="只列出待处理文档，不实际重建")

    def handle(self, *args, **options):
        dry = options["dry_run"]
        only_slug = options["kb"]

        qs = Document.objects.filter(status=Document.Status.COMPLETED).exclude(md_content="")
        if only_slug:
            qs = qs.filter(kb__slug=only_slug)
        docs = list(qs)

        if not docs:
            self.stdout.write(self.style.WARNING("没有已完成的文档可重建。"))
            return

        self.stdout.write(f"待重建文档 {len(docs)} 份。")
        if dry:
            for d in docs:
                self.stdout.write(f"  · {d.original_name} (库 {d.kb.slug})")
            self.stdout.write(self.style.WARNING("[dry-run] 不实际重建。"))
            return

        # 预检 embedding 端点：本命令先删后建，端点不可用时重建必然失败，
        # 届时旧向量已删——静默丢失整库检索能力（文档还显示 COMPLETED）。
        # 动手删任何东西前先探活，失败直接中止。
        try:
            from kb.pipeline import _embeddings
            _embeddings().embed_query("重建预检")
        except Exception as e:
            self.stderr.write(self.style.ERROR(
                f"embedding 端点不可用，中止（未删除任何数据）: {str(e)[:160]}"))
            return

        # 按文档库分组，每个库处理完后统一刷新统计 + 缓存
        by_kb: dict[str, list[Document]] = {}
        for d in docs:
            by_kb.setdefault(d.kb.slug, []).append(d)

        total_done = 0
        for kb_slug, lib_docs in by_kb.items():
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== 文档库「{kb_slug}」({len(lib_docs)} 份) ==="))
            kb = lib_docs[0].kb

            # 整目录删除：维度可能已随 embedding 模型变化，collection 不可复用。
            # 先弹掉本进程的向量库缓存，避免持有已删除 sqlite 的句柄。
            _VS_CACHE.pop(kb_slug, None)
            shutil.rmtree(_kb_persist_dir(kb_slug), ignore_errors=True)
            # 混合检索：关键词索引整库清空（随后 run_indexing 会重灌）
            from kb import keyword_index
            keyword_index.delete_kb(kb_slug)

            new_chunk_total = 0
            for doc in lib_docs:
                try:
                    # 溯源重建：OCR 时落盘的 content_list 还在 → 不重跑 OCR 也能
                    # 恢复页码/坐标（没有则该文档无溯源，引用退化为切片页）
                    from kb import provenance as _prov
                    n = run_indexing(doc.md_content, kb_slug, doc.original_name,
                                     doc_id=doc.id,
                                     content_list=_prov.load_content_list(doc.id))
                    doc.chunk_count = n
                    doc.save(update_fields=["chunk_count", "updated_at"])
                    new_chunk_total += n
                    total_done += 1
                    self.stdout.write(self.style.SUCCESS(f"  ✓ {doc.original_name} → {n} 块"))
                except Exception as e:
                    self.stderr.write(self.style.ERROR(f"  ✗ {doc.original_name}: {e}"))
                    # 向量已随目录删除而丢失 → chunk_count 必须归零，
                    # 否则语义检索仍会选中该库却永远查不到内容（静默失效）
                    doc.chunk_count = 0
                    doc.save(update_fields=["chunk_count", "updated_at"])

            # 刷新 KB 缓存统计
            kb.doc_count = kb.documents.filter(status=Document.Status.COMPLETED).count()
            kb.chunk_count = sum(
                d.chunk_count for d in kb.documents.filter(status=Document.Status.COMPLETED)
            )
            kb.save(update_fields=["doc_count", "chunk_count", "updated_at"])
            self.stdout.write(f"  库统计刷新：{kb.doc_count} 文档 / {kb.chunk_count} 向量块")

        self.stdout.write(self.style.SUCCESS(f"\n完成：{total_done}/{len(docs)} 份文档已重建索引。"))
        self.stdout.write(self.style.WARNING("提醒：长驻的 Django 服务进程持有旧向量库句柄，请重启后生效。"))
