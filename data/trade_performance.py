from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from data.prices import CACHE_PATH
from data.trade_safety_gate import has_concrete_reentry_plan


REAL_ACTIONS = {"buy", "add", "sell", "trim"}
BUY_ACTIONS = {"buy", "add"}
SELL_ACTIONS = {"sell", "trim"}
EMOTIONAL_SELL_REASONS = {"macro", "macro_fear", "anxiety", "panic_sell"}
EMOTIONAL_SELL_MOODS = {"macro_fear", "anxiety", "panic_sell", "regret_chase"}
HKT = timezone(timedelta(hours=8))
A_CLASS_SHORT_HOLDING_DAYS = 14


@dataclass
class TradeLot:
    entry_id: int | None
    ticker: str
    quantity: float
    remaining_quantity: float
    price: float
    trade_date: date
    position_tier: str
    mood: str
    target_sell_price: float | None
    buy_reason: str
    source_entry: dict[str, Any]


def summarize_trade_performance(
    *,
    path: Path = CACHE_PATH,
    entries: Iterable[dict[str, Any]] | None = None,
    opening_lots: Iterable[dict[str, Any]] | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_entries = list(entries) if entries is not None else load_synced_trade_entries(path)
    matched = match_realized_trades(source_entries, opening_lots=opening_lots)
    realized = _apply_filters(matched["realized_trades"], filters or {})
    stats = calculate_realized_pnl(realized)
    groups = {
        "ticker": _group_summary(realized, "ticker"),
        "position_tier": _group_summary(realized, "position_tier"),
        "buy_mood": _group_summary(realized, "buy_mood"),
        "sell_mood": _group_summary(realized, "sell_mood"),
        "sell_reason": _group_summary(realized, "sell_reason_type"),
        "holding_bucket": _group_summary(realized, "holding_days_bucket"),
    }
    return {
        "summary": stats,
        "realized_trades": realized,
        "matches": matched["matches"],
        "open_lots": matched["open_lots"],
        "unmatched_sells": matched["unmatched_sells"],
        "groups": groups,
        "warnings": matched["warnings"],
    }


def load_synced_trade_entries(path: Path = CACHE_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as conn:
        if not _table_exists(conn, "trade_journal_entries"):
            return []
        if not _table_exists(conn, "trade_portfolio_sync_logs"):
            return []
        cursor = conn.execute(
            """
            SELECT entry.*
            FROM trade_journal_entries AS entry
            INNER JOIN trade_portfolio_sync_logs AS log
                ON log.entry_id = entry.id
               AND log.status = 'synced'
            WHERE entry.action_type IN ('buy', 'add', 'sell', 'trim')
            ORDER BY entry.trade_date ASC, entry.created_at ASC, entry.id ASC
            """
        )
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description] if cursor.description else []
    return [_row_to_dict(columns, row) for row in rows]


def build_manual_cost_basis_lot(
    *,
    ticker: str,
    quantity: float,
    avg_cost: float,
    buy_date: str,
    position_tier: str = "",
    note: str = "",
) -> dict[str, Any]:
    return {
        "ticker": _symbol(ticker),
        "quantity": quantity,
        "avg_cost": avg_cost,
        "buy_date": buy_date,
        "position_tier": position_tier,
        "note": note,
        "cost_basis_source": "manual_cost_basis",
    }


def build_trade_lots(entries: Iterable[dict[str, Any]], opening_lots: Iterable[dict[str, Any]] | None = None) -> list[TradeLot]:
    lots: list[TradeLot] = []
    lots.extend(_manual_opening_lots(opening_lots or []))
    for entry in _chronological_entries(entries):
        if str(entry.get("action_type") or "").lower() not in BUY_ACTIONS:
            continue
        if not _is_real_trade(entry):
            continue
        quantity = _number(entry.get("quantity"))
        price = _number(entry.get("price"))
        trade_date = _entry_date(entry)
        if quantity is None or quantity <= 0 or price is None or price <= 0 or trade_date is None:
            continue
        lots.append(_entry_to_lot(entry, _symbol(entry.get("symbol")), quantity, price, trade_date))
    return sorted(lots, key=lambda lot: (lot.ticker, lot.trade_date))


def _entry_to_lot(entry: dict[str, Any], symbol: str, quantity: float, price: float, trade_date: date) -> TradeLot:
    return TradeLot(
        entry_id=_optional_int(entry.get("id")),
        ticker=symbol,
        quantity=quantity,
        remaining_quantity=quantity,
        price=price,
        trade_date=trade_date,
        position_tier=_position_tier(entry),
        mood=str(entry.get("decision_mood") or ""),
        target_sell_price=_number(entry.get("target_sell_price")),
        buy_reason=str(entry.get("notes") or entry.get("buy_reason") or ""),
        source_entry={**entry, "cost_basis_source": entry.get("cost_basis_source") or "fifo"},
    )


def _manual_opening_lots(opening_lots: Iterable[dict[str, Any]]) -> list[TradeLot]:
    lots: list[TradeLot] = []
    for item in opening_lots:
        quantity = _number(item.get("quantity"))
        price = _number(item.get("avg_cost") or item.get("price"))
        buy_date = _coerce_date(item.get("buy_date") or item.get("trade_date"))
        if quantity is None or quantity <= 0 or price is None or price <= 0 or buy_date is None:
            continue
        tier = str(item.get("position_tier") or item.get("position_class") or "").strip().upper()
        lots.append(
            TradeLot(
                entry_id=None,
                ticker=_symbol(item.get("ticker") or item.get("symbol")),
                quantity=quantity,
                remaining_quantity=quantity,
                price=price,
                trade_date=buy_date,
                position_tier=tier if tier in {"A", "B", "C"} else "",
                mood="",
                target_sell_price=_number(item.get("target_sell_price")),
                buy_reason=str(item.get("note") or ""),
                source_entry={**item, "cost_basis_source": "manual_cost_basis"},
            )
        )
    return lots


def _append_lot(lots: list[TradeLot], lot: TradeLot) -> None:
    lots.append(lot)
    lots.sort(key=lambda item: item.trade_date)


def _snapshot_cost_basis_match(
    entry: dict[str, Any],
    symbol: str,
    quantity: float,
    sell_price: float,
    sell_date: date,
) -> dict[str, Any] | None:
    avg_cost = _number(entry.get("pre_trade_avg_cost"))
    if avg_cost is None or avg_cost <= 0:
        return None
    pnl = (sell_price - avg_cost) * quantity
    pnl_pct = (sell_price / avg_cost - 1) * 100 if avg_cost > 0 else None
    return {
        "sell_entry_id": _optional_int(entry.get("id")),
        "buy_entry_id": None,
        "ticker": symbol,
        "matched_quantity": quantity,
        "buy_date": "",
        "sell_date": sell_date.isoformat(),
        "buy_price": avg_cost,
        "sell_price": sell_price,
        "realized_pnl": pnl,
        "realized_pnl_pct": pnl_pct,
        "holding_days": None,
        "position_tier": _pre_trade_position_tier(entry) or _position_tier(entry),
        "buy_mood": "",
        "sell_mood": str(entry.get("decision_mood") or ""),
        "sell_reason_type": str(entry.get("sell_reason_type") or ""),
        "target_sell_price": _number(entry.get("pre_trade_target_sell_price")) or _number(entry.get("target_sell_price")),
        "buy_reason": "",
        "sell_notes": str(entry.get("notes") or ""),
        "cost_basis_source": "position_snapshot",
    }


def match_realized_trades(entries: Iterable[dict[str, Any]], opening_lots: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    lots_by_symbol: dict[str, list[TradeLot]] = defaultdict(list)
    matches: list[dict[str, Any]] = []
    realized_by_sell: dict[int | str, dict[str, Any]] = {}
    unmatched_sells: list[dict[str, Any]] = []
    warnings: list[str] = []
    for lot in _manual_opening_lots(opening_lots or []):
        _append_lot(lots_by_symbol[lot.ticker], lot)

    for entry in _chronological_entries(entries):
        action = str(entry.get("action_type") or "").lower()
        if action not in REAL_ACTIONS:
            continue
        if not _is_real_trade(entry):
            continue
        symbol = _symbol(entry.get("symbol"))
        quantity = _number(entry.get("quantity"))
        price = _number(entry.get("price"))
        trade_date = _entry_date(entry)
        if quantity is None or quantity <= 0 or price is None or price <= 0:
            warnings.append(f"{symbol} {action} 缺数量或价格，已跳过。")
            continue
        if trade_date is None:
            warnings.append(f"{symbol} {action} 缺交易日期，已跳过。")
            continue
        if action in BUY_ACTIONS:
            _append_lot(lots_by_symbol[symbol], _entry_to_lot(entry, symbol, quantity, price, trade_date))
            continue

        remaining_sell_qty = quantity
        sell_id = _optional_int(entry.get("id")) or f"{symbol}:{trade_date.isoformat()}:{len(realized_by_sell)}"
        while remaining_sell_qty > 1e-9 and lots_by_symbol[symbol]:
            lot = lots_by_symbol[symbol][0]
            matched_qty = min(remaining_sell_qty, lot.remaining_quantity)
            holding_days = calculate_holding_days(lot.trade_date, trade_date)
            pnl = (price - lot.price) * matched_qty
            pnl_pct = (price / lot.price - 1) * 100 if lot.price > 0 else None
            match = {
                "sell_entry_id": _optional_int(entry.get("id")),
                "buy_entry_id": lot.entry_id,
                "ticker": symbol,
                "matched_quantity": matched_qty,
                "buy_date": lot.trade_date.isoformat(),
                "sell_date": trade_date.isoformat(),
                "buy_price": lot.price,
                "sell_price": price,
                "realized_pnl": pnl,
                "realized_pnl_pct": pnl_pct,
                "holding_days": holding_days,
                "position_tier": lot.position_tier or _position_tier(entry),
                "buy_mood": lot.mood,
                "sell_mood": str(entry.get("decision_mood") or ""),
                "sell_reason_type": str(entry.get("sell_reason_type") or ""),
                "target_sell_price": lot.target_sell_price,
                "buy_reason": lot.buy_reason,
                "sell_notes": str(entry.get("notes") or ""),
                "cost_basis_source": str(lot.source_entry.get("cost_basis_source") or "fifo"),
            }
            matches.append(match)
            _accumulate_sell(realized_by_sell, sell_id, entry, match)
            lot.remaining_quantity -= matched_qty
            remaining_sell_qty -= matched_qty
            if lot.remaining_quantity <= 1e-9:
                lots_by_symbol[symbol].pop(0)

        if remaining_sell_qty > 1e-9:
            snapshot_match = _snapshot_cost_basis_match(entry, symbol, remaining_sell_qty, price, trade_date)
            if snapshot_match:
                matches.append(snapshot_match)
                _accumulate_sell(realized_by_sell, sell_id, entry, snapshot_match)
                remaining_sell_qty = 0.0
                continue
            missing = {
                "sell_entry_id": _optional_int(entry.get("id")),
                "ticker": symbol,
                "sell_date": trade_date.isoformat(),
                "sell_price": price,
                "unmatched_quantity": remaining_sell_qty,
                "cost_basis_missing": True,
                "reason": "缺买入成本",
            }
            unmatched_sells.append(missing)
            _accumulate_unmatched(realized_by_sell, sell_id, entry, missing)

    realized = [_finalize_sell_trade(item) for item in realized_by_sell.values()]
    open_lots = [
        _lot_to_dict(lot)
        for lots in lots_by_symbol.values()
        for lot in lots
        if lot.remaining_quantity > 1e-9
    ]
    return {
        "matches": matches,
        "realized_trades": realized,
        "open_lots": open_lots,
        "unmatched_sells": unmatched_sells,
        "warnings": warnings,
    }


def calculate_realized_pnl(realized_trades: Iterable[dict[str, Any]]) -> dict[str, Any]:
    all_rows = list(realized_trades)
    rows = [row for row in all_rows if _number(row.get("realized_pnl")) is not None and _number(row.get("matched_quantity"))]
    missing_rows = [row for row in all_rows if row.get("cost_basis_missing")]
    missing_quantity = sum(float(_number(row.get("unmatched_quantity")) or _number(row.get("sell_quantity")) or 0) for row in missing_rows)
    missing_amount = sum(
        float(_number(row.get("sell_price")) or 0)
        * float(_number(row.get("unmatched_quantity")) or _number(row.get("sell_quantity")) or 0)
        for row in missing_rows
    )
    missing_stats = {
        "missing_cost_count": len(missing_rows),
        "missing_cost_quantity": round(missing_quantity, 8),
        "missing_cost_amount": round(missing_amount, 2),
    }
    if not rows:
        return {
            "total_realized_pnl": 0.0,
            "total_cost_basis": 0.0,
            "realized_pnl_pct": None,
            "completed_sell_count": 0,
            "win_rate": None,
            "average_winner": None,
            "average_loser": None,
            "max_winner": None,
            "max_loser": None,
            "average_holding_days": None,
            "median_holding_days": None,
            "discipline_issue_count": 0,
            **missing_stats,
        }
    total_pnl = sum(float(row["realized_pnl"]) for row in rows)
    total_cost = sum(float(row.get("cost_basis") or 0) for row in rows)
    winners = [float(row["realized_pnl"]) for row in rows if float(row["realized_pnl"]) > 0]
    losers = [float(row["realized_pnl"]) for row in rows if float(row["realized_pnl"]) < 0]
    holding_days = [_number(row.get("holding_days")) for row in rows if _number(row.get("holding_days")) is not None]
    issue_count = sum(1 for row in rows if row.get("discipline_issue"))
    return {
        "total_realized_pnl": round(total_pnl, 2),
        "total_cost_basis": round(total_cost, 2),
        "realized_pnl_pct": round(total_pnl / total_cost * 100, 2) if total_cost > 0 else None,
        "completed_sell_count": len(rows),
        "win_rate": round(len(winners) / len(rows) * 100, 1) if rows else None,
        "average_winner": round(sum(winners) / len(winners), 2) if winners else None,
        "average_loser": round(sum(losers) / len(losers), 2) if losers else None,
        "max_winner": round(max(winners), 2) if winners else None,
        "max_loser": round(min(losers), 2) if losers else None,
        "average_holding_days": round(sum(float(day) for day in holding_days) / len(holding_days), 1) if holding_days else None,
        "median_holding_days": round(float(median(holding_days)), 1) if holding_days else None,
        "discipline_issue_count": issue_count,
        **missing_stats,
    }


def calculate_holding_days(buy_date: date | datetime | str, sell_date: date | datetime | str) -> int | None:
    buy = _coerce_date(buy_date)
    sell = _coerce_date(sell_date)
    if buy is None or sell is None:
        return None
    return max(0, (sell - buy).days)


def _accumulate_sell(target: dict[int | str, dict[str, Any]], key: int | str, sell_entry: dict, match: dict) -> None:
    row = target.setdefault(key, _base_sell_trade(sell_entry))
    matched_qty = float(match["matched_quantity"])
    row["matched_quantity"] += matched_qty
    row["sell_quantity"] += matched_qty
    row["realized_pnl"] += float(match["realized_pnl"])
    row["cost_basis"] += float(match["buy_price"]) * matched_qty
    holding_days = _number(match.get("holding_days"))
    if holding_days is not None:
        row["holding_days_weighted_total"] += holding_days * matched_qty
        row["holding_days_quantity"] += matched_qty
    row["matched_lots"].append(match)
    row["cost_basis_source"] = _merge_cost_basis_source(row.get("cost_basis_source"), match.get("cost_basis_source"))
    if not row.get("position_tier"):
        row["position_tier"] = match.get("position_tier") or ""
    if not row.get("buy_mood"):
        row["buy_mood"] = match.get("buy_mood") or ""
    if match.get("target_sell_price") is not None and row.get("target_sell_price") is None:
        row["target_sell_price"] = match.get("target_sell_price")


def _accumulate_unmatched(target: dict[int | str, dict[str, Any]], key: int | str, sell_entry: dict, missing: dict) -> None:
    row = target.setdefault(key, _base_sell_trade(sell_entry))
    row["sell_quantity"] += float(missing.get("unmatched_quantity") or 0)
    row["unmatched_quantity"] += float(missing.get("unmatched_quantity") or 0)
    row["cost_basis_missing"] = True
    row["cost_basis_source"] = "missing"
    row["cost_basis_status"] = "missing"


def _base_sell_trade(entry: dict[str, Any]) -> dict[str, Any]:
    trade_date = _entry_date(entry)
    return {
        "sell_entry_id": _optional_int(entry.get("id")),
        "ticker": _symbol(entry.get("symbol")),
        "action_type": str(entry.get("action_type") or ""),
        "sell_date": trade_date.isoformat() if trade_date else "",
        "sell_quantity": 0.0,
        "matched_quantity": 0.0,
        "unmatched_quantity": 0.0,
        "buy_avg_price": None,
        "sell_price": _number(entry.get("price")),
        "realized_pnl": 0.0,
        "realized_pnl_pct": None,
        "cost_basis": 0.0,
        "cost_basis_source": "missing",
        "cost_basis_status": "missing",
        "included_in_performance": False,
        "holding_days": None,
        "holding_days_weighted_total": 0.0,
        "holding_days_quantity": 0.0,
        "holding_days_bucket": "",
        "position_tier": _position_tier(entry),
        "buy_mood": "",
        "sell_mood": str(entry.get("decision_mood") or ""),
        "sell_reason_type": str(entry.get("sell_reason_type") or ""),
        "target_sell_price": _number(entry.get("target_sell_price")),
        "below_target_sell_price": False,
        "discipline_issue": False,
        "discipline_flags": [],
        "cost_basis_missing": False,
        "matched_lots": [],
        "blockers": list(entry.get("blockers") or _load_json_list(entry.get("blockers_json"))),
        "warnings": list(entry.get("warnings") or _load_json_list(entry.get("warnings_json"))),
        "reentry_plan_text": str(entry.get("reentry_plan_text") or ""),
        "notes": str(entry.get("notes") or ""),
        "raw_entry": entry,
    }


def _finalize_sell_trade(row: dict[str, Any]) -> dict[str, Any]:
    matched_qty = float(row.get("matched_quantity") or 0)
    cost_basis = float(row.get("cost_basis") or 0)
    if matched_qty > 0 and cost_basis > 0:
        row["buy_avg_price"] = round(cost_basis / matched_qty, 4)
        row["realized_pnl"] = round(float(row["realized_pnl"]), 2)
        row["realized_pnl_pct"] = round(float(row["realized_pnl"]) / cost_basis * 100, 2)
        holding_qty = float(row.get("holding_days_quantity") or 0)
        if holding_qty > 0:
            row["holding_days"] = round(float(row["holding_days_weighted_total"]) / holding_qty, 1)
            row["holding_days_bucket"] = _holding_bucket(row["holding_days"])
        else:
            row["holding_days"] = None
            row["holding_days_bucket"] = "缺买入日期"
        row["cost_basis_missing"] = False
        row["cost_basis_status"] = _cost_basis_status(row.get("cost_basis_source"))
        row["included_in_performance"] = True
    else:
        row["realized_pnl"] = None
        row["realized_pnl_pct"] = None
        row["holding_days"] = None
        row["holding_days_bucket"] = "缺买入日期"
        row["cost_basis_source"] = "missing"
        row["cost_basis_status"] = "missing"
        row["included_in_performance"] = False
    flags = _discipline_flags(row)
    row["discipline_flags"] = flags
    row["discipline_issue"] = bool(flags)
    row["below_target_sell_price"] = "低于买入目标价卖出" in flags
    row.pop("holding_days_weighted_total", None)
    row.pop("holding_days_quantity", None)
    return row


def _discipline_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    a_class_review_needed = False
    is_a_class = str(row.get("position_tier") or "").upper() == "A"
    sell_price = _number(row.get("sell_price"))
    target = _number(row.get("target_sell_price"))
    if sell_price is not None and target is not None and sell_price < target:
        flags.append("低于买入目标价卖出")
        a_class_review_needed = a_class_review_needed or is_a_class
    if is_a_class and not has_concrete_reentry_plan(row.get("raw_entry") or {}):
        flags.append("A类卖出缺少具体回补计划")
        a_class_review_needed = True
    reason = str(row.get("sell_reason_type") or "").lower()
    mood = str(row.get("sell_mood") or "").lower()
    if reason in EMOTIONAL_SELL_REASONS or mood in EMOTIONAL_SELL_MOODS:
        flags.append("宏观/情绪型卖出")
        a_class_review_needed = a_class_review_needed or is_a_class
    holding_days = _number(row.get("holding_days"))
    if is_a_class and holding_days is not None and holding_days <= A_CLASS_SHORT_HOLDING_DAYS:
        flags.append("A类持仓天数过短")
        a_class_review_needed = True
    zone = str((row.get("raw_entry") or {}).get("zone_status") or (row.get("raw_entry") or {}).get("buy_zone_status") or "")
    if zone in {"IN_BUY_ZONE", "BELOW_BUY_ZONE"}:
        flags.append("卖出时处于买区/低于买区")
        a_class_review_needed = a_class_review_needed or is_a_class
    blockers = [str(item) for item in (row.get("blockers") or [])]
    if blockers:
        flags.append("卖出门禁曾阻断")
    if a_class_review_needed:
        flags.insert(0, "核心仓卖出需复盘")
    return flags


def _group_summary(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        missing_label = "等级缺失" if key == "position_tier" else "未记录"
        groups[str(row.get(key) or missing_label)].append(row)
    result: list[dict[str, Any]] = []
    for label, items in groups.items():
        stats = calculate_realized_pnl(items)
        result.append(
            {
                "key": label,
                "count": stats["completed_sell_count"],
                "realized_pnl": stats["total_realized_pnl"],
                "realized_pnl_pct": stats["realized_pnl_pct"],
                "win_rate": stats["win_rate"],
                "average_winner": stats["average_winner"],
                "average_loser": stats["average_loser"],
                "average_holding_days": stats["average_holding_days"],
                "median_holding_days": stats["median_holding_days"],
                "discipline_issue_count": stats["discipline_issue_count"],
            }
        )
    return sorted(result, key=lambda item: (str(item["key"])))


def _merge_cost_basis_source(current: object, incoming: object) -> str:
    current_text = str(current or "").strip() or "missing"
    incoming_text = str(incoming or "").strip() or "fifo"
    if current_text == "missing":
        return incoming_text
    if current_text == incoming_text:
        return current_text
    return "mixed"


def _cost_basis_status(source: object) -> str:
    value = str(source or "").strip()
    if value == "fifo":
        return "matched_fifo"
    if value == "position_snapshot":
        return "position_snapshot"
    if value == "manual_cost_basis":
        return "manual_cost_basis"
    if value == "mixed":
        return "mixed"
    return "missing"


def _apply_filters(rows: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    result = list(rows)
    ticker = str(filters.get("ticker") or "").strip().upper()
    tier = str(filters.get("position_tier") or "").strip().upper()
    date_from = _coerce_date(filters.get("date_from"))
    date_to = _coerce_date(filters.get("date_to"))
    outcome = str(filters.get("outcome") or "").strip()
    issue_only = bool(filters.get("discipline_issue_only"))
    if ticker:
        result = [row for row in result if str(row.get("ticker") or "").upper() == ticker]
    if tier:
        result = [row for row in result if str(row.get("position_tier") or "").upper() == tier]
    if date_from:
        result = [row for row in result if (_coerce_date(row.get("sell_date")) or date.min) >= date_from]
    if date_to:
        result = [row for row in result if (_coerce_date(row.get("sell_date")) or date.max) <= date_to]
    if outcome == "profit":
        result = [row for row in result if (_number(row.get("realized_pnl")) or 0) > 0]
    if outcome == "loss":
        result = [row for row in result if (_number(row.get("realized_pnl")) or 0) < 0]
    if issue_only:
        result = [row for row in result if row.get("discipline_issue")]
    return result


def _chronological_entries(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [dict(entry) for entry in entries],
        key=lambda entry: (
            _entry_date(entry) or date.max,
            str(entry.get("created_at") or ""),
            _optional_int(entry.get("id")) or 0,
        ),
    )


def _is_real_trade(entry: dict[str, Any]) -> bool:
    action = str(entry.get("action_type") or "").lower()
    if action not in REAL_ACTIONS:
        return False
    if _bool(entry.get("radar_observation_only")):
        return False
    if action in BUY_ACTIONS and _bool(entry.get("radar_blocked")):
        return False
    if action in SELL_ACTIONS and str(entry.get("discipline_status") or "").lower() == "blocked":
        return False
    return True


def _entry_date(entry: dict[str, Any]) -> date | None:
    for key in ("executed_at", "trade_date", "created_at"):
        parsed = _coerce_date(entry.get(key))
        if parsed is not None:
            return parsed
    return None


def _coerce_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(HKT).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(HKT).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _holding_bucket(days: object) -> str:
    value = _number(days)
    if value is None:
        return "缺数据"
    if value <= 3:
        return "0-3 天"
    if value <= 14:
        return "4-14 天"
    if value <= 30:
        return "15-30 天"
    if value <= 90:
        return "31-90 天"
    return "90 天以上"


def _position_tier(entry: dict[str, Any]) -> str:
    value = str(entry.get("position_tier") or entry.get("position_class") or "").strip().upper()
    return value if value in {"A", "B", "C"} else ""


def _pre_trade_position_tier(entry: dict[str, Any]) -> str:
    value = str(entry.get("pre_trade_position_tier") or "").strip().upper()
    return value if value in {"A", "B", "C"} else ""


def _lot_to_dict(lot: TradeLot) -> dict[str, Any]:
    return {
        "entry_id": lot.entry_id,
        "ticker": lot.ticker,
        "quantity": lot.quantity,
        "remaining_quantity": lot.remaining_quantity,
        "price": lot.price,
        "trade_date": lot.trade_date.isoformat(),
        "position_tier": lot.position_tier,
        "mood": lot.mood,
        "target_sell_price": lot.target_sell_price,
        "buy_reason": lot.buy_reason,
    }


def _row_to_dict(columns: list[str], row: tuple) -> dict[str, Any]:
    item = dict(zip(columns, row))
    item["blockers"] = _load_json_list(item.get("blockers_json"))
    item["warnings"] = _load_json_list(item.get("warnings_json"))
    item["radar_block_reasons"] = _load_json_list(item.get("radar_block_reasons_json"))
    return item


def _load_json_list(value: object) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(parsed, list):
        return parsed
    return [parsed]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone())


def _symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _number(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}
