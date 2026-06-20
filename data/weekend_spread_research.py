from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo

from settings import PROJECT_ROOT


DEFAULT_RESEARCH_DB_PATH = PROJECT_ROOT / "data" / "cache" / "weekend_spread_research.sqlite"
ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")
DEFAULT_TICK_INTERVAL_MINUTES = 3.0


def append_monitor_ticks(
    rows: Iterable[dict[str, Any]],
    *,
    db_path: Path = DEFAULT_RESEARCH_DB_PATH,
    run_id: str = "",
    scan_time: datetime | str | None = None,
) -> int:
    normalized = [_monitor_tick_record(row, run_id=run_id, scan_time=scan_time) for row in rows or []]
    if not normalized:
        _init_db(db_path)
        return 0
    with _connect(db_path) as conn:
        _init_schema(conn)
        with conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO weekend_spread_monitor_ticks (
                    run_id, scan_time_et, scan_time_hkt, week_id, ticker, binance_symbol,
                    anchor_price, anchor_time_et, binance_price, premium_pct,
                    regular_close_price, vs_regular_close_pct, atr14_pct, avg_range_20d_pct,
                    spread_atr_ratio, spread_reasonableness, news_label, trend_label, created_at
                ) VALUES (
                    :run_id, :scan_time_et, :scan_time_hkt, :week_id, :ticker, :binance_symbol,
                    :anchor_price, :anchor_time_et, :binance_price, :premium_pct,
                    :regular_close_price, :vs_regular_close_pct, :atr14_pct, :avg_range_20d_pct,
                    :spread_atr_ratio, :spread_reasonableness, :news_label, :trend_label, :created_at
                )
                """,
                normalized,
            )
    return len(normalized)


def build_weekend_spread_research_samples(
    week_id: str | None = None,
    *,
    db_path: Path = DEFAULT_RESEARCH_DB_PATH,
    ticks: Iterable[dict[str, Any]] | None = None,
    backtest_rows: Iterable[dict[str, Any]] | None = None,
    news_contexts: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_ticks = [_normalize_tick(row) for row in ticks] if ticks is not None else list_monitor_ticks(db_path=db_path, week_id=week_id)
    if week_id:
        source_ticks = [row for row in source_ticks if row.get("week_id") == week_id]
    source_ticks = [row for row in source_ticks if row.get("ticker") and row.get("week_id")]
    events = build_premium_events(source_ticks)
    samples = build_research_samples(source_ticks, backtest_rows=backtest_rows, news_contexts=news_contexts)
    week_ids = sorted({str(row.get("week_id") or "") for row in source_ticks if str(row.get("week_id") or "")})
    with _connect(db_path) as conn:
        _init_schema(conn)
        with conn:
            _delete_existing_research_rows(conn, week_ids)
            _replace_events(conn, events)
            _replace_samples(conn, samples)
    return {"events": events, "samples": samples, "event_count": len(events), "sample_count": len(samples)}


def build_premium_events(ticks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = _group_ticks(ticks)
    events: list[dict[str, Any]] = []
    for (week_id, ticker), rows in grouped.items():
        active: list[dict[str, Any]] = []
        for row in rows:
            if _event_start_row(row):
                active.append(row)
                continue
            if active:
                events.append(_event_from_rows(week_id, ticker, active, close_row=row))
                active = []
        if active:
            events.append(_event_from_rows(week_id, ticker, active, close_row=None))
    return events


def build_research_samples(
    ticks: Iterable[dict[str, Any]],
    *,
    backtest_rows: Iterable[dict[str, Any]] | None = None,
    news_contexts: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    grouped = _group_ticks(ticks)
    backtest_map = {
        (str(row.get("week_id") or "").strip(), str(row.get("ticker") or "").strip().upper()): dict(row)
        for row in (backtest_rows or [])
        if isinstance(row, dict)
    }
    samples: list[dict[str, Any]] = []
    for (week_id, ticker), rows in grouped.items():
        news = dict((news_contexts or {}).get((week_id, ticker)) or {})
        backtest = backtest_map.get((week_id, ticker), {})
        premiums = [_number(row.get("premium_pct")) for row in rows]
        premiums = [float(value) for value in premiums if value is not None]
        if not premiums:
            continue
        max_row = max(rows, key=lambda row: _number(row.get("premium_pct")) or float("-inf"))
        min_row = min(rows, key=lambda row: _number(row.get("premium_pct")) or float("inf"))
        max_atr = max((_number(row.get("spread_atr_ratio")) or 0.0 for row in rows), default=0.0)
        news_count = int(news.get("news_count") or _count_news(rows))
        major_news_count = int(news.get("major_news_count") or _count_major_news(rows))
        sample_quality = _sample_quality(backtest, max_atr=max_atr, major_news_count=major_news_count)
        sample_id = _stable_id("sample", week_id, ticker)
        samples.append(
            {
                "sample_id": sample_id,
                "week_id": week_id,
                "ticker": ticker,
                "binance_symbol": str(rows[-1].get("binance_symbol") or ""),
                "p0_price": _first_number(rows, "anchor_price"),
                "p0_time_et": str(rows[0].get("anchor_time_et") or ""),
                "p1_max_price": _number(max_row.get("binance_price")),
                "p1_max_time_et": str(max_row.get("scan_time_et") or ""),
                "p1_min_price": _number(min_row.get("binance_price")),
                "last_binance_price_before_open": _number(rows[-1].get("binance_price")),
                "p2_price": _number(backtest.get("broker_open_close") or backtest.get("p2_price")),
                "p2_time_et": str(backtest.get("p2_first_valid_time") or backtest.get("p2_time_et") or ""),
                "p2_delay_minutes": _number(backtest.get("p2_delay_minutes")),
                "max_premium_pct": max(premiums),
                "max_discount_pct": min(premiums),
                "avg_premium_pct": sum(premiums) / len(premiums),
                "max_spread_atr_ratio": max_atr if max_atr else None,
                "premium_duration_minutes": _duration_for_rows(rows, lambda row: (_number(row.get("premium_pct")) or 0) >= 2),
                "discount_duration_minutes": _duration_for_rows(rows, lambda row: (_number(row.get("premium_pct")) or 0) <= -2),
                "extreme_spread_count": sum(1 for row in rows if abs(_number(row.get("premium_pct")) or 0) >= 5 or (_number(row.get("spread_atr_ratio")) or 0) >= 2),
                "news_label": str(news.get("gap_explanation_label") or _dominant_news_label(rows) or "无新闻解释"),
                "news_count": news_count,
                "major_news_count": major_news_count,
                "first_minute_liquidity": _first_minute_liquidity(backtest),
                "capture_pct": _number(backtest.get("capture_pct")),
                "final_transmission_pct": _number(backtest.get("overnight_vs_afterhours_pct") or backtest.get("final_transmission_pct")),
                "sample_quality": sample_quality,
                "created_at": _now_utc_text(),
            }
        )
    return samples


def cleanup_old_monitor_ticks(*, db_path: Path = DEFAULT_RESEARCH_DB_PATH, days: int = 30, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)).astimezone(ET) - timedelta(days=days)
    cutoff_text = cutoff.isoformat()
    with _connect(db_path) as conn:
        _init_schema(conn)
        with conn:
            cursor = conn.execute("DELETE FROM weekend_spread_monitor_ticks WHERE scan_time_et < ?", (cutoff_text,))
            return int(cursor.rowcount or 0)


def list_monitor_ticks(*, db_path: Path = DEFAULT_RESEARCH_DB_PATH, week_id: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        _init_schema(conn)
        query = "SELECT * FROM weekend_spread_monitor_ticks"
        params: list[Any] = []
        if week_id:
            query += " WHERE week_id = ?"
            params.append(week_id)
        query += " ORDER BY scan_time_et ASC, ticker ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def list_premium_events(*, db_path: Path = DEFAULT_RESEARCH_DB_PATH, week_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        _init_schema(conn)
        query = "SELECT * FROM weekend_spread_premium_events"
        params: list[Any] = []
        if week_id:
            query += " WHERE week_id = ?"
            params.append(week_id)
        query += " ORDER BY event_start_et DESC, ticker ASC LIMIT ?"
        params.append(int(limit))
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def list_research_samples(*, db_path: Path = DEFAULT_RESEARCH_DB_PATH, week_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        _init_schema(conn)
        query = "SELECT * FROM weekend_spread_research_samples"
        params: list[Any] = []
        if week_id:
            query += " WHERE week_id = ?"
            params.append(week_id)
        query += " ORDER BY week_id DESC, ticker ASC LIMIT ?"
        params.append(int(limit))
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def research_summary(*, db_path: Path = DEFAULT_RESEARCH_DB_PATH) -> dict[str, int]:
    with _connect(db_path) as conn:
        _init_schema(conn)
        return {
            "scan_count": int(conn.execute("SELECT COUNT(DISTINCT run_id) FROM weekend_spread_monitor_ticks").fetchone()[0] or 0),
            "tick_count": int(conn.execute("SELECT COUNT(*) FROM weekend_spread_monitor_ticks").fetchone()[0] or 0),
            "effective_ticker_count": int(conn.execute("SELECT COUNT(DISTINCT ticker) FROM weekend_spread_monitor_ticks").fetchone()[0] or 0),
            "event_count": int(conn.execute("SELECT COUNT(*) FROM weekend_spread_premium_events").fetchone()[0] or 0),
            "extreme_event_count": int(conn.execute("SELECT COUNT(*) FROM weekend_spread_premium_events WHERE max_spread_atr_ratio >= 2 OR ABS(max_premium_pct) >= 5 OR ABS(min_premium_pct) >= 5").fetchone()[0] or 0),
            "no_news_extreme_count": int(conn.execute("SELECT COUNT(*) FROM weekend_spread_research_samples WHERE sample_quality = '无新闻极端价差'").fetchone()[0] or 0),
            "first_minute_count": int(conn.execute("SELECT COUNT(*) FROM weekend_spread_research_samples WHERE sample_quality = '首分钟样本'").fetchone()[0] or 0),
            "delayed_count": int(conn.execute("SELECT COUNT(*) FROM weekend_spread_research_samples WHERE sample_quality = '延迟成交样本'").fetchone()[0] or 0),
        }


def research_path_for_snapshot(snapshot_path: Path) -> Path:
    if snapshot_path == DEFAULT_RESEARCH_DB_PATH:
        return snapshot_path
    return snapshot_path.parent / "weekend_spread_research.sqlite"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(path: Path) -> None:
    with _connect(path) as conn:
        _init_schema(conn)


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weekend_spread_monitor_ticks (
            run_id TEXT NOT NULL,
            scan_time_et TEXT NOT NULL,
            scan_time_hkt TEXT NOT NULL,
            week_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            binance_symbol TEXT NOT NULL,
            anchor_price REAL,
            anchor_time_et TEXT,
            binance_price REAL,
            premium_pct REAL,
            regular_close_price REAL,
            vs_regular_close_pct REAL,
            atr14_pct REAL,
            avg_range_20d_pct REAL,
            spread_atr_ratio REAL,
            spread_reasonableness TEXT,
            news_label TEXT,
            trend_label TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, ticker)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weekend_spread_premium_events (
            event_id TEXT PRIMARY KEY,
            week_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            binance_symbol TEXT,
            direction TEXT,
            event_start_et TEXT,
            event_end_et TEXT,
            duration_minutes REAL,
            start_premium_pct REAL,
            max_premium_pct REAL,
            min_premium_pct REAL,
            peak_time_et TEXT,
            end_premium_pct REAL,
            max_spread_atr_ratio REAL,
            minutes_above_2pct REAL,
            minutes_above_5pct REAL,
            minutes_above_1_5x_atr REAL,
            converged_before_open INTEGER,
            news_label TEXT,
            event_quality TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weekend_spread_research_samples (
            sample_id TEXT PRIMARY KEY,
            week_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            binance_symbol TEXT,
            p0_price REAL,
            p0_time_et TEXT,
            p1_max_price REAL,
            p1_max_time_et TEXT,
            p1_min_price REAL,
            last_binance_price_before_open REAL,
            p2_price REAL,
            p2_time_et TEXT,
            p2_delay_minutes REAL,
            max_premium_pct REAL,
            max_discount_pct REAL,
            avg_premium_pct REAL,
            max_spread_atr_ratio REAL,
            premium_duration_minutes REAL,
            discount_duration_minutes REAL,
            extreme_spread_count INTEGER,
            news_label TEXT,
            news_count INTEGER,
            major_news_count INTEGER,
            first_minute_liquidity TEXT,
            capture_pct REAL,
            final_transmission_pct REAL,
            sample_quality TEXT,
            created_at TEXT
        )
        """
    )


