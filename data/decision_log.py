from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from contextlib import closing
from datetime import date, datetime, timezone
from datetime import timedelta
from pathlib import Path
from typing import Iterator

from data.market_context import build_market_history
from data.prices import CACHE_PATH
from data.trade_safety_gate import build_trade_safety_snapshot


ACTION_TYPES = {"buy", "sell", "add", "trim", "skip"}
DECISION_MOOD_TYPES = {
    "well_reasoned",
    "plan_execution",
    "fomo",
    "anxiety",
    "bottom_fishing_impulse",
    "macro_fear",
    "revenge_trade",
    "boredom_trade",
    "panic_sell",
    "regret_chase",
    "uncertainty",
}
OUTCOME_HORIZONS = {"1d": 1, "1w": 7, "1m": 30, "3m": 90, "6m": 180}
DECISION_ERROR_TAGS = {
    "valuation_too_high",
    "low_confidence_data",
    "pre_earnings_misread",
    "technical_breakdown",
    "macro_shock",
    "thesis_broken",
    "position_too_large",
    "ignored_system_warning",
}
TRADE_DISCIPLINE_COLUMNS = {
    "decision_mood": "TEXT",
    "position_class": "TEXT",
    "core_position_min_pct": "REAL",
    "trading_position_max_pct": "REAL",
    "classification_note": "TEXT",
    "target_sell_price": "REAL",
    "planned_sell_pct": "REAL",
    "actual_sell_pct": "REAL",
    "sell_reason_type": "TEXT",
    "sell_level": "TEXT",
    "thesis_broken": "INTEGER",
    "position_over_limit": "INTEGER",
    "has_reentry_plan": "INTEGER",
    "reentry_plan_text": "TEXT",
    "reentry_pullback_price": "REAL",
    "reentry_breakout_price": "REAL",
    "reentry_time_stop_days": "INTEGER",
    "reentry_buy_back_pct_on_pullback": "REAL",
    "reentry_buy_back_pct_on_breakout": "REAL",
    "reentry_thesis_invalidation": "TEXT",
    "max_allowed_sell_pct": "REAL",
    "can_sell_core": "INTEGER",
    "requires_reentry_plan": "INTEGER",
    "discipline_status": "TEXT",
    "blockers_json": "TEXT",
    "warnings_json": "TEXT",
    "reminder_text": "TEXT",
    "radar_decision": "TEXT",
    "radar_blocked": "INTEGER",
    "radar_block_reasons_json": "TEXT",
    "mood_gate_blocked": "INTEGER",
    "position_gate_blocked": "INTEGER",
    "radar_observation_only": "INTEGER",
    "gate_checked_at": "TEXT",
    "pre_trade_quantity": "REAL",
    "pre_trade_avg_cost": "REAL",
    "pre_trade_total_cost": "REAL",
    "pre_trade_position_tier": "TEXT",
    "pre_trade_target_sell_price": "REAL",
    "pre_trade_unrealized_pnl": "REAL",
    "cost_basis_source": "TEXT",
}


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
        "buy_zone_status": _clean_text(_bundle_buy_zone_status(final_decision_bundle)),
        "block_reasons_json": _reasons_json(block_reasons),
        "review_reasons_json": _reasons_json(review_reasons),
        "reason_text": _reason_text(block_reasons, review_reasons, final_decision_bundle),
        "source_page": _clean_text(source_page),
    }


