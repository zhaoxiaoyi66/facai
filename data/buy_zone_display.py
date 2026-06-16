from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any


SMALL_BUY_ACTIONS = {"ALLOW_SMALL_BUY", "ALLOW_ADD_ON_PULLBACK"}
WAIT_ACTIONS = {"WAIT_PULLBACK", "WAIT_CONFIRMATION"}
PAUSE_ACTIONS = {"PAUSE_BUY"}

ACCEPTANCE_STATE_TEXT = {
    "CLEAR_ACCEPTANCE": "明显承接",
    "FORMING_ACCEPTANCE": "初步承接",
    "WEAK_ACCEPTANCE": "承接不足",
    "HIGH_VOLUME_UNCONFIRMED": "放量未确认",
    "FALLING_KNIFE_RISK": "飞刀风险",
    "STRUCTURE_BROKEN": "结构破坏",
}

ENTRY_QUALITY_TEXT = {
    "GOOD_LEFT_SIDE": "舒服左侧",
    "EDGE_OBSERVE": "边缘观察",
    "WAIT_CONFIRMATION": "等确认",
    "HIGH_RISK": "高风险",
    "INVALID": "无效",
}

SUBZONE_TEXT = {
    "DEEP_SUPPORT_ZONE": "深度承接区",
    "LEFT_PROBE": "左侧试仓候选区",
    "LEFT_PROBE_LOWER": "左侧试仓候选区",
    "LEFT_PROBE_MID": "左侧试仓候选区",
    "LEFT_PROBE_UPPER": "左侧试仓候选区",
    "ACCEPTANCE_OBSERVATION_ZONE": "承接观察区",
    "REPAIR_OBSERVATION_ZONE": "修复观察区",
    "REEVALUATION_ZONE": "重评区",
    "INVALIDATION_ZONE": "结构失效风险区",
    "CHASE_RISK_ZONE": "追高风险区",
    "ABOVE_TECHNICAL_PULLBACK_BAND": "等待回踩区",
    "OUTSIDE": "观察区外",
}

POSITION_TEXT = {
    "LOWER_EDGE": "下沿",
    "MID_ZONE": "中段",
    "UPPER_EDGE": "上沿",
    "OUTSIDE": "",
}


