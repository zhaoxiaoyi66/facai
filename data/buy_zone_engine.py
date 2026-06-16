from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


WAIT_PULLBACK = "WAIT_PULLBACK"
WAIT_CONFIRMATION = "WAIT_CONFIRMATION"
ALLOW_SMALL_BUY = "ALLOW_SMALL_BUY"
ALLOW_ADD_ON_PULLBACK = "ALLOW_ADD_ON_PULLBACK"
BLOCK_CHASE = "BLOCK_CHASE"
RISK_REVIEW = "RISK_REVIEW"
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
AVOID = "AVOID"
PAUSE_BUY = "PAUSE_BUY"

ACTION_TEXT = {
    WAIT_PULLBACK: "等待回踩",
    WAIT_CONFIRMATION: "等待确认",
    ALLOW_SMALL_BUY: "小仓观察建议",
    ALLOW_ADD_ON_PULLBACK: "回踩复核参考",
    BLOCK_CHASE: "追高风险提醒",
    RISK_REVIEW: "进入风控复核",
    DATA_INSUFFICIENT: "技术承接数据不足",
    AVOID: "暂不参与",
    PAUSE_BUY: "系统不建议新增",
}

ZONE_TEXT = {
    "DEEP_ACCEPTANCE": "深度承接区",
    "PULLBACK_BUY": "回踩买区",
    "PULLBACK_WATCH": "技术回踩带内，可观察",
    "PULLBACK_UPPER_WATCH": "买区上沿 / 修复观察区",
    "REPAIR_WATCH": "修复观察区",
    "CONFIRMATION_REVIEW": "确认复核区",
    "CHASE_RISK": "追高风险区",
    "INVALIDATION": "失效风控区",
    "DATA_INSUFFICIENT": "技术承接数据不足",
}

