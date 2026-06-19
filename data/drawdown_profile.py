from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from data.market_context import build_market_context, build_market_history


PROFILE_CACHE_TTL_SECONDS = 600
MIN_OBSERVATIONS = 3
EPISODE_THRESHOLD_PCT = -3.0

_PROFILE_CACHE: dict[tuple[str, int, str], tuple[datetime, dict[str, Any]]] = {}


@dataclass(frozen=True)
class DrawdownEpisode:
    peak_date: str
    peak_price: float
    trough_date: str
    trough_price: float
    drawdown_pct: float
    days_to_trough: int
    recovered: bool
    recovery_date: str
    days_to_recover: int | None
    new_high_after_recovery: bool
    regime_at_peak: str


def build_drawdown_profile(
    symbol: str,
    *,
    years: int = 3,
    history: pd.DataFrame | None = None,
    now: datetime | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    normalized = str(symbol or "").strip().upper()
    raw_history = history if history is not None else build_market_history(normalized)
    market_context = None if history is not None else build_market_context(normalized)
    frame = _normalize_history(raw_history)
    frame = _limit_years(frame, years=years, now=now)
    latest_date = _latest_date_text(frame)
    cache_key = (normalized, int(years or 3), latest_date)
    if history is None and use_cache:
        cached = _PROFILE_CACHE.get(cache_key)
        if cached and not _cache_expired(cached[0], now=now):
            return dict(cached[1])

    profile = _build_profile(normalized, frame, years=int(years or 3), market_context=market_context)
    if history is None and use_cache:
        _PROFILE_CACHE[cache_key] = ((now or datetime.now(timezone.utc)).astimezone(timezone.utc), dict(profile))
    return profile


def drawdown_profile_summary_text(profile: dict[str, Any]) -> str:
    if not profile or profile.get("data_status") != "OK":
        return "历史回撤档案：数据不足，暂时无法判断当前回撤规律。"
    quality = str((profile.get("data_quality") or {}).get("data_quality_status") or profile.get("data_quality_status") or "正常")
    if quality != "正常":
        return f"行情口径待复核：{quality}。暂不输出强回撤结论。"
    current = _pct_text(profile.get("current_drawdown_pct"))
    state = str(profile.get("drawdown_state") or "数据不足")
    effective = _pct_text(profile.get("current_regime_max_recovered_drawdown_pct") or profile.get("recent_12m_max_recovered_drawdown_pct") or profile.get("max_recovered_drawdown_pct"))
    reason = str(profile.get("drawdown_state_reason") or "").strip()
    if reason:
        return f"当前回撤 {current}，属于{state}；本轮/近期有效回撤参考 {effective}。{reason}"
    return f"当前回撤 {current}，属于{state}；本轮/近期有效回撤参考 {effective}。"


def _build_profile(symbol: str, frame: pd.DataFrame, *, years: int, market_context: dict[str, Any] | None = None) -> dict[str, Any]:
    if frame.empty or len(frame) < MIN_OBSERVATIONS:
        return _insufficient_profile(symbol, years, f"近 {years} 年日线数据不足")
    close = frame["price"].astype(float)
    running_peak = close.cummax()
    drawdowns = close / running_peak - 1.0
    current_drawdown = float(drawdowns.iloc[-1] * 100.0)
    max_drawdown = float(drawdowns.min() * 100.0)
    episodes = _detect_episodes(frame)
    recovered_drawdowns = [episode.drawdown_pct for episode in episodes if episode.recovered]
    max_recovered = min(recovered_drawdowns) if recovered_drawdowns else None
    recovered_p90 = _negative_percentile(recovered_drawdowns, 90)
    episode_drawdowns = [episode.drawdown_pct for episode in episodes]
    percentiles = _drawdown_percentiles(episode_drawdowns)
    recovery_stats = _recovery_stats(episodes)
    pullback_stats = _new_high_pullback_stats(frame)
    trend_stats = _trend_drawdown_stats(episodes)
    current_regime_start = _current_regime_start_date(frame)
    current_regime_max_recovered = _max_recovered_since(episodes, current_regime_start)
    latest_date = frame["date"].max()
    recent_6m_max_recovered = _max_recovered_since(episodes, latest_date - pd.Timedelta(days=183))
    recent_12m_max_recovered = _max_recovered_since(episodes, latest_date - pd.Timedelta(days=365))
    data_quality = _data_quality_report(symbol, frame, market_context)
    state, reason, rank = _classify_current_drawdown(
        current_drawdown,
        max_recovered,
        percentiles,
        current_regime_max_recovered=current_regime_max_recovered,
        recent_6m_max_recovered=recent_6m_max_recovered,
        recent_12m_max_recovered=recent_12m_max_recovered,
        recovered_drawdown_p90=recovered_p90,
        latest_close=float(close.iloc[-1]),
        latest_ema200=_number(frame["ema200"].iloc[-1]) if "ema200" in frame else None,
        years=years,
    )
    if str(data_quality.get("data_quality_status") or "正常") != "正常":
        state = "行情口径待复核"
        reason = "行情价格口径存在异常信号，先复核拆股、复权或代码映射后再使用回撤结论。"
    return {
        "symbol": symbol,
        "years": years,
        "data_status": "OK",
        "latest_data_date": _latest_date_text(frame),
        "latest_date": _latest_date_text(frame),
        "latest_close": float(close.iloc[-1]),
        "latest_close_used": float(close.iloc[-1]),
        "rolling_2y_peak": float(running_peak.max()),
        "current_drawdown_pct": current_drawdown,
        "max_drawdown_2y_pct": max_drawdown,
        "max_recovered_drawdown_pct": max_recovered,
        "max_recovered_drawdown_background_pct": max_recovered,
        "current_regime_start_date": _date_text(current_regime_start),
        "current_regime_max_recovered_drawdown_pct": current_regime_max_recovered,
        "recent_6m_max_recovered_drawdown_pct": recent_6m_max_recovered,
        "recent_12m_max_recovered_drawdown_pct": recent_12m_max_recovered,
        "recovered_drawdown_p90_pct": recovered_p90,
        "drawdown_percentiles": percentiles,
        "current_drawdown_rank": rank,
        "recovery_stats": recovery_stats,
        "new_high_pullback_stats": pullback_stats,
        "trend_drawdown_stats": trend_stats,
        "uptrend_max_recovered_drawdown_pct": trend_stats.get("uptrend_max_recovered_drawdown_pct"),
        "uptrend_median_drawdown_pct": trend_stats.get("uptrend_median_drawdown_pct"),
        "uptrend_p90_drawdown_pct": trend_stats.get("uptrend_p90_drawdown_pct"),
        "data_quality": data_quality,
        "data_quality_status": data_quality.get("data_quality_status"),
        "price_column_used": data_quality.get("price_column_used"),
        "raw_close": data_quality.get("raw_close"),
        "adjusted_close": data_quality.get("adjusted_close"),
        "split_factor": data_quality.get("split_factor"),
        "data_source": data_quality.get("data_source"),
        "cache_table": data_quality.get("cache_table"),
        "cache_latest_date": data_quality.get("cache_latest_date"),
        "is_consistent_with_current_quote": data_quality.get("is_consistent_with_current_quote"),
        "suspected_price_multiplier_anomaly": data_quality.get("suspected_price_multiplier_anomaly"),
        "drawdown_state": state,
        "drawdown_state_reason": reason,
        "episodes": [asdict(episode) for episode in episodes],
        "similar_drawdown_cases": _similar_drawdown_cases(frame, current_drawdown),
    }


def _normalize_history(history: pd.DataFrame | None) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame(columns=["date", "price", "open", "high", "low", "close", "volume"])
    frame = history.copy()
    if "date" not in frame.columns:
        return pd.DataFrame(columns=["date", "price", "open", "high", "low", "close", "volume"])
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    price_source = None
    for candidate in ("adjusted_close", "adj_close", "adjClose", "adjustedClose", "close"):
        if candidate in frame.columns:
            price_source = candidate
            break
    if price_source is None:
        return pd.DataFrame(columns=["date", "price", "open", "high", "low", "close", "volume"])
    frame["price"] = pd.to_numeric(frame[price_source], errors="coerce")
    frame["raw_close"] = pd.to_numeric(frame["close"], errors="coerce") if "close" in frame.columns else None
    adjusted_source = next((column for column in ("adjusted_close", "adj_close", "adjClose", "adjustedClose") if column in frame.columns), None)
    frame["adjusted_close"] = pd.to_numeric(frame[adjusted_source], errors="coerce") if adjusted_source else None
    for column in ("open", "high", "low", "close", "volume"):
        if column not in frame.columns:
            frame[column] = None
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "price"])
    frame = frame[frame["price"] > 0].sort_values("date").drop_duplicates(subset=["date"], keep="last")
    if frame.empty:
        return frame
    frame["ema50"] = frame["price"].ewm(span=50, adjust=False, min_periods=50).mean()
    frame["ema200"] = frame["price"].ewm(span=200, adjust=False, min_periods=200).mean()
    frame["ema50_slope"] = frame["ema50"].diff(10)
    frame["regime"] = frame.apply(_regime_label, axis=1)
    frame.attrs["price_column_used"] = "adjusted_close" if price_source != "close" else "close"
    frame.attrs["price_column_raw"] = price_source
    frame.attrs["adjusted_column_raw"] = adjusted_source or ""
    return frame.reset_index(drop=True)


