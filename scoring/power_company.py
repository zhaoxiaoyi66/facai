from __future__ import annotations

from dataclasses import dataclass

from scoring.risk_flags import RiskFlag
from scoring.sector_models import classifyStockModel


POWER_COMPANY_TICKERS = {"VST", "CEG", "TLN", "NRG", "DUK", "SO", "NEE"}
POWER_SECTOR_TERMS = {
    "utilities",
    "independent power producers",
    "power generation",
    "energy infrastructure",
}
POWER_INDUSTRY_TERMS = {
    "utilities",
    "independent power",
    "independent power producers",
    "power generation",
    "electric utilities",
    "electric utility",
    "regulated electric",
    "energy infrastructure",
}


@dataclass(frozen=True)
class PowerCompanyScore:
    quality_score: float
    growth_score: float
    valuation_score: float
    balance_sheet_risk_score: float
    catalyst_score: float
    value_zone: str
    risk_flags: list[RiskFlag]
    missing_data: list[str]


class PowerCompanyScoringModel:
    def score(self, snapshot: dict, technicals: dict) -> PowerCompanyScore:
        return calculate_power_company_score(snapshot, technicals)


def is_power_company(snapshot: dict) -> bool:
    return classifyStockModel(snapshot) in {"POWER_GENERATION", "REGULATED_UTILITIES"}