def _replace_events(conn: sqlite3.Connection, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO weekend_spread_premium_events (
            event_id, week_id, ticker, binance_symbol, direction, event_start_et, event_end_et,
            duration_minutes, start_premium_pct, max_premium_pct, min_premium_pct, peak_time_et,
            end_premium_pct, max_spread_atr_ratio, minutes_above_2pct, minutes_above_5pct,
            minutes_above_1_5x_atr, converged_before_open, news_label, event_quality, created_at
        ) VALUES (
            :event_id, :week_id, :ticker, :binance_symbol, :direction, :event_start_et, :event_end_et,
            :duration_minutes, :start_premium_pct, :max_premium_pct, :min_premium_pct, :peak_time_et,
            :end_premium_pct, :max_spread_atr_ratio, :minutes_above_2pct, :minutes_above_5pct,
            :minutes_above_1_5x_atr, :converged_before_open, :news_label, :event_quality, :created_at
        )
        """,
        events,
    )


def _delete_existing_research_rows(conn: sqlite3.Connection, week_ids: list[str]) -> None:
    if not week_ids:
        return
    placeholders = ",".join("?" for _ in week_ids)
    conn.execute(f"DELETE FROM weekend_spread_premium_events WHERE week_id IN ({placeholders})", week_ids)
    conn.execute(f"DELETE FROM weekend_spread_research_samples WHERE week_id IN ({placeholders})", week_ids)


def _replace_samples(conn: sqlite3.Connection, samples: list[dict[str, Any]]) -> None:
    if not samples:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO weekend_spread_research_samples (
            sample_id, week_id, ticker, binance_symbol, p0_price, p0_time_et, p1_max_price,
            p1_max_time_et, p1_min_price, last_binance_price_before_open, p2_price, p2_time_et,
            p2_delay_minutes, max_premium_pct, max_discount_pct, avg_premium_pct,
            max_spread_atr_ratio, premium_duration_minutes, discount_duration_minutes,
            extreme_spread_count, news_label, news_count, major_news_count, first_minute_liquidity,
            capture_pct, final_transmission_pct, sample_quality, created_at
        ) VALUES (
            :sample_id, :week_id, :ticker, :binance_symbol, :p0_price, :p0_time_et, :p1_max_price,
            :p1_max_time_et, :p1_min_price, :last_binance_price_before_open, :p2_price, :p2_time_et,
            :p2_delay_minutes, :max_premium_pct, :max_discount_pct, :avg_premium_pct,
            :max_spread_atr_ratio, :premium_duration_minutes, :discount_duration_minutes,
            :extreme_spread_count, :news_label, :news_count, :major_news_count, :first_minute_liquidity,
            :capture_pct, :final_transmission_pct, :sample_quality, :created_at
        )
        """,
        samples,
    )


