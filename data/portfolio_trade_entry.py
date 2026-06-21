from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from data.action_fusion import evaluate_action_fusion
from data.ai_stock_radar import build_cached_ai_stock_radar_report
from data.buy_setup_quality import setup_quality_note
from data.buy_zone_engine import build_buy_zone_context
from data.decision_log import TradeJournalStore
from data.macro_regime import load_macro_regime
from data.market_context import build_market_history
from data.planned_ladder_buy import evaluate_planned_ladder_buy
from data.portfolio import PortfolioPositionStore
from data.portfolio_roles import (
    ROLE_OBSERVATION,
    normalize_portfolio_role,
    portfolio_role_core_tactical_split,
    portfolio_role_label,
    portfolio_role_target_weight,
)
from data.portfolio_targets import build_action_fusion_portfolio_context
from data.portfolio_structure_health import build_portfolio_structure_check
from data.portfolio_trade_sync import apply_trade_to_portfolio, get_trade_portfolio_sync_status, preview_trade_values_portfolio_effect
from data.portfolio_view_model import build_portfolio_view_model
from data.prices import CACHE_PATH
from data.price_alerts import sync_buy_plan_price_alert
from data.pullback_acceptance import evaluate_pullback_acceptance, pullback_acceptance_snapshot_fields
from data.stock_plan import StockPlanStore, is_active_buy_plan
from data.starter_position import evaluate_starter_position
from data.structure_entry import build_structure_entry_advisor_for_symbol, structure_entry_snapshot_fields
from data.trade_gate import buy_gate_entry_fields, evaluate_buy_gate
from data.trade_intent import TradeIntentStore, normalize_trade_intent_payload
from data.volume_price_acceptance import evaluate_volume_price_acceptance, volume_price_acceptance_snapshot_fields


