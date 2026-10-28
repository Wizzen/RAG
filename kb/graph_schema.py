from __future__ import annotations

"""聚焦抽取的受控词表和确定性检查；只规范身份，不替模型推断新事实。"""

import re
from copy import deepcopy

KIND_NAMES = {'equipment':'设备', 'component':'部件', 'inspection':'检查项目', 'action':'操作',
              'standard':'标准', 'clause':'条款', 'parameter':'参数', 'defect':'缺陷',
              'material':'材料', 'drawing':'图纸', 'person':'人员', 'requirement':'要求', 'concept':'概念'}
KIND_ALIASES = {'part':'component', 'tool':'equipment', 'test_method':'inspection',
                'inspection_activity':'inspection', 'maintenance_action':'action',
                'failure':'defect', 'fault':'defect', 'defect_type':'defect', 'failure_form':'defect',
                'defect_subtype':'defect', 'failure_category':'defect', 'defect_category':'defect',
                'document_section':'clause', 'material_layer':'material'}
PREDICATES = {'contains', 'has_part', 'documented_by', 'requires', 'applies_to', 'references',
              'has_parameter', 'has_failure', 'prohibits', 'does_not_apply_to', 'is_a',
              'has_subtype', 'defines', 'uses', 'checks'}
PREDICATE_ALIASES = {'belongs_to_category':'is_a', 'classified_as':'is_a', 'has_category':'is_a',
                     'has_subcategory':'has_subtype', 'has_form':'has_subtype'}
CONDITION_NAMES = {'parameter':'参数名称', 'operator':'比较关系', 'value':'数值', 'unit':'单位',
                   'frequency':'周期', 'scope':'适用范围', 'equipment':'设备范围',
                   'modality':'规范用语', 'negation':'否定条件', 'condition':'条件', 'version':'版本'}
def _predicate_menu() -> str:
    """现有谓词清单（中英对照），单一来源取自 knowledge_graph 的展示映射。"""
    from .knowledge_graph import PREDICATES
    return '；'.join(f'{key}（{label}）' for key, label in sorted(PREDICATES.items()))


SCHEMA_PROMPT = ('\n受控抽取词表：entity.type 只能使用 ' + ', '.join(KIND_NAMES) +
                '。predicate 优先从现有清单中选用：' + _predicate_menu() +
                '。现有清单都不贴切时，可以自创新谓词：小写英文 snake_case、动词性、领域通用'
                '（如 measured_by、requires_calibration）；同一语义只用一个谓词，不得为已有谓词造同义词，禁止中英混排。'
                '同一概念在本次运行中保持一致类型，不创造 failure/fault/defect_type 等同义类型。'
                'has_part 仅用于实体组成关系，分类用 is_a 或 has_subtype。'
                'has_parameter 的 conditions 必须包含 parameter（参数名称）、operator（<,<=,=,>=,>,range,!=之一）、'
                'value、unit（无量纲填空字符串）。不同阈值的适用情形分别输出。'
                '禁止抽取表格序号、无指向的“本部分”等主体；不要把长句或来源描述当作一个实体。'
                '表号/图号/附录号/条款号（如 表7、图3、附录K、10.2.8.1）只用于定位，绝不能作为实体或关系终点；'
                '“某内容见表N”本身不是知识——必须读取该表所在的正文片段，把表内每一行的要求展开为'
                'has_parameter（参数、比较符、数值、单位、条件）或 applies_to 等具体关系。'
                '原文否定与谓词必须一致，禁止用 has_failure 表达“不应有缺陷”。')

# 指针型名称：表/图/附录/条款编号只指出知识的位置，本身不是实体。
_POINTER_NAME = re.compile(
    r'^(?:[表图]\s*[0-9A-Za-z][0-9A-Za-z.．\-—～~\s]*'   # 表7 / 表 G.5～表 G.7 / 图K.7
    r'|附录\s*[0-9A-Za-z]+(?:\s*[.．][0-9]+)*'          # 附录 K / 附录 K.5
    r'|第\s*[0-9]+\s*[条款]'                            # 第5条 / 第3款
    r'|[0-9]+(?:[.．][0-9]+)+\s*[条款]?'                # 10.2.8.1 / 10.2 条
    r'|[表图]\s*[0-9A-Za-z]+.*[～~].*)$')               # 表 G.5～表 G.7（含连接式）


def is_pointer_name(name: str) -> bool:
    return bool(_POINTER_NAME.match((name or '').strip()))


def normalize_candidate(row: dict) -> dict | None:
    """规范同义类型，拒绝无参数语义、错误否定、无指向主体和指针型实体。"""
    row = deepcopy(row)
    for side in ('subject', 'object'):
        entity = row[side]
        entity['type'] = KIND_ALIASES.get(entity['type'], entity['type'])
        if entity['type'] not in KIND_NAMES or entity['name'].strip() in {'本部分', '本标准', '本文件', '该部分', '该标准'}:
            return None
        # 表号/图号/条款号只是“知识在哪”，不是知识本身——直接拒绝指针关系。
        if is_pointer_name(entity['name']):
            return None
    row['predicate'] = PREDICATE_ALIASES.get(row['predicate'], row['predicate'])
    if row['predicate'] in {'has_number','has_row_number','has_table_number'}:
        return None
    # 谓词开放式：优先复用现有清单；自创谓词由 parse_relations 的 snake_case
    # 正则约束格式，这里不做封闭词表硬拒（显示侧未知谓词原样展示英文）。
    if row['predicate'] == 'has_part' and row['subject']['type'] == row['object']['type'] == 'defect':
        return None
    if row['predicate'] == 'has_parameter':
        conditions = row.get('conditions', {})
        if (not all(key in conditions for key in ('parameter', 'operator', 'value', 'unit'))
                or not str(conditions['parameter']).strip()
                or conditions['operator'] not in ('<', '<=', '=', '>=', '>', 'range', '!=')
                or any(isinstance(conditions[key], (dict, list, bool)) for key in ('parameter','operator','value','unit'))):
            return None
    if row['predicate'] == 'has_failure' and (row.get('conditions', {}).get('negation')
                                           or any(word in row['quote'] for word in ('不应有', '不得有', '禁止'))):
        return None
    if row['predicate'] == 'applies_to' and '不适用' in row['quote']:
        return None
    return row
