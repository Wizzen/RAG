from __future__ import annotations

"""把可解释的旧关系转换为新候选；不调用模型、不继承旧审核结论。"""

from copy import deepcopy
import re

from .knowledge_graph import evidence_current, relation_queryset
from .knowledge_hierarchy import persist_assertions, validate_assertions
from .publication import digest


def convert_relations(scope_slugs, *, run=None, apply=False):
    from .keyword_index import get_chunks
    qs=relation_queryset().select_related('extraction__document__kb').filter(
        subject__kb__slug__in=scope_slugs,object__kb__slug__in=scope_slugs).exclude(status='rejected')
    if run is not None:qs=qs.filter(extraction_id=run)
    converted=skipped=created=0
    cache={}
    for relation in qs.order_by('pk'):
        sources=[evidence for evidence in relation.evidence.all() if evidence.document_id and evidence_current(evidence)]
        if not sources or relation.predicate not in {'has_parameter','graded_as','assigned_quality_grade'}:
            skipped+=1;continue
        doc=sources[0].document
        sources=[source for source in sources if source.document_id==doc.pk]
        if doc.pk not in cache:
            cache[doc.pk]=get_chunks(doc.kb.slug,doc.original_name)
        chunks=cache[doc.pk]
        located=[]
        for source in sources:
            chunk=next((chunk for chunk in chunks if source.quote in chunk['text']),None)
            if chunk:located.append((source,chunk))
        if not located:
            skipped+=1;continue
        source,primary=located[0]
        conditions=deepcopy(relation.qualifiers)
        grade=conditions.pop('quality_grade',None) or conditions.pop('质量等级',None)
        if relation.predicate in {'graded_as','assigned_quality_grade'}:grade=grade or relation.object.label
        material=conditions.pop('material',None) or conditions.pop('适用材料',None)
        name=material or relation.subject.label
        path=[]
        if grade:
            text='\n'.join(item.quote for item,_ in located)
            original=next((label for label in ('质量分级','质量等级','等级') if label in text),None)
            if original is None:
                skipped+=1;continue
            path=[{'name':'质量分级','source_name':original,'kind':'property'},
                  {'name':str(grade).strip(),'kind':'entry'}]
        if relation.predicate=='has_parameter':
            parameter=conditions.pop('parameter',relation.object.label)
            path.extend([{'name':'判定条件','kind':'group'}] if grade else [])
            path.append({'name':str(parameter),'kind':'property'})
            content=conditions.pop('value',None)
            if content is None:
                skipped+=1;continue
            numeric=isinstance(content,(float,int)) and not isinstance(content,bool)
            if isinstance(content,str) and re.fullmatch(r'-?\d+(?:\.\d+)?',content.strip()):
                content=float(content) if '.' in content else int(content);numeric=True
            value={'type':'number' if numeric else 'text','content':content,
                   'operator':conditions.pop('operator',''),'unit':conditions.pop('unit','')}
        else:
            # A source-backed long description is retained for human review.
            path.append({'name':'说明','kind':'group'})
            value={'type':'text','content':source.quote}
        row={'object':{'name':str(name),'type':'material' if material else relation.subject.kind},'path':path,'value':value,
             'scope':{'standard':doc.original_name},'conditions':conditions,'quote':source.quote,
             'source_chunk_id':primary['chunk_id'],'supporting_quotes':[
                 {'chunk_id':chunk['chunk_id'],'quote':item.quote} for item,chunk in located[1:8]]}
        accepted,rejected=validate_assertions({'assertions':[row]},{chunk['chunk_id']:chunk for _,chunk in located},document=doc)
        if not accepted:
            skipped+=1;continue
        converted+=1
        if apply:
            created+=persist_assertions(doc,digest(doc.md_content),accepted,extraction=relation.extraction,origin_relation=relation)
    return {'eligible':converted,'skipped':skipped,'created':created,'applied':apply}
