from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from data.cache_read_model import CacheReadModel
from data.prices import CACHE_PATH


HORIZONS = (1, 3, 5, 10, 20)
DRAWNDOWN_LARGE_THRESHOLD_PCT = -8.0

SIGNAL_TYPE_OPTIONS = [
    "低位试仓区",
    "计划买入区",
    "观察承接区",
    "中性持有区",
    "追高风险区",
    "破位重评区",
    "正常洗盘",
    "深度洗盘",
    "极限洗盘",
    "趋势重评",
    "Binance 周末冲高",
    "Alpaca BOATS 兑现",
    "TradingView Webhook 样本",
    "手动信号",
]

SIGNAL_TYPE_DISPLAY_ALIASES = {
    "价格位置": "研报中心",
}

RESULT_LABELS = ["有效", "震荡有效", "买早", "追高", "无效", "数据不足"]


class SignalPerformanceStore:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_performance_records (
                    signal_id TEXT PRIMARY KEY,
                    signal_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    signal_label TEXT NOT NULL,
                    signal_price REAL NOT NULL,
                    price_source TEXT,
                    confidence_score REAL,
                    position_context TEXT,
                    note TEXT,
                    invalidation_price REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    return_1d_pct REAL,
                    return_3d_pct REAL,
                    return_5d_pct REAL,
                    return_10d_pct REAL,
                    return_20d_pct REAL,
                    max_drawdown_pct REAL,
                    made_new_high INTEGER,
                    invalidation_triggered INTEGER,
                    data_status TEXT NOT NULL DEFAULT '待刷新',
                    result_label TEXT NOT NULL DEFAULT '数据不足',
                    outcome_updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_signal_performance_symbol_date
                ON signal_performance_records(symbol, signal_date)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_signal_performance_type_result
                ON signal_performance_records(signal_type, result_label)
                """
            )

    def save_signal(
        self,
        *,
        symbol: object,
        signal_date: object,
        signal_type: object,
        signal_label: object,
        signal_price: object,
        price_source: object = "",
        confidence_score: object = None,
        position_context: object = "",
        note: object = "",
        invalidation_price: object = None,
        signal_id: object = None,
    ) -> dict[str, Any]:
        clean = _clean_signal(
            symbol=symbol,
            signal_date=signal_date,
            signal_type=signal_type,
            signal_label=signal_label,
            signal_price=signal_price,
            price_source=price_source,
            confidence_score=confidence_score,
            position_context=position_context,
            note=note,
            invalidation_price=invalidation_price,
            signal_id=signal_id,
        )
        now = _now()
        clean["created_at"] = now
        clean["updated_at"] = now
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO signal_performance_records (
                    signal_id, signal_date, symbol, signal_type, signal_label, signal_price,
                    price_source, confidence_score, position_context, note, invalidation_price,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id) DO UPDATE SET
                    signal_date = excluded.signal_date,
                    symbol = excluded.symbol,
                    signal_type = excluded.signal_type,
                    signal_label = excluded.signal_label,
                    signal_price = excluded.signal_price,
                    price_source = excluded.price_source,
                    confidence_score = excluded.confidence_score,
                    position_context = excluded.position_context,
                    note = excluded.note,
                    invalidation_price = excluded.invalidation_price,
                    updated_at = excluded.updated_at
                """,
                (
                    clean["signal_id"],
                    clean["signal_date"],
                    clean["symbol"],
                    clean["signal_type"],
                    clean["signal_label"],
                    clean["signal_price"],
                    clean["price_source"],
                    clean["confidence_score"],
                    clean["position_context"],
                    clean["note"],
                    clean["invalidation_price"],
                    clean["created_at"],
                    clean["updated_at"],
                ),
            )
        return self.get_signal(clean["signal_id"]) or clean

    def update_outcome(self, signal_id: object, outcome: dict[str, Any]) -> dict[str, Any]:
        clean_id = str(signal_id or "").strip()
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE signal_performance_records
                SET return_1d_pct = ?,
                    return_3d_pct = ?,
                    return_5d_pct = ?,
                    return_10d_pct = ?,
                    return_20d_pct = ?,
                    max_drawdown_pct = ?,
                    made_new_high = ?,
                    invalidation_triggered = ?,
                    data_status = ?,
                    result_label = ?,
                    outcome_updated_at = ?,
                    updated_at = ?
                WHERE signal_id = ?
                """,
                (
                    _number(outcome.get("return_1d_pct")),
                    _number(outcome.get("return_3d_pct")),
                    _number(outcome.get("return_5d_pct")),
                    _number(outcome.get("return_10d_pct")),
                    _number(outcome.get("return_20d_pct")),
                    _number(outcome.get("max_drawdown_pct")),
                    1 if outcome.get("made_new_high") else 0,
                    1 if outcome.get("invalidation_triggered") else 0,
                    str(outcome.get("data_status") or "数据不足"),
                    str(outcome.get("result_label") or "数据不足"),
                    now,
                    now,
                    clean_id,
                ),
            )
        return self.get_signal(clean_id) or {}

    def get_signal(self, signal_id: object) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM signal_performance_records WHERE signal_id = ?",
                (str(signal_id or "").strip(),),
            ).fetchone()
        return _row_to_dict(row) if row else None

    def list_signals(
        self,
        *,
        symbol: object = "",
        signal_type: object = "",
        result_label: object = "",
        start_date: object = "",
        end_date: object = "",
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[object] = []
        clean_symbol = _normalize_symbol(symbol)
        if clean_symbol:
            clauses.append("symbol = ?")
            params.append(clean_symbol)
        signal_type_values = _signal_type_filter_values(signal_type)
        if signal_type_values:
            placeholders = ", ".join("?" for _ in signal_type_values)
            clauses.append(f"signal_type IN ({placeholders})")
            params.extend(signal_type_values)
        if str(result_label or "").strip():
            clauses.append("result_label = ?")
            params.append(str(result_label).strip())
        if str(start_date or "").strip():
            clauses.append("signal_date >= ?")
            params.append(_date_text(start_date))
        if str(end_date or "").strip():
            clauses.append("signal_date <= ?")
            params.append(_date_text(end_date))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM signal_performance_records
                {where_sql}
                ORDER BY signal_date DESC, created_at DESC
                """,
                params,
            ).fetchall()
        return [_row_to_dict(row) for row in rows]


