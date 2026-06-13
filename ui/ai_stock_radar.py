from __future__ import annotations

from html import escape
from typing import Any

import pandas as pd
import streamlit as st

from data.ai_stock_radar import RADAR_REPORT_VERSION, RadarScores, build_ai_stock_radar_list_row, build_ai_stock_radar_report
from data.entry_display import format_buy_zone, format_zone_status
from data.market_context import build_market_context, build_market_history
from settings import load_watchlist
from ui.theme import render_page_header


def render() -> None:
    _render_styles()
    render_page_header("AI Stock Radar", "只读本地缓存，生成单票纪律雷达；价格到达和评分通过都不是自动交易信号。")
    tickers, source = select_radar_symbols(load_watchlist())
    if not tickers:
        st.info("观察池为空。")
        return

    selected = _selected_symbol(tickers)
    _render_list(tickers, selected, source)
    if selected:
        _render_report(selected)


def _render_list(tickers: list[str], selected: str, source: str) -> None:
    rows = _sort_rows([_list_row(ticker) for ticker in tickers])
    filter_key = _selected_radar_filter_key()
    filter_counts = _filter_counts(rows)
    st.markdown(_filter_chips_html(filter_key, filter_counts), unsafe_allow_html=True)
    rows = _filter_rows(rows, filter_key)
    body = "".join(_list_row_html(row, selected) for row in rows)
    st.markdown(
        (
            '<section class="ai-radar-list-card">'
            f'<div class="ai-radar-section-head"><strong>Radar 研究入口</strong><span>{len(rows)}/{len(tickers)} 只｜来源：{escape(source)}｜只读缓存，不自动刷新</span></div>'
            '<p class="ai-radar-list-note">列表只做研究入口；完整评分、区间判断和风险依据请进入单股研报页。</p>'
            '<div class="ai-radar-table-wrap">'
            '<table class="ai-radar-table">'
            "<thead><tr>"
            "<th>股票</th><th>公司 / 赛道</th><th>当前价</th><th>核心状态</th><th>研报状态</th><th>数据完整度</th><th>更新时间</th><th>操作</th>"
            "</tr></thead>"
            f"<tbody>{body}</tbody>"
            "</table>"
            "</div>"
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _render_report(symbol: str) -> None:
    row = _single_report_row(symbol)
    snapshot = _dict_value(row, "rawSnapshot")
    technicals = _dict_value(row, "rawTechnicals")
    report = build_ai_stock_radar_report(
        symbol,
        company_name=str(_row_value(row, "companyName") or symbol),
        scores=None if snapshot and technicals else _scores_from_row(row),
        snapshot=snapshot,
        technicals=technicals,
        bull_points=_list_value(row, "keyPositiveDrivers"),
        risk_points=_list_value(row, "keyNegativeDrivers"),
        watch_points=_watch_points(row),
    )
    market = build_market_context(symbol)
    history = build_market_history(symbol)
    st.markdown('<div id="radar-report"></div>', unsafe_allow_html=True)
    report_dict = report.to_dict()
    st.markdown(_report_html(report_dict, market, snapshot or {}, technicals or {}, row or {}, history), unsafe_allow_html=True)
    with st.expander("评分依据 / 数据诊断", expanded=False):
        st.markdown(_debug_html(report_dict.get("debug") or {}), unsafe_allow_html=True)


def _list_row(ticker: str) -> dict[str, Any]:
    row = _dashboard_row(ticker) or _single_report_row(ticker)
    snapshot = _dict_value(row, "rawSnapshot")
    technicals = _dict_value(row, "rawTechnicals")
    company_name = _company_name_from_sources(ticker, row, snapshot or {})
    list_row = build_ai_stock_radar_list_row(
        ticker,
        company_name=company_name,
        scores=None if snapshot and technicals else _scores_from_row(row),
        snapshot=snapshot,
        technicals=technicals,
    )
    list_row["sector"] = _sector_track_from_sources(row, snapshot or {})
    return list_row


def _company_name_from_sources(ticker: str, row: dict[str, Any] | None, snapshot: dict[str, Any]) -> str:
    value = (
        _first_present(snapshot, "companyName", "company_name", "name", "company")
        or _row_value(row, "companyName", "company_name", "name", "company")
        or ticker
    )
    return str(value or ticker).strip() or ticker


def _sector_track_from_sources(row: dict[str, Any] | None, snapshot: dict[str, Any]) -> str:
    sector = _clean_text(
        _first_present(snapshot, "sector", "sectorName")
        or _row_value(row, "sector", "sectorName")
    )
    industry = _clean_text(
        _first_present(snapshot, "industry", "industry_group", "industryGroup", "business_model", "businessModel", "model")
        or _row_value(row, "industry", "industry_group", "industryGroup", "business_model", "businessModel", "model")
    )
    if sector and industry and industry.lower() != sector.lower():
        return f"{sector} / {industry}"
    return sector or industry


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    status_rank = {
        "价值复核": 0,
        "近端复核": 0,
        "买区内": 1,
        "技术待确认": 2,
        "观察": 3,
        "破位复核": 4,
        "追高风险": 5,
        "风险区": 5,
    }
    confidence_rank = {"高": 0, "中": 1, "低": 2, "不足": 3}
    return sorted(
        rows,
        key=lambda row: (
            status_rank.get(_core_status(row), 9),
            confidence_rank.get(_data_confidence(row), 9),
            _updated_sort_key(row),
            str(row.get("ticker") or ""),
        ),
    )


def _updated_sort_key(row: dict[str, Any]) -> float:
    updated = pd.to_datetime(row.get("data_updated_at"), errors="coerce")
    if pd.isna(updated):
        return float("inf")
    try:
        return -float(updated.timestamp())
    except (AttributeError, OSError, OverflowError, ValueError):
        return float("inf")


def _selected_radar_filter_key() -> str:
    key = str(st.query_params.get("radarFilter", "all") or "all").strip()
    valid = {"all", "value", "pullback", "watch", "chase", "data"}
    return key if key in valid else "all"


def _filter_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        key: sum(1 for row in rows if _row_matches_filter(row, key))
        for key in ("all", "value", "pullback", "watch", "chase", "data")
    }


def _filter_rows(rows: list[dict[str, Any]], filter_key: str) -> list[dict[str, Any]]:
    if filter_key == "all":
        return rows
    return [row for row in rows if _row_matches_filter(row, filter_key)]


def _row_matches_filter(row: dict[str, Any], filter_key: str) -> bool:
    status = _core_status(row)
    if filter_key == "all":
        return True
    if filter_key == "value":
        return status in {"价值复核", "近端复核"}
    if filter_key == "pullback":
        return status == "买区内"
    if filter_key == "watch":
        return status in {"观察", "技术待确认"}
    if filter_key == "chase":
        return status in {"风险区", "追高风险", "破位复核"}
    if filter_key == "data":
        return _data_confidence(row) != "高"
    return False


def _filter_chips_html(active_key: str, counts: dict[str, int]) -> str:
    labels = [
        ("all", "全部"),
        ("value", "价值复核"),
        ("pullback", "回踩区"),
        ("watch", "观察"),
        ("chase", "追高区"),
        ("data", "数据缺口"),
    ]
    chips = "".join(
        '<a class="ai-radar-filter-chip {active}" href="?page=ai-radar&amp;radarFilter={key}">'
        "<span>{label}</span><b>{count}</b></a>".format(
            active="active" if key == active_key else "",
            key=escape(key, quote=True),
            label=escape(label),
            count=escape(str(counts.get(key, 0))),
        )
        for key, label in labels
    )
    return f'<nav class="ai-radar-filter-chips">{chips}</nav>'


def _list_row_html(row: dict[str, Any], selected: str) -> str:
    ticker = str(row.get("ticker") or "")
    decision = str(row.get("decision") or "")
    active = " active" if ticker == selected else ""
    return (
        f'<tr class="{escape(_decision_tone(decision))}{active}">'
        f'<td><a class="ai-radar-ticker" href="?page=ai-radar&symbol={escape(ticker, quote=True)}#radar-report" target="_self">{escape(ticker)}</a></td>'
        f"<td>{_company_track_html(row)}</td>"
        f'<td>{escape(_money(row.get("current_price")))}</td>'
        f'<td><span class="ai-radar-status-pill">{escape(_core_status(row))}</span></td>'
        f'<td><span class="ai-radar-report-status">{escape(_report_status_text(row))}</span></td>'
        f"<td>{_data_confidence_html(row)}</td>"
        f'<td>{escape(_short_time(row.get("data_updated_at")))}</td>'
        f'<td><a class="ai-radar-report-link" href="?page=ai-radar&symbol={escape(ticker, quote=True)}#radar-report" target="_self">查看</a></td>'
        "</tr>"
    )


def _report_html(
    report: dict[str, Any],
    market: dict[str, Any],
    snapshot: dict[str, Any],
    technicals: dict[str, Any],
    row: dict[str, Any],
    history: pd.DataFrame,
) -> str:
    decision = str(report.get("decision") or "")
    core_status = _core_status(report)
    confidence = _data_confidence(report)
    summary_lines = _research_summary_lines(report, snapshot, market)
    return (
        f'<article class="ai-radar-research-report {_decision_tone(decision)}">'
        f"{_research_header_html(report, market, snapshot, technicals, core_status)}"
        '<section class="ai-radar-research-section ai-radar-summary-section">'
        '<div class="ai-radar-section-title"><span>核心摘要</span><b>结论先行</b></div>'
        f"{_summary_lines_html(summary_lines)}"
        "</section>"
        '<section class="ai-radar-research-grid first-screen">'
        f"{_range_chart_html(report)}"
        f"{_score_card_html(report)}"
        "</section>"
        '<section class="ai-radar-opinion-grid">'
        f'{_text_card_html("看多逻辑", report.get("bull_points") or [])}'
        f'{_text_card_html("风险提示", report.get("risk_points") or [])}'
        f'{_text_card_html("关键监控点", _research_watch_points(report, row))}'
        "</section>"
        '<section class="ai-radar-research-grid">'
        f'{_metric_table_card_html("关键指标（今日）", _key_metric_rows(report, market, snapshot, technicals))}'
        f'{_metric_table_card_html("核心财务摘要", _financial_metric_rows(snapshot))}'
        "</section>"
        '<section class="ai-radar-research-grid">'
        f'{_metric_table_card_html("市场表现", _performance_rows(history))}'
        f'{_text_card_html("近期新闻 / 催化", _catalyst_items(row, snapshot, report))}'
        "</section>"
        f"{_data_completeness_html(report, confidence)}"
        '<footer class="ai-radar-report-foot">'
        f'<span>更新时间：{escape(str(market.get("fetchedAt") or report.get("data_updated_at") or "N/A"))}</span>'
        f'<span>数据完整度：{escape(confidence)}</span>'
        f'<span>报告版本：{escape(RADAR_REPORT_VERSION)}</span>'
        "</footer>"
        "</article>"
    )


def _debug_html(debug: dict[str, Any]) -> str:
    if not debug:
        return '<section class="ai-radar-debug">暂无评分诊断。</section>'
    score_inputs = _dict_value(debug, "score_inputs") or {}
    score_labels = [
        ("quality_score", "质量"),
        ("growth_score", "成长"),
        ("valuation_score", "估值"),
        ("technical_score", "技术"),
        ("risk_score", "风险"),
        ("final_score", "总分"),
    ]
    rows = []
    for key, label in score_labels:
        item = _dict_value(score_inputs, key) or {}
        rows.append(
            "<tr>"
            f"<td>{escape(label)}</td>"
            f"<td>{escape(_inline_list(item.get('used_fields')))}</td>"
            f"<td>{escape(_inline_list(item.get('missing_fields')))}</td>"
            f"<td>{escape(_inline_list(item.get('positive_fields')))}</td>"
            f"<td>{escape(_inline_list(item.get('negative_fields')))}</td>"
            "</tr>"
        )
    zones = _dict_value(debug, "price_zones") or {}
    zone_sources = _dict_value(zones, "zone_sources") or {}
    below_reason = str(debug.get("below_buy_zone_reason") or "").strip()
    below_note = f'<div class="ai-radar-debug-note">{escape(below_reason)}</div>' if below_reason else ""
    return (
        '<section class="ai-radar-debug">'
        '<div class="ai-radar-debug-summary">'
        f'<div><span>数据状态</span><strong>{escape(str(debug.get("data_status") or "N/A"))}</strong></div>'
        f'<div><span>Zone status</span><strong>{escape(_price_position_label(debug.get("price_position")))}</strong></div>'
        f'<div><span>距买区</span><strong>{escape(_signed_pct(debug.get("distance_to_buy_zone_pct")))}</strong></div>'
        f'<div><span>缺失字段</span><strong>{escape(_inline_list(debug.get("data_missing_fields")))}</strong></div>'
        f'<div><span>区间来源</span><strong>{escape(str(zones.get("source") or "missing"))}</strong></div>'
        f'<div><span>字段别名风险</span><strong>{escape(_inline_list(debug.get("field_alias_notes")))}</strong></div>'
        '</div>'
        '<div class="ai-radar-debug-summary compact">'
        f'<div><span>击球区</span><strong>{escape(str(zone_sources.get("buy_zone") or "missing"))}</strong></div>'
        f'<div><span>观察区</span><strong>{escape(str(zone_sources.get("watch_zone") or "missing"))}</strong></div>'
        f'<div><span>追高区</span><strong>{escape(str(zone_sources.get("chase_zone") or "missing"))}</strong></div>'
        f'<div><span>阻止原因</span><strong>{escape(_inline_list(debug.get("block_reasons")))}</strong></div>'
        '</div>'
        f'{below_note}'
        '<table class="ai-radar-debug-table">'
        '<thead><tr><th>评分</th><th>使用字段</th><th>缺失字段</th><th>加分字段</th><th>扣分字段</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
        '</section>'
    )


def _research_header_html(
    report: dict[str, Any],
    market: dict[str, Any],
    snapshot: dict[str, Any],
    technicals: dict[str, Any],
    core_status: str,
) -> str:
    ticker = str(report.get("ticker") or "")
    company = str(report.get("company_name") or ticker)
    meta = "｜".join(
        item
        for item in (
            _clean_text(_first_present(snapshot, "business_model", "businessModel", "model", "industry")),
            _clean_text(_first_present(snapshot, "sector", "industry_group", "industryGroup")),
            _clean_text(_first_present(snapshot, "country", "exchange")),
        )
        if item
    ) or "本地缓存研究视图"
    stats = [
        ("最新价", _money(report.get("current_price"))),
        ("52周区间", _range_text(_first_number(snapshot, technicals, "fifty_two_week_low", "yearLow"), _first_number(snapshot, technicals, "fifty_two_week_high", "yearHigh"))),
        ("市值", _compact_money(_first_number(snapshot, "market_cap", "marketCap"))),
        ("成交量", _compact_number(_first_number(snapshot, technicals, "volume"))),
        ("更新时间", _short_time(report.get("data_updated_at") or market.get("fetchedAt"))),
        ("当前区间", core_status),
    ]
    stat_html = "".join(
        f"<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>" for label, value in stats
    )
    return (
        '<header class="ai-radar-research-header">'
        '<div class="ai-radar-title-block">'
        f"<span>AI Stock Radar Research</span>"
        f"<h1>{escape(ticker)}</h1>"
        f"<p>{escape(company)}</p>"
        f"<em>{escape(meta)}</em>"
        "</div>"
        f'<div class="ai-radar-zone-badge">{escape(core_status)}</div>'
        f'<div class="ai-radar-header-stats">{stat_html}</div>'
        "</header>"
    )


def _summary_lines_html(lines: list[str]) -> str:
    return '<div class="ai-radar-summary-lines">' + "".join(f"<p>{escape(line)}</p>" for line in lines[:5]) + "</div>"


def _research_summary_lines(report: dict[str, Any], snapshot: dict[str, Any], market: dict[str, Any]) -> list[str]:
    ticker = str(report.get("ticker") or "该股票")
    company = str(report.get("company_name") or ticker)
    status = _core_status(report)
    score = _number_text(report.get("final_score"))
    data_confidence = _data_confidence(report)
    summary = str(report.get("summary") or "").strip()
    lines = [
        summary if summary else f"{company} 当前处于“{status}”语境，Radar 总分 {score}，适合先按研究清单复核而不是看状态码交易。",
        f"价格位置：{_entry_sentence(report)}",
        f"核心判断：{_decision_to_sentence(report)}",
        f"下一步重点：{_next_step_sentence(report)}",
        f"数据完整度：{data_confidence}；缺失项放在报告末尾，不让内部缺数据状态主导结论。",
    ]
    return [line for line in lines if line]


def _range_chart_html(report: dict[str, Any]) -> str:
    ranges = _range_chart_items(report)
    current = _number(report.get("current_price"))
    values = [value for item in ranges for value in item["range"] if value is not None]
    if current is not None:
        values.append(current)
    if not values:
        return _empty_card_html("目标价区间与估值/技术区间图", "缺少价格和区间数据，暂时无法绘制。")
    low = min(values)
    high = max(values)
    padding = max((high - low) * 0.08, 1.0)
    low -= padding
    high += padding
    marker_left = _range_position(current, low, high) if current is not None else None
    marker = (
        f'<i class="ai-radar-current-marker" style="left:{marker_left:.2f}%"><b>现价 {_money(current)}</b></i>'
        if marker_left is not None
        else ""
    )
    rows = []
    for item in ranges:
        item_low, item_high = item["range"]
        if item_low is None and item_high is None:
            continue
        start = _range_position(item_low if item_low is not None else low, low, high)
        end = _range_position(item_high if item_high is not None else high, low, high)
        width = max(end - start, 1.2)
        rows.append(
            '<div class="ai-radar-range-row">'
            f'<span>{escape(item["label"])}</span>'
            f'<div class="ai-radar-range-track"><i class="{escape(item["tone"])}" style="left:{start:.2f}%;width:{width:.2f}%"></i>{marker}</div>'
            f'<b>{escape(_range_text(item_low, item_high))}</b>'
            "</div>"
        )
    return (
        '<section class="ai-radar-card ai-radar-range-card">'
        '<div class="ai-radar-section-title"><span>目标价区间与估值/技术区间图</span><b>当前价相对区间</b></div>'
        f'<div class="ai-radar-range-axis"><span>{escape(_money(low))}</span><span>{escape(_money(high))}</span></div>'
        f'{"".join(rows)}'
        "</section>"
    )


def _range_chart_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"label": "技术回踩区", "range": (_first_number(report, "effective_technical_entry_zone_low", "technical_pullback_zone_low", "technical_entry_zone_low"), _first_number(report, "effective_technical_entry_zone_high", "technical_pullback_zone_high", "technical_entry_zone_high")), "tone": "blue"},
        {"label": "近端修复观察区", "range": (_first_number(report, "near_term_repair_zone_low", "technical_repair_zone_low"), _first_number(report, "near_term_repair_zone_high", "technical_repair_zone_high")), "tone": "slate"},
        {"label": "趋势确认区", "range": (_first_number(report, "trend_reclaim_zone_low"), _first_number(report, "trend_reclaim_zone_high", "confirmation_price")), "tone": "green"},
        {"label": "估值参考区", "range": (_first_number(report, "valuation_reference_zone_low"), _first_number(report, "valuation_reference_zone_high")), "tone": "amber"},
        {"label": "深度支撑区", "range": (_first_number(report, "deep_support_zone_low", "invalidation_price"), _first_number(report, "deep_support_zone_high", "support_watch_zone_high")), "tone": "orange"},
        {"label": "追高禁区", "range": (_first_number(report, "chase_above_price"), None), "tone": "red"},
    ]


