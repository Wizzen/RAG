"""多知识库检索封装。

每个 KB 有独立的 Chroma collection（persist_dir = data/chroma/<slug>/）。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from django.conf import settings
from langchain_chroma import Chroma

from .config import embedding_settings, retrieval_settings
from .pipeline import _chroma_collection_name, _embeddings, _kb_persist_dir

# 进程级缓存：kb_slug -> Chroma。Chroma 的 Rust 后端在多线程下重复创建
# PersistentClient 会触发 'RustBindingsAPI' object has no attribute 'bindings'，
# 因此复用同一个客户端实例。
_VS_CACHE: dict[str, Chroma] = {}
_VS_LOCK = threading.Lock()
# 缓存对应的 embedding 配置指纹（base_url + model）。设置页换了 embedding
# 端点/模型后指纹变化，缓存全部失效——否则运行中的服务会继续拿【旧模型】
# 编码查询向量去比【新模型】入库的向量，结果静默变成垃圾。
_EMB_FINGERPRINT: str | None = None


def get_kb_vectorstore(kb_slug: str) -> Chroma:
    """打开指定 KB 的 Chroma 向量库（缓存实例，线程安全）。"""
    global _EMB_FINGERPRINT
    with _VS_LOCK:
        e = embedding_settings()
        # 指纹含 key 哈希：只换 key（同端点同模型）时缓存也要失效，
        # 否则旧客户端持旧 key → 401 → 静默降级纯关键词
        import hashlib
        key_h = hashlib.sha256((e["api_key"] or "").encode()).hexdigest()[:8]
        fp = f"{e['base_url']}|{e['model']}|{key_h}"
        if fp != _EMB_FINGERPRINT:
            _VS_CACHE.clear()
            _EMB_FINGERPRINT = fp
        vs = _VS_CACHE.get(kb_slug)
        if vs is None:
            vs = Chroma(
                collection_name=_chroma_collection_name(kb_slug),
                embedding_function=_embeddings(),
                persist_directory=str(_kb_persist_dir(kb_slug)),
            )
            _VS_CACHE[kb_slug] = vs
        return vs


def delete_doc_vectors(kb_slug: str, source: str) -> None:
    """删除某文档在 Chroma 中的全部向量（按 source=文件名 过滤）。

    刻意使用**原生 chromadb 客户端**而非 langchain Chroma：
    - 删除只依赖 metadata 过滤，不需要 embedding 计算；
    - embedding 配置（api_key 等）未就绪时，langchain 的 Chroma() 构造即抛
      'Missing credentials'，导致文档已删但向量残留（检索仍能搜到已删内容）。
    """
    from chromadb import PersistentClient
    from chromadb.config import Settings
    from . import keyword_index

    persist_dir = str(_kb_persist_dir(kb_slug))
    coll_name = _chroma_collection_name(kb_slug)
    # 整体兜底：任何阶段失败（含 PersistentClient 构造期，如目录损坏）
    # 都不放过关键词清理——否则已删文档在关键词路"复活"
    try:
        client = PersistentClient(path=persist_dir, settings=Settings(anonymized_telemetry=False))
        col = client.get_collection(coll_name)
        col.delete(where={"source": source})
    except Exception:
        logging.getLogger(__name__).exception(
            "Chroma 向量删除失败（继续清理关键词索引）kb=%s source=%s", kb_slug, source)
    # 两路清理互相独立：任一路失败不拖累另一路（否则残留孤儿行）
    keyword_index.delete_doc(kb_slug, source)


def _fuse_key(text: str) -> str:
    """RRF 融合的 chunk 身份键：全文 md5（向量路与关键词路同源同文；
    不能用前缀——表格类 chunk 常共享相同开头，会把不同块错误合并）。"""
    import hashlib
    return hashlib.md5((text or "").strip().encode("utf-8")).hexdigest()


# RRF（倒数排名融合）常数：score = Σ 1/(K + rank)，与两路分数的量纲无关
RRF_K = 60


def search(kb_slug: str, query: str, k: int | None = None) -> list[dict[str, Any]]:
    """混合检索：向量（语义）+ 关键词（精确编号/术语）双路召回，RRF 融合。

    - 向量路：Chroma 相似度（当前配置的 embedding 模型）
    - 关键词路：keyword.db 文本匹配（与 embedding 模型无关，换模型不影响）
    - 任一路失败自动降级为单路，不互相拖累
    """
    k = k or retrieval_settings()["top_k"]
    recall = max(k * 3, 15)
    vs = get_kb_vectorstore(kb_slug)

    # ---- 向量路 ----
    vec_docs: list[tuple[Any, float]] = []
    try:
        vec_docs = vs.similarity_search_with_relevance_scores(query, k=recall)
    except Exception as e:
        # 降级为纯关键词检索，但必须留痕——embedding 端点挂掉时运维要能发现
        logging.getLogger(__name__).warning("向量检索失败（降级纯关键词）kb=%s: %s", kb_slug, e)
        vec_docs = []

    # ---- 关键词路 ----
    from . import keyword_index
    kw_rows = keyword_index.search(kb_slug, query, limit=recall)

    # ---- RRF 融合（同一路内重复文本只计首次——语料里存在内容相同的 chunk） ----
    fused: dict[str, dict[str, Any]] = {}
    seen_vec: set[str] = set()
    for rank, (doc, _sim) in enumerate(vec_docs, start=1):
        meta = doc.metadata or {}
        key = _fuse_key(doc.page_content)
        if key in seen_vec:
            continue
        seen_vec.add(key)
        item = fused.setdefault(key, {
            "text": doc.page_content, "source": meta.get("source", ""),
            "section": meta.get("section") or meta.get("drug_name") or "",
            "score": 0.0, "via": set(),
        })
        item["score"] += 1.0 / (RRF_K + rank)
        item["via"].add("vec")
    seen_kw: set[str] = set()
    for rank, row in enumerate(kw_rows, start=1):
        key = _fuse_key(row["text"])
        if key in seen_kw:
            continue
        seen_kw.add(key)
        item = fused.setdefault(key, {
            "text": row["text"], "source": row["source"], "section": row["section"],
            "score": 0.0, "via": set(),
        })
        item["score"] += 1.0 / (RRF_K + rank)
        item["via"].add("kw")

    results = sorted(fused.values(), key=lambda r: r["score"], reverse=True)[:k]

    # ---- 重排序（可选）：RRF 融合序取前若干 → cross-encoder 精排 → 取 top_k。
    # 失败/未启用自动降级用融合序，不阻断。----
    from . import rerank as rerank_mod
    rr_cfg = rerank_mod.rerank_settings()
    if rr_cfg["enabled"] and len(results) > 1:
        candidates = sorted(fused.values(), key=lambda r: r["score"], reverse=True)[:max(k * 2, 8)]
        rr = rerank_mod.rerank(query, [c["text"] for c in candidates], top_n=k)
        if rr:
            reranked = []
            for it in rr:
                idx = it.get("index")
                if idx is not None and 0 <= idx < len(candidates):
                    item = dict(candidates[idx])
                    via = item["via"]
                    via = "+".join(sorted(via)) if isinstance(via, set) else str(via)
                    item["via"] = via + "+rr"
                    item["rerank_score"] = round(it.get("relevance_score", 0.0), 4)
                    reranked.append(item)
            if reranked:
                results = reranked[:k]

    # set 不能 JSON 序列化 → 转字符串（vec / kw / vec+kw）
    for r in results:
        if isinstance(r.get("via"), set):
            r["via"] = "+".join(sorted(r["via"]))

    # 文件名 → doc_id 映射（来源出处链接需要 doc_id 指向查看页）。
    name_to_id: dict[str, str] = {}
    try:
        from .models import Document
        for d in Document.objects.filter(kb__slug=kb_slug).values("original_name", "id"):
            name_to_id[d["original_name"]] = str(d["id"])
    except Exception:
        pass

    for r in results:
        r["doc_id"] = name_to_id.get(r["source"], "")
    return results


def is_kb_ready(kb_slug: str) -> bool:
    """该 KB 是否已有向量。"""
    try:
        return get_kb_vectorstore(kb_slug)._collection.count() > 0  # noqa: SLF001
    except Exception:
        return False


def search_folder(child_slugs: list[str], query: str, k: int | None = None) -> list[dict[str, Any]]:
    """跨多个子文档库扇出检索：对每个子库各取 top_k，合并后按 score 排序取前 k。

    用于文件夹级搜索（通用问题跨所有子文档库）。
    """
    k = k or retrieval_settings()["top_k"]
    all_results: list[dict[str, Any]] = []
    for slug in child_slugs:
        try:
            all_results.extend(search(slug, query, k=k))
        except Exception:
            continue  # 某个子库缺失/出错不阻断整体
    # 分桶排序：有精排分的（桶 0，按绝对相关度）永远排在无精排分的（桶 1，
    # 按 RRF 分）之前——避免 0~1 的 rerank 分与 ~0.03 的 RRF 分跨量纲直接比较，
    # 否则部分子库 rerank 瞬时失败时其结果会被系统性错位。
    def _merge_key(r):
        rs = r.get("rerank_score")
        return (0, rs) if rs is not None else (1, r.get("score", 0))

    all_results.sort(key=_merge_key, reverse=True)
    return all_results[:k]


def fetch_doc(
    kb_slug: str,
    source: str = "",
    section: str = "",
    contains: str = "",
    limit: int = 40,
) -> list[dict[str, Any]]:
    """按元数据/内容条件提取一个文档库的片段（不做相似度检索）。

    用于「取完整表格/列表」这类需求：相似度检索只返回 top_k 片段，会漏掉同一张表
    被切到其它块里的行；这里按 source/section 元数据 + 内容关键词一次性取出全部命中片段，
    按入库顺序（即文档原序）返回，便于把碎片拼回完整内容。

    参数:
        source: 限定文档文件名（不传则该库下所有文档）。
        section: 限定章节名（切块时按 ## 标题提取；不传则所有章节）。
        contains: 只保留正文含该子串的片段（用于跨章节/section 元数据缺失的表格，
                  如 contains="受力部件" 可抓全一张散落多块的表）。
        limit: 最多返回片段数（控制 token 用量，默认 40）。

    返回: [{"text","source","section"}, ...]，按入库顺序。
    """
    vs = get_kb_vectorstore(kb_slug)
    where: dict[str, str] = {}
    if source:
        where["source"] = source
    if section:
        where["section"] = section

    # 注意：limit 是对【最终输出】的上限，不能下推给 Chroma 的 limit。
    # 因为 contains 是取出后才做的过滤，若先 limit=N 再过滤，会把目标片段
    # 排在 N 之后的情况漏掉（整本文档可能几百块，关键词散落在各处）。
    # 所以先全量取（仅按 where 过滤），再用 contains 过滤，最后截断到 limit。
    res = vs._collection.get(  # noqa: SLF001  与 views.py 的 delete(where=) 同一模式
        where=where or None,
        include=["documents", "metadatas"],
    )
    out: list[dict[str, Any]] = []
    for doc, meta in zip(res.get("documents") or [], res.get("metadatas") or []):
        text = doc or ""
        if contains and contains not in text:
            continue
        m = meta or {}
        out.append({
            "text": text,
            "source": m.get("source", ""),
            # 优先 section，回退旧向量的 drug_name
            "section": m.get("section") or m.get("drug_name") or "",
        })
    return out[:limit]
