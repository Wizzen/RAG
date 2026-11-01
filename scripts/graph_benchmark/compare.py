"""Compare extraction artifacts against the frozen, manually inspected table 9.

This is a narrow regression fixture, not a general accuracy benchmark. Grounding
and correct parameter/grade/value association are checked independently.
"""
from __future__ import annotations

import argparse
from collections import Counter
import html
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def flatten_cognee(graph):
    """Resolve scoped parent references; reject cycles, duplicate keys and dangling edges."""
    rows, rejected = [], 0
    for obj in graph.get('objects', []):
        nodes = obj.get('parameters', [])
        by_key = {node['key']: node for node in nodes}
        if len(by_key) != len(nodes):
            rejected += sum(len(node.get('values', [])) for node in nodes)
            continue
        for node in nodes:
            values = node.get('values', [])
            try:
                path, current, seen = [], node, set()
                while current:
                    key = current['key']
                    if key in seen or len(path) >= 16:
                        raise ValueError('Cycle or excessive depth')
                    seen.add(key)
                    part = {'name': current['name'], 'kind': current.get('role', 'property')}
                    if current.get('source_name'): part['source_name'] = current['source_name']
                    path.insert(0, part)
                    parent = current.get('parent')
                    parent_key = parent.get('key') if isinstance(parent, dict) else parent
                    current = by_key[parent_key] if parent_key else None
            except (KeyError, TypeError, ValueError):
                rejected += len(values); continue
            for value in values:
                evidence = value.get('evidence', [])
                if not evidence:
                    rejected += 1; continue
                # Original formulas are retained as text; do not invent numeric conversions.
                content = value['text']
                kind = 'text'
                if value.get('value_type') == 'number' and re.fullmatch(r'-?\d+(?:\.\d+)?', content.strip()):
                    content = float(content) if '.' in content else int(content)
                    kind = 'number'
                primary, *support = evidence
                rows.append({'object': {'name': obj['name'], 'type': obj.get('kind', 'material')},
                    'path': path, 'value': {'type': kind, 'content': content,
                        'operator': value.get('operator', ''), 'unit': value.get('unit', '')},
                    'scope': {key: obj[key] for key in ('standard', 'method') if obj.get(key)},
                    'conditions': {'原文条件': value['conditions']} if value.get('conditions') else {},
                    'quote': primary['quote'], 'source_chunk_id': primary['chunk_id'],
                    'supporting_quotes': support})
    return rows, rejected


def recover_unique_primary_ids(rows, fetched):
    """Diagnostic only: fill a missing ID when the quote has exactly one read source.

    Never changes a provided ID, quote, scope, value, path or review state.
    Ambiguous matches remain invalid. Not installed in the production extractor.
    """
    from copy import deepcopy
    repaired, count = deepcopy(rows), 0
    for row in repaired:
        if not isinstance(row, dict) or row.get('source_chunk_id') or not isinstance(row.get('quote'), str):
            continue
        quote = row['quote']
        matches = [cid for cid, chunk in fetched.items() if quote and
                   any(quote in text for text in (chunk.get('text',''), chunk.get('header_context','')))]
        if len(matches)==1:
            row['source_chunk_id']=matches[0]; count+=1
    return repaired, count


def expression(value):
    from kb.knowledge_hierarchy import normalized_text, value_text
    text = normalized_text(value_text(value)).replace('≤', '<=').replace('≥', '>=')
    text = re.sub(r'[\s${}\\]', '', text)
    # A unit or operator copied into both the raw value and its field is only
    # display duplication; do not count it as a different physical requirement.
    text = re.sub(r'(db){2,}', 'db', text)
    return text.replace('<=<=', '<=').replace('>>', '>')


