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
class QuoteSnapshotFreshnessResult:
    freshness_status: str
    freshness_label: str
    reason: str
    can_use_price: bool
    should_prompt_refresh: bool
    snapshot_cached_at: datetime | None
    price_date: date | None
    latest_expected_trading_day: date | None
    market_state_label: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.snapshot_cached_at is not None:
            data["snapshot_cached_at"] = self.snapshot_cached_at.isoformat()
        for key in ("price_date", "latest_expected_trading_day"):
            if data[key] is not None:
                data[key] = data[key].isoformat()
        return data


@dataclass(frozen=True)
class USMarketContext:
    now_et: datetime
    now_hkt: datetime
    is_trading_day: bool
    is_market_open: bool
    is_regular_session: bool
    is_premarket: bool
    is_afterhours: bool
    is_weekend: bool
    is_holiday: bool
    latest_expected_trading_day: date | None
    latest_expected_close_time_et: datetime | None
    next_market_open_et: datetime | None
    reason: str
    market_status: str
    market_state_label: str
    allow_update_delay: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["now_et"] = self.now_et.isoformat()
        data["now_hkt"] = self.now_hkt.isoformat()
        if self.latest_expected_trading_day is not None:
            data["latest_expected_trading_day"] = self.latest_expected_trading_day.isoformat()
        if self.latest_expected_close_time_et is not None:
            data["latest_expected_close_time_et"] = self.latest_expected_close_time_et.isoformat()
        if self.next_market_open_et is not None:
            data["next_market_open_et"] = self.next_market_open_et.isoformat()
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
    is_premarket = bool(trading_day and extended_open <= now_et < regular_open)
    is_afterhours = bool(trading_day and close_dt and close_dt <= now_et < extended_close)
    is_market_open = bool(trading_day and extended_open <= now_et < extended_close)
    next_market_open = _next_regular_open(now_et)

    if not trading_day:
        expected_day = _previous_trading_day(today)
        return USMarketContext(
            now_et=now_et,
            now_hkt=now_et.astimezone(HONG_KONG),
            is_trading_day=False,
            is_market_open=False,
            is_regular_session=False,
            is_premarket=False,
            is_afterhours=False,
            is_weekend=is_weekend,
            is_holiday=is_holiday,
            latest_expected_trading_day=expected_day,
            latest_expected_close_time_et=_close_datetime(expected_day) if expected_day else None,
            next_market_open_et=next_market_open,
            reason="周末休市" if is_weekend else "美股假期休市",
            market_status="美股休市",
            market_state_label="周末休市" if is_weekend else "节假日休市",
        ).to_dict()

    previous_day = _previous_trading_day(today)
    if close_dt and now_et < close_dt:
        return USMarketContext(
            now_et=now_et,
            now_hkt=now_et.astimezone(HONG_KONG),
            is_trading_day=True,
            is_market_open=is_market_open,
            is_regular_session=is_regular_session,
            is_premarket=is_premarket,
            is_afterhours=is_afterhours,
            is_weekend=False,
            is_holiday=False,
            latest_expected_trading_day=previous_day,
            latest_expected_close_time_et=_close_datetime(previous_day) if previous_day else None,
            next_market_open_et=regular_open if now_et < regular_open else None,
            reason="盘中等待收盘",
            market_status="盘中等待收盘",
            market_state_label="盘前" if is_premarket else "盘中",
        ).to_dict()

    if update_ready_dt and now_et < update_ready_dt:
        return USMarketContext(
            now_et=now_et,
            now_hkt=now_et.astimezone(HONG_KONG),
            is_trading_day=True,
            is_market_open=is_market_open,
            is_regular_session=is_regular_session,
            is_premarket=is_premarket,
            is_afterhours=is_afterhours,
            is_weekend=False,
            is_holiday=False,
            latest_expected_trading_day=previous_day,
            latest_expected_close_time_et=_close_datetime(previous_day) if previous_day else None,
            next_market_open_et=next_market_open,
            reason="盘后等待数据更新",
            market_status="盘后等待更新",
            market_state_label="盘后",
            allow_update_delay=True,
        ).to_dict()

    return USMarketContext(
        now_et=now_et,
        now_hkt=now_et.astimezone(HONG_KONG),
        is_trading_day=True,
        is_market_open=is_market_open,
        is_regular_session=is_regular_session,
        is_premarket=is_premarket,
        is_afterhours=is_afterhours,
        is_weekend=False,
        is_holiday=False,
        latest_expected_trading_day=today,
        latest_expected_close_time_et=close_dt,
        next_market_open_et=next_market_open,
        reason="价格应覆盖最新交易日",
        market_status="价格应更新",
        market_state_label="盘后" if is_afterhours else "休市中",
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


def classify_quote_snapshot_freshness(
    symbol: str,
    snapshot: dict[str, Any] | None,
    latest_price_date: Any = None,
    market_context: dict[str, Any] | None = None,
    *,
    regular_ttl_minutes: float = 5,
    extended_ttl_minutes: float = 30,
    open_grace_minutes: float = 15,
) -> dict[str, Any]:
    context = market_context or get_us_market_context()
    payload = snapshot.get("payload") if isinstance(snapshot, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    snapshot_cached_at = _coerce_datetime(snapshot.get("fetched_at") if isinstance(snapshot, dict) else None)
    has_snapshot_price = _has_quote_price(payload)
    explicit_price_date = coerce_price_date(latest_price_date)
    price_date = explicit_price_date or infer_latest_price_date(payload)
    if price_date is None and has_snapshot_price and snapshot_cached_at is not None:
        price_date = snapshot_cached_at.astimezone(US_EASTERN).date()
    expected_day = coerce_price_date(context.get("latest_expected_trading_day"))
    market_state = str(context.get("market_state_label") or context.get("market_status") or "市场状态未知")

    if not has_snapshot_price and explicit_price_date is None:
        return QuoteSnapshotFreshnessResult(
            freshness_status="数据不足",
            freshness_label="数据不足",
            reason=f"{symbol} 缺少 quote snapshot 和可用日线价格",
            can_use_price=False,
            should_prompt_refresh=True,
            snapshot_cached_at=snapshot_cached_at,
            price_date=None,
            latest_expected_trading_day=expected_day,
            market_state_label=market_state,
        ).to_dict()

    if expected_day is None:
        return QuoteSnapshotFreshnessResult(
            freshness_status="数据不足",
            freshness_label="数据不足",
            reason="无法确认最近应有交易日",
            can_use_price=False,
            should_prompt_refresh=True,
            snapshot_cached_at=snapshot_cached_at,
            price_date=price_date,
            latest_expected_trading_day=None,
            market_state_label=market_state,
        ).to_dict()

    if price_date is not None and price_date < expected_day:
        return QuoteSnapshotFreshnessResult(
            freshness_status="数据过期",
            freshness_label="数据过期",
            reason=f"价格日期 {price_date.isoformat()} 落后于应有交易日 {expected_day.isoformat()}",
            can_use_price=False,
            should_prompt_refresh=True,
            snapshot_cached_at=snapshot_cached_at,
            price_date=price_date,
            latest_expected_trading_day=expected_day,
            market_state_label=market_state,
        ).to_dict()

    if _market_is_closed_for_quotes(context):
        return QuoteSnapshotFreshnessResult(
            freshness_status="休市中，价格有效",
            freshness_label="休市中，价格有效",
            reason=f"当前美股休市，数据已覆盖最近一个有效交易日 {expected_day.isoformat()}",
            can_use_price=True,
            should_prompt_refresh=False,
            snapshot_cached_at=snapshot_cached_at,
            price_date=price_date,
            latest_expected_trading_day=expected_day,
            market_state_label=market_state,
        ).to_dict()

    if context.get("is_premarket") or context.get("is_afterhours") or context.get("allow_update_delay"):
        return QuoteSnapshotFreshnessResult(
            freshness_status="盘后使用最近有效价",
            freshness_label="盘后使用最近有效价",
            reason="当前处于盘前/盘后或数据更新宽限期，使用最近有效价格",
            can_use_price=True,
            should_prompt_refresh=False,
            snapshot_cached_at=snapshot_cached_at,
            price_date=price_date,
            latest_expected_trading_day=expected_day,
            market_state_label=market_state,
        ).to_dict()

    if context.get("is_regular_session"):
        if snapshot_cached_at is None or not has_snapshot_price:
            return QuoteSnapshotFreshnessResult(
                freshness_status="盘中价格过期",
                freshness_label="盘中价格过期",
                reason="当前美股盘中，缺少可用 quote snapshot",
                can_use_price=False,
                should_prompt_refresh=True,
                snapshot_cached_at=snapshot_cached_at,
                price_date=price_date,
                latest_expected_trading_day=expected_day,
                market_state_label=market_state,
            ).to_dict()
        if _inside_regular_open_grace(context, open_grace_minutes):
            return QuoteSnapshotFreshnessResult(
                freshness_status="盘后使用最近有效价",
                freshness_label="盘后使用最近有效价",
                reason=f"美股刚开盘，仍在 {int(open_grace_minutes)} 分钟刷新宽限期",
                can_use_price=True,
                should_prompt_refresh=False,
                snapshot_cached_at=snapshot_cached_at,
                price_date=price_date,
                latest_expected_trading_day=expected_day,
                market_state_label=market_state,
            ).to_dict()
        age_minutes = _age_minutes(snapshot_cached_at, context)
        if age_minutes is not None and age_minutes <= regular_ttl_minutes:
            return QuoteSnapshotFreshnessResult(
                freshness_status="实时有效",
                freshness_label="实时有效",
                reason=f"quote snapshot 在 {int(regular_ttl_minutes)} 分钟 TTL 内",
                can_use_price=True,
                should_prompt_refresh=False,
                snapshot_cached_at=snapshot_cached_at,
                price_date=price_date,
                latest_expected_trading_day=expected_day,
                market_state_label=market_state,
            ).to_dict()
        return QuoteSnapshotFreshnessResult(
            freshness_status="盘中价格过期",
            freshness_label="盘中价格过期",
            reason=f"当前美股盘中，quote snapshot 已超过 {int(regular_ttl_minutes)} 分钟 TTL",
            can_use_price=False,
            should_prompt_refresh=True,
            snapshot_cached_at=snapshot_cached_at,
            price_date=price_date,
            latest_expected_trading_day=expected_day,
            market_state_label=market_state,
        ).to_dict()

    age_minutes = _age_minutes(snapshot_cached_at, context)
    if age_minutes is not None and age_minutes <= extended_ttl_minutes:
        return QuoteSnapshotFreshnessResult(
            freshness_status="实时有效",
            freshness_label="实时有效",
            reason=f"quote snapshot 在 {int(extended_ttl_minutes)} 分钟 TTL 内",
            can_use_price=True,
            should_prompt_refresh=False,
            snapshot_cached_at=snapshot_cached_at,
            price_date=price_date,
            latest_expected_trading_day=expected_day,
            market_state_label=market_state,
        ).to_dict()

    return QuoteSnapshotFreshnessResult(
        freshness_status="盘后使用最近有效价",
        freshness_label="盘后使用最近有效价",
        reason="当前非盘中，使用最近有效价格",
        can_use_price=True,
        should_prompt_refresh=False,
        snapshot_cached_at=snapshot_cached_at,
        price_date=price_date,
        latest_expected_trading_day=expected_day,
        market_state_label=market_state,
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


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=US_EASTERN)
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "nat", "--"}:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=US_EASTERN)