def build_buy_zone_display(
    context: Any,
    row: Any | None = None,
    *,
    mode: str = "default",
) -> dict[str, Any]:
    """Build the single display contract for canonical buy zone context.

    The context remains the only technical source of truth. Optional row data is
    used only for account/sizing copy such as current add room and holdings.
    """

    ctx = _dict(context)
    row_data = _dict(row)
    action = str(_value(ctx, "current_action", "currentAction", default="") or "").strip().upper()
    if not action:
        action = "DATA_INSUFFICIENT"
    current_price = _number(_value(ctx, "current_price", "currentPrice"))
    if current_price is None:
        current_price = _number(_value(row_data, "current_price", "currentPrice", "price"))
    primary_zone = str(_value(ctx, "primary_zone", "primaryZone", default="") or "").strip().upper()
    primary_zone_text = str(_value(ctx, "primary_zone_text", "primaryZoneText", default="") or "").strip()
    zone_text = _zone_text(ctx, action)
    in_zone = _is_current_in_primary_zone(ctx, current_price)
    has_position, shares = _position_state(row_data)
    current_add = _current_add_limit(row_data)
    volume_text = _volume_confirmation_text(ctx, row_data)
    technical = _technical_text(action, primary_zone_text, in_zone, ctx)
    account = _account_text(
        action=action,
        has_position=has_position,
        shares=shares,
        current_add=current_add,
        row=row_data,
    )
    main_action = _main_action_text(action, account, technical, has_position, current_add, primary_zone)
    acceptance_state = str(_value(ctx, "acceptance_state", "acceptanceState", default="") or "").strip().upper()
    acceptance_text = str(
        _value(ctx, "acceptance_state_text", "acceptanceStateText", default="")
        or ACCEPTANCE_STATE_TEXT.get(acceptance_state, "")
    ).strip()
    entry_quality = str(_value(ctx, "entry_quality", "entryQuality", default="") or "").strip().upper()
    entry_quality_text = ENTRY_QUALITY_TEXT.get(entry_quality, entry_quality)
    current_subzone = str(_value(ctx, "current_subzone", "currentSubzone", default="") or "").strip().upper()
    subzone_text = _current_subzone_text(ctx, current_subzone)
    subzone_position_label = _current_subzone_position_label(ctx, current_subzone, current_price)
    subzone_position_text = POSITION_TEXT.get(subzone_position_label, "")
    subzone_display_text = _join_subzone_position(subzone_text, subzone_position_text)
    missing = _text_list(_value(ctx, "missing_fields", "missingFields", default=[]))
    missing_text = " / ".join(_missing_label(item) for item in missing if str(item).strip())
    explanation = _explanation(ctx, technical["explanation"], account["sizing_action_text"])
    next_step = _next_step_text(action, ctx, missing_text, has_position, current_add)
    entry_hint = account["sizing_action_text"] if current_add is not None and current_add <= 0 else technical["badge_hint"]
    risk_reward_text = _risk_reward_display_text(ctx, row_data)
    main_conclusion = _main_conclusion_text(
        acceptance_text=acceptance_text,
        subzone_display_text=subzone_display_text,
        entry_quality_text=entry_quality_text,
        main_action=main_action,
    )
    result = {
        "mode": mode,
        "action": action,
        "action_code": action,
        "buy_zone_action": action,
        "badge_label": technical["badge_label"],
        "badge_hint": entry_hint,
        "label": technical["badge_label"],
        "hint": entry_hint,
        "technical_hint": technical["badge_hint"],
        "technical_action_text": technical["technical_action_text"],
        "technical_status_text": technical["technical_action_text"],
        "technical_layer_text": technical["technical_action_text"],
        "main_action_text": main_action,
        "main_advisory_text": main_action,
        "main_conclusion_text": main_conclusion,
        "advisory_headline_text": main_conclusion,
        "sharp_conclusion_text": main_conclusion,
        "action_text": main_action,
        "display_action_text": main_action,
        "sizing_action": account["sizing_action"],
        "sizing_action_text": account["sizing_action_text"],
        "account_action_text": account["account_action_text"],
        "position_action_text": account["account_action_text"],
        "has_position": has_position,
        "current_shares": shares,
        "current_add_limit_percent": current_add,
        "primary_zone_text": primary_zone_text,
        "zone_text": zone_text,
        "primary_zone_range_text": zone_text,
        "current_price_text": _money(current_price),
        "volume_confirmation_text": volume_text,
        "acceptance_state": acceptance_state,
        "acceptance_state_text": acceptance_text,
        "acceptance_badge_text": acceptance_text,
        "acceptance_action_text": f"{acceptance_text} / {main_action}" if acceptance_text else main_action,
        "entry_quality": entry_quality,
        "entry_quality_text": entry_quality_text,
        "falling_knife_risk": str(_value(ctx, "falling_knife_risk", "fallingKnifeRisk", default="") or ""),
        "acceptance_reasons": _text_list(_value(ctx, "acceptance_reasons", "acceptanceReasons", default=[])),
        "missing_confirmation": _text_list(_value(ctx, "missing_confirmation", "missingConfirmation", default=[])),
        "required_confirmation_price": _number(_value(ctx, "required_confirmation_price", "requiredConfirmationPrice", "confirmation_price", "confirmationPrice")),
        "risk_reward_text": risk_reward_text,
        "risk_reward_note": _risk_reward_note(ctx, row_data),
        "risk_reward": _number(_value(ctx, "risk_reward", "riskReward", "raw_rr", "rawRr")),
        "action_new_cash": str(_value(ctx, "action_new_cash", "actionNewCash", default="") or account["account_action_text"]),
        "action_existing_position": str(_value(ctx, "action_existing_position", "actionExistingPosition", default="") or account["account_action_text"]),
        "entry_condition_text": str(_value(ctx, "entry_condition_text", "entryConditionText", "add_trigger_condition_text", default="") or ""),
        "invalidation_condition_text": str(_value(ctx, "invalidation_condition_text", "invalidationConditionText", "pause_new_condition_text", default="") or ""),
        "confidence_breakdown": _dict(_value(ctx, "confidence_breakdown", "confidenceBreakdown", default={}) or {}),
        "zone_position": _number(_value(ctx, "zone_position", "zonePosition")),
        "zone_position_text": str(_value(ctx, "zone_position_text", "zonePositionText", default="") or ""),
        "current_subzone": current_subzone,
        "current_subzone_text": subzone_text,
        "current_subzone_position_label": subzone_position_label,
        "current_subzone_position_text": subzone_position_text,
        "current_subzone_display_text": subzone_display_text,
        "left_side_position_pct": _number(_value(ctx, "left_side_position_pct", "leftSidePositionPct")),
        "left_side_quality": str(_value(ctx, "left_side_quality", "leftSideQuality", default="") or ""),
        "left_probe_position_label": str(_value(ctx, "left_probe_position_label", "leftProbePositionLabel", default="") or ""),
        "distance_to_left_probe_low_pct": _number(_value(ctx, "distance_to_left_probe_low_pct", "distanceToLeftProbeLowPct")),
        "distance_to_left_probe_high_pct": _number(_value(ctx, "distance_to_left_probe_high_pct", "distanceToLeftProbeHighPct")),
        "volume_price_gate": str(_value(ctx, "volume_price_gate", "volumePriceGate", default="") or ""),
        "volume_price_state": str(_value(ctx, "volume_price_state", "volumePriceState", "volume_price_gate", "volumePriceGate", default="") or ""),
        "execution_gate_reason": str(_value(ctx, "execution_gate_reason", "executionGateReason", default="") or ""),
        "zone_action_quality": str(_value(ctx, "zone_action_quality", "zoneActionQuality", default="") or ""),
        "advisory_level": str(_value(ctx, "advisory_level", "advisoryLevel", default="") or _display_advisory_level(action)),
        "advisory_reasons": _text_list(_value(ctx, "advisory_reasons", "advisoryReasons", default=[])),
        "next_step_text": next_step,
        "missing_fields": missing,
        "missing_fields_text": missing_text,
        "explanation": explanation,
        "status_explanation": explanation,
        "entry_display_label": technical["badge_label"],
        "entry_action_hint": entry_hint,
        "entry_display_reason": explanation,
        "entry_context_status": action,
        "compact_label": technical["badge_label"],
        "compact_hint": entry_hint,
    }
    return result