ACCEPTANCE_STATE_TEXT = {
    "CLEAR_ACCEPTANCE": "明显承接",
    "FORMING_ACCEPTANCE": "初步承接",
    "WEAK_ACCEPTANCE": "承接不足",
    "HIGH_VOLUME_UNCONFIRMED": "放量未确认",
    "FALLING_KNIFE_RISK": "飞刀风险",
    "STRUCTURE_BROKEN": "结构破坏",
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
    support_clusters: list[dict[str, Any]] = field(default_factory=list)
    selected_support_cluster: dict[str, Any] = field(default_factory=dict)
    support_cluster_score: float = 0.0
    support_score: float = 0.0
    trend_score: float = 0.0
    zone_width: float | None = None
    repair_observation_zone_low: float | None = None
    repair_observation_zone_high: float | None = None
    primary_buy_zone_low: float | None = None
    primary_buy_zone_high: float | None = None
    deep_support_zone_low: float | None = None
    deep_support_zone_high: float | None = None
    invalidation_zone_low: float | None = None
    invalidation_zone_high: float | None = None
    invalidation_risk_zone_low: float | None = None
    invalidation_risk_zone_high: float | None = None
    suspend_new_line: float | None = None
    buy_zone_failure_line: float | None = None
    deep_support_break_line: float | None = None
    risk_reward: float | None = None
    risk_reward_text: str = ""
    action_new_cash: str = ""
    action_existing_position: str = ""
    entry_condition_text: str = ""
    invalidation_condition_text: str = ""
    confidence_breakdown: dict[str, float] = field(default_factory=dict)
    current_subzone: str = ""
    left_side_position_pct: float | None = None
    left_side_quality: str = ""
    left_probe_position_label: str = ""
    distance_to_left_probe_low_pct: float | None = None
    distance_to_left_probe_high_pct: float | None = None
    volume_price_gate: str = ""
    volume_price_state: str = ""
    execution_gate_reason: str = ""
    zone_action_quality: str = ""
    advisory_level: str = ""
    advisory_reasons: list[str] = field(default_factory=list)
    confirmation_score: float = 0.0
    volume_price_status: str = ""
    acceptance_state: str = ""
    acceptance_state_text: str = ""
    entry_quality: str = ""
    falling_knife_risk: str = ""
    acceptance_reasons: list[str] = field(default_factory=list)
    missing_confirmation: list[str] = field(default_factory=list)
    required_confirmation_price: float | None = None

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


@dataclass(frozen=True)
class SupportCluster:
    low: float
    high: float
    center: float
    score: float
    sources: list[str]
    candidate_count: int
    zone_low: float
    zone_high: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BuyZoneSnapshot:
    symbol: str
    date: str
    price: float | None
    zone_low: float | None
    zone_high: float | None
    zone_position: float | None
    setup_score: float
    support_score: float
    trend_score: float
    volume_score: float
    risk_reward: float | None
    action_new_cash: str
    action_existing_position: str
    invalidation_line: float | None
    confirmation_line: float | None
    context: dict[str, Any] = field(default_factory=dict)

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
    atr = _first_number(data, "atr_20", "atr20", "atr_14", "atr14")
    zone_width = _dynamic_zone_width(price, atr)
    support_clusters = _support_clusters(data, price=price, atr=atr)
    selected_cluster = _select_support_cluster(support_clusters, price)
    support_low = _first_number(data, "support_zone_low", "deep_support_zone_low", "support_watch_zone_low", "recent_swing_low")
    support_high = _first_number(data, "support_zone_high", "deep_support_zone_high", "support_watch_zone_high", "recent_swing_low")
    if selected_cluster is not None:
        support_low = support_low if support_low is not None else selected_cluster.zone_low
        support_high = support_high if support_high is not None else selected_cluster.zone_high
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
    if selected_cluster is not None:
        pullback_low = pullback_low if pullback_low is not None else selected_cluster.zone_low
        pullback_high = pullback_high if pullback_high is not None else selected_cluster.zone_high
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
    resistance = _first_number(
        data,
        "resistance_zone_high",
        "resistance_zone_low",
        "recent_swing_high",
        "recent_breakout_level",
        "confirmation_price",
    )
    suspend_new_line = _suspend_new_line(pullback_low, atr)
    buy_zone_failure_line = pullback_low
    deep_support_break_line = support_low
    if invalidation is None:
        invalidation = suspend_new_line if suspend_new_line is not None else support_low
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
            support_clusters=[cluster.to_dict() for cluster in support_clusters],
            selected_support_cluster=selected_cluster.to_dict() if selected_cluster is not None else {},
            support_cluster_score=selected_cluster.score if selected_cluster is not None else 0.0,
            support_score=selected_cluster.score if selected_cluster is not None else 0.0,
            trend_score=_trend_score(price, ma20, ma50, ma200),
            zone_width=zone_width,
            repair_observation_zone_low=repair_low,
            repair_observation_zone_high=repair_high,
            primary_buy_zone_low=pullback_low,
            primary_buy_zone_high=pullback_high,
            deep_support_zone_low=support_low,
            deep_support_zone_high=support_high,
            invalidation_zone_low=suspend_new_line,
            invalidation_zone_high=buy_zone_failure_line,
            suspend_new_line=suspend_new_line,
            buy_zone_failure_line=buy_zone_failure_line,
            deep_support_break_line=deep_support_break_line,
            risk_reward=None,
            risk_reward_text="风险收益比暂缺",
            action_new_cash="数据不足 / 等待补齐",
            action_existing_position="持有观察 / 不建议加仓",
            entry_condition_text=_add_trigger_condition_text(confirmation, breakout_reevaluation),
            invalidation_condition_text=_pause_new_condition_text(pullback_low, invalidation, data),
            confidence_breakdown={
                "support_score": selected_cluster.score if selected_cluster is not None else 0.0,
                "trend_score": _trend_score(price, ma20, ma50, ma200),
                "volume_score": 0.0,
                "risk_reward_score": 0.0,
            },
            volume_price_gate="DATA_INSUFFICIENT",
            volume_price_state="DATA_INSUFFICIENT",
            execution_gate_reason="技术承接数据不足，不给明确买区。",
            zone_action_quality="DATA_INSUFFICIENT",
            advisory_level="WARNING",
            advisory_reasons=["技术承接数据不足", *missing[:4]],
            acceptance_state="WEAK_ACCEPTANCE",
            acceptance_state_text=ACCEPTANCE_STATE_TEXT["WEAK_ACCEPTANCE"],
            entry_quality="WAIT_CONFIRMATION",
            falling_knife_risk="MEDIUM",
            acceptance_reasons=["技术承接数据不足，无法确认承接。"],
            missing_confirmation=missing,
            required_confirmation_price=confirmation,
        )

    raw_left_probe_low, raw_left_probe_high, observe_low, observe_high = _pullback_layers(pullback_low, pullback_high)
    left_probe_low, left_probe_high, invalidation_risk_low, invalidation_risk_high = _clip_left_probe_by_invalidation(
        raw_left_probe_low,
        raw_left_probe_high,
        invalidation,
    )
    zone_position = _zone_position(price, pullback_low, pullback_high)
    left_side_position_pct = _left_side_position_pct(price, left_probe_low, left_probe_high)
    left_probe_label = _left_probe_position_label(left_side_position_pct)
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
    resistance_low = _first_number(data, "resistance_zone_low", "technical_resistance_price", "recent_breakout_level")
    daily_return = _first_number(data, "daily_return_pct", "day_change_pct", "change_pct", "changePercent")
    close_position = _first_number(data, "close_position", "closePosition", "close_position_in_range", "closePositionInRange")
    volume_score = _volume_acceptance_score(
        volume_status,
        volume_score_input,
        volume_ratio=volume_ratio,
        price=price,
        confirmation=confirmation,
        resistance=resistance_low,
        support_low=support_low,
        daily_return=daily_return,
        close_position=close_position,
    )
    confirmation_score = _confirmation_score(data, volume, volume_score)
    volume_price_gate = _volume_price_gate(
        primary_zone=primary_zone,
        volume_status=volume_status,
        volume_score=volume_score,
        volume_ratio=volume_ratio,
        price=price,
        confirmation=confirmation,
        resistance=resistance_low,
        support_low=support_low,
        invalidation=invalidation,
        daily_return=daily_return,
        close_position=close_position,
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
    support_score = selected_cluster.score if selected_cluster is not None else 0.0
    trend_score = _trend_score(price, ma20, ma50, ma200)
    setup_score = round(technical_score * 0.35 + volume_score * 0.30 + rr_score * 0.20 + support_score * 0.10 + trend_score * 0.05, 1)
    action = _current_action(
        primary_zone,
        setup_score,
        volume_status,
        volume_score,
        rr_score,
        left_probe_position_label=left_probe_label,
        volume_price_gate=volume_price_gate,
        confirmation_score=confirmation_score,
        target_quality=rr.target_quality,
    )
    execution_gate_reason = _execution_gate_reason(
        action=action,
        primary_zone=primary_zone,
        left_probe_position_label=left_probe_label,
        volume_price_gate=volume_price_gate,
        confirmation_score=confirmation_score,
        target_quality=rr.target_quality,
        rr_score=rr_score,
    )
    current_subzone = _current_subzone(primary_zone, left_probe_label, zone_position)
    left_side_quality = _left_side_quality(left_probe_label, volume_price_gate, rr.target_quality, rr_score)
    advisory_level, advisory_reasons = _advisory_review(
        action=action,
        primary_zone=primary_zone,
        current_subzone=current_subzone,
        left_probe_position_label=left_probe_label,
        volume_price_gate=volume_price_gate,
        confirmation_score=confirmation_score,
        target_quality=rr.target_quality,
        rr_score=rr_score,
        execution_reason=execution_gate_reason,
    )
    acceptance = _acceptance_assessment(
        primary_zone=primary_zone,
        current_subzone=current_subzone,
        price=price,
        support_low=support_low,
        invalidation=invalidation,
        confirmation=confirmation,
        volume_price_gate=volume_price_gate,
        volume_status=volume_status,
        volume_score=volume_score,
        confirmation_score=confirmation_score,
        volume_ratio=volume_ratio,
        daily_return=daily_return,
        close_position=close_position,
    )
    entry_quality = _entry_quality(
        acceptance_state=str(acceptance["acceptance_state"]),
        primary_zone=primary_zone,
        current_subzone=current_subzone,
        left_probe_position_label=left_probe_label,
        target_quality=rr.target_quality,
        rr_score=rr_score,
    )
    pause_text = _pause_new_condition_text(pullback_low, invalidation, data)
    add_trigger_text = _add_trigger_condition_text(confirmation, breakout_reevaluation)
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
        add_trigger_condition_text=add_trigger_text,
        pause_new_condition_text=pause_text,
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
        support_clusters=[cluster.to_dict() for cluster in support_clusters],
        selected_support_cluster=selected_cluster.to_dict() if selected_cluster is not None else {},
        support_cluster_score=support_score,
        support_score=support_score,
        trend_score=trend_score,
        zone_width=zone_width,
        repair_observation_zone_low=repair_low,
        repair_observation_zone_high=repair_high,
        primary_buy_zone_low=pullback_low,
        primary_buy_zone_high=pullback_high,
        deep_support_zone_low=support_low,
        deep_support_zone_high=support_high,
        invalidation_zone_low=suspend_new_line,
        invalidation_zone_high=buy_zone_failure_line,
        invalidation_risk_zone_low=invalidation_risk_low,
        invalidation_risk_zone_high=invalidation_risk_high,
        suspend_new_line=suspend_new_line,
        buy_zone_failure_line=buy_zone_failure_line,
        deep_support_break_line=deep_support_break_line,
        risk_reward=rr.raw_rr,
        risk_reward_text=_risk_reward_text(rr.raw_rr),
        action_new_cash=_no_position_action(action),
        action_existing_position=_existing_position_action(action),
        entry_condition_text=add_trigger_text,
        invalidation_condition_text=pause_text,
        confidence_breakdown={
            "support_score": support_score,
            "trend_score": trend_score,
            "volume_score": volume_score,
            "risk_reward_score": rr_score,
        },
        current_subzone=current_subzone,
        left_side_position_pct=left_side_position_pct,
        left_side_quality=left_side_quality,
        left_probe_position_label=left_probe_label,
        distance_to_left_probe_low_pct=_distance_pct(price, left_probe_low),
        distance_to_left_probe_high_pct=_distance_pct(price, left_probe_high),
        volume_price_gate=volume_price_gate,
        volume_price_state=volume_price_gate,
        execution_gate_reason=execution_gate_reason,
        zone_action_quality=_zone_action_quality(action, volume_price_gate, rr_score),
        advisory_level=advisory_level,
        advisory_reasons=advisory_reasons,
        confirmation_score=confirmation_score,
        volume_price_status=volume_status,
        acceptance_state=str(acceptance["acceptance_state"]),
        acceptance_state_text=str(acceptance["acceptance_state_text"]),
        entry_quality=entry_quality,
        falling_knife_risk=str(acceptance["falling_knife_risk"]),
        acceptance_reasons=list(acceptance["acceptance_reasons"]),
        missing_confirmation=list(acceptance["missing_confirmation"]),
        required_confirmation_price=confirmation,
    )


