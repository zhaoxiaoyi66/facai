from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from data.prices import CACHE_PATH
from data.portfolio_roles import (
    ROLE_UNDEFINED,
    normalize_portfolio_role,
    portfolio_role_badge_class,
    portfolio_role_label,
)


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
TRIM_PRICE_NEAR_PCT = 5.0
VALID_POSITION_TIERS = {"A", "B", "C"}
POSITION_TIER_LABELS = {
    "A": "A类",
    "B": "B类",
    "C": "C类",
}
POSITION_TIER_MISSING_LABEL = "需设置等级"


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
                    position_tier TEXT,
                    role TEXT NOT NULL DEFAULT 'UNDEFINED',
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
            "position_tier": "TEXT",
            "role": "TEXT NOT NULL DEFAULT 'UNDEFINED'",
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
                "SELECT created_at, position_tier, role FROM portfolio_positions WHERE symbol = ?",
                (cleaned["symbol"],),
            ).fetchone()
            created_at = existing[0] if existing and existing[0] else now
            position_tier = cleaned["position_tier"]
            if position_tier is None and existing:
                try:
                    position_tier = _clean_position_tier(existing[1], required=False)
                except ValueError:
                    position_tier = None
            role = cleaned["role"]
            if role is None and existing:
                role = _clean_position_role(existing[2])
            role = role or ROLE_UNDEFINED
            conn.execute(
                """
                INSERT INTO portfolio_positions (
                    symbol,
                    quantity,
                    average_cost,
                    position_tier,
                    role,
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    quantity = excluded.quantity,
                    average_cost = excluded.average_cost,
                    position_tier = excluded.position_tier,
                    role = excluded.role,
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
                    position_tier,
                    role,
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
                  AND COALESCE(quantity, 0) > 0
                ORDER BY symbol
                """
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]

    def update_position_tier(self, symbol: str, position_tier: str) -> dict:
        clean_symbol = _normalize_symbol(symbol)
        clean_tier = _clean_position_tier(position_tier, required=True)
        now = _now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE portfolio_positions
                SET position_tier = ?,
                    updated_at = ?
                WHERE symbol = ?
                  AND is_active = 1
                """,
                (clean_tier, now, clean_symbol),
            )
        if cursor.rowcount <= 0:
            raise ValueError("position not found")
        return self.get_position(clean_symbol) or {"symbol": clean_symbol, "position_tier": clean_tier}

    def update_position_role(self, symbol: str, role: str) -> dict:
        clean_symbol = _normalize_symbol(symbol)
        clean_role = _clean_position_role(role) or ROLE_UNDEFINED
        now = _now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE portfolio_positions
                SET role = ?,
                    updated_at = ?
                WHERE symbol = ?
                  AND is_active = 1
                """,
                (clean_role, now, clean_symbol),
            )
        if cursor.rowcount <= 0:
            raise ValueError("position not found")
        return self.get_position(clean_symbol) or {"symbol": clean_symbol, "role": clean_role}

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


def calculate_portfolio_positions(
    positions: list[dict],
    current_prices: dict[str, float | None],
    settings: dict | None = None,
    system_refs: dict[str, dict] | None = None,
    trim_near_pct: float = TRIM_PRICE_NEAR_PCT,
) -> list[dict]:
    active_positions = [
        position
        for position in positions
        if position.get("is_active", True) and _has_position_quantity(position)
    ]
    market_values = {
        _normalize_symbol(str(position.get("symbol") or "")): _market_value(position, current_prices)
        for position in active_positions
    }
    denominator = _portfolio_denominator(settings, market_values)
    return [
        calculate_portfolio_position(
            position,
            current_prices.get(_normalize_symbol(str(position.get("symbol") or ""))),
            denominator,
            (system_refs or {}).get(_normalize_symbol(str(position.get("symbol") or "")), {}),
            trim_near_pct=trim_near_pct,
        )
        for position in active_positions
    ]


