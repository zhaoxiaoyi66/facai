from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo

from settings import PROJECT_ROOT
from data.weekend_spread_news import (
    NEWS_STATUS_DIRECTION_MATCH,
    NEWS_STATUS_DIRECTION_MISMATCH,
    NEWS_STATUS_EXPLAINED,
    NEWS_STATUS_FAILED,
    NEWS_STATUS_INSUFFICIENT,
    NEWS_STATUS_NO_MAJOR,
    NEWS_STATUS_NO_RELEVANT,
    NEWS_STATUS_OPINION,
    NEWS_STATUS_UNCHECKED,
    WeekendSpreadNewsStore,
    build_weekend_spread_news_status,
    current_shutdown_news_sample,
)


DEFAULT_RESEARCH_DB_PATH = PROJECT_ROOT / "data" / "cache" / "weekend_spread_research.sqlite"
ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")
DEFAULT_TICK_INTERVAL_MINUTES = 3.0
NEWS_CHECKED_STATUSES = {
    NEWS_STATUS_NO_RELEVANT,
    NEWS_STATUS_NO_MAJOR,
    NEWS_STATUS_EXPLAINED,
    NEWS_STATUS_DIRECTION_MATCH,
    NEWS_STATUS_DIRECTION_MISMATCH,
    NEWS_STATUS_OPINION,
}
NEWS_EXPLAINED_STATUSES = {NEWS_STATUS_EXPLAINED, NEWS_STATUS_DIRECTION_MATCH}


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
    now: datetime | None = None,
    news_store: WeekendSpreadNewsStore | None = None,
) -> dict[str, Any]:
    source_ticks = [_normalize_tick(row) for row in ticks] if ticks is not None else list_monitor_ticks(db_path=db_path, week_id=week_id)
    if week_id:
        source_ticks = [row for row in source_ticks if row.get("week_id") == week_id]
    source_ticks = [row for row in source_ticks if row.get("ticker") and row.get("week_id")]
    news_map = _build_news_status_map(source_ticks, news_contexts=news_contexts, news_store=news_store)
    events = _apply_news_to_events(build_premium_events(source_ticks), news_map, now=now)
    samples = build_research_samples(source_ticks, backtest_rows=backtest_rows, news_contexts=news_map, now=now)
    week_ids = sorted({str(row.get("week_id") or "") for row in source_ticks if str(row.get("week_id") or "")})
    with _connect(db_path) as conn:
        _init_schema(conn)
        with conn:
            _delete_existing_research_rows(conn, week_ids)
            _replace_events(conn, events)
            _replace_samples(conn, samples)
    report = build_generation_report(source_ticks, events, samples)
    return {"events": events, "samples": samples, "event_count": len(events), "sample_count": len(samples), "report": report}


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
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    grouped = _group_ticks(ticks)
    backtest_map = {
        (str(row.get("week_id") or "").strip(), str(row.get("ticker") or "").strip().upper()): dict(row)
        for row in (backtest_rows or [])
        if isinstance(row, dict)
    }
    samples: list[dict[str, Any]] = []
    for (week_id, ticker), rows in grouped.items():
        news = _normalize_news_status(dict((news_contexts or {}).get((week_id, ticker)) or {}))
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
        p2_status = _p2_status(backtest, week_id=week_id, now=now)
        sample_quality = _sample_quality(
            backtest,
            max_atr=max_atr,
            major_news_count=major_news_count,
            news_status=str(news.get("news_status") or NEWS_STATUS_UNCHECKED),
            p2_status=p2_status,
            rows=rows,
        )
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
                "news_label": str(news.get("gap_news_explanation") or news.get("gap_explanation_label") or _dominant_news_label(rows) or "未检查"),
                "news_status": str(news.get("news_status") or NEWS_STATUS_UNCHECKED),
                "opinion_news_count": int(news.get("opinion_news_count") or 0),
                "latest_news_time": str(news.get("latest_news_time") or ""),
                "news_checked_at": str(news.get("last_checked_at") or news.get("news_checked_at") or ""),
                "news_fetch_status": str(news.get("fetch_status") or news.get("news_fetch_status") or ""),
                "news_error": str(news.get("fetch_error") or news.get("news_error") or ""),
                "p2_status": p2_status,
                "first_minute_liquidity": _first_minute_liquidity(backtest),
                "capture_pct": _number(backtest.get("capture_pct")),
                "final_transmission_pct": _number(backtest.get("overnight_vs_afterhours_pct") or backtest.get("final_transmission_pct")),
                "sample_quality": sample_quality,
                "data_health_label": _sample_data_health_label(rows),
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
            "news_checked_count": int(conn.execute("SELECT COUNT(*) FROM weekend_spread_research_samples WHERE news_status NOT IN ('', '未检查')").fetchone()[0] or 0),
            "news_unchecked_count": int(conn.execute("SELECT COUNT(*) FROM weekend_spread_research_samples WHERE COALESCE(news_status, '') IN ('', '未检查')").fetchone()[0] or 0),
            "pending_p2_count": int(conn.execute("SELECT COUNT(*) FROM weekend_spread_research_samples WHERE sample_quality = '等待夜盘验证' OR p2_status = '未到夜盘时间'").fetchone()[0] or 0),
        }


