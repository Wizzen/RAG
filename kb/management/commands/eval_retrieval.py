"""跑检索质量评估（回归问题集 → 命中报告），终端输出。

用法：
    python manage.py eval_retrieval           # 表格输出
"""
from django.core.management.base import BaseCommand

from kb.eval import run_eval


class Command(BaseCommand):
    help = "对回归问题集跑混合检索，输出命中报告（换模型/调参前后各跑一次对比）"

    def handle(self, *args, **options):
        report = run_eval()
        s = report["summary"]
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n检索评估：{s['total']} 个问题（{s['scored']} 个带期望）"
            f" | hit@1 {s['hit1']}/{s['scored']}（{s['hit1_rate']}）"
            f" | hit@3 {s['hit3']}/{s['scored']}（{s['hit3_rate']}）\n"))
        for q in report["questions"]:
            mark = f"✓@{q['rank_hit']}" if q["rank_hit"] else "✗"
            self.stdout.write(f"\n[{mark}] {q['question']}  (期望: {q['expected']})")
            for r in q["rows"][:3]:
                flag = "★" if r["hit"] else " "
                self.stdout.write(
                    f"  {flag} [{r['via']:7s}] {r['kb'][:18]:18s} | {r['text'][:60]}")
        if not report["questions"]:
            self.stdout.write(self.style.WARNING(
                "评估问题集为空。到 /kb/eval/ 添加问题（或 EvalQuestion.objects.create）。"))