def _main_conclusion_text(
    *,
    acceptance_text: str,
    subzone_display_text: str,
    entry_quality_text: str,
    main_action: str,
) -> str:
    parts = [part for part in (acceptance_text, subzone_display_text, entry_quality_text, main_action) if str(part or "").strip()]
    if not parts:
        return main_action or "等待复核"
    return "，".join(dict.fromkeys(parts))


def _current_subzone_text(context: dict[str, Any], current_subzone: str) -> str:
    explicit = str(_value(context, "current_subzone_text", "currentSubzoneText", default="") or "").strip()
    if explicit:
        return explicit
    if current_subzone in SUBZONE_TEXT:
        return SUBZONE_TEXT[current_subzone]
    primary_zone = str(_value(context, "primary_zone", "primaryZone", default="") or "").strip().upper()
    if primary_zone in {"PULLBACK_WATCH", "PULLBACK_BUY"}:
        return "承接观察区" if primary_zone == "PULLBACK_WATCH" else "左侧试仓候选区"
    if primary_zone in {"PULLBACK_UPPER_WATCH", "REPAIR_WATCH"}:
        return "修复观察区"
    if primary_zone == "CONFIRMATION_REVIEW":
        return "重评区"
    if primary_zone == "INVALIDATION":
        return "结构失效风险区"
    if primary_zone == "CHASE_RISK":
        return "追高风险区"
    return ""


def _current_subzone_position_label(context: dict[str, Any], current_subzone: str, current_price: float | None) -> str:
    explicit = str(_value(context, "current_subzone_position_label", "currentSubzonePositionLabel", default="") or "").strip().upper()
    if explicit:
        return explicit
    if current_subzone.startswith("LEFT_PROBE"):
        return str(_value(context, "left_probe_position_label", "leftProbePositionLabel", default="") or "OUTSIDE").strip().upper()
    bounds = _current_subzone_bounds(context, current_subzone)
    position = _range_position(current_price, bounds[0], bounds[1])
    if position is None:
        return "OUTSIDE"
    if position < 0.35:
        return "LOWER_EDGE"
    if position < 0.70:
        return "MID_ZONE"
    return "UPPER_EDGE"


