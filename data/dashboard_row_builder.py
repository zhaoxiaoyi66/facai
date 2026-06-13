from __future__ import annotations

from typing import Any

from buy_zone_engine import buy_zone_with_manual_override, generate_buy_zone
from data.action_fusion import evaluate_action_fusion
from data.ai_stock_radar import build_ai_stock_radar_list_row
from data.entry_display import build_entry_display
from data.market_context import build_market_history
from data.pullback_acceptance import evaluate_pullback_acceptance
from data.review_queue_builder import ReviewQueueStore
from data.stock_plan import StockPlanStore
from data.structure_entry import evaluate_structure_entry
from data.volume_price_acceptance import evaluate_volume_price_acceptance
from formatting import format_currency, format_multiple, format_percent
from scoring.final_decision_adapter import build_final_decision_bundle
from scoring.metric_sources import fcf_margin_metric, fcf_margin_source_note
from ui.metric_labels import confidence_label, metric_label, model_type_label


DETAIL_GROUPS = [
    (
        "Valuation",
        [
            ("trailingPe", "TTM市盈率"),
            ("forwardPe", "预期市盈率"),
            ("priceToSales", "市销率"),
            ("enterpriseToRevenue", "EV/销售额"),
            ("priceToFcf", "P/FCF"),
            ("freeCashFlowYield", "FCF收益率"),
        ],
    ),
    (
        "Quality",
        [
            ("revenueGrowth", "收入增速"),
            ("operatingMargin", "经营利润率"),
            ("returnOnInvestedCapital", "ROIC"),
            ("fcfMargin", "FCF margin"),
        ],
    ),
    (
        "Balance Sheet",
        [
            ("netDebtToEbitda", "净债务/EBITDA"),
            ("currentRatio", "流动比率"),
        ],
    ),
    (
        "Technical Setup",
        [
            ("rsi14", "RSI14"),
            ("ema20", "EMA20"),
            ("ema50", "EMA50"),
            ("ema200", "EMA200"),
            ("priceVsEma20", "距EMA20"),
            ("priceVsEma50", "距EMA50"),
            ("dailyReturn", "今日涨跌"),
            ("gain20d", "20日涨幅"),
            ("gain60d", "60日涨幅"),
            ("fiftyTwoWeekHigh", "52周高点"),
            ("fiftyTwoWeekLow", "52周低点"),
        ],
    ),
]


