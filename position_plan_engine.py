from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from buy_zone_engine import BuyZoneEstimate


@dataclass(frozen=True)
class PositionPlanSuggestion:
    symbol: str
    currentAddLimitPercent: float
    maxPortfolioWeightPercent: float
    firstBuyPrice: float | None
    firstBuyLabel: str
    secondBuyPrice: float | None
    thirdBuyPrice: float | None
    noChaseAbove: float | None
    heavyBuyBelow: float | None
    stopAddingCondition: str
    thesisBreakCondition: str
    earningsReviewCondition: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_position_plan(symbol: str, buyZone: BuyZoneEstimate, scoringResult=None) -> PositionPlanSuggestion:
    quality = _score_attr(scoringResult, "quality_rating") or _score_attr(scoringResult, "qualityRating") or ""
    risk = _score_attr(scoringResult, "risk_rating") or _score_attr(scoringResult, "riskRating") or ""
    entry = _score_attr(scoringResult, "entry_rating") or _score_attr(scoringResult, "entryRating") or ""
    action = _score_attr(scoringResult, "action") or ""
    data_confidence = _score_attr(scoringResult, "data_confidence") or _score_attr(scoringResult, "dataConfidence") or ""
    max_portfolio = _max_portfolio_weight(quality, risk)
    current_add = _current_add_limit(entry, risk, buyZone.currentZone, action, data_confidence)

    tranche_low = buyZone.trancheBuyLow
    tranche_high = buyZone.trancheBuyHigh
    first_buy, first_buy_label = _first_buy_trigger(buyZone)
    second = None
    if tranche_low is not None and tranche_high is not None:
        second = round((tranche_low + tranche_high) / 2, 2)

    return PositionPlanSuggestion(
        symbol=symbol.upper(),
        currentAddLimitPercent=current_add,
        maxPortfolioWeightPercent=max_portfolio,
        firstBuyPrice=first_buy,
        firstBuyLabel=first_buy_label,
        secondBuyPrice=second,
        thirdBuyPrice=buyZone.heavyBuyBelow,
        noChaseAbove=buyZone.noChaseAbove,
        heavyBuyBelow=buyZone.heavyBuyBelow,
        stopAddingCondition=_stop_adding_condition(buyZone, action),
        thesisBreakCondition=_thesis_break_condition(risk),
        earningsReviewCondition=_earnings_review_condition(buyZone.modelType),
    )


def _max_portfolio_weight(quality: str, risk: str) -> float:
    quality_rank = _quality_rank(quality)
    if _is_high_risk(risk):
        return 5.0
    if "中高" in risk:
        return 10.0 if quality_rank >= 3 else 5.0
    if "中" in risk:
        return 15.0 if quality_rank >= 4 else 10.0
    if "低" in risk:
        return 20.0 if quality_rank >= 4 else 15.0 if quality_rank >= 3 else 8.0
    return 8.0


def _current_add_limit(entry: str, risk: str, zone: str, action: str, data_confidence: str = "") -> float:
    if str(data_confidence).lower() == "low":
        return 0.0
    if _is_high_risk(risk) or zone in {"invalid_zone", "invalid_manual_override", "data_insufficient", "low_confidence_zone", "unsupported_buy_zone_model"}:
        return 0.0
    if _is_high_risk(risk) or zone == "no_chase" or "禁止追高" in action:
        return 0.0
    if action not in {"可小仓分批", "可正常分批"}:
        return 0.0
    if zone in {"data_insufficient", "fair_observation"}:
        return 3.0
    if zone == "tranche_buy":
        return 5.0
    if zone in {"heavy_buy", "below_heavy_buy"}:
        return 8.0 if "中高" not in risk else 5.0
    if entry.startswith("A"):
        return 5.0
    if entry.startswith("B"):
        return 3.0
    return 0.0


def _first_buy_trigger(buyZone: BuyZoneEstimate) -> tuple[float | None, str]:
    zone = buyZone.currentZone
    current_price = buyZone.currentPrice
    if zone in {"invalid_zone", "invalid_manual_override", "data_insufficient", "low_confidence_zone", "unsupported_buy_zone_model"}:
        return None, "买区异常，需复核"
    if zone == "tranche_buy":
        return current_price, "已进入可分批区"
    if zone == "heavy_buy":
        return None, "已低于重仓区"
    if zone == "fair_observation":
        trigger = buyZone.trancheBuyHigh
        if current_price is not None and trigger is not None and trigger > current_price:
            return None, "已进入买区"
        return trigger, "下一买入触发价"
    if zone == "no_chase":
        return buyZone.fairValueHigh or buyZone.fairValueLow, "等回踩"
    return None, "买区异常，需复核"


def _quality_rank(quality: str) -> int:
    text = str(quality).upper()
    if text.startswith("A+"):
        return 5
    if text.startswith("A"):
        return 4
    if text.startswith("B+"):
        return 3
    if text.startswith("B"):
        return 2
    if text.startswith("C"):
        return 1
    return 0


def _is_high_risk(risk: str) -> bool:
    text = str(risk).lower()
    if ("高" in text or "high" in text) and "中高" not in text and "medium high" not in text:
        return True
    return "高" in str(risk) and "中高" not in str(risk)


def _stop_adding_condition(buyZone: BuyZoneEstimate, action: str) -> str:
    if buyZone.noChaseAbove is None:
        return "关键估值输入缺失时暂停新增，等待数据复核。"
    if buyZone.currentZone == "no_chase" or "禁止追高" in str(action):
        return f"价格高于 {buyZone.noChaseAbove:.2f} 或短线过热时停止新增。"
    return f"价格重新高于禁止追高线 {buyZone.noChaseAbove:.2f}，或买点评级降至 C/D 时停止新增。"


def _thesis_break_condition(risk: str) -> str:
    if "中高" in str(risk) or _is_high_risk(str(risk)):
        return "现金流转负、杠杆恶化、关键行业 KPI 明显低于预期，或风险评级升至高风险。"
    return "收入/FCF趋势连续恶化、估值逻辑失效，或风险评级上调两个档位。"


def _earnings_review_condition(model_type: str) -> str:
    model = str(model_type).upper()
    if model == "SAAS_SOFTWARE":
        return "财报后复核收入增速、FCF利润率、RPO/cRPO、SBC/收入和净留存。"
    if model == "MEGA_CAP_PLATFORM":
        return "财报后复核云业务增长、AI资本开支回报、FCF转化率和回购纪律。"
    if model == "SEMICONDUCTOR":
        return "财报后复核收入指引、毛利率、库存周期、客户集中和出口管制影响。"
    if model == "POWER_GENERATION":
        return "财报后复核调整后EBITDA、增长投资前FCF、杠杆、对冲覆盖和电价敞口。"
    return "财报后复核收入、利润率、FCF、负债和下一季指引。"


def _score_attr(score, name: str):
    if score is None:
        return None
    if isinstance(score, dict):
        return score.get(name)
    return getattr(score, name, None)
