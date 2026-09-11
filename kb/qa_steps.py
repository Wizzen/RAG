"""问答管线增强的两步（站点开关 qa_enhance 控制）：

1. plan_query（query_plan）：回答前把用户问题改写/拆解成利于检索的形式，
   结果作为「问题理解」注入 agent 输入——小模型+短输出，代价低。
2. verify_answer（answer_verification）：回答完成后用第二次 LLM 调用把
   答案的事实性结论逐一对照本轮检索证据核实；核实不过 → 前端拒答展示。
   核实器本身不可用/输出不可解析 → 放行原答案（不让基础设施故障惩罚用户）。

两步都返回 usage，供 token 统计并入本轮用量。
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# 思考型模型（Qwen3 等）的结论可能全部落在 reasoning_content、content 为空，
# 且思考本身消耗输出 token——max_tokens 必须给思考留余量
_PLAN_MAX_TOKENS = 1000
_VERIFY_MAX_TOKENS = 2500
_VERIFY_MAX_EVIDENCE_CHARS = 400  # 每条证据截断
_VERIFY_MAX_ITEMS = 24            # 证据条数上限


def build_llm(llm_cfg: dict):
    """按站点配置构建 ChatOpenAI（规划/核实与主回答共用同一模型配置）。"""
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=llm_cfg["model"],
        # OpenAI's client requires a non-empty credential even when a local
        # OpenAI-compatible server (for example LM Studio) disables auth.
        api_key=llm_cfg["api_key"] or "local-no-key",
        base_url=llm_cfg["base_url"],
        temperature=llm_cfg["temperature"],
        stream_usage=True,
    )


def _usage(result) -> dict:
    um = getattr(result, "usage_metadata", None) or {}
    return {
        "input_tokens": int(um.get("input_tokens") or 0),
        "output_tokens": int(um.get("output_tokens") or 0),
    }


def _result_text(result) -> str:
    """取 LLM 回复文本；思考型模型 content 为空时回退 reasoning_content。"""
    text = getattr(result, "content", "")
    if not text:
        kw = getattr(result, "additional_kwargs", None) or {}
        rc = kw.get("reasoning_content") or kw.get("reasoning")
        if isinstance(rc, str):
            text = rc
    return text if isinstance(text, str) else ""


_PLAN_PROMPT = """你是检索规划器。把用户问题改写成更利于知识库（游乐设施维护手册/检测报告）检索的形式。

输出格式（总共不超过 100 字，不要解释）：
核心问题：<一句话>
关键词：<3-5 个检索词，空格分隔>

如果问题是问候、闲聊或与文档检索无关，只输出：SKIP"""


async def plan_query(llm_cfg: dict, message: str) -> tuple[str | None, dict]:
    """问题规划。返回 (规划文本 or None, usage)。失败返回 (None, usage)。

    返回 None = 跳过规划（闲聊或调用失败），主流程不受影响。
    """
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        llm = build_llm(llm_cfg)
        result = await llm.ainvoke(
            [{"role": "user", "content": f"{_PLAN_PROMPT}\n\n用户问题：{message}"}],
            max_tokens=_PLAN_MAX_TOKENS,
        )
        usage = _usage(result)
        text = _result_text(result).strip()
        if not text or "SKIP" in text[:20]:
            return None, usage
        # 只取前 6 行，防止模型絮叨
        text = "\n".join(text.splitlines()[:6]).strip()
        return text, usage
    except Exception as e:
        logger.warning("问题规划失败（跳过该步）: %s", str(e)[:160])
        return None, usage


_VERIFY_PROMPT = """你是答案核实器。对照【检索证据】核对【回答】，区分「核心结论」与「附带信息」：
- 核心结论 = 直接回答【用户问题】的内容（数值、型号、参数、日期、适用条件等）。
- 附带信息 = 回答中主动补充的背景/延伸内容（问题没问但回答里提到的）。

