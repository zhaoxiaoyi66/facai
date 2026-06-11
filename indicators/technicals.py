from __future__ import annotations

import numpy as np
import pandas as pd


TRADING_DAYS_IN_YEAR = 252


def calculate_rsi14(prices: pd.Series) -> pd.Series:
    return calculate_rsi(prices, window=14)


def calculate_rsi(prices: pd.Series, window: int = 14) -> pd.Series:
    prices = pd.to_numeric(prices, errors="coerce")
    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    relative_strength = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + relative_strength))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss > 0), 0)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss == 0), 50)
    return rsi.clip(lower=0, upper=100)


def calculate_ema(prices: pd.Series, span: int, min_periods: int | None = None) -> pd.Series:
    prices = pd.to_numeric(prices, errors="coerce")
    return prices.ewm(span=span, adjust=False, min_periods=min_periods or span).mean()


def calculate_ema20(prices: pd.Series) -> pd.Series:
    return calculate_ema(prices, span=20)


def calculate_ema50(prices: pd.Series) -> pd.Series:
    return calculate_ema(prices, span=50)


def calculate_ema100(prices: pd.Series) -> pd.Series:
    return calculate_ema(prices, span=100)


def calculate_ema200(prices: pd.Series) -> pd.Series:
    return calculate_ema(prices, span=200)


