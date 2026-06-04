from __future__ import annotations

import json
from typing import Any

from data.trading_discipline import evaluate_trading_discipline


DISCIPLINE_ACTION_TYPES = {"sell", "trim"}
CLASSIFICATION_ACTION_TYPES = {"buy", "add"}
DECISION_MOOD_TYPES = {
    "well_reasoned",
    "plan_execution",
    "fomo",
    "anxiety",
    "macro_fear",
    "revenge_trade",
    "boredom_trade",
    "panic_sell",
    "regret_chase",
    "uncertainty",
    "calm",
    "discipline_check",
}
SELL_SYNC_BLOCK_REASON = "纪律门禁 BLOCK，禁止同步到组合持仓；该记录只能作为违规交易记录用于复盘。"
BUY_RADAR_SYNC_BLOCK_REASON = "Radar 买入门禁未通过或标记为仅观察，禁止同步到组合持仓。"
BUY_RADAR_MISSING_GATE_REASON = "Radar 买入门禁快照缺失，禁止自动同步到组合持仓。"
BUY_TIER_MISSING_REASON = "买入 / 加仓缺少 A/B/C 持仓属性，禁止同步到组合持仓。"


def build_trade_safety_snapshot(symbol: str, action_type: str, values: dict[str, Any]) -> dict[str, Any]:
    """Canonical backend gate for trade discipline snapshots.

    UI may render its own live explanation, but saved records and portfolio sync
    must use this module so actual/planned sell checks cannot drift.
    """
    action = str(action_type or "").strip().lower()
    empty = empty_trade_safety_snapshot()
    if action not in DISCIPLINE_ACTION_TYPES | CLASSIFICATION_ACTION_TYPES:
        return empty

    position_class = _clean_position_class(_value(values, "positionClass", "position_class"))
    core_position_min_pct = _optional_ratio(
        _value(values, "corePositionMinPct", "core_position_min_pct", "corePositionPct", "core_position_pct"),
        "core_position_min_pct",
    )
    trading_position_max_pct = _optional_ratio(
        _value(values, "tradingPositionMaxPct", "trading_position_max_pct", "tradingPositionPct", "trading_position_pct"),
        "trading_position_max_pct",
    )
    classification_note = _clean_text(_value(values, "classificationNote", "classification_note"))

    if action in CLASSIFICATION_ACTION_TYPES:
        empty.update(
            {
                "position_class": position_class or None,
                "core_position_min_pct": core_position_min_pct,
                "trading_position_max_pct": trading_position_max_pct,
                "classification_note": classification_note,
            }
        )
        return empty

    planned_sell_pct = _optional_ratio(_value(values, "plannedSellPct", "planned_sell_pct"), "planned_sell_pct")
    if planned_sell_pct is None:
        planned_sell_pct = 0.0
    actual_sell_pct = _actual_sell_pct(values, planned_sell_pct)
    sell_reason_type = _clean_text(_value(values, "sellReasonType", "sell_reason_type"))
    thesis_broken = _clean_bool(_value(values, "thesisBroken", "thesis_broken"))
    position_over_limit = _clean_bool(_value(values, "positionOverLimit", "position_over_limit"))
    reentry_plan = _clean_reentry_plan(values)
    has_reentry_plan = has_concrete_reentry_plan(reentry_plan)

    result = evaluate_trading_discipline(
        symbol=symbol,
        positionClass=position_class or "C",
        corePositionPct=core_position_min_pct,
        tradingPositionPct=trading_position_max_pct,
        unrealizedGainPct=_optional_ratio(_value(values, "unrealizedGainPct", "unrealized_gain_pct"), "unrealized_gain_pct"),
        plannedAction=action,
        plannedSellPct=planned_sell_pct,
        sellReasonType=sell_reason_type,
        thesisBroken=thesis_broken,
        positionOverLimit=position_over_limit,
        hasReentryPlan=has_reentry_plan,
        actualSellPct=actual_sell_pct,
        decisionMood=_clean_decision_mood(_value(values, "decisionMood", "decision_mood")),
    )

    return {
        "position_class": position_class or None,
        "core_position_min_pct": core_position_min_pct,
        "trading_position_max_pct": trading_position_max_pct,
        "classification_note": classification_note,
        "planned_sell_pct": planned_sell_pct,
        "actual_sell_pct": actual_sell_pct,
        "sell_reason_type": sell_reason_type,
        "sell_level": result.sellLevel,
        "thesis_broken": int(thesis_broken),
        "position_over_limit": int(position_over_limit),
        "has_reentry_plan": int(has_reentry_plan),
        **reentry_plan,
        "max_allowed_sell_pct": result.maxAllowedSellPct,
        "can_sell_core": int(result.canSellCore),
        "requires_reentry_plan": int(result.requiresReentryPlan),
        "discipline_status": result.disciplineStatus,
        "blockers_json": _reasons_json(result.blockers),
        "warnings_json": _reasons_json(result.warnings),
        "reminder_text": result.reminderText,
    }


