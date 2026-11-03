"""Bridge Maximo downtime rows to Splunk Apex searches."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


from .downtime_catalog import attraction_for_location

SHANGHAI = ZoneInfo("Asia/Shanghai")
BASE_SEARCH = "search index=sdl_apex_pro sourcetype=apex:raw:sdr"
_APEX_ATTRACTIONS_PATH = Path(__file__).with_name("attractions.json")
MAX_APEX_WINDOW = timedelta(hours=24)
_APEX_BY_LOCATION_PREFIX = {
    "SDLAIRR": ["SDR_SDL_Roaring Rapids"],
    "SDLAISOA": [
        "SDR_SDL_Soaring Over the Horizon (Theater A)",
        "SDR_SDL_Soaring Over the Horizon (Theater B)",
    ],
    "SDLFLESC": [],
    "SDLFLHPS": ["SDR_SDL_Hunny Pot Spin"],
    "SDLFLMAWP": ["SDR_SDL_The Many Adventures of Winnie the Pooh"],
    "SDLFLPPF": ["SDR_SDL_Peter Pan's Flight"],
    "SDLFLSDMT": ["SDR_SDL_Seven Dwarfs Mine Train"],
    "SDLFLVCG": ["SDR_SDL_Voyage to the Crystal Grotto"],
    "SDLFLZTP": ["SDR_SDL_Hot Pursuit"],
    "SDLGIDF": ["SDR_SDL_Dumbo the Flying Elephant"],
    "SDLGIFC": ["SDR_SDL_Fantasia Carousel"],
    "SDLTCPOTC": [
        "SDR_SDL_Pirates of the Caribbean Battle for the Sunken Treasure"
    ],
    "SDLTLBLPR": ["SDR_SDL_Buzz Lightyear Planet Rescue"],
    "SDLTLJP": ["SDR_SDL_Jet Packs"],
    "SDLTLPR": ["SDR_SDL_TRON Lightcycle Power Run"],
    "SDLTSLRR": ["SDR_SDL_Rex's Racer"],
    "SDLTSLSL": ["SDR_SDL_Slinky Dog Spin"],
    "SDLTSLWR": ["SDR_SDL_Woody's Roundup"],
}


class DowntimeApexError(ValueError):
    """Safe error for the combined downtime + Apex UI."""


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _load_apex_attractions() -> list[dict[str, str]]:
    if not _APEX_ATTRACTIONS_PATH.is_file():
        return []
    return json.loads(_APEX_ATTRACTIONS_PATH.read_text(encoding="utf-8"))


def resolve_apex_attrnames(
    *,
    location: str | None = None,
    attraction_name_en: str | None = None,
    apex_attrname: str | None = None,
) -> list[str]:
    if apex_attrname and str(apex_attrname).strip():
        value = str(apex_attrname).strip()
        return [value if value.startswith("SDR_SDL_") else f"SDR_SDL_{value}"]

    normalized_location = str(location or "").strip().upper()
    location_matches = [
        (prefix, attrnames)
        for prefix, attrnames in _APEX_BY_LOCATION_PREFIX.items()
        if normalized_location == prefix or normalized_location.startswith(prefix)
    ]
    if location_matches:
        _, attrnames = max(location_matches, key=lambda item: len(item[0]))
        return list(attrnames)

    catalog = attraction_for_location(location)
    candidates = [
        attraction_name_en or "",
        catalog["name_en"] if catalog else "",
    ]
    normalized_targets = [_normalize_name(item) for item in candidates if item]
    if not normalized_targets:
        return []

    matches: list[tuple[int, str]] = []
    for entry in _load_apex_attractions():
        label = entry.get("label") or ""
        value = entry.get("value") or ""
        norm_label = _normalize_name(label)
        for target in normalized_targets:
            if not target:
                continue
            score = 0
            if norm_label == target:
                score = 100
            elif target in norm_label or norm_label in target:
                score = 80 + min(len(target), len(norm_label))
            if score:
                matches.append((score, value))
    if not matches:
        return []
    best_score = max(score for score, _ in matches)
    # Keep equally good matches, e.g. Soaring Theater A and Theater B.
    return list(dict.fromkeys(value for score, value in matches if score == best_score))


def parse_site_local_time(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=SHANGHAI)
        except ValueError:
            continue
    return None


def build_apex_window(payload: dict[str, Any]) -> tuple[int, int, dict[str, Any]]:
    try:
        raw_padding = payload.get("padding_minutes", 15)
        if raw_padding is None or isinstance(raw_padding, bool) or not re.fullmatch(r"[0-9]+", str(raw_padding)):
            raise ValueError("invalid padding")
        padding = int(raw_padding)
        if not 0 <= padding <= 240:
            raise ValueError("invalid padding")
    except (TypeError, ValueError) as exc:
        raise DowntimeApexError("Apex 时间缓冲必须是 0–240 分钟。") from exc
    for field in ("downtime_start_local", "downtime_end_local", "report_local", "snapshot_end_local"):
        if payload.get(field) and parse_site_local_time(payload[field]) is None:
            raise DowntimeApexError(f"{field} 时间格式无效，必须为 YYYY-MM-DD HH:MM:SS。")
    start = parse_site_local_time(payload.get("downtime_start_local"))
    end = parse_site_local_time(payload.get("downtime_end_local"))
    report = parse_site_local_time(payload.get("report_local"))
    if start is None:
        start = report
    if start is None:
        raise DowntimeApexError("缺少停机开始时间或事件时间，无法构造 Apex 检索窗口。")
    if end is None:
        end = parse_site_local_time(payload.get("snapshot_end_local")) or datetime.now(SHANGHAI)
    if end < start:
        raise DowntimeApexError("停机恢复时间不能早于开始时间。")
    window_start = start - timedelta(minutes=padding)
    window_end = end + timedelta(minutes=padding)
    if window_end <= window_start:
        window_end = window_start + timedelta(seconds=1)
    capped = False
    if window_end - window_start > MAX_APEX_WINDOW:
        window_end = window_start + MAX_APEX_WINDOW
        capped = True
    meta = {
        "window_start_local": window_start.strftime("%Y-%m-%d %H:%M:%S"),
        "window_end_local": window_end.strftime("%Y-%m-%d %H:%M:%S"),
        "padding_minutes": padding,
        "capped": capped,
        "timezone": "Asia/Shanghai",
        "start_source": "downtime_start" if payload.get("downtime_start_local") else "report",
        "end_source": "downtime_end" if payload.get("downtime_end_local") else "snapshot",
        "event_end_local": end.strftime("%Y-%m-%d %H:%M:%S"),
        "zero_duration_expanded_seconds": 1 if end == start and padding == 0 else 0,
    }
    return int(window_start.timestamp()), int(window_end.timestamp()), meta