def _limit_years(frame: pd.DataFrame, *, years: int, now: datetime | None) -> pd.DataFrame:
    if frame.empty:
        return frame
    end = (now.date() if now else frame["date"].max().date())
    start = pd.Timestamp(end - timedelta(days=max(1, int(years or 3)) * 365))
    return frame[frame["date"] >= start].reset_index(drop=True)


def _detect_episodes(frame: pd.DataFrame) -> list[DrawdownEpisode]:
    episodes: list[DrawdownEpisode] = []
    if frame.empty:
        return episodes
    peak_idx = 0
    peak_price = float(frame.loc[0, "price"])
    in_episode = False
    trough_idx = 0
    trough_price = peak_price
    episode_peak_idx = 0
    episode_peak_price = peak_price
    for idx in range(1, len(frame)):
        price = float(frame.loc[idx, "price"])
        if price > peak_price:
            if in_episode:
                episodes.append(_episode_from_indices(frame, episode_peak_idx, trough_idx, idx, recovered=True))
                in_episode = False
            peak_idx = idx
            peak_price = price
            trough_idx = idx
            trough_price = price
            continue
        drawdown_pct = (price / peak_price - 1.0) * 100.0
        if drawdown_pct <= EPISODE_THRESHOLD_PCT and not in_episode:
            in_episode = True
            episode_peak_idx = peak_idx
            episode_peak_price = peak_price
            trough_idx = idx
            trough_price = price
        if in_episode and price < trough_price:
            trough_idx = idx
            trough_price = price
    if in_episode:
        episodes.append(_episode_from_indices(frame, episode_peak_idx, trough_idx, None, recovered=False))
    return episodes