def build_dashboard_row(ticker: str, snapshot: dict, technicals: dict, score, data_quality: dict) -> dict:
    high_risk_flags = sum(1 for flag in score.risk_flags if flag.severity == "high")
    medium_risk_flags = sum(1 for flag in score.risk_flags if flag.severity == "medium")
    anti_fomo = _signal_message(score.trading_signals, "anti_fomo")
    left_side_opportunity = _signal_message(score.trading_signals, "left_side_opportunity")
    price = _first_present(technicals.get("price"), snapshot.get("current_price"))
    fcf_metric = fcf_margin_metric(snapshot)
    direct_fcf_margin = fcf_metric.value if fcf_metric.sourceType != "derivedFromMarket" else None
    implied_fcf_margin = fcf_metric.value if fcf_metric.sourceType == "derivedFromMarket" else None
    buy_zone = derive_dashboard_buy_zone(ticker, snapshot, technicals, score)
    plan = StockPlanStore().get_plan(ticker)
    active_buy_zone = buy_zone_with_manual_override(buy_zone, plan) if buy_zone is not None else None
    final_decision = derive_dashboard_final_decision(ticker, snapshot, technicals, score, buy_zone=buy_zone)
    current_add_limit = final_decision.currentAddLimitPercent
    max_portfolio_weight = final_decision.maxPortfolioWeightPercent
    radar_entry_display = _radar_entry_display_fields(ticker, snapshot, technicals)
    structure_entry = evaluate_structure_entry(
        ticker=ticker,
        technicals=technicals,
        decline_reason=str(snapshot.get("decline_reason") or snapshot.get("declineReason") or "未知"),
        thesis_status=str(snapshot.get("thesis_status") or snapshot.get("thesisStatus") or "UNKNOWN"),
    )
    pullback_acceptance = evaluate_pullback_acceptance(
        ticker=ticker,
        technicals={**technicals, **snapshot, **radar_entry_display},
    )
    volume_price_acceptance = evaluate_volume_price_acceptance(
        ticker=ticker,
        daily_bars=_safe_market_history(ticker),
        technicals={**technicals, **snapshot, **radar_entry_display},
    )
    action_fusion = evaluate_action_fusion(
        ticker=ticker,
        context={
            **technicals,
            **snapshot,
            **radar_entry_display,
            "quality_score": score.total_score,
            "valuation_score": getattr(score, "valuation_score", None),
            "volume_price_status": volume_price_acceptance.volume_price_status,
            "volume_price_score": volume_price_acceptance.volume_price_score,
            "volume_ratio": volume_price_acceptance.volume_ratio,
            "volume_regime_cn": volume_price_acceptance.volume_regime_cn,
            "volume_price_reason_cn": volume_price_acceptance.acceptance_reason_cn,
        },
        portfolio_context={
            "max_weight": max_portfolio_weight,
            "target_weight": current_add_limit,
        },
    )

    return {
        "symbol": ticker,
        "companyName": snapshot.get("company_name") or snapshot.get("companyName") or "",
        "rawSnapshot": snapshot,
        "rawTechnicals": technicals,
        "modelType": score.scoring_model,
        "price": format_currency(price),
        "marketCap": _format_billions(snapshot.get("market_cap")),
        "drawdownFromHigh": format_percent(technicals.get("drawdown_from_high_pct")),
        "qualityRating": score.quality_rating,
        "entryRating": score.entry_rating,
        "riskRating": score.risk_rating,
        "valuationStatus": score.valuation_status,
        "action": score.action,
        "finalAction": final_decision.finalAction,
        "decisionLane": final_decision.decisionLane,
        "displayCategory": final_decision.displayCategory,
        "buyZoneStatus": getattr(buy_zone, "currentZone", None),
        "systemZone": buy_zone,
        "activeZone": active_buy_zone,
        "combinedEntry": getattr(buy_zone, "combinedEntry", None) or {},
        "isActionable": final_decision.isActionable,
        "decisionBlockReasons": final_decision.blockReasons,
        "decisionReviewReasons": final_decision.reviewReasons,
        "scoreCurrentAddLimitPercent": getattr(score, "current_add_limit_percent", score.max_suggested_position_percent),
        "scoreMaxPortfolioWeightPercent": getattr(score, "max_portfolio_weight_percent", None),
        "maxSuggestedPositionPercent": score.max_suggested_position_percent,
        "maxPortfolioWeightPercent": max_portfolio_weight,
        "currentAddLimitPercent": current_add_limit,
        "maxSuggestedPosition": _position_limit_text(current_add_limit),
        "maxPortfolioWeight": _portfolio_weight_text(max_portfolio_weight),
        "currentAddLimit": _position_limit_text(current_add_limit),
        "dataConfidence": final_decision.dataConfidence,
        "proxyConfidence": score.proxy_confidence,
        "dataStatus": _data_status_label(score),
        "missingIndustryMetrics": score.missing_industry_metrics or [],
        "proxyMetricsUsed": score.proxy_metrics_used or [],
        "hardMissingFields": getattr(score, "hard_missing_fields", None) or [],
        "notDisclosedFields": getattr(score, "not_disclosed_fields", None) or [],
        "notApplicableFields": getattr(score, "not_applicable_fields", None) or [],
        "proxyUsedFields": getattr(score, "proxy_used_fields", None) or [],
        "confidencePenaltyReasons": getattr(score, "confidence_penalty_reasons", None) or [],
        "modelFitNotes": getattr(score, "model_fit_notes", None) or [],
        "missingMetricImpact": getattr(score, "missing_metric_impacts", None) or [],
        "metricResolutionStatus": getattr(score, "metric_resolution_statuses", None) or [],
        "reviewQueueSummary": ReviewQueueStore().summary(ticker),
        "disclosureReviewSummary": snapshot.get("disclosureReviewSummary") or {},
        "criticalPendingReviewMetrics": snapshot.get("criticalPendingReviewMetrics") or [],
        "humanReadableSummary": getattr(score, "human_readable_summary", None) or {},
        "activeRiskDrivers": getattr(score, "active_risk_drivers", None) or getattr(score, "activeRiskDrivers", None) or [],
        "missingDataExplanation": getattr(score, "missing_data_explanation", None) or [],
        "ratingCap": getattr(score, "rating_cap", None),
        "keyPositiveDrivers": score.key_positives or [],
        "keyNegativeDrivers": score.key_risks or [],
        "trailingPe": format_multiple(snapshot.get("trailing_pe")),
        "forwardPe": format_multiple(snapshot.get("forward_pe")),
        "priceToSales": format_multiple(snapshot.get("price_to_sales")),
        "enterpriseToRevenue": format_multiple(snapshot.get("enterprise_to_revenue")),
        "priceToFcf": format_multiple(snapshot.get("price_to_fcf")),
        "freeCashFlowYield": format_percent(snapshot.get("free_cash_flow_yield"), already_percent=False),
        "revenueGrowth": format_percent(snapshot.get("revenue_growth"), already_percent=False),
        "operatingMargin": format_percent(snapshot.get("operating_margin"), already_percent=False),
        "returnOnInvestedCapital": format_percent(snapshot.get("return_on_invested_capital"), already_percent=False),
        "fcfMargin": format_percent(fcf_metric.value, already_percent=False),
        "directFcfMargin": format_percent(direct_fcf_margin, already_percent=False),
        "impliedFcfMargin": format_percent(implied_fcf_margin, already_percent=False),
        "fcfMarginLabel": "估算FCF利润率" if fcf_metric.sourceType == "derivedFromMarket" else "FCF利润率",
        "fcfMarginSourceType": fcf_metric.sourceType,
        "fcfMarginNote": fcf_margin_source_note(snapshot),
        "netDebtToEbitda": format_multiple(snapshot.get("net_debt_to_ebitda")),
        "currentRatio": format_multiple(snapshot.get("current_ratio")),
        "rsi14": _format_plain_number(technicals.get("rsi14")),
        "ema20": format_currency(technicals.get("ema20")),
        "ema50": format_currency(technicals.get("ema50")),
        "ema200": format_currency(technicals.get("ema200")),
        "gain20d": format_percent(technicals.get("gain_20d_pct")),
        "gain60d": format_percent(technicals.get("gain_60d_pct")),
        "dailyReturn": format_percent(technicals.get("daily_return_pct")),
        "priceVsEma20": format_percent(technicals.get("pct_above_ema20")),
        "priceVsEma50": format_percent(technicals.get("pct_above_ema50")),
        "fiftyTwoWeekHigh": format_currency(_first_present(technicals.get("fifty_two_week_high"), snapshot.get("fifty_two_week_high"))),
        "fiftyTwoWeekLow": format_currency(_first_present(technicals.get("fifty_two_week_low"), snapshot.get("fifty_two_week_low"))),
        "totalScore": score.total_score,
        "valueZone": score.value_zone,
        "rating": score.rating,
        "antiFomo": bool(anti_fomo),
        "leftSideOpportunity": bool(left_side_opportunity),
        "riskFlagCount": high_risk_flags + medium_risk_flags,
        "highRiskFlagCount": high_risk_flags,
        "dataQualityPct": data_quality["pct"],
        "dataNote": _data_note(snapshot, data_quality, score),
        "overheatScore": score.overheat_score,
        "overheatStatus": score.overheat_status,
        "overheatAction": score.overheat_action,
        "overheatRecommendation": score.overheat_recommendation,
        "overheatReasons": score.overheat_reasons or [],
        "structureEntryAdvisor": structure_entry.to_dict(),
        "structureStatus": structure_entry.structure_status,
        "structureScore": structure_entry.structure_score,
        "structureReasons": structure_entry.structure_reasons,
        "structureWarnings": structure_entry.structure_warnings,
        "structureNextSteps": structure_entry.next_confirmation_steps,
        "pullbackAcceptance": pullback_acceptance.to_dict(),
        "acceptanceStatus": pullback_acceptance.acceptance_status,
        "acceptanceScore": pullback_acceptance.acceptance_score,
        "acceptanceReasons": pullback_acceptance.acceptance_reasons,
        "acceptanceWarnings": pullback_acceptance.acceptance_warnings,
        "acceptanceNextSteps": pullback_acceptance.next_acceptance_steps,
        "volumePriceAcceptance": volume_price_acceptance.to_dict(),
        "volumePriceStatus": volume_price_acceptance.volume_price_status,
        "volumePriceScore": volume_price_acceptance.volume_price_score,
        "volumePriceReasonCn": volume_price_acceptance.acceptance_reason_cn,
        "actionFusion": action_fusion.to_dict(),
        "actionFusionCode": action_fusion.action_code,
        "actionFusionCn": action_fusion.action_cn,
        "actionFusionSetupType": action_fusion.setup_type,
        "actionFusionConfidence": action_fusion.confidence_level,
        **radar_entry_display,
    }


