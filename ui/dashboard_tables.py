from __future__ import annotations

from collections.abc import Callable, Sequence
from html import escape

import pandas as pd

from data.dashboard_lanes import row_current_add_text, row_final_action
from data.entry_display import build_entry_display


BADGE_STYLES = {
    "green": ("#F4FAF6", "#166534", "#DDEBE2"),
    "blue": ("#F4F7FB", "#36516F", "#DCE6F2"),
    "yellow": ("#FCFAF0", "#7A5C12", "#EEE6C8"),
    "orange": ("#FBF7F1", "#7C4A1D", "#ECDCC8"),
    "red": ("#FBF5F5", "#8A1F1F", "#ECD5D5"),
    "deepred": ("#FDF1F1", "#6F1111", "#E7B9B9"),
    "gray": ("#F8FAFC", "#475569", "#E4EAF1"),
}

TECHNICAL_ERROR_HINTS = (
    "fmp",
    "curl",
    "traceback",
    "timed out",
    "timeout",
    "handshake",
    "network",
    "connection",
    "httperror",
    "ssl",
)

BadgeColorFn = Callable[[str, object, pd.Series | None], str]
ActionHtmlFn = Callable[[str], str]


def _header_cell_html(value: object, align: object = None) -> str:
    align_class = " align-right" if align == "right" else " align-center" if align == "center" else ""
    return f'<div class="decision-header{align_class}">{escape(str(value))}</div>'


def _decision_table_row_html(
    row: pd.Series,
    columns: Sequence[dict] | None = None,
    action_html_builder: ActionHtmlFn | None = None,
    badge_color_for_cell: BadgeColorFn | None = None,
) -> str:
    symbol = str(row.get("symbol", "")).upper()
    safe_symbol = escape(symbol)
    cells = "".join(
        _decision_table_cell_html(row, definition, symbol, action_html_builder, badge_color_for_cell)
        for definition in (columns or [])
    )
    return (
        f'<div class="decision-grid decision-row" data-dashboard-drawer-open="{safe_symbol}" '
        f'title="打开 {safe_symbol} 右侧详情面板">{cells}</div>'
    )


def _decision_table_cell_html(
    row: pd.Series,
    definition: dict,
    symbol: str,
    action_html_builder: ActionHtmlFn | None = None,
    badge_color_for_cell: BadgeColorFn | None = None,
) -> str:
    key = str(definition["key"])
    align_class = " align-right" if definition.get("align") == "right" else ""
    if key == "symbol":
        return (
            '<div class="decision-cell decision-cell-stack stock-cell">'
            f'<strong>{escape(symbol)}</strong>'
            "</div>"
        )
    if key == "priceMarket":
        price = _display_table_text(_safe_table_value("price", row.get("price")), fallback="当前价待补")
        market_cap = _display_table_text(_safe_table_value("marketCap", row.get("marketCap")), fallback="市值待补")
        return (
            '<div class="decision-cell decision-cell-stack price-market-cell">'
            f'<strong>{escape(price)}</strong>'
            f'<span>{escape(market_cap)}</span>'
            "</div>"
        )
    if key == "actionSummary":
        action = _display_table_text(_safe_table_value("action", row_final_action(row)), fallback="待复核")
        valuation = _display_table_text(_safe_table_value("valuationStatus", row.get("valuationStatus")), fallback="估值待确认")
        entry_label, _entry_grade, _entry_raw = _entry_rating_display_parts(row)
        raw_entry = str(row.get("entryRating") or "")
        if "击球区" in valuation or "击球区" in raw_entry or "接近买点" in raw_entry:
            valuation = entry_label or valuation
        position = _display_table_text(row_current_add_text(row), fallback="")
        secondary_parts = [_short_badge_text(valuation)]
        if position and position not in {"不建议新增", "待补"}:
            secondary_parts.append(position)
        elif position == "不建议新增":
            secondary_parts.append(position)
        return (
            '<div class="decision-cell decision-cell-stack action-cell">'
            f'<strong>{escape(_short_badge_text(action))}</strong>'
            f'<span>{escape(" · ".join(secondary_parts))}</span>'
            "</div>"
        )
    if key == "dataStatus":
        value = _display_table_text(_safe_table_value(key, row.get(key)), fallback="待复核")
        return f'<div class="decision-cell">{_data_status_dot_html(value)}</div>'
    if key == "actions":
        action_html = action_html_builder(symbol) if action_html_builder else ""
        return f'<div class="decision-cell action-view-cell">{action_html}</div>'
    if key == "entryRating":
        return _entry_rating_cell_html(row)
    value = _safe_table_value(key, row.get(key, ""))
    value = _display_table_text(value, fallback="待补")
    if definition.get("kind") == "badge":
        color = badge_color_for_cell(key, value, row) if badge_color_for_cell else "gray"
        return _badge_cell_html(_compact_watchlist_badge_text(key, value), color, title=value)
    return f'<div class="decision-cell{align_class}">{escape(str(value))}</div>'


