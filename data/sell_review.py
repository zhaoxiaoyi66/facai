from __future__ import annotations

import json
from typing import Any, Iterable

from data.trade_safety_gate import has_concrete_reentry_plan


A_CLASS_SHORT_HOLDING_DAYS = 14
BUY_ZONE_STATUSES = {"IN_BUY_ZONE", "BELOW_BUY_ZONE"}
EMOTIONAL_SELL_REASONS = {"macro", "macro_fear", "anxiety", "panic_sell"}
EMOTIONAL_SELL_MOODS = {"macro_fear", "anxiety", "panic_sell", "regret_chase", "revenge_trade"}
PLANNED_EXECUTION_MOODS = {"plan_execution", "well_reasoned", "thoughtful"}
EVENT_EXIT_REASONS = {
    "earnings_catalyst_done",
    "event_trade_done",
    "catalyst_failed",
    "no_post_earnings_reaction",
    "planned_exit",
}
EVENT_PLAN_KEYWORDS = (
    "按计划",
    "退出",
    "无反应",
    "无波动",
    "催化失败",
    "事件结束",
    "止损",
    "目标",
    "小亏",
    "卖出计划",
    "退出条件",
)

FLAG_LABELS = {
    "below_target_sell": "低于买入目标价卖出",
    "sell_in_buy_zone": "卖出时处于买区/低于买区",
    "a_class_short_hold": "A类持仓天数过短",
    "a_class_missing_reentry": "A类卖出缺少具体回补计划",
    "emotional_sell": "情绪型卖出",
    "full_exit_without_review": "清仓无复盘",
    "non_c_event_review": "非 C 类事件交易需复核",
    "gate_blocked": "历史卖出风险提醒",
    "core_review": "核心仓卖出需复盘",
    "fundamental_change_sell": "基本面改写型卖出",
    "valuation_compression_sell": "估值压缩型卖出",
    "liquidity_shock_sell": "流动性冲击型卖出",
    "a_class_liquidity_shock_sell": "A类核心仓在流动性冲击下卖出",
}