VALID_POSITION_TIERS = {"A", "B", "C"}
VALID_ENTRY_MODES = {"normal_buy", "planned_ladder_buy", "starter_position"}
TRADE_ENTRY_FIELD_LABELS = {
    "price": "成交价格",
    "quantity": "数量",
}


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
    decision_mood = str(values.get("decision_mood") or values.get("decisionMood") or "NEUTRAL").strip()
    portfolio_role = normalize_portfolio_role(values.get("role") or values.get("portfolio_role") or values.get("tradeRole")) or ROLE_OBSERVATION
    buy_reason = str(values.get("buy_reason") or values.get("notes") or "").strip()
    missing_buy_reason = not bool(buy_reason)
    target_sell_price = values.get("target_sell_price") or values.get("targetSellPrice")
    entry_mode = _clean_entry_mode(values.get("entry_mode") or values.get("entryMode"))
    starter_thesis = str(values.get("starter_thesis") or values.get("thesis") or buy_reason).strip()
    starter_add_plan = str(values.get("starter_add_plan") or values.get("add_plan") or values.get("follow_up_add_plan") or "").strip()
    starter_invalidation = str(
        values.get("starter_invalidation_condition") or values.get("invalidation_condition") or values.get("starterInvalidationCondition") or ""
    ).strip()
    observation_only = bool(values.get("radar_observation_only") or values.get("radarObservationOnly"))
    user_confirmed_advisory = bool(values.get("userConfirmedAdvisory") or values.get("user_confirmed_advisory"))
    user_confirmed_daily_trade_advisory = bool(
        values.get("userConfirmedDailyTradeAdvisory") or values.get("user_confirmed_daily_trade_advisory")
    )
    pre_trade_intent = normalize_trade_intent_payload(values.get("pre_trade_intent") or values.get("preTradeIntent"), side="buy")
    _require_positive_number(quantity, "quantity")
    _require_positive_number(price, "price")
    if missing_buy_reason:
        buy_reason = "买入前记录已保存。" if pre_trade_intent else "未保存买入前记录；系统已记录为风险提示。"
    action_type = _portfolio_trade_action(ticker, path, values.get("action_type"))
    submitted_at = _hkt_now()
    portfolio_preview = preview_trade_values_portfolio_effect(
        ticker,
        {"action_type": action_type, "quantity": quantity, "price": price},
        path=path,
    )
    report = radar_report or build_cached_ai_stock_radar_report(ticker)
    structure_advisor = build_structure_entry_advisor_for_symbol(ticker, path=path, now=submitted_at)
    pullback_acceptance = evaluate_pullback_acceptance(
        ticker=ticker,
        technicals=_report_dict(report),
        checked_at=submitted_at,
    )
    volume_price_acceptance = evaluate_volume_price_acceptance(
        ticker=ticker,
        daily_bars=_safe_market_history(ticker, path=path, now=submitted_at),
        technicals=_report_dict(report),
        checked_at=submitted_at,
    )
    report_data = _report_dict(report)
    buy_zone_context = _safe_buy_zone_context(report_data, volume_price_acceptance)
    report_for_gate = {**report_data, "buy_zone_context": buy_zone_context} if buy_zone_context else report
    action_fusion = _safe_action_fusion(
        ticker=ticker,
        report=report_for_gate,
        volume_price_acceptance=volume_price_acceptance,
        path=path,
    )
    plan = StockPlanStore(path).get_plan(ticker)
    prior_level_quantities = _planned_ladder_prior_quantities(ticker, path)
    plan_gate = evaluate_planned_ladder_buy(
        ticker=ticker,
        plan=plan if entry_mode == "planned_ladder_buy" else None,
        radar_report=report_for_gate,
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
        radar_report=report_for_gate,
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
        report_for_gate,
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
    if missing_buy_reason and not pre_trade_intent:
        advisory_notes.append("买入前记录缺失；系统只做风险提示，不阻止你继续入账。")
    advisory_notes.extend(_buy_zone_context_advisory_notes(buy_zone_context))
    advisory_notes.extend(_action_fusion_advisory_notes(action_fusion))
    if entry_mode == "planned_ladder_buy":
        advisory_notes.extend(plan_gate.plan_notes)
        advisory_notes.extend(plan_gate.plan_block_reasons)
    if entry_mode == "starter_position":
        advisory_notes.extend(starter_gate.starter_notes)
        advisory_notes.extend(starter_gate.starter_block_reasons)
    gate_fields["radarAdvisoryWarnings"] = _dedupe_text(advisory_notes)
    gate_fields["radarAdvisoryOnly"] = bool(gate_fields["radarAdvisoryWarnings"])
    gate_fields["advisoryReasons"] = list(gate_fields["radarAdvisoryWarnings"])
    gate_fields["advisoryLevel"] = _entry_advisory_level(
        gate_fields.get("warningLevel"),
        gate_fields["radarAdvisoryWarnings"],
    )
    gate_fields["advisoryText"] = _entry_advisory_text(
        gate_fields["advisoryLevel"],
        gate_fields["radarAdvisoryWarnings"],
    )
    gate_fields["userConfirmedAdvisory"] = user_confirmed_advisory
    gate_fields["validationPassed"] = True
    gate_fields["canSubmit"] = True
    gate_fields["radarBlocked"] = False
    gate_fields["gateHardBlocked"] = False
    gate_fields["moodGateBlocked"] = False
    gate_fields["positionGateBlocked"] = False
    gate_fields["radarBlockReasons"] = []
    gate_fields["warningLevel"] = _merged_warning_level(
        gate_fields.get("warningLevel"),
        gate_fields["radarAdvisoryWarnings"],
    )
    advisory_context_fields = _buy_advisory_context_fields(
        path=path,
        checked_at=submitted_at.isoformat(),
        warnings=gate_fields["radarAdvisoryWarnings"],
    )
    action_fusion_fields = _action_fusion_entry_fields(
        action_fusion,
        warnings=gate_fields["radarAdvisoryWarnings"],
        override_reason=str(values.get("override_reason") or values.get("overrideReason") or "").strip(),
    )
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
        "tradeRole": portfolio_role,
        "roleLabel": portfolio_role_label(portfolio_role),
        "roleTargetWeight": portfolio_role_target_weight(portfolio_role),
        "coreTacticalSplit": portfolio_role_core_tactical_split(portfolio_role),
        "roleReason": values.get("role_reason") or values.get("roleReason") or "",
        "corePositionMinPct": core_pct,
        "tradingPositionMaxPct": trading_pct,
        "classificationNote": values.get("classification_note") or values.get("classificationNote") or "",
        "createdAt": submitted_at.isoformat(),
        "userConfirmedDailyTradeAdvisory": user_confirmed_daily_trade_advisory,
        "radarDataStatus": _report_value(report_for_gate, "data_status"),
        "radarIsStale": bool(_report_value(report_for_gate, "is_stale")),
        **gate_fields,
        **plan_fields,
        **starter_fields,
        **structure_entry_snapshot_fields(structure_advisor, checked_at=submitted_at.isoformat()),
        **pullback_acceptance_snapshot_fields(pullback_acceptance, checked_at=submitted_at.isoformat()),
        **volume_price_acceptance_snapshot_fields(volume_price_acceptance, checked_at=submitted_at.isoformat()),
        **advisory_context_fields,
        **action_fusion_fields,
        "gateCheckedAt": submitted_at.isoformat(),
    }
    store = TradeJournalStore(path)
    saved = store.save_entry(ticker, entry_values)
    sync_result = apply_trade_to_portfolio(int(saved.get("id") or 0), path=path)
    if str(sync_result.get("status") or "") != "success":
        store.delete_entry(int(saved.get("id") or 0))
        raise ValueError(str(sync_result.get("error") or "成交入账失败，交易日志未保存。"))
    if pre_trade_intent:
        TradeIntentStore(path).save_intent(
            int(saved.get("id") or 0),
            ticker,
            action_type,
            pre_trade_intent,
            source="portfolio_trade_entry",
            snapshots={
                "setup_score": (buy_zone_context or {}).get("setup_score") if isinstance(buy_zone_context, dict) else None,
                "technical_structure_score": (buy_zone_context or {}).get("technical_structure_score")
                if isinstance(buy_zone_context, dict)
                else None,
                "volume_acceptance_score": (buy_zone_context or {}).get("volume_acceptance_score")
                if isinstance(buy_zone_context, dict)
                else None,
                "risk_reward_score": (buy_zone_context or {}).get("risk_reward_score") if isinstance(buy_zone_context, dict) else None,
                "buy_zone_context": buy_zone_context if isinstance(buy_zone_context, dict) else {},
                "buy_zone_display": report_data.get("buy_zone_display") or report_data.get("buyZoneDisplay") or {},
                "position_quantity": portfolio_preview.get("afterQuantity"),
                "position_weight": portfolio_preview.get("afterPositionPct"),
            },
        )
    completed_plan = _complete_buy_plan_after_success(
        ticker=ticker,
        plan=plan,
        entry_mode=entry_mode,
        plan_gate=plan_gate,
        path=path,
    )
    gate_snapshot = gate.to_dict()
    gate_snapshot["warning_level"] = gate_fields.get("warningLevel")
    gate_snapshot["advisory_level"] = gate_fields.get("advisoryLevel")
    gate_snapshot["advisory_text"] = gate_fields.get("advisoryText")
    gate_snapshot["advisory_warnings"] = gate_fields["radarAdvisoryWarnings"]
    gate_snapshot["user_confirmed_advisory"] = user_confirmed_advisory
    gate_snapshot["validation_passed"] = True
    gate_snapshot["can_submit"] = True
    gate_snapshot["radar_advisory_only"] = bool(gate_fields["radarAdvisoryWarnings"])
    gate_snapshot["is_blocked"] = False
    gate_snapshot["can_continue"] = True
    gate_snapshot["can_sync_to_portfolio"] = True
    return {
        "entry": saved,
        "gate": gate_snapshot,
        "planGate": plan_gate.to_dict(),
        "starterGate": starter_gate.to_dict(),
        "structureEntry": structure_advisor.to_dict(),
        "pullbackAcceptance": pullback_acceptance.to_dict(),
        "volumePriceAcceptance": volume_price_acceptance.to_dict(),
        "actionFusion": action_fusion.to_dict() if action_fusion is not None else {},
        "marketStatus": _buy_market_status(report_for_gate, gate),
        "completedPlan": completed_plan,
        "sync": sync_result,
        "actionType": action_type,
        "synced": bool(sync_result and sync_result.get("status") == "success"),
    }