def _range_position(value: float | None, low: float, high: float) -> float:
    if value is None or high <= low:
        return 0.0
    return max(0.0, min(100.0, (value - low) / (high - low) * 100))


def _score_card_html(report: dict[str, Any]) -> str:
    fundamental = _average_score(report.get("quality_score"), report.get("growth_score"), report.get("valuation_score"), report.get("risk_score"))
    items = [
        ("总分", report.get("final_score")),
        ("基本面", fundamental),
        ("技术", report.get("technical_score")),
        ("质量", report.get("quality_score")),
        ("估值", report.get("valuation_score")),
        ("风险", report.get("risk_score")),
    ]
    body = "".join(f"<div><span>{escape(label)}</span><strong>{escape(_number_text(value))}</strong></div>" for label, value in items)
    return f'<section class="ai-radar-card score"><div class="ai-radar-section-title"><span>评分卡</span><b>综合 / 基本面 / 技术</b></div><div class="ai-radar-score-grid">{body}</div></section>'


def _zones_card_html(report: dict[str, Any]) -> str:
    items = [
        ("击球区", report.get("buy_zone")),
        ("观察区", report.get("watch_zone")),
        ("追高区", report.get("chase_zone")),
    ]
    body = "".join(f"<div><span>{escape(label)}</span><strong>{escape(_zone_text(zone))}</strong></div>" for label, zone in items)
    return f'<article class="ai-radar-card zones"><h3>击球区 / 观察区 / 追高区</h3>{body}</article>'