def evaluate_sell_review_flags(trade: dict[str, Any]) -> dict[str, Any]:
    action = str(trade.get("action_type") or trade.get("action") or "").strip().lower()
    if action not in {"sell", "trim"}:
        return _empty_review()

    raw = trade.get("raw_entry") if isinstance(trade.get("raw_entry"), dict) else {}
    snapshot = _sell_context_snapshot(trade) or _sell_context_snapshot(raw)
    tier = _position_tier(snapshot) or _position_tier(trade) or _position_tier(raw)
    is_a_class = tier == "A"
    is_c_planned_event_exit = tier == "C" and _is_event_exit_reason(trade) and _has_structured_event_exit_plan(trade)

    sell_price = _first_number(snapshot.get("sell_price"), trade.get("sell_price"), trade.get("price"))
    target_price = _first_number(
        snapshot.get("target_sell_price"),
        trade.get("target_sell_price"),
        trade.get("pre_trade_target_sell_price"),
        raw.get("target_sell_price"),
        raw.get("pre_trade_target_sell_price"),
    )
    holding_days = _first_number(snapshot.get("holding_days_reference"), snapshot.get("holding_days"), trade.get("holding_days"))
    sell_context_type = _sell_context_type(trade, raw, snapshot)
    fundamental_change = bool(sell_context_type == "fundamental_change" or _fundamental_change_types(trade, raw, snapshot))
    valuation_compression = bool(sell_context_type == "valuation_compression")
    liquidity_shock = bool(sell_context_type == "liquidity_shock")
    planned_reduction = bool(sell_context_type == "planned_reduction")

    below_target = bool(
        sell_price is not None
        and target_price is not None
        and target_price > 0
        and sell_price < target_price
        and not is_c_planned_event_exit
    )
    sell_in_buy_zone = bool(_sell_in_buy_zone(trade, sell_price, snapshot) and not is_c_planned_event_exit)
    a_short_hold = bool(is_a_class and holding_days is not None and holding_days <= A_CLASS_SHORT_HOLDING_DAYS)
    missing_reentry = bool(is_a_class and not has_concrete_reentry_plan(raw or trade))
    emotional = _is_emotional_sell(trade)
    full_exit_without_review = bool(action == "sell" and not _has_meaningful_exit_review(trade))
    non_c_event_review = bool(tier in {"A", "B"} and _is_event_exit_reason(trade))
    gate_blocked = bool([str(item) for item in (trade.get("blockers") or raw.get("blockers") or [])])
    missing_fields = _missing_fields(trade, sell_price, target_price, holding_days, snapshot)
    context_too_sparse = _context_too_sparse(missing_fields)

    flag_keys: list[str] = []
    core_review_needed = False
    if below_target:
        flag_keys.append("below_target_sell")
        core_review_needed = core_review_needed or is_a_class
    if missing_reentry and not context_too_sparse:
        flag_keys.append("a_class_missing_reentry")
        core_review_needed = True
    if emotional:
        flag_keys.append("emotional_sell")
        core_review_needed = core_review_needed or is_a_class
    if a_short_hold:
        flag_keys.append("a_class_short_hold")
        core_review_needed = True
    if sell_in_buy_zone:
        flag_keys.append("sell_in_buy_zone")
        core_review_needed = core_review_needed or is_a_class
    if full_exit_without_review and not context_too_sparse:
        flag_keys.append("full_exit_without_review")
    if non_c_event_review:
        flag_keys.append("non_c_event_review")
    if gate_blocked:
        flag_keys.append("gate_blocked")
    if fundamental_change:
        flag_keys.append("fundamental_change_sell")
    if valuation_compression:
        flag_keys.append("valuation_compression_sell")
    if liquidity_shock:
        flag_keys.append("liquidity_shock_sell")
    a_class_liquidity_shock = bool(is_a_class and liquidity_shock)
    if a_class_liquidity_shock:
        flag_keys.append("a_class_liquidity_shock_sell")

    suspected = bool(
        (is_a_class and (below_target or sell_in_buy_zone or (missing_reentry and not context_too_sparse)))
        or (emotional and not _is_plan_execution(trade))
        or (a_short_hold and not _has_meaningful_exit_review(trade))
        or gate_blocked
    )
    labels = [FLAG_LABELS[key] for key in _dedupe(flag_keys)]
    if core_review_needed:
        labels.insert(0, FLAG_LABELS["core_review"])

    return {
        "flags": _dedupe(flag_keys),
        "labels": _dedupe(labels),
        "below_target_sell": below_target,
        "sell_in_buy_zone": sell_in_buy_zone,
        "a_class_short_hold": a_short_hold,
        "a_class_missing_reentry": missing_reentry,
        "emotional_sell": emotional,
        "sell_context_type": sell_context_type,
        "fundamental_change_sell": fundamental_change,
        "valuation_compression_sell": valuation_compression,
        "liquidity_shock_sell": liquidity_shock,
        "a_class_liquidity_shock_sell": a_class_liquidity_shock,
        "planned_reduction_sell": planned_reduction,
        "full_exit_without_review": full_exit_without_review,
        "suspected_sell_fly": suspected,
        "data_missing_fields": missing_fields,
        "position_tier": tier,
    }


def summarize_sell_review_flags(trades: Iterable[dict[str, Any]]) -> dict[str, int]:
    reviews = [_review(trade) for trade in trades]
    return {
        "suspected_sell_fly_count": sum(1 for item in reviews if item.get("suspected_sell_fly")),
        "a_class_suspected_sell_fly_count": sum(
            1
            for item in reviews
            if item.get("suspected_sell_fly") and str(item.get("position_tier") or "").upper() == "A"
        ),
        "emotional_sell_count": sum(1 for item in reviews if item.get("emotional_sell")),
        "buy_zone_sell_count": sum(1 for item in reviews if item.get("sell_in_buy_zone")),
        "below_target_sell_count": sum(1 for item in reviews if item.get("below_target_sell")),
        "a_class_short_hold_sell_count": sum(1 for item in reviews if item.get("a_class_short_hold")),
        "a_class_missing_reentry_count": sum(1 for item in reviews if item.get("a_class_missing_reentry")),
        "full_exit_without_review_count": sum(1 for item in reviews if item.get("full_exit_without_review")),
    }


def format_sell_review_label(flags: dict[str, Any] | list[str] | None) -> str:
    if flags is None:
        return "数据不足"
    if isinstance(flags, list):
        labels = [str(item) for item in flags if str(item).strip()]
    else:
        labels = [str(item) for item in flags.get("labels") or [] if str(item).strip()]
        if not labels and flags.get("data_missing_fields"):
            return "数据不足"
    return " / ".join(labels) if labels else "无明显复盘标签"


def _review(trade: dict[str, Any]) -> dict[str, Any]:
    if isinstance(trade.get("sell_review"), dict):
        return trade["sell_review"]
    return evaluate_sell_review_flags(trade)