def _entry_advisory_level(warning_level: object, warnings: list[str]) -> str:
    if not warnings:
        return "NONE"
    level = str(warning_level or "").strip().upper()
    if level in {"DANGER", "HIGH_RISK", "CRITICAL"}:
        return "HIGH_RISK"
    if level in {"WARNING", "WARN"}:
        return "WARNING"
    return "INFO"


def _entry_advisory_text(level: str, warnings: list[str]) -> str:
    if not warnings:
        return ""
    normalized = str(level or "").strip().upper()
    if normalized in {"HIGH_RISK", "CRITICAL"}:
        return "高风险买入提醒：系统不建议，但不会阻止；继续操作将记录为已确认风险。"
    if normalized == "WARNING":
        return "买入前风险提示：系统建议复核，但不会阻止你继续。"
    return "买入提醒：请确认本次操作符合你的计划。"


def _buy_entry_block_reason(*, gate: Any, plan_gate: Any, starter_gate: Any, entry_mode: str) -> str:
    if entry_mode == "planned_ladder_buy" and not bool(plan_gate.can_sync_to_portfolio):
        reasons = [str(item) for item in getattr(plan_gate, "plan_block_reasons", []) if str(item).strip()]
        return "；".join(reasons) or "计划买入条件未触发，请按普通买入确认。"
    if entry_mode == "starter_position" and not bool(starter_gate.can_sync_to_portfolio):
        reasons = [str(item) for item in getattr(starter_gate, "starter_block_reasons", []) if str(item).strip()]
        return "；".join(reasons) or "底仓建仓条件建议复核。"
    reasons = [str(item) for item in [*getattr(gate, "reasons", []), *getattr(gate, "required_actions", [])] if str(item).strip()]
    return "；".join(reasons) or "买入校验未通过。"