def derive_dashboard_buy_zone(ticker: str, snapshot: dict, technicals: dict, score):
    try:
        stock_data = {**snapshot, **technicals}
        price = _first_present(technicals.get("price"), snapshot.get("current_price"))
        if price is not None:
            stock_data["price"] = price
            stock_data["current_price"] = price
        return generate_buy_zone(ticker, stock_data, score, score.scoring_model)
    except Exception:
        return None


RADAR_ENTRY_DISPLAY_KEYS = (
    "buy_zone",
    "price_position",
    "decision",
    "data_status",
    "entry_reference_low",
    "entry_reference_high",
    "next_action_price",
    "chase_above_price",
    "current_vs_entry_pct",
    "missing_entry_fields",
    "technical_entry_zone_low",
    "technical_entry_zone_high",
    "effective_technical_entry_zone_low",
    "effective_technical_entry_zone_high",
    "technical_chase_overlap",
    "technical_entry_source",
    "technical_entry_reason",
    "technical_entry_missing_fields",
    "technical_entry_missing_reason",
    "technical_entry_confidence",
    "technical_structure_status",
    "technical_structure_label",
    "technical_pullback_zone_low",
    "technical_pullback_zone_high",
    "adaptive_pullback_zone_low",
    "adaptive_pullback_zone_high",
    "adaptive_pullback_label",
    "adaptive_pullback_type",
    "adaptive_pullback_confidence",
    "adaptive_pullback_reason",
    "adaptive_pullback_is_entry_signal",
    "technical_repair_zone_low",
    "technical_repair_zone_high",
    "near_term_repair_zone_low",
    "near_term_repair_zone_high",
    "trend_reclaim_zone_low",
    "trend_reclaim_zone_high",
    "valuation_reference_zone_low",
    "valuation_reference_zone_high",
    "deep_support_zone_low",
    "deep_support_zone_high",
    "zone_semantic_label",
    "primary_entry_interpretation",
    "support_watch_zone_low",
    "support_watch_zone_high",
    "confirmation_price",
    "invalidation_price",
    "technical_structure_reason",
    "technical_missing_fields",
    "next_technical_steps",
    "technical_position",
    "entry_context_status",
    "valuation_deep_zone_label",
    "nearest_support_price",
    "ema20",
    "ema50",
    "ema100",
    "ema200",
    "atr14",
    "recent_swing_low",
    "recent_breakout_level",
    "entry_display_label",
    "entry_display_reason",
    "entry_action_hint",
)