def _empty_review() -> dict[str, Any]:
    return {
        "flags": [],
        "labels": [],
        "below_target_sell": False,
        "sell_in_buy_zone": False,
        "a_class_short_hold": False,
        "a_class_missing_reentry": False,
        "emotional_sell": False,
        "sell_context_type": "",
        "fundamental_change_sell": False,
        "valuation_compression_sell": False,
        "liquidity_shock_sell": False,
        "a_class_liquidity_shock_sell": False,
        "planned_reduction_sell": False,
        "full_exit_without_review": False,
        "suspected_sell_fly": False,
        "data_missing_fields": [],
        "position_tier": "",
    }


def _sell_in_buy_zone(trade: dict[str, Any], sell_price: float | None, snapshot: dict[str, Any] | None = None) -> bool:
    raw = trade.get("raw_entry") if isinstance(trade.get("raw_entry"), dict) else {}
    snapshot = snapshot or {}
    explicit = snapshot.get("in_or_below_buy_zone_at_sell")
    if isinstance(explicit, bool):
        return explicit
    zone = str(
        snapshot.get("zone_status")
        or snapshot.get("price_position")
        or trade.get("zone_status")
        or trade.get("buy_zone_status")
        or raw.get("zone_status")
        or raw.get("buy_zone_status")
        or ""
    ).strip().upper()
    if zone in BUY_ZONE_STATUSES:
        return True
    buy_zone = (
        snapshot.get("buy_zone")
        or trade.get("buy_zone")
        or trade.get("radar_buy_zone")
        or raw.get("buy_zone")
        or raw.get("radar_buy_zone")
    )
    lower, upper = _zone_bounds(buy_zone)
    return bool(sell_price is not None and lower is not None and upper is not None and lower <= sell_price <= upper)


def _zone_bounds(value: object) -> tuple[float | None, float | None]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return (None, None)
    if isinstance(value, dict):
        lower = _first_number(value.get("lower"), value.get("low"), value.get("min"), value.get("from"))
        upper = _first_number(value.get("upper"), value.get("high"), value.get("max"), value.get("to"))
        return (lower, upper)
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return (_number(value[0]), _number(value[1]))
    return (None, None)


def _is_emotional_sell(trade: dict[str, Any]) -> bool:
    raw = trade.get("raw_entry") if isinstance(trade.get("raw_entry"), dict) else {}
    snapshot = _sell_context_snapshot(trade) or _sell_context_snapshot(raw)
    context_type = _sell_context_type(trade, raw, snapshot)
    if context_type == "emotional_sell":
        return True
    reason = str(trade.get("sell_reason_type") or raw.get("sell_reason_type") or "").strip().lower()
    mood = str(trade.get("sell_mood") or trade.get("decision_mood") or raw.get("decision_mood") or "").strip().lower()
    text = " ".join(
        str(item or "")
        for item in (
            trade.get("sell_reason"),
            trade.get("notes"),
            trade.get("sell_thesis_note"),
            raw.get("notes"),
            raw.get("sell_reason"),
            raw.get("sell_thesis_note"),
            snapshot.get("sell_thesis_note"),
        )
    )
    keywords = ("焦虑", "恐慌", "宏观恐慌", "害怕回撤", "复仇交易", "临时害怕")
    return bool(reason in EMOTIONAL_SELL_REASONS or mood in EMOTIONAL_SELL_MOODS or any(item in text for item in keywords))


def _is_plan_execution(trade: dict[str, Any]) -> bool:
    raw = trade.get("raw_entry") if isinstance(trade.get("raw_entry"), dict) else {}
    snapshot = _sell_context_snapshot(trade) or _sell_context_snapshot(raw)
    if _sell_context_type(trade, raw, snapshot) == "planned_reduction":
        return True
    mood = str(trade.get("sell_mood") or trade.get("decision_mood") or raw.get("decision_mood") or "").strip().lower()
    reason = str(trade.get("sell_reason_type") or raw.get("sell_reason_type") or "").strip().lower()
    text = " ".join(str(item or "") for item in (trade.get("notes"), raw.get("notes"), trade.get("reentry_plan_text")))
    return bool(mood in PLANNED_EXECUTION_MOODS or reason in {"target_price", "planned_exit"} or "按计划" in text)


def _has_meaningful_exit_review(trade: dict[str, Any]) -> bool:
    raw = trade.get("raw_entry") if isinstance(trade.get("raw_entry"), dict) else {}
    if has_concrete_reentry_plan(raw or trade):
        return True
    text = " ".join(
        str(item or "")
        for item in (
            trade.get("reentry_plan_text"),
            trade.get("post_sell_plan"),
            trade.get("sell_review_reason"),
            trade.get("notes"),
            raw.get("reentry_plan_text"),
            raw.get("post_sell_plan"),
            raw.get("sell_review_reason"),
            raw.get("notes"),
        )
    ).strip()
    if len(text) >= 12:
        return True
    reason = str(trade.get("sell_reason_type") or raw.get("sell_reason_type") or "").strip().lower()
    return reason in {"target_price", "thesis_broken", "risk_control", "planned_exit"}