def calculate_power_company_score(snapshot: dict, technicals: dict) -> PowerCompanyScore:
    adjusted_ebitda = _first_number(
        snapshot.get("adjustedEbitda"),
        snapshot.get("adjusted_ebitda"),
        snapshot.get("manualAdjustedEbitda"),
        snapshot.get("manual_adjusted_ebitda"),
        snapshot.get("ebitda"),
    )
    adjusted_ebitda_growth = _first_number(
        snapshot.get("adjustedEbitdaGrowth"),
        snapshot.get("adjusted_ebitda_growth"),
    )
    adjusted_fcf_before_growth = _first_number(
        snapshot.get("adjustedFcfBeforeGrowth"),
        snapshot.get("adjusted_fcf_before_growth"),
        snapshot.get("manualAdjustedFcfBeforeGrowth"),
        snapshot.get("manual_adjusted_fcf_before_growth"),
        snapshot.get("free_cash_flow"),
    )
    market_cap = _first_number(snapshot.get("market_cap"), snapshot.get("marketCap"))
    enterprise_value = _first_number(snapshot.get("enterprise_value"), snapshot.get("enterpriseValue"))
    total_debt = _first_number(snapshot.get("total_debt"), snapshot.get("totalDebt"))
    total_cash = _first_number(snapshot.get("total_cash"), snapshot.get("totalCash"))
    net_debt = _first_number(snapshot.get("netDebt"), snapshot.get("net_debt"), _net_debt(total_debt, total_cash))
    net_debt_to_adjusted_ebitda = _first_number(
        snapshot.get("netDebtToAdjustedEbitda"),
        snapshot.get("net_debt_to_adjusted_ebitda"),
        snapshot.get("manualNetDebtToAdjustedEbitda"),
        snapshot.get("manual_net_debt_to_adjusted_ebitda"),
        snapshot.get("net_debt_to_ebitda"),
        _ratio(net_debt, adjusted_ebitda),
    )
    market_cap_to_adjusted_fcf = _ratio(market_cap, adjusted_fcf_before_growth)
    adjusted_fcf_yield = _ratio(adjusted_fcf_before_growth, market_cap)
    ev_to_adjusted_ebitda = _first_number(
        snapshot.get("enterpriseValueToAdjustedEbitda"),
        snapshot.get("enterprise_value_to_adjusted_ebitda"),
        snapshot.get("enterprise_to_ebitda"),
        _ratio(enterprise_value, adjusted_ebitda),
    )
    hedge_current = _first_number(
        snapshot.get("hedgeCoverageCurrentYear"),
        snapshot.get("hedge_coverage_current_year"),
        snapshot.get("manualHedgeCoverageCurrentYear"),
        snapshot.get("manual_hedge_coverage_current_year"),
        snapshot.get("manualHedgeCoverage"),
        snapshot.get("manual_hedge_coverage"),
    )
    hedge_next = _first_number(
        snapshot.get("hedgeCoverageNextYear"),
        snapshot.get("hedge_coverage_next_year"),
        snapshot.get("manualHedgeCoverageNextYear"),
        snapshot.get("manual_hedge_coverage_next_year"),
    )
    buyback_amount = _first_number(
        snapshot.get("buybackAmount"),
        snapshot.get("buyback_amount"),
        snapshot.get("manualBuybackAmount"),
        snapshot.get("manual_buyback_amount"),
    )
    share_count_reduction = _first_number(
        snapshot.get("shareCountReduction"),
        snapshot.get("share_count_reduction"),
        snapshot.get("manualShareCountReduction"),
        snapshot.get("manual_share_count_reduction"),
    )
    nuclear_exposure = _first_number(snapshot.get("nuclearCapacityExposure"), snapshot.get("nuclear_capacity_exposure"))
    data_center_exposure = _first_number(snapshot.get("dataCenterPowerDemandExposure"), snapshot.get("data_center_power_demand_exposure"))
    regulatory_risk = _risk_level(snapshot.get("regulatoryRisk"), snapshot.get("regulatory_risk"))
    commodity_exposure = _risk_level(snapshot.get("commodityPriceExposure"), snapshot.get("commodity_price_exposure"))
    current_ratio = _first_number(snapshot.get("current_ratio"), snapshot.get("currentRatio"))

    quality = _quality_score(
        adjusted_ebitda,
        adjusted_ebitda_growth,
        adjusted_fcf_before_growth,
        adjusted_fcf_yield,
        share_count_reduction,
        nuclear_exposure,
        data_center_exposure,
    )
    quality = _apply_quality_floor(
        quality,
        adjusted_ebitda,
        adjusted_fcf_before_growth,
        net_debt_to_adjusted_ebitda,
        current_ratio,
    )
    growth = _growth_score(adjusted_ebitda_growth, data_center_exposure, nuclear_exposure)
    valuation = _valuation_score(adjusted_fcf_yield, market_cap_to_adjusted_fcf, ev_to_adjusted_ebitda, technicals)
    balance_sheet = _balance_sheet_score(net_debt_to_adjusted_ebitda, current_ratio)
    catalyst = _catalyst_score(hedge_current, hedge_next, buyback_amount, data_center_exposure, regulatory_risk, commodity_exposure)
    flags = _risk_flags(
        adjusted_fcf_before_growth,
        net_debt_to_adjusted_ebitda,
        current_ratio,
        ev_to_adjusted_ebitda,
        adjusted_fcf_yield,
        regulatory_risk,
        commodity_exposure,
    )
    missing = _missing_power_fields(
        {
            "adjustedEbitda": adjusted_ebitda,
            "adjustedFcfBeforeGrowth": adjusted_fcf_before_growth,
            "marketCap / adjustedFcfBeforeGrowth": market_cap_to_adjusted_fcf,
            "enterpriseValue / adjustedEbitda": ev_to_adjusted_ebitda,
            "netDebt / adjustedEbitda": net_debt_to_adjusted_ebitda,
            "hedgeCoverageCurrentYear": hedge_current,
            "hedgeCoverageNextYear": hedge_next,
            "buybackAmount": buyback_amount,
            "shareCountReduction": share_count_reduction,
            "generationMix": snapshot.get("generationMix") or snapshot.get("generation_mix"),
            "nuclearCapacityExposure": nuclear_exposure,
            "dataCenterPowerDemandExposure": data_center_exposure,
            "regulatoryRisk": regulatory_risk,
            "commodityPriceExposure": commodity_exposure,
        }
    )

    return PowerCompanyScore(
        quality_score=quality,
        growth_score=growth,
        valuation_score=valuation,
        balance_sheet_risk_score=balance_sheet,
        catalyst_score=catalyst,
        value_zone=_value_zone(valuation, technicals),
        risk_flags=flags,
        missing_data=missing,
    )


