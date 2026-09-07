"""检索质量评估（回归问题集 → 各文档库混合检索 → 命中报告）。

供 management command（eval_retrieval）与 staff 评估面板（views.eval_panel）共用。
"""
from __future__ import annotations

from .models import Document, EvalQuestion, KnowledgeBase
from .retriever import search

TOP_K = 5


def _doc_libs() -> list[KnowledgeBase]:
    """有向量的文档库（子库 + 独立库）。"""
    return list(
        KnowledgeBase.objects.filter(is_folder=False, chunk_count__gt=0)
        .only("slug", "name")
    )


def _hit(item: dict, q: EvalQuestion) -> bool:
    if q.expected_source and q.expected_source not in (item.get("source") or ""):
        return False
    if q.expected_keyword and q.expected_keyword not in (item.get("text") or ""):
        return False
    if not q.expected_source and not q.expected_keyword:
        return False  # 无期望 → 不参与命中率统计
    return True


def run_eval(top_k: int = TOP_K) -> dict:
    """跑全部评估问题，返回报告 dict（模板与命令共用结构）。

    返回：{
      "questions": [{question, expected, rows: [{rank, kb, via, source, section,
                                                  score, text, hit}], rank_hit}],
      "summary": {"total", "scored", "hit1", "hit3", "hit1_rate", "hit3_rate"},
    }
    """
    libs = _doc_libs()
    questions = []
    scored = hit1 = hit3 = 0
    for q in EvalQuestion.objects.all():
        rows = []
        # 每个问题只嵌一次查询向量（此前每库重复嵌入，Q×L 次远程调用）
        q_emb = None
        try:
            from .pipeline import _embeddings
            q_emb = _embeddings().embed_query(q.question)
        except Exception:
            pass
        for lib in libs:
            try:
                results = search(lib.slug, q.question, k=top_k, query_embedding=q_emb)
            except Exception as e:
                results = [{"text": f"(检索失败: {e})", "source": "", "section": "",
                            "score": 0, "via": "err", "doc_id": ""}]
            for r in results:
                rows.append({
                    "kb": lib.name, "kb_slug": lib.slug,
                    "via": r.get("via", ""), "source": r.get("source", ""),
                    "section": r.get("section", ""), "score": round(r.get("score", 0), 4),
                    "text": (r.get("text") or "")[:160].replace("\n", " "),
                    "rr": r.get("rerank_score"),  # 精排分（启用时与排序一致）
                    "hit": _hit(r, q),
                })
        # 命中名次 = 命中行之前同库行数 + 1（每库独立排名）
        rank_hit = None
        for i, row in enumerate(rows):
            if row["hit"]:
                rank_hit = sum(1 for x in rows[:i + 1] if x["kb"] == row["kb"])
                break
        has_expectation = bool(q.expected_source or q.expected_keyword)
        if has_expectation:
            scored += 1
            if rank_hit is not None and rank_hit <= 1:
                hit1 += 1
            if rank_hit is not None and rank_hit <= 3:
                hit3 += 1
        questions.append({
            "question": q.question,
            "expected": " / ".join(filter(None, [q.expected_source, q.expected_keyword])) or "（无期望，仅观察）",
            "rows": rows,
            "rank_hit": rank_hit,
        })
    summary = {
        "total": len(questions),
        "scored": scored,
        "hit1": hit1,
        "hit3": hit3,
        "hit1_rate": round(hit1 / scored, 3) if scored else None,
        "hit3_rate": round(hit3 / scored, 3) if scored else None,
    }
    return {"questions": questions, "summary": summary}
