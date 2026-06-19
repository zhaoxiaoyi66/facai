from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from data.cache_read_model import CacheReadModel
from data.fmp_queue import get_fmp_request_queue
from data.portfolio import PortfolioPositionStore
from data.prices import CACHE_PATH
from settings import load_watchlist
from settings import PROJECT_ROOT


MAJOR_NEGATIVE_KEYWORDS = (
    "miss",
    "cut guidance",
    "cuts guidance",
    "downgrade",
    "price target cut",
    "lawsuit",
    "investigation",
    "sec",
    "ftc",
    "doj",
    "ceo resigns",
    "cfo resigns",
    "short seller",
    "accounting",
    "delay",
    "customer loss",
    "margin pressure",
)
MAJOR_POSITIVE_KEYWORDS = (
    "beat",
    "beats",
    "raise guidance",
    "raises guidance",
    "upgrade",
    "price target raised",
    "price target hike",
    "partnership",
    "large contract",
    "ai demand",
    "data center",
    "record revenue",
    "accelerating growth",
)
LOW_VALUE_KEYWORDS = (
    "stock moves",
    "why shares are trading",
    "market update",
    "recap",
    "mixed trading",
)

EVENT_KEYWORDS = (
    ("财报", ("earnings", "revenue", "profit", "eps", "beat", "miss")),
    ("指引", ("guidance", "outlook", "forecast")),
    ("评级调整", ("downgrade", "upgrade", "rating")),
    ("目标价调整", ("price target", "target price")),
    ("并购", ("acquisition", "merger", "takeover", "buyout")),
    ("合作/订单", ("partnership", "contract", "order", "customer win")),
    ("AI/数据中心", ("ai", "artificial intelligence", "data center", "datacenter", "gpu")),
    ("产品/技术", ("product", "launch", "technology", "chip", "platform")),
    ("监管/诉讼", ("lawsuit", "investigation", "sec", "ftc", "doj", "regulator")),
    ("管理层变动", ("ceo resigns", "cfo resigns", "management", "appoints ceo", "appoints cfo")),
    ("做空报告", ("short seller", "short report")),
    ("宏观/板块", ("sector", "industry", "macro", "fed", "rates", "inflation")),
)

SENTIMENT_OPTIONS = ("正面", "负面", "中性", "待判断")
IMPACT_OPTIONS = ("重大", "中等", "低")
NEWS_KEEP_DAYS = 30
PORTFOLIO_TTL_HOURS = 6
WATCHLIST_TTL_HOURS = 12


class NewsEndpointUnavailable(RuntimeError):
    """Raised when an FMP news endpoint is unavailable for the current plan."""


