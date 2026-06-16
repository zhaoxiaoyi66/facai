from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo


US_EASTERN = ZoneInfo("America/New_York")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")


class USMarketSession(str, Enum):
    REGULAR = "REGULAR"
    PRE_MARKET = "PRE_MARKET"
    AFTER_HOURS = "AFTER_HOURS"
    CLOSED_AFTER_SESSION = "CLOSED_AFTER_SESSION"
    WEEKEND_OR_HOLIDAY = "WEEKEND_OR_HOLIDAY"
    UNKNOWN = "UNKNOWN"


SESSION_LABELS = {
    USMarketSession.REGULAR: "美股盘中",
    USMarketSession.PRE_MARKET: "美股盘前",
    USMarketSession.AFTER_HOURS: "美股盘后",
    USMarketSession.CLOSED_AFTER_SESSION: "美股已收盘",
    USMarketSession.WEEKEND_OR_HOLIDAY: "美股休市",
    USMarketSession.UNKNOWN: "市场状态未知",
}

LATEST_DATA_LABELS = {
    USMarketSession.REGULAR: "盘中报价",
    USMarketSession.PRE_MARKET: "盘前参考",
    USMarketSession.AFTER_HOURS: "盘后参考",
    USMarketSession.CLOSED_AFTER_SESSION: "昨夜收盘",
    USMarketSession.WEEKEND_OR_HOLIDAY: "最新可用收盘",
    USMarketSession.UNKNOWN: "最新可用数据",
}

TECHNICAL_STATUS_LABELS = {
    USMarketSession.REGULAR: "价格敏感状态",
    USMarketSession.PRE_MARKET: "盘前参考",
    USMarketSession.AFTER_HOURS: "盘后参考",
    USMarketSession.CLOSED_AFTER_SESSION: "日线按需更新",
    USMarketSession.WEEKEND_OR_HOLIDAY: "沿用最新缓存",
    USMarketSession.UNKNOWN: "需复核",
}


@dataclass(frozen=True)
class USMarketSessionStatus:
    status: USMarketSession
    label: str
    latest_data_label: str
    technical_status_label: str
    now_et: datetime
    next_regular_open_et: datetime | None = None

    @property
    def next_regular_open_hkt(self) -> datetime | None:
        if self.next_regular_open_et is None:
            return None
        return self.next_regular_open_et.astimezone(HONG_KONG)

    @property
    def next_regular_open_hkt_text(self) -> str:
        value = self.next_regular_open_hkt
        if value is None:
            return ""
        return value.strftime("%m/%d %H:%M HKT")


def get_us_market_session_status(now: datetime | None = None, *, calendar: Any | None = None) -> USMarketSessionStatus:
    current = _aware_datetime(now)
    now_et = current.astimezone(US_EASTERN)
    try:
        is_trading_day = _is_trading_day(now_et.date(), calendar=calendar)
    except Exception:
        return _snapshot(USMarketSession.UNKNOWN, now_et, None)

    if not is_trading_day:
        return _snapshot(
            USMarketSession.WEEKEND_OR_HOLIDAY,
            now_et,
            _next_regular_open(now_et, calendar=calendar),
        )

    current_time = now_et.time()
    if time(4, 0) <= current_time < time(9, 30):
        status = USMarketSession.PRE_MARKET
        next_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    elif time(9, 30) <= current_time < time(16, 0):
        status = USMarketSession.REGULAR
        next_open = None
    elif time(16, 0) <= current_time < time(20, 0):
        status = USMarketSession.AFTER_HOURS
        next_open = _next_regular_open(now_et + timedelta(days=1), calendar=calendar, include_current_day=True)
    else:
        status = USMarketSession.CLOSED_AFTER_SESSION
        start = now_et if current_time < time(4, 0) else now_et + timedelta(days=1)
        next_open = _next_regular_open(start, calendar=calendar, include_current_day=True)
    return _snapshot(status, now_et, next_open)


def _snapshot(
    status: USMarketSession,
    now_et: datetime,
    next_regular_open_et: datetime | None,
) -> USMarketSessionStatus:
    return USMarketSessionStatus(
        status=status,
        label=SESSION_LABELS.get(status, "市场状态未知"),
        latest_data_label=LATEST_DATA_LABELS.get(status, "最新可用数据"),
        technical_status_label=TECHNICAL_STATUS_LABELS.get(status, "需复核"),
        now_et=now_et,
        next_regular_open_et=next_regular_open_et,
    )


def _aware_datetime(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current


def _is_trading_day(day: object, *, calendar: Any | None = None) -> bool:
    if calendar is not None:
        if hasattr(calendar, "is_session"):
            return bool(calendar.is_session(day))
        if hasattr(calendar, "valid_days"):
            return bool(calendar.valid_days(day, day))
    return getattr(day, "weekday")() < 5


def _next_regular_open(
    start_et: datetime,
    *,
    calendar: Any | None = None,
    include_current_day: bool = False,
) -> datetime | None:
    day_start = start_et if include_current_day else start_et + timedelta(days=1)
    for offset in range(0, 10):
        candidate_day = (day_start + timedelta(days=offset)).date()
        if _is_trading_day(candidate_day, calendar=calendar):
            return datetime.combine(candidate_day, time(9, 30), tzinfo=US_EASTERN)
    return None