def _current_subzone_bounds(context: dict[str, Any], current_subzone: str) -> tuple[float | None, float | None]:
    if current_subzone == "ACCEPTANCE_OBSERVATION_ZONE":
        return (
            _number(_value(context, "left_probe_zone_high", "leftProbeZoneHigh")),
            _number(_value(context, "observe_zone_high", "observeZoneHigh")),
        )
    if current_subzone == "REPAIR_OBSERVATION_ZONE":
        return (
            _number(_value(context, "observe_zone_high", "observeZoneHigh")),
            _number(_value(context, "pullback_zone_high", "pullbackZoneHigh")),
        )
    if current_subzone.startswith("LEFT_PROBE") or current_subzone == "LEFT_PROBE":
        return (
            _number(_value(context, "left_probe_zone_low", "leftProbeZoneLow")),
            _number(_value(context, "left_probe_zone_high", "leftProbeZoneHigh")),
        )
    if current_subzone == "DEEP_SUPPORT_ZONE":
        return (
            _number(_value(context, "deep_support_zone_low", "deepSupportZoneLow", "support_zone_low", "supportZoneLow")),
            _number(_value(context, "deep_support_zone_high", "deepSupportZoneHigh", "support_zone_high", "supportZoneHigh")),
        )
    if current_subzone == "INVALIDATION_ZONE":
        return (
            _number(_value(context, "invalidation_risk_zone_low", "invalidationRiskZoneLow", "invalidation_zone_low", "invalidationZoneLow")),
            _number(_value(context, "invalidation_risk_zone_high", "invalidationRiskZoneHigh", "invalidation_zone_high", "invalidationZoneHigh")),
        )
    return (None, None)


def _range_position(current: float | None, low: float | None, high: float | None) -> float | None:
    if current is None or low is None or high is None:
        return None
    low, high = sorted((low, high))
    width = high - low
    if width <= 0 or current < low or current > high:
        return None
    return (current - low) / width


def _join_subzone_position(subzone_text: str, position_text: str) -> str:
    if not subzone_text:
        return ""
    if not position_text:
        return subzone_text
    if subzone_text.endswith(position_text):
        return subzone_text
    return f"{subzone_text}{position_text}"


def _technical_text(action: str, primary_zone_text: str, in_zone: bool, context: dict[str, Any]) -> dict[str, str]:
    near_recheck = _number(
        _value(context, "confirmation_price", "confirm_price", "confirmation_line", "confirm_line")
    ) is not None
    zone_position = _number(_value(context, "zone_position", "zonePosition"))
    primary_zone_code = str(_value(context, "primary_zone", "primaryZone") or "").upper()
    if zone_position is not None and zone_position > 1.0 and action in {"WAIT_CONFIRMATION", "WAIT_PULLBACK"}:
        return _technical("等待回踩", "不追", "当前价高于技术回踩带，等待回踩；若继续上冲则提示追高风险。")
    if primary_zone_code == "PULLBACK_UPPER_WATCH" or (
        zone_position is not None and zone_position > 0.75 and "上沿" in primary_zone_text
    ):
        if action in {"WAIT_CONFIRMATION", "WAIT_PULLBACK"}:
            return _technical("买区上沿", "不建议新增", "当前价位于买区上沿 / 修复观察区，持有观察，不主动新增。")
    if action == "WAIT_PULLBACK":
        return _technical("等待回踩", "不追", "价格偏高，等待回到技术回踩带。")
    if action == "WAIT_CONFIRMATION":
        if in_zone:
            return _technical("区内看承接", "等量价", "价格到了，但还要看量价和K线承接。")
        if near_recheck:
            return _technical("等突破再评估", "不追", "站上重新评估线后再判断，不等于直接买入。")
        return _technical("区内看承接", "等量价", "价格接近观察范围，但量价承接还未确认。")
    if action in SMALL_BUY_ACTIONS:
        if "回踩" in primary_zone_text or str(context.get("primary_zone") or "") == "PULLBACK_BUY":
            text = "技术回踩带内，可观察"
        else:
            text = "价格候选区内，可观察"
        return _technical("区内观察", "小仓观察参考", text)
    if action == "BLOCK_CHASE":
        return _technical("追高风险区", "高风险", "价格已脱离技术回踩带，系统提示追高风险。")
    if action == "RISK_REVIEW":
        return _technical("风险复核", "不建议加仓", "先复核失效线和风险，再决定是否处理。")
    if action == "PAUSE_BUY":
        return _technical("结构失效风险", "不建议新增", "买区或承接已经失效，等待重新评估。")
    if action == "AVOID":
        return _technical("暂不参与", "观望", "当前不参与，等待结构改善。")
    return _technical("数据不足", "不给买区", "技术承接数据不足，不生成明确买区建议。")


