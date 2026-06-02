from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data.market_context import build_market_context
from data.decision_log import TradeJournalStore
from data.portfolio import PortfolioPositionStore, PortfolioSettingsStore
from data.portfolio_ledger_projection import POSITION_AFFECTING_ACTIONS
from data.portfolio_ledger_projection import project_trade_effect
from data.prices import CACHE_PATH
from data.trade_safety_gate import trade_sync_policy


PORTFOLIO_SYNC_ACTIONS = {"buy", "add", "sell", "trim", "skip"}


def preview_trade_portfolio_effect(entry_id: int, path: Path = CACHE_PATH) -> dict[str, Any]:
    _ensure_sync_schema(path)
    entry = TradeJournalStore(path).get_entry(entry_id)
    if not entry:
        return _empty_preview(entry_id, status="failed", error="交易记录不存在")
    preview = _preview_entry_effect(entry, path)
    sync_policy = trade_sync_policy(entry)
    if not sync_policy["canSync"] and preview.get("status") != "already_synced":
        preview.update({"status": "failed", "syncStatus": "failed", "error": sync_policy["reason"]})
    return preview


def preview_trade_values_portfolio_effect(
    symbol: str,
    values: dict[str, Any],
    path: Path = CACHE_PATH,
) -> dict[str, Any]:
    if not str(symbol or "").strip():
        return _empty_preview(None, status="failed", error="请先填写股票代码")
    entry = {
        "id": None,
        "symbol": _normalize_symbol(symbol),
        "action_type": str(values.get("action_type") or "").strip().lower(),
        "quantity": _number(values.get("quantity")),
        "price": _number(values.get("price")),
    }
    return _preview_entry_effect(entry, path, check_sync_log=False)


def apply_trade_to_portfolio(entry_id: int, path: Path = CACHE_PATH) -> dict[str, Any]:
    _ensure_sync_schema(path)
    entry = TradeJournalStore(path).get_entry(entry_id)
    if not entry:
        result = _empty_preview(entry_id, status="failed", error="交易记录不存在")
        _write_sync_log(path, result, status="failed")
        return result

    sync_policy = trade_sync_policy(entry)
    if not sync_policy["canSync"]:
        result = _preview_entry_effect(entry, path)
        result.update(
            {
                "status": "failed",
                "syncStatus": "failed",
                "error": sync_policy["reason"],
            }
        )
        _write_sync_log(path, result, status="failed")
        return result

    existing = _sync_log(path, entry_id)
    if existing and existing.get("status") == "synced":
        preview = _preview_entry_effect(entry, path)
        preview.update({"status": "already_synced", "syncStatus": "synced", "error": "这条交易已经同步过"})
        return preview

    preview = _preview_entry_effect(entry, path)
    if preview["status"] not in {"ready", "no_effect"}:
        _write_sync_log(path, preview, status="failed")
        return preview

    if preview["actionType"] in POSITION_AFFECTING_ACTIONS:
        position_store = PortfolioPositionStore(path)
        current = position_store.get_position(preview["symbol"]) or {}
        if not _position_is_active(current):
            current = {}
        position_store.save_position(
            preview["symbol"],
            {
                "quantity": preview["afterQuantity"],
                "average_cost": preview["afterAverageCost"],
                "target_position_pct": current.get("target_position_pct"),
                "max_acceptable_position_pct": current.get("max_acceptable_position_pct"),
                "planned_sell_price": current.get("planned_sell_price"),
                "first_trim_price": current.get("first_trim_price"),
                "second_trim_price": current.get("second_trim_price"),
                "review_price": current.get("review_price"),
                "notes": current.get("notes"),
                "is_active": True,
            },
        )

    preview.update({"status": "success", "syncStatus": "synced", "error": None})
    _write_sync_log(path, preview, status="synced")
    return preview


def get_trade_portfolio_sync_status(entry_id: int, path: Path = CACHE_PATH) -> dict[str, Any]:
    _ensure_sync_schema(path)
    log = _sync_log(path, entry_id)
    if not log:
        return {"entryId": int(entry_id), "syncStatus": "not_synced", "error": None}
    return {
        "entryId": int(entry_id),
        "syncStatus": log.get("status") or "not_synced",
        "error": log.get("error"),
        "syncedAt": log.get("created_at"),
    }


def _entry_sync_blocked_by_discipline(entry: dict[str, Any]) -> bool:
    return not bool(trade_sync_policy(entry).get("canSync"))


def unsynced_trade_counts_by_symbol(path: Path = CACHE_PATH) -> dict[str, int]:
    _ensure_sync_schema(path)
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute(
            """
            SELECT
                entry.symbol,
                entry.action_type,
                entry.discipline_status,
                entry.blockers_json
            FROM trade_journal_entries AS entry
            LEFT JOIN trade_portfolio_sync_logs AS log
                ON log.entry_id = entry.id AND log.status = 'synced'
            WHERE entry.action_type IN ('buy', 'add', 'sell', 'trim')
              AND log.entry_id IS NULL
            """
        ).fetchall()
    counts: dict[str, int] = {}
    for symbol, action_type, discipline_status, blockers_json in rows:
        entry = {
            "symbol": symbol,
            "action_type": action_type,
            "discipline_status": discipline_status,
            "blockers_json": blockers_json,
        }
        if _entry_sync_blocked_by_discipline(entry):
            continue
        normalized = str(symbol).upper()
        counts[normalized] = counts.get(normalized, 0) + 1
    return counts


