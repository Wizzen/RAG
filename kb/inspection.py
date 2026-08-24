"""检查项提取器：从维护手册 Markdown 内容中自动提取检查内容。

纯 regex + 启发式规则，不依赖外部 API 或 AI。

提取维度：
  - 图纸号（drawing_no）：工程图纸编号
  - 部件名称（part_name）：被检查的部件
  - 检查频率（frequency）：日检/周检/月检/季检/年检/定期等
  - 检查要求（requirement）：检查项目简述
  - 详细要求（detail）：上下文扩展（前后文片段）

策略：
  1. HTML 表格提取：解析表格，按表头匹配列，逐行提取结构化检查项
  2. 文本块提取：识别 "字段: 值" 格式的连续行，合并为一个检查项；
     对独立的检查描述行，单独提取
  3. 合并去重
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass


# ------------------------------------------------------------------
# 正则模式
# ------------------------------------------------------------------

# 图纸号：常见工程编号格式
_DRAWING_NO_PATTERNS = [
    re.compile(r"图\s*纸\s*(?:编\s*)?号\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-_/.]+)", re.IGNORECASE),
    re.compile(r"图\s*号\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-_/.]+)", re.IGNORECASE),
    re.compile(r"DWG\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-_/.]+)", re.IGNORECASE),
    re.compile(r"图\s*样\s*编\s*号\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-_/.]+)", re.IGNORECASE),
    # 独立工程编号：字母前缀 + 分隔符 + 数字/字母（如 13YF140-YS01, P8-001, A-100）
    re.compile(r"\b([A-Z]{1,6}[\-_][A-Z0-9]{1,}(?:[\-_][A-Z0-9]+)*)\b"),
]

# 部件名称关键词
_PART_KEYWORDS = ["部件", "零件", "组件", "构件", "机构", "装置", "总成", "结构件"]

# 检查频率
_FREQ_PATTERNS = [
    (re.compile(r"每\s*日|每\s*天|日\s*检"), "每日"),
    (re.compile(r"每\s*周|周\s*检"), "每周"),
    (re.compile(r"每\s*月|月\s*检"), "每月"),
    (re.compile(r"每\s*季\s*度|季\s*检|每\s*季"), "每季度"),
    (re.compile(r"每\s*半\s*年|半\s*年\s*检"), "每半年"),
    (re.compile(r"每\s*年|年\s*检|年\s*度"), "每年"),
    (re.compile(r"每\s*\d+\s*天"), ""),
    (re.compile(r"每\s*\d+\s*日"), ""),
    (re.compile(r"每\s*\d+\s*周"), ""),
    (re.compile(r"每\s*\d+\s*月"), ""),
    (re.compile(r"每\s*\d+\s*年"), ""),
    (re.compile(r"每\s*\d+\s*小\s*时"), ""),
    (re.compile(r"每\s*\d+\s*次"), ""),
    (re.compile(r"日\s*常\s*检\s*查|日\s*常"), "日常"),
    (re.compile(r"定\s*期\s*检\s*查|定\s*期"), "定期"),
    (re.compile(r"每\s*次\s*(?:运\s*行|使\s*用|开\s*机)"), "每次运行"),
    (re.compile(r"运\s*行\s*前|开\s*机\s*前"), "运行前"),
    (re.compile(r"运\s*行\s*后|停\s*机\s*后"), "运行后"),
]

# 检查关键词（用于识别检查项行）
_INSPECTION_KEYWORDS = [
    "检查", "检验", "检测", "复检", "点检", "巡检", "探伤",
    "试验", "测试", "测量", "校验", "标定",
    "扭矩", "间隙", "磨损", "裂纹", "变形",
    "润滑", "紧固", "密封", "腐蚀", "泄漏",
    "螺栓", "销轴", "轴承", "齿轮", "钢丝绳",
    "焊缝", "连接", "锁紧", "调整",
]

# 检查相关标题（## 级别）
_INSPECTION_HEADING_KEYWORDS = [
    "检查", "检验", "检测", "点检", "巡检", "维护", "保养",
    "检修", "维修", "定期", "日常", "安全",
]

# "字段: 值" 格式的行识别
_FIELD_PATTERNS = {
    "drawing_no": re.compile(
        r"^\s*(?:图\s*纸\s*(?:编\s*)?号|图\s*号|DWG|图\s*样\s*编\s*号)\s*[:：]\s*(.+)", re.IGNORECASE
    ),
    "part_name": re.compile(
        r"^\s*(?:部\s*件\s*(?:名\s*)?称|零\s*件\s*(?:名\s*)?称|组\s*件\s*名\s*称|名\s*称)\s*[:：]\s*(.+)"
    ),
    "frequency": re.compile(
        r"^\s*(?:检\s*查\s*频\s*率|频\s*率|周\s*期|检\s*查\s*周\s*期|频\s*次)\s*[:：]\s*(.+)"
    ),
    "requirement": re.compile(
        r"^\s*(?:检\s*查\s*要\s*求|检\s*验\s*要\s*求|要\s*求|检\s*查\s*内\s*容|检\s*查\s*项\s*目|检\s*查\s*标\s*准)\s*[:：]\s*(.+)"
    ),
    "detail": re.compile(
        r"^\s*(?:详\s*细\s*要\s*求|技\s*术\s*要\s*求|详\s*细\s*说\s*明|备\s*注|说\s*明)\s*[:：]\s*(.+)"
    ),
}


# ------------------------------------------------------------------
# HTML 表格解析
# ------------------------------------------------------------------
_TD_RE = re.compile(r"<td\s+rowspan=(\d+)\s+colspan=(\d+)>(.*?)</td>", re.DOTALL)
_TR_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
_TABLE_RE = re.compile(r"<table>.*?</table>", re.DOTALL)


def _parse_html_table(table_html: str) -> list[list[str]]:
    """把一个 <table>…</table> 解析成二维列表（含 rowspan/colspan 还原）。

    返回 rows[row_idx][col_idx] = cell_text。
    """
    rows_raw: list[list[tuple[str, int, int]]] = []
    for tr_m in _TR_RE.finditer(table_html):
        cells = [
            (_html.unescape(c.group(3).strip()), int(c.group(1)), int(c.group(2)))
            for c in _TD_RE.finditer(tr_m.group(1))
        ]
        if cells:
            rows_raw.append(cells)
    if not rows_raw:
        return []

    ncols = 0
    for cells in rows_raw:
        row_cols = sum(cs for _, _, cs in cells)
        if row_cols > ncols:
            ncols = row_cols
    if ncols == 0:
        ncols = 1

    grid: list[list[str]] = []
    carries: dict[int, dict[int, str]] = {}

    for ri, cells in enumerate(rows_raw):
        while len(grid) <= ri:
            grid.append([""] * ncols)
        row = grid[ri]
        covered: set[int] = set()

        for col, val in carries.get(ri, {}).items():
            if col < ncols:
                row[col] = val
                covered.add(col)

        for text, rs, cs in cells:
            col = 0
            while col in covered:
                col += 1
            for j in range(cs):
                if col + j < ncols:
                    row[col + j] = text
                    covered.add(col + j)
            for k in range(1, rs):
                carries.setdefault(ri + k, {})[col] = text
                for j in range(1, cs):
                    if col + j < ncols:
                        carries.setdefault(ri + k, {})[col + j] = text

    return grid


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------
def _match_header(text: str, keywords: list[str]) -> bool:
    """检查文本是否包含任一关键词。"""
    t = text.lower().strip()
    return any(k in t for k in keywords)


def _find_columns(headers: list[str]) -> dict[str, int]:
    """一次性识别表头中各字段对应的列索引。

    优先级：频率 > 要求 > 详细 > 图号 > 部件
    （先匹配更具体的词，避免 "检查频率" 被 "检查" 误匹配到要求列）

    返回: {"drawing_no": idx, "part_name": idx, "frequency": idx,
           "requirement": idx, "detail": idx}（未找到的为 -1）
    """
    cols: dict[str, int] = {k: -1 for k in ("drawing_no", "part_name", "frequency", "requirement", "detail")}
    used: set[int] = set()

    # 按优先级排列的匹配模式（从最具体到最宽泛）
    match_order = [
        # 频率：最先匹配，避免 "检查频率" 被要求列抢走
        ("frequency", ["检查频率", "检查周期", "频率", "周期", "频次", "interval", "frequency", "检查频次"]),
        # 要求：匹配 "检查要求"/"检验要求"/"检查内容" 等
        ("requirement", ["检查要求", "检验要求", "检查内容", "检查项目", "检查标准", "检验内容",
                         "检验项目", "要求", "内容", "项目", "标准", "method", "require"]),
        # 详细：匹配 "详细要求"/"技术要求"/"备注" 等
        ("detail", ["详细要求", "技术要求", "详细说明", "备注", "说明", "标准值", "note", "detail", "技术"]),
        # 图号
        ("drawing_no", ["图号", "图纸号", "图纸编号", "图样编号", "编号", "drawing", "dwg"]),
        # 部件名称
        ("part_name", ["部件名称", "零件名称", "组件名称", "部件", "零件", "名称", "组件", "构件", "part", "name"]),
    ]

    for field_name, patterns in match_order:
        for i, h in enumerate(headers):
            if i in used:
                continue
            h_lower = h.lower().strip()
            for p in patterns:
                if p in h_lower:
                    cols[field_name] = i
                    used.add(i)
                    break
            if cols[field_name] >= 0:
                break

    return cols


# ------------------------------------------------------------------
# 字段提取
# ------------------------------------------------------------------
def _extract_frequency(text: str) -> str:
    """从文本中提取检查频率。"""
    for pat, label in _FREQ_PATTERNS:
        m = pat.search(text)
        if m:
            if label:
                return label
            return m.group(0).strip()
    return ""


def _extract_drawing_no(text: str) -> str:
    """从文本中提取图纸号。"""
    for pat in _DRAWING_NO_PATTERNS:
        m = pat.search(text)
        if m:
            val = m.group(1).strip().rstrip(".,;:，。；：")
            if len(val) >= 2:
                return val
    return ""


def _extract_part_name(text: str) -> str:
    """从文本中提取部件名称。"""
    for kw in _PART_KEYWORDS:
        if kw in text:
            idx = text.index(kw)
            start = max(0, idx - 8)
            end = min(len(text), idx + len(kw) + 12)
            return text[start:end].strip()
    return ""


def _is_inspection_line(text: str) -> bool:
    """判断一行文本是否是检查相关内容。"""
    return _match_header(text, _INSPECTION_KEYWORDS)


def _is_inspection_heading(text: str) -> bool:
    """判断标题文本是否是检查相关章节。"""
    return _match_header(text, _INSPECTION_HEADING_KEYWORDS)


def _match_field_line(text: str) -> tuple[str, str] | None:
    """匹配 "字段: 值" 格式的行，返回 (字段名, 值) 或 None。"""
    for field_name, pat in _FIELD_PATTERNS.items():
        m = pat.match(text)
        if m:
            val = m.group(1).strip()
            if val:
                return field_name, val
    return None


# ------------------------------------------------------------------
# 检查项数据结构
# ------------------------------------------------------------------
@dataclass
class InspectionItem:
    """单个检查项。"""
    drawing_no: str = ""
    part_name: str = ""
    frequency: str = ""
    requirement: str = ""
    detail: str = ""
    source_section: str = ""


# ------------------------------------------------------------------
# 主提取函数
# ------------------------------------------------------------------
def extract_inspection_items(md_content: str, doc_name: str = "") -> list[dict]:
    """从文档 Markdown 内容中提取检查项。

    策略：
    1. 解析 HTML 表格，识别含检查相关表头的表格，按行列提取
    2. 扫描非表格文本，用 "字段: 值" 块合并 + 独立检查行提取
    3. 合并去重

    返回: [{"drawing_no","part_name","frequency","requirement","detail","section"}, ...]
    """
    if not md_content or not md_content.strip():
        return []

    items: list[InspectionItem] = []
    seen: set[str] = set()

    def _add(item: InspectionItem) -> None:
        """添加并去重。"""
        # 至少要有 requirement 或 drawing_no 才算有效项
        if not item.requirement.strip() and not item.drawing_no.strip():
            return
        key = (item.requirement.strip()[:60], item.drawing_no.strip(), item.frequency.strip()[:20])
        if key in seen:
            return
        seen.add(key)
        items.append(item)

    # ---- 1. 表格提取 ----
    for tbl_m in _TABLE_RE.finditer(md_content):
        rows = _parse_html_table(tbl_m.group(0))
        if not rows:
            continue

        # 用前 3 行找表头
        header_row_idx = -1
        headers: list[str] = []
        for hi in range(min(3, len(rows))):
            hr = [c.strip() for c in rows[hi]]
            if _match_header(" ".join(hr), _INSPECTION_KEYWORDS + ["频率", "周期", "图号", "部件"]):
                header_row_idx = hi
                headers = hr
                break

        if header_row_idx >= 0:
            cols = _find_columns(headers)

            for row in rows[header_row_idx + 1:]:
                non_empty = [c for c in row if c.strip()]
                if len(non_empty) <= 1:
                    continue

                item = InspectionItem()
                if cols["drawing_no"] >= 0 and cols["drawing_no"] < len(row):
                    item.drawing_no = row[cols["drawing_no"]].strip()
                if cols["part_name"] >= 0 and cols["part_name"] < len(row):
                    item.part_name = row[cols["part_name"]].strip()
                if cols["frequency"] >= 0 and cols["frequency"] < len(row):
                    item.frequency = row[cols["frequency"]].strip()
                if cols["requirement"] >= 0 and cols["requirement"] < len(row):
                    item.requirement = row[cols["requirement"]].strip()
                if cols["detail"] >= 0 and cols["detail"] < len(row) and cols["detail"] != cols["requirement"]:
                    item.detail = row[cols["detail"]].strip()

                # 补充提取（当列匹配不全时）
                full_text = " | ".join(c.strip() for c in row if c.strip())
                if not item.drawing_no:
                    item.drawing_no = _extract_drawing_no(full_text)
                if not item.frequency:
                    item.frequency = _extract_frequency(full_text)
                if not item.requirement:
                    if _is_inspection_line(full_text):
                        item.requirement = full_text[:120]
                if not item.detail:
                    item.detail = full_text[:200]
                if not item.part_name:
                    item.part_name = _extract_part_name(full_text)

                _add(item)
        else:
            # 无明确表头：扫描表格行内容
            for row in rows:
                full_text = " | ".join(c.strip() for c in row if c.strip())
                if not full_text or not _is_inspection_line(full_text):
                    continue
                item = InspectionItem()
                item.drawing_no = _extract_drawing_no(full_text)
                item.frequency = _extract_frequency(full_text)
                item.requirement = full_text[:120]
                item.detail = full_text[:200]
                item.part_name = _extract_part_name(full_text)
                _add(item)

    # ---- 2. 文本块提取 ----
    # 去掉表格块（已在上面处理），保留纯文本
    text_no_tables = _TABLE_RE.sub("\n\n", md_content)
    section_split = re.compile(r"(?=\n##\s)", re.MULTILINE)
    heading_re = re.compile(r"^#+\s+(.*)$", re.MULTILINE)

    sections = section_split.split(text_no_tables)
    for sec in sections:
        if not sec.strip():
            continue

        head_m = heading_re.match(sec.lstrip())
        section_name = head_m.group(1).strip() if head_m else ""
        clean_section = re.sub(r"<[^>]+>", "", section_name).strip()
        is_inspection_section = _is_inspection_heading(clean_section)

        lines = sec.split("\n")

        # ---- 2a. "字段: 值" 块合并 ----
        # 当连续多行都是 "图号: xxx" / "部件名称: xxx" / "检查频率: xxx" / "检查要求: xxx" 时
        # 合并为一个检查项
        current_item: InspectionItem | None = None
        block_lines: list[str] = []  # 用于构建 detail

        def _flush_block():
            """提交当前正在累积的块。"""
            nonlocal current_item, block_lines
            if current_item is not None:
                if block_lines:
                    current_item.detail = "\n".join(block_lines)[:300]
                    current_item.source_section = clean_section
                _add(current_item)
                current_item = None
                block_lines = []

        for i, line in enumerate(lines):
            clean_line = re.sub(r"<[^>]+>", "", line).strip()
            if not clean_line:
                continue

            # 标题行：提交当前块
            if re.match(r"^#+\s+", clean_line):
                _flush_block()
                continue

            # 尝试匹配 "字段: 值" 格式
            field_match = _match_field_line(clean_line)
            if field_match:
                field_name, field_val = field_match
                if current_item is None:
                    current_item = InspectionItem()
                setattr(current_item, field_name, field_val)
                block_lines.append(clean_line)
                continue

            # 非字段行
            if current_item is not None:
                # 当前正在累积块中，遇到非字段行
                # 如果是检查相关行，加入 detail；否则提交块
                if _is_inspection_line(clean_line):
                    block_lines.append(clean_line)
                    # 也尝试从中提取未填的字段
                    if not current_item.drawing_no:
                        current_item.drawing_no = _extract_drawing_no(clean_line)
                    if not current_item.frequency:
                        current_item.frequency = _extract_frequency(clean_line)
                    if not current_item.part_name:
                        current_item.part_name = _extract_part_name(clean_line)
                else:
                    _flush_block()
            else:
                # 不在块中：判断是否是独立的检查行
                is_insp = _is_inspection_line(clean_line)
                if not is_insp and not is_inspection_section:
                    continue

                # 独立检查行：创建单项
                item = InspectionItem()
                item.drawing_no = _extract_drawing_no(clean_line)
                item.frequency = _extract_frequency(clean_line)
                item.part_name = _extract_part_name(clean_line)
                item.requirement = clean_line[:120]
                item.source_section = clean_section

                # 上下文作为 detail
                context_lines = []
                for j in range(max(0, i - 2), min(len(lines), i + 3)):
                    cl = re.sub(r"<[^>]+>", "", lines[j]).strip()
                    if cl:
                        context_lines.append(cl)
                item.detail = "\n".join(context_lines)[:300]

                _add(item)

        # 提交最后一个块
        _flush_block()

    # 转为 dict 列表
    return [
        {
            "drawing_no": it.drawing_no,
            "part_name": it.part_name,
            "frequency": it.frequency,
            "requirement": it.requirement,
            "detail": it.detail,
            "section": it.source_section,
        }
        for it in items
    ]


# ------------------------------------------------------------------
# 搜索（模糊搜索）
# ------------------------------------------------------------------
def search_in_content(
    query: str,
    docs: list,
    limit: int = 50,
) -> list[dict]:
    """在多份文档的 md_content 中做模糊搜索。

    参数:
        query: 搜索关键词（图纸号、部件名称等）
        docs: Document 对象列表（需有 md_content, original_name, kb）
        limit: 最多返回结果数

    返回: [{"doc_name","kb_name","doc_id","line_no","snippet","context","section"}, ...]
    """
    if not query or not query.strip():
        return []

    query = query.strip()
    query_lower = query.lower()

    results: list[dict] = []
    for doc in docs:
        md = doc.md_content or ""
        if not md:
            continue

        doc_id = str(doc.id)
        doc_name = doc.original_name
        kb_name = doc.kb.name if doc.kb else ""

        # 先在文件名中搜索
        if query_lower in doc_name.lower():
            results.append({
                "doc_name": doc_name,
                "kb_name": kb_name,
                "doc_id": doc_id,
                "line_no": 0,
                "snippet": f"文件名匹配: {doc_name}",
                # 文件名命中：正文里未必含该词，用检索词做高亮锚点（命中与否由查看页提示）
                "highlight": query,
                "context": "",
                "section": "",
                "match_type": "filename",
            })

        # 在内容中搜索（逐行）
        lines = md.split("\n")
        current_section = ""

        for i, line in enumerate(lines):
            heading_m = re.match(r"^#+\s+(.*)$", line)
            if heading_m:
                current_section = re.sub(r"<[^>]+>", "", heading_m.group(1)).strip()
                continue

            clean_line = re.sub(r"<[^>]+>", "", line).strip()
            if not clean_line:
                continue

            if query_lower in clean_line.lower():
                context_lines = []
                for j in range(max(0, i - 2), min(len(lines), i + 3)):
                    cl = re.sub(r"<[^>]+>", "", lines[j]).strip()
                    if cl:
                        context_lines.append(cl)
                context = "\n".join(context_lines)[:300]

                idx = clean_line.lower().index(query_lower)
                start = max(0, idx - 30)
                end = min(len(clean_line), idx + len(query) + 30)
                snippet = clean_line[start:end]
                if start > 0:
                    snippet = "…" + snippet
                if end < len(clean_line):
                    snippet = snippet + "…"

                results.append({
                    "doc_name": doc_name,
                    "kb_name": kb_name,
                    "doc_id": doc_id,
                    "line_no": i + 1,
                    "snippet": snippet,
                    # 文档查看页 ?h= 高亮锚点：去掉首尾省略号后是文档原文的连续子串
                    "highlight": snippet.strip("…"),
                    "context": context,
                    "section": current_section,
                    "match_type": "content",
                })

                if len(results) >= limit:
                    return results

    return results
