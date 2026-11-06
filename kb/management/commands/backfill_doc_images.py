"""补回历史 PDF 文档的图片（修复查看页裂图 + 为多模态索引备料）。

背景：早期入库只取 MinerU 的 md_content，图片二进制被丢弃，md 里的
images/<hash>.jpg 引用悬空。MinerU 的图片名按内容哈希生成，重跑 OCR
名字不变，因此重新解析一次即可无损补回，md_content 本身不动。

用法：
    python manage.py backfill_doc_images            # 补所有缺图的 PDF
    python manage.py backfill_doc_images --kb <slug>  # 只补某个库
    python manage.py backfill_doc_images --rebuild-html  # 顺带重建 html_content（重写图片 URL）
    python manage.py backfill_doc_images --reindex   # 补图后重建向量索引（图片块需 WeMM 在线）
"""
import re

from django.core.management.base import BaseCommand

from kb.models import Document
from kb.pipeline import (
    build_doc_html, doc_image_dir, run_indexing, run_ocr_with_images, save_doc_images,
)
from kb.retriever import delete_doc_vectors


class Command(BaseCommand):
    help = "重跑 MinerU 补回历史 PDF 文档的图片（图片名是内容哈希，重跑不变）"

    def add_arguments(self, parser):
        parser.add_argument("--kb", default="", help="只处理指定 slug 的文档库")
        parser.add_argument("--force", action="store_true", help="已有图片的文档也重新解析")
        parser.add_argument("--rebuild-html", action="store_true", help="重建 html_content（应用图片 URL 重写）")
        parser.add_argument("--reindex", action="store_true", help="补图后重建该文档向量索引（图片块需 WeMM）")

    def handle(self, *args, **options):
        only_slug = options["kb"]
        qs = Document.objects.filter(file_type="pdf", status=Document.Status.COMPLETED).exclude(md_content="")
        if only_slug:
            qs = qs.filter(kb__slug=only_slug)
        docs = [d for d in qs if options["force"] or not doc_image_dir(d.id).is_dir()]

        if not docs:
            self.stdout.write(self.style.WARNING("没有待补图的 PDF 文档。"))
            return

        self.stdout.write(f"待补图文档 {len(docs)} 份（MinerU 逐份重新解析，耗时视文档大小）")
        # --reindex 会先删旧向量再重建：预检 embedding 端点，不可用直接中止，
        # 避免"删完才发现嵌不进去"的静默检索丢失
        if options["reindex"]:
            try:
                from kb.pipeline import _embeddings
                _embeddings().embed_query("重建预检")
            except Exception as e:
                self.stderr.write(self.style.ERROR(
                    f"embedding 端点不可用，中止（未删除任何数据）: {str(e)[:160]}"))
                return
        ok = skip = fail = 0
        for doc in docs:
            self.stdout.write(f"· {doc.original_name} (库 {doc.kb.slug}) …", ending="")
            try:
                from pathlib import Path as _Path
                md, images, content_list = run_ocr_with_images(_Path(doc.file.path), doc.file_type)
                n = save_doc_images(doc.id, images)
                # 新 md 里引用了、但 md_content（旧）里也引用的图片都应落盘；
                # 名字按内容哈希 → 新旧一致，无需回写 md_content。
                if options["rebuild_html"]:
                    build_doc_html(doc)
                if options["reindex"]:
                    # 先清旧向量再重建：run_indexing 的文本块用全新 uuid upsert，
                    # 不清会让同一文档的向量越积越多（吃召回名额、统计失真）。
                    delete_doc_vectors(doc.kb.slug, doc.original_name)
                    try:
                        n_chunks = run_indexing(doc.md_content, doc.kb.slug, doc.original_name,
                                                doc_id=doc.id, content_list=content_list)
                        doc.chunk_count = n_chunks
                        doc.save(update_fields=["chunk_count", "updated_at"])
                    except Exception:
                        # 旧向量已删且重建失败 → 归零，防止"统计有数、检索无货"
                        doc.chunk_count = 0
                        doc.save(update_fields=["chunk_count", "updated_at"])
                        raise
                d = doc_image_dir(doc.id)
                refs = {m.group(1) for m in re.finditer(r"images/([0-9a-f]+\.\w+)", doc.md_content)}
                missing = sum(1 for name in refs if not (d / name).is_file()) if d.is_dir() else len(refs)
                msg = f" {n} 张图片落盘"
                if missing > 0:
                    msg += f"（警告：md 引用但磁盘缺失 {missing} 张）"
                elif options["reindex"]:
                    msg += f"，重建 {doc.chunk_count} 块"
                self.stdout.write(self.style.SUCCESS(msg))
                ok += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f" 失败: {str(e)[:120]}"))
                fail += 1
        self.stdout.write(self.style.SUCCESS(f"\n完成：成功 {ok}，失败 {fail}。"))
        if options["reindex"]:
            self.stdout.write(self.style.WARNING("提醒：长驻 Django 进程缓存了旧向量库句柄，请重启后生效。"))
