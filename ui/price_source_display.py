from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


HONG_KONG = ZoneInfo("Asia/Hong_Kong")


def price_source_label(*sources: object) -> tuple[str, str]:
    session = str(
        _first_present(
            sources,
            "price_session",
            "current_price_source",
            "priceSession",
            "currentPriceSource",
        )
        or ""
    ).strip().upper()
    label = {
        "REGULAR": "盘中报价",
        "PRE_MARKET": "盘前参考",
        "AFTER_HOURS": "盘后参考",
        "LAST_CLOSE": "昨夜收盘",
    }.get(session)
    if not label:
        refresh_mode = str(_first_present(sources, "refresh_mode", "refreshMode") or "").strip().upper()
        raw_source = str(
            _first_present(sources, "price_source", "priceSource", "quote_source", "quoteSource", "source") or ""
        ).strip().lower()
        if refresh_mode == "PRICE_ONLY":
            label = "最新报价"
        elif raw_source in {"price_history", "daily_cache", "last_close", "close"}:
            label = "收盘价"
        elif raw_source in {"quote", "quote_snapshot", "fmp", "fmp_cache"}:
            label = "最新报价"
        elif _first_present(sources, "currentPrice", "current_price", "price") is not None:
            label = "最新报价"
        elif _first_present(sources, "quote_updated_at", "price_updated_at", "fetched_at", "fetchedAt") is not None:
            label = "报价缓存"
        elif _first_present(sources, "price_as_of", "history_latest_date", "historyLatestDate", "latest_close") is not None:
            label = "收盘价"
        else:
            label = "价格口径待补"
    return _compact_price_source_label(label, sources), _price_source_detail(label, sources)


def price_source_label_from_row(row: object) -> tuple[str, str]:
    snapshot = _source_value(row, "rawSnapshot")
    return price_source_label(snapshot if _is_source(snapshot) else {}, row)


def _price_source_detail(label: str, sources: tuple[object, ...]) -> str:
    parts = [f"价格口径：{label}"]
    market_session = _first_present(sources, "market_session_at_refresh", "marketSessionAtRefresh")
    as_of = _first_present(
        sources,
        "price_as_of",
        "history_latest_date",
        "historyLatestDate",
        "latest_close_date",
        "date",
    )
    updated_at = _first_present(
        sources,
        "quote_updated_at",
        "price_updated_at",
        "last_close_synced_at",
        "data_updated_at",
        "dataUpdatedAt",
        "updated_at",
        "updatedAt",
        "fetched_at",
        "fetchedAt",
    )
    if market_session:
        parts.append(f"刷新时段：{_market_session_label(market_session)}")
    if as_of:
        parts.append(f"数据日期：{_detail_date(as_of)}")
    if updated_at:
        parts.append(f"刷新时间：{_detail_datetime(updated_at)}")
    return "｜".join(str(part) for part in parts if part)


def _market_session_label(value: object) -> str:
    text = str(value or "").strip().upper()
    return {
        "REGULAR": "美股盘中",
        "PRE_MARKET": "美股盘前",
        "AFTER_HOURS": "美股盘后",
        "CLOSED_AFTER_SESSION": "美股已收盘",
        "WEEKEND_OR_HOLIDAY": "美股休市",
        "UNKNOWN": "市场状态未知",
    }.get(text, "市场状态未知")


def _compact_price_source_label(label: str, sources: tuple[object, ...]) -> str:
    if label in {"价格口径待补"}:
        return label
    as_of = _first_present(
        sources,
        "price_as_of",
        "history_latest_date",
        "historyLatestDate",
        "latest_close_date",
        "date",
    )
    updated_at = _first_present(
        sources,
        "quote_updated_at",
        "price_updated_at",
        "last_close_synced_at",
        "data_updated_at",
        "dataUpdatedAt",
        "updated_at",
        "updatedAt",
        "fetched_at",
        "fetchedAt",
    )
    suffix = _compact_date(as_of) if label in {"昨夜收盘", "收盘价"} and as_of else _compact_datetime(updated_at)
    return f"{label} {suffix}" if suffix else label


def _compact_date(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = _parse_datetime(text)
    if parsed is not None:
        return parsed.strftime("%m/%d")
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return f"{text[5:7]}/{text[8:10]}"
    return text[:10]


def _detail_date(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "待补"
    parsed = _parse_datetime(text)
    if parsed is not None:
        return parsed.strftime("%Y-%m-%d")
    return text


def _compact_datetime(value: object) -> str:
    parsed = _parse_datetime(str(value or "").strip())
    if parsed is None:
        return ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(HONG_KONG)
    return parsed.strftime("%m/%d %H:%M")


def _detail_datetime(value: object) -> str:
    parsed = _parse_datetime(str(value or "").strip())
    if parsed is None:
        return "待补"
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(HONG_KONG)
    return f"{parsed:%m/%d %H:%M} HKT"


def _parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromisoformat(f"{text}T00:00:00")
        except ValueError:
            return None


def _first_present(sources: tuple[object, ...], *keys: str) -> Any:
    for source in sources:
        if not _is_source(source):
            continue
        for key in keys:
            value = _source_value(source, key)
            if value not in (None, ""):
                return value
    return None


def _source_value(source: object, key: str) -> Any:
    getter = getattr(source, "get", None)
    if callable(getter):
        value = getter(key)
        if value not in (None, ""):
            return value
        payload = getter("payload")
        if _is_source(payload):
            return _source_value(payload, key)
    return None


def _is_source(source: object) -> bool:
    return callable(getattr(source, "get", None))
