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
from data.portfolio_roles import (
    normalize_portfolio_role,
    portfolio_role_core_tactical_split,
    portfolio_role_label,
    portfolio_role_target_weight,
)
from data.trade_activity import build_daily_trade_activity, snapshot_json
from data.trade_safety_gate import build_trade_safety_snapshot


ACTION_TYPES = {"buy", "sell", "add", "trim", "skip"}
DECISION_MOOD_TYPES = {
    "NEUTRAL",
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
VALIDATION_FIELD_LABELS = {
    "close": "收盘价",
    "current_add_pct": "当前可加仓比例",
    "decision_snapshot_id": "决策快照",
    "end_price": "结束价格",
    "entry_id": "交易记录",
    "max_drawdown_pct": "最大回撤",
    "max_position_pct": "最大仓位",
    "plan_age_minutes": "计划更新时间",
    "plan_max_position_pct": "计划最大仓位",
    "plan_planned_quantity": "计划数量",
    "plan_remaining_quantity": "计划剩余数量",
    "plan_trigger_price": "计划触发价",
    "pre_trade_avg_cost": "交易前平均成本",
    "pre_trade_quantity": "交易前股数",
    "pre_trade_target_sell_price": "交易前目标卖出价",
    "pre_trade_total_cost": "交易前总成本",
    "pre_trade_unrealized_pnl": "交易前浮动盈亏",
    "premium": "权利金",
    "price": "当前价格",
    "quantity": "数量",
    "return_pct": "收益率",
    "setup_score": "结构评分",
    "starter_max_pct": "起步仓上限",
    "starter_position_after_pct": "起步仓后仓位",
    "starter_position_before_pct": "起步仓前仓位",
    "start_price": "开始价格",
    "strike_price": "行权价",
    "structure_score": "结构评分",
    "target_sell_price": "目标卖出价",
    "volume_ma20": "20日均量",
    "volume_price_score": "量价承接评分",
    "volume_ratio": "量比",
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
    "sell_context_type": "TEXT",
    "sell_reason_tags": "TEXT",
    "fundamental_change_type": "TEXT",
    "valuation_compression_reason": "TEXT",
    "liquidity_shock_reason": "TEXT",
    "position_risk_reason": "TEXT",
    "sell_thesis_note": "TEXT",
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
    "radar_data_status": "TEXT",
    "radar_is_stale": "INTEGER",
    "radar_blocked": "INTEGER",
    "radar_block_reasons_json": "TEXT",
    "gate_hard_blocked": "INTEGER",
    "radar_advisory_only": "INTEGER",
    "radar_advisory_warnings_json": "TEXT",
    "price_position": "TEXT",
    "entry_display_label": "TEXT",
    "entry_action_hint": "TEXT",
    "entry_display_reason": "TEXT",
    "buy_zone_snapshot_json": "TEXT",
    "buy_zone_context_json": "TEXT",
    "setup_score": "REAL",
    "buy_zone_action": "TEXT",
    "buy_zone_action_text": "TEXT",
    "primary_zone_text": "TEXT",
    "technical_entry_zone_json": "TEXT",
    "deep_valuation_zone_json": "TEXT",
    "chase_above_price": "REAL",
    "mood_gate_blocked": "INTEGER",
    "position_gate_blocked": "INTEGER",
    "radar_observation_only": "INTEGER",
    "gate_checked_at": "TEXT",
    "entry_mode": "TEXT",
    "buy_plan_id": "TEXT",
    "buy_plan_level": "TEXT",
    "planned_ladder_buy": "INTEGER",
    "plan_trigger_price": "REAL",
    "plan_planned_quantity": "REAL",
    "plan_remaining_quantity": "REAL",
    "plan_max_position_pct": "REAL",
    "plan_match_status": "TEXT",
    "plan_block_reasons_json": "TEXT",
    "fresh_plan_execution": "INTEGER",
    "plan_age_minutes": "REAL",
    "plan_recently_created_or_modified": "INTEGER",
    "starter_position": "INTEGER",
    "starter_max_pct": "REAL",
    "starter_position_before_pct": "REAL",
    "starter_position_after_pct": "REAL",
    "starter_match_status": "TEXT",
    "starter_block_reasons_json": "TEXT",
    "pre_trade_quantity": "REAL",
    "pre_trade_avg_cost": "REAL",
    "pre_trade_total_cost": "REAL",
    "pre_trade_position_tier": "TEXT",
    "pre_trade_target_sell_price": "REAL",
    "pre_trade_unrealized_pnl": "REAL",
    "cost_basis_source": "TEXT",
    "sell_context_snapshot_json": "TEXT",
    "structure_status": "TEXT",
    "structure_score": "REAL",
    "structure_reasons_json": "TEXT",
    "structure_warnings_json": "TEXT",
    "structure_checked_at": "TEXT",
    "acceptance_status": "TEXT",
    "acceptance_score": "REAL",
    "acceptance_reasons_json": "TEXT",
    "acceptance_warnings_json": "TEXT",
    "acceptance_checked_at": "TEXT",
    "volume_price_status": "TEXT",
    "volume_price_score": "REAL",
    "volume_ratio": "REAL",
    "volume_ma20": "REAL",
    "volume_regime_cn": "TEXT",
    "close_position": "REAL",
    "candle_signal_cn": "TEXT",
    "volume_signal_cn": "TEXT",
    "support_signal_cn": "TEXT",
    "confirmation_signal_cn": "TEXT",
    "distribution_count_10d": "INTEGER",
    "volume_price_reason_cn": "TEXT",
    "volume_price_zone_source": "TEXT",
    "volume_price_checked_at": "TEXT",
    "buy_advisory_warnings_json": "TEXT",
    "buy_advisory_acknowledged": "INTEGER",
    "advisory_level": "TEXT",
    "advisory_text": "TEXT",
    "advisory_reasons_json": "TEXT",
    "user_confirmed_advisory": "INTEGER",
    "validation_passed": "INTEGER",
    "can_submit": "INTEGER",
    "advisory_checked_at": "TEXT",
    "macro_regime": "TEXT",
    "portfolio_structure_status": "TEXT",
    "advisory_action": "TEXT",
    "risk_warning_cn": "TEXT",
    "user_override": "INTEGER",
    "override_reason": "TEXT",
    "action_fusion_action": "TEXT",
    "left_side_action_cn": "TEXT",
    "position_status": "TEXT",
    "sell_warning_level": "TEXT",
    "sell_warning_text": "TEXT",
    "sell_warning_reasons_json": "TEXT",
    "sell_review_required": "INTEGER",
    "sell_blocked": "INTEGER",
    "user_confirmed_sell_warning": "INTEGER",
    "buy_zone_display_json": "TEXT",
    "final_decision_snapshot_json": "TEXT",
    "daily_trade_record_count": "INTEGER",
    "daily_trade_decision_count": "INTEGER",
    "daily_trade_advisory_level": "TEXT",
    "daily_trade_advisory_text": "TEXT",
    "daily_trade_advisory_reasons_json": "TEXT",
    "daily_trade_activity_snapshot_json": "TEXT",
    "user_confirmed_daily_trade_advisory": "INTEGER",
    "trade_role": "TEXT",
    "role_label": "TEXT",
    "role_target_weight": "TEXT",
    "core_tactical_split": "TEXT",
    "role_reason": "TEXT",
}


def build_decision_snapshot_from_bundle(
    symbol: str,
    price,
    final_decision_bundle,
    source_page: str,
    *,
    buy_zone_context: dict | None = None,
    buy_zone_display: dict | None = None,
) -> dict:
    block_reasons = _bundle_list(final_decision_bundle, "blockReasons", "block_reasons")
    review_reasons = _bundle_list(final_decision_bundle, "reviewReasons", "review_reasons")
    context_snapshot = buy_zone_context if buy_zone_context is not None else _bundle_value(final_decision_bundle, "buyZoneContext", "buy_zone_context")
    display_snapshot = buy_zone_display if buy_zone_display is not None else _bundle_value(final_decision_bundle, "buyZoneDisplay", "buy_zone_display")
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
        "buy_zone_context_json": _dict_json(context_snapshot),
        "buy_zone_display_json": _dict_json(display_snapshot),
    }


