from __future__ import annotations

import pandas as pd

from data.dashboard_lanes import (
    DASHBOARD_BLOCKED_ACTIONS,
    row_decision_lane,
    row_final_action,
    row_value,
)
from data.data_health import build_data_health_summary


DataHealthView = dict[str, object]
RiskRadarItem = dict[str, object]


def build_dashboard_risk_radar(table: pd.DataFrame, portfolio_view: dict) -> list[RiskRadarItem]:
    rows = [row for _, row in table.iterrows()]
    portfolio_rows = list((portfolio_view or {}).get("rows") or [])
    overweight = [
        str(row.get("symbol") or "").upper()
        for row in portfolio_rows
        if row.get("overweightSystem") or row.get("overweightPersonal")
    ]
    no_chase = [
        str(row.get("symbol") or "").upper()
        for row in rows
        if row_final_action(row) in DASHBOARD_BLOCKED_ACTIONS or row_decision_lane(row) == "blocked"
    ]
    review = [
        str(row.get("symbol") or "").upper()
        for row in rows
        if row_decision_lane(row) == "review" or "复核" in row_final_action(row)
    ]
    low_confidence = [
        str(row.get("symbol") or "").upper()
        for row in rows
        if str(row.get("dataConfidence") or row.get("confidence") or "").lower() == "low"
    ]
    no_add = [
        str(row.get("symbol") or "").upper()
        for row in rows
        if row_current_add_limit_value(row) <= 0
    ]
    industry_concentration = industry_concentration_symbols(rows, portfolio_rows)
    return [
        {"key": "overweight", "label": "超仓位", "tone": "red", "symbols": overweight, "reason": "暂无" if not overweight else "持仓高于系统或个人上限"},
        {"key": "noChase", "label": "追高风险提醒", "tone": "red", "symbols": no_chase, "reason": "暂无" if not no_chase else "当前价格不适合新增"},
        {"key": "review", "label": "需复核", "tone": "amber", "symbols": review, "reason": "暂无" if not review else "买区异常或数据置信低"},
        {"key": "lowConfidence", "label": "低置信", "tone": "amber", "symbols": low_confidence, "reason": "暂无" if not low_confidence else "数据置信度偏低"},
        {"key": "noAdd", "label": "不可新增", "tone": "slate", "symbols": no_add, "reason": "暂无" if not no_add else "当前可加仓为 0"},
        {"key": "concentration", "label": "行业集中", "tone": "blue", "symbols": industry_concentration, "reason": "暂无" if not industry_concentration else "持仓行业暴露偏集中"},
    ]


def industry_concentration_symbols(rows: list[pd.Series], portfolio_rows: list[dict]) -> list[str]:
    held_symbols = {str(row.get("symbol") or "").upper() for row in portfolio_rows}
    if not held_symbols:
        return []
    industry_map = {
        str(row.get("symbol") or "").upper(): str(row.get("modelType") or row.get("industryModel") or "").strip()
        for row in rows
    }
    grouped: dict[str, list[str]] = {}
    for symbol in held_symbols:
        industry = industry_map.get(symbol)
        if industry:
            grouped.setdefault(industry, []).append(symbol)
    concentrated: list[str] = []
    for symbols in grouped.values():
        if len(symbols) >= 2:
            concentrated.extend(sorted(symbols))
    return concentrated


def row_current_add_limit_value(row: pd.Series) -> float:
    value = row_value(row, "currentAddLimitPercent")
    if value is None:
        value = row_value(row, "maxSuggestedPositionPercent")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_dashboard_data_health_view(table: pd.DataFrame) -> DataHealthView:
    symbols = dashboard_symbols(table)
    try:
        summary = build_data_health_summary(watchlist=symbols)
    except Exception as exc:
        return build_dashboard_data_health_error_view(exc)
    return build_dashboard_data_health_view_from_summary(summary, symbols)


def build_dashboard_data_health_error_view(exc: Exception) -> DataHealthView:
    return {
        "tone": "warning",
        "statusLabel": "检查失败",
        "subtitle": "本地缓存体检",
        "items": [("健康项", "N/A")],
        "issues": [str(exc)],
        "issueSummary": "主要问题 1 项",
    }


def build_dashboard_data_health_view_from_summary(summary: dict, symbols: list[str]) -> DataHealthView:
    decision_blocked_count = int(summary.get("decisionBlockedCount") or 0)
    precision_blocked_count = int(summary.get("preciseBuyZoneBlockedCount") or 0)
    issue_count = (
        int(summary.get("missingPriceCount") or 0)
        + int(summary.get("missingHistoryCount") or 0)
        + int(summary.get("staleHistoryCount") or 0)
        + int(summary.get("finalDecisionErrorCount") or 0)
        + int(summary.get("portfolioMissingPriceCount") or 0)
        + int(summary.get("outcomeMissingCount") or 0)
        + decision_blocked_count
    )
    missing_price_count = int(summary.get("missingPriceCount") or 0)
    severe_price_gap = bool(symbols) and missing_price_count >= max(3, int(len(symbols) * 0.2))
    has_final_decision_error = int(summary.get("finalDecisionErrorCount") or 0) > 0
    if not summary.get("cacheExists") or has_final_decision_error or severe_price_gap or decision_blocked_count:
        tone = "error"
        status_label = "异常"
    elif issue_count:
        tone = "warning"
        status_label = "注意"
    else:
        tone = "ok"
        status_label = "正常"
    issues = [data_health_issue_text(issue) for issue in list(summary.get("topIssues") or [])[:3]]
    items = [
        ("健康项", summary.get("healthyCount", 0)),
        ("价格缺失", summary.get("missingPriceCount", 0)),
        ("历史缺失", summary.get("missingHistoryCount", 0)),
        ("历史过期", summary.get("staleHistoryCount", 0)),
        ("决策结论异常", summary.get("finalDecisionErrorCount", 0)),
        ("持仓缺价", summary.get("portfolioMissingPriceCount", 0)),
        ("复盘缺失", summary.get("outcomeMissingCount", 0)),
    ]
    if decision_blocked_count:
        items.append(("需复核", decision_blocked_count))
    if precision_blocked_count:
        items.append(("精确买点待复核", precision_blocked_count))
    return {
        "tone": tone,
        "statusLabel": status_label,
        "subtitle": "本地缓存体检",
        "items": items,
        "issues": issues,
        "issueSummary": f"主要问题 {len(issues)} 项" if issues else "无主要问题",
    }


def dashboard_symbols(table: pd.DataFrame) -> list[str]:
    return [str(symbol).upper() for symbol in table.get("symbol", []) if str(symbol or "").strip()]


def data_health_issue_text(issue: object) -> str:
    if not isinstance(issue, dict):
        return str(issue)
    symbol = str(issue.get("symbol") or "").upper()
    category = str(issue.get("category") or "")
    labels = {
        "cache_missing": "缓存缺失",
        "missing_price": "价格缺失",
        "stale_quote": "价格过期",
        "missing_history": "历史缺失",
        "stale_history": "历史过期",
        "final_decision_error": "决策结论异常",
        "portfolio_missing_price": "持仓缺价",
        "outcome_missing": "复盘结果缺失",
    }
    label = labels.get(category, category or "数据问题")
    return f"{symbol} {label}".strip()
