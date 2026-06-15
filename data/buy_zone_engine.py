from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


WAIT_PULLBACK = "WAIT_PULLBACK"
WAIT_CONFIRMATION = "WAIT_CONFIRMATION"
ALLOW_SMALL_BUY = "ALLOW_SMALL_BUY"
ALLOW_ADD_ON_PULLBACK = "ALLOW_ADD_ON_PULLBACK"
BLOCK_CHASE = "BLOCK_CHASE"
RISK_REVIEW = "RISK_REVIEW"
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
AVOID = "AVOID"

ACTION_TEXT = {
    WAIT_PULLBACK: "等待回踩",
    WAIT_CONFIRMATION: "等待确认",
    ALLOW_SMALL_BUY: "允许小仓观察",
    ALLOW_ADD_ON_PULLBACK: "允许回踩复核加仓",
    BLOCK_CHASE: "禁止追高",
    RISK_REVIEW: "进入风控复核",
    DATA_INSUFFICIENT: "技术承接数据不足",
    AVOID: "暂不参与",
}

ZONE_TEXT = {
    "DEEP_ACCEPTANCE": "深度承接区",
    "PULLBACK_BUY": "回踩买区",
    "PULLBACK_WATCH": "技术回踩带内，可观察",
    "PULLBACK_UPPER_WATCH": "买区上沿 / 修复观察区",
    "REPAIR_WATCH": "修复观察区",
    "CONFIRMATION_REVIEW": "确认复核区",
    "CHASE_RISK": "追高禁区",
    "INVALIDATION": "失效风控区",
    "DATA_INSUFFICIENT": "技术承接数据不足",
}


@dataclass(frozen=True)
class BuyZoneContext:
    primary_zone: str
    primary_zone_text: str
    setup_score: float
    technical_structure_score: float
    volume_acceptance_score: float
    risk_reward_score: float
    support_zone_low: float | None
    support_zone_high: float | None
    pullback_zone_low: float | None
    pullback_zone_high: float | None
    left_probe_zone_low: float | None
    left_probe_zone_high: float | None
    observe_zone_low: float | None
    observe_zone_high: float | None
    zone_position: float | None
    zone_position_text: str
    confirmation_price: float | None
    invalidation_price: float | None
    chase_price: float | None
    breakout_reevaluation_price: float | None
    add_trigger_condition_text: str
    pause_new_condition_text: str
    current_action: str
    action_text: str
    existing_position_action_text: str
    no_position_action_text: str
    zone_selection_reason: str
    missing_fields: list[str] = field(default_factory=list)
    core_position_allowed: bool = True
    core_position_reason: str = ""
    current_price: float | None = None
    latest_volume: float | None = None
    avg_volume_20d: float | None = None
    volume_ratio: float | None = None
    volume_source: str = ""
    technical_data_source: str = ""
    upside_target: float | None = None
    target_source: str = ""
    target_quality: str = ""
    target_source_detail: str = ""
    raw_rr: float | None = None
    rr_score_capped: bool = False
    rr_cap_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskRewardAssessment:
    score: float
    upside_target: float | None
    target_source: str
    target_quality: str
    target_source_detail: str
    raw_rr: float | None
    rr_score_capped: bool
    rr_cap_reason: str