def build_buy_zone_snapshot(
    symbol: str,
    snapshot_date: str,
    source: dict[str, Any] | None = None,
    *,
    technicals: dict[str, Any] | None = None,
    volume_snapshot: dict[str, Any] | None = None,
) -> BuyZoneSnapshot:
    context = build_buy_zone_context({"ticker": symbol, **(source or {})}, technicals=technicals, volume_snapshot=volume_snapshot)
    return BuyZoneSnapshot(
        symbol=symbol,
        date=str(snapshot_date),
        price=context.current_price,
        zone_low=context.primary_buy_zone_low,
        zone_high=context.primary_buy_zone_high,
        zone_position=context.zone_position,
        setup_score=context.setup_score,
        support_score=context.support_score,
        trend_score=context.trend_score,
        volume_score=context.volume_acceptance_score,
        risk_reward=context.risk_reward,
        action_new_cash=context.action_new_cash,
        action_existing_position=context.action_existing_position,
        invalidation_line=context.invalidation_price,
        confirmation_line=context.confirmation_price,
        context=context.to_dict(),
    )


def save_buy_zone_snapshot(snapshot: BuyZoneSnapshot | dict[str, Any], path: str | Path = "data/cache/buy_zone_snapshots.json") -> dict[str, Any]:
    record = snapshot.to_dict() if isinstance(snapshot, BuyZoneSnapshot) else dict(snapshot)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                records = [item for item in loaded if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError):
            records = []
    symbol = str(record.get("symbol") or "")
    date_text = str(record.get("date") or "")
    records = [item for item in records if not (str(item.get("symbol") or "") == symbol and str(item.get("date") or "") == date_text)]
    records.append(record)
    records.sort(key=lambda item: (str(item.get("symbol") or ""), str(item.get("date") or "")))
    target.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def backtest_buy_zone_snapshots(
    symbol: str,
    daily_ohlcv: Any,
    *,
    base_source: dict[str, Any] | None = None,
    min_history: int = 60,
) -> list[dict[str, Any]]:
    bars = _daily_bars({"daily_ohlcv": daily_ohlcv})
    if len(bars) <= min_history + 5:
        return []
    results: list[dict[str, Any]] = []
    max_horizon = 60
    for index in range(min_history, len(bars) - 5):
        history = bars[: index + 1]
        latest = history[-1]
        date_text = str(latest.get("date") or index)
        source = {**(base_source or {}), "ticker": symbol, "daily_ohlcv": history}
        context = build_buy_zone_context(source)
        close = context.current_price
        if close is None:
            continue
        future = bars[index + 1 : min(len(bars), index + 1 + max_horizon)]
        if not future:
            continue
        result = build_buy_zone_snapshot(symbol, date_text, source).to_dict()
        result.update(_future_return_metrics(close, future, context.invalidation_price))
        results.append(result)
    return results


