from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from data.ai_stock_radar import build_cached_ai_stock_radar_report
from data.decision_log import TradeJournalStore
from data.planned_ladder_buy import evaluate_planned_ladder_buy
from data.portfolio import PortfolioPositionStore
from data.portfolio_trade_sync import apply_trade_to_portfolio, get_trade_portfolio_sync_status, preview_trade_values_portfolio_effect
from data.prices import CACHE_PATH
from data.stock_plan import StockPlanStore
from data.starter_position import evaluate_starter_position
from data.structure_entry import build_structure_entry_advisor_for_symbol, structure_entry_snapshot_fields
from data.trade_gate import buy_gate_entry_fields, evaluate_buy_gate


VALID_POSITION_TIERS = {"A", "B", "C"}
VALID_ENTRY_MODES = {"normal_buy", "planned_ladder_buy", "starter_position"}


def submit_portfolio_buy_add(
    symbol: str,
    values: dict[str, Any],
    *,
    path: Path = CACHE_PATH,
    radar_report: object | None = None,
) -> dict[str, Any]:
    ticker = _normalize_symbol(symbol)
    quantity = values.get("quantity")
    price = values.get("price")
    tier = _clean_position_tier(values.get("position_tier") or values.get("positionClass"))
    decision_mood = str(values.get("decision_mood") or values.get("decisionMood") or "").strip()
    buy_reason = str(values.get("buy_reason") or values.get("notes") or "").strip()
    target_sell_price = values.get("target_sell_price") or values.get("targetSellPrice")
    entry_mode = _clean_entry_mode(values.get("entry_mode") or values.get("entryMode"))
    starter_thesis = str(values.get("starter_thesis") or values.get("thesis") or buy_reason).strip()
    starter_add_plan = str(values.get("starter_add_plan") or values.get("add_plan") or values.get("follow_up_add_plan") or "").strip()
    starter_invalidation = str(
        values.get("starter_invalidation_condition") or values.get("invalidation_condition") or values.get("starterInvalidationCondition") or ""
    ).strip()
    observation_only = bool(values.get("radar_observation_only") or values.get("radarObservationOnly"))
    if observation_only:
        raise ValueError("仅观察不是真实成交；请用计划买入或价格提醒记录观察，不写入交易日志。")
    action_type = _portfolio_trade_action(ticker, path, values.get("action_type"))
    submitted_at = _hkt_now()
    portfolio_preview = preview_trade_values_portfolio_effect(
        ticker,
        {"action_type": action_type, "quantity": quantity, "price": price},
        path=path,
    )
    report = radar_report or build_cached_ai_stock_radar_report(ticker)
    structure_advisor = build_structure_entry_advisor_for_symbol(ticker, path=path, now=submitted_at)
    plan = StockPlanStore(path).get_plan(ticker)
    prior_level_quantities = _planned_ladder_prior_quantities(ticker, path)
    plan_gate = evaluate_planned_ladder_buy(
        ticker=ticker,
        plan=plan if entry_mode == "planned_ladder_buy" else None,
        radar_report=report,
        quantity=quantity,
        trade_price=price,
        planned_after_position_pct=portfolio_preview.get("afterPositionPct"),
        decision_mood=decision_mood,
        trade_created_at=submitted_at,
        prior_level_quantities=prior_level_quantities,
    )
    starter_gate = evaluate_starter_position(
        ticker=ticker,
        entry_mode=entry_mode,
        position_tier=tier,
        radar_report=report,
        before_position_pct=_before_position_pct(portfolio_preview),
        after_position_pct=portfolio_preview.get("afterPositionPct"),
        decision_mood=decision_mood,
        buy_reason=buy_reason,
        target_sell_price=target_sell_price,
        thesis=starter_thesis,
        add_plan=starter_add_plan,
        invalidation_condition=starter_invalidation,
        starter_max_pct=values.get("starter_max_pct") or values.get("starterMaxPct") or 7,
    )
    gate = evaluate_buy_gate(
        report,
        action_type=action_type,
        position_bucket=_position_bucket_for_tier(tier),
        planned_after_position_pct=portfolio_preview.get("afterPositionPct"),
        decision_mood=decision_mood,
        observation_only=observation_only,
        buy_reason=buy_reason,
    )
    gate_fields = buy_gate_entry_fields(gate, action_type=action_type)
    plan_fields = _buy_plan_entry_fields(plan_gate)
    starter_fields = _starter_entry_fields(entry_mode, starter_gate)
    advisory_notes = list(gate_fields.get("radarAdvisoryWarnings") or [])
    if entry_mode == "planned_ladder_buy" and plan_gate.can_sync_to_portfolio:
        advisory_notes.extend(plan_gate.plan_notes)
    if entry_mode == "starter_position" and starter_gate.can_sync_to_portfolio:
        advisory_notes.extend(starter_gate.starter_notes)
    gate_fields["radarAdvisoryWarnings"] = _dedupe_text(advisory_notes)
    if gate_fields["radarAdvisoryWarnings"] and not bool(gate_fields.get("gateHardBlocked")):
        gate_fields["radarAdvisoryOnly"] = True
    selected_entry_block_reasons: list[str] = []
    if entry_mode == "planned_ladder_buy" and not plan_gate.can_sync_to_portfolio:
        selected_entry_block_reasons.extend(plan_gate.plan_block_reasons)
    if entry_mode == "starter_position" and not starter_gate.can_sync_to_portfolio:
        selected_entry_block_reasons.extend(starter_gate.starter_block_reasons)
    if selected_entry_block_reasons:
        gate_fields["radarBlocked"] = True
        gate_fields["gateHardBlocked"] = True
        gate_fields["radarAdvisoryOnly"] = False
        gate_fields["radarBlockReasons"] = [
            *gate.reasons,
            *gate.required_actions,
            *selected_entry_block_reasons,
        ]
    if (plan_gate.can_sync_to_portfolio or starter_gate.can_sync_to_portfolio) and gate.is_blocked and not observation_only:
        override_notes = plan_gate.plan_notes if plan_gate.can_sync_to_portfolio else starter_gate.starter_notes
        gate_fields.update(
            {
                "radarBlocked": False,
                "radarBlockReasons": [
                    *gate.reasons,
                    *gate.required_actions,
                    *override_notes,
                ],
                "positionGateBlocked": False,
                "moodGateBlocked": False,
            }
        )
    elif gate.is_blocked or observation_only:
        plan_block_reasons = plan_gate.plan_block_reasons if entry_mode == "planned_ladder_buy" else []
        gate_fields["radarBlockReasons"] = [
            *gate.reasons,
            *gate.required_actions,
            *plan_block_reasons,
            *starter_gate.starter_block_reasons,
        ]
    core_pct, trading_pct = _tier_ratio_defaults(tier)
    entry_values = {
        "trade_date": str(values.get("trade_date") or submitted_at.date().isoformat()),
        "action_type": action_type,
        "quantity": quantity,
        "price": price,
        "decision_mood": decision_mood,
        "notes": buy_reason,
        "targetSellPrice": target_sell_price,
        "entryMode": entry_mode,
        "positionClass": tier,
        "corePositionMinPct": core_pct,
        "tradingPositionMaxPct": trading_pct,
        "classificationNote": values.get("classification_note") or values.get("classificationNote") or "",
        "createdAt": submitted_at.isoformat(),
        "radarDataStatus": _report_value(report, "data_status"),
        "radarIsStale": bool(_report_value(report, "is_stale")),
        **gate_fields,
        **plan_fields,
        **starter_fields,
        **structure_entry_snapshot_fields(structure_advisor, checked_at=submitted_at.isoformat()),
        "gateCheckedAt": submitted_at.isoformat(),
    }
    can_sync = _can_sync_buy_entry(
        entry_mode=entry_mode,
        gate=gate,
        plan_gate=plan_gate,
        starter_gate=starter_gate,
        observation_only=observation_only,
    )
    if not can_sync:
        raise ValueError(_buy_entry_block_reason(gate=gate, plan_gate=plan_gate, starter_gate=starter_gate, entry_mode=entry_mode))
    store = TradeJournalStore(path)
    saved = store.save_entry(ticker, entry_values)
    sync_result = apply_trade_to_portfolio(int(saved.get("id") or 0), path=path)
    if str(sync_result.get("status") or "") != "success":
        store.delete_entry(int(saved.get("id") or 0))
        raise ValueError(str(sync_result.get("error") or "成交入账失败，交易日志未保存。"))
    return {
        "entry": saved,
        "gate": gate.to_dict(),
        "planGate": plan_gate.to_dict(),
        "starterGate": starter_gate.to_dict(),
        "structureEntry": structure_advisor.to_dict(),
        "marketStatus": _buy_market_status(report, gate),
        "sync": sync_result,
        "actionType": action_type,
        "synced": bool(sync_result and sync_result.get("status") == "success"),
    }


