from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from data.news_radar import FMPNewsClient, NewsEndpointUnavailable, normalize_news_record
from data.providers import get_secret
from data.us_market_session import _is_trading_day, _previous_trading_day

ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")
NEWS_CACHE_PATH = Path("data/cache/weekend_spread_news.sqlite")
NEWS_TTL_HOURS = 48
NEWS_MODE_CURRENT = "current_shutdown_window"
NEWS_MODE_HISTORICAL = "historical_sample"

NEWS_STATUS_UNCHECKED = "未检查"
NEWS_STATUS_CHECKING = "检查中"
NEWS_STATUS_NO_RELEVANT = "无相关新闻"
NEWS_STATUS_NO_MAJOR = "无重大新闻"
NEWS_STATUS_EXPLAINED = "有新闻解释"
NEWS_STATUS_DIRECTION_MATCH = "新闻方向一致"
NEWS_STATUS_DIRECTION_MISMATCH = "新闻方向不一致"
NEWS_STATUS_OPINION = "观点文章"
NEWS_STATUS_FAILED = "接口失败"
NEWS_STATUS_INSUFFICIENT = "数据不足"
NEWS_STATUS_CACHE_EXPIRED = "缓存过期"

NEWS_CACHE_VALID = "缓存有效"
NEWS_CACHE_EXPIRED = "缓存过期"
NEWS_CACHE_MISSING = "未检查"

GAP_EXPLANATION_NONE = "无新闻解释"
GAP_EXPLANATION_EXPLAINED = "有新闻解释"
GAP_EXPLANATION_MATCH = "新闻方向一致"
GAP_EXPLANATION_MISMATCH = "新闻方向不一致"
GAP_EXPLANATION_OPINION = "观点文章，不足以解释价差"
GAP_EXPLANATION_INSUFFICIENT = "数据不足"

WINDOW_CLOSED_MARKET = "closed_market"
WINDOW_PRE_ANCHOR = "pre_anchor"
WINDOW_AFTER_P0 = "after_p0"
WINDOW_PRE_OVERNIGHT = "pre_overnight"

WINDOW_LABELS = {
    WINDOW_CLOSED_MARKET: "休市新闻窗口",
    WINDOW_PRE_ANCHOR: "盘后锚点前新闻",
    WINDOW_AFTER_P0: "锚点后新闻，重点",
    WINDOW_PRE_OVERNIGHT: "夜盘前新闻",
}

MISSING_URL_TEXT = "原文链接缺失"


