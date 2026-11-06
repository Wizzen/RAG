from __future__ import annotations

"""可组合、可重放的业务计算；仅标准库，不执行模型代码。"""

import json
import math
import re
from datetime import datetime
from decimal import Decimal,ROUND_HALF_UP,InvalidOperation

ARITY={
    'add':(2,20),'subtract':(2,2),'multiply':(2,20),'divide':(2,2),'round':(1,1),'abs':(1,1),
    'count':(1,1),'sum':(1,1),'average':(1,1),'min':(1,1),'max':(1,1),'unique':(1,1),
    'split':(1,1),'match':(1,1),'number':(1,1),'join':(1,1),'concat':(1,20),
    'eq':(2,2),'ne':(2,2),'gt':(2,2),'gte':(2,2),'lt':(2,2),'lte':(2,2),'contains':(2,2),
    'and':(2,20),'or':(2,20),'not':(1,1),'if':(3,3),'coalesce':(1,20),
    'map':(2,2),'filter':(2,2),'group_by':(1,1),'duration':(2,2),'date_part':(1,1),
}
OPTIONS={'count':{'pattern','group','distinct','separator'},'split':{'separator'},'match':{'pattern','group'},
    'round':{'digits'},'group_by':{'path','aggregate'},'join':{'separator'},'duration':{'format','unit'},'date_part':{'part'}}


def _get(data,path):
    if not path:return data
    parts=[p.replace('~1','/').replace('~0','~') for p in path[1:].split('/')] if path.startswith('/') else path.split('.')
    try:
        for part in parts:data=data[int(part)] if isinstance(data,list) and part.isdecimal() else data[part]
        return data
    except (KeyError,IndexError,ValueError,TypeError):return None


def _pattern(pattern):
    if not isinstance(pattern,str) or not 1<=len(pattern)<=300 or re.search(r'\\[1-9]|\(\?[=!<]|\([^)]*[+*{][^)]*\)[+*{]',pattern):raise ValueError('计算正则需为不超过300字符的简单模式。')
    try:return re.compile(pattern,re.I)
    except re.error as exc:raise ValueError('计算正则无效：'+str(exc)) from exc


def validate_computed(computed):
    if not isinstance(computed,dict) or 'op' not in computed:raise ValueError('computed需要op；取原字段使用{"field":"路径"}。')
    nodes=[0]
    def visit(node,depth=0):
        nodes[0]+=1
        if depth>12 or nodes[0]>200:raise ValueError('计算规则超过12层或200个节点，请拆分规则。')
        if not isinstance(node,dict):
            if isinstance(node,(list,tuple)) or not isinstance(node,(str,int,float,bool,type(None))):raise ValueError('数组或对象常量请放在literal中。')
            if isinstance(node,float) and not math.isfinite(node):raise ValueError('计算常量必须为有限数。')
            if isinstance(node,str) and len(node)>20000:raise ValueError('计算常量过长。')
            return
        if set(node)=={'field'}:
            if not isinstance(node['field'],str) or len(node['field'])>1000:raise ValueError('field必须是短字段路径。')
            return
        if set(node)=={'literal'}:
            if len(json.dumps(node['literal'],allow_nan=False))>20000:raise ValueError('literal常量过大。')
            return
        op=node.get('op')
        if op=='duration' and 'args' not in node:
            if not node.keys()<={'op','start','end','format','unit'} or any(not isinstance(node.get(k),str) or not 1<=len(node[k])<=1000 for k in ('start','end')):raise ValueError('duration需指定start/end路径或两个args。')
        else:
            if op not in ARITY:raise ValueError('不支持的计算op：'+str(op)+'；可用：'+', '.join(ARITY))
            unsupported=node.keys()-({'op','args'}|OPTIONS.get(op,set()))
            if unsupported:raise ValueError('computed.'+op+'不支持参数：'+', '.join(sorted(unsupported))+'；原字段使用args:[{"field":"实际路径"}]。')
            args=node.get('args');low,high=ARITY[op]
            if not isinstance(args,list) or not low<=len(args)<=high:raise ValueError(op+'需要'+str(low)+'至'+str(high)+'个args。')
            for arg in args:visit(arg,depth+1)
        if op=='duration':
            if node.get('format','iso8601') not in ('hhmm','iso8601','unix_seconds','unix_millis') or node.get('unit','minutes') not in ('seconds','minutes','hours','days'):raise ValueError('duration时间格式或单位无效。')
        for key in ('pattern','separator'):
            if key in node and not (op=='join' and key=='separator'):_pattern(node[key])
        if op=='match' and 'pattern' not in node:raise ValueError('match需要pattern。')
        if 'group' in node and (type(node['group']) is not int or not 0<=node['group']<=8):raise ValueError('计算正则group须为0至8。')
        if 'group' in node and node['group']>_pattern(node.get('pattern','(.)')).groups:raise ValueError('group超过计算正则的捕获组数量。')
        if 'distinct' in node and not isinstance(node['distinct'],bool):raise ValueError('distinct必须是布尔值。')
        if op=='round' and (type(node.get('digits',0)) is not int or not 0<=node.get('digits',0)<=8):raise ValueError('round的digits须为0至8。')
        if op=='group_by':
            if not isinstance(node.get('path'),str) or len(node['path'])>1000:raise ValueError('group_by需要每条记录内的分组path。')
            if 'aggregate' in node:visit(node['aggregate'],depth+1)
        if op=='date_part' and node.get('part') not in ('date','year','month','day','weekday','hour'):raise ValueError('date_part需指定date/year/month/day/weekday/hour。')
        if op=='join' and (not isinstance(node.get('separator','|'),str) or len(node.get('separator','|'))>100):raise ValueError('join的separator必须是短字符串。')
    visit(computed)
    return computed