def save_decision_snapshot_from_bundle(
    symbol: str,
    price,
    final_decision_bundle,
    source_page: str,
    path: Path = CACHE_PATH,
) -> dict:
    snapshot = build_decision_snapshot_from_bundle(symbol, price, final_decision_bundle, source_page)
    return DecisionLogStore(path).save_snapshot(symbol, snapshot)


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

    def list_all_snapshots(self) -> list[dict]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM decision_snapshots
                ORDER BY decision_date DESC, created_at DESC, id DESC
                """
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]

    def list_recent_snapshots(self, limit: int = 30) -> list[dict]:
        safe_limit = max(1, int(limit))
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM decision_snapshots
                ORDER BY decision_date DESC, created_at DESC, id DESC
                LIMIT ?
                """,
                (safe_limit,),
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]

    def delete_snapshot(self, snapshot_id: int) -> bool:
        clean_id = _required_int(snapshot_id, "decision_snapshot_id")
        with self.connect() as conn:
            if _table_exists(conn, "decision_error_tags"):
                conn.execute("DELETE FROM decision_error_tags WHERE decision_snapshot_id = ?", (clean_id,))
            if _table_exists(conn, "decision_outcomes"):
                conn.execute("DELETE FROM decision_outcomes WHERE decision_snapshot_id = ?", (clean_id,))
            if _table_exists(conn, "trade_journal_entries"):
                conn.execute(
                    "UPDATE trade_journal_entries SET decision_snapshot_id = NULL WHERE decision_snapshot_id = ?",
                    (clean_id,),
                )
            cursor = conn.execute("DELETE FROM decision_snapshots WHERE id = ?", (clean_id,))
        return cursor.rowcount > 0


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
            _ensure_columns(conn, "trade_journal_entries", TRADE_DISCIPLINE_COLUMNS)

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
                    decision_mood,
                    position_class,
                    core_position_min_pct,
                    trading_position_max_pct,
                    classification_note,
                    target_sell_price,
                    planned_sell_pct,
                    actual_sell_pct,
                    sell_reason_type,
                    sell_level,
                    thesis_broken,
                    position_over_limit,
                    has_reentry_plan,
                    reentry_plan_text,
                    reentry_pullback_price,
                    reentry_breakout_price,
                    reentry_time_stop_days,
                    reentry_buy_back_pct_on_pullback,
                    reentry_buy_back_pct_on_breakout,
                    reentry_thesis_invalidation,
                    max_allowed_sell_pct,
                    can_sell_core,
                    requires_reentry_plan,
                    discipline_status,
                    blockers_json,
                    warnings_json,
                    reminder_text,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    cleaned["decision_mood"],
                    cleaned["position_class"],
                    cleaned["core_position_min_pct"],
                    cleaned["trading_position_max_pct"],
                    cleaned["classification_note"],
                    cleaned["target_sell_price"],
                    cleaned["planned_sell_pct"],
                    cleaned["actual_sell_pct"],
                    cleaned["sell_reason_type"],
                    cleaned["sell_level"],
                    cleaned["thesis_broken"],
                    cleaned["position_over_limit"],
                    cleaned["has_reentry_plan"],
                    cleaned["reentry_plan_text"],
                    cleaned["reentry_pullback_price"],
                    cleaned["reentry_breakout_price"],
                    cleaned["reentry_time_stop_days"],
                    cleaned["reentry_buy_back_pct_on_pullback"],
                    cleaned["reentry_buy_back_pct_on_breakout"],
                    cleaned["reentry_thesis_invalidation"],
                    cleaned["max_allowed_sell_pct"],
                    cleaned["can_sell_core"],
                    cleaned["requires_reentry_plan"],
                    cleaned["discipline_status"],
                    cleaned["blockers_json"],
                    cleaned["warnings_json"],
                    cleaned["reminder_text"],
                    cleaned["created_at"],
                ),
            )
            entry_id = cursor.lastrowid
            _write_radar_gate_snapshot(conn, int(entry_id), cleaned)
            _write_pre_trade_snapshot(conn, int(entry_id), cleaned)
        return self.get_entry(int(entry_id)) or cleaned

    def update_entry(self, entry_id: int, symbol: str, values: dict) -> dict:
        clean_id = _required_int(entry_id, "entry_id")
        existing = self.get_entry(clean_id)
        if not existing:
            raise ValueError("trade entry not found")
        cleaned = _clean_trade_entry(symbol, values)
        if str(existing.get("action_type") or "") != cleaned["action_type"]:
            raise ValueError("历史交易类型不可修改")
        if _normalize_symbol(existing.get("symbol")) != cleaned["symbol"]:
            raise ValueError("历史交易股票不可修改")
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE trade_journal_entries
                SET
                    symbol = ?,
                    trade_date = ?,
                    action_type = ?,
                    quantity = ?,
                    price = ?,
                    premium = ?,
                    strike_price = ?,
                    expiry_date = ?,
                    decision_snapshot_id = ?,
                    notes = ?,
                    decision_mood = ?,
                    position_class = ?,
                    core_position_min_pct = ?,
                    trading_position_max_pct = ?,
                    classification_note = ?,
                    target_sell_price = ?,
                    planned_sell_pct = ?,
                    actual_sell_pct = ?,
                    sell_reason_type = ?,
                    sell_level = ?,
                    thesis_broken = ?,
                    position_over_limit = ?,
                    has_reentry_plan = ?,
                    reentry_plan_text = ?,
                    reentry_pullback_price = ?,
                    reentry_breakout_price = ?,
                    reentry_time_stop_days = ?,
                    reentry_buy_back_pct_on_pullback = ?,
                    reentry_buy_back_pct_on_breakout = ?,
                    reentry_thesis_invalidation = ?,
                    max_allowed_sell_pct = ?,
                    can_sell_core = ?,
                    requires_reentry_plan = ?,
                    discipline_status = ?,
                    blockers_json = ?,
                    warnings_json = ?,
                    reminder_text = ?
                WHERE id = ?
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
                    cleaned["decision_mood"],
                    cleaned["position_class"],
                    cleaned["core_position_min_pct"],
                    cleaned["trading_position_max_pct"],
                    cleaned["classification_note"],
                    cleaned["target_sell_price"],
                    cleaned["planned_sell_pct"],
                    cleaned["actual_sell_pct"],
                    cleaned["sell_reason_type"],
                    cleaned["sell_level"],
                    cleaned["thesis_broken"],
                    cleaned["position_over_limit"],
                    cleaned["has_reentry_plan"],
                    cleaned["reentry_plan_text"],
                    cleaned["reentry_pullback_price"],
                    cleaned["reentry_breakout_price"],
                    cleaned["reentry_time_stop_days"],
                    cleaned["reentry_buy_back_pct_on_pullback"],
                    cleaned["reentry_buy_back_pct_on_breakout"],
                    cleaned["reentry_thesis_invalidation"],
                    cleaned["max_allowed_sell_pct"],
                    cleaned["can_sell_core"],
                    cleaned["requires_reentry_plan"],
                    cleaned["discipline_status"],
                    cleaned["blockers_json"],
                    cleaned["warnings_json"],
                    cleaned["reminder_text"],
                    clean_id,
                ),
            )
            if cursor.rowcount > 0:
                _write_radar_gate_snapshot(conn, clean_id, cleaned)
                _write_pre_trade_snapshot(conn, clean_id, cleaned)
        if cursor.rowcount <= 0:
            raise ValueError("trade entry not found")
        return self.get_entry(clean_id) or cleaned

    def get_entry(self, entry_id: int) -> dict | None:
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM trade_journal_entries WHERE id = ?", (entry_id,))
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return _row_to_dict(columns, row) if row else None

    def list_entries(self, symbol: str | None = None) -> list[dict]:
        with self.connect() as conn:
            if symbol:
                cursor = conn.execute(
                    """
                    SELECT *
                    FROM trade_journal_entries
                    WHERE symbol = ?
                    ORDER BY trade_date DESC, created_at DESC, id DESC
                    """,
                    (_normalize_symbol(symbol),),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT *
                    FROM trade_journal_entries
                    ORDER BY trade_date DESC, created_at DESC, id DESC
                    """
                )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]

    def list_symbols(self) -> list[str]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT DISTINCT symbol
                FROM trade_journal_entries
                ORDER BY symbol ASC
                """
            )
            rows = cursor.fetchall()
        return [str(row[0]) for row in rows]

    def delete_entry(self, entry_id: int) -> bool:
        clean_id = _required_int(entry_id, "entry_id")
        with self.connect() as conn:
            if self._has_synced_portfolio_log(conn, clean_id):
                return False
            cursor = conn.execute(
                "DELETE FROM trade_journal_entries WHERE id = ?",
                (clean_id,),
            )
        return cursor.rowcount > 0

    def delete_entry_block_reason(self, entry_id: int) -> str:
        clean_id = _required_int(entry_id, "entry_id")
        with self.connect() as conn:
            if self._has_synced_portfolio_log(conn, clean_id):
                return "这条交易已经同步到组合持仓，不能直接删除；请用冲销/修正交易处理，避免交易日志和持仓变成两套账。"
        return ""

    def _has_synced_portfolio_log(self, conn: sqlite3.Connection, entry_id: int) -> bool:
        table = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'trade_portfolio_sync_logs'
            """
        ).fetchone()
        if not table:
            return False
        row = conn.execute(
            """
            SELECT 1
            FROM trade_portfolio_sync_logs
            WHERE entry_id = ? AND status = 'synced'
            LIMIT 1
            """,
            (entry_id,),
        ).fetchone()
        return bool(row)


def _write_radar_gate_snapshot(conn: sqlite3.Connection, entry_id: int, cleaned: dict) -> None:
    conn.execute(
        """
        UPDATE trade_journal_entries
        SET
            radar_decision = ?,
            radar_blocked = ?,
            radar_block_reasons_json = ?,
            mood_gate_blocked = ?,
            position_gate_blocked = ?,
            radar_observation_only = ?,
            gate_checked_at = ?
        WHERE id = ?
        """,
        (
            cleaned["radar_decision"],
            cleaned["radar_blocked"],
            cleaned["radar_block_reasons_json"],
            cleaned["mood_gate_blocked"],
            cleaned["position_gate_blocked"],
            cleaned["radar_observation_only"],
            cleaned["gate_checked_at"],
            entry_id,
        ),
    )


def _write_pre_trade_snapshot(conn: sqlite3.Connection, entry_id: int, cleaned: dict) -> None:
    if not any(
        cleaned.get(field) not in {None, ""}
        for field in (
            "pre_trade_quantity",
            "pre_trade_avg_cost",
            "pre_trade_total_cost",
            "pre_trade_position_tier",
            "pre_trade_target_sell_price",
            "pre_trade_unrealized_pnl",
            "cost_basis_source",
        )
    ):
        return
    conn.execute(
        """
        UPDATE trade_journal_entries
        SET
            pre_trade_quantity = ?,
            pre_trade_avg_cost = ?,
            pre_trade_total_cost = ?,
            pre_trade_position_tier = ?,
            pre_trade_target_sell_price = ?,
            pre_trade_unrealized_pnl = ?,
            cost_basis_source = ?
        WHERE id = ?
        """,
        (
            cleaned["pre_trade_quantity"],
            cleaned["pre_trade_avg_cost"],
            cleaned["pre_trade_total_cost"],
            cleaned["pre_trade_position_tier"],
            cleaned["pre_trade_target_sell_price"],
            cleaned["pre_trade_unrealized_pnl"],
            cleaned["cost_basis_source"],
            entry_id,
        ),
    )


class DecisionOutcomeStore:
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
                CREATE TABLE IF NOT EXISTS decision_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_snapshot_id INTEGER NOT NULL,
                    horizon TEXT NOT NULL,
                    start_price REAL,
                    end_price REAL,
                    return_pct REAL,
                    max_drawdown_pct REAL,
                    status TEXT NOT NULL DEFAULT 'missing',
                    created_at TEXT NOT NULL,
                    UNIQUE(decision_snapshot_id, horizon),
                    FOREIGN KEY(decision_snapshot_id) REFERENCES decision_snapshots(id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_decision_outcomes_snapshot
                ON decision_outcomes(decision_snapshot_id)
                """
            )

    def calculate_and_save_outcomes(self, decision_snapshot_id: int) -> list[dict]:
        snapshot = DecisionLogStore(self.path).get_snapshot(decision_snapshot_id)
        if not snapshot:
            return []
        outcomes = build_decision_outcomes_from_price_history(snapshot, self.path)
        return [self.save_outcome(decision_snapshot_id, outcome["horizon"], outcome) for outcome in outcomes]

    def save_outcome(self, decision_snapshot_id: int, horizon: str, values: dict) -> dict:
        cleaned = _clean_decision_outcome(decision_snapshot_id, horizon, values)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO decision_outcomes (
                    decision_snapshot_id,
                    horizon,
                    start_price,
                    end_price,
                    return_pct,
                    max_drawdown_pct,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_snapshot_id, horizon) DO UPDATE SET
                    start_price = excluded.start_price,
                    end_price = excluded.end_price,
                    return_pct = excluded.return_pct,
                    max_drawdown_pct = excluded.max_drawdown_pct,
                    status = excluded.status,
                    created_at = excluded.created_at
                """,
                (
                    cleaned["decision_snapshot_id"],
                    cleaned["horizon"],
                    cleaned["start_price"],
                    cleaned["end_price"],
                    cleaned["return_pct"],
                    cleaned["max_drawdown_pct"],
                    cleaned["status"],
                    cleaned["created_at"],
                ),
            )
        return self.get_outcome(decision_snapshot_id, horizon) or cleaned

    def get_outcome(self, decision_snapshot_id: int, horizon: str) -> dict | None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM decision_outcomes
                WHERE decision_snapshot_id = ?
                  AND horizon = ?
                """,
                (decision_snapshot_id, _clean_horizon(horizon)),
            )
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return _row_to_dict(columns, row) if row else None

    def list_outcomes(self, decision_snapshot_id: int) -> list[dict]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM decision_outcomes
                WHERE decision_snapshot_id = ?
                ORDER BY CASE horizon
                    WHEN '1d' THEN 1
                    WHEN '1w' THEN 2
                    WHEN '1m' THEN 3
                    WHEN '3m' THEN 4
                    WHEN '6m' THEN 5
                    ELSE 99
                END
                """,
                (decision_snapshot_id,),
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]


class DecisionErrorTagStore:
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
                CREATE TABLE IF NOT EXISTS decision_error_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_snapshot_id INTEGER NOT NULL,
                    tag TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(decision_snapshot_id, tag),
                    FOREIGN KEY(decision_snapshot_id) REFERENCES decision_snapshots(id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_decision_error_tags_snapshot
                ON decision_error_tags(decision_snapshot_id)
                """
            )

    def save_tag(self, decision_snapshot_id: int, tag: str, notes: str | None = None) -> dict:
        cleaned = _clean_error_tag(decision_snapshot_id, tag, notes)
        now = _now()
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT created_at
                FROM decision_error_tags
                WHERE decision_snapshot_id = ?
                  AND tag = ?
                """,
                (cleaned["decision_snapshot_id"], cleaned["tag"]),
            ).fetchone()
            created_at = existing[0] if existing and existing[0] else now
            conn.execute(
                """
                INSERT INTO decision_error_tags (
                    decision_snapshot_id,
                    tag,
                    notes,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(decision_snapshot_id, tag) DO UPDATE SET
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    cleaned["decision_snapshot_id"],
                    cleaned["tag"],
                    cleaned["notes"],
                    created_at,
                    now,
                ),
            )
        return self.get_tag(cleaned["decision_snapshot_id"], cleaned["tag"]) or cleaned

    def get_tag(self, decision_snapshot_id: int, tag: str) -> dict | None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM decision_error_tags
                WHERE decision_snapshot_id = ?
                  AND tag = ?
                """,
                (_required_int(decision_snapshot_id, "decision_snapshot_id"), _clean_error_tag_name(tag)),
            )
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return _row_to_dict(columns, row) if row else None

    def list_tags_for_snapshot(self, decision_snapshot_id: int) -> list[dict]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM decision_error_tags
                WHERE decision_snapshot_id = ?
                ORDER BY tag ASC
                """,
                (_required_int(decision_snapshot_id, "decision_snapshot_id"),),
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]

    def list_tags_for_symbol(self, symbol: str) -> list[dict]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT tags.*, snapshots.symbol, snapshots.decision_date
                FROM decision_error_tags AS tags
                JOIN decision_snapshots AS snapshots
                  ON snapshots.id = tags.decision_snapshot_id
                WHERE snapshots.symbol = ?
                ORDER BY snapshots.decision_date DESC, tags.tag ASC
                """,
                (_normalize_symbol(symbol),),
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]

    def tag_counts(self) -> list[dict]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT tag, COUNT(*) AS count
                FROM decision_error_tags
                GROUP BY tag
                ORDER BY count DESC, tag ASC
                """
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]

    def recent_tags(self, limit: int = 5) -> list[dict]:
        safe_limit = max(1, int(limit))
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT
                    tags.*,
                    snapshots.symbol,
                    snapshots.decision_date,
                    snapshots.final_action,
                    snapshots.decision_lane
                FROM decision_error_tags AS tags
                JOIN decision_snapshots AS snapshots
                  ON snapshots.id = tags.decision_snapshot_id
                ORDER BY tags.updated_at DESC, tags.created_at DESC, tags.id DESC
                LIMIT ?
                """,
                (safe_limit,),
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]

    def delete_tag(self, decision_snapshot_id: int, tag: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM decision_error_tags
                WHERE decision_snapshot_id = ?
                  AND tag = ?
                """,
                (_required_int(decision_snapshot_id, "decision_snapshot_id"), _clean_error_tag_name(tag)),
            )
        return cursor.rowcount > 0