def build_weekend_spread_news_context(
    symbol: str,
    sample: dict[str, Any],
    *,
    store: "WeekendSpreadNewsStore | None" = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a cached after-hours/weekend news explanation for one spread sample."""

    store = store or WeekendSpreadNewsStore()
    clean_symbol = _clean_symbol(symbol or sample.get("ticker"))
    windows = weekend_spread_news_windows(sample, now=now)
    if not clean_symbol or not windows.get("ok"):
        reason = str(windows.get("reason") or "缺少休市新闻窗口时间，暂时无法判断新闻是否解释价差。")
        return {
            "symbol": clean_symbol,
            "windows": windows,
            "window_start_et": windows.get("window_start_et"),
            "window_end_et": windows.get("window_end_et"),
            "window_label": windows.get("window_label") or "休市新闻窗口",
            "news_count": 0,
            "major_news_count": 0,
            "positive_news_count": 0,
            "negative_news_count": 0,
            "opinion_news_count": 0,
            "news_count_after_p0": 0,
            "major_news_after_p0": 0,
            "positive_news_after_p0": 0,
            "negative_news_after_p0": 0,
            "gap_explanation_label": "数据不足",
            "explanation_zh": reason,
            "key_news_list": [],
            "news_items": [],
            "window_news": _legacy_window_news([]),
        }

    all_news = store.list_news(
        clean_symbol,
        start_et=windows["window_start_et"],
        end_et=windows["window_end_et"],
        limit=200,
    )
    window_items = _sort_news_desc(all_news)
    major_news = [item for item in window_items if item.get("impact_level") == "重大"]
    positive_news = [item for item in major_news if item.get("sentiment_label") == "正面"]
    negative_news = [item for item in major_news if item.get("sentiment_label") == "负面"]
    premium = _number(sample.get("binance_premium_pct") or sample.get("weekend_premium_pct"))

    opinion_news = [item for item in window_items if item.get("event_type") == "观点文章"]
    if not window_items:
        label = "无新闻解释"
        explanation = "休市期间未发现重大新闻，本轮 Binance 价差缺少明确新闻解释，更可能来自流动性、资金行为、映射溢价或市场预期。"
    elif not major_news and opinion_news:
        label = "观点文章，不足以解释价差"
        explanation = "休市期间主要为观点文章，不属于明确公司基本面事件，需人工复核。"
    elif not major_news:
        label = "无新闻解释"
        explanation = "休市期间有普通新闻，但未发现重大公司事件，暂不足以解释大幅价差。"
    elif premium is None:
        label = "有新闻解释"
        explanation = "休市期间出现重大新闻，但缺少价差方向，需人工判断是否解释本轮波动。"
    elif (premium >= 0 and positive_news) or (premium < 0 and negative_news):
        label = "新闻方向一致"
        explanation = "休市期间重大新闻方向与 Binance 价差方向一致，价差可能部分受到新闻催化。"
    elif (premium >= 0 and negative_news) or (premium < 0 and positive_news):
        label = "新闻方向不一致"
        explanation = "休市期间重大新闻方向与 Binance 价差方向相反，暂不足以解释本轮价差。"
    else:
        label = "有新闻解释"
        explanation = "休市期间出现重大新闻，但情绪方向不明确，需要人工复核影响。"

    key_news = major_news or opinion_news[:3] or window_items[:3]
    return {
        "symbol": clean_symbol,
        "windows": windows,
        "window_start_et": windows.get("window_start_et"),
        "window_end_et": windows.get("window_end_et"),
        "window_label": windows.get("window_label") or "休市新闻窗口",
        "news_count": len(window_items),
        "major_news_count": len(major_news),
        "positive_news_count": len(positive_news),
        "negative_news_count": len(negative_news),
        "opinion_news_count": len(opinion_news),
        "news_count_after_p0": len(window_items),
        "major_news_after_p0": len(major_news),
        "positive_news_after_p0": len(positive_news),
        "negative_news_after_p0": len(negative_news),
        "gap_explanation_label": label,
        "explanation_zh": explanation,
        "key_news_list": key_news[:5],
        "news_items": window_items,
        "window_news": _legacy_window_news(window_items),
    }


def refresh_weekend_spread_news(
    symbol: str,
    sample: dict[str, Any],
    *,
    store: "WeekendSpreadNewsStore | None" = None,
    client: FMPNewsClient | None = None,
    force: bool = False,
    limit: int = 30,
) -> dict[str, Any]:
    """Refresh only the weekend-spread news cache for one symbol/sample."""

    store = store or WeekendSpreadNewsStore()
    client = client or FMPNewsClient(api_key=get_secret("FMP_API_KEY"))
    clean_symbol = _clean_symbol(symbol or sample.get("ticker"))
    if not clean_symbol:
        return {"status": "error", "count": 0, "message": "缺少股票代码"}
    sample_key = weekend_spread_news_sample_key(clean_symbol, sample)
    if not force and not store.should_refresh(sample_key, NEWS_TTL_HOURS):
        return {"status": "cache", "count": 0, "message": "使用缓存"}
    windows = weekend_spread_news_windows(sample)

    fetched_at = datetime.now(timezone.utc)
    raw_items: list[dict[str, Any]] = []
    unavailable: list[str] = []
    errors: list[str] = []
    successful_endpoints = 0
    for label, fetcher in (
        ("Stock News", lambda: client.fetch_stock_news(clean_symbol, limit=limit)),
        ("Press Releases", lambda: client.fetch_press_releases(clean_symbol, limit=limit)),
    ):
        try:
            raw_items.extend(fetcher())
            successful_endpoints += 1
        except NewsEndpointUnavailable:
            unavailable.append(label)
        except Exception as exc:  # pragma: no cover - defensive downgrade
            if "HTTP Error 404" in str(exc):
                unavailable.append(label)
            else:
                errors.append(f"{label}: {exc}")

    items = [normalize_weekend_spread_news_record(clean_symbol, raw, fetched_at=fetched_at) for raw in raw_items]
    count = store.upsert_many(items)
    status = "ok" if successful_endpoints > 0 else "error"
    message_parts = [f"写入 {count} 条休市新闻"]
    if unavailable:
        message_parts.append("套餐不可用：" + "、".join(unavailable))
    if errors:
        message_parts.append("部分失败：" + "；".join(errors[:2]))
    message = "；".join(str(part) for part in message_parts if str(part).strip())
    store.set_fetch_status(sample_key, status, message)
    if status == "error":
        store.set_check_status(
            sample_key,
            {
                "symbol": clean_symbol,
                "sample_key": sample_key,
                "window_id": weekend_spread_news_window_id(sample),
                "window_start_et": _format_time_for_cache(windows.get("window_start_et")),
                "window_end_et": _format_time_for_cache(windows.get("window_end_et")),
                "spread_pct_at_check": _number(sample.get("binance_premium_pct") or sample.get("weekend_premium_pct")),
                "news_status": NEWS_STATUS_FAILED,
                "gap_news_explanation": GAP_EXPLANATION_INSUFFICIENT,
                "fetch_status": status,
                "fetch_error": message,
                "source": "weekend_spread_afterhours_news",
            },
        )
    else:
        status_payload = _compute_weekend_spread_news_status(
            clean_symbol,
            sample,
            store,
            sample_key,
            windows,
            store.get_fetch_status(sample_key),
        )
        store.set_check_status(sample_key, status_payload)
    store.prune()
    return {"status": status, "count": count, "message": message, "unavailable": unavailable, "errors": errors}


def normalize_weekend_spread_news_record(symbol: str, raw: dict[str, Any], fetched_at: datetime | None = None) -> dict[str, Any]:
    item = normalize_news_record(symbol, raw, fetched_at=fetched_at)
    published = _parse_datetime(item.get("published_at"))
    if published:
        item["published_at_et"] = published.astimezone(ET).isoformat()
        item["published_at_hkt"] = published.astimezone(HKT).isoformat()
    else:
        item["published_at_et"] = ""
        item["published_at_hkt"] = ""
    item["gap_explanation_label"] = str(raw.get("gap_explanation_label") or "")
    item["cache_namespace"] = "weekend_spread_news"
    return item


def current_shutdown_news_sample(
    symbol: str,
    *,
    premium_pct: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a current after-hours/weekend news sample for realtime spread checks."""

    windows = current_shutdown_news_window(now=now)
    sample: dict[str, Any] = {
        "ticker": _clean_symbol(symbol),
        "news_mode": NEWS_MODE_CURRENT,
        "mode": NEWS_MODE_CURRENT,
        "binance_premium_pct": premium_pct,
    }
    sample.update(windows)
    if windows.get("window_start_et"):
        sample["window_start_et"] = windows["window_start_et"]
    if windows.get("window_end_et"):
        sample["window_end_et"] = windows["window_end_et"]
    if windows.get("window_cache_end_et"):
        sample["window_cache_end_et"] = windows["window_cache_end_et"]
        sample["window_end_bucket"] = _window_end_bucket(windows["window_cache_end_et"])
    sample["window_id"] = weekend_spread_news_window_id(sample)
    return sample


def current_shutdown_news_window(*, now: datetime | None = None) -> dict[str, Any]:
    """Return the current completed-close-to-now window for weekend spread news."""

    now_et = _ensure_et(now or datetime.now(timezone.utc))
    today = now_et.date()
    close_dt = datetime.combine(today, time(16, 0), tzinfo=ET)
    regular_open_dt = datetime.combine(today, time(9, 30), tzinfo=ET)
    if _is_trading_day(today) and regular_open_dt <= now_et < close_dt:
        return {
            "ok": False,
            "mode": NEWS_MODE_CURRENT,
            "window_label": "当前休市窗口",
            "now_et": now_et,
            "reason": "当前不是休市窗口。美股收盘后，本页会检查收盘后至当前时间的新闻。",
            "is_current_shutdown_window": False,
            "window_ended": False,
        }

    if _is_trading_day(today) and now_et >= close_dt:
        last_trading_day = today
    else:
        last_trading_day = _previous_trading_day(today)
    if last_trading_day is None:
        return {
            "ok": False,
            "mode": NEWS_MODE_CURRENT,
            "window_label": "当前休市窗口",
            "now_et": now_et,
            "reason": "缺少最近一个有效美股交易日，暂时无法判断休市新闻窗口。",
            "is_current_shutdown_window": False,
            "window_ended": False,
        }

    start = datetime.combine(last_trading_day, time(16, 0), tzinfo=ET)
    next_session_day = _next_trading_day(last_trading_day)
    next_overnight_open = (
        datetime.combine(next_session_day - timedelta(days=1), time(20, 0), tzinfo=ET)
        if next_session_day is not None
        else None
    )
    window_ended = bool(next_overnight_open and now_et >= next_overnight_open)
    end = next_overnight_open if window_ended and next_overnight_open is not None else now_et
    if end < start:
        return {
            "ok": False,
            "mode": NEWS_MODE_CURRENT,
            "window_label": "当前休市窗口",
            "now_et": now_et,
            "window_start_et": start,
            "window_end_et": end,
            "reason": "当前不是休市窗口。美股收盘后，本页会检查收盘后至当前时间的新闻。",
            "is_current_shutdown_window": False,
            "window_ended": False,
        }
    window_cache_end = next_overnight_open or end
    result = {
        "ok": True,
        "mode": NEWS_MODE_CURRENT,
        "window_start_et": start,
        "window_end_et": end,
        "window_cache_end_et": window_cache_end,
        "window_label": "当前休市窗口",
        "now_et": now_et,
        "last_trading_day": last_trading_day.isoformat(),
        "next_overnight_open_et": next_overnight_open,
        "is_current_shutdown_window": True,
        "window_ended": window_ended,
        "reason": "本轮休市窗口已结束。" if window_ended else "当前休市窗口进行中。",
    }
    result["window_id"] = _build_window_id(NEWS_MODE_CURRENT, start, window_cache_end)
    return result


def weekend_spread_news_windows(sample: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    if str(sample.get("news_mode") or sample.get("mode") or "") == NEWS_MODE_CURRENT:
        if sample.get("window_start_et") and sample.get("window_end_et"):
            start = _parse_datetime(sample.get("window_start_et"))
            end = _parse_datetime(sample.get("window_end_et"))
            if start is None or end is None:
                return {
                    "ok": False,
                    "mode": NEWS_MODE_CURRENT,
                    "window_label": "当前休市窗口",
                    "reason": "缺少当前休市窗口时间，暂时无法判断新闻是否解释价差。",
                    "labels": WINDOW_LABELS,
                }
            start = _ensure_et(start)
            end = _ensure_et(end)
            return {
                "ok": bool(sample.get("ok", True)),
                "mode": NEWS_MODE_CURRENT,
                "window_start_et": start,
                "window_end_et": end,
                "window_cache_end_et": _parse_datetime(sample.get("window_cache_end_et"))
                or _parse_datetime(sample.get("next_overnight_open_et"))
                or end,
                "window_label": "当前休市窗口",
                "now_et": _ensure_et(now or sample.get("now_et") or datetime.now(timezone.utc)),
                "last_trading_day": sample.get("last_trading_day"),
                "next_overnight_open_et": sample.get("next_overnight_open_et"),
                "is_current_shutdown_window": bool(sample.get("is_current_shutdown_window", True)),
                "window_ended": bool(sample.get("window_ended", False)),
                "reason": str(sample.get("reason") or ""),
                "window_id": str(sample.get("window_id") or "")
                or _build_window_id(
                    NEWS_MODE_CURRENT,
                    start,
                    _parse_datetime(sample.get("window_cache_end_et"))
                    or _parse_datetime(sample.get("next_overnight_open_et"))
                    or end,
                ),
                "labels": WINDOW_LABELS,
            }
        return current_shutdown_news_window(now=now)

    raw = sample.get("raw_row") if isinstance(sample.get("raw_row"), dict) else {}
    p0 = _first_datetime(
        sample,
        raw,
        (
            "p0_time",
            "p0_selected_bar_time",
            "friday_afterhours_time",
            "afterhours_reference_time",
            "friday_afterhours_reference_time",
            "last_trading_day_close_time_et",
        ),
    )
    p2 = _first_datetime(
        sample,
        raw,
        (
            "p2_time",
            "p2_first_valid_time",
            "broker_first_time",
            "broker_first_1m_time",
            "overnight_first_1m_time",
            "stock_bar_selected_time",
        ),
    )
    session_start = _first_datetime(
        sample,
        raw,
        (
            "p2_session_start_et",
            "monday_reference_time_et",
            "stock_bar_requested_start",
            "overnight_bar_start_et",
        ),
    )
    current_et = _ensure_et(now or datetime.now(timezone.utc))
    planned_end = session_start or p2
    if planned_end is None:
        end = current_et
    else:
        planned_end = _ensure_et(planned_end)
        end = current_et if current_et < planned_end else planned_end

    last_trading_day = _first_date(
        sample,
        raw,
        (
            "last_trading_day",
            "regular_close_date",
            "friday_close_date",
            "close_date",
        ),
    )
    if last_trading_day is None and p0 is not None:
        last_trading_day = _ensure_et(p0).date()
    if last_trading_day is None:
        return {"ok": False, "reason": "缺少最后交易日时间"}
    start = datetime.combine(last_trading_day, time(16, 0), tzinfo=ET)
    if end < start:
        end = current_et if current_et >= start else start
    return {
        "ok": True,
        "mode": NEWS_MODE_HISTORICAL,
        "window_start_et": start,
        "window_end_et": end,
        "window_label": "休市新闻窗口",
        "pre_anchor_start_et": start,
        "p0_time_et": _ensure_et(p0) if p0 is not None else None,
        "p1_time_et": _first_datetime(
            sample,
            raw,
            (
                "p1_time",
                "binance_max_time",
                "binance_weekend_max_time",
                "weekend_max_time",
                "contract_sample_time",
                "binance_high_time",
            ),
        ),
        "pre_overnight_end_et": end,
        "p2_or_session_time_et": end,
        "labels": WINDOW_LABELS,
    }


def split_news_by_weekend_windows(items: Iterable[dict[str, Any]], windows: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    buckets = _legacy_window_news([])
    if not windows.get("ok"):
        return buckets
    start = windows.get("window_start_et") or windows.get("pre_anchor_start_et")
    end = windows.get("window_end_et") or windows.get("pre_overnight_end_et")
    if start is None or end is None:
        return buckets
    for item in items:
        published = _parse_datetime(item.get("published_at_et") or item.get("published_at"))
        if published is None:
            continue
        published = _ensure_et(published)
        if start <= published <= end:
            buckets[WINDOW_CLOSED_MARKET].append(item)
            buckets[WINDOW_AFTER_P0].append(item)
    for key in buckets:
        buckets[key] = _sort_news_desc(buckets[key])
    return buckets


def build_weekend_spread_news_status(
    symbol: str,
    sample: dict[str, Any],
    *,
    store: "WeekendSpreadNewsStore | None" = None,
) -> dict[str, Any]:
    store = store or WeekendSpreadNewsStore()
    clean_symbol = _clean_symbol(symbol or sample.get("ticker"))
    sample_key = weekend_spread_news_sample_key(clean_symbol, sample)
    windows = weekend_spread_news_windows(sample)
    if not clean_symbol or not windows.get("ok"):
        return _news_status_payload(
            clean_symbol,
            sample_key,
            windows,
            sample,
            NEWS_STATUS_INSUFFICIENT,
            GAP_EXPLANATION_INSUFFICIENT,
            fetch_status="insufficient",
            fetch_error=str(windows.get("reason") or "缺少时间窗口、股票或价差数据"),
        )

    cached = store.get_check_status(sample_key)
    if cached:
        return _decorate_cached_status(cached)

    fetch = store.get_fetch_status(sample_key)
    if not fetch:
        return _news_status_payload(
            clean_symbol,
            sample_key,
            windows,
            sample,
            NEWS_STATUS_UNCHECKED,
            GAP_EXPLANATION_INSUFFICIENT,
            fetch_status="unchecked",
            fetch_error="",
        )

    return _compute_weekend_spread_news_status(clean_symbol, sample, store, sample_key, windows, fetch)


def _compute_weekend_spread_news_status(
    clean_symbol: str,
    sample: dict[str, Any],
    store: "WeekendSpreadNewsStore",
    sample_key: str,
    windows: dict[str, Any],
    fetch: dict[str, Any] | None,
) -> dict[str, Any]:
    if str((fetch or {}).get("status") or "") == "error":
        return _news_status_payload(
            clean_symbol,
            sample_key,
            windows,
            sample,
            NEWS_STATUS_FAILED,
            GAP_EXPLANATION_INSUFFICIENT,
            fetch_status="error",
            fetch_error=str((fetch or {}).get("message") or "新闻接口请求失败"),
        )

    context = build_weekend_spread_news_context(clean_symbol, sample, store=store)
    items = [item for item in context.get("news_items") or [] if isinstance(item, dict)]
    major_items = [item for item in items if _is_major_news(item)]
    opinion_items = [item for item in items if _is_opinion_news(item)]
    positive_items = [item for item in major_items if _is_positive_news(item)]
    negative_items = [item for item in major_items if _is_negative_news(item)]
    premium = _number(sample.get("binance_premium_pct") or sample.get("weekend_premium_pct"))
    if not items:
        news_status = NEWS_STATUS_NO_RELEVANT
        explanation = GAP_EXPLANATION_NONE
    elif opinion_items and not major_items:
        news_status = NEWS_STATUS_OPINION
        explanation = GAP_EXPLANATION_OPINION
    elif not major_items:
        news_status = NEWS_STATUS_NO_MAJOR
        explanation = GAP_EXPLANATION_NONE
    elif premium is None:
        news_status = NEWS_STATUS_EXPLAINED
        explanation = GAP_EXPLANATION_EXPLAINED
    elif (premium >= 0 and positive_items) or (premium < 0 and negative_items):
        news_status = NEWS_STATUS_DIRECTION_MATCH
        explanation = GAP_EXPLANATION_MATCH
    elif (premium >= 0 and negative_items) or (premium < 0 and positive_items):
        news_status = NEWS_STATUS_DIRECTION_MISMATCH
        explanation = GAP_EXPLANATION_MISMATCH
    else:
        news_status = NEWS_STATUS_EXPLAINED
        explanation = GAP_EXPLANATION_EXPLAINED

    return _news_status_payload(
        clean_symbol,
        sample_key,
        windows,
        sample,
        news_status,
        explanation,
        major_news_count=len(major_items),
        opinion_news_count=len(opinion_items),
        latest_news_time=_latest_news_time(items),
        fetch_status=str(fetch.get("status") or "ok"),
        fetch_error=str(fetch.get("message") or ""),
    )


def _decorate_cached_status(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    row = dict(payload)
    checked = _parse_datetime(row.get("last_checked_at"))
    expires = _parse_datetime(row.get("cache_expires_at"))
    if expires is None and checked is not None:
        expires = checked.astimezone(timezone.utc) + timedelta(hours=NEWS_TTL_HOURS)
        row["cache_expires_at"] = expires.isoformat()
    if str(row.get("fetch_status") or "") == "error" or str(row.get("news_status") or "") == NEWS_STATUS_FAILED:
        row["cache_status"] = NEWS_STATUS_FAILED
    elif expires is not None and _ensure_utc(now or datetime.now(timezone.utc)) > expires.astimezone(timezone.utc):
        row["cache_status"] = NEWS_CACHE_EXPIRED
    else:
        row["cache_status"] = NEWS_CACHE_VALID
    return row


def _news_status_payload(
    symbol: str,
    sample_key: str,
    windows: dict[str, Any],
    sample: dict[str, Any],
    news_status: str,
    gap_news_explanation: str,
    *,
    major_news_count: int = 0,
    opinion_news_count: int = 0,
    latest_news_time: str = "",
    fetch_status: str = "",
    fetch_error: str = "",
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "sample_key": sample_key,
        "window_id": weekend_spread_news_window_id(sample),
        "window_start_et": _format_time_for_cache(windows.get("window_start_et")),
        "window_end_et": _format_time_for_cache(windows.get("window_end_et")),
        "spread_pct_at_check": _number(sample.get("binance_premium_pct") or sample.get("weekend_premium_pct")),
        "news_status": news_status,
        "gap_news_explanation": gap_news_explanation,
        "major_news_count": int(major_news_count or 0),
        "opinion_news_count": int(opinion_news_count or 0),
        "latest_news_time": latest_news_time,
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
        "cache_expires_at": (datetime.now(timezone.utc) + timedelta(hours=NEWS_TTL_HOURS)).isoformat(),
        "cache_status": NEWS_CACHE_VALID if fetch_status not in {"", "unchecked", "insufficient", "error"} else NEWS_CACHE_MISSING,
        "fetch_status": fetch_status,
        "fetch_error": fetch_error,
        "source": "weekend_spread_afterhours_news",
    }


def _is_major_news(item: dict[str, Any]) -> bool:
    text = str(item.get("impact_level") or "")
    return text == "重大"


def _is_positive_news(item: dict[str, Any]) -> bool:
    text = str(item.get("sentiment_label") or "")
    return text == "正面"


def _is_negative_news(item: dict[str, Any]) -> bool:
    text = str(item.get("sentiment_label") or "")
    return text == "负面"


def _is_opinion_news(item: dict[str, Any]) -> bool:
    text = str(item.get("event_type") or "")
    return text == "观点文章"


def _latest_news_time(items: list[dict[str, Any]]) -> str:
    times = [str(item.get("published_at_hkt") or item.get("published_at") or "") for item in items if str(item.get("published_at_hkt") or item.get("published_at") or "").strip()]
    return max(times) if times else ""


def _format_time_for_cache(value: Any) -> str:
    parsed = _parse_datetime(value)
    return parsed.isoformat() if parsed is not None else ""


def _legacy_window_news(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        WINDOW_CLOSED_MARKET: list(items),
        WINDOW_PRE_ANCHOR: [],
        WINDOW_AFTER_P0: list(items),
        WINDOW_PRE_OVERNIGHT: [],
    }


def _sort_news_desc(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        list(items),
        key=lambda row: str(row.get("published_at") or row.get("published_at_et") or row.get("fetched_at") or ""),
        reverse=True,
    )


def _next_trading_day(start_day: date) -> date | None:
    for offset in range(1, 12):
        candidate = start_day + timedelta(days=offset)
        if _is_trading_day(candidate):
            return candidate
    return None


def _window_end_bucket(value: datetime) -> str:
    end_et = _ensure_et(value)
    return end_et.replace(minute=0, second=0, microsecond=0).isoformat()


def _window_id_time(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return ""
    return _ensure_et(parsed).strftime("%Y-%m-%d_%H%MET")


def _build_window_id(mode: str, start: Any, end: Any, *, week_id: str = "") -> str:
    start_text = _window_id_time(start)
    end_text = _window_id_time(end)
    prefix = mode or NEWS_MODE_HISTORICAL
    suffix = f"{start_text}_to_{end_text}" if start_text or end_text else "unknown_window"
    if week_id:
        return f"{prefix}|{week_id}|{suffix}"
    return f"{prefix}|{suffix}"


def weekend_spread_news_window_id(sample: dict[str, Any], *, now: datetime | None = None) -> str:
    mode = str(sample.get("news_mode") or sample.get("mode") or NEWS_MODE_HISTORICAL)
    explicit = str(sample.get("window_id") or "").strip()
    if explicit:
        return explicit
    windows = weekend_spread_news_windows(sample, now=now)
    start = windows.get("window_start_et") or sample.get("window_start_et")
    stable_end = (
        windows.get("window_cache_end_et")
        or sample.get("window_cache_end_et")
        or windows.get("next_overnight_open_et")
        or sample.get("next_overnight_open_et")
        or windows.get("window_end_et")
        or sample.get("window_end_et")
    )
    return _build_window_id(mode, start, stable_end, week_id=str(sample.get("week_id") or ""))


def weekend_spread_news_label(symbol: str, sample: dict[str, Any], *, store: "WeekendSpreadNewsStore | None" = None) -> str:
    status = build_weekend_spread_news_status(symbol, sample, store=store)
    if str(status.get("cache_status") or "") == NEWS_CACHE_EXPIRED:
        return NEWS_STATUS_CACHE_EXPIRED
    label = str(status.get("news_status") or "")
    return label if label else NEWS_STATUS_INSUFFICIENT


def weekend_spread_news_sample_key(symbol: str, sample: dict[str, Any]) -> str:
    basis = "|".join([_clean_symbol(symbol or sample.get("ticker")), weekend_spread_news_window_id(sample)])
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def source_link_text(item: dict[str, Any]) -> str:
    url = str(item.get("url") or "").strip()
    return f"[查看原文]({url})" if url else MISSING_URL_TEXT


class WeekendSpreadNewsStore:
    def __init__(self, db_path: Path | str = NEWS_CACHE_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS weekend_spread_news_items (
                    dedupe_key TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    original_title TEXT,
                    title_zh TEXT,
                    original_text TEXT,
                    summary_zh TEXT,
                    source TEXT,
                    site TEXT,
                    published_at TEXT,
                    published_at_et TEXT,
                    published_at_hkt TEXT,
                    url TEXT,
                    image TEXT,
                    event_type TEXT,
                    sentiment_label TEXT,
                    impact_level TEXT,
                    relevance_reason_zh TEXT,
                    gap_explanation_label TEXT,
                    fetched_at TEXT,
                    raw_text TEXT,
                    summary TEXT,
                    keywords_hit TEXT,
                    translated_at TEXT,
                    translation_provider TEXT,
                    translation_status TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS weekend_spread_news_fetch_status (
                    sample_key TEXT PRIMARY KEY,
                    fetched_at TEXT,
                    status TEXT,
                    message TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS weekend_spread_news_checks (
                    sample_key TEXT PRIMARY KEY,
                    symbol TEXT,
                    window_id TEXT,
                    window_start_et TEXT,
                    window_end_et TEXT,
                    spread_pct_at_check REAL,
                    news_status TEXT,
                    gap_news_explanation TEXT,
                    major_news_count INTEGER,
                    opinion_news_count INTEGER,
                    latest_news_time TEXT,
                    last_checked_at TEXT,
                    cache_expires_at TEXT,
                    cache_status TEXT,
                    fetch_status TEXT,
                    fetch_error TEXT,
                    source TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS weekend_spread_news_refresh_batches (
                    batch_id TEXT PRIMARY KEY,
                    started_at TEXT,
                    finished_at TEXT,
                    total_symbols INTEGER,
                    checked_count INTEGER,
                    cached_count INTEGER,
                    no_news_count INTEGER,
                    no_major_news_count INTEGER,
                    explained_count INTEGER,
                    opinion_count INTEGER,
                    failed_count INTEGER,
                    duration_seconds REAL,
                    error_summary TEXT
                )
                """
            )
            self._ensure_column(conn, "weekend_spread_news_checks", "window_id", "TEXT")
            self._ensure_column(conn, "weekend_spread_news_checks", "cache_expires_at", "TEXT")
            self._ensure_column(conn, "weekend_spread_news_checks", "cache_status", "TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_weekend_spread_news_symbol ON weekend_spread_news_items(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_weekend_spread_news_published ON weekend_spread_news_items(published_at)")
            conn.commit()

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def upsert_news(self, item: dict[str, Any]) -> None:
        columns = [
            "dedupe_key",
            "symbol",
            "original_title",
            "title_zh",
            "original_text",
            "summary_zh",
            "source",
            "site",
            "published_at",
            "published_at_et",
            "published_at_hkt",
            "url",
            "image",
            "event_type",
            "sentiment_label",
            "impact_level",
            "relevance_reason_zh",
            "gap_explanation_label",
            "fetched_at",
            "raw_text",
            "summary",
            "keywords_hit",
            "translated_at",
            "translation_provider",
            "translation_status",
        ]
        values = [item.get(column, "") for column in columns]
        update = ", ".join(f"{column}=excluded.{column}" for column in columns if column != "dedupe_key")
        placeholders = ",".join("?" for _ in columns)
        with closing(self._connect()) as conn:
            conn.execute(
                f"""
                INSERT INTO weekend_spread_news_items ({",".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(dedupe_key) DO UPDATE SET {update}
                """,
                values,
            )
            conn.commit()

    def upsert_many(self, items: Iterable[dict[str, Any]]) -> int:
        count = 0
        for item in items:
            self.upsert_news(item)
            count += 1
        return count

    def list_news(
        self,
        symbol: str,
        *,
        start_et: datetime | None = None,
        end_et: datetime | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["symbol=?"]
        params: list[Any] = [_clean_symbol(symbol)]
        if start_et is not None:
            clauses.append("published_at >= ?")
            params.append(_ensure_et(start_et).astimezone(timezone.utc).isoformat())
        if end_et is not None:
            clauses.append("published_at <= ?")
            params.append(_ensure_et(end_et).astimezone(timezone.utc).isoformat())
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM weekend_spread_news_items
                WHERE {" AND ".join(clauses)}
                ORDER BY COALESCE(published_at, fetched_at) DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def set_fetch_status(self, sample_key: str, status: str, message: str = "") -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO weekend_spread_news_fetch_status(sample_key, fetched_at, status, message)
                VALUES(?,?,?,?)
                ON CONFLICT(sample_key) DO UPDATE SET
                    fetched_at=excluded.fetched_at,
                    status=excluded.status,
                    message=excluded.message
                """,
                (sample_key, datetime.now(timezone.utc).isoformat(), status, message),
            )
            conn.commit()

    def get_fetch_status(self, sample_key: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM weekend_spread_news_fetch_status WHERE sample_key=?",
                (sample_key,),
            ).fetchone()
        return dict(row) if row else None

    def set_check_status(self, sample_key: str, payload: dict[str, Any]) -> None:
        columns = [
            "sample_key",
            "symbol",
            "window_id",
            "window_start_et",
            "window_end_et",
            "spread_pct_at_check",
            "news_status",
            "gap_news_explanation",
            "major_news_count",
            "opinion_news_count",
            "latest_news_time",
            "last_checked_at",
            "cache_expires_at",
            "cache_status",
            "fetch_status",
            "fetch_error",
            "source",
        ]
        row = {column: payload.get(column, "") for column in columns}
        row["sample_key"] = sample_key
        row["last_checked_at"] = row.get("last_checked_at") or datetime.now(timezone.utc).isoformat()
        if not row.get("cache_expires_at"):
            checked = _parse_datetime(row["last_checked_at"]) or datetime.now(timezone.utc)
            row["cache_expires_at"] = (checked.astimezone(timezone.utc) + timedelta(hours=NEWS_TTL_HOURS)).isoformat()
        row["cache_status"] = row.get("cache_status") or NEWS_CACHE_VALID
        row["source"] = row.get("source") or "weekend_spread_afterhours_news"
        update = ", ".join(f"{column}=excluded.{column}" for column in columns if column != "sample_key")
        placeholders = ",".join("?" for _ in columns)
        with closing(self._connect()) as conn:
            conn.execute(
                f"""
                INSERT INTO weekend_spread_news_checks({",".join(columns)})
                VALUES({placeholders})
                ON CONFLICT(sample_key) DO UPDATE SET {update}
                """,
                [row.get(column, "") for column in columns],
            )
            conn.commit()

    def get_check_status(self, sample_key: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM weekend_spread_news_checks WHERE sample_key=?",
                (sample_key,),
            ).fetchone()
        return dict(row) if row else None

    def should_refresh(self, sample_key: str, ttl_hours: int = NEWS_TTL_HOURS) -> bool:
        check = self.get_check_status(sample_key)
        if check:
            if str(check.get("fetch_status") or "") == "error" or str(check.get("news_status") or "") == NEWS_STATUS_FAILED:
                return True
            expires = _parse_datetime(check.get("cache_expires_at"))
            if expires is not None:
                return datetime.now(timezone.utc) > expires.astimezone(timezone.utc)
            checked = _parse_datetime(check.get("last_checked_at"))
            if checked is not None:
                return datetime.now(timezone.utc) - checked.astimezone(timezone.utc) > timedelta(hours=ttl_hours)
        row = self.get_fetch_status(sample_key)
        if not row or row.get("status") != "ok":
            return True
        fetched = _parse_datetime(row.get("fetched_at"))
        if fetched is None:
            return True
        return datetime.now(timezone.utc) - fetched.astimezone(timezone.utc) > timedelta(hours=ttl_hours)

    def record_refresh_batch(self, payload: dict[str, Any]) -> None:
        columns = [
            "batch_id",
            "started_at",
            "finished_at",
            "total_symbols",
            "checked_count",
            "cached_count",
            "no_news_count",
            "no_major_news_count",
            "explained_count",
            "opinion_count",
            "failed_count",
            "duration_seconds",
            "error_summary",
        ]
        row = {column: payload.get(column, "") for column in columns}
        row["batch_id"] = row.get("batch_id") or hashlib.sha1(str(datetime.now(timezone.utc).timestamp()).encode()).hexdigest()
        placeholders = ",".join("?" for _ in columns)
        update = ", ".join(f"{column}=excluded.{column}" for column in columns if column != "batch_id")
        with closing(self._connect()) as conn:
            conn.execute(
                f"""
                INSERT INTO weekend_spread_news_refresh_batches({",".join(columns)})
                VALUES({placeholders})
                ON CONFLICT(batch_id) DO UPDATE SET {update}
                """,
                [row.get(column, "") for column in columns],
            )
            conn.commit()

    def list_refresh_batches(self, limit: int = 20) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM weekend_spread_news_refresh_batches
                ORDER BY COALESCE(finished_at, started_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def fill_missing_translations(self, items: Iterable[dict[str, Any]]) -> dict[str, int]:
        title_count = 0
        summary_count = 0
        failed = 0
        for item in items:
            key = str(item.get("dedupe_key") or "").strip()
            if not key:
                failed += 1
                continue
            has_title = bool(str(item.get("title_zh") or "").strip())
            has_summary = bool(str(item.get("summary_zh") or "").strip())
            if has_title and has_summary:
                continue
            normalized = normalize_weekend_spread_news_record(str(item.get("symbol") or ""), item)
            title_zh = str(item.get("title_zh") or normalized.get("title_zh") or "").strip()
            summary_zh = str(item.get("summary_zh") or normalized.get("summary_zh") or "").strip()
            if not title_zh and not summary_zh:
                failed += 1
                continue
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    UPDATE weekend_spread_news_items
                    SET title_zh=?, summary_zh=?, translated_at=?, translation_provider=?, translation_status=?
                    WHERE dedupe_key=?
                    """,
                    (
                        title_zh,
                        summary_zh or "待生成摘要。",
                        datetime.now(timezone.utc).isoformat(),
                        "LOCAL_RULES",
                        "已生成" if title_zh and summary_zh else "待翻译",
                        key,
                    ),
                )
                conn.commit()
            if title_zh and not has_title:
                title_count += 1
            if summary_zh and not has_summary:
                summary_count += 1
        return {"title": title_count, "summary": summary_count, "failed": failed}

    def prune(self, retention_days: int = 30) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        with closing(self._connect()) as conn:
            conn.execute(
                """
                DELETE FROM weekend_spread_news_items
                WHERE impact_level != '重大' AND COALESCE(published_at, fetched_at) < ?
                """,
                (cutoff.isoformat(),),
            )
            conn.commit()


def _clean_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=ET)
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "nan", "nat"}:
        return None
    if len(text) == 16 and text.count("-") == 1:
        text = f"{datetime.now(ET).year}-{text}:00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed


def _ensure_et(value: datetime) -> datetime:
    return value.replace(tzinfo=ET) if value.tzinfo is None else value.astimezone(ET)


def _ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _first_datetime(primary: dict[str, Any], secondary: dict[str, Any], keys: tuple[str, ...]) -> datetime | None:
    for source in (primary, secondary):
        for key in keys:
            parsed = _parse_datetime(source.get(key))
            if parsed is not None:
                return parsed
    return None


def _first_date(primary: dict[str, Any], secondary: dict[str, Any], keys: tuple[str, ...]) -> date | None:
    for source in (primary, secondary):
        for key in keys:
            value = source.get(key)
            if isinstance(value, datetime):
                return _ensure_et(value).date()
            text = str(value or "").strip()
            if not text:
                continue
            parsed = _parse_datetime(text)
            if parsed is not None:
                return _ensure_et(parsed).date()
            try:
                return datetime.fromisoformat(text[:10]).date()
            except ValueError:
                continue
    return None


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
