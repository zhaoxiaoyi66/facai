from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from indicators.technicals import calculate_rsi14


SHORT_TERM_EXTENDED_STATE = "short_term_extended"
HEALTHY_PULLBACK_STATE = "healthy_pullback"
TREND_BREAK_REVIEW_STATE = "trend_break_review"
TACTICAL_OBSERVATION_STATE = "tactical_observation"
NEUTRAL_STATE = "neutral"
INSUFFICIENT_DATA_STATE = "insufficient_data"
UNAVAILABLE_STATE = "unavailable"


@dataclass(frozen=True)
class TechnicalEntryModel:
    technicalTrend: str
    technicalState: str
    supportLevels: list[dict[str, Any]]
    resistanceLevels: list[dict[str, Any]]
    ma20: float | None
    ma50: float | None
    ma100: float | None
    ma200: float | None
    rsi14: float | None
    atr14: float | None
    distanceToMA50: float | None
    distanceToMA200: float | None
    technicalEntryPrice: float | None
    technicalNoChaseAbove: float | None
    technicalReviewPrice: float | None
    technicalConfidence: str
    technicalReasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_technical_entry_model(
    symbol: str,
    currentPrice: float | None,
    price_history: Any,
    buyZone: Any = None,
    finalDecision: Any = None,
) -> TechnicalEntryModel:
    history = _prepare_history(price_history)
    current_price = _first_number(currentPrice)
    if current_price is None and not history.empty:
        current_price = _first_number(history["close"].iloc[-1])

    if current_price is None or current_price <= 0 or history.empty or len(history) < 30:
        return _empty_result(
            UNAVAILABLE_STATE,
            ["本地 price_history 或当前价不足，技术入场模型暂不生成触发价。"],
        )

    close = pd.to_numeric(history["close"], errors="coerce")
    high = pd.to_numeric(history.get("high", close), errors="coerce").fillna(close)
    low = pd.to_numeric(history.get("low", close), errors="coerce").fillna(close)
    ma20 = _last_rolling_mean(close, 20, 10)
    ma50 = _last_rolling_mean(close, 50, 20)
    ma100 = _last_rolling_mean(close, 100, 40)
    ma200 = _last_rolling_mean(close, 200, 80)
    rsi14 = _round_optional(_last_value(calculate_rsi14(close)))
    atr14 = _round_optional(_last_value(_atr14(high, low, close)))
    distance_to_ma50 = _distance_pct(current_price, ma50)
    distance_to_ma200 = _distance_pct(current_price, ma200)

    supports = _support_levels(current_price, close, high, low, ma20, ma50, ma100, ma200, atr14)
    resistances = _resistance_levels(current_price, close, high, ma20, ma50, ma100, ma200, atr14)
    trend = _technical_trend(current_price, ma50, ma200)
    state = _technical_state(current_price, ma20, ma50, ma200, rsi14, supports, buyZone)
    final_decision_blocked = _final_decision_blocks_action(finalDecision)
    technical_entry = _technical_entry_price(current_price, supports, ma50, atr14)
    technical_no_chase = _technical_no_chase_above(current_price, resistances, atr14, state)
    technical_review = _technical_review_price(current_price, supports, ma200)
    confidence = _technical_confidence(len(history), current_price, ma50, ma200, rsi14, final_decision_blocked)
    reasons = _technical_reasons(
        symbol=symbol,
        state=state,
        trend=trend,
        current_price=current_price,
        ma20=ma20,
        ma50=ma50,
        ma200=ma200,
        rsi14=rsi14,
        atr14=atr14,
        entry_price=technical_entry,
        review_price=technical_review,
        buy_zone=buyZone,
        final_decision=finalDecision,
        final_decision_blocked=final_decision_blocked,
    )

    return TechnicalEntryModel(
        technicalTrend=trend,
        technicalState=state,
        supportLevels=supports,
        resistanceLevels=resistances,
        ma20=ma20,
        ma50=ma50,
        ma100=ma100,
        ma200=ma200,
        rsi14=rsi14,
        atr14=atr14,
        distanceToMA50=distance_to_ma50,
        distanceToMA200=distance_to_ma200,
        technicalEntryPrice=technical_entry,
        technicalNoChaseAbove=technical_no_chase,
        technicalReviewPrice=technical_review,
        technicalConfidence=confidence,
        technicalReasons=reasons,
    )