def _future_return_metrics(close: float, future: list[dict[str, Any]], invalidation_line: float | None) -> dict[str, Any]:
    closes = [_number(bar.get("close")) for bar in future]
    highs = [_number(bar.get("high")) for bar in future]
    lows = [_number(bar.get("low")) for bar in future]
    closes = [value for value in closes if value is not None]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]

    def ret(days: int) -> float | None:
        if len(closes) < days or close == 0:
            return None
        return (closes[days - 1] / close - 1.0) * 100.0

    lows_20 = lows[:20]
    highs_20 = highs[:20]
    mae_20 = (min(lows_20) / close - 1.0) * 100.0 if lows_20 and close else None
    mfe_20 = (max(highs_20) / close - 1.0) * 100.0 if highs_20 and close else None
    stop_hit_index = None
    rebound_hit_index = None
    for offset, bar in enumerate(future[:20], start=1):
        low = _number(bar.get("low"))
        high = _number(bar.get("high"))
        if stop_hit_index is None and invalidation_line is not None and low is not None and low <= invalidation_line:
            stop_hit_index = offset
        if rebound_hit_index is None and high is not None and high >= close * 1.03:
            rebound_hit_index = offset
    stop_first = stop_hit_index is not None and (rebound_hit_index is None or stop_hit_index <= rebound_hit_index)
    return {
        "return_5d": ret(5),
        "return_20d": ret(20),
        "return_60d": ret(60),
        "MAE_20": mae_20,
        "MFE_20": mfe_20,
        "stop_first_rate": 1.0 if stop_first else 0.0,
        "rebound_rate": 1.0 if rebound_hit_index is not None else 0.0,
        "false_buy_rate": 1.0 if stop_first or ((ret(20) or 0.0) < 0) else 0.0,
    }


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


def _dynamic_zone_width(price: float | None, atr: float | None) -> float | None:
    if price is None or price <= 0:
        return None
    atr_component = 0.8 * atr if atr is not None and atr > 0 else 0.0
    width = max(atr_component, price * 0.015)
    return round(min(width, price * 0.06), 4)


def _support_clusters(data: dict[str, Any], *, price: float | None, atr: float | None) -> list[SupportCluster]:
    if price is None or price <= 0:
        return []
    width = _dynamic_zone_width(price, atr) or price * 0.015
    merge_distance = max((atr or 0.0) * 0.6, price * 0.015)
    candidates = _support_candidates(data)
    if not candidates:
        return []
    candidates.sort(key=lambda item: item["price"])
    groups: list[list[dict[str, Any]]] = []
    for candidate in candidates:
        if not groups:
            groups.append([candidate])
            continue
        last_group = groups[-1]
        group_center = sum(item["price"] for item in last_group) / len(last_group)
        if abs(candidate["price"] - group_center) <= merge_distance:
            last_group.append(candidate)
        else:
            groups.append([candidate])
    clusters: list[SupportCluster] = []
    for group in groups:
        prices = [item["price"] for item in group]
        center = sum(prices) / len(prices)
        score = min(100.0, sum(float(item["score"]) for item in group) + min(len(group), 6) * 4.0)
        sources = sorted({str(item["source"]) for item in group})
        zone_low = max(0.0, min(prices) - width * 0.35)
        zone_high = max(prices) + width * 0.65
        clusters.append(
            SupportCluster(
                low=min(prices),
                high=max(prices),
                center=center,
                score=round(score, 1),
                sources=sources,
                candidate_count=len(group),
                zone_low=round(zone_low, 2),
                zone_high=round(zone_high, 2),
            )
        )
    return clusters


