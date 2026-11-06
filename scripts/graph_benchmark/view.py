"""Self-contained, escaped report of the experimental hierarchy candidates."""
from __future__ import annotations

import html
import json
from pathlib import Path
import re


def escape(value):
    return html.escape(str(value), quote=True)


def math_text(value):
    """Display source formulas legibly; keep the quoted evidence unchanged."""
    text=str(value)
    for source,target in [(r'\leqslant','≤'),(r'\geqslant','≥'),(r'\leq','≤'),(r'\geq','≥'),(r'\phi','φ')]:
        text=text.replace(source,target)
    return re.sub(r'\\(?:text|mathrm)\{([^{}]*)\}',r'\1',text)


def content(value):
    if isinstance(value, dict):
        return '<dl>'+''.join('<dt>'+escape(key)+'</dt><dd>'+content(item)+'</dd>' for key,item in value.items())+'</dl>'
    if isinstance(value, list):
        return '<ul>'+''.join('<li>'+content(item)+'</li>' for item in value)+'</ul>'
    return escape(math_text(value))


def tree(rows):
    roots = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('object'), dict): continue
        name = row['object'].get('name', '结构无效')
        branch = roots.setdefault(name, {'children': {}, 'values': []})
        for part in row.get('path', []):
            if not isinstance(part, dict): continue
            key = (part.get('kind',''), part.get('name',''))
            branch = branch['children'].setdefault(key, {'children': {}, 'values': []})
        branch['values'].append(row)
    def render(name, branch, depth=0):
        result = '<details'+(' open' if depth<2 else '')+'><summary>'+escape(name)+'</summary>'
        for row in branch['values']:
            value = row.get('value') or {}
            result += '<article><strong>内容 / 取值</strong><div class="value">'+escape(value.get('operator',''))+content(value.get('content',''))+' '+escape(value.get('unit',''))+'</div>'
            if row.get('conditions'): result += '<div class="conditions"><strong>适用条件</strong>'+content(row['conditions'])+'</div>'
            result += '<details class="evidence"><summary>查看原文依据</summary><blockquote>'+escape(row.get('quote',''))+'</blockquote>'
            for part in row.get('supporting_quotes',[]): result += '<blockquote>'+escape(part.get('quote',''))+'</blockquote>'
            result += '</details></article>'
        for (_,label), child in branch['children'].items(): result += render(label, child, depth+1)
        return result+'</details>'
    return ''.join(render(name, branch) for name,branch in roots.items()) or '<p>无通过现有校验的条目。</p>'


