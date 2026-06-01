from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from statistics import median
from typing import Any

from data.technical_entry_model import build_technical_entry_model


BUY_ZONE_OVERRIDE_FIELDS = (
    "no_chase_above",
    "fair_value_low",
    "fair_value_high",
    "tranche_buy_low",
    "tranche_buy_high",
    "heavy_buy_below",
)

SUPPORTED_BUY_ZONE_MODELS = {
    "SAAS_SOFTWARE",
    "MEGA_CAP_PLATFORM",
    "SEMICONDUCTOR",
    "POWER_GENERATION",
    "NETWORKING_HARDWARE",
    "CRYPTO_FINANCIAL_INFRA",
    "BROKERAGE_FINTECH",
    "AI_CLOUD_INFRA",
}

BLOCKED_BUY_ZONE_STATES = {
    "invalid_zone",
    "invalid_manual_override",
    "data_insufficient",
    "low_confidence_zone",
    "unsupported_buy_zone_model",
}

CASHFLOW_SIGNAL_KEYS = {"price_to_fcf", "fcf_yield"}
GROWTH_MARGIN_ANCHOR_LIMITS = {
    "SAAS_SOFTWARE": (0.90, 1.15),
    "MEGA_CAP_PLATFORM": (0.92, 1.15),
    "SEMICONDUCTOR": (0.85, 1.22),
    "NETWORKING_HARDWARE": (0.92, 1.10),
}
SAAS_SALES_ANCHOR_CAP = 1.10


@dataclass(frozen=True)
class BuyZoneEstimate:
    symbol: str
    modelType: str
    currentPrice: float | None
    noChaseAbove: float | None
    fairValueLow: float | None
    fairValueHigh: float | None
    trancheBuyLow: float | None
    trancheBuyHigh: float | None
    heavyBuyBelow: float | None
    currentZone: str
    confidence: str
    method: str
    inputsUsed: list[str]
    keyReasons: list[str]
    warnings: list[str]
    createdAt: str
    action: str = ""
    nextTriggerPrice: float | None = None
    nextBuyLabel: str = ""
    isValid: bool = True
    validationErrors: list[str] | None = None
    explainability: dict[str, Any] | None = None
    technicalEntry: dict[str, Any] | None = None
    combinedEntry: dict[str, Any] | None = None
    precisionContract: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_plan_fields(self) -> dict[str, float | None]:
        return {
            "no_chase_above": self.noChaseAbove,
            "fair_value_low": self.fairValueLow,
            "fair_value_high": self.fairValueHigh,
            "tranche_buy_low": self.trancheBuyLow,
            "tranche_buy_high": self.trancheBuyHigh,
            "heavy_buy_below": self.heavyBuyBelow,
        }


def generate_buy_zone(symbol: str, stockData: dict, scoringResult=None, modelType: str | None = None) -> BuyZoneEstimate:
    raw_model = modelType or _score_attr(scoringResult, "scoring_model") or _score_attr(scoringResult, "modelType") or stockData.get("modelType") or "GENERIC"
    model = _buy_zone_model_for_symbol(symbol, raw_model)
    stockData = _with_brokerage_fintech_review_candidates(symbol, model, stockData)
    price = _first_number(stockData, "current_price", "currentPrice", "price")
    inputs: list[str] = []
    reasons: list[str] = []
    warnings: list[str] = []
    method = "blended"

    metrics = _collect_metrics(stockData, warnings)
    if price is None or price <= 0:
        warnings.append("当前价格缺失或无效")
        return _insufficient(symbol, model, price, ["缺少当前价格，无法生成价格区间。"], warnings)

    model_key = str(model).upper()
    if model_key not in SUPPORTED_BUY_ZONE_MODELS or not _buy_zone_model_supported_for_symbol(symbol, model_key):
        return _blocked_estimate(
            symbol,
            str(model),
            price,
            "unsupported_buy_zone_model",
            ["buy_zone_model_not_supported"],
            ["当前板块暂无专属买区模型，禁用精确买点"],
            inputs,
            method="unsupported_buy_zone_model",
        )

    if _power_generation_core_inputs_missing(model_key, stockData):
        return _blocked_estimate(
            symbol,
            str(model),
            price,
            "data_insufficient",
            ["missing_power_generation_core_inputs"],
            ["missing_power_generation_core_inputs"],
            inputs,
            method="data_insufficient",
        )

    if _networking_hardware_core_inputs_missing(model_key, metrics, stockData):
        return _blocked_estimate(
            symbol,
            str(model),
            price,
            "data_insufficient",
            ["missing_networking_hardware_growth_or_margin"],
            ["missing_networking_hardware_growth_or_margin"],
            inputs,
            method="data_insufficient",
        )

    if _ai_cloud_infra_core_inputs_missing(model_key, metrics, stockData):
        return _blocked_estimate(
            symbol,
            str(model),
            price,
            "data_insufficient",
            ["data_insufficient"],
            ["data_insufficient"],
            inputs,
            method="data_insufficient",
        )

    if _crypto_financial_infra_core_anchor_missing(model_key, symbol, metrics):
        return _blocked_estimate(
            symbol,
            str(model),
            price,
            "data_insufficient",
            ["missing_crypto_ev_sales_anchor"],
            ["missing_crypto_ev_sales_anchor"],
            inputs,
            method="data_insufficient",
        )

    brokerage_missing = _brokerage_fintech_core_inputs_missing(model_key, symbol, metrics, stockData)
    if brokerage_missing:
        return _blocked_estimate(
            symbol,
            str(model),
            price,
            "data_insufficient",
            ["missing_brokerage_fintech_core_inputs", *brokerage_missing],
            ["missing_brokerage_fintech_core_inputs", *brokerage_missing],
            inputs,
            method="data_insufficient",
        )

    targets = _apply_growth_margin_anchor(str(model), _model_targets(str(model)), metrics, stockData)
    candidates = _valuation_candidates(price, metrics, targets, inputs)
    if not candidates:
        candidates = _technical_candidates(price, metrics, inputs)
        method = "technical_proxy"

    if not candidates:
        return _insufficient(symbol, model, price, ["估值和技术输入都不足，暂时只能等待数据补齐。"], warnings)

    fair_price = _weighted_average(candidates["fair"])
    tranche_price = _weighted_average(candidates["tranche"])
    heavy_price = _weighted_average(candidates["heavy"])
    if fair_price is None or tranche_price is None or heavy_price is None:
        return _insufficient(symbol, model, price, ["可用估值输入不足，无法合成区间。"], warnings)

    fair_price, tranche_price, heavy_price = _make_monotonic(price, fair_price, tranche_price, heavy_price)
    no_chase_above = _round_price(max(fair_price * 1.08, price * 1.04 if _is_overheated(metrics) else fair_price * 1.06))
    fair_value_high = _round_price(fair_price * 1.04)
    fair_value_low = _round_price(max(tranche_price * 1.04, heavy_price * 1.12))
    tranche_buy_high = _round_price(fair_value_low * 0.98)
    tranche_buy_low = _round_price(max(heavy_price * 1.06, heavy_price + (tranche_buy_high - heavy_price) * 0.25))
    heavy_buy_below = _round_price(heavy_price)

    if tranche_buy_low >= tranche_buy_high:
        tranche_buy_low = _round_price(max(heavy_buy_below, tranche_buy_high * 0.92))
    if fair_value_low <= tranche_buy_high:
        fair_value_low = _round_price(tranche_buy_high * 1.03)
    if fair_value_low >= fair_value_high:
        fair_value_low = _round_price(fair_value_high * 0.9)
        tranche_buy_high = _round_price(fair_value_low * 0.96)
        tranche_buy_low = _round_price(min(tranche_buy_low, tranche_buy_high * 0.92))

    current_zone = derive_current_zone(
        price,
        {
            "noChaseAbove": no_chase_above,
            "fairValueLow": fair_value_low,
            "fairValueHigh": fair_value_high,
            "trancheBuyLow": tranche_buy_low,
            "trancheBuyHigh": tranche_buy_high,
            "heavyBuyBelow": heavy_buy_below,
        },
    )
    if _networking_hardware_sales_multiple_overextended(model_key, metrics):
        current_zone = "no_chase"
        _append_once(warnings, "networking_hardware_sales_multiple_overextended")
    if _crypto_financial_infra_beta_sales_overextended(model_key, symbol, metrics, stockData):
        current_zone = "no_chase"
        _append_once(warnings, "crypto_financial_infra_high_beta_sales_multiple")
    if _crypto_financial_infra_regulatory_risk_high(model_key, symbol, stockData, scoringResult):
        current_zone = "no_chase"
        _append_once(warnings, "crypto_financial_infra_regulatory_risk_high")
    if _brokerage_fintech_beta_sales_overextended(model_key, symbol, metrics, stockData):
        current_zone = "no_chase"
        _append_once(warnings, "brokerage_fintech_high_beta_sales_multiple")
    if _ai_cloud_infra_overextended(model_key, metrics, stockData):
        current_zone = "no_chase"
        _append_once(warnings, "ai_cloud_infra_high_ev_sales_capex_debt")
    _append_brokerage_fintech_operating_inputs(model_key, symbol, stockData, inputs, warnings)
    _append_ai_cloud_infra_operating_inputs(model_key, stockData, inputs, warnings)
    confidence = _confidence(inputs, method, model, stockData, warnings)
    method = "fcf_yield" if any("FCF收益率" in item for item in inputs) and len(inputs) == 1 else method
    if len(inputs) >= 2 and method != "technical_proxy":
        method = "blended"

    reasons.extend(_reason_texts(model, metrics, current_zone, inputs, confidence))
    estimate = BuyZoneEstimate(
        symbol=symbol.upper(),
        modelType=str(model),
        currentPrice=_round_price(price),
        noChaseAbove=no_chase_above,
        fairValueLow=fair_value_low,
        fairValueHigh=fair_value_high,
        trancheBuyLow=tranche_buy_low,
        trancheBuyHigh=tranche_buy_high,
        heavyBuyBelow=heavy_buy_below,
        currentZone=current_zone,
        confidence=confidence,
        method=method,
        inputsUsed=inputs,
        keyReasons=reasons,
        warnings=warnings,
        createdAt=datetime.now(timezone.utc).isoformat(),
    )
    validated = validate_buy_zone_estimate(estimate, stockData, scoringResult)
    finalized = _finalize_brokerage_fintech_estimate(validated, stockData, scoringResult)
    finalized = _finalize_ai_cloud_infra_estimate(finalized, stockData, scoringResult)
    return attach_technical_entry(finalized, stockData=stockData)


