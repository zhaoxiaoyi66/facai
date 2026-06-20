from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from data.us_market_session import US_EASTERN, _is_trading_day, _previous_trading_day

HONG_KONG = ZoneInfo("Asia/Hong_Kong")
PRICE_UPDATE_DELAY_HOURS = 2


@dataclass(frozen=True)
class PriceFreshnessResult:
    status: str
    detail: str
    is_stale: bool
    price_date: date | None
    latest_expected_trading_day: date | None
    market_status: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("price_date", "latest_expected_trading_day"):
            if data[key] is not None:
                data[key] = data[key].isoformat()
        return data


@dataclass(frozen=True)
class USMarketContext:
    now_et: datetime
    is_trading_day: bool
    is_market_open: bool
    is_regular_session: bool
    is_afterhours: bool
    is_weekend: bool
    is_holiday: bool
    latest_expected_trading_day: date | None
    latest_expected_close_time_et: datetime | None
    reason: str
    market_status: str
    allow_update_delay: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["now_et"] = self.now_et.isoformat()
        if self.latest_expected_trading_day is not None:
            data["latest_expected_trading_day"] = self.latest_expected_trading_day.isoformat()
        if self.latest_expected_close_time_et is not None:
            data["latest_expected_close_time_et"] = self.latest_expected_close_time_et.isoformat()
        return data


def get_us_market_context(now: datetime | None = None) -> dict[str, Any]:
    """Return the latest US trading day that local daily prices should cover."""
    now_et = _to_et(now or datetime.now(tz=HONG_KONG))
    today = now_et.date()
    trading_day = _is_trading_day(today)
    close_dt = _close_datetime(today) if trading_day else None
    update_ready_dt = close_dt + timedelta(hours=PRICE_UPDATE_DELAY_HOURS) if close_dt else None

    is_weekend = today.weekday() >= 5
    is_holiday = not is_weekend and not trading_day
    regular_open = datetime.combine(today, time(9, 30), tzinfo=US_EASTERN)
    extended_open = datetime.combine(today, time(4, 0), tzinfo=US_EASTERN)
    extended_close = datetime.combine(today, time(20, 0), tzinfo=US_EASTERN)

    is_regular_session = bool(trading_day and close_dt and regular_open <= now_et < close_dt)
    is_afterhours = bool(trading_day and close_dt and close_dt <= now_et < extended_close)
    is_market_open = bool(trading_day and extended_open <= now_et < extended_close)

    if not trading_day:
        expected_day = _previous_trading_day(today)
        return USMarketContext(
            now_et=now_et,
            is_trading_day=False,
            is_market_open=False,
            is_regular_session=False,
            is_afterhours=False,
            is_weekend=is_weekend,
            is_holiday=is_holiday,
            latest_expected_trading_day=expected_day,
            latest_expected_close_time_et=_close_datetime(expected_day) if expected_day else None,
            reason="周末休市" if is_weekend else "美股假期休市",
            market_status="美股休市",
        ).to_dict()

    previous_day = _previous_trading_day(today)
    if close_dt and now_et < close_dt:
        return USMarketContext(
            now_et=now_et,
            is_trading_day=True,
            is_market_open=is_market_open,
            is_regular_session=is_regular_session,
            is_afterhours=is_afterhours,
            is_weekend=False,
            is_holiday=False,
            latest_expected_trading_day=previous_day,
            latest_expected_close_time_et=_close_datetime(previous_day) if previous_day else None,
            reason="盘中等待收盘",
            market_status="盘中等待收盘",
        ).to_dict()

    if update_ready_dt and now_et < update_ready_dt:
        return USMarketContext(
            now_et=now_et,
            is_trading_day=True,
            is_market_open=is_market_open,
            is_regular_session=is_regular_session,
            is_afterhours=is_afterhours,
            is_weekend=False,
            is_holiday=False,
            latest_expected_trading_day=previous_day,
            latest_expected_close_time_et=_close_datetime(previous_day) if previous_day else None,
            reason="盘后等待数据更新",
            market_status="盘后等待更新",
            allow_update_delay=True,
        ).to_dict()

    return USMarketContext(
        now_et=now_et,
        is_trading_day=True,
        is_market_open=is_market_open,
        is_regular_session=is_regular_session,
        is_afterhours=is_afterhours,
        is_weekend=False,
        is_holiday=False,
        latest_expected_trading_day=today,
        latest_expected_close_time_et=close_dt,
        reason="价格应覆盖最新交易日",
        market_status="价格应更新",
    ).to_dict()