def _buy_entry_block_reason(*, gate: Any, plan_gate: Any, starter_gate: Any, entry_mode: str) -> str:
    if entry_mode == "planned_ladder_buy" and not bool(plan_gate.can_sync_to_portfolio):
        reasons = [str(item) for item in getattr(plan_gate, "plan_block_reasons", []) if str(item).strip()]
        return "；".join(reasons) or "计划买入条件未触发，未入账。"
    if entry_mode == "starter_position" and not bool(starter_gate.can_sync_to_portfolio):
        reasons = [str(item) for item in getattr(starter_gate, "starter_block_reasons", []) if str(item).strip()]
        return "；".join(reasons) or "底仓建仓条件未通过，未入账。"
    reasons = [str(item) for item in [*getattr(gate, "reasons", []), *getattr(gate, "required_actions", [])] if str(item).strip()]
    return "；".join(reasons) or "买入校验未通过，未入账。"


def _can_sync_buy_entry(
    *,
    entry_mode: str,
    gate: Any,
    plan_gate: Any,
    starter_gate: Any,
    observation_only: bool,
) -> bool:
    if observation_only or gate.gate_hard_blocked:
        return False
    if entry_mode == "planned_ladder_buy":
        return bool(plan_gate.can_sync_to_portfolio)
    if entry_mode == "starter_position":
        return bool(starter_gate.can_sync_to_portfolio)
    return bool(gate.can_sync_to_portfolio)