def _episode_from_indices(frame: pd.DataFrame, peak_idx: int, trough_idx: int, recovery_idx: int | None, *, recovered: bool) -> DrawdownEpisode:
    peak_date = frame.loc[peak_idx, "date"]
    trough_date = frame.loc[trough_idx, "date"]
    peak_price = float(frame.loc[peak_idx, "price"])
    trough_price = float(frame.loc[trough_idx, "price"])
    recovery_date = frame.loc[recovery_idx, "date"] if recovered and recovery_idx is not None else None
    return DrawdownEpisode(
        peak_date=_date_text(peak_date),
        peak_price=peak_price,
        trough_date=_date_text(trough_date),
        trough_price=trough_price,
        drawdown_pct=(trough_price / peak_price - 1.0) * 100.0,
        days_to_trough=max(0, int((trough_date - peak_date).days)),
        recovered=bool(recovered),
        recovery_date=_date_text(recovery_date) if recovery_date is not None else "",
        days_to_recover=max(0, int((recovery_date - peak_date).days)) if recovery_date is not None else None,
        new_high_after_recovery=bool(recovered),
        regime_at_peak=str(frame.loc[peak_idx, "regime"] if "regime" in frame else "数据不足"),
    )


def _drawdown_percentiles(drawdowns: list[float]) -> dict[str, float | None]:
    return {
        "median_drawdown_pct": _negative_percentile(drawdowns, 50),
        "p75_drawdown_pct": _negative_percentile(drawdowns, 75),
        "p90_drawdown_pct": _negative_percentile(drawdowns, 90),
        "p95_drawdown_pct": _negative_percentile(drawdowns, 95),
    }


def _negative_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    series = pd.Series([abs(float(value)) for value in values if value is not None])
    if series.empty:
        return None
    return -float(series.quantile(percentile / 100.0))