def _can_sync_buy_entry(
    *,
    entry_mode: str,
    gate: Any,
    plan_gate: Any,
    starter_gate: Any,
    observation_only: bool,
) -> bool:
    return True


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


def _buy_advisory_context_fields(*, path: Path, checked_at: str, warnings: list[str]) -> dict[str, Any]:
    macro_regime_text = None
    portfolio_structure_status = None
    macro_snapshot = None
    try:
        macro_snapshot = load_macro_regime(path)
        macro_regime_text = str(getattr(macro_snapshot, "regime", "") or "") or None
    except Exception:
        macro_snapshot = None
    try:
        portfolio_view = build_portfolio_view_model(path)
        structure_check = build_portfolio_structure_check(portfolio_view, macro_regime=macro_snapshot)
        portfolio_structure_status = str(getattr(structure_check, "status", "") or "") or None
    except Exception:
        portfolio_structure_status = None
    return {
        "buyAdvisoryWarnings": _dedupe_text(warnings),
        "buyAdvisoryAcknowledged": bool(warnings),
        "advisoryCheckedAt": checked_at,
        "macroRegime": macro_regime_text,
        "portfolioStructureStatus": portfolio_structure_status,
    }


def _safe_action_fusion(*, ticker: str, report: object, volume_price_acceptance: Any, path: Path):
    try:
        report_data = _report_dict(report)
        if not _has_action_fusion_levels(report_data):
            return None
        volume_snapshot = volume_price_acceptance.to_dict() if hasattr(volume_price_acceptance, "to_dict") else {}
        return evaluate_action_fusion(
            ticker=ticker,
            context={
                **report_data,
                "volume_price_status": volume_snapshot.get("volume_price_status"),
                "volume_price_score": volume_snapshot.get("volume_price_score"),
                "volume_ratio": volume_snapshot.get("volume_ratio"),
                "volume_regime_cn": volume_snapshot.get("volume_regime_cn"),
                "volume_price_reason_cn": volume_snapshot.get("acceptance_reason_cn") or volume_snapshot.get("reason_cn"),
            },
            portfolio_context=build_action_fusion_portfolio_context(ticker, path=path),
        )
    except Exception:
        return None


def _safe_buy_zone_context(report_data: dict[str, Any], volume_price_acceptance: Any) -> dict[str, Any]:
    if not _has_buy_zone_context_inputs(report_data):
        return {}
    try:
        volume_snapshot = volume_price_acceptance.to_dict() if hasattr(volume_price_acceptance, "to_dict") else {}
        return build_buy_zone_context(report_data, volume_snapshot=volume_snapshot).to_dict()
    except Exception:
        return {}