def classify_price_freshness(
    symbol: str,
    latest_price_date: Any,
    market_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = market_context or get_us_market_context()
    price_date = coerce_price_date(latest_price_date)
    expected_day = coerce_price_date(context.get("latest_expected_trading_day"))
    market_status = str(context.get("market_status") or "市场状态未知")

    if price_date is None:
        return PriceFreshnessResult(
            status="数据不足",
            detail=f"{symbol} 缺少价格日期",
            is_stale=True,
            price_date=None,
            latest_expected_trading_day=expected_day,
            market_status=market_status,
        ).to_dict()

    if expected_day is None:
        return PriceFreshnessResult(
            status="数据不足",
            detail="无法确认最近应有交易日",
            is_stale=True,
            price_date=price_date,
            latest_expected_trading_day=None,
            market_status=market_status,
        ).to_dict()

    if price_date < expected_day:
        return PriceFreshnessResult(
            status="数据过期",
            detail=f"价格日期 {price_date.isoformat()} 落后于应有交易日 {expected_day.isoformat()}",
            is_stale=True,
            price_date=price_date,
            latest_expected_trading_day=expected_day,
            market_status=market_status,
        ).to_dict()

    reason = str(context.get("reason") or "")
    if context.get("is_weekend") or context.get("is_holiday") or not context.get("is_market_open"):
        status = "休市中，价格有效"
        detail = "已覆盖最近一个有效美股交易日"
    elif reason == "盘中等待收盘":
        status = "盘中等待收盘"
        detail = "当前交易日日线尚未形成，使用上一交易日数据"
    elif reason == "盘后等待数据更新":
        status = "盘后等待数据更新"
        detail = "刚收盘不久，允许日线数据延迟更新"
    else:
        status = "价格有效"
        detail = "已覆盖最近应有美股交易日"

    return PriceFreshnessResult(
        status=status,
        detail=detail,
        is_stale=False,
        price_date=price_date,
        latest_expected_trading_day=expected_day,
        market_status=market_status,
    ).to_dict()


def infer_latest_price_date(payload: dict[str, Any]) -> date | None:
    date_keys = (
        "latest_price_date",
        "price_date",
        "market_date",
        "history_latest_date",
        "historyLatestDate",
        "latest_close_date",
        "latestCloseDate",
        "data_as_of",
        "as_of_date",
    )
    for key in date_keys:
        value = payload.get(key)
        parsed = coerce_price_date(value)
        if parsed is not None:
            return parsed

    timestamp_keys = (
        "data_updated_at",
        "updated_at",
        "updatedAt",
        "fetchedAt",
        "quote_updated_at",
        "price_updated_at",
    )
    for key in timestamp_keys:
        parsed = coerce_price_date(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def coerce_price_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(US_EASTERN).date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "nat", "--"}:
        return None

    normalized = text.replace("Z", "+00:00")
    if "T" in normalized or ":" in normalized:
        try:
            parsed_dt = datetime.fromisoformat(normalized)
        except ValueError:
            parsed_dt = None
        if parsed_dt is not None:
            if parsed_dt.tzinfo is None:
                return parsed_dt.date()
            return parsed_dt.astimezone(US_EASTERN).date()

    try:
        return date.fromisoformat(normalized[:10])
    except ValueError:
        return None


def _to_et(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=US_EASTERN)
    return value.astimezone(US_EASTERN)


def _close_datetime(day: date | None) -> datetime | None:
    if day is None:
        return None
    close_time = time(13, 0) if _is_early_close_day(day) else time(16, 0)
    return datetime.combine(day, close_time, tzinfo=US_EASTERN)


def _is_early_close_day(day: date) -> bool:
    if not _is_trading_day(day):
        return False
    if day.month == 11 and day.weekday() == 4 and day == _thanksgiving(day.year) + timedelta(days=1):
        return True
    if day.month == 12 and day.day == 24 and day.weekday() < 5:
        return True
    if day.month == 7 and day.day == 3 and day.weekday() < 5:
        return True
    return False


def _thanksgiving(year: int) -> date:
    first = date(year, 11, 1)
    days_until_thursday = (3 - first.weekday()) % 7
    first_thursday = first + timedelta(days=days_until_thursday)
    return first_thursday + timedelta(weeks=3)
