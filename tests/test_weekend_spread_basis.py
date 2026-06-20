from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from data.us_market_session import US_EASTERN
from data.weekend_spread_basis import (
    QUALITY_INSUFFICIENT,
    QUALITY_SUFFICIENT,
    QUALITY_TIME_MISALIGNED,
    build_normal_basis_profile,
    calculate_adjusted_spread_pct,
    calculate_basis_pct,
    collect_open_market_basis_once,
    is_open_market_basis_window,
    save_basis_samples,
)


def _sample(ticker: str, when: datetime, basis_pct: float, *, diff_seconds: float = 10.0) -> dict:
    return {
        "sample_time_et": when.isoformat(),
        "sample_time_hkt": when.astimezone(timezone(timedelta(hours=8))).isoformat(),
        "ticker": ticker,
        "binance_symbol": f"{ticker}USDT",
        "binance_price": 101.0,
        "stock_spot_price": 100.0,
        "stock_spot_source": "quote_snapshot",
        "binance_source": "binance_usdm_futures",
        "basis_pct": basis_pct,
        "price_time_diff_seconds": diff_seconds,
        "market_session": "regular",
        "sample_quality": "可用" if diff_seconds <= 60 else "时间未对齐",
        "created_at": when.astimezone(timezone.utc).isoformat(),
    }


def test_basis_and_adjusted_spread_calculation() -> None:
    assert calculate_basis_pct(101, 100) == pytest.approx(1.0)
    assert calculate_adjusted_spread_pct(4.8, 0.6) == pytest.approx(4.2)
    assert calculate_adjusted_spread_pct(4.8, None) is None


def test_normal_basis_profile_uses_recent_aligned_median(tmp_path) -> None:
    db_path = tmp_path / "basis.sqlite3"
    now = datetime(2026, 6, 15, 14, 0, tzinfo=US_EASTERN)
    values = [0.1, 0.2, 0.3, 0.4, 0.5, 9.9]
    save_basis_samples([_sample("GLW", now - timedelta(days=index), value) for index, value in enumerate(values)], db_path=db_path)

    profile = build_normal_basis_profile("GLW", db_path=db_path, now=now)

    assert profile["normal_basis_median_pct"] == pytest.approx(0.35)
    assert profile["sample_count"] == 6
    assert profile["basis_quality"] == QUALITY_INSUFFICIENT


def test_normal_basis_profile_is_usable_after_thirty_samples_and_three_days(tmp_path) -> None:
    db_path = tmp_path / "basis.sqlite3"
    now = datetime(2026, 6, 15, 14, 0, tzinfo=US_EASTERN)
    samples = [
        _sample("GLW", now - timedelta(days=day, minutes=index), 0.2 + index * 0.001)
        for day in range(3)
        for index in range(10)
    ]
    save_basis_samples(samples, db_path=db_path)

    profile = build_normal_basis_profile("GLW", db_path=db_path, now=now)

    assert profile["sample_count"] == 30
    assert profile["trading_days_count"] == 3
    assert profile["basis_quality"] == QUALITY_SUFFICIENT


def test_misaligned_basis_samples_are_not_high_quality(tmp_path) -> None:
    db_path = tmp_path / "basis.sqlite3"
    now = datetime(2026, 6, 15, 14, 0, tzinfo=US_EASTERN)
    samples = [_sample("GLW", now - timedelta(minutes=index), 0.5, diff_seconds=300) for index in range(12)]
    save_basis_samples(samples, db_path=db_path)

    profile = build_normal_basis_profile("GLW", db_path=db_path, now=now)

    assert profile["normal_basis_median_pct"] is None
    assert profile["basis_quality"] == QUALITY_TIME_MISALIGNED


def test_open_market_basis_collection_is_blocked_outside_regular_window(tmp_path) -> None:
    saturday = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)

    assert is_open_market_basis_window(saturday) is False
    result = collect_open_market_basis_once(mapping={}, ignored={}, db_path=tmp_path / "basis.sqlite3", now=saturday)

    assert result["ok"] is False
    assert result["collected_count"] == 0
    assert "不能采集开市基差" in result["message"]


def test_collect_open_market_basis_once_writes_sample_and_profile(tmp_path) -> None:
    class FakeCache:
        def get_quote_snapshot(self, _ticker: str) -> dict:
            return {
                "payload": {
                    "current_price": 100.0,
                    "quote_updated_at": now.astimezone(timezone.utc).isoformat(),
                    "source": "fake_quote",
                },
                "fetched_at": now.astimezone(timezone.utc).isoformat(),
            }

    db_path = tmp_path / "basis.sqlite3"
    now = datetime(2026, 6, 15, 14, 0, tzinfo=US_EASTERN)
    mapping = {"GLW": {"binance_symbol": "GLWUSDT"}}

    result = collect_open_market_basis_once(
        mapping=mapping,
        ignored={},
        cache=FakeCache(),
        price_map={"GLWUSDT": 101.0},
        db_path=db_path,
        now=now,
    )
    profile = build_normal_basis_profile("GLW", db_path=db_path, now=now)

    assert result["ok"] is True
    assert result["collected_count"] == 1
    assert result["samples"][0]["basis_pct"] == pytest.approx(1.0)
    assert profile["normal_basis_median_pct"] == pytest.approx(1.0)


def test_basis_collector_script_supports_quiet_mode() -> None:
    source = (Path(__file__).resolve().parents[1] / "tools" / "weekend_spread_basis_collector.py").read_text(encoding="utf-8")

    assert "--quiet" in source
    assert "collect_open_market_basis_once" in source