def _monitor_tick_record(row: dict[str, Any], *, run_id: str, scan_time: datetime | str | None) -> dict[str, Any]:
    normalized = _normalize_tick(row)
    if run_id:
        normalized["run_id"] = run_id
    if scan_time:
        parsed = _parse_time(scan_time)
        normalized["scan_time_et"] = parsed.astimezone(ET).isoformat()
        normalized["scan_time_hkt"] = parsed.astimezone(HKT).isoformat()
        normalized["week_id"] = _week_id(parsed.astimezone(ET))
    return normalized


def _normalize_tick(row: dict[str, Any]) -> dict[str, Any]:
    scan = _parse_time(row.get("scan_time"))
    scan_et = scan.astimezone(ET)
    anchor_time = _parse_optional_time(row.get("anchor_time") or row.get("anchor_time_et"))
    anchor_time_et = anchor_time.astimezone(ET).isoformat() if anchor_time else str(row.get("anchor_time_et") or "")
    regular_close = _number(row.get("regular_close_price"))
    binance_price = _number(row.get("binance_price"))
    vs_regular = _number(row.get("vs_regular_close_pct"))
    if vs_regular is None and regular_close and regular_close > 0 and binance_price is not None:
        vs_regular = (binance_price / regular_close - 1) * 100.0
    return {
        "run_id": str(row.get("run_id") or ""),
        "scan_time_et": scan_et.isoformat(),
        "scan_time_hkt": scan.astimezone(HKT).isoformat(),
        "week_id": str(row.get("week_id") or _week_id(scan_et)),
        "ticker": str(row.get("ticker") or "").strip().upper(),
        "binance_symbol": str(row.get("binance_symbol") or "").strip().upper(),
        "anchor_price": _number(row.get("anchor_price")),
        "anchor_time_et": anchor_time_et,
        "binance_price": binance_price,
        "premium_pct": _number(row.get("premium_pct")),
        "regular_close_price": regular_close,
        "vs_regular_close_pct": vs_regular,
        "atr14_pct": _number(row.get("atr14_pct")),
        "avg_range_20d_pct": _number(row.get("avg_range_20d_pct") or row.get("avg_range_20d")),
        "spread_atr_ratio": _number(row.get("spread_atr_ratio")),
        "spread_reasonableness": str(row.get("spread_reasonableness") or row.get("spread_reasonableness_label") or ""),
        "news_label": str(row.get("news_label") or row.get("closed_market_news_label") or ""),
        "trend_label": str(row.get("trend_label") or row.get("premium_trend_label") or ""),
        "created_at": str(row.get("created_at") or _now_utc_text()),
    }


