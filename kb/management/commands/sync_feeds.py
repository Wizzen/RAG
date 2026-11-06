from __future__ import annotations

"""供系统 cron 每分钟调用；到期任务使用数据库租约互斥。"""
import json
from django.core.management.base import BaseCommand,CommandError
from django.utils import timezone
from kb.models import FeedSchedulerState,KnowledgeFeed
from kb.feeds import claim,execute,run_due

class Command(BaseCommand):
    help='执行到期的 API 数据源，或手动执行指定数据源。'
    def add_arguments(self,parser):
        group=parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--due',action='store_true')
        group.add_argument('--source')
    def handle(self,*args,**options):
        FeedSchedulerState.objects.update_or_create(key='default',defaults={'heartbeat_at':timezone.now()})
        if options['due']:runs=run_due()
        else:
            try:
                feed=KnowledgeFeed.objects.get(pk=options['source'],mode='pull')
                run,fresh=claim(feed.pk,force=True);runs=[execute(run.pk) if fresh else run]
            except (ValueError,KnowledgeFeed.DoesNotExist) as exc:raise CommandError(str(exc)) from exc
        for run in runs:self.stdout.write(json.dumps({'run_id':str(run.pk),'status':run.status,'counts':run.counts,'error':run.error},ensure_ascii=False))
        if any(run.status=='failed' for run in runs):raise CommandError('部分数据源更新失败，请查看运行日志。')