def _radar_entry_display_fields(ticker: str, snapshot: dict, technicals: dict) -> dict[str, Any]:
    try:
        row = build_ai_stock_radar_list_row(ticker, snapshot=snapshot, technicals=technicals)
    except Exception:
        row = build_entry_display(
            data_status="MISSING_BUY_ZONE",
            price_position="ZONE_MISSING",
            missing_entry_fields=["Radar 纪律买区缺失"],
        )
        row.update({"buy_zone": {}, "price_position": "ZONE_MISSING", "decision": "DATA_MISSING", "data_status": "MISSING_BUY_ZONE"})
    public_entry_keys = {
        "next_action_price",
        "chase_above_price",
        "current_vs_entry_pct",
        "missing_entry_fields",
        "technical_entry_zone_low",
        "technical_entry_zone_high",
        "effective_technical_entry_zone_low",
        "effective_technical_entry_zone_high",
        "technical_chase_overlap",
        "technical_entry_source",
        "technical_entry_reason",
        "technical_entry_missing_fields",
        "technical_entry_missing_reason",
        "technical_entry_confidence",
        "technical_structure_status",
        "technical_structure_label",
        "technical_pullback_zone_low",
        "technical_pullback_zone_high",
        "adaptive_pullback_zone_low",
        "adaptive_pullback_zone_high",
        "adaptive_pullback_label",
        "adaptive_pullback_type",
        "adaptive_pullback_confidence",
        "adaptive_pullback_reason",
        "adaptive_pullback_is_entry_signal",
        "technical_repair_zone_low",
        "technical_repair_zone_high",
        "near_term_repair_zone_low",
        "near_term_repair_zone_high",
        "trend_reclaim_zone_low",
        "trend_reclaim_zone_high",
        "valuation_reference_zone_low",
        "valuation_reference_zone_high",
        "deep_support_zone_low",
        "deep_support_zone_high",
        "zone_semantic_label",
        "primary_entry_interpretation",
        "support_watch_zone_low",
        "support_watch_zone_high",
        "confirmation_price",
        "invalidation_price",
        "technical_structure_reason",
        "technical_missing_fields",
        "next_technical_steps",
        "technical_position",
        "entry_context_status",
        "valuation_deep_zone_label",
        "nearest_support_price",
        "ema20",
        "ema50",
        "ema100",
        "ema200",
        "atr14",
        "recent_swing_low",
        "recent_breakout_level",
    }
    return {f"radar_{key}": row.get(key) for key in RADAR_ENTRY_DISPLAY_KEYS} | {
        key: row.get(key)
        for key in RADAR_ENTRY_DISPLAY_KEYS
        if key.startswith("entry_") or key in public_entry_keys
    }