def _has_buy_zone_context_inputs(data: dict[str, Any]) -> bool:
    if _present(data.get("buy_zone_context")) or _present(data.get("buyZoneContext")):
        return True
    if _present(data.get("buy_zone_display")) or _present(data.get("buyZoneDisplay")):
        return True
    has_levels = any(
        _present(data.get(key))
        for key in (
            "current_price",
            "currentPrice",
            "price",
            "deep_support_zone_low",
            "support_watch_zone_low",
            "effective_technical_entry_zone_low",
            "technical_pullback_zone_low",
            "near_term_repair_zone_low",
            "confirmation_price",
            "invalidation_price",
            "chase_above_price",
            "daily_ohlcv",
            "price_history",
            "history",
            "ohlcv",
            "ma20",
            "ma50",
            "ma200",
            "ema20",
            "ema50",
            "ema200",
            "atr",
            "atr_14",
            "atr14",
            "support_cluster",
            "support_clusters",
            "support_zone",
            "support_zone_low",
            "resistance",
            "resistance_zone",
            "resistance_zone_high",
            "technical_levels",
        )
    )
    return has_levels


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    empty = getattr(value, "empty", None)
    if empty is not None:
        try:
            return not bool(empty)
        except Exception:
            return True
    return True


def _buy_zone_context_advisory_notes(context: dict[str, Any]) -> list[str]:
    if not context:
        return []
    action = str(context.get("current_action") or "").strip().upper()
    action_text = str(context.get("action_text") or "").strip()
    zone = str(context.get("primary_zone_text") or "").strip()
    reason = str(context.get("zone_selection_reason") or "").strip()
    notes: list[str] = []
    if action in {"BLOCK_CHASE", "RISK_REVIEW", "DATA_INSUFFICIENT", "WAIT_CONFIRMATION", "WAIT_PULLBACK"}:
        notes.append(f"统一买区：{zone or action_text}，{action_text}；{reason}")
    core_reason = str(context.get("core_position_reason") or "").strip()
    if core_reason:
        notes.append(core_reason)
    return _dedupe_text(notes)


def _has_action_fusion_levels(data: dict[str, Any]) -> bool:
    keys = (
        "observation_low",
        "observationLow",
        "near_term_repair_zone_low",
        "technical_pullback_zone_low",
        "effective_technical_entry_zone_low",
    )
    return any(data.get(key) not in (None, "") for key in keys)


def _action_fusion_advisory_notes(action_fusion: Any) -> list[str]:
    if action_fusion is None:
        return []
    notes: list[str] = []
    notes.extend(str(item) for item in getattr(action_fusion, "advisory_warnings_cn", []) if str(item).strip())
    notes.extend(str(item) for item in getattr(action_fusion, "risk_bullets_cn", []) if str(item).strip())
    left_warning = str(getattr(action_fusion, "left_side_warning_cn", "") or "").strip()
    if left_warning:
        notes.append(left_warning)
    action_code = str(getattr(action_fusion, "action_code", "") or "").strip().upper()
    action_cn = str(getattr(action_fusion, "action_cn", "") or "").strip()
    if action_code in {"BLOCK_CHASE", "EVENT_REVIEW", "DATA_INSUFFICIENT", "WAIT_CONFIRMATION", "HOLD_NO_ADD", "BREAKDOWN_REVIEW"} and action_cn:
        notes.append(f"系统提示：{action_cn}；可手动继续，系统会记录为人工复核记录。")
    return _dedupe_text(notes)


def _action_fusion_entry_fields(action_fusion: Any, *, warnings: list[str], override_reason: str = "") -> dict[str, Any]:
    warning_text = "；".join(_dedupe_text([str(item) for item in warnings if str(item).strip()]))
    if action_fusion is None:
        return {
            "advisoryAction": "",
            "riskWarningCn": warning_text,
            "userOverride": bool(warning_text),
            "overrideReason": override_reason,
            "actionFusionAction": "",
            "leftSideActionCn": "",
            "positionStatus": "",
        }
    advisory_action = str(getattr(action_fusion, "left_side_action_cn", "") or getattr(action_fusion, "action_cn", "") or "").strip()
    action_code = str(getattr(action_fusion, "action_code", "") or "").strip()
    position_status = str(getattr(action_fusion, "position_status_cn", "") or "").strip()
    return {
        "advisoryAction": advisory_action,
        "riskWarningCn": warning_text,
        "userOverride": bool(warning_text),
        "overrideReason": override_reason,
        "actionFusionAction": action_code,
        "leftSideActionCn": str(getattr(action_fusion, "left_side_action_cn", "") or "").strip(),
        "positionStatus": position_status,
    }


