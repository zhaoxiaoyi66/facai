from __future__ import annotations

from dataclasses import dataclass
from typing import Any


BUY_ACTIONS = {"可小仓分批", "可正常分批"}
NON_BUY_VALUATION_STATUSES = {"只观察", "偏贵", "极贵"}
BLOCKING_ZONES = {"no_chase", "invalid_zone", "invalid_manual_override", "data_insufficient", "low_confidence_zone"}
REVIEW_ACTION = "待复核，暂不新增"


@dataclass(frozen=True)
class FinalDecision:
    finalAction: str
    decisionLane: str
    displayCategory: str
    isActionable: bool
    currentAddLimitPercent: float
    maxPortfolioWeightPercent: float
    blockReasons: list[str]
    reviewReasons: list[str]
    dataConfidence: str


def derive_final_decision(score: Any, buy_zone: Any = None, position_plan: Any = None) -> FinalDecision:
    action = str(_first_value(score, "action", default="只观察") or "只观察")
    valuation_status = str(_first_value(score, "valuation_status", "valuationStatus", default="") or "")
    entry_rating = str(_first_value(score, "entry_rating", "entryRating", default="") or "")
    risk_rating = str(_first_value(score, "risk_rating", "riskRating", default="") or "")
    data_confidence = str(_first_value(score, "data_confidence", "dataConfidence", default="high") or "high")
    current_add = _combined_current_add(score, position_plan)
    max_portfolio = _first_number(
        position_plan,
        "maxPortfolioWeightPercent",
        "max_portfolio_weight_percent",
        default=None,
    )
    if max_portfolio is None:
        max_portfolio = _first_number(score, "max_portfolio_weight_percent", "maxPortfolioWeightPercent", default=0.0)
    zone = str(_first_value(buy_zone, "currentZone", "current_zone", default="") or "")

    final_action = action
    block_reasons: list[str] = []
    review_reasons: list[str] = []

    if zone in BLOCKING_ZONES:
        block_reasons.append("buy_zone")
        final_action = "禁止追高" if zone == "no_chase" else REVIEW_ACTION

    if data_confidence.lower() == "low":
        block_reasons.append("data_confidence")
        final_action = REVIEW_ACTION

    if valuation_status in NON_BUY_VALUATION_STATUSES:
        block_reasons.append("valuation_status")
        final_action = "只观察" if valuation_status == "只观察" else "等回踩"

    if _is_c_or_d_entry(entry_rating):
        block_reasons.append("entry_rating")
        final_action = "只观察"

    if _is_medium_high_or_high_risk(risk_rating):
        review_reasons.append("risk_rating")
        if final_action in BUY_ACTIONS:
            final_action = "等回踩"

    has_blocking_reason = bool(block_reasons or review_reasons)
    if has_blocking_reason or final_action not in BUY_ACTIONS:
        current_add = 0.0

    is_actionable = final_action in BUY_ACTIONS and current_add > 0 and not has_blocking_reason
    decision_lane, display_category = _classify(final_action, is_actionable, block_reasons, review_reasons, zone)

    return FinalDecision(
        finalAction=final_action,
        decisionLane=decision_lane,
        displayCategory=display_category,
        isActionable=is_actionable,
        currentAddLimitPercent=round(current_add, 1),
        maxPortfolioWeightPercent=round(float(max_portfolio or 0.0), 1),
        blockReasons=block_reasons,
        reviewReasons=review_reasons,
        dataConfidence=data_confidence,
    )


def _combined_current_add(score: Any, position_plan: Any) -> float:
    score_add = _first_number(
        score,
        "current_add_limit_percent",
        "currentAddLimitPercent",
        "max_suggested_position_percent",
        "maxSuggestedPositionPercent",
        default=None,
    )
    plan_add = _first_number(position_plan, "currentAddLimitPercent", "current_add_limit_percent", default=None)
    if score_add is not None and plan_add is not None:
        return min(score_add, plan_add)
    if plan_add is not None:
        return plan_add
    if score_add is not None:
        return score_add
    return 0.0


def _first_value(source: Any, *names: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        for name in names:
            if name in source:
                return source[name]
        return default
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    return default


def _first_number(source: Any, *names: str, default: float | None = 0.0) -> float | None:
    value = _first_value(source, *names, default=default)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_c_or_d_entry(entry_rating: str) -> bool:
    grade = entry_rating.strip().upper()
    return grade.startswith("C") or grade.startswith("D")


def _is_medium_high_or_high_risk(risk_rating: str) -> bool:
    return "中高" in risk_rating or "高" in risk_rating


def _classify(
    final_action: str,
    is_actionable: bool,
    block_reasons: list[str],
    review_reasons: list[str],
    zone: str,
) -> tuple[str, str]:
    if is_actionable:
        return "actionable", "可执行"
    if "data_confidence" in block_reasons or zone in {"invalid_zone", "invalid_manual_override", "data_insufficient", "low_confidence_zone"}:
        return "review", "需复核"
    if final_action == "禁止追高" or zone == "no_chase":
        return "blocked", "禁止追高"
    if review_reasons:
        return "review", "需复核"
    if final_action == "等回踩" or "valuation_status" in block_reasons:
        return "wait", "等回踩"
    return "wait", "只观察"
