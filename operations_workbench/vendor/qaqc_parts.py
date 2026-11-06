"""Parsing helpers for QAQC work orders: part numbers, report file names, engineer comments.

Part numbers are typed by hand into the work-order description, e.g.

    G1/G3:CSFAB委托检测SDLPIRAMM3017非自制首制件非G3件检测数量10件   检测项目：VT/PT/DT
    MT SDLROARMT8100-20241009 复测              -> SDLROARMT8100 (the date is not a suffix)
    Part# 00906-55-0050                          -> 00906-55-0050 (only when no SDL code)

The same code appears in the SharePoint report names
(``DT_SDLPIRAMM3017_WO7777953_N_2026.10.15-MRB.pdf``), which is the fallback when the
description carries none.  Rules follow RSE_ME ``qaqc/parts.py`` and ``qaqc/sharepoint.py``.
"""
from __future__ import annotations

import re

PART_RE = re.compile(
    r"(?<![A-Z0-9])SDL(?:"
    r"[A-Z]{2,6}\d{3,8}"
    r"|\d{3,6}-\d{2}-\d{4}[A-Z]?"
    r"|\d{6,12}"
    r")"
    r"(?:-(?:\d{1,3}[A-Z]?|[A-Z]{1,8}))?(?![0-9])",
    re.I,
)
DRAWING_RE = re.compile(r"(?<![\d-])(\d{5}-\d{2}-\d{4}[A-Z]?)(?![\d-])")
STOPWORDS = {"SDLBOHFOS"}

# Attraction code inside SDL part numbers (SDLPIRAMM3017 -> PIRA) mapped to the
# downtime catalog location; codes without a confident mapping are shown as-is.
PART_ATTRACTIONS = {
    "TRON": "SDLTLPR", "DWAR": "SDLFLSDMT", "PIRA": "SDLTCPOTC", "PETE": "SDLFLPPF",
    "ZTHP": "SDLFLZTP", "WOOD": "SDLTSLWR", "PRAD": "SDLBOHPRD", "SOAR": "SDLAISOA",
    "JEPA": "SDLTLJP", "SLIN": "SDLTSLSL", "BUZZ": "SDLTLBLPR", "DUBO": "SDLGIDF",
    "POOH": "SDLFLMAWP", "ROAR": "SDLAIRR", "RCRA": "SDLTSLRR", "CARO": "SDLGIFC",
    "WDTH": "DTDWT", "CUPS": "SDLFLHPS",
}
_PART_ATTRACTION_RE = re.compile(r"^SDL([A-Z]{4})[A-Z]{0,2}\d")
# Typed without the L ("SDWOODMV3051"); only accepted for a known attraction code.
_MISSING_L_RE = re.compile(r"(?<![A-Z])SD([A-Z]{4})(M[A-Z]\d{3,6})(?![0-9])")

NAME_RE = re.compile(
    r"^(?P<method>[^_]+)_"
    r"(?P<component>[A-Za-z0-9\-]+)_"
    r"WO0*(?P<wonum>\d{3,12})"
    r"(?:_(?P<batch>[A-Za-z]))?"
    r"(?:_(?P<date>\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}))?"
    r"(?P<mrb>-MRB)?"
    r"\.(?P<ext>[A-Za-z0-9]{1,5})$",
    re.I,
)
WO_RE = re.compile(r"WO0*(\d{3,12})", re.I)
DOCUMENT_EXT = {"pdf", "jpg", "jpeg", "png", "gif", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "txt", "csv"}

_DATE_RE = re.compile(r"(\d{4}[./\-年]\d{1,2}[./\-月]\d{1,2}日?|\d{1,2}/\d{1,2}/\d{4})")
_SIGNER_RE = re.compile(r"^(?:[\u4e00-\u9fff]{2,4}|[A-Za-z][A-Za-z .'\-]{1,30})$")

# Ordered by severity: the first matching disposition wins.
DISPOSITIONS = [
    ("scrap", "报废", ("报废", "scrap")),
    ("return", "退回供应商", ("退回", "退货", "返厂", "return to vendor", "rtv")),
    ("rework", "返工 / 返修", ("返工", "返修", "修复", "修磨", "重新加工", "rework", "repair")),
    ("reinspect", "复检", ("复检", "复测", "重新检测", "再次检测", "re-inspect", "reinspect")),
    ("accept", "让步接受", ("让步", "可以接受", "接受使用", "可接受", "继续使用", "可以使用", "use as is", "use-as-is", "accept")),
]
DISPOSITION_LABELS = {key: label for key, label, _ in DISPOSITIONS}
_NEGATED_ACCEPT_RE = re.compile(r"不(?:可以|可|能|予)?(?:接受|使用)")
# MRB tasks are either the inspector's finding ("尺寸超差", "VT不合格") or the engineer's
# disposition ("Engineering Disposition"); the latter decides the case.
ENGINEER_TASK_RE = re.compile(r"disposition|处置|工程师|工程意见|engineer", re.I)