def build_buy_zone_context(
    source: dict[str, Any] | None = None,
    *,
    technicals: dict[str, Any] | None = None,
    volume_snapshot: dict[str, Any] | None = None,
) -> BuyZoneContext:
    data = _enrich_daily_technical_inputs({**(source or {}), **(technicals or {})})
    volume = _enrich_daily_volume_inputs(dict(volume_snapshot or {}), data)
    price = _first_number(data, "current_price", "currentPrice", "price", "close")
    support_low = _first_number(data, "support_zone_low", "deep_support_zone_low", "support_watch_zone_low", "recent_swing_low")
    support_high = _first_number(data, "support_zone_high", "deep_support_zone_high", "support_watch_zone_high", "recent_swing_low")
    pullback_low = _first_number(
        data,
        "effective_technical_entry_zone_low",
        "technical_pullback_zone_low",
        "technical_entry_zone_low",
        "near_term_repair_zone_low",
        "ma50",
        "ma20",
        "ema50",
        "ema20",
    )
    pullback_high = _first_number(
        data,
        "effective_technical_entry_zone_high",
        "technical_pullback_zone_high",
        "technical_entry_zone_high",
        "near_term_repair_zone_high",
        "ma20",
        "ma50",
        "ema20",
        "ema50",
    )
    repair_low = _first_number(data, "near_term_repair_zone_low", "technical_repair_zone_low")
    repair_high = _first_number(data, "near_term_repair_zone_high", "technical_repair_zone_high")
    raw_confirmation = _first_number(data, "confirmation_price", "radar_confirmation_price", "confirm_line")
    confirmation = _normalized_confirmation_price(data, price=price, raw_confirmation=raw_confirmation)
    if confirmation is None:
        confirmation = _first_number(data, "resistance_zone_low")
    invalidation = _first_number(data, "invalidation_price", "radar_invalidation_price", "invalid_line")
    chase = _first_number(data, "chase_above_price", "radar_chase_above_price", "chase_price")
    breakout_reevaluation = _breakout_reevaluation_price(data, price=price)
    ma20 = _first_number(data, "ma20", "ema20")
    ma50 = _first_number(data, "ma50", "ema50")
    ma200 = _first_number(data, "ma200", "ema200")
    atr = _first_number(data, "atr_14", "atr14")
    resistance = _first_number(
        data,
        "resistance_zone_high",
        "resistance_zone_low",
        "recent_swing_high",
        "recent_breakout_level",
        "confirmation_price",
    )
    if invalidation is None and support_low is not None:
        invalidation = support_low
    final_score = _first_number(data, "final_score", "finalScore")
    volume_status = str(
        _value(volume, "volume_price_status", "volumePriceStatus")
        or _value(data, "volume_price_status", "volumePriceStatus")
        or ""
    ).upper()
    volume_score_input = _first_number(volume, "volume_price_score", "volumePriceScore") or _first_number(
        data, "volume_price_score", "volumePriceScore"
    )
    volume_ratio = _first_number(volume, "volume_ratio", "volumeRatio") or _first_number(data, "volume_ratio", "volumeRatio")
    missing = _missing_fields(
        price=price,
        support_low=support_low,
        support_high=support_high,
        pullback_low=pullback_low,
        pullback_high=pullback_high,
        confirmation=confirmation,
        invalidation=invalidation,
        chase=chase,
        ma20=ma20,
        ma50=ma50,
        ma200=ma200,
        atr=atr,
        resistance=resistance,
        volume_status=volume_status,
        volume_ratio=volume_ratio,
        daily_ohlcv_present=bool(_daily_bars(data)),
        daily_ohlcv_count=_first_number(data, "daily_ohlcv_count", "dailyOhlcvCount"),
    )
    core_allowed = final_score is None or final_score >= 70
    core_reason = (
        ""
        if core_allowed
        else "综合评分低于70，系统不建议作为核心仓；小仓观察仍以技术承接和量价确认为准。"
    )
    if missing:
        return BuyZoneContext(
            primary_zone="DATA_INSUFFICIENT",
            primary_zone_text=ZONE_TEXT["DATA_INSUFFICIENT"],
            setup_score=0.0,
            technical_structure_score=0.0,
            volume_acceptance_score=0.0,
            risk_reward_score=0.0,
            support_zone_low=None,
            support_zone_high=None,
            pullback_zone_low=None,
            pullback_zone_high=None,
            left_probe_zone_low=None,
            left_probe_zone_high=None,
            observe_zone_low=None,
            observe_zone_high=None,
            zone_position=None,
            zone_position_text="技术承接数据不足",
            confirmation_price=confirmation,
            invalidation_price=invalidation,
            chase_price=chase,
            breakout_reevaluation_price=breakout_reevaluation,
            add_trigger_condition_text=_add_trigger_condition_text(confirmation, breakout_reevaluation),
            pause_new_condition_text=_pause_new_condition_text(None, invalidation, data),
            current_action=DATA_INSUFFICIENT,
            action_text=ACTION_TEXT[DATA_INSUFFICIENT],
            existing_position_action_text="已有持仓：技术承接数据不足，先控制新增买入并人工复核。",
            no_position_action_text="未持仓：技术承接数据不足，不给明确买入区。",
            zone_selection_reason="缺少历史K线、成交量或关键技术区间，不能用基本面或估值替代买区。",
            missing_fields=missing,
            core_position_allowed=core_allowed,
            core_position_reason=core_reason,
            current_price=price,
            latest_volume=_first_number(volume, "latest_volume", "latestVolume") or _first_number(data, "latest_volume", "volume"),
            avg_volume_20d=_first_number(volume, "volume_ma20", "avg_volume_20d", "avgVolume20d") or _first_number(data, "volume_ma20", "avg_volume_20d"),
            volume_ratio=volume_ratio,
            volume_source=str(_value(volume, "volume_source", "volumeSource") or _value(data, "volume_source", "volumeSource") or ""),
            technical_data_source=str(_value(data, "technical_data_source", "technicalDataSource") or ""),
        )

    left_probe_low, left_probe_high, observe_low, observe_high = _pullback_layers(pullback_low, pullback_high)
    zone_position = _zone_position(price, pullback_low, pullback_high)
    primary_zone = _primary_zone(
        price=price,
        support_low=support_low,
        support_high=support_high,
        pullback_low=pullback_low,
        pullback_high=pullback_high,
        repair_low=repair_low,
        repair_high=repair_high,
        confirmation=confirmation,
        invalidation=invalidation,
        chase=chase,
    )
    technical_score = _technical_structure_score(primary_zone)
    volume_score = _volume_acceptance_score(
        volume_status,
        volume_score_input,
        volume_ratio=volume_ratio,
        price=price,
        confirmation=confirmation,
        resistance=_first_number(data, "resistance_zone_low", "technical_resistance_price", "recent_breakout_level"),
        daily_return=_first_number(data, "daily_return_pct", "day_change_pct", "change_pct", "changePercent"),
        close_position=_first_number(data, "close_position", "closePosition", "close_position_in_range", "closePositionInRange"),
    )
    rr = _risk_reward_assessment(
        data=data,
        price=price,
        confirmation=confirmation,
        invalidation=invalidation,
        chase=chase,
        primary_zone=primary_zone,
    )
    rr_score = rr.score
    setup_score = round(technical_score * 0.45 + volume_score * 0.35 + rr_score * 0.20, 1)
    action = _current_action(primary_zone, setup_score, volume_status, volume_score, rr_score)
    return BuyZoneContext(
        primary_zone=primary_zone,
        primary_zone_text=ZONE_TEXT.get(primary_zone, "修复观察区"),
        setup_score=setup_score,
        technical_structure_score=technical_score,
        volume_acceptance_score=volume_score,
        risk_reward_score=rr_score,
        support_zone_low=support_low,
        support_zone_high=support_high,
        pullback_zone_low=pullback_low,
        pullback_zone_high=pullback_high,
        left_probe_zone_low=left_probe_low,
        left_probe_zone_high=left_probe_high,
        observe_zone_low=observe_low,
        observe_zone_high=observe_high,
        zone_position=zone_position,
        zone_position_text=_zone_position_text(zone_position),
        confirmation_price=confirmation,
        invalidation_price=invalidation,
        chase_price=chase,
        breakout_reevaluation_price=breakout_reevaluation,
        add_trigger_condition_text=_add_trigger_condition_text(confirmation, breakout_reevaluation),
        pause_new_condition_text=_pause_new_condition_text(pullback_low, invalidation, data),
        current_action=action,
        action_text=ACTION_TEXT[action],
        existing_position_action_text=_existing_position_action(action),
        no_position_action_text=_no_position_action(action),
        zone_selection_reason=_zone_reason(primary_zone, volume_status, rr_score, core_reason),
        missing_fields=[],
        core_position_allowed=core_allowed,
        core_position_reason=core_reason,
        current_price=price,
        latest_volume=_first_number(volume, "latest_volume", "latestVolume") or _first_number(data, "latest_volume", "volume"),
        avg_volume_20d=_first_number(volume, "volume_ma20", "avg_volume_20d", "avgVolume20d") or _first_number(data, "volume_ma20", "avg_volume_20d"),
        volume_ratio=volume_ratio,
        volume_source=str(_value(volume, "volume_source", "volumeSource") or _value(data, "volume_source", "volumeSource") or ""),
        technical_data_source=str(_value(data, "technical_data_source", "technicalDataSource") or ""),
        upside_target=rr.upside_target,
        target_source=rr.target_source,
        target_quality=rr.target_quality,
        target_source_detail=rr.target_source_detail,
        raw_rr=rr.raw_rr,
        rr_score_capped=rr.rr_score_capped,
        rr_cap_reason=rr.rr_cap_reason,
    )


