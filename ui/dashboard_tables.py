from __future__ import annotations

from collections.abc import Callable, Sequence
from html import escape

import pandas as pd

from data.dashboard_lanes import row_current_add_text, row_final_action


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
    label, grade, title = _entry_rating_display_parts(row)
    tone = _buy_point_label_tone(label)
    background, foreground, border = BADGE_STYLES.get(tone, BADGE_STYLES["gray"])
    display_text = _entry_rating_chip_text(label, grade)
    return (
        '<div class="decision-cell entry-rating-cell">'
        f'<span class="entry-rating-token" title="{escape(title)}" '
        f'style="background:{background};color:{foreground};border:1px solid {border};">'
        f"<strong>{escape(display_text)}</strong>"
        "</span></div>"
    )


def _entry_rating_chip_text(label: object, grade: object) -> str:
    label_text = str(label or "").strip()
    grade_text = str(grade or "").strip().upper()
    if grade_text and label_text:
        return f"{grade_text} · {label_text}"
    return label_text or grade_text or "待确认"


def _buy_point_label_tone(label: object) -> str:
    text = str(label or "").strip()
    if "极贵" in text:
        return "deepred"
    if "偏贵" in text:
        return "orange"
    if "击球区" in text or "回撤买点" in text or "合理偏便宜" in text:
        return "green"
    if "等回踩" in text or "接近" in text:
        return "blue"
    if "只观察" in text or "观察" in text or "待复核" in text:
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
    valuation_label = _entry_rating_text_label(row.get("valuationStatus"))
    if valuation_label == "极贵":
        return valuation_label, grade, raw
    label = _entry_rating_text_label(remainder)
    if not label:
        label = valuation_label
    if not label:
        label = _entry_label_from_grade(grade)
    return label or "待确认", grade, raw


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