def _safe_market_history(ticker: str):
    try:
        return build_market_history(ticker)
    except Exception:
        return None


def derive_dashboard_final_decision(ticker: str, snapshot: dict, technicals: dict, score, *, buy_zone: Any = None):
    try:
        if buy_zone is None:
            buy_zone = derive_dashboard_buy_zone(ticker, snapshot, technicals, score)
        plan = StockPlanStore().get_plan(ticker)
        return build_final_decision_bundle(score, buy_zone, manual_plan_override=plan, symbol=ticker)
    except Exception:
        return build_final_decision_bundle(score)


def error_dashboard_row(ticker: str, exc: Exception) -> dict:
    row = {
        "symbol": ticker,
        "companyName": "",
        "price": "N/A",
        "marketCap": "N/A",
        "drawdownFromHigh": "N/A",
        "qualityRating": "数据不足",
        "entryRating": "数据不足",
        "riskRating": "数据不足",
        "valuationStatus": "数据不足",
        "action": "数据不足，需复核",
        "finalAction": "数据不足，需复核",
        "decisionLane": "review",
        "displayCategory": "需复核",
        "buyZoneStatus": None,
        "combinedEntry": {},
        "isActionable": False,
        "decisionBlockReasons": ["data_unavailable"],
        "decisionReviewReasons": [],
        "scoreCurrentAddLimitPercent": 0,
        "scoreMaxPortfolioWeightPercent": 0,
        "maxSuggestedPositionPercent": 0,
        "maxSuggestedPosition": "不建议新增",
        "maxPortfolioWeightPercent": 0,
        "currentAddLimitPercent": 0,
        "maxPortfolioWeight": "不建议配置",
        "currentAddLimit": "不建议新增",
        "dataConfidence": "low",
        "proxyConfidence": "low",
        "dataStatus": "数据不足",
        "missingIndustryMetrics": [],
        "proxyMetricsUsed": [],
        "missingMetricImpact": [],
        "metricResolutionStatus": [],
        "humanReadableSummary": {},
        "missingDataExplanation": [],
        "ratingCap": None,
        "keyPositiveDrivers": [],
        "keyNegativeDrivers": [str(exc)],
        "totalScore": 0,
        "valueZone": "数据不可用",
        "rating": "需要数据",
        "antiFomo": False,
        "leftSideOpportunity": False,
        "riskFlagCount": 0,
        "highRiskFlagCount": 0,
        "dataQualityPct": 0,
        "dataNote": str(exc),
        "fcfMarginNote": "",
        "overheatScore": 0,
        "overheatStatus": "数据不足",
        "overheatAction": "数据不足，需复核",
        "overheatRecommendation": "先补齐数据",
        "overheatReasons": [str(exc)],
    }
    for _, metrics in DETAIL_GROUPS:
        for key, _ in metrics:
            row[key] = "N/A"
    return row


