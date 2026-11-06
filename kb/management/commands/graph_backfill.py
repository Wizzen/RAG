from __future__ import annotations

"""为已归属知识库的业务表幂等补建图谱候选。"""

from django.core.management.base import BaseCommand, CommandError

from kb.knowledge_graph import seed_dataset
from kb.models import KnowledgeBase, StructuredDataset


class Command(BaseCommand):
    help = "从已归属知识库的有效业务表构建待核对关系，不自动核对或关联旧表。"

    def add_arguments(self, parser):
        parser.add_argument("--kb", help="只处理指定知识库 slug")

    def handle(self, *args, **options):
        datasets = StructuredDataset.objects.filter(active=True, kb__isnull=False)
        if options["kb"]:
            if not KnowledgeBase.objects.filter(slug=options["kb"]).exists():
                raise CommandError("知识库不存在。")
            datasets = datasets.filter(kb__slug=options["kb"])
        created = 0
        failed = 0
        for dataset in datasets.order_by("pk").iterator():
            try:
                created += seed_dataset(dataset)
            except Exception as exc:
                failed += 1
                self.stderr.write(f"{dataset.pk} 构建失败：{exc}")
        self.stdout.write(f"新增 {created} 条候选关系；失败 {failed} 份数据集。")
        if failed:
            raise CommandError("部分数据集构建失败，可修复后安全重跑。")