def score(rows):
    from kb.knowledge_hierarchy import node_key, value_text
    expected = [('单个缺陷', grade, value) for grade, value in zip(
        ['i','ii','iii','iv','v'], ['<=φ4','<=φ4+6db','<=φ4+12db','<=φ4+18db','>φ4+18db'])]
    expected += [('底波', grade, value) for grade, value in zip(
        ['i','ii','iii','iv','v'], ['<=6db','<=12db','<=18db','<=24db','>24db'])]
    expected += [('密集', grade, value) for grade, value in zip(
        ['iii','iv','v'], ['<=φ4+6db','<=φ4+12db','<=φ4+18db'])]
    found, grades, wrong_objects, candidates, malformed, semantic = [], set(), 0, [], 0, []
    explicit, embedded, objects, requested = set(), set(), Counter(), set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('object'), dict)
                or not isinstance(row['object'].get('name'), str) or not isinstance(row.get('value'), dict)
                or not isinstance(row.get('path'), list)
                or any(not isinstance(part, dict) or not isinstance(part.get('name'), str)
                       or not isinstance(part.get('kind'), str) for part in row['path'])):
            malformed += 1; continue
        if '锻' not in row['object']['name']:
            wrong_objects += 1; continue
        objects[row['object']['name']] += 1
        path = row['path']
        entries = {node_key(part['name'], 'entry') for part in path if part['kind']=='entry'}
        grades.update(entries)
        text = ' '.join(part['name'] for part in path)
        variants = [(text, row['value'], 'node')]
        def embedded_fields(content, parent):
            if isinstance(content, dict):
                for key, value in content.items():
                    embedded_fields(value, parent+' / '+key)
            elif isinstance(content, (str, int, float)) and not isinstance(content, bool):
                variants.append((parent, {'type': 'text', 'content': content}, 'embedded_field'))
        if row['value'].get('type')=='object':
            embedded_fields(row['value'].get('content'), text)
        semantic.append(text+' '+value_text(row['value'])+' '+json.dumps(row.get('conditions', {}), ensure_ascii=False))
        for label, value_data, representation in variants:
            actual = expression(value_data)
            candidates.append({'path': label, 'grades': sorted(entries), 'expression': actual, 'representation': representation})
            for parameter, grade, value in expected:
                if parameter in label and grade in entries and actual == value:
                    item = (parameter, grade, value)
                    found.append(item)
                    (explicit if representation=='node' else embedded).add(item)
                    grade_positions=[i for i,part in enumerate(path) if part['kind']=='entry' and node_key(part['name'],'entry')==grade]
                    parameter_positions=[i for i,part in enumerate(path) if part['kind']=='property' and parameter in part['name']]
                    if representation=='node' and grade_positions and parameter_positions and min(grade_positions)<max(parameter_positions):
                        requested.add(item)
    unique = set(found)
    combined = '\n'.join(semantic)
    return {'grade_branches': sorted(grades), 'matched_table_cells': len(unique), 'expected_table_cells': len(expected),
        'missing_table_cells': [list(item) for item in expected if item not in unique],
        'other_object_assertions': wrong_objects,
        'malformed_assertions': malformed,
        'main_objects': dict(objects),
        'explicit_parameter_cells': len(explicit), 'embedded_parameter_cells': len(embedded),
        'requested_grade_then_parameter_cells': len(requested),
        'near_field_condition_present': '近场' in combined,
        'independent_grading_present': '独立' in combined,
        'hazardous_defect_present': '危害性' in combined or '裂纹' in combined,
        'normalized_values': candidates}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', default='output/cognee-comparison')
    args = parser.parse_args()
    folder = (ROOT/args.folder).resolve()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fos_rag.settings')
    import django; django.setup()
    from kb.knowledge_hierarchy import validate_assertions
    from kb.models import Document
    fixture = json.loads((folder/'fixture.json').read_text(encoding='utf8'))
    document = Document.objects.get(pk=fixture['document_id'])
    fetched = {chunk['chunk_id']: chunk for chunk in fixture['chunks']}
    report = {'fixture': {'document': fixture['document'], 'source_digest': fixture['source_digest'],
        'focus': fixture['focus'], 'characters': sum(len(c['text']) for c in fixture['chunks'])},
        'limitations': ['One frozen excerpt fixture; no claim of general accuracy.',
            'Table-cell matching is conservative and reported with the normalized values for inspection.',
            'No graph ingestion, embeddings, production database writes, or approvals performed.'], 'backends': {}}
    displays = {}
    variants = [('baseline','baseline.json','baseline',False),
                ('baseline_no_thinking','baseline-no-thinking.json','baseline',True),
                ('cognee','cognee.json','cognee',False),
                ('schema_pattern','cognee-schema-result.json','cognee',False),
                ('schema_pattern_no_thinking','cognee-schema-result-no-thinking.json','cognee',True),
                ('table_geometry','table-geometry.json','deterministic',False)]
    for backend, filename, phase, no_thinking in variants:
        file = folder/filename
        if not file.exists(): continue
        data = json.loads(file.read_text(encoding='utf8'))
        rows, malformed = (data['assertions'], 0) if backend.startswith('baseline') or backend=='table_geometry' else flatten_cognee(data)
        accepted, rejected = validate_assertions({'assertions': rows}, fetched, document=document)
        repaired_rows, repaired_count = recover_unique_primary_ids(rows, fetched)
        repaired_accepted, _ = validate_assertions({'assertions': repaired_rows}, fetched, document=document)
        grounding = {'accepted': len(accepted), 'rejected': rejected+malformed}
        # Score all generated rows too: a strict filter must not hide extraction errors.
        report['backends'][backend] = {'raw_assertions': len(rows), 'grounding': grounding,
            'raw_quality': score(rows), 'accepted_quality': score(accepted),
            'thinking_requested': 'disabled' if no_thinking else 'provider default',
            'request_phase': phase}
        report['backends'][backend]['primary_chunk_id_missing'] = sum(not row.get('source_chunk_id') for row in rows if isinstance(row,dict))
        report['backends'][backend]['diagnostic_unique_id_recovery'] = {
            'ids_recovered': repaired_count, 'accepted_after_only_this_change': len(repaired_accepted),
            'production_implementation_changed': False}
        report['backends'][backend]['native_cognee_sdk_executed'] = backend=='cognee'
        displays[backend] = {'raw': rows, 'accepted': accepted}
        (folder/(backend+'-validated.json')).write_text(json.dumps(accepted, ensure_ascii=False, indent=2), encoding='utf8')
    ledger = folder/'requests.json'
    requests = json.loads(ledger.read_text(encoding='utf8')) if ledger.exists() else []
    report['requests'] = requests
    report['actual_model_requests'] = len(requests)
    report['failed_requests_without_usage'] = sum(call.get('status')!=200 and not call.get('usage') for call in requests)
    for backend, row in report['backends'].items():
        no_thinking = row['thinking_requested']=='disabled'
        calls = [call for call in requests if call['backend']==row['request_phase']
                 and (call.get('thinking_requested')=='disabled')==no_thinking]
        row['calls'] = len(calls)
        row['request_seconds'] = round(sum(call['seconds'] for call in calls), 2)
        row['usage'] = {key: sum((call.get('usage', {}).get(key) or 0) for call in calls)
                        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    (folder/'comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    lines = ['# 锻件分级抽取对照', '', '**样本限定：同一份正文，未执行正式图谱入库。所有输出均为未审核候选。**', '',
        '| 实现 | 原始条目 | 通过现有校验 | 原始表9匹配项 | 等级→子参数层级匹配项 | 调用次数 | 请求耗时 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for name, row in report['backends'].items():
        lines.append(f'| {name} | {row["raw_assertions"]} | {row["grounding"]["accepted"]} | '
            f'{row["raw_quality"]["matched_table_cells"]}/13 | {row["raw_quality"]["requested_grade_then_parameter_cells"]}/13 | '
            f'{row["calls"]} | {row["request_seconds"]} s |')
    if any(name.startswith('schema_pattern') for name in report['backends']):
        lines += ['', 'schema_pattern 是 Cognee 文档中的类型化领域建模方案，通过现有 OpenAI 客户端与 Pydantic 验证；',
                  '**未运行完整 Cognee SDK，不能据此声称 Cognee 的运行性能、入库或增量归并已获验证。**']
    lines += ['', '逐项值、缺失项、条件检查与实际 token 使用记录见 comparison.json。',
        '这些检查不证明语义完全正确；近场条件和独立分级须人工核对其作用范围。']
    (folder/'comparison.md').write_text('\n'.join(lines)+'\n', encoding='utf8')
    from scripts.graph_benchmark.view import write_report
    write_report(folder, report, displays, fixture)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
