from __future__ import annotations

import json
from django.core.management.base import BaseCommand,CommandError
from django.db.models import Q
from kb.models import KnowledgeBase
from kb.hierarchy_conversion import convert_relations


class Command(BaseCommand):
    help='预览或转换旧版关系为层级候选，保留旧数据且不调用 LLM。'
    def add_arguments(self,parser):
        parser.add_argument('--kb',required=True)
        parser.add_argument('--run',type=int)
        parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**options):
        kb=KnowledgeBase.objects.filter(slug=options['kb']).first()
        if not kb:raise CommandError('知识库不存在。')
        slugs=list(KnowledgeBase.objects.filter(Q(pk=kb.pk)|Q(parent=kb)).values_list('slug',flat=True))
        self.stdout.write(json.dumps(convert_relations(slugs,run=options.get('run'),apply=options['apply']),ensure_ascii=False))
