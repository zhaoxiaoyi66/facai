from __future__ import annotations

from html import escape
from typing import Any

import pandas as pd
import streamlit as st

from data.ai_stock_radar import RADAR_REPORT_VERSION, RadarScores, build_ai_stock_radar_list_row, build_ai_stock_radar_report
from data.market_context import build_market_context
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
    body = "".join(_list_row_html(row, selected) for row in rows)
    st.markdown(
        (
            '<section class="ai-radar-list-card">'
            f'<div class="ai-radar-section-head"><strong>Radar 列表</strong><span>来源：{escape(source)}；轻量读取，不自动刷新评分或价格</span></div>'
            '<div class="ai-radar-table-wrap">'
            '<table class="ai-radar-table">'
            "<thead><tr>"
            "<th>Ticker</th><th>Decision</th><th>Block reasons</th><th>公司</th><th>当前价</th><th>更新</th><th>Stale</th><th>总分</th>"
            "<th>击球区</th><th>核心仓</th><th>交易仓</th><th>数据</th>"
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
    st.markdown('<div id="radar-report"></div>', unsafe_allow_html=True)
    st.markdown(_report_html(report.to_dict(), market), unsafe_allow_html=True)


def _list_row(ticker: str) -> dict[str, Any]:
    row = _dashboard_row(ticker)
    snapshot = _dict_value(row, "rawSnapshot")
    technicals = _dict_value(row, "rawTechnicals")
    return build_ai_stock_radar_list_row(
        ticker,
        company_name=str(_row_value(row, "companyName") or ticker),
        scores=None if snapshot and technicals else _scores_from_row(row),
        snapshot=snapshot,
        technicals=technicals,
    )


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rank = {
        "BLOCK_CHASE": 0,
        "DATA_MISSING": 1,
        "AVOID": 2,
        "WAIT": 3,
        "ALLOW_BUY": 4,
    }
    return sorted(
        rows,
        key=lambda row: (
            rank.get(str(row.get("decision") or ""), 9),
            -(_number(row.get("final_score")) or -1),
            str(row.get("ticker") or ""),
        ),
    )


def _list_row_html(row: dict[str, Any], selected: str) -> str:
    ticker = str(row.get("ticker") or "")
    decision = str(row.get("decision") or "")
    active = " active" if ticker == selected else ""
    reason_text = "；".join(str(item) for item in (row.get("block_reasons") or [])[:2]) or "无"
    return (
        f'<tr class="{escape(_decision_tone(decision))}{active}">'
        f'<td><a href="?page=ai-radar&symbol={escape(ticker, quote=True)}#radar-report" target="_self">{escape(ticker)}</a></td>'
        f'<td><span class="ai-radar-decision">{escape(_decision_label(decision))}</span></td>'
        f'<td class="ai-radar-reasons">{escape(reason_text)}</td>'
        f'<td>{escape(str(row.get("company_name") or "-"))}</td>'
        f'<td>{escape(_money(row.get("current_price")))}</td>'
        f'<td>{escape(_short_time(row.get("data_updated_at")))}</td>'
        f'<td>{escape("是" if row.get("is_stale") else "否")}</td>'
        f'<td>{escape(_number_text(row.get("final_score")))}</td>'
        f'<td>{escape(_zone_text(row.get("buy_zone")))}</td>'
        f'<td>{escape(_pct(row.get("core_max_pct")))}</td>'
        f'<td>{escape(_pct(row.get("trade_max_pct")))}</td>'
        f'<td>{escape(_data_status_label(str(row.get("data_status") or "")))}</td>'
        "</tr>"
    )


def _report_html(report: dict[str, Any], market: dict[str, Any]) -> str:
    decision = str(report.get("decision") or "")
    allowed = (_number(report.get("allowed_add_pct")) or 0.0) > 0
    block_reasons = report.get("block_reasons") or []
    return (
        f'<section class="ai-radar-report {_decision_tone(decision)}">'
        '<div class="ai-radar-report-top">'
        f'<div><span>当前结论</span><strong>{escape(_decision_label(decision))}</strong></div>'
        f'<div><span>是否允许新增</span><strong>{escape("允许" if allowed else "不允许")}</strong></div>'
        f'<div class="wide"><span>阻止原因</span><strong>{escape("；".join(str(item) for item in block_reasons) or "无")}</strong></div>'
        f'<div><span>当前价格</span><strong>{escape(_money(report.get("current_price")))}</strong></div>'
        f'<div><span>击球区</span><strong>{escape(_zone_text(report.get("buy_zone")))}</strong></div>'
        f'<div><span>核心仓上限</span><strong>{escape(_pct(report.get("core_max_pct")))}</strong></div>'
        f'<div><span>交易仓上限</span><strong>{escape(_pct(report.get("trade_max_pct")))}</strong></div>'
        f'<div><span>allowed_add_pct</span><strong>{escape(_pct(report.get("allowed_add_pct")))}</strong></div>'
        f'<div><span>price_source</span><strong>{escape(str(report.get("price_source") or "N/A"))}</strong></div>'
        f'<div><span>fetched_at</span><strong>{escape(str(report.get("data_updated_at") or "N/A"))}</strong></div>'
        f'<div><span>history_status</span><strong>{escape(str(report.get("history_status") or "N/A"))}</strong></div>'
        "</div>"
        '<div class="ai-radar-report-grid">'
        f'{_score_card_html(report)}'
        f'{_zones_card_html(report)}'
        f'{_text_card_html("核心摘要", [str(report.get("summary") or "")])}'
        f'{_text_card_html("看多理由", report.get("bull_points") or [])}'
        f'{_text_card_html("风险点", report.get("risk_points") or [])}'
        f'{_text_card_html("关键验证点", report.get("watch_points") or [])}'
        "</div>"
        '<div class="ai-radar-report-foot">'
        f'<span>数据更新时间：{escape(str(market.get("fetchedAt") or "N/A"))}</span>'
        f'<span>数据状态：{escape(_data_status_label(str(report.get("data_status") or "")))}</span>'
        f'<span>报告版本：{escape(RADAR_REPORT_VERSION)}</span>'
        "</div>"
        "</section>"
    )


def _score_card_html(report: dict[str, Any]) -> str:
    items = [
        ("总分", report.get("final_score")),
        ("质量", report.get("quality_score")),
        ("成长", report.get("growth_score")),
        ("估值", report.get("valuation_score")),
        ("技术", report.get("technical_score")),
        ("风险", report.get("risk_score")),
    ]
    body = "".join(f"<div><span>{escape(label)}</span><strong>{escape(_number_text(value))}</strong></div>" for label, value in items)
    return f'<article class="ai-radar-card score"><h3>评分卡</h3><div class="ai-radar-score-grid">{body}</div></article>'


def _zones_card_html(report: dict[str, Any]) -> str:
    items = [
        ("击球区", report.get("buy_zone")),
        ("观察区", report.get("watch_zone")),
        ("追高区", report.get("chase_zone")),
    ]
    body = "".join(f"<div><span>{escape(label)}</span><strong>{escape(_zone_text(zone))}</strong></div>" for label, zone in items)
    return f'<article class="ai-radar-card zones"><h3>击球区 / 观察区 / 追高区</h3>{body}</article>'


def _text_card_html(title: str, items: list[Any]) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        cleaned = ["暂无明确内容，先保持复查。"]
    body = "".join(f"<li>{escape(item)}</li>" for item in cleaned[:6])
    return f'<article class="ai-radar-card"><h3>{escape(title)}</h3><ul>{body}</ul></article>'


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


def _decision_tone(value: str) -> str:
    return {
        "ALLOW_BUY": "allow",
        "WAIT": "wait",
        "BLOCK_CHASE": "block",
        "AVOID": "avoid",
        "DATA_MISSING": "missing",
    }.get(value, "wait")


def _zone_text(value: Any) -> str:
    zone = value if isinstance(value, dict) else {}
    lower = _number(zone.get("lower"))
    upper = _number(zone.get("upper"))
    if lower is not None and upper is not None:
        return f"{_money(lower)} - {_money(upper)}"
    if upper is not None:
        return f"<= {_money(upper)}"
    if lower is not None:
        return f">= {_money(lower)}"
    return "N/A"


def _money(value: Any) -> str:
    number = _number(value)
    return "N/A" if number is None else f"${number:,.2f}"


def _pct(value: Any) -> str:
    number = _number(value)
    return "N/A" if number is None else f"{number:.1f}%"


def _number_text(value: Any) -> str:
    number = _number(value)
    return "N/A" if number is None else f"{number:.1f}"


def _short_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "N/A"
    return text.replace("T", " ")[:16]


def _number(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _render_styles() -> None:
    st.markdown(
        """
        <style>
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
        @media (max-width: 900px) {
            .ai-radar-report-top,
            .ai-radar-report-grid { grid-template-columns:1fr; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