def _display_table_text(value: object, fallback: str = "待补") -> str:
    if _looks_like_technical_error(value):
        return "数据异常"
    text = str(value or "").strip()
    if not text or text.lower() in {"n/a", "none", "nan", "null"}:
        return fallback
    return text


def _compact_watchlist_badge_text(key: str, value: object) -> str:
    text = str(value or "").strip()
    if key == "qualityRating":
        first = text.split(" ", 1)[0].strip()
        if first:
            return first
    return _short_badge_text(text)


def _entry_rating_cell_html(row: pd.Series) -> str:
    display = _dashboard_entry_display(row)
    label = str(display.get("entry_display_label") or "").strip()
    hint = str(display.get("entry_action_hint") or display.get("entry_display_reason") or "").strip()
    reason = str(display.get("entry_display_reason") or "").strip()
    if not label:
        label, grade, _title = _entry_rating_display_parts(row)
        hint = _entry_rating_chip_text(label, grade)
        reason = hint
    compact_label, compact_hint = _dashboard_compact_entry_text(display, row)
    tone = _buy_point_label_tone(compact_label or label)
    background, foreground, border = BADGE_STYLES.get(tone, BADGE_STYLES["gray"])
    title = "；".join(part for part in (label, hint, reason) if part)
    return (
        '<div class="decision-cell entry-rating-cell">'
        f'<span class="entry-rating-token" title="{escape(title)}" '
        f'style="background:{background};color:{foreground};border:1px solid {border};">'
        f"<strong>{escape(compact_label or '待确认')}</strong>"
        f"<em>{escape(compact_hint)}</em>"
        "</span></div>"
    )


def _entry_rating_chip_text(label: object, grade: object) -> str:
    label_text = str(label or "").strip()
    grade_text = str(grade or "").strip().upper()
    if grade_text and label_text:
        return f"{grade_text} · {label_text}"
    return label_text or grade_text or "待确认"


def _dashboard_entry_display(row: pd.Series | dict) -> dict:
    radar_label = str(_row_value(row, "entry_display_label") or "").strip()
    radar_reason = str(_row_value(row, "entry_display_reason") or "").strip()
    radar_hint = str(_row_value(row, "entry_action_hint") or "").strip()
    if radar_label or radar_reason or radar_hint:
        return {
            "entry_display_label": radar_label,
            "entry_display_reason": radar_reason,
            "entry_action_hint": radar_hint,
            "price_position": _row_value(row, "radar_price_position") or _row_value(row, "price_position") or _row_value(row, "zone_status"),
            "data_status": _row_value(row, "radar_data_status") or _row_value(row, "data_status") or _row_value(row, "dataStatus"),
            "entry_reference_low": _row_value(row, "entry_reference_low"),
            "entry_reference_high": _row_value(row, "entry_reference_high"),
            "next_action_price": _row_value(row, "next_action_price"),
            "chase_above_price": _row_value(row, "chase_above_price"),
            "current_vs_entry_pct": _row_value(row, "current_vs_entry_pct"),
            "missing_entry_fields": _row_value(row, "missing_entry_fields") or [],
        }
    return build_entry_display(
        data_status="MISSING_BUY_ZONE",
        price_position="ZONE_MISSING",
        missing_entry_fields=["Radar 纪律买区缺失"],
        decision=str(_row_value(row, "finalDecision") or _row_value(row, "decision") or ""),
        final_score=_number(_row_value(row, "finalScore") or _row_value(row, "totalScore")),
        valuation_score=_number(_row_value(row, "valuationScore")),
        risk_score=_number(_row_value(row, "riskScore")),
    )


