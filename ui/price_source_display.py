from __future__ import annotations

from typing import Any


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
            _first_present(sources, "priceSource", "quote_source", "quoteSource", "source") or ""
        ).strip().lower()
        if refresh_mode == "PRICE_ONLY":
            label = "最新报价"
        elif raw_source in {"price_history", "daily_cache", "last_close", "close"}:
            label = "收盘价"
        elif raw_source in {"quote", "quote_snapshot", "fmp", "fmp_cache"}:
            label = "最新报价"
        elif _first_present(sources, "currentPrice", "current_price", "price") is not None:
            label = "最新报价"
        elif _first_present(sources, "quote_updated_at", "price_updated_at", "fetchedAt") is not None:
            label = "报价缓存"
        elif _first_present(sources, "price_as_of", "history_latest_date", "historyLatestDate", "latest_close") is not None:
            label = "收盘价"
        else:
            label = "价格口径待补"
    return label, _price_source_detail(label, sources)


def price_source_label_from_row(row: object) -> tuple[str, str]:
    snapshot = _source_value(row, "rawSnapshot")
    return price_source_label(snapshot if _is_source(snapshot) else {}, row)


def _price_source_detail(label: str, sources: tuple[object, ...]) -> str:
    parts = [f"价格口径：{label}"]
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
        "updated_at",
        "updatedAt",
        "fetchedAt",
    )
    if as_of:
        parts.append(f"参考日：{as_of}")
    if updated_at:
        parts.append(f"更新时间：{updated_at}")
    return "｜".join(str(part) for part in parts if part)


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
        return getter(key)
    return None


def _is_source(source: object) -> bool:
    return callable(getattr(source, "get", None))