def write_report(folder, report, displays, fixture):
    root = Path(__file__).resolve().parents[2]
    css = (root/'static/css/fos.css').read_text(encoding='utf8')
    names = {'baseline':'现有方案 · 默认 thinking', 'baseline_no_thinking':'现有方案 · 关闭 thinking',
             'cognee':'原生 Cognee SDK', 'schema_pattern':'类型化建模 · 默认 thinking',
             'schema_pattern_no_thinking':'类型化建模 · 关闭 thinking', 'table_geometry':'已定位表格 · 程序解析'}
    table = '<table><thead><tr><th>实验</th><th>表9取值</th><th>等级→子参数层级</th><th>通过现有校验</th><th>请求耗时</th><th>返回的总 tokens</th></tr></thead><tbody>'
    for key,row in report['backends'].items():
        table += '<tr><td>'+escape(names[key])+'</td><td>'+str(row['raw_quality']['matched_table_cells'])+'/13</td><td>'+str(row['raw_quality']['requested_grade_then_parameter_cells'])+'/13</td><td>'+str(row['grounding']['accepted'])+'/'+str(row['raw_assertions'])+'</td><td>'+str(row['request_seconds'])+' 秒</td><td>'+str(row['usage']['total_tokens'])+'</td></tr>'
    table += '</tbody></table>'
    panels = ''
    primary = [key for key in ('baseline_no_thinking','schema_pattern_no_thinking','table_geometry') if key in displays]
    if not primary: primary=list(displays)
    for key in primary:
        item = displays[key]
        panels += '<section class="column"><h2>'+escape(names[key])+'</h2><p class="muted">实验候选，尚未人工确认</p><div class="switch"><button data-view="raw" data-panel="'+key+'">原始结构</button><button data-view="accepted" data-panel="'+key+'">通过现有校验</button></div><div id="'+key+'-raw">'+tree(item['raw'])+'</div><div id="'+key+'-accepted" hidden>'+tree(item['accepted'])+'</div></section>'
    source = '<details class="source"><summary>本次相同的5段输入正文</summary>'
    for chunk in fixture['chunks']: source += '<h3>'+escape(chunk['chunk_id'])+'</h3><pre>'+escape(chunk.get('header_context','')+'\n'+chunk['text'])+'</pre>'
    source += '</details>'
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>锻件知识结构对照</title><style>'''+css+'''
body{padding:28px}.report{max-width:1500px;margin:auto}h1{font-size:24px}h2{font-size:18px}.muted{color:var(--dim);font-size:13px}.notice{padding:16px;border-left:3px solid var(--warn);background:var(--surface);line-height:1.8}.table-wrap{overflow:auto;margin:22px 0}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:12px;text-align:left;border:1px solid var(--rule-soft)}th{background:var(--surface)}.columns{display:grid;grid-template-columns:1fr 1fr;gap:20px}.column{min-width:0;background:var(--surface);border:1px solid var(--rule-soft);padding:20px;border-radius:8px}.column details{border-left:1px solid var(--rule);padding-left:12px;margin:10px 0 10px 5px}summary{cursor:pointer;font-weight:600;overflow-wrap:anywhere}article{background:var(--paper);margin:10px 0;padding:12px;font-size:13px;line-height:1.8;overflow-wrap:anywhere}.value{font-size:15px}.conditions{margin-top:10px}dl{margin:6px 0}dt{color:var(--dim)}dd{margin:0 0 8px 12px}blockquote{white-space:pre-wrap;margin:8px 0;padding:8px;border-left:2px solid var(--rule);font-weight:400}.switch{display:flex;gap:8px;margin-bottom:16px}button{font:inherit;background:var(--paper);color:var(--text);border:1px solid var(--rule);padding:7px 12px;cursor:pointer;border-radius:5px}.source{margin-top:25px;padding:18px;border:1px solid var(--rule-soft)}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.source h3{overflow-wrap:anywhere;font-size:12px;margin-top:20px}@media(max-width:800px){body{padding:12px}.columns{grid-template-columns:1fr}.column{padding:12px}}
</style><main class="report"><h1>锻件 · 知识结构对照</h1><p class="muted">'''+escape(fixture['document'])+''' · 同一模型、同一份正文、temperature=0</p><p class="notice">类型化方案参考 Cognee 的领域建模机制，使用现有 OpenAI 客户端和 Pydantic 执行。<strong>完整 Cognee SDK 未运行，不能据此判断其框架性能或入库能力。</strong><br>四次请求中含一次超时，超时请求没有可用 token 使用量。这里只是单样本实验；两种提示也有差异，不构成框架准确率排名。</p><div class="table-wrap">'''+table+'''</div><div class="columns">'''+panels+'''</div>'''+source+'''<p class="muted">“取值覆盖”与“独立参数节点”分别计分。原文匹配、结构校验和本报告均不替代逐条人工确认。</p></main><script>document.addEventListener('click',event=>{const button=event.target.closest('[data-panel]');if(!button)return;for(const name of ['raw','accepted'])document.getElementById(button.dataset.panel+'-'+name).hidden=name!==button.dataset.view;});</script></html>'''
    page=page.replace('</style>', '.column:last-child:nth-child(odd){grid-column:1/-1}</style>',1)
    (folder/'comparison.html').write_text(page, encoding='utf8')
    viewer = folder/'view'; viewer.mkdir(exist_ok=True)
    (viewer/'index.html').write_text(page, encoding='utf8')