def build_decision_outcomes_from_price_history(snapshot: dict, path: Path = CACHE_PATH) -> list[dict]:
    return [_build_outcome_for_horizon(snapshot, horizon, days, path) for horizon, days in OUTCOME_HORIZONS.items()]


def refresh_decision_outcomes(path: Path = CACHE_PATH) -> dict:
    decision_store = DecisionLogStore(path)
    outcome_store = DecisionOutcomeStore(path)
    snapshots = decision_store.list_all_snapshots()
    refreshed_count = 0
    missing_count = 0
    for snapshot in snapshots:
        outcomes = outcome_store.calculate_and_save_outcomes(int(snapshot["id"]))
        refreshed_count += len(outcomes)
        missing_count += sum(1 for outcome in outcomes if outcome.get("status") == "missing")
    return {
        "snapshotCount": len(snapshots),
        "outcomeCount": refreshed_count,
        "missingCount": missing_count,
    }


def build_decision_signal_stats(path: Path = CACHE_PATH) -> dict:
    rows = _decision_stat_rows(path)
    error_rows = _decision_error_stat_rows(path)
    return {
        "horizons": list(OUTCOME_HORIZONS),
        "errorTags": {
            "counts": _decision_error_tag_counts(path),
        },
        "byHorizon": {
            horizon: {
                "summary": _overall_signal_stats(rows, horizon),
                "byFinalAction": _group_signal_stats(rows, horizon, "final_action"),
                "byDecisionLane": _group_signal_stats(rows, horizon, "decision_lane"),
                "byErrorTag": _group_signal_stats(error_rows, horizon, "error_tag"),
                "byFinalActionErrorTag": _cross_signal_stats(
                    error_rows,
                    horizon,
                    "final_action",
                    "error_tag",
                    "finalAction",
                    "errorTag",
                ),
                "byDecisionLaneErrorTag": _cross_signal_stats(
                    error_rows,
                    horizon,
                    "decision_lane",
                    "error_tag",
                    "decisionLane",
                    "errorTag",
                ),
            }
            for horizon in OUTCOME_HORIZONS
        },
    }


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
    cleaned = {
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
        "decision_mood": _clean_decision_mood(_value(values, "decisionMood", "decision_mood")),
        "target_sell_price": _optional_non_negative_number(_value(values, "targetSellPrice", "target_sell_price"), "target_sell_price"),
        "created_at": _clean_optional_text(_value(values, "createdAt", "created_at")) or _now(),
    }
    cleaned.update(_clean_pre_trade_snapshot(values))
    cleaned.update(_clean_trade_discipline_snapshot(cleaned["symbol"], action_type, values))
    cleaned.update(_clean_radar_gate_snapshot(action_type, values))
    return cleaned