def _metric_table_card_html(title: str, rows: list[tuple[str, str]]) -> str:
    body = "".join(f"<tr><td>{escape(label)}</td><td>{escape(value)}</td></tr>" for label, value in rows)
    return (
        '<section class="ai-radar-card ai-radar-metric-card">'
        f'<div class="ai-radar-section-title"><span>{escape(title)}</span><b>只读缓存</b></div>'
        f'<table class="ai-radar-metric-table"><tbody>{body}</tbody></table>'
        "</section>"
    )


def _key_metric_rows(report: dict[str, Any], market: dict[str, Any], snapshot: dict[str, Any], technicals: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        ("最新价", _money(report.get("current_price"))),
        ("日内涨跌幅", _signed_pct(_first_number(snapshot, technicals, market, "change_pct", "changePercent", "day_change_pct"))),
        ("成交量", _compact_number(_first_number(snapshot, technicals, "volume"))),
        ("52周高低", _range_text(_first_number(snapshot, technicals, "fifty_two_week_low", "yearLow"), _first_number(snapshot, technicals, "fifty_two_week_high", "yearHigh"))),
        ("PE / Forward PE", f"{_multiple(_first_number(snapshot, 'pe', 'trailing_pe', 'price_to_earnings'))} / {_multiple(_first_number(snapshot, 'forward_pe', 'forwardPE'))}"),
        ("EV/Sales", _multiple(_first_number(snapshot, "enterprise_to_revenue", "enterpriseToRevenue", "ev_to_sales"))),
        ("FCF yield", _ratio_pct(_first_number(snapshot, "free_cash_flow_yield", "fcf_yield"))),
        ("毛利率", _ratio_pct(_first_number(snapshot, "gross_margin", "grossMargin"))),
        ("净利率", _ratio_pct(_first_number(snapshot, "net_margin", "profit_margin", "netMargin"))),
        ("ROE", _ratio_pct(_first_number(snapshot, "roe", "returnOnEquity"))),
    ]


