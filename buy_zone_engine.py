from __future__ import annotations

from dataclasses import asdict, dataclass
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
        return _insufficient(symbol, model, price, ["缺少当前价格，无法生成价格区间。"], warnings)

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
    tranche_buy_high = fair_value_low
    tranche_buy_low = _round_price(max(heavy_price * 1.06, heavy_price + (tranche_buy_high - heavy_price) * 0.25))
    heavy_buy_below = _round_price(heavy_price)

    if tranche_buy_low >= tranche_buy_high:
        tranche_buy_low = _round_price(max(heavy_buy_below, tranche_buy_high * 0.92))
    if fair_value_low >= fair_value_high:
        fair_value_low = _round_price(fair_value_high * 0.9)

    current_zone = _current_zone(
        price,
        no_chase_above,
        fair_value_low,
        fair_value_high,
        tranche_buy_low,
        tranche_buy_high,
        heavy_buy_below,
    )
    confidence = _confidence(inputs, method, model, stockData, warnings)
    method = "fcf_yield" if any("FCF收益率" in item for item in inputs) and len(inputs) == 1 else method
    if len(inputs) >= 2 and method != "technical_proxy":
        method = "blended"

    reasons.extend(_reason_texts(model, metrics, current_zone, inputs, confidence))
    return BuyZoneEstimate(
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
    return BuyZoneEstimate(
        symbol=estimate.symbol,
        modelType=estimate.modelType,
        currentPrice=estimate.currentPrice,
        noChaseAbove=_number(plan.get("no_chase_above")) or estimate.noChaseAbove,
        fairValueLow=_number(plan.get("fair_value_low")) or estimate.fairValueLow,
        fairValueHigh=_number(plan.get("fair_value_high")) or estimate.fairValueHigh,
        trancheBuyLow=_number(plan.get("tranche_buy_low")) or estimate.trancheBuyLow,
        trancheBuyHigh=_number(plan.get("tranche_buy_high")) or estimate.trancheBuyHigh,
        heavyBuyBelow=_number(plan.get("heavy_buy_below")) or estimate.heavyBuyBelow,
        currentZone=estimate.currentZone,
        confidence=estimate.confidence,
        method="manual_override",
        inputsUsed=estimate.inputsUsed,
        keyReasons=["当前使用手动买区，系统建议仍保留供对比。", *estimate.keyReasons],
        warnings=estimate.warnings,
        createdAt=estimate.createdAt,
    )


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
    for key, target in targets.items():
        current = metrics.get(key)
        if current is None or current <= 0:
            continue
        weight = target.get("weight", 1)
        inputs.append(labels.get(key, key))
        for zone in ("fair", "tranche", "heavy"):
            target_value = target[zone]
            if key == "fcf_yield":
                implied_price = price * current / target_value
            else:
                implied_price = price * target_value / current
            if implied_price > 0:
                candidates[zone].append((implied_price, weight))
    return candidates


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


def _current_zone(price: float, no_chase: float, fair_low: float, fair_high: float, tranche_low: float, tranche_high: float, heavy_below: float) -> str:
    if price >= no_chase:
        return "no_chase"
    if fair_low <= price <= fair_high:
        return "fair_observation"
    if tranche_low <= price <= tranche_high:
        return "tranche_buy"
    if heavy_below <= price < tranche_low:
        return "heavy_buy"
    if price < heavy_below:
        return "below_heavy_buy"
    return "fair_observation" if price < no_chase else "no_chase"


def _confidence(inputs: list[str], method: str, model: str, stockData: dict, warnings: list[str]) -> str:
    if method == "technical_proxy":
        return "low"
    distinct = set(inputs)
    if str(model).upper() == "POWER_GENERATION" and not any(stockData.get(key) is not None for key in ("adjustedEbitda", "manualAdjustedEbitda", "adjustedFcfBeforeGrowth", "manualAdjustedFcfBeforeGrowth")):
        warnings.append("电力模型缺少调整后 EBITDA / 增长投资前 FCF，使用代理估值，置信度不高于中。")
        return "medium" if len(distinct) >= 2 else "low"
    if len(distinct) >= 3:
        return "high"
    if len(distinct) >= 2:
        return "medium"
    return "low"


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
    return BuyZoneEstimate(
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
    )