def _technical(label: str, hint: str, explanation: str) -> dict[str, str]:
    return {
        "badge_label": label,
        "badge_hint": hint,
        "technical_action_text": explanation if "，" in explanation else label,
        "explanation": explanation,
    }


def _account_text(
    *,
    action: str,
    has_position: bool,
    shares: float | None,
    current_add: float | None,
    row: dict[str, Any],
) -> dict[str, str]:
    add_is_zero = current_add is not None and current_add <= 0
    if action == "DATA_INSUFFICIENT" and not add_is_zero:
        if has_position:
            return {
                "sizing_action": "HOLD_NO_ADD",
                "sizing_action_text": "不建议加仓",
                "account_action_text": f"已有 {_shares_text(shares)}，持有观察，不建议加仓",
            }
        return {
            "sizing_action": "WAIT_DATA",
            "sizing_action_text": "不建议买入",
            "account_action_text": "无持仓，不建议买入，等待数据补齐",
        }
    if action == "PAUSE_BUY" and not add_is_zero:
        if has_position:
            return {
                "sizing_action": "PAUSE_ADD",
                "sizing_action_text": "不建议加仓",
                "account_action_text": f"已有 {_shares_text(shares)}，持有观察，不建议新增",
            }
        return {
            "sizing_action": "PAUSE_BUY",
            "sizing_action_text": "不建议买入",
            "account_action_text": "无持仓，不建议买入，等待买区重新评估",
        }
    if add_is_zero:
        if has_position:
            return {
                "sizing_action": "CURRENT_NO_ADD",
                "sizing_action_text": "当前不建议新增",
                "account_action_text": f"已有 {_shares_text(shares)}，当前新增额度为 0，系统不建议新增",
            }
        return {
            "sizing_action": "CURRENT_NO_ADD",
            "sizing_action_text": "当前不建议新增",
            "account_action_text": "无持仓，当前新增额度为 0，系统不建议新增",
        }
    if has_position:
        if action in SMALL_BUY_ACTIONS:
            return {
                "sizing_action": "CAN_REVIEW_ADD",
                "sizing_action_text": "可小幅复核",
                "account_action_text": f"已有 {_shares_text(shares)}，新增额度参考 {_pct_text(current_add)}",
            }
        return {
            "sizing_action": "HOLD_WAIT",
            "sizing_action_text": "持有观察",
            "account_action_text": f"已有 {_shares_text(shares)}，持有观察",
        }
    if action in SMALL_BUY_ACTIONS:
        return {
            "sizing_action": "CAN_PROBE",
            "sizing_action_text": "小仓观察建议",
            "account_action_text": f"无持仓，新增额度参考 {_pct_text(current_add)}",
        }
    return {
        "sizing_action": "WAIT",
        "sizing_action_text": "等待",
        "account_action_text": "无持仓，等待更清晰条件",
    }


def _main_action_text(
    action: str,
    account: dict[str, str],
    technical: dict[str, str],
    has_position: bool,
    current_add: float | None,
    primary_zone: str = "",
) -> str:
    if current_add is not None and current_add <= 0:
        if has_position:
            return "持有观察 / 当前不建议新增"
        if action == "WAIT_CONFIRMATION" and primary_zone == "PULLBACK_BUY":
            return f"{technical['badge_label']} / 当前不建议新增"
        return "仅观察 / 当前不建议新增"
    if action == "DATA_INSUFFICIENT":
        return "持有观察 / 不建议加仓" if has_position else "数据不足 / 等待补齐"
    if action == "BLOCK_CHASE":
        return "追高风险提醒"
    if action == "RISK_REVIEW":
        return "风险复核 / 不建议加仓" if has_position else "风险复核 / 不建议新增"
    if action == "PAUSE_BUY":
        return "持有观察 / 不建议加仓" if has_position else "结构失效风险 / 重新评估"
    if action in SMALL_BUY_ACTIONS:
        return "小仓观察参考"
    if action == "WAIT_PULLBACK":
        return "等待回踩"
    if action == "WAIT_CONFIRMATION":
        return technical["badge_label"]
    if action == "AVOID":
        return "暂不参与"
    return account.get("sizing_action_text") or technical["badge_label"]


