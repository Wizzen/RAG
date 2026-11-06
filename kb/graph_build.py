from __future__ import annotations

"""全篇分批扫描、逐批查漏并保存候选；覆盖率表示处理范围，不是真值证明。"""

import json

from .graph_progress import GraphProgress
from .knowledge_graph import PREDICATES, normalize_identifier
from .models import Document, GraphExtraction
from .publication import digest

BATCH_CHARS = 2400
BATCH_CHUNKS = 8


class SourceChanged(ValueError):
    """生成过程中原始文档已发生变化，停止剩余调用。"""


def plan_batches(chunks: list[dict]) -> list[list[dict]]:
    """按原文顺序覆盖每个片段；长表切段但保留实际表头及重叠。"""
    batches, batch, size = [], [], 0
    for chunk in chunks:
        text = chunk['text']
        pieces, start = [], 0
        while start < len(text):
            pieces.append({**chunk,'text':text[start:start+BATCH_CHARS], 'header_context':text[:400] if start else ''})
            if start+BATCH_CHARS >= len(text):
                break
            start += BATCH_CHARS-160
        for piece in pieces:
            if batch and (len(batch) >= BATCH_CHUNKS or size + len(piece['text']) > BATCH_CHARS
                          or any(c['chunk_id']==piece['chunk_id'] for c in batch)):
                batches.append(batch); batch, size = [], 0
            batch.append(piece); size += len(piece['text'])
    if batch:
        batches.append(batch)
    return batches


def fact_key(row: dict) -> str:
    """同主体、谓词、客体及条件合并；不同条件或等级必须独立。"""
    return json.dumps([row['subject']['type'],normalize_identifier(row['subject']['name']),row['predicate'],
                       row['object']['type'],normalize_identifier(row['object']['name']),row.get('conditions',{})],
                      sort_keys=True,ensure_ascii=False)


def _compact(rows: list[dict]) -> list[dict]:
    return [{key:row[key] for key in ('subject','predicate','object','conditions') if key in row} for row in rows]


