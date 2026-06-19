from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from data.us_market_session import USMarketSession, get_us_market_session_status


ET = ZoneInfo("America/New_York")


def test_us_market_session_detects_regular_hours() -> None:
    status = get_us_market_session_status(datetime(2026, 6, 15, 10, 0, tzinfo=ET))

    assert status.status == USMarketSession.REGULAR
    assert status.label == "美股盘中"
    assert status.latest_data_label == "盘中报价"
    assert status.latest_data_display_label == "盘中报价 06/15"


def test_us_market_session_detects_pre_and_after_hours() -> None:
    pre = get_us_market_session_status(datetime(2026, 6, 15, 8, 0, tzinfo=ET))
    after = get_us_market_session_status(datetime(2026, 6, 15, 17, 0, tzinfo=ET))

    assert pre.status == USMarketSession.PRE_MARKET
    assert pre.label == "美股盘前"
    assert pre.latest_data_display_label == "盘前参考 06/15"
    assert after.status == USMarketSession.AFTER_HOURS
    assert after.label == "美股盘后"
    assert after.latest_data_display_label == "盘后参考 06/15"


def test_us_market_session_detects_hkt_daytime_as_closed_after_session() -> None:
    hkt = ZoneInfo("Asia/Hong_Kong")
    status = get_us_market_session_status(datetime(2026, 6, 16, 10, 0, tzinfo=hkt))

    assert status.status == USMarketSession.CLOSED_AFTER_SESSION
    assert status.label == "美股已收盘"
    assert status.latest_data_label == "昨夜收盘"
    assert status.latest_data_display_label == "昨夜收盘 06/15"
    assert status.next_regular_open_hkt_text


def test_us_market_session_detects_weekend() -> None:
    status = get_us_market_session_status(datetime(2026, 6, 13, 12, 0, tzinfo=ET))

    assert status.status == USMarketSession.WEEKEND_OR_HOLIDAY
    assert status.label == "美股休市"
    assert status.latest_data_display_label == "最新可用收盘 06/12"


def test_us_market_session_skips_juneteenth_holiday() -> None:
    status = get_us_market_session_status(datetime(2026, 6, 19, 20, 39, tzinfo=ZoneInfo("Asia/Hong_Kong")))

    assert status.status == USMarketSession.WEEKEND_OR_HOLIDAY
    assert status.label == "美股休市"
    assert status.latest_data_display_label == "最新可用收盘 06/18"
    assert status.next_regular_open_hkt_text == "06/22 21:30 HKT"
