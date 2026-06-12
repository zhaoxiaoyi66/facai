from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


BLOCKED_BUY_MOODS = {"fomo", "anxiety", "bottom_fishing_impulse", "revenge_trade", "regret_chase"}
FRESH_PLAN_REVIEW_MINUTES = 30


@dataclass(frozen=True)
class PlannedLadderBuyResult:
    planned_ladder_buy: bool
    can_sync_to_portfolio: bool
    plan_match_status: str
    buy_plan_id: str
    buy_plan_level: str
    plan_trigger_price: float | None
    plan_planned_quantity: float | None
    plan_remaining_quantity: float | None
    plan_max_position_pct: float | None
    plan_block_reasons: list[str]
    plan_notes: list[str]
    fresh_plan_execution: bool = False
    plan_age_minutes: float | None = None
    plan_recently_created_or_modified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_planned_ladder_buy(
    *,
    ticker: str,
    plan: dict[str, Any] | None,
    radar_report: object | None,
    quantity: object,
    trade_price: object,
    planned_after_position_pct: object,
    decision_mood: str,
    trade_created_at: object | None = None,
    prior_level_quantities: dict[str, float] | None = None,
) -> PlannedLadderBuyResult:
    symbol = str(ticker or "").strip().upper()
    active_plan = plan or {}
    levels = _plan_levels(active_plan, prior_level_quantities or {})
    current_price = _first_number(_report_value(radar_report, "current_price"), trade_price)
    qty = _number(quantity)
    after_pct = _number(planned_after_position_pct)
    max_position_pct = _first_number(
        active_plan.get("max_position_pct"),
        active_plan.get("target_position_pct"),
        active_plan.get("planned_position_pct"),
    )
    data_status = str(_report_value(radar_report, "data_status") or "").strip().upper()
    decision = str(_report_value(radar_report, "decision") or "").strip().upper()
    is_stale = bool(_report_value(radar_report, "is_stale"))
    valuation_score = _number(_report_value(radar_report, "valuation_score"))
    mood = str(decision_mood or "").strip().lower()
    notes: list[str] = []

    if not levels:
        return _blocked(symbol, "no_plan", ["未找到分批买入计划。"], max_position_pct=max_position_pct)

    timing = _plan_timing_snapshot(active_plan, trade_created_at)

    if current_price is None:
        return _blocked(symbol, "price_missing", ["缺少当前价格，不能匹配分批买入计划。"], max_position_pct=max_position_pct, timing=timing)
    if data_status in {"DATA_MISSING", "MISSING"} or decision == "DATA_MISSING" or is_stale:
        notes.append("Radar buy-zone data is missing or stale; this is an advisory warning and does not block a matched plan buy.")
    if qty is None or qty <= 0:
        return _blocked(symbol, "quantity_missing", ["买入数量无效，不能匹配计划档位。"], max_position_pct=max_position_pct, timing=timing)
    if mood in BLOCKED_BUY_MOODS:
        return _blocked(symbol, "mood_blocked", ["当前交易心理属于情绪交易风险；系统不阻止买入，但建议确认不是情绪交易。"], max_position_pct=max_position_pct, timing=timing)
    if not _has_exit_or_invalidation(active_plan):
        return _blocked(symbol, "missing_exit_condition", ["分批买入计划缺少失效条件 / 退出条件。"], max_position_pct=max_position_pct, timing=timing)
    if max_position_pct is None:
        return _blocked(symbol, "missing_position_limit", ["分批买入计划缺少买后仓位上限。"], max_position_pct=max_position_pct, timing=timing)
    if after_pct is not None and after_pct > max_position_pct + 1e-9:
        return _blocked(
            symbol,
            "position_exceeds_plan",
            [f"买入后仓位 {after_pct:.1f}% 超过计划上限 {max_position_pct:.1f}%。"],
            max_position_pct=max_position_pct,
            timing=timing,
        )
    if valuation_score is not None and valuation_score < 40 and not _has_review_note(active_plan):
        return _blocked(symbol, "valuation_review_required", ["估值评分低于 40，计划缺少复核说明。"], max_position_pct=max_position_pct, timing=timing)

    eligible = [level for level in levels if level["trigger_price"] is not None and current_price <= level["trigger_price"]]
    if not eligible:
        next_level = min((level for level in levels if level["trigger_price"] is not None), key=lambda item: item["trigger_price"], default=None)
        trigger = next_level.get("trigger_price") if next_level else None
        reason = "当前价尚未触发下一档分批买入价格。"
        if trigger is not None:
            reason = f"当前价 {current_price:g} 高于下一档触发价 {trigger:g}。"
        return _blocked(symbol, "not_triggered", [reason], max_position_pct=max_position_pct, timing=timing)

    level = eligible[0]
    remaining = level["remaining_quantity"]
    if remaining is None or remaining <= 0:
        return _blocked(
            symbol,
            "level_filled",
            [f"{level['label']} 已没有剩余可买数量。"],
            level=level,
            max_position_pct=max_position_pct,
            timing=timing,
        )
    if qty > remaining + 1e-9:
        return _blocked(
            symbol,
            "quantity_exceeds_level",
            [f"买入数量 {qty:g} 股超过 {level['label']} 剩余计划数量 {remaining:g} 股。"],
            level=level,
            max_position_pct=max_position_pct,
            timing=timing,
        )

    notes.extend([
        f"已匹配 {level['label']}：当前价不高于触发价 {level['trigger_price']:g}。",
        f"本次数量 {qty:g} 股不超过剩余计划数量 {remaining:g} 股。",
    ])
    if timing["fresh_plan_execution"]:
        notes.append("该计划刚创建或刚修改，本次执行会被标记为临时计划执行，供复盘参考。")

    return PlannedLadderBuyResult(
        planned_ladder_buy=True,
        can_sync_to_portfolio=True,
        plan_match_status="allow_planned_add",
        buy_plan_id=symbol,
        buy_plan_level=level["label"],
        plan_trigger_price=level["trigger_price"],
        plan_planned_quantity=level["planned_quantity"],
        plan_remaining_quantity=remaining,
        plan_max_position_pct=max_position_pct,
        plan_block_reasons=[],
        plan_notes=notes,
        fresh_plan_execution=timing["fresh_plan_execution"],
        plan_age_minutes=timing["plan_age_minutes"],
        plan_recently_created_or_modified=timing["plan_recently_created_or_modified"],
    )


