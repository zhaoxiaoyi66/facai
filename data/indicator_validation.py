from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from data.cache_read_model import CacheReadModel


RSI_PERIOD = 14
RSI_WARMUP_BARS = 100
DRAW_DOWN_YEARS = 3

ADJUSTED_CLOSE_COLUMNS = ("adjusted_close", "adj_close", "adjClose", "adjustedClose")


def validate_local_indicators(
    symbol: str,
    *,
    history: pd.DataFrame | None = None,
    years: int = DRAW_DOWN_YEARS,
) -> dict[str, Any]:
    """Validate local RSI, EMA and drawdown values without changing trading logic."""
    normalized = str(symbol or "").strip().upper()
    raw_history = history if history is not None else CacheReadModel().get_price_history(normalized)
    frame, price_column, uses_unadjusted = _prepare_price_frame(raw_history)
    if frame.empty:
        return _insufficient_result(normalized, price_column=price_column, daily_count=0)

    daily_count = len(frame)
    price = frame["indicator_price"]
    latest_date = _date_text(frame["date"].iloc[-1])
    latest_price = _last_number(price)
    quality = _data_quality(frame, price_column=price_column, uses_unadjusted=uses_unadjusted)

    if daily_count < RSI_WARMUP_BARS:
        return {
            **_base_result(normalized, frame, price_column, uses_unadjusted, quality),
            "rsi14": None,
            "ema20": _last_number(calculate_standard_ema(price, 20)),
            "ema50": _last_number(calculate_standard_ema(price, 50)),
            "ema200": _last_number(calculate_standard_ema(price, 200)),
            "current_drawdown_pct": _current_drawdown_pct(frame, years=years),
            "max_drawdown_3y_pct": _max_drawdown_pct(frame, years=years),
            "data_quality_status": "数据不足",
            "quality_notes": [*quality["quality_notes"], "日线数量少于 100 根，RSI 预热不足"],
            "latest_data_date": latest_date,
            "latest_close_used": latest_price,
            "daily_count": daily_count,
        }

    rsi = calculate_wilder_rsi(price, RSI_PERIOD)
    return {
        **_base_result(normalized, frame, price_column, uses_unadjusted, quality),
        "rsi14": _last_number(rsi),
        "ema20": _last_number(calculate_standard_ema(price, 20)),
        "ema50": _last_number(calculate_standard_ema(price, 50)),
        "ema200": _last_number(calculate_standard_ema(price, 200)),
        "current_drawdown_pct": _current_drawdown_pct(frame, years=years),
        "max_drawdown_3y_pct": _max_drawdown_pct(frame, years=years),
        "latest_data_date": latest_date,
        "latest_close_used": latest_price,
        "daily_count": daily_count,
    }


def calculate_standard_ema(prices: pd.Series, period: int) -> pd.Series:
    numeric = pd.to_numeric(prices, errors="coerce")
    alpha = 2 / (period + 1)
    return numeric.ewm(alpha=alpha, adjust=False, min_periods=period).mean()


