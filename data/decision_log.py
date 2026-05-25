from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from data.prices import CACHE_PATH


ACTION_TYPES = {"buy", "sell", "add", "trim", "sell_put", "covered_call", "skip"}


def build_decision_snapshot_from_bundle(
    symbol: str,
    price,
    final_decision_bundle,
    source_page: str,
) -> dict:
    block_reasons = _bundle_list(final_decision_bundle, "blockReasons", "block_reasons")
    review_reasons = _bundle_list(final_decision_bundle, "reviewReasons", "review_reasons")
    return {
        "symbol": _normalize_symbol(symbol),
        "decision_date": date.today().isoformat(),
        "price": _optional_non_negative_number(price, "price"),
        "final_action": _clean_text(_bundle_value(final_decision_bundle, "finalAction", "final_action")),
        "decision_lane": _clean_text(_bundle_value(final_decision_bundle, "decisionLane", "decision_lane")),
        "current_add_pct": _optional_non_negative_number(
            _bundle_value(final_decision_bundle, "currentAddLimitPercent", "current_add_pct"),
            "current_add_pct",
        ),
        "max_position_pct": _optional_non_negative_number(
            _bundle_value(final_decision_bundle, "maxPortfolioWeightPercent", "max_position_pct"),
            "max_position_pct",
        ),
        "risk_rating": _clean_text(_bundle_value(final_decision_bundle, "riskRating", "risk_rating")),
        "data_confidence": _clean_text(_bundle_value(final_decision_bundle, "dataConfidence", "data_confidence")),
        "buy_zone_status": _clean_text(
            _bundle_value(
                final_decision_bundle,
                "buyZoneStatus",
                "buy_zone_status",
                "displayCategory",
                "display_category",
            )
        ),
        "block_reasons_json": _reasons_json(block_reasons),
        "review_reasons_json": _reasons_json(review_reasons),
        "reason_text": _reason_text(block_reasons, review_reasons, final_decision_bundle),
        "source_page": _clean_text(source_page),
    }


class DecisionLogStore:
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
                CREATE TABLE IF NOT EXISTS decision_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    decision_date TEXT NOT NULL,
                    price REAL,
                    final_action TEXT,
                    decision_lane TEXT,
                    current_add_pct REAL,
                    max_position_pct REAL,
                    risk_rating TEXT,
                    data_confidence TEXT,
                    buy_zone_status TEXT,
                    block_reasons_json TEXT NOT NULL DEFAULT '[]',
                    review_reasons_json TEXT NOT NULL DEFAULT '[]',
                    reason_text TEXT,
                    source_page TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_decision_snapshots_symbol_date
                ON decision_snapshots(symbol, decision_date, created_at)
                """
            )

    def save_snapshot(self, symbol: str, values: dict) -> dict:
        cleaned = _clean_decision_snapshot(symbol, values)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO decision_snapshots (
                    symbol,
                    decision_date,
                    price,
                    final_action,
                    decision_lane,
                    current_add_pct,
                    max_position_pct,
                    risk_rating,
                    data_confidence,
                    buy_zone_status,
                    block_reasons_json,
                    review_reasons_json,
                    reason_text,
                    source_page,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cleaned["symbol"],
                    cleaned["decision_date"],
                    cleaned["price"],
                    cleaned["final_action"],
                    cleaned["decision_lane"],
                    cleaned["current_add_pct"],
                    cleaned["max_position_pct"],
                    cleaned["risk_rating"],
                    cleaned["data_confidence"],
                    cleaned["buy_zone_status"],
                    cleaned["block_reasons_json"],
                    cleaned["review_reasons_json"],
                    cleaned["reason_text"],
                    cleaned["source_page"],
                    cleaned["created_at"],
                ),
            )
            snapshot_id = cursor.lastrowid
        return self.get_snapshot(int(snapshot_id)) or cleaned

    def get_snapshot(self, snapshot_id: int) -> dict | None:
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM decision_snapshots WHERE id = ?", (snapshot_id,))
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return _row_to_dict(columns, row) if row else None

    def list_snapshots(self, symbol: str) -> list[dict]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM decision_snapshots
                WHERE symbol = ?
                ORDER BY decision_date DESC, created_at DESC, id DESC
                """,
                (_normalize_symbol(symbol),),
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]