def _has_quote_price(payload: dict[str, Any]) -> bool:
    for key in ("current_price", "currentPrice", "price", "regularMarketPrice"):
        value = payload.get(key)
        if value is None or value == "":
            continue
        try:
            float(value)
        except (TypeError, ValueError):
            continue
        return True
    return False


def _to_et(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=US_EASTERN)
    return value.astimezone(US_EASTERN)


def _next_regular_open(now_et: datetime) -> datetime | None:
    start = now_et.astimezone(US_EASTERN).date()
    include_today = now_et.astimezone(US_EASTERN).time() < time(9, 30)
    for offset in range(0 if include_today else 1, 10):
        candidate = start + timedelta(days=offset)
        if _is_trading_day(candidate):
            return datetime.combine(candidate, time(9, 30), tzinfo=US_EASTERN)
    return None


def _market_is_closed_for_quotes(context: dict[str, Any]) -> bool:
    return bool(
        context.get("is_weekend")
        or context.get("is_holiday")
        or (
            not context.get("is_market_open")
            and not context.get("is_premarket")
            and not context.get("is_afterhours")
            and not context.get("is_regular_session")
        )
    )


def _inside_regular_open_grace(context: dict[str, Any], open_grace_minutes: float) -> bool:
    now_et = _coerce_datetime(context.get("now_et"))
    if now_et is None:
        return False
    now_et = now_et.astimezone(US_EASTERN)
    regular_open = datetime.combine(now_et.date(), time(9, 30), tzinfo=US_EASTERN)
    return regular_open <= now_et < regular_open + timedelta(minutes=open_grace_minutes)


def _age_minutes(snapshot_cached_at: datetime | None, context: dict[str, Any]) -> float | None:
    if snapshot_cached_at is None:
        return None
    now_et = _coerce_datetime(context.get("now_et"))
    if now_et is None:
        return None
    return (now_et.astimezone(US_EASTERN) - snapshot_cached_at.astimezone(US_EASTERN)).total_seconds() / 60


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