def _recovery_stats(episodes: list[DrawdownEpisode]) -> dict[str, float | int | None]:
    days = [episode.days_to_recover for episode in episodes if episode.days_to_recover is not None]
    series = pd.Series(days, dtype="float64")
    return {
        "median_recovery_days": None if series.empty else float(series.quantile(0.5)),
        "p75_recovery_days": None if series.empty else float(series.quantile(0.75)),
        "max_recovery_days": None if series.empty else int(series.max()),
        "unrecovered_count": sum(1 for episode in episodes if not episode.recovered),
    }


def _new_high_pullback_stats(frame: pd.DataFrame) -> dict[str, float | int | None]:
    thresholds = (5, 10, 15, 20)
    values: dict[int, list[int]] = {threshold: [] for threshold in thresholds}
    if frame.empty:
        return _pullback_stats_result(values)
    closes = frame["price"].astype(float).tolist()
    dates = frame["date"].tolist()
    running_peak = -1.0
    new_high_indices: list[int] = []
    for idx, close in enumerate(closes):
        if close > running_peak:
            running_peak = close
            new_high_indices.append(idx)
    for index, peak_idx in enumerate(new_high_indices):
        next_high_idx = new_high_indices[index + 1] if index + 1 < len(new_high_indices) else len(closes)
        peak_price = closes[peak_idx]
        for threshold in thresholds:
            target = peak_price * (1.0 - threshold / 100.0)
            for scan_idx in range(peak_idx + 1, next_high_idx):
                if closes[scan_idx] <= target:
                    values[threshold].append(max(0, int((dates[scan_idx] - dates[peak_idx]).days)))
                    break
    return _pullback_stats_result(values)


def _pullback_stats_result(values: dict[int, list[int]]) -> dict[str, float | int | None]:
    result: dict[str, float | int | None] = {}
    for threshold in (5, 10, 15, 20):
        series = pd.Series(values.get(threshold) or [], dtype="float64")
        result[f"median_days_to_{threshold}pct_pullback"] = None if series.empty else float(series.quantile(0.5))
        result[f"count_{threshold}pct_pullback"] = int(series.count())
    return result


def _trend_drawdown_stats(episodes: list[DrawdownEpisode]) -> dict[str, float | None]:
    uptrend = [episode.drawdown_pct for episode in episodes if episode.regime_at_peak == "主升趋势"]
    recovered_uptrend = [episode.drawdown_pct for episode in episodes if episode.regime_at_peak == "主升趋势" and episode.recovered]
    return {
        "uptrend_max_recovered_drawdown_pct": min(recovered_uptrend) if recovered_uptrend else None,
        "uptrend_median_drawdown_pct": _negative_percentile(uptrend, 50),
        "uptrend_p90_drawdown_pct": _negative_percentile(uptrend, 90),
    }


def _data_quality_report(symbol: str, frame: pd.DataFrame, market_context: dict[str, Any] | None) -> dict[str, Any]:
    latest = frame.iloc[-1] if not frame.empty else {}
    latest_close_used = _number(latest.get("price") if hasattr(latest, "get") else None)
    raw_close = _number(latest.get("raw_close") if hasattr(latest, "get") else None)
    adjusted_close = _number(latest.get("adjusted_close") if hasattr(latest, "get") else None)
    quote_price = _number((market_context or {}).get("currentPrice"))
    latest_date = _latest_date_text(frame)
    price_column_used = str(frame.attrs.get("price_column_used") or "close")
    cache_latest_date = str((market_context or {}).get("historyLatestDate") or latest_date)
    quote_ratio = latest_close_used / quote_price if latest_close_used and quote_price else None
    ratio_to_quote = abs(quote_ratio - 1.0) * 100.0 if quote_ratio else None
    split_factor = raw_close / adjusted_close if raw_close and adjusted_close else None
    adjusted_raw_diff = abs(adjusted_close / raw_close - 1.0) * 100.0 if adjusted_close and raw_close else None
    rolling_median = _number(frame["price"].tail(min(len(frame), 252)).median()) if not frame.empty else None
    latest_vs_median = latest_close_used / rolling_median if latest_close_used and rolling_median else None
    suspected_multiplier = _is_suspicious_multiplier(quote_ratio) or _is_suspicious_scale_jump(latest_vs_median)
    scale_review_reason = (
        "价格绝对值较高且显著高于近一年中位数"
        if latest_close_used is not None and latest_close_used >= 300 and latest_vs_median is not None and latest_vs_median >= 2.0
        else None
    )
    status = "正常"
    if latest_close_used is None:
        status = "数据不足"
    elif ratio_to_quote is not None and ratio_to_quote > 30:
        status = "疑似拆股未处理" if _is_suspicious_multiplier(quote_ratio) else "疑似代码映射异常"
    elif adjusted_raw_diff is not None and adjusted_raw_diff > 30:
        status = "疑似复权口径异常"
    elif suspected_multiplier or scale_review_reason:
        status = "待人工复核"
    return {
        "symbol": symbol,
        "latest_close_used": latest_close_used,
        "latest_date": latest_date,
        "price_column_used": price_column_used,
        "raw_close": raw_close,
        "adjusted_close": adjusted_close,
        "split_factor": split_factor,
        "data_source": _data_source_text(market_context),
        "cache_table": "price_history" if market_context is not None else "传入数据",
        "cache_latest_date": cache_latest_date,
        "current_quote_price": quote_price,
        "quote_price_ratio": quote_ratio,
        "quote_price_diff_pct": ratio_to_quote,
        "is_consistent_with_current_quote": _quote_consistency_text(ratio_to_quote),
        "suspected_price_multiplier_anomaly": bool(suspected_multiplier),
        "price_scale_review_reason": scale_review_reason,
        "data_quality_status": status,
    }


