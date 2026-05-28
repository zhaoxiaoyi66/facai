from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from statistics import median
from typing import Any


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
}

BLOCKED_BUY_ZONE_STATES = {
    "invalid_zone",
    "invalid_manual_override",
    "data_insufficient",
    "low_confidence_zone",
    "unsupported_buy_zone_model",
}

CASHFLOW_SIGNAL_KEYS = {"price_to_fcf", "fcf_yield"}


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
    model = modelType or _score_attr(scoringResult, "scoring_model") or _score_attr(scoringResult, "modelType") or stockData.get("modelType") or "GENERIC"
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
    if model_key not in SUPPORTED_BUY_ZONE_MODELS:
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

    targets = _model_targets(str(model))
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
    return validate_buy_zone_estimate(estimate, stockData, scoringResult)


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
    )
    return validate_buy_zone_estimate(overridden, {}, None)


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
        "revenue_growth": _ratio_like(_first_number(stockData, "revenue_growth", "revenueGrowth")),
        "fcf_margin": direct_margin,
        "implied_fcf_margin": implied_fcf_margin(stockData),
        "drawdown": drawdown,
        "rsi14": _first_number(stockData, "rsi14", "RSI14"),
        "return20d": _ratio_like(_first_number(stockData, "gain_20d_pct", "return20d", "return20D")),
        "return60d": _ratio_like(_first_number(stockData, "gain_60d_pct", "return60d", "return60D")),
        "net_debt_to_ebitda": _first_number(stockData, "net_debt_to_ebitda", "netDebtToEbitda"),
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
    )
    return _with_explainability(estimate, {}, None)


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


def _with_explainability(buyZone: BuyZoneEstimate, stockData: dict | None = None, scoringResult=None) -> BuyZoneEstimate:
    return replace(
        buyZone,
        explainability=_build_explainability(buyZone, stockData or {}, scoringResult),
    )


def _build_explainability(buyZone: BuyZoneEstimate, stockData: dict, scoringResult=None) -> dict[str, Any]:
    zone = str(buyZone.currentZone or "")
    warnings = list(dict.fromkeys(str(item) for item in (buyZone.warnings or []) if item))
    validation_errors = list(dict.fromkeys(str(item) for item in (buyZone.validationErrors or []) if item))
    guardrail_reasons = _guardrail_reasons(zone, validation_errors, warnings)
    confidence_reasons = _confidence_reasons(buyZone, stockData, scoringResult, warnings, validation_errors)
    missing_inputs = _missing_inputs(zone, stockData, validation_errors)
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
            "数据置信度不足，暂不输出可执行买点。",
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
        f"系统基于 {driver_text} 生成买区，当前状态为 {zone or 'fair_observation'}。",
    )


def _guardrail_reasons(zone: str, validation_errors: list[str], warnings: list[str]) -> list[str]:
    reasons: list[str] = []
    if zone == "unsupported_buy_zone_model":
        reasons.append("当前板块暂无专属买区模型，禁用精确买点")
    if zone in {"invalid_zone", "invalid_manual_override"}:
        reasons.append("当前估值区间异常，系统暂不输出买点，需复核输入")
    if zone == "low_confidence_zone":
        reasons.append("数据置信度不足，暂不输出可执行买点")
    if zone == "data_insufficient":
        reasons.append("关键买区输入不足，暂不输出精确买点")
    if zone == "no_chase":
        reasons.append("当前价处于禁止追高区，不输出新增建议")
    for item in [*validation_errors, *warnings]:
        if item in {"buy_zone_model_not_supported", "data_confidence_low", "missing_power_generation_core_inputs"}:
            continue
        if "异常" in item or "缺" in item or "不足" in item or "unsupported" in item:
            reasons.append(item)
    return list(dict.fromkeys(reasons))


def _missing_inputs(zone: str, stockData: dict, validation_errors: list[str]) -> list[str]:
    missing: list[str] = []
    if "buy_zone_model_not_supported" in validation_errors or zone == "unsupported_buy_zone_model":
        missing.append("专属买区模型")
    if "missing_power_generation_core_inputs" in validation_errors:
        missing.extend(["adjusted EBITDA", "adjusted FCF before growth"])
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
        return _round_price(trigger), "下一买入触发价", warnings
    if currentZone == "tranche_buy":
        return None, "已进入可分批区", warnings
    if currentZone == "heavy_buy":
        return None, "已低于重仓区", warnings
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


def _reason_texts(model: str, metrics: dict[str, float | None], current_zone: str, inputs: list[str], confidence: str) -> list[str]:
    reasons = [f"使用 {', '.join(dict.fromkeys(inputs))} 合成系统买区。"]
    if metrics.get("fcf_yield") is not None:
        reasons.append(f"FCF收益率约 {metrics['fcf_yield'] * 100:.1f}%，用于估值锚。")
    if metrics.get("price_to_fcf") is not None:
        reasons.append(f"P/FCF 约 {metrics['price_to_fcf']:.1f}x，作为主要现金流估值输入。")
    if metrics.get("drawdown") is not None:
        reasons.append(f"距高点回撤约 {metrics['drawdown'] * 100:.1f}%，用于调节当前区间。")
    if current_zone == "no_chase" or _is_overheated(metrics):
        reasons.append("短线或估值偏热时不生成重仓买入信号。")
    if confidence != "high":
        reasons.append("部分行业关键输入缺失，系统建议仅作为初版买区。")
    if str(model).upper() == "POWER_GENERATION":
        reasons.append("电力股优先参考 FCF、EV/EBITDA、杠杆和回撤，不按 SaaS 模型硬套。")
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
        return "已低于重仓区"
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
    metric_sources = stockData.get("metric_sources")
    if isinstance(metric_sources, dict):
        raw = metric_sources.get(key)
        if isinstance(raw, dict):
            value = raw.get("sourceType") or raw.get("source_type")
            if value:
                return str(value)
    for suffix in ("sourceType", "source_type"):
        value = stockData.get(f"{key}_{suffix}")
        if value:
            return str(value)
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
        action="买区异常，需复核",
        nextTriggerPrice=None,
        nextBuyLabel="买区异常，需复核",
        isValid=False,
        validationErrors=["当前价格缺失或无效"] if price is None or price <= 0 else ["估值输入不足"],
    )
    return _with_explainability(_without_actionable_prices(estimate), {}, None)