def _missing_fields(**values: Any) -> list[str]:
    fields: list[str] = []
    for key in (
        "price",
        "support_low",
        "support_high",
        "pullback_low",
        "pullback_high",
        "confirmation",
        "invalidation",
        "ma20",
        "ma50",
        "ma200",
        "atr",
        "resistance",
    ):
        if values.get(key) is None:
            fields.append(_missing_label(key))
    daily_ohlcv_count = _number(values.get("daily_ohlcv_count"))
    technical_window_missing = any(
        field in fields
        for field in (
            "ma20",
            "ma50",
            "ma200",
            "atr_14",
            "volume_ratio",
        )
    )
    if values.get("daily_ohlcv_present") and daily_ohlcv_count is not None and daily_ohlcv_count < 200 and technical_window_missing:
        fields.insert(0, "daily_ohlcv_window")
    if not values.get("daily_ohlcv_present") and any(
        field in fields
        for field in (
            "support_zone_low",
            "support_zone_high",
            "pullback_zone_low",
            "pullback_zone_high",
            "ma20",
            "ma50",
            "ma200",
            "atr_14",
            "resistance_zone",
        )
    ):
        fields.insert(0, "daily_ohlcv")
    if not values.get("volume_status") or values.get("volume_status") == "DATA_MISSING":
        fields.append("volume_acceptance")
    if values.get("volume_ratio") is None:
        fields.append("volume_ratio")
    return fields


def _normalized_confirmation_price(data: dict[str, Any], *, price: float | None, raw_confirmation: float | None) -> float | None:
    if raw_confirmation is None:
        return None
    if not _is_fifty_two_week_high(data, raw_confirmation):
        return raw_confirmation
    near = _near_confirmation_candidate(data, price)
    return near


def _is_fifty_two_week_high(data: dict[str, Any], value: float | None) -> bool:
    target = _first_number(data, "fifty_two_week_high", "fiftyTwoWeekHigh", "yearHigh", "52_week_high")
    if value is None or target is None:
        return False
    return abs(value - target) <= max(0.05, target * 0.001)


def _breakout_reevaluation_price(data: dict[str, Any], *, price: float | None = None) -> float | None:
    high = _first_number(data, "fifty_two_week_high", "fiftyTwoWeekHigh", "yearHigh", "52_week_high")
    if high is not None:
        return high
    return _first_number(data, "breakout_reevaluation_price", "breakoutReevaluationPrice")


def _near_confirmation_candidate(data: dict[str, Any], price: float | None) -> float | None:
    candidates: list[float] = []
    for key in (
        "near_confirmation_price",
        "nearConfirmationPrice",
        "technical_resistance_price",
        "technicalResistancePrice",
        "resistance_zone_low",
        "resistanceZoneLow",
        "trend_reclaim_zone_low",
        "trendReclaimZoneLow",
        "recent_breakout_level",
        "recentBreakoutLevel",
        "recent_swing_high",
        "recentSwingHigh",
    ):
        value = _number(data.get(key))
        if value is None:
            continue
        if price is not None and value <= price * 1.0001:
            continue
        if price is not None and value > price * 1.18:
            continue
        candidates.append(value)
    for item in _resistance_level_items(data):
        value = _first_number(item, "price", "level", "value")
        if value is None:
            continue
        if price is not None and (value <= price * 1.0001 or value > price * 1.18):
            continue
        candidates.append(value)
    return min(candidates) if candidates else None


def _add_trigger_condition_text(confirmation: float | None, breakout_reevaluation: float | None) -> str:
    if confirmation is not None:
        return f"加仓触发：放量站上近端确认线 {_money(confirmation)} 后重新评估。"
    if breakout_reevaluation is not None:
        return f"加仓触发：52周高点 {_money(breakout_reevaluation)} 仅作为突破重估线，不是买入确认线。"
    return "加仓触发：等待近端压力位和量价承接补齐。"