def _dedupe_text(items: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _portfolio_trade_action(symbol: str, path: Path, requested: object = None) -> str:
    action = str(requested or "").strip().lower()
    if action in {"buy", "add"}:
        return action
    position = PortfolioPositionStore(path).get_position(symbol) or {}
    return "add" if _position_is_active(position) else "buy"


def _position_is_active(position: dict[str, Any]) -> bool:
    if not position:
        return False
    value = position.get("is_active", True)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in {"0", "false", "no", "n", "off"}


def _clean_position_tier(value: object) -> str:
    tier = str(value or "").strip().upper()
    if tier not in VALID_POSITION_TIERS:
        raise ValueError("position_tier must be A, B, or C")
    return tier


def _clean_entry_mode(value: object) -> str:
    mode = str(value or "normal_buy").strip().lower()
    return mode if mode in VALID_ENTRY_MODES else "normal_buy"


def _position_bucket_for_tier(tier: str) -> str:
    return "core" if tier == "A" else "trade"


def _tier_ratio_defaults(tier: str) -> tuple[float, float]:
    if tier == "A":
        return 0.60, 0.40
    return 0.0, 1.0


def _buy_plan_entry_fields(plan_gate) -> dict[str, Any]:
    return {
        "buyPlanId": plan_gate.buy_plan_id,
        "buyPlanLevel": plan_gate.buy_plan_level,
        "plannedLadderBuy": plan_gate.planned_ladder_buy,
        "planTriggerPrice": plan_gate.plan_trigger_price,
        "planPlannedQuantity": plan_gate.plan_planned_quantity,
        "planRemainingQuantity": plan_gate.plan_remaining_quantity,
        "planMaxPositionPct": plan_gate.plan_max_position_pct,
        "planMatchStatus": plan_gate.plan_match_status,
        "planBlockReasons": plan_gate.plan_block_reasons,
        "freshPlanExecution": plan_gate.fresh_plan_execution,
        "planAgeMinutes": plan_gate.plan_age_minutes,
        "planRecentlyCreatedOrModified": plan_gate.plan_recently_created_or_modified,
    }


def _starter_entry_fields(entry_mode: str, starter_gate) -> dict[str, Any]:
    return {
        "entryMode": entry_mode,
        "starterPosition": starter_gate.starter_position,
        "starterMaxPct": starter_gate.starter_max_pct,
        "starterPositionBeforePct": starter_gate.starter_position_before_pct,
        "starterPositionAfterPct": starter_gate.starter_position_after_pct,
        "starterMatchStatus": starter_gate.starter_match_status,
        "starterBlockReasons": starter_gate.starter_block_reasons,
    }


def _buy_market_status(report: object, gate) -> dict[str, Any]:
    daily_change_pct = _daily_change_pct(report)
    price_position = str(_first_report_value(report, "price_position", "zone_status") or "").strip().upper()
    decision = str(_report_value(report, "decision") or getattr(gate, "decision", "") or "").strip().upper()
    valuation_score = _number(_report_value(report, "valuation_score"))
    final_score = _number(_report_value(report, "final_score"))
    allowed_add_pct = _number(getattr(gate, "allowed_add_pct", None))
    is_stale = _boolish(_report_value(report, "is_stale"))
    data_status = str(_report_value(report, "data_status") or "").strip().lower()

    if is_stale or data_status in {"missing", "data_missing", "stale"}:
        technical_status = "买区数据缺失 / 过期，需人工判断"
    elif daily_change_pct is not None and daily_change_pct <= -8:
        technical_status = "财报后大跌 / 高波动"
    elif price_position == "IN_CHASE_ZONE" or decision == "BLOCK_CHASE":
        technical_status = "技术偏热 / 追高风险"
    elif price_position == "BELOW_BUY_ZONE":
        technical_status = "低于买区，需复核基本面"
    else:
        technical_status = "正常波动"

    if valuation_score is None:
        valuation_status = "估值缺失"
    elif valuation_score < 40:
        valuation_status = "估值仍偏高"
    elif price_position == "BELOW_BUY_ZONE":
        valuation_status = "价格低于买区，需确认基本面"
    else:
        valuation_status = "估值需复核"

    if is_stale or data_status in {"missing", "data_missing", "stale"}:
        discipline_status = "买区参考不可用，不单独阻止买入"
    elif allowed_add_pct is not None and allowed_add_pct <= 0:
        discipline_status = "系统参考新增仓位为 0%，仅作风险提示"
    elif price_position == "IN_BUY_ZONE":
        discipline_status = "进入纪律买区"
    elif price_position == "BELOW_BUY_ZONE":
        discipline_status = "低于买区需复核"
    elif price_position in {"ABOVE_BUY_ZONE", "IN_CHASE_ZONE"}:
        discipline_status = "未进入参考买区"
    else:
        discipline_status = "纪律区间需复核"

    notes: list[str] = []
    if daily_change_pct is not None and daily_change_pct <= -8:
        notes.append("不是系统错误；大跌不等于进入买区。")
    if valuation_score is not None and valuation_score < 40:
        notes.append("估值分低，不能因为回撤自动放行。")
    if final_score is not None and final_score < 70:
        notes.append("综合评分低于 70，仍不允许核心仓。")

    return {
        "technical_status": technical_status,
        "valuation_status": valuation_status,
        "discipline_status": discipline_status,
        "daily_change_pct": daily_change_pct,
        "price_position": price_position,
        "valuation_score": valuation_score,
        "final_score": final_score,
        "allowed_add_pct": allowed_add_pct,
        "notes": notes,
    }


def _before_position_pct(preview: dict[str, Any]) -> float | None:
    current_qty = _number(preview.get("currentQuantity"))
    after_qty = _number(preview.get("afterQuantity"))
    after_pct = _number(preview.get("afterPositionPct"))
    if current_qty is None or after_qty is None or after_pct is None or after_qty <= 0:
        return 0.0 if current_qty == 0 else None
    return after_pct * current_qty / after_qty


def _planned_ladder_prior_quantities(symbol: str, path: Path) -> dict[str, float]:
    result: dict[str, float] = {}
    for entry in TradeJournalStore(path).list_entries(symbol):
        if not bool(entry.get("planned_ladder_buy")):
            continue
        if bool(entry.get("radar_blocked")) or bool(entry.get("radar_observation_only")):
            continue
        sync_status = get_trade_portfolio_sync_status(int(entry.get("id") or 0), path=path)
        if str(sync_status.get("syncStatus") or "") != "synced":
            continue
        level = str(entry.get("buy_plan_level") or "").strip()
        if not level:
            continue
        result[level] = result.get(level, 0.0) + float(entry.get("quantity") or 0)
    return result


def _normalize_symbol(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        raise ValueError("symbol is required")
    return normalized


def _hkt_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Hong_Kong"))


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


def _first_report_value(report: object, *keys: str) -> Any:
    debug = _report_value(report, "debug")
    for key in keys:
        value = _report_value(report, key)
        if value is not None:
            return value
        if isinstance(debug, dict) and debug.get(key) is not None:
            return debug.get(key)
    return None


def _daily_change_pct(report: object) -> float | None:
    value = _first_report_value(
        report,
        "one_day_change_pct",
        "daily_change_pct",
        "day_change_pct",
        "price_change_pct_1d",
        "change_percent",
        "changes_percentage",
        "changesPercentage",
    )
    number = _number(value)
    if number is None:
        return None
    if -1 < number < 1 and number != 0:
        return number * 100
    return number


def _boolish(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "stale"}


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
