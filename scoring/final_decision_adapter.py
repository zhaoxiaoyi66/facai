from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from buy_zone_engine import BuyZoneEstimate, buy_zone_with_manual_override, has_buy_zone_override
from position_plan_engine import generate_position_plan
from scoring.final_decision import derive_final_decision


@dataclass(frozen=True)
class FinalDecisionBundle:
    executionSource: str
    finalAction: str
    decisionLane: str
    displayCategory: str
    isActionable: bool
    currentAddLimitPercent: float
    maxPortfolioWeightPercent: float
    blockReasons: list[str]
    reviewReasons: list[str]
    dataConfidence: str
    buyZoneStatus: str | None
    legacyAction: str
    scoreCurrentAddLimitPercent: float | None
    scoreMaxPortfolioWeightPercent: float | None
    positionPlanCurrentAddLimitPercent: float | None
    positionPlanMaxPortfolioWeightPercent: float | None
    setupScore: float | None = None
    buyZoneAction: str = ""
    buyZoneActionText: str = ""
    buyZonePrimaryZone: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def build_final_decision_bundle(
    score: Any,
    buy_zone: Any = None,
    position_plan: Any = None,
    manual_plan_override: dict | None = None,
    symbol: str | None = None,
    buy_zone_context: dict | None = None,
) -> FinalDecisionBundle:
    effective_buy_zone = _effective_buy_zone(buy_zone, manual_plan_override)
    decision_buy_zone = buy_zone_context or effective_buy_zone
    effective_position_plan = _effective_position_plan(
        score,
        effective_buy_zone,
        position_plan,
        manual_plan_override,
        symbol,
    )
    decision = derive_final_decision(score, decision_buy_zone, effective_position_plan)
    return FinalDecisionBundle(
        executionSource="finalDecisionBundle",
        finalAction=decision.finalAction,
        decisionLane=decision.decisionLane,
        displayCategory=decision.displayCategory,
        isActionable=decision.isActionable,
        currentAddLimitPercent=decision.currentAddLimitPercent,
        maxPortfolioWeightPercent=decision.maxPortfolioWeightPercent,
        blockReasons=decision.blockReasons,
        reviewReasons=decision.reviewReasons,
        dataConfidence=decision.dataConfidence,
        buyZoneStatus=_buy_zone_status(decision_buy_zone) or _buy_zone_status(effective_buy_zone),
        legacyAction=str(_value(score, "action", default="") or ""),
        scoreCurrentAddLimitPercent=_number(
            score,
            "current_add_limit_percent",
            "currentAddLimitPercent",
            "max_suggested_position_percent",
            "maxSuggestedPositionPercent",
        ),
        scoreMaxPortfolioWeightPercent=_number(
            score,
            "max_portfolio_weight_percent",
            "maxPortfolioWeightPercent",
        ),
        positionPlanCurrentAddLimitPercent=_number(
            effective_position_plan,
            "currentAddLimitPercent",
            "current_add_limit_percent",
        ),
        positionPlanMaxPortfolioWeightPercent=_number(
            effective_position_plan,
            "maxPortfolioWeightPercent",
            "max_portfolio_weight_percent",
        ),
        setupScore=decision.setupScore,
        buyZoneAction=decision.buyZoneAction,
        buyZoneActionText=decision.buyZoneActionText,
        buyZonePrimaryZone=decision.buyZonePrimaryZone,
    )


def _effective_buy_zone(buy_zone: Any, manual_plan_override: dict | None) -> Any:
    if isinstance(buy_zone, BuyZoneEstimate) and has_buy_zone_override(manual_plan_override):
        return buy_zone_with_manual_override(buy_zone, manual_plan_override)
    return buy_zone


def _effective_position_plan(
    score: Any,
    effective_buy_zone: Any,
    position_plan: Any,
    manual_plan_override: dict | None,
    symbol: str | None,
) -> Any:
    if not isinstance(effective_buy_zone, BuyZoneEstimate):
        return position_plan
    if position_plan is not None and not has_buy_zone_override(manual_plan_override):
        return position_plan
    resolved_symbol = symbol or effective_buy_zone.symbol
    return generate_position_plan(resolved_symbol, effective_buy_zone, score)


def _buy_zone_status(buy_zone: Any) -> str | None:
    value = _value(buy_zone, "currentZone", "current_zone", "primary_zone", "primaryZone", "current_action", "currentAction", default=None)
    return str(value) if value not in {None, ""} else None


def _number(source: Any, *names: str) -> float | None:
    value = _value(source, *names, default=None)
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _value(source: Any, *names: str, default: Any = None) -> Any:
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
