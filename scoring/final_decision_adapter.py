from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from buy_zone_engine import BuyZoneEstimate, buy_zone_with_manual_override, has_buy_zone_override
from position_plan_engine import generate_position_plan
from scoring.final_decision import derive_final_decision


@dataclass(frozen=True)
class FinalDecisionBundle:
    finalAction: str
    decisionLane: str
    displayCategory: str
    isActionable: bool
    currentAddLimitPercent: float
    maxPortfolioWeightPercent: float
    blockReasons: list[str]
    reviewReasons: list[str]
    dataConfidence: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def build_final_decision_bundle(
    score: Any,
    buy_zone: Any = None,
    position_plan: Any = None,
    manual_plan_override: dict | None = None,
    symbol: str | None = None,
) -> FinalDecisionBundle:
    effective_buy_zone = _effective_buy_zone(buy_zone, manual_plan_override)
    effective_position_plan = _effective_position_plan(
        score,
        effective_buy_zone,
        position_plan,
        manual_plan_override,
        symbol,
    )
    decision = derive_final_decision(score, effective_buy_zone, effective_position_plan)
    return FinalDecisionBundle(
        finalAction=decision.finalAction,
        decisionLane=decision.decisionLane,
        displayCategory=decision.displayCategory,
        isActionable=decision.isActionable,
        currentAddLimitPercent=decision.currentAddLimitPercent,
        maxPortfolioWeightPercent=decision.maxPortfolioWeightPercent,
        blockReasons=decision.blockReasons,
        reviewReasons=decision.reviewReasons,
        dataConfidence=decision.dataConfidence,
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