def _data_source_text(market_context: dict[str, Any] | None) -> str:
    if market_context is None:
        return "传入数据"
    source = str(market_context.get("priceSource") or "").strip()
    return {
        "quote_snapshot": "本地行情快照",
        "price_history": "本地日线缓存",
        "missing": "数据缺失",
    }.get(source, source or "本地缓存")


def _quote_consistency_text(diff_pct: float | None) -> str:
    if diff_pct is None:
        return "无法判断"
    return "一致" if diff_pct <= 5 else "不一致"


def _is_suspicious_multiplier(price_ratio: float | None) -> bool:
    if price_ratio is None:
        return False
    return any(abs(price_ratio - factor) / factor <= 0.08 for factor in (10.0, 100.0, 0.1, 0.01))


def _is_suspicious_scale_jump(latest_vs_median: float | None) -> bool:
    if latest_vs_median is None:
        return False
    return latest_vs_median >= 4.0 or latest_vs_median <= 0.25


def _current_regime_start_date(frame: pd.DataFrame) -> pd.Timestamp | None:
    if frame.empty or "regime" not in frame.columns:
        return None
    latest_regime = str(frame["regime"].iloc[-1])
    if latest_regime != "主升趋势":
        lows = frame["price"].astype(float).rolling(252, min_periods=1).min()
        latest_low = lows.iloc[-1]
        matches = frame.index[frame["price"].astype(float) <= latest_low]
        if len(matches) > 0:
            return frame.loc[int(matches[-1]), "date"]
        return frame["date"].iloc[max(0, len(frame) - 252)]
    start_idx = len(frame) - 1
    while start_idx > 0 and str(frame.loc[start_idx - 1, "regime"]) == "主升趋势":
        start_idx -= 1
    return frame.loc[start_idx, "date"]


def _max_recovered_since(episodes: list[DrawdownEpisode], start_date: Any) -> float | None:
    if start_date is None:
        return None
    start = pd.to_datetime(start_date, errors="coerce")
    if pd.isna(start):
        return None
    values = [
        episode.drawdown_pct
        for episode in episodes
        if episode.recovered and pd.to_datetime(episode.peak_date, errors="coerce") >= start
    ]
    return min(values) if values else None