def _zone_text(context: dict[str, Any], action: str) -> str:
    if action == "DATA_INSUFFICIENT":
        return "暂不生成"
    low = _number(_value(context, "pullback_zone_low", "support_zone_low", "primary_zone_low"))
    high = _number(_value(context, "pullback_zone_high", "support_zone_high", "primary_zone_high"))
    if low is not None or high is not None:
        return _range_text(low, high)
    return str(_value(context, "primary_zone_text", "primaryZoneText", default="暂缺") or "暂缺")


def _is_current_in_primary_zone(context: dict[str, Any], current_price: float | None) -> bool:
    if current_price is None:
        return False
    low = _number(_value(context, "pullback_zone_low", "support_zone_low", "primary_zone_low"))
    high = _number(_value(context, "pullback_zone_high", "support_zone_high", "primary_zone_high"))
    return low is not None and high is not None and low <= current_price <= high


def _volume_confirmation_text(context: dict[str, Any], row: dict[str, Any]) -> str:
    nested = _dict(_value(row, "volumePriceAcceptance", "volume_price_acceptance"))
    gate = str(_value(context, "volume_price_gate", "volumePriceGate") or "").strip().upper()
    confirmation_score = _number(_value(context, "confirmation_score", "confirmationScore"))
    early_volume_ratio = _number(
        _value(context, "volume_ratio", "volumeRatio")
        or _value(row, "volumeRatio", "volume_ratio")
        or _value(nested, "volume_ratio", "volumeRatio")
    )
    if gate == "CONFIRMED_ACCEPTANCE":
        return "量价承接确认"
    if gate == "FORMING_ACCEPTANCE":
        if early_volume_ratio is not None and early_volume_ratio < 0.7:
            return "缩量回踩，但承接未确认"
        if confirmation_score is not None and confirmation_score < 60:
            return "量能不足，暂不能确认有效承接"
        return "初步承接，尚未确认"
    if gate == "HIGH_VOLUME_UNCONFIRMED":
        return "放量未确认，等收盘确认 / 事件复核"
    if gate == "FAILED_ACCEPTANCE":
        return "承接失败"
    if gate == "OVEREXTENDED":
        return "脱离观察区，不构成低吸依据"
    status = str(
        _value(context, "volume_price_status", "volumePriceStatus")
        or _value(row, "volumePriceStatus", "volume_price_status")
        or _value(nested, "volume_price_status", "volumePriceStatus")
        or ""
    ).strip().upper()
    score = _number(
        _value(context, "volume_acceptance_score", "volume_price_score", "volumePriceScore")
        or _value(row, "volumePriceScore", "volume_price_score")
        or _value(nested, "volume_price_score", "volumePriceScore")
    )
    volume_ratio = _number(
        _value(context, "volume_ratio", "volumeRatio")
        or _value(row, "volumeRatio", "volume_ratio")
        or _value(nested, "volume_ratio", "volumeRatio")
    )
    if status == "ACCEPTANCE_CONFIRMED":
        return "量价承接确认"
    if status == "FAILED":
        return "承接失败"
    if status == "OVEREXTENDED_SUPPORT_READ":
        return "脱离观察区，不构成低吸依据"
    if status in {"DATA_MISSING", "DATA_INSUFFICIENT"}:
        return "量价数据不足"
    if status == "UNCONFIRMED":
        if (volume_ratio is not None and volume_ratio >= 2.0) or (score is not None and score < 35):
            return "放量未确认，等收盘确认 / 事件复核"
        return "量价未确认，等收盘确认"
    if status == "FORMING" or (score is not None and score < 55):
        daily_return = _number(_value(context, "daily_return_pct", "day_change_pct") or _value(row, "dailyReturnPct", "day_change_pct"))
        close_position = _number(_value(context, "close_position", "closePosition", "close_position_in_range") or _value(row, "closePosition"))
        if volume_ratio is not None and volume_ratio < 0.7:
            if (daily_return is not None and daily_return < 0) and not (close_position is not None and close_position >= 0.55):
                return "缩量调整，尚不构成承接"
            return "初步止跌，仍需确认"
        current_price = _number(_value(context, "current_price", "currentPrice") or _value(row, "current_price", "currentPrice", "price"))
        confirmation = _number(_value(context, "confirmation_price", "confirm_price", "confirmation_line", "confirm_line"))
        resistance = _number(_value(context, "resistance_zone_low", "technical_resistance_price", "recent_breakout_level"))
        if volume_ratio is not None and volume_ratio > 1.2 and confirmation is not None and current_price is not None and current_price >= confirmation:
            return "承接成立，建议复核"
        if volume_ratio is not None and volume_ratio > 1.0 and resistance is not None and current_price is not None and current_price >= resistance:
            return "承接成立"
        if volume_ratio is not None and volume_ratio >= 2.0:
            return "放量未确认，等收盘确认"
        return "初步承接，尚未确认"
    return "量价待确认"


