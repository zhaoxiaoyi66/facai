from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


SPREAD_REASON_NORMAL = "正常波动"
SPREAD_REASON_MILD = "轻微偏离"
SPREAD_REASON_NOTICEABLE = "明显偏离"
SPREAD_REASON_ANOMALY = "异常价差"
SPREAD_REASON_EXTREME = "极端价差"
SPREAD_REASON_INSUFFICIENT = "数据不足"

NEWS_EXPLAINED_LABELS = {"有新闻解释", "新闻方向一致", "重大新闻", "有重大新闻"}
NEWS_OPINION_LABELS = {"观点文章", "观点文章，不足以解释价差"}


@dataclass(frozen=True)
class WeekendSpreadVolatilityProfile:
    avg_range_20d: float | None
    atr14_pct: float | None
    spread_atr_ratio: float | None
    spread_range_ratio: float | None
    spread_percentile: float | None
    spread_reasonableness_label: str
    spread_reasonableness_explanation: str
    volatility_status: str
    latest_data_date: str
    history_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "avg_range_20d": self.avg_range_20d,
            "atr14_pct": self.atr14_pct,
            "spread_atr_ratio": self.spread_atr_ratio,
            "spread_range_ratio": self.spread_range_ratio,
            "spread_percentile": self.spread_percentile,
            "spread_reasonableness_label": self.spread_reasonableness_label,
            "spread_reasonableness_explanation": self.spread_reasonableness_explanation,
            "volatility_status": self.volatility_status,
            "latest_data_date": self.latest_data_date,
            "history_count": self.history_count,
        }


def build_spread_volatility_profile(
    history: pd.DataFrame | list[dict[str, Any]] | None,
    spread_pct: float | None,
    *,
    news_label: str | None = None,
    range_lookback: int = 20,
    atr_period: int = 14,
    percentile_lookback: int = 60,
) -> WeekendSpreadVolatilityProfile:
    frame = _normalize_history(history)
    spread_abs = _number_abs(spread_pct)
    if frame.empty or spread_abs is None:
        return _insufficient_profile(frame)

    frame = _with_volatility_columns(frame)
    latest_close = _last_number(frame["close"])
    range_values = _finite_series(frame["daily_range_pct"]).tail(range_lookback)
    tr_values = _finite_series(frame["true_range"]).tail(atr_period)
    percentile_values = _finite_series(frame["daily_range_pct"]).tail(percentile_lookback)

    avg_range = float(range_values.mean()) if len(range_values) >= range_lookback else None
    atr = float(tr_values.mean()) if len(tr_values) >= atr_period else None
    atr_pct = (atr / latest_close * 100.0) if atr is not None and latest_close and latest_close > 0 else None
    spread_atr_ratio = (spread_abs / atr_pct) if atr_pct and atr_pct > 0 else None
    spread_range_ratio = (spread_abs / avg_range) if avg_range and avg_range > 0 else None
    percentile = _percentile_rank(percentile_values, spread_abs) if len(percentile_values) >= 5 else None
    label = classify_spread_reasonableness(
        spread_atr_ratio=spread_atr_ratio,
        spread_range_ratio=spread_range_ratio,
        spread_percentile=percentile,
    )
    if label == SPREAD_REASON_INSUFFICIENT:
        return _profile(
            frame,
            avg_range=avg_range,
            atr_pct=atr_pct,
            spread_atr_ratio=spread_atr_ratio,
            spread_range_ratio=spread_range_ratio,
            percentile=percentile,
            label=label,
            explanation="缺少足够日线数据，无法判断当前价差是否超出正常波动。",
            status="波动数据不足",
        )

    return _profile(
        frame,
        avg_range=avg_range,
        atr_pct=atr_pct,
        spread_atr_ratio=spread_atr_ratio,
        spread_range_ratio=spread_range_ratio,
        percentile=percentile,
        label=label,
        explanation=spread_reasonableness_explanation(label, news_label=news_label),
        status="可用",
    )


def classify_spread_reasonableness(
    *,
    spread_atr_ratio: float | None,
    spread_range_ratio: float | None = None,
    spread_percentile: float | None = None,
) -> str:
    primary = _number(spread_atr_ratio)
    if primary is None:
        primary = _number(spread_range_ratio)
    percentile = _number(spread_percentile)
    if primary is None:
        return SPREAD_REASON_INSUFFICIENT
    if primary >= 2.0 or (percentile is not None and percentile >= 90.0):
        return SPREAD_REASON_EXTREME
    if primary >= 1.5:
        return SPREAD_REASON_ANOMALY
    if primary >= 1.0:
        return SPREAD_REASON_NOTICEABLE
    if primary >= 0.5:
        return SPREAD_REASON_MILD
    return SPREAD_REASON_NORMAL