@dataclass(frozen=True)
class FMPNewsClient:
    api_key: str | None = None

    def __post_init__(self) -> None:
        if self.api_key is None:
            object.__setattr__(self, "api_key", _get_secret("FMP_API_KEY"))

    def fetch_stock_news(self, symbol: str, *, limit: int = 50) -> list[dict[str, Any]]:
        clean = _normalize_symbol(symbol)
        params_list = [
            ("stock-news", {"symbols": clean, "limit": limit}),
            ("news/stock", {"symbols": clean, "limit": limit}),
            ("news/stock", {"symbol": clean, "limit": limit}),
        ]
        errors: list[str] = []
        for endpoint, params in params_list:
            try:
                payload = self._get_json(endpoint, params)
            except NewsEndpointUnavailable:
                raise
            except RuntimeError as exc:
                errors.append(str(exc))
                continue
            rows = _records(payload)
            if rows:
                return rows
        if errors:
            raise RuntimeError("; ".join(errors[:2]))
        return []

    def fetch_general_news(self, *, limit: int = 50) -> list[dict[str, Any]]:
        try:
            return _records(self._get_json("news/general-latest", {"limit": limit}))
        except RuntimeError as exc:
            if _is_permission_error(str(exc)):
                raise NewsEndpointUnavailable("当前套餐不可用") from exc
            raise

    def fetch_press_releases(self, symbol: str, *, limit: int = 20) -> list[dict[str, Any]]:
        try:
            return _records(self._get_json("press-releases", {"symbol": _normalize_symbol(symbol), "limit": limit}))
        except RuntimeError as exc:
            if _is_permission_error(str(exc)):
                raise NewsEndpointUnavailable("当前套餐不可用") from exc
            raise

    def _get_json(self, endpoint: str, params: dict[str, Any], timeout_seconds: int = 15) -> list | dict:
        if not self.api_key:
            raise RuntimeError("缺少 FMP_API_KEY")
        query = urlencode({**params, "apikey": self.api_key})
        request = Request(f"https://financialmodelingprep.com/stable/{endpoint}?{query}", headers={"User-Agent": "ZHX-Research/1.0"})
        try:
            payload = get_fmp_request_queue().submit(lambda: _read_url(request, timeout_seconds), timeout_seconds=timeout_seconds + 8)
        except HTTPError as exc:
            if exc.code in {401, 402, 403}:
                raise NewsEndpointUnavailable("当前套餐不可用") from exc
            raise RuntimeError(f"FMP 新闻请求失败：HTTP {exc.code} {exc.reason}") from exc
        except Exception as exc:
            raise RuntimeError(f"FMP 新闻请求失败：{exc}") from exc
        data = json.loads(payload)
        if isinstance(data, dict) and (data.get("Error Message") or data.get("error")):
            message = str(data.get("Error Message") or data.get("error"))
            if _is_permission_error(message):
                raise NewsEndpointUnavailable("当前套餐不可用")
            raise RuntimeError(message)
        return data