def _clean_pre_trade_snapshot(values: dict) -> dict:
    position_tier = _clean_optional_text(_value(values, "preTradePositionTier", "pre_trade_position_tier"))
    if position_tier:
        position_tier = position_tier.upper()
        if position_tier not in {"A", "B", "C"}:
            position_tier = None
    return {
        "pre_trade_quantity": _optional_non_negative_number(
            _value(values, "preTradeQuantity", "pre_trade_quantity"),
            "pre_trade_quantity",
        ),
        "pre_trade_avg_cost": _optional_non_negative_number(
            _value(values, "preTradeAvgCost", "pre_trade_avg_cost"),
            "pre_trade_avg_cost",
        ),
        "pre_trade_total_cost": _optional_non_negative_number(
            _value(values, "preTradeTotalCost", "pre_trade_total_cost"),
            "pre_trade_total_cost",
        ),
        "pre_trade_position_tier": position_tier,
        "pre_trade_target_sell_price": _optional_non_negative_number(
            _value(values, "preTradeTargetSellPrice", "pre_trade_target_sell_price"),
            "pre_trade_target_sell_price",
        ),
        "pre_trade_unrealized_pnl": _optional_number(
            _value(values, "preTradeUnrealizedPnl", "pre_trade_unrealized_pnl"),
            "pre_trade_unrealized_pnl",
        ),
        "cost_basis_source": _clean_optional_text(_value(values, "costBasisSource", "cost_basis_source")),
    }