def _dashboard_compact_entry_text(display: dict, row: pd.Series | dict) -> tuple[str, str]:
    missing_fields = _text_list(display.get("missing_entry_fields"))
    label = str(display.get("entry_display_label") or "").strip()
    hint = str(display.get("entry_action_hint") or "").strip()
    price_position = str(display.get("price_position") or _row_value(row, "radar_price_position") or _row_value(row, "price_position") or "").strip()
    if missing_fields or "暂无参考买区" in label or "缺" in label:
        return "数据不足", "补数据"
    if price_position == "IN_BUY_ZONE" or label.startswith("买区内"):
        return "买区内", "需复核" if "复核" in hint else "可复核"
    if price_position == "ABOVE_BUY_ZONE" or label.startswith("等待回落"):
        return "买区外", "等回落"
    if price_position == "IN_CHASE_ZONE" or "禁止追高" in label:
        return "追高区", "禁止新增"
    if price_position == "BELOW_BUY_ZONE" or label.startswith("低于买区"):
        return "低于买区", "不自动买入"
    if price_position == "ZONE_MISSING":
        return "无买区", "补数据"
    if label:
        return _short_entry_status(label), _short_entry_hint(hint, "看详情")
    return "", ""


def _short_entry_status(label: str) -> str:
    if "买区内" in label:
        return "买区内"
    if "等待回落" in label or "高于买区" in label:
        return "买区外"
    if "追高" in label:
        return "追高区"
    if "低于买区" in label:
        return "低于买区"
    if "数据" in label or "暂无" in label:
        return "数据不足"
    return _short_badge_text(label)


def _short_entry_hint(hint: str, fallback: str) -> str:
    text = str(hint or "").strip()
    if "禁止" in text:
        return "禁止新增"
    if "等待" in text or "回落" in text:
        return "等回落"
    if "低于买区" in text:
        return "不自动买入"
    if "补齐" in text or "数据" in text:
        return "补数据"
    if "需复核" in text or "复核" in text:
        return "需复核"
    if "交易计划" in text or "买区" in text:
        return "可复核"
    return fallback


def _text_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _price_position_from_dashboard_zone(value: object) -> str:
    zone = str(value or "").strip()
    if zone in {"tranche_buy", "heavy_buy"}:
        return "IN_BUY_ZONE"
    if zone == "below_heavy_buy":
        return "BELOW_BUY_ZONE"
    if zone == "no_chase":
        return "IN_CHASE_ZONE"
    if zone == "fair_observation":
        return "ABOVE_BUY_ZONE"
    return "ZONE_MISSING"


def _dashboard_missing_entry_fields(zone: object, price_position: str) -> list[str]:
    if price_position != "ZONE_MISSING":
        return []
    if zone is None:
        return ["无法生成纪律买区"]
    current_zone = str(_zone_value(zone, "currentZone") or "").strip()
    explain = _zone_value(zone, "explainability")
    missing_inputs: list[str] = []
    if isinstance(explain, dict):
        missing_inputs = [str(item).strip() for item in (explain.get("missingInputs") or []) if str(item).strip()]
    if current_zone == "unsupported_buy_zone_model":
        return ["暂无专属买区模型", "无法生成纪律买区"]
    if current_zone == "data_insufficient":
        if missing_inputs:
            return [f"缺关键买区输入：{', '.join(missing_inputs[:3])}", "无法生成纪律买区"]
        return ["缺估值指标", "无法生成纪律买区"]
    if current_zone == "low_confidence_zone":
        return ["数据置信度不足", "无法生成纪律买区"]
    if current_zone in {"invalid_zone", "invalid_manual_override"}:
        if missing_inputs:
            return [f"缺关键买区输入：{', '.join(missing_inputs[:3])}", "无法生成纪律买区"]
        return ["买区输入异常", "无法生成纪律买区"]
    return ["无法生成纪律买区"]


