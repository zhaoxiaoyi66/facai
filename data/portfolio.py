from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from data.prices import CACHE_PATH


POSITION_NUMERIC_FIELDS = [
    "quantity",
    "average_cost",
    "target_position_pct",
    "max_acceptable_position_pct",
    "planned_sell_price",
    "first_trim_price",
    "second_trim_price",
    "review_price",
]

SETTINGS_ID = "default"


class PortfolioPositionStore:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL UNIQUE,
                    quantity REAL NOT NULL,
                    average_cost REAL NOT NULL,
                    target_position_pct REAL,
                    max_acceptable_position_pct REAL,
                    planned_sell_price REAL,
                    first_trim_price REAL,
                    second_trim_price REAL,
                    review_price REAL,
                    notes TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_columns(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(portfolio_positions)").fetchall()}
        additions = {
            "target_position_pct": "REAL",
            "max_acceptable_position_pct": "REAL",
            "planned_sell_price": "REAL",
            "first_trim_price": "REAL",
            "second_trim_price": "REAL",
            "review_price": "REAL",
            "notes": "TEXT",
            "is_active": "INTEGER NOT NULL DEFAULT 1",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        }
        for column, definition in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE portfolio_positions ADD COLUMN {column} {definition}")

    def save_position(self, symbol: str, values: dict) -> dict:
        cleaned = _clean_position(symbol, values)
        now = _now()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM portfolio_positions WHERE symbol = ?",
                (cleaned["symbol"],),
            ).fetchone()
            created_at = existing[0] if existing and existing[0] else now
            conn.execute(
                """
                INSERT INTO portfolio_positions (
                    symbol,
                    quantity,
                    average_cost,
                    target_position_pct,
                    max_acceptable_position_pct,
                    planned_sell_price,
                    first_trim_price,
                    second_trim_price,
                    review_price,
                    notes,
                    is_active,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    quantity = excluded.quantity,
                    average_cost = excluded.average_cost,
                    target_position_pct = excluded.target_position_pct,
                    max_acceptable_position_pct = excluded.max_acceptable_position_pct,
                    planned_sell_price = excluded.planned_sell_price,
                    first_trim_price = excluded.first_trim_price,
                    second_trim_price = excluded.second_trim_price,
                    review_price = excluded.review_price,
                    notes = excluded.notes,
                    is_active = excluded.is_active,
                    updated_at = excluded.updated_at
                """,
                (
                    cleaned["symbol"],
                    cleaned["quantity"],
                    cleaned["average_cost"],
                    cleaned["target_position_pct"],
                    cleaned["max_acceptable_position_pct"],
                    cleaned["planned_sell_price"],
                    cleaned["first_trim_price"],
                    cleaned["second_trim_price"],
                    cleaned["review_price"],
                    cleaned["notes"],
                    1 if cleaned["is_active"] else 0,
                    created_at,
                    now,
                ),
            )
        return self.get_position(cleaned["symbol"]) or cleaned

    def get_position(self, symbol: str) -> dict | None:
        with self.connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM portfolio_positions WHERE symbol = ?",
                (_normalize_symbol(symbol),),
            )
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return _row_to_dict(columns, row) if row else None

    def list_active_positions(self) -> list[dict]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM portfolio_positions
                WHERE is_active = 1
                ORDER BY symbol
                """
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]

    def deactivate_position(self, symbol: str) -> dict | None:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE portfolio_positions
                SET is_active = 0,
                    updated_at = ?
                WHERE symbol = ?
                """,
                (now, _normalize_symbol(symbol)),
            )
        return self.get_position(symbol)


class PortfolioSettingsStore:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio_settings (
                    id TEXT PRIMARY KEY,
                    total_portfolio_value REAL,
                    cash_balance REAL,
                    base_currency TEXT NOT NULL DEFAULT 'USD',
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_columns(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(portfolio_settings)").fetchall()}
        additions = {
            "total_portfolio_value": "REAL",
            "cash_balance": "REAL",
            "base_currency": "TEXT NOT NULL DEFAULT 'USD'",
            "updated_at": "TEXT",
        }
        for column, definition in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE portfolio_settings ADD COLUMN {column} {definition}")

    def get_settings(self) -> dict:
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM portfolio_settings WHERE id = ?", (SETTINGS_ID,))
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        if not row:
            return _empty_settings()
        return _settings_row_to_dict(columns, row)

    def save_settings(self, values: dict) -> dict:
        total_value = _to_non_negative_number(values.get("total_portfolio_value"), "total_portfolio_value", required=False)
        cash_balance = _to_non_negative_number(values.get("cash_balance"), "cash_balance", required=False)
        base_currency = str(values.get("base_currency") or "USD").strip().upper() or "USD"
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO portfolio_settings (
                    id,
                    total_portfolio_value,
                    cash_balance,
                    base_currency,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    total_portfolio_value = excluded.total_portfolio_value,
                    cash_balance = excluded.cash_balance,
                    base_currency = excluded.base_currency,
                    updated_at = excluded.updated_at
                """,
                (SETTINGS_ID, total_value, cash_balance, base_currency, now),
            )
        return self.get_settings()


def _clean_position(symbol: str, values: dict) -> dict:
    return {
        "symbol": _normalize_symbol(symbol),
        "quantity": _to_non_negative_number(values.get("quantity"), "quantity", required=True),
        "average_cost": _to_non_negative_number(values.get("average_cost"), "average_cost", required=True),
        "target_position_pct": _to_non_negative_number(values.get("target_position_pct"), "target_position_pct", required=False),
        "max_acceptable_position_pct": _to_non_negative_number(values.get("max_acceptable_position_pct"), "max_acceptable_position_pct", required=False),
        "planned_sell_price": _to_non_negative_number(values.get("planned_sell_price"), "planned_sell_price", required=False),
        "first_trim_price": _to_non_negative_number(values.get("first_trim_price"), "first_trim_price", required=False),
        "second_trim_price": _to_non_negative_number(values.get("second_trim_price"), "second_trim_price", required=False),
        "review_price": _to_non_negative_number(values.get("review_price"), "review_price", required=False),
        "notes": _clean_text(values.get("notes")),
        "is_active": bool(values.get("is_active", True)),
    }


def _normalize_symbol(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        raise ValueError("symbol is required")
    return normalized


def _to_non_negative_number(value, field: str, required: bool) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"{field} is required")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if number < 0:
        raise ValueError(f"{field} cannot be negative")
    return number


def _clean_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _row_to_dict(columns: list[str], row: tuple) -> dict:
    item = dict(zip(columns, row))
    if "is_active" in item:
        item["is_active"] = bool(item["is_active"])
    return item


def _empty_settings() -> dict:
    return {
        "id": SETTINGS_ID,
        "total_portfolio_value": None,
        "cash_balance": None,
        "base_currency": "USD",
        "updated_at": None,
    }


def _settings_row_to_dict(columns: list[str], row: tuple) -> dict:
    item = dict(zip(columns, row))
    item.setdefault("id", SETTINGS_ID)
    item.setdefault("base_currency", "USD")
    return item


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