def refresh_signal_outcomes(store: SignalPerformanceStore, *, symbols: list[str] | None = None) -> dict[str, int]:
    normalized_symbols = {_normalize_symbol(symbol) for symbol in (symbols or []) if _normalize_symbol(symbol)}
    records = store.list_signals()
    updated = 0
    skipped = 0
    for record in records:
        if normalized_symbols and record.get("symbol") not in normalized_symbols:
            skipped += 1
            continue
        history = CacheReadModel(store.path).get_price_history(str(record.get("symbol") or ""))
        outcome = build_signal_outcome(record, history)
        store.update_outcome(record["signal_id"], outcome)
        updated += 1
    return {"updated": updated, "skipped": skipped}


def build_signal_outcome(signal: dict[str, Any], history: pd.DataFrame) -> dict[str, Any]:
    signal_price = _required_positive_number(signal.get("signal_price"), "信号价")
    signal_date = pd.to_datetime(signal.get("signal_date"), errors="coerce")
    if pd.isna(signal_date) or history is None or history.empty:
        return _empty_outcome("数据不足")
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame.get("close"), errors="coerce")
    if "low" not in frame.columns:
        frame["low"] = frame["close"]
    if "high" not in frame.columns:
        frame["high"] = frame["close"]
    frame["low"] = pd.to_numeric(frame["low"], errors="coerce").fillna(frame["close"])
    frame["high"] = pd.to_numeric(frame["high"], errors="coerce").fillna(frame["close"])
    frame = frame.dropna(subset=["date", "close"]).sort_values("date")
    future = frame[frame["date"] > signal_date].head(max(HORIZONS)).reset_index(drop=True)
    if future.empty:
        return _empty_outcome("数据不足")

    outcome: dict[str, Any] = {}
    for horizon in HORIZONS:
        outcome[f"return_{horizon}d_pct"] = _future_return_pct(future, signal_price, horizon)
    window = future.head(max(HORIZONS))
    outcome["max_drawdown_pct"] = _round_pct((window["low"].min() / signal_price - 1.0) * 100.0)
    outcome["made_new_high"] = bool(window["high"].max() > signal_price)
    invalidation_price = _number(signal.get("invalidation_price"))
    outcome["invalidation_triggered"] = bool(invalidation_price is not None and window["low"].min() <= invalidation_price)
    outcome["data_status"] = "完整" if len(future) >= max(HORIZONS) else "数据不足"
    outcome["result_label"] = _result_label(outcome)
    return outcome