def _zone_value(zone: object, key: str) -> object:
    if isinstance(zone, dict):
        return zone.get(key)
    return getattr(zone, key, None)


def _number(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _buy_point_label_tone(label: object) -> str:
    text = str(label or "").strip()
    if "追高区" in text or "禁止追高" in text:
        return "red"
    if "无买区" in text or "暂无参考买区" in text or "数据" in text:
        return "gray"
    if "低于买区" in text:
        return "yellow"
    if "买区内" in text:
        return "green"
    if "买区外" in text or "等待回落" in text:
        return "blue"
    if "极贵" in text:
        return "deepred"
    if "偏贵" in text:
        return "orange"
    if "击球区" in text or "回撤买点" in text or "合理偏便宜" in text:
        return "green"
    if "等回踩" in text or "接近" in text:
        return "blue"
    if "未到估值买点" in text or "只观察" in text or "观察" in text or "待复核" in text or "需复核" in text:
        return "yellow"
    if "数据" in text:
        return "gray"
    return "gray"


def _entry_rating_display_parts(row: pd.Series) -> tuple[str, str, str]:
    raw = _display_table_text(_safe_table_value("entryRating", row.get("entryRating")), fallback="待确认")
    normalized = raw.replace("－", "-").replace("–", "-").replace("—", "-").strip()
    grade = ""
    remainder = normalized
    first_token = normalized.split(" ", 1)[0].strip()
    if _looks_like_rating_token(first_token):
        grade = first_token.upper()
        remainder = normalized[len(first_token) :].strip()
    elif "-" in normalized:
        prefix, suffix = normalized.split("-", 1)
        if _looks_like_rating_token(prefix.strip()):
            grade = prefix.strip().upper()
            remainder = suffix.strip()
    remainder = remainder.lstrip("-").strip()
    combined_label = _combined_entry_label(row)
    if combined_label:
        return combined_label, "", raw
    valuation_label = _entry_rating_text_label(row.get("valuationStatus"))
    if valuation_label == "极贵":
        return valuation_label, grade, raw
    label = _entry_rating_text_label(remainder)
    if not label:
        label = valuation_label
    fallback_label = _misleading_entry_fallback_label(row, label)
    if fallback_label:
        return fallback_label, "", raw
    if not label:
        label = _entry_label_from_grade(grade)
    return label or "待确认", grade, raw


def _combined_entry_label(row: pd.Series | dict) -> str:
    combined = _row_value(row, "combinedEntry")
    if isinstance(combined, dict):
        label = str(combined.get("entryLabel") or "").strip()
        if label:
            return label
    for key in ("activeZone", "systemZone"):
        zone = _row_value(row, key)
        combined = getattr(zone, "combinedEntry", None)
        if isinstance(combined, dict):
            label = str(combined.get("entryLabel") or "").strip()
            if label:
                return label
    return ""


def _row_value(row: pd.Series | dict, key: str) -> object:
    if isinstance(row, dict):
        return row.get(key)
    return row.get(key)


def _misleading_entry_fallback_label(row: pd.Series | dict, label: str) -> str:
    text = " ".join(
        str(value or "")
        for value in (
            label,
            _row_value(row, "entryRating"),
            _row_value(row, "valuationStatus"),
        )
    )
    if "击球区" not in text and "接近买点" not in text:
        return ""
    action_text = str(_row_value(row, "finalAction") or _row_value(row, "action") or "")
    lane = str(_row_value(row, "decisionLane") or "")
    if "禁止追高" in action_text or lane == "blocked":
        return "禁止追高，未到估值买点"
    if "复核" in action_text or str(_row_value(row, "dataConfidence") or "") == "low":
        return "需复核，未到估值买点"
    return "合理观察，未到估值买点"


def _looks_like_rating_token(value: object) -> bool:
    token = str(value or "").strip().upper()
    if not token:
        return False
    core = token.rstrip("+-")
    return core in {"A", "B", "C", "D"} and len(token) <= 3


def _entry_rating_text_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "数据" in text:
        return "数据不足"
    if "极贵" in text:
        return "极贵"
    if "击球区" in text:
        return "击球区附近"
    if "回撤" in text:
        return "回撤买点"
    if "合理偏便宜" in text:
        return "合理偏便宜"
    if "等回踩" in text or "可等回踩" in text:
        return "等回踩"
    if "待复核" in text or "复核" in text:
        return "待复核"
    if "只观察" in text or "观察" in text:
        return "只观察"
    if "偏贵" in text:
        return "偏贵"
    return ""


def _entry_label_from_grade(grade: str) -> str:
    normalized = str(grade or "").upper()
    if normalized.startswith("A"):
        return "回撤买点"
    if normalized == "B+":
        return "接近买点"
    if normalized.startswith("B"):
        return "等回踩"
    if normalized.startswith("C"):
        return "只观察"
    if normalized.startswith("D"):
        return "偏贵"
    return "待确认"


def _data_status_dot_html(value: object) -> str:
    label = _compact_data_status_label(value)
    tone = _data_status_tone(value)
    return f'<span class="dashboard-dot-status {escape(tone)}"><i></i>{escape(label)}</span>'


def _compact_data_status_label(value: object) -> str:
    text = str(value or "")
    if "完整" in text or text == "高":
        return "完整"
    if "中" in text:
        return "中"
    if "低" in text:
        return "低"
    if "缓存" in text:
        return "缓存"
    if "异常" in text:
        return "异常"
    if "不足" in text:
        return "不足"
    return "复核"


def _data_status_tone(value: object) -> str:
    text = str(value or "")
    if "完整" in text or text == "高":
        return "green"
    if "中" in text:
        return "blue"
    if "低" in text:
        return "orange"
    if "异常" in text or "不足" in text or "缓存" in text:
        return "yellow"
    return "gray"


def _badge_cell_html(value: object, color: str, title: object | None = None) -> str:
    background, foreground, border = BADGE_STYLES.get(color, BADGE_STYLES["gray"])
    title_attr = f' title="{escape(str(title))}"' if title else ""
    return (
        '<div class="decision-cell">'
        f'<span class="decision-badge"{title_attr} style="background:{background};color:{foreground};border:1px solid {border};">'
        f"{escape(str(value))}"
        "</span></div>"
    )


def _badge_span_html(value: object, color: str, extra_class: str = "") -> str:
    background, foreground, border = BADGE_STYLES.get(color, BADGE_STYLES["gray"])
    class_name = "decision-badge"
    if extra_class:
        class_name = f"{class_name} {escape(extra_class)}"
    return (
        f'<span class="{class_name}" style="background:{background};color:{foreground};border:1px solid {border};">'
        f"{escape(str(value))}"
        "</span>"
    )


def _badge_html(value: object, color: str, symbol: str | None = None) -> str:
    background, foreground, border = BADGE_STYLES.get(color, BADGE_STYLES["gray"])
    return _dashboard_cell_link(
        '<div class="decision-cell">'
        f'<span class="decision-badge" style="background:{background};color:{foreground};border:1px solid {border};">'
        f"{escape(str(value))}"
        "</span></div>",
        symbol,
    )


def _short_badge_text(value: object) -> str:
    text = str(value)
    replacements = {
        "可小仓观察，待关键数据复核后再加仓": "待复核",
        "可小仓分批": "可小仓",
        "可正常分批": "可分批",
        "回撤后有吸引力": "回撤买点",
        "数据不足，需复核": "数据不足",
    }
    return replacements.get(text, text)


def _dashboard_cell_link(inner_html: str, symbol: str | None) -> str:
    safe_symbol = escape(str(symbol or "").upper())
    if not safe_symbol:
        return inner_html
    return (
        f'<a class="decision-cell-link" href="#" data-dashboard-drawer-open="{safe_symbol}" '
        f'title="打开 {safe_symbol} 右侧详情面板">{inner_html}</a>'
    )


def _safe_table_value(key: str, value: object) -> object:
    if not _looks_like_technical_error(value):
        return value
    if key == "dataStatus":
        return "数据异常"
    if key == "action":
        return "数据不足，需复核"
    if key in {"valuationStatus", "qualityRating", "entryRating", "riskRating"}:
        return "数据不足"
    return "数据异常"


def _looks_like_technical_error(value: object) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(hint in text for hint in TECHNICAL_ERROR_HINTS)