def trade_sync_policy(entry: dict[str, Any]) -> dict[str, Any]:
    action_type = str(entry.get("action_type") or "").strip().lower()
    discipline_status = str(entry.get("discipline_status") or "").strip().lower()
    blockers = _reasons_list(entry.get("blockers"), entry.get("blockers_json"))
    sell_blocked = action_type in DISCIPLINE_ACTION_TYPES and (discipline_status == "blocked" or bool(blockers))
    buy_blocked = _buy_sync_blocked_by_radar(entry, action_type)
    buy_missing_gate = _buy_sync_missing_radar_gate(entry, action_type)
    buy_missing_tier = _buy_sync_missing_position_class(entry, action_type)
    blocked = sell_blocked or buy_blocked or buy_missing_gate or buy_missing_tier
    reason = ""
    if sell_blocked:
        reason = SELL_SYNC_BLOCK_REASON
    elif buy_blocked:
        reason = BUY_RADAR_SYNC_BLOCK_REASON
    elif buy_missing_gate:
        reason = BUY_RADAR_MISSING_GATE_REASON
    elif buy_missing_tier:
        reason = BUY_TIER_MISSING_REASON
    return {
        "canSync": not blocked,
        "reason": reason,
    }


def _buy_sync_blocked_by_radar(entry: dict[str, Any], action_type: str) -> bool:
    return action_type in CLASSIFICATION_ACTION_TYPES and (
        _clean_bool(entry.get("radar_blocked")) or _clean_bool(entry.get("radar_observation_only"))
    )


def _buy_sync_missing_radar_gate(entry: dict[str, Any], action_type: str) -> bool:
    return (
        action_type in CLASSIFICATION_ACTION_TYPES
        and not str(entry.get("radar_decision") or "").strip()
        and not str(entry.get("gate_checked_at") or "").strip()
    )


def _buy_sync_missing_position_class(entry: dict[str, Any], action_type: str) -> bool:
    position_class = str(entry.get("position_class") or "").strip().upper()
    return action_type in CLASSIFICATION_ACTION_TYPES and position_class not in {"A", "B", "C"}


def empty_trade_safety_snapshot() -> dict[str, Any]:
    return {
        "position_class": None,
        "core_position_min_pct": None,
        "trading_position_max_pct": None,
        "classification_note": "",
        "planned_sell_pct": None,
        "actual_sell_pct": None,
        "sell_reason_type": None,
        "sell_level": None,
        "thesis_broken": None,
        "position_over_limit": None,
        "has_reentry_plan": None,
        "reentry_plan_text": None,
        "reentry_pullback_price": None,
        "reentry_breakout_price": None,
        "reentry_time_stop_days": None,
        "reentry_buy_back_pct_on_pullback": None,
        "reentry_buy_back_pct_on_breakout": None,
        "reentry_thesis_invalidation": None,
        "max_allowed_sell_pct": None,
        "can_sell_core": None,
        "requires_reentry_plan": None,
        "discipline_status": None,
        "blockers_json": None,
        "warnings_json": None,
        "reminder_text": None,
    }


def has_concrete_reentry_plan(values: dict[str, Any]) -> bool:
    thesis_invalidation = _clean_text(values.get("reentry_thesis_invalidation") or values.get("reentryThesisInvalidation"))
    if not thesis_invalidation:
        return False
    pullback_price = _safe_optional_non_negative_number(
        values.get("reentry_pullback_price") or values.get("reentryPullbackPrice"),
        "reentry_pullback_price",
    )
    pullback_pct = _safe_optional_ratio(
        values.get("reentry_buy_back_pct_on_pullback") or values.get("reentryBuyBackPctOnPullback"),
        "reentry_buy_back_pct_on_pullback",
    )
    breakout_price = _safe_optional_non_negative_number(
        values.get("reentry_breakout_price") or values.get("reentryBreakoutPrice"),
        "reentry_breakout_price",
    )
    breakout_pct = _safe_optional_ratio(
        values.get("reentry_buy_back_pct_on_breakout") or values.get("reentryBuyBackPctOnBreakout"),
        "reentry_buy_back_pct_on_breakout",
    )
    time_stop_days = _safe_optional_int(
        values.get("reentry_time_stop_days") or values.get("reentryTimeStopDays"),
        "reentry_time_stop_days",
    )
    if _positive_number(pullback_price) and _positive_number(pullback_pct):
        return True
    if _positive_number(breakout_price) and _positive_number(breakout_pct):
        return True
    if time_stop_days is not None and time_stop_days > 0 and (
        _positive_number(pullback_pct) or _positive_number(breakout_pct)
    ):
        return True
    return False


