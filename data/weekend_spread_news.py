from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from data.news_radar import FMPNewsClient, NewsEndpointUnavailable, normalize_news_record
from data.providers import get_secret

ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")
NEWS_CACHE_PATH = Path("data/cache/weekend_spread_news.sqlite")
NEWS_TTL_HOURS = 6

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
        return {
            "symbol": clean_symbol,
            "windows": windows,
            "window_start_et": windows.get("window_start_et"),
            "window_end_et": windows.get("window_end_et"),
            "window_label": "休市新闻窗口",
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
            "explanation_zh": "缺少休市新闻窗口时间，暂时无法判断新闻是否解释价差。",
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
        "window_label": "休市新闻窗口",
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

    fetched_at = datetime.now(timezone.utc)
    raw_items: list[dict[str, Any]] = []
    unavailable: list[str] = []
    errors: list[str] = []
    for label, fetcher in (
        ("Stock News", lambda: client.fetch_stock_news(clean_symbol, limit=limit)),
        ("Press Releases", lambda: client.fetch_press_releases(clean_symbol, limit=limit)),
    ):
        try:
            raw_items.extend(fetcher())
        except NewsEndpointUnavailable:
            unavailable.append(label)
        except Exception as exc:  # pragma: no cover - defensive downgrade
            if "HTTP Error 404" in str(exc):
                unavailable.append(label)
            else:
                errors.append(f"{label}: {exc}")

    items = [normalize_weekend_spread_news_record(clean_symbol, raw, fetched_at=fetched_at) for raw in raw_items]
    count = store.upsert_many(items)
    message_parts = [f"写入 {count} 条休市新闻"]
    if unavailable:
        message_parts.append("套餐不可用：" + "、".join(unavailable))
    if errors:
        message_parts.append("部分失败：" + "；".join(errors[:2]))
    status = "ok" if count or not errors else "error"
    store.set_fetch_status(sample_key, status, "；".join(message_parts))
    store.prune()
    return {"status": status, "count": count, "message": "；".join(message_parts), "unavailable": unavailable, "errors": errors}


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


def weekend_spread_news_windows(sample: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
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


def weekend_spread_news_label(symbol: str, sample: dict[str, Any], *, store: "WeekendSpreadNewsStore | None" = None) -> str:
    context = build_weekend_spread_news_context(symbol, sample, store=store)
    label = str(context.get("gap_explanation_label") or "")
    return label if label else "数据不足"


def weekend_spread_news_sample_key(symbol: str, sample: dict[str, Any]) -> str:
    basis = "|".join(
        [
            _clean_symbol(symbol or sample.get("ticker")),
            str(sample.get("week_id") or ""),
            str(sample.get("p0_selected_bar_time") or sample.get("friday_afterhours_time") or ""),
            str(sample.get("contract_sample_time") or sample.get("binance_max_time") or ""),
        ]
    )
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
        with self._connect() as conn:
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_weekend_spread_news_symbol ON weekend_spread_news_items(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_weekend_spread_news_published ON weekend_spread_news_items(published_at)")
            conn.commit()

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
        with self._connect() as conn:
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
        with self._connect() as conn:
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
        with self._connect() as conn:
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
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM weekend_spread_news_fetch_status WHERE sample_key=?",
                (sample_key,),
            ).fetchone()
        return dict(row) if row else None

    def should_refresh(self, sample_key: str, ttl_hours: int = NEWS_TTL_HOURS) -> bool:
        row = self.get_fetch_status(sample_key)
        if not row or row.get("status") != "ok":
            return True
        fetched = _parse_datetime(row.get("fetched_at"))
        if fetched is None:
            return True
        return datetime.now(timezone.utc) - fetched.astimezone(timezone.utc) > timedelta(hours=ttl_hours)

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
            with self._connect() as conn:
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
        with self._connect() as conn:
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