def _pause_new_condition_text(pullback_low: float | None, invalidation: float | None, data: dict[str, Any]) -> str:
    trend_low = _first_number(data, "trend_critical_zone_low", "trendCriticalZoneLow", "support_zone_low", "supportZoneLow")
    trend_high = _first_number(data, "trend_critical_zone_high", "trendCriticalZoneHigh", "support_zone_high", "supportZoneHigh")
    deep_low = _first_number(data, "deep_panic_zone_low", "deepPanicZoneLow", "deep_support_zone_low", "deepSupportZoneLow")
    deep_high = _first_number(data, "deep_panic_zone_high", "deepPanicZoneHigh", "deep_support_zone_high", "deepSupportZoneHigh")
    parts: list[str] = []
    if pullback_low is not None:
        parts.append(f"跌破买区下沿 {_money(pullback_low)}：暂停新增")
    if invalidation is not None and (pullback_low is None or abs(invalidation - pullback_low) > max(0.05, pullback_low * 0.005)):
        parts.append(f"跌破 {_money(invalidation)}：买区失效，重新评估")
    if trend_low is not None or trend_high is not None:
        parts.append(f"跌破 {_range_money(trend_low, trend_high)}：趋势恶化，禁止继续摊低")
    if deep_low is not None or deep_high is not None:
        parts.append(f"{_range_money(deep_low, deep_high)}：极端风险/基本面复核区，不是自动买入区")
    return "；".join(parts) if parts else "暂停新增条件：跌破失效线或承接失败。"