def monitor_recording_health(
    *,
    db_path: Path = DEFAULT_RESEARCH_DB_PATH,
    week_id: str | None = None,
    ticks: Iterable[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    rows = [_normalize_tick(row) for row in ticks] if ticks is not None else list_monitor_ticks(db_path=db_path, week_id=week_id)
    if week_id:
        rows = [row for row in rows if row.get("week_id") == week_id]
    rows = [row for row in rows if row.get("ticker") and row.get("scan_time_et")]
    if not rows:
        return {
            "window_label": "暂无监控记录",
            "first_tick_time": "",
            "latest_tick_time": "",
            "expected_scan_count": 0,
            "actual_scan_count": 0,
            "coverage_pct": 0.0,
            "max_gap_minutes": 0.0,
            "effective_ticker_count": 0,
            "avg_effective_tickers_per_scan": 0.0,
            "anchor_missing_count": 0,
            "binance_missing_count": 0,
            "volatility_missing_count": 0,
            "news_checked_count": 0,
            "news_unchecked_count": 0,
            "health_warning": "暂无监控扫描记录。",
        }
    times = sorted({_parse_time(row.get("scan_time_et")) for row in rows})
    first_time = times[0]
    latest_time = times[-1]
    end_time = (now or latest_time).astimezone(ET)
    if end_time < latest_time:
        end_time = latest_time
    expected = max(1, int(((end_time - first_time).total_seconds() / 60.0) // DEFAULT_TICK_INTERVAL_MINUTES) + 1)
    actual = len(times)
    coverage = min(100.0, (actual / expected) * 100.0) if expected else 0.0
    max_gap = 0.0
    for previous, current in zip(times, times[1:]):
        max_gap = max(max_gap, (current - previous).total_seconds() / 60.0)
    scan_groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("run_id") or row.get("scan_time_et") or "")
        scan_groups.setdefault(key, []).append(row)
    valid_counts = [sum(1 for row in group if _valid_tick(row)) for group in scan_groups.values()]
    news_checked = sum(1 for row in rows if _news_checked(_tick_news_status(row)))
    news_unchecked = len(rows) - news_checked
    if ticks is None:
        sample_rows = list_research_samples(db_path=db_path, week_id=week_id, limit=10000)
        if sample_rows:
            news_checked = sum(1 for row in sample_rows if _news_checked(str(row.get("news_status") or NEWS_STATUS_UNCHECKED)))
            news_unchecked = len(sample_rows) - news_checked
    warnings: list[str] = []
    if coverage < 70.0:
        warnings.append("监控记录不完整，本轮复盘仅供参考。")
    if max_gap > 15.0:
        warnings.append("监控存在较长断档。")
    return {
        "window_label": f"{first_time.date().isoformat()} {first_time.strftime('%H:%M')} ET → {end_time.strftime('%m-%d %H:%M')} ET",
        "first_tick_time": first_time.isoformat(),
        "latest_tick_time": latest_time.isoformat(),
        "expected_scan_count": expected,
        "actual_scan_count": actual,
        "coverage_pct": coverage,
        "max_gap_minutes": max_gap,
        "effective_ticker_count": len({row["ticker"] for row in rows if _valid_tick(row)}),
        "avg_effective_tickers_per_scan": (sum(valid_counts) / len(valid_counts)) if valid_counts else 0.0,
        "anchor_missing_count": sum(1 for row in rows if _number(row.get("anchor_price")) is None),
        "binance_missing_count": sum(1 for row in rows if _number(row.get("binance_price")) is None),
        "volatility_missing_count": sum(1 for row in rows if _number(row.get("spread_atr_ratio")) is None and _number(row.get("avg_range_20d_pct")) is None),
        "news_checked_count": news_checked,
        "news_unchecked_count": news_unchecked,
        "health_warning": " ".join(warnings),
    }


def build_generation_report(ticks: Iterable[dict[str, Any]], events: list[dict[str, Any]], samples: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [_normalize_tick(row) for row in ticks or []]
    scan_keys = {str(row.get("run_id") or row.get("scan_time_et") or "") for row in rows}
    return {
        "raw_tick_count": len(rows),
        "scan_count": len([key for key in scan_keys if key]),
        "ticker_count": len({row.get("ticker") for row in rows if row.get("ticker")}),
        "event_count": len(events),
        "sample_count": len(samples),
        "skipped_anchor_missing_count": sum(1 for row in rows if _number(row.get("anchor_price")) is None),
        "skipped_binance_missing_count": sum(1 for row in rows if _number(row.get("binance_price")) is None),
        "downgraded_volatility_missing_count": sum(1 for row in rows if _number(row.get("spread_atr_ratio")) is None and _number(row.get("avg_range_20d_pct")) is None),
        "downgraded_news_unchecked_count": sum(1 for sample in samples if str(sample.get("news_status") or "") == NEWS_STATUS_UNCHECKED),
        "pending_p2_count": sum(1 for sample in samples if str(sample.get("p2_status") or "") == "未到夜盘时间"),
        "p2_first_minute_missing_count": sum(1 for sample in samples if str(sample.get("sample_quality") or "") == "延迟成交样本"),
        "p2_liquidity_missing_count": sum(1 for sample in samples if str(sample.get("p2_status") or "") == "夜盘窗口无有效价格"),
    }


def research_path_for_snapshot(snapshot_path: Path) -> Path:
    if snapshot_path == DEFAULT_RESEARCH_DB_PATH:
        return snapshot_path
    return snapshot_path.parent / "weekend_spread_research.sqlite"


def _build_news_status_map(
    ticks: list[dict[str, Any]],
    *,
    news_contexts: dict[tuple[str, str], dict[str, Any]] | None = None,
    news_store: WeekendSpreadNewsStore | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    provided = {key: _normalize_news_status(value) for key, value in (news_contexts or {}).items()}
    grouped = _group_ticks(ticks)
    if not grouped:
        return provided
    store = news_store or WeekendSpreadNewsStore()
    result = dict(provided)
    for (week_id, ticker), rows in grouped.items():
        if (week_id, ticker) in result:
            continue
        max_premium = max((abs(_number(row.get("premium_pct")) or 0.0) for row in rows), default=0.0)
        premium_row = max(rows, key=lambda row: abs(_number(row.get("premium_pct")) or 0.0))
        sample = current_shutdown_news_sample(ticker, premium_pct=_number(premium_row.get("premium_pct")) or max_premium)
        sample["friday_afterhours_close"] = _first_number(rows, "anchor_price")
        sample["binance_price"] = _number(premium_row.get("binance_price"))
        result[(week_id, ticker)] = _normalize_news_status(build_weekend_spread_news_status(ticker, sample, store=store))
    return result


def _apply_news_to_events(events: list[dict[str, Any]], news_map: dict[tuple[str, str], dict[str, Any]], *, now: datetime | None = None) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    for event in events:
        item = dict(event)
        news = _normalize_news_status(news_map.get((str(item.get("week_id") or ""), str(item.get("ticker") or "").upper())) or {})
        item.update(
            {
                "news_status": news.get("news_status") or NEWS_STATUS_UNCHECKED,
                "news_label": news.get("gap_news_explanation") or news.get("gap_explanation_label") or news.get("news_status") or NEWS_STATUS_UNCHECKED,
                "news_count": int(news.get("news_count") or 0),
                "major_news_count": int(news.get("major_news_count") or 0),
                "opinion_news_count": int(news.get("opinion_news_count") or 0),
                "latest_news_time": str(news.get("latest_news_time") or ""),
                "news_checked_at": str(news.get("last_checked_at") or news.get("news_checked_at") or ""),
                "news_fetch_status": str(news.get("fetch_status") or ""),
                "news_error": str(news.get("fetch_error") or ""),
            }
        )
        p2_status = _p2_status({}, week_id=str(item.get("week_id") or ""), now=now)
        sample_quality = "等待夜盘验证" if p2_status == "未到夜盘时间" else ""
        quality = _event_quality(
            [],
            duration=float(_number(item.get("duration_minutes")) or 0.0),
            max_ratio=float(_number(item.get("max_spread_atr_ratio")) or 0.0),
            news_status=str(item.get("news_status") or NEWS_STATUS_UNCHECKED),
            sample_quality=sample_quality,
        )
        item["event_quality"] = quality
        item["review_reason"] = _event_review_reason(item)
        updated.append(item)
    return updated


def _normalize_news_status(news: dict[str, Any]) -> dict[str, Any]:
    status = str(news.get("news_status") or news.get("status") or "").strip()
    label = str(news.get("gap_news_explanation") or news.get("gap_explanation_label") or news.get("news_label") or "").strip()
    if not status:
        if label in {NEWS_STATUS_NO_RELEVANT, NEWS_STATUS_NO_MAJOR, NEWS_STATUS_EXPLAINED, NEWS_STATUS_DIRECTION_MATCH, NEWS_STATUS_DIRECTION_MISMATCH, NEWS_STATUS_OPINION, NEWS_STATUS_FAILED}:
            status = label
        elif label:
            status = NEWS_STATUS_EXPLAINED if "新闻" in label and "无" not in label else NEWS_STATUS_UNCHECKED
        else:
            status = NEWS_STATUS_UNCHECKED
    normalized = dict(news)
    normalized["news_status"] = status
    normalized["gap_news_explanation"] = label or (status if status != NEWS_STATUS_UNCHECKED else "新闻未检查，无法判断是否有基本面解释。")
    return normalized


def _p2_status(backtest: dict[str, Any], *, week_id: str, now: datetime | None = None) -> str:
    p2 = _number(backtest.get("broker_open_close") or backtest.get("p2_price"))
    delay = _number(backtest.get("p2_delay_minutes"))
    if p2 is not None:
        if delay is not None and delay <= 0:
            return "首分钟样本"
        return "延迟成交样本"
    current = (now or datetime.now(timezone.utc)).astimezone(ET)
    opening = _next_overnight_open_for_week_id(week_id)
    if opening and current < opening:
        return "未到夜盘时间"
    return "夜盘窗口无有效价格"


def _next_overnight_open_for_week_id(week_id: str) -> datetime | None:
    try:
        year_text, week_text = str(week_id).split("-W", 1)
        sunday = datetime.fromisocalendar(int(year_text), int(week_text), 7)
        return sunday.replace(hour=20, minute=0, second=0, microsecond=0, tzinfo=ET)
    except Exception:
        return None


def _row_data_missing(row: dict[str, Any]) -> bool:
    return _number(row.get("anchor_price")) is None or _number(row.get("binance_price")) is None or (
        _number(row.get("spread_atr_ratio")) is None and _number(row.get("avg_range_20d_pct")) is None
    )


def _valid_tick(row: dict[str, Any]) -> bool:
    return _number(row.get("anchor_price")) is not None and _number(row.get("binance_price")) is not None and _number(row.get("premium_pct")) is not None


def _tick_news_status(row: dict[str, Any]) -> str:
    text = str(row.get("news_status") or row.get("news_label") or "").strip()
    return text or NEWS_STATUS_UNCHECKED


def _news_checked(status: str) -> bool:
    return status in NEWS_CHECKED_STATUSES or status in {NEWS_STATUS_FAILED, NEWS_STATUS_INSUFFICIENT}


def _sample_data_health_label(rows: list[dict[str, Any]]) -> str:
    if any(_row_data_missing(row) for row in rows):
        return "数据待补齐"
    return "记录完整"


def _event_review_reason(event: dict[str, Any]) -> str:
    quality = str(event.get("event_quality") or "")
    ratio = _number(event.get("max_spread_atr_ratio")) or 0.0
    if quality == "无新闻极端价差":
        return "价差超过2天波动，且无重大新闻解释。"
    if quality == "待新闻确认":
        return "新闻未检查，暂不能判断是否为无新闻错价。"
    if quality == "普通偏离":
        return f"价差较大，但仅约{ratio:.1f}天波动，属于普通偏离。"
    if quality == "高质量事件":
        return "价差强度和持续时间都较高，且新闻状态已确认。"
    if quality == "等待夜盘验证":
        return "夜盘尚未开盘，等待 P2 验证。"
    if quality == "瞬时插针":
        return "持续时间较短，先按瞬时插针观察。"
    if quality == "数据待核":
        return "锚点、价格或波动参照不完整，需要先核对数据。"
    return "作为普通价差事件复盘。"


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
            news_status TEXT,
            news_label TEXT,
            news_count INTEGER,
            major_news_count INTEGER,
            opinion_news_count INTEGER,
            latest_news_time TEXT,
            news_checked_at TEXT,
            news_fetch_status TEXT,
            news_error TEXT,
            event_quality TEXT,
            review_reason TEXT,
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
            news_status TEXT,
            news_label TEXT,
            news_count INTEGER,
            major_news_count INTEGER,
            opinion_news_count INTEGER,
            latest_news_time TEXT,
            news_checked_at TEXT,
            news_fetch_status TEXT,
            news_error TEXT,
            p2_status TEXT,
            first_minute_liquidity TEXT,
            capture_pct REAL,
            final_transmission_pct REAL,
            sample_quality TEXT,
            data_health_label TEXT,
            created_at TEXT
        )
        """
    )
    _ensure_columns(
        conn,
        "weekend_spread_premium_events",
        {
            "news_status": "TEXT",
            "news_count": "INTEGER",
            "major_news_count": "INTEGER",
            "opinion_news_count": "INTEGER",
            "latest_news_time": "TEXT",
            "news_checked_at": "TEXT",
            "news_fetch_status": "TEXT",
            "news_error": "TEXT",
            "review_reason": "TEXT",
        },
    )
    _ensure_columns(
        conn,
        "weekend_spread_research_samples",
        {
            "news_status": "TEXT",
            "opinion_news_count": "INTEGER",
            "latest_news_time": "TEXT",
            "news_checked_at": "TEXT",
            "news_fetch_status": "TEXT",
            "news_error": "TEXT",
            "p2_status": "TEXT",
            "data_health_label": "TEXT",
        },
    )


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")


def _replace_events(conn: sqlite3.Connection, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO weekend_spread_premium_events (
            event_id, week_id, ticker, binance_symbol, direction, event_start_et, event_end_et,
            duration_minutes, start_premium_pct, max_premium_pct, min_premium_pct, peak_time_et,
            end_premium_pct, max_spread_atr_ratio, minutes_above_2pct, minutes_above_5pct,
            minutes_above_1_5x_atr, converged_before_open, news_status, news_label, news_count,
            major_news_count, opinion_news_count, latest_news_time, news_checked_at, news_fetch_status,
            news_error, event_quality, review_reason, created_at
        ) VALUES (
            :event_id, :week_id, :ticker, :binance_symbol, :direction, :event_start_et, :event_end_et,
            :duration_minutes, :start_premium_pct, :max_premium_pct, :min_premium_pct, :peak_time_et,
            :end_premium_pct, :max_spread_atr_ratio, :minutes_above_2pct, :minutes_above_5pct,
            :minutes_above_1_5x_atr, :converged_before_open, :news_status, :news_label, :news_count,
            :major_news_count, :opinion_news_count, :latest_news_time, :news_checked_at, :news_fetch_status,
            :news_error, :event_quality, :review_reason, :created_at
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
            extreme_spread_count, news_status, news_label, news_count, major_news_count,
            opinion_news_count, latest_news_time, news_checked_at, news_fetch_status, news_error,
            p2_status, first_minute_liquidity, capture_pct, final_transmission_pct, sample_quality,
            data_health_label, created_at
        ) VALUES (
            :sample_id, :week_id, :ticker, :binance_symbol, :p0_price, :p0_time_et, :p1_max_price,
            :p1_max_time_et, :p1_min_price, :last_binance_price_before_open, :p2_price, :p2_time_et,
            :p2_delay_minutes, :max_premium_pct, :max_discount_pct, :avg_premium_pct,
            :max_spread_atr_ratio, :premium_duration_minutes, :discount_duration_minutes,
            :extreme_spread_count, :news_status, :news_label, :news_count, :major_news_count,
            :opinion_news_count, :latest_news_time, :news_checked_at, :news_fetch_status, :news_error,
            :p2_status, :first_minute_liquidity, :capture_pct, :final_transmission_pct, :sample_quality,
            :data_health_label, :created_at
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
        "direction": "溢价" if abs(max_premium) >= abs(min_premium) else "折价",
        "news_status": NEWS_STATUS_UNCHECKED,
        "news_label": _dominant_news_label(rows),
        "news_count": 0,
        "major_news_count": 0,
        "opinion_news_count": 0,
        "latest_news_time": "",
        "news_checked_at": "",
        "news_fetch_status": "",
        "news_error": "",
        "event_quality": _event_quality(rows, duration=duration, max_ratio=max_ratio),
        "review_reason": "",
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


def _event_quality(
    rows: list[dict[str, Any]],
    *,
    duration: float,
    max_ratio: float,
    news_status: str | None = None,
    sample_quality: str | None = None,
) -> str:
    if sample_quality == "等待夜盘验证":
        return "等待夜盘验证"
    if any(_row_data_missing(row) for row in rows):
        return "数据待核"
    if duration <= DEFAULT_TICK_INTERVAL_MINUTES or len(rows) <= 1:
        return "瞬时插针"
    status = news_status or _dominant_news_label(rows) or NEWS_STATUS_UNCHECKED
    if status == "无新闻解释":
        status = NEWS_STATUS_UNCHECKED
    if max_ratio >= 2.0 and status in {NEWS_STATUS_NO_RELEVANT, NEWS_STATUS_NO_MAJOR}:
        return "无新闻极端价差"
    if max_ratio >= 1.5 and not _news_checked(status):
        return "待新闻确认"
    if max_ratio and max_ratio < 1.0:
        return "普通偏离"
    if max_ratio >= 1.5 and duration >= 6 and status in NEWS_CHECKED_STATUSES:
        return "高质量事件"
    return "普通偏离"


def _sample_quality(
    backtest: dict[str, Any],
    *,
    max_atr: float,
    major_news_count: int,
    news_status: str = NEWS_STATUS_UNCHECKED,
    p2_status: str = "",
    rows: list[dict[str, Any]] | None = None,
) -> str:
    if rows and any(_number(row.get("anchor_price")) is None or _number(row.get("binance_price")) is None for row in rows):
        return "数据不足"
    if p2_status == "未到夜盘时间":
        return "等待夜盘验证"
    if p2_status == "夜盘窗口无有效价格":
        return "夜盘流动性不足"
    if max_atr >= 2.0 and news_status in {NEWS_STATUS_NO_RELEVANT, NEWS_STATUS_NO_MAJOR}:
        return "无新闻极端价差"
    if max_atr >= 2.0 and news_status == NEWS_STATUS_UNCHECKED:
        return "待新闻确认的极端价差"
    if major_news_count > 0:
        return "新闻解释样本"
    if p2_status == "首分钟样本":
        return "首分钟样本"
    if p2_status == "延迟成交样本":
        return "延迟成交样本"
    return "仅观察样本"


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