def _merged_warning_level(current: object, warnings: list[str]) -> str:
    level = str(current or "info").strip().lower()
    if level == "danger":
        return "danger"
    if not warnings:
        return level if level in {"info", "warning"} else "info"
    text = " ".join(str(item) for item in warnings).upper()
    danger_tokens = (
        "BLOCK_CHASE",
        "EVENT_REVIEW",
        "DATA_INSUFFICIENT",
        "HOLD_NO_ADD",
        "POSITION_LIMITED",
        "FAILED",
        "追高",
        "冲击",
        "数据不足",
        "仓位",
        "不建议",
    )
    if any(token in text for token in danger_tokens):
        return "danger"
    return "warning"


def _complete_buy_plan_after_success(
    *,
    ticker: str,
    plan: dict[str, Any],
    entry_mode: str,
    plan_gate: Any,
    path: Path,
) -> dict[str, Any]:
    if not is_active_buy_plan(plan):
        return {}
    should_complete = False
    levels = plan.get("buy_plan_tranches") if isinstance(plan, dict) else []
    if entry_mode == "planned_ladder_buy":
        if bool(getattr(plan_gate, "planned_ladder_buy", False)):
            should_complete = _planned_ladder_plan_filled(plan, _planned_ladder_prior_quantities(ticker, path))
    else:
        should_complete = not levels or str(plan.get("plan_type") or "").strip() in {"starter_position", "event_trade", "watch_only"}
    if not should_complete:
        return {}
    closed = StockPlanStore(path).close_plan(ticker, "completed", note="计划已执行；提醒已停用。")
    sync_buy_plan_price_alert(path, symbol=ticker, plan=closed, is_active=False)
    return closed


def _planned_ladder_plan_filled(plan: dict[str, Any], prior_level_quantities: dict[str, float]) -> bool:
    levels = plan.get("buy_plan_tranches") if isinstance(plan, dict) else []
    if not isinstance(levels, list) or not levels:
        return False
    for index, item in enumerate(levels):
        if not isinstance(item, dict):
            return False
        label = str(item.get("label") or f"第 {index + 1} 档").strip()
        planned_quantity = _number(item.get("shares") or item.get("planned_quantity"))
        if planned_quantity is None or planned_quantity <= 0:
            return False
        if float(prior_level_quantities.get(label) or 0.0) + 1e-9 < planned_quantity:
            return False
    return True


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
        raise ValueError("持仓等级必须选择 A / B / C")
    return tier


def _clean_entry_mode(value: object) -> str:
    mode = str(value or "normal_buy").strip().lower()
    return mode if mode in VALID_ENTRY_MODES else "normal_buy"