def _next_step_text(
    action: str,
    context: dict[str, Any],
    missing_text: str,
    has_position: bool,
    current_add: float | None,
) -> str:
    if action == "DATA_INSUFFICIENT":
        suffix = f"：{missing_text}" if missing_text else ""
        return f"补齐技术承接数据{suffix}"
    if current_add is not None and current_add <= 0:
        return "等待新增额度恢复或技术确认进一步增强"
    confirmation = _number(_value(context, "confirmation_price", "confirm_price", "confirmation_line", "confirm_line"))
    invalidation = _number(_value(context, "invalidation_price", "invalid_price", "invalid_line"))
    if action == "WAIT_PULLBACK":
        return "等待价格回到技术回踩带"
    if action == "WAIT_CONFIRMATION":
        if confirmation is not None:
            extra = _higher_confirmation_text(context, confirmation)
            return f"站上 {_money(confirmation)} 后重新评估，不等于直接买入。{extra}".strip()
        return "等待量价和K线承接"
    if action in SMALL_BUY_ACTIONS:
        return "先小仓观察，后续加仓仍等确认"
    if action == "BLOCK_CHASE":
        return "等待回到技术回踩带或重新形成低吸结构"
    if action == "RISK_REVIEW":
        if invalidation is not None:
            return f"复核是否跌破 {_money(invalidation)}"
        return "先复核失效线和风险"
    if action == "PAUSE_BUY":
        if invalidation is not None:
            return f"跌破 {_money(invalidation)} 后系统不建议新增"
        return "等待买区重新评估"
    return "等待结构改善"


def _higher_confirmation_text(context: dict[str, Any], confirmation: float) -> str:
    candidates = (
        _number(_value(context, "resistance_zone_high", "resistanceZoneHigh")),
        _number(_value(context, "technical_resistance_price", "technicalResistancePrice")),
        _number(_value(context, "recent_breakout_level", "recentBreakoutLevel")),
        _number(_value(context, "pullback_zone_high", "pullbackZoneHigh")),
    )
    higher = [value for value in candidates if value is not None and value > confirmation * 1.001]
    if not higher:
        return ""
    return f" 放量站稳上方压力 {_money(min(higher))} 后，确认质量提高。"


def _risk_reward_display_text(context: dict[str, Any], row: dict[str, Any]) -> str:
    text = str(_value(context, "risk_reward_text", "riskRewardText", default="") or "").strip()
    note = _risk_reward_note(context, row)
    if not text:
        return note
    if note and note not in text:
        return f"{text}（{note}）"
    return text


def _risk_reward_note(context: dict[str, Any], row: dict[str, Any]) -> str:
    acceptance_state = str(_value(context, "acceptance_state", "acceptanceState", default="") or "").strip().upper()
    volume_score = _number(
        _value(context, "volume_acceptance_score", "volume_price_score", "volumePriceScore")
        or _value(row, "volumePriceScore", "volume_price_score")
    )
    if volume_score is None:
        breakdown = _dict(_value(context, "confidence_breakdown", "confidenceBreakdown", default={}) or {})
        volume_score = _number(_value(breakdown, "volume_score", "volumeScore"))
    if (acceptance_state and acceptance_state != "CLEAR_ACCEPTANCE") or (volume_score is not None and volume_score < 60):
        return "仅作参考，量价未确认"
    return ""