def _plan_levels(plan: dict[str, Any], prior: dict[str, float]) -> list[dict[str, Any]]:
    raw = plan.get("buy_plan_tranches") if isinstance(plan, dict) else []
    if not isinstance(raw, list):
        raw = []
    levels: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or f"第 {index + 1} 档")
        trigger = _number(item.get("price"))
        planned_qty = _number(item.get("shares"))
        bought_qty = float(prior.get(label) or 0)
        remaining = None if planned_qty is None else max(0.0, planned_qty - bought_qty)
        levels.append(
            {
                "label": label,
                "trigger_price": trigger,
                "planned_quantity": planned_qty,
                "remaining_quantity": remaining,
            }
        )
    return levels


def _blocked(
    symbol: str,
    status: str,
    reasons: list[str],
    *,
    level: dict[str, Any] | None = None,
    max_position_pct: float | None = None,
    timing: dict[str, Any] | None = None,
) -> PlannedLadderBuyResult:
    level = level or {}
    timing = timing or {}
    return PlannedLadderBuyResult(
        planned_ladder_buy=False,
        can_sync_to_portfolio=False,
        plan_match_status=status,
        buy_plan_id=symbol,
        buy_plan_level=str(level.get("label") or ""),
        plan_trigger_price=level.get("trigger_price"),
        plan_planned_quantity=level.get("planned_quantity"),
        plan_remaining_quantity=level.get("remaining_quantity"),
        plan_max_position_pct=max_position_pct,
        plan_block_reasons=reasons,
        plan_notes=[],
        fresh_plan_execution=bool(timing.get("fresh_plan_execution")),
        plan_age_minutes=timing.get("plan_age_minutes"),
        plan_recently_created_or_modified=bool(timing.get("plan_recently_created_or_modified")),
    )


def _has_exit_or_invalidation(plan: dict[str, Any]) -> bool:
    return any(
        str(plan.get(key) or "").strip()
        for key in ("invalidation_condition", "stop_adding_condition", "notes", "earnings_review_points")
    )


def _has_review_note(plan: dict[str, Any]) -> bool:
    text = " ".join(str(plan.get(key) or "") for key in ("notes", "invalidation_condition", "earnings_review_points"))
    return bool(text.strip())


def _plan_timing_snapshot(plan: dict[str, Any], trade_created_at: object | None) -> dict[str, Any]:
    trade_time = _parse_datetime(trade_created_at)
    created_at = _parse_datetime(plan.get("created_at"))
    material_updated_at = _parse_datetime(plan.get("material_updated_at")) or _parse_datetime(plan.get("updated_at"))
    candidates = [value for value in (created_at, material_updated_at) if value is not None]
    if trade_time is None or not candidates:
        return {
            "fresh_plan_execution": False,
            "plan_age_minutes": None,
            "plan_recently_created_or_modified": False,
        }
    effective_plan_time = max(candidates)
    age_minutes = max(0.0, (trade_time - effective_plan_time).total_seconds() / 60)
    is_fresh = age_minutes < FRESH_PLAN_REVIEW_MINUTES
    return {
        "fresh_plan_execution": is_fresh,
        "plan_age_minutes": round(age_minutes, 2),
        "plan_recently_created_or_modified": is_fresh,
    }


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _report_value(report: object, key: str) -> Any:
    if report is None:
        return None
    if isinstance(report, dict):
        return report.get(key)
    if hasattr(report, key):
        return getattr(report, key)
    if hasattr(report, "to_dict"):
        data = report.to_dict()
        if isinstance(data, dict):
            return data.get(key)
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
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
