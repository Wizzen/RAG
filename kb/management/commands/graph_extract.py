from __future__ import annotations

"""对已有知识库文档启动自动图谱抽取。"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from kb.graph_extraction import schedule_document
from kb.models import Document, KnowledgeBase


class Command(BaseCommand):
    help = "从知识库原文自动抽取图谱候选并附原文证据，不自动核对。"

    def add_arguments(self, parser):
        parser.add_argument('--kb', required=True)
        parser.add_argument('--retry', action='store_true')
        parser.add_argument('--focus', required=True, help='本次仅关注的一个方面')
        parser.add_argument('--legacy', action='store_true', help='使用旧版关系抽取')

    def handle(self, *args, **options):
        kb = KnowledgeBase.objects.filter(slug=options['kb']).first()
        if kb is None:
            raise CommandError('知识库不存在。')
        documents = Document.objects.filter(Q(kb=kb) | Q(kb__parent=kb), status='completed').exclude(md_content='')
        count = sum(schedule_document(doc, retry=options['retry'], focus=options['focus'],schema_version=1 if options['legacy'] else 2) for doc in documents)
        self.stdout.write(f'已安排 {count} 份文档抽取；进度可在知识图谱页面查看。')