def _support_candidates(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def add(value: float | None, source: str, score: float) -> None:
        if value is not None and value > 0:
            candidates.append({"price": value, "source": source, "score": score})

    for key, source, score in (
        ("swing_low_20d", "20日波段低点", 20),
        ("swingLow20d", "20日波段低点", 20),
        ("recent_swing_low", "20日波段低点", 20),
        ("swing_low", "20日波段低点", 18),
        ("swing_low_60d", "60日波段低点", 22),
        ("swingLow60d", "60日波段低点", 22),
        ("swing_low_120d", "120日波段低点", 24),
        ("swingLow120d", "120日波段低点", 24),
        ("ma20", "EMA20", 14),
        ("ema20", "EMA20", 14),
        ("ma50", "EMA50", 18),
        ("ema50", "EMA50", 18),
        ("sma100", "SMA100", 18),
        ("ma100", "SMA100", 18),
        ("sma200", "SMA200", 20),
        ("ma200", "SMA200", 20),
        ("ema200", "SMA200", 20),
        ("recent_breakout_level", "前高回踩位", 18),
        ("recentBreakoutLevel", "前高回踩位", 18),
        ("previous_platform_high", "前一平台高点", 18),
        ("previousPlatformHigh", "前一平台高点", 18),
        ("gap_low", "跳空缺口下沿", 16),
        ("gapLow", "跳空缺口下沿", 16),
        ("gap_high", "跳空缺口上沿", 15),
        ("gapHigh", "跳空缺口上沿", 15),
        ("volume_profile_poc", "成交量密集区", 22),
        ("volumeProfilePoc", "成交量密集区", 22),
        ("volume_profile_support", "成交量密集区", 22),
        ("anchored_vwap", "Anchored VWAP", 20),
        ("anchoredVwap", "Anchored VWAP", 20),
        ("high_volume_bullish_low", "前期放量阳线低点", 18),
        ("highVolumeBullishLow", "前期放量阳线低点", 18),
        ("high_volume_bullish_mid", "前期放量阳线中位线", 17),
        ("highVolumeBullishMid", "前期放量阳线中位线", 17),
        ("support_zone_low", "显式支撑区", 22),
        ("support_zone_high", "显式支撑区", 18),
        ("effective_technical_entry_zone_low", "显式技术买区", 22),
        ("effective_technical_entry_zone_high", "显式技术买区", 16),
        ("technical_pullback_zone_low", "显式回踩区", 22),
        ("technical_pullback_zone_high", "显式回踩区", 16),
    ):
        add(_first_number(data, key), source, score)

    bars = _daily_bars(data)
    if bars:
        lows = [_number(bar.get("low")) for bar in bars]
        highs = [_number(bar.get("high")) for bar in bars]
        closes = [_number(bar.get("close")) for bar in bars]
        volumes = [_number(bar.get("volume")) for bar in bars]
        lows = [value for value in lows if value is not None]
        highs = [value for value in highs if value is not None]
        closes = [value for value in closes if value is not None]
        volumes = [value for value in volumes if value is not None and value > 0]
        for window, label, score in ((20, "20日波段低点", 20), (60, "60日波段低点", 22), (120, "120日波段低点", 24)):
            if len(lows) >= window:
                add(min(lows[-window:]), label, score)
        if len(highs) >= 40:
            add(max(highs[-40:-5] or highs[-40:]), "前高回踩位", 16)
        avg_volume = _tail_mean(volumes, 20, require_full=False)
        if avg_volume is not None and avg_volume > 0:
            for bar in bars[-60:]:
                open_price = _number(bar.get("open"))
                high = _number(bar.get("high"))
                low = _number(bar.get("low"))
                close = _number(bar.get("close"))
                volume = _number(bar.get("volume"))
                if None in (open_price, high, low, close, volume) or avg_volume in (None, 0):
                    continue
                if close > open_price and volume >= avg_volume * 1.4:
                    add(low, "前期放量阳线低点", 18)
                    add((low + high) / 2.0, "前期放量阳线中位线", 17)
    return candidates


def _select_support_cluster(clusters: list[SupportCluster], price: float | None) -> SupportCluster | None:
    if not clusters:
        return None
    if price is None:
        return max(clusters, key=lambda cluster: cluster.score)
    below_or_near = [cluster for cluster in clusters if cluster.center <= price * 1.03]
    pool = below_or_near or clusters
    return max(pool, key=lambda cluster: cluster.score - abs(cluster.center - price) / max(price, 1.0) * 120.0)


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
        "ma20",
        "ema20",
        "ma50",
        "ema50",
        "resistance_zone_low",
        "resistanceZoneLow",
        "trend_reclaim_zone_low",
        "trendReclaimZoneLow",
        "previous_platform_high",
        "previousPlatformHigh",
        "recent_breakout_level",
        "recentBreakoutLevel",
        "recent_high_volume_bearish_high",
        "recentHighVolumeBearishHigh",
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
        parts.append(f"跌破 {_range_money(trend_low, trend_high)}：趋势恶化，系统不建议继续摊低")
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


def _clip_left_probe_by_invalidation(
    left_probe_low: float | None,
    left_probe_high: float | None,
    invalidation: float | None,
) -> tuple[float | None, float | None, float | None, float | None]:
    if left_probe_low is None or left_probe_high is None:
        return left_probe_low, left_probe_high, None, None
    low, high = sorted((left_probe_low, left_probe_high))
    if invalidation is None or invalidation <= low:
        return low, high, None, None
    risk_high = min(invalidation, high)
    risk_low = low
    effective_low = max(invalidation, low)
    if effective_low >= high:
        return None, None, risk_low, high
    return effective_low, high, risk_low, risk_high


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
        return "买区下沿，小仓观察参考"
    if position <= 0.75:
        return "买区中段，等待承接"
    if position > 1.0:
        return "高于买区，等待回踩/防追高"
    return "买区上沿 / 修复观察区，不主动新增"


def _left_side_position_pct(price: float | None, left_low: float | None, left_high: float | None) -> float | None:
    if price is None or left_low is None or left_high is None:
        return None
    low, high = sorted((left_low, left_high))
    width = high - low
    if width <= 0 or price < low or price > high:
        return None
    return round((price - low) / width * 100.0, 1)


def _left_probe_position_label(left_side_position_pct: float | None) -> str:
    if left_side_position_pct is None:
        return "OUTSIDE"
    if left_side_position_pct < 35:
        return "LOWER_EDGE"
    if left_side_position_pct <= 75:
        return "MID_ZONE"
    return "UPPER_EDGE"


def _distance_pct(price: float | None, reference: float | None) -> float | None:
    if price is None or reference is None or reference <= 0:
        return None
    return round((price / reference - 1.0) * 100.0, 2)


def _suspend_new_line(zone_low: float | None, atr: float | None) -> float | None:
    if zone_low is None:
        return None
    if atr is None or atr <= 0:
        return zone_low
    return round(max(0.0, zone_low - atr * 0.4), 2)


def _trend_score(price: float | None, ma20: float | None, ma50: float | None, ma200: float | None) -> float:
    if price is None:
        return 0.0
    available = [level for level in (ma20, ma50, ma200) if level is not None and level > 0]
    if not available:
        return 0.0
    score = 45.0
    if ma20 is not None and price >= ma20:
        score += 18.0
    if ma50 is not None and price >= ma50:
        score += 18.0
    if ma200 is not None and price >= ma200:
        score += 14.0
    return min(100.0, score)


def _risk_reward_text(raw_rr: float | None) -> str:
    if raw_rr is None:
        return "风险收益比暂缺"
    if raw_rr < 1.2:
        return f"RR {raw_rr:.2f}：不买"
    if raw_rr < 1.8:
        return f"RR {raw_rr:.2f}：观察"
    if raw_rr < 2.5:
        return f"RR {raw_rr:.2f}：小仓"
    return f"RR {raw_rr:.2f}：高优先级"


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
    support_low: float | None = None,
    daily_return: float | None = None,
    close_position: float | None = None,
) -> float:
    if volume_ratio is not None and volume_ratio > 1.2 and support_low is not None and price is not None and price < support_low:
        return 0.0
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


def _confirmation_score(data: dict[str, Any], volume: dict[str, Any], volume_score: float) -> float:
    explicit = _first_number(volume, "confirmation_score", "confirmationScore") or _first_number(
        data, "confirmation_score", "confirmationScore"
    )
    if explicit is not None:
        return max(0.0, min(100.0, explicit))
    return max(0.0, min(100.0, volume_score))