def calculate_portfolio_position(
    position: dict,
    current_price: float | None,
    portfolio_denominator: float | None,
    system_ref: dict | None = None,
    trim_near_pct: float = TRIM_PRICE_NEAR_PCT,
) -> dict:
    symbol = _normalize_symbol(str(position.get("symbol") or ""))
    quantity = _to_non_negative_number(position.get("quantity"), "quantity", required=True) or 0.0
    average_cost = _to_non_negative_number(position.get("average_cost"), "average_cost", required=True) or 0.0
    price = _optional_non_negative_number(current_price, "current_price")
    cost_basis = quantity * average_cost
    market_value = quantity * price if price is not None else None
    unrealized_pnl = market_value - cost_basis if market_value is not None else None
    unrealized_pnl_pct = unrealized_pnl / cost_basis * 100 if unrealized_pnl is not None and cost_basis > 0 else None
    position_pct = market_value / portfolio_denominator * 100 if market_value is not None and portfolio_denominator and portfolio_denominator > 0 else None
    system_ref = system_ref or {}
    system_max = _optional_non_negative_number(
        system_ref.get("systemMaxPosition", system_ref.get("maxPortfolioWeightPercent")),
        "systemMaxPosition",
    )
    personal_max = _optional_non_negative_number(position.get("max_acceptable_position_pct"), "max_acceptable_position_pct")
    system_status = str(system_ref.get("systemStatus") or system_ref.get("decisionLane") or "").strip().lower()

    return {
        **position,
        "symbol": symbol,
        "role": _clean_position_role(position.get("role")) or ROLE_UNDEFINED,
        "currentPrice": price,
        "marketValue": market_value,
        "costBasis": cost_basis,
        "unrealizedPnl": unrealized_pnl,
        "unrealizedPnlPct": unrealized_pnl_pct,
        "positionPct": position_pct,
        "overweightSystem": _exceeds(position_pct, system_max),
        "overweightPersonal": _exceeds(position_pct, personal_max),
        "nearTrimPrice": _near_trim_price(price, position, trim_near_pct),
        "needsReview": _needs_review(price, position, system_status),
        "missingPrice": price is None,
        "systemMaxPosition": system_max,
        "systemStatus": system_status,
    }


def _clean_position(symbol: str, values: dict) -> dict:
    return {
        "symbol": _normalize_symbol(symbol),
        "quantity": _to_non_negative_number(values.get("quantity"), "quantity", required=True),
        "average_cost": _to_non_negative_number(values.get("average_cost"), "average_cost", required=True),
        "position_tier": _clean_position_tier(values.get("position_tier"), required=False),
        "role": _clean_position_role(
            values.get("role", values.get("holding_role", values.get("portfolio_role")))
            if any(key in values for key in ("role", "holding_role", "portfolio_role"))
            else None
        ),
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


def _clean_position_tier(value, *, required: bool) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError("position_tier is required")
        return None
    tier = str(value).strip().upper()
    if tier not in VALID_POSITION_TIERS:
        raise ValueError("position_tier must be A, B, or C")
    return tier


def _clean_position_role(value) -> str | None:
    return normalize_portfolio_role(value, default=None)


def _has_position_quantity(position: dict) -> bool:
    quantity = _to_non_negative_number(position.get("quantity"), "quantity", required=True) or 0.0
    return quantity > 0


def format_position_tier_label(value) -> str:
    try:
        tier = _clean_position_tier(value, required=False)
    except ValueError:
        tier = None
    if tier is None:
        return POSITION_TIER_MISSING_LABEL
    return POSITION_TIER_LABELS[tier]


def format_portfolio_role_label(value) -> str:
    return portfolio_role_label(value)


def portfolio_role_badge_class_for_value(value) -> str:
    return portfolio_role_badge_class(value)


def position_tier_badge_class(value) -> str:
    try:
        tier = _clean_position_tier(value, required=False)
    except ValueError:
        tier = None
    return f"tier-{tier.lower()}" if tier else "tier-missing"


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


def _optional_non_negative_number(value, field: str) -> float | None:
    return _to_non_negative_number(value, field, required=False)


def _market_value(position: dict, current_prices: dict[str, float | None]) -> float:
    symbol = _normalize_symbol(str(position.get("symbol") or ""))
    price = _optional_non_negative_number(current_prices.get(symbol), "current_price")
    if price is None:
        return 0.0
    quantity = _to_non_negative_number(position.get("quantity"), "quantity", required=True) or 0.0
    return quantity * price


def _portfolio_denominator(settings: dict | None, market_values: dict[str, float]) -> float | None:
    total_value = _optional_non_negative_number((settings or {}).get("total_portfolio_value"), "total_portfolio_value")
    if total_value is not None and total_value > 0:
        return total_value
    total_market_value = sum(market_values.values())
    return total_market_value if total_market_value > 0 else None


def _exceeds(position_pct: float | None, limit: float | None) -> bool:
    return position_pct is not None and limit is not None and position_pct > limit


def _near_trim_price(price: float | None, position: dict, trim_near_pct: float) -> bool:
    if price is None:
        return False
    targets = [
        _optional_non_negative_number(position.get("first_trim_price"), "first_trim_price"),
        _optional_non_negative_number(position.get("second_trim_price"), "second_trim_price"),
        _optional_non_negative_number(position.get("planned_sell_price"), "planned_sell_price"),
    ]
    for target in targets:
        if target is not None and target > 0 and price >= target * (1 - trim_near_pct / 100):
            return True
    return False


def _needs_review(price: float | None, position: dict, system_status: str) -> bool:
    if system_status in {"review", "blocked"}:
        return True
    review_price = _optional_non_negative_number(position.get("review_price"), "review_price")
    return price is not None and review_price is not None and price <= review_price


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
