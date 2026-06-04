from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from data.ai_stock_radar import build_cached_ai_stock_radar_report
from data.decision_log import TradeJournalStore
from data.portfolio import PortfolioPositionStore
from data.portfolio_trade_sync import apply_trade_to_portfolio, preview_trade_values_portfolio_effect
from data.prices import CACHE_PATH
from data.trade_gate import buy_gate_entry_fields, evaluate_buy_gate


VALID_POSITION_TIERS = {"A", "B", "C"}


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
    observation_only = bool(values.get("radar_observation_only") or values.get("radarObservationOnly"))
    action_type = _portfolio_trade_action(ticker, path, values.get("action_type"))
    submitted_at = _hkt_now()
    portfolio_preview = preview_trade_values_portfolio_effect(
        ticker,
        {"action_type": action_type, "quantity": quantity, "price": price},
        path=path,
    )
    report = radar_report or build_cached_ai_stock_radar_report(ticker)
    gate = evaluate_buy_gate(
        report,
        action_type=action_type,
        position_bucket=_position_bucket_for_tier(tier),
        planned_after_position_pct=portfolio_preview.get("afterPositionPct"),
        decision_mood=decision_mood,
        observation_only=observation_only,
        buy_reason=buy_reason,
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
        "positionClass": tier,
        "corePositionMinPct": core_pct,
        "tradingPositionMaxPct": trading_pct,
        "classificationNote": values.get("classification_note") or values.get("classificationNote") or "",
        "createdAt": submitted_at.isoformat(),
        **buy_gate_entry_fields(gate, action_type=action_type),
        "gateCheckedAt": submitted_at.isoformat(),
    }
    saved = TradeJournalStore(path).save_entry(ticker, entry_values)
    sync_result = None
    if gate.can_sync_to_portfolio:
        sync_result = apply_trade_to_portfolio(int(saved.get("id") or 0), path=path)
    return {
        "entry": saved,
        "gate": gate.to_dict(),
        "sync": sync_result,
        "actionType": action_type,
        "synced": bool(sync_result and sync_result.get("status") == "success"),
    }


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


def _position_bucket_for_tier(tier: str) -> str:
    return "core" if tier == "A" else "trade"


def _tier_ratio_defaults(tier: str) -> tuple[float, float]:
    if tier == "A":
        return 0.60, 0.40
    return 0.0, 1.0


def _normalize_symbol(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        raise ValueError("symbol is required")
    return normalized


def _hkt_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Hong_Kong"))
