from __future__ import annotations

"""从已读表格构建分级判定组合，并保留跨表定义与各自适用说明。"""

import hashlib
import re
from collections import defaultdict


def read_html_tables(fetched: dict, ordered_ids: list[str], document) -> list:
    """用原始 HTML 恢复行列，只暴露已经在相邻索引片段中读到的内容。"""
    from .knowledge_tables import ReadTable, SourceLine
    from .pipeline import _md_for_embedding
    from .table_structure import table_grid
    result=[]
    for match in re.finditer(r'<table\b[^>]*>.*?</table\s*>',document.md_content,re.I|re.S):
        before=document.md_content[:match.start()].splitlines()
        caption=next((line.strip() for line in reversed(before[-12:]) if re.match(r'^\s*表\s*\S+',line)),None)
        if not caption:continue  # An unanchored table must not borrow another table's chunk.
        anchors=[(cid,i) for cid,chunk in fetched.items() for i,line in enumerate(chunk['text'].splitlines()) if line.strip()==caption]
        if len(anchors)!=1:continue
        cid,start=anchors[0]
        table_id='html_'+hashlib.sha256(f'{document.pk}:{match.start()}'.encode()).hexdigest()[:16]
        try:
            grid,raw_rows=table_grid(match.group())
            rendered=_md_for_embedding(match.group()).splitlines()
        except ValueError as exc:
            result.append(ReadTable(table_id,SourceLine(caption,cid),[],issue=str(exc),format='html'));continue
        if not grid or len(rendered)!=len(grid):continue
        grid=[[_md_for_embedding(value) for value in row] for row in grid]
        width=len(grid[0]);first_index=ordered_ids.index(cid)
        lines=[SourceLine(line,cid) for line in fetched[cid]['text'].splitlines()[start+1:]]
        last_index=first_index
        while last_index+1<len(ordered_ids) and ordered_ids[last_index+1] in fetched:
            last_index+=1;next_id=ordered_ids[last_index]
            lines.extend(SourceLine(line,next_id) for line in fetched[next_id]['text'].splitlines())
        table=ReadTable(table_id,SourceLine(rendered[0],cid),grid[0],caption=SourceLine(caption,cid),format='html')
        table.header_depth=max(cell['rowspan'] for cell in raw_rows[0])
        local=fetched[cid]['text'].splitlines()[:start]
        section=next((line for line in reversed(local) if re.match(r'^#{1,6}\s',line)),None)
        if section:table.section=SourceLine(section,cid)
        # Preserve nearby prose as context, never reinterpret it as universal AND.
        for line in reversed(local):
            if re.match(r'^\s*(?:表\s*\S+|#{1,6}\s)',line):break
            if len(line)>=8 and ' | ' not in line and not line.lstrip().startswith('|'):
                table.notes.insert(0,SourceLine(line,cid))
        cursor=0
        caption_line=max(i for i,line in enumerate(before) if line.strip()==caption)
        prelude=[line for line in _md_for_embedding('\n'.join(before[caption_line+1:])).splitlines() if line.strip()]
        prelude_end=None
        for wanted in prelude:
            while cursor<len(lines) and not lines[cursor].text.strip():cursor+=1
            if cursor>=len(lines) or lines[cursor].text.strip()!=wanted.strip():
                table.issue='表格标题后的单位或说明与已读片段不一致，不能省略。';break
            prelude_end=lines[cursor];cursor+=1
        if prelude_end:
            # Short unit lines need their adjacent caption to form a reliable,
            # continuous quote; never drop "单位为毫米" because it is short.
            if prelude_end.chunk_id!=cid:
                table.issue='单位说明跨越片段，需检查完整表头。'
            else:
                text=fetched[cid]['text'];left=text.find(caption);right=text.find(prelude_end.text,left)+len(prelude_end.text)
                table.notes.append(SourceLine(text[left:right],cid))
        if table.issue:result.append(table);continue
        for index,wanted in enumerate(rendered):
            while cursor<len(lines) and (not lines[cursor].text.strip() or lines[cursor].text.lstrip().startswith('#')):cursor+=1
            if cursor>=len(lines):
                if last_index+1<len(ordered_ids):table.needs_chunk=ordered_ids[last_index+1]
                else:table.issue='HTML 表格与当前索引内容不完整对应，请检查原文或重建索引。'
                break
            source=lines[cursor]
            if source.text.strip()!=wanted.strip():
                table.issue='HTML 表格与已读索引行不一致，不能猜测行列对应关系。';break
            cursor+=1
            if index==0:table.header=source;continue
            cells=raw_rows[index]
            if len(cells)==1 and cells[0]['colspan']==width and cells[0]['rowspan']==1:
                table.notes.append(source)
            else:table.rows.append((source,grid[index]))
        if not table.rows and not table.needs_chunk and not table.issue:table.issue='没有完整的数据行。'
        result.append(table)
    return result


