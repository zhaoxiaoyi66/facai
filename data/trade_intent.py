from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from data.prices import CACHE_PATH


BUY_INTENT_FIELDS = {
    "primary_intent": [
        "计划内买入",
        "加深已有方向",
        "回补前次卖出",
        "试探观察仓",
        "价格到位执行",
        "怕错过",
        "参与感小仓",
    ],
    "position_intent": [
        "让组合更集中",
        "保持现有结构",
        "替换其他持仓",
        "提高现金使用",
        "小仓观察",
    ],
    "timing_intent": [
        "到达计划价",
        "量价承接改善",
        "分批第一笔",
        "分批追加",
        "临时决定",
    ],
    "risk_intent": [
        "按计划执行",
        "接受波动后复盘",
        "先小额观察",
        "不确定但想参与",
    ],
}

SELL_INTENT_FIELDS = {
    "primary_intent": [
        "计划内止盈",
        "计划内止损",
        "降低仓位风险",
        "换仓",
        "清仓结束跟踪",
        "情绪压力",
        "释放现金",
    ],
    "position_intent": [
        "降低集中度",
        "释放现金",
        "保留底仓",
        "完全退出",
        "等待回补",
    ],
    "timing_intent": [
        "到达目标价",
        "跌破计划线",
        "财报前调整",
        "事件后兑现",
        "临时决定",
    ],
    "risk_intent": [
        "按计划执行",
        "担心继续回撤",
        "担心卖飞",
        "先降低情绪压力",
    ],
}

INTENT_FIELD_LABELS = {
    "primary_intent": "这笔交易主要是",
    "position_intent": "仓位意图",
    "timing_intent": "触发原因",
    "risk_intent": "当下真实状态",
}


class TradeIntentStore:
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
                CREATE TABLE IF NOT EXISTS trade_intent_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_entry_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    intent_side TEXT NOT NULL,
                    primary_intent TEXT,
                    position_intent TEXT,
                    timing_intent TEXT,
                    risk_intent TEXT,
                    source TEXT,
                    payload_json TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(trade_entry_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trade_intent_records_symbol_date
                ON trade_intent_records(symbol, created_at)
                """
            )

    def save_intent(
        self,
        trade_entry_id: int,
        symbol: str,
        action_type: str,
        intent: dict[str, Any],
        *,
        source: str = "trade_entry",
    ) -> dict[str, Any]:
        clean_id = int(trade_entry_id)
        if clean_id <= 0:
            raise ValueError("trade_entry_id is required")
        normalized = normalize_trade_intent_payload(intent)
        if not normalized:
            return {}
        now = _hkt_now()
        ticker = str(symbol or "").strip().upper()
        action = str(action_type or "").strip().lower()
        payload_json = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO trade_intent_records (
                    trade_entry_id,
                    symbol,
                    action_type,
                    intent_side,
                    primary_intent,
                    position_intent,
                    timing_intent,
                    risk_intent,
                    source,
                    payload_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_entry_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    action_type = excluded.action_type,
                    intent_side = excluded.intent_side,
                    primary_intent = excluded.primary_intent,
                    position_intent = excluded.position_intent,
                    timing_intent = excluded.timing_intent,
                    risk_intent = excluded.risk_intent,
                    source = excluded.source,
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at
                """,
                (
                    clean_id,
                    ticker,
                    action,
                    normalized["intent_side"],
                    normalized.get("primary_intent"),
                    normalized.get("position_intent"),
                    normalized.get("timing_intent"),
                    normalized.get("risk_intent"),
                    str(source or "trade_entry"),
                    payload_json,
                    now,
                ),
            )
        return self.get_intent_for_trade(clean_id) or normalized

    def get_intent_for_trade(self, trade_entry_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM trade_intent_records WHERE trade_entry_id = ?", (int(trade_entry_id),))
            row = cursor.fetchone()
            columns = [item[0] for item in cursor.description] if cursor.description else []
        return _row_to_dict(columns, row) if row else None

    def list_intents(self, symbol: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if symbol:
                cursor = conn.execute(
                    """
                    SELECT *
                    FROM trade_intent_records
                    WHERE UPPER(symbol) = ?
                    ORDER BY created_at DESC, id DESC
                    """,
                    (str(symbol).strip().upper(),),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT *
                    FROM trade_intent_records
                    ORDER BY created_at DESC, id DESC
                    """
                )
            rows = cursor.fetchall()
            columns = [item[0] for item in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]


def normalize_trade_intent_payload(payload: Any, *, side: str | None = None) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    clean_side = _clean_side(payload.get("intent_side") or payload.get("side") or side)
    if not clean_side:
        return {}
    options = BUY_INTENT_FIELDS if clean_side == "buy" else SELL_INTENT_FIELDS
    result: dict[str, str] = {"intent_side": clean_side}
    for field, allowed in options.items():
        value = str(payload.get(field) or "").strip()
        if value not in allowed:
            value = allowed[0]
        result[field] = value
    return result


def intent_side_for_action(action_type: object) -> str:
    action = str(action_type or "").strip().lower()
    if action in {"sell", "trim", "close", "exit"}:
        return "sell"
    return "buy"


def intent_title(side: str) -> str:
    return "卖出前记录" if str(side or "").strip().lower() == "sell" else "买入前记录"


def _clean_side(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"buy", "sell"}:
        return text
    return ""


def _hkt_now() -> str:
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds")


def _row_to_dict(columns: list[str], row: Any) -> dict[str, Any]:
    result = dict(zip(columns, row))
    raw_payload = str(result.get("payload_json") or "").strip()
    try:
        result["payload"] = json.loads(raw_payload) if raw_payload else {}
    except json.JSONDecodeError:
        result["payload"] = {}
    return result