def spread_reasonableness_explanation(label: str, *, news_label: str | None = None) -> str:
    news = str(news_label or "").strip()
    if any(token in news for token in NEWS_EXPLAINED_LABELS) and label in {SPREAD_REASON_ANOMALY, SPREAD_REASON_EXTREME, SPREAD_REASON_NOTICEABLE}:
        return "价差偏大，但存在新闻解释。"
    if any(token in news for token in NEWS_OPINION_LABELS):
        return "有观点文章，但不足以完全解释价差。"
    if news in {"无新闻解释", "无重大新闻", "无新闻"} and label in {SPREAD_REASON_ANOMALY, SPREAD_REASON_EXTREME}:
        return "价差显著超过日常波动，且缺少明确新闻解释。"
    return {
        SPREAD_REASON_NORMAL: "当前价差低于半个 ATR，仍在日常波动范围内。",
        SPREAD_REASON_MILD: "当前价差接近半个 ATR，属于轻微偏离。",
        SPREAD_REASON_NOTICEABLE: "当前价差接近一个 ATR，需要观察是否继续扩大。",
        SPREAD_REASON_ANOMALY: "当前价差超过常规日内波动，建议复核映射和锚点质量。",
        SPREAD_REASON_EXTREME: "当前价差已接近或超过历史高分位波动，属于极端偏离。",
    }.get(label, "缺少足够日线数据，无法判断当前价差是否超出正常波动。")


def _normalize_history(history: pd.DataFrame | list[dict[str, Any]] | None) -> pd.DataFrame:
    if history is None:
        return pd.DataFrame(columns=["date", "high", "low", "close"])
    frame = history.copy() if isinstance(history, pd.DataFrame) else pd.DataFrame(history)
    if frame.empty:
        return pd.DataFrame(columns=["date", "high", "low", "close"])
    rename = {
        "adjClose": "adjusted_close",
        "adjustedClose": "adjusted_close",
        "adj_close": "adjusted_close",
    }
    frame = frame.rename(columns={key: value for key, value in rename.items() if key in frame.columns})
    required = {"high", "low", "close"}
    if not required.issubset(set(frame.columns)):
        return pd.DataFrame(columns=["date", "high", "low", "close"])
    for column in ("high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.sort_values("date")
    frame = frame.dropna(subset=["high", "low", "close"])
    frame = frame[(frame["high"] > 0) & (frame["low"] > 0) & (frame["close"] > 0)]
    return frame.reset_index(drop=True)


def _with_volatility_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    previous_close = result["close"].shift(1)
    result["daily_range_pct"] = (result["high"] - result["low"]) / previous_close * 100.0
    high_low = result["high"] - result["low"]
    high_prev = (result["high"] - previous_close).abs()
    low_prev = (result["low"] - previous_close).abs()
    result["true_range"] = pd.concat([high_low, high_prev, low_prev], axis=1).max(axis=1)
    return result


def _percentile_rank(values: pd.Series, current: float) -> float | None:
    finite = [float(value) for value in values if pd.notna(value)]
    if not finite:
        return None
    below_or_equal = sum(1 for value in finite if value <= current)
    return below_or_equal / len(finite) * 100.0


def _insufficient_profile(frame: pd.DataFrame) -> WeekendSpreadVolatilityProfile:
    return _profile(
        frame,
        avg_range=None,
        atr_pct=None,
        spread_atr_ratio=None,
        spread_range_ratio=None,
        percentile=None,
        label=SPREAD_REASON_INSUFFICIENT,
        explanation="缺少足够日线数据，无法判断当前价差是否超出正常波动。",
        status="波动数据不足",
    )


def _profile(
    frame: pd.DataFrame,
    *,
    avg_range: float | None,
    atr_pct: float | None,
    spread_atr_ratio: float | None,
    spread_range_ratio: float | None,
    percentile: float | None,
    label: str,
    explanation: str,
    status: str,
) -> WeekendSpreadVolatilityProfile:
    latest_date = ""
    if "date" in frame.columns and not frame.empty:
        latest = frame["date"].iloc[-1]
        if pd.notna(latest):
            latest_date = pd.Timestamp(latest).date().isoformat()
    return WeekendSpreadVolatilityProfile(
        avg_range_20d=avg_range,
        atr14_pct=atr_pct,
        spread_atr_ratio=spread_atr_ratio,
        spread_range_ratio=spread_range_ratio,
        spread_percentile=percentile,
        spread_reasonableness_label=label,
        spread_reasonableness_explanation=explanation,
        volatility_status=status,
        latest_data_date=latest_date,
        history_count=len(frame),
    )


def _finite_series(values: pd.Series) -> pd.Series:
    return values[pd.notna(values)]


def _last_number(values: pd.Series) -> float | None:
    finite = _finite_series(values)
    if finite.empty:
        return None
    return _number(finite.iloc[-1])


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _number_abs(value: Any) -> float | None:
    number = _number(value)
    return abs(number) if number is not None else None