def _pack_quotes(sources: list, fetched: dict) -> list[dict]:
    """同一片段合并为连续引文，避免多级表头与多表规则丢失依据。"""
    grouped=defaultdict(list)
    for source in sources:
        text=fetched[source.chunk_id]['text'];position=text.find(source.text)
        if position<0:raise ValueError('规则引用不在已读正文中。')
        grouped[source.chunk_id].append((position,position+len(source.text)))
    quotes=[]
    for cid,spans in grouped.items():
        spans=sorted(set(spans));start,end=spans[0]
        for left,right in spans[1:]:
            if max(right,end)-start<=2000:end=max(right,end)
            else:
                quotes.append({'chunk_id':cid,'quote':fetched[cid]['text'][start:end]});start,end=left,right
        quotes.append({'chunk_id':cid,'quote':fetched[cid]['text'][start:end]})
    if len(quotes)>9 or any(not 8<=len(item['quote'])<=2000 for item in quotes):
        raise ValueError('规则的完整证据超过本次引用预算，请缩小范围。')
    return quotes


def build_matrix_rules(table, fetched: dict, document, *, object_name: str, property_name: str,
                       object_type: str='concept', header_rows: int=1,
                       row_parameter: str='', column_parameter: str='', bindings=None,
                       scope=None, context_chunk_ids=None, ordered_ids=None) -> list[dict]:
    """矩阵每个单元格是 AND 组合，同一结果的多个单元格是 OR；不执行规则。"""
    from .knowledge_tables import SourceLine, read_tables
    from .knowledge_hierarchy import normalized_text, validate_assertions
    if table.issue:raise ValueError(table.issue)
    if table.needs_chunk:raise ValueError('请先补读表格的相邻片段：'+table.needs_chunk)
    if not isinstance(scope or {},dict):raise ValueError('适用范围必须是对象。')
    if context_chunk_ids is not None and (not isinstance(context_chunk_ids,list) or len(context_chunk_ids)>3):
        raise ValueError('补充标题最多3个已读片段。')
    if not isinstance(header_rows,int) or isinstance(header_rows,bool) or not 1<=header_rows<=4:
        raise ValueError('表头行数必须在1到4之间。')
    all_rows=[(table.header,table.columns),*table.rows]
    if header_rows>=len(all_rows):raise ValueError('表头之后没有数据行。')
    if table.header_depth>header_rows:raise ValueError('不能把合并表头作为数据行，请检查 header_rows。')
    headings=all_rows[:header_rows];body=all_rows[header_rows:]
    if not row_parameter:row_parameter=next((cells[0] for _,cells in headings if cells[0]),'')
    if not column_parameter:
        values=set(headings[0][1][1:])
        if header_rows>1 and len(values)==1:column_parameter=next(iter(values))
    if not row_parameter or not column_parameter or row_parameter==column_parameter:
        raise ValueError('请明确原文中的行条件名称 row_parameter 和列条件名称 column_parameter。')
    header_text='\n'.join(source.text for source,_ in headings)
    if any(normalized_text(name) not in normalized_text(header_text) for name in (row_parameter,column_parameter)):
        raise ValueError('两个条件名称必须得到当前表头支持。')
    columns=headings[-1][1][1:];labels=[cells[0] for _,cells in body]
    if any(not label or label in {'—','-'} for label in [*columns,*labels]) or len(set(columns))!=len(columns) or len(set(labels))!=len(labels):
        raise ValueError('判定矩阵的行列条件为空或重复。')
    if sum(len(cells)-1 for _,cells in body)>160:raise ValueError('判定矩阵超过160个条件组合，请缩小范围。')
    available={item.id:item for item in read_tables(fetched,ordered_ids or list(fetched),document)}
    if bindings is None:bindings=[]
    if not isinstance(bindings,list) or len(bindings)>6:raise ValueError('关联定义表最多6张。')
    anchors=[table.header,*([table.caption] if table.caption else []),*([table.section] if table.section else [])]
    if normalized_text(object_name) not in normalized_text('\n'.join(line.text for line in anchors)):
        numbered=re.match(r'^\s*#{1,6}\s+(\d+(?:\.\d+)*)(?:\s|$)',table.section.text) if table.section else None
        supported=False
        for cid in context_chunk_ids or []:
            if cid not in fetched:raise ValueError('补充标题必须来自已读片段。')
            for line in fetched[cid]['text'].splitlines():
                ancestor=re.match(r'^\s*#{1,6}\s+(\d+(?:\.\d+)*)(?:\s|$)',line)
                if numbered and ancestor and numbered[1].startswith(ancestor[1]+'.') and normalized_text(object_name) in normalized_text(line):supported=True
        if not supported:raise ValueError('矩阵标题或所属章节不能支持该主体；不能借其它材料章节改名。')
    def definition(parameter,label):
        definitions=[];evidence=[]
        for binding in bindings:
            if not isinstance(binding,dict) or binding.get('parameter') not in (row_parameter,column_parameter):
                raise ValueError('关联表的 parameter 必须是本矩阵的行或列条件。')
            if binding['parameter']!=parameter:continue
            if 'quote' in binding:
                if normalized_text(binding.get('entry',''))!=normalized_text(label):continue
                cid=binding.get('source_chunk_id');quote=binding['quote']
                if (cid not in fetched or not isinstance(quote,str) or not 8<=len(quote)<=2000
                    or quote not in fetched[cid]['text'] or normalized_text(label) not in normalized_text(quote)):
                    raise ValueError('正文定义必须逐字来自已读片段并明确包含对应分类名称。')
                notes=[]
                context=binding.get('context_quotes',[])
                if not isinstance(context,list) or len(context)>3:raise ValueError('正文定义最多3段适用条件。')
                for item in context:
                    if (not isinstance(item,dict) or item.get('chunk_id') not in fetched
                        or not isinstance(item.get('quote'),str) or not 8<=len(item['quote'])<=2000
                        or item['quote'] not in fetched[item['chunk_id']]['text']):raise ValueError('正文定义的适用条件必须来自已读原文。')
                    notes.append(item['quote']);evidence.append(SourceLine(item['quote'],item['chunk_id']))
                definitions.append({'parameter':parameter,'value':quote,**({'source_notes':notes} if notes else {})})
                evidence.append(SourceLine(quote,cid));continue
            target=available.get(binding.get('table_id'))
            if target is None or target.id==table.id or target.issue or target.needs_chunk or target.header_depth!=1:
                raise ValueError('关联定义表不可用；只能引用已读、完整且表头明确的其它表格。')
            key=binding.get('match_column');values=binding.get('value_columns')
            if (not isinstance(key,int) or isinstance(key,bool) or not 0<=key<len(target.columns)
                or not isinstance(values,list) or not values or len(values)>8
                or any(not isinstance(i,int) or isinstance(i,bool) or not 0<=i<len(target.columns) or i==key for i in values)):
                raise ValueError('请指定关联表的 match_column 和 value_columns（从0开始）。')
            matches=[(source,cells) for source,cells in target.rows if normalized_text(cells[key])==normalized_text(label)]
            if len(matches)>1:raise ValueError('关联定义表中分类名称重复，不能自动绑定：'+label)
            if not matches:continue
            source,cells=matches[0]
            notes=[note.text for note in target.notes]
            context=binding.get('context_quotes',[])
            if not isinstance(context,list) or len(context)>3:raise ValueError('关联表最多3段额外适用条件。')
            for item in context:
                if (not isinstance(item,dict) or item.get('chunk_id') not in fetched
                    or not isinstance(item.get('quote'),str) or not 8<=len(item['quote'])<=2000
                    or item['quote'] not in fetched[item['chunk_id']]['text']):raise ValueError('关联表适用条件必须来自已读原文。')
                notes.append(item['quote']);evidence.append(SourceLine(item['quote'],item['chunk_id']))
            for index in values:
                definitions.append({'parameter':target.columns[index],'value':cells[index],**({'source_notes':notes} if notes else {})})
            evidence.extend([source,target.header,*target.notes])
            if target.caption:evidence.append(target.caption)
            if target.section:evidence.append(target.section)
        if any(binding['parameter']==parameter for binding in bindings) and not definitions:
            raise ValueError('没有找到“'+label+'”的判定定义；请补读正文并通过 entry/source_chunk_id/quote 关联该分类，不可猜测阈值。')
        return definitions,evidence
    grouped=defaultdict(list);proof=defaultdict(list)
    for source,cells in body:
        if len(cells)!=len(table.columns):raise ValueError('判定矩阵行列不齐。')
        for index,outcome in enumerate(cells[1:]):
            if outcome in {'','—','–','-'}:continue
            terms=[];sources=[source,*[line for line,_ in headings],*table.notes]
            if table.caption:sources.append(table.caption)
            if table.section:sources.append(table.section)
            for parameter,label in ((row_parameter,cells[0]),(column_parameter,columns[index])):
                definitions,related=definition(parameter,label)
                terms.append({'parameter':parameter,'value':label,**({'definitions':definitions} if definitions else {})})
                sources.extend(related)
            grouped[outcome].append({'all_of':terms});proof[outcome].extend(sources)
    rows=[]
    for grade,combinations in grouped.items():
        sources=proof[grade]
        for cid in context_chunk_ids or []:
            if cid not in fetched:raise ValueError('补充标题必须来自已读片段。')
            for line in fetched[cid]['text'].splitlines():
                if (8<=len(line)<=2000 and (normalized_text(object_name) in normalized_text(line) or normalized_text(property_name) in normalized_text(line))):
                    sources.append(SourceLine(line,cid));break
        quotes=_pack_quotes(sources,fetched);primary,*support=quotes
        rows.append({'object':{'name':object_name,'type':object_type},
            'path':[{'name':property_name,'kind':'property'},{'name':grade,'kind':'entry'},{'name':'判定条件','kind':'group'}],
            'value':{'type':'rule','content':{'any_of':combinations}},
            'scope':{'standard':document.original_name,**(scope or {})},
            'conditions':{'source_notes':[note.text for note in table.notes]} if table.notes else {},
            'source_chunk_id':primary['chunk_id'],'quote':primary['quote'],'supporting_quotes':support})
    accepted,rejected=validate_assertions({'assertions':rows},fetched,document=document)
    if rejected:raise ValueError('判定规则的对象、表头、分类或关联定义没有通过原文校验，本表未保存。')
    return accepted