def _financial_metric_rows(snapshot: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        ("营收", _compact_money(_first_number(snapshot, "total_revenue", "revenue", "revenue_ttm"))),
        ("同比增速", _ratio_pct(_first_number(snapshot, "revenue_growth", "revenueGrowth"))),
        ("毛利率", _ratio_pct(_first_number(snapshot, "gross_margin", "grossMargin"))),
        ("净利率", _ratio_pct(_first_number(snapshot, "net_margin", "profit_margin", "netMargin"))),
        ("经营现金流", _compact_money(_first_number(snapshot, "operating_cash_flow", "operatingCashFlow"))),
        ("自由现金流", _compact_money(_first_number(snapshot, "free_cash_flow", "freeCashFlow"))),
        ("现金及短投", _compact_money(_first_number(snapshot, "total_cash", "cash", "cashAndShortTermInvestments", "cashAndEquivalents"))),
        ("总债务", _compact_money(_first_number(snapshot, "total_debt", "debt", "totalDebt"))),
    ]


def _performance_rows(history: pd.DataFrame) -> list[tuple[str, str]]:
    return [
        ("1日", _signed_pct(_history_return(history, 1))),
        ("5日", _signed_pct(_history_return(history, 5))),
        ("1月", _signed_pct(_history_return(history, 21))),
        ("3月", _signed_pct(_history_return(history, 63))),
        ("年初至今", _signed_pct(_history_ytd_return(history))),
        ("1年", _signed_pct(_history_return(history, 252))),
    ]


