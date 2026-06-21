from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import escape
from typing import Any

import pandas as pd


ACCEPTANCE_CONFIRMED = "ACCEPTANCE_CONFIRMED"
FORMING = "FORMING"
UNCONFIRMED = "UNCONFIRMED"
FAILED = "FAILED"
OVEREXTENDED_SUPPORT_READ = "OVEREXTENDED_SUPPORT_READ"
DATA_MISSING = "DATA_MISSING"

STATUS_LABELS = {
    ACCEPTANCE_CONFIRMED: "已确认",
    FORMING: "形成中",
    UNCONFIRMED: "未确认",
    FAILED: "失效",
    OVEREXTENDED_SUPPORT_READ: "脱离观察区",
    DATA_MISSING: "数据不足",
}


def _unknown_status_label(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    if all(char.isascii() and (char.isalnum() or char in {"_", "-", "^", ".", " ", "&"}) for char in text):
        return fallback
    return text


@dataclass(frozen=True)
class VolumePriceAcceptanceSnapshot:
    volume_price_status: str
    volume_price_score: float
    volume_ratio: float | None
    volume_ma20: float | None
    volume_regime: str
    volume_regime_cn: str
    volume_interpretation_cn: str
    close_position: float | None
    candle_signal_cn: str
    volume_signal_cn: str
    support_signal_cn: str
    confirmation_signal_cn: str
    distribution_count_10d: int
    acceptance_reason_cn: str
    zone_source: str = "upstream"
    risk_deductions: list[str] = field(default_factory=list)
    volume_price_checked_at: str | None = None
    latest_volume: float | None = None
    volume_source: str | None = None

    @property
    def status_label(self) -> str:
        if self.volume_price_status == FORMING and self.volume_price_score < 55:
            return "初步承接，尚未确认"
        return STATUS_LABELS.get(self.volume_price_status, _unknown_status_label(self.volume_price_status, "量价待确认"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"status_label": self.status_label}


def evaluate_volume_price_acceptance(
    *,
    ticker: str = "",
    daily_bars: pd.DataFrame | None = None,
    technicals: dict[str, Any] | None = None,
    entry_context: dict[str, Any] | None = None,
    checked_at: datetime | None = None,
) -> VolumePriceAcceptanceSnapshot:
    source = {**(entry_context or {}), **(technicals or {})}
    bars = _normalize_bars(daily_bars)
    latest = _latest_bar(bars)
    previous = _previous_bar(bars)

    close = _first_number(
        latest.get("close") if latest else None,
        _value(source, "close", "current_price", "currentPrice", "price"),
    )
    current_price = _first_number(_value(source, "current_price", "currentPrice", "price"), close)
    open_price = _first_number(latest.get("open") if latest else None, _value(source, "open", "day_open", "dayOpen"), close)
    high = _first_number(latest.get("high") if latest else None, _value(source, "high", "day_high", "dayHigh"), close)
    low = _first_number(latest.get("low") if latest else None, _value(source, "low", "day_low", "dayLow"), close)

    observation_low = _first_number(
        _value(
            source,
            "observation_low",
            "observationLow",
            "near_term_repair_zone_low",
            "nearTermRepairZoneLow",
            "technical_pullback_zone_low",
            "technicalPullbackZoneLow",
            "effective_technical_entry_zone_low",
            "effectiveTechnicalEntryZoneLow",
            "support_watch_zone_low",
            "supportWatchZoneLow",
        )
    )
    observation_high = _first_number(
        _value(
            source,
            "observation_high",
            "observationHigh",
            "near_term_repair_zone_high",
            "nearTermRepairZoneHigh",
            "technical_pullback_zone_high",
            "technicalPullbackZoneHigh",
            "effective_technical_entry_zone_high",
            "effectiveTechnicalEntryZoneHigh",
            "support_watch_zone_high",
            "supportWatchZoneHigh",
        )
    )
    zone_source = _zone_source(source, observation_low=observation_low, observation_high=observation_high)
    support_line = _first_number(
        _value(source, "support_line", "supportLine", "support_watch_zone_low", "supportWatchZoneLow", "recent_swing_low", "recentSwingLow"),
        observation_low,
    )
    invalid_line = _first_number(_value(source, "invalid_line", "invalidLine", "invalidation_price", "invalidationPrice"), support_line)
    confirm_line = _first_number(_value(source, "confirm_line", "confirmLine", "confirmation_price", "confirmationPrice"))
    ema20 = _first_number(_value(source, "ema20"))
    ema50 = _first_number(_value(source, "ema50"))
    latest_bar_volume = _positive_number(latest.get("volume") if latest else None)
    context_volume = _first_number(_value(source, "volume", "latest_volume", "latestVolume", "quoteVolume"))
    latest_volume = latest_bar_volume or context_volume
    latest_volume_source = "daily_cache" if latest_bar_volume is not None else ("context" if context_volume is not None else "unavailable")
    volume_ma20 = _volume_ma20(bars) or _first_number(_value(source, "volume_ma20", "volumeMa20", "avg_volume", "avgVolume"))
    volume_ratio = _safe_ratio(latest_volume, volume_ma20)
    volume_regime, volume_regime_cn = _volume_regime(volume_ratio)

    if close is None or low is None or high is None or support_line is None:
        missing = []
        if close is None:
            missing.append("缺少收盘价 / 当前价")
        if low is None or high is None:
            missing.append("缺少日线高低点")
        if support_line is None:
            missing.append("缺少支撑线 / 观察区下沿")
        return _snapshot(
            DATA_MISSING,
            0,
            volume_ratio,
            volume_ma20,
            None,
            "K线数据不足",
            "量能待补",
            "支撑待确认",
            "确认线待补",
            0,
            "；".join(missing) or "缺少核心日线数据，暂时无法判断量价承接。",
            volume_regime=volume_regime,
            volume_regime_cn=volume_regime_cn,
            zone_source=zone_source,
            checked_at=checked_at,
            latest_volume=latest_volume,
            volume_source=latest_volume_source,
        )

    close_position = _range_position(low=low, high=high, close=close)
    body_ratio, lower_shadow_ratio, upper_shadow_ratio = _candle_ratios(open_price, high, low, close)
    is_up_day = close >= open_price
    is_down_day = close < open_price
    prev_close = _first_number(previous.get("close") if previous else None)
    prev_open = _first_number(previous.get("open") if previous else None)
    gap_down = prev_close is not None and open_price < prev_close * 0.985
    gap_up = prev_close is not None and open_price > prev_close * 1.015
    distribution_day = bool(is_down_day and (volume_ratio or 0) >= 1.2 and (close_position or 0) <= 0.45)
    distribution_count_10d = _distribution_count_10d(bars)
    support_hold = close >= support_line and (invalid_line is None or close >= invalid_line)
    support_reclaim = low < support_line <= close
    breakout_confirmed = confirm_line is not None and close >= confirm_line
    in_observation = _in_zone(close, observation_low, observation_high)
    shrink_pullback = bool(volume_ratio is not None and volume_ratio <= 0.9 and support_hold and close <= (confirm_line or close))
    reversal_candle = bool((lower_shadow_ratio or 0) >= 0.35 and (close_position or 0) >= 0.55 and body_ratio <= 0.65)
    bullish_engulfing = bool(
        previous
        and is_up_day
        and prev_open is not None
        and prev_close is not None
        and prev_close < prev_open
        and close >= prev_open
        and open_price <= prev_close
    )
    failed_breakdown_reclaim = bool((low < support_line or (invalid_line is not None and low < invalid_line)) and support_hold)

    risk_deductions: list[str] = []
    failed = (
        (invalid_line is not None and close < invalid_line)
        or (close < support_line and (volume_ratio or 0) >= 1.3)
        or (distribution_day and observation_low is not None and close < observation_low)
        or (gap_down and (volume_ratio or 0) >= 1.3 and (close_position or 0) <= 0.35)
    )
    if distribution_day:
        risk_deductions.append("放量派发")
    if gap_down and (volume_ratio or 0) >= 1.3:
        risk_deductions.append("高量跳空下跌")
    if close < support_line:
        risk_deductions.append("收盘低于支撑")
    if invalid_line is not None and close < invalid_line:
        risk_deductions.append("收盘跌破失效线")

    overextended = _is_overextended(current_price, observation_high, source)
    score = _score(
        support_hold=support_hold,
        support_reclaim=support_reclaim,
        volume_ratio=volume_ratio,
        shrink_pullback=shrink_pullback,
        breakout_confirmed=breakout_confirmed,
        reversal_candle=reversal_candle,
        bullish_engulfing=bullish_engulfing,
        failed_breakdown_reclaim=failed_breakdown_reclaim,
        close=close,
        confirm_line=confirm_line,
        ema20=ema20,
        ema50=ema50,
        risk_deductions=risk_deductions,
        failed=failed,
    )

    if failed:
        status = FAILED
    elif overextended:
        status = OVEREXTENDED_SUPPORT_READ
    elif breakout_confirmed and volume_ratio is not None and volume_ratio >= 1.2 and (close_position or 0) >= 0.6 and not distribution_day:
        status = ACCEPTANCE_CONFIRMED
    elif (
        not distribution_day
        and not risk_deductions
        and (support_hold or support_reclaim)
        and in_observation
        and (shrink_pullback or reversal_candle or bullish_engulfing or failed_breakdown_reclaim)
    ):
        status = FORMING
    elif in_observation and (support_hold or close >= support_line):
        status = UNCONFIRMED
    else:
        status = UNCONFIRMED

    if status == FORMING and score < 40:
        status = UNCONFIRMED
    if volume_ratio is None and status in {ACCEPTANCE_CONFIRMED, FORMING}:
        status = UNCONFIRMED
        risk_deductions.append("成交量缺失")

    candle_signal = _candle_signal_cn(
        reversal_candle=reversal_candle,
        bullish_engulfing=bullish_engulfing,
        failed_breakdown_reclaim=failed_breakdown_reclaim,
        gap_down=gap_down,
        gap_up=gap_up,
        close_position=close_position,
        is_up_day=is_up_day,
    )
    volume_signal = _volume_signal_cn(volume_ratio, shrink_pullback=shrink_pullback, distribution_day=distribution_day)
    support_signal = _support_signal_cn(
        status=status,
        support_hold=support_hold,
        support_reclaim=support_reclaim,
        close=close,
        support_line=support_line,
        invalid_line=invalid_line,
    )
    confirmation_signal = _confirmation_signal_cn(
        breakout_confirmed=breakout_confirmed,
        close=close,
        confirm_line=confirm_line,
        ema20=ema20,
        ema50=ema50,
    )
    reason = _reason_cn(
        status=status,
        support_signal=support_signal,
        volume_signal=volume_signal,
        candle_signal=candle_signal,
        confirmation_signal=confirmation_signal,
        risk_deductions=risk_deductions,
    )
    volume_interpretation = _volume_interpretation_cn(
        volume_regime=volume_regime,
        support_hold=support_hold,
        support_reclaim=support_reclaim,
        breakout_confirmed=breakout_confirmed,
        close=close,
        support_line=support_line,
        confirm_line=confirm_line,
        is_up_day=is_up_day,
        is_down_day=is_down_day,
        gap_down=gap_down,
        lower_shadow_ratio=lower_shadow_ratio,
        close_position=close_position,
    )

    return _snapshot(
        status,
        score,
        volume_ratio,
        volume_ma20,
        close_position,
        candle_signal,
        volume_signal,
        support_signal,
        confirmation_signal,
        distribution_count_10d,
        reason,
        volume_regime=volume_regime,
        volume_regime_cn=volume_regime_cn,
        volume_interpretation_cn=volume_interpretation,
        zone_source=zone_source,
        risk_deductions=risk_deductions,
        checked_at=checked_at,
        latest_volume=latest_volume,
        volume_source=latest_volume_source,
    )


def volume_price_acceptance_snapshot_fields(
    snapshot: VolumePriceAcceptanceSnapshot,
    *,
    checked_at: datetime | str | None = None,
) -> dict[str, Any]:
    checked_text = checked_at if isinstance(checked_at, str) else _checked_at(checked_at)
    return {
        "volumePriceStatus": snapshot.volume_price_status,
        "volumePriceScore": snapshot.volume_price_score,
        "volumeRatio": snapshot.volume_ratio,
        "volumeMa20": snapshot.volume_ma20,
        "volumeRegime": snapshot.volume_regime,
        "volumeRegimeCn": snapshot.volume_regime_cn,
        "volumeInterpretationCn": snapshot.volume_interpretation_cn,
        "closePosition": snapshot.close_position,
        "candleSignalCn": snapshot.candle_signal_cn,
        "volumeSignalCn": snapshot.volume_signal_cn,
        "supportSignalCn": snapshot.support_signal_cn,
        "confirmationSignalCn": snapshot.confirmation_signal_cn,
        "distributionCount10d": snapshot.distribution_count_10d,
        "volumePriceReasonCn": snapshot.acceptance_reason_cn,
        "reasonCn": snapshot.acceptance_reason_cn,
        "reason_cn": snapshot.acceptance_reason_cn,
        "volumePriceZoneSource": snapshot.zone_source,
        "volumePriceCheckedAt": checked_text or snapshot.volume_price_checked_at,
    }


def volume_price_acceptance_hint_html(snapshot: VolumePriceAcceptanceSnapshot) -> str:
    score_text = "待补数据" if snapshot.volume_price_status == DATA_MISSING else f"{snapshot.volume_price_score:g}分"
    ratio_text = "缺失" if snapshot.volume_ratio is None else f"{snapshot.volume_ratio:.2f}x"
    ma_text = "缺失" if snapshot.volume_ma20 is None else _format_volume(snapshot.volume_ma20)
    return (
        '<div class="structure-entry-advisor volume-price-acceptance-advisor">'
        f"<strong>量价承接：{escape(snapshot.status_label)}｜{escape(score_text)}</strong>"
        f"<span>量能标签：{escape(snapshot.volume_regime_cn)}｜量比：{escape(ratio_text)}｜20日均量：{escape(ma_text)}</span>"
        f"<span>量能解释：{escape(snapshot.volume_interpretation_cn)}</span>"
        f"<span>K线：{escape(snapshot.candle_signal_cn)}｜支撑：{escape(snapshot.support_signal_cn)}</span>"
        f"<span>确认：{escape(snapshot.confirmation_signal_cn)}｜派发日：{snapshot.distribution_count_10d}</span>"
        f"<small>{escape(snapshot.acceptance_reason_cn)}</small>"
        "<small>仅作量价承接提示，不改变买入权限。</small>"
        "</div>"
    )


def _snapshot(
    status: str,
    score: float,
    volume_ratio: float | None,
    volume_ma20: float | None,
    close_position: float | None,
    candle_signal: str,
    volume_signal: str,
    support_signal: str,
    confirmation_signal: str,
    distribution_count_10d: int,
    reason: str,
    *,
    volume_regime: str | None = None,
    volume_regime_cn: str | None = None,
    volume_interpretation_cn: str | None = None,
    zone_source: str = "upstream",
    risk_deductions: list[str] | None = None,
    checked_at: datetime | None = None,
    latest_volume: float | None = None,
    volume_source: str | None = None,
) -> VolumePriceAcceptanceSnapshot:
    normalized_regime, normalized_regime_cn = _volume_regime(volume_ratio)
    return VolumePriceAcceptanceSnapshot(
        volume_price_status=status,
        volume_price_score=round(max(0.0, min(100.0, float(score or 0))), 1),
        volume_ratio=None if volume_ratio is None else round(volume_ratio, 2),
        volume_ma20=None if volume_ma20 is None else round(volume_ma20, 2),
        volume_regime=volume_regime or normalized_regime,
        volume_regime_cn=volume_regime_cn or normalized_regime_cn,
        volume_interpretation_cn=volume_interpretation_cn or "量能缺失，暂时无法判断放量/缩量结构。",
        close_position=None if close_position is None else round(close_position, 2),
        candle_signal_cn=candle_signal,
        volume_signal_cn=volume_signal,
        support_signal_cn=support_signal,
        confirmation_signal_cn=confirmation_signal,
        distribution_count_10d=int(distribution_count_10d or 0),
        acceptance_reason_cn=reason,
        zone_source=zone_source,
        risk_deductions=_dedupe(risk_deductions or []),
        volume_price_checked_at=_checked_at(checked_at),
        latest_volume=None if latest_volume is None else round(latest_volume, 2),
        volume_source=volume_source,
    )


def _score(
    *,
    support_hold: bool,
    support_reclaim: bool,
    volume_ratio: float | None,
    shrink_pullback: bool,
    breakout_confirmed: bool,
    reversal_candle: bool,
    bullish_engulfing: bool,
    failed_breakdown_reclaim: bool,
    close: float,
    confirm_line: float | None,
    ema20: float | None,
    ema50: float | None,
    risk_deductions: list[str],
    failed: bool,
) -> float:
    if failed:
        base = 25.0 if support_reclaim else 15.0
    else:
        base = 0.0
        if support_hold:
            base += 20
        if support_reclaim:
            base += 5
        if volume_ratio is None:
            base += 8
        elif shrink_pullback:
            base += 22
        elif volume_ratio >= 1.2 and close >= (confirm_line or close):
            base += 25
        elif volume_ratio >= 1.0:
            base += 15
        else:
            base += 14
        if reversal_candle:
            base += 12
        if bullish_engulfing:
            base += 14
        if failed_breakdown_reclaim:
            base += 10
        if not (reversal_candle or bullish_engulfing or failed_breakdown_reclaim):
            base += 6
        if breakout_confirmed:
            base += 12
        if ema20 is not None and close > ema20:
            base += 4
        if ema50 is not None and close > ema50:
            base += 4
    penalty_map = {
        "放量派发": 20,
        "高量跳空下跌": 20,
        "收盘低于支撑": 25,
        "收盘跌破失效线": 40,
        "成交量缺失": 15,
    }
    return base - sum(penalty_map.get(item, 0) for item in risk_deductions)


def _normalize_bars(daily_bars: pd.DataFrame | None) -> pd.DataFrame:
    if daily_bars is None or daily_bars.empty:
        return pd.DataFrame()
    frame = daily_bars.copy()
    lower_map = {column: str(column).strip().lower() for column in frame.columns}
    frame = frame.rename(columns=lower_map)
    if "date" in frame.columns:
        frame = frame.sort_values("date")
    for column in ("open", "high", "low", "close", "volume"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["close"]) if "close" in frame.columns else pd.DataFrame()


def _latest_bar(frame: pd.DataFrame) -> dict[str, Any] | None:
    if frame.empty:
        return None
    return frame.iloc[-1].to_dict()


def _previous_bar(frame: pd.DataFrame) -> dict[str, Any] | None:
    if len(frame) < 2:
        return None
    return frame.iloc[-2].to_dict()


def _volume_ma20(frame: pd.DataFrame) -> float | None:
    if frame.empty or "volume" not in frame.columns:
        return None
    volumes = [float(value) for value in frame["volume"].tail(20).tolist() if _positive_number(value) is not None]
    if not volumes:
        return None
    return sum(volumes) / len(volumes)


def _distribution_count_10d(frame: pd.DataFrame) -> int:
    if frame.empty or not {"open", "high", "low", "close", "volume"}.issubset(set(frame.columns)):
        return 0
    window = frame.tail(20).copy()
    volumes = pd.to_numeric(window["volume"], errors="coerce")
    ma20 = volumes.rolling(20, min_periods=5).mean()
    count = 0
    for _, row in window.tail(10).iterrows():
        volume = _positive_number(row.get("volume"))
        avg = _positive_number(ma20.loc[row.name]) if row.name in ma20.index else None
        ratio = _safe_ratio(volume, avg)
        close_position = _range_position(low=row.get("low"), high=row.get("high"), close=row.get("close"))
        if row.get("close") < row.get("open") and (ratio or 0) >= 1.2 and (close_position or 0) <= 0.45:
            count += 1
    return count


def _candle_ratios(open_price: float, high: float, low: float, close: float) -> tuple[float, float, float]:
    width = high - low
    if width <= 0:
        return 0.0, 0.0, 0.0
    body = abs(close - open_price) / width
    lower = (min(open_price, close) - low) / width
    upper = (high - max(open_price, close)) / width
    return max(0.0, body), max(0.0, lower), max(0.0, upper)


def _range_position(*, low: object, high: object, close: object) -> float | None:
    low_num = _first_number(low)
    high_num = _first_number(high)
    close_num = _first_number(close)
    if low_num is None or high_num is None or close_num is None or high_num <= low_num:
        return None
    return max(0.0, min(1.0, (close_num - low_num) / (high_num - low_num)))


def _in_zone(price: float, low: float | None, high: float | None) -> bool:
    if low is not None and price < low:
        return False
    if high is not None and price > high:
        return False
    return low is not None or high is not None


def _is_overextended(price: float | None, observation_high: float | None, source: dict[str, Any]) -> bool:
    if price is not None and observation_high is not None and price > observation_high:
        return True
    decision = str(_value(source, "decision", "radarDecision") or "").upper()
    position = str(_value(source, "price_position", "pricePosition", "zone_status", "zoneStatus") or "").upper()
    label = " ".join(str(_value(source, key) or "") for key in ("entry_display_label", "entryDisplayLabel", "entry_action_hint", "entryActionHint"))
    return decision == "BLOCK_CHASE" or position == "IN_CHASE_ZONE" or "追高" in label


def _zone_source(source: dict[str, Any], *, observation_low: float | None, observation_high: float | None) -> str:
    explicit_keys = ("observation_low", "observationLow", "observation_high", "observationHigh")
    if any(_number(_value(source, key)) is not None for key in explicit_keys):
        return "upstream"
    radar_keys = (
        "near_term_repair_zone_low",
        "nearTermRepairZoneLow",
        "near_term_repair_zone_high",
        "nearTermRepairZoneHigh",
        "technical_pullback_zone_low",
        "technicalPullbackZoneLow",
        "technical_pullback_zone_high",
        "technicalPullbackZoneHigh",
        "effective_technical_entry_zone_low",
        "effectiveTechnicalEntryZoneLow",
        "effective_technical_entry_zone_high",
        "effectiveTechnicalEntryZoneHigh",
        "support_watch_zone_low",
        "supportWatchZoneLow",
        "support_watch_zone_high",
        "supportWatchZoneHigh",
    )
    if any(_number(_value(source, key)) is not None for key in radar_keys):
        return "radar"
    if observation_low is not None or observation_high is not None:
        return "fallback"
    return "missing"


def _candle_signal_cn(
    *,
    reversal_candle: bool,
    bullish_engulfing: bool,
    failed_breakdown_reclaim: bool,
    gap_down: bool,
    gap_up: bool,
    close_position: float | None,
    is_up_day: bool,
) -> str:
    if bullish_engulfing:
        return "阳线反包"
    if failed_breakdown_reclaim:
        return "破位后收回"
    if reversal_candle:
        return "下影线承接"
    if gap_down and (close_position or 0) <= 0.35:
        return "跳空低收"
    if gap_up:
        return "跳空上行"
    if close_position is not None and close_position >= 0.6:
        return "收盘偏强"
    if is_up_day:
        return "小阳整理"
    return "普通K线"


def _volume_regime(volume_ratio: float | None) -> tuple[str, str]:
    if volume_ratio is None:
        return "UNAVAILABLE", "量能缺失"
    if volume_ratio < 0.65:
        return "VERY_LOW", "明显缩量"
    if volume_ratio < 0.80:
        return "LOW", "缩量"
    if volume_ratio < 1.10:
        return "NORMAL", "量能普通"
    if volume_ratio < 1.30:
        return "MILD_EXPANSION", "温和放量"
    if volume_ratio < 2.00:
        return "EXPANSION", "放量"
    if volume_ratio < 3.00:
        return "STRONG_EXPANSION", "明显放量"
    return "EXTREME", "爆量"


def _volume_interpretation_cn(
    *,
    volume_regime: str,
    support_hold: bool,
    support_reclaim: bool,
    breakout_confirmed: bool,
    close: float,
    support_line: float,
    confirm_line: float | None,
    is_up_day: bool,
    is_down_day: bool,
    gap_down: bool,
    lower_shadow_ratio: float | None,
    close_position: float | None,
) -> str:
    reclaimed_or_held = support_hold or support_reclaim
    high_volume = volume_regime in {"MILD_EXPANSION", "EXPANSION", "STRONG_EXPANSION", "EXTREME"}
    shrink_volume = volume_regime in {"VERY_LOW", "LOW"}
    strong_close = (close_position or 0) >= 0.6
    long_lower_shadow = (lower_shadow_ratio or 0) >= 0.35 and (close_position or 0) >= 0.5
    if volume_regime == "UNAVAILABLE":
        return "缺少成交量，暂时无法判断放量/缩量结构。"
    if volume_regime == "EXTREME" and gap_down:
        if long_lower_shadow and reclaimed_or_held:
            return "爆量恐慌换手后收回，但仍需下一根K线确认。"
        return "爆量跳空下跌，需复核财报/消息冲击。"
    if high_volume and is_down_day and close < support_line:
        return "放量跌破支撑，承接失败。"
    if high_volume and breakout_confirmed and confirm_line is not None and close >= confirm_line:
        return "放量站上确认线，承接确认。"
    if high_volume and is_up_day and strong_close:
        return "放量收强，出现主动承接。"
    if shrink_volume and reclaimed_or_held:
        return "缩量回踩，支撑暂时守住。"
    if shrink_volume:
        return "缩量但承接不足，等待确认。"
    if volume_regime == "NORMAL":
        return "量能普通，需继续观察支撑和确认线。"
    return "量能活跃，但仍需结合观察区和确认线复核。"


def _volume_signal_cn(volume_ratio: float | None, *, shrink_pullback: bool, distribution_day: bool) -> str:
    if volume_ratio is None:
        return "量能缺失"
    if distribution_day:
        return f"放量下跌 {volume_ratio:.2f}x"
    if shrink_pullback:
        return f"缩量回踩 {volume_ratio:.2f}x"
    if volume_ratio >= 1.2:
        return f"放量活跃 {volume_ratio:.2f}x"
    if volume_ratio <= 0.9:
        return f"量能收缩 {volume_ratio:.2f}x"
    return f"量能普通 {volume_ratio:.2f}x"


def _support_signal_cn(
    *,
    status: str,
    support_hold: bool,
    support_reclaim: bool,
    close: float,
    support_line: float,
    invalid_line: float | None,
) -> str:
    if status == FAILED and invalid_line is not None and close < invalid_line:
        return f"跌破失效线 {invalid_line:.2f}"
    if status == FAILED and close < support_line:
        return f"跌破支撑 {support_line:.2f}"
    if support_reclaim:
        return f"盘中跌破后收回 {support_line:.2f}"
    if support_hold:
        return f"守住支撑 {support_line:.2f}"
    return f"支撑未确认 {support_line:.2f}"


def _confirmation_signal_cn(
    *,
    breakout_confirmed: bool,
    close: float,
    confirm_line: float | None,
    ema20: float | None,
    ema50: float | None,
) -> str:
    if breakout_confirmed and confirm_line is not None:
        return f"站上确认线 {confirm_line:.2f}"
    confirmations: list[str] = []
    if ema20 is not None and close > ema20:
        confirmations.append("EMA20")
    if ema50 is not None and close > ema50:
        confirmations.append("EMA50")
    if confirmations:
        return "站上" + " / ".join(confirmations)
    if confirm_line is not None:
        return f"未站上确认线 {confirm_line:.2f}"
    return "确认线待补"


def _reason_cn(
    *,
    status: str,
    support_signal: str,
    volume_signal: str,
    candle_signal: str,
    confirmation_signal: str,
    risk_deductions: list[str],
) -> str:
    if status == FAILED:
        return "放量跌破支撑/失效线，承接失败，暂停加仓。"
    if status == ACCEPTANCE_CONFIRMED:
        return "放量站上确认线，回踩承接确认。"
    if status == FORMING:
        if "缩量" in volume_signal or "收缩" in volume_signal:
            return "支撑暂时守住，回踩量能收缩，但未放量站上确认线，不构成买入确认。"
        return f"支撑暂时守住，{candle_signal}，{volume_signal}，但未放量站上确认线，不构成买入确认。"
    if status == OVEREXTENDED_SUPPORT_READ:
        return "价格已脱离回踩观察区，承接读数不构成低吸依据。"
    if risk_deductions:
        return "；".join(risk_deductions) + "，量价承接仍需复核。"
    return f"价格仍在观察区，但量价承接不足，等待下一根K线确认。{support_signal}；{volume_signal}；{candle_signal}；{confirmation_signal}。"


def _value(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source:
            return source.get(key)
    debug = source.get("debug")
    if isinstance(debug, dict):
        for key in keys:
            if key in debug:
                return debug.get(key)
    return None


def _first_number(*values: object) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _positive_number(value: object) -> float | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return number


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _checked_at(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _format_volume(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
