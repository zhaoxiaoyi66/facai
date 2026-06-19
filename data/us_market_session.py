from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
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
    latest_regular_close_date: date | None = None

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

    @property
    def latest_data_display_label(self) -> str:
        if self.latest_regular_close_date is None:
            return self.latest_data_label
        return f"{self.latest_data_label} {self.latest_regular_close_date:%m/%d}"


def get_us_market_session_status(now: datetime | None = None, *, calendar: Any | None = None) -> USMarketSessionStatus:
    current = _aware_datetime(now)
    now_et = current.astimezone(US_EASTERN)
    try:
        is_trading_day = _is_trading_day(now_et.date(), calendar=calendar)
    except Exception:
        return _snapshot(USMarketSession.UNKNOWN, now_et, None, calendar=calendar)

    if not is_trading_day:
        return _snapshot(
            USMarketSession.WEEKEND_OR_HOLIDAY,
            now_et,
            _next_regular_open(now_et, calendar=calendar),
            calendar=calendar,
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
    return _snapshot(status, now_et, next_open, calendar=calendar)


def _snapshot(
    status: USMarketSession,
    now_et: datetime,
    next_regular_open_et: datetime | None,
    *,
    calendar: Any | None = None,
) -> USMarketSessionStatus:
    return USMarketSessionStatus(
        status=status,
        label=SESSION_LABELS.get(status, "市场状态未知"),
        latest_data_label=LATEST_DATA_LABELS.get(status, "最新可用数据"),
        technical_status_label=TECHNICAL_STATUS_LABELS.get(status, "需复核"),
        now_et=now_et,
        next_regular_open_et=next_regular_open_et,
        latest_regular_close_date=_latest_regular_close_date(status, now_et, calendar=calendar),
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
    if not isinstance(day, date):
        return getattr(day, "weekday")() < 5
    return day.weekday() < 5 and day not in _us_market_holidays(day.year)


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


def _latest_regular_close_date(
    status: USMarketSession,
    now_et: datetime,
    *,
    calendar: Any | None = None,
) -> date | None:
    if status in {USMarketSession.REGULAR, USMarketSession.PRE_MARKET, USMarketSession.AFTER_HOURS}:
        return now_et.date()
    if status == USMarketSession.CLOSED_AFTER_SESSION and now_et.time() >= time(20, 0):
        return now_et.date()
    if status in {USMarketSession.CLOSED_AFTER_SESSION, USMarketSession.WEEKEND_OR_HOLIDAY}:
        return _previous_trading_day(now_et.date(), calendar=calendar)
    return None


def _previous_trading_day(start_day: date, *, calendar: Any | None = None) -> date | None:
    for offset in range(1, 10):
        candidate = start_day - timedelta(days=offset)
        if _is_trading_day(candidate, calendar=calendar):
            return candidate
    return None


def _us_market_holidays(year: int) -> set[date]:
    holidays: set[date] = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _good_friday(year),
        _last_weekday(year, 5, 0),  # Memorial Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving Day
    }

    for month, day in ((1, 1), (6, 19), (7, 4), (12, 25)):
        observed = _observed_fixed_holiday(year, month, day)
        if observed.year == year:
            holidays.add(observed)

    next_new_year_observed = _observed_fixed_holiday(year + 1, 1, 1)
    if next_new_year_observed.year == year:
        holidays.add(next_new_year_observed)

    return holidays


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    value = date(year, month, day)
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    value = date(year, month, 1)
    days_until_weekday = (weekday - value.weekday()) % 7
    return value + timedelta(days=days_until_weekday + 7 * (nth - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        value = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        value = date(year, month + 1, 1) - timedelta(days=1)
    return value - timedelta(days=(value.weekday() - weekday) % 7)


def _good_friday(year: int) -> date:
    # Anonymous Gregorian algorithm; Good Friday is two days before Easter Sunday.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day) - timedelta(days=2)