def signal_performance_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    win_count = sum(1 for record in records if _number(record.get("return_20d_pct")) is not None and _number(record.get("return_20d_pct")) > 0)
    return {
        "total": total,
        "avg_1d_pct": _average(record.get("return_1d_pct") for record in records),
        "avg_3d_pct": _average(record.get("return_3d_pct") for record in records),
        "avg_5d_pct": _average(record.get("return_5d_pct") for record in records),
        "avg_20d_pct": _average(record.get("return_20d_pct") for record in records),
        "win_rate_pct": (win_count / total * 100.0) if total else None,
        "avg_max_drawdown_pct": _average(record.get("max_drawdown_pct") for record in records),
        "best_signal_type": _best_or_worst_signal_type(records, best=True),
        "worst_signal_type": _best_or_worst_signal_type(records, best=False),
    }


def signal_performance_table_rows(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record in records:
        rows.append(
            {
                "日期": _date_text(record.get("signal_date")),
                "股票": str(record.get("symbol") or ""),
                "信号类型": signal_type_display_label(record.get("signal_label") or record.get("signal_type")),
                "信号价": _money_text(record.get("signal_price")),
                "价格来源": str(record.get("price_source") or "未填写"),
                "1日收益": _pct_text(record.get("return_1d_pct")),
                "3日收益": _pct_text(record.get("return_3d_pct")),
                "5日收益": _pct_text(record.get("return_5d_pct")),
                "20日收益": _pct_text(record.get("return_20d_pct")),
                "最大回撤": _pct_text(record.get("max_drawdown_pct")),
                "结果判定": str(record.get("result_label") or "数据不足"),
            }
        )
    return rows


def infer_price_position_signal_label(display: dict[str, Any] | None) -> str:
    text = " ".join(
        str((display or {}).get(key) or "")
        for key in (
            "current_subzone_display_text",
            "primary_zone_text",
            "current_price_action_text",
            "action_text",
            "radar_status",
            "zone_text",
        )
    )
    if any(token in text for token in ("低位", "试仓", "左侧")):
        return "低位试仓区"
    if any(token in text for token in ("计划", "买入区")):
        return "计划买入区"
    if "承接" in text:
        return "观察承接区"
    if any(token in text for token in ("中性", "持有")):
        return "中性持有区"
    if any(token in text for token in ("追高", "买区上方", "偏高")):
        return "追高风险区"
    if any(token in text for token in ("破位", "重评", "失效", "复核")):
        return "破位重评区"
    return "观察承接区"


def _result_label(outcome: dict[str, Any]) -> str:
    return_5d = _number(outcome.get("return_5d_pct"))
    return_20d = _number(outcome.get("return_20d_pct"))
    max_drawdown = _number(outcome.get("max_drawdown_pct"))
    if return_20d is None:
        return "数据不足"
    large_drawdown = max_drawdown is not None and max_drawdown <= DRAWNDOWN_LARGE_THRESHOLD_PCT
    if return_20d > 0:
        return "买早" if large_drawdown else "有效"
    if large_drawdown:
        return "追高"
    if return_5d is not None and return_5d > 0:
        return "震荡有效"
    return "无效"


def _future_return_pct(future: pd.DataFrame, signal_price: float, horizon: int) -> float | None:
    if len(future) < horizon:
        return None
    close = _number(future.iloc[horizon - 1].get("close"))
    return None if close is None else _round_pct((close / signal_price - 1.0) * 100.0)


def _empty_outcome(status: str) -> dict[str, Any]:
    return {
        "return_1d_pct": None,
        "return_3d_pct": None,
        "return_5d_pct": None,
        "return_10d_pct": None,
        "return_20d_pct": None,
        "max_drawdown_pct": None,
        "made_new_high": False,
        "invalidation_triggered": False,
        "data_status": status,
        "result_label": "数据不足",
    }


def _clean_signal(**values: object) -> dict[str, Any]:
    clean_symbol = _normalize_symbol(values.get("symbol"))
    if not clean_symbol:
        raise ValueError("股票不能为空")
    return {
        "signal_id": str(values.get("signal_id") or f"sig_{uuid.uuid4().hex}").strip(),
        "signal_date": _date_text(values.get("signal_date")),
        "symbol": clean_symbol,
        "signal_type": str(values.get("signal_type") or "手动信号").strip(),
        "signal_label": str(values.get("signal_label") or values.get("signal_type") or "手动记录").strip(),
        "signal_price": _required_positive_number(values.get("signal_price"), "信号价"),
        "price_source": str(values.get("price_source") or "").strip(),
        "confidence_score": _number(values.get("confidence_score")),
        "position_context": str(values.get("position_context") or "").strip(),
        "note": str(values.get("note") or "").strip(),
        "invalidation_price": _number(values.get("invalidation_price")),
    }


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _date_text(value: object) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return datetime.now().date().isoformat()
    return parsed.date().isoformat()


def _required_positive_number(value: object, label: str) -> float:
    number = _number(value)
    if number is None or number <= 0:
        raise ValueError(f"{label}必须大于 0")
    return float(number)


def _number(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in ("made_new_high", "invalidation_triggered"):
        if key in result:
            result[key] = bool(result[key])
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pct_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "数据不足"
    return f"{number:+.2f}%"


def _money_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "数据不足"
    return f"${number:,.2f}"


def _round_pct(value: object) -> float | None:
    number = _number(value)
    return None if number is None else round(float(number), 4)


def _average(values: Any) -> float | None:
    numbers = [_number(value) for value in values]
    present = [number for number in numbers if number is not None]
    return sum(present) / len(present) if present else None


def _best_or_worst_signal_type(records: list[dict[str, Any]], *, best: bool) -> str:
    grouped: dict[str, list[float]] = {}
    for record in records:
        signal_type = signal_type_display_label(record.get("signal_label") or record.get("signal_type"))
        value = _number(record.get("return_20d_pct"))
        if value is None:
            continue
        grouped.setdefault(signal_type, []).append(value)
    if not grouped:
        return "数据不足"
    scored = [(signal_type, sum(values) / len(values)) for signal_type, values in grouped.items()]
    selected = max(scored, key=lambda item: item[1]) if best else min(scored, key=lambda item: item[1])
    return selected[0]


def signal_type_display_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "未标注"
    return SIGNAL_TYPE_DISPLAY_ALIASES.get(text, text)


def _signal_type_filter_values(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    values = {text}
    display = signal_type_display_label(text)
    values.add(display)
    values.update(raw for raw, label in SIGNAL_TYPE_DISPLAY_ALIASES.items() if label == display)
    return sorted(values)