def _quality_score(
    adjusted_ebitda: float | None,
    adjusted_ebitda_growth: float | None,
    adjusted_fcf_before_growth: float | None,
    adjusted_fcf_yield: float | None,
    share_count_reduction: float | None,
    nuclear_exposure: float | None,
    data_center_exposure: float | None,
) -> float:
    score = 6.0

    if adjusted_ebitda is not None and adjusted_ebitda > 0:
        score += 3
    if adjusted_ebitda_growth is not None:
        if adjusted_ebitda_growth >= 0.12:
            score += 4
        elif adjusted_ebitda_growth > 0:
            score += 2.5
        elif adjusted_ebitda_growth < -0.10:
            score -= 3
    else:
        score += 1.5

    if adjusted_fcf_before_growth is not None:
        if adjusted_fcf_before_growth > 0:
            score += 3
        else:
            score -= 6

    if adjusted_fcf_yield is not None:
        if adjusted_fcf_yield >= 0.08:
            score += 3
        elif adjusted_fcf_yield >= 0.05:
            score += 2
        elif adjusted_fcf_yield < 0:
            score -= 4

    if share_count_reduction is not None and share_count_reduction > 0:
        score += 1.5
    if nuclear_exposure is not None and nuclear_exposure > 0:
        score += 1
    if data_center_exposure is not None and data_center_exposure > 0:
        score += 1

    return round(max(0, min(score, 25)), 1)


def _apply_quality_floor(
    quality: float,
    adjusted_ebitda: float | None,
    adjusted_fcf_before_growth: float | None,
    net_debt_to_adjusted_ebitda: float | None,
    current_ratio: float | None,
) -> float:
    has_extreme_leverage = net_debt_to_adjusted_ebitda is not None and net_debt_to_adjusted_ebitda > 4
    has_negative_adjusted_fcf = adjusted_fcf_before_growth is not None and adjusted_fcf_before_growth < 0
    has_stressed_liquidity = current_ratio is not None and current_ratio < 0.8

    if has_extreme_leverage or has_negative_adjusted_fcf or has_stressed_liquidity:
        return quality

    if adjusted_ebitda is None or adjusted_fcf_before_growth is None:
        return max(quality, 16.0)
    return quality


def _growth_score(
    adjusted_ebitda_growth: float | None,
    data_center_exposure: float | None,
    nuclear_exposure: float | None,
) -> float:
    score = 8.0
    if adjusted_ebitda_growth is not None:
        if adjusted_ebitda_growth >= 0.15:
            score += 7
        elif adjusted_ebitda_growth > 0:
            score += 4
        elif adjusted_ebitda_growth < -0.10:
            score -= 3
    else:
        score += 2

    if data_center_exposure is not None and data_center_exposure > 0:
        score += 3
    if nuclear_exposure is not None and nuclear_exposure > 0:
        score += 2
    return round(max(0, min(score, 20)), 1)


def _valuation_score(
    adjusted_fcf_yield: float | None,
    market_cap_to_adjusted_fcf: float | None,
    ev_to_adjusted_ebitda: float | None,
    technicals: dict,
) -> float:
    score = 12.0
    if adjusted_fcf_yield is not None:
        if adjusted_fcf_yield >= 0.10:
            score += 7
        elif adjusted_fcf_yield >= 0.07:
            score += 5
        elif adjusted_fcf_yield >= 0.04:
            score += 2
        elif adjusted_fcf_yield < 0:
            score -= 8
    elif market_cap_to_adjusted_fcf is not None:
        if 0 < market_cap_to_adjusted_fcf <= 10:
            score += 6
        elif market_cap_to_adjusted_fcf <= 14:
            score += 3
        elif market_cap_to_adjusted_fcf > 25:
            score -= 6

    if ev_to_adjusted_ebitda is not None:
        if 0 < ev_to_adjusted_ebitda <= 8:
            score += 4
        elif ev_to_adjusted_ebitda <= 11:
            score += 2
        elif ev_to_adjusted_ebitda > 14:
            score -= 5

    drawdown = technicals.get("drawdown_from_high_pct")
    if drawdown is not None:
        if drawdown <= -25:
            score += 3
        elif drawdown <= -10:
            score += 1.5
        elif drawdown > -5:
            score -= 2

    return round(max(0, min(score, 25)), 1)


def _balance_sheet_score(net_debt_to_adjusted_ebitda: float | None, current_ratio: float | None) -> float:
    score = 8.0
    if net_debt_to_adjusted_ebitda is None:
        score -= 1
    elif net_debt_to_adjusted_ebitda > 4:
        score -= 5
    elif net_debt_to_adjusted_ebitda >= 3:
        score -= 2
    elif net_debt_to_adjusted_ebitda < 2.5:
        score += 1

    if current_ratio is not None and current_ratio < 1:
        score -= 1.5
    return round(max(0, min(score, 10)), 1)