def _actual_sell_pct(values: dict[str, Any], planned_sell_pct: float) -> float:
    explicit = _optional_ratio(_value(values, "actualSellPct", "actual_sell_pct"), "actual_sell_pct")
    if explicit is not None:
        return explicit
    current_position_quantity = _optional_non_negative_number(
        _value(values, "currentPositionQuantity", "current_position_quantity"),
        "current_position_quantity",
    )
    trade_quantity = _optional_non_negative_number(_value(values, "quantity", "tradeQuantity", "trade_quantity"), "quantity")
    if current_position_quantity and current_position_quantity > 0 and trade_quantity is not None:
        return trade_quantity / current_position_quantity
    return planned_sell_pct


def _clean_reentry_plan(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "reentry_plan_text": _clean_text(_value(values, "reentryPlanText", "reentry_plan_text")),
        "reentry_pullback_price": _optional_non_negative_number(
            _value(values, "reentryPullbackPrice", "reentry_pullback_price"),
            "reentry_pullback_price",
        ),
        "reentry_breakout_price": _optional_non_negative_number(
            _value(values, "reentryBreakoutPrice", "reentry_breakout_price"),
            "reentry_breakout_price",
        ),
        "reentry_time_stop_days": _optional_int(
            _value(values, "reentryTimeStopDays", "reentry_time_stop_days"),
            "reentry_time_stop_days",
        ),
        "reentry_buy_back_pct_on_pullback": _optional_ratio(
            _value(values, "reentryBuyBackPctOnPullback", "reentry_buy_back_pct_on_pullback"),
            "reentry_buy_back_pct_on_pullback",
        ),
        "reentry_buy_back_pct_on_breakout": _optional_ratio(
            _value(values, "reentryBuyBackPctOnBreakout", "reentry_buy_back_pct_on_breakout"),
            "reentry_buy_back_pct_on_breakout",
        ),
        "reentry_thesis_invalidation": _clean_text(
            _value(values, "reentryThesisInvalidation", "reentry_thesis_invalidation")
        ),
    }


def _clean_decision_mood(value: object) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    if text not in DECISION_MOOD_TYPES:
        raise ValueError("decision_mood is invalid")
    return text


def _optional_non_negative_number(value: object, field: str) -> float | None:
    if value is None or value == "":
        return None
    number = _optional_number(value, field)
    if number is None:
        return None
    if number < 0:
        raise ValueError(f"{field} must be non-negative")
    return number


def _optional_ratio(value: object, field: str) -> float | None:
    number = _optional_non_negative_number(value, field)
    if number is None:
        return None
    return number / 100 if abs(number) > 1 else number


def _safe_optional_non_negative_number(value: object, field: str) -> float | None:
    try:
        return _optional_non_negative_number(value, field)
    except ValueError:
        return None


def _safe_optional_ratio(value: object, field: str) -> float | None:
    try:
        return _optional_ratio(value, field)
    except ValueError:
        return None


def _safe_optional_int(value: object, field: str) -> int | None:
    try:
        return _optional_int(value, field)
    except ValueError:
        return None


def _positive_number(value: object) -> bool:
    try:
        return value is not None and float(value) > 0
    except (TypeError, ValueError):
        return False


def _optional_number(value: object, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc


def _optional_int(value: object, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if number < 0:
        raise ValueError(f"{field} must be non-negative")
    return number


def _clean_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _value(values: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in values:
            return values[name]
    return None


def _reasons_json(value: object) -> str:
    if value is None:
        return "[]"
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value]
        value = parsed
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    return json.dumps([str(item) for item in value if str(item).strip()], ensure_ascii=False)


def _reasons_list(*values: object) -> list[str]:
    reasons: list[str] = []
    for value in values:
        try:
            parsed = json.loads(_reasons_json(value))
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, list):
            continue
        reasons.extend(str(item) for item in parsed if str(item).strip())
    return reasons


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _clean_position_class(value: object) -> str:
    text = _clean_text(value).upper()
    return text if text in {"A", "B", "C"} else ""
