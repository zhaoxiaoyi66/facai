from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from data.prices import CACHE_PATH


ALERT_ACTIVE = "ACTIVE"
ALERT_TRIGGERED = "TRIGGERED"
ALERT_CANCELLED = "CANCELLED"

VALID_ALERT_STATUSES = {ALERT_ACTIVE, ALERT_TRIGGERED, ALERT_CANCELLED}
TRIGGER_REGULAR = "REGULAR"
TRIGGER_PRE_MARKET = "PRE_MARKET"
TRIGGER_AFTER_HOURS = "AFTER_HOURS"
TRIGGER_LAST_CLOSE = "LAST_CLOSE"
VALID_TRIGGER_SOURCES = {TRIGGER_REGULAR, TRIGGER_PRE_MARKET, TRIGGER_AFTER_HOURS, TRIGGER_LAST_CLOSE}

ALERT_STATUS_LABELS = {
    ALERT_ACTIVE: "等待触发",
    ALERT_TRIGGERED: "已到计划价",
    ALERT_CANCELLED: "已取消",
}


class BuyPlanAlertStore:
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
                CREATE TABLE IF NOT EXISTS buy_plan_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL UNIQUE,
                    planned_buy_price REAL NOT NULL,
                    planned_buy_shares INTEGER NOT NULL,
                    note TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    triggered_at TEXT,
                    trigger_source TEXT
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(buy_plan_alerts)").fetchall()}
            if "trigger_source" not in columns:
                conn.execute("ALTER TABLE buy_plan_alerts ADD COLUMN trigger_source TEXT")

    def save_alert(
        self,
        symbol: object,
        planned_buy_price: object,
        planned_buy_shares: object,
        note: object = "",
        *,
        current_price: object = None,
        trigger_source: object = None,
    ) -> dict:
        clean_symbol = _normalize_symbol(symbol)
        price = _required_positive_number(planned_buy_price, "计划买入价")
        shares = _required_positive_int(planned_buy_shares, "计划买入股数")
        clean_note = str(note or "").strip()
        now = _now()
        status = ALERT_ACTIVE
        triggered_at = None
        current = _number(current_price)
        if current is not None and current <= price:
            status = ALERT_TRIGGERED
            triggered_at = now
            trigger_source = _normalize_trigger_source(trigger_source)
        else:
            trigger_source = None
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM buy_plan_alerts WHERE symbol = ?",
                (clean_symbol,),
            ).fetchone()
            created_at = existing[0] if existing and existing[0] else now
            conn.execute(
                """
                INSERT INTO buy_plan_alerts (
                    symbol,
                    planned_buy_price,
                    planned_buy_shares,
                    note,
                    status,
                    created_at,
                    updated_at,
                    triggered_at,
                    trigger_source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    planned_buy_price = excluded.planned_buy_price,
                    planned_buy_shares = excluded.planned_buy_shares,
                    note = excluded.note,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    triggered_at = excluded.triggered_at,
                    trigger_source = excluded.trigger_source
                """,
                (clean_symbol, price, shares, clean_note, status, created_at, now, triggered_at, trigger_source),
            )
        return self.get_alert(clean_symbol) or {}

    def cancel_alert(self, symbol: object) -> dict:
        clean_symbol = _normalize_symbol(symbol)
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE buy_plan_alerts
                SET status = ?,
                    updated_at = ?,
                    triggered_at = NULL,
                    trigger_source = NULL
                WHERE symbol = ?
                """,
                (ALERT_CANCELLED, now, clean_symbol),
            )
        return self.get_alert(clean_symbol, include_cancelled=True) or {}

    def get_alert(self, symbol: object, *, include_cancelled: bool = False) -> dict | None:
        clean_symbol = _normalize_symbol(symbol)
        with self.connect() as conn:
            if include_cancelled:
                cursor = conn.execute("SELECT * FROM buy_plan_alerts WHERE symbol = ?", (clean_symbol,))
            else:
                cursor = conn.execute(
                    "SELECT * FROM buy_plan_alerts WHERE symbol = ? AND status != ?",
                    (clean_symbol, ALERT_CANCELLED),
                )
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return _row_to_dict(columns, row) if row else None

    def list_alerts(self, symbols: list[str] | None = None, *, include_cancelled: bool = False) -> list[dict]:
        normalized = [_normalize_symbol(symbol) for symbol in (symbols or []) if str(symbol or "").strip()]
        with self.connect() as conn:
            if normalized:
                placeholders = ",".join("?" for _ in normalized)
                params: list[object] = list(normalized)
                status_sql = ""
                if not include_cancelled:
                    status_sql = " AND status != ?"
                    params.append(ALERT_CANCELLED)
                cursor = conn.execute(
                    f"SELECT * FROM buy_plan_alerts WHERE symbol IN ({placeholders}){status_sql}",
                    params,
                )
            elif include_cancelled:
                cursor = conn.execute("SELECT * FROM buy_plan_alerts")
            else:
                cursor = conn.execute("SELECT * FROM buy_plan_alerts WHERE status != ?", (ALERT_CANCELLED,))
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]

    def check_and_update(self, symbol: object, current_price: object, *, trigger_source: object = None) -> dict | None:
        alert = self.get_alert(symbol)
        if not alert:
            return None
        if alert.get("status") != ALERT_ACTIVE:
            alert["just_triggered"] = False
            return alert
        current = _number(current_price)
        planned = _number(alert.get("planned_buy_price"))
        if current is None or planned is None or current > planned:
            alert["just_triggered"] = False
            return alert
        now = _now()
        normalized_source = _normalize_trigger_source(trigger_source)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE buy_plan_alerts
                SET status = ?,
                    updated_at = ?,
                    triggered_at = ?,
                    trigger_source = ?
                WHERE symbol = ?
                  AND status = ?
                """,
                (ALERT_TRIGGERED, now, now, normalized_source, alert["symbol"], ALERT_ACTIVE),
            )
        updated = self.get_alert(symbol) or alert
        updated["just_triggered"] = True
        return updated