def _range_money(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{_money(low)} - {_money(high)}"
    if low is not None:
        return _money(low)
    if high is not None:
        return _money(high)
    return "暂缺"


def _money(value: float | None) -> str:
    if value is None:
        return "暂缺"
    return f"${value:,.2f}"


def _primary_zone(
    *,
    price: float,
    support_low: float,
    support_high: float,
    pullback_low: float,
    pullback_high: float,
    repair_low: float | None,
    repair_high: float | None,
    confirmation: float,
    invalidation: float,
    chase: float | None,
) -> str:
    if price < invalidation:
        return "INVALIDATION"
    if chase is not None and price >= chase:
        return "CHASE_RISK"
    if price >= confirmation:
        return "CONFIRMATION_REVIEW"
    if _in_range(price, support_low, support_high):
        return "DEEP_ACCEPTANCE"
    if _in_range(price, pullback_low, pullback_high):
        _left_low, left_probe_high, _observe_low, observe_high = _pullback_layers(pullback_low, pullback_high)
        position = _zone_position(price, pullback_low, pullback_high)
        if position is not None and position > 0.75:
            return "PULLBACK_UPPER_WATCH"
        if price <= left_probe_high:
            return "PULLBACK_BUY"
        if price <= min(observe_high, confirmation):
            return "PULLBACK_WATCH"
        return "REPAIR_WATCH"
    if repair_low is not None and repair_high is not None and _in_range(price, repair_low, repair_high):
        return "REPAIR_WATCH"
    if price > pullback_high:
        return "REPAIR_WATCH"
    return "WAIT_PULLBACK"


def _pullback_layers(pullback_low: float, pullback_high: float) -> tuple[float, float, float, float]:
    low, high = sorted((pullback_low, pullback_high))
    width = max(high - low, 0.0)
    left_probe_high = low + width * 0.35
    observe_high = low + width * 0.75
    return low, left_probe_high, left_probe_high, observe_high


def _zone_position(price: float | None, zone_low: float | None, zone_high: float | None) -> float | None:
    if price is None or zone_low is None or zone_high is None:
        return None
    low, high = sorted((zone_low, zone_high))
    width = high - low
    if width <= 0:
        return None
    return round((price - low) / width, 4)


def _zone_position_text(position: float | None) -> str:
    if position is None:
        return "位置暂缺"
    if position < 0.35:
        return "买区下沿，允许小仓观察"
    if position <= 0.75:
        return "买区中段，等待承接"
    return "买区上沿 / 修复观察区，不主动新增"


def _technical_structure_score(primary_zone: str) -> float:
    return {
        "DEEP_ACCEPTANCE": 82.0,
        "PULLBACK_BUY": 78.0,
        "PULLBACK_WATCH": 63.0,
        "PULLBACK_UPPER_WATCH": 56.0,
        "REPAIR_WATCH": 58.0,
        "CONFIRMATION_REVIEW": 62.0,
        "CHASE_RISK": 18.0,
        "INVALIDATION": 5.0,
        "WAIT_PULLBACK": 45.0,
    }.get(primary_zone, 40.0)


def _volume_acceptance_score(
    status: str,
    explicit_score: float | None,
    *,
    volume_ratio: float | None = None,
    price: float | None = None,
    confirmation: float | None = None,
    resistance: float | None = None,
    daily_return: float | None = None,
    close_position: float | None = None,
) -> float:
    low_volume = volume_ratio is not None and volume_ratio < 0.7
    close_improved = (daily_return is not None and daily_return >= 0) or (close_position is not None and close_position >= 0.55)
    if low_volume and not close_improved:
        return min(42.0, explicit_score or 38.0)
    if low_volume and close_improved:
        return min(55.0, max(45.0, explicit_score or 50.0))
    if volume_ratio is not None and volume_ratio > 1.2 and confirmation is not None and price is not None and price >= confirmation:
        return max(80.0, explicit_score or 82.0)
    if volume_ratio is not None and volume_ratio > 1.0 and resistance is not None and price is not None and price >= resistance:
        return max(70.0, explicit_score or 72.0)
    if status == "ACCEPTANCE_CONFIRMED":
        return max(78.0, explicit_score or 82.0)
    if status == "FORMING":
        return max(45.0, min(72.0, explicit_score or 56.0))
    if status == "UNCONFIRMED":
        return min(48.0, explicit_score or 42.0)
    if status == "FAILED":
        return 0.0
    if status == "OVEREXTENDED_SUPPORT_READ":
        return 20.0
    return 0.0


def _risk_reward_assessment(
    *,
    data: dict[str, Any],
    price: float,
    confirmation: float,
    invalidation: float,
    chase: float | None,
    primary_zone: str,
) -> RiskRewardAssessment:
    if primary_zone in {"INVALIDATION", "CHASE_RISK"}:
        return RiskRewardAssessment(
            score=5.0 if primary_zone == "INVALIDATION" else 18.0,
            upside_target=None,
            target_source="",
            target_quality="NOT_APPLICABLE",
            target_source_detail="",
            raw_rr=None,
            rr_score_capped=False,
            rr_cap_reason="",
        )

    target, source, quality, detail = _resolve_rr_target(data, price=price, confirmation=confirmation, chase=chase)
    downside = price - invalidation
    upside = None if target is None else target - price
    if target is None or downside <= 0 or upside is None or upside <= 0:
        return RiskRewardAssessment(
            score=28.0,
            upside_target=target,
            target_source=source,
            target_quality=quality,
            target_source_detail=detail,
            raw_rr=None,
            rr_score_capped=False,
            rr_cap_reason="upside_or_downside_invalid",
        )

    raw_rr = upside / downside
    if raw_rr >= 2.0:
        score = 88.0
    elif raw_rr >= 1.4:
        score = 75.0
    elif raw_rr >= 1.0:
        score = 62.0
    elif raw_rr >= 0.6:
        score = 45.0
    else:
        score = 28.0

    cap = _target_quality_cap(quality)
    cap_reason = ""
    capped = False
    if cap is not None and score > cap:
        score = cap
        capped = True
        cap_reason = _target_quality_cap_reason(quality)

    return RiskRewardAssessment(
        score=score,
        upside_target=target,
        target_source=source,
        target_quality=quality,
        target_source_detail=detail,
        raw_rr=raw_rr,
        rr_score_capped=capped,
        rr_cap_reason=cap_reason,
    )


def _resolve_rr_target(
    data: dict[str, Any],
    *,
    price: float,
    confirmation: float | None,
    chase: float | None,
) -> tuple[float | None, str, str, str]:
    explicit = _first_valid_target_with_key(
        data,
        price,
        "manual_target_price",
        "manualTargetPrice",
        "target_price",
        "targetPrice",
    )
    if explicit is not None:
        key, value = explicit
        return value, key, "EXPLICIT_MANUAL_TARGET", key

    analyst = _first_valid_target_with_key(
        data,
        price,
        "analyst_median_target_price",
        "analystMedianTargetPrice",
        "analyst_target_price",
        "consensus_target_price",
        "consensusTargetPrice",
    )
    if analyst is not None:
        key, value = analyst
        return value, key, "ANALYST_MEDIAN_TARGET", key

    technical = _technical_resistance_candidate(data, price)
    if technical is not None:
        value, source, detail = technical
        return value, source, "TECH_RESISTANCE_HIGH", detail

    breakout = _first_valid_target_with_key(data, price, "recent_breakout_level", "recentBreakoutLevel", "swing_high_60d", "swingHigh60d")
    if breakout is not None:
        key, value = breakout
        return value, key, "SWING_HIGH_60D", key

    swing = _first_valid_target_with_key(data, price, "recent_swing_high", "recentSwingHigh", "swing_high", "swingHigh", "swing_high_20d", "swingHigh20d")
    if swing is not None:
        key, value = swing
        return value, key, "SWING_HIGH_20D", key

    fifty_two_week = _first_valid_target_with_key(data, price, "fifty_two_week_high", "fiftyTwoWeekHigh", "yearHigh", "52_week_high")
    if fifty_two_week is not None:
        key, value = fifty_two_week
        return value, key, "FIFTY_TWO_WEEK_HIGH", key

    if _valid_upside_target(confirmation, price):
        return confirmation, "confirmation_price", "CONFIRMATION_LINE", "confirmation_price"

    explicit_chase = _first_valid_target_with_key(data, price, "chase_price", "chase_above_price", "radar_chase_above_price")
    if explicit_chase is not None:
        key, value = explicit_chase
        return value, key, "CHASE_LINE", key
    if _valid_upside_target(chase, price):
        return chase, "chase_price", "CHASE_LINE", "chase_price"
    return None, "", "MISSING", ""


def _target_quality_cap(quality: str) -> float | None:
    return {
        "TECH_RESISTANCE_HIGH": 82.0,
        "SWING_HIGH": 70.0,
        "SWING_HIGH_60D": 75.0,
        "SWING_HIGH_20D": 70.0,
        "FIFTY_TWO_WEEK_HIGH": 65.0,
        "CONFIRMATION_LINE": 60.0,
        "CHASE_LINE": 55.0,
        "MISSING": 45.0,
    }.get(quality)


def _target_quality_cap_reason(quality: str) -> str:
    return {
        "TECH_RESISTANCE_HIGH": "target uses technical resistance; rr capped",
        "SWING_HIGH": "target uses swing high; rr capped",
        "SWING_HIGH_60D": "target uses 60d swing high; rr capped",
        "SWING_HIGH_20D": "target uses 20d swing high; rr capped",
        "FIFTY_TWO_WEEK_HIGH": "target uses 52w high; rr capped",
        "CONFIRMATION_LINE": "target uses reevaluation line; rr capped",
        "CHASE_LINE": "target equals chase line; rr capped",
        "MISSING": "target missing; rr capped",
    }.get(quality, "")


def _technical_resistance_candidate(data: dict[str, Any], price: float) -> tuple[float, str, str] | None:
    explicit = _first_valid_target_with_key(
        data,
        price,
        "technical_resistance_price",
        "technicalResistancePrice",
        "technical_resistance_high",
        "technicalResistanceHigh",
    )
    if explicit is not None:
        key, value = explicit
        detail = str(_value(data, "technical_resistance_source", "technicalResistanceSource") or key)
        return value, key, detail

    level = _nearest_resistance_level(data, price)
    if level is not None:
        value, detail = level
        return value, "resistanceLevels", detail

    field_candidates: list[tuple[str, float]] = []
    for key in (
        "resistance_zone_low",
        "resistanceZoneLow",
        "resistance_zone_high",
        "resistanceZoneHigh",
        "resistance_zone_upper",
        "resistanceZoneUpper",
    ):
        if key not in data:
            continue
        value = _number(data.get(key))
        if _valid_upside_target(value, price, max_multiple=2.5):
            field_candidates.append((key, value))
    if not field_candidates:
        return None
    key, value = min(field_candidates, key=lambda item: item[1])
    return value, key, key


def _nearest_resistance_level(data: dict[str, Any], price: float) -> tuple[float, str] | None:
    candidates: list[tuple[float, str]] = []
    for item in _resistance_level_items(data):
        value = _first_number(item, "price", "level", "value")
        if not _valid_upside_target(value, price, max_multiple=2.5):
            continue
        label = str(item.get("label") or item.get("name") or item.get("source") or "resistanceLevels")
        source = str(item.get("source") or "").strip()
        detail = f"{label} / {source}" if source and source not in label else label
        candidates.append((value, detail))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])


