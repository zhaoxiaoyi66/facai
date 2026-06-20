from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from data.price_freshness import classify_price_freshness, get_us_market_context


ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")


def test_weekend_uses_previous_us_trading_day_as_valid() -> None:
    context = get_us_market_context(datetime(2026, 6, 13, 12, 0, tzinfo=ET))

    result = classify_price_freshness("NVDA", "2026-06-12", context)

    assert context["latest_expected_trading_day"] == "2026-06-12"
    assert result["status"] == "休市中，价格有效"
    assert result["is_stale"] is False


def test_holiday_uses_previous_trading_day_as_valid() -> None:
    context = get_us_market_context(datetime(2026, 6, 19, 12, 0, tzinfo=ET))

    result = classify_price_freshness("NVDA", "2026-06-18", context)

    assert context["latest_expected_trading_day"] == "2026-06-18"
    assert result["status"] == "休市中，价格有效"
    assert result["is_stale"] is False


def test_regular_session_waits_for_close_before_requiring_today() -> None:
    context = get_us_market_context(datetime(2026, 6, 16, 10, 0, tzinfo=ET))

    result = classify_price_freshness("NVDA", "2026-06-15", context)

    assert context["latest_expected_trading_day"] == "2026-06-15"
    assert result["status"] == "盘中等待收盘"
    assert result["is_stale"] is False


def test_after_update_window_requires_current_trading_day() -> None:
    context = get_us_market_context(datetime(2026, 6, 16, 20, 0, tzinfo=ET))

    result = classify_price_freshness("NVDA", "2026-06-15", context)

    assert context["latest_expected_trading_day"] == "2026-06-16"
    assert result["status"] == "数据过期"
    assert result["is_stale"] is True


def test_missing_price_date_is_data_insufficient() -> None:
    context = get_us_market_context(datetime(2026, 6, 20, 12, 0, tzinfo=HKT))

    result = classify_price_freshness("NVDA", None, context)

    assert result["status"] == "数据不足"
    assert result["is_stale"] is True


def test_old_price_date_is_expired() -> None:
    context = get_us_market_context(datetime(2026, 6, 13, 12, 0, tzinfo=ET))

    result = classify_price_freshness("NVDA", "2026-06-11", context)

    assert result["status"] == "数据过期"
    assert result["is_stale"] is True
