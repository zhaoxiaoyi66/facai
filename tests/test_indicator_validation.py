from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from data.indicator_validation import (
    calculate_standard_ema,
    calculate_wilder_rsi,
    indicator_validation_display_rows,
    validate_local_indicators,
)


def _history(closes: list[float], start: str = "2025-01-01") -> pd.DataFrame:
    start_date = datetime.fromisoformat(start)
    dates = pd.bdate_range(start_date, periods=len(closes))
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1_000_000] * len(closes),
        }
    )


def _manual_ema(values: list[float], period: int) -> float:
    alpha = 2 / (period + 1)
    ema = values[0]
    for value in values[1:]:
        ema = value * alpha + ema * (1 - alpha)
    return ema


def _manual_wilder_rsi(values: list[float], period: int = 14) -> float:
    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(delta, 0) for delta in deltas]
    losses = [max(-delta, 0) for delta in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def test_standard_ema_uses_alpha_two_over_period_plus_one() -> None:
    values = [float(100 + index) for index in range(40)]
    result = calculate_standard_ema(pd.Series(values), 20)

    assert round(result.iloc[-1], 8) == round(_manual_ema(values, 20), 8)


def test_wilder_rsi_matches_recursive_wilder_formula() -> None:
    values = [100 + index * 0.3 + ((index % 7) - 3) * 1.1 for index in range(120)]
    result = calculate_wilder_rsi(pd.Series(values), 14)

    assert round(result.iloc[-1], 8) == round(_manual_wilder_rsi(values, 14), 8)


def test_wilder_rsi_is_not_simple_rolling_mean_rsi() -> None:
    values = [100 + index * 0.2 + ((index % 6) - 2) * 1.4 for index in range(120)]
    prices = pd.Series(values)
    delta = prices.diff()
    simple_gain = delta.clip(lower=0).rolling(14).mean()
    simple_loss = (-delta.clip(upper=0)).rolling(14).mean()
    simple_rsi = 100 - (100 / (1 + simple_gain / simple_loss))

    assert abs(calculate_wilder_rsi(prices, 14).iloc[-1] - simple_rsi.iloc[-1]) > 0.5


def test_validation_prefers_adjusted_close_for_drawdown() -> None:
    history = _history([100, 90, 120] * 45)
    history["adjusted_close"] = [50, 45, 60] * 45

    result = validate_local_indicators("NVDA", history=history)

    assert result["price_column_used"] == "adjusted close"
    assert result["latest_close_used"] == 60.0
    assert result["uses_unadjusted_price"] is False
    assert result["data_quality_status"] == "正常"


def test_validation_falls_back_to_close_and_marks_unadjusted_price() -> None:
    result = validate_local_indicators("NVDA", history=_history([100 + index for index in range(130)]))

    assert result["price_column_used"] == "close"
    assert result["uses_unadjusted_price"] is True
    assert result["technical_quality_status"] == "正常"
    assert result["drawdown_quality_status"] == "close 口径可用"
    assert result["data_quality_status"] == "close 口径可用"
    assert "未发现明显拆股异常" in "；".join(result["quality_notes"])


def test_validation_flags_unadjusted_split_like_jump_for_drawdown_only() -> None:
    closes = [100 + index * 0.2 for index in range(80)]
    closes.extend([value / 10 for value in [116 + index * 0.2 for index in range(80)]])
    result = validate_local_indicators("NVDA", history=_history(closes))

    assert result["technical_quality_status"] == "正常"
    assert result["drawdown_quality_status"] == "回撤口径待复核"
    assert result["data_quality_status"] == "回撤口径待复核"
    assert result["suspected_split"] is True


def test_validation_with_adjusted_close_marks_drawdown_normal() -> None:
    history = _history([100 + index for index in range(130)])
    history["adjusted_close"] = [100 + index for index in range(130)]

    result = validate_local_indicators("NVDA", history=history)

    assert result["technical_quality_status"] == "正常"
    assert result["drawdown_quality_status"] == "正常"
    assert result["data_quality_status"] == "正常"


def test_validation_returns_data_insufficient_without_error() -> None:
    result = validate_local_indicators("NVDA", history=_history([100, 101, 102]))

    assert result["data_quality_status"] == "数据不足"
    assert result["rsi14"] is None
    assert result["latest_data_date"]


def test_indicator_display_rows_do_not_expose_none_or_internal_fields() -> None:
    result = validate_local_indicators("NVDA", history=_history([100, 101, 102]))
    text = " ".join(
        str(value)
        for row in indicator_validation_display_rows(result)
        for value in row.values()
    )

    assert "None" not in text
    assert "current_drawdown_pct" not in text
    assert "adjusted_close" not in text
    assert "data_quality_status" not in text
    assert "数据不足" in text
