from __future__ import annotations

from collections.abc import Callable, Mapping
from html import escape

import pandas as pd

from data.dashboard_lanes import row_final_action, today_priority_rows
from ui.dashboard_tables import BADGE_STYLES, _badge_span_html, _entry_rating_display_parts, _short_badge_text


LANE_FILTER_LABELS = {
    "actionable": "可行动",
    "nearBuyZone": "接近买区",
    "waitOrReview": "待确认",
    "noChaseHighRisk": "风险隔离",
}

LaneFullReasonFn = Callable[[pd.Series], str]
LaneItemHtmlFn = Callable[[pd.Series], str]
PriorityItemHtmlFn = Callable[[str, pd.Series, str], str]
BadgeColorFn = Callable[[str, object, pd.Series | None], str]


def _dashboard_priority_strip_html(
    table: pd.DataFrame,
    priority_item_html: PriorityItemHtmlFn | None = None,
) -> str:
    item_html = priority_item_html or _dashboard_priority_item_html
    items = [
        item_html(lane_key, row, color)
        for lane_key, row, color in today_priority_rows(table)
    ]
    if items:
        body = "".join(items)
    else:
        body = '<div class="dashboard-priority-empty">暂无明确可执行机会，优先等待回踩或复核数据。</div>'
    return (
        '<section class="dashboard-priority-strip">'
        '<div class="dashboard-priority-head"><strong>今日优先</strong><span>最多 5 项</span></div>'
        f'<div class="dashboard-priority-list">{body}</div>'
        "</section>"
    )


def _dashboard_priority_item_html(
    lane_key: str,
    row: pd.Series,
    color: str,
    lane_full_reason: LaneFullReasonFn | None = None,
) -> str:
    label = _dashboard_priority_label(lane_key, row)
    symbol = str(row.get("symbol") or "").upper()
    safe_symbol = escape(symbol)
    action = _short_badge_text(row_final_action(row) or row.get("valuationStatus") or "只观察")
    full_reason = lane_full_reason(row) if lane_full_reason else _lane_full_reason(row)
    reason = _lane_short_reason(full_reason)
    return (
        f'<a class="dashboard-priority-row tone-{escape(color)}" href="?page=detail&symbol={safe_symbol}" target="_self" '
        f'aria-label="打开 {safe_symbol} 个股研究" '
        f'title="{escape(label)} · {safe_symbol} · {escape(str(action))} · {escape(reason)}">'
        f'<span class="dashboard-priority-status {escape(color)}" title="{escape(label)}"><i></i></span>'
        f'<strong>{safe_symbol}</strong>'
        f'<span>{escape(str(action))}</span>'
        f'<em>{escape(reason)}</em>'
        "</a>"
    )


def _dashboard_priority_label(lane_key: str, row: pd.Series) -> str:
    if lane_key == "actionable":
        return "可行动"
    if lane_key == "nearBuyZone":
        return "接近"
    if lane_key == "waitOrReview":
        action = row_final_action(row)
        if "复核" in action or row.get("dataConfidence") == "low":
            return "复核"
        return "等待"
    if lane_key == "noChaseHighRisk":
        action = row_final_action(row)
        if "数据" in action or row.get("dataConfidence") == "low":
            return "复核"
        return "风险"
    return "观察"


def _summary_panel_head_html(title: object, subtitle: object, count: int, color: str) -> str:
    background, foreground, border = BADGE_STYLES.get(color, BADGE_STYLES["gray"])
    return (
        f'<div class="summary-panel-head tone-{escape(color)}">'
        "<div>"
        f'<div class="summary-panel-title">{escape(str(title))}</div>'
        f'<div class="summary-panel-subtitle">{escape(str(subtitle))}</div>'
        "</div>"
        f'<span class="summary-count" style="background:{background};color:{foreground};border:1px solid {border};">{count}</span>'
        "</div>"
    )


def _lane_item_html(
    row: pd.Series,
    badge_color_for_cell: BadgeColorFn | None = None,
    lane_full_reason: LaneFullReasonFn | None = None,
) -> str:
    state = _lane_state_label(row)
    color_fn = badge_color_for_cell or _default_badge_color_for_cell
    state_color = color_fn("valuationStatus", state, row)
    symbol = str(row.get("symbol") or "")
    full_reason = lane_full_reason(row) if lane_full_reason else _lane_full_reason(row)
    short_reason = _lane_short_reason(full_reason)
    return (
        f'<a class="lane-item" href="#" data-dashboard-drawer-open="{escape(symbol)}" title="{escape(full_reason)}">'
        f'<span class="lane-symbol">{escape(symbol)}</span>'
        f'<span class="lane-reason">{escape(short_reason)}</span>'
        f'{_badge_span_html(_short_badge_text(state), state_color, "lane-state-badge")}'
        "</a>"
    )


