"""Printable PDFs for Maximo job plans (JobPlanPrint.rpt) and work orders (WOPrint1.rpt)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .maximo_workbench import TIMEZONE, pick_lang

FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN_X = 14 * mm
MARGIN_TOP = 14 * mm
MARGIN_BOTTOM = 24 * mm
BODY_WIDTH = PAGE_WIDTH - 2 * MARGIN_X
REPORT_VERSION = "SHDR RAG"
LINK_BLUE = colors.HexColor("#1f4fbf")

WO_MARGIN_TOP = 34 * mm
WO_MARGIN_BOTTOM = 28 * mm
WO_REPORT_NAME = "WOPrint1.rpt"
WO_REPORT_VERSION = "2.0 NewSvr"
WO_DISPLAY_TZ = "Asia/Hong_Kong"

LABELS = {
    "CN": {
        "jobplan_title": "作业计划",
        "workorder_title": "工单",
        "jpnum": "作业计划ID:",
        "wonum": "工单号:",
        "description": "描述:",
        "compliance": "遵从:",
        "priority": "优先级:",
        "duration": "持续时间:",
        "interruptible": "可中断:",
        "status": "状态:",
        "drawing": "部件/图纸:",
        "part_desc": "部件/图纸描述:",
        "supervisor": "主管:",
        "craft": "工艺:",
        "labor": "员工卡:",
        "qual_req": "资格要求:",
        "drawing_rev": "图纸复查:",
        "worktype": "工单类型:",
        "location": "位置:",
        "asset": "资产:",
        "jobplan": "作业计划:",
        "pmnum": "PM:",
        "persongroup": "班组:",
        "lead": "负责人:",
        "reported": "报告时间:",
        "sched": "计划起止:",
        "actual": "实际起止:",
        "frequency": "频率:",
        "task_id": "任务ID",
        "task_desc": "描述:",
        "task_duration": "预计持续时间",
        "total": "全部预计持续时间:",
        "page": "页码 {page} / {total}",
        "timezone": "时区是 {tz}",
        "printed": "打印时间: {stamp}",
        "yes": "是",
        "no": "否",
        "no_tasks": "（无任务步骤）",
        "tasks_from": "任务来源:",
        "task_source": "工单无任务行，以下步骤取自作业计划 {jpnum}",
    },
    "EN": {
        "jobplan_title": "Job Plan",
        "workorder_title": "Work Order",
        "jpnum": "Job Plan:",
        "wonum": "Work Order:",
        "description": "Description:",
        "compliance": "Compliance:",
        "priority": "Priority:",
        "duration": "Duration:",
        "interruptible": "Interruptible:",
        "status": "Status:",
        "drawing": "Part/Drawing:",
        "part_desc": "Part/Drawing Desc:",
        "supervisor": "Supervisor:",
        "craft": "Craft:",
        "labor": "Labor:",
        "qual_req": "Qualification Req:",
        "drawing_rev": "Drawing Rev:",
        "worktype": "Work Type:",
        "location": "Location:",
        "asset": "Asset:",
        "jobplan": "Job Plan:",
        "pmnum": "PM:",
        "persongroup": "Person Group:",
        "lead": "Lead:",
        "reported": "Reported:",
        "sched": "Scheduled:",
        "actual": "Actual:",
        "frequency": "Frequency:",
        "task_id": "Task ID",
        "task_desc": "Description:",
        "task_duration": "Est. Duration",
        "total": "Total Estimated Duration:",
        "page": "Page {page} / {total}",
        "timezone": "Time zone {tz}",
        "printed": "Printed: {stamp}",
        "yes": "Yes",
        "no": "No",
        "no_tasks": "(No task steps)",
        "tasks_from": "Task Source:",
        "task_source": "No work-order task rows; steps below are from job plan {jpnum}",
    },
}

WO_LABELS = {
    "CN": {
        "planned_hours": "计划持续小时:",
        "orig_wo": "原工单编号:",
        "parent_wo": "父级工单:",
        "priority": "优先级:",
        "pm": "PM:",
        "act_finish": "实际完成时间:",
        "worktype": "工作类型:",
        "act_start": "实际开始时间:",
        "pm_due": "系统到期日期:",
        "compliance": "合规:",
        "status": "状态:",
        "sap_wbs": "SAP WBS:",
        "reported_by": "报告人:",
        "report_date": "报告日期:",
        "persongroup": "班组:",
        "manager": "经理:",
        "supervisor": "主管:",
        "dept": "部门:",
        "jobplan": "作业计划:",
        "eq_serial": "设备序号:",
        "location": "位置:",
        "asset": "资产:",
        "craft": "工种:",
        "shift": "班次:",
        "lightboard": "灯板显示?:",
        "target_start": "目标开始日期:",
        "target_finish": "目标完成日期:",
        "equipment_type": "Equipment Type:",
        "handover": "交接班信息（请不要输入到 maximo)",
        "status_history": "状态历史记录",
        "status_memo": "状态历史备注",
        "status_changed": "变更日期",
        "status_by": "变更人",
        "status_col": "状态",
        "steps": "操作步骤",
        "step_id": "任务标识",
        "mandatory": "强制?",
        "labor_summary": "人工汇总:",
        "labor_actual": "人工实际情况:",
        "labor_header": "员工代码 姓名 任务标识 开始日期 开始时间 结束时间 标准工时 加班工时 输入人",
        "parts": "部件报告：",
        "parts_header": "货柜\t成本\t使用数量\t申请数量\t部件描述\t部件编号",
        "meters": "计量器读数",
        "completion_flags": "遗漏的PM\t需要额外工作\t订购零件\t工作完成",
        "sign_supervisor": "主管签名",
        "sign_billing": "计费签名",
        "sign_tech": "技术员签名",
        "sign_date": "日期",
        "page": "页 {page} of {total}",
        "timezone_label": "时区：",
        "printed_label": "打印时间：",
        "report_version": "Report Version:",
        "dash": "-",
        "mandatory_no": "否",
        "wo_barcode": "工单:",
    },
    "EN": {
        "planned_hours": "Planned labor hours:",
        "orig_wo": "Originating WO:",
        "parent_wo": "Parent WO:",
        "priority": "Priority:",
        "pm": "PM:",
        "act_finish": "Actual finish:",
        "worktype": "Work type:",
        "act_start": "Actual start:",
        "pm_due": "PM due date:",
        "compliance": "Compliance:",
        "status": "Status:",
        "sap_wbs": "SAP WBS:",
        "reported_by": "Reported by:",
        "report_date": "Report date:",
        "persongroup": "Person group:",
        "manager": "Manager:",
        "supervisor": "Supervisor:",
        "dept": "Department:",
        "jobplan": "Job plan:",
        "eq_serial": "Equipment serial:",
        "location": "Location:",
        "asset": "Asset:",
        "craft": "Craft:",
        "shift": "Shift:",
        "lightboard": "Light board?:",
        "target_start": "Target start:",
        "target_finish": "Target finish:",
        "equipment_type": "Equipment Type:",
        "handover": "Shift handover (do not enter in Maximo)",
        "status_history": "Status history",
        "status_memo": "Memo",
        "status_changed": "Changed",
        "status_by": "Changed by",
        "status_col": "Status",
        "steps": "Operating steps",
        "step_id": "Task",
        "mandatory": "Mandatory?",
        "labor_summary": "Labor summary:",
        "labor_actual": "Actual labor:",
        "labor_header": "Labor code Name Task Start End Reg OT Entered by",
        "parts": "Materials:",
        "parts_header": "Storeroom Cost Qty used Qty req Description Item",
        "meters": "Meter readings",
        "completion_flags": "Missed PM Extra work Order parts Work complete",
        "sign_supervisor": "Supervisor",
        "sign_billing": "Billing",
        "sign_tech": "Technician",
        "sign_date": "Date",
        "page": "Page {page} of {total}",
        "timezone_label": "Time zone:",
        "printed_label": "Printed:",
        "report_version": "Report Version:",
        "dash": "-",
        "mandatory_no": "No",
        "wo_barcode": "Work order:",
    },
}

WO_TYPE_TITLES = {
    "CN": {
        "PM": "预防性维护工单",
        "EM": "紧急维修工单",
        "RC": "Ride Critical 工单",
        "PC": "计划维修工单",
        "UCH": "非计划维修工单（高）",
        "UCL": "非计划维修工单（低）",
        "FC": "设施关键工单",
        "INS": "检查工单",
        "INSPECTION": "检查工单",
        "CAL": "校准工单",
        "PRJ": "项目工单",
        "SERV": "服务工单",
        "REPAIR": "维修工单",
        "OVERHAUL": "大修工单",
    },
    "EN": {
        "PM": "Preventive Maintenance Work Order",
        "EM": "Emergency Work Order",
        "RC": "Ride Critical Work Order",
        "PC": "Planned Corrective Work Order",
        "UCH": "Unplanned Corrective Work Order (High)",
        "UCL": "Unplanned Corrective Work Order (Low)",
        "FC": "Facility Critical Work Order",
        "INS": "Inspection Work Order",
        "INSPECTION": "Inspection Work Order",
        "CAL": "Calibration Work Order",
        "PRJ": "Project Work Order",
        "SERV": "Service Work Order",
        "REPAIR": "Repair Work Order",
        "OVERHAUL": "Overhaul Work Order",
    },
}

_BODY = ParagraphStyle("body", fontName=FONT, fontSize=8.6, leading=12.2, wordWrap="CJK")
_BODY_LATIN = ParagraphStyle("body_latin", parent=_BODY, wordWrap=None)
_TASK_ID = ParagraphStyle("task_id", parent=_BODY, alignment=TA_CENTER)
_RIGHT = ParagraphStyle("right", parent=_BODY, alignment=TA_RIGHT)
_WO_SECTION = ParagraphStyle("wo_section", parent=_BODY, fontSize=9.2, leading=12.5, spaceBefore=4, spaceAfter=2)
_CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")


class BoldText(Flowable):
    """Single-line faux-bold text; CID fonts ship without a bold face."""

    def __init__(self, text: str, size: float = 8.6, align: str = "right", stroke: float = 0.28):
        super().__init__()
        self.text = text
        self.size = size
        self.align = align
        self.stroke = stroke

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        self.height = self.size * 1.42
        return self.width, self.height

    def draw(self) -> None:
        text_width = pdfmetrics.stringWidth(self.text, FONT, self.size)
        if self.align == "right":
            x = self.width - text_width
        elif self.align == "center":
            x = (self.width - text_width) / 2
        else:
            x = 0
        self.canv.saveState()
        self.canv.setLineWidth(self.stroke)
        text = self.canv.beginText(x, self.size * 0.3)
        text.setFont(FONT, self.size)
        text.setTextRenderMode(2)
        text.textOut(self.text)
        self.canv.drawText(text)
        self.canv.restoreState()


@dataclass
class PrintDocument:
    lang: str
    heading: str
    title: str
    report_name: str
    header_rows: list[tuple[str, Any, str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    total_duration: float = 0.0
    body_blocks: list[Any] = field(default_factory=list)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _duration(value: Any) -> str:
    return f"{_number(value):.2f}" if _text(value) else ""


def _yes_no(value: Any, labels: dict[str, str]) -> str:
    text = _text(value)
    if not text:
        return ""
    return labels["yes"] if text in {"1", "Y", "TRUE", "True"} else labels["no"]


def _paragraph(value: Any, style: ParagraphStyle | None = None) -> Paragraph:
    content = _text(value)
    if style is None:
        style = _BODY if _CJK.search(content) else _BODY_LATIN
    return Paragraph(escape(content).replace("\n", "<br/>"), style)


def _rich_paragraph(title: Any, body: Any, style: ParagraphStyle | None = None) -> Paragraph:
    parts: list[str] = []
    if _text(title):
        parts.append(escape(_text(title)))
    if _text(body):
        if parts:
            parts.append("<br/><br/>")
        parts.append(escape(_text(body)).replace("\n", "<br/>"))
    content = "".join(parts) if parts else " "
    if style is None:
        style = _BODY if _CJK.search(content) else _BODY_LATIN
    return Paragraph(content, style)


def _header_table(doc: PrintDocument) -> Table:
    label_w, value_w = 30 * mm, 66 * mm
    widths = [label_w, value_w, label_w - 4 * mm, BODY_WIDTH - 2 * label_w - value_w + 4 * mm]
    data, spans = [], []
    for index, (left_label, left_value, right_label, right_value) in enumerate(doc.header_rows):
        if right_label == "__span__":
            data.append([BoldText(left_label), _paragraph(left_value), "", ""])
            spans.append(("SPAN", (1, index), (3, index)))
        else:
            data.append([
                BoldText(left_label) if left_label else "",
                _paragraph(left_value),
                BoldText(right_label) if right_label else "",
                _paragraph(right_value),
            ])
    table = Table(data, colWidths=widths, hAlign="LEFT", splitByRow=1, splitInRow=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 0.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0.6),
        *spans,
    ]))
    return table


def _task_table(doc: PrintDocument) -> Table:
    labels = LABELS[doc.lang]
    id_w, duration_w = 20 * mm, 26 * mm
    widths = [id_w, BODY_WIDTH - id_w - duration_w, duration_w]
    data: list[list[Any]] = [[
        BoldText(labels["task_id"], align="center"),
        BoldText(labels["task_desc"], align="left"),
        BoldText(labels["task_duration"], align="right"),
    ]]
    for task in doc.tasks:
        duration = _number(task.get("duration"))
        data.append([
            _paragraph(task.get("id"), _TASK_ID),
            _rich_paragraph(task.get("title"), task.get("body")),
            _paragraph(f"{duration:.2f}" if duration else "", _RIGHT),
        ])
    if not doc.tasks:
        data.append(["", _paragraph(labels["no_tasks"]), ""])
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1, splitInRow=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, colors.black),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 13),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _total_table(doc: PrintDocument) -> Table:
    labels = LABELS[doc.lang]
    value_w = 26 * mm
    table = Table(
        [[BoldText(labels["total"], size=9), BoldText(f"{doc.total_duration:.2f}", size=9)]],
        colWidths=[BODY_WIDTH - value_w, value_w],
        hAlign="LEFT",
    )
    table.setStyle(TableStyle([
        ("LINEABOVE", (1, 0), (1, 0), 0.7, colors.black, None, None, None, 2, 1.6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _printed_stamp(lang: str) -> str:
    now = datetime.now(ZoneInfo(TIMEZONE))
    if lang == "CN":
        return now.strftime("%Y/%m/%d %I:%M") + ("上午" if now.hour < 12 else "下午")
    return now.strftime("%Y/%m/%d %I:%M %p")


def _numbered_canvas(doc: PrintDocument):
    labels = LABELS[doc.lang]
    printed = labels["printed"].format(stamp=_printed_stamp(doc.lang))
    timezone_line = labels["timezone"].format(tz=TIMEZONE)

    class NumberedCanvas(pdf_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages: list[dict[str, Any]] = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._draw_footer(total)
                super().showPage()
            super().save()

        def _draw_footer(self, total: int) -> None:
            base = MARGIN_BOTTOM - 12 * mm
            self.saveState()
            self.setFont(FONT, 7.5)
            self.drawString(MARGIN_X, base + 4.2 * mm, doc.report_name)
            self.setFillColor(LINK_BLUE)
            self.drawString(MARGIN_X, base, f"Version:{REPORT_VERSION}")
            self.setFillColor(colors.black)
            self.setFont(FONT, 8)
            center = PAGE_WIDTH / 2
            self.drawCentredString(center, base + 4.2 * mm, labels["page"].format(page=self._pageNumber, total=total))
            self.drawCentredString(center, base, timezone_line)
            self.drawRightString(PAGE_WIDTH - MARGIN_X, base + 2 * mm, printed)
            self.restoreState()

    return NumberedCanvas


def render(doc: PrintDocument) -> bytes:
    buffer = BytesIO()
    template = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title=doc.title,
        author="SHDR RAG",
        subject=doc.report_name,
    )
    story: list[Any] = [
        BoldText(doc.heading, size=13, align="center", stroke=0.35),
        Spacer(1, 4 * mm),
        _header_table(doc),
    ]
    if doc.body_blocks:
        story.append(Spacer(1, 3 * mm))
        story.extend(doc.body_blocks)
    story.extend([
        Spacer(1, 5 * mm),
        _task_table(doc),
        _total_table(doc),
    ])
    template.build(story, canvasmaker=_numbered_canvas(doc))
    return buffer.getvalue()


def _desc(row: dict[str, Any], lang: str, en_key: str = "description", zh_key: str = "description_zh") -> str:
    return pick_lang(row, lang, en_key, zh_key)


def _long(row: dict[str, Any], lang: str) -> str:
    return pick_lang(row, lang, "long_description_en", "long_description_zh")


def _craft(row: dict[str, Any], lang: str) -> str:
    code = _text(row.get("cust_craft"))
    description = pick_lang(row, lang, "craft_description_en", "craft_description_zh")
    return f"{code} - {description}" if code and description else code


def jobplan_pdf(detail: dict[str, Any], lang: str) -> bytes:
    labels = LABELS[lang]
    jp = detail["jobplan"]
    tasks = [
        {
            "id": task.get("jptask"),
            "title": _desc(task, lang),
            "body": _long(task, lang),
            "duration": task.get("taskduration"),
        }
        for task in detail["tasks"]
    ]
    header_rows = [
        (labels["jpnum"], jp.get("jpnum"), "", ""),
        (labels["description"], _desc(jp, lang), "__span__", ""),
        (labels["compliance"], jp.get("cust_compliance"), labels["supervisor"], jp.get("supervisor")),
        (labels["priority"], jp.get("priority"), labels["craft"], _craft(jp, lang)),
        (labels["duration"], _duration(jp.get("cust_wrenchtime")), labels["labor"], jp.get("laborcode")),
        (labels["interruptible"], _yes_no(jp.get("interruptible"), labels), labels["qual_req"], _yes_no(jp.get("cust_qual_req"), labels)),
        (labels["status"], jp.get("status"), labels["frequency"], jp.get("cust_jpfreq")),
        (labels["drawing"], jp.get("cust_drawing"), labels["drawing_rev"], jp.get("cust_partrev")),
        (labels["part_desc"], jp.get("cust_partdesc"), "__span__", ""),
    ]
    body_blocks: list[Any] = []
    plan_long = _long(jp, lang)
    if plan_long:
        body_blocks.append(_paragraph(plan_long))
    return render(PrintDocument(
        lang=lang,
        heading=labels["jobplan_title"],
        title=f"{labels['jobplan_title']} {jp.get('jpnum')}",
        report_name="JobPlanPrint.rpt",
        header_rows=header_rows,
        body_blocks=body_blocks,
        tasks=tasks,
        total_duration=sum(_number(task["duration"]) for task in tasks),
    ))


def _wo_type_title(worktype: Any, lang: str) -> str:
    code = _text(worktype).upper()
    titles = WO_TYPE_TITLES.get(lang, WO_TYPE_TITLES["CN"])
    return titles.get(code, WO_LABELS[lang]["worktype"].replace(":", "") if lang == "EN" else "工单")


def _dash(labels: dict[str, str], value: Any) -> str:
    return _text(value) or labels["dash"]


def _coded(code: Any, description: Any) -> str:
    code, description = _text(code), _text(description)
    if code and description:
        return f"{code} - {description}"
    return code or description


def _jp_line(wo: dict[str, Any], lang: str) -> str:
    jpnum = _text(wo.get("jpnum"))
    if not jpnum:
        return _dash(WO_LABELS[lang], "")
    description = pick_lang(wo, lang, "jp_description_en", "jp_description_zh")
    return f"{jpnum} - {description}" if description else jpnum


def _wo_pair_table(rows: list[tuple[str, Any, str, Any]], split_long: bool = True) -> Table:
    label_w = 32 * mm
    value_w = (BODY_WIDTH - 2 * label_w) / 2
    widths = [label_w, value_w, label_w, value_w]
    data = []
    for left_label, left_value, right_label, right_value in rows:
        data.append([
            BoldText(left_label, align="left"),
            _paragraph(left_value),
            BoldText(right_label, align="left"),
            _paragraph(right_value),
        ])
    kwargs = {"splitByRow": 1}
    if split_long:
        kwargs["splitInRow"] = 1
    table = Table(data, colWidths=widths, hAlign="LEFT", **kwargs)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    return table


def _wo_status_history_table(history: list[dict[str, Any]], labels: dict[str, str]) -> list[Any]:
    blocks: list[Any] = [Paragraph(f"<b>{labels['status_history']}</b>", _WO_SECTION), Spacer(1, 2 * mm)]
    col_w = [BODY_WIDTH * 0.42, BODY_WIDTH * 0.22, BODY_WIDTH * 0.14, BODY_WIDTH * 0.22]
    data: list[list[Any]] = [[
        BoldText(labels["status_memo"], align="left"),
        BoldText(labels["status_changed"], align="left"),
        BoldText(labels["status_by"], align="left"),
        BoldText(labels["status_col"], align="left"),
    ]]
    if history:
        for row in history:
            memo = _text(row.get("memo"))
            changed = _text(row.get("changed_local"))
            changeby = _text(row.get("changeby"))
            status = _text(row.get("status"))
            data.append([_paragraph(memo or labels["dash"]), _paragraph(changed), _paragraph(changeby), _paragraph(status)])
    else:
        data.append([_paragraph(labels["dash"]), _paragraph(labels["dash"]), _paragraph(labels["dash"]), _paragraph(labels["dash"])])
    table = Table(data, colWidths=col_w, repeatRows=1, hAlign="LEFT", splitByRow=1, splitInRow=1)
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    blocks.append(table)
    return blocks


def _wo_steps_table(tasks: list[dict[str, Any]], labels: dict[str, str]) -> Table:
    id_w, mand_w = 16 * mm, 14 * mm
    desc_w = BODY_WIDTH - id_w - mand_w - 12 * mm
    dur_w = 12 * mm
    widths = [id_w, desc_w, mand_w, dur_w]
    data: list[list[Any]] = [[
        BoldText(labels["step_id"], align="center"),
        BoldText("", align="left"),
        BoldText(labels["mandatory"], align="center"),
        BoldText("", align="right"),
    ]]
    for task in tasks:
        data.append([
            _paragraph(task.get("id"), _TASK_ID),
            _rich_paragraph(task.get("title"), task.get("body")),
            _paragraph(labels["mandatory_no"], _TASK_ID),
            _paragraph(labels["dash"], _RIGHT),
        ])
    if not tasks:
        data.append(["", _paragraph(WO_LABELS["CN"]["dash"]), "", ""])
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1, splitInRow=1)
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _wo_canvas(meta: dict[str, str]):
    printed = _printed_stamp(meta["lang"])

    class WOPrintCanvas(pdf_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages: list[dict[str, Any]] = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._draw_page(total)
                super().showPage()
            super().save()

        def _draw_page(self, total: int) -> None:
            labels = WO_LABELS[meta["lang"]]
            self.saveState()
            y = PAGE_HEIGHT - 10 * mm
            self.setFont(FONT, 12)
            self.drawCentredString(PAGE_WIDTH / 2, y, meta["type_title"])
            y -= 5.5 * mm
            self.setFont(FONT, 9)
            self.drawCentredString(PAGE_WIDTH / 2, y, meta["subtitle"])
            y -= 4.5 * mm
            self.setFont(FONT, 8.5)
            self.drawString(MARGIN_X, y, f"*{meta['wonum']}*{labels['wo_barcode']}")

            base = WO_MARGIN_BOTTOM - 14 * mm
            self.setFont(FONT, 7.5)
            self.drawString(MARGIN_X, base + 8 * mm, WO_DISPLAY_TZ)
            self.drawString(MARGIN_X + 38 * mm, base + 8 * mm, WO_REPORT_NAME)
            self.drawRightString(PAGE_WIDTH - MARGIN_X, base + 8 * mm, printed)
            self.drawString(MARGIN_X, base + 4 * mm, labels["timezone_label"])
            self.drawRightString(PAGE_WIDTH - MARGIN_X, base + 4 * mm, labels["printed_label"])
            self.setFont(FONT, 8)
            self.drawCentredString(PAGE_WIDTH / 2, base + 6 * mm, labels["page"].format(page=self._pageNumber, total=total))
            self.drawCentredString(PAGE_WIDTH / 2, base + 1.5 * mm, f"{labels['report_version']} {WO_REPORT_VERSION}")
            self.restoreState()

    return WOPrintCanvas


def _render_woprint1(doc: PrintDocument, meta: dict[str, str], story: list[Any]) -> bytes:
    buffer = BytesIO()
    template = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=WO_MARGIN_TOP,
        bottomMargin=WO_MARGIN_BOTTOM,
        title=doc.title,
        author="SHDR RAG",
        subject=WO_REPORT_NAME,
    )
    template.build(story, canvasmaker=_wo_canvas(meta))
    return buffer.getvalue()


def workorder_pdf(detail: dict[str, Any], lang: str, jobplan: dict[str, Any] | None = None) -> bytes:
    """Render a work order using the Maximo WOPrint1.rpt layout."""
    labels = WO_LABELS[lang]
    wo = detail["workorder"]
    tasks = [
        {
            "id": task.get("taskid"),
            "title": _desc(task, lang, "description_en", "description_zh"),
            "body": _long(task, lang),
            "duration": task.get("estdur"),
        }
        for task in detail["tasks"]
    ]
    if not tasks and jobplan and jobplan.get("tasks"):
        tasks = [
            {
                "id": task.get("jptask"),
                "title": _desc(task, lang),
                "body": _long(task, lang),
                "duration": task.get("taskduration"),
            }
            for task in jobplan["tasks"]
        ]

    wo_desc = _desc(wo, lang, "description_en", "description_zh")
    subtitle = f"{wo.get('wonum')} - {wo_desc}" if wo_desc else _text(wo.get("wonum"))
    meta = {
        "lang": lang,
        "wonum": _text(wo.get("wonum")),
        "type_title": _wo_type_title(wo.get("worktype"), lang),
        "subtitle": subtitle,
    }

    dash = labels["dash"]
    field_rows = [
        (labels["planned_hours"], _duration(wo.get("estlabhrs")) or dash, labels["orig_wo"], _dash(labels, wo.get("origrecordid"))),
        (labels["parent_wo"], _dash(labels, wo.get("parent")), labels["priority"], _dash(labels, wo.get("wopriority"))),
        (labels["pm"], _dash(labels, wo.get("pmnum")), labels["act_finish"], _dash(labels, wo.get("actfinish_local"))),
        (labels["worktype"], _dash(labels, wo.get("worktype")), labels["act_start"], _dash(labels, wo.get("actstart_local"))),
        (labels["pm_due"], _dash(labels, wo.get("pmdue_local")), labels["compliance"], _dash(labels, wo.get("cust_compliance"))),
        (labels["status"], _dash(labels, wo.get("status")), labels["sap_wbs"], dash),
        (labels["reported_by"], _dash(labels, wo.get("reportedby")), labels["report_date"], _dash(labels, wo.get("report_local"))),
        (labels["persongroup"], _dash(labels, wo.get("persongroup")), labels["manager"], dash),
        (labels["supervisor"], _dash(labels, wo.get("supervisor")), labels["dept"], dash),
        (labels["jobplan"], _jp_line(wo, lang), labels["eq_serial"], dash),
        (labels["location"], _coded(wo.get("location"), wo.get("location_description")), labels["asset"], _coded(wo.get("assetnum"), wo.get("asset_description"))),
        (labels["craft"], _dash(labels, wo.get("cust_craft")), labels["shift"], _dash(labels, wo.get("crewid"))),
        (labels["lightboard"], _yes_no(wo.get("cust_guestimpact"), LABELS[lang]) or dash, "", dash),
        (labels["target_start"], _dash(labels, wo.get("targstart_local")), labels["target_finish"], _dash(labels, wo.get("targcomp_local"))),
    ]

    story: list[Any] = [_wo_pair_table(field_rows), Spacer(1, 3 * mm)]
    story.append(_paragraph(f"{labels['equipment_type']} {dash}"))
    story.append(Spacer(1, 2 * mm))
    story.append(_paragraph(labels["handover"]))
    story.append(Spacer(1, 2 * mm))
    story.append(_paragraph(labels["labor_summary"]))
    story.append(_paragraph(labels["labor_actual"]))
    story.append(_paragraph(labels["labor_header"]))
    story.append(Spacer(1, 2 * mm))
    story.append(_paragraph(labels["parts"]))
    story.append(_paragraph(labels["parts_header"]))
    story.append(Spacer(1, 2 * mm))
    story.append(_paragraph(labels["meters"]))
    story.append(Spacer(1, 4 * mm))
    story.extend(_wo_status_history_table(detail.get("status_history") or [], labels))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(f"<b>{labels['steps']}</b>", _WO_SECTION))
    story.append(_wo_steps_table(tasks, labels))
    story.append(Spacer(1, 6 * mm))
    story.append(_paragraph(labels["completion_flags"]))
    story.append(Spacer(1, 8 * mm))
    sign_w = BODY_WIDTH / 3
    sign_table = Table(
        [[
            _paragraph(f"{labels['sign_supervisor']} {labels['sign_date']}"),
            _paragraph(f"{labels['sign_billing']} {labels['sign_date']}"),
            _paragraph(f"{labels['sign_tech']} {labels['sign_date']}"),
        ]],
        colWidths=[sign_w, sign_w, sign_w],
        hAlign="LEFT",
    )
    sign_table.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.black), ("TOPPADDING", (0, 0), (-1, -1), 16)]))
    story.append(sign_table)

    wo_long = _long(wo, lang)
    if wo_long:
        story.insert(1, Spacer(1, 2 * mm))
        story.insert(2, _paragraph(wo_long))

    print_doc = PrintDocument(
        lang=lang,
        heading=meta["type_title"],
        title=f"WO {wo.get('wonum')}",
        report_name=WO_REPORT_NAME,
        tasks=tasks,
    )
    return _render_woprint1(print_doc, meta, story)