def buy_plan_alert_status_label(status: object) -> str:
    return ALERT_STATUS_LABELS.get(str(status or "").strip().upper(), "未设置")


def buy_plan_alert_table_label(alert: dict | None) -> str:
    if not alert:
        return ""
    status = str(alert.get("status") or "").strip().upper()
    if status == ALERT_TRIGGERED:
        return "已到计划价"
    if status == ALERT_ACTIVE:
        price = _format_money(alert.get("planned_buy_price"))
        return f"买入提醒 {price}" if price else "买入提醒"
    return ""


def buy_plan_alert_message(alert: dict | None, current_price: object = None) -> str:
    if not alert:
        return "未设置计划买入提醒"
    status = str(alert.get("status") or "").strip().upper()
    planned = _format_money(alert.get("planned_buy_price"))
    shares = int(alert.get("planned_buy_shares") or 0)
    symbol = str(alert.get("symbol") or "").strip().upper()
    current = _format_money(current_price)
    source = _normalize_trigger_source(alert.get("trigger_source"))
    if status == ALERT_TRIGGERED:
        if source == TRIGGER_LAST_CLOSE:
            close_text = f"收盘 {current}" if current else "昨夜收盘"
            return f"{symbol} 昨夜收盘已到达计划买入价：{close_text}，计划 {planned}，今晚开盘重点观察。"
        if source == TRIGGER_PRE_MARKET:
            return f"{symbol} 盘前价格已到计划价，但盘前流动性较低，建议等待开盘确认。"
        if source == TRIGGER_AFTER_HOURS:
            return f"{symbol} 盘后价格已到计划价，建议次日开盘确认。"
        if current:
            return f"{symbol} 已到达计划买入价：当前 {current}，计划 {planned}，提醒买入 {shares} 股。"
        return f"{symbol} 已到达计划买入价：计划 {planned}，提醒买入 {shares} 股。"
    if status == ALERT_ACTIVE:
        return f"已设置：跌到 {planned} 提醒买入 {shares} 股"
    return "未设置计划买入提醒"


def _row_to_dict(columns: list[str], row: object) -> dict:
    if not row:
        return {}
    result = dict(zip(columns, row))
    result["status_label"] = buy_plan_alert_status_label(result.get("status"))
    return result


def _normalize_symbol(symbol: object) -> str:
    text = str(symbol or "").strip().upper()
    if not text:
        raise ValueError("股票代码不能为空")
    return text


def _normalize_trigger_source(value: object) -> str:
    text = str(value or "").strip().upper()
    return text if text in VALID_TRIGGER_SOURCES else TRIGGER_REGULAR


def _required_positive_number(value: object, label: str) -> float:
    number = _number(value)
    if number is None or number <= 0:
        raise ValueError(f"{label}必须大于 0")
    return number


def _required_positive_int(value: object, label: str) -> int:
    number = _number(value)
    if number is None or number <= 0:
        raise ValueError(f"{label}必须大于 0")
    return int(number)


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _format_money(value: object) -> str:
    number = _number(value)
    if number is None:
        return ""
    return f"${number:,.2f}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
