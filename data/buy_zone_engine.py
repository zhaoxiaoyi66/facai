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
    confirmation_price: float | None
    invalidation_price: float | None
    chase_price: float | None
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    confirmation = _first_number(data, "confirmation_price", "radar_confirmation_price", "confirm_line", "resistance_zone_low")
    invalidation = _first_number(data, "invalidation_price", "radar_invalidation_price", "invalid_line")
    chase = _first_number(data, "chase_above_price", "radar_chase_above_price", "chase_price")
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
    target_price = _first_number(data, "target_price", "targetPrice", "analyst_target_price", "consensus_target_price")
    resistance_high = _first_number(data, "resistance_zone_high", "recent_swing_high", "swing_high")
    if chase is None:
        chase = resistance
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
            confirmation_price=confirmation,
            invalidation_price=invalidation,
            chase_price=chase,
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
    volume_score = _volume_acceptance_score(volume_status, volume_score_input)
    rr_score = _risk_reward_score(
        price=price,
        confirmation=confirmation,
        invalidation=invalidation,
        target_price=target_price,
        resistance_high=resistance_high,
        primary_zone=primary_zone,
    )
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
        confirmation_price=confirmation,
        invalidation_price=invalidation,
        chase_price=chase,
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
        "chase",
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
    chase: float,
) -> str:
    if price < invalidation:
        return "INVALIDATION"
    if price >= chase:
        return "CHASE_RISK"
    if price >= confirmation:
        return "CONFIRMATION_REVIEW"
    if _in_range(price, support_low, support_high):
        return "DEEP_ACCEPTANCE"
    if _in_range(price, pullback_low, pullback_high):
        _left_low, left_probe_high, _observe_low, observe_high = _pullback_layers(pullback_low, pullback_high)
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
    left_probe_high = low + width * 0.30
    observe_high = low + width * 0.70
    return low, left_probe_high, left_probe_high, observe_high


def _technical_structure_score(primary_zone: str) -> float:
    return {
        "DEEP_ACCEPTANCE": 82.0,
        "PULLBACK_BUY": 78.0,
        "PULLBACK_WATCH": 63.0,
        "REPAIR_WATCH": 58.0,
        "CONFIRMATION_REVIEW": 62.0,
        "CHASE_RISK": 18.0,
        "INVALIDATION": 5.0,
        "WAIT_PULLBACK": 45.0,
    }.get(primary_zone, 40.0)


def _volume_acceptance_score(status: str, explicit_score: float | None) -> float:
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


def _risk_reward_score(
    *,
    price: float,
    confirmation: float,
    invalidation: float,
    target_price: float | None,
    resistance_high: float | None,
    primary_zone: str,
) -> float:
    if primary_zone in {"INVALIDATION", "CHASE_RISK"}:
        return 5.0 if primary_zone == "INVALIDATION" else 18.0
    explicit_target = _first_defined_number(target_price, resistance_high)
    target = explicit_target if explicit_target is not None else confirmation
    downside = price - invalidation
    upside = target - price
    if downside <= 0 or upside <= 0:
        return 28.0
    ratio = upside / downside
    if ratio >= 2.0:
        score = 88.0
    elif ratio >= 1.4:
        score = 75.0
    elif ratio >= 1.0:
        score = 62.0
    elif ratio >= 0.6:
        score = 45.0
    else:
        score = 28.0
    if explicit_target is None:
        return min(score, 60.0)
    return score


def _current_action(primary_zone: str, setup_score: float, volume_status: str, volume_score: float, rr_score: float) -> str:
    if primary_zone == "INVALIDATION" or volume_status == "FAILED":
        return RISK_REVIEW
    if primary_zone == "CHASE_RISK" or volume_status == "OVEREXTENDED_SUPPORT_READ":
        return BLOCK_CHASE
    if primary_zone in {"DEEP_ACCEPTANCE", "PULLBACK_BUY"} and setup_score >= 62 and volume_score >= 50 and rr_score >= 55:
        return ALLOW_SMALL_BUY
    if primary_zone == "PULLBACK_BUY":
        return WAIT_CONFIRMATION
    if primary_zone == "PULLBACK_WATCH":
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
        enriched.setdefault("chase_above_price", swing_high)
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


def _first_defined_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
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
