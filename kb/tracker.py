"""知识库追踪表：文档入库完成后由 AI 抽取关键信息登记成行。

设计要点：
- 一库一表（KbTracker），字段由管理员自定义（label 即列名即 JSON 键）。
- 抽取走现有 LLM 配置（llm_settings → ChatOpenAI 非流式 invoke），
  提示词要求只输出 JSON；解析容错（剥 ```json 围栏 / 截取首尾大括号）。
- 行状态机 pending → running → done/failed；用条件 UPDATE 原子认领，
  补录与自动钩子并发时同一文档不会被抽两次。
- 失败只标记行（error 保留原因），不影响文档本身；页面可一键重试。
"""
from __future__ import annotations

import json
import logging
import re
import threading

log = logging.getLogger(__name__)

# 送入 LLM 的正文上限：定检/财务类报告的关键信息集中在头部与表格，
# 12k 字符足够覆盖；超出部分截断（也可在字段提示里要求只看某章节）。
_MAX_CONTENT_CHARS = 12000
_LLM_TIMEOUT = 600  # 本地 LLM（LM Studio）prefill 慢，超时给足


def build_extraction_prompt(kb_name: str, fields: list[dict], md_content: str,
                            instruction: str = "",
                            prior_values: dict | None = None) -> str:
    """构建抽取提示词（模块函数，便于单测）。

    prior_values：该文档此前已登记的旧值（重试时传入）。LLM 被要求
    保旧优先——只有文档内容明确支持不同的值时才改动，防止弱模型
    把原本正确的值改错。
    """
    field_lines = "\n".join(f"- {f['label']}：{f.get('hint', '')}".rstrip("：")
                            for f in fields)
    tpl = f"""你是资料登记助手。下面是知识库「{kb_name}」中新入库的一份文档全文。
请从文档中提取以下追踪字段，并只输出一个 JSON 对象（不要输出任何其它文字）：

{field_lines}

规则：
1. 每个字段的值必须是文档中事实信息的简明摘录，长度不超过 80 字。
2. 文档中没有提到的字段，值填空字符串 ""；严禁编造或推测。
3. 日期统一写成 YYYY-MM-DD；金额写数字（可带单位说明）。
"""
    if prior_values:
        import json as _json
        shown = {k: (prior_values.get(k) or "") for k in (f["label"] for f in fields)}
        tpl += ("4. 该文档此前已登记过（旧值）：" +
                _json.dumps(shown, ensure_ascii=False) +
                "\n   除非文档内容明确表明旧值有误，否则保持旧值原样输出；只有文档确实"
                "支持不同值时才改动，不确定时一律沿用旧值。\n")
    if instruction.strip():
        tpl += f"5. 补充要求：{instruction.strip()}\n"
    tpl += f"""

只输出 JSON，形如：{json.dumps({f["label"]: "" for f in fields}, ensure_ascii=False)}

文档全文（可能截断）：
{md_content[:_MAX_CONTENT_CHARS]}"""
    return tpl


def parse_llm_json(text: str) -> dict:
    """解析 LLM 输出的 JSON：剥代码围栏 → 截取首尾大括号 → json.loads。"""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("输出中没有 JSON 对象")
    data = json.loads(t[i:j + 1])
    if not isinstance(data, dict):
        raise ValueError("输出不是 JSON 对象")
    return data


def _llm_invoke(prompt: str) -> str:
    """走站点 LLM 配置做一次非流式补全（服务层可 patch 此函数做单测）。"""
    from langchain_openai import ChatOpenAI
    from .config import llm_settings

    cfg = llm_settings()
    llm = ChatOpenAI(
        model=cfg["model"],
        api_key=cfg["api_key"] or "local-no-key",
        base_url=cfg["base_url"],
        temperature=0,
        timeout=_LLM_TIMEOUT,
    )
    resp = llm.invoke(prompt)
    return getattr(resp, "content", "") or ""