def normalize_percent_metric(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if -1 <= number <= 1:
        return number
    if -100 <= number <= 100:
        return number / 100
    return None


def direct_fcf_margin(stockData: dict) -> tuple[float | None, str, str | None]:
    fcf = _first_number(stockData, "free_cash_flow", "freeCashFlow")
    revenue = _first_number(stockData, "total_revenue", "revenue", "totalRevenue")
    if fcf is not None and revenue not in {None, 0}:
        return fcf / revenue, "calculated", "free_cash_flow / revenue"

    source = _metric_source(stockData, "fcf_margin") or _metric_source(stockData, "direct_fcf_margin")
    direct = _first_number(stockData, "direct_fcf_margin", "directFcfMargin", "fcf_margin", "free_cash_flow_margin")
    if direct is not None:
        if source in {"SEC_8K", "SEC_10Q", "SEC_10K", "IR_RELEASE", "IR_PRESENTATION", "FMP_TRANSCRIPT"}:
            return None, "needs_review", "文本来源没有明确 free cash flow margin，不参与买区引擎。"
        return _ratio_like(direct), source or "reported", "reported_or_structured_fcf_margin"

    return None, "missing", "缺少自由现金流和收入。"


def implied_fcf_margin(stockData: dict) -> float | None:
    fcf_yield = _ratio_like(_first_number(stockData, "free_cash_flow_yield", "freeCashFlowYield", "fcf_yield"))
    ps = _first_number(stockData, "price_to_sales", "priceToSales", "psRatio")
    if fcf_yield is None or ps is None:
        return None
    return fcf_yield * ps


def has_buy_zone_override(plan: dict | None) -> bool:
    if not plan:
        return False
    return any(_number(plan.get(field)) is not None for field in BUY_ZONE_OVERRIDE_FIELDS)


def clear_buy_zone_override_values(plan: dict | None) -> dict:
    cleaned = dict(plan or {})
    for field in BUY_ZONE_OVERRIDE_FIELDS:
        cleaned[field] = None
    return cleaned


def effective_buy_zone_plan(plan: dict | None, estimate: BuyZoneEstimate) -> dict:
    effective = dict(plan or {})
    if has_buy_zone_override(plan):
        return effective
    effective.update(estimate.to_plan_fields())
    return effective


def attach_technical_entry(
    estimate: BuyZoneEstimate,
    price_history: Any = None,
    finalDecision: Any = None,
    *,
    stockData: dict | None = None,
) -> BuyZoneEstimate:
    stockData = stockData or {}
    history = price_history if price_history is not None else _price_history_from_stock_data(stockData)
    if _history_is_stale(stockData):
        technical_entry = build_technical_entry_model(estimate.symbol, estimate.currentPrice, None, estimate, finalDecision).to_dict()
        technical_entry["technicalState"] = "unavailable"
        technical_entry["technicalTrend"] = "unavailable"
        technical_entry["technicalConfidence"] = "low"
        technical_entry["technicalReasons"] = ["本地 price_history 已过期，技术入场模型暂不生成触发价。"]
        return attach_combined_entry(replace(estimate, technicalEntry=technical_entry), finalDecision)
    technical_entry = build_technical_entry_model(estimate.symbol, estimate.currentPrice, history, estimate, finalDecision).to_dict()
    return attach_combined_entry(replace(estimate, technicalEntry=technical_entry), finalDecision)


def attach_combined_entry(estimate: BuyZoneEstimate, finalDecision: Any = None) -> BuyZoneEstimate:
    return replace(estimate, combinedEntry=_build_combined_entry(estimate, finalDecision))


def buy_zone_with_manual_override(estimate: BuyZoneEstimate, plan: dict | None) -> BuyZoneEstimate:
    if not has_buy_zone_override(plan):
        return estimate
    plan = plan or {}
    overridden = BuyZoneEstimate(
        symbol=estimate.symbol,
        modelType=estimate.modelType,
        currentPrice=estimate.currentPrice,
        noChaseAbove=_override_number(plan, "no_chase_above", estimate.noChaseAbove),
        fairValueLow=_override_number(plan, "fair_value_low", estimate.fairValueLow),
        fairValueHigh=_override_number(plan, "fair_value_high", estimate.fairValueHigh),
        trancheBuyLow=_override_number(plan, "tranche_buy_low", estimate.trancheBuyLow),
        trancheBuyHigh=_override_number(plan, "tranche_buy_high", estimate.trancheBuyHigh),
        heavyBuyBelow=_override_number(plan, "heavy_buy_below", estimate.heavyBuyBelow),
        currentZone=estimate.currentZone,
        confidence=estimate.confidence,
        method="manual_override",
        inputsUsed=estimate.inputsUsed,
        keyReasons=["当前使用手动买区，系统建议仍保留供对比。", *estimate.keyReasons],
        warnings=estimate.warnings,
        createdAt=estimate.createdAt,
        technicalEntry=estimate.technicalEntry,
    )
    return attach_combined_entry(validate_buy_zone_estimate(overridden, {}, None))


def _collect_metrics(stockData: dict, warnings: list[str]) -> dict[str, float | None]:
    direct_margin, source, note = direct_fcf_margin(stockData)
    if source == "needs_review" and note:
        warnings.append("FCF利润率来自文本抽取且需复核，未用于买区估值。")
    drawdown = normalize_percent_metric(_first_number(stockData, "drawdownFrom52WeekHigh", "drawdown_from_high_pct", "drawdownFromHigh"))
    if drawdown is None and _first_number(stockData, "drawdownFrom52WeekHigh", "drawdown_from_high_pct", "drawdownFromHigh") is not None:
        warnings.append("距高点回撤百分比异常，已排除。")
    return {
        "price_to_fcf": _first_number(stockData, "price_to_fcf", "priceToFcf", "priceToFCF", "pfcf"),
        "ev_to_fcf": _first_number(stockData, "ev_to_fcf", "enterprise_to_fcf", "enterpriseValueToFcf"),
        "fcf_yield": _ratio_like(_first_number(stockData, "free_cash_flow_yield", "freeCashFlowYield", "fcfYield")),
        "price_to_sales": _first_number(stockData, "price_to_sales", "priceToSales", "psRatio"),
        "ev_to_sales": _first_number(stockData, "enterprise_to_revenue", "enterpriseToRevenue", "evToSales"),
        "forward_pe": _first_number(stockData, "forward_pe", "forwardPE"),
        "ev_to_ebitda": _first_number(stockData, "enterprise_to_ebitda", "enterpriseValueToEbitda", "evToEbitda"),
        "market_cap_to_fcf": _market_cap_to_fcf(stockData),
        "forward_revenue_growth": _ratio_like(_first_number(stockData, "forward_revenue_growth", "forwardRevenueGrowth")),
        "revenue_growth": _ratio_like(_first_number(stockData, "revenue_growth", "revenueGrowth")),
        "earnings_growth": _ratio_like(_first_number(stockData, "earnings_growth", "earningsGrowth", "eps_growth", "epsGrowth")),
        "forward_eps_growth": _ratio_like(_first_number(stockData, "forward_eps_growth_estimate", "forwardEpsGrowthEstimate", "forward_eps_growth", "forwardEpsGrowth")),
        "fcf_margin": direct_margin,
        "implied_fcf_margin": implied_fcf_margin(stockData),
        "gross_margin": _ratio_like(_first_number(stockData, "gross_margin", "grossMargin")),
        "operating_margin": _ratio_like(_first_number(stockData, "operating_margin", "operatingMargin")),
        "drawdown": drawdown,
        "rsi14": _first_number(stockData, "rsi14", "RSI14"),
        "return20d": _ratio_like(_first_number(stockData, "gain_20d_pct", "return20d", "return20D")),
        "return60d": _ratio_like(_first_number(stockData, "gain_60d_pct", "return60d", "return60D")),
        "net_debt_to_ebitda": _first_number(stockData, "net_debt_to_ebitda", "netDebtToEbitda"),
        "beta": _first_number(stockData, "beta", "marketBeta"),
        "ev_to_rpo": _ai_cloud_infra_ev_to_demand(stockData, "rpo"),
        "ev_to_backlog": _ai_cloud_infra_ev_to_demand(stockData, "backlog"),
        "capex_intensity": _ai_cloud_infra_capex_intensity(stockData),
        "debt_to_revenue": _ai_cloud_infra_debt_to_revenue(stockData),
    }


def _model_targets(model: str) -> dict[str, dict[str, float]]:
    model = model.upper()
    if model == "SAAS_SOFTWARE":
        return {
            "price_to_fcf": {"fair": 30, "tranche": 22, "heavy": 16, "weight": 3},
            "ev_to_fcf": {"fair": 32, "tranche": 24, "heavy": 18, "weight": 2},
            "fcf_yield": {"fair": 0.035, "tranche": 0.0475, "heavy": 0.06, "weight": 3},
            "price_to_sales": {"fair": 10, "tranche": 7, "heavy": 5, "weight": 1.5},
            "ev_to_sales": {"fair": 10, "tranche": 7, "heavy": 5, "weight": 1},
        }
    if model == "MEGA_CAP_PLATFORM":
        return {
            "price_to_fcf": {"fair": 30, "tranche": 24, "heavy": 18, "weight": 3},
            "ev_to_fcf": {"fair": 32, "tranche": 25, "heavy": 19, "weight": 2},
            "fcf_yield": {"fair": 0.033, "tranche": 0.042, "heavy": 0.055, "weight": 3},
            "forward_pe": {"fair": 32, "tranche": 26, "heavy": 20, "weight": 1.5},
            "price_to_sales": {"fair": 11, "tranche": 8, "heavy": 6, "weight": 1},
        }
    if model == "SEMICONDUCTOR":
        return {
            "price_to_fcf": {"fair": 35, "tranche": 25, "heavy": 18, "weight": 2},
            "ev_to_fcf": {"fair": 36, "tranche": 26, "heavy": 19, "weight": 1.5},
            "fcf_yield": {"fair": 0.028, "tranche": 0.04, "heavy": 0.055, "weight": 2},
            "price_to_sales": {"fair": 16, "tranche": 10, "heavy": 7, "weight": 1},
            "ev_to_ebitda": {"fair": 24, "tranche": 18, "heavy": 13, "weight": 1.5},
            "forward_pe": {"fair": 35, "tranche": 27, "heavy": 20, "weight": 1.5},
        }
    if model == "NETWORKING_HARDWARE":
        return {
            "price_to_fcf": {"fair": 32, "tranche": 24, "heavy": 18, "weight": 2.5},
            "ev_to_sales": {"fair": 14, "tranche": 10, "heavy": 7, "weight": 2},
        }
    if model == "CRYPTO_FINANCIAL_INFRA":
        return {
            "ev_to_sales": {"fair": 6, "tranche": 4.5, "heavy": 3.2, "weight": 4},
            "price_to_fcf": {"fair": 20, "tranche": 14, "heavy": 10, "weight": 0.4},
            "fcf_yield": {"fair": 0.05, "tranche": 0.07, "heavy": 0.10, "weight": 0.4},
        }
    if model == "BROKERAGE_FINTECH":
        return {
            "ev_to_sales": {"fair": 8, "tranche": 5.8, "heavy": 4.2, "weight": 3},
            "price_to_sales": {"fair": 7.5, "tranche": 5.5, "heavy": 4.0, "weight": 2},
        }
    if model == "AI_CLOUD_INFRA":
        return {
            "ev_to_sales": {"fair": 10, "tranche": 7, "heavy": 5, "weight": 2},
            "ev_to_rpo": {"fair": 3.5, "tranche": 2.5, "heavy": 1.8, "weight": 1.2},
            "ev_to_backlog": {"fair": 3.5, "tranche": 2.5, "heavy": 1.8, "weight": 1.2},
        }
    if model == "POWER_GENERATION":
        return {
            "ev_to_ebitda": {"fair": 11, "tranche": 9, "heavy": 7, "weight": 2},
            "market_cap_to_fcf": {"fair": 16, "tranche": 12, "heavy": 9, "weight": 2},
            "price_to_fcf": {"fair": 16, "tranche": 12, "heavy": 9, "weight": 2},
            "fcf_yield": {"fair": 0.06, "tranche": 0.08, "heavy": 0.11, "weight": 2},
        }
    return {
        "price_to_fcf": {"fair": 24, "tranche": 18, "heavy": 13, "weight": 2},
        "ev_to_fcf": {"fair": 26, "tranche": 19, "heavy": 14, "weight": 1.5},
        "fcf_yield": {"fair": 0.04, "tranche": 0.055, "heavy": 0.075, "weight": 2},
        "price_to_sales": {"fair": 8, "tranche": 5.5, "heavy": 4, "weight": 1},
    }


def _apply_growth_margin_anchor(
    model: str,
    targets: dict[str, dict[str, float]],
    metrics: dict[str, float | None],
    stockData: dict,
) -> dict[str, dict[str, float]]:
    model_key = str(model).upper()
    anchor_factor = _growth_margin_anchor_factor(model_key, metrics, stockData)
    if anchor_factor == 1:
        return targets

    adjusted: dict[str, dict[str, float]] = {}
    for key, target in targets.items():
        factor = anchor_factor
        if model_key == "SAAS_SOFTWARE" and key in {"price_to_sales", "ev_to_sales"}:
            factor = min(factor, SAAS_SALES_ANCHOR_CAP)
        adjusted[key] = dict(target)
        for zone in ("fair", "tranche", "heavy"):
            if key == "fcf_yield":
                adjusted[key][zone] = target[zone] / factor
            else:
                adjusted[key][zone] = target[zone] * factor
    return adjusted


def _growth_margin_anchor_factor(model: str, metrics: dict[str, float | None], stock_data: dict) -> float:
    model_key = str(model).upper()
    limits = GROWTH_MARGIN_ANCHOR_LIMITS.get(model_key)
    if limits is None:
        return 1.0

    growth = _growth_anchor_value(metrics)
    margin, margin_kind = _margin_anchor_value(metrics, stock_data)
    if growth is None or margin is None or margin_kind is None:
        return 1.0

    growth_signal = _growth_anchor_signal(model_key, growth)
    margin_signal = _margin_anchor_signal(model_key, margin_kind, margin)
    quality_signal = _clamp_value(0.6 * growth_signal + 0.4 * margin_signal, -1, 1)
    floor, cap = limits
    if quality_signal >= 0:
        return 1 + (cap - 1) * quality_signal
    return 1 + (1 - floor) * quality_signal


def _growth_anchor_value(metrics: dict[str, float | None]) -> float | None:
    for key in ("forward_revenue_growth", "revenue_growth", "earnings_growth", "forward_eps_growth"):
        value = metrics.get(key)
        if value is not None:
            return value
    return None


def _margin_anchor_value(metrics: dict[str, float | None], stockData: dict) -> tuple[float | None, str | None]:
    if not _fcf_margin_is_market_derived(stockData):
        fcf_margin = metrics.get("fcf_margin")
        if fcf_margin is not None:
            return fcf_margin, "fcf"
    operating_margin = metrics.get("operating_margin")
    if operating_margin is not None:
        return operating_margin, "operating"
    gross_margin = metrics.get("gross_margin")
    if gross_margin is not None:
        return gross_margin, "gross"
    return None, None


def _fcf_margin_is_market_derived(stockData: dict) -> bool:
    if stockData.get("impliedFcfMarginAsPrimaryInput") or stockData.get("implied_fcf_margin_as_primary_input"):
        return True
    source = _metric_source(stockData, "fcf_margin") or _metric_source(stockData, "direct_fcf_margin")
    return str(source or "").lower() in {"derivedfrommarket", "market_derived", "implied", "derived"}


def _growth_anchor_signal(model: str, growth: float) -> float:
    thresholds = {
        "SAAS_SOFTWARE": (-0.02, 0.05, 0.25),
        "MEGA_CAP_PLATFORM": (-0.02, 0.03, 0.15),
        "SEMICONDUCTOR": (-0.05, 0.05, 0.30),
        "NETWORKING_HARDWARE": (-0.05, 0.08, 0.30),
    }
    bad, neutral, good = thresholds.get(model, (-0.02, 0.05, 0.20))
    return _bounded_anchor_signal(growth, bad, neutral, good)


def _margin_anchor_signal(model: str, margin_kind: str, margin: float) -> float:
    if margin_kind == "gross":
        thresholds = {
            "SAAS_SOFTWARE": (0.35, 0.55, 0.78),
            "MEGA_CAP_PLATFORM": (0.35, 0.50, 0.68),
            "SEMICONDUCTOR": (0.30, 0.45, 0.68),
            "NETWORKING_HARDWARE": (0.35, 0.50, 0.68),
        }
    else:
        thresholds = {
            "SAAS_SOFTWARE": (0.00, 0.10, 0.28),
            "MEGA_CAP_PLATFORM": (0.05, 0.15, 0.32),
            "SEMICONDUCTOR": (0.00, 0.10, 0.32),
            "NETWORKING_HARDWARE": (0.05, 0.15, 0.35),
        }
    bad, neutral, good = thresholds.get(model, (0.00, 0.10, 0.25))
    return _bounded_anchor_signal(margin, bad, neutral, good)


def _bounded_anchor_signal(value: float, bad: float, neutral: float, good: float) -> float:
    if value >= good:
        return 1.0
    if value <= bad:
        return -1.0
    if value >= neutral:
        return (value - neutral) / (good - neutral)
    return -((neutral - value) / (neutral - bad))


def _clamp_value(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _buy_zone_model_supported_for_symbol(symbol: str, model: str) -> bool:
    if str(model).upper() == "BROKERAGE_FINTECH":
        return str(symbol).upper() == "HOOD"
    if str(model).upper() == "CRYPTO_FINANCIAL_INFRA":
        return str(symbol).upper() == "COIN"
    return True


def _buy_zone_model_for_symbol(symbol: str, model: str) -> str:
    symbol_key = str(symbol or "").upper()
    model_key = str(model or "").upper()
    if model_key == "AI_INFRA_HIGH_RISK":
        return "AI_CLOUD_INFRA"
    if symbol_key == "HOOD" and model_key in {"CRYPTO_FINANCIAL_INFRA", "BROKERAGE_FINTECH"}:
        return "BROKERAGE_FINTECH"
    return str(model)


def _valuation_candidates(price: float, metrics: dict[str, float | None], targets: dict[str, dict[str, float]], inputs: list[str]) -> dict[str, list[tuple[float, float]]]:
    candidates: dict[str, list[tuple[float, float]]] = {"fair": [], "tranche": [], "heavy": []}
    labels = {
        "price_to_fcf": "P/FCF",
        "ev_to_fcf": "EV/FCF",
        "fcf_yield": "FCF收益率",
        "price_to_sales": "P/S",
        "ev_to_sales": "EV/Sales",
        "forward_pe": "Forward PE",
        "ev_to_ebitda": "EV/EBITDA",
        "market_cap_to_fcf": "市值/FCF",
        "ev_to_rpo": "EV/RPO",
        "ev_to_backlog": "EV/contracted backlog",
    }
    _add_cashflow_signal_candidate(price, metrics, targets, candidates, inputs, labels)

    for key, target in targets.items():
        if key in CASHFLOW_SIGNAL_KEYS:
            continue
        current = metrics.get(key)
        if current is None or current <= 0:
            continue
        weight = target.get("weight", 1)
        inputs.append(labels.get(key, key))
        for zone in ("fair", "tranche", "heavy"):
            target_value = target[zone]
            implied_price = _implied_price(price, key, current, target_value)
            if implied_price > 0:
                candidates[zone].append((implied_price, weight))
    return candidates


def _add_cashflow_signal_candidate(
    price: float,
    metrics: dict[str, float | None],
    targets: dict[str, dict[str, float]],
    candidates: dict[str, list[tuple[float, float]]],
    inputs: list[str],
    labels: dict[str, str],
) -> None:
    available: list[tuple[str, dict[str, float], float]] = []
    for key in ("price_to_fcf", "fcf_yield"):
        target = targets.get(key)
        current = metrics.get(key)
        if target is not None and current is not None and current > 0:
            available.append((key, target, current))

    if not available:
        return

    source_labels = [labels.get(key, key) for key, _, _ in available]
    inputs.append(f"Cashflow valuation ({', '.join(source_labels)})")
    signal_weight = max(target.get("weight", 1) for _, target, _ in available)
    for zone in ("fair", "tranche", "heavy"):
        implied_prices = [
            _implied_price(price, key, current, target[zone])
            for key, target, current in available
        ]
        implied_prices = [value for value in implied_prices if value > 0]
        if implied_prices:
            candidates[zone].append((sum(implied_prices) / len(implied_prices), signal_weight))


def _implied_price(price: float, key: str, current: float, target_value: float) -> float:
    if key == "fcf_yield":
        return price * current / target_value
    return price * target_value / current


def _technical_candidates(price: float, metrics: dict[str, float | None], inputs: list[str]) -> dict[str, list[tuple[float, float]]]:
    drawdown = metrics.get("drawdown")
    if drawdown is None:
        return {"fair": [], "tranche": [], "heavy": []}
    inputs.append("技术回撤代理")
    if drawdown <= -0.35:
        return {
            "fair": [(price * 1.08, 1)],
            "tranche": [(price * 0.96, 1)],
            "heavy": [(price * 0.84, 1)],
        }
    return {
        "fair": [(price * 0.98, 1)],
        "tranche": [(price * 0.88, 1)],
        "heavy": [(price * 0.76, 1)],
    }


def _weighted_average(values: list[tuple[float, float]]) -> float | None:
    if not values:
        return None
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        return median(value for value, _ in values)
    return sum(value * weight for value, weight in values) / total_weight


def _make_monotonic(price: float, fair: float, tranche: float, heavy: float) -> tuple[float, float, float]:
    fair = max(fair, price * 0.72)
    tranche = min(tranche, fair * 0.9)
    heavy = min(heavy, tranche * 0.86)
    return fair, tranche, heavy


def _blocked_estimate(
    symbol: str,
    model: str,
    price: float | None,
    zone: str,
    reasons: list[str],
    warnings: list[str],
    inputs: list[str] | None = None,
    *,
    method: str = "guardrail",
) -> BuyZoneEstimate:
    estimate = BuyZoneEstimate(
        symbol=symbol.upper(),
        modelType=str(model),
        currentPrice=_round_price(price),
        noChaseAbove=None,
        fairValueLow=None,
        fairValueHigh=None,
        trancheBuyLow=None,
        trancheBuyHigh=None,
        heavyBuyBelow=None,
        currentZone=zone,
        confidence="low",
        method=method,
        inputsUsed=list(inputs or []),
        keyReasons=reasons,
        warnings=warnings,
        createdAt=datetime.now(timezone.utc).isoformat(),
        action="needs_review",
        nextTriggerPrice=None,
        nextBuyLabel="needs_review",
        isValid=False,
        validationErrors=list(reasons),
        technicalEntry=build_technical_entry_model(symbol, price, None, None, None).to_dict(),
    )
    return attach_combined_entry(_with_explainability(estimate, {}, None))


def _without_actionable_prices(buyZone: BuyZoneEstimate) -> BuyZoneEstimate:
    return replace(
        buyZone,
        noChaseAbove=None,
        fairValueLow=None,
        fairValueHigh=None,
        trancheBuyLow=None,
        trancheBuyHigh=None,
        heavyBuyBelow=None,
        nextTriggerPrice=None,
        isValid=False,
    )


def _with_precision_contract(buyZone: BuyZoneEstimate) -> BuyZoneEstimate:
    return replace(buyZone, precisionContract=_build_precision_contract(buyZone))


def _with_explainability(buyZone: BuyZoneEstimate, stockData: dict | None = None, scoringResult=None) -> BuyZoneEstimate:
    contracted = _with_precision_contract(buyZone)
    return replace(
        contracted,
        explainability=_build_explainability(contracted, stockData or {}, scoringResult),
    )


def _build_precision_contract(buyZone: BuyZoneEstimate) -> dict[str, Any]:
    zone = str(buyZone.currentZone or "")
    confidence = str(buyZone.confidence or "low").lower()
    validation_errors = [str(item) for item in (buyZone.validationErrors or []) if str(item)]
    blocked_reasons: list[str] = []
    precision_warnings: list[str] = []
    heavy_block_reasons: list[str] = []
    allowed_fields: list[str] = []
    blocked_fields = [
        "noChaseAbove",
        "fairValueLow",
        "fairValueHigh",
        "trancheBuyLow",
        "trancheBuyHigh",
        "heavyBuyBelow",
        "nextTriggerPrice",
    ]

    if zone in BLOCKED_BUY_ZONE_STATES:
        blocked_reasons.append(f"zone:{zone}")
    if zone == "no_chase":
        blocked_reasons.append("zone:no_chase")
    if confidence == "low":
        blocked_reasons.append("confidence:low")
    if buyZone.isValid is False:
        blocked_reasons.append("invalid_estimate")
    for reason in validation_errors:
        if _precision_validation_blocks_all(reason):
            blocked_reasons.append(f"validation:{reason}")
        elif _precision_validation_blocks_heavy(reason):
            heavy_block_reasons.append(f"validation:{reason}")
        else:
            precision_warnings.append(f"validation:{reason}")

    allow_precise = not blocked_reasons and zone in {"tranche_buy", "heavy_buy", "below_heavy_buy"}
    if allow_precise:
        allowed_fields = [
            "noChaseAbove",
            "fairValueLow",
            "fairValueHigh",
            "trancheBuyLow",
            "trancheBuyHigh",
            "nextTriggerPrice",
        ]
        if buyZone.heavyBuyBelow is not None and not heavy_block_reasons:
            allowed_fields.append("heavyBuyBelow")
        else:
            blocked_fields = ["heavyBuyBelow"]
        if zone in {"heavy_buy", "below_heavy_buy"} and heavy_block_reasons:
            blocked_reasons.extend(heavy_block_reasons)
            allow_precise = False
            allowed_fields = []
            blocked_fields = [
                "noChaseAbove",
                "fairValueLow",
                "fairValueHigh",
                "trancheBuyLow",
                "trancheBuyHigh",
                "heavyBuyBelow",
                "nextTriggerPrice",
            ]
        elif not heavy_block_reasons:
            blocked_fields = []
    elif zone == "fair_observation" and not blocked_reasons:
        allowed_fields = ["noChaseAbove", "fairValueLow", "fairValueHigh"]
        blocked_fields = ["trancheBuyLow", "trancheBuyHigh", "heavyBuyBelow", "nextTriggerPrice"]
        blocked_reasons.append("fair_observation_not_entry")

    return {
        "version": 1,
        "canShowPreciseBuyZone": allow_precise,
        "canShowObservationRange": zone == "fair_observation" and confidence != "low" and buyZone.isValid is not False,
        "allowedPriceFields": allowed_fields,
        "blockedPriceFields": blocked_fields,
        "blockedReasons": list(dict.fromkeys(blocked_reasons)),
        "precisionWarnings": list(dict.fromkeys(precision_warnings)),
        "heavyBuyBlockedReasons": list(dict.fromkeys(heavy_block_reasons)),
        "zone": zone,
        "confidence": confidence,
    }


def _precision_validation_blocks_all(reason: str) -> bool:
    lowered = str(reason or "").lower()
    return any(
        token in lowered
        for token in (
            "data_confidence_low",
            "data_insufficient",
            "buy_zone_model_not_supported",
            "invalid",
            "价格无效",
            "区间顺序异常",
        )
    )


def _precision_validation_blocks_heavy(reason: str) -> bool:
    lowered = str(reason or "").lower()
    return "heavy_buy" in lowered or "heavy" in lowered or "重仓" in lowered


def _build_explainability(buyZone: BuyZoneEstimate, stockData: dict, scoringResult=None) -> dict[str, Any]:
    zone = str(buyZone.currentZone or "")
    warnings = list(dict.fromkeys(str(item) for item in (buyZone.warnings or []) if item))
    validation_errors = list(dict.fromkeys(str(item) for item in (buyZone.validationErrors or []) if item))
    guardrail_reasons = _guardrail_reasons(zone, validation_errors, warnings)
    confidence_reasons = _confidence_reasons(buyZone, stockData, scoringResult, warnings, validation_errors)
    missing_inputs = _missing_inputs(zone, stockData, validation_errors, buyZone.modelType, buyZone.symbol)
    main_drivers = _main_drivers(buyZone, stockData)
    title, summary = _explain_title_summary(zone, buyZone, main_drivers, guardrail_reasons)
    return {
        "explainTitle": title,
        "explainSummary": summary,
        "mainDrivers": main_drivers,
        "guardrailReasons": guardrail_reasons,
        "missingInputs": missing_inputs,
        "confidenceReasons": confidence_reasons,
    }


def _explain_title_summary(zone: str, buyZone: BuyZoneEstimate, main_drivers: list[str], guardrail_reasons: list[str]) -> tuple[str, str]:
    if zone == "unsupported_buy_zone_model":
        return (
            "买区模型暂不支持",
            "当前板块暂无专属买区模型，系统保留评分和观察结论，但不输出精确买点。",
        )
    if zone in {"invalid_zone", "invalid_manual_override"}:
        return (
            "买区输入需复核",
            "当前估值区间异常，系统暂不输出买点，需复核输入。",
        )
    if zone == "low_confidence_zone":
        return (
            "买区置信度不足",
            "数据置信度不足，暂不输出入场买点。",
        )
    if zone == "data_insufficient":
        return (
            "买区数据不足",
            "关键买区输入不足，系统暂不输出精确买点。",
        )
    if zone == "no_chase":
        return (
            "当前不追高",
            "当前价高于系统买区上沿，保留观察结论，但不建议新增。",
        )
    driver_text = "、".join(main_drivers[:3]) if main_drivers else "可用估值和技术输入"
    return (
        "系统买区已生成",
        f"系统基于 {driver_text} 生成买区，当前状态为 {_buy_zone_state_label(zone)}。",
    )


def _buy_zone_state_label(zone: str) -> str:
    return {
        "invalid_zone": "买区异常",
        "invalid_manual_override": "手动买区异常",
        "low_confidence_zone": "需复核",
        "no_chase": "禁止追高",
        "fair_observation": "合理观察区",
        "tranche_buy": "可分批区",
        "heavy_buy": "极端恐慌区",
        "below_heavy_buy": "低于极端恐慌区",
        "data_insufficient": "数据不足",
        "unsupported_buy_zone_model": "买区模型暂不支持",
    }.get(str(zone or ""), "需复核")


def _guardrail_reasons(zone: str, validation_errors: list[str], warnings: list[str]) -> list[str]:
    reasons: list[str] = []
    if zone == "unsupported_buy_zone_model":
        reasons.append("当前板块暂无专属买区模型，禁用精确买点")
    if zone in {"invalid_zone", "invalid_manual_override"}:
        reasons.append("当前估值区间异常，系统暂不输出买点，需复核输入")
    if zone == "low_confidence_zone":
        reasons.append("数据置信度不足，暂不输出入场买点")
    if zone == "data_insufficient":
        reasons.append("关键买区输入不足，暂不输出精确买点")
    if zone == "no_chase":
        reasons.append("当前价处于禁止追高区，不输出新增建议")
    for item in [*validation_errors, *warnings]:
        if item in {"buy_zone_model_not_supported", "data_confidence_low", "missing_power_generation_core_inputs"}:
            continue
        if "missing" in item or "overextended" in item or "high_beta" in item or "high_ev_sales" in item or "regulatory_risk_high" in item:
            reasons.append(item)
            continue
        if "异常" in item or "缺" in item or "不足" in item or "unsupported" in item:
            reasons.append(item)
    return list(dict.fromkeys(reasons))


def _missing_inputs(zone: str, stockData: dict, validation_errors: list[str], model: str | None = None, symbol: str | None = None) -> list[str]:
    missing: list[str] = []
    if "buy_zone_model_not_supported" in validation_errors or zone == "unsupported_buy_zone_model":
        missing.append("专属买区模型")
    if "missing_power_generation_core_inputs" in validation_errors:
        missing.extend(["adjusted EBITDA", "adjusted FCF before growth"])
    if "missing_networking_hardware_growth_or_margin" in validation_errors:
        if _growth_anchor_value(_collect_metrics(stockData, [])) is None:
            missing.append("revenue growth")
        if _margin_anchor_value(_collect_metrics(stockData, []), stockData)[0] is None:
            missing.append("reliable margin")
    if "missing_crypto_ev_sales_anchor" in validation_errors:
        missing.append("EV/Sales")
    if str(model or stockData.get("modelType") or "").upper() == "NETWORKING_HARDWARE" or "networking_hardware_risk_inputs_missing" in validation_errors:
        if _networking_hardware_risk_inputs_missing("NETWORKING_HARDWARE", stockData):
            missing.extend(["customer concentration risk", "cloud capex risk"])
    if str(model or stockData.get("modelType") or "").upper() == "CRYPTO_FINANCIAL_INFRA" and str(symbol or stockData.get("ticker") or stockData.get("symbol") or "").upper() == "COIN":
        missing.extend(_crypto_financial_infra_missing_operating_inputs(stockData))
    if str(model or stockData.get("modelType") or "").upper() == "BROKERAGE_FINTECH" and str(symbol or stockData.get("ticker") or stockData.get("symbol") or "").upper() == "HOOD":
        missing.extend(_brokerage_fintech_core_inputs_missing("BROKERAGE_FINTECH", "HOOD", _collect_metrics(stockData, []), stockData))
        if _brokerage_fintech_normalized_earnings_missing("BROKERAGE_FINTECH", "HOOD", stockData):
            missing.append("normalized earnings")
    if str(model or stockData.get("modelType") or "").upper() == "AI_CLOUD_INFRA":
        missing.extend(_ai_cloud_infra_missing_inputs(stockData))
    if zone == "data_insufficient" and not missing:
        for label, keys in (
            ("current price", ("current_price", "currentPrice", "price")),
            ("P/FCF", ("price_to_fcf", "priceToFcf", "priceToFCF", "pfcf")),
            ("FCF yield", ("free_cash_flow_yield", "freeCashFlowYield", "fcfYield")),
            ("P/S", ("price_to_sales", "priceToSales", "psRatio")),
        ):
            if _first_number(stockData, *keys) is None:
                missing.append(label)
    return list(dict.fromkeys(missing))


def _confidence_reasons(
    buyZone: BuyZoneEstimate,
    stockData: dict,
    scoringResult,
    warnings: list[str],
    validation_errors: list[str],
) -> list[str]:
    reasons: list[str] = []
    confidence = str(buyZone.confidence or "").lower()
    score_confidence = str(_score_attr(scoringResult, "data_confidence") or _score_attr(scoringResult, "dataConfidence") or "").lower()
    stock_confidence = str(stockData.get("data_confidence") or stockData.get("dataConfidence") or "").lower()
    if score_confidence == "low" or stock_confidence == "low" or "data_confidence_low" in validation_errors:
        reasons.append("dataConfidence = low")
    if buyZone.currentZone in BLOCKED_BUY_ZONE_STATES:
        reasons.extend(validation_errors or warnings)
    for item in warnings:
        if "networking_hardware" in item or "crypto_financial_infra" in item or "brokerage_fintech" in item or "ai_cloud_infra" in item:
            reasons.append(item)
    if buyZone.method == "technical_proxy":
        reasons.append("仅使用技术代理，置信度较低")
    if confidence == "high" and not reasons:
        reasons.append("多项核心买区输入可用")
    if confidence == "medium" and not reasons:
        reasons.append("部分关键输入缺失或需要复核")
    if confidence == "low" and not reasons:
        reasons.append("买区输入不足或触发 guardrail")
    return list(dict.fromkeys(str(item) for item in reasons if item))


def _main_drivers(buyZone: BuyZoneEstimate, stockData: dict) -> list[str]:
    drivers: list[str] = []
    for item in dict.fromkeys(buyZone.inputsUsed or []):
        drivers.append(_driver_label(str(item), stockData))
    if not drivers and buyZone.currentZone == "unsupported_buy_zone_model":
        drivers.append(f"model_type: {buyZone.modelType}")
    if not drivers and buyZone.currentPrice is not None:
        drivers.append(f"current price: {buyZone.currentPrice:.2f}")
    return drivers[:5]


def _driver_label(label: str, stockData: dict) -> str:
    lowered = label.lower()
    if "cashflow valuation" in lowered:
        parts = []
        pfcf = _first_number(stockData, "price_to_fcf", "priceToFcf", "priceToFCF", "pfcf")
        fcf_yield = _ratio_like(_first_number(stockData, "free_cash_flow_yield", "freeCashFlowYield", "fcfYield"))
        if pfcf is not None:
            parts.append(f"P/FCF {pfcf:.1f}x")
        if fcf_yield is not None:
            parts.append(f"FCF yield {fcf_yield * 100:.1f}%")
        if parts:
            return f"{label}: {', '.join(parts)}"
        return label
    if "p/fcf" in lowered:
        return _format_driver(label, _first_number(stockData, "price_to_fcf", "priceToFcf", "priceToFCF", "pfcf"), "x")
    if "fcf" in lowered and ("yield" in lowered or "收益" in label):
        return _format_driver(label, _ratio_like(_first_number(stockData, "free_cash_flow_yield", "freeCashFlowYield", "fcfYield")), "%")
    if "p/s" in lowered:
        return _format_driver(label, _first_number(stockData, "price_to_sales", "priceToSales", "psRatio"), "x")
    if "ev/fcf" in lowered:
        return _format_driver(label, _first_number(stockData, "ev_to_fcf", "enterprise_to_fcf", "enterpriseValueToFcf"), "x")
    if "sales" in lowered:
        return _format_driver(label, _first_number(stockData, "enterprise_to_revenue", "enterpriseToRevenue", "evToSales"), "x")
    if "rpo" in lowered:
        return _format_driver(label, _ai_cloud_infra_ev_to_demand(stockData, "rpo"), "x")
    if "backlog" in lowered:
        return _format_driver(label, _ai_cloud_infra_ev_to_demand(stockData, "backlog"), "x")
    if "ebitda" in lowered:
        return _format_driver(label, _first_number(stockData, "enterprise_to_ebitda", "enterpriseValueToEbitda", "evToEbitda"), "x")
    if "technical" in lowered or "技术" in label:
        return _format_driver(label, normalize_percent_metric(_first_number(stockData, "drawdownFrom52WeekHigh", "drawdown_from_high_pct", "drawdownFromHigh")), "%")
    return label


def _format_driver(label: str, value: float | None, unit: str) -> str:
    if value is None:
        return label
    if unit == "%":
        return f"{label}: {value * 100:.1f}%"
    return f"{label}: {value:.1f}{unit}"


def validate_buy_zone_estimate(buyZone: BuyZoneEstimate, stockData: dict | None = None, scoringResult=None) -> BuyZoneEstimate:
    stockData = stockData or {}
    warnings = list(buyZone.warnings or [])
    validation_errors = list(buyZone.validationErrors or [])
    price = _number(buyZone.currentPrice)
    confidence = str(buyZone.confidence or "low").lower()
    current_zone = buyZone.currentZone
    is_valid = True

    if price is None or price <= 0:
        _append_once(warnings, "当前价格缺失或无效")
        _append_once(validation_errors, "当前价格缺失或无效")
        invalid = replace(
            buyZone,
            currentPrice=_round_price(price),
            currentZone="data_insufficient",
            confidence="low",
            action="买区异常，需复核",
            nextTriggerPrice=None,
            nextBuyLabel="买区异常，需复核",
            isValid=False,
            validationErrors=validation_errors,
            warnings=warnings,
        )
        return _with_explainability(_without_actionable_prices(invalid), stockData, scoringResult)

    if not _buy_zone_prices_valid(buyZone):
        _append_once(warnings, "买区价格无效")
        _append_once(validation_errors, "买区价格无效")
        current_zone = "invalid_manual_override" if buyZone.method == "manual_override" else "invalid_zone"
        confidence = "low"
        is_valid = False
    elif not _buy_zone_is_monotonic(buyZone):
        _append_once(warnings, "买区区间顺序异常")
        _append_once(validation_errors, "买区区间顺序异常")
        current_zone = "invalid_manual_override" if buyZone.method == "manual_override" else "invalid_zone"
        confidence = "low"
        is_valid = False
    else:
        current_zone = derive_current_zone(price, buyZone)
        if (buyZone.noChaseAbove or 0) > price * 2.5:
            _append_once(warnings, "禁止追高价与当前价偏离过大")
            current_zone = "invalid_zone"
            confidence = _downgrade_confidence(confidence, "medium")
            is_valid = False
        if (buyZone.heavyBuyBelow or 0) < price * 0.25:
            _append_once(warnings, "重仓区与当前价偏离过大")
            confidence = _downgrade_confidence(confidence, "medium")

    quality = _input_quality_flags(stockData, scoringResult, buyZone)
    for message in quality["warnings"]:
        _append_once(warnings, message)
    if quality["data_confidence_low"]:
        confidence = "low"
        if current_zone not in {"invalid_zone", "invalid_manual_override", "data_insufficient", "unsupported_buy_zone_model"}:
            current_zone = "low_confidence_zone"
            is_valid = False
            _append_once(validation_errors, "data_confidence_low")
    elif quality["forces_medium"]:
        confidence = _downgrade_confidence(confidence, "medium")
    if confidence == "high" and _core_input_count(buyZone.inputsUsed) < 2:
        confidence = "medium"
    if buyZone.method == "technical_proxy":
        confidence = "low"
    crypto_operating_inputs_missing = _crypto_financial_infra_operating_inputs_missing(buyZone.modelType, buyZone.symbol, stockData)
    if crypto_operating_inputs_missing:
        _append_once(validation_errors, "missing_crypto_operating_inputs")
    if crypto_operating_inputs_missing and current_zone in {"heavy_buy", "below_heavy_buy", "invalid_zone"}:
        current_zone = "data_insufficient"
        confidence = "low"
        is_valid = False
        _append_once(validation_errors, "missing_crypto_core_inputs_for_heavy_buy")

    next_price, next_label, trigger_warnings = derive_next_trigger_price(price, buyZone, current_zone)
    for message in trigger_warnings:
        _append_once(warnings, message)
    if current_zone in BLOCKED_BUY_ZONE_STATES:
        next_price = None
        is_valid = False
        next_label = "买区异常，需复核"
    action = _buy_zone_action(current_zone, next_label)
    validated = replace(
        buyZone,
        currentPrice=_round_price(price),
        currentZone=current_zone,
        confidence=confidence,
        action=action,
        nextTriggerPrice=next_price,
        nextBuyLabel=next_label,
        isValid=is_valid and current_zone not in BLOCKED_BUY_ZONE_STATES,
        validationErrors=validation_errors,
        warnings=warnings,
    )
    if current_zone in BLOCKED_BUY_ZONE_STATES:
        return _with_explainability(_without_actionable_prices(validated), stockData, scoringResult)
    if crypto_operating_inputs_missing and current_zone == "no_chase":
        return _with_explainability(_without_actionable_prices(validated), stockData, scoringResult)
    return _with_explainability(validated, stockData, scoringResult)


def derive_current_zone(currentPrice: Any, buyZone: BuyZoneEstimate | dict) -> str:
    price = _number(currentPrice)
    if price is None or price <= 0:
        return "data_insufficient"
    if not _zone_prices_valid(buyZone):
        return "invalid_zone"
    no_chase = _zone_value(buyZone, "noChaseAbove")
    fair_low = _zone_value(buyZone, "fairValueLow")
    fair_high = _zone_value(buyZone, "fairValueHigh")
    tranche_low = _zone_value(buyZone, "trancheBuyLow")
    tranche_high = _zone_value(buyZone, "trancheBuyHigh")
    heavy_below = _zone_value(buyZone, "heavyBuyBelow")
    if not _zone_values_are_monotonic(no_chase, fair_high, fair_low, tranche_high, tranche_low, heavy_below):
        return "invalid_zone"
    if price > no_chase:
        return "no_chase"
    if fair_low <= price <= fair_high:
        return "fair_observation"
    if tranche_low <= price <= tranche_high:
        return "tranche_buy"
    if price <= heavy_below:
        return "heavy_buy"
    if heavy_below < price < tranche_low:
        return "heavy_buy"
    if tranche_high < price < fair_low:
        return "fair_observation"
    return "fair_observation"


def derive_next_trigger_price(currentPrice: Any, buyZone: BuyZoneEstimate | dict, currentZone: str) -> tuple[float | None, str, list[str]]:
    price = _number(currentPrice)
    warnings: list[str] = []
    if price is None or price <= 0 or currentZone in {"invalid_zone", "invalid_manual_override", "data_insufficient"}:
        return None, "买区异常，需复核", warnings
    if currentZone == "no_chase":
        trigger = _zone_value(buyZone, "fairValueHigh") or _zone_value(buyZone, "fairValueLow")
        return _round_price(trigger), "等待回踩到观察区", warnings
    if currentZone == "fair_observation":
        trigger = _zone_value(buyZone, "trancheBuyHigh")
        if trigger is not None and trigger > price:
            warnings.append("当前价已低于买入触发价")
            return None, "已进入买区", warnings
        return _round_price(trigger), "估值折价触发价", warnings
    if currentZone == "tranche_buy":
        return None, "已进入可分批区", warnings
    if currentZone == "heavy_buy":
        return None, "已进入极端恐慌区", warnings
    return None, "买区异常，需复核", warnings


def _current_zone(price: float, no_chase: float, fair_low: float, fair_high: float, tranche_low: float, tranche_high: float, heavy_below: float) -> str:
    return derive_current_zone(
        price,
        {
            "noChaseAbove": no_chase,
            "fairValueLow": fair_low,
            "fairValueHigh": fair_high,
            "trancheBuyLow": tranche_low,
            "trancheBuyHigh": tranche_high,
            "heavyBuyBelow": heavy_below,
        },
    )


def _confidence(inputs: list[str], method: str, model: str, stockData: dict, warnings: list[str]) -> str:
    if method == "technical_proxy":
        return "low"
    distinct = set(inputs)
    if str(model).upper() == "BROKERAGE_FINTECH":
        if _brokerage_fintech_core_inputs_missing("BROKERAGE_FINTECH", "HOOD", _collect_metrics(stockData, []), stockData):
            return "low"
        return "medium" if len(distinct) >= 3 else "low"
    if str(model).upper() == "AI_CLOUD_INFRA":
        if _ai_cloud_infra_core_inputs_missing("AI_CLOUD_INFRA", _collect_metrics(stockData, []), stockData):
            return "low"
        return "medium" if len(distinct) >= 2 else "low"
    if _power_generation_core_inputs_missing(str(model).upper(), stockData):
        warnings.append("电力模型缺少调整后 EBITDA / 增长投资前 FCF，使用代理估值，置信度不高于中。")
        return "medium" if len(distinct) >= 2 else "low"
    if len(distinct) >= 3:
        return "high"
    if len(distinct) >= 2:
        return "medium"
    return "low"


def _power_generation_core_inputs_missing(model: str, stockData: dict) -> bool:
    if str(model).upper() != "POWER_GENERATION":
        return False
    return not any(
        stockData.get(key) is not None
        for key in (
            "adjustedEbitda",
            "manualAdjustedEbitda",
            "manual_adjusted_ebitda",
            "adjustedFcfBeforeGrowth",
            "manualAdjustedFcfBeforeGrowth",
            "adjusted_fcf_before_growth",
            "manual_adjusted_fcf_before_growth",
        )
    )


def _networking_hardware_core_inputs_missing(model: str, metrics: dict[str, float | None], stockData: dict) -> bool:
    if str(model).upper() != "NETWORKING_HARDWARE":
        return False
    return _growth_anchor_value(metrics) is None or _margin_anchor_value(metrics, stockData)[0] is None


def _networking_hardware_sales_multiple_overextended(model: str, metrics: dict[str, float | None]) -> bool:
    if str(model).upper() != "NETWORKING_HARDWARE":
        return False
    sales_multiples = (
        metrics.get("price_to_sales"),
        metrics.get("ev_to_sales"),
    )
    return any(value is not None and value >= 18 for value in sales_multiples)


def _networking_hardware_risk_inputs_missing(model: str, stockData: dict) -> bool:
    if str(model).upper() != "NETWORKING_HARDWARE":
        return False
    customer_risk = any(
        stockData.get(key) is not None
        for key in (
            "manualCustomerConcentration",
            "manual_customer_concentration",
            "customerConcentrationRisk",
            "customer_concentration_risk",
        )
    )
    cloud_capex_risk = any(
        stockData.get(key) is not None
        for key in (
            "manualCloudCapexRisk",
            "manual_cloud_capex_risk",
            "manualCapexConcern",
            "manual_capex_concern",
            "cloudCapexRisk",
            "cloud_capex_risk",
        )
    )
    return not (customer_risk and cloud_capex_risk)


AI_CLOUD_INFRA_RPO_KEYS = (
    "remaining_performance_obligations",
    "remainingPerformanceObligations",
    "rpo",
    "RPO",
    "contracted_rpo",
    "contractedRpo",
)
AI_CLOUD_INFRA_BACKLOG_KEYS = (
    "contracted_backlog",
    "contractedBacklog",
    "backlog",
    "revenue_backlog",
    "revenueBacklog",
)
AI_CLOUD_INFRA_UTILIZATION_KEYS = (
    "utilization",
    "gpu_utilization",
    "gpuUtilization",
    "fleet_utilization",
    "fleetUtilization",
    "capacity_utilization",
    "capacityUtilization",
)
AI_CLOUD_INFRA_CAPEX_COMMITMENT_KEYS = (
    "capex_commitments",
    "capexCommitments",
    "contracted_capex_commitments",
    "contractedCapexCommitments",
    "remaining_capex_commitments",
    "remainingCapexCommitments",
)
AI_CLOUD_INFRA_CUSTOMER_CONCENTRATION_KEYS = (
    "customer_concentration",
    "customerConcentration",
    "customer_concentration_risk",
    "customerConcentrationRisk",
    "top_customer_revenue_share",
    "topCustomerRevenueShare",
    "manual_customer_concentration",
    "manualCustomerConcentration",
)
AI_CLOUD_INFRA_DEBT_MATURITY_KEYS = (
    "debt_maturity",
    "debtMaturity",
    "debt_maturity_schedule",
    "debtMaturitySchedule",
    "debt_maturity_pressure",
    "debtMaturityPressure",
    "nearest_debt_maturity",
    "nearestDebtMaturity",
)


def _ai_cloud_infra_core_inputs_missing(model: str, metrics: dict[str, float | None], stockData: dict) -> bool:
    if str(model).upper() != "AI_CLOUD_INFRA":
        return False
    has_valuation_anchor = any(
        value is not None and value > 0
        for value in (metrics.get("ev_to_sales"), metrics.get("ev_to_rpo"), metrics.get("ev_to_backlog"))
    )
    has_reliable_demand = _ai_cloud_infra_has_reliable_demand(stockData)
    has_utilization = _ai_cloud_infra_has_ratio(stockData, *AI_CLOUD_INFRA_UTILIZATION_KEYS)
    has_capex_commitments = _ai_cloud_infra_has_clean_money(stockData, *AI_CLOUD_INFRA_CAPEX_COMMITMENT_KEYS)
    has_operating_context = (
        has_reliable_demand
        or (has_utilization and has_capex_commitments)
    )
    return not (has_valuation_anchor and has_operating_context)


def _ai_cloud_infra_ev_to_demand(stockData: dict, demand_kind: str) -> float | None:
    if demand_kind == "rpo":
        explicit = _first_number(stockData, "ev_to_rpo", "evToRpo", "enterprise_to_rpo", "enterpriseToRpo")
        keys = AI_CLOUD_INFRA_RPO_KEYS
    else:
        explicit = _first_number(stockData, "ev_to_backlog", "evToBacklog", "enterprise_to_backlog", "enterpriseToBacklog")
        keys = AI_CLOUD_INFRA_BACKLOG_KEYS
    if explicit is not None and explicit > 0:
        return explicit
    enterprise_value = _first_number(stockData, "enterprise_value", "enterpriseValue", "ev")
    demand = _first_number_from_stock_or_disclosure(stockData, *keys)
    if enterprise_value is None or enterprise_value <= 0 or demand is None or demand <= 0:
        return None
    if not _ai_cloud_infra_clean_metric_source(stockData, *keys):
        return None
    return enterprise_value / demand


def _ai_cloud_infra_capex_intensity(stockData: dict) -> float | None:
    explicit = _ratio_like(_first_number(stockData, "capex_intensity", "capexIntensity", "capex_to_revenue", "capexToRevenue"))
    if explicit is not None:
        return abs(explicit)
    capex = _first_number(stockData, "capex", "capital_expenditure", "capitalExpenditure", "capital_expenditures", "capitalExpenditures")
    revenue = _first_number(stockData, "total_revenue", "totalRevenue", "revenue")
    if capex is None or revenue is None or revenue <= 0:
        return None
    return abs(capex) / revenue


def _ai_cloud_infra_debt_to_revenue(stockData: dict) -> float | None:
    explicit = _first_number(stockData, "debt_to_revenue", "debtToRevenue")
    if explicit is not None:
        return explicit
    debt = _first_number(stockData, "total_debt", "totalDebt", "net_debt", "netDebt")
    revenue = _first_number(stockData, "total_revenue", "totalRevenue", "revenue")
    if debt is None or revenue is None or revenue <= 0:
        return None
    return max(debt, 0) / revenue


def _ai_cloud_infra_overextended(model: str, metrics: dict[str, float | None], stockData: dict) -> bool:
    if str(model).upper() != "AI_CLOUD_INFRA":
        return False
    ev_sales = metrics.get("ev_to_sales")
    capex_intensity = metrics.get("capex_intensity")
    debt_to_revenue = metrics.get("debt_to_revenue")
    net_debt_to_ebitda = metrics.get("net_debt_to_ebitda")
    high_debt = (debt_to_revenue is not None and debt_to_revenue >= 2.0) or (net_debt_to_ebitda is not None and net_debt_to_ebitda >= 4.0)
    return bool(ev_sales is not None and ev_sales >= 15 and capex_intensity is not None and capex_intensity >= 0.45 and high_debt)


def _ai_cloud_infra_blocks_heavy_buy(metrics: dict[str, float | None], stockData: dict) -> bool:
    fcf = _first_number(stockData, "free_cash_flow", "freeCashFlow", "fcf")
    capex_intensity = metrics.get("capex_intensity") or _ai_cloud_infra_capex_intensity(stockData)
    return bool((fcf is not None and fcf < 0) or (capex_intensity is not None and capex_intensity >= 0.45))


def _ai_cloud_infra_has_reliable_demand(stockData: dict) -> bool:
    return _ai_cloud_infra_has_clean_money(stockData, *AI_CLOUD_INFRA_RPO_KEYS) or _ai_cloud_infra_has_clean_money(stockData, *AI_CLOUD_INFRA_BACKLOG_KEYS)


def _ai_cloud_infra_has_clean_money(stockData: dict, *keys: str) -> bool:
    value = _first_number_from_stock_or_disclosure(stockData, *keys)
    return value is not None and value > 0 and _ai_cloud_infra_clean_metric_source(stockData, *keys)


def _ai_cloud_infra_has_ratio(stockData: dict, *keys: str) -> bool:
    value = _ratio_like(_first_number_from_stock_or_disclosure(stockData, *keys))
    return value is not None and value > 0 and _ai_cloud_infra_clean_metric_source(stockData, *keys)


def _ai_cloud_infra_clean_metric_source(stockData: dict, *keys: str) -> bool:
    source = _metric_source_payload(stockData, *keys)
    if not source:
        return True
    unit = str(source.get("unit") or source.get("valueScale") or source.get("value_scale") or "").lower()
    if unit == "percent" and not set(keys).intersection(AI_CLOUD_INFRA_UTILIZATION_KEYS):
        return False
    review_status = str(source.get("reviewStatus") or source.get("review_status") or "").lower()
    if review_status in {"stale", "duplicate_archived", "auto_archived", "invalid_review_item", "rejected"}:
        return False
    freshness_status = str(source.get("freshnessStatus") or source.get("freshness_status") or "").lower()
    if freshness_status == "historical_value":
        return False
    return True


def _ai_cloud_infra_missing_inputs(stockData: dict) -> list[str]:
    missing: list[str] = []
    if not _ai_cloud_infra_has_reliable_demand(stockData):
        missing.append("RPO / contracted backlog")
    if not _ai_cloud_infra_has_ratio(stockData, *AI_CLOUD_INFRA_UTILIZATION_KEYS):
        missing.append("utilization")
    if not _ai_cloud_infra_has_clean_money(stockData, *AI_CLOUD_INFRA_CAPEX_COMMITMENT_KEYS):
        missing.append("capex commitments")
    if not _has_any_value(stockData, *AI_CLOUD_INFRA_CUSTOMER_CONCENTRATION_KEYS):
        missing.append("customer concentration")
    if not _has_any_value(stockData, *AI_CLOUD_INFRA_DEBT_MATURITY_KEYS):
        missing.append("debt maturity")
    return missing


def _append_ai_cloud_infra_operating_inputs(model: str, stockData: dict, inputs: list[str], warnings: list[str]) -> None:
    if str(model).upper() != "AI_CLOUD_INFRA":
        return
    if _ai_cloud_infra_has_reliable_demand(stockData):
        inputs.append("AI cloud contracted demand")
    if _ai_cloud_infra_has_ratio(stockData, *AI_CLOUD_INFRA_UTILIZATION_KEYS):
        inputs.append("AI cloud utilization")
    if _ai_cloud_infra_has_clean_money(stockData, *AI_CLOUD_INFRA_CAPEX_COMMITMENT_KEYS):
        inputs.append("AI cloud capex commitments")
    missing = _ai_cloud_infra_missing_inputs(stockData)
    if "customer concentration" in missing:
        _append_once(warnings, "ai_cloud_infra_customer_concentration_missing")
    if "debt maturity" in missing:
        _append_once(warnings, "ai_cloud_infra_debt_maturity_unclear")
    if "utilization" in missing or "capex commitments" in missing:
        _append_once(warnings, "ai_cloud_infra_operating_inputs_incomplete")


def _crypto_financial_infra_core_anchor_missing(model: str, symbol: str, metrics: dict[str, float | None]) -> bool:
    if str(model).upper() != "CRYPTO_FINANCIAL_INFRA" or str(symbol).upper() != "COIN":
        return False
    return metrics.get("ev_to_sales") is None


def _crypto_financial_infra_operating_inputs_missing(model: str, symbol: str, stockData: dict) -> bool:
    if str(model).upper() != "CRYPTO_FINANCIAL_INFRA" or str(symbol).upper() != "COIN":
        return False
    return bool(_crypto_financial_infra_missing_operating_inputs(stockData))


def _crypto_financial_infra_missing_operating_inputs(stockData: dict) -> list[str]:
    missing: list[str] = []
    if not _has_any_value(
        stockData,
        "transactionRevenueMix",
        "transaction_revenue_mix",
        "transactionRevenueShare",
        "transaction_revenue_share",
        "manualTransactionRevenueMix",
        "manual_transaction_revenue_mix",
    ):
        missing.append("transaction revenue mix")
    if not _has_any_value(
        stockData,
        "usdcRevenueMix",
        "usdc_revenue_mix",
        "stablecoinRevenueMix",
        "stablecoin_revenue_mix",
        "subscriptionRevenueMix",
        "subscription_revenue_mix",
        "manualUsdcRevenueMix",
        "manual_usdc_revenue_mix",
    ):
        missing.append("subscription / USDC revenue mix")
    if not _has_any_value(
        stockData,
        "normalizedEarnings",
        "normalized_earnings",
        "normalizedEps",
        "normalized_eps",
        "normalizedEbitda",
        "normalized_ebitda",
        "manualNormalizedEarnings",
        "manual_normalized_earnings",
    ):
        missing.append("normalized earnings")
    if not _has_any_value(
        stockData,
        "btcBeta",
        "btc_beta",
        "bitcoinBeta",
        "bitcoin_beta",
        "cryptoCycleSignal",
        "crypto_cycle_signal",
        "manualCryptoCycleSetup",
        "manual_crypto_cycle_setup",
    ):
        missing.append("BTC cycle signal")
    return missing


def _crypto_financial_infra_beta_sales_overextended(model: str, symbol: str, metrics: dict[str, float | None], stockData: dict) -> bool:
    if str(model).upper() != "CRYPTO_FINANCIAL_INFRA" or str(symbol).upper() != "COIN":
        return False
    beta = metrics.get("beta") or _first_number(stockData, "beta", "marketBeta")
    ev_sales = metrics.get("ev_to_sales")
    ps = metrics.get("price_to_sales")
    sales_multiple = ev_sales if ev_sales is not None else ps
    return beta is not None and beta >= 2.0 and sales_multiple is not None and sales_multiple >= 7.0


def _crypto_financial_infra_regulatory_risk_high(model: str, symbol: str, stockData: dict, scoringResult=None) -> bool:
    if str(model).upper() != "CRYPTO_FINANCIAL_INFRA" or str(symbol).upper() != "COIN":
        return False
    raw_values = (
        stockData.get("regulatoryRisk"),
        stockData.get("manualRegulatoryRisk"),
        stockData.get("regulatory_risk"),
        stockData.get("manual_regulatory_risk"),
        _score_attr(scoringResult, "regulatoryRisk"),
        _score_attr(scoringResult, "manualRegulatoryRisk"),
    )
    for value in raw_values:
        if value in {None, ""}:
            continue
        number = _number(value)
        if number is not None:
            return number >= 70
        text = str(value).strip().lower()
        if text in {"high", "高", "high risk", "高风险"}:
            return True
    return False


BROKERAGE_FINTECH_CORE_FIELDS = (
    ("AUC", ("hood_auc", "hoodAuc", "manualHoodAuc", "manual_hood_auc")),
    ("net deposits", ("hood_net_deposits", "hoodNetDeposits", "manualHoodNetDeposits", "manual_hood_net_deposits")),
    (
        "transaction revenue",
        ("hood_transaction_revenue", "hoodTransactionRevenue", "manualHoodTransactionRevenue", "manual_hood_transaction_revenue"),
    ),
    (
        "subscription / Gold revenue",
        (
            "hood_subscription_gold_revenue",
            "hoodSubscriptionGoldRevenue",
            "manualHoodSubscriptionGoldRevenue",
            "manual_hood_subscription_gold_revenue",
        ),
    ),
    (
        "normalized EBITDA",
        ("hood_normalized_ebitda", "hoodNormalizedEbitda", "manualHoodNormalizedEbitda", "manual_hood_normalized_ebitda"),
    ),
)


def _brokerage_fintech_core_inputs_missing(model: str, symbol: str, metrics: dict[str, float | None], stockData: dict) -> list[str]:
    if str(model).upper() != "BROKERAGE_FINTECH" or str(symbol).upper() != "HOOD":
        return []
    missing: list[str] = []
    if metrics.get("ev_to_sales") is None and metrics.get("price_to_sales") is None:
        missing.append("missing_brokerage_sales_valuation_anchor")
    for label, keys in BROKERAGE_FINTECH_CORE_FIELDS:
        if not _brokerage_fintech_clean_field(stockData, keys):
            missing.append(f"missing_or_unreliable_{label}")
    return missing


def _with_brokerage_fintech_review_candidates(symbol: str, model: str, stockData: dict) -> dict:
    if str(model).upper() != "BROKERAGE_FINTECH" or str(symbol).upper() != "HOOD":
        return stockData
    if all(_brokerage_fintech_clean_field(stockData, keys) for _, keys in BROKERAGE_FINTECH_CORE_FIELDS):
        return stockData
    try:
        from data.review_queue_builder import ReviewQueueStore
    except Exception:
        return stockData

    try:
        rows = ReviewQueueStore().list_items("HOOD")
    except Exception:
        return stockData

    enriched = dict(stockData)
    metric_sources = dict(enriched.get("metric_sources") or {})
    for _, keys in BROKERAGE_FINTECH_CORE_FIELDS:
        target_key = _snake_metric_key(keys[0])
        if _brokerage_fintech_clean_field(enriched, keys):
            continue
        if _has_any_value(enriched, *keys):
            continue
        row = _best_brokerage_fintech_review_candidate(rows, keys)
        if not row:
            continue
        value = _number(row.get("value") if row.get("value") is not None else row.get("normalizedValue"))
        if value is None:
            continue
        enriched[target_key] = value
        metric_sources[target_key] = {
            "sourceType": row.get("sourceType"),
            "sourceUrl": row.get("sourceUrl"),
            "sourceDocumentTitle": row.get("sourceDocumentTitle"),
            "extractedText": row.get("evidenceText") or row.get("extractedText"),
            "confidence": row.get("confidence"),
            "period": row.get("metricPeriod") or row.get("period"),
            "reviewStatus": row.get("reviewStatus"),
            "unit": row.get("unit"),
            "freshnessStatus": row.get("freshnessStatus"),
        }
    if metric_sources:
        enriched["metric_sources"] = metric_sources
    return enriched


def _best_brokerage_fintech_review_candidate(rows: list[dict], keys: tuple[str, ...]) -> dict | None:
    key_set = set(keys)
    candidates = []
    for row in rows:
        if str(row.get("metricKey") or "") not in key_set:
            continue
        if str(row.get("itemType") or "") != "extracted_value":
            continue
        if str(row.get("reviewStatus") or "") != "pending_review" or row.get("hiddenByDefault"):
            continue
        if str(row.get("unit") or "").lower() == "percent":
            continue
        if not str(row.get("evidenceText") or row.get("extractedText") or "").strip():
            continue
        candidates.append(row)
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: (str(row.get("metricPeriod") or row.get("period") or ""), str(row.get("updatedAt") or ""), int(row.get("id") or 0)), reverse=True)[0]


def _snake_metric_key(value: str) -> str:
    text = str(value or "")
    return "".join([f"_{char.lower()}" if char.isupper() else char for char in text]).lstrip("_")


def _brokerage_fintech_clean_field(stockData: dict, keys: tuple[str, ...]) -> bool:
    value = _first_number_from_stock_or_disclosure(stockData, *keys)
    if value is None or value <= 0:
        return False
    source = _metric_source_payload(stockData, *keys)
    if not source:
        return True
    unit = str(source.get("unit") or source.get("valueScale") or source.get("value_scale") or "").lower()
    if unit == "percent":
        return False
    review_status = str(source.get("reviewStatus") or source.get("review_status") or "").lower()
    if review_status in {"stale", "duplicate_archived", "auto_archived", "invalid_review_item", "rejected"}:
        return False
    freshness_status = str(source.get("freshnessStatus") or source.get("freshness_status") or "").lower()
    if freshness_status == "historical_value":
        return False
    return True


def _brokerage_fintech_normalized_earnings_missing(model: str, symbol: str, stockData: dict) -> bool:
    if str(model).upper() != "BROKERAGE_FINTECH" or str(symbol).upper() != "HOOD":
        return False
    return not _brokerage_fintech_clean_field(
        stockData,
        (
            "hood_normalized_earnings",
            "hoodNormalizedEarnings",
            "manualHoodNormalizedEarnings",
            "manual_hood_normalized_earnings",
        ),
    )


def _brokerage_fintech_beta_sales_overextended(model: str, symbol: str, metrics: dict[str, float | None], stockData: dict) -> bool:
    if str(model).upper() != "BROKERAGE_FINTECH" or str(symbol).upper() != "HOOD":
        return False
    beta = metrics.get("beta") or _first_number(stockData, "beta", "marketBeta")
    sales_multiple = metrics.get("ev_to_sales") if metrics.get("ev_to_sales") is not None else metrics.get("price_to_sales")
    return beta is not None and beta >= 1.8 and sales_multiple is not None and sales_multiple >= 8.0


def _append_brokerage_fintech_operating_inputs(model: str, symbol: str, stockData: dict, inputs: list[str], warnings: list[str]) -> None:
    if str(model).upper() != "BROKERAGE_FINTECH" or str(symbol).upper() != "HOOD":
        return
    for label, keys in BROKERAGE_FINTECH_CORE_FIELDS:
        if _brokerage_fintech_clean_field(stockData, keys):
            inputs.append(f"HOOD operating field: {label}")
    if _brokerage_fintech_clean_field(
        stockData,
        ("hood_normalized_ebitda", "hoodNormalizedEbitda", "manualHoodNormalizedEbitda", "manual_hood_normalized_ebitda"),
    ):
        _append_once(warnings, "brokerage_fintech_normalized_ebitda_secondary_anchor_needs_non_gaap_review")
    if _brokerage_fintech_normalized_earnings_missing(model, symbol, stockData):
        _append_once(warnings, "brokerage_fintech_normalized_earnings_missing_blocks_high_confidence_and_heavy_buy")


def _finalize_brokerage_fintech_estimate(buyZone: BuyZoneEstimate, stockData: dict, scoringResult=None) -> BuyZoneEstimate:
    if str(buyZone.modelType).upper() != "BROKERAGE_FINTECH" or str(buyZone.symbol).upper() != "HOOD":
        return buyZone
    warnings = list(buyZone.warnings or [])
    validation_errors = list(buyZone.validationErrors or [])
    confidence = _downgrade_confidence(str(buyZone.confidence or "low"), "medium")
    current_zone = buyZone.currentZone
    if _brokerage_fintech_normalized_earnings_missing("BROKERAGE_FINTECH", "HOOD", stockData):
        _append_once(warnings, "brokerage_fintech_normalized_earnings_missing_blocks_high_confidence_and_heavy_buy")
        _append_once(validation_errors, "missing_hood_normalized_earnings_for_heavy_buy")
        if current_zone in {"heavy_buy", "below_heavy_buy"}:
            current_zone = "tranche_buy"
    next_price, next_label, _ = derive_next_trigger_price(buyZone.currentPrice, buyZone, current_zone)
    finalized = replace(
        buyZone,
        currentZone=current_zone,
        confidence=confidence,
        heavyBuyBelow=None,
        action=_buy_zone_action(current_zone, next_label),
        nextTriggerPrice=next_price,
        nextBuyLabel=next_label,
        warnings=warnings,
        validationErrors=validation_errors,
    )
    return _with_explainability(finalized, stockData, scoringResult)


def _finalize_ai_cloud_infra_estimate(buyZone: BuyZoneEstimate, stockData: dict, scoringResult=None) -> BuyZoneEstimate:
    if str(buyZone.modelType).upper() != "AI_CLOUD_INFRA":
        return buyZone
    metrics = _collect_metrics(stockData, [])
    warnings = list(buyZone.warnings or [])
    validation_errors = list(buyZone.validationErrors or [])
    confidence = str(buyZone.confidence or "low").lower()
    current_zone = buyZone.currentZone
    heavy_buy_below = buyZone.heavyBuyBelow

    if _ai_cloud_infra_overextended("AI_CLOUD_INFRA", metrics, stockData):
        current_zone = "no_chase"
        _append_once(warnings, "ai_cloud_infra_high_ev_sales_capex_debt")

    missing_inputs = _ai_cloud_infra_missing_inputs(stockData)
    if "customer concentration" in missing_inputs:
        confidence = _downgrade_confidence(confidence, "medium")
        _append_once(warnings, "ai_cloud_infra_customer_concentration_missing")
    if "debt maturity" in missing_inputs:
        confidence = _downgrade_confidence(confidence, "medium")
        _append_once(warnings, "ai_cloud_infra_debt_maturity_unclear")
    if "utilization" in missing_inputs or "capex commitments" in missing_inputs:
        confidence = _downgrade_confidence(confidence, "medium")
        _append_once(warnings, "ai_cloud_infra_operating_inputs_incomplete")

    if _ai_cloud_infra_blocks_heavy_buy(metrics, stockData):
        confidence = _downgrade_confidence(confidence, "medium")
        heavy_buy_below = None
        _append_once(warnings, "ai_cloud_infra_fcf_burn_or_capex_intensity_blocks_heavy_buy")
        _append_once(validation_errors, "ai_cloud_infra_no_heavy_buy_without_positive_fcf_and_capex_discipline")
        if current_zone in {"heavy_buy", "below_heavy_buy"}:
            current_zone = "tranche_buy"

    next_price, next_label, _ = derive_next_trigger_price(buyZone.currentPrice, buyZone, current_zone)
    finalized = replace(
        buyZone,
        currentZone=current_zone,
        confidence=confidence,
        heavyBuyBelow=heavy_buy_below,
        action=_buy_zone_action(current_zone, next_label),
        nextTriggerPrice=next_price,
        nextBuyLabel=next_label,
        warnings=warnings,
        validationErrors=validation_errors,
    )
    return _with_explainability(finalized, stockData, scoringResult)


def _has_any_value(stockData: dict, *keys: str) -> bool:
    return any(stockData.get(key) not in {None, ""} for key in keys)


def _reason_texts(model: str, metrics: dict[str, float | None], current_zone: str, inputs: list[str], confidence: str) -> list[str]:
    reasons = [f"使用 {', '.join(dict.fromkeys(inputs))} 合成系统买区。"]
    input_text = " ".join(inputs).lower()
    uses_cashflow = "cashflow valuation" in input_text or "p/fcf" in input_text or "fcf" in input_text
    if metrics.get("fcf_yield") is not None and uses_cashflow:
        reasons.append(f"FCF收益率约 {metrics['fcf_yield'] * 100:.1f}%，用于估值锚。")
    if metrics.get("price_to_fcf") is not None and uses_cashflow:
        reasons.append(f"P/FCF 约 {metrics['price_to_fcf']:.1f}x，作为主要现金流估值输入。")
    if metrics.get("drawdown") is not None:
        reasons.append(f"距高点回撤约 {metrics['drawdown'] * 100:.1f}%，用于调节当前区间。")
    if current_zone == "no_chase" or _is_overheated(metrics):
        reasons.append("短线或估值偏热时不生成重仓买入信号。")
    if confidence != "high":
        reasons.append("部分行业关键输入缺失，系统建议仅作为初版买区。")
    if str(model).upper() == "POWER_GENERATION":
        reasons.append("电力股优先参考 FCF、EV/EBITDA、杠杆和回撤，不按 SaaS 模型硬套。")
    if str(model).upper() == "BROKERAGE_FINTECH":
        reasons.append("HOOD brokerage fintech model uses EV/Sales or P/S as valuation anchors and operating fields as guardrails.")
        reasons.append("Normalized EBITDA is only a secondary non-GAAP anchor; normalized earnings is required before heavy-buy output.")
    if str(model).upper() == "AI_CLOUD_INFRA":
        reasons.append("AI cloud infra model uses EV/Sales and reliable EV/RPO or contracted backlog only as weak anchors.")
        reasons.append("Capex intensity, leverage, FCF burn, utilization and customer concentration act as guardrails before any precise buy-zone output.")
    return reasons


def _append_once(items: list[str], message: str) -> None:
    if message not in items:
        items.append(message)


def _downgrade_confidence(current: str, ceiling: str) -> str:
    rank = {"low": 0, "medium": 1, "high": 2}
    reverse = {0: "low", 1: "medium", 2: "high"}
    return reverse[min(rank.get(str(current).lower(), 0), rank.get(ceiling, 0))]


def _core_input_count(inputs: list[str]) -> int:
    core: set[str] = set()
    for item in inputs:
        text = str(item).lower()
        if any(marker in text for marker in ("technical", "proxy", "implied", "代理", "技术")):
            continue
        core.add(text)
    return len(core)


def _input_quality_flags(stockData: dict, scoringResult, buyZone: BuyZoneEstimate) -> dict:
    warnings: list[str] = []
    forces_medium = False
    data_confidence_values = (
        _score_attr(scoringResult, "data_confidence"),
        _score_attr(scoringResult, "dataConfidence"),
        stockData.get("data_confidence"),
        stockData.get("dataConfidence"),
    )
    data_confidence_low = any(str(value).lower() == "low" for value in data_confidence_values if value not in {None, ""})
    if data_confidence_low:
        warnings.append("dataConfidence = low，买区置信度降为 low")

    if _has_unreviewed_metric_source(stockData):
        warnings.append("包含未复核或缺证据指标，买区置信度不能为 high")
        forces_medium = True
    if _uses_implied_fcf_margin(stockData, buyZone):
        warnings.append("使用 impliedFcfMargin 作为估值输入，买区置信度不能为 high")
        forces_medium = True
    if _uses_low_confidence_proxy(stockData, scoringResult, buyZone):
        warnings.append("使用低置信 proxy，买区置信度不能为 high")
        forces_medium = True
    if _networking_hardware_risk_inputs_missing(buyZone.modelType, stockData):
        warnings.append("networking_hardware_risk_inputs_missing: customer concentration / cloud capex risk")
        forces_medium = True
    if _crypto_financial_infra_operating_inputs_missing(buyZone.modelType, buyZone.symbol, stockData):
        warnings.append("crypto_financial_infra_operating_mix_missing")
        forces_medium = True
    if str(buyZone.modelType).upper() == "BROKERAGE_FINTECH":
        if _brokerage_fintech_normalized_earnings_missing(buyZone.modelType, buyZone.symbol, stockData):
            warnings.append("brokerage_fintech_normalized_earnings_missing_blocks_high_confidence_and_heavy_buy")
            forces_medium = True
        if _brokerage_fintech_clean_field(
            stockData,
            ("hood_normalized_ebitda", "hoodNormalizedEbitda", "manualHoodNormalizedEbitda", "manual_hood_normalized_ebitda"),
        ):
            warnings.append("brokerage_fintech_normalized_ebitda_secondary_anchor_needs_non_gaap_review")
            forces_medium = True
    if str(buyZone.modelType).upper() == "AI_CLOUD_INFRA":
        missing_ai_inputs = _ai_cloud_infra_missing_inputs(stockData)
        if "customer concentration" in missing_ai_inputs:
            warnings.append("ai_cloud_infra_customer_concentration_missing")
            forces_medium = True
        if "debt maturity" in missing_ai_inputs:
            warnings.append("ai_cloud_infra_debt_maturity_unclear")
            forces_medium = True
        if "utilization" in missing_ai_inputs or "capex commitments" in missing_ai_inputs:
            warnings.append("ai_cloud_infra_operating_inputs_incomplete")
            forces_medium = True
        if _ai_cloud_infra_blocks_heavy_buy(_collect_metrics(stockData, []), stockData):
            warnings.append("ai_cloud_infra_fcf_burn_or_capex_intensity_blocks_heavy_buy")
            forces_medium = True
    if _has_abnormal_percent_input(stockData):
        warnings.append("存在异常百分比输入，买区置信度不能为 high")
        forces_medium = True
    return {"warnings": warnings, "forces_medium": forces_medium, "data_confidence_low": data_confidence_low}


def _has_unreviewed_metric_source(stockData: dict) -> bool:
    blocked = {"pending_review", "needs_evidence", "needs_data"}
    top_status = str(stockData.get("reviewStatus") or stockData.get("review_status") or "").lower()
    if top_status in blocked:
        return True
    metric_sources = stockData.get("metric_sources") or stockData.get("metricSources")
    if not isinstance(metric_sources, dict):
        return False
    for source in metric_sources.values():
        if not isinstance(source, dict):
            continue
        review_status = str(source.get("reviewStatus") or source.get("review_status") or "").lower()
        resolution_status = str(source.get("resolutionStatus") or source.get("resolution_status") or "").lower()
        if review_status in blocked or resolution_status in blocked:
            return True
        source_type = str(source.get("sourceType") or source.get("source_type") or "").lower()
        if "unreviewed" in source_type:
            return True
    return False


def _uses_implied_fcf_margin(stockData: dict, buyZone: BuyZoneEstimate) -> bool:
    input_text = " ".join(str(item) for item in [*buyZone.inputsUsed, stockData.get("usedInputs"), stockData.get("inputsUsed"), stockData.get("primaryInput")]).lower()
    if any(marker in input_text for marker in ("impliedfcfmargin", "implied_fcf_margin", "implied fcf margin")):
        return True
    return bool(stockData.get("impliedFcfMarginAsPrimaryInput") or stockData.get("implied_fcf_margin_as_primary_input"))


def _uses_low_confidence_proxy(stockData: dict, scoringResult, buyZone: BuyZoneEstimate) -> bool:
    proxy_confidence = str(stockData.get("proxyConfidence") or stockData.get("proxy_confidence") or _score_attr(scoringResult, "proxyConfidence") or "").lower()
    input_text = " ".join(str(item) for item in buyZone.inputsUsed).lower()
    return proxy_confidence == "low" or buyZone.method == "technical_proxy" or any(marker in input_text for marker in ("proxy", "代理"))


def _has_abnormal_percent_input(stockData: dict) -> bool:
    keys = (
        "drawdownFrom52WeekHigh",
        "drawdown_from_high_pct",
        "drawdownFromHigh",
        "gain_20d_pct",
        "gain_60d_pct",
        "return20d",
        "return60d",
        "free_cash_flow_yield",
        "freeCashFlowYield",
        "fcf_yield",
        "revenue_growth",
        "revenueGrowth",
    )
    for key in keys:
        value = _number(stockData.get(key))
        if value is not None and abs(value) > 100:
            return True
    return False


def _buy_zone_action(current_zone: str, next_label: str) -> str:
    if current_zone in {"invalid_zone", "invalid_manual_override", "data_insufficient"}:
        return "买区异常，需复核"
    if current_zone == "no_chase":
        return "等待回踩"
    if current_zone == "fair_observation":
        return next_label or "观察，等待买入触发"
    if current_zone == "tranche_buy":
        return "已进入可分批区"
    if current_zone == "heavy_buy":
        return "已进入极端恐慌区"
    return "买区异常，需复核"


def _buy_zone_prices_valid(buyZone: BuyZoneEstimate) -> bool:
    return _zone_prices_valid(buyZone)


def _buy_zone_is_monotonic(buyZone: BuyZoneEstimate) -> bool:
    return _zone_values_are_monotonic(
        _zone_value(buyZone, "noChaseAbove"),
        _zone_value(buyZone, "fairValueHigh"),
        _zone_value(buyZone, "fairValueLow"),
        _zone_value(buyZone, "trancheBuyHigh"),
        _zone_value(buyZone, "trancheBuyLow"),
        _zone_value(buyZone, "heavyBuyBelow"),
    )


def _zone_prices_valid(buyZone: BuyZoneEstimate | dict) -> bool:
    values = [
        _zone_value(buyZone, "noChaseAbove"),
        _zone_value(buyZone, "fairValueHigh"),
        _zone_value(buyZone, "fairValueLow"),
        _zone_value(buyZone, "trancheBuyHigh"),
        _zone_value(buyZone, "trancheBuyLow"),
        _zone_value(buyZone, "heavyBuyBelow"),
    ]
    return all(value is not None and value > 0 for value in values)


def _zone_values_are_monotonic(no_chase: float | None, fair_high: float | None, fair_low: float | None, tranche_high: float | None, tranche_low: float | None, heavy_below: float | None) -> bool:
    values = [no_chase, fair_high, fair_low, tranche_high, tranche_low, heavy_below]
    if any(value is None for value in values):
        return False
    return bool(no_chase > fair_high > fair_low > tranche_high > tranche_low > heavy_below)


def _zone_value(buyZone: BuyZoneEstimate | dict, attr: str) -> float | None:
    if isinstance(buyZone, dict):
        value = buyZone.get(attr)
        if value is None:
            snake = "".join([f"_{char.lower()}" if char.isupper() else char for char in attr]).lstrip("_")
            value = buyZone.get(snake)
        return _number(value)
    return _number(getattr(buyZone, attr, None))


def _override_number(plan: dict, field: str, fallback: float | None) -> float | None:
    value = _number(plan.get(field))
    return value if value is not None else fallback


def _is_overheated(metrics: dict[str, float | None]) -> bool:
    rsi = metrics.get("rsi14")
    ret20 = metrics.get("return20d")
    drawdown = metrics.get("drawdown")
    return bool((rsi is not None and rsi >= 70) or (ret20 is not None and ret20 >= 0.20) or (drawdown is not None and drawdown > -0.08 and rsi is not None and rsi >= 62))


def _market_cap_to_fcf(stockData: dict) -> float | None:
    market_cap = _first_number(stockData, "market_cap", "marketCap")
    fcf = _first_number(stockData, "free_cash_flow", "freeCashFlow")
    if market_cap is None or fcf in {None, 0}:
        return None
    return market_cap / fcf


def _metric_source(stockData: dict, key: str) -> str | None:
    payload = _metric_source_payload(stockData, key)
    if payload:
        value = payload.get("sourceType") or payload.get("source_type")
        if value:
            return str(value)
    return None


def _metric_source_payload(stockData: dict, *keys: str) -> dict | None:
    metric_sources = stockData.get("metric_sources")
    if isinstance(metric_sources, dict):
        for key in keys:
            raw = metric_sources.get(key)
            if isinstance(raw, dict):
                return raw
    for suffix in ("sourceType", "source_type"):
        for key in keys:
            value = stockData.get(f"{key}_{suffix}")
            if value:
                return {"sourceType": value}
    disclosures = stockData.get("disclosureMetrics")
    if isinstance(disclosures, list):
        key_set = set(keys)
        for row in disclosures:
            if not isinstance(row, dict):
                continue
            metric_key = str(row.get("metricKey") or row.get("snapshotKey") or "")
            if metric_key in key_set:
                return row
    return None


def _ratio_like(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if abs(number) > 1 and abs(number) <= 100:
        return number / 100
    return number


def _first_number(data: dict, *keys: str) -> float | None:
    for key in keys:
        value = _number(data.get(key))
        if value is not None:
            return value
    return None


def _first_number_from_stock_or_disclosure(data: dict, *keys: str) -> float | None:
    value = _first_number(data, *keys)
    if value is not None:
        return value
    disclosures = data.get("disclosureMetrics")
    if not isinstance(disclosures, list):
        return None
    key_set = set(keys)
    for row in disclosures:
        if not isinstance(row, dict):
            continue
        metric_key = str(row.get("metricKey") or row.get("snapshotKey") or "")
        if metric_key not in key_set:
            continue
        value = _number(row.get("value") if row.get("value") is not None else row.get("normalizedValue"))
        if value is not None:
            return value
    return None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value != value:
            return None
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace("$", "").replace(",", "").replace("x", "").replace("X", "")
        percent = cleaned.endswith("%")
        cleaned = cleaned.replace("%", "")
        if not cleaned or cleaned.upper() in {"N/A", "NA", "NONE", "NULL"}:
            return None
        try:
            number = float(cleaned)
        except ValueError:
            return None
        return number / 100 if percent else number
    return None


def _round_price(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _score_attr(score, name: str):
    if score is None:
        return None
    if isinstance(score, dict):
        return score.get(name)
    return getattr(score, name, None)


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


def _price_history_from_stock_data(stockData: dict | None) -> Any:
    if not stockData:
        return None
    for key in ("price_history", "priceHistory", "history"):
        if key in stockData:
            return stockData.get(key)
    return None


def _history_is_stale(stockData: dict | None) -> bool:
    if not stockData:
        return False
    status = str(
        stockData.get("historyStatus")
        or stockData.get("history_status")
        or stockData.get("priceHistoryStatus")
        or stockData.get("price_history_status")
        or ""
    ).strip()
    if status == "stale_history":
        return True
    return bool(stockData.get("stale_history") or stockData.get("staleHistory"))


def _build_combined_entry(estimate: BuyZoneEstimate, finalDecision: Any = None) -> dict[str, Any]:
    technical = estimate.technicalEntry if isinstance(estimate.technicalEntry, dict) else {}
    valuation_entry = _valuation_entry_price(estimate)
    if not _precision_field_allowed(estimate, "nextTriggerPrice", "trancheBuyHigh"):
        valuation_entry = None
    technical_pullback = _first_number_from_value(technical.get("technicalEntryPrice"))
    review_price = _first_number_from_value(technical.get("technicalReviewPrice"))
    blocked = _combined_entry_blocked(estimate, finalDecision)
    trend_break = str(technical.get("technicalState") or "") == "trend_break_review"
    fair_not_tranche = _estimate_is_fair_not_tranche(estimate)
    distance = _distance_to_valuation_entry_pct(estimate.currentPrice, valuation_entry)
    light_probe = _light_probe_price(estimate, technical, technical_pullback, fair_not_tranche, trend_break)
    deep_discount = _first_number_from_value(estimate.heavyBuyBelow) if _precision_field_allowed(estimate, "heavyBuyBelow") else None
    combined_trigger = _combined_trigger_price(valuation_entry, technical_pullback, blocked or trend_break)
    label = _combined_entry_label(
        estimate,
        finalDecision,
        blocked=blocked,
        trend_break=trend_break,
        fair_not_tranche=fair_not_tranche,
        valuation_distance_pct=distance,
    )
    reasons = _combined_entry_reasons(
        estimate,
        technical,
        finalDecision,
        valuation_entry=valuation_entry,
        technical_pullback=technical_pullback,
        light_probe=light_probe,
        deep_discount=deep_discount,
        combined_trigger=combined_trigger,
        review_price=review_price,
        blocked=blocked,
        trend_break=trend_break,
        fair_not_tranche=fair_not_tranche,
        valuation_distance_pct=distance,
    )
    return {
        "valuationEntryPrice": _round_price(valuation_entry),
        "technicalPullbackPrice": _round_price(technical_pullback),
        "lightProbePrice": _round_price(light_probe),
        "valuationDiscountPrice": _round_price(valuation_entry),
        "deepDiscountPrice": _round_price(deep_discount),
        "combinedTriggerPrice": _round_price(combined_trigger),
        "reviewPrice": _round_price(review_price),
        "entryLabel": label,
        "entryReasons": reasons,
        "entryLayers": _combined_entry_layers(
            estimate,
            valuation_entry=valuation_entry,
            technical_pullback=technical_pullback,
            light_probe=light_probe,
            deep_discount=deep_discount,
            blocked=blocked or trend_break,
        ),
    }


def _valuation_entry_price(estimate: BuyZoneEstimate) -> float | None:
    if estimate.currentZone in BLOCKED_BUY_ZONE_STATES:
        return None
    return _first_number_from_value(estimate.nextTriggerPrice) or _first_number_from_value(estimate.trancheBuyHigh)


def _precision_field_allowed(estimate: BuyZoneEstimate, *fields: str) -> bool:
    contract = estimate.precisionContract if isinstance(estimate.precisionContract, dict) else {}
    if not contract:
        return True
    allowed = {str(item) for item in contract.get("allowedPriceFields") or []}
    return any(str(field) in allowed for field in fields)


def _combined_trigger_price(
    valuation_entry: float | None,
    technical_pullback: float | None,
    review_only: bool,
) -> float | None:
    if review_only:
        return None
    if valuation_entry is None:
        return None
    if technical_pullback is None or technical_pullback <= 0:
        return valuation_entry
    return min(valuation_entry, technical_pullback)


def _combined_entry_label(
    estimate: BuyZoneEstimate,
    finalDecision: Any,
    *,
    blocked: bool,
    trend_break: bool,
    fair_not_tranche: bool,
    valuation_distance_pct: float | None,
) -> str:
    if trend_break:
        return "趋势破坏，需复核"
    if blocked:
        if fair_not_tranche and _combined_entry_wait_state(finalDecision):
            return "合理观察，未到估值买点"
        return "需复核或禁止追高，技术面不转买点"
    if fair_not_tranche:
        return "合理观察，未到估值买点"
    if valuation_distance_pct is not None and valuation_distance_pct > 15:
        return "合理观察，未到估值买点"
    if estimate.currentZone in {"tranche_buy", "heavy_buy", "below_heavy_buy"}:
        return "进入估值买点，参考技术回踩"
    return "等待估值买点"


def _combined_entry_reasons(
    estimate: BuyZoneEstimate,
    technical: dict[str, Any],
    finalDecision: Any,
    *,
    valuation_entry: float | None,
    technical_pullback: float | None,
    light_probe: float | None,
    deep_discount: float | None,
    combined_trigger: float | None,
    review_price: float | None,
    blocked: bool,
    trend_break: bool,
    fair_not_tranche: bool,
    valuation_distance_pct: float | None,
) -> list[str]:
    reasons: list[str] = []
    if valuation_entry is not None:
        reasons.append(f"估值买点参考估值折价区上沿：{valuation_entry:.2f}，不是合理观察区内的常规买点。")
    else:
        reasons.append("估值买点当前不可用，综合入场不输出入场触发价。")
    if technical_pullback is not None:
        reasons.append(f"技术回踩点：{technical_pullback:.2f}，仅作辅助，不覆盖估值买区。")
    if light_probe is not None:
        reasons.append(f"轻仓试探点：{light_probe:.2f}，仅用于高质量半导体龙头的战术观察，需 finalDecision 放行才可执行。")
    if combined_trigger is not None:
        reasons.append(f"综合触发价：{combined_trigger:.2f}，不会高于估值买点，避免技术面把价格提前变成入场信号。")
    if review_price is not None:
        reasons.append(f"技术复核线：{review_price:.2f}，跌破后优先复核趋势和支撑。")
    if trend_break:
        reasons.append("技术面跌破 MA200 或关键支撑，显示趋势破坏，需复核。")
    if blocked:
        action = _first_value(finalDecision, "finalAction", "displayCategory", default="") or estimate.action or estimate.currentZone
        reasons.append(f"最终结论或买区已阻断（{action}），技术面不能转成入场信号。")
    if fair_not_tranche:
        reasons.append("当前处于合理观察区但未进入估值折价区，只显示合理观察，未到估值买点。")
    if valuation_distance_pct is not None and valuation_distance_pct > 15:
        reasons.append(f"距离估值折价区约 {valuation_distance_pct:.1f}%，不得显示接近买点。")
    if deep_discount is not None:
        reasons.append(f"深度折价区 / 极端恐慌区：{deep_discount:.2f}，不是常规重仓计划。")
    if str(technical.get("technicalState") or "") in {"unavailable", "insufficient_data"}:
        reasons.append("本地 price_history 缺失或不可用，技术层仅保留 low / unavailable 状态。")
    return reasons[:8]


def _combined_entry_blocked(estimate: BuyZoneEstimate, finalDecision: Any = None) -> bool:
    if estimate.currentZone in BLOCKED_BUY_ZONE_STATES or estimate.currentZone == "no_chase":
        return True
    if str(estimate.confidence or "").lower() == "low" or estimate.isValid is False:
        return True
    if finalDecision is None:
        return False
    lane = str(_first_value(finalDecision, "decisionLane", default="") or "").lower()
    action = str(_first_value(finalDecision, "finalAction", default="") or "")
    display = str(_first_value(finalDecision, "displayCategory", default="") or "")
    data_confidence = str(_first_value(finalDecision, "dataConfidence", default="") or "").lower()
    current_add = _first_number_from_value(_first_value(finalDecision, "currentAddLimitPercent", default=None))
    actionable = _first_value(finalDecision, "isActionable", default=None)
    if data_confidence == "low" or lane in {"blocked", "review", "wait"}:
        return True
    if isinstance(actionable, bool) and not actionable:
        return True
    if current_add is not None and current_add <= 0:
        return True
    return any(token in f"{action} {display}" for token in ["禁止追高", "需复核", "数据不足", "待复核", "等回踩", "只观察"])


def _combined_entry_wait_state(finalDecision: Any = None) -> bool:
    if finalDecision is None:
        return False
    lane = str(_first_value(finalDecision, "decisionLane", default="") or "").lower()
    action = str(_first_value(finalDecision, "finalAction", "displayCategory", default="") or "")
    return lane == "wait" or any(token in action for token in {"等回踩", "只观察"})


def _light_probe_price(
    estimate: BuyZoneEstimate,
    technical: dict[str, Any],
    technical_pullback: float | None,
    fair_not_tranche: bool,
    trend_break: bool,
) -> float | None:
    if trend_break or not fair_not_tranche:
        return None
    if str(estimate.modelType or "").upper() != "SEMICONDUCTOR":
        return None
    if str(estimate.confidence or "").lower() == "low" or estimate.isValid is False:
        return None
    if technical_pullback is None or technical_pullback <= 0:
        return None
    state = str(technical.get("technicalState") or "")
    if state not in {"healthy_pullback", "tactical_observation", "neutral"}:
        return None
    return technical_pullback


def _combined_entry_layers(
    estimate: BuyZoneEstimate,
    *,
    valuation_entry: float | None,
    technical_pullback: float | None,
    light_probe: float | None,
    deep_discount: float | None,
    blocked: bool,
) -> list[dict[str, Any]]:
    fair_range = None
    if estimate.fairValueLow is not None and estimate.fairValueHigh is not None:
        fair_range = [_round_price(estimate.fairValueLow), _round_price(estimate.fairValueHigh)]
    return [
        {"key": "fair_observation", "label": "合理观察区", "range": fair_range, "price": None, "isActionable": False},
        {"key": "technical_pullback", "label": "技术回踩观察区", "price": _round_price(technical_pullback), "isActionable": False},
        {"key": "light_probe", "label": "轻仓试探区", "price": _round_price(light_probe), "isActionable": False if blocked else light_probe is not None},
        {"key": "valuation_discount", "label": "估值折价区", "price": _round_price(valuation_entry), "isActionable": False if blocked else valuation_entry is not None},
        {"key": "deep_discount", "label": "深度折价区 / 极端恐慌区", "price": _round_price(deep_discount), "isActionable": False if blocked else deep_discount is not None},
    ]


def _estimate_is_fair_not_tranche(estimate: BuyZoneEstimate) -> bool:
    price = _first_number_from_value(estimate.currentPrice)
    if price is None or estimate.currentZone != "fair_observation":
        return False
    in_fair = _between(price, estimate.fairValueLow, estimate.fairValueHigh)
    in_tranche = _between(price, estimate.trancheBuyLow, estimate.trancheBuyHigh)
    return in_fair and not in_tranche


def _distance_to_valuation_entry_pct(current_price: float | None, valuation_entry: float | None) -> float | None:
    price = _first_number_from_value(current_price)
    if price is None or price <= 0 or valuation_entry is None or valuation_entry <= 0 or price <= valuation_entry:
        return None
    return round((price - valuation_entry) / price * 100, 1)


def _between(value: float, low: float | None, high: float | None) -> bool:
    lower = _first_number_from_value(low)
    upper = _first_number_from_value(high)
    return lower is not None and upper is not None and lower <= value <= upper


def _first_number_from_value(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number == number else None


def _insufficient(symbol: str, model: str, price: float | None, reasons: list[str], warnings: list[str]) -> BuyZoneEstimate:
    estimate = BuyZoneEstimate(
        symbol=symbol.upper(),
        modelType=str(model),
        currentPrice=_round_price(price),
        noChaseAbove=_round_price(price * 1.08) if price else None,
        fairValueLow=_round_price(price * 0.92) if price else None,
        fairValueHigh=_round_price(price * 1.03) if price else None,
        trancheBuyLow=_round_price(price * 0.78) if price else None,
        trancheBuyHigh=_round_price(price * 0.92) if price else None,
        heavyBuyBelow=_round_price(price * 0.72) if price else None,
        currentZone="data_insufficient",
        confidence="low",
        method="technical_proxy",
        inputsUsed=[],
        keyReasons=reasons,
        warnings=warnings,
        createdAt=datetime.now(timezone.utc).isoformat(),
        technicalEntry=build_technical_entry_model(symbol, price, None, None, None).to_dict(),
        action="买区异常，需复核",
        nextTriggerPrice=None,
        nextBuyLabel="买区异常，需复核",
        isValid=False,
        validationErrors=["当前价格缺失或无效"] if price is None or price <= 0 else ["估值输入不足"],
    )
    return attach_combined_entry(_with_explainability(_without_actionable_prices(estimate), {}, None))
