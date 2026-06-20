from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from data.weekend_spread_research import (
    append_monitor_ticks,
    build_generation_report,
    build_premium_events,
    build_research_samples,
    build_weekend_spread_research_samples,
    cleanup_old_monitor_ticks,
    list_monitor_ticks,
    list_premium_events,
    list_research_samples,
    monitor_recording_health,
    research_summary,
)


def _tick(ticker: str, minute: int, premium: float, *, ratio: float | None = None, news: str = "") -> dict:
    scan = datetime(2026, 6, 20, 0, minute, tzinfo=timezone.utc)
    return {
        "run_id": f"run-{minute}",
        "scan_time": scan.isoformat(),
        "week_id": "2026-W25",
        "ticker": ticker,
        "binance_symbol": f"{ticker}USDT",
        "anchor_price": 100.0,
        "anchor_time": "2026-06-19T16:00:00-04:00",
        "binance_price": 100.0 * (1 + premium / 100.0),
        "premium_pct": premium,
        "regular_close_price": 99.0,
        "atr14_pct": 2.0,
        "avg_range_20d_pct": 2.4,
        "spread_atr_ratio": ratio,
        "spread_reasonableness": "异常价差" if ratio and ratio >= 1.5 else "正常波动",
        "news_label": news,
        "premium_trend_label": "溢价扩大",
    }


def test_monitor_ticks_are_persisted(tmp_path) -> None:
    db_path = tmp_path / "research.sqlite"
    rows = [_tick("NVDA", 0, 2.5, ratio=1.6)]

    count = append_monitor_ticks(rows, db_path=db_path)
    stored = list_monitor_ticks(db_path=db_path)

    assert count == 1
    assert stored[0]["ticker"] == "NVDA"
    assert stored[0]["premium_pct"] == pytest.approx(2.5)
    assert stored[0]["scan_time_et"].endswith("-04:00")


def test_continuous_premium_ticks_are_compressed_into_one_event() -> None:
    ticks = [
        _tick("GLW", 0, 0.8, ratio=0.6),
        _tick("GLW", 3, 2.1, ratio=1.6),
        _tick("GLW", 6, 3.0, ratio=1.8),
        _tick("GLW", 9, 0.7, ratio=0.8),
    ]

    events = build_premium_events(ticks)

    assert len(events) == 1
    event = events[0]
    assert event["ticker"] == "GLW"
    assert event["direction"] == "溢价"
    assert event["max_premium_pct"] == pytest.approx(3.0)
    assert event["minutes_above_2pct"] == pytest.approx(6.0)
    assert event["converged_before_open"] == 1
    assert event["event_quality"] == "待新闻确认"


def test_single_tick_spike_is_marked_as_instant_spike() -> None:
    events = build_premium_events([_tick("IBM", 0, 2.4, ratio=1.6), _tick("IBM", 3, 0.4, ratio=0.6)])

    assert len(events) == 1
    assert events[0]["event_quality"] == "瞬时插针"


def test_research_sample_calculates_weekend_metrics() -> None:
    ticks = [_tick("NVDA", 0, 2.0, ratio=1.0), _tick("NVDA", 3, 4.0, ratio=1.8), _tick("NVDA", 6, -1.0, ratio=0.5)]
    backtest = [{"week_id": "2026-W25", "ticker": "NVDA", "broker_open_close": 103.0, "p2_delay_minutes": 0, "capture_pct": 50.0}]

    samples = build_research_samples(ticks, backtest_rows=backtest)

    assert len(samples) == 1
    sample = samples[0]
    assert sample["max_premium_pct"] == pytest.approx(4.0)
    assert sample["max_discount_pct"] == pytest.approx(-1.0)
    assert sample["avg_premium_pct"] == pytest.approx((2.0 + 4.0 - 1.0) / 3)
    assert sample["premium_duration_minutes"] == pytest.approx(6.0)
    assert sample["max_spread_atr_ratio"] == pytest.approx(1.8)
    assert sample["sample_quality"] == "首分钟样本"