def _clean_trade_discipline_snapshot(symbol: str, action_type: str, values: dict) -> dict:
    return build_trade_safety_snapshot(symbol, action_type, values)


def _clean_radar_gate_snapshot(action_type: str, values: dict) -> dict:
    if action_type not in {"buy", "add"}:
        return {
            "radar_decision": None,
            "radar_blocked": False,
            "radar_block_reasons_json": "[]",
            "mood_gate_blocked": False,
            "position_gate_blocked": False,
            "radar_observation_only": False,
            "gate_checked_at": None,
        }
    has_gate_snapshot = any(
        name in values
        for name in (
            "radarDecision",
            "radar_decision",
            "radarBlocked",
            "radar_blocked",
            "gateCheckedAt",
            "gate_checked_at",
        )
    )
    if not has_gate_snapshot:
        return {
            "radar_decision": "DATA_MISSING",
            "radar_blocked": True,
            "radar_block_reasons_json": _reasons_json(["Radar 买入门禁结果缺失，禁止自动同步组合持仓。"]),
            "mood_gate_blocked": False,
            "position_gate_blocked": False,
            "radar_observation_only": False,
            "gate_checked_at": _now(),
        }
    return {
        "radar_decision": _clean_optional_text(_value(values, "radarDecision", "radar_decision")),
        "radar_blocked": _clean_bool(_value(values, "radarBlocked", "radar_blocked")),
        "radar_block_reasons_json": _reasons_json(_value(values, "radarBlockReasons", "radar_block_reasons", "radar_block_reasons_json")),
        "mood_gate_blocked": _clean_bool(_value(values, "moodGateBlocked", "mood_gate_blocked")),
        "position_gate_blocked": _clean_bool(_value(values, "positionGateBlocked", "position_gate_blocked")),
        "radar_observation_only": _clean_bool(_value(values, "radarObservationOnly", "radar_observation_only")),
        "gate_checked_at": _clean_optional_text(_value(values, "gateCheckedAt", "gate_checked_at")),
    }


