"""Build source-backed hierarchy candidates from explicitly selected table geometry.

The model chooses the object, property, table and axis. Cell values, row/header
quotes and note text are copied from read chunks; this module performs no search
and never reads an unprovided chunk or infers logical applicability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re

MAX_CELLS = 160
MAX_CATALOG = 12
MAX_NOTES = 5


def split_cells(line):
    """Keep escaped pipes inside cells, including a pipe in a Markdown code span."""
    text = line.strip()
    if not text.startswith('|') or not text.endswith('|'):
        raise ValueError('表格缺少明确的列边界。')
    cells, part, escaped, ticks = [], [], False, False
    for char in text[1:-1]:
        if char=='`' and not escaped: ticks=not ticks
        if char=='|' and not escaped and not ticks:
            cells.append(''.join(part).strip()); part=[]
        else: part.append(char)
        escaped = char=='\\' and not escaped
    cells.append(''.join(part).strip())
    return cells


def separator(line):
    try: parts=split_cells(line)
    except ValueError: return False
    return bool(parts) and all(re.fullmatch(r':?-+:?', part) for part in parts)


@dataclass
class SourceLine:
    text: str
    chunk_id: str


@dataclass
class ReadTable:
    id: str
    header: SourceLine
    columns: list[str]
    rows: list[tuple[SourceLine, list[str]]] = field(default_factory=list)
    caption: SourceLine | None = None
    section: SourceLine | None = None
    notes: list[SourceLine] = field(default_factory=list)
    issue: str = ''
    needs_chunk: str = ''
    format: str = 'markdown'
    header_depth: int = 1

    def summary(self):
        return {'table_id':self.id,'chunk_id':self.header.chunk_id,
                'caption':self.caption.text if self.caption else '',
                'columns':self.columns[:24], 'row_labels':[cells[0] for _,cells in self.rows[:32]],
                'rows':len(self.rows),'buildable':not (self.issue or self.needs_chunk),
                'issue':self.issue,'needs_chunk':self.needs_chunk,
                'format':self.format,'suggested_header_rows':self.header_depth,
                'row_preview':[cells for _,cells in self.rows[:6]],
                'catalog_partial':len(self.columns)>24 or len(self.rows)>32}


def read_tables(fetched: dict, ordered_ids: list[str], document=None) -> list[ReadTable]:
    """Parse only already read, adjacent chunks. A boundary requires its next chunk."""
    tables, consumed = [], set()
    positions={cid:i for i,cid in enumerate(ordered_ids)}
    for cid in sorted(fetched,key=lambda value:positions.get(value,len(positions))):
        lines=fetched[cid]['text'].splitlines()
        fence=False
        for i,line in enumerate(lines):
            if line.lstrip().startswith(('```','~~~')): fence=not fence; continue
            if fence or (cid,i) in consumed or i+1>=len(lines) or not separator(lines[i+1]): continue
            try: columns=split_cells(line)
            except ValueError: continue
            table_id='table_'+hashlib.sha256(f'{cid}:{i}:{line}'.encode()).hexdigest()[:16]
            table=ReadTable(table_id,SourceLine(line,cid),columns)
            if len(columns)<2 or len(columns)!=len(split_cells(lines[i+1])):
                table.issue='表头列数不一致。'
            start=0
            for j in range(i-1,-1,-1):
                if re.match(r'^#{1,6}\s',lines[j]):
                    start=j; table.section=SourceLine(lines[j],cid); break
            for text in lines[start:i]:
                if re.match(r'^\s*(?:表\s*[A-Za-z0-9一二三四五六七八九十]|Table\s+\w)',text,re.I):
                    table.caption=SourceLine(text,cid)
                    table.notes=[]  # A later caption starts its own table context.
                elif re.match(r'^\d+(?:\.\d+)+\s+\S',text):
                    table.notes.append(SourceLine(text,cid))
            # Numbered clauses often precede the caption; keep only this section's
            # clauses, and never synthesize AND/OR or apply one rule to every row.
            if table.caption:
                caption_i=max(j for j in range(start,i) if lines[j]==table.caption.text)
                previous=[j for j in range(start,caption_i) if re.match(r'^\s*(?:表\s*\S+|Table\s+\w)',lines[j],re.I)]
                note_start=previous[-1]+1 if previous else start
                table.notes=[SourceLine(text,cid) for text in lines[note_start:caption_i]
                             if re.match(r'^\d+(?:\.\d+)+\s+\S',text)]
            current_cid, current_lines, cursor=cid,lines,i+2
            tail=None
            while True:
                while cursor<len(current_lines) and current_lines[cursor].lstrip().startswith('|'):
                    if separator(current_lines[cursor]):
                        table.issue='正文出现额外表头分隔线，需检查多级表头。'; break
                    source=SourceLine(current_lines[cursor],current_cid)
                    try: row=split_cells(source.text)
                    except ValueError as exc: table.issue=str(exc); break
                    if len(row)!=len(columns): table.issue='表格行列不齐，不能自动对应等级与取值。'; break
                    if row==columns:table.issue='正文出现重复表头，需检查续表或多级表头。';break
                    table.rows.append((source,row)); consumed.add((current_cid,cursor)); cursor+=1
                if table.issue or any(text.strip() for text in current_lines[cursor:]):
                    tail=(current_cid,current_lines,cursor);break
                index=positions.get(current_cid)
                if index is None or index+1>=len(ordered_ids): break
                next_id=ordered_ids[index+1]
                if next_id not in fetched:
                    table.needs_chunk=next_id; break
                next_lines=fetched[next_id]['text'].splitlines()
                first=next((j for j,text in enumerate(next_lines) if text.strip()),len(next_lines))
                if first==len(next_lines) or not next_lines[first].lstrip().startswith('|'):
                    tail=(next_id,next_lines,first);break
                # Only unambiguous contiguous body rows extend a table. A new
                # or repeated header needs an explicit continuation decision.
                if first+1<len(next_lines) and separator(next_lines[first+1]):
                    try: repeated=split_cells(next_lines[first])
                    except ValueError: break
                    if repeated==columns:
                        table.issue='相邻片段重复表头但缺少明确的续表标识，不能自动合并。'
                    break  # A new table, or an ambiguous repeated header.
                current_cid,current_lines,cursor=next_id,next_lines,first
            # Keep clearly attached trailing notes as source text, including
            # wrapped lines. Do not silently drop a table's applicability footnote.
            if tail:
                tail_id,tail_lines,j=tail
                section_number=re.match(r'^\s*#{1,6}\s+(\d+(?:\.\d+)*)(?:\s|$)',table.section.text) if table.section else None
                while j<len(tail_lines):
                    if not tail_lines[j].strip():j+=1;continue
                    numbered=re.match(r'^(\d+(?:\.\d+)+)\s+\S',tail_lines[j])
                    is_note=re.match(r'^\s*(?:注(?:\d+)?[：:]|Note\b)',tail_lines[j],re.I)
                    same_section=numbered and section_number and numbered[1].startswith(section_number[1]+'.')
                    if not is_note and not same_section:break
                    end=j+1
                    while (end<len(tail_lines) and tail_lines[end].strip()
                           and not re.match(r'^\s*(?:#|注(?:\d+)?[：:]|Note\b|\d+(?:\.\d+)+\s|表\s*\S+|\|)',tail_lines[end],re.I)):
                        end+=1
                    table.notes.append(SourceLine('\n'.join(tail_lines[j:end]),tail_id));j=end
            if not table.rows and not table.issue: table.issue='没有完整的数据行。'
            tables.append(table)
    if document is not None:
        from .knowledge_rules import read_html_tables
        tables.extend(read_html_tables(fetched,ordered_ids,document))
    return tables


def catalog(fetched, ordered_ids, document=None):
    tables=read_tables(fetched,ordered_ids,document)
    unsupported=[{'chunk_id':cid,'issue':'HTML、合并单元格或多级表头当前需逐条检查，不能自动按矩形表格建图。'}
                 for cid,chunk in fetched.items() if re.search(r'<table\b',chunk['text'],re.I)]
    return {'tables':[table.summary() for table in tables[:MAX_CATALOG]],
            'truncated':len(tables)>MAX_CATALOG,'unsupported':unsupported[:MAX_CATALOG]}


def _anchor_quote(chunk, name):
    from .knowledge_hierarchy import normalized_text
    for text in (chunk.get('text',''),chunk.get('header_context','')):
        for line in text.splitlines():
            if 8<=len(line)<=2000 and normalized_text(name) in normalized_text(line):
                return line
    return None


def build_candidates(table, fetched, document, *, object_name, property_name,
                     object_type='concept', entries_axis='columns', parameter_names=None,
                     scope=None, context_chunk_ids=None):
    """Return ordinary V2 candidates; complete source validation remains mandatory."""
    from .knowledge_hierarchy import node_key, normalized_text, validate_assertions
    if table.header_depth>1:raise ValueError('多级表头请显式选择 decision_matrix 布局构建判定规则。')
    if table.issue: raise ValueError(table.issue)
    if table.needs_chunk: raise ValueError('表格到达片段边界，请先补读下一片段确认是否续表：'+table.needs_chunk)
    if entries_axis not in {'columns','rows'}: raise ValueError('分类方向只能是 columns 或 rows。')
    if not isinstance(object_name,str) or not isinstance(property_name,str) or not object_name.strip() or not property_name.strip():
        raise ValueError('必须给出原文中的主体名和属性名。')
    if context_chunk_ids is None: context_chunk_ids=[]
    if (not isinstance(context_chunk_ids,list) or len(context_chunk_ids)>3
            or any(not isinstance(cid,str) or cid not in fetched for cid in context_chunk_ids)):
        raise ValueError('补充上下文必须来自本次已经读取的片段，最多3段。')
    if scope is None: scope={}
    if not isinstance(scope,dict):raise ValueError('适用范围必须是对象。')
    scope={'standard':document.original_name,**scope}
    if len(table.notes)>MAX_NOTES:raise ValueError('表格前的条款过多，无法完整保留，请按条款抽取并核对适用范围。')
    if any(not 8<=len(note.text)<=2000 for note in table.notes):
        raise ValueError('关联条款长度不在可可靠引用的范围内。')
    evidence=[table.header,*([table.caption] if table.caption else []),*table.notes]
    # An unrelated chapter heading must not relabel a table belonging to another
    # material. External object evidence is accepted only from a numbered ancestor.
    anchors=[table.header,*([table.caption] if table.caption else []),*([table.section] if table.section else [])]
    local=next((item for item in anchors if normalized_text(object_name) in normalized_text(item.text)),None)
    if local and local not in evidence:evidence.append(local)
    if local is None:
        numbered=re.match(r'^\s*#{1,6}\s+(\d+(?:\.\d+)*)(?:\s|$)',table.section.text) if table.section else None
        ancestor=None
        if numbered:
            for cid in context_chunk_ids:
                for text in (fetched[cid].get('text',''),fetched[cid].get('header_context','')):
                    for line in text.splitlines():
                        match=re.match(r'^\s*#{1,6}\s+(\d+(?:\.\d+)*)(?:\s|$)',line)
                        if (match and numbered[1].startswith(match[1]+'.')
                                and normalized_text(object_name) in normalized_text(line)):
                            ancestor=SourceLine(line,cid)
        if ancestor is None:raise ValueError('表格标题或所属章节不能明确支持该主体，请补充同一章节的标题依据；不能用其它材料的表格建图。')
        evidence.append(ancestor)
    for name in [object_name,property_name,*[value for value in scope.values() if isinstance(value,str) and value]]:
        if normalized_text(name) in normalized_text(document.original_name): continue
        if any(normalized_text(name) in normalized_text(item.text) for item in evidence): continue
        for cid in context_chunk_ids:
            quote=_anchor_quote(fetched[cid],name)
            if quote: evidence.append(SourceLine(quote,cid)); break
    evidence=list({(item.chunk_id,item.text):item for item in evidence}.values())
    if len(evidence)>8 or any(not 8<=len(item.text)<=2000 for item in evidence):
        raise ValueError('表头或关联依据超出引用预算，不能截断后自动建图。')
    if entries_axis=='columns':
        entries=table.columns[1:]
        parameters=[row[0] for _,row in table.rows]
    else:
        entries=[row[0] for _,row in table.rows]
        parameters=table.columns[1:]
    for labels,kind in [(entries,'entry'),(parameters,'property')]:
        keys=[node_key(label,kind) for label in labels]
        if any(not key or key in {'—','-'} for key in keys) or len(keys)!=len(set(keys)):
            raise ValueError('分类或参数表头为空或重复，不能可靠归并。')
    if parameter_names is not None:
        if (not isinstance(parameter_names,list) or not parameter_names or len(parameter_names)>80
                or any(not isinstance(name,str) or name not in parameters for name in parameter_names)):
            raise ValueError('请选择目录中完整的参数名：'+'、'.join(parameters[:32]))
    selected=set(parameters if parameter_names is None else parameter_names)
    rows=[]
    for source,cells in table.rows:
        for index,value in enumerate(cells[1:],1):
            entry,parameter=(table.columns[index],cells[0]) if entries_axis=='columns' else (cells[0],table.columns[index])
            if parameter not in selected or value in {'','—','–','-'}:continue
            if len(rows)>=MAX_CELLS:raise ValueError('选中的表格内容超过160项，请用 parameter_names 缩小范围。')
            rows.append({'object':{'name':object_name.strip(),'type':object_type},
                'path':[{'name':property_name.strip(),'kind':'property'},
                        {'name':entry,'kind':'entry'},{'name':parameter,'kind':'property'}],
                'value':{'type':'text','content':value},'scope':scope,
                'conditions':{'source_notes':[note.text for note in table.notes]} if table.notes else {},
                'quote':source.text,'source_chunk_id':source.chunk_id,
                'supporting_quotes':[{'chunk_id':item.chunk_id,'quote':item.text} for item in evidence]})
    accepted,rejected=validate_assertions({'assertions':rows},fetched,document=document)
    if rejected:
        raise ValueError('表格的对象、属性、表头或单元格没有通过完整原文校验，请补充真实上下文；本表未保存。')
    return accepted