def _sell_context_type(trade: dict[str, Any], raw: dict[str, Any] | None = None, snapshot: dict[str, Any] | None = None) -> str:
    raw = raw or (trade.get("raw_entry") if isinstance(trade.get("raw_entry"), dict) else {})
    snapshot = snapshot or _sell_context_snapshot(trade) or _sell_context_snapshot(raw)
    return str(
        trade.get("sell_context_type")
        or raw.get("sell_context_type")
        or snapshot.get("sell_context_type")
        or ""
    ).strip().lower()


def _fundamental_change_types(
    trade: dict[str, Any],
    raw: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> list[str]:
    raw = raw or (trade.get("raw_entry") if isinstance(trade.get("raw_entry"), dict) else {})
    snapshot = snapshot or _sell_context_snapshot(trade) or _sell_context_snapshot(raw)
    value = (
        trade.get("fundamental_change_types")
        or trade.get("fundamental_change_type")
        or raw.get("fundamental_change_types")
        or raw.get("fundamental_change_type")
        or snapshot.get("fundamental_change_type")
        or snapshot.get("fundamental_change_types")
    )
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value] if value.strip() else []
    elif isinstance(value, (list, tuple, set)):
        parsed = list(value)
    else:
        parsed = []
    return [str(item) for item in parsed if str(item).strip()]


def _is_event_exit_reason(trade: dict[str, Any]) -> bool:
    raw = trade.get("raw_entry") if isinstance(trade.get("raw_entry"), dict) else {}
    return str(trade.get("sell_reason_type") or raw.get("sell_reason_type") or "").strip().lower() in EVENT_EXIT_REASONS


def _has_structured_event_exit_plan(trade: dict[str, Any]) -> bool:
    raw = trade.get("raw_entry") if isinstance(trade.get("raw_entry"), dict) else {}
    text = " ".join(
        str(item or "")
        for item in (
            trade.get("notes"),
            trade.get("buy_reason"),
            trade.get("reentry_plan_text"),
            raw.get("notes"),
            raw.get("buy_reason"),
            raw.get("reentry_plan_text"),
            raw.get("classification_note"),
            raw.get("exit_plan"),
            raw.get("event_plan"),
        )
    )
    return any(keyword in text for keyword in EVENT_PLAN_KEYWORDS)


def _missing_fields(
    trade: dict[str, Any],
    sell_price: float | None,
    target_price: float | None,
    holding_days: float | None,
    snapshot: dict[str, Any] | None = None,
) -> list[str]:
    missing: list[str] = []
    snapshot = snapshot or _sell_context_snapshot(trade)
    if sell_price is None:
        missing.append("sell_price")
    if target_price is None:
        missing.append("target_sell_price")
    if holding_days is None:
        missing.append("holding_days")
    raw = trade.get("raw_entry") if isinstance(trade.get("raw_entry"), dict) else {}
    if not (
        trade.get("zone_status")
        or trade.get("buy_zone_status")
        or trade.get("buy_zone")
        or snapshot.get("zone_status")
        or snapshot.get("price_position")
        or snapshot.get("buy_zone")
        or raw.get("zone_status")
        or raw.get("buy_zone_status")
        or raw.get("buy_zone")
    ):
        missing.append("zone_status")
    return missing


def _context_too_sparse(missing_fields: list[str]) -> bool:
    critical = {"sell_price", "target_sell_price", "holding_days", "zone_status"}
    return critical.issubset(set(missing_fields))


def _position_tier(item: dict[str, Any]) -> str:
    value = str(
        item.get("position_tier")
        or item.get("position_class")
        or item.get("pre_trade_position_tier")
        or ""
    ).strip().upper()
    return value if value in {"A", "B", "C"} else ""


def _sell_context_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    snapshot = item.get("sell_context_snapshot")
    if isinstance(snapshot, dict):
        return snapshot
    snapshot_json = item.get("sell_context_snapshot_json")
    if isinstance(snapshot_json, str) and snapshot_json.strip():
        try:
            parsed = json.loads(snapshot_json)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _first_number(*values: object) -> float | None:
    for value in values:
        parsed = _number(value)
        if parsed is not None:
            return parsed
    return None


def _number(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