def run_extraction(doc_id, tracker_id=None) -> bool:
    """对一份文档执行抽取（幂等入口）。返回是否成功改到 done。

    条件 UPDATE 认领：仅当行处于 pending/failed 时置 running，
    并发调用（自动钩子 + 手动补录/重试）只有一方能继续。
    """
    import django
    django.setup()
    from .models import Document, KbTracker, TrackerRow

    doc = Document.objects.select_related("kb", "kb__parent").filter(id=doc_id).first()
    if doc is None:
        return False
    if tracker_id is not None:
        tracker = KbTracker.objects.filter(id=tracker_id).first()
    else:
        # 自动钩子：文档挂在子库时向上找父库（文件夹）上的追踪表
        tracker = (getattr(doc.kb, "tracker", None)
                   or getattr(doc.kb.parent, "tracker", None))
    if tracker is None or not tracker.enabled or not tracker.fields:
        return False

    row, _created = TrackerRow.objects.get_or_create(
        tracker=tracker, document=doc,
        defaults={"status": TrackerRow.Status.PENDING},
    )
    claimed = TrackerRow.objects.filter(
        id=row.id, status__in=[TrackerRow.Status.PENDING, TrackerRow.Status.FAILED],
    ).update(status=TrackerRow.Status.RUNNING, error="")
    if not claimed:
        return False  # 已有进行中的抽取

    # 重试场景：把旧值带进提示词（保旧优先），差异走人工确认
    prior = dict(row.values) if row.values else None

    try:
        prompt = build_extraction_prompt(
            doc.kb.name, tracker.fields, doc.md_content or "",
            tracker.instruction, prior_values=prior)
        data = parse_llm_json(_llm_invoke(prompt))
        values = {}
        for f in tracker.fields:
            v = data.get(f["label"])
            values[f["label"]] = str(v).strip()[:80] if v is not None else ""

        if prior and any(values[k] != (prior.get(k) or "") for k in values):
            # 有旧值且出现差异 → 挂起待确认（旧值保留，新值存 proposed_values）
            TrackerRow.objects.filter(id=row.id).update(
                proposed_values=values, status=TrackerRow.Status.PROPOSED, error="")
            return True
        TrackerRow.objects.filter(id=row.id).update(
            values=values, proposed_values={},
            status=TrackerRow.Status.DONE,
            schema_version=tracker.schema_version, error="")
        return True
    except Exception as e:  # noqa: BLE001 —— 失败要落到行上可见可重试
        TrackerRow.objects.filter(id=row.id).update(
            status=TrackerRow.Status.FAILED, error=str(e)[:280])
        log.warning("追踪表抽取失败 doc=%s tracker=%s: %s",
                    doc_id, tracker.id, str(e)[:160])
        return False


def run_extraction_async(doc_id, tracker_id=None) -> None:
    """后台线程执行抽取（与 process_document_async 同款模式）。"""
    t = threading.Thread(target=run_extraction, args=(doc_id, tracker_id),
                         daemon=True)
    t.start()


def backfill_async(tracker: "KbTracker") -> int:
    """补录：为库内所有已完成、且尚无 done 行的文档排队抽取。

    返回排队的文档数；后台逐份执行（顺序，避免打爆本地 LLM）。
    """
    import django
    django.setup()
    from .models import Document, TrackerRow

    doc_ids = list(
        Document.objects.filter(
            kb__in=[tracker.kb_id] + list(
                tracker.kb.children.values_list("id", flat=True)),
            status=Document.Status.COMPLETED,
        ).exclude(tracker_rows__tracker=tracker,
                  tracker_rows__status__in=[TrackerRow.Status.DONE,
                                            TrackerRow.Status.PROPOSED],
        ).values_list("id", flat=True))

    def _worker():
        for did in doc_ids:
            run_extraction(did, tracker.id)

    if doc_ids:
        threading.Thread(target=_worker, daemon=True).start()
    return len(doc_ids)