def _clean_decision_mood(value: object) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    if text not in DECISION_MOOD_TYPES:
        raise ValueError("decision_mood is invalid")
    return text


def _clean_decision_outcome(decision_snapshot_id: int, horizon: str, values: dict) -> dict:
    return {
        "decision_snapshot_id": _required_int(decision_snapshot_id, "decision_snapshot_id"),
        "horizon": _clean_horizon(horizon),
        "start_price": _optional_non_negative_number(values.get("start_price", values.get("startPrice")), "start_price"),
        "end_price": _optional_non_negative_number(values.get("end_price", values.get("endPrice")), "end_price"),
        "return_pct": _optional_number(values.get("return_pct", values.get("returnPct")), "return_pct"),
        "max_drawdown_pct": _optional_number(
            values.get("max_drawdown_pct", values.get("maxDrawdownPct")),
            "max_drawdown_pct",
        ),
        "status": _clean_outcome_status(values.get("status")),
        "created_at": _now(),
    }


def _clean_error_tag(decision_snapshot_id: int, tag: str, notes: str | None) -> dict:
    return {
        "decision_snapshot_id": _required_int(decision_snapshot_id, "decision_snapshot_id"),
        "tag": _clean_error_tag_name(tag),
        "notes": _clean_text(notes),
    }


def _clean_error_tag_name(value: str) -> str:
    tag = str(value or "").strip().lower()
    if tag not in DECISION_ERROR_TAGS:
        raise ValueError("tag is invalid")
    return tag


def _build_outcome_for_horizon(snapshot: dict, horizon: str, days: int, path: Path) -> dict:
    start_price = _optional_non_negative_number(snapshot.get("price"), "price")
    decision_date = _parse_date(snapshot.get("decision_date"))
    if start_price is None or decision_date is None:
        return _missing_outcome(horizon, start_price)

    end_date = decision_date + timedelta(days=days)
    closes = _history_closes(path, str(snapshot.get("symbol") or ""), decision_date, end_date)
    if not closes:
        return _missing_outcome(horizon, start_price)

    end_price = closes[-1]
    return_pct = (end_price - start_price) / start_price * 100 if start_price > 0 else None
    max_drawdown_pct = _max_drawdown_pct(start_price, closes)
    return {
        "horizon": horizon,
        "start_price": start_price,
        "end_price": end_price,
        "return_pct": return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "status": "complete",
    }