def _resistance_level_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [
        _value(data, "resistanceLevels", "resistance_levels", "technical_resistance_levels", "technicalResistanceLevels"),
        _nested_value(data, "technical_entry_model", "resistanceLevels"),
        _nested_value(data, "technicalEntryModel", "resistanceLevels"),
        _nested_value(data, "technical_entry", "resistanceLevels"),
    ]
    levels: list[dict[str, Any]] = []
    for raw_levels in sources:
        if not isinstance(raw_levels, (list, tuple)):
            continue
        for item in raw_levels:
            if isinstance(item, dict):
                levels.append(item)
    return levels


def _nested_value(source: dict[str, Any], outer_key: str, inner_key: str) -> Any:
    outer = source.get(outer_key)
    if isinstance(outer, dict):
        return outer.get(inner_key)
    return getattr(outer, inner_key, None)


def _first_valid_target_with_key(source: dict[str, Any], price: float, *keys: str, max_multiple: float | None = None) -> tuple[str, float] | None:
    for key in keys:
        if key not in source:
            continue
        number = _number(source.get(key))
        if _valid_upside_target(number, price, max_multiple=max_multiple):
            return key, number
    return None


def _valid_upside_target(value: float | None, price: float, *, max_multiple: float | None = None) -> bool:
    if value is None or price <= 0:
        return False
    if value <= price * 1.0001:
        return False
    if max_multiple is not None and value > price * max_multiple:
        return False
    return True


def _current_action(primary_zone: str, setup_score: float, volume_status: str, volume_score: float, rr_score: float) -> str:
    if primary_zone == "INVALIDATION" or volume_status == "FAILED":
        return RISK_REVIEW
    if primary_zone == "CHASE_RISK" or volume_status == "OVEREXTENDED_SUPPORT_READ":
        return BLOCK_CHASE
    if primary_zone in {"DEEP_ACCEPTANCE", "PULLBACK_BUY"} and setup_score >= 62 and volume_score >= 55 and rr_score >= 55:
        return ALLOW_SMALL_BUY
    if primary_zone == "CONFIRMATION_REVIEW" and setup_score >= 62 and volume_score >= 78 and rr_score >= 55:
        return ALLOW_SMALL_BUY
    if primary_zone == "PULLBACK_BUY":
        return WAIT_CONFIRMATION
    if primary_zone == "PULLBACK_WATCH":
        return WAIT_CONFIRMATION
    if primary_zone == "PULLBACK_UPPER_WATCH":
        return WAIT_CONFIRMATION
    if primary_zone == "REPAIR_WATCH":
        return WAIT_CONFIRMATION
    if primary_zone == "CONFIRMATION_REVIEW":
        return WAIT_CONFIRMATION
    return WAIT_PULLBACK


def _existing_position_action(action: str) -> str:
    if action == ALLOW_SMALL_BUY:
        return "已有持仓：股票层可观察，是否新增取决于账户额度与持仓约束。"
    if action == BLOCK_CHASE:
        return "已有持仓：不追高加仓，等待回到承接区。"
    if action == RISK_REVIEW:
        return "已有持仓：进入风控复核，暂停新增买入。"
    if action == DATA_INSUFFICIENT:
        return "已有持仓：技术承接数据不足，先暂停新增买入。"
    return "已有持仓：持有观察，等待量价确认或更低回踩。"


def _no_position_action(action: str) -> str:
    if action == ALLOW_SMALL_BUY:
        return "未持仓：允许小仓观察，后续加仓必须等确认。"
    if action == BLOCK_CHASE:
        return "未持仓：禁止追高，等待回到回踩买区。"
    if action == RISK_REVIEW:
        return "未持仓：暂停买入，先复核失效风险。"
    if action == DATA_INSUFFICIENT:
        return "未持仓：技术承接数据不足，不给明确买入区。"
    if action == WAIT_PULLBACK:
        return "未持仓：等待回踩到承接区。"
    return "未持仓：等待重新评估线和量价确认。"