def test_no_news_extreme_spread_sample_is_marked() -> None:
    ticks = [_tick("MU", 0, 5.5, ratio=2.2), _tick("MU", 3, 4.0, ratio=1.8)]

    sample = build_research_samples(
        ticks,
        backtest_rows=[{"week_id": "2026-W25", "ticker": "MU", "broker_open_close": 104.0, "p2_delay_minutes": 0}],
        now=datetime(2026, 6, 22, 1, 0, tzinfo=timezone.utc),
        news_contexts={("2026-W25", "MU"): {"news_status": "无重大新闻"}},
    )[0]

    assert sample["sample_quality"] == "无新闻极端价差"


def test_research_sample_waits_for_overnight_validation_before_open() -> None:
    ticks = [_tick("MU", 0, 5.5, ratio=2.2), _tick("MU", 3, 4.0, ratio=1.8)]

    sample = build_research_samples(ticks, now=datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc))[0]

    assert sample["p2_status"] == "未到夜盘时间"
    assert sample["sample_quality"] == "等待夜盘验证"


def test_research_sample_marks_liquidity_missing_after_open_without_p2() -> None:
    ticks = [_tick("MU", 0, 5.5, ratio=2.2), _tick("MU", 3, 4.0, ratio=1.8)]

    sample = build_research_samples(ticks, now=datetime(2026, 6, 22, 1, 0, tzinfo=timezone.utc))[0]

    assert sample["p2_status"] == "夜盘窗口无有效价格"
    assert sample["sample_quality"] == "夜盘流动性不足"


def test_generation_report_and_recording_health_surface_quality_counts() -> None:
    ticks = [
        _tick("GLW", 0, 2.5, ratio=1.2),
        _tick("GLW", 3, 2.8, ratio=1.4),
        _tick("NOW", 9, 3.2, ratio=None),
    ]
    ticks[-1]["avg_range_20d_pct"] = None
    events = build_premium_events(ticks)
    samples = build_research_samples(ticks, now=datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc))

    report = build_generation_report(ticks, events, samples)
    health = monitor_recording_health(ticks=ticks, now=datetime(2026, 6, 20, 0, 12, tzinfo=timezone.utc))

    assert report["raw_tick_count"] == 3
    assert report["ticker_count"] == 2
    assert report["pending_p2_count"] == 2
    assert report["downgraded_volatility_missing_count"] == 1
    assert health["coverage_pct"] < 100
    assert health["max_gap_minutes"] == pytest.approx(6.0)
    assert health["volatility_missing_count"] == 1


def test_build_research_samples_persists_events_and_samples(tmp_path) -> None:
    db_path = tmp_path / "research.sqlite"
    ticks = [_tick("NOW", 0, 0.5, ratio=0.4), _tick("NOW", 3, 2.5, ratio=1.6), _tick("NOW", 6, 2.8, ratio=1.7)]
    append_monitor_ticks(ticks, db_path=db_path)

    result = build_weekend_spread_research_samples("2026-W25", db_path=db_path)

    assert result["event_count"] == 1
    assert result["sample_count"] == 1
    assert list_premium_events(db_path=db_path)[0]["ticker"] == "NOW"
    assert list_research_samples(db_path=db_path)[0]["ticker"] == "NOW"
    assert research_summary(db_path=db_path)["event_count"] == 1


def test_cleanup_old_ticks_does_not_delete_research_samples(tmp_path) -> None:
    db_path = tmp_path / "research.sqlite"
    old = _tick("NVDA", 0, 2.5, ratio=1.6)
    old["scan_time"] = datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat()
    old["week_id"] = "2026-W18"
    append_monitor_ticks([old, _tick("NVDA", 3, 3.0, ratio=1.8)], db_path=db_path)
    build_weekend_spread_research_samples("2026-W18", db_path=db_path)

    deleted = cleanup_old_monitor_ticks(db_path=db_path, days=30, now=datetime(2026, 6, 20, tzinfo=timezone.utc))

    assert deleted >= 1
    assert list_research_samples(db_path=db_path)
    assert all(row["ticker"] == "NVDA" for row in list_research_samples(db_path=db_path))
