from __future__ import annotations

"""将实际模型输出与工具活动分批持久化，供界面独立订阅和重连。"""

import time

from .models import GraphExtractionEvent

MAX_OUTPUT_CHARS = 240000


def visible_text(content: str | list) -> str:
    """只展示模型公开文本块，不读取 reasoning 等内部元数据。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(block if isinstance(block, str) else block.get('text', '')
                       for block in content if isinstance(block, str)
                       or isinstance(block, dict) and block.get('type') == 'text')
    return ''


class GraphProgress:
    """限制文本留存量及写入频率，阶段事件前先落盘尚未写入的文本。"""

    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        self.pending = ''
        self.turn = 1
        self.last_flush = 0.0
        self.characters = 0
        self.limited = False
        self.turn_sequence = 0

    def next_turn(self, message: str) -> int:
        """让分批抽取和查漏的轮次连续递增，避免输出日志混在同一轮。"""
        self.turn_sequence += 1
        self.emit('phase', stage='generate', turn=self.turn_sequence, message=message)
        return self.turn_sequence

    def _write(self, kind: str, payload: dict) -> None:
        GraphExtractionEvent.objects.create(extraction_id=self.job_id, kind=kind, payload=payload)

    def emit(self, kind: str, **payload) -> None:
        """记录真实阶段、工具和校验结果，不生成虚构的模型思考。"""
        self.flush()
        self._write(kind, payload)

    def token(self, text: str, turn: int) -> None:
        """接收实际增量文本；超出展示上限仍允许抽取继续完成。"""
        if not text:
            return
        if turn != self.turn:
            self.flush()
            self.turn = turn
        available = max(0, MAX_OUTPUT_CHARS - self.characters)
        kept = text[:available]
        self.pending += kept
        self.characters += len(kept)
        if len(self.pending) >= 768 or time.monotonic() - self.last_flush >= 0.2:
            self.flush()
        if len(text) > available and not self.limited:
            self.limited = True
            self.emit('notice', message='已达到模型输出留存上限；后续文本不再展示，抽取与校验继续进行。')

    def flush(self) -> None:
        """合并短 token，避免每个字符都占用一次数据库写锁。"""
        if self.pending:
            self._write('token', {'text': self.pending, 'turn': self.turn})
            self.pending = ''
            self.last_flush = time.monotonic()


def call_model(model, messages: list, progress: GraphProgress | None = None, turn: int = 1, *, on_chunk=None):
    """通过 LangChain 原生流聚合完整消息，保留分片工具参数及结束信息。"""
    if progress is None:
        response = model.invoke(messages)
        if on_chunk:
            on_chunk(response)
        return response
    from langchain_core.messages import message_chunk_to_message
    aggregate = None
    stream = model.stream(messages)
    try:
        for chunk in stream:
            if on_chunk:
                on_chunk(chunk)
            aggregate = chunk if aggregate is None else aggregate + chunk
            progress.token(visible_text(chunk.content), turn)
    finally:
        close = getattr(stream, 'close', None)
        if callable(close):
            close()
        progress.flush()
    if aggregate is None:
        raise ValueError('模型没有返回输出，请重试。')
    return message_chunk_to_message(aggregate)