def calculate_wilder_rsi(prices: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    numeric = pd.to_numeric(prices, errors="coerce")
    rsi = pd.Series(np.nan, index=numeric.index, dtype="float64")
    valid = numeric.dropna()
    if len(valid) <= period:
        return rsi

    delta = valid.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.iloc[1 : period + 1].mean()
    avg_loss = loss.iloc[1 : period + 1].mean()
    first_index = valid.index[period]
    rsi.loc[first_index] = _rsi_from_average(avg_gain, avg_loss)

    for idx in range(period + 1, len(valid)):
        current_index = valid.index[idx]
        avg_gain = (avg_gain * (period - 1) + float(gain.iloc[idx])) / period
        avg_loss = (avg_loss * (period - 1) + float(loss.iloc[idx])) / period
        rsi.loc[current_index] = _rsi_from_average(avg_gain, avg_loss)
    return rsi.clip(lower=0, upper=100)


def indicator_validation_display_rows(result: dict[str, Any]) -> list[dict[str, str]]:
    price_column = str(result.get("price_column_used") or "close")
    drawdown_price_label = "adjusted close 口径" if price_column == "adjusted close" else "close 口径"
    return [
        {
            "项目": "RSI",
            "口径": "Wilder RSI 14，close 口径",
            "当前值": _number_text(result.get("rsi14"), digits=1),
            "状态": str(result.get("technical_quality_status") or "数据不足"),
        },
        {
            "项目": "EMA",
            "口径": "EMA20 / EMA50 / EMA200，close 口径",
            "当前值": _ema_summary(result),
            "状态": str(result.get("technical_quality_status") or "数据不足"),
        },
        {
            "项目": "回撤",
            "口径": f"近 3 年 {drawdown_price_label}",
            "当前值": _drawdown_summary(result),
            "状态": str(result.get("drawdown_quality_status") or "数据不足"),
        },
        {
            "项目": "复权检查",
            "口径": _quality_basis_text(result),
            "当前值": _adjustment_check_text(result),
            "状态": str(result.get("data_quality_status") or "数据不足"),
        },
    ]


def _prepare_price_frame(history: pd.DataFrame | None) -> tuple[pd.DataFrame, str, bool]:
    if history is None or history.empty or "date" not in history.columns:
        return pd.DataFrame(), "close", True
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    adjusted_column = next((column for column in ADJUSTED_CLOSE_COLUMNS if column in frame.columns), None)
    price_column = adjusted_column or "close"
    if price_column not in frame.columns:
        return pd.DataFrame(), "close", True
    frame["indicator_price"] = pd.to_numeric(frame[price_column], errors="coerce")
    frame["raw_close"] = pd.to_numeric(frame.get("close"), errors="coerce") if "close" in frame.columns else np.nan
    frame["adjusted_close"] = (
        pd.to_numeric(frame[adjusted_column], errors="coerce") if adjusted_column else np.nan
    )
    frame = frame.dropna(subset=["date", "indicator_price"])
    frame = frame[frame["indicator_price"] > 0].sort_values("date").drop_duplicates(subset=["date"], keep="last")
    label = "adjusted close" if adjusted_column else "close"
    return frame.reset_index(drop=True), label, adjusted_column is None


def _base_result(
    symbol: str,
    frame: pd.DataFrame,
    price_column: str,
    uses_unadjusted: bool,
    quality: dict[str, Any],
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "price_column_used": price_column,
        "uses_unadjusted_price": uses_unadjusted,
        "raw_close": _last_number(frame.get("raw_close", pd.Series(dtype=float))),
        "adjusted_close": _last_number(frame.get("adjusted_close", pd.Series(dtype=float))),
        "latest_data_date": _date_text(frame["date"].iloc[-1]) if not frame.empty else "",
        "daily_count": len(frame),
        "missing_trading_days": quality["missing_trading_days"],
        "suspected_split": quality["suspected_split"],
        "suspected_split_reason": quality["suspected_split_reason"],
        "has_adjusted_close": not uses_unadjusted,
        "technical_quality_status": quality["technical_quality_status"],
        "technical_price_basis": quality["technical_price_basis"],
        "drawdown_quality_status": quality["drawdown_quality_status"],
        "drawdown_price_basis": quality["drawdown_price_basis"],
        "adjustment_check_status": quality["adjustment_check_status"],
        "data_quality_status": quality["data_quality_status"],
        "quality_notes": quality["quality_notes"],
    }


def _insufficient_result(symbol: str, *, price_column: str, daily_count: int) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "rsi14": None,
        "ema20": None,
        "ema50": None,
        "ema200": None,
        "current_drawdown_pct": None,
        "max_drawdown_3y_pct": None,
        "data_quality_status": "数据不足",
        "price_column_used": price_column,
        "uses_unadjusted_price": price_column == "close",
        "has_adjusted_close": price_column != "close",
        "daily_count": daily_count,
        "latest_data_date": "",
        "latest_close_used": None,
        "missing_trading_days": False,
        "suspected_split": False,
        "suspected_split_reason": "",
        "technical_quality_status": "数据不足",
        "technical_price_basis": "close 口径",
        "drawdown_quality_status": "数据不足",
        "drawdown_price_basis": "close 口径" if price_column == "close" else "adjusted close 口径",
        "adjustment_check_status": "数据不足",
        "quality_notes": ["日线数据不足"],
    }


def _data_quality(frame: pd.DataFrame, *, price_column: str, uses_unadjusted: bool) -> dict[str, Any]:
    notes: list[str] = []
    missing_trading_days = _has_missing_trading_days(frame)
    suspected_split, suspected_split_reason = _suspicious_split_check(frame["indicator_price"])
    enough_daily = len(frame) >= RSI_WARMUP_BARS
    if uses_unadjusted:
        notes.append("回撤暂用 close 口径")
    if missing_trading_days:
        notes.append("交易日序列存在明显缺口")
    if suspected_split:
        notes.append(suspected_split_reason or "价格序列存在疑似拆股或倍率跳变")

    technical_status = "正常" if enough_daily else "数据不足"
    technical_basis = "close 口径，正常" if enough_daily else "close 口径，数据不足"
    drawdown_basis = "close 口径" if uses_unadjusted else "adjusted close 口径"

    if not enough_daily:
        drawdown_status = "数据不足"
        adjustment_status = "数据不足"
        status = "数据不足"
    elif not uses_unadjusted:
        drawdown_status = "正常"
        adjustment_status = "正常"
        status = "正常"
    elif suspected_split or missing_trading_days:
        drawdown_status = "回撤口径待复核"
        adjustment_status = "疑似拆股影响，需复核" if suspected_split else "交易日缺口较多，需复核"
        status = "回撤口径待复核"
    else:
        drawdown_status = "close 口径可用"
        adjustment_status = "未发现明显拆股异常"
        status = "close 口径可用"

    if uses_unadjusted and not suspected_split and enough_daily:
        notes.append("未发现明显拆股异常")
    return {
        "data_quality_status": status,
        "technical_quality_status": technical_status,
        "technical_price_basis": technical_basis,
        "drawdown_quality_status": drawdown_status,
        "drawdown_price_basis": drawdown_basis,
        "adjustment_check_status": adjustment_status,
        "missing_trading_days": missing_trading_days,
        "suspected_split": suspected_split,
        "suspected_split_reason": suspected_split_reason,
        "quality_notes": notes or ["本地日线口径可用"],
    }