def generate_technical_entry_model(
    symbol: str,
    currentPrice: float | None,
    price_history: Any,
    buyZone: Any = None,
    finalDecision: Any = None,
) -> TechnicalEntryModel:
    return build_technical_entry_model(symbol, currentPrice, price_history, buyZone, finalDecision)


def _prepare_history(price_history: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(price_history, pd.DataFrame) or price_history.empty:
        return pd.DataFrame()
    history = price_history.copy()
    if "date" in history.columns:
        history = history.sort_values("date")
    if "close" not in history.columns:
        return pd.DataFrame()
    history["close"] = pd.to_numeric(history["close"], errors="coerce")
    if "high" not in history.columns:
        history["high"] = history["close"]
    if "low" not in history.columns:
        history["low"] = history["close"]
    history["high"] = pd.to_numeric(history["high"], errors="coerce").fillna(history["close"])
    history["low"] = pd.to_numeric(history["low"], errors="coerce").fillna(history["close"])
    return history.dropna(subset=["close"])


def _empty_result(state: str, reasons: list[str]) -> TechnicalEntryModel:
    return TechnicalEntryModel(
        technicalTrend="unavailable" if state == UNAVAILABLE_STATE else "insufficient_data",
        technicalState=state,
        supportLevels=[],
        resistanceLevels=[],
        ma20=None,
        ma50=None,
        ma100=None,
        ma200=None,
        rsi14=None,
        atr14=None,
        distanceToMA50=None,
        distanceToMA200=None,
        technicalEntryPrice=None,
        technicalNoChaseAbove=None,
        technicalReviewPrice=None,
        technicalConfidence="low",
        technicalReasons=reasons,
    )


def _support_levels(
    current_price: float,
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    ma20: float | None,
    ma50: float | None,
    ma100: float | None,
    ma200: float | None,
    atr14: float | None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    _add_level(candidates, "MA20", ma20, "moving_average", current_price, support=True)
    _add_level(candidates, "MA50", ma50, "moving_average", current_price, support=True)
    _add_level(candidates, "MA100", ma100, "moving_average", current_price, support=True)
    _add_level(candidates, "MA200", ma200, "moving_average", current_price, support=True)
    _add_level(candidates, "20日低点", _rolling_low(low, 20), "recent_support", current_price, support=True)
    _add_level(candidates, "50日低点", _rolling_low(low, 50), "recent_support", current_price, support=True)
    _add_level(candidates, "前高回踩", _prior_high(high), "prior_high_pullback", current_price, support=True)
    if atr14 is not None:
        _add_level(candidates, "ATR回撤位", current_price - atr14 * 1.5, "atr_pullback", current_price, support=True)
    return _rank_levels(candidates, current_price, lower_first=False)


def _resistance_levels(
    current_price: float,
    close: pd.Series,
    high: pd.Series,
    ma20: float | None,
    ma50: float | None,
    ma100: float | None,
    ma200: float | None,
    atr14: float | None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    _add_level(candidates, "MA20", ma20, "moving_average", current_price, support=False)
    _add_level(candidates, "MA50", ma50, "moving_average", current_price, support=False)
    _add_level(candidates, "MA100", ma100, "moving_average", current_price, support=False)
    _add_level(candidates, "MA200", ma200, "moving_average", current_price, support=False)
    _add_level(candidates, "20日高点", _rolling_high(high, 20), "recent_resistance", current_price, support=False)
    _add_level(candidates, "50日高点", _rolling_high(high, 50), "recent_resistance", current_price, support=False)
    if atr14 is not None:
        _add_level(candidates, "ATR上沿", current_price + atr14 * 1.5, "atr_extension", current_price, support=False)
    return _rank_levels(candidates, current_price, lower_first=True)


def _technical_trend(current_price: float, ma50: float | None, ma200: float | None) -> str:
    if ma50 is None or ma200 is None:
        return "insufficient_data"
    if current_price < ma200 * 0.98:
        return "broken_trend"
    if ma50 >= ma200 and current_price >= ma50:
        return "uptrend"
    if ma50 >= ma200 and current_price < ma50:
        return "pullback_in_uptrend"
    if ma50 < ma200 and current_price < ma200:
        return "downtrend"
    return "sideways"


def _technical_state(
    current_price: float,
    ma20: float | None,
    ma50: float | None,
    ma200: float | None,
    rsi14: float | None,
    supports: list[dict[str, Any]],
    buy_zone: Any,
) -> str:
    nearest_support = _nearest_support_price(supports)
    if ma200 is not None and current_price < ma200 * 0.98:
        return TREND_BREAK_REVIEW_STATE
    if nearest_support is not None and current_price < nearest_support * 0.97:
        return TREND_BREAK_REVIEW_STATE
    if _is_short_term_extended(current_price, ma20, ma50, rsi14):
        return SHORT_TERM_EXTENDED_STATE
    if _buy_zone_is_fair_but_not_tranche(current_price, buy_zone):
        return TACTICAL_OBSERVATION_STATE
    if _is_healthy_pullback(current_price, ma20, ma50, nearest_support, rsi14, ma200):
        return HEALTHY_PULLBACK_STATE
    return NEUTRAL_STATE


def _technical_entry_price(
    current_price: float,
    supports: list[dict[str, Any]],
    ma50: float | None,
    atr14: float | None,
) -> float | None:
    ordered_sources = ("recent_support", "moving_average", "prior_high_pullback", "atr_pullback")
    for source in ordered_sources:
        for level in supports:
            if level.get("source") != source:
                continue
            price = _first_number(level.get("price"))
            if price is not None and 0 < price <= current_price * 1.02:
                return _round_price(price)
        if source == "moving_average" and ma50 is not None and 0 < ma50 <= current_price * 1.02:
            return _round_price(ma50)
    if atr14 is not None and atr14 > 0:
        return _round_price(current_price - atr14 * 1.5)
    return None


def _technical_no_chase_above(
    current_price: float,
    resistances: list[dict[str, Any]],
    atr14: float | None,
    state: str,
) -> float | None:
    if state == SHORT_TERM_EXTENDED_STATE:
        return _round_price(current_price)
    nearest = _nearest_resistance_price(resistances)
    if nearest is not None:
        return _round_price(nearest)
    if atr14 is not None and atr14 > 0:
        return _round_price(current_price + atr14 * 1.5)
    return None


def _technical_review_price(current_price: float, supports: list[dict[str, Any]], ma200: float | None) -> float | None:
    candidates = []
    nearest_support = _nearest_support_price(supports)
    if nearest_support is not None:
        candidates.append(nearest_support * 0.97)
    if ma200 is not None and ma200 <= current_price * 1.05:
        candidates.append(ma200 * 0.98)
    candidates = [price for price in candidates if price is not None and price > 0]
    return _round_price(max(candidates)) if candidates else None


def _technical_confidence(
    history_len: int,
    current_price: float,
    ma50: float | None,
    ma200: float | None,
    rsi14: float | None,
    final_decision_blocked: bool,
) -> str:
    if history_len < 80 or ma50 is None or rsi14 is None:
        return "low"
    confidence = "high" if history_len >= 200 and ma200 is not None else "medium"
    if current_price <= 0:
        confidence = "low"
    if final_decision_blocked and confidence == "high":
        confidence = "medium"
    return confidence


def _technical_reasons(
    *,
    symbol: str,
    state: str,
    trend: str,
    current_price: float,
    ma20: float | None,
    ma50: float | None,
    ma200: float | None,
    rsi14: float | None,
    atr14: float | None,
    entry_price: float | None,
    review_price: float | None,
    buy_zone: Any,
    final_decision: Any,
    final_decision_blocked: bool,
) -> list[str]:
    reasons = [f"{symbol.upper()} 技术趋势：{trend}。"]
    if state == SHORT_TERM_EXTENDED_STATE:
        reasons.append("短期追高：价格明显高于 MA20/MA50，且 RSI 偏高，技术层不建议追价。")
    elif state == HEALTHY_PULLBACK_STATE:
        reasons.append("健康回踩：价格接近 MA20/MA50 或近期支撑，且长期趋势未破坏。")
    elif state == TREND_BREAK_REVIEW_STATE:
        reasons.append("趋势破坏/需复核：价格跌破 MA200 或关键支撑，技术层只给复核线。")
    elif state == TACTICAL_OBSERVATION_STATE:
        reasons.append("估值处于 fair zone 但未进入 tranche zone，技术层只给战术观察价，不视为价值买点。")
    else:
        reasons.append("技术状态中性：等待更清晰的回踩或突破确认。")

    if entry_price is not None:
        reasons.append(f"技术回踩点：{entry_price:.2f}，来源优先级为近期支撑、MA50、前高回踩、ATR 回撤位。")
    if review_price is not None:
        reasons.append(f"技术复核线：{review_price:.2f}，跌破后优先复核趋势和支撑有效性。")
    if ma20 is not None or ma50 is not None or ma200 is not None or rsi14 is not None or atr14 is not None:
        parts = []
        if ma20 is not None:
            parts.append(f"MA20 {ma20:.2f}")
        if ma50 is not None:
            parts.append(f"MA50 {ma50:.2f}")
        if ma200 is not None:
            parts.append(f"MA200 {ma200:.2f}")
        if rsi14 is not None:
            parts.append(f"RSI14 {rsi14:.1f}")
        if atr14 is not None:
            parts.append(f"ATR14 {atr14:.2f}")
        reasons.append("关键技术输入：" + " / ".join(parts) + "。")
    if _buy_zone_value(buy_zone, "heavyBuyBelow") is not None:
        reasons.append("极端恐慌区仍以买区模型输出为估值压力情景，技术层不替代估值买点。")
    if final_decision_blocked:
        action = _first_value(final_decision, "finalAction", "displayCategory", default="") or ""
        reasons.append(f"最终结论已阻断或需复核（{action}），技术面只能辅助解释，不能直接变成入场信号。")
    return reasons[:8]


def _is_short_term_extended(current_price: float, ma20: float | None, ma50: float | None, rsi14: float | None) -> bool:
    above_ma20 = ma20 is not None and current_price >= ma20 * 1.07
    above_ma50 = ma50 is not None and current_price >= ma50 * 1.12
    rsi_hot = rsi14 is not None and rsi14 >= 68
    return above_ma20 and above_ma50 and rsi_hot


def _is_healthy_pullback(
    current_price: float,
    ma20: float | None,
    ma50: float | None,
    nearest_support: float | None,
    rsi14: float | None,
    ma200: float | None,
) -> bool:
    near_ma20 = ma20 is not None and abs(current_price / ma20 - 1) <= 0.035
    near_ma50 = ma50 is not None and abs(current_price / ma50 - 1) <= 0.045
    near_support = nearest_support is not None and abs(current_price / nearest_support - 1) <= 0.045
    rsi_ok = rsi14 is None or 35 <= rsi14 <= 65
    trend_ok = ma200 is None or current_price >= ma200 * 0.98
    return trend_ok and rsi_ok and (near_ma20 or near_ma50 or near_support)


def _buy_zone_is_fair_but_not_tranche(current_price: float, buy_zone: Any) -> bool:
    if buy_zone is None:
        return False
    zone = str(_buy_zone_value(buy_zone, "currentZone") or "")
    if zone != "fair_observation":
        return False
    fair_low = _first_number(_buy_zone_value(buy_zone, "fairValueLow"))
    fair_high = _first_number(_buy_zone_value(buy_zone, "fairValueHigh"))
    tranche_low = _first_number(_buy_zone_value(buy_zone, "trancheBuyLow"))
    tranche_high = _first_number(_buy_zone_value(buy_zone, "trancheBuyHigh"))
    in_fair = fair_low is not None and fair_high is not None and fair_low <= current_price <= fair_high
    in_tranche = tranche_low is not None and tranche_high is not None and tranche_low <= current_price <= tranche_high
    return in_fair and not in_tranche


def _final_decision_blocks_action(final_decision: Any) -> bool:
    if final_decision is None:
        return False
    if _first_value(final_decision, "isActionable", default=None) is True:
        return False
    lane = str(_first_value(final_decision, "decisionLane", default="") or "").lower()
    action = str(_first_value(final_decision, "finalAction", default="") or "")
    display = str(_first_value(final_decision, "displayCategory", default="") or "")
    if lane in {"blocked", "review"}:
        return True
    return any(token in f"{action} {display}" for token in ["禁止追高", "需复核", "数据不足", "待复核"])


def _add_level(
    levels: list[dict[str, Any]],
    label: str,
    price: float | None,
    source: str,
    current_price: float,
    *,
    support: bool,
) -> None:
    price = _first_number(price)
    if price is None or price <= 0:
        return
    if support and price > current_price * 1.03:
        return
    if not support and price < current_price * 0.97:
        return
    levels.append(
        {
            "label": label,
            "price": _round_price(price),
            "source": source,
            "distancePct": _round_optional((price / current_price - 1.0) * 100.0),
        }
    )


def _rank_levels(levels: list[dict[str, Any]], current_price: float, *, lower_first: bool) -> list[dict[str, Any]]:
    unique: dict[tuple[str, float], dict[str, Any]] = {}
    for level in levels:
        price = _first_number(level.get("price"))
        if price is None:
            continue
        unique[(str(level.get("label")), round(price, 2))] = level
    sorted_levels = sorted(
        unique.values(),
        key=lambda item: (abs(float(item["price"]) - current_price), float(item["price"]) if lower_first else -float(item["price"])),
    )
    return sorted_levels[:6]


def _nearest_support_price(supports: list[dict[str, Any]]) -> float | None:
    for level in supports:
        price = _first_number(level.get("price"))
        if price is not None:
            return price
    return None


def _nearest_resistance_price(resistances: list[dict[str, Any]]) -> float | None:
    for level in resistances:
        price = _first_number(level.get("price"))
        if price is not None:
            return price
    return None


def _rolling_low(series: pd.Series, window: int) -> float | None:
    if len(series) < max(5, window // 2):
        return None
    return _last_value(series.rolling(window, min_periods=max(5, window // 2)).min())


def _rolling_high(series: pd.Series, window: int) -> float | None:
    if len(series) < max(5, window // 2):
        return None
    return _last_value(series.rolling(window, min_periods=max(5, window // 2)).max())


def _prior_high(high: pd.Series) -> float | None:
    if len(high) < 45:
        return None
    start = max(0, len(high) - 120)
    prior = high.iloc[start : len(high) - 20]
    if prior.empty:
        return None
    return _last_value(pd.Series([prior.max()]))


def _atr14(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(14, min_periods=5).mean()


def _last_rolling_mean(series: pd.Series, window: int, min_periods: int) -> float | None:
    return _round_optional(_last_value(series.rolling(window, min_periods=min_periods).mean()))


def _last_value(series: pd.Series) -> float | None:
    if series.empty:
        return None
    value = series.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def _distance_pct(current_price: float, reference: float | None) -> float | None:
    if reference is None or reference <= 0:
        return None
    return _round_optional((current_price / reference - 1.0) * 100.0)


def _buy_zone_value(source: Any, name: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


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


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number:
            return number
    return None


def _round_optional(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _round_price(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)