def _preview_entry_effect(
    entry: dict[str, Any],
    path: Path,
    *,
    check_sync_log: bool = True,
) -> dict[str, Any]:
    entry_id = _optional_int(entry.get("id"))
    symbol = _normalize_symbol(entry.get("symbol"))
    action_type = str(entry.get("action_type") or "").strip().lower()
    quantity = _number(entry.get("quantity"))
    price = _number(entry.get("price"))
    position = PortfolioPositionStore(path).get_position(symbol) or {}
    if not _position_is_active(position):
        position = {}
    current_quantity = _number(position.get("quantity")) or 0.0
    current_average_cost = _number(position.get("average_cost")) or 0.0
    log = _sync_log(path, entry_id) if check_sync_log and entry_id is not None else None
    sync_status = str((log or {}).get("status") or "not_synced")

    base = {
        "entryId": entry_id,
        "symbol": symbol,
        "actionType": action_type,
        "syncStatus": sync_status,
        "currentQuantity": current_quantity,
        "currentAverageCost": current_average_cost,
        "tradeQuantity": quantity,
        "tradePrice": price,
        "quantityDelta": 0.0,
        "afterQuantity": current_quantity,
        "afterAverageCost": current_average_cost,
        "afterMarketValue": None,
        "afterPositionPct": None,
        "error": None,
    }

    if sync_status == "synced":
        return {**base, "status": "already_synced", "error": "这条交易已经同步过"}
    if action_type == "skip":
        return {**base, "status": "no_effect"}
    if action_type not in POSITION_AFFECTING_ACTIONS:
        return {**base, "status": "failed", "error": "该交易类型暂不支持同步到组合持仓"}
    if quantity is None or quantity <= 0:
        return {**base, "status": "failed", "error": "同步需要有效成交数量"}
    if price is None or price <= 0:
        return {**base, "status": "failed", "error": "同步需要有效成交价格"}

    projected = project_trade_effect(
        current_quantity=current_quantity,
        current_average_cost=current_average_cost,
        action_type=action_type,
        quantity=quantity,
        price=price,
    )
    if projected["status"] != "ready":
        return {**base, "status": projected["status"], "error": projected["error"]}

    market = _market_effect(symbol, projected["afterQuantity"], path)
    return {
        **base,
        "status": "ready",
        "quantityDelta": projected["quantityDelta"],
        "afterQuantity": projected["afterQuantity"],
        "afterAverageCost": projected["afterAverageCost"],
        "afterMarketValue": market["afterMarketValue"],
        "afterPositionPct": market["afterPositionPct"],
    }


def _market_effect(symbol: str, after_quantity: float, path: Path) -> dict[str, float | None]:
    current_price = _number(build_market_context(symbol, path=path).get("currentPrice"))
    if current_price is None:
        return {"afterMarketValue": None, "afterPositionPct": None}
    market_value = after_quantity * current_price
    total_value = _number(PortfolioSettingsStore(path).get_settings().get("total_portfolio_value"))
    return {
        "afterMarketValue": market_value,
        "afterPositionPct": market_value / total_value * 100 if total_value and total_value > 0 else None,
    }


def _position_is_active(position: dict[str, Any]) -> bool:
    if not position:
        return False
    value = position.get("is_active", 1)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in {"0", "false", "no", "n", "off"}


def _ensure_sync_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    TradeJournalStore(path)
    PortfolioPositionStore(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_portfolio_sync_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL UNIQUE,
                symbol TEXT NOT NULL,
                action_type TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                before_quantity REAL,
                before_average_cost REAL,
                trade_quantity REAL,
                trade_price REAL,
                after_quantity REAL,
                after_average_cost REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _sync_log(path: Path, entry_id: int | None) -> dict[str, Any] | None:
    if entry_id is None:
        return None
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM trade_portfolio_sync_logs
            WHERE entry_id = ?
            """,
            (int(entry_id),),
        ).fetchone()
        columns = [description[0] for description in conn.execute("SELECT * FROM trade_portfolio_sync_logs LIMIT 0").description]
    return dict(zip(columns, row)) if row else None


def _write_sync_log(path: Path, preview: dict[str, Any], *, status: str) -> None:
    entry_id = preview.get("entryId")
    if entry_id is None:
        return
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            INSERT INTO trade_portfolio_sync_logs (
                entry_id,
                symbol,
                action_type,
                status,
                error,
                before_quantity,
                before_average_cost,
                trade_quantity,
                trade_price,
                after_quantity,
                after_average_cost,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
                status = excluded.status,
                error = excluded.error,
                before_quantity = excluded.before_quantity,
                before_average_cost = excluded.before_average_cost,
                trade_quantity = excluded.trade_quantity,
                trade_price = excluded.trade_price,
                after_quantity = excluded.after_quantity,
                after_average_cost = excluded.after_average_cost,
                created_at = excluded.created_at
            """,
            (
                int(entry_id),
                preview.get("symbol") or "",
                preview.get("actionType") or "",
                status,
                preview.get("error"),
                preview.get("currentQuantity"),
                preview.get("currentAverageCost"),
                preview.get("tradeQuantity"),
                preview.get("tradePrice"),
                preview.get("afterQuantity"),
                preview.get("afterAverageCost"),
                _now(),
            ),
        )
        conn.commit()


def _empty_preview(entry_id: int | None, *, status: str, error: str) -> dict[str, Any]:
    return {
        "entryId": entry_id,
        "symbol": "",
        "actionType": "",
        "status": status,
        "syncStatus": "failed" if status == "failed" else "not_synced",
        "currentQuantity": None,
        "currentAverageCost": None,
        "tradeQuantity": None,
        "tradePrice": None,
        "quantityDelta": None,
        "afterQuantity": None,
        "afterAverageCost": None,
        "afterMarketValue": None,
        "afterPositionPct": None,
        "error": error,
    }


def _normalize_symbol(value: object) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    return symbol


def _number(value: object) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
