from __future__ import annotations

"""将适配脚本的标准输出或 JSON 文件作为一次增量更新导入。"""
import json
import sys
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from kb.feed_protocol import MAX_BYTES,load_batch
from kb.feeds import claim,execute

class Command(BaseCommand):
    help='校验或导入 fos-rag-feed/v1 JSON；path=- 表示标准输入。'
    def add_arguments(self,parser):
        parser.add_argument('path')
        parser.add_argument('--source')
        parser.add_argument('--validate-only',action='store_true')
    def handle(self,*args,**options):
        try:
            if options['path']=='-':raw=sys.stdin.buffer.read(MAX_BYTES+1)
            else:
                with Path(options['path']).open('rb') as stream:raw=stream.read(MAX_BYTES+1)
            payload=load_batch(raw)
            if options['validate_only']:
                self.stdout.write('Valid fos-rag-feed/v1 batch.');return
            if not options['source']:raise ValueError('导入时必须使用 --source 指定数据源 ID。')
            run,fresh=claim(options['source'],trigger='import',payload=payload,request_key='import:'+payload['batch_id'] if payload.get('batch_id') else '')
            if fresh:run=execute(run.pk)
            self.stdout.write(json.dumps({'run_id':str(run.pk),'status':run.status,'counts':run.counts,'error':run.error},ensure_ascii=False))
            if run.status=='failed':raise ValueError('导入失败，请查看数据源运行日志。')
        except (ValueError,OSError) as exc:raise CommandError(str(exc)) from exc
