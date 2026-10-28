from __future__ import annotations

"""以主题召回为起点的有限抽取；预算在整次任务内共享，不递归重做。"""

import json
import time

from .graph_progress import GraphProgress, visible_text
from .models import Document, GraphExtraction
from .publication import digest

SEED_CHUNKS = 6
MAX_CALLS = 8
MAX_TOOLS = 8
MAX_PAGES = 4
MAX_READ_CHUNKS = 24
MAX_READ_CHARS = 24000
MAX_INPUT_CHARS = 160000
MAX_OUTPUT_CHARS = 32000
MAX_SECONDS = 600


class FocusLimit(RuntimeError):
    """本次有限任务达到预算，已有候选保留，不自动重启。"""


class FocusBudget:
    """记录实际读取与调用量；在发送请求和接收流时检查整个任务的预算。"""

    def __init__(self, job: GraphExtraction, progress: GraphProgress) -> None:
        self.job, self.progress = job, progress
        self.started = time.monotonic()
        self.calls = self.tools = self.pages = 0
        self.input_chars = self.output_chars = self.read_chars = 0
        self.input_tokens = self.output_tokens = self.usage_calls = 0
        self.read_ids: set[str] = set()
        self.chunks_total = 0
        self.stage = 'retrieve'

    def checkpoint(self) -> None:
        """复用覆盖记录保存主题任务范围，不能将读取段数标成全文覆盖率。"""
        self.job.coverage = {
            'mode': 'focused', 'stage': self.stage, 'chunks_total': self.chunks_total,
            'chunks_read': len(self.read_ids), 'read_ids': sorted(self.read_ids),
            'model_calls': self.calls, 'tool_calls': self.tools, 'pages': self.pages,
            'input_chars': self.input_chars, 'output_chars': self.output_chars,
            'input_tokens': self.input_tokens, 'output_tokens': self.output_tokens,
            'usage_calls': self.usage_calls,
            'limits': {'model_calls': MAX_CALLS, 'tool_calls': MAX_TOOLS, 'pages': MAX_PAGES,
                       'chunks_read': MAX_READ_CHUNKS, 'read_chars': MAX_READ_CHARS,
                       'input_chars': MAX_INPUT_CHARS, 'output_chars': MAX_OUTPUT_CHARS,
                       'seconds': MAX_SECONDS},
        }
        self.job.save(update_fields=['coverage', 'updated_at'])
        self.progress.emit('coverage', **self.job.coverage)

    def check(self) -> None:
        """文档版本变化或耗时超限时，不再读取或发送后续模型请求。"""
        if time.monotonic() - self.started >= MAX_SECONDS:
            raise FocusLimit('已达到本次任务的时间预算；已保存候选保留，未完成范围不再自动重试。')
        current = Document.objects.filter(pk=self.job.document_id).values('md_content', 'status', 'kb_id').first()
        if (not current or current['status'] != 'completed' or current['kb_id'] != self.job.document.kb_id
                or digest(current['md_content']) != self.job.source_digest):
            raise FocusLimit('原始资料已变化，已停止本次生成。')

    def read(self, chunks: list[dict]) -> None:
        """只计入首次读取的正文，拒绝无上限扩展上下文。"""
        self.check()
        fresh = {c['chunk_id']: c for c in chunks if c['chunk_id'] not in self.read_ids}
        chars = sum(len(c['text']) + len(c.get('header_context', '')) for c in fresh.values())
        if len(self.read_ids) + len(fresh) > MAX_READ_CHUNKS or self.read_chars + chars > MAX_READ_CHARS:
            raise FocusLimit('已达到本次任务的原文读取预算；请按更具体的主题继续，已有候选保留。')
        self.read_ids.update(fresh)
        self.read_chars += chars
        self.checkpoint()

    def before_call(self, messages: list) -> None:
        """限制所有轮次累计输入量与调用数，包括续页和工具补读后的生成。"""
        self.check()
        chars = sum(len(json.dumps({'content': m.content, 'tool_calls': getattr(m, 'tool_calls', [])},
                                   ensure_ascii=False)) for m in messages)
        if self.calls >= MAX_CALLS or self.input_chars + chars > MAX_INPUT_CHARS:
            raise FocusLimit('已达到本次任务的模型调用或累计输入预算；已有候选保留，不自动重做。')
        self.calls += 1
        self.input_chars += chars
        self.stage = 'generate'
        self.checkpoint()

    def stream(self, chunk) -> None:
        """流式生成中检查时间与累计输出量，展示截断上限不能代替消费上限。"""
        if time.monotonic() - self.started >= MAX_SECONDS:
            raise FocusLimit('已达到本次任务的时间预算；已保存候选保留。')
        self.output_chars += len(visible_text(chunk.content))
        if self.output_chars > MAX_OUTPUT_CHARS:
            raise FocusLimit('已达到本次任务的累计输出预算；已保存候选保留，不自动重做。')

    def after_call(self, response) -> None:
        """记录服务端实际回报的 token；缺失用量不能显示成零消耗。"""
        usage = getattr(response, 'usage_metadata', None) or {}
        if usage:
            self.input_tokens += int(usage.get('input_tokens') or 0)
            self.output_tokens += int(usage.get('output_tokens') or 0)
            self.usage_calls += 1
        self.checkpoint()

    def before_tool(self) -> None:
        """所有工具共用一次任务的预算，失败和重复请求也占用次数。"""
        self.check()
        if self.tools >= MAX_TOOLS:
            raise FocusLimit('已达到本次任务的补查次数预算；已有候选保留，不自动重做。')
        self.tools += 1
        self.checkpoint()