def validate_rule(rule, source: str, depth: int=0) -> None:
    """验证逻辑树和全部叶项的逐字依据；不把分类结果当作已证实的推理。"""
    from .knowledge_hierarchy import normalized_text
    if depth>12 or not isinstance(rule,dict):raise ValueError('判定规则结构无效或嵌套过深')
    operators=set(rule)&{'any_of','all_of'}
    if operators:
        if len(rule)!=1:raise ValueError('每个逻辑组只能使用一个 AND 或 OR')
        items=rule[next(iter(operators))]
        if not isinstance(items,list) or not 1<=len(items)<=160:raise ValueError('判定条件组为空或过大')
        for item in items:validate_rule(item,source,depth+1)
        return
    if not {'parameter','value'}<=rule.keys() or not rule.keys()<={'parameter','value','definitions','source_notes'}:
        raise ValueError('判定叶项必须包含参数和原文要求')
    for key in ('parameter','value'):
        if not isinstance(rule[key],str) or not rule[key].strip() or normalized_text(rule[key]) not in normalized_text(source):
            raise ValueError('判定参数或要求缺少逐字依据')
    notes=rule.get('source_notes',[])
    if not isinstance(notes,list) or len(notes)>12:raise ValueError('关联定义说明结构无效')
    for note in notes:
        if not isinstance(note,str) or normalized_text(note) not in normalized_text(source):raise ValueError('关联定义说明缺少依据')
    definitions=rule.get('definitions',[])
    if not isinstance(definitions,list) or len(definitions)>8:raise ValueError('关联定义结构无效')
    for item in definitions:validate_rule(item,source,depth+1)


def rule_text(rule: dict) -> str:
    """用于列表和问答快照的完整逻辑说明，不求值也不省略关联条件。"""
    for key,label in [('any_of','满足任一组合'),('all_of','同时满足')]:
        if key in rule:return label+'（'+'；'.join(rule_text(item) for item in rule[key])+'）'
    text=rule['parameter']+'：'+rule['value']
    if rule.get('definitions'):text+='〔关联判定依据：'+'；'.join(rule_text(item) for item in rule['definitions'])+'〕'
    if rule.get('source_notes'):text+='〔相关原文说明：'+'；'.join(rule['source_notes'])+'〕'
    return text
