from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import escape
from typing import Any

import pandas as pd


ACCEPTANCE_CONFIRMED = "ACCEPTANCE_CONFIRMED"
ACCEPTANCE_FORMING = "ACCEPTANCE_FORMING"
ACCEPTANCE_UNCONFIRMED = "ACCEPTANCE_UNCONFIRMED"
ACCEPTANCE_FAILED = "ACCEPTANCE_FAILED"
DATA_MISSING = "DATA_MISSING"

STATUS_LABELS = {
    ACCEPTANCE_CONFIRMED: "承接确认",
    ACCEPTANCE_FORMING: "承接形成中",
    ACCEPTANCE_UNCONFIRMED: "承接未确认",
    ACCEPTANCE_FAILED: "承接失败",
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
class PullbackAcceptanceSnapshot:
    acceptance_status: str
    acceptance_score: float
    support_hold_status: str
    close_confirmation_status: str
    volume_confirmation_status: str
    relative_strength_confirmation_status: str
    vwap_confirmation_status: str
    acceptance_reasons: list[str] = field(default_factory=list)
    acceptance_warnings: list[str] = field(default_factory=list)
    next_acceptance_steps: list[str] = field(default_factory=list)
    acceptance_checked_at: str | None = None
    zone_source: str = "fallback"
    support_source: str = "missing"
    confirm_line_source: str = "missing"
    invalid_line_source: str = "missing"

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.acceptance_status, _unknown_status_label(self.acceptance_status, "承接待确认"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"status_label": self.status_label}


def evaluate_pullback_acceptance(
    *,
    ticker: str = "",
    technicals: dict[str, Any] | None = None,
    entry_context: dict[str, Any] | None = None,
    checked_at: datetime | None = None,
) -> PullbackAcceptanceSnapshot:
    source = {**(entry_context or {}), **(technicals or {})}
    close = _first_number(source, "close", "current_price", "currentPrice", "price")
    low = _first_number(source, "low", "day_low", "dayLow")
    open_price = _first_number(source, "open", "day_open", "dayOpen")
    high = _first_number(source, "high", "day_high", "dayHigh")
    if low is None:
        low = close
    if high is None:
        high = close

    support_info = _support_level_info(source)
    support = support_info["value"]
    if close is None or support is None:
        missing = []
        if close is None:
            missing.append("缺少收盘价 / 当前价")
        if support is None:
            missing.append("缺少失效线 / swing low / 支撑观察区")
        return _missing_snapshot(
            missing,
            checked_at=checked_at,
            zone_source=str(support_info["zone_source"]),
            support_source=str(support_info["support_source"]),
        )

    confirmation_price, confirm_line_source = _first_number_with_source(
        source,
        "confirmation_price",
        "confirmationPrice",
        "confirm_line",
        "confirmLine",
    )
    invalidation, invalid_line_source = _first_number_with_source(
        source,
        "invalidation_price",
        "invalidationPrice",
        "invalid_line",
        "invalidLine",
    )
    ema20 = _first_number(source, "ema20")
    ema50 = _first_number(source, "ema50")
    volume = _first_number(source, "volume")
    avg_volume = _first_number(source, "avg_volume", "avgVolume", "avg_volume_20d", "avgVolume20d")
    volume_ratio = _first_number(source, "volume_ratio", "volumeRatio")
    if volume_ratio is None and volume is not None and avg_volume:
        volume_ratio = volume / avg_volume
    close_position = _first_number(source, "close_position_in_range", "closePositionInRange")
    if close_position is None:
        close_position = _close_position(low=low, high=high, close=close)
    relative_strength = _relative_strength(source)
    vwap = _first_number(source, "vwap", "VWAP", "session_vwap", "sessionVwap")

    failed = False
    score = 0.0
    reasons: list[str] = []
    warnings: list[str] = []
    steps: list[str] = []

    support_status, support_score, support_failed, support_reason, support_warning = _support_confirmation(
        close=close,
        low=low,
        support=support,
        invalidation=invalidation,
        swing=_first_number(source, "recent_swing_low", "recentSwingLow"),
    )
    score += support_score
    failed = failed or support_failed
    reasons.append(support_reason)
    if support_warning:
        warnings.append(support_warning)

    close_status, close_score, close_reason = _close_confirmation(
        close=close,
        open_price=open_price,
        ema20=ema20,
        ema50=ema50,
        confirmation_price=confirmation_price,
        close_position=close_position,
    )
    score += close_score
    reasons.append(close_reason)

    volume_status, volume_score, volume_failed, volume_reason, volume_warning = _volume_confirmation(
        failed=failed,
        support_held=not support_failed,
        close_status=close_status,
        volume_ratio=volume_ratio,
        close=close,
        open_price=open_price,
    )
    score += volume_score
    failed = failed or volume_failed
    reasons.append(volume_reason)
    if volume_warning:
        warnings.append(volume_warning)

    rs_status, rs_score, rs_reason, rs_warning = _relative_strength_confirmation(relative_strength)
    score += rs_score
    reasons.append(rs_reason)
    if rs_warning:
        warnings.append(rs_warning)

    vwap_status, vwap_score, vwap_reason = _vwap_confirmation(
        close=close,
        vwap=vwap,
        close_position=close_position,
    )
    score += vwap_score
    reasons.append(vwap_reason)

    if close_status not in {"收盘确认", "收盘强势"}:
        steps.append("等待收盘站回确认线或关键均线。")
    if support_status != "支撑守住":
        steps.append("观察是否缩量回踩不破失效线 / swing low。")
    if rs_status != "相对强势":
        steps.append("等待相对强于 SPY / QQQ。")
    if volume_status not in {"缩量守支撑", "放量反包"}:
        steps.append("观察量能是否支持止跌或反包。")
    if not steps:
        steps.append("承接已较清楚，仍需结合买区提示、仓位计划和基本面复核。")

    score = round(max(0.0, min(100.0, score)), 1)
    if failed:
        status = ACCEPTANCE_FAILED
    elif score >= 80:
        status = ACCEPTANCE_CONFIRMED
    elif score >= 60:
        status = ACCEPTANCE_FORMING
    else:
        status = ACCEPTANCE_UNCONFIRMED

    if status == ACCEPTANCE_FAILED and not warnings:
        warnings.append("已有破位证据，不能把下跌直接当成买点。")

    return PullbackAcceptanceSnapshot(
        acceptance_status=status,
        acceptance_score=score,
        support_hold_status=support_status,
        close_confirmation_status=close_status,
        volume_confirmation_status=volume_status,
        relative_strength_confirmation_status=rs_status,
        vwap_confirmation_status=vwap_status,
        acceptance_reasons=_dedupe(reasons),
        acceptance_warnings=_dedupe(warnings),
        next_acceptance_steps=_dedupe(steps),
        acceptance_checked_at=_checked_at(checked_at),
        zone_source=str(support_info["zone_source"]),
        support_source=str(support_info["support_source"]),
        confirm_line_source=confirm_line_source,
        invalid_line_source=invalid_line_source,
    )


def pullback_acceptance_snapshot_fields(
    snapshot: PullbackAcceptanceSnapshot,
    *,
    checked_at: datetime | str | None = None,
) -> dict[str, Any]:
    checked_text = checked_at if isinstance(checked_at, str) else _checked_at(checked_at)
    return {
        "acceptanceStatus": snapshot.acceptance_status,
        "acceptanceScore": snapshot.acceptance_score,
        "acceptanceReasons": list(snapshot.acceptance_reasons),
        "acceptanceWarnings": list(snapshot.acceptance_warnings),
        "acceptanceCheckedAt": checked_text or snapshot.acceptance_checked_at,
        "pullbackZoneSource": snapshot.zone_source,
        "pullbackSupportSource": snapshot.support_source,
        "pullbackConfirmLineSource": snapshot.confirm_line_source,
        "pullbackInvalidLineSource": snapshot.invalid_line_source,
    }


def pullback_acceptance_hint_html(
    snapshot: PullbackAcceptanceSnapshot,
    *,
    context_lines: list[str] | None = None,
) -> str:
    score_text = "待补数据" if snapshot.acceptance_status == DATA_MISSING else f"{snapshot.acceptance_score:g}分"
    detail = "；".join(snapshot.acceptance_reasons[:2])
    warnings = "；".join(snapshot.acceptance_warnings[:2])
    steps = "；".join(snapshot.next_acceptance_steps[:2])
    warning_html = f"<span>{escape(warnings)}</span>" if warnings else ""
    context_html = "".join(f"<span>{escape(line)}</span>" for line in context_lines or [] if line)
    return (
        '<div class="structure-entry-advisor pullback-acceptance-advisor">'
        f"<strong>回踩承接确认：{escape(snapshot.status_label)}｜{escape(score_text)}</strong>"
        f"<span>支撑：{escape(snapshot.support_hold_status)}｜收盘：{escape(snapshot.close_confirmation_status)}｜量能：{escape(snapshot.volume_confirmation_status)}</span>"
        f"<span>相对强弱：{escape(snapshot.relative_strength_confirmation_status)}｜VWAP：{escape(snapshot.vwap_confirmation_status)}</span>"
        f"<small>{escape(detail)}</small>"
        f"{context_html}"
        f"{warning_html}"
        f"<small>下一步：{escape(steps)}</small>"
        "<small>仅作承接提示，不改变买入权限。</small>"
        "</div>"
    )


def pullback_acceptance_context_lines(
    snapshot: PullbackAcceptanceSnapshot | dict[str, Any],
    context: dict[str, Any] | None = None,
) -> list[str]:
    source = context or {}
    status = _snapshot_value(snapshot, "acceptance_status", "acceptanceStatus")
    lines: list[str] = []
    if status in {ACCEPTANCE_FORMING, ACCEPTANCE_UNCONFIRMED}:
        price = _first_number(source, "current_price", "currentPrice", "price", "close")
        observation_high = _first_number(
            source,
            "technical_pullback_zone_high",
            "technicalPullbackZoneHigh",
            "near_term_repair_zone_high",
            "nearTermRepairZoneHigh",
            "effective_technical_entry_zone_high",
            "effectiveTechnicalEntryZoneHigh",
            "technical_entry_zone_high",
            "technicalEntryZoneHigh",
            "support_watch_zone_high",
            "supportWatchZoneHigh",
        )
        if price is not None and observation_high is not None and price > observation_high:
            lines.append("支撑承接存在，但价格已脱离回踩观察区，不构成低吸确认。")
        if _is_chase_context(source):
            lines.append("买区提示仍为追高语境，承接读数仅代表支撑状态，不解除追高限制。")
    if status == ACCEPTANCE_FORMING and _technical_structure_status(source) == "BREAKDOWN_REVIEW":
        lines.append("破位复核结构：支撑暂未失效，不代表趋势修复；需重新站上确认线。")
    return _dedupe(lines)


def _snapshot_value(snapshot: PullbackAcceptanceSnapshot | dict[str, Any], *keys: str) -> str:
    for key in keys:
        if isinstance(snapshot, dict):
            value = snapshot.get(key)
        else:
            value = getattr(snapshot, key, None)
        if value not in (None, ""):
            return str(value)
    return ""


def _is_chase_context(source: dict[str, Any]) -> bool:
    decision = _first_text(source, "decision", "radarDecision").upper()
    price_position = _first_text(source, "price_position", "pricePosition", "zone_status", "zoneStatus").upper()
    if decision == "BLOCK_CHASE" or price_position == "IN_CHASE_ZONE":
        return True
    text = " ".join(
        _first_text(
            source,
            key,
        )
        for key in (
            "entry_display_label",
            "entryDisplayLabel",
            "entry_action_hint",
            "entryActionHint",
            "entry_display_reason",
            "entryDisplayReason",
            "radar_status",
            "radarStatus",
        )
    )
    return "追高" in text or "BLOCK_CHASE" in text


def _technical_structure_status(source: dict[str, Any]) -> str:
    return _first_text(source, "technical_structure_status", "technicalStructureStatus").upper()


def _support_confirmation(
    *,
    close: float,
    low: float | None,
    support: float,
    invalidation: float | None,
    swing: float | None,
) -> tuple[str, float, bool, str, str]:
    break_line = invalidation if invalidation is not None else swing
    if break_line is not None and close < break_line:
        return (
            "跌破失效线",
            0.0,
            True,
            f"支撑失败：收盘 {close:.2f} 跌破失效线 / swing {break_line:.2f}。",
            "收盘跌破关键支撑，承接失败。",
        )
    if swing is not None and low is not None and low < swing and close < swing:
        return (
            "跌破 swing low",
            0.0,
            True,
            f"支撑失败：低点和收盘均跌破 recent swing low {swing:.2f}。",
            "跌破 recent swing low，先按破位处理。",
        )
    if low is not None and low < support <= close:
        return (
            "盘中跌破后收回",
            22.0,
            False,
            f"支撑初步守住：盘中跌破 {support:.2f} 后收回。",
            "",
        )
    if low is not None and low >= support:
        return (
            "支撑守住",
            30.0,
            False,
            f"支撑守住：低点未跌破 {support:.2f}。",
            "",
        )
    if close >= support:
        return (
            "暂守支撑",
            20.0,
            False,
            f"支撑暂守：收盘仍高于 {support:.2f}。",
            "",
        )
    return (
        "支撑未确认",
        8.0,
        False,
        f"支撑未确认：当前仍低于 {support:.2f}。",
        "支撑证据不足。",
    )


def _close_confirmation(
    *,
    close: float,
    open_price: float | None,
    ema20: float | None,
    ema50: float | None,
    confirmation_price: float | None,
    close_position: float | None,
) -> tuple[str, float, str]:
    key_levels = [value for value in (confirmation_price, ema20, ema50) if value is not None]
    reclaimed = [value for value in key_levels if close >= value]
    strong_range = close_position is not None and close_position >= 0.6
    green_close = open_price is not None and close >= open_price
    if confirmation_price is not None and close >= confirmation_price and strong_range:
        return "收盘确认", 25.0, f"收盘站回确认线 {confirmation_price:.2f}，且收在日内偏强位置。"
    if reclaimed and strong_range:
        return "收盘强势", 20.0, "收盘站回关键均线 / 压力位，且日内位置偏强。"
    if strong_range or green_close:
        return "收盘改善", 14.0, "收盘位置有所改善，但尚未完成确认。"
    return "收盘未确认", 4.0, "尚未站回确认线或关键均线。"


def _volume_confirmation(
    *,
    failed: bool,
    support_held: bool,
    close_status: str,
    volume_ratio: float | None,
    close: float,
    open_price: float | None,
) -> tuple[str, float, bool, str, str]:
    if volume_ratio is None:
        return "量能缺失", 8.0, False, "量能：缺少 volume / avg volume，先用价格结构复核。", ""
    green_close = open_price is not None and close >= open_price
    if failed and volume_ratio >= 1.2:
        return "放量破位", 0.0, True, f"量能负面：放量 {volume_ratio:.2f}x 跌破支撑。", "放量跌破支撑，承接失败。"
    if support_held and volume_ratio <= 0.9:
        return "缩量守支撑", 18.0, False, f"量能正面：缩量 {volume_ratio:.2f}x 回踩且守住支撑。", ""
    if close_status in {"收盘确认", "收盘强势"} and green_close and volume_ratio >= 1.1:
        return "放量反包", 20.0, False, f"量能正面：放量 {volume_ratio:.2f}x 且收盘转强。", ""
    if volume_ratio >= 1.3 and not green_close:
        return "放量但收弱", 4.0, False, f"量能偏负面：放量 {volume_ratio:.2f}x 但收盘不强。", "放量但缺少收盘确认。"
    return "量能中性", 10.0, False, f"量能中性：{volume_ratio:.2f}x。", ""


def _relative_strength_confirmation(value: float | str | None) -> tuple[str, float, str, str]:
    if value is None:
        return "相对强弱缺失", 6.0, "相对强弱：缺少 SPY / QQQ 对比，暂不作为硬缺口。", ""
    if isinstance(value, str):
        text = value.strip()
        if any(token in text for token in ("强", "strong", "outperform")):
            return "相对强势", 15.0, f"相对强弱正面：{text}。", ""
        if any(token in text for token in ("弱", "underperform")):
            return "相对弱势", 2.0, f"相对强弱偏弱：{text}。", "相对弱于 SPY / QQQ。"
        return "相对强弱中性", 8.0, f"相对强弱：{text}。", ""
    if value >= 0.02:
        return "相对强势", 15.0, f"相对强于基准 {value:.1%}。", ""
    if value <= -0.02:
        return "相对弱势", 2.0, f"相对弱于基准 {value:.1%}。", "相对弱势扩大。"
    return "相对强弱中性", 8.0, f"相对强弱接近基准 {value:.1%}。", ""


def _vwap_confirmation(*, close: float, vwap: float | None, close_position: float | None) -> tuple[str, float, str]:
    if vwap is not None:
        if close >= vwap:
            return "站回 VWAP", 10.0, f"VWAP：收盘在 VWAP {vwap:.2f} 上方。"
        return "未收复 VWAP", 2.0, f"VWAP：尚未收复 VWAP {vwap:.2f}。"
    if close_position is not None and close_position >= 0.6:
        return "缺 VWAP，日线位置替代", 7.0, "VWAP 缺失：使用日线收盘位置替代，收在区间上半部。"
    return "VWAP 缺失", 4.0, "VWAP 缺失：第一版使用日线位置替代，不直接判定数据不足。"


def _support_level(source: dict[str, Any]) -> float | None:
    value = _support_level_info(source)["value"]
    return float(value) if value is not None else None


def _support_level_info(source: dict[str, Any]) -> dict[str, Any]:
    candidates: list[tuple[float, str, str]] = []
    for keys, zone_source in (
        (("invalidation_price", "invalidationPrice", "invalid_line", "invalidLine"), "radar"),
        (("recent_swing_low", "recentSwingLow"), "fallback"),
        (("near_term_repair_zone_low", "nearTermRepairZoneLow"), "radar"),
        (("support_watch_zone_low", "supportWatchZoneLow"), "radar"),
        (("technical_pullback_zone_low", "technicalPullbackZoneLow"), "radar"),
        (("technical_entry_zone_low", "technicalEntryZoneLow"), "radar"),
    ):
        value, source_key = _first_number_with_source(source, *keys)
        if value is not None and value > 0:
            candidates.append((value, source_key, zone_source))
    if not candidates:
        return {"value": None, "support_source": "missing", "zone_source": "fallback"}
    value, support_source, zone_source = max(candidates, key=lambda item: item[0])
    return {"value": value, "support_source": support_source, "zone_source": zone_source}


def _relative_strength(source: dict[str, Any]) -> float | str | None:
    text = source.get("relative_strength_status") or source.get("relativeStrengthStatus")
    if text:
        return str(text)
    values = [
        _first_number(source, "relative_strength_vs_SPY", "relativeStrengthVsSpy"),
        _first_number(source, "relative_strength_vs_QQQ", "relativeStrengthVsQqq"),
        _first_number(source, "relative_strength_vs_sector", "relativeStrengthVsSector"),
    ]
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return sum(valid) / len(valid)


def _close_position(*, low: float | None, high: float | None, close: float | None) -> float | None:
    if low is None or high is None or close is None or high <= low:
        return None
    return max(0.0, min(1.0, (close - low) / (high - low)))


def _missing_snapshot(
    reasons: list[str],
    *,
    checked_at: datetime | None,
    zone_source: str = "fallback",
    support_source: str = "missing",
) -> PullbackAcceptanceSnapshot:
    return PullbackAcceptanceSnapshot(
        acceptance_status=DATA_MISSING,
        acceptance_score=0.0,
        support_hold_status="数据不足",
        close_confirmation_status="数据不足",
        volume_confirmation_status="数据不足",
        relative_strength_confirmation_status="数据不足",
        vwap_confirmation_status="数据不足",
        acceptance_reasons=_dedupe(reasons),
        acceptance_warnings=["缺少核心价格 / K线 / 支撑字段，无法判断承接。"],
        next_acceptance_steps=["点击“更新技术”，补齐 K线、均线、swing、确认线和失效线。"],
        acceptance_checked_at=_checked_at(checked_at),
        zone_source=zone_source,
        support_source=support_source,
    )


def _first_number(mapping: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = _number(mapping.get(key))
        if number is not None:
            return number
    debug = mapping.get("debug")
    if isinstance(debug, dict):
        for key in keys:
            number = _number(debug.get(key))
            if number is not None:
                return number
    return None


def _first_number_with_source(mapping: dict[str, Any], *keys: str) -> tuple[float | None, str]:
    for key in keys:
        number = _number(mapping.get(key))
        if number is not None:
            return number, key
    debug = mapping.get("debug")
    if isinstance(debug, dict):
        for key in keys:
            number = _number(debug.get(key))
            if number is not None:
                return number, f"debug.{key}"
    return None, "missing"


def _first_text(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return str(value)
    debug = mapping.get("debug")
    if isinstance(debug, dict):
        for key in keys:
            value = debug.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _checked_at(value: datetime | str | None) -> str:
    if isinstance(value, str):
        return value
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
