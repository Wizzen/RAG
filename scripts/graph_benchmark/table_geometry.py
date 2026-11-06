"""Narrow PoC: turn an ALREADY LOCATED Markdown grading table into candidates.

No retrieval or model calls. No parameter values are hardcoded. Table location
comes from the saved model result; axis orientation is explicit for this test.
Clause notes remain source text: this parser does not infer logical applicability.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys


def cells(line):
    return [part.strip() for part in line.strip().strip('|').split('|')]


def extract_table(fixture, chunk_id, object_name, property_name):
    chunks = {item['chunk_id']: item for item in fixture['chunks']}
    chunk = chunks[chunk_id]
    lines = chunk['text'].splitlines()
    blocks = []
    for i in range(len(lines)-1):
        if not lines[i].lstrip().startswith('|') or not lines[i+1].lstrip().startswith('|'): continue
        separator = cells(lines[i+1])
        if not separator or not all(re.fullmatch(r':?-+:?', part) for part in separator): continue
        header = cells(lines[i])
        captions = [line for line in lines[:i] if re.match(r'^表\s*\S+', line.strip())]
        if not captions: continue
        caption = captions[-1]
        if object_name not in caption or property_name not in caption: continue
        body=[]
        for line in lines[i+2:]:
            if not line.lstrip().startswith('|'): break
            values=cells(line)
            if len(values)!=len(header): raise ValueError('表格行列不齐，不能可靠建立对应关系。')
            body.append((line,values))
        blocks.append((i,caption,lines[i],header,body))
    if len(blocks)!=1:raise ValueError('已定位片段中没有唯一匹配的完整表格，不猜测表格。')
    start,caption,header_quote,header,body=blocks[0]
    if len(set(header[1:]))!=len(header[1:]):raise ValueError('分类表头重复，不能自动定位。')
    rows=[]
    def assertion(path,value,quote):
        return {'object':{'name':object_name,'type':'material'},
            'path':[{'name':property_name,'kind':'property'},*path],
            'value':{'type':'text','content':value},'scope':{'standard':fixture['document']},'conditions':{},
            'quote':quote,'source_chunk_id':chunk_id,'supporting_quotes':[
                {'chunk_id':chunk_id,'quote':caption},{'chunk_id':chunk_id,'quote':header_quote}]}
    for quote,row in body:
        for grade,value in zip(header[1:],row[1:]):
            if value in ('','—','–','-'):continue
            rows.append(assertion([{'name':grade,'kind':'entry'},{'name':'判定条件','kind':'group'},
                                   {'name':row[0],'kind':'property'}],value,quote))
    # Preserve all numbered clause text without making up AND/OR or extending a
    # condition to the other defect types. A reviewer sees its precise wording.
    for line in lines[:start]:
        match=re.match(r'^\d+(?:\.\d+)+\s+(.+)$',line)
        if match and len(match.group(1))>=8:
            rows.append(assertion([{'name':'说明','kind':'group'}],match.group(1),line))
    if len(rows)>200:raise ValueError('本次表格超过候选上限。')
    return rows


def main():
    root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root))
    folder=root/'output/cognee-comparison'
    fixture=json.loads((folder/'fixture.json').read_text(encoding='utf8'))
    located=json.loads((folder/'baseline-no-thinking.json').read_text(encoding='utf8'))['assertions'][0]['source_chunk_id']
    rows=extract_table(fixture,located,'锻件','质量分级')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','fos_rag.settings')
    import django;django.setup()
    from kb.knowledge_hierarchy import validate_assertions
    from kb.models import Document
    accepted,rejected=validate_assertions({'assertions':rows},
        {chunk['chunk_id']:chunk for chunk in fixture['chunks']},document=Document.objects.get(pk=fixture['document_id']))
    data={'assertions':rows,'validated_assertions':accepted,'rejected':rejected,
          'source':'existing model location + deterministic table cell geometry',
          'additional_model_calls':0,'production_writes':False,
          'limitations':['Explicit object and axis orientation for this fixture.',
                         'Notes preserve wording; automatic semantic condition binding is not evaluated.']}
    (folder/'table-geometry.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'rows':len(rows),'accepted':len(accepted),'rejected':rejected,'extra_llm_calls':0}))


if __name__=='__main__':main()