def _group_ticks(ticks: Iterable[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for raw in ticks or []:
        row = _normalize_tick(raw)
        if not row.get("week_id") or not row.get("ticker"):
            continue
        grouped.setdefault((str(row["week_id"]), str(row["ticker"])), []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: str(row.get("scan_time_et") or ""))
    return grouped


def _event_start_row(row: dict[str, Any]) -> bool:
    premium = abs(_number(row.get("premium_pct")) or 0.0)
    ratio = _number(row.get("spread_atr_ratio")) or 0.0
    return premium >= 2.0 or ratio >= 1.5


def _event_from_rows(week_id: str, ticker: str, rows: list[dict[str, Any]], *, close_row: dict[str, Any] | None) -> dict[str, Any]:
    start = rows[0]
    end = close_row or rows[-1]
    premiums = [float(_number(row.get("premium_pct")) or 0.0) for row in rows]
    max_premium = max(premiums)
    min_premium = min(premiums)
    direction = "溢价" if abs(max_premium) >= abs(min_premium) else "折价"
    peak_row = max(rows, key=lambda row: abs(_number(row.get("premium_pct")) or 0.0))
    max_ratio = max((_number(row.get("spread_atr_ratio")) or 0.0 for row in rows), default=0.0)
    duration = _duration_for_rows(rows, lambda row: True)
    event = {
        "event_id": _stable_id("event", week_id, ticker, str(start.get("scan_time_et") or "")),
        "week_id": week_id,
        "ticker": ticker,
        "binance_symbol": str(start.get("binance_symbol") or ""),
        "direction": direction,
        "event_start_et": str(start.get("scan_time_et") or ""),
        "event_end_et": str(end.get("scan_time_et") or rows[-1].get("scan_time_et") or ""),
        "duration_minutes": duration,
        "start_premium_pct": _number(start.get("premium_pct")),
        "max_premium_pct": max_premium,
        "min_premium_pct": min_premium,
        "peak_time_et": str(peak_row.get("scan_time_et") or ""),
        "end_premium_pct": _number(end.get("premium_pct")),
        "max_spread_atr_ratio": max_ratio if max_ratio else None,
        "minutes_above_2pct": _duration_for_rows(rows, lambda row: abs(_number(row.get("premium_pct")) or 0.0) >= 2),
        "minutes_above_5pct": _duration_for_rows(rows, lambda row: abs(_number(row.get("premium_pct")) or 0.0) >= 5),
        "minutes_above_1_5x_atr": _duration_for_rows(rows, lambda row: (_number(row.get("spread_atr_ratio")) or 0.0) >= 1.5),
        "converged_before_open": 1 if close_row is not None else 0,
        "news_label": _dominant_news_label(rows),
        "event_quality": _event_quality(rows, duration=duration, max_ratio=max_ratio),
        "created_at": _now_utc_text(),
    }
    return event


def _event_quality(rows: list[dict[str, Any]], *, duration: float, max_ratio: float) -> str:
    if any("新闻" in str(row.get("news_label") or "") and "无" not in str(row.get("news_label") or "") for row in rows):
        return "新闻驱动"
    if any(str(row.get("spread_reasonableness") or "") == "数据不足" for row in rows):
        return "数据待核"
    if duration <= DEFAULT_TICK_INTERVAL_MINUTES or len(rows) <= 1:
        return "瞬时插针"
    if max_ratio and max_ratio < 1.0:
        return "流动性不足"
    return "高质量事件"


def _sample_quality(backtest: dict[str, Any], *, max_atr: float, major_news_count: int) -> str:
    if max_atr >= 2.0 and major_news_count <= 0:
        return "无新闻极端价差"
    delay = _number(backtest.get("p2_delay_minutes"))
    p2 = _number(backtest.get("broker_open_close") or backtest.get("p2_price"))
    if p2 is None:
        return "仅观察样本"
    if delay is None:
        return "仅观察样本"
    if delay == 0:
        return "首分钟样本"
    if delay > 0:
        return "延迟成交样本"
    return "数据不足"


def _first_minute_liquidity(backtest: dict[str, Any]) -> str:
    delay = _number(backtest.get("p2_delay_minutes"))
    if delay is None:
        return "数据不足"
    return "首分钟" if delay == 0 else "延迟成交"


def _duration_for_rows(rows: list[dict[str, Any]], predicate) -> float:
    if not rows:
        return 0.0
    total = 0.0
    for index, row in enumerate(rows):
        if not predicate(row):
            continue
        current = _parse_time(row.get("scan_time_et"))
        if index + 1 < len(rows):
            next_time = _parse_time(rows[index + 1].get("scan_time_et"))
            minutes = max(0.0, (next_time - current).total_seconds() / 60.0)
        else:
            minutes = DEFAULT_TICK_INTERVAL_MINUTES
        total += minutes if minutes > 0 else DEFAULT_TICK_INTERVAL_MINUTES
    return total


def _count_news(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if str(row.get("news_label") or "").strip() and str(row.get("news_label") or "").strip() != "无新闻解释")


def _count_major_news(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if "重大" in str(row.get("news_label") or ""))


def _dominant_news_label(rows: list[dict[str, Any]]) -> str:
    labels = [str(row.get("news_label") or "").strip() for row in rows if str(row.get("news_label") or "").strip()]
    if not labels:
        return "无新闻解释"
    for keyword in ("重大", "新闻方向一致", "有新闻", "观点文章"):
        for label in labels:
            if keyword in label:
                return label
    return labels[-1]


def _first_number(rows: list[dict[str, Any]], key: str) -> float | None:
    for row in rows:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _parse_time(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return datetime.now(timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_optional_time(value: Any) -> datetime | None:
    try:
        return _parse_time(value)
    except Exception:
        return None


def _week_id(time_et: datetime) -> str:
    iso = time_et.date().isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _stable_id(*parts: object) -> str:
    return uuid5(NAMESPACE_URL, "|".join(str(part) for part in parts)).hex


def _now_utc_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