判定规则：
- 关键事实只要出现在【任一】证据片段中即算支持（不必与回答同序；同义转述、单位换算算支持）。
- 核心结论任一无证据或与证据矛盾 → verdict=fail。
- 核心结论全部有证据，但附带信息存在未核实项 → verdict=warn（这些项列入 issues，不拒答）。
- 回答明确说「检索到的片段未包含/未找到」的，不算错误。
- 页码、行号等定位引用（「第 1 页」「原表第 1-2 行」）来自系统元数据，不需核对。
- 回答是对问候/闲聊的回应、或没有任何事实性结论 → verdict=pass。

只输出 JSON（不要其它文字）：
{"verdict": "pass", "issues": []}
或
{"verdict": "warn", "issues": ["<附带信息中哪条未核实，≤40字>", ...]}（≤3 条）
或
{"verdict": "fail", "issues": ["<核心结论哪条无证据/矛盾，≤40字>", ...]}（≤3 条）"""


def _format_evidence(evidence: list[dict]) -> str:
    lines = []
    for i, ev in enumerate(evidence[:_VERIFY_MAX_ITEMS], 1):
        text = (ev.get("text") or "").strip().replace("\n", " ")
        if len(text) > _VERIFY_MAX_EVIDENCE_CHARS:
            text = text[:_VERIFY_MAX_EVIDENCE_CHARS] + "…"
        loc = f" {ev['page']}" if ev.get("page") else ""
        lines.append(f"[{i}] {ev.get('source', '未知')}{loc}\n{text}")
    return "\n\n".join(lines)


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_verdict(text: str) -> dict | None:
    """解析核实器输出 → {"verdict": pass|warn|fail, "issues": [...]}。

    兼容旧格式 {"verified": bool}。不可解析 → None（放行）。
    """
    if not text:
        return None
    m = _JSON_RE.search(text)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                issues = [str(x)[:60] for x in (data.get("issues") or [])
                          if str(x).strip()][:3]
                verdict = str(data.get("verdict") or "").lower().strip()
                if verdict in ("pass", "warn", "fail"):
                    return {"verdict": verdict, "issues": issues}
                if isinstance(data.get("verified"), bool):
                    return {"verdict": "pass" if data["verified"] else "fail",
                            "issues": issues}
        except ValueError:
            pass
    # 无 JSON：弱模型兜底（裸词判定）
    low = text.lower()
    if "fail" in low:
        return {"verdict": "fail", "issues": ["核心结论存在证据未支持项"]}
    if "warn" in low:
        return {"verdict": "warn", "issues": []}
    if "pass" in low or "true" in low:
        return {"verdict": "pass", "issues": []}
    return None


async def verify_answer(llm_cfg: dict, question: str, answer: str,
                        evidence: list[dict]) -> tuple[dict | None, dict]:
    """答案核实。返回 (判定 or None, usage)；None = 核实器不可用/不可解析（放行）。"""
    usage = {"input_tokens": 0, "output_tokens": 0}
    if not evidence or not (answer or "").strip():
        return None, usage
    try:
        llm = build_llm(llm_cfg)
        prompt = (
            f"{_VERIFY_PROMPT}\n\n"
            f"【用户问题】\n{question}\n\n"
            f"【检索证据】\n{_format_evidence(evidence)}\n\n"
            f"【待核实回答】\n{answer[:4000]}"
        )
        result = await llm.ainvoke([{"role": "user", "content": prompt}],
                                   max_tokens=_VERIFY_MAX_TOKENS)
        usage = _usage(result)
        text = _result_text(result)
        verdict = _parse_verdict(text)
        if verdict is None:
            logger.warning("核实输出不可解析（放行原答案）: %s", (text or "")[:160])
        return verdict, usage
    except Exception as e:
        logger.warning("答案核实调用失败（放行原答案）: %s", str(e)[:160])
        return None, usage