def _volume_price_gate(
    *,
    primary_zone: str,
    volume_status: str,
    volume_score: float,
    volume_ratio: float | None,
    price: float,
    confirmation: float,
    resistance: float | None,
    support_low: float,
    invalidation: float,
    daily_return: float | None,
    close_position: float | None,
) -> str:
    if primary_zone == "INVALIDATION" or volume_status == "FAILED":
        return "FAILED_ACCEPTANCE"
    if volume_ratio is not None and volume_ratio > 1.2 and (price < support_low or price < invalidation):
        return "FAILED_ACCEPTANCE"
    if volume_status == "OVEREXTENDED_SUPPORT_READ":
        return "OVEREXTENDED"
    stood_up = (
        (confirmation is not None and price >= confirmation)
        or (resistance is not None and price >= resistance)
        or (support_low is not None and price >= support_low)
    )
    high_volume = volume_ratio is not None and volume_ratio > 1.2
    if high_volume and not stood_up:
        return "HIGH_VOLUME_UNCONFIRMED"
    if volume_status == "UNCONFIRMED":
        if high_volume or volume_score < 45:
            return "HIGH_VOLUME_UNCONFIRMED"
        return "FORMING_ACCEPTANCE"
    if volume_status == "ACCEPTANCE_CONFIRMED":
        return "CONFIRMED_ACCEPTANCE"
    low_volume = volume_ratio is not None and volume_ratio < 0.7
    close_improved = (daily_return is not None and daily_return >= 0) or (
        close_position is not None and close_position >= 0.55
    )
    if low_volume and not close_improved:
        return "FORMING_ACCEPTANCE"
    if volume_ratio is not None and volume_ratio > 1.0 and stood_up and volume_score >= 60:
        return "CONFIRMED_ACCEPTANCE"
    if volume_score >= 60:
        return "FORMING_ACCEPTANCE"
    return "FORMING_ACCEPTANCE"


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
    if raw_rr >= 2.5:
        score = 88.0
    elif raw_rr >= 1.8:
        score = 72.0
    elif raw_rr >= 1.2:
        score = 55.0
    else:
        score = 35.0

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


def _acceptance_assessment(
    *,
    primary_zone: str,
    current_subzone: str,
    price: float,
    support_low: float,
    invalidation: float,
    confirmation: float,
    volume_price_gate: str,
    volume_status: str,
    volume_score: float,
    confirmation_score: float,
    volume_ratio: float | None,
    daily_return: float | None,
    close_position: float | None,
) -> dict[str, Any]:
    reasons: list[str] = []
    missing: list[str] = []
    falling_risk = "LOW"
    technical_area = primary_zone in {
        "DEEP_ACCEPTANCE",
        "PULLBACK_BUY",
        "PULLBACK_WATCH",
        "PULLBACK_UPPER_WATCH",
        "REPAIR_WATCH",
    }

    if price < invalidation or primary_zone == "INVALIDATION" or volume_price_gate == "FAILED_ACCEPTANCE":
        return {
            "acceptance_state": "STRUCTURE_BROKEN",
            "acceptance_state_text": ACCEPTANCE_STATE_TEXT["STRUCTURE_BROKEN"],
            "falling_knife_risk": "HIGH",
            "acceptance_reasons": ["跌破失效线或量价承接失败，原左侧逻辑需要复核。"],
            "missing_confirmation": ["重新生成买区上下文", "复核失效线"],
        }

    high_volume = volume_ratio is not None and volume_ratio >= 1.2
    fast_selloff = daily_return is not None and daily_return <= -3.0
    weak_close = close_position is not None and close_position <= 0.25
    near_invalidation = invalidation > 0 and price <= invalidation * 1.03
    broke_short_support = price < support_low
    if high_volume and (fast_selloff or weak_close) and (near_invalidation or broke_short_support or confirmation_score < 50):
        return {
            "acceptance_state": "FALLING_KNIFE_RISK",
            "acceptance_state_text": ACCEPTANCE_STATE_TEXT["FALLING_KNIFE_RISK"],
            "falling_knife_risk": "HIGH",
            "acceptance_reasons": ["价格快速下跌且量能放大，靠近失效风险区，暂未看到稳定承接。"],
            "missing_confirmation": ["止跌K线", "站回关键支撑", "收盘确认"],
        }

    if volume_price_gate == "HIGH_VOLUME_UNCONFIRMED":
        return {
            "acceptance_state": "HIGH_VOLUME_UNCONFIRMED",
            "acceptance_state_text": ACCEPTANCE_STATE_TEXT["HIGH_VOLUME_UNCONFIRMED"],
            "falling_knife_risk": "MEDIUM",
            "acceptance_reasons": ["量能明显放大，但尚未站回关键线。"],
            "missing_confirmation": ["收盘确认", "事件复核", _confirmation_requirement(confirmation)],
        }

    if confirmation_score < 60:
        missing.append("量价确认分低于60")
    if volume_score < 60:
        missing.append("量价承接分低于60")
    if confirmation is not None and price < confirmation:
        missing.append(_confirmation_requirement(confirmation))
    if volume_ratio is not None and volume_ratio < 0.7:
        reasons.append("缩量回踩，但承接未确认。")
    if volume_status == "UNCONFIRMED":
        reasons.append("量价状态未确认。")

    stood_back = price >= support_low and (confirmation is None or price >= min(confirmation, support_low * 1.08))
    if technical_area and volume_price_gate == "CONFIRMED_ACCEPTANCE" and confirmation_score >= 65 and volume_score >= 60 and stood_back:
        return {
            "acceptance_state": "CLEAR_ACCEPTANCE",
            "acceptance_state_text": ACCEPTANCE_STATE_TEXT["CLEAR_ACCEPTANCE"],
            "falling_knife_risk": falling_risk,
            "acceptance_reasons": _dedupe_text(["价格守住技术区并站回关键支撑，量价承接确认。", *reasons]),
            "missing_confirmation": [],
        }

    if technical_area and confirmation_score >= 55 and volume_score >= 50 and volume_price_gate in {"FORMING_ACCEPTANCE", "CONFIRMED_ACCEPTANCE"}:
        return {
            "acceptance_state": "FORMING_ACCEPTANCE",
            "acceptance_state_text": ACCEPTANCE_STATE_TEXT["FORMING_ACCEPTANCE"],
            "falling_knife_risk": "LOW" if not near_invalidation else "MEDIUM",
            "acceptance_reasons": _dedupe_text(["技术区暂时守住，承接正在形成但尚未完全确认。", *reasons]),
            "missing_confirmation": _dedupe_text(missing),
        }

    if near_invalidation and (fast_selloff or high_volume):
        falling_risk = "MEDIUM"
    return {
        "acceptance_state": "WEAK_ACCEPTANCE",
        "acceptance_state_text": ACCEPTANCE_STATE_TEXT["WEAK_ACCEPTANCE"],
        "falling_knife_risk": falling_risk,
        "acceptance_reasons": _dedupe_text(reasons or ["价格在技术区内，但量价承接不足。"]),
        "missing_confirmation": _dedupe_text(missing),
    }


