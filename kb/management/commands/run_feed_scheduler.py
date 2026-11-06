from __future__ import annotations

"""Windows 或无 cron 环境的常驻调度器；不在 Web 请求进程中自动启动。"""
import time
from django.core.management.base import BaseCommand,CommandError
from django.db import close_old_connections
from django.utils import timezone
from kb.models import FeedSchedulerState
from kb.feeds import run_due

class Command(BaseCommand):
    help='常驻扫描到期数据源；生产也可用系统 cron 调用 sync_feeds --due。'
    def add_arguments(self,parser):
        parser.add_argument('--interval',type=int,default=30)
        parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        if not 5<=options['interval']<=60:raise CommandError('扫描间隔必须为5至60秒。')
        self.stdout.write('Knowledge feed scheduler started.')
        try:
            while True:
                close_old_connections()
                try:
                    FeedSchedulerState.objects.update_or_create(key='default',defaults={'heartbeat_at':timezone.now()})
                    run_due()
                except Exception as exc:self.stderr.write('Scheduler tick failed: '+type(exc).__name__)
                finally:close_old_connections()
                if options['once']:return
                time.sleep(options['interval'])
        except KeyboardInterrupt:self.stdout.write('Scheduler stopped.')
