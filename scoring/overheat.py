from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OverheatResult:
    score: float
    status: str
    action: str
    recommendation: str
    reasons: list[str]


def calculate_overheat_score(
    snapshot: dict,
    technicals: dict,
    valuation_status: str,
    model_type: str,
    quality_rating: str,
) -> OverheatResult:
    score = 0.0
    reasons: list[str] = []

    rsi = _number(technicals.get("rsi14"))
    return_20d = _number(
        technicals.get("return20d"),
        technicals.get("return_20d"),
        technicals.get("gain_20d_pct"),
    )
    return_60d = _number(
        technicals.get("return60d"),
        technicals.get("return_60d"),
        technicals.get("gain_60d_pct"),
    )
    drawdown = _number(
        technicals.get("distanceTo52WeekHigh"),
        technicals.get("distance_to_52w_high"),
        technicals.get("drawdown_from_high_pct"),
    )
    daily_return = _number(
        technicals.get("daily_return_pct"),
        technicals.get("return_1d_pct"),
        technicals.get("return1d"),
    )
    price_vs_ema20 = _price_vs_ema(technicals, "ema20", "priceVsEMA20")
    price_vs_ema50 = _price_vs_ema(technicals, "ema50", "priceVsEMA50")
    ps_ratio = _number(snapshot.get("psRatio"), snapshot.get("price_to_sales"), snapshot.get("enterprise_to_revenue"))
    forward_pe = _number(snapshot.get("forwardPE"), snapshot.get("forward_pe"))
    ev_fcf = _number(snapshot.get("evFcf"), snapshot.get("ev_to_fcf"), snapshot.get("price_to_fcf"))
    fcf_yield = _number(snapshot.get("fcfYield"), snapshot.get("free_cash_flow_yield"))

    if rsi is not None:
        if rsi > 75:
            score += 24
            reasons.append("RSI14 仍处在极高区间")
        elif rsi > 70:
            score += 18
            reasons.append("RSI14 高于 70")
        elif rsi > 65:
            score += 10
            reasons.append("RSI14 仍偏热")

    if return_20d is not None:
        if return_20d > 20:
            score += 22
            reasons.append("20日涨幅仍未完全消化")
        elif return_20d > 12:
            score += 12
            reasons.append("20日涨幅仍偏高")

    if return_60d is not None:
        if return_60d > 35:
            score += 18
            reasons.append("60日涨幅较大")
        elif return_60d > 20:
            score += 10
            reasons.append("60日涨幅仍偏高")

    if drawdown is not None:
        if drawdown >= -5:
            score += 20
            reasons.append("距52周高点仍较近")
        elif drawdown > -8:
            score += 15
            reasons.append("距52周高点不到 8%")
        elif drawdown > -12:
            score += 8
            reasons.append("回撤还不够充分")

    valuation_heat = _valuation_heat_score(valuation_status, ps_ratio, forward_pe, ev_fcf, fcf_yield, model_type)
    score += valuation_heat
    if valuation_heat >= 18:
        reasons.append("估值处于高温区")
    elif valuation_heat >= 10:
        reasons.append("估值偏高")

    if fcf_yield is not None and fcf_yield < 0.02 and valuation_heat >= 10:
        score += 8
        reasons.append("FCF收益率偏低且估值仍在扩张")

    if price_vs_ema20 is not None and price_vs_ema20 > 8:
        score += 8
        reasons.append("价格仍明显高于 EMA20")
    if price_vs_ema50 is not None and price_vs_ema50 > 12:
        score += 8
        reasons.append("价格仍明显高于 EMA50")

    if model_type in {"SEMICONDUCTOR", "AI_INFRA_HIGH_RISK", "SEMICONDUCTOR_CYCLICAL"} and valuation_heat >= 10:
        score += 5
        reasons.append("所属赛道容易受情绪和估值波动放大")

    if drawdown is not None and valuation_heat >= 10:
        if drawdown > -8:
            score = max(score, 60)
        elif drawdown > -12:
            score = max(score, 40)

    cooling_credit = _cooling_credit(rsi, drawdown, return_20d, daily_return, price_vs_ema20)
    if cooling_credit > 0:
        score -= cooling_credit
        reasons.append("短线开始冷却，但尚未自动进入买点")

    if drawdown is not None and valuation_heat >= 10:
        if drawdown > -8:
            score = max(score, 60)
        elif drawdown > -12:
            score = max(score, 40)

    if daily_return is not None and daily_return < 0 and score >= 35:
        reasons.insert(0, "今日下跌只是短期冷却，不等于进入击球区")

    if quality_rating.startswith(("A", "B+")) and score < 80:
        score -= 4

    if drawdown is not None and valuation_heat >= 10:
        if drawdown > -8:
            score = max(score, 60)
        elif drawdown > -12:
            score = max(score, 40)

    score = max(0.0, min(100.0, score))
    if (
        daily_return is not None
        and daily_return < 0
        and (rsi is None or rsi < 70)
        and (return_20d is None or return_20d <= 20)
    ):
        score = min(score, 79.0)
    score = round(score, 1)
    status, action, recommendation = _status_for_score(score)

    if not reasons:
        reasons.append("未触发明显追高风险")

    return OverheatResult(
        score=score,
        status=status,
        action=action,
        recommendation=recommendation,
        reasons=_dedupe(reasons)[:5],
    )