def _catalyst_items(row: dict[str, Any], snapshot: dict[str, Any], report: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for source in (row, snapshot, report):
        for key in ("recent_news", "recentNews", "news", "catalysts", "keyCatalysts", "events", "watch_points"):
            candidates.extend(_list_value(source, key))
    cleaned = [item for item in _dedupe_text(candidates) if item and item.lower() not in {"n/a", "none"}]
    return cleaned[:5] or ["暂无高相关事件缓存；先关注财报、指引、产品/监管事件和价格确认线。"]


def _research_watch_points(report: dict[str, Any], row: dict[str, Any]) -> list[str]:
    points = list(report.get("watch_points") or [])
    for value in (
        report.get("entry_action_hint"),
        report.get("technical_structure_reason"),
        report.get("technical_entry_missing_reason"),
    ):
        if value:
            points.append(str(value))
    points.extend(_watch_points(row))
    return _dedupe_text(points)[:6]


def _data_completeness_html(report: dict[str, Any], confidence: str) -> str:
    missing = _missing_group_text(report) or "暂无关键缺口"
    impact = _data_impact_sentence(report, confidence)
    detail = _inline_list((report.get("debug") or {}).get("data_missing_fields"))
    return (
        '<section class="ai-radar-card ai-radar-data-quality">'
        '<div class="ai-radar-section-title"><span>数据完整度</span><b>缺数据不压倒主结论</b></div>'
        '<div class="ai-radar-data-quality-grid">'
        f"<div><span>完整度</span><strong>{escape(confidence)}</strong></div>"
        f"<div><span>缺失项</span><strong>{escape(missing)}</strong></div>"
        f"<div><span>对结论影响</span><strong>{escape(impact)}</strong></div>"
        f"<div><span>字段明细</span><strong>{escape(detail)}</strong></div>"
        "</div>"
        "</section>"
    )


def _text_card_html(title: str, items: list[Any]) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        cleaned = ["暂无明确内容，先保持复查。"]
    body = "".join(f"<li>{escape(item)}</li>" for item in cleaned[:6])
    return f'<section class="ai-radar-card"><div class="ai-radar-section-title"><span>{escape(title)}</span><b>Research notes</b></div><ul>{body}</ul></section>'


def _inline_list(value: Any) -> str:
    if not value:
        return "无"
    if isinstance(value, (list, tuple, set)):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return "、".join(cleaned[:8]) if cleaned else "无"
    return str(value)


def _core_status(row: dict[str, Any]) -> str:
    entry_label = str(row.get("entry_display_label") or "").strip()
    interpretation = str(row.get("primary_entry_interpretation") or row.get("zone_semantic_label") or "").strip()
    price_position = str(row.get("price_position") or "")
    decision = str(row.get("decision") or "")
    structure = str(row.get("technical_structure_status") or "")
    combined = f"{entry_label} {interpretation}"
    if price_position == "IN_CHASE_ZONE" or decision == "BLOCK_CHASE":
        return "追高风险"
    if "价值复核" in combined or "估值吸引" in combined:
        return "价值复核"
    if "近端复核" in combined or "近端修复" in combined:
        return "近端复核"
    if price_position == "IN_BUY_ZONE":
        return "买区内"
    if structure == "BREAKDOWN_REVIEW":
        return "破位复核"
    if structure == "WEAK_TREND_REPAIR":
        return "技术待确认"
    if price_position in {"ABOVE_BUY_ZONE", "WAIT"}:
        return "观察"
    if decision == "AVOID":
        return "风险区"
    return "观察"


def _company_track_html(row: dict[str, Any]) -> str:
    ticker = str(row.get("ticker") or "").strip().upper()
    company = str(row.get("company_name") or "").strip()
    if company.upper() == ticker:
        company = ""
    track = _clean_text(row.get("sector"))
    if company and track:
        return f'<div class="ai-radar-company-cell"><strong>{escape(company)}</strong><span>{escape(track)}</span></div>'
    if company:
        return f'<div class="ai-radar-company-cell"><strong>{escape(company)}</strong></div>'
    if track:
        return f'<div class="ai-radar-company-cell"><span>{escape(track)}</span></div>'
    return '<span class="ai-radar-muted">—</span>'


def _report_status_text(row: dict[str, Any]) -> str:
    price_state = _price_data_state(row)
    if price_state == "missing":
        return "需补数据"
    if price_state == "stale" or bool(row.get("is_stale")):
        return "研报过期"
    if _data_confidence(row) in {"高", "中"}:
        return "已生成"
    return "需补数据"


def _data_confidence_html(row: dict[str, Any]) -> str:
    confidence = _data_confidence(row)
    groups = _missing_groups(row)
    optional_groups = _optional_missing_groups(row)
    summary = _missing_group_summary(groups) or _optional_group_summary(optional_groups)
    detail_items = [*groups, *optional_groups]
    detail = "、".join(detail_items) if detail_items else "关键数据完整"
    text = confidence if not summary else f"{confidence}｜{summary}"
    return (
        f'<span class="ai-radar-data-confidence {escape(confidence)}" title="{escape(detail, quote=True)}">'
        f"{escape(text)}</span>"
    )


def _missing_group_summary(groups: list[str]) -> str:
    if not groups:
        return ""
    if len(groups) == 1:
        return groups[0]
    return f"{len(groups)}项缺口"


def _data_confidence(row: dict[str, Any]) -> str:
    missing = _missing_groups(row)
    data_status = str(row.get("data_status") or "")
    price_state = _price_data_state(row)
    if price_state == "missing":
        return "不足"
    if data_status == "OK" and not bool(row.get("is_stale")) and not missing:
        return "高"
    if price_state == "stale" or data_status == "STALE":
        return "低"
    if data_status == "MISSING_SCORE" or len(missing) >= 3:
        return "低"
    return "中"


def _missing_groups(row: dict[str, Any]) -> list[str]:
    fields = _actionable_missing_fields(row)
    status = str(row.get("data_status") or "")
    if status and status != "OK":
        fields.append(status)
    text = " ".join(str(item).lower() for item in fields)
    price_state = _price_data_state(row, text)
    groups: list[str] = []
    if any(token in text for token in ("valuation", "forward_pe", "enterprise_to_revenue", "free_cash_flow_yield", "fcf")):
        groups.append("估值缺口")
    if any(token in text for token in ("technical", "ema", "atr", "swing", "history", "price_history", "buy_zone")):
        groups.append("技术缺口")
    if any(token in text for token in ("disclosure", "filing", "kpi", "sec")):
        groups.append("披露缺口")
    if _profile_missing(row):
        groups.append("资料缺口")
    if price_state == "stale":
        groups.append("价格过期")
    elif price_state == "missing":
        groups.append("价格缺失")
    if any(token in text for token in ("score", "quality", "growth", "risk")):
        groups.append("评分缺口")
    return _dedupe_text(groups)


def _actionable_missing_fields(row: dict[str, Any]) -> list[str]:
    return [
        str(field)
        for field in _all_missing_fields(row)
        if not _is_optional_gap_field(str(field))
    ]


def _all_missing_fields(row: dict[str, Any]) -> list[str]:
    debug = row.get("debug") if isinstance(row.get("debug"), dict) else {}
    fields = []
    fields.extend(debug.get("data_missing_fields") or [])
    fields.extend(row.get("data_missing_fields") or [])
    fields.extend(row.get("missing_entry_fields") or [])
    fields.extend(row.get("technical_entry_missing_fields") or [])
    fields.extend(row.get("technical_missing_fields") or [])
    return [str(field) for field in fields if str(field).strip()]


def _optional_missing_groups(row: dict[str, Any]) -> list[str]:
    fields = [field for field in _all_missing_fields(row) if _is_optional_gap_field(field)]
    groups: list[str] = []
    text = " ".join(field.lower() for field in fields)
    if "vwap" in text:
        groups.append("可选：已用日线替代 VWAP")
    if any(token in text for token in ("relative_strength", "relative strength", "rs_vs", "spy", "qqq", "benchmark")):
        groups.append("可选：相对强弱缺失")
    return _dedupe_text(groups)


def _optional_group_summary(groups: list[str]) -> str:
    if not groups:
        return ""
    return "可选项缺失"


def _is_optional_gap_field(field: str) -> bool:
    text = str(field or "").strip().lower()
    if not text:
        return False
    return any(
        token in text
        for token in (
            "vwap",
            "relative_strength",
            "relative strength",
            "rs_vs",
            "rs_",
            "spy_relative",
            "qqq_relative",
            "benchmark",
        )
    )


def _profile_missing(row: dict[str, Any]) -> bool:
    ticker = str(row.get("ticker") or "").strip().upper()
    company = str(row.get("company_name") or row.get("companyName") or row.get("name") or "").strip()
    company_missing = not company or company.upper() == ticker
    sector = _clean_text(row.get("sector") or row.get("industry") or row.get("model"))
    return bool(company_missing or not sector)


def _price_data_state(row: dict[str, Any], field_text: str | None = None) -> str:
    status = str(row.get("data_status") or "").upper()
    text = field_text
    if text is None:
        debug = row.get("debug") if isinstance(row.get("debug"), dict) else {}
        fields = []
        fields.extend(debug.get("data_missing_fields") or [])
        fields.extend(row.get("data_missing_fields") or [])
        text = " ".join(str(item).lower() for item in fields)
    price = _number(row.get("current_price"))
    if bool(row.get("is_stale")) or status == "STALE" or "current_price_stale" in text or "price_stale" in text:
        return "stale"
    if price is None and (status == "MISSING_PRICE" or "current_price" in text or "price" in text or "quote" in text):
        return "missing"
    return "ok"


def _missing_group_text(row: dict[str, Any]) -> str:
    groups = _missing_groups(row)
    return "、".join(groups[:4])


def _data_impact_sentence(report: dict[str, Any], confidence: str) -> str:
    if confidence == "高":
        return "关键价格、评分和区间数据较完整，可直接阅读结论。"
    if confidence == "中":
        return "结论可读，但估值或技术细节需要结合 Drawer / 缓存继续复核。"
    return "只做方向性研究入口，需先补价格、评分或技术缓存。"


def _entry_sentence(report: dict[str, Any]) -> str:
    label = str(report.get("entry_display_label") or "").strip()
    hint = str(report.get("entry_action_hint") or report.get("entry_display_reason") or "").strip()
    if label and hint:
        return f"{label}；{hint}"
    return label or hint or "缺少明确买区提示，先按观察处理。"


def _decision_to_sentence(report: dict[str, Any]) -> str:
    status = _core_status(report)
    decision = str(report.get("decision") or "")
    if status in {"价值复核", "近端复核"}:
        return "估值或近端区间已值得复核，但仍需要技术结构、承接和基本面确认。"
    if status == "买区内":
        return "价格进入纪律参考区，仍需结合仓位计划和承接确认。"
    if status == "追高风险":
        return "价格处于追高语境，系统建议等待回踩或新的确认线。"
    if decision == "DATA_MISSING":
        return "数据存在缺口，但报告仍给出可读结论，缺口在末尾说明。"
    return "当前以观察和复核为主，不把单一状态码当交易指令。"


def _next_step_sentence(report: dict[str, Any]) -> str:
    steps = report.get("next_technical_steps") or []
    if steps:
        return str(steps[0])
    if report.get("confirmation_price"):
        return f"观察能否重新站上确认线 {_money(report.get('confirmation_price'))}。"
    if report.get("invalidation_price"):
        return f"观察是否守住失效线 {_money(report.get('invalidation_price'))}。"
    return "等待价格、技术和基本面缓存进一步补齐。"


def _average_score(*values: Any) -> float | None:
    numbers = [_number(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _history_return(history: pd.DataFrame, days: int) -> float | None:
    if history is None or history.empty or "close" not in history:
        return None
    closes = pd.to_numeric(history["close"], errors="coerce").dropna()
    if len(closes) <= days:
        return None
    current = float(closes.iloc[-1])
    base = float(closes.iloc[-days - 1])
    if base == 0:
        return None
    return (current - base) / base * 100


def _history_ytd_return(history: pd.DataFrame) -> float | None:
    if history is None or history.empty or "close" not in history or "date" not in history:
        return None
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"])
    if frame.empty:
        return None
    current = float(frame.iloc[-1]["close"])
    year = frame.iloc[-1]["date"].year
    ytd = frame[frame["date"].dt.year == year]
    if ytd.empty:
        return None
    base = float(ytd.iloc[0]["close"])
    if base == 0:
        return None
    return (current - base) / base * 100


def _selected_symbol(tickers: list[str]) -> str:
    query_symbol = str(st.query_params.get("symbol", "")).strip().upper()
    if query_symbol:
        return query_symbol
    return ""


def select_radar_symbols(watchlist: list[str], sample_symbols: list[str] | None = None) -> tuple[list[str], str]:
    real = _normalize_symbols(watchlist)
    if real:
        return real, "watchlist"
    sample = _normalize_symbols(sample_symbols or [])
    if sample:
        return sample, "sample fallback"
    return [], "empty watchlist"


def _normalize_symbols(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    symbols: list[str] = []
    for value in values:
        symbol = str(value or "").strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def _single_report_row(symbol: str) -> dict[str, Any] | None:
    cached = _dashboard_row(symbol)
    if cached:
        return cached
    try:
        from data.fundamentals import FundamentalCache
        from ui import dashboard as dashboard_ui

        return dashboard_ui._load_cached_dashboard_row(FundamentalCache(), symbol)
    except Exception:
        return None


def _dashboard_row(symbol: str) -> dict[str, Any] | None:
    table = st.session_state.get("dashboard_table_cache")
    if not isinstance(table, pd.DataFrame) or table.empty:
        return None
    symbol_upper = symbol.upper()
    matches = table[table["symbol"].astype(str).str.upper() == symbol_upper] if "symbol" in table.columns else pd.DataFrame()
    if matches.empty:
        return None
    return dict(matches.iloc[0].to_dict())


def _scores_from_row(row: dict[str, Any] | None) -> RadarScores | None:
    if not row:
        return None
    return RadarScores(
        final_score=_number(_row_value(row, "totalScore")),
        quality_score=_number(_row_value(row, "qualityScore", "quality_score")),
        growth_score=_number(_row_value(row, "growthScore", "growth_score")),
        valuation_score=_number(_row_value(row, "valuationScore", "entryScore", "valuation_score")),
        technical_score=_number(_row_value(row, "technicalScore", "technical_score")),
        risk_score=_number(_row_value(row, "riskScore", "risk_score")),
    )


def _watch_points(row: dict[str, Any] | None) -> list[str]:
    if not row:
        return []
    points = []
    points.extend(_list_value(row, "missingDataExplanation"))
    points.extend(_list_value(row, "decisionReviewReasons"))
    points.extend(_list_value(row, "decisionBlockReasons"))
    return points


def _row_value(row: dict[str, Any] | None, *keys: str) -> Any:
    if not row:
        return None
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _dict_value(row: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    value = _row_value(row, key)
    return value if isinstance(value, dict) else None


def _list_value(row: dict[str, Any] | None, key: str) -> list[str]:
    value = _row_value(row, key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if value else []


def _entry_display_html(row: dict[str, Any]) -> str:
    label = str(row.get("entry_display_label") or "暂无参考买区").strip()
    hint = str(row.get("entry_action_hint") or row.get("entry_display_reason") or "").strip()
    if not hint:
        hint = "只读参考，不改变门禁"
    return (
        '<div class="ai-radar-entry-ref">'
        f'<strong>{escape(label)}</strong>'
        f'<span>{escape(hint)}</span>'
        "</div>"
    )


def _decision_label(value: str) -> str:
    return value if value else "UNKNOWN"


def _data_status_label(value: str) -> str:
    return {
        "OK": "正常",
        "STALE": "价格可能过期",
        "MISSING_PRICE": "缺价格",
        "MISSING_SCORE": "缺评分",
        "MISSING_BUY_ZONE": "缺击球区",
    }.get(value, value or "未知")


def _price_position_label(value: Any) -> str:
    return format_zone_status(value)


def _decision_tone(value: str) -> str:
    return {
        "ALLOW_BUY": "allow",
        "WAIT": "wait",
        "BLOCK_CHASE": "block",
        "AVOID": "avoid",
        "DATA_MISSING": "missing",
    }.get(value, "wait")


def _zone_text(value: Any) -> str:
    return format_buy_zone(value)


def _empty_card_html(title: str, message: str) -> str:
    return (
        '<section class="ai-radar-card">'
        f'<div class="ai-radar-section-title"><span>{escape(title)}</span><b>暂无图表</b></div>'
        f"<p class=\"ai-radar-empty-note\">{escape(message)}</p>"
        "</section>"
    )


def _money(value: Any) -> str:
    number = _number(value)
    return "N/A" if number is None else f"${number:,.2f}"


def _compact_money(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    abs_value = abs(number)
    if abs_value >= 1_000_000_000_000:
        return f"${number / 1_000_000_000_000:.2f}T"
    if abs_value >= 1_000_000_000:
        return f"${number / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"${number / 1_000_000:.1f}M"
    return f"${number:,.0f}"


def _compact_number(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    abs_value = abs(number)
    if abs_value >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if abs_value >= 1_000:
        return f"{number / 1_000:.1f}K"
    return f"{number:,.0f}"


def _pct(value: Any) -> str:
    number = _number(value)
    return "N/A" if number is None else f"{number:.1f}%"


def _signed_pct(value: Any) -> str:
    number = _number(value)
    return "N/A" if number is None else f"{number:+.1f}%"


def _number_text(value: Any) -> str:
    number = _number(value)
    return "N/A" if number is None else f"{number:.1f}"


def _multiple(value: Any) -> str:
    number = _number(value)
    return "N/A" if number is None else f"{number:.1f}x"


def _ratio_pct(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    if abs(number) <= 1:
        number *= 100
    return f"{number:.1f}%"


def _range_text(low: Any, high: Any) -> str:
    low_number = _number(low)
    high_number = _number(high)
    if low_number is None and high_number is None:
        return "N/A"
    if low_number is None:
        return f"≤ {_money(high_number)}"
    if high_number is None:
        return f"≥ {_money(low_number)}"
    return f"{_money(low_number)} - {_money(high_number)}"


def _short_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "N/A"
    return text.replace("T", " ")[:16]


def _number(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _first_number(*sources: Any) -> float | None:
    if not sources:
        return None
    if len(sources) > 1 and all(isinstance(source, dict) for source in sources[:-1]) and isinstance(sources[-1], str):
        keys = (str(sources[-1]),)
        containers = sources[:-1]
    else:
        containers = [source for source in sources if isinstance(source, dict)]
        keys = tuple(str(source) for source in sources if isinstance(source, str))
    for container in containers:
        for key in keys:
            if key in container:
                number = _number(container.get(key))
                if number is not None:
                    return number
    return None


def _first_present(container: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in container and container.get(key) not in (None, ""):
            return container.get(key)
    return None


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"none", "nan", "n/a", "unknown"} else text


def _dedupe_text(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _render_styles() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stMainBlockContainer"],
        div.block-container {
            margin-left:252px !important;
            margin-right:24px !important;
            width:calc(100vw - 300px) !important;
            max-width:calc(100vw - 300px) !important;
        }
        @media (max-width: 980px) {
            [data-testid="stMainBlockContainer"],
            div.block-container {
                margin-left:0 !important;
                margin-right:0 !important;
                width:100% !important;
                max-width:100% !important;
            }
        }
        .ai-radar-filter-chips {
            display:flex;
            align-items:center;
            flex-wrap:wrap;
            gap:8px;
            margin:6px 0 12px;
        }
        .ai-radar-filter-chip {
            display:inline-flex;
            align-items:center;
            gap:6px;
            min-height:30px;
            padding:0 12px;
            border:1px solid #D8E0EA;
            border-radius:999px;
            background:#FFFFFF;
            color:#334155 !important;
            text-decoration:none !important;
            font-size:12px;
            font-weight:760;
        }
        .ai-radar-filter-chip b {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            min-width:20px;
            height:20px;
            padding:0 5px;
            border-radius:999px;
            background:#EEF2F7;
            color:#0B1F3A;
            font-size:11px;
        }
        .ai-radar-filter-chip.active {
            background:#0B1F3A;
            border-color:#0B1F3A;
            color:#FFFFFF !important;
        }
        .ai-radar-filter-chip.active b {
            background:rgba(255,255,255,0.16);
            color:#FFFFFF;
        }
        .ai-radar-list-card {
            border:1px solid #D8E0EA;
            background:#FFFFFF;
            border-radius:10px;
            margin-top:12px;
            overflow:hidden;
            box-shadow:0 10px 28px rgba(15, 23, 42, 0.06);
        }
        .ai-radar-section-head {
            display:flex;
            justify-content:space-between;
            align-items:center;
            padding:12px 16px;
            border-bottom:1px solid #E6EDF5;
            background:#F8FAFC;
        }
        .ai-radar-section-head strong { font-size:15px; color:#0B1F3A; letter-spacing:0; }
        .ai-radar-section-head span { font-size:12px; color:#64748B; }
        .ai-radar-list-note {
            margin:0;
            padding:10px 16px;
            border-bottom:1px solid #EEF2F7;
            color:#64748B;
            font-size:12px;
            background:#FFFFFF;
        }
        .ai-radar-table-wrap { overflow-x:auto; }
        .ai-radar-table {
            width:100%;
            border-collapse:collapse;
            font-size:12px;
        }
        .ai-radar-table th {
            text-align:left;
            color:#64748B;
            background:#F8FAFC;
            padding:9px 12px;
            border-bottom:1px solid #E2E8F0;
            white-space:nowrap;
            font-weight:700;
        }
        .ai-radar-table td {
            padding:10px 12px;
            border-bottom:1px solid #EEF2F7;
            color:#1E293B;
            vertical-align:middle;
        }
        .ai-radar-table tr:hover td,
        .ai-radar-table tr.active td { background:#F8FBFF; }
        .ai-radar-ticker {
            color:#0B1F3A !important;
            font-weight:800;
            text-decoration:none !important;
            font-size:13px;
        }
        .ai-radar-status-pill,
        .ai-radar-data-confidence,
        .ai-radar-report-status,
        .ai-radar-report-link {
            display:inline-flex;
            align-items:center;
            min-height:24px;
            padding:2px 9px;
            border-radius:999px;
            border:1px solid #D8E0EA;
            background:#F8FAFC;
            color:#334155;
            font-weight:700;
            white-space:nowrap;
            text-decoration:none !important;
        }
        .ai-radar-data-confidence.高 { background:#ECFDF3; color:#166534; border-color:#BBE5C6; }
        .ai-radar-data-confidence.中 { background:#EFF6FF; color:#1D4E89; border-color:#BFDBFE; }
        .ai-radar-data-confidence.低 { background:#FFFBEB; color:#92400E; border-color:#FDE68A; }
        .ai-radar-data-confidence.不足 { background:#FFF1F2; color:#9F1239; border-color:#F4C7CE; }
        .ai-radar-company-cell {
            display:flex;
            flex-direction:column;
            gap:2px;
            min-width:0;
        }
        .ai-radar-company-cell strong {
            color:#0B1F3A;
            font-size:12px;
            font-weight:780;
        }
        .ai-radar-company-cell span,
        .ai-radar-muted {
            color:#64748B;
            font-size:11px;
        }
        .ai-radar-report-status {
            background:#F8FAFC;
            color:#334155;
            border-color:#E2E8F0;
        }
        .ai-radar-report-link {
            background:transparent;
            color:#0B1F3A !important;
            border-color:transparent;
            padding:0;
            min-height:auto;
        }
        .ai-radar-report-link:hover { text-decoration:underline !important; }
        .ai-radar-research-report {
            max-width:1280px;
            margin:18px auto 0;
            padding:0;
            border:1px solid #D8E0EA;
            background:#FFFFFF;
            border-radius:12px;
            overflow:hidden;
            box-shadow:0 18px 46px rgba(15, 23, 42, 0.08);
        }
        .ai-radar-research-header {
            position:relative;
            display:grid;
            grid-template-columns:minmax(260px, 1fr) auto;
            gap:16px;
            padding:22px 24px;
            background:linear-gradient(135deg, #0B1F3A 0%, #17375F 100%);
            color:#FFFFFF;
        }
        .ai-radar-title-block span,
        .ai-radar-title-block em {
            display:block;
            color:#BED3EA;
            font-size:12px;
            font-style:normal;
        }
        .ai-radar-title-block h1 {
            margin:4px 0 0;
            font-size:38px;
            line-height:1;
            letter-spacing:0;
        }
        .ai-radar-title-block p {
            margin:6px 0;
            color:#F8FAFC;
            font-size:15px;
        }
        .ai-radar-zone-badge {
            align-self:start;
            padding:6px 12px;
            border-radius:999px;
            background:rgba(255,255,255,0.14);
            border:1px solid rgba(255,255,255,0.22);
            font-weight:800;
        }
        .ai-radar-header-stats {
            grid-column:1 / -1;
            display:grid;
            grid-template-columns:repeat(6, minmax(0, 1fr));
            gap:10px;
        }
        .ai-radar-header-stats div {
            background:rgba(255,255,255,0.10);
            border:1px solid rgba(255,255,255,0.16);
            border-radius:8px;
            padding:9px 10px;
        }
        .ai-radar-header-stats span {
            display:block;
            color:#BED3EA;
            font-size:11px;
            margin-bottom:3px;
        }
        .ai-radar-header-stats strong {
            color:#FFFFFF;
            font-size:14px;
        }
        .ai-radar-research-section,
        .ai-radar-research-grid,
        .ai-radar-opinion-grid,
        .ai-radar-data-quality,
        .ai-radar-report-foot {
            margin:14px 18px;
        }
        .ai-radar-research-grid,
        .ai-radar-opinion-grid {
            display:grid;
            gap:12px;
        }
        .ai-radar-research-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); }
        .ai-radar-opinion-grid { grid-template-columns:repeat(3, minmax(0, 1fr)); }
        .ai-radar-card,
        .ai-radar-summary-section {
            border:1px solid #E2E8F0;
            background:#FFFFFF;
            border-radius:10px;
            padding:14px;
        }
        .ai-radar-section-title {
            display:flex;
            justify-content:space-between;
            gap:12px;
            align-items:baseline;
            margin-bottom:10px;
        }
        .ai-radar-section-title span {
            color:#0B1F3A;
            font-size:14px;
            font-weight:850;
        }
        .ai-radar-section-title b {
            color:#64748B;
            font-size:11px;
            font-weight:700;
        }
        .ai-radar-summary-lines p {
            margin:7px 0;
            color:#26364D;
            font-size:14px;
            line-height:1.65;
        }
        .ai-radar-range-axis {
            display:flex;
            justify-content:space-between;
            color:#64748B;
            font-size:11px;
            margin-bottom:8px;
        }
        .ai-radar-range-row {
            display:grid;
            grid-template-columns:112px minmax(160px, 1fr) 142px;
            gap:10px;
            align-items:center;
            padding:7px 0;
            border-top:1px solid #EEF2F7;
            font-size:12px;
        }
        .ai-radar-range-row span { color:#334155; font-weight:700; }
        .ai-radar-range-row b { color:#0F172A; font-weight:750; text-align:right; }
        .ai-radar-range-track {
            position:relative;
            height:10px;
            border-radius:999px;
            background:#E8EEF5;
        }
        .ai-radar-range-track > i:not(.ai-radar-current-marker) {
            position:absolute;
            top:2px;
            height:6px;
            border-radius:999px;
        }
        .ai-radar-range-track i.blue { background:#2563EB; }
        .ai-radar-range-track i.slate { background:#64748B; }
        .ai-radar-range-track i.green { background:#16A34A; }
        .ai-radar-range-track i.amber { background:#D97706; }
        .ai-radar-range-track i.orange { background:#EA580C; }
        .ai-radar-range-track i.red { background:#DC2626; }
        .ai-radar-current-marker {
            position:absolute;
            top:-6px;
            width:2px;
            height:22px;
            background:#0B1F3A;
            border-radius:2px;
        }
        .ai-radar-current-marker b {
            position:absolute;
            top:-20px;
            left:-42px;
            color:#0B1F3A;
            background:#FFFFFF;
            border:1px solid #CBD5E1;
            border-radius:6px;
            padding:1px 5px;
            font-size:10px;
            white-space:nowrap;
        }
        .ai-radar-score-grid,
        .ai-radar-data-quality-grid {
            display:grid;
            grid-template-columns:repeat(3, minmax(0, 1fr));
            gap:9px;
        }
        .ai-radar-score-grid div,
        .ai-radar-data-quality-grid div {
            background:#F8FAFC;
            border:1px solid #E8EEF5;
            border-radius:8px;
            padding:9px 10px;
        }
        .ai-radar-score-grid span,
        .ai-radar-data-quality-grid span {
            display:block;
            color:#64748B;
            font-size:11px;
            margin-bottom:4px;
        }
        .ai-radar-score-grid strong,
        .ai-radar-data-quality-grid strong {
            color:#0B1F3A;
            font-size:16px;
        }
        .ai-radar-card ul {
            margin:0;
            padding-left:18px;
            color:#334155;
            font-size:13px;
            line-height:1.7;
        }
        .ai-radar-metric-table {
            width:100%;
            border-collapse:collapse;
            font-size:12px;
        }
        .ai-radar-metric-table td {
            padding:8px 6px;
            border-top:1px solid #EEF2F7;
        }
        .ai-radar-metric-table td:first-child { color:#64748B; font-weight:700; }
        .ai-radar-metric-table td:last-child { color:#0F172A; text-align:right; font-weight:750; }
        .ai-radar-empty-note { color:#64748B; font-size:13px; margin:0; }
        .ai-radar-report-foot {
            display:flex;
            gap:14px;
            flex-wrap:wrap;
            color:#64748B;
            font-size:12px;
            border-top:1px solid #E8EEF5;
            padding:12px 0 16px;
        }
        @media (max-width: 980px) {
            .ai-radar-research-header,
            .ai-radar-header-stats,
            .ai-radar-research-grid,
            .ai-radar-opinion-grid,
            .ai-radar-score-grid,
            .ai-radar-data-quality-grid { grid-template-columns:1fr; }
            .ai-radar-range-row { grid-template-columns:1fr; }
            .ai-radar-range-row b { text-align:left; }
        }
        /*
           Legacy selectors below are kept for old tests and debug blocks; the
           active page uses ai-radar-research-* classes above.
        */
        .ai-radar-list-card,
        .ai-radar-report {
            border: 1px solid #E2E8F0;
            background: #FFFFFF;
            border-radius: 8px;
            margin-top: 12px;
            overflow: hidden;
        }
        .ai-radar-section-head {
            display:flex;
            justify-content:space-between;
            align-items:center;
            padding:10px 14px;
            border-bottom:1px solid #E8EEF5;
        }
        .ai-radar-section-head strong { font-size:14px; color:#0F172A; }
        .ai-radar-section-head span { font-size:12px; color:#64748B; }
        .ai-radar-table-wrap { overflow-x:auto; }
        .ai-radar-table {
            width:100%;
            border-collapse:collapse;
            font-size:12px;
        }
        .ai-radar-table th {
            text-align:left;
            color:#64748B;
            background:#F8FAFC;
            padding:8px 10px;
            border-bottom:1px solid #E2E8F0;
            white-space:nowrap;
        }
        .ai-radar-table td {
            padding:8px 10px;
            border-bottom:1px solid #EEF2F7;
            color:#1E293B;
            vertical-align:top;
        }
        .ai-radar-table tr.active td { background:#F8FBFF; }
        .ai-radar-table a { color:#0F172A; font-weight:750; text-decoration:none; }
        .ai-radar-reasons { max-width:340px; color:#64748B !important; }
        .ai-radar-entry-ref {
            min-width:150px;
            max-width:230px;
            line-height:1.25;
        }
        .ai-radar-entry-ref strong {
            display:block;
            color:#0F172A;
            font-size:12px;
            font-weight:750;
        }
        .ai-radar-entry-ref span {
            display:block;
            color:#64748B;
            font-size:11px;
            margin-top:3px;
        }
        .ai-radar-decision {
            display:inline-flex;
            padding:2px 8px;
            border-radius:999px;
            background:#F1F5F9;
            border:1px solid #E2E8F0;
            font-weight:700;
            white-space:nowrap;
        }
        tr.allow .ai-radar-decision { background:#F0FDF4; color:#166534; border-color:#BBE5C6; }
        tr.block td { background:#FFF8F8; }
        tr.block .ai-radar-decision,
        tr.avoid .ai-radar-decision { background:#FFF1F2; color:#9F1239; border-color:#F4C7CE; }
        tr.missing td { background:#F8FAFC; }
        tr.missing .ai-radar-decision { background:#F1F5F9; color:#475569; border-color:#CBD5E1; }
        .ai-radar-report {
            max-width: 1080px;
            margin: 16px auto 0;
            padding: 14px;
        }
        .ai-radar-report-top {
            display:grid;
            grid-template-columns: repeat(4, minmax(140px, 1fr));
            gap:8px;
            margin-bottom:12px;
        }
        .ai-radar-report-top div,
        .ai-radar-card {
            border:1px solid #E2E8F0;
            background:#FBFCFE;
            border-radius:8px;
            padding:10px;
        }
        .ai-radar-report-top .wide { grid-column: 1 / -1; }
        .ai-radar-report-top span,
        .ai-radar-card h3,
        .ai-radar-score-grid span,
        .ai-radar-card.zones span {
            display:block;
            color:#64748B;
            font-size:12px;
            font-weight:650;
            margin:0 0 4px;
        }
        .ai-radar-report-top strong {
            color:#0F172A;
            font-size:15px;
            line-height:1.25;
        }
        .ai-radar-report.block { border-left:4px solid #B91C1C; }
        .ai-radar-report.allow { border-left:4px solid #15803D; }
        .ai-radar-report.wait,
        .ai-radar-report.missing { border-left:4px solid #D97706; }
        .ai-radar-report-grid {
            display:grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap:10px;
        }
        .ai-radar-card h3 {
            color:#0F172A;
            font-size:13px;
            margin-bottom:8px;
        }
        .ai-radar-score-grid {
            display:grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap:8px;
        }
        .ai-radar-score-grid div,
        .ai-radar-card.zones div {
            border-top:1px solid #E8EEF5;
            padding-top:8px;
        }
        .ai-radar-score-grid strong,
        .ai-radar-card.zones strong {
            color:#0F172A;
            font-size:16px;
        }
        .ai-radar-card ul {
            margin:0;
            padding-left:18px;
            color:#334155;
            font-size:13px;
            line-height:1.55;
        }
        .ai-radar-report-foot {
            display:flex;
            gap:14px;
            flex-wrap:wrap;
            color:#64748B;
            font-size:12px;
            margin-top:12px;
            border-top:1px solid #E8EEF5;
            padding-top:10px;
        }
        .ai-radar-debug {
            border:1px solid #E2E8F0;
            background:#FFFFFF;
            border-radius:8px;
            padding:10px;
        }
        .ai-radar-debug-summary {
            display:grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap:8px;
            margin-bottom:8px;
        }
        .ai-radar-debug-summary div {
            background:#F8FAFC;
            border:1px solid #E8EEF5;
            border-radius:6px;
            padding:8px;
        }
        .ai-radar-debug-summary span {
            display:block;
            font-size:11px;
            color:#64748B;
            margin-bottom:3px;
        }
        .ai-radar-debug-summary strong {
            color:#0F172A;
            font-size:12px;
            line-height:1.35;
        }
        .ai-radar-debug-note {
            border:1px solid #F3D19E;
            background:#FFFBEB;
            color:#78350F;
            border-radius:6px;
            padding:8px 10px;
            font-size:12px;
            line-height:1.4;
            margin:6px 0 8px;
        }
        .ai-radar-debug-table {
            width:100%;
            border-collapse:collapse;
            font-size:12px;
        }
        .ai-radar-debug-table th,
        .ai-radar-debug-table td {
            border-top:1px solid #E8EEF5;
            padding:7px 8px;
            text-align:left;
            vertical-align:top;
        }
        .ai-radar-debug-table th {
            color:#64748B;
            background:#F8FAFC;
            font-weight:700;
        }
        @media (max-width: 900px) {
            .ai-radar-report-top,
            .ai-radar-report-grid,
            .ai-radar-debug-summary { grid-template-columns:1fr; }
        }
        /* Keep the active research-view polish after legacy debug selectors. */
        .ai-radar-list-card {
            border:1px solid #D8E0EA;
            border-radius:10px;
            box-shadow:0 10px 28px rgba(15, 23, 42, 0.06);
        }
        .ai-radar-section-head {
            padding:12px 16px;
            background:#F8FAFC;
            border-bottom:1px solid #E6EDF5;
        }
        .ai-radar-section-head strong { font-size:15px; color:#0B1F3A; }
        .ai-radar-table th { padding:9px 12px; font-weight:700; }
        .ai-radar-table td { padding:10px 12px; vertical-align:middle; }
        .ai-radar-table tr:hover td { background:#F8FBFF; }
        .ai-radar-research-report .ai-radar-card,
        .ai-radar-research-report .ai-radar-summary-section {
            border:1px solid #E2E8F0;
            background:#FFFFFF;
            border-radius:10px;
            padding:14px;
        }
        .ai-radar-research-report .ai-radar-score-grid div,
        .ai-radar-research-report .ai-radar-data-quality-grid div {
            background:#F8FAFC;
            border:1px solid #E8EEF5;
            border-radius:8px;
            padding:9px 10px;
        }
        .ai-radar-research-report .ai-radar-report-foot {
            margin:14px 18px;
            padding:12px 0 16px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
