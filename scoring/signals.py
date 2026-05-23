from __future__ import annotations

from dataclasses import dataclass

from scoring.overheat import OverheatResult
from scoring.risk_flags import RiskFlag


ANTI_FOMO_MESSAGE = (
    "不要追高。动量很强，但除非基本面已经被实质性上修，否则风险回报不佳。"
)
LEFT_SIDE_OPPORTUNITY_MESSAGE = "潜在左侧机会。只考虑分批买入。"


@dataclass(frozen=True)
class TradingSignal:
    kind: str
    message: str
    reasons: list[str]


def build_trading_signals(
    technicals: dict,
    valuation_score: float,
    technical_score: float,
    risk_flags: list[RiskFlag],
    overheat: OverheatResult | None = None,
) -> list[TradingSignal]:
    signals: list[TradingSignal] = []
    anti_fomo_reasons = overheat.reasons if overheat is not None and overheat.score >= 40 else anti_fomo_reasons_for(technicals, valuation_score, technical_score)
    if anti_fomo_reasons:
        message = f"追高状态：{overheat.status} / {overheat.action}" if overheat is not None else ANTI_FOMO_MESSAGE
        signals.append(TradingSignal("anti_fomo", message, anti_fomo_reasons))

    left_side_reasons = left_side_opportunity_reasons_for(technicals, valuation_score, risk_flags)
    if left_side_reasons:
        signals.append(TradingSignal("left_side_opportunity", LEFT_SIDE_OPPORTUNITY_MESSAGE, left_side_reasons))

    return signals


def anti_fomo_reasons_for(technicals: dict, valuation_score: float, technical_score: float) -> list[str]:
    reasons: list[str] = []
    valuation_score_pct = normalize_valuation_score(valuation_score)

    rsi = technicals.get("rsi14")
    if rsi is not None and rsi > 70:
        reasons.append("RSI14 高于 70。")

    drawdown = technicals.get("drawdown_from_high_pct")
    if drawdown is not None and drawdown >= -5:
        reasons.append("价格距离 52 周高点不到 5%。")

    pct_above_ema200 = technicals.get("pct_above_ema200")
    if pct_above_ema200 is not None and pct_above_ema200 > 25:
        reasons.append("价格高于 EMA200 超过 25%。")

    gain_20d = technicals.get("gain_20d_pct")
    if gain_20d is not None and gain_20d > 20:
        reasons.append("过去 20 个交易日涨幅超过 20%。")

    if valuation_score_pct < 40 and is_strong_technical_momentum(technicals, technical_score):
        reasons.append("估值分低于 40，但技术动量很强。")

    return reasons


def left_side_opportunity_reasons_for(
    technicals: dict,
    valuation_score: float,
    risk_flags: list[RiskFlag],
) -> list[str]:
    valuation_score_pct = normalize_valuation_score(valuation_score)
    if has_major_risk_flags(risk_flags):
        return []

    drawdown = technicals.get("drawdown_from_high_pct")
    rsi = technicals.get("rsi14")

    if not (drawdown is not None and drawdown <= -25):
        return []
    if not (rsi is not None and rsi < 40):
        return []
    if not is_near_or_below_ema200(technicals):
        return []
    if not (valuation_score_pct > 65):
        return []

    return [
        "距离 52 周高点回撤超过 25%。",
        "RSI14 低于 40。",
        "价格接近或低于 EMA200。",
        "估值分高于 65。",
        "没有重大风险旗标。",
    ]


def normalize_valuation_score(valuation_score: float, max_points: float = 25.0) -> float:
    if max_points <= 0:
        return 0.0
    return max(0.0, min(100.0, valuation_score / max_points * 100.0))


def is_strong_technical_momentum(technicals: dict, technical_score: float) -> bool:
    if technical_score >= 7:
        return True

    checks = 0
    price = technicals.get("price")
    ema50 = technicals.get("ema50")
    ema200 = technicals.get("ema200")
    rsi = technicals.get("rsi14")
    gain_20d = technicals.get("gain_20d_pct")

    if price is not None and ema50 is not None and price > ema50:
        checks += 1
    if price is not None and ema200 is not None and price > ema200:
        checks += 1
    if rsi is not None and rsi > 60:
        checks += 1
    if gain_20d is not None and gain_20d > 10:
        checks += 1
    return checks >= 3


def is_near_or_below_ema200(technicals: dict, near_pct: float = 5.0) -> bool:
    price = technicals.get("price")
    ema200 = technicals.get("ema200")
    if price is None or ema200 is None or ema200 <= 0:
        return False
    return price <= ema200 * (1 + near_pct / 100)


def has_major_risk_flags(risk_flags: list[RiskFlag]) -> bool:
    return any(flag.severity == "high" for flag in risk_flags)
