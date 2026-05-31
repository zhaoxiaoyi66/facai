from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from data.portfolio import PortfolioPositionStore
from data.portfolio_ledger_projection import project_positions_from_trade_rows
from data.portfolio_trade_sync import unsynced_trade_counts_by_symbol
from data.prices import CACHE_PATH
from data.decision_log import TradeJournalStore


QUANTITY_TOLERANCE = 1e-6
COST_TOLERANCE = 0.01


def build_portfolio_reconciliation(path: Path = CACHE_PATH) -> list[dict[str, Any]]:
    _ensure_tables(path)
    positions = {
        str(position.get("symbol") or "").upper(): position
        for position in PortfolioPositionStore(path).list_active_positions()
    }
    journal = _journal_positions_from_synced_trades(path)
    unsynced_counts = unsynced_trade_counts_by_symbol(path)
    symbols = sorted(set(positions) | set(journal) | set(unsynced_counts))
    return [
        _reconciliation_row(symbol, positions.get(symbol), journal.get(symbol), unsynced_counts.get(symbol, 0))
        for symbol in symbols
    ]


def _reconciliation_row(
    symbol: str,
    position: dict[str, Any] | None,
    journal: dict[str, Any] | None,
    unsynced_count: int,
) -> dict[str, Any]:
    position_quantity = _number((position or {}).get("quantity"))
    position_cost = _number((position or {}).get("average_cost"))
    journal_quantity = _number((journal or {}).get("quantity"))
    journal_cost = _number((journal or {}).get("average_cost"))
    quantity_diff = _diff(position_quantity, journal_quantity)
    cost_diff = _diff(position_cost, journal_cost)
    reasons: list[str] = []

    if position is None and journal is not None:
        reasons.append("synced_journal_without_active_position")
    if position is not None and journal is None:
        reasons.append("position_without_synced_journal")
    if unsynced_count > 0:
        reasons.append("unsynced_trades_exist")
    if quantity_diff is not None and abs(quantity_diff) > QUANTITY_TOLERANCE:
        reasons.append("quantity_mismatch")
    if cost_diff is not None and abs(cost_diff) > COST_TOLERANCE:
        reasons.append("average_cost_mismatch")

    status = _status(reasons)
    return {
        "symbol": symbol,
        "positionQuantity": position_quantity,
        "journalQuantity": journal_quantity,
        "quantityDiff": quantity_diff,
        "positionAverageCost": position_cost,
        "journalAverageCost": journal_cost,
        "costDiff": cost_diff,
        "unsyncedTradeCount": int(unsynced_count or 0),
        "status": status,
        "reasons": reasons,
    }


def _journal_positions_from_synced_trades(path: Path) -> dict[str, dict[str, float]]:
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute(
            """
            SELECT
                entry.symbol,
                entry.action_type,
                entry.quantity,
                entry.price
            FROM trade_journal_entries AS entry
            INNER JOIN trade_portfolio_sync_logs AS log
                ON log.entry_id = entry.id AND log.status = 'synced'
            WHERE entry.action_type IN ('buy', 'add', 'sell', 'trim')
            ORDER BY entry.symbol ASC, entry.trade_date ASC, entry.created_at ASC, entry.id ASC
            """
        ).fetchall()
    return project_positions_from_trade_rows(
        {
            "symbol": symbol,
            "action_type": action_type,
            "quantity": raw_quantity,
            "price": raw_price,
        }
        for symbol, action_type, raw_quantity, raw_price in rows
    )


def _status(reasons: list[str]) -> str:
    mismatch_reasons = {"quantity_mismatch", "synced_journal_without_active_position"}
    if any(reason in mismatch_reasons for reason in reasons):
        return "mismatch"
    if reasons:
        return "warning"
    return "ok"


def _ensure_tables(path: Path) -> None:
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


def _diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 8)


def _number(value: object) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