def _zone_reason(primary_zone: str, volume_status: str, rr_score: float, core_reason: str) -> str:
    base = {
        "DEEP_ACCEPTANCE": "价格接近强支撑 / 前低 / 承接区，按深度承接区处理。",
        "PULLBACK_BUY": "价格回到技术回踩买区，买区由技术结构和量价承接决定。",
        "PULLBACK_WATCH": "价格处于技术回踩带观察区，但未进入更靠近下沿的左侧试仓区。",
        "PULLBACK_UPPER_WATCH": "当前价格位于买区上沿 75% 以上，按修复观察区处理，不主动新增。",
        "REPAIR_WATCH": "价格已修复但量能或承接尚未给出确认，先观察。",
        "CONFIRMATION_REVIEW": "价格接近确认复核区，确认线只触发重新评估，不等于直接买入。",
        "CHASE_RISK": "价格远离承接区或进入追高阈值，盈亏比恶化。",
        "INVALIDATION": "价格跌破失效线，优先进入风控复核。",
        "WAIT_PULLBACK": "价格不在高质量承接区，等待回踩。",
    }.get(primary_zone, "买区由技术结构、量价承接和风险收益比共同决定。")
    details = [base, f"量价状态：{_volume_status_text(volume_status)}。", f"风险收益比分 {rr_score:.1f}。"]
    if core_reason:
        details.append(core_reason)
    return "".join(details)


def _volume_status_text(status: str) -> str:
    return {
        "ACCEPTANCE_CONFIRMED": "承接确认",
        "FORMING": "承接形成中",
        "UNCONFIRMED": "量价未确认",
        "FAILED": "承接失败",
        "OVEREXTENDED_SUPPORT_READ": "脱离观察区",
        "DATA_MISSING": "数据不足",
    }.get(status or "", "数据不足")


def _missing_label(key: str) -> str:
    return {
        "price": "current_price",
        "support_low": "support_zone_low",
        "support_high": "support_zone_high",
        "pullback_low": "pullback_zone_low",
        "pullback_high": "pullback_zone_high",
        "confirmation": "confirmation_price",
        "invalidation": "invalidation_price",
        "chase": "chase_price",
        "ma20": "ma20",
        "ma50": "ma50",
        "ma200": "ma200",
        "atr": "atr_14",
        "resistance": "resistance_zone",
    }.get(key, key)