def build_focused_graph(model, job: GraphExtraction, progress: GraphProgress) -> None:
    """召回少量正文，由模型决定补读；逐页保存候选，失败不会触发全篇重做。"""
    from .graph_extraction import extract_focused, persist_relations
    from .graph_build import fact_key

    budget = FocusBudget(job, progress)
    emitted = set()
    job.done, job.total = 0, 1
    job.save(update_fields=['done', 'total', 'updated_at'])
    budget.checkpoint()

    def save_page(rows: list[dict], rejected: int) -> None:
        budget.check()
        job.created_relations += persist_relations(job.document, job.source_digest, rows, extraction=job, merge_facts=True)
        job.rejected += rejected
        job.save(update_fields=['created_relations', 'rejected', 'updated_at'])
        budget.pages += 1
        budget.checkpoint()
        from .knowledge_graph import PREDICATES
        for row in rows:
            key = fact_key(row)
            if key in emitted:
                continue
            emitted.add(key)
            progress.emit('candidate', subject=row['subject']['name'], predicate=PREDICATES.get(row['predicate'], row['predicate']),
                          object=row['object']['name'], conditions=row.get('conditions', {}),
                          quote=row['quote'], chunks=row.get('_chunks', []))

    try:
        progress.emit('notice', message=f'按关注主题先召回最多 {SEED_CHUNKS} 段正文，模型按需补读邻文；'
                      f'本次最多 {MAX_CALLS} 轮模型调用、{MAX_TOOLS} 次工具补查，不自动扫描全篇或另做查漏。')
        extract_focused(model, job.document, job.focus, progress, on_page=save_page, budget=budget)
        budget.check()
        job.done, job.status = 1, 'completed'
        budget.stage = 'complete'
        budget.checkpoint()
        progress.emit('complete', created=job.created_relations, rejected=job.rejected,
                      message=f'主题抽取已结束，保存 {job.created_relations} 条待确认候选；'
                      f'本次实际读取 {len(budget.read_ids)} 段正文，不代表全文已穷尽。')
        job.save(update_fields=['done', 'status', 'updated_at'])
    except Exception as exc:
        job.status, job.error = 'failed', str(exc)[:1000]
        budget.stage = 'limited' if isinstance(exc, FocusLimit) else 'failed'
        budget.checkpoint()
        job.save(update_fields=['status', 'error', 'updated_at'])
        progress.emit('failed', message=job.error)