def _explanation(context: dict[str, Any], technical_text: str, sizing_text: str) -> str:
    reason = str(_value(context, "zone_selection_reason", "zoneSelectionReason", default="") or "").strip()
    parts = [technical_text]
    if reason:
        parts.append(reason)
    if sizing_text:
        parts.append(f"账户层：{sizing_text}。")
    return "；".join(part.rstrip("。") for part in parts if part)


def _position_state(row: dict[str, Any]) -> tuple[bool, float | None]:
    nested = _dict(_value(row, "actionFusion", "action_fusion"))
    shares = _number(
        _value(
            row,
            "current_shares",
            "currentShares",
            "quantity",
            "shares",
            "position_shares",
            "positionShares",
        )
    )
    if shares is None:
        shares = _number(_value(nested, "current_shares", "currentShares", "quantity"))
    weight = _number(_value(row, "portfolio_weight", "portfolioWeight", "positionPct", "current_weight", "currentWeight"))
    if weight is None:
        weight = _number(_value(nested, "current_weight", "currentWeight", "portfolio_weight", "portfolioWeight"))
    return bool((shares is not None and shares > 0) or (shares is None and weight is not None and weight > 0)), shares


def _current_add_limit(row: dict[str, Any]) -> float | None:
    nested = _dict(_value(row, "finalDecision", "final_decision"))
    value = _number(
        _value(
            row,
            "currentAddLimitPercent",
            "current_add_limit_percent",
            "current_add_pct",
            "systemCurrentAdd",
            "currentAddLimit",
            "maxSuggestedPosition",
        )
    )
    if value is None:
        value = _number(_value(nested, "currentAddLimitPercent", "current_add_pct"))
    return value


def _dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "to_dict"):
        try:
            converted = value.to_dict()
            return dict(converted) if isinstance(converted, dict) else {}
        except Exception:
            return {}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return dict(value.to_dict())
    if hasattr(value, "items"):
        try:
            return dict(value.items())
        except Exception:
            return {}
    return {}


def _value(source: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in source:
            return source[name]
    return default


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("$", "")
        if text.endswith("%"):
            text = text[:-1]
        if text.upper() in {"N/A", "NA", "NONE", "NULL", "暂缺"}:
            return None
        value = text
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _missing_label(value: str) -> str:
    text = str(value or "").strip()
    return {
        "daily_ohlcv": "历史K线",
        "daily_ohlcv_window": "历史K线窗口不足",
        "volume_ratio": "成交量/量比",
        "volume_acceptance": "量价承接",
        "ma20": "均线",
        "ma50": "均线",
        "ma200": "均线",
        "atr_14": "ATR",
        "atr": "ATR",
        "support_zone_low": "支撑压力",
        "support_zone_high": "支撑压力",
        "pullback_zone_low": "技术回踩带",
        "pullback_zone_high": "技术回踩带",
        "resistance_zone": "阻力区",
        "price": "当前价格",
        "buy_zone_context": "统一买区上下文",
    }.get(text, text)


def _display_advisory_level(action: str) -> str:
    if action in {"PAUSE_BUY", "RISK_REVIEW", "BLOCK_CHASE"}:
        return "HIGH_RISK"
    if action in {"DATA_INSUFFICIENT", "WAIT_CONFIRMATION", "WAIT_PULLBACK"}:
        return "WARNING"
    if action in SMALL_BUY_ACTIONS:
        return "INFO"
    return "INFO"


def _money(value: float | None) -> str:
    return "暂缺" if value is None else f"${value:,.2f}"


def _range_text(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{_money(low)} - {_money(high)}"
    if low is not None:
        return f">= {_money(low)}"
    if high is not None:
        return f"<= {_money(high)}"
    return "暂缺"


def _shares_text(shares: float | None) -> str:
    if shares is None:
        return "持仓"
    if abs(shares - round(shares)) < 0.0001:
        return f"{int(round(shares))} 股"
    return f"{shares:g} 股"


def _pct_text(value: float | None) -> str:
    if value is None:
        return "暂缺"
    return f"{value:.1f}%".replace(".0%", "%")