def _confirmation_requirement(confirmation: float | None) -> str:
    if confirmation is None:
        return "站回确认线"
    return f"站上 {_money(confirmation)} 确认线"


def _entry_quality(
    *,
    acceptance_state: str,
    primary_zone: str,
    current_subzone: str,
    left_probe_position_label: str,
    target_quality: str,
    rr_score: float,
) -> str:
    if acceptance_state == "STRUCTURE_BROKEN":
        return "INVALID"
    if acceptance_state in {"FALLING_KNIFE_RISK", "HIGH_VOLUME_UNCONFIRMED"} or primary_zone == "CHASE_RISK":
        return "HIGH_RISK"
    target_ok = target_quality not in {"CHASE_LINE", "CONFIRMATION_LINE", "MISSING", ""}
    if (
        acceptance_state == "CLEAR_ACCEPTANCE"
        and (primary_zone == "DEEP_ACCEPTANCE" or left_probe_position_label == "LOWER_EDGE")
        and target_ok
        and rr_score >= 65
    ):
        return "GOOD_LEFT_SIDE"
    if current_subzone in {"LEFT_PROBE_UPPER", "LEFT_PROBE_MID", "ACCEPTANCE_OBSERVATION_ZONE", "REPAIR_OBSERVATION_ZONE"}:
        return "EDGE_OBSERVE"
    if acceptance_state in {"FORMING_ACCEPTANCE", "WEAK_ACCEPTANCE"}:
        return "WAIT_CONFIRMATION"
    return "EDGE_OBSERVE"


def _current_action(
    primary_zone: str,
    setup_score: float,
    volume_status: str,
    volume_score: float,
    rr_score: float,
    *,
    left_probe_position_label: str,
    volume_price_gate: str,
    confirmation_score: float,
    target_quality: str,
) -> str:
    if primary_zone == "INVALIDATION" or volume_status == "FAILED" or volume_price_gate == "FAILED_ACCEPTANCE":
        return PAUSE_BUY
    if volume_score <= 0:
        return RISK_REVIEW
    if primary_zone == "CHASE_RISK" or volume_status == "OVEREXTENDED_SUPPORT_READ" or volume_price_gate == "OVEREXTENDED":
        return BLOCK_CHASE
    target_ok = target_quality not in {"CHASE_LINE", "CONFIRMATION_LINE", "MISSING", ""}
    volume_ok = volume_price_gate in {"CONFIRMED_ACCEPTANCE", "FORMING_ACCEPTANCE"}
    left_position_ok = primary_zone == "DEEP_ACCEPTANCE" or (
        primary_zone == "PULLBACK_BUY" and left_probe_position_label == "LOWER_EDGE"
    )
    if (
        left_position_ok
        and setup_score >= 62
        and confirmation_score >= 60
        and volume_ok
        and target_ok
        and rr_score >= 65
    ):
        return ALLOW_SMALL_BUY
    if volume_price_gate == "HIGH_VOLUME_UNCONFIRMED":
        return WAIT_CONFIRMATION
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


def _execution_gate_reason(
    *,
    action: str,
    primary_zone: str,
    left_probe_position_label: str,
    volume_price_gate: str,
    confirmation_score: float,
    target_quality: str,
    rr_score: float,
) -> str:
    if action == ALLOW_SMALL_BUY:
        return "左侧位置、量价承接、目标质量和风险收益比均满足小仓观察条件。"
    if action == PAUSE_BUY:
        return "跌破失效线或放量破位，系统不建议新增并需要重新评估。"
    if action == BLOCK_CHASE:
        return "价格脱离承接区或进入追高语境，提示追高风险。"
    reasons: list[str] = []
    if primary_zone == "PULLBACK_BUY" and left_probe_position_label != "LOWER_EDGE":
        reasons.append("价格在左侧试仓区中上部，先看承接。")
    if primary_zone in {"PULLBACK_WATCH", "PULLBACK_UPPER_WATCH", "REPAIR_WATCH"}:
        reasons.append("价格仍在技术回踩带观察层，不是主动买点。")
    if confirmation_score < 60:
        reasons.append("量价确认分低于60。")
    if volume_price_gate == "HIGH_VOLUME_UNCONFIRMED":
        reasons.append("放量未确认，需等收盘确认或事件复核。")
    if target_quality in {"CHASE_LINE", "CONFIRMATION_LINE", "MISSING", ""}:
        reasons.append("收益目标质量不足，不能作为左侧买入依据。")
    if rr_score < 65:
        reasons.append("风险收益比分未达到小仓门槛。")
    return "".join(reasons) or "等待更清晰的技术承接与量价确认。"


