from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from data.signal_performance import (
    SignalPerformanceStore,
    infer_price_position_signal_label,
    refresh_signal_outcomes,
    signal_performance_table_rows,
)


def _write_history(path: Path, symbol: str, closes: list[float], lows: list[float] | None = None) -> None:
    start = datetime.fromisoformat("2026-01-01")
    lows = lows or closes
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (ticker, date)
            )
            """
        )
        for index, close in enumerate(closes):
            day = (start + timedelta(days=index)).date().isoformat()
            low = lows[index] if index < len(lows) else close
            conn.execute(
                """
                INSERT INTO price_history (ticker, date, open, high, low, close, volume, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (symbol, day, close, close, low, close, 1_000_000, "2026-01-22T00:00:00+00:00"),
            )
        conn.commit()


def test_signal_record_save_and_refresh_calculates_returns_and_drawdown(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite"
    closes = [100, 102, 101, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120]
    lows = [100, 101, 95, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119]
    _write_history(db_path, "NVDA", closes, lows)

    store = SignalPerformanceStore(db_path)
    signal = store.save_signal(
        symbol="NVDA",
        signal_date="2026-01-01",
        signal_type="价格位置",
        signal_label="低位试仓区",
        signal_price=100,
        price_source="本地日线",
    )

    result = refresh_signal_outcomes(store)
    updated = store.get_signal(signal["signal_id"])

    assert result["updated"] == 1
    assert updated["return_1d_pct"] == 2.0
    assert updated["return_3d_pct"] == 3.0
    assert updated["return_5d_pct"] == 5.0
    assert updated["return_20d_pct"] == 20.0
    assert updated["max_drawdown_pct"] == -5.0
    assert updated["result_label"] == "有效"


def test_signal_result_marks_buy_early_when_large_drawdown_recovers(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite"
    _write_history(db_path, "NOW", [100, *([86] * 10), *([101] * 10)], [100, *([84] * 20)])

    store = SignalPerformanceStore(db_path)
    signal = store.save_signal(
        symbol="NOW",
        signal_date="2026-01-01",
        signal_type="价格位置",
        signal_label="观察承接区",
        signal_price=100,
        price_source="本地日线",
    )
    refresh_signal_outcomes(store)

    assert store.get_signal(signal["signal_id"])["result_label"] == "买早"


def test_signal_result_marks_chasing_when_drawdown_stays_negative(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite"
    _write_history(db_path, "ORCL", [100, *([86] * 10), *([94] * 10)], [100, *([84] * 20)])

    store = SignalPerformanceStore(db_path)
    signal = store.save_signal(
        symbol="ORCL",
        signal_date="2026-01-01",
        signal_type="价格位置",
        signal_label="追高风险区",
        signal_price=100,
        price_source="本地日线",
    )
    refresh_signal_outcomes(store)

    assert store.get_signal(signal["signal_id"])["result_label"] == "追高"


def test_signal_outcome_handles_insufficient_data(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.sqlite"
    _write_history(db_path, "ADBE", [100, 101, 102])

    store = SignalPerformanceStore(db_path)
    signal = store.save_signal(
        symbol="ADBE",
        signal_date="2026-01-01",
        signal_type="手动信号",
        signal_label="手动记录",
        signal_price=100,
        price_source="手动",
    )
    refresh_signal_outcomes(store)
    updated = store.get_signal(signal["signal_id"])

    assert updated["return_1d_pct"] == 1.0
    assert updated["return_20d_pct"] is None
    assert updated["data_status"] == "数据不足"
    assert updated["result_label"] == "数据不足"


def test_signal_table_rows_do_not_show_none_or_internal_fields(tmp_path: Path) -> None:
    store = SignalPerformanceStore(tmp_path / "cache.sqlite")
    signal = store.save_signal(
        symbol="NVDA",
        signal_date="2026-01-01",
        signal_type="手动信号",
        signal_label="手动记录",
        signal_price=100,
        price_source="手动",
    )

    row_text = str(signal_performance_table_rows([signal])[0])

    assert "None" not in row_text
    assert "signal_id" not in row_text


def test_infer_price_position_signal_label_uses_chinese_buckets() -> None:
    assert infer_price_position_signal_label({"primary_zone_text": "左侧试仓候选区"}) == "低位试仓区"
    assert infer_price_position_signal_label({"primary_zone_text": "承接观察区内"}) == "观察承接区"
    assert infer_price_position_signal_label({"primary_zone_text": "买区上方，追高风险"}) == "追高风险区"