def _require_positive_number(value: object, field: str) -> float:
    number = _number(value)
    if number is None or number <= 0:
        label = TRADE_ENTRY_FIELD_LABELS.get(str(field or "").strip(), "该字段")
        raise ValueError(f"{label}必须大于 0")
    return number


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
    buy_zone_context = _report_value(report, "buy_zone_context") or getattr(gate, "buy_zone_context", {}) or {}
    buy_zone_action = str(buy_zone_context.get("current_action") or getattr(gate, "buy_zone_action", "") or "").strip().upper()
    setup_score = _number(buy_zone_context.get("setup_score") or getattr(gate, "setup_score", None))
    primary_zone_text = str(buy_zone_context.get("primary_zone_text") or getattr(gate, "primary_zone_text", "") or "").strip()
    daily_change_pct = _daily_change_pct(report)
    price_position = str(_first_report_value(report, "price_position", "zone_status") or "").strip().upper()
    decision = str(_report_value(report, "decision") or getattr(gate, "decision", "") or "").strip().upper()
    valuation_score = _number(_report_value(report, "valuation_score"))
    final_score = _number(_report_value(report, "final_score"))
    allowed_add_pct = _number(getattr(gate, "allowed_add_pct", None))
    is_stale = _boolish(_report_value(report, "is_stale"))
    data_status = str(_report_value(report, "data_status") or "").strip().lower()

    if daily_change_pct is not None and daily_change_pct <= -8:
        technical_status = "财报后大跌 / 高波动"
    elif buy_zone_action == "DATA_INSUFFICIENT":
        technical_status = "技术承接数据不足"
    elif buy_zone_action == "RISK_REVIEW":
        technical_status = "跌破失效线 / 风控复核"
    elif buy_zone_action == "BLOCK_CHASE":
        technical_status = "技术偏热 / 追高风险"
    elif buy_zone_action == "ALLOW_SMALL_BUY":
        technical_status = "回踩买区 / 可小仓观察"
    elif buy_zone_action in {"WAIT_CONFIRMATION", "WAIT_PULLBACK"}:
        technical_status = "等待确认 / 等待回踩"
    elif is_stale or data_status in {"missing", "data_missing", "stale"}:
        technical_status = "买区数据缺失 / 过期，需人工判断"
    elif price_position == "IN_CHASE_ZONE" or decision == "BLOCK_CHASE":
        technical_status = "技术偏热 / 追高风险"
    elif price_position == "BELOW_BUY_ZONE":
        technical_status = "低于估值参考，等待结构确认"
    else:
        technical_status = "正常波动"

    if valuation_score is None:
        valuation_status = "估值缺失"
    elif valuation_score < 40:
        valuation_status = "估值仍偏高"
    elif price_position == "BELOW_BUY_ZONE":
        valuation_status = "价格低于估值参考，需确认基本面"
    else:
        valuation_status = "估值需复核"

    if buy_zone_action == "DATA_INSUFFICIENT":
        discipline_status = "技术承接数据不足，不给明确买入区；可手动继续"
    elif buy_zone_action == "RISK_REVIEW":
        discipline_status = "统一买区进入风控复核，暂停新增买入建议"
    elif buy_zone_action == "BLOCK_CHASE":
        discipline_status = "统一买区提示追高风险，不建议追买"
    elif buy_zone_action == "ALLOW_SMALL_BUY":
        discipline_status = "统一买区小仓观察参考"
    elif buy_zone_action in {"WAIT_CONFIRMATION", "WAIT_PULLBACK"}:
        discipline_status = "统一买区建议等待确认或回踩"
    elif is_stale or data_status in {"missing", "data_missing", "stale"}:
        discipline_status = "买区参考不可用，需人工判断；可手动继续"
    elif allowed_add_pct is not None and allowed_add_pct <= 0:
        discipline_status = "系统参考新增仓位为 0%，仅作风险提示"
    elif price_position == "IN_BUY_ZONE":
        discipline_status = "进入主击球区"
    elif price_position == "BELOW_BUY_ZONE":
        discipline_status = "低于估值参考需复核"
    elif price_position in {"ABOVE_BUY_ZONE", "IN_CHASE_ZONE"}:
        discipline_status = "未进入参考买区"
    else:
        discipline_status = "纪律区间需复核"

    notes: list[str] = []
    if daily_change_pct is not None and daily_change_pct <= -8:
        notes.append("不是系统错误；大跌不等于进入买区。")
    if valuation_score is not None and valuation_score < 40:
        notes.append("估值分低，不能因为回撤自动放行。")
    if setup_score is not None:
        volume_score = _number(buy_zone_context.get("volume_acceptance_score"))
        notes.append(setup_quality_note(setup_score, volume_acceptance_score=volume_score))

    return {
        "technical_status": technical_status,
        "valuation_status": valuation_status,
        "discipline_status": discipline_status,
        "daily_change_pct": daily_change_pct,
        "price_position": price_position,
        "valuation_score": valuation_score,
        "final_score": final_score,
        "allowed_add_pct": allowed_add_pct,
        "setup_score": setup_score,
        "buy_zone_action": buy_zone_action,
        "primary_zone_text": primary_zone_text,
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
        raise ValueError("缺少股票代码")
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


def _report_dict(report: object) -> dict[str, Any]:
    if isinstance(report, dict):
        return dict(report)
    if hasattr(report, "to_dict"):
        try:
            data = report.to_dict()
        except Exception:
            data = None
        if isinstance(data, dict):
            return dict(data)
    return {}


def _safe_market_history(symbol: str, *, path: Path, now: datetime):
    try:
        return build_market_history(symbol, path=path, now=now)
    except Exception:
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