def _missing_outcome(horizon: str, start_price: float | None) -> dict:
    return {
        "horizon": horizon,
        "start_price": start_price,
        "end_price": None,
        "return_pct": None,
        "max_drawdown_pct": None,
        "status": "missing",
    }


def _decision_stat_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as conn:
        if not _table_exists(conn, "decision_snapshots"):
            return []
        if not _table_exists(conn, "decision_outcomes"):
            return []
        cursor = conn.execute(
            """
            WITH horizons(horizon) AS (
                VALUES ('1d'), ('1w'), ('1m'), ('3m'), ('6m')
            )
            SELECT
                snapshots.id AS decision_snapshot_id,
                snapshots.final_action,
                snapshots.decision_lane,
                horizons.horizon,
                outcomes.status,
                outcomes.return_pct,
                outcomes.max_drawdown_pct
            FROM decision_snapshots AS snapshots
            CROSS JOIN horizons
            LEFT JOIN decision_outcomes AS outcomes
              ON outcomes.decision_snapshot_id = snapshots.id
             AND outcomes.horizon = horizons.horizon
            """
        )
        columns = [description[0] for description in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
    return [_row_to_dict(columns, row) for row in rows]


def _decision_error_stat_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as conn:
        if not _table_exists(conn, "decision_snapshots") or not _table_exists(conn, "decision_error_tags"):
            return []
        has_outcomes = _table_exists(conn, "decision_outcomes")
        outcome_join = (
            """
            LEFT JOIN decision_outcomes AS outcomes
              ON outcomes.decision_snapshot_id = snapshots.id
             AND outcomes.horizon = horizons.horizon
            """
            if has_outcomes
            else ""
        )
        outcome_select = (
            """
                outcomes.status,
                outcomes.return_pct,
                outcomes.max_drawdown_pct
            """
            if has_outcomes
            else """
                NULL AS status,
                NULL AS return_pct,
                NULL AS max_drawdown_pct
            """
        )
        cursor = conn.execute(
            f"""
            WITH horizons(horizon) AS (
                VALUES ('1d'), ('1w'), ('1m'), ('3m'), ('6m')
            )
            SELECT
                snapshots.id AS decision_snapshot_id,
                snapshots.final_action,
                snapshots.decision_lane,
                tags.tag AS error_tag,
                horizons.horizon,
                {outcome_select}
            FROM decision_error_tags AS tags
            JOIN decision_snapshots AS snapshots
              ON snapshots.id = tags.decision_snapshot_id
            CROSS JOIN horizons
            {outcome_join}
            """
        )
        columns = [description[0] for description in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
    return [_row_to_dict(columns, row) for row in rows]


def _decision_error_tag_counts(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as conn:
        if not _table_exists(conn, "decision_error_tags"):
            return []
        cursor = conn.execute(
            """
            SELECT tag, COUNT(*) AS count
            FROM decision_error_tags
            GROUP BY tag
            ORDER BY count DESC, tag ASC
            """
        )
        columns = [description[0] for description in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
    return [_row_to_dict(columns, row) for row in rows]


def _group_signal_stats(rows: list[dict], horizon: str, field: str) -> list[dict]:
    groups: dict[str, dict] = {}
    for row in rows:
        label = _clean_text(row.get(field)) or "unknown"
        group = groups.setdefault(label, {"total": 0, "missing": 0, "returns": [], "drawdowns": []})
        if row.get("horizon") != horizon:
            continue
        group["total"] += 1
        if row.get("status") != "complete" or row.get("return_pct") is None:
            group["missing"] += 1
            continue
        group["returns"].append(float(row["return_pct"]))
        if row.get("max_drawdown_pct") is not None:
            group["drawdowns"].append(float(row["max_drawdown_pct"]))

    return [_signal_stats_row(label, values) for label, values in sorted(groups.items()) if values["total"] > 0]


def _cross_signal_stats(
    rows: list[dict],
    horizon: str,
    left_field: str,
    right_field: str,
    left_output: str,
    right_output: str,
) -> list[dict]:
    groups: dict[tuple[str, str], dict] = {}
    for row in rows:
        if row.get("horizon") != horizon:
            continue
        left = _clean_text(row.get(left_field)) or "unknown"
        right = _clean_text(row.get(right_field)) or "unknown"
        group = groups.setdefault((left, right), {"total": 0, "missing": 0, "returns": [], "drawdowns": []})
        group["total"] += 1
        if row.get("status") != "complete" or row.get("return_pct") is None:
            group["missing"] += 1
            continue
        group["returns"].append(float(row["return_pct"]))
        if row.get("max_drawdown_pct") is not None:
            group["drawdowns"].append(float(row["max_drawdown_pct"]))

    results = []
    for (left, right), values in sorted(groups.items()):
        row = _signal_stats_row(f"{left} / {right}", values)
        row[left_output] = left
        row[right_output] = right
        results.append(row)
    return results


def _overall_signal_stats(rows: list[dict], horizon: str) -> dict:
    values = {"total": 0, "missing": 0, "returns": [], "drawdowns": []}
    for row in rows:
        if row.get("horizon") != horizon:
            continue
        values["total"] += 1
        if row.get("status") != "complete" or row.get("return_pct") is None:
            values["missing"] += 1
            continue
        values["returns"].append(float(row["return_pct"]))
        if row.get("max_drawdown_pct") is not None:
            values["drawdowns"].append(float(row["max_drawdown_pct"]))
    return _signal_stats_row("overall", values)


def _signal_stats_row(label: str, values: dict) -> dict:
    returns = values["returns"]
    drawdowns = values["drawdowns"]
    sample_count = len(returns)
    wins = sum(1 for value in returns if value > 0)
    return {
        "group": label,
        "sampleCount": sample_count,
        "missingCount": values["missing"],
        "totalCount": values["total"],
        "winRate": wins / sample_count * 100 if sample_count else None,
        "averageReturnPct": _average(returns),
        "medianReturnPct": _median(returns),
        "averageMaxDrawdownPct": _average(drawdowns),
    }


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


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


def _bundle_buy_zone_status(bundle) -> str:
    if _bundle_value(bundle, "executionSource", "execution_source") == "finalDecisionBundle":
        return _clean_text(_bundle_value(bundle, "displayCategory", "display_category"))
    return _clean_text(
        _bundle_value(
            bundle,
            "buyZoneStatus",
            "buy_zone_status",
            "displayCategory",
            "display_category",
        )
    )


def _reason_text(block_reasons: list, review_reasons: list, bundle) -> str:
    reasons = [str(reason) for reason in [*block_reasons, *review_reasons] if str(reason).strip()]
    if reasons:
        return "; ".join(reasons)
    return _clean_text(_bundle_value(bundle, "displayCategory", "display_category"))


def _clean_date(value) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return date.today().isoformat()
    return str(value).strip()


def _parse_date(value) -> date | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _clean_horizon(value) -> str:
    horizon = str(value or "").strip().lower()
    if horizon not in OUTCOME_HORIZONS:
        raise ValueError("horizon is invalid")
    return horizon


def _clean_outcome_status(value) -> str:
    status = str(value or "missing").strip().lower()
    if status not in {"complete", "missing"}:
        raise ValueError("status is invalid")
    return status


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


def _optional_ratio(value, field: str) -> float | None:
    number = _optional_non_negative_number(value, field)
    if number is None:
        return None
    return number / 100 if number > 1 else number


def _clean_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _value(values: dict, *names: str):
    for name in names:
        if name in values:
            return values.get(name)
    return None


def _optional_number(value, field: str) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc


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


def _required_int(value, field: str) -> int:
    number = _optional_int(value, field)
    if number is None:
        raise ValueError(f"{field} is required")
    return number


def _history_closes(path: Path, symbol: str, start_date: date, end_date: date) -> list[float]:
    history = build_market_history(symbol, path=path)
    if history.empty or "date" not in history or "close" not in history:
        return []
    closes: list[float] = []
    for row in history.itertuples(index=False):
        history_date = _history_row_date(getattr(row, "date", None))
        if history_date is None or history_date <= start_date or history_date > end_date:
            continue
        price = _optional_non_negative_number(getattr(row, "close", None), "close")
        if price is not None:
            closes.append(price)
    return closes


def _history_row_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _max_drawdown_pct(start_price: float, closes: list[float]) -> float | None:
    if start_price <= 0:
        return None
    peak = start_price
    max_drawdown = 0.0
    for close in closes:
        if close > peak:
            peak = close
        if peak > 0:
            drawdown = (close - peak) / peak * 100
            if drawdown < max_drawdown:
                max_drawdown = drawdown
    return max_drawdown


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _ensure_columns(conn: sqlite3.Connection, table_name: str, columns: dict[str, str]) -> None:
    existing = {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column, definition in columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column} {definition}")


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


def _clean_position_class(value) -> str:
    text = _clean_text(value).upper()
    return text if text in {"A", "B", "C"} else ""


def _clean_optional_text(value) -> str | None:
    text = _clean_text(value)
    return text or None


def _row_to_dict(columns: list[str], row: tuple) -> dict:
    item = dict(zip(columns, row))
    if "block_reasons_json" in item:
        item["block_reasons"] = _load_json_list(item["block_reasons_json"])
    if "review_reasons_json" in item:
        item["review_reasons"] = _load_json_list(item["review_reasons_json"])
    if "blockers_json" in item:
        item["blockers"] = _load_json_list(item["blockers_json"])
    if "warnings_json" in item:
        item["warnings"] = _load_json_list(item["warnings_json"])
    if "radar_block_reasons_json" in item:
        item["radar_block_reasons"] = _load_json_list(item["radar_block_reasons_json"])
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