def _catalyst_score(
    hedge_current: float | None,
    hedge_next: float | None,
    buyback_amount: float | None,
    data_center_exposure: float | None,
    regulatory_risk: str | None,
    commodity_exposure: str | None,
) -> float:
    score = 5.0
    for hedge in [hedge_current, hedge_next]:
        if hedge is not None:
            if hedge >= 0.70:
                score += 1.25
            elif hedge < 0.35:
                score -= 1
    if buyback_amount is not None and buyback_amount > 0:
        score += 1.5
    if data_center_exposure is not None and data_center_exposure > 0:
        score += 1.5
    if regulatory_risk == "high":
        score -= 1.5
    if commodity_exposure == "high":
        score -= 1
    return round(max(0, min(score, 10)), 1)


def _risk_flags(
    adjusted_fcf_before_growth: float | None,
    net_debt_to_adjusted_ebitda: float | None,
    current_ratio: float | None,
    ev_to_adjusted_ebitda: float | None,
    adjusted_fcf_yield: float | None,
    regulatory_risk: str | None,
    commodity_exposure: str | None,
) -> list[RiskFlag]:
    flags: list[RiskFlag] = []

    if adjusted_fcf_before_growth is not None and adjusted_fcf_before_growth < 0:
        flags.append(RiskFlag("调整后 FCF 为负", "high", "调整后 growth capex 前自由现金流为负。"))

    if net_debt_to_adjusted_ebitda is not None:
        if net_debt_to_adjusted_ebitda > 4:
            flags.append(RiskFlag("杠杆高于电力模型阈值", "high", "净债务/调整后 EBITDA 高于 4.0x。"))
        elif net_debt_to_adjusted_ebitda >= 3:
            flags.append(RiskFlag("杠杆中高", "medium", "净债务/调整后 EBITDA 在 3.0x 到 4.0x 区间。"))

    if current_ratio is not None and current_ratio < 0.8:
        flags.append(RiskFlag("流动性偏紧", "high", "流动比率低于 0.8，需要复核短期偿债压力。"))
    elif current_ratio is not None and current_ratio < 1:
        flags.append(RiskFlag("短期偿债压力", "medium", "流动比率低于 1。"))

    if ev_to_adjusted_ebitda is not None and ev_to_adjusted_ebitda > 16:
        flags.append(RiskFlag("电力估值极端", "high", "EV/调整后 EBITDA 高于 16x。"))
    elif adjusted_fcf_yield is not None and 0 <= adjusted_fcf_yield < 0.025:
        flags.append(RiskFlag("FCF 收益率过低", "high", "调整后 FCF 收益率低于 2.5%。"))

    if regulatory_risk == "high":
        flags.append(RiskFlag("监管风险高", "medium", "需要跟踪州监管、容量市场或价格上限变化。"))
    if commodity_exposure == "high":
        flags.append(RiskFlag("商品电价暴露高", "medium", "未对冲电价暴露较高，盈利对电价周期敏感。"))

    return flags


def _value_zone(valuation_score: float, technicals: dict) -> str:
    drawdown = technicals.get("drawdown_from_high_pct")
    if valuation_score >= 18 and drawdown is not None and drawdown <= -10:
        return "回撤后有吸引力"
    if valuation_score >= 15 and drawdown is not None and drawdown <= -25:
        return "回撤后有吸引力"
    if valuation_score >= 15:
        return "合理偏便宜"
    if valuation_score >= 11:
        return "只观察"
    return "偏贵"


def _missing_power_fields(values: dict) -> list[str]:
    return [key for key, value in values.items() if value is None]


def _normalize(value: object) -> str:
    return str(value or "").strip().lower()


def _risk_level(*values: object) -> str | None:
    for value in values:
        text = _normalize(value)
        if not text:
            continue
        if text in {"high", "medium", "low"}:
            return text
        if text in {"高", "高风险"}:
            return "high"
        if text in {"中", "中等", "中高"}:
            return "medium"
        if text in {"低", "低风险"}:
            return "low"
    return None


def _first_number(*values: object) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number:
            continue
        return number
    return None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator


def _net_debt(total_debt: float | None, total_cash: float | None) -> float | None:
    if total_debt is None:
        return None
    return total_debt - (total_cash or 0)