def _valuation_heat_score(
    valuation_status: str,
    ps_ratio: float | None,
    forward_pe: float | None,
    ev_fcf: float | None,
    fcf_yield: float | None,
    model_type: str,
) -> float:
    text = str(valuation_status or "").lower()
    score = 0.0
    if any(token in text for token in ["expensive", "overheated", "极贵", "偏贵", "禁止追高"]):
        score += 18
    elif any(token in text for token in ["高估", "偏热"]):
        score += 12

    ps_hot = 20 if model_type in {"SAAS_SOFTWARE", "SEMICONDUCTOR", "AI_INFRA_HIGH_RISK"} else 12
    if ps_ratio is not None:
        if ps_ratio > ps_hot:
            score += 12
        elif ps_ratio > ps_hot * 0.65:
            score += 7

    pe_hot = 55 if model_type in {"SEMICONDUCTOR", "MEDICAL_DEVICE", "PHARMA"} else 45
    if forward_pe is not None:
        if forward_pe > pe_hot:
            score += 10
        elif forward_pe > pe_hot * 0.7:
            score += 5

    if ev_fcf is not None:
        if ev_fcf > 55:
            score += 10
        elif ev_fcf > 35:
            score += 5

    if fcf_yield is not None:
        if 0 <= fcf_yield < 0.015:
            score += 8
        elif fcf_yield < 0.03:
            score += 4

    return min(score, 30.0)


def _cooling_credit(
    rsi: float | None,
    drawdown: float | None,
    return_20d: float | None,
    daily_return: float | None,
    price_vs_ema20: float | None,
) -> float:
    credit = 0.0
    if rsi is not None and rsi < 60:
        credit += 8
    if drawdown is not None and drawdown <= -12:
        credit += 10
    if return_20d is not None and return_20d <= 8:
        credit += 7
    if daily_return is not None and daily_return < 0:
        credit += 3
    if price_vs_ema20 is not None and price_vs_ema20 < 0:
        credit += 4
    return min(credit, 24.0)


def _status_for_score(score: float) -> tuple[str, str, str]:
    if score >= 80:
        return "极度过热", "禁止追高", "等待更深回调或财报后确认"
    if score >= 60:
        return "偏热", "只观察", "等待更深回调或财报后确认"
    if score >= 40:
        return "开始冷却", "等回踩", "等待 RSI、估值和回撤共同确认"
    if score >= 20:
        return "回调较充分", "可小仓观察", "只适合小仓观察，仍需确认基本面"
    return "非过热", "正常评估", "回到行业模型正常评估"


def _price_vs_ema(technicals: dict, ema_key: str, explicit_key: str) -> float | None:
    explicit = _number(technicals.get(explicit_key), technicals.get(_camel_to_snake(explicit_key)))
    if explicit is not None:
        return explicit
    price = _number(technicals.get("price"))
    ema = _number(technicals.get(ema_key))
    if price is None or ema in {None, 0}:
        return None
    return (price / ema - 1.0) * 100.0


def _number(*values: object) -> float | None:
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


def _camel_to_snake(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper():
            chars.append("_")
            chars.append(char.lower())
        else:
            chars.append(char)
    return "".join(chars).lstrip("_")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
