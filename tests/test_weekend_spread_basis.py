from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
import zipfile

import pytest

import data.weekend_spread_basis as basis_module
from data.us_market_session import US_EASTERN
from data.weekend_spread_basis import (
    QUALITY_INSUFFICIENT,
    QUALITY_LIMITED,
    QUALITY_SUFFICIENT,
    QUALITY_TIME_MISALIGNED,
    backfill_open_market_basis_history,
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
    assert profile["basis_quality"] == QUALITY_LIMITED


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
    assert profile["basis_quality"] == QUALITY_LIMITED


class _FakeBinanceHistoryProvider:
    def __init__(self, *, close: float = 101.0) -> None:
        self.close = close

    def get_klines(
        self,
        _symbol: str,
        *,
        market_type: str = "usdm_futures",
        interval: str = "1m",
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 1000,
    ) -> list[list[object]]:
        start = datetime.fromtimestamp(int(start_time_ms or 0) / 1000, tz=timezone.utc)
        end = datetime.fromtimestamp(int(end_time_ms or 0) / 1000, tz=timezone.utc)
        current = start
        rows: list[list[object]] = []
        while current < end:
            rows.append([int(current.timestamp() * 1000), self.close, self.close, self.close, self.close, 1])
            current += timedelta(minutes=30)
        return rows


class _BrokenBinanceHistoryProvider:
    def get_klines(
        self,
        _symbol: str,
        *,
        market_type: str = "usdm_futures",
        interval: str = "1m",
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 1000,
    ) -> list[list[object]]:
        raise URLError("forced disconnect")


class _FakeStockHistoryProvider:
    is_configured = True
    provider_name = "FAKE_STOCK_HISTORY"

    def __init__(self, *, close: float = 100.0, offset_seconds: int = 0) -> None:
        self.close = close
        self.offset_seconds = offset_seconds

    def get_stock_bars(
        self,
        _symbol: str,
        *,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1m",
    ) -> list[dict[str, object]]:
        current = start_time.astimezone(timezone.utc) + timedelta(seconds=self.offset_seconds)
        end = end_time.astimezone(timezone.utc)
        rows: list[dict[str, object]] = []
        while current < end:
            rows.append({"t": current.isoformat(), "c": self.close})
            current += timedelta(minutes=30)
        return rows


def test_backfill_open_market_basis_history_writes_aligned_samples_and_profile(tmp_path) -> None:
    db_path = tmp_path / "basis.sqlite3"
    now = datetime(2026, 6, 17, 17, 0, tzinfo=US_EASTERN)

    result = backfill_open_market_basis_history(
        mapping={"GLW": {"binance_symbol": "GLWUSDT"}},
        ignored={},
        db_path=db_path,
        now=now,
        lookback_trading_days=1,
        sample_interval_minutes=30,
        binance_history_provider=_FakeBinanceHistoryProvider(close=101.0),
        stock_history_provider=_FakeStockHistoryProvider(close=100.0),
    )
    profile = build_normal_basis_profile("GLW", db_path=db_path, now=now)

    assert result["ok"] is True
    assert result["collected_count"] >= 10
    assert result["misaligned_count"] == 0
    assert profile["normal_basis_median_pct"] == pytest.approx(1.0)
    assert profile["basis_quality"] == QUALITY_LIMITED


def test_backfill_open_market_basis_history_falls_back_to_binance_vision_archive(tmp_path, monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return self.payload

    def archive_payload() -> bytes:
        rows = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"]
        current = datetime(2026, 6, 17, 14, 0, tzinfo=timezone.utc)
        for _index in range(12):
            open_ms = int(current.timestamp() * 1000)
            rows.append(f"{open_ms},101,101,101,101,1,{open_ms + 59999},101,1,1,101,0")
            current += timedelta(minutes=30)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("GLWUSDT-1m-2026-06-17.csv", "\n".join(rows).encode("utf-8"))
        return buffer.getvalue()

    def fake_urlopen(request: object, timeout: float = 20) -> FakeResponse:
        assert "data/futures/um/daily/klines/GLWUSDT/1m/GLWUSDT-1m-2026-06-17.zip" in request.full_url
        return FakeResponse(archive_payload())

    monkeypatch.setattr(basis_module, "urlopen", fake_urlopen)
    db_path = tmp_path / "basis.sqlite3"
    now = datetime(2026, 6, 17, 17, 0, tzinfo=US_EASTERN)

    result = backfill_open_market_basis_history(
        mapping={"GLW": {"binance_symbol": "GLWUSDT"}},
        ignored={},
        db_path=db_path,
        now=now,
        lookback_trading_days=1,
        sample_interval_minutes=30,
        binance_history_provider=_BrokenBinanceHistoryProvider(),
        stock_history_provider=_FakeStockHistoryProvider(close=100.0),
    )
    profile = build_normal_basis_profile("GLW", db_path=db_path, now=now)

    assert result["ok"] is True
    assert result["collected_count"] >= 10
    assert result["samples"][0]["binance_source"] == "binance_vision_usdm_futures_1m"
    assert profile["normal_basis_median_pct"] == pytest.approx(1.0)


def test_backfill_open_market_basis_history_skips_completed_days_on_resume(tmp_path) -> None:
    db_path = tmp_path / "basis.sqlite3"
    now = datetime(2026, 6, 17, 17, 0, tzinfo=US_EASTERN)

    first = backfill_open_market_basis_history(
        mapping={"GLW": {"binance_symbol": "GLWUSDT"}},
        ignored={},
        db_path=db_path,
        now=now,
        lookback_trading_days=1,
        sample_interval_minutes=30,
        binance_history_provider=_FakeBinanceHistoryProvider(close=101.0),
        stock_history_provider=_FakeStockHistoryProvider(close=100.0),
    )
    first_count = len(basis_module.load_basis_samples("GLW", db_path=db_path))
    second = backfill_open_market_basis_history(
        mapping={"GLW": {"binance_symbol": "GLWUSDT"}},
        ignored={},
        db_path=db_path,
        now=now,
        lookback_trading_days=1,
        sample_interval_minutes=30,
        binance_history_provider=_FakeBinanceHistoryProvider(close=102.0),
        stock_history_provider=_FakeStockHistoryProvider(close=100.0),
    )

    assert first["collected_count"] == first_count
    assert second["ok"] is True
    assert second["collected_count"] == 0
    assert second["skipped_existing_count"] == first_count
    assert len(basis_module.load_basis_samples("GLW", db_path=db_path)) == first_count


def test_backfill_open_market_basis_history_replaces_partial_day_samples(tmp_path) -> None:
    db_path = tmp_path / "basis.sqlite3"
    now = datetime(2026, 6, 17, 17, 0, tzinfo=US_EASTERN)
    save_basis_samples([_sample("GLW", datetime(2026, 6, 17, 10, 0, tzinfo=US_EASTERN), 9.9)], db_path=db_path)

    result = backfill_open_market_basis_history(
        mapping={"GLW": {"binance_symbol": "GLWUSDT"}},
        ignored={},
        db_path=db_path,
        now=now,
        lookback_trading_days=1,
        sample_interval_minutes=30,
        binance_history_provider=_FakeBinanceHistoryProvider(close=101.0),
        stock_history_provider=_FakeStockHistoryProvider(close=100.0),
    )
    frame = basis_module.load_basis_samples("GLW", db_path=db_path)

    assert result["ok"] is True
    assert result["collected_count"] >= 10
    assert len(frame) == result["collected_count"]
    assert frame["basis_pct"].max() == pytest.approx(1.0)


def test_backfill_open_market_basis_history_keeps_misaligned_samples_out_of_profile(tmp_path) -> None:
    db_path = tmp_path / "basis.sqlite3"
    now = datetime(2026, 6, 17, 17, 0, tzinfo=US_EASTERN)

    result = backfill_open_market_basis_history(
        mapping={"GLW": {"binance_symbol": "GLWUSDT"}},
        ignored={},
        db_path=db_path,
        now=now,
        lookback_trading_days=1,
        sample_interval_minutes=30,
        max_alignment_seconds=60,
        max_candidate_gap_seconds=300,
        binance_history_provider=_FakeBinanceHistoryProvider(close=101.0),
        stock_history_provider=_FakeStockHistoryProvider(close=100.0, offset_seconds=120),
    )
    profile = build_normal_basis_profile("GLW", db_path=db_path, now=now)

    assert result["ok"] is True
    assert result["misaligned_count"] >= 10
    assert profile["normal_basis_median_pct"] is None
    assert profile["basis_quality"] == QUALITY_TIME_MISALIGNED


def test_empty_normal_basis_profile_is_marked_uncollected(tmp_path) -> None:
    profile = build_normal_basis_profile("GLW", db_path=tmp_path / "basis.sqlite3")

    assert profile["sample_count"] == 0
    assert profile["basis_quality"] == "未采集"


def test_basis_collector_script_supports_quiet_mode() -> None:
    source = (Path(__file__).resolve().parents[1] / "tools" / "weekend_spread_basis_collector.py").read_text(encoding="utf-8")

    assert "--quiet" in source
    assert "--backfill" in source
    assert "--lookback-days" in source
    assert "--sample-interval-minutes" in source
    assert "--symbols" in source
    assert "--no-resume" in source
    assert "backfill_open_market_basis_history" in source
    assert "collect_open_market_basis_once" in source


def test_basis_task_installer_uses_silent_scheduler_command() -> None:
    source = (Path(__file__).resolve().parents[1] / "data" / "weekend_spread_basis.py").read_text(encoding="utf-8")

    assert "install_open_market_basis_task" in source
    assert "pythonw.exe" in source
    assert "--quiet" in source
    assert "Register-ScheduledTask" in source
    assert "-Hidden" in source