def is_engineer_task(title: str | None) -> bool:
    return bool(ENGINEER_TASK_RE.search(title or ""))


def parse_part_nos(text: str | None) -> list[str]:
    """Every drawing / part number in ``text``, in order, de-duplicated."""
    if not text:
        return []
    upper = str(text).upper()
    found: list[str] = []
    for match in PART_RE.finditer(upper):
        token = match.group(0).strip("-")
        if token not in STOPWORDS and token not in found:
            found.append(token)
    if found:
        return found
    for match in _MISSING_L_RE.finditer(upper):
        if match.group(1) in PART_ATTRACTIONS and f"SDL{match.group(1)}{match.group(2)}" not in found:
            found.append(f"SDL{match.group(1)}{match.group(2)}")
    if found:
        return found
    for match in DRAWING_RE.finditer(upper):
        if match.group(1) not in found:
            found.append(match.group(1))
    return found


def parse_part_no(text: str | None) -> str:
    parts = parse_part_nos(text)
    return parts[0] if parts else ""


def part_attraction(part_no: str | None) -> str:
    """Catalog location for an SDL part number, or the raw 4-letter code, or ``""``."""
    match = _PART_ATTRACTION_RE.match(part_no or "")
    if not match:
        return ""
    return PART_ATTRACTIONS.get(match.group(1), match.group(1))


def description_attraction(text: str | None, catalog: list[dict]) -> str:
    """Catalog location whose Chinese name or alias (2+ characters) appears in the description."""
    if not text:
        return ""
    names = sorted(((name, item["location"]) for item in catalog
                    for name in [item["name_zh"], *item.get("aliases", [])] if len(name) >= 2),
                   key=lambda pair: -len(pair[0]))
    return next((location for name, location in names if name in text), "")


def parse_report_name(name: str | None) -> dict | None:
    """``DT_SDLPIRAMM3017_WO7777953_N_2026.10.15-MRB.pdf`` -> its parts; ``None`` if unrecognised."""
    if not name:
        return None
    match = NAME_RE.match(name.strip())
    if not match:
        wo = WO_RE.search(name)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if not wo:
            return None
        return {"method": "", "component": "", "wonum": wo.group(1), "batch": "", "date_text": "",
                "is_mrb": name.upper().rstrip().split(".")[0].endswith("-MRB"), "extension": ext}
    parts = match.groupdict()
    return {
        "method": (parts["method"] or "").strip().upper(),
        "component": (parts["component"] or "").strip().upper(),
        "wonum": parts["wonum"] or "",
        "batch": (parts["batch"] or "").strip().upper(),
        "date_text": (parts["date"] or "").strip(),
        "is_mrb": bool(parts["mrb"]),
        "extension": (parts["ext"] or "").strip().lower(),
    }


def is_comment(text: str | None, *descriptions: str | None) -> bool:
    """True when someone typed something beyond a copy of the description (RSE_ME ``has_comment``)."""
    body = " ".join(str(text or "").split())
    return bool(body) and all(body != " ".join(str(d or "").split()) for d in descriptions)


def comment_meta(text: str | None) -> dict:
    """Best-effort signer / date from a hand-typed conclusion; the text itself stays verbatim."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    signer, signed = "", ""
    for index in range(len(lines) - 1, max(-1, len(lines) - 5), -1):
        line = lines[index]
        date = _DATE_RE.search(line)
        if date and not signed:
            signed = date.group(1)
            rest = line.replace(date.group(1), "").strip(" ,，;；")
            if rest and _SIGNER_RE.match(rest):
                signer = rest
            continue
        if signed and not signer and _SIGNER_RE.match(line):
            signer = line
            break
    body = [line for line in lines if line not in {signer, signed} and not (signed and line.startswith(signed))]
    return {"signer": signer, "signed": signed, "summary": body[0] if body else (lines[0] if lines else "")}


def classify_disposition(*texts: str | None) -> str:
    """Keyword-based disposition of an MRB case (reference only, never a verdict)."""
    joined = " ".join(str(text or "") for text in texts).lower()
    if not joined.strip():
        return ""
    joined = _NEGATED_ACCEPT_RE.sub(" ", joined)
    for key, _label, words in DISPOSITIONS:
        if any(word in joined for word in words):
            return key
    return "other"