def _number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float,str)):raise ValueError('计算输入不是数字：'+str(value)[:100])
    if isinstance(value,str):
        text=value.strip().replace(',','')
        if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)',text):raise ValueError('数值格式无法解析：'+value[:100])
        value=float(text)
    if not math.isfinite(value):raise ValueError('计算结果不是有限数。')
    return int(value) if float(value).is_integer() else value


def _decimal(value):return Decimal(str(_number(value)))


def _result(value):
    if not value.is_finite():raise ValueError('计算结果不是有限数。')
    return _number(int(value) if value==value.to_integral_value() else float(value))


def evaluate_computed(record,computed,context=None):
    validate_computed(computed);steps=[0]
    def evaluate(node,current):
        steps[0]+=1
        if steps[0]>10000:raise ValueError('计算步骤超过10000，请缩小集合或简化表达式。')
        if not isinstance(node,dict):return node
        if 'field' in node:
            path=node['field']
            if path=='$records':return (context or {}).get('records')
            if path=='$root':return record
            if path.startswith('$root.'):return _get(record,path[6:])
            return _get(current,path)
        if 'literal' in node:return node['literal']
        op=node['op'];args=node.get('args',[])
        if op=='duration' and not args:args=[{'field':node['start']},{'field':node['end']}]
        if op=='if':
            condition=evaluate(args[0],current)
            return None if condition is None else evaluate(args[1] if condition else args[2],current)
        if op in ('coalesce','and','or'):
            unknown=False
            for arg in args:
                value=evaluate(arg,current)
                if op=='coalesce':
                    if value not in (None,'',[]):return value
                elif value is None:unknown=True
                elif op=='and' and not value:return False
                elif op=='or' and value:return True
            return None if unknown or op=='coalesce' else op=='and'
        first=evaluate(args[0],current)
        if first is None:return None
        if op in ('map','filter','group_by'):
            if not isinstance(first,list) or len(first)>1000:raise ValueError(op+'输入须为最多1000项的数组。')
            if op=='map':return [evaluate(args[1],row) for row in first]
            if op=='filter':
                choices=[evaluate(args[1],row) for row in first]
                return None if any(value is None for value in choices) else [row for row,keep in zip(first,choices) if keep]
            groups={}
            for row in first:
                key=_get(row,node['path'])
                if key is None:return None
                if not isinstance(key,(str,int,float,bool)):raise ValueError('分组键必须为标量。')
                groups.setdefault(str(key),[]).append(row)
            return {key:evaluate(node['aggregate'],rows) if 'aggregate' in node else rows for key,rows in groups.items()}
        values=[first]+[evaluate(arg,current) for arg in args[1:]]
        if any(value is None for value in values):return None
        if op=='duration':
            try:from .connector_runtime import duration_value
            except ImportError:from connector_runtime import duration_value
            return duration_value(*values,node)
        if op in ('split','match','count','unique'):
            collection=first
            if isinstance(first,str):
                if len(first)>20000:raise ValueError('计算文本超过20000字符。')
                if op=='match' or node.get('pattern'):
                    collection=[match.group(node.get('group',0)) for match in _pattern(node['pattern']).finditer(first)]
                    if not collection and first.strip():return None
                else:collection=[part.strip() for part in re.split(node.get('separator',r'[|,，;；\r\n]+'),first) if part.strip()]
            if not isinstance(collection,list) or len(collection)>1000:raise ValueError(op+'需要数组或可拆分文本，最多1000项。')
            if node.get('distinct') or op=='unique':
                seen=set();dedup=[]
                for value in collection:
                    key=json.dumps(value,sort_keys=True,ensure_ascii=False)
                    if key not in seen:seen.add(key);dedup.append(value)
                collection=dedup
            return len(collection) if op=='count' else collection
        if op in ('sum','average','min','max'):
            if not isinstance(first,list) or len(first)>1000:raise ValueError(op+'需要最多1000项的数值数组，记录数组请先map。')
            if any(value is None for value in first):return None
            numbers=[_decimal(value) for value in first]
            if not numbers:return 0 if op=='sum' else None
            if op=='sum':return _result(sum(numbers))
            if op=='average':return _result(sum(numbers)/len(numbers))
            return _result(min(numbers) if op=='min' else max(numbers))
        if op in ('add','subtract','multiply','divide','round','abs','number'):
            numbers=[_decimal(value) for value in values]
            if op=='add':result=sum(numbers)
            elif op=='subtract':result=numbers[0]-numbers[1]
            elif op=='multiply':result=math.prod(numbers)
            elif op=='divide':
                if numbers[1]==0:raise ValueError('除数为0，无法计算。')
                result=numbers[0]/numbers[1]
            elif op=='round':result=numbers[0].quantize(Decimal(1).scaleb(-node.get('digits',0)),rounding=ROUND_HALF_UP)
            elif op=='abs':result=abs(numbers[0])
            else:result=numbers[0]
            return _result(result)
        if op=='not':return not first
        if op=='contains':return values[1] in first
        if op in ('eq','ne','gt','gte','lt','lte'):
            left,right=values
            if op in ('gt','gte','lt','lte'):left,right=_number(left),_number(right)
            if op=='eq':return left==right
            if op=='ne':return left!=right
            if op=='gt':return left>right
            if op=='gte':return left>=right
            if op=='lt':return left<right
            return left<=right
        if op=='concat':return ''.join(str(value) for value in values)
        if op=='join':
            if not isinstance(first,list) or len(first)>1000:raise ValueError('join需要最多1000项的数组。')
            return node.get('separator','|').join(str(value) for value in first)
        if op=='date_part':
            stamp=datetime.fromisoformat(str(first).replace('Z','+00:00'));part=node['part']
            return stamp.date().isoformat() if part=='date' else stamp.isoweekday() if part=='weekday' else getattr(stamp,part)
        raise ValueError('计算操作未实现：'+op)
    try:return evaluate(computed,record)
    except (TypeError,OverflowError,ZeroDivisionError,InvalidOperation) as exc:raise ValueError('计算输入类型不符或超出范围：'+str(exc)[:160]) from exc


def calculation_inputs(record,computed):
    paths=set()
    def visit(node):
        if isinstance(node,dict):
            paths.update(node[key] for key in ('field','start','end') if isinstance(node.get(key),str))
            for value in node.values():visit(value)
        elif isinstance(node,list):
            for value in node:visit(value)
    visit(computed)
    return {path:_get(record,path) for path in sorted(paths)}