def _lane_stack_html(rows: list[pd.Series], lane_item_html: LaneItemHtmlFn | None = None) -> str:
    if not rows:
        body = '<div class="summary-empty is-blank" aria-hidden="true"></div>'
    else:
        item_html = lane_item_html or _lane_item_html
        body = "".join(item_html(row) for row in rows[:4])
    return f'<div class="lane-row-stack">{body}</div>'


def _lane_reason(row: pd.Series, lane_full_reason: LaneFullReasonFn | None = None) -> str:
    full_reason = lane_full_reason(row) if lane_full_reason else _lane_full_reason(row)
    return _lane_short_reason(full_reason)


def _lane_full_reason(
    row: pd.Series,
    list_value: Callable[[object], list[str]] | None = None,
    numeric: Callable[[object], float] | None = None,
    translate_factor: Callable[[str], str] | None = None,
    translated_list: Callable[[list[str], int | None], list[str]] | None = None,
    quality_negative_items: Callable[[pd.Series], list[str]] | None = None,
) -> str:
    list_fn = list_value or _default_list_value
    numeric_fn = numeric or _default_numeric
    translate_fn = translate_factor or (lambda value: value)
    translated_fn = translated_list or _default_translated_list
    quality_negative_fn = quality_negative_items or (lambda _row: [])
    reasons = list_fn(row.get("overheatReasons"))
    if row.get("dataConfidence") == "low":
        return "关键数据待复核"
    if reasons and numeric_fn(row.get("overheatScore")) >= 40:
        return translate_fn(str(reasons[0])).rstrip("。")
    positives = translated_fn(list_fn(row.get("keyPositiveDrivers")), 1)
    if positives:
        return positives[0]
    risks = translated_fn(quality_negative_fn(row), 1)
    if risks:
        return risks[0]
    return _lane_state_label(row) or "等待确认"


def _lane_state_label(row: pd.Series) -> str:
    entry_label, _entry_grade, _entry_raw = _entry_rating_display_parts(row)
    valuation = str(row.get("valuationStatus") or "")
    raw_entry = str(row.get("entryRating") or "")
    if "击球区" in valuation or "击球区" in raw_entry or "接近买点" in raw_entry:
        return entry_label
    return valuation or entry_label or "待确认"


def _lane_short_reason(reason: object) -> str:
    text = str(reason or "").strip().rstrip("。")
    if not text:
        return "等待确认"
    if "今日下跌只是短期冷却" in text or "不等于进入击球区" in text:
        return "短期冷却，未到估值买点"
    if "关键数据待复核" in text or ("数据" in text and "复核" in text):
        return "数据待复核"
    if "RSI" in text and any(keyword in text for keyword in ["高于", "偏热", "过热", "极高"]):
        return "RSI仍偏热"
    if "回撤" in text and any(keyword in text for keyword in ["深", "较大", "超过", "距高点"]):
        return "回撤较深"
    if "收入增速" in text or "收入增长" in text:
        return "收入增速"
    if "自由现金流" in text or "FCF" in text:
        return "FCF支撑"
    if len(text) <= 18:
        return text
    return text[:17] + "…"


def _lane_more_html(
    lane_key: str,
    hidden_count: int,
    lane_filter_labels: Mapping[str, str] | None = None,
) -> str:
    labels = lane_filter_labels or LANE_FILTER_LABELS
    label = labels.get(str(lane_key), "该分组")
    legacy_label = f"还有 {int(hidden_count)} 只 · 查看全部"
    return (
        f'<span class="lane-more" title="原地聚焦主表：{escape(label)}" aria-label="{escape(legacy_label)}">'
        f"<span>+{int(hidden_count)} 未显示</span><b>查看全部</b>"
        "</span>"
    )


def _default_badge_color_for_cell(_key: str, _value: object, _row: pd.Series | None = None) -> str:
    return "gray"


def _default_list_value(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item]
    if value is None or value == "":
        return []
    return [str(value)]


def _default_numeric(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _default_translated_list(items: list[str], limit: int | None = None) -> list[str]:
    translated = [str(item) for item in items if item and str(item) != "drawdown > 40%"]
    return translated[:limit] if limit else translated