def save_decision_snapshot_from_bundle(
    symbol: str,
    price,
    final_decision_bundle,
    source_page: str,
    path: Path = CACHE_PATH,
    *,
    buy_zone_context: dict | None = None,
    buy_zone_display: dict | None = None,
) -> dict:
    snapshot = build_decision_snapshot_from_bundle(
        symbol,
        price,
        final_decision_bundle,
        source_page,
        buy_zone_context=buy_zone_context,
        buy_zone_display=buy_zone_display,
    )
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
            _ensure_columns(
                conn,
                "decision_snapshots",
                {
                    "buy_zone_context_json": "TEXT",
                    "buy_zone_display_json": "TEXT",
                },
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
                    buy_zone_context_json,
                    buy_zone_display_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    cleaned["buy_zone_context_json"],
                    cleaned["buy_zone_display_json"],
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
                    sell_context_type,
                    sell_reason_tags,
                    fundamental_change_type,
                    valuation_compression_reason,
                    liquidity_shock_reason,
                    position_risk_reason,
                    sell_thesis_note,
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    cleaned["sell_context_type"],
                    cleaned["sell_reason_tags"],
                    cleaned["fundamental_change_type"],
                    cleaned["valuation_compression_reason"],
                    cleaned["liquidity_shock_reason"],
                    cleaned["position_risk_reason"],
                    cleaned["sell_thesis_note"],
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
            _write_buy_plan_snapshot(conn, int(entry_id), cleaned)
            _write_starter_snapshot(conn, int(entry_id), cleaned)
            _write_pre_trade_snapshot(conn, int(entry_id), cleaned)
            _write_sell_context_snapshot(conn, int(entry_id), cleaned)
            _write_structure_entry_snapshot(conn, int(entry_id), cleaned)
            _write_pullback_acceptance_snapshot(conn, int(entry_id), cleaned)
            _write_volume_price_acceptance_snapshot(conn, int(entry_id), cleaned)
            _write_buy_advisory_snapshot(conn, int(entry_id), cleaned)
            _write_sell_advisory_snapshot(conn, int(entry_id), cleaned)
            _write_daily_trade_activity_snapshot(conn, int(entry_id), cleaned)
            _write_trade_role_snapshot(conn, int(entry_id), cleaned)
        return self.get_entry(int(entry_id)) or cleaned

    def update_entry(self, entry_id: int, symbol: str, values: dict) -> dict:
        clean_id = _required_int(entry_id, "entry_id")
        existing = self.get_entry(clean_id)
        if not existing:
            raise ValueError("交易日志记录不存在")
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
                    sell_context_type = ?,
                    sell_reason_tags = ?,
                    fundamental_change_type = ?,
                    valuation_compression_reason = ?,
                    liquidity_shock_reason = ?,
                    position_risk_reason = ?,
                    sell_thesis_note = ?,
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
                    cleaned["sell_context_type"],
                    cleaned["sell_reason_tags"],
                    cleaned["fundamental_change_type"],
                    cleaned["valuation_compression_reason"],
                    cleaned["liquidity_shock_reason"],
                    cleaned["position_risk_reason"],
                    cleaned["sell_thesis_note"],
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
                _write_buy_plan_snapshot(conn, clean_id, cleaned)
                _write_starter_snapshot(conn, clean_id, cleaned)
                _write_pre_trade_snapshot(conn, clean_id, cleaned)
                _write_sell_context_snapshot(conn, clean_id, cleaned)
                _write_structure_entry_snapshot(conn, clean_id, cleaned)
                _write_pullback_acceptance_snapshot(conn, clean_id, cleaned)
                _write_volume_price_acceptance_snapshot(conn, clean_id, cleaned)
            _write_buy_advisory_snapshot(conn, clean_id, cleaned)
            _write_sell_advisory_snapshot(conn, clean_id, cleaned)
            _write_daily_trade_activity_snapshot(conn, clean_id, cleaned)
            _write_trade_role_snapshot(conn, clean_id, cleaned)
        if cursor.rowcount <= 0:
            raise ValueError("交易日志记录不存在")
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
                return "这条交易已经入账到组合持仓，不能直接删除；请用冲销/修正交易处理，避免交易日志和持仓变成两套账。"
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
            radar_data_status = ?,
            radar_is_stale = ?,
            radar_blocked = ?,
            radar_block_reasons_json = ?,
            gate_hard_blocked = ?,
            radar_advisory_only = ?,
            radar_advisory_warnings_json = ?,
            price_position = ?,
            entry_display_label = ?,
            entry_action_hint = ?,
            entry_display_reason = ?,
            buy_zone_snapshot_json = ?,
            buy_zone_context_json = ?,
            setup_score = ?,
            buy_zone_action = ?,
            buy_zone_action_text = ?,
            primary_zone_text = ?,
            technical_entry_zone_json = ?,
            deep_valuation_zone_json = ?,
            chase_above_price = ?,
            mood_gate_blocked = ?,
            position_gate_blocked = ?,
            radar_observation_only = ?,
            gate_checked_at = ?
        WHERE id = ?
        """,
        (
            cleaned["radar_decision"],
            cleaned["radar_data_status"],
            cleaned["radar_is_stale"],
            cleaned["radar_blocked"],
            cleaned["radar_block_reasons_json"],
            cleaned["gate_hard_blocked"],
            cleaned["radar_advisory_only"],
            cleaned["radar_advisory_warnings_json"],
            cleaned["price_position"],
            cleaned["entry_display_label"],
            cleaned["entry_action_hint"],
            cleaned["entry_display_reason"],
            cleaned["buy_zone_snapshot_json"],
            cleaned["buy_zone_context_json"],
            cleaned["setup_score"],
            cleaned["buy_zone_action"],
            cleaned["buy_zone_action_text"],
            cleaned["primary_zone_text"],
            cleaned["technical_entry_zone_json"],
            cleaned["deep_valuation_zone_json"],
            cleaned["chase_above_price"],
            cleaned["mood_gate_blocked"],
            cleaned["position_gate_blocked"],
            cleaned["radar_observation_only"],
            cleaned["gate_checked_at"],
            entry_id,
        ),
    )


def _write_buy_plan_snapshot(conn: sqlite3.Connection, entry_id: int, cleaned: dict) -> None:
    conn.execute(
        """
        UPDATE trade_journal_entries
        SET
            entry_mode = ?,
            buy_plan_id = ?,
            buy_plan_level = ?,
            planned_ladder_buy = ?,
            plan_trigger_price = ?,
            plan_planned_quantity = ?,
            plan_remaining_quantity = ?,
            plan_max_position_pct = ?,
            plan_match_status = ?,
            plan_block_reasons_json = ?,
            fresh_plan_execution = ?,
            plan_age_minutes = ?,
            plan_recently_created_or_modified = ?
        WHERE id = ?
        """,
        (
            cleaned["entry_mode"],
            cleaned["buy_plan_id"],
            cleaned["buy_plan_level"],
            cleaned["planned_ladder_buy"],
            cleaned["plan_trigger_price"],
            cleaned["plan_planned_quantity"],
            cleaned["plan_remaining_quantity"],
            cleaned["plan_max_position_pct"],
            cleaned["plan_match_status"],
            cleaned["plan_block_reasons_json"],
            cleaned["fresh_plan_execution"],
            cleaned["plan_age_minutes"],
            cleaned["plan_recently_created_or_modified"],
            entry_id,
        ),
    )


def _write_starter_snapshot(conn: sqlite3.Connection, entry_id: int, cleaned: dict) -> None:
    conn.execute(
        """
        UPDATE trade_journal_entries
        SET
            starter_position = ?,
            starter_max_pct = ?,
            starter_position_before_pct = ?,
            starter_position_after_pct = ?,
            starter_match_status = ?,
            starter_block_reasons_json = ?
        WHERE id = ?
        """,
        (
            cleaned["starter_position"],
            cleaned["starter_max_pct"],
            cleaned["starter_position_before_pct"],
            cleaned["starter_position_after_pct"],
            cleaned["starter_match_status"],
            cleaned["starter_block_reasons_json"],
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


def _write_sell_context_snapshot(conn: sqlite3.Connection, entry_id: int, cleaned: dict) -> None:
    snapshot_json = cleaned.get("sell_context_snapshot_json")
    if not snapshot_json:
        return
    conn.execute(
        """
        UPDATE trade_journal_entries
        SET sell_context_snapshot_json = ?
        WHERE id = ?
        """,
        (snapshot_json, entry_id),
    )


def _write_structure_entry_snapshot(conn: sqlite3.Connection, entry_id: int, cleaned: dict) -> None:
    if not any(
        cleaned.get(field) not in {None, "", "[]"}
        for field in (
            "structure_status",
            "structure_score",
            "structure_reasons_json",
            "structure_warnings_json",
            "structure_checked_at",
        )
    ):
        return
    conn.execute(
        """
        UPDATE trade_journal_entries
        SET
            structure_status = ?,
            structure_score = ?,
            structure_reasons_json = ?,
            structure_warnings_json = ?,
            structure_checked_at = ?
        WHERE id = ?
        """,
        (
            cleaned["structure_status"],
            cleaned["structure_score"],
            cleaned["structure_reasons_json"],
            cleaned["structure_warnings_json"],
            cleaned["structure_checked_at"],
            entry_id,
        ),
    )


def _write_pullback_acceptance_snapshot(conn: sqlite3.Connection, entry_id: int, cleaned: dict) -> None:
    if not any(
        cleaned.get(field) not in {None, "", "[]"}
        for field in (
            "acceptance_status",
            "acceptance_score",
            "acceptance_reasons_json",
            "acceptance_warnings_json",
            "acceptance_checked_at",
        )
    ):
        return
    conn.execute(
        """
        UPDATE trade_journal_entries
        SET
            acceptance_status = ?,
            acceptance_score = ?,
            acceptance_reasons_json = ?,
            acceptance_warnings_json = ?,
            acceptance_checked_at = ?
        WHERE id = ?
        """,
        (
            cleaned["acceptance_status"],
            cleaned["acceptance_score"],
            cleaned["acceptance_reasons_json"],
            cleaned["acceptance_warnings_json"],
            cleaned["acceptance_checked_at"],
            entry_id,
        ),
    )


def _write_volume_price_acceptance_snapshot(conn: sqlite3.Connection, entry_id: int, cleaned: dict) -> None:
    if not any(
        cleaned.get(field) not in {None, "", "[]"}
        for field in (
            "volume_price_status",
            "volume_price_score",
            "volume_ratio",
            "volume_ma20",
            "volume_regime_cn",
            "close_position",
            "candle_signal_cn",
            "volume_signal_cn",
            "support_signal_cn",
            "confirmation_signal_cn",
            "distribution_count_10d",
            "volume_price_reason_cn",
            "volume_price_zone_source",
            "volume_price_checked_at",
        )
    ):
        return
    conn.execute(
        """
        UPDATE trade_journal_entries
        SET
            volume_price_status = ?,
            volume_price_score = ?,
            volume_ratio = ?,
            volume_ma20 = ?,
            volume_regime_cn = ?,
            close_position = ?,
            candle_signal_cn = ?,
            volume_signal_cn = ?,
            support_signal_cn = ?,
            confirmation_signal_cn = ?,
            distribution_count_10d = ?,
            volume_price_reason_cn = ?,
            volume_price_zone_source = ?,
            volume_price_checked_at = ?
        WHERE id = ?
        """,
        (
            cleaned["volume_price_status"],
            cleaned["volume_price_score"],
            cleaned["volume_ratio"],
            cleaned["volume_ma20"],
            cleaned["volume_regime_cn"],
            cleaned["close_position"],
            cleaned["candle_signal_cn"],
            cleaned["volume_signal_cn"],
            cleaned["support_signal_cn"],
            cleaned["confirmation_signal_cn"],
            cleaned["distribution_count_10d"],
            cleaned["volume_price_reason_cn"],
            cleaned["volume_price_zone_source"],
            cleaned["volume_price_checked_at"],
            entry_id,
        ),
    )


def _write_buy_advisory_snapshot(conn: sqlite3.Connection, entry_id: int, cleaned: dict) -> None:
    if not any(
        cleaned.get(field) not in {None, "", "[]", False}
        for field in (
            "buy_advisory_warnings_json",
            "buy_advisory_acknowledged",
            "advisory_level",
            "advisory_text",
            "advisory_reasons_json",
            "user_confirmed_advisory",
            "validation_passed",
            "can_submit",
            "advisory_checked_at",
            "macro_regime",
            "portfolio_structure_status",
            "advisory_action",
            "risk_warning_cn",
            "user_override",
            "override_reason",
            "action_fusion_action",
            "left_side_action_cn",
            "position_status",
        )
    ):
        return
    conn.execute(
        """
        UPDATE trade_journal_entries
        SET
            buy_advisory_warnings_json = ?,
            buy_advisory_acknowledged = ?,
            advisory_level = ?,
            advisory_text = ?,
            advisory_reasons_json = ?,
            user_confirmed_advisory = ?,
            validation_passed = ?,
            can_submit = ?,
            advisory_checked_at = ?,
            macro_regime = ?,
            portfolio_structure_status = ?,
            advisory_action = ?,
            risk_warning_cn = ?,
            user_override = ?,
            override_reason = ?,
            action_fusion_action = ?,
            left_side_action_cn = ?,
            position_status = ?
        WHERE id = ?
        """,
        (
            cleaned["buy_advisory_warnings_json"],
            cleaned["buy_advisory_acknowledged"],
            cleaned["advisory_level"],
            cleaned["advisory_text"],
            cleaned["advisory_reasons_json"],
            cleaned["user_confirmed_advisory"],
            cleaned["validation_passed"],
            cleaned["can_submit"],
            cleaned["advisory_checked_at"],
            cleaned["macro_regime"],
            cleaned["portfolio_structure_status"],
            cleaned["advisory_action"],
            cleaned["risk_warning_cn"],
            cleaned["user_override"],
            cleaned["override_reason"],
            cleaned["action_fusion_action"],
            cleaned["left_side_action_cn"],
            cleaned["position_status"],
            entry_id,
        ),
    )


def _write_sell_advisory_snapshot(conn: sqlite3.Connection, entry_id: int, cleaned: dict) -> None:
    if not any(
        cleaned.get(field) not in {None, "", "[]", False}
        for field in (
            "sell_warning_level",
            "sell_warning_text",
            "sell_warning_reasons_json",
            "sell_review_required",
            "sell_blocked",
            "user_confirmed_sell_warning",
            "buy_zone_display_json",
            "final_decision_snapshot_json",
        )
    ):
        return
    conn.execute(
        """
        UPDATE trade_journal_entries
        SET
            sell_warning_level = ?,
            sell_warning_text = ?,
            sell_warning_reasons_json = ?,
            sell_review_required = ?,
            sell_blocked = ?,
            user_confirmed_sell_warning = ?,
            buy_zone_display_json = ?,
            final_decision_snapshot_json = ?
        WHERE id = ?
        """,
        (
            cleaned["sell_warning_level"],
            cleaned["sell_warning_text"],
            cleaned["sell_warning_reasons_json"],
            cleaned["sell_review_required"],
            cleaned["sell_blocked"],
            cleaned["user_confirmed_sell_warning"],
            cleaned["buy_zone_display_json"],
            cleaned["final_decision_snapshot_json"],
            entry_id,
        ),
    )


def _write_daily_trade_activity_snapshot(conn: sqlite3.Connection, entry_id: int, cleaned: dict) -> None:
    trade_date = str(cleaned.get("trade_date") or "").strip()
    if not trade_date:
        return
    cursor = conn.execute(
        """
        SELECT *
        FROM trade_journal_entries
        WHERE trade_date = ?
        ORDER BY trade_date ASC, created_at ASC, id ASC
        """,
        (trade_date,),
    )

    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description] if cursor.description else []
    trades = [_row_to_dict(columns, row) for row in rows]
    activity = build_daily_trade_activity(trade_date, trades)
    conn.execute(
        """
        UPDATE trade_journal_entries
        SET
            daily_trade_record_count = ?,
            daily_trade_decision_count = ?,
            daily_trade_advisory_level = ?,
            daily_trade_advisory_text = ?,
            daily_trade_advisory_reasons_json = ?,
            daily_trade_activity_snapshot_json = ?,
            user_confirmed_daily_trade_advisory = ?
        WHERE id = ?
        """,
        (
            int(activity.get("trade_record_count") or 0),
            int(activity.get("trade_decision_count") or 0),
            str(activity.get("advisory_level") or "LOW"),
            str(activity.get("advisory_text") or ""),
            _reasons_json(activity.get("advisory_reasons") or []),
            snapshot_json(activity),
            _clean_bool(cleaned.get("user_confirmed_daily_trade_advisory")),
            entry_id,
        ),
    )


def _write_trade_role_snapshot(conn: sqlite3.Connection, entry_id: int, cleaned: dict) -> None:
    if not any(
        cleaned.get(field) not in {None, ""}
        for field in ("trade_role", "role_label", "role_target_weight", "core_tactical_split", "role_reason")
    ):
        return
    conn.execute(
        """
        UPDATE trade_journal_entries
        SET
            trade_role = ?,
            role_label = ?,
            role_target_weight = ?,
            core_tactical_split = ?,
            role_reason = ?
        WHERE id = ?
        """,
        (
            cleaned["trade_role"],
            cleaned["role_label"],
            cleaned["role_target_weight"],
            cleaned["core_tactical_split"],
            cleaned["role_reason"],
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
        "buy_zone_context_json": _dict_json(values.get("buy_zone_context", values.get("buy_zone_context_json"))),
        "buy_zone_display_json": _dict_json(values.get("buy_zone_display", values.get("buy_zone_display_json"))),
        "created_at": _now(),
    }


def _clean_trade_entry(symbol: str, values: dict) -> dict:
    action_type = str(values.get("action_type") or "").strip().lower()
    if action_type not in ACTION_TYPES:
        raise ValueError("请选择有效的交易操作类型")
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
    cleaned.update(_clean_structured_sell_reason(action_type, values))
    cleaned.update(_clean_radar_gate_snapshot(action_type, values))
    cleaned.update(_clean_buy_plan_snapshot(action_type, values))
    cleaned.update(_clean_starter_snapshot(action_type, values))
    cleaned.update(_clean_sell_context_snapshot(action_type, values))
    cleaned.update(_clean_structure_entry_snapshot(action_type, values))
    cleaned.update(_clean_pullback_acceptance_snapshot(action_type, values))
    cleaned.update(_clean_volume_price_acceptance_snapshot(action_type, values))
    cleaned.update(_clean_buy_advisory_snapshot(action_type, values))
    cleaned.update(_clean_sell_advisory_snapshot(action_type, {**values, **cleaned}))
    cleaned.update(_clean_daily_trade_activity_snapshot(values))
    cleaned.update(_clean_trade_role_snapshot(values))
    return cleaned


def _clean_trade_role_snapshot(values: dict) -> dict:
    role = normalize_portfolio_role(_value(values, "tradeRole", "trade_role", "role", "portfolio_role"), default=None)
    if role is None:
        return {
            "trade_role": None,
            "role_label": _clean_optional_text(_value(values, "roleLabel", "role_label")),
            "role_target_weight": _clean_optional_text(_value(values, "roleTargetWeight", "role_target_weight")),
            "core_tactical_split": _clean_optional_text(_value(values, "coreTacticalSplit", "core_tactical_split")),
            "role_reason": _clean_optional_text(_value(values, "roleReason", "role_reason")),
        }
    return {
        "trade_role": role,
        "role_label": portfolio_role_label(role),
        "role_target_weight": _clean_optional_text(_value(values, "roleTargetWeight", "role_target_weight"))
        or portfolio_role_target_weight(role),
        "core_tactical_split": _clean_optional_text(_value(values, "coreTacticalSplit", "core_tactical_split"))
        or portfolio_role_core_tactical_split(role),
        "role_reason": _clean_optional_text(_value(values, "roleReason", "role_reason")),
    }


def _clean_daily_trade_activity_snapshot(values: dict) -> dict:
    return {
        "user_confirmed_daily_trade_advisory": _clean_bool(
            _value(values, "userConfirmedDailyTradeAdvisory", "user_confirmed_daily_trade_advisory")
        )
    }


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


def _clean_structured_sell_reason(action_type: str, values: dict) -> dict:
    if action_type not in {"sell", "trim"}:
        return {
            "sell_context_type": None,
            "sell_reason_tags": None,
            "fundamental_change_type": None,
            "valuation_compression_reason": None,
            "liquidity_shock_reason": None,
            "position_risk_reason": None,
            "sell_thesis_note": None,
        }
    return {
        "sell_context_type": _clean_optional_text(_value(values, "sellContextType", "sell_context_type")),
        "sell_reason_tags": _reasons_json(_value(values, "sellReasonTags", "sell_reason_tags", "sell_reason_tag_list")),
        "fundamental_change_type": _reasons_json(
            _value(values, "fundamentalChangeType", "fundamental_change_type", "fundamental_change_types")
        ),
        "valuation_compression_reason": _clean_optional_text(
            _value(values, "valuationCompressionReason", "valuation_compression_reason")
        ),
        "liquidity_shock_reason": _clean_optional_text(_value(values, "liquidityShockReason", "liquidity_shock_reason")),
        "position_risk_reason": _clean_optional_text(_value(values, "positionRiskReason", "position_risk_reason")),
        "sell_thesis_note": _clean_optional_text(_value(values, "sellThesisNote", "sell_thesis_note")),
    }


def _clean_radar_gate_snapshot(action_type: str, values: dict) -> dict:
    if action_type not in {"buy", "add"}:
        context = _value(values, "buyZoneContext", "buy_zone_context", "buy_zone_context_json")
        return {
            "radar_decision": None,
            "radar_data_status": None,
            "radar_is_stale": False,
            "radar_blocked": False,
            "radar_block_reasons_json": "[]",
            "gate_hard_blocked": False,
            "radar_advisory_only": False,
            "radar_advisory_warnings_json": "[]",
            "price_position": None,
            "entry_display_label": None,
            "entry_action_hint": None,
            "entry_display_reason": None,
            "buy_zone_snapshot_json": None,
            "buy_zone_context_json": _dict_json(context),
            "setup_score": _optional_non_negative_number(_value(values, "setupScore", "setup_score"), "setup_score"),
            "buy_zone_action": _clean_optional_text(_value(values, "buyZoneAction", "buy_zone_action")),
            "buy_zone_action_text": _clean_optional_text(_value(values, "buyZoneActionText", "buy_zone_action_text")),
            "primary_zone_text": _clean_optional_text(_value(values, "primaryZoneText", "primary_zone_text")),
            "technical_entry_zone_json": None,
            "deep_valuation_zone_json": None,
            "chase_above_price": None,
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
            "radar_data_status": "DATA_MISSING",
            "radar_is_stale": False,
            "radar_blocked": False,
            "radar_block_reasons_json": "[]",
            "gate_hard_blocked": False,
            "radar_advisory_only": True,
            "radar_advisory_warnings_json": _reasons_json(["买区提示缺失，需人工判断；可手动继续，系统会记录为人工复核记录。"]),
            "price_position": None,
            "entry_display_label": None,
            "entry_action_hint": None,
            "entry_display_reason": None,
            "buy_zone_snapshot_json": None,
            "buy_zone_context_json": None,
            "setup_score": None,
            "buy_zone_action": None,
            "buy_zone_action_text": None,
            "primary_zone_text": None,
            "technical_entry_zone_json": None,
            "deep_valuation_zone_json": None,
            "chase_above_price": None,
            "mood_gate_blocked": False,
            "position_gate_blocked": False,
            "radar_observation_only": False,
            "gate_checked_at": _now(),
        }
    advisory_warnings = _buy_advisory_warnings_from_values(values)
    return {
        "radar_decision": _clean_optional_text(_value(values, "radarDecision", "radar_decision")),
        "radar_data_status": _clean_optional_text(_value(values, "radarDataStatus", "radar_data_status")),
        "radar_is_stale": _clean_bool(_value(values, "radarIsStale", "radar_is_stale")),
        "radar_blocked": False,
        "radar_block_reasons_json": "[]",
        "gate_hard_blocked": False,
        "radar_advisory_only": bool(advisory_warnings) or _clean_bool(_value(values, "radarAdvisoryOnly", "radar_advisory_only")),
        "radar_advisory_warnings_json": _reasons_json(advisory_warnings),
        "price_position": _clean_optional_text(_value(values, "pricePosition", "price_position", "zoneStatus", "zone_status")),
        "entry_display_label": _clean_optional_text(_value(values, "entryDisplayLabel", "entry_display_label")),
        "entry_action_hint": _clean_optional_text(_value(values, "entryActionHint", "entry_action_hint")),
        "entry_display_reason": _clean_optional_text(_value(values, "entryDisplayReason", "entry_display_reason")),
        "buy_zone_snapshot_json": _dict_json(_value(values, "buyZoneSnapshot", "buy_zone_snapshot", "buy_zone_snapshot_json")),
        "buy_zone_context_json": _dict_json(_value(values, "buyZoneContext", "buy_zone_context", "buy_zone_context_json")),
        "setup_score": _optional_non_negative_number(_value(values, "setupScore", "setup_score"), "setup_score"),
        "buy_zone_action": _clean_optional_text(_value(values, "buyZoneAction", "buy_zone_action")),
        "buy_zone_action_text": _clean_optional_text(_value(values, "buyZoneActionText", "buy_zone_action_text")),
        "primary_zone_text": _clean_optional_text(_value(values, "primaryZoneText", "primary_zone_text")),
        "technical_entry_zone_json": _dict_json(
            _value(values, "technicalEntryZone", "technical_entry_zone", "technical_entry_zone_json")
        ),
        "deep_valuation_zone_json": _dict_json(_value(values, "deepValuationZone", "deep_valuation_zone", "deep_valuation_zone_json")),
        "chase_above_price": _optional_non_negative_number(_value(values, "chaseAbovePrice", "chase_above_price"), "chase_above_price"),
        "mood_gate_blocked": False,
        "position_gate_blocked": False,
        "radar_observation_only": _clean_bool(_value(values, "radarObservationOnly", "radar_observation_only")),
        "gate_checked_at": _clean_optional_text(_value(values, "gateCheckedAt", "gate_checked_at")),
    }


def _buy_advisory_warnings_from_values(values: dict) -> list[str]:
    warnings: list[str] = []
    for key_group in (
        ("radarAdvisoryWarnings", "radar_advisory_warnings", "radar_advisory_warnings_json"),
        ("radarBlockReasons", "radar_block_reasons", "radar_block_reasons_json"),
    ):
        warnings.extend(_reasons_list(_value(values, *key_group)))
    if _clean_bool(_value(values, "radarBlocked", "radar_blocked")) and not warnings:
        warnings.append("买区提示需人工复核；可手动继续，系统会记录为人工复核记录。")
    if _clean_bool(_value(values, "gateHardBlocked", "gate_hard_blocked")) and not warnings:
        warnings.append("历史买入风险标记已按提示保存；可手动继续，系统会记录为人工复核记录。")
    if _clean_bool(_value(values, "moodGateBlocked", "mood_gate_blocked")):
        warnings.append("买入情绪提示：请确认这不是 FOMO / 焦虑 / 复仇交易。")
    if _clean_bool(_value(values, "positionGateBlocked", "position_gate_blocked")):
        warnings.append("买入仓位提示：买入后仓位偏离系统参考上限。")
    return _dedupe_text(warnings)


def _reasons_list(value) -> list[str]:
    try:
        parsed = json.loads(_reasons_json(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        parsed = [parsed]
    return [str(item) for item in parsed if str(item).strip()]


def _dedupe_text(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _clean_buy_plan_snapshot(action_type: str, values: dict) -> dict:
    if action_type not in {"buy", "add"}:
        return {
            "entry_mode": None,
            "buy_plan_id": None,
            "buy_plan_level": None,
            "planned_ladder_buy": False,
            "plan_trigger_price": None,
            "plan_planned_quantity": None,
            "plan_remaining_quantity": None,
            "plan_max_position_pct": None,
            "plan_match_status": None,
            "plan_block_reasons_json": "[]",
            "fresh_plan_execution": False,
            "plan_age_minutes": None,
            "plan_recently_created_or_modified": False,
        }
    return {
        "entry_mode": _clean_entry_mode(_value(values, "entryMode", "entry_mode")),
        "buy_plan_id": _clean_optional_text(_value(values, "buyPlanId", "buy_plan_id")),
        "buy_plan_level": _clean_optional_text(_value(values, "buyPlanLevel", "buy_plan_level")),
        "planned_ladder_buy": _clean_bool(_value(values, "plannedLadderBuy", "planned_ladder_buy")),
        "plan_trigger_price": _optional_non_negative_number(_value(values, "planTriggerPrice", "plan_trigger_price"), "plan_trigger_price"),
        "plan_planned_quantity": _optional_non_negative_number(
            _value(values, "planPlannedQuantity", "plan_planned_quantity"),
            "plan_planned_quantity",
        ),
        "plan_remaining_quantity": _optional_non_negative_number(
            _value(values, "planRemainingQuantity", "plan_remaining_quantity"),
            "plan_remaining_quantity",
        ),
        "plan_max_position_pct": _optional_non_negative_number(
            _value(values, "planMaxPositionPct", "plan_max_position_pct"),
            "plan_max_position_pct",
        ),
        "plan_match_status": _clean_optional_text(_value(values, "planMatchStatus", "plan_match_status")),
        "plan_block_reasons_json": _reasons_json(
            _value(values, "planBlockReasons", "plan_block_reasons", "plan_block_reasons_json")
        ),
        "fresh_plan_execution": _clean_bool(_value(values, "freshPlanExecution", "fresh_plan_execution")),
        "plan_age_minutes": _optional_non_negative_number(_value(values, "planAgeMinutes", "plan_age_minutes"), "plan_age_minutes"),
        "plan_recently_created_or_modified": _clean_bool(
            _value(values, "planRecentlyCreatedOrModified", "plan_recently_created_or_modified")
        ),
    }


def _clean_starter_snapshot(action_type: str, values: dict) -> dict:
    if action_type not in {"buy", "add"}:
        return {
            "starter_position": False,
            "starter_max_pct": None,
            "starter_position_before_pct": None,
            "starter_position_after_pct": None,
            "starter_match_status": None,
            "starter_block_reasons_json": "[]",
        }
    return {
        "starter_position": _clean_bool(_value(values, "starterPosition", "starter_position")),
        "starter_max_pct": _optional_non_negative_number(_value(values, "starterMaxPct", "starter_max_pct"), "starter_max_pct"),
        "starter_position_before_pct": _optional_non_negative_number(
            _value(values, "starterPositionBeforePct", "starter_position_before_pct"),
            "starter_position_before_pct",
        ),
        "starter_position_after_pct": _optional_non_negative_number(
            _value(values, "starterPositionAfterPct", "starter_position_after_pct"),
            "starter_position_after_pct",
        ),
        "starter_match_status": _clean_optional_text(_value(values, "starterMatchStatus", "starter_match_status")),
        "starter_block_reasons_json": _reasons_json(
            _value(values, "starterBlockReasons", "starter_block_reasons", "starter_block_reasons_json")
        ),
    }


def _clean_sell_context_snapshot(action_type: str, values: dict) -> dict:
    if action_type not in {"sell", "trim"}:
        return {"sell_context_snapshot_json": None}
    snapshot = _value(values, "sellContextSnapshot", "sell_context_snapshot", "sell_context_snapshot_json")
    if snapshot in (None, ""):
        return {"sell_context_snapshot_json": None}
    if isinstance(snapshot, str):
        try:
            parsed = json.loads(snapshot)
        except json.JSONDecodeError:
            parsed = {}
    elif isinstance(snapshot, dict):
        parsed = dict(snapshot)
    else:
        parsed = {}
    if not parsed:
        return {"sell_context_snapshot_json": None}
    return {"sell_context_snapshot_json": json.dumps(parsed, ensure_ascii=False, sort_keys=True)}


def _clean_structure_entry_snapshot(action_type: str, values: dict) -> dict:
    if action_type not in {"buy", "add"}:
        return {
            "structure_status": None,
            "structure_score": None,
            "structure_reasons_json": "[]",
            "structure_warnings_json": "[]",
            "structure_checked_at": None,
        }
    return {
        "structure_status": _clean_optional_text(_value(values, "structureStatus", "structure_status")),
        "structure_score": _optional_non_negative_number(
            _value(values, "structureScore", "structure_score"),
            "structure_score",
        ),
        "structure_reasons_json": _reasons_json(
            _value(values, "structureReasons", "structure_reasons", "structure_reasons_json")
        ),
        "structure_warnings_json": _reasons_json(
            _value(values, "structureWarnings", "structure_warnings", "structure_warnings_json")
        ),
        "structure_checked_at": _clean_optional_text(_value(values, "structureCheckedAt", "structure_checked_at")),
    }


def _clean_pullback_acceptance_snapshot(action_type: str, values: dict) -> dict:
    if action_type not in {"buy", "add"}:
        return {
            "acceptance_status": None,
            "acceptance_score": None,
            "acceptance_reasons_json": "[]",
            "acceptance_warnings_json": "[]",
            "acceptance_checked_at": None,
        }
    return {
        "acceptance_status": _clean_optional_text(_value(values, "acceptanceStatus", "acceptance_status")),
        "acceptance_score": _optional_non_negative_number(
            _value(values, "acceptanceScore", "acceptance_score"),
            "acceptance_score",
        ),
        "acceptance_reasons_json": _reasons_json(
            _value(values, "acceptanceReasons", "acceptance_reasons", "acceptance_reasons_json")
        ),
        "acceptance_warnings_json": _reasons_json(
            _value(values, "acceptanceWarnings", "acceptance_warnings", "acceptance_warnings_json")
        ),
        "acceptance_checked_at": _clean_optional_text(_value(values, "acceptanceCheckedAt", "acceptance_checked_at")),
    }


def _clean_volume_price_acceptance_snapshot(action_type: str, values: dict) -> dict:
    if action_type not in {"buy", "add"}:
        return {
            "volume_price_status": None,
            "volume_price_score": None,
            "volume_ratio": None,
            "volume_ma20": None,
            "volume_regime_cn": None,
            "close_position": None,
            "candle_signal_cn": None,
            "volume_signal_cn": None,
            "support_signal_cn": None,
            "confirmation_signal_cn": None,
            "distribution_count_10d": None,
            "volume_price_reason_cn": None,
            "volume_price_zone_source": None,
            "volume_price_checked_at": None,
        }
    return {
        "volume_price_status": _clean_optional_text(_value(values, "volumePriceStatus", "volume_price_status")),
        "volume_price_score": _optional_non_negative_number(
            _value(values, "volumePriceScore", "volume_price_score"),
            "volume_price_score",
        ),
        "volume_ratio": _optional_non_negative_number(_value(values, "volumeRatio", "volume_ratio"), "volume_ratio"),
        "volume_ma20": _optional_non_negative_number(_value(values, "volumeMa20", "volume_ma20"), "volume_ma20"),
        "volume_regime_cn": _clean_optional_text(_value(values, "volumeRegimeCn", "volume_regime_cn")),
        "close_position": _optional_non_negative_number(_value(values, "closePosition", "close_position"), "close_position"),
        "candle_signal_cn": _clean_optional_text(_value(values, "candleSignalCn", "candle_signal_cn")),
        "volume_signal_cn": _clean_optional_text(_value(values, "volumeSignalCn", "volume_signal_cn")),
        "support_signal_cn": _clean_optional_text(_value(values, "supportSignalCn", "support_signal_cn")),
        "confirmation_signal_cn": _clean_optional_text(_value(values, "confirmationSignalCn", "confirmation_signal_cn")),
        "distribution_count_10d": _optional_int(
            _value(values, "distributionCount10d", "distribution_count_10d"),
            "distribution_count_10d",
        ),
        "volume_price_reason_cn": _clean_optional_text(
            _value(values, "volumePriceReasonCn", "volume_price_reason_cn", "reasonCn", "reason_cn")
        ),
        "volume_price_zone_source": _clean_optional_text(
            _value(values, "volumePriceZoneSource", "volume_price_zone_source", "zoneSource", "zone_source")
        ),
        "volume_price_checked_at": _clean_optional_text(
            _value(values, "volumePriceCheckedAt", "volume_price_checked_at")
        ),
    }


def _clean_buy_advisory_snapshot(action_type: str, values: dict) -> dict:
    if action_type not in {"buy", "add"}:
        return {
            "buy_advisory_warnings_json": "[]",
            "buy_advisory_acknowledged": False,
            "advisory_level": None,
            "advisory_text": None,
            "advisory_reasons_json": "[]",
            "user_confirmed_advisory": False,
            "validation_passed": True,
            "can_submit": True,
            "advisory_checked_at": None,
            "macro_regime": None,
            "portfolio_structure_status": None,
            "advisory_action": None,
            "risk_warning_cn": None,
            "user_override": False,
            "override_reason": None,
            "action_fusion_action": None,
            "left_side_action_cn": None,
            "position_status": None,
        }
    warnings = _reasons_list(_value(values, "buyAdvisoryWarnings", "buy_advisory_warnings", "buy_advisory_warnings_json"))
    if not warnings:
        warnings = _buy_advisory_warnings_from_values(values)
    explicit_reasons = _reasons_list(_value(values, "advisoryReasons", "advisory_reasons", "advisory_reasons_json"))
    if explicit_reasons:
        warnings = explicit_reasons
    level = _clean_optional_text(_value(values, "advisoryLevel", "advisory_level")) or _buy_advisory_level_from_reasons(warnings)
    text = _clean_optional_text(_value(values, "advisoryText", "advisory_text")) or _buy_advisory_text(level, warnings)
    explicit_confirmed = _value(values, "userConfirmedAdvisory", "user_confirmed_advisory")
    confirmed = _clean_bool(explicit_confirmed) if explicit_confirmed is not None else bool(warnings)
    explicit_override = _value(values, "userOverride", "user_override")
    user_override = _clean_bool(explicit_override) if explicit_override is not None else bool(warnings)
    return {
        "buy_advisory_warnings_json": _reasons_json(_dedupe_text(warnings)),
        "buy_advisory_acknowledged": _clean_bool(_value(values, "buyAdvisoryAcknowledged", "buy_advisory_acknowledged")) or confirmed,
        "advisory_level": level,
        "advisory_text": text,
        "advisory_reasons_json": _reasons_json(_dedupe_text(warnings)),
        "user_confirmed_advisory": confirmed,
        "validation_passed": _clean_bool(_value(values, "validationPassed", "validation_passed")) if _value(values, "validationPassed", "validation_passed") is not None else True,
        "can_submit": _clean_bool(_value(values, "canSubmit", "can_submit")) if _value(values, "canSubmit", "can_submit") is not None else True,
        "advisory_checked_at": _clean_optional_text(_value(values, "advisoryCheckedAt", "advisory_checked_at", "gateCheckedAt", "gate_checked_at")),
        "macro_regime": _clean_optional_text(_value(values, "macroRegime", "macro_regime")),
        "portfolio_structure_status": _clean_optional_text(
            _value(values, "portfolioStructureStatus", "portfolio_structure_status")
        ),
        "advisory_action": _clean_optional_text(_value(values, "advisoryAction", "advisory_action")),
        "risk_warning_cn": _clean_optional_text(
            _value(values, "riskWarningCn", "risk_warning_cn")
            or "；".join(_dedupe_text(warnings))
        ),
        "user_override": user_override,
        "override_reason": _clean_optional_text(_value(values, "overrideReason", "override_reason")),
        "action_fusion_action": _clean_optional_text(_value(values, "actionFusionAction", "action_fusion_action")),
        "left_side_action_cn": _clean_optional_text(_value(values, "leftSideActionCn", "left_side_action_cn")),
        "position_status": _clean_optional_text(_value(values, "positionStatus", "position_status")),
    }


def _buy_advisory_level_from_reasons(reasons: list[str]) -> str:
    if not reasons:
        return "NONE"
    text = " ".join(str(item or "") for item in reasons).upper()
    if any(token in text for token in ("BLOCK_CHASE", "HIGH_RISK", "追高", "风险", "数据不足", "失效", "RR")):
        return "HIGH_RISK"
    return "WARNING"


def _buy_advisory_text(level: str, reasons: list[str]) -> str:
    if not reasons:
        return ""
    normalized = str(level or "").strip().upper()
    if normalized in {"HIGH_RISK", "CRITICAL"}:
        return "高风险买入提醒：系统不建议，但不会阻止；继续操作将记录为已确认风险。"
    if normalized == "WARNING":
        return "买入前风险提示：系统建议复核，但不会阻止你继续。"
    return "买入提醒：请确认本次操作符合你的计划。"


def _clean_sell_advisory_snapshot(action_type: str, values: dict) -> dict:
    if action_type not in {"sell", "trim"}:
        return {
            "sell_warning_level": None,
            "sell_warning_text": None,
            "sell_warning_reasons_json": "[]",
            "sell_review_required": False,
            "sell_blocked": False,
            "user_confirmed_sell_warning": False,
            "buy_zone_display_json": None,
            "final_decision_snapshot_json": None,
        }
    reasons = _reasons_list(
        _value(values, "sellWarningReasons", "sell_warning_reasons", "sell_warning_reasons_json")
    )
    if not reasons:
        reasons = _reasons_list(_value(values, "warnings", "warnings_json"))
    level = _clean_optional_text(_value(values, "sellWarningLevel", "sell_warning_level")) or _sell_warning_level_from_reasons(reasons)
    text = _clean_optional_text(_value(values, "sellWarningText", "sell_warning_text")) or _sell_warning_text(level, reasons)
    explicit_confirmed = _value(values, "userConfirmedSellWarning", "user_confirmed_sell_warning")
    confirmed = _clean_bool(explicit_confirmed) if explicit_confirmed is not None else bool(reasons)
    return {
        "sell_warning_level": level,
        "sell_warning_text": text,
        "sell_warning_reasons_json": _reasons_json(_dedupe_text(reasons)),
        "sell_review_required": bool(reasons) or level in {"WARNING", "HIGH_RISK"},
        "sell_blocked": False,
        "user_confirmed_sell_warning": confirmed,
        "buy_zone_display_json": _dict_json(_value(values, "buyZoneDisplay", "buy_zone_display", "buy_zone_display_json")),
        "final_decision_snapshot_json": _dict_json(
            _value(values, "finalDecision", "final_decision", "finalDecisionSnapshot", "final_decision_snapshot", "final_decision_snapshot_json")
        ),
    }


def _sell_warning_level_from_reasons(reasons: list[str]) -> str:
    if not reasons:
        return "NONE"
    return "HIGH_RISK" if any("block" in str(item).lower() or "blocked" in str(item).lower() for item in reasons) else "WARNING"


def _sell_warning_text(level: str, reasons: list[str]) -> str:
    if level == "HIGH_RISK":
        return "高风险卖出提醒：系统不建议，但你可以继续；继续操作将记录为人工确认。"
    if level == "WARNING" or reasons:
        return "卖出前复核：系统提示风险，但不会阻止你继续卖出。"
    if level == "INFO":
        return "卖出提醒：请确认本次卖出符合你的计划。"
    return ""


def _clean_entry_mode(value: object) -> str:
    text = _clean_text(value or "normal_buy")
    if text not in {"normal_buy", "planned_ladder_buy", "starter_position"}:
        return "normal_buy"
    return text


def _clean_decision_mood(value: object) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    if text not in DECISION_MOOD_TYPES:
        raise ValueError("请选择有效的交易心理标签")
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
        raise ValueError("请选择有效的错误标签")
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
        raise ValueError("缺少股票代码")
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
        raise ValueError("请选择有效的复盘周期")
    return horizon


def _clean_outcome_status(value) -> str:
    status = str(value or "missing").strip().lower()
    if status not in {"complete", "missing"}:
        raise ValueError("请选择有效的复盘状态")
    return status


def _validation_field_label(field: str) -> str:
    return VALIDATION_FIELD_LABELS.get(str(field or "").strip(), "该字段")


def _optional_non_negative_number(value, field: str) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{_validation_field_label(field)}需要填写数字") from exc
    if number < 0:
        raise ValueError(f"{_validation_field_label(field)}不能为负数")
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
        raise ValueError(f"{_validation_field_label(field)}需要填写数字") from exc


def _optional_int(value, field: str) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{_validation_field_label(field)}需要填写整数") from exc
    if number < 0:
        raise ValueError(f"{_validation_field_label(field)}不能为负数")
    return number


def _required_int(value, field: str) -> int:
    number = _optional_int(value, field)
    if number is None:
        raise ValueError(f"缺少必填信息：{_validation_field_label(field)}")
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


def _dict_json(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
    elif isinstance(value, dict):
        parsed = dict(value)
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        parsed = {"lower": value[0], "upper": value[1]}
    else:
        return None
    if not isinstance(parsed, dict):
        return None
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


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
    if "radar_advisory_warnings_json" in item:
        item["radar_advisory_warnings"] = _load_json_list(item["radar_advisory_warnings_json"])
    if "buy_zone_snapshot_json" in item:
        item["buy_zone_snapshot"] = _load_json_dict(item["buy_zone_snapshot_json"])
    if "buy_zone_context_json" in item:
        item["buy_zone_context"] = _load_json_dict(item["buy_zone_context_json"])
    if "buy_zone_display_json" in item:
        item["buy_zone_display"] = _load_json_dict(item["buy_zone_display_json"])
    if "technical_entry_zone_json" in item:
        item["technical_entry_zone"] = _load_json_dict(item["technical_entry_zone_json"])
    if "deep_valuation_zone_json" in item:
        item["deep_valuation_zone"] = _load_json_dict(item["deep_valuation_zone_json"])
    if "sell_context_snapshot_json" in item:
        item["sell_context_snapshot"] = _load_json_dict(item["sell_context_snapshot_json"])
    if "fundamental_change_type" in item:
        item["fundamental_change_types"] = _load_json_list(item["fundamental_change_type"])
    if "sell_reason_tags" in item:
        item["sell_reason_tag_list"] = _load_json_list(item["sell_reason_tags"])
    if "structure_reasons_json" in item:
        item["structure_reasons"] = _load_json_list(item["structure_reasons_json"])
    if "structure_warnings_json" in item:
        item["structure_warnings"] = _load_json_list(item["structure_warnings_json"])
    if "acceptance_reasons_json" in item:
        item["acceptance_reasons"] = _load_json_list(item["acceptance_reasons_json"])
    if "acceptance_warnings_json" in item:
        item["acceptance_warnings"] = _load_json_list(item["acceptance_warnings_json"])
    if "volume_price_status" in item:
        item["volume_price_acceptance"] = {
            "volume_price_status": item.get("volume_price_status"),
            "volume_price_score": item.get("volume_price_score"),
            "volume_ratio": item.get("volume_ratio"),
            "volume_ma20": item.get("volume_ma20"),
            "close_position": item.get("close_position"),
            "candle_signal_cn": item.get("candle_signal_cn"),
            "volume_signal_cn": item.get("volume_signal_cn"),
            "support_signal_cn": item.get("support_signal_cn"),
            "confirmation_signal_cn": item.get("confirmation_signal_cn"),
            "distribution_count_10d": item.get("distribution_count_10d"),
            "acceptance_reason_cn": item.get("volume_price_reason_cn"),
            "zone_source": item.get("volume_price_zone_source"),
            "volume_price_checked_at": item.get("volume_price_checked_at"),
        }
    if "buy_advisory_warnings_json" in item:
        item["buy_advisory_warnings"] = _load_json_list(item["buy_advisory_warnings_json"])
    if "advisory_reasons_json" in item:
        item["advisory_reasons"] = _load_json_list(item["advisory_reasons_json"])
    if "sell_warning_reasons_json" in item:
        item["sell_warning_reasons"] = _load_json_list(item["sell_warning_reasons_json"])
    if "final_decision_snapshot_json" in item:
        item["final_decision_snapshot"] = _load_json_dict(item["final_decision_snapshot_json"])
    for key in (
        "user_override",
        "buy_advisory_acknowledged",
        "user_confirmed_advisory",
        "validation_passed",
        "can_submit",
        "sell_review_required",
        "sell_blocked",
        "user_confirmed_sell_warning",
        "radar_blocked",
        "gate_hard_blocked",
        "radar_advisory_only",
        "mood_gate_blocked",
        "position_gate_blocked",
        "radar_observation_only",
    ):
        if key in item and item[key] is not None:
            item[key] = bool(item[key])
    return item


def _load_json_list(value) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def _load_json_dict(value) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