class TradeJournalStore:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        DecisionLogStore(path)
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
                CREATE TABLE IF NOT EXISTS trade_journal_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    quantity REAL,
                    price REAL,
                    premium REAL,
                    strike_price REAL,
                    expiry_date TEXT,
                    decision_snapshot_id INTEGER,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(decision_snapshot_id) REFERENCES decision_snapshots(id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trade_journal_entries_symbol_date
                ON trade_journal_entries(symbol, trade_date, created_at)
                """
            )

    def save_entry(self, symbol: str, values: dict) -> dict:
        cleaned = _clean_trade_entry(symbol, values)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trade_journal_entries (
                    symbol,
                    trade_date,
                    action_type,
                    quantity,
                    price,
                    premium,
                    strike_price,
                    expiry_date,
                    decision_snapshot_id,
                    notes,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cleaned["symbol"],
                    cleaned["trade_date"],
                    cleaned["action_type"],
                    cleaned["quantity"],
                    cleaned["price"],
                    cleaned["premium"],
                    cleaned["strike_price"],
                    cleaned["expiry_date"],
                    cleaned["decision_snapshot_id"],
                    cleaned["notes"],
                    cleaned["created_at"],
                ),
            )
            entry_id = cursor.lastrowid
        return self.get_entry(int(entry_id)) or cleaned

    def get_entry(self, entry_id: int) -> dict | None:
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM trade_journal_entries WHERE id = ?", (entry_id,))
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return _row_to_dict(columns, row) if row else None

    def list_entries(self, symbol: str) -> list[dict]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM trade_journal_entries
                WHERE symbol = ?
                ORDER BY trade_date DESC, created_at DESC, id DESC
                """,
                (_normalize_symbol(symbol),),
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]


def _clean_decision_snapshot(symbol: str, values: dict) -> dict:
    return {
        "symbol": _normalize_symbol(symbol),
        "decision_date": _clean_date(values.get("decision_date")),
        "price": _optional_non_negative_number(values.get("price"), "price"),
        "final_action": _clean_text(values.get("final_action")),
        "decision_lane": _clean_text(values.get("decision_lane")),
        "current_add_pct": _optional_non_negative_number(values.get("current_add_pct"), "current_add_pct"),
        "max_position_pct": _optional_non_negative_number(values.get("max_position_pct"), "max_position_pct"),
        "risk_rating": _clean_text(values.get("risk_rating")),
        "data_confidence": _clean_text(values.get("data_confidence")),
        "buy_zone_status": _clean_text(values.get("buy_zone_status")),
        "block_reasons_json": _reasons_json(values.get("block_reasons", values.get("block_reasons_json"))),
        "review_reasons_json": _reasons_json(values.get("review_reasons", values.get("review_reasons_json"))),
        "reason_text": _clean_text(values.get("reason_text")),
        "source_page": _clean_text(values.get("source_page")),
        "created_at": _now(),
    }


def _clean_trade_entry(symbol: str, values: dict) -> dict:
    action_type = str(values.get("action_type") or "").strip().lower()
    if action_type not in ACTION_TYPES:
        raise ValueError("action_type is invalid")
    return {
        "symbol": _normalize_symbol(symbol),
        "trade_date": _clean_date(values.get("trade_date")),
        "action_type": action_type,
        "quantity": _optional_non_negative_number(values.get("quantity"), "quantity"),
        "price": _optional_non_negative_number(values.get("price"), "price"),
        "premium": _optional_non_negative_number(values.get("premium"), "premium"),
        "strike_price": _optional_non_negative_number(values.get("strike_price"), "strike_price"),
        "expiry_date": _clean_optional_text(values.get("expiry_date")),
        "decision_snapshot_id": _optional_int(values.get("decision_snapshot_id"), "decision_snapshot_id"),
        "notes": _clean_text(values.get("notes")),
        "created_at": _now(),
    }


def _normalize_symbol(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        raise ValueError("symbol is required")
    return normalized


def _bundle_value(bundle, *names: str):
    if isinstance(bundle, dict):
        for name in names:
            if name in bundle:
                return bundle.get(name)
        return None
    for name in names:
        if hasattr(bundle, name):
            return getattr(bundle, name)
    return None


def _bundle_list(bundle, *names: str) -> list:
    value = _bundle_value(bundle, *names)
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value]
    else:
        parsed = value
    if isinstance(parsed, tuple):
        parsed = list(parsed)
    if not isinstance(parsed, list):
        parsed = [parsed]
    return parsed


def _reason_text(block_reasons: list, review_reasons: list, bundle) -> str:
    reasons = [str(reason) for reason in [*block_reasons, *review_reasons] if str(reason).strip()]
    if reasons:
        return "; ".join(reasons)
    return _clean_text(_bundle_value(bundle, "displayCategory", "display_category"))


def _clean_date(value) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return date.today().isoformat()
    return str(value).strip()


def _optional_non_negative_number(value, field: str) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if number < 0:
        raise ValueError(f"{field} cannot be negative")
    return number


def _optional_int(value, field: str) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if number < 0:
        raise ValueError(f"{field} cannot be negative")
    return number


def _reasons_json(value) -> str:
    if value is None or value == "":
        return "[]"
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value]
    else:
        parsed = value
    if isinstance(parsed, tuple):
        parsed = list(parsed)
    if not isinstance(parsed, list):
        parsed = [parsed]
    return json.dumps(parsed)


def _clean_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_optional_text(value) -> str | None:
    text = _clean_text(value)
    return text or None


def _row_to_dict(columns: list[str], row: tuple) -> dict:
    item = dict(zip(columns, row))
    if "block_reasons_json" in item:
        item["block_reasons"] = _load_json_list(item["block_reasons_json"])
    if "review_reasons_json" in item:
        item["review_reasons"] = _load_json_list(item["review_reasons_json"])
    return item


def _load_json_list(value) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