def _has_missing_trading_days(frame: pd.DataFrame) -> bool:
    if frame.empty or len(frame) < 20:
        return False
    start = frame["date"].min().normalize()
    end = frame["date"].max().normalize()
    expected = len(pd.bdate_range(start, end))
    if expected <= 0:
        return False
    missing_ratio = max(0, expected - len(frame)) / expected
    return missing_ratio > 0.12


def _suspicious_split_check(prices: pd.Series) -> tuple[bool, str]:
    numeric = pd.to_numeric(prices, errors="coerce").dropna()
    if len(numeric) < 2:
        return False, ""
    ratios = numeric / numeric.shift(1)
    ratios = ratios.replace([np.inf, -np.inf], np.nan).dropna()
    if ratios.empty:
        return False, ""
    common_ratios = (2.0, 3.0, 4.0, 5.0, 10.0)
    for ratio in ratios:
        value = float(ratio)
        inverse = 1.0 / value if value else np.nan
        for split_ratio in common_ratios:
            if _near_ratio(value, split_ratio) or _near_ratio(inverse, split_ratio):
                return True, f"价格跳变接近 {split_ratio:g}:1 拆股比例"
    large_jump = ratios[(ratios >= 1.5) | (ratios <= 0.67)]
    if not large_jump.empty:
        return True, "单日价格跳变超过 50%，需复核拆股或倍率异常"
    medium_jump = ratios[(ratios >= 1.35) | (ratios <= 0.74)]
    if not medium_jump.empty:
        return True, "单日价格跳变超过 35%，需复核拆股或异常行情"
    return False, ""


def _near_ratio(value: float, target: float) -> bool:
    if value is None or np.isnan(value) or target <= 0:
        return False
    return abs(value - target) / target <= 0.08


def _current_drawdown_pct(frame: pd.DataFrame, *, years: int) -> float | None:
    window = _drawdown_window(frame, years=years)
    if window.empty:
        return None
    latest = _last_number(window["indicator_price"])
    peak = _number(window["indicator_price"].max())
    if latest is None or peak is None or peak <= 0:
        return None
    return (latest / peak - 1.0) * 100.0


def _max_drawdown_pct(frame: pd.DataFrame, *, years: int) -> float | None:
    window = _drawdown_window(frame, years=years)
    if window.empty:
        return None
    price = window["indicator_price"].astype(float)
    drawdown = price / price.cummax() - 1.0
    return _number(drawdown.min() * 100.0)


def _drawdown_window(frame: pd.DataFrame, *, years: int) -> pd.DataFrame:
    if frame.empty:
        return frame
    latest = frame["date"].max()
    start = latest - pd.Timedelta(days=max(1, int(years or DRAW_DOWN_YEARS)) * 365)
    return frame[frame["date"] >= start].copy()


def _rsi_from_average(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0 and avg_gain == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _ema_summary(result: dict[str, Any]) -> str:
    return (
        f"20日 {_number_text(result.get('ema20'))} / "
        f"50日 {_number_text(result.get('ema50'))} / "
        f"200日 {_number_text(result.get('ema200'))}"
    )


def _drawdown_summary(result: dict[str, Any]) -> str:
    return (
        f"当前 {_percent_text(result.get('current_drawdown_pct'))} / "
        f"近3年最深 {_percent_text(result.get('max_drawdown_3y_pct'))}"
    )


def _quality_basis_text(result: dict[str, Any]) -> str:
    notes = [str(item) for item in result.get("quality_notes") or [] if str(item).strip()]
    if not notes:
        return "本地缓存日线可用"
    return "；".join(notes[:3])


def _adjustment_check_text(result: dict[str, Any]) -> str:
    status = str(result.get("adjustment_check_status") or "").strip()
    if status:
        return status
    if result.get("price_column_used") == "adjusted close":
        return "正常"
    if result.get("suspected_split"):
        return "疑似拆股影响，需复核"
    return "未发现明显拆股异常"


def _number_text(value: object, *, digits: int = 2) -> str:
    number = _number(value)
    if number is None:
        return "数据不足"
    return f"{number:,.{digits}f}"


def _percent_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "数据不足"
    return f"{number:+.1f}%"


def _last_number(series: pd.Series | object) -> float | None:
    if not isinstance(series, pd.Series) or series.empty:
        return None
    return _number(series.dropna().iloc[-1]) if not series.dropna().empty else None


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(number) or np.isinf(number):
        return None
    return number


def _date_text(value: object) -> str:
    if value is None or value == "":
        return ""
    try:
        return pd.Timestamp(value).date().isoformat()
    except (TypeError, ValueError):
        return str(value)
