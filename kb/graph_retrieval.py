from __future__ import annotations

"""图谱补查的文档级混合检索；语义向量与关键词融合，不能扩大已授权范围。"""

import logging

from . import keyword_index
from .models import Document
from .retriever import get_kb_vectorstore

logger = logging.getLogger(__name__)


def search_document(document: Document, query: str, chunks: list[dict], *,
                    allowed_ids: set[str] | None = None, limit: int = 4) -> dict:
    """先限定当前文档及本批片段，再分别召回并按 RRF 融合；只返回当前正文。"""
    canonical = {c['chunk_id']:c for c in chunks if c.get('chunk_id') and
                 (allowed_ids is None or c['chunk_id'] in allowed_ids)}
    if not canonical:
        return {'rows':[], 'method':'none', 'vector_status':'empty'}
    fused: dict[str,dict] = {}

    def add(ids: list[str], lane: str, weight: float) -> None:
        seen = set()
        for rank,cid in enumerate(ids,1):
            if cid not in canonical or cid in seen:
                continue
            seen.add(cid)
            item = fused.setdefault(cid,{'score':0.0,'lanes':set()})
            item['score'] += weight/(60+rank); item['lanes'].add(lane)

    vector_status = 'ok'
    try:
        store = get_kb_vectorstore(document.kb.slug)
        embedding = store.embeddings.embed_query(query)
        if embedding is None:
            raise ValueError('查询向量不可用')
        result = store._collection.query(  # noqa: SLF001 已安装 Chroma 原生支持 ids 与 source 的前置过滤。
            query_embeddings=[embedding],ids=list(canonical),where={'source':document.original_name},
            n_results=min(max(limit*3,8),len(canonical)),include=['metadatas'])
        ids = (result.get('ids') or [[]])[0] or []
        if not ids:
            vector_status = 'empty'
        add(ids,'vector',1.0)
    except Exception as exc:
        vector_status = 'unavailable'
        logger.warning('图谱语义检索不可用，回退关键词 kb=%s error=%s',document.kb.slug,type(exc).__name__)

    keyword_hits = keyword_index.search(document.kb.slug,query,limit=len(chunks),source=document.original_name)
    add([row.get('chunk_id','') for row in keyword_hits if row.get('chunk_id') in canonical],'keyword',1.0)
    ranked = sorted(fused,key=lambda cid:fused[cid]['score'],reverse=True)[:limit]
    rows = [{**canonical[cid],'via':'+'.join(sorted(fused[cid]['lanes']))} for cid in ranked]
    return {'rows':rows, 'method':'hybrid' if vector_status=='ok' else 'keyword', 'vector_status':vector_status}