def _classify_current_drawdown(
    current: float,
    max_recovered: float | None,
    percentiles: dict[str, float | None],
    *,
    current_regime_max_recovered: float | None = None,
    recent_6m_max_recovered: float | None = None,
    recent_12m_max_recovered: float | None = None,
    recovered_drawdown_p90: float | None = None,
    latest_close: float,
    latest_ema200: float | None,
    years: int = 3,
) -> tuple[str, str, str]:
    if latest_ema200 is not None and latest_close < latest_ema200:
        return "趋势重评", "当前价格跌破长期趋势线，需要重新评估趋势。", "超过 95%"
    median = percentiles.get("median_drawdown_pct")
    p75 = percentiles.get("p75_drawdown_pct")
    p90 = percentiles.get("p90_drawdown_pct")
    p95 = percentiles.get("p95_drawdown_pct")
    active_thresholds = [
        ("本轮主升最大有效回撤", current_regime_max_recovered),
        ("近期最大有效回撤", recent_6m_max_recovered),
        ("近期最大有效回撤", recent_12m_max_recovered),
        ("有效回撤 90% 分位", recovered_drawdown_p90),
    ]
    breached = [label for label, value in active_thresholds if value is not None and current < value]
    if breached and p95 is not None and current <= p95:
        return "趋势重评", f"当前回撤超过{breached[0]}，且进入历史 95% 极端区间。", "超过 95%"
    if breached:
        return "极限洗盘", f"当前回撤超过{breached[0]}，近 {years} 年最大有效回撤仅作背景参考。", "90%-95%"
    if max_recovered is not None and current < max_recovered:
        return "极限洗盘", f"当前回撤已经超过近 {years} 年最大有效回撤；该指标仅作背景参考，需重点复核。", "90%-95%"
    if p95 is not None and current <= p95:
        return "趋势重评", "当前回撤超过历史 95% 分位，需要重新评估趋势。", "超过 95%"
    if p90 is not None and current <= p90:
        return "极限洗盘", "当前回撤接近历史极端区间，重点观察是否修复。", "90%-95%"
    if p75 is not None and current <= p75:
        return "深度洗盘", "当前回撤处于历史 75%-90% 区间。", "75%-90%"
    if median is not None and current <= median:
        return "正常洗盘", "当前回撤处于历史 50%-75% 区间。", "50%-75%"
    return "浅回调", "当前回撤低于历史中位回撤。", "低于中位数"


def _similar_drawdown_cases(frame: pd.DataFrame, current_drawdown_pct: float) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    if frame.empty:
        return cases
    prices = frame["price"].astype(float)
    drawdowns = prices / prices.cummax() - 1.0
    for idx, value in enumerate(drawdowns * 100.0):
        if abs(float(value) - current_drawdown_pct) > 2.0:
            continue
        row = {
            "date": _date_text(frame.loc[idx, "date"]),
            "drawdown_pct": float(value),
            "return_10d_pct": _forward_return(prices, idx, 10),
            "return_20d_pct": _forward_return(prices, idx, 20),
            "return_60d_pct": _forward_return(prices, idx, 60),
            "made_new_high": bool(prices.iloc[idx:].max() > prices.cummax().iloc[idx]),
        }
        cases.append(row)
        if len(cases) >= 8:
            break
    return cases


def _forward_return(prices: pd.Series, idx: int, days: int) -> float | None:
    target = idx + days
    if target >= len(prices):
        return None
    return float((prices.iloc[target] / prices.iloc[idx] - 1.0) * 100.0)


def _regime_label(row: pd.Series) -> str:
    close = _number(row.get("price"))
    ema50 = _number(row.get("ema50"))
    ema200 = _number(row.get("ema200"))
    slope = _number(row.get("ema50_slope"))
    if close is None or ema50 is None or ema200 is None or slope is None:
        return "数据不足"
    if close > ema50 > ema200 and slope > 0:
        return "主升趋势"
    if close < ema50 < ema200:
        return "下跌"
    return "震荡"


def _insufficient_profile(symbol: str, years: int, reason: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "years": years,
        "data_status": "DATA_INSUFFICIENT",
        "data_status_label": "数据不足",
        "drawdown_state": "数据不足",
        "drawdown_state_reason": reason,
        "current_drawdown_pct": None,
        "max_drawdown_2y_pct": None,
        "max_recovered_drawdown_pct": None,
        "drawdown_percentiles": _drawdown_percentiles([]),
        "current_drawdown_rank": "数据不足",
        "recovery_stats": _recovery_stats([]),
        "new_high_pullback_stats": _pullback_stats_result({5: [], 10: [], 15: [], 20: []}),
        "trend_drawdown_stats": _trend_drawdown_stats([]),
        "uptrend_max_recovered_drawdown_pct": None,
        "uptrend_median_drawdown_pct": None,
        "uptrend_p90_drawdown_pct": None,
        "episodes": [],
        "similar_drawdown_cases": [],
    }


def _latest_date_text(frame: pd.DataFrame) -> str:
    if frame.empty or "date" not in frame.columns:
        return ""
    return _date_text(frame["date"].max())


def _date_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.date().isoformat()


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_text(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "数据不足"
    return f"{number:.1f}%"


def _cache_expired(fetched_at: datetime, *, now: datetime | None = None) -> bool:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return (current - fetched_at.astimezone(timezone.utc)).total_seconds() > PROFILE_CACHE_TTL_SECONDS
