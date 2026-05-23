from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from data.prices import CACHE_PATH


NUMERIC_PLAN_FIELDS = [
    "target_position_pct",
    "planned_position_pct",
    "first_buy_price",
    "second_buy_price",
    "third_buy_price",
    "no_chase_above",
    "fair_value_low",
    "fair_value_high",
    "tranche_buy_low",
    "tranche_buy_high",
    "heavy_buy_below",
]

TEXT_PLAN_FIELDS = [
    "stop_adding_condition",
    "invalidation_condition",
    "earnings_review_points",
    "notes",
]


class StockPlanStore:
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
        numeric_columns = ",\n                    ".join(f"{field} REAL" for field in NUMERIC_PLAN_FIELDS)
        text_columns = ",\n                    ".join(f"{field} TEXT" for field in TEXT_PLAN_FIELDS)
        with self.connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS stock_action_plans (
                    ticker TEXT PRIMARY KEY,
                    {numeric_columns},
                    {text_columns},
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_columns(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(stock_action_plans)").fetchall()}
        for field in NUMERIC_PLAN_FIELDS:
            if field not in existing:
                conn.execute(f"ALTER TABLE stock_action_plans ADD COLUMN {field} REAL")
        for field in TEXT_PLAN_FIELDS:
            if field not in existing:
                conn.execute(f"ALTER TABLE stock_action_plans ADD COLUMN {field} TEXT")
        if "updated_at" not in existing:
            conn.execute("ALTER TABLE stock_action_plans ADD COLUMN updated_at TEXT")

    def get_plan(self, ticker: str) -> dict:
        with self.connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM stock_action_plans WHERE ticker = ?",
                (ticker.upper(),),
            )
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []

        plan = _empty_plan(ticker)
        if not row:
            return plan
        for column, value in zip(columns, row):
            plan[column] = value
        return plan

    def save_plan(self, ticker: str, values: dict) -> dict:
        cleaned = _empty_plan(ticker)
        for field in NUMERIC_PLAN_FIELDS:
            cleaned[field] = _to_number(values.get(field))
        for field in TEXT_PLAN_FIELDS:
            cleaned[field] = _clean_text(values.get(field))
        cleaned["updated_at"] = datetime.now(timezone.utc).isoformat()

        fields = [*NUMERIC_PLAN_FIELDS, *TEXT_PLAN_FIELDS, "updated_at"]
        columns = ["ticker", *fields]
        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(f"{field} = excluded.{field}" for field in fields)
        with self.connect() as conn:
            conn.execute(
                f"""
                INSERT INTO stock_action_plans ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(ticker) DO UPDATE SET
                    {assignments}
                """,
                (ticker.upper(), *(cleaned[field] for field in fields)),
            )
        return cleaned

    def clear_buy_zone_override(self, ticker: str) -> dict:
        plan = self.get_plan(ticker)
        for field in (
            "no_chase_above",
            "fair_value_low",
            "fair_value_high",
            "tranche_buy_low",
            "tranche_buy_high",
            "heavy_buy_below",
        ):
            plan[field] = None
        return self.save_plan(ticker, plan)


def _empty_plan(ticker: str) -> dict:
    return {
        "ticker": ticker.upper(),
        **{field: None for field in NUMERIC_PLAN_FIELDS},
        **{field: "" for field in TEXT_PLAN_FIELDS},
        "updated_at": None,
    }


def _to_number(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()