def _enrich_daily_technical_inputs(data: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(data or {})
    _flatten_zone(enriched, "support_zone", "support_zone_low", "support_zone_high")
    _flatten_zone(enriched, "resistance_zone", "resistance_zone_low", "resistance_zone_high")

    bars = _daily_bars(enriched)
    if not bars:
        return enriched
    enriched["daily_ohlcv_count"] = len(bars)

    latest = bars[-1]
    latest_close = _number(latest.get("close"))
    latest_volume = _number(latest.get("volume"))
    if latest_close is not None:
        enriched.setdefault("latest_close", latest_close)
        enriched.setdefault("close", latest_close)
        enriched.setdefault("current_price", latest_close)
        enriched.setdefault("price", latest_close)
    if latest_volume is not None and latest_volume > 0:
        enriched.setdefault("latest_volume", latest_volume)
        enriched.setdefault("volume", latest_volume)
        enriched.setdefault("volume_source", "daily_ohlcv")

    closes = [_number(bar.get("close")) for bar in bars]
    highs = [_number(bar.get("high")) for bar in bars]
    lows = [_number(bar.get("low")) for bar in bars]
    volumes = [_number(bar.get("volume")) for bar in bars]
    closes = [value for value in closes if value is not None]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    volumes = [value for value in volumes if value is not None and value > 0]

    ma20 = _tail_mean(closes, 20, require_full=True)
    ma50 = _tail_mean(closes, 50, require_full=True)
    ma200 = _tail_mean(closes, 200, require_full=True)
    for key, value in (("ma20", ma20), ("ema20", ma20), ("ma50", ma50), ("ema50", ma50), ("ma200", ma200), ("ema200", ma200)):
        if value is not None:
            enriched.setdefault(key, value)

    avg_volume_20d = _tail_mean(volumes, 20, require_full=True)
    if avg_volume_20d is not None:
        enriched.setdefault("avg_volume_20d", avg_volume_20d)
        enriched.setdefault("volume_ma20", avg_volume_20d)
    if latest_volume is not None and latest_volume > 0 and avg_volume_20d not in (None, 0):
        enriched.setdefault("volume_ratio", latest_volume / avg_volume_20d)

    atr14 = _atr(bars, 14)
    if atr14 is not None:
        enriched.setdefault("atr_14", atr14)
        enriched.setdefault("atr14", atr14)
        if latest_close not in (None, 0):
            enriched.setdefault("atr_pct", atr14 / latest_close * 100.0)

    rsi14 = _rsi(closes, 14)
    if rsi14 is not None:
        enriched.setdefault("rsi_14", rsi14)
        enriched.setdefault("rsi14", rsi14)

    swing_high = max(highs[-20:]) if highs else None
    swing_low = min(lows[-20:]) if lows else None
    if swing_high is not None:
        enriched.setdefault("swing_high", swing_high)
        enriched.setdefault("recent_swing_high", swing_high)
    if swing_low is not None:
        enriched.setdefault("swing_low", swing_low)
        enriched.setdefault("recent_swing_low", swing_low)

    if swing_low is not None and atr14 is not None:
        support_low = max(0.0, swing_low - atr14 * 0.25)
        support_high = swing_low + atr14 * 0.25
        enriched.setdefault("support_zone_low", support_low)
        enriched.setdefault("support_zone_high", support_high)
        enriched.setdefault("deep_support_zone_low", support_low)
        enriched.setdefault("deep_support_zone_high", support_high)
        enriched.setdefault("support_zone", {"low": support_low, "high": support_high})
    if swing_high is not None and atr14 is not None:
        resistance_low = max(0.0, swing_high - atr14 * 0.25)
        enriched.setdefault("resistance_zone_low", resistance_low)
        enriched.setdefault("resistance_zone_high", swing_high)
        enriched.setdefault("resistance_zone", {"low": resistance_low, "high": swing_high})
        enriched.setdefault("confirmation_price", resistance_low)
    if _first_number(enriched, "invalidation_price", "invalid_line") is None:
        support_low = _first_number(enriched, "support_zone_low", "deep_support_zone_low", "recent_swing_low")
        if support_low is not None:
            enriched.setdefault("invalidation_price", support_low)

    source = "daily_ohlcv" if len(bars) >= 200 else "daily_ohlcv_partial"
    enriched.setdefault("technical_data_source", source)
    return enriched


def _enrich_daily_volume_inputs(volume: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(volume or {})
    latest_volume = _first_number(enriched, "latest_volume", "latestVolume") or _first_number(data, "latest_volume", "volume")
    avg_volume = (
        _first_number(enriched, "volume_ma20", "avg_volume_20d", "avgVolume20d", "avgVolume")
        or _first_number(data, "volume_ma20", "avg_volume_20d", "avgVolume20d", "avgVolume")
    )
    if latest_volume is not None and latest_volume > 0:
        enriched.setdefault("latest_volume", latest_volume)
        enriched.setdefault("volume_source", _value(data, "volume_source", "volumeSource") or "daily_ohlcv")
    if avg_volume is not None and avg_volume > 0:
        enriched.setdefault("volume_ma20", avg_volume)
        enriched.setdefault("avg_volume_20d", avg_volume)
    if _first_number(enriched, "volume_ratio", "volumeRatio") is None and latest_volume is not None and avg_volume not in (None, 0):
        enriched["volume_ratio"] = latest_volume / avg_volume
    ratio = _first_number(enriched, "volume_ratio", "volumeRatio")
    if not str(_value(enriched, "volume_price_status", "volumePriceStatus") or "").strip() and ratio is not None:
        enriched["volume_price_status"] = "FORMING" if ratio <= 1.0 else "UNCONFIRMED"
    return enriched


def _flatten_zone(data: dict[str, Any], zone_key: str, low_key: str, high_key: str) -> None:
    zone = data.get(zone_key)
    if not isinstance(zone, dict):
        return
    low = _first_number(zone, "low", "lower", "min")
    high = _first_number(zone, "high", "upper", "max")
    if low is not None:
        data.setdefault(low_key, low)
    if high is not None:
        data.setdefault(high_key, high)


def _daily_bars(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _value(data, "daily_ohlcv", "daily_bars", "dailyBars", "historical_prices", "history")
    if raw is None:
        return []
    if hasattr(raw, "to_dict") and not isinstance(raw, dict):
        try:
            raw = raw.to_dict("records")
        except TypeError:
            raw = raw.to_dict()
    if isinstance(raw, dict):
        nested = _value(raw, "bars", "rows", "data", "prices", "history")
        if nested is not None:
            return _daily_bars({"daily_ohlcv": nested})
        if any(isinstance(value, (list, tuple)) for value in raw.values()):
            keys = list(raw.keys())
            length = max((len(value) for value in raw.values() if isinstance(value, (list, tuple))), default=0)
            rows: list[dict[str, Any]] = []
            for index in range(length):
                row = {key: raw[key][index] for key in keys if isinstance(raw.get(key), (list, tuple)) and len(raw[key]) > index}
                if row:
                    rows.append(_normalize_bar(row))
            return [row for row in rows if row]
        bar = _normalize_bar(raw)
        return [bar] if bar else []
    if isinstance(raw, (list, tuple)):
        return [bar for item in raw if (bar := _normalize_bar(item))]
    return []


def _normalize_bar(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    bar = {
        "open": _first_number(item, "open", "o"),
        "high": _first_number(item, "high", "h"),
        "low": _first_number(item, "low", "l"),
        "close": _first_number(item, "close", "c", "adjClose", "adj_close"),
        "volume": _first_number(item, "volume", "v"),
    }
    return bar if any(value is not None for value in bar.values()) else {}


def _tail_mean(values: list[float], window: int, *, require_full: bool = False) -> float | None:
    usable = [value for value in values if value is not None]
    if not usable:
        return None
    if require_full and len(usable) < window:
        return None
    tail = usable[-window:]
    return sum(tail) / len(tail) if tail else None


def _atr(bars: list[dict[str, Any]], window: int = 14) -> float | None:
    ranges: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        high = _number(bar.get("high"))
        low = _number(bar.get("low"))
        close = _number(bar.get("close"))
        if high is None or low is None:
            previous_close = close if close is not None else previous_close
            continue
        true_range = high - low
        if previous_close is not None:
            true_range = max(true_range, abs(high - previous_close), abs(low - previous_close))
        ranges.append(true_range)
        previous_close = close if close is not None else previous_close
    if not ranges:
        return None
    if len(ranges) < window:
        return None
    tail = ranges[-window:]
    return sum(tail) / len(tail) if tail else None


def _rsi(closes: list[float], window: int = 14) -> float | None:
    if len(closes) < 2:
        return None
    deltas = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    if len(deltas) < window:
        return None
    tail = deltas[-window:]
    if not tail:
        return None
    gains = [delta for delta in tail if delta > 0]
    losses = [-delta for delta in tail if delta < 0]
    avg_gain = sum(gains) / len(tail)
    avg_loss = sum(losses) / len(tail)
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _in_range(price: float, low: float, high: float) -> bool:
    lower, upper = sorted((low, high))
    return lower <= price <= upper


def _first_number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in source:
            number = _number(source.get(key))
            if number is not None:
                return number
    return None


def _first_number_with_key(source: dict[str, Any], *keys: str) -> tuple[str, float] | None:
    for key in keys:
        if key not in source:
            continue
        number = _number(source.get(key))
        if number is not None:
            return key, number
    return None


def _number(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str):
        value = value.strip().replace("$", "").replace(",", "").replace("%", "").replace("x", "").replace("X", "")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _same_price(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return False
    tolerance = max(0.01, abs(left) * 0.0001)
    return abs(left - right) <= tolerance


def _value(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key not in source:
            continue
        value = source.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None