def build_document_graph(model, job: GraphExtraction, progress: GraphProgress) -> None:
    """全部正文逐批抽取与独立查漏；单批失败保留候选并报告未完成范围。"""
    from .graph_extraction import extract_focused, persist_relations
    from .keyword_index import get_chunks
    from .provenance import annotate_results
    chunks = [c for c in get_chunks(job.document.kb.slug,job.document.original_name) if c.get('chunk_id') and c.get('text')]
    if not chunks:
        raise ValueError('文档尚无可定位的正文片段，请先完成索引。')
    annotate_results(chunks)
    batches = plan_batches(chunks)
    source_ids = {c['chunk_id'] for c in chunks}
    indices = {c['chunk_id']:i for i,c in enumerate(chunks)}
    entries = [{'index':i+1,'status':'pending','chunk_ids':list(dict.fromkeys(c['chunk_id'] for c in batch)),
                'sections':list(dict.fromkeys((c.get('section') or c['text'][:45])[:120] for c in batch))}
               for i,batch in enumerate(batches)]
    seen, errors = set(), []
    job.done, job.total, job.rejected, job.created_relations = 0,len(batches),0,0

    def coverage() -> None:
        complete_ids = {cid for cid in source_ids if all(entry['status']=='complete' for entry in entries if cid in entry['chunk_ids'])}
        job.coverage = {'mode':'systematic','chunks_total':len(source_ids),'chunks_done':len(complete_ids),
                        'batches_total':len(batches),'batches_done':job.done,'audited_batches':job.done,'batches':entries}
        job.save(update_fields=['coverage','done','total','created_relations','rejected','updated_at'])
        progress.emit('coverage', **job.coverage, message=f'已检查 {len(complete_ids)}/{len(source_ids)} 个正文片段，完成 {job.done}/{len(batches)} 批抽取与查漏。')

    def check_source() -> None:
        current = Document.objects.filter(pk=job.document_id).values('md_content','status','kb_id').first()
        if not current or current['status']!='completed' or current['kb_id']!=job.document.kb_id or digest(current['md_content'])!=job.source_digest:
            raise SourceChanged('文档已变化，已停止生成；尚未检查的范围不能视为完成。')

    def save_page(rows: list[dict], rejected: int) -> None:
        new_rows = []
        page_keys = set()
        for row in rows:
            key = fact_key(row)
            if key not in seen and key not in page_keys:
                new_rows.append(row); page_keys.add(key)
        created = persist_relations(job.document,job.source_digest,rows,extraction=job,merge_facts=True)
        seen.update(fact_key(row) for row in rows)
        job.created_relations += created; job.rejected += rejected
        job.save(update_fields=['created_relations','rejected','updated_at'])
        for row in new_rows:
            progress.emit('candidate', subject=row['subject']['name'],predicate=PREDICATES.get(row['predicate'],row['predicate']),
                          object=row['object']['name'],conditions=row.get('conditions',{}),quote=row['quote'],chunks=row.get('_chunks',[]))

    def process(primary: list[dict], label: str, depth: int = 0) -> None:
        check_source()
        positions = [indices[c['chunk_id']] for c in primary]
        context = [chunks[i] for i in (min(positions)-1,max(positions)+1) if 0 <= i < len(chunks)]
        provided = {c['chunk_id']:c for c in context}
        provided.update({c['chunk_id']:c for c in primary})
        initial = list(provided.values())
        try:
            progress.emit('context',message=f'{label}，逐一抽取本批 {len(primary)} 个正文片段。',chunks=[
                {'chunk_id':c['chunk_id'],'page':c.get('page_label',''),'text':c['text'][:500]} for c in primary])
            rows,_ = extract_focused(model,job.document,job.focus,progress,document_chunks=chunks,initial_chunks=initial,
                                     pass_label=label+' · 抽取 · ',primary_chunk_ids=[c['chunk_id'] for c in primary],on_page=save_page)
            check_source()
            progress.emit('phase',stage='validate',message=f'{label}正在逐行查漏，包括表格、等级、阈值和适用条件。')
            extract_focused(model,job.document,job.focus,progress,document_chunks=chunks,initial_chunks=initial,
                            prior_relations=_compact(rows),pass_label=label+' · 查漏 · ',
                            primary_chunk_ids=[c['chunk_id'] for c in primary],on_page=save_page)
        except Exception as exc:
            retryable = isinstance(exc,(TimeoutError,ValueError)) or type(exc).__name__ in ('APITimeoutError','ReadTimeout','ConnectTimeout')
            if isinstance(exc,SourceChanged):
                raise
            if retryable and depth < 2 and (len(primary)>1 or len(primary[0]['text'])>900):
                progress.emit('notice',message=f'{label}输出截断、格式异常或超时，正在缩小范围重试；已保存候选保持待确认。')
                if len(primary)>1:
                    middle=len(primary)//2; left,right=primary[:middle],primary[middle:]
                else:
                    chunk=primary[0]; middle=len(chunk['text'])//2
                    left=[{**chunk,'text':chunk['text'][:middle+80]}]
                    right=[{**chunk,'text':chunk['text'][middle-80:],'header_context':chunk.get('header_context') or chunk['text'][:400]}]
                process(left,label+'（前半）',depth+1); process(right,label+'（后半）',depth+1)
            else:
                raise

    coverage()
    progress.emit('notice',message=f'全篇扫描 {len(chunks)} 个正文片段，分为 {len(batches)} 批；每批抽取后另做一次查漏，候选逐批保存。')
    for entry,batch in zip(entries,batches):
        entry['status']='running'; coverage()
        try:
            process(batch,f'第 {entry["index"]}/{len(batches)} 批')
            entry['status']='complete'; job.done += 1
        except Exception as exc:
            entry['status']='failed'; entry['error']=str(exc)[:500]; errors.append(str(exc)[:500])
            progress.emit('notice',message=f'第 {entry["index"]} 批未完成：{str(exc)[:300]}。已保存候选可查看，其余批次继续检查。')
            if isinstance(exc,SourceChanged) or type(exc).__name__ in ('AuthenticationError','PermissionDeniedError','NotFoundError'):
                break
        coverage()
    coverage()
    job.status='failed' if errors else 'completed'
    job.error='；'.join(dict.fromkeys(errors))[:1000]
    if errors:
        progress.emit('failed',message=job.error)
    else:
        progress.emit('complete',created=job.created_relations,rejected=job.rejected,
                      message=f'全部 {len(source_ids)} 个正文片段已扫描并查漏，保存 {job.created_relations} 条待确认关系；这表示扫描范围完成，不保证语义无遗漏。')
    job.save(update_fields=['status','error','updated_at'])
