from __future__ import annotations

"""命令行创建/批准定时采集任务：与页面、外部接口共用 `kb.collector_api`。

    # 默认最多等 15 分钟，实时打印工具活动，结束后给出真实样例与候选脚本
    python manage.py build_feed "每天13点拉 http://host/api/v1/lightboard/board/ 每车里程" \
        --url http://host/api/v1/lightboard/board/ --token-env LIGHTBOARD_TOKEN

    # 等生成结束并批准入库、启动 cron
    python manage.py build_feed "……" --token-env LIGHTBOARD_TOKEN --wait 900 --approve

    # 之后某个时刻再批准已经生成好的任务
    python manage.py build_feed --job 6c1f… --approve

密钥只从环境变量读取，不出现在命令行参数、任务标题或日志里。
批准（`--approve`）永远要显式给出：本命令不会自动启用调度。
"""

import json
import os
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from kb import collector_api
from kb.models import ConnectorDraft, ConnectorEvent, KnowledgeBase

DONE = ("ready", "needs_info", "failed", "cancelled", "enabled")
TERMINAL_FAIL = ("failed", "cancelled")


class Command(BaseCommand):
    help = "用一句话创建知识库定时采集任务；--wait 等真实样例，--approve 才启用调度"

    def add_arguments(self, parser):
        parser.add_argument("requirement", nargs="?", default="", help="数据来源、要采什么、多久更新")
        parser.add_argument("--user", default="", help="以哪个账号的身份创建（默认第一个管理员账号）")
        parser.add_argument("--url", default="", help="实际 API 地址（凭据只发往这个主机）")
        parser.add_argument("--token-env", default="", help="存放 Bearer Token 的环境变量名")
        parser.add_argument("--header-env", default="", help='服务器环境变量引用，JSON 形式如 {"Authorization":"FOS_FEED_X"}（仅全局管理员）')
        parser.add_argument("--kb", default="", help="归入已有知识库 slug；留空则由模型建议、批准时新建")
        parser.add_argument("--no-private", action="store_false", dest="private", default=True,
                            help="关闭内网/本机地址访问（默认开放，仅影响本次任务）")
        parser.add_argument("--wait", type=int, default=900,
                            help="等待生成结束的秒数（生成在本进程里跑；设 0 会随命令退出而中断，不推荐）")
        parser.add_argument("--approve", action="store_true", help="批准样例并建立定时数据源（会立即首跑）")
        parser.add_argument("--no-first-run", action="store_true", help="批准但不立刻跑第一次")
        parser.add_argument("--write-mode", default="", choices=["", "append", "upsert"], help="覆盖脚本默认的写入模式")
        parser.add_argument("--job", default="", help="操作已存在的任务号（配合 --approve）")
        parser.add_argument("--json", action="store_true", dest="as_json", help="只输出 JSON 结果")

    def handle(self, *args, **options):
        user = self._user(options["user"])
        if options["job"]:
            job = self._job(user, options["job"])
        else:
            if not options["requirement"].strip():
                raise CommandError("请给出采集需求，或用 --job 指定已存在的任务。")
            job = collector_api.create_draft(
                user=user, requirement=options["requirement"], api_url=options["url"],
                authentication=self._auth(options), header_env=self._header_env(options),
                allow_private=options["private"], kb=self._kb(user, options["kb"]),
                created_via="cli")
            if not options["as_json"]:
                self.stdout.write(self.style.SUCCESS(f"已启动生成任务 {job.pk}（通常需要几分钟）"))
        if options["wait"] <= 0:
            self.stderr.write("警告：生成跑在本命令进程里，--wait 0 会让任务停在「进行中」直到两小时后被回收。"
                              f"要么等到底（默认 900 秒），要么改用页面。任务号：{job.pk}")
        else:
            job = self._watch(job, options["wait"])
        if options["approve"]:
            job.refresh_from_db()
            if job.status != "ready":
                raise CommandError(f"任务当前是 {job.status}，只有样例校验通过（ready）才能批准。"
                                   + (f" 待补充：{job.questions}" if job.questions else ""))
            feed = collector_api.approve(user=user, job=job, write_mode=options["write_mode"] or None,
                                         run_now=not options["no_first_run"], first_run_async=False)
            job.refresh_from_db()
            if not options["as_json"]:
                self.stdout.write(self.style.SUCCESS(f"已启用定时数据源 {feed.pk}｜{feed.name}｜cron={feed.cron} {feed.timezone}"))
            if not options['no_first_run']:
                first_run = feed.runs.order_by('-started_at').first()
                if first_run and first_run.status != 'completed':
                    self.report(job, as_json=options['as_json'])
                    raise CommandError('定时任务已启用，但首次入库未完成：' + (first_run.error or first_run.status))
        self.report(job, as_json=options["as_json"])
        if job.status in TERMINAL_FAIL:
            raise SystemExit(1)
        if job.status == "needs_info":
            raise SystemExit(2)

    # ------------------------------------------------------------------ 输入

    def _user(self, username):
        Users = get_user_model()
        user = Users.objects.get(username=username) if username else next(
            (u for u in Users.objects.filter(is_active=True).order_by("-is_superuser", "-is_staff", "id")), None)
        if user is None:
            raise CommandError("没有可用账号，请先创建管理员或用 --user 指定。")
        return user

    def _job(self, user, pk):
        try:
            return ConnectorDraft.objects.select_related("kb", "feed", "parent").get(pk=pk, user=user)
        except ConnectorDraft.DoesNotExist as exc:
            raise CommandError(f"找不到属于该账号的任务 {pk}。") from exc

    def _kb(self, user, slug):
        if not slug:
            return None
        kb = KnowledgeBase.objects.filter(slug=slug).first()
        if kb is None:
            raise CommandError(f"知识库 {slug} 不存在。")
        return kb

    def _auth(self, options):
        """Bearer Token 只从环境变量取，避免进入命令行历史与进程参数。"""
        name = options["token_env"]
        if not name:
            return collector_api.UNSET if not options["header_env"] else {"type": "none"}
        value = (os.environ.get(name) or "").strip()
        if not value:
            raise CommandError(f"环境变量 {name} 未设置，无法带上 Bearer Token。")
        return {"type": "bearer", "token": value}

    def _header_env(self, options):
        raw = options["header_env"]
        if not raw:
            return collector_api.UNSET
        try:
            mapping = json.loads(raw)
        except ValueError as exc:
            raise CommandError("--header-env 需要是 JSON 对象，例如 {\"Authorization\":\"FOS_FEED_X\"}") from exc
        return mapping

    # ------------------------------------------------------------------ 输出

    def _watch(self, job, seconds):
        """轮询任务状态，并把新生成的工具活动逐行输出到 stderr。"""
        ids = list(ConnectorEvent.objects.filter(job=job).order_by("id").values_list("id", flat=True))
        last, deadline = (ids[-1] if ids else 0), time.time() + seconds
        while True:
            for row in ConnectorEvent.objects.filter(job=job, id__gt=last).order_by("id")[:200]:
                last = row.pk
                payload = row.payload or {}
                if row.kind in ("phase", "tool_start", "tool_end", "notice", "question"):
                    self.stderr.write(f"  [{row.turn}] {row.kind:<11} {payload.get('tool', '')} {str(payload.get('message', ''))[:110]}")
            job.refresh_from_db()
            if job.status in DONE:
                return job
            if time.time() >= deadline:
                self.stderr.write(f"  已等待 {seconds}s，任务仍是 {job.status}；稍后可用 --job {job.pk} 继续查看。")
                return job
            time.sleep(2)

    def report(self, job, *, as_json):
        state = collector_api.status(job)
        if as_json:
            self.stdout.write(json.dumps(state, ensure_ascii=False, default=str))
            return
        self.stdout.write(f"任务 {state['id']}  状态={state['status']}  入口={state['created_via']}  "
                          f"来源={state['api_url'] or '（未定）'}  内网={'是' if state['allow_private'] else '否'}  "
                          f"凭据={'已绑定' if state['has_credential'] else '无'}")
        if job.error:
            self.stderr.write(self.style.ERROR(f"失败原因：{job.error}"))
        if state["questions"]:
            self.stdout.write("需要你补充：")
            for item in state["questions"]:
                self.stdout.write(f"  · {item}")
        if state["preview"]:
            self.stdout.write(f"真实样例：{state['preview']['items']} 条，告警 {state['preview']['warnings']} 条"
                              f"（校验={'通过' if state['preview']['valid'] else '未通过'}）")
        if job.spec:
            self.stdout.write("候选脚本：" + json.dumps(
                {key: job.spec.get(key) for key in ("name", "url", "records_path", "id_paths", "cron", "timezone", "write_mode", "source_type")},
                ensure_ascii=False))
        if state["feed_id"]:
            self.stdout.write(self.style.SUCCESS(f"定时数据源：{state['feed_id']}（cron 已启用，可在「自动更新知识库」查看运行记录）"))
        elif job.status == "ready":
            self.stdout.write(self.style.WARNING("样例已就绪：确认无误后加 --approve 才会入库并启用定时更新。"))