def _signal_message(signals, kind: str) -> str:
    for signal in signals:
        if signal.kind == kind:
            return signal.message
    return ""


def _format_billions(value: float | None) -> str:
    if value is None or _is_missing(value):
        return "N/A"
    try:
        return f"{float(value) / 1_000_000_000:,.1f}B"
    except (TypeError, ValueError):
        return "N/A"


def _format_plain_number(value: float | None, digits: int = 1) -> str:
    if _is_missing(value):
        return "N/A"
    return f"{float(value):,.{digits}f}"


def _data_status_label(score) -> str:
    if getattr(score, "data_insufficient", False):
        return "数据不足"
    confidence = getattr(score, "data_confidence", None)
    if confidence == "high":
        return "完整"
    if confidence == "medium":
        return "中等"
    if confidence == "low":
        return "低置信度"
    return "待复核"


def _position_limit_text(value: float | None) -> str:
    if value is None or _is_missing(value):
        return "N/A"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if number <= 0:
        return "不建议新增"
    return f"≤{number:g}%"


def _portfolio_weight_text(value: float | None) -> str:
    if value is None or _is_missing(value):
        return "N/A"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if number <= 0:
        return "不建议配置"
    if number >= 15:
        return "15%-20%"
    if number >= 10:
        return "10%-15%"
    if number >= 5:
        return "5%-10%"
    return f"≤{number:g}%"


def _data_note(snapshot: dict, data_quality: dict, score=None) -> str:
    notes = snapshot.get("data_quality_notes") or []
    messages = list(notes[:2])
    if snapshot.get("cache_note"):
        messages.append(str(snapshot["cache_note"]))
    if score is not None:
        messages.append(f"模型：{model_type_label(getattr(score, 'scoring_model', 'GENERIC'))}")
        if getattr(score, "fcf_margin_source_type", "") == "derivedFromMarket":
            messages.append("FCF margin 为推导值，不参与质量评分")
        if getattr(score, "missing_industry_metrics", None):
            missing_labels = "、".join(metric_label(item) for item in score.missing_industry_metrics[:4])
            messages.append(f"代理置信度：{confidence_label(score.proxy_confidence)}；缺行业 KPI：" + missing_labels)
    missing = data_quality.get("missing") or []
    if missing:
        messages.append("缺失：" + "、".join(missing[:4]))
    return "；".join(messages)


def _first_present(*values: object) -> float | None:
    for value in values:
        if not _is_missing(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value)
    except Exception:
        return False