class NewsRadarStore:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_radar_items (
                    dedupe_key TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    title TEXT NOT NULL,
                    published_at TEXT,
                    source TEXT,
                    url TEXT,
                    summary TEXT,
                    raw_text TEXT,
                    fetched_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    sentiment_label TEXT NOT NULL,
                    impact_level TEXT NOT NULL,
                    relevance_score REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_radar_fetch_status (
                    symbol TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, scope)
                )
                """
            )

    def upsert_news(self, symbol: str, rows: Iterable[dict[str, Any]], *, now: datetime | None = None) -> dict[str, int]:
        clean_symbol = _normalize_symbol(symbol)
        fetched_at = _iso(now or datetime.now(timezone.utc))
        inserted = 0
        updated = 0
        with self.connect() as conn:
            for raw in rows:
                item = normalize_news_record(clean_symbol, raw, fetched_at=fetched_at)
                if not item:
                    continue
                existed = conn.execute("SELECT 1 FROM news_radar_items WHERE dedupe_key = ?", (item["dedupe_key"],)).fetchone()
                conn.execute(
                    """
                    INSERT INTO news_radar_items (
                        dedupe_key, symbol, title, published_at, source, url, summary, raw_text,
                        fetched_at, event_type, sentiment_label, impact_level, relevance_score
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dedupe_key) DO UPDATE SET
                        published_at = excluded.published_at,
                        url = excluded.url,
                        summary = excluded.summary,
                        raw_text = excluded.raw_text,
                        fetched_at = excluded.fetched_at,
                        event_type = excluded.event_type,
                        sentiment_label = excluded.sentiment_label,
                        impact_level = excluded.impact_level,
                        relevance_score = excluded.relevance_score
                    """,
                    (
                        item["dedupe_key"],
                        item["symbol"],
                        item["title"],
                        item["published_at"],
                        item["source"],
                        item["url"],
                        item["summary"],
                        item["raw_text"],
                        item["fetched_at"],
                        item["event_type"],
                        item["sentiment_label"],
                        item["impact_level"],
                        item["relevance_score"],
                    ),
                )
                if existed:
                    updated += 1
                else:
                    inserted += 1
            self._purge_old_non_major(conn)
        return {"inserted": inserted, "updated": updated}

    def list_news(
        self,
        *,
        symbols: Iterable[str] | None = None,
        since: datetime | None = None,
        impact_levels: Iterable[str] | None = None,
        sentiment_labels: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        symbols_list = [_normalize_symbol(symbol) for symbol in (symbols or []) if str(symbol or "").strip()]
        if symbols_list:
            where.append(f"symbol IN ({','.join('?' for _ in symbols_list)})")
            params.extend(symbols_list)
        if since is not None:
            where.append("COALESCE(published_at, fetched_at) >= ?")
            params.append(_iso(since))
        impact_list = [str(item) for item in (impact_levels or []) if str(item or "").strip()]
        if impact_list:
            where.append(f"impact_level IN ({','.join('?' for _ in impact_list)})")
            params.extend(impact_list)
        sentiment_list = [str(item) for item in (sentiment_labels or []) if str(item or "").strip()]
        if sentiment_list:
            where.append(f"sentiment_label IN ({','.join('?' for _ in sentiment_list)})")
            params.extend(sentiment_list)
        sql = "SELECT * FROM news_radar_items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(published_at, fetched_at) DESC, fetched_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def mark_fetch_status(
        self,
        symbol: str,
        *,
        scope: str,
        status: str,
        message: str,
        now: datetime | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO news_radar_fetch_status (symbol, scope, status, message, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol, scope) DO UPDATE SET
                    status = excluded.status,
                    message = excluded.message,
                    fetched_at = excluded.fetched_at
                """,
                (_normalize_symbol(symbol), scope, status, message, _iso(now or datetime.now(timezone.utc))),
            )

    def get_fetch_status(self, symbol: str, scope: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT symbol, scope, status, message, fetched_at
                FROM news_radar_fetch_status
                WHERE symbol = ? AND scope = ?
                """,
                (_normalize_symbol(symbol), scope),
            ).fetchone()
        return dict(row) if row else None

    def has_fresh_fetch_status(self, symbol: str, scope: str, *, ttl_hours: float, now: datetime | None = None) -> bool:
        status = self.get_fetch_status(symbol, scope)
        if not status:
            return False
        fetched_at = _parse_dt(status.get("fetched_at"))
        if fetched_at is None:
            return False
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc) - fetched_at.astimezone(timezone.utc) <= timedelta(hours=ttl_hours)

    def _purge_old_non_major(self, conn: sqlite3.Connection) -> None:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=NEWS_KEEP_DAYS))
        conn.execute("DELETE FROM news_radar_items WHERE impact_level != '重大' AND fetched_at < ?", (cutoff,))


def classify_news_item(title: object, summary: object = "", raw_text: object = "") -> dict[str, Any]:
    text = " ".join(str(value or "") for value in (title, summary, raw_text)).lower()
    low_value = any(keyword in text for keyword in LOW_VALUE_KEYWORDS)
    negative = any(keyword in text for keyword in MAJOR_NEGATIVE_KEYWORDS)
    positive = any(keyword in text for keyword in MAJOR_POSITIVE_KEYWORDS)

    event_type = "普通市场新闻"
    for label, keywords in EVENT_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            event_type = label
            break
    if low_value and not positive and not negative:
        event_type = "低价值复述"

    if negative and not positive:
        sentiment = "负面"
    elif positive and not negative:
        sentiment = "正面"
    elif positive and negative:
        sentiment = "待判断"
    elif low_value:
        sentiment = "中性"
    else:
        sentiment = "待判断"

    if positive or negative:
        impact = "重大"
    elif low_value:
        impact = "低"
    elif event_type != "普通市场新闻":
        impact = "中等"
    else:
        impact = "低"

    relevance = 0.9 if impact == "重大" else 0.62 if impact == "中等" else 0.35
    if sentiment == "待判断":
        relevance -= 0.08
    return {
        "event_type": event_type,
        "sentiment_label": sentiment,
        "impact_level": impact,
        "relevance_score": round(max(0.1, relevance), 2),
    }


def normalize_news_record(symbol: str, raw: dict[str, Any], *, fetched_at: str | None = None) -> dict[str, Any] | None:
    title = _clean_text(raw.get("title") or raw.get("headline"))
    if not title:
        return None
    clean_symbol = _normalize_symbol(raw.get("symbol") or raw.get("ticker") or symbol)
    source = _clean_text(raw.get("source") or raw.get("site") or raw.get("publisher") or "FMP")
    published_at = _published_at(raw)
    summary = _clean_text(raw.get("summary") or raw.get("text") or raw.get("content") or raw.get("description"))
    raw_text = _clean_text(raw.get("raw_text") or raw.get("text") or "")
    classification = classify_news_item(title, summary, raw_text)
    dedupe_key = _dedupe_key(clean_symbol, title, source)
    return {
        "symbol": clean_symbol,
        "title": title,
        "published_at": published_at,
        "source": source,
        "url": _clean_text(raw.get("url") or raw.get("link")),
        "summary": summary,
        "raw_text": raw_text,
        "fetched_at": fetched_at or _iso(datetime.now(timezone.utc)),
        "dedupe_key": dedupe_key,
        **classification,
    }


def refresh_symbol_news(
    symbol: str,
    *,
    client: Any | None = None,
    store: NewsRadarStore | None = None,
    scope: str = "watchlist",
    force: bool = False,
    ttl_hours: float | None = None,
    limit: int = 50,
    now: datetime | None = None,
) -> dict[str, Any]:
    clean = _normalize_symbol(symbol)
    store = store or NewsRadarStore()
    current = now or datetime.now(timezone.utc)
    ttl = ttl_hours if ttl_hours is not None else (PORTFOLIO_TTL_HOURS if scope == "portfolio" else WATCHLIST_TTL_HOURS)
    if not force and store.has_fresh_fetch_status(clean, scope, ttl_hours=ttl, now=current):
        return {"symbol": clean, "status": "cache_hit", "requested": False, "message": "使用本地新闻缓存", "inserted": 0, "updated": 0}

    client = client or FMPNewsClient()
    try:
        rows = client.fetch_stock_news(clean, limit=limit)
    except NewsEndpointUnavailable as exc:
        message = str(exc) or "当前套餐不可用"
        store.mark_fetch_status(clean, scope=scope, status="unavailable", message=message, now=current)
        return {"symbol": clean, "status": "unavailable", "requested": True, "message": message, "inserted": 0, "updated": 0}
    except Exception as exc:
        message = _friendly_error(exc)
        store.mark_fetch_status(clean, scope=scope, status="error", message=message, now=current)
        return {"symbol": clean, "status": "error", "requested": True, "message": message, "inserted": 0, "updated": 0}

    press_message = ""
    try:
        rows = [*rows, *client.fetch_press_releases(clean, limit=20)]
    except NewsEndpointUnavailable:
        press_message = "；官方公告当前套餐不可用"
    except Exception:
        press_message = "；官方公告暂时不可用"

    counts = store.upsert_news(clean, rows, now=current)
    message = f"已刷新 {len(rows)} 条新闻{press_message}"
    store.mark_fetch_status(clean, scope=scope, status="ok", message=message, now=current)
    return {"symbol": clean, "status": "ok", "requested": True, "message": message, **counts}


def refresh_symbols_news(
    symbols: Iterable[str],
    *,
    client: Any | None = None,
    store: NewsRadarStore | None = None,
    scope: str = "watchlist",
    force: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    store = store or NewsRadarStore()
    client = client or FMPNewsClient()
    results = [refresh_symbol_news(symbol, client=client, store=store, scope=scope, force=force, limit=limit) for symbol in _unique_symbols(symbols)]
    return {
        "total": len(results),
        "requested": sum(1 for item in results if item.get("requested")),
        "ok": sum(1 for item in results if item.get("status") == "ok"),
        "cache_hit": sum(1 for item in results if item.get("status") == "cache_hit"),
        "unavailable": sum(1 for item in results if item.get("status") == "unavailable"),
        "error": sum(1 for item in results if item.get("status") == "error"),
        "inserted": sum(int(item.get("inserted") or 0) for item in results),
        "updated": sum(int(item.get("updated") or 0) for item in results),
        "results": results,
    }


def refresh_general_market_news(
    *,
    client: Any | None = None,
    store: NewsRadarStore | None = None,
    force: bool = False,
    limit: int = 50,
    now: datetime | None = None,
) -> dict[str, Any]:
    store = store or NewsRadarStore()
    current = now or datetime.now(timezone.utc)
    if not force and store.has_fresh_fetch_status("MARKET", "market", ttl_hours=WATCHLIST_TTL_HOURS, now=current):
        return {"symbol": "MARKET", "status": "cache_hit", "requested": False, "message": "使用本地市场新闻缓存", "inserted": 0, "updated": 0}
    client = client or FMPNewsClient()
    try:
        rows = client.fetch_general_news(limit=limit)
    except NewsEndpointUnavailable as exc:
        message = str(exc) or "当前套餐不可用"
        store.mark_fetch_status("MARKET", scope="market", status="unavailable", message=message, now=current)
        return {"symbol": "MARKET", "status": "unavailable", "requested": True, "message": message, "inserted": 0, "updated": 0}
    except Exception as exc:
        message = _friendly_error(exc)
        store.mark_fetch_status("MARKET", scope="market", status="error", message=message, now=current)
        return {"symbol": "MARKET", "status": "error", "requested": True, "message": message, "inserted": 0, "updated": 0}
    counts = store.upsert_news("MARKET", [dict(row, symbol="MARKET") for row in rows], now=current)
    message = f"已刷新 {len(rows)} 条市场新闻"
    store.mark_fetch_status("MARKET", scope="market", status="ok", message=message, now=current)
    return {"symbol": "MARKET", "status": "ok", "requested": True, "message": message, **counts}


def build_news_price_context(
    symbol: str,
    *,
    lookback_days: int = 7,
    store: NewsRadarStore | None = None,
    history: pd.DataFrame | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    clean = _normalize_symbol(symbol)
    store = store or NewsRadarStore()
    current = now or datetime.now(timezone.utc)
    since = current - timedelta(days=lookback_days)
    news = store.list_news(symbols=[clean], since=since)
    price_context = _price_changes(clean, history=history)
    major = [item for item in news if item.get("impact_level") == "重大"]
    positive = [item for item in major if item.get("sentiment_label") == "正面"]
    negative = [item for item in major if item.get("sentiment_label") == "负面"]
    change_5d = price_context.get("price_change_5d")
    label, explanation = _news_price_match_label(positive, negative, change_5d)
    return {
        "symbol": clean,
        "price_change_1d": price_context.get("price_change_1d"),
        "price_change_5d": change_5d,
        "major_news_count": len(major),
        "positive_news_count": len(positive),
        "negative_news_count": len(negative),
        "news_price_match_label": label,
        "explanation": explanation,
    }


def trade_news_check(symbol: str, *, store: NewsRadarStore | None = None) -> dict[str, Any]:
    clean = _normalize_symbol(symbol)
    store = store or NewsRadarStore()
    now = datetime.now(timezone.utc)
    recent_7 = store.list_news(symbols=[clean], since=now - timedelta(days=7), impact_levels=["重大"])
    recent_30 = store.list_news(symbols=[clean], since=now - timedelta(days=30), impact_levels=["重大"])
    negatives = [item for item in recent_7 if item.get("sentiment_label") == "负面"]
    positives = [item for item in recent_7 if item.get("sentiment_label") == "正面"]
    context = build_news_price_context(clean, store=store, lookback_days=7, now=now)
    if negatives:
        summary = f"过去 7 天存在 {len(negatives)} 条重大负面新闻，建议先复核。"
    else:
        summary = "过去 7 天无重大负面新闻。"
    return {
        "symbol": clean,
        "major_news_7d": len(recent_7),
        "major_news_30d": len(recent_30),
        "positive_major_7d": len(positives),
        "negative_major_7d": len(negatives),
        "has_major_negative_7d": bool(negatives),
        "news_price_match_label": context["news_price_match_label"],
        "summary": summary,
        "headlines": [str(item.get("title") or "") for item in recent_7[:5]],
    }


def portfolio_news_badge(symbol: str, *, store: NewsRadarStore | None = None) -> str:
    clean = _normalize_symbol(symbol)
    store = store or NewsRadarStore()
    recent = store.list_news(symbols=[clean], since=datetime.now(timezone.utc) - timedelta(days=7), impact_levels=["重大"])
    if not recent:
        return "无重大新闻"
    if any(item.get("sentiment_label") == "负面" for item in recent):
        return "重大负面"
    if any(item.get("sentiment_label") == "正面" for item in recent):
        return "正面催化"
    return "待复核"


def available_news_symbols(*, include_portfolio: bool = True, include_watchlist: bool = True) -> dict[str, list[str]]:
    watchlist = load_watchlist() if include_watchlist else []
    positions = []
    if include_portfolio:
        try:
            positions = [str(row.get("symbol") or "").upper() for row in PortfolioPositionStore().list_active_positions()]
        except Exception:
            positions = []
    core = []
    try:
        rows = PortfolioPositionStore().list_active_positions()
        core = [
            str(row.get("symbol") or "").upper()
            for row in rows
            if str(row.get("role") or "").upper() not in {"", "OBSERVATION"}
        ]
    except Exception:
        core = []
    return {
        "watchlist": _unique_symbols(watchlist),
        "portfolio": _unique_symbols(positions),
        "core": _unique_symbols(core),
        "all": _unique_symbols([*watchlist, *positions]),
    }


def news_display_rows(news: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "股票": _display_text(item.get("symbol")),
            "事件类型": _display_text(item.get("event_type")),
            "情绪": _display_text(item.get("sentiment_label")),
            "影响等级": _display_text(item.get("impact_level")),
            "标题": _display_text(item.get("title")),
            "来源": _display_text(item.get("source")),
            "发布时间": _display_time(item.get("published_at")),
            "是否影响交易逻辑": _trade_logic_impact_text(item),
        }
        for item in news
    ]


def price_context_display_rows(contexts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "股票": _display_text(item.get("symbol")),
            "过去 1 日涨跌": _percent_display(item.get("price_change_1d")),
            "过去 5 日涨跌": _percent_display(item.get("price_change_5d")),
            "重大新闻": int(item.get("major_news_count") or 0),
            "新闻方向": _news_direction_text(item),
            "一致性判断": _display_text(item.get("news_price_match_label")),
            "解释": _display_text(item.get("explanation")),
        }
        for item in contexts
    ]


def weekend_news_review(symbols: Iterable[str], *, store: NewsRadarStore | None = None) -> dict[str, Any]:
    store = store or NewsRadarStore()
    clean_symbols = _unique_symbols(symbols)
    now = datetime.now(timezone.utc)
    news = store.list_news(symbols=clean_symbols, since=now - timedelta(days=7))
    major = [item for item in news if item.get("impact_level") == "重大"]
    negative_counts = _count_by_symbol(item for item in major if item.get("sentiment_label") == "负面")
    positive_counts = _count_by_symbol(item for item in major if item.get("sentiment_label") == "正面")
    contexts = [build_news_price_context(symbol, store=store, lookback_days=7) for symbol in clean_symbols]
    unexplained = [item["symbol"] for item in contexts if item.get("news_price_match_label") == "价格波动无明确新闻解释"]
    return {
        "major_news": major,
        "unexplained_price_moves": unexplained,
        "negative_focus": sorted(negative_counts.items(), key=lambda item: item[1], reverse=True),
        "positive_focus": sorted(positive_counts.items(), key=lambda item: item[1], reverse=True),
    }


def _news_price_match_label(positive: list[dict], negative: list[dict], price_change_5d: float | None) -> tuple[str, str]:
    if price_change_5d is None:
        return "数据不足", "缺少足够历史价格，暂时无法判断新闻与价格是否匹配。"
    if positive and price_change_5d >= 0:
        return "新闻与价格一致", "重大正面新闻后股价同步上涨，属于正常兑现。"
    if positive and price_change_5d < 0:
        return "利好未兑现", "有重大正面新闻，但股价没有跟随上涨，可能是预期过高或资金不认可。"
    if negative and price_change_5d < 0:
        return "新闻与价格一致", "重大负面新闻后股价下跌，新闻与价格方向一致。"
    if negative and price_change_5d >= 0:
        return "利空未发酵", "有重大负面新闻，但股价没有下跌，可能是利空出尽或市场暂不买账。"
    if abs(price_change_5d) >= 0.05:
        return "价格波动无明确新闻解释", "过去 5 日价格波动较大，但缓存中没有匹配的重大新闻。"
    return "数据不足", "没有足够重大新闻信号，暂不强行解释。"


def _price_changes(symbol: str, *, history: pd.DataFrame | None = None) -> dict[str, float | None]:
    frame = history if history is not None else CacheReadModel().get_price_history(symbol)
    if frame is None or frame.empty or "close" not in frame.columns:
        return {"price_change_1d": None, "price_change_5d": None}
    closes = pd.to_numeric(frame["close"], errors="coerce").dropna().tolist()
    if len(closes) < 2:
        return {"price_change_1d": None, "price_change_5d": None}
    latest = float(closes[-1])
    prev_1 = float(closes[-2])
    change_1d = latest / prev_1 - 1 if prev_1 else None
    change_5d = None
    if len(closes) >= 6:
        prev_5 = float(closes[-6])
        change_5d = latest / prev_5 - 1 if prev_5 else None
    return {"price_change_1d": change_1d, "price_change_5d": change_5d}


def _records(payload: list | dict) -> list[dict[str, Any]]:
    rows = payload.get("data") or payload.get("news") or payload.get("results") if isinstance(payload, dict) else payload
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return [rows] if isinstance(rows, dict) else []


def _published_at(raw: dict[str, Any]) -> str:
    value = raw.get("publishedDate") or raw.get("published_at") or raw.get("date") or raw.get("datetime") or raw.get("time")
    parsed = _parse_dt(value)
    return _iso(parsed) if parsed else _clean_text(value)


def _parse_dt(value: object) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = pd.to_datetime(text).to_pydatetime()
        except Exception:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _dedupe_key(symbol: str, title: str, source: str) -> str:
    text = f"{_normalize_symbol(symbol)}|{_clean_text(title).lower()}|{_clean_text(source).lower()}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper()


def _unique_symbols(symbols: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        clean = _normalize_symbol(symbol)
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _display_text(value: object, fallback: str = "待复核") -> str:
    text = _clean_text(value)
    return text if text else fallback


def _display_time(value: object) -> str:
    dt = _parse_dt(value)
    if dt is None:
        return _display_text(value, "时间待确认")
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M HKT")


def _percent_display(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "数据不足"
    return f"{number * 100:+.2f}%"


def _trade_logic_impact_text(item: dict[str, Any]) -> str:
    if item.get("impact_level") == "重大":
        return "待复核" if item.get("sentiment_label") == "待判断" else "是"
    return "否"


def _news_direction_text(item: dict[str, Any]) -> str:
    positives = int(item.get("positive_news_count") or 0)
    negatives = int(item.get("negative_news_count") or 0)
    if positives and negatives:
        return "多空都有"
    if positives:
        return "正面"
    if negatives:
        return "负面"
    return "无重大新闻"


def _count_by_symbol(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        symbol = _normalize_symbol(row.get("symbol"))
        if symbol:
            counts[symbol] = counts.get(symbol, 0) + 1
    return counts


def _friendly_error(exc: Exception) -> str:
    text = str(exc).strip()
    if not text:
        return "数据源错误"
    if _is_permission_error(text):
        return "当前套餐不可用"
    if "api_key" in text.lower() or "apikey" in text.lower() or "缺少" in text:
        return "API key 缺失"
    return text[:120]


def _is_permission_error(text: str) -> bool:
    lower = str(text or "").lower()
    return any(token in lower for token in ("permission", "forbidden", "unauthorized", "not available", "upgrade", "paid", "402", "403"))


def _read_url(request: Request, timeout_seconds: int) -> str:
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8")


def _get_secret(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value.strip()
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        if key.strip().lstrip("\ufeff") == name:
            return raw_value.strip().strip('"').strip("'")
    return None