def calculate_atr14(highs: pd.Series, lows: pd.Series, closes: pd.Series) -> pd.Series:
    highs = pd.to_numeric(highs, errors="coerce")
    lows = pd.to_numeric(lows, errors="coerce")
    closes = pd.to_numeric(closes, errors="coerce")
    previous_close = closes.shift(1)
    true_range = pd.concat(
        [
            highs - lows,
            (highs - previous_close).abs(),
            (lows - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(14, min_periods=14).mean()


def calculate_drawdown_from_52_week_high(
    closes: pd.Series,
    highs: pd.Series | None = None,
    window: int = TRADING_DAYS_IN_YEAR,
    min_periods: int = 30,
) -> pd.Series:
    closes = pd.to_numeric(closes, errors="coerce")
    high_series = pd.to_numeric(highs if highs is not None else closes, errors="coerce").fillna(closes)
    rolling_high = high_series.rolling(window, min_periods=min_periods).max()
    return (closes / rolling_high - 1.0) * 100.0


def add_technical_indicators(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return history.copy()

    df = history.sort_values("date").copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    high_series = pd.to_numeric(df.get("high", df["close"]), errors="coerce").fillna(df["close"])
    low_series = pd.to_numeric(df.get("low", df["close"]), errors="coerce").fillna(df["close"])

    df["ema20"] = calculate_ema20(df["close"])
    df["ema50"] = calculate_ema50(df["close"])
    df["ema100"] = calculate_ema100(df["close"])
    df["ema200"] = calculate_ema200(df["close"])
    df["atr14"] = calculate_atr14(high_series, low_series, df["close"])
    df["rsi14"] = calculate_rsi14(df["close"])
    df["fifty_two_week_high"] = high_series.rolling(TRADING_DAYS_IN_YEAR, min_periods=30).max()
    df["fifty_two_week_low"] = low_series.rolling(TRADING_DAYS_IN_YEAR, min_periods=30).min()
    df["recent_swing_low"] = low_series.rolling(20, min_periods=10).min()
    df["recent_swing_high"] = high_series.rolling(20, min_periods=10).max()
    df["recent_breakout_level"] = high_series.shift(1).rolling(60, min_periods=20).max()
    df["drawdown_from_high_pct"] = calculate_drawdown_from_52_week_high(df["close"], high_series)
    df["pct_above_ema20"] = (df["close"] / df["ema20"] - 1.0) * 100.0
    df["pct_above_ema50"] = (df["close"] / df["ema50"] - 1.0) * 100.0
    df["pct_above_ema200"] = (df["close"] / df["ema200"] - 1.0) * 100.0
    df["ema50_slope_20d_pct"] = (df["ema50"] / df["ema50"].shift(20) - 1.0) * 100.0
    df["ema200_slope_20d_pct"] = (df["ema200"] / df["ema200"].shift(20) - 1.0) * 100.0
    df["daily_return_pct"] = df["close"].pct_change(periods=1) * 100.0
    df["gain_20d_pct"] = calculate_gain_over_trading_days(df["close"], days=20)
    df["gain_60d_pct"] = calculate_gain_over_trading_days(df["close"], days=60)
    if "volume" in df:
        volume = pd.to_numeric(df["volume"], errors="coerce")
        avg_volume_20d = volume.rolling(20, min_periods=10).mean()
        avg_volume_60d = volume.rolling(60, min_periods=20).mean()
        df["avg_volume_20d"] = avg_volume_20d
        df["avg_volume_60d"] = avg_volume_60d
        df["volume_trend"] = (volume / avg_volume_20d - 1.0).replace([np.inf, -np.inf], np.nan)
        df["volume_trend_60d"] = (volume / avg_volume_60d - 1.0).replace([np.inf, -np.inf], np.nan)
    return df


def latest_technical_snapshot(history_with_indicators: pd.DataFrame) -> dict:
    if history_with_indicators.empty:
        return {
            "price": None,
            "ema20": None,
            "ema50": None,
            "ema100": None,
            "ema200": None,
            "atr14": None,
            "rsi14": None,
            "fifty_two_week_high": None,
            "fifty_two_week_low": None,
            "recent_swing_low": None,
            "recent_swing_high": None,
            "recent_breakout_level": None,
            "drawdown_from_high_pct": None,
            "pct_above_ema20": None,
            "pct_above_ema50": None,
            "pct_above_ema200": None,
            "ema50_slope_20d_pct": None,
            "ema200_slope_20d_pct": None,
            "daily_return_pct": None,
            "gain_20d_pct": None,
            "gain_60d_pct": None,
            "volume_trend": None,
            "volume_trend_60d": None,
        }

    latest = history_with_indicators.iloc[-1]
    return {
        "price": _clean(latest.get("close")),
        "ema20": _clean(latest.get("ema20")),
        "ema50": _clean(latest.get("ema50")),
        "ema100": _clean(latest.get("ema100")),
        "ema200": _clean(latest.get("ema200")),
        "atr14": _clean(latest.get("atr14")),
        "rsi14": _clean(latest.get("rsi14")),
        "fifty_two_week_high": _clean(latest.get("fifty_two_week_high")),
        "fifty_two_week_low": _clean(latest.get("fifty_two_week_low")),
        "recent_swing_low": _clean(latest.get("recent_swing_low")),
        "recent_swing_high": _clean(latest.get("recent_swing_high")),
        "recent_breakout_level": _clean(latest.get("recent_breakout_level")),
        "drawdown_from_high_pct": _clean(latest.get("drawdown_from_high_pct")),
        "pct_above_ema20": _clean(latest.get("pct_above_ema20")),
        "pct_above_ema50": _clean(latest.get("pct_above_ema50")),
        "pct_above_ema200": _clean(latest.get("pct_above_ema200")),
        "ema50_slope_20d_pct": _clean(latest.get("ema50_slope_20d_pct")),
        "ema200_slope_20d_pct": _clean(latest.get("ema200_slope_20d_pct")),
        "daily_return_pct": _clean(latest.get("daily_return_pct")),
        "gain_20d_pct": _clean(latest.get("gain_20d_pct")),
        "gain_60d_pct": _clean(latest.get("gain_60d_pct")),
        "volume_trend": _clean(latest.get("volume_trend")),
        "volume_trend_60d": _clean(latest.get("volume_trend_60d")),
    }


def calculate_technical_score(technicals: dict) -> float:
    score = 0.0
    price = technicals.get("price")
    ema50 = technicals.get("ema50")
    ema200 = technicals.get("ema200")
    rsi = technicals.get("rsi14")

    # Technical rule: price above EMA200 confirms long-term trend support.
    if price is not None and ema200 is not None:
        score += 4 if price >= ema200 else 1
    else:
        score += 2

    # Technical rule: price above EMA50 confirms shorter-term momentum.
    if price is not None and ema50 is not None:
        score += 3 if price >= ema50 else 1
    else:
        score += 1.5

    # Technical rule: RSI between 35 and 65 is a healthier accumulation range than overbought extremes.
    if rsi is None:
        score += 1.5
    elif 35 <= rsi <= 65:
        score += 3
    elif 25 <= rsi < 35 or 65 < rsi < 70:
        score += 2
    else:
        score += 0.5

    return round(min(score, 10), 1)


def calculate_gain_over_trading_days(prices: pd.Series, days: int = 20) -> pd.Series:
    prices = pd.to_numeric(prices, errors="coerce")
    return prices.pct_change(periods=days) * 100.0


def _clean(value: object) -> float | None:
    if pd.isna(value):
        return None
    return float(value)