def _current_subzone(primary_zone: str, left_probe_label: str, zone_position: float | None) -> str:
    if primary_zone == "DEEP_ACCEPTANCE":
        return "DEEP_SUPPORT_ZONE"
    if primary_zone == "PULLBACK_BUY":
        return {
            "LOWER_EDGE": "LEFT_PROBE_LOWER",
            "MID_ZONE": "LEFT_PROBE_MID",
            "UPPER_EDGE": "LEFT_PROBE_UPPER",
        }.get(left_probe_label, "LEFT_PROBE")
    if primary_zone == "PULLBACK_WATCH":
        return "ACCEPTANCE_OBSERVATION_ZONE"
    if primary_zone in {"PULLBACK_UPPER_WATCH", "REPAIR_WATCH"}:
        return "REPAIR_OBSERVATION_ZONE"
    if primary_zone == "CONFIRMATION_REVIEW":
        return "REEVALUATION_ZONE"
    if primary_zone == "INVALIDATION":
        return "INVALIDATION_ZONE"
    if primary_zone == "CHASE_RISK":
        return "CHASE_RISK_ZONE"
    if zone_position is not None and zone_position > 1.0:
        return "ABOVE_TECHNICAL_PULLBACK_BAND"
    return "OUTSIDE"


def _left_side_quality(left_probe_label: str, volume_price_gate: str, target_quality: str, rr_score: float) -> str:
    if left_probe_label == "OUTSIDE":
        return "OUTSIDE"
    if volume_price_gate in {"FAILED_ACCEPTANCE", "HIGH_VOLUME_UNCONFIRMED", "OVEREXTENDED"}:
        return "WEAK"
    if target_quality in {"CHASE_LINE", "CONFIRMATION_LINE", "MISSING", ""} or rr_score < 65:
        return "WATCH"
    if left_probe_label == "LOWER_EDGE":
        return "GOOD"
    return "WATCH"


def _zone_action_quality(action: str, volume_price_gate: str, rr_score: float) -> str:
    if action == ALLOW_SMALL_BUY:
        return "LOW_RISK_OBSERVATION"
    if action in {PAUSE_BUY, RISK_REVIEW, BLOCK_CHASE}:
        return "HIGH_RISK_ADVISORY"
    if volume_price_gate == "HIGH_VOLUME_UNCONFIRMED" or rr_score < 65:
        return "WAIT_CONFIRMATION"
    return "OBSERVE"


def _advisory_review(
    *,
    action: str,
    primary_zone: str,
    current_subzone: str,
    left_probe_position_label: str,
    volume_price_gate: str,
    confirmation_score: float,
    target_quality: str,
    rr_score: float,
    execution_reason: str,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    level = "INFO"
    if action == ALLOW_SMALL_BUY:
        return "INFO", ["左侧位置、量价承接、目标质量和风险收益比达到小仓观察参考条件"]
    if primary_zone == "CHASE_RISK" or action == BLOCK_CHASE:
        reasons.append("追高风险提醒")
        level = "HIGH_RISK"
    if primary_zone == "INVALIDATION" or action == PAUSE_BUY:
        reasons.append("结构失效风险，建议复核")
        level = "HIGH_RISK"
    if volume_price_gate == "HIGH_VOLUME_UNCONFIRMED":
        reasons.append("放量未确认，等收盘确认 / 事件复核")
        level = max(level, "WARNING", key=_advisory_rank)
    if volume_price_gate == "FAILED_ACCEPTANCE":
        reasons.append("量价承接失败")
        level = "HIGH_RISK"
    if left_probe_position_label == "UPPER_EDGE":
        reasons.append("左侧试仓区上沿，边缘观察")
        level = max(level, "WARNING", key=_advisory_rank)
    elif left_probe_position_label == "MID_ZONE":
        reasons.append("左侧试仓区中段，区内看承接")
        level = max(level, "WARNING", key=_advisory_rank)
    elif left_probe_position_label == "OUTSIDE" and current_subzone in {"ACCEPTANCE_OBSERVATION_ZONE", "REPAIR_OBSERVATION_ZONE"}:
        reasons.append("技术回踩带观察层，不建议主动新增")
        level = max(level, "WARNING", key=_advisory_rank)
    if target_quality in {"CHASE_LINE", "CONFIRMATION_LINE", "MISSING", ""}:
        reasons.append("收益目标质量不足")
        level = max(level, "WARNING", key=_advisory_rank)
    if rr_score < 65:
        reasons.append("风险收益比质量不足")
        level = max(level, "WARNING", key=_advisory_rank)
    if confirmation_score < 60 and action != WAIT_PULLBACK:
        reasons.append("量价确认分不足")
        level = max(level, "WARNING", key=_advisory_rank)
    if execution_reason:
        reasons.append(execution_reason)
    return level, _dedupe_text(reasons)


def _advisory_rank(level: str) -> int:
    return {"INFO": 0, "WARNING": 1, "HIGH_RISK": 2, "CRITICAL": 3}.get(str(level or "").upper(), 0)


def _dedupe_text(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _existing_position_action(action: str) -> str:
    if action == ALLOW_SMALL_BUY:
        return "已有持仓：股票层可观察，是否新增取决于账户额度与持仓约束。"
    if action == BLOCK_CHASE:
        return "已有持仓：不追高加仓，等待回到承接区。"
    if action == RISK_REVIEW:
        return "已有持仓：进入风控复核，暂停新增买入。"
    if action == PAUSE_BUY:
        return "已有持仓：暂停新增，复核失效线和放量破位风险。"
    if action == DATA_INSUFFICIENT:
        return "已有持仓：技术承接数据不足，先暂停新增买入。"
    return "已有持仓：持有观察，等待量价确认或更低回踩。"


def _no_position_action(action: str) -> str:
    if action == ALLOW_SMALL_BUY:
        return "未持仓：小仓观察参考，后续加仓仍需确认。"
    if action == BLOCK_CHASE:
        return "未持仓：追高风险提醒，等待回到技术回踩带。"
    if action == RISK_REVIEW:
        return "未持仓：系统不建议新增，先复核失效风险。"
    if action == PAUSE_BUY:
        return "未持仓：系统不建议新增，等待买区重新生成。"
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
        "date": _value(item, "date", "datetime", "timestamp", "time", "t"),
        "open": _first_number(item, "open", "o"),
        "high": _first_number(item, "high", "h"),
        "low": _first_number(item, "low", "l"),
        "close": _first_number(item, "close", "c", "adjClose", "adj_close"),
        "volume": _first_number(item, "volume", "v"),
    }
    return bar if any(bar.get(key) is not None for key in ("open", "high", "low", "close", "volume")) else {}


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
