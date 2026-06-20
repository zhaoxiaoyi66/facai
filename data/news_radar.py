"""News radar cache, classification, and price-context helpers.

The module is intentionally conservative: page render reads cached news only,
while refresh actions explicitly call FMP. Translation is cached and uses a
deterministic local fallback when no LLM provider is wired in.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from data.cache_read_model import CacheReadModel
from data.fmp_queue import get_fmp_request_queue
from data.portfolio import PortfolioPositionStore
from data.prices import CACHE_PATH
from data.watchlist_store import load_watchlist_entries
from settings import load_watchlist

NEWS_CACHE_PATH = CACHE_PATH
NEWS_TTL_HOURS = {"portfolio": 6, "watchlist": 12, "default": 24}
NEWS_RETENTION_DAYS = 30

EVENT_LABELS = [
    "财报",
    "指引",
    "评级调整",
    "目标价调整",
    "并购",
    "合作/订单",
    "AI/数据中心",
    "产品/技术",
    "监管/诉讼",
    "管理层变动",
    "做空报告",
    "宏观/板块",
    "普通市场新闻",
    "低价值复述",
    "观点文章",
]
SENTIMENT_LABELS = ["正面", "负面", "中性", "待判断"]
IMPACT_LABELS = ["重大", "中等", "低"]

GENERIC_RELEVANCE = "需要人工复核影响。"
MISSING_URL_TEXT = "原文链接缺失"

NEGATIVE_MAJOR_KEYWORDS = {
    "miss": "业绩不及预期",
    "misses": "业绩不及预期",
    "cut guidance": "下调指引",
    "cuts guidance": "下调指引",
    "downgrade": "评级下调",
    "downgraded": "评级下调",
    "price target cut": "下调目标价",
    "target cut": "下调目标价",
    "lawsuit": "诉讼",
    "investigation": "调查",
    "sec": "监管调查",
    "ftc": "监管调查",
    "doj": "监管调查",
    "ceo resigns": "管理层离任",
    "cfo resigns": "管理层离任",
    "short seller": "做空报告",
    "accounting": "会计问题",
    "delay": "项目延迟",
    "customer loss": "客户流失",
    "margin pressure": "利润率压力",
}
POSITIVE_MAJOR_KEYWORDS = {
    "beat": "业绩超预期",
    "beats": "业绩超预期",
    "raise guidance": "上调指引",
    "raises guidance": "上调指引",
    "upgrade": "评级上调",
    "upgraded": "评级上调",
    "price target raised": "上调目标价",
    "target raised": "上调目标价",
    "partnership": "合作",
    "large contract": "大额订单",
    "ai demand": "AI需求",
    "data center": "数据中心",
    "record revenue": "收入创新高",
    "accelerating growth": "增长加速",
}
LOW_VALUE_KEYWORDS = {
    "stock moves": "股价波动复述",
    "why shares are trading": "股价波动复述",
    "why shares": "股价波动复述",
    "market update": "市场更新",
    "recap": "复盘摘要",
    "mixed trading": "市场复述",
}
FACTUAL_NEGATIVE_KEYWORDS = {
    "downgrade",
    "cut guidance",
    "lawsuit",
    "investigation",
    "sec",
    "ftc",
    "doj",
    "short seller",
    "accounting",
}
OPINION_SOURCES = {
    "seeking alpha",
    "motley fool",
    "investorplace",
    "benzinga",
    "zacks",
}
OPINION_KEYWORDS = {"opinion", "analysis", "analyst", "is it time", "should you", "why i"}


class NewsEndpointUnavailable(RuntimeError):
    """Raised when an FMP news endpoint is unavailable for the current plan."""


@dataclass(frozen=True)
class NewsClassification:
    event_type: str
    sentiment_label: str
    impact_level: str
    relevance_score: float
    keywords_hit: tuple[str, ...]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now_utc()).astimezone(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _clean_symbol(symbol: str | None) -> str:
    return (symbol or "").strip().upper()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _first_non_empty(raw: dict[str, Any], names: Iterable[str]) -> str:
    for name in names:
        value = _clean_text(raw.get(name))
        if value:
            return value
    return ""


def _normalise_url(raw: dict[str, Any]) -> str:
    return _first_non_empty(raw, ("url", "link", "news_url", "newsUrl", "article_url", "articleUrl"))


def _dedupe_key(symbol: str, title: str, source: str, url: str = "") -> str:
    basis = url.strip().lower() if url else "|".join([symbol.upper(), title.lower(), source.lower()])
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _keyword_hits(text: str, keywords: dict[str, str] | set[str]) -> list[str]:
    lower = text.lower()
    hits: list[str] = []
    iterable = keywords.items() if isinstance(keywords, dict) else ((kw, kw) for kw in keywords)
    for keyword, label in iterable:
        if keyword in lower:
            hits.append(label)
    return hits


def _is_opinion_article(title: str, source: str, body: str = "") -> bool:
    lower_source = source.lower()
    lower_text = f"{title} {body}".lower()
    if any(src in lower_source for src in OPINION_SOURCES):
        return not any(keyword in lower_text for keyword in FACTUAL_NEGATIVE_KEYWORDS)
    return any(keyword in lower_text for keyword in OPINION_KEYWORDS)


def classify_news_item(
    title: str,
    summary: str = "",
    raw_text: str = "",
    *,
    source: str = "",
) -> NewsClassification:
    """Classify one news item with transparent keyword rules."""

    text = " ".join([title or "", summary or "", raw_text or ""])
    lower = text.lower()
    negative_hits = _keyword_hits(lower, NEGATIVE_MAJOR_KEYWORDS)
    positive_hits = _keyword_hits(lower, POSITIVE_MAJOR_KEYWORDS)
    low_hits = _keyword_hits(lower, LOW_VALUE_KEYWORDS)

    if _is_opinion_article(title, source, summary or raw_text):
        event_type = "观点文章"
    elif "earnings" in lower or "revenue" in lower or "profit" in lower:
        event_type = "财报"
    elif "guidance" in lower or "outlook" in lower or "forecast" in lower:
        event_type = "指引"
    elif "downgrade" in lower or "upgrade" in lower or "rating" in lower:
        event_type = "评级调整"
    elif "price target" in lower or "target price" in lower:
        event_type = "目标价调整"
    elif "acquisition" in lower or "merger" in lower or "takeover" in lower:
        event_type = "并购"
    elif "partnership" in lower or "contract" in lower or "order" in lower:
        event_type = "合作/订单"
    elif "ai" in lower or "artificial intelligence" in lower or "data center" in lower:
        event_type = "AI/数据中心"
    elif "product" in lower or "launch" in lower or "technology" in lower:
        event_type = "产品/技术"
    elif any(word in lower for word in ("lawsuit", "investigation", "sec", "ftc", "doj", "regulator")):
        event_type = "监管/诉讼"
    elif "ceo" in lower or "cfo" in lower or "resigns" in lower:
        event_type = "管理层变动"
    elif "short seller" in lower or "short report" in lower:
        event_type = "做空报告"
    elif "market" in lower or "sector" in lower or "fed" in lower or "inflation" in lower:
        event_type = "宏观/板块"
    elif low_hits:
        event_type = "低价值复述"
    else:
        event_type = "普通市场新闻"

    if negative_hits and not positive_hits:
        sentiment = "负面"
    elif positive_hits and not negative_hits:
        sentiment = "正面"
    elif positive_hits and negative_hits:
        sentiment = "待判断"
    elif event_type == "低价值复述":
        sentiment = "中性"
    else:
        sentiment = "待判断"

    if negative_hits or positive_hits:
        impact = "重大"
    elif event_type in {"财报", "指引", "评级调整", "目标价调整", "并购", "监管/诉讼", "做空报告"}:
        impact = "中等"
    elif event_type == "低价值复述":
        impact = "低"
    else:
        impact = "中等" if sentiment == "待判断" else "低"

    score = 0.2
    if impact == "重大":
        score += 0.6
    elif impact == "中等":
        score += 0.35
    if event_type == "观点文章":
        score = min(score, 0.55)
    if event_type == "低价值复述":
        score = min(score, 0.25)

    keywords = tuple(dict.fromkeys([*negative_hits, *positive_hits, *low_hits]))
    return NewsClassification(event_type, sentiment, impact, round(score, 3), keywords)


def _simple_title_zh(title: str, symbol: str = "") -> str:
    """Small deterministic title helper. Empty return means the title is pending."""

    clean = _clean_text(title)
    if not clean:
        return ""
    lower = clean.lower()
    prefix = f"{symbol}：" if symbol else ""
    replacements = [
        ("price target raised", "目标价上调"),
        ("price target cut", "目标价下调"),
        ("downgraded", "评级遭下调"),
        ("downgrade", "评级下调"),
        ("upgraded", "评级获上调"),
        ("upgrade", "评级上调"),
        ("raises guidance", "上调业绩指引"),
        ("raise guidance", "上调业绩指引"),
        ("cuts guidance", "下调业绩指引"),
        ("cut guidance", "下调业绩指引"),
        ("beats", "业绩超预期"),
        ("misses", "业绩不及预期"),
        ("ai threat", "AI威胁"),
        ("data center", "数据中心"),
        ("partnership", "合作"),
        ("lawsuit", "诉讼"),
        ("investigation", "调查"),
    ]
    for needle, zh in replacements:
        if needle in lower:
            return f"{prefix}{zh}：{clean}"[:90]
    return ""


def _summary_from_title(symbol: str, title: str, classification: NewsClassification) -> str:
    clean = _clean_text(title)
    if not clean:
        return "待生成摘要。"
    subject = symbol or "该标的"
    if classification.event_type == "观点文章":
        return f"这是一篇观点文章，讨论{subject}相关预期和估值，需要与事实公告区分。"
    if classification.event_type == "AI/数据中心":
        return f"新闻聚焦{subject}的AI或数据中心相关变化，需观察是否影响需求预期。"
    if classification.event_type == "评级调整":
        return f"新闻涉及{subject}评级变化，可能影响短期市场预期。"
    if classification.event_type == "目标价调整":
        return f"新闻涉及{subject}目标价调整，重点看调整理由是否改变增长假设。"
    if classification.event_type == "监管/诉讼":
        return f"新闻涉及{subject}监管或诉讼事项，需要复核潜在基本面影响。"
    if classification.event_type == "财报":
        return f"新闻涉及{subject}财报或业绩表现，重点看收入、利润和指引变化。"
    if classification.event_type == "低价值复述":
        return f"这条新闻主要复述{subject}股价波动，信息增量较低。"
    return f"新闻讨论{subject}相关事件：{clean[:48]}。"


def _summary_zh(symbol: str, title: str, summary: str, raw_text: str, classification: NewsClassification) -> str:
    source = _clean_text(summary or raw_text)
    if source:
        sentence = re.split(r"(?<=[.!?。！？])\s+", source)[0]
        sentence = sentence[:76].rstrip()
        if sentence:
            if re.search(r"[\u4e00-\u9fff]", sentence):
                return sentence if len(sentence) <= 80 else sentence[:78] + "..."
            return _summary_from_title(symbol, title, classification)
    return _summary_from_title(symbol, title, classification)


def _relevance_reason(symbol: str, title: str, summary: str = "", *, groups: dict[str, set[str]] | None = None) -> str:
    clean_symbol = _clean_symbol(symbol)
    text = f"{title} {summary}".lower()
    groups = groups or {}
    if clean_symbol == "NVDA" and any(word in text for word in ("custom chip", "in-house chip", "google", "tpu", "asic")):
        return "属于客户自研芯片风险，需要区分长期竞争和短期需求。"
    if clean_symbol == "NOW" and any(word in text for word in ("ai", "saas", "automation", "agent")):
        return "属于企业 AI 对 SaaS 护城河的复核项。"
    if clean_symbol in groups.get("core", set()):
        return "这是你的核心仓，需要重点复核是否影响长期假设。"
    if clean_symbol in groups.get("portfolio", set()):
        return "这是你的持仓，可能影响持仓逻辑。"
    if clean_symbol in groups.get("watchlist", set()):
        return "这是观察池标的，可能影响买入等待逻辑。"
    return GENERIC_RELEVANCE


def normalize_news_record(symbol: str, raw: dict[str, Any], fetched_at: datetime | None = None) -> dict[str, Any]:
    clean_symbol = _clean_symbol(symbol or raw.get("symbol") or raw.get("ticker"))
    fetched_at = fetched_at or _now_utc()
    original_title = _first_non_empty(raw, ("original_title", "title", "headline"))
    original_text = _first_non_empty(raw, ("original_text", "raw_text", "text", "content"))
    summary = _first_non_empty(raw, ("summary", "description", "text", "content"))
    source = _first_non_empty(raw, ("source", "publisher", "site")) or "FMP"
    site = _first_non_empty(raw, ("site", "source", "publisher")) or source
    url = _normalise_url(raw)
    image = _first_non_empty(raw, ("image", "image_url", "imageUrl"))
    published = _parse_datetime(_first_non_empty(raw, ("published_at", "publishedDate", "date", "datetime"))) or fetched_at

    classification = classify_news_item(original_title, summary, original_text, source=source)
    title_zh = _first_non_empty(raw, ("title_zh", "titleZh")) or _simple_title_zh(original_title, clean_symbol)
    summary_zh = _first_non_empty(raw, ("summary_zh", "summaryZh")) or _summary_zh(
        clean_symbol, original_title, summary, original_text, classification
    )
    translation_status = _first_non_empty(raw, ("translation_status", "translationStatus"))
    if not translation_status:
        translation_status = "已生成" if title_zh and summary_zh else "待翻译"

    item = {
        "symbol": clean_symbol,
        "title": original_title,
        "original_title": original_title,
        "title_zh": title_zh,
        "published_at": published.astimezone(timezone.utc).isoformat(),
        "source": source,
        "site": site,
        "url": url,
        "image": image,
        "summary": summary,
        "summary_zh": summary_zh,
        "raw_text": original_text,
        "original_text": original_text,
        "fetched_at": fetched_at.astimezone(timezone.utc).isoformat(),
        "event_type": classification.event_type,
        "sentiment_label": classification.sentiment_label,
        "impact_level": classification.impact_level,
        "relevance_score": classification.relevance_score,
        "relevance_reason_zh": _first_non_empty(raw, ("relevance_reason_zh", "relevanceReasonZh"))
        or _relevance_reason(clean_symbol, original_title, summary),
        "price_reaction_summary": _first_non_empty(raw, ("price_reaction_summary", "priceReactionSummary")),
        "keywords_hit": json.dumps(list(classification.keywords_hit), ensure_ascii=False),
        "translated_at": _first_non_empty(raw, ("translated_at", "translatedAt")),
        "translation_provider": _first_non_empty(raw, ("translation_provider", "translationProvider")) or "LOCAL_RULES",
        "translation_status": translation_status,
    }
    item["dedupe_key"] = raw.get("dedupe_key") or _dedupe_key(clean_symbol, original_title, source, url)
    return item


class NewsRadarStore:
    def __init__(self, db_path: Path | str = NEWS_CACHE_PATH):
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
                CREATE TABLE IF NOT EXISTS news_radar_items (
                    dedupe_key TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    title TEXT NOT NULL,
                    original_title TEXT,
                    title_zh TEXT,
                    published_at TEXT,
                    source TEXT,
                    site TEXT,
                    url TEXT,
                    image TEXT,
                    summary TEXT,
                    summary_zh TEXT,
                    raw_text TEXT,
                    original_text TEXT,
                    fetched_at TEXT,
                    event_type TEXT,
                    sentiment_label TEXT,
                    impact_level TEXT,
                    relevance_score REAL,
                    relevance_reason_zh TEXT,
                    price_reaction_summary TEXT,
                    keywords_hit TEXT,
                    translated_at TEXT,
                    translation_provider TEXT,
                    translation_status TEXT
                )
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(news_radar_items)").fetchall()}
            expected = {
                "original_title": "TEXT",
                "title_zh": "TEXT",
                "site": "TEXT",
                "image": "TEXT",
                "summary_zh": "TEXT",
                "original_text": "TEXT",
                "relevance_reason_zh": "TEXT",
                "price_reaction_summary": "TEXT",
                "keywords_hit": "TEXT",
                "translated_at": "TEXT",
                "translation_provider": "TEXT",
                "translation_status": "TEXT",
            }
            for name, column_type in expected.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE news_radar_items ADD COLUMN {name} {column_type}")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_radar_fetch_status (
                    scope_key TEXT PRIMARY KEY,
                    fetched_at TEXT,
                    status TEXT,
                    message TEXT
                )
                """
            )
            self._ensure_fetch_status_schema(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_news_radar_symbol ON news_radar_items(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_news_radar_published ON news_radar_items(published_at)")
            conn.commit()

    def _ensure_fetch_status_schema(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("PRAGMA table_info(news_radar_fetch_status)").fetchall()
        columns = {row["name"] for row in rows}
        if not columns:
            return
        if "scope_key" not in columns:
            key_column = next((name for name in ("scope", "cache_key", "key", "symbol") if name in columns), None)
            key_expr = f"CAST({self._sql_identifier(key_column)} AS TEXT)" if key_column else "('legacy:' || rowid)"
            self._rebuild_fetch_status_table(conn, key_expr=key_expr, columns=columns)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(news_radar_fetch_status)").fetchall()}
        for name in ("fetched_at", "status", "message"):
            if name not in columns:
                conn.execute(f"ALTER TABLE news_radar_fetch_status ADD COLUMN {name} TEXT")
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_news_radar_fetch_status_scope "
                "ON news_radar_fetch_status(scope_key)"
            )
        except sqlite3.IntegrityError:
            self._rebuild_fetch_status_table(conn, key_expr="scope_key", columns=columns)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_news_radar_fetch_status_scope "
                "ON news_radar_fetch_status(scope_key)"
            )

    def _rebuild_fetch_status_table(self, conn: sqlite3.Connection, *, key_expr: str, columns: set[str]) -> None:
        fetched_expr = self._sql_identifier("fetched_at") if "fetched_at" in columns else "''"
        status_expr = self._sql_identifier("status") if "status" in columns else "'unknown'"
        message_expr = self._sql_identifier("message") if "message" in columns else "''"
        conn.execute("DROP TABLE IF EXISTS news_radar_fetch_status_migrated")
        conn.execute(
            """
            CREATE TABLE news_radar_fetch_status_migrated (
                scope_key TEXT PRIMARY KEY,
                fetched_at TEXT,
                status TEXT,
                message TEXT
            )
            """
        )
        conn.execute(
            f"""
            INSERT OR REPLACE INTO news_radar_fetch_status_migrated(scope_key, fetched_at, status, message)
            SELECT COALESCE(NULLIF({key_expr}, ''), 'legacy:' || rowid), {fetched_expr}, {status_expr}, {message_expr}
            FROM news_radar_fetch_status
            ORDER BY rowid
            """
        )
        conn.execute("DROP TABLE news_radar_fetch_status")
        conn.execute("ALTER TABLE news_radar_fetch_status_migrated RENAME TO news_radar_fetch_status")

    @staticmethod
    def _sql_identifier(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def upsert_news(self, item: dict[str, Any]) -> None:
        columns = [
            "dedupe_key",
            "symbol",
            "title",
            "original_title",
            "title_zh",
            "published_at",
            "source",
            "site",
            "url",
            "image",
            "summary",
            "summary_zh",
            "raw_text",
            "original_text",
            "fetched_at",
            "event_type",
            "sentiment_label",
            "impact_level",
            "relevance_score",
            "relevance_reason_zh",
            "price_reaction_summary",
            "keywords_hit",
            "translated_at",
            "translation_provider",
            "translation_status",
        ]
        values = [item.get(column, "") for column in columns]
        update = ", ".join([f"{column}=excluded.{column}" for column in columns if column != "dedupe_key"])
        placeholders = ",".join(["?"] * len(columns))
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO news_radar_items ({",".join(columns)})
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
        symbols: Iterable[str] | None = None,
        since: datetime | None = None,
        impact_levels: Iterable[str] | None = None,
        sentiment_labels: Iterable[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        clean_symbols = [_clean_symbol(s) for s in symbols or [] if _clean_symbol(s)]
        if clean_symbols:
            placeholders = ",".join("?" for _ in clean_symbols)
            clauses.append(f"symbol IN ({placeholders})")
            params.extend(clean_symbols)
        if since:
            clauses.append("published_at >= ?")
            params.append(since.astimezone(timezone.utc).isoformat())
        impacts = [x for x in impact_levels or [] if x and x != "全部"]
        if impacts:
            placeholders = ",".join("?" for _ in impacts)
            clauses.append(f"impact_level IN ({placeholders})")
            params.extend(impacts)
        sentiments = [x for x in sentiment_labels or [] if x and x != "全部"]
        if sentiments:
            placeholders = ",".join("?" for _ in sentiments)
            clauses.append(f"sentiment_label IN ({placeholders})")
            params.extend(sentiments)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM news_radar_items
                {where}
                ORDER BY COALESCE(published_at, fetched_at) DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def set_fetch_status(self, scope_key: str, status: str, message: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO news_radar_fetch_status(scope_key, fetched_at, status, message)
                VALUES(?,?,?,?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    fetched_at=excluded.fetched_at,
                    status=excluded.status,
                    message=excluded.message
                """,
                (scope_key, _iso(), status, message),
            )
            conn.commit()

    def get_fetch_status(self, scope_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM news_radar_fetch_status WHERE scope_key=?",
                (scope_key,),
            ).fetchone()
        return dict(row) if row else None

    def should_refresh(self, scope_key: str, ttl_hours: int) -> bool:
        row = self.get_fetch_status(scope_key)
        if not row or row.get("status") != "ok":
            return True
        fetched = _parse_datetime(row.get("fetched_at"))
        if not fetched:
            return True
        return _now_utc() - fetched > timedelta(hours=ttl_hours)

    def update_translation(
        self,
        dedupe_key: str,
        *,
        title_zh: str,
        summary_zh: str,
        provider: str,
        status: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE news_radar_items
                SET title_zh=?, summary_zh=?, translated_at=?, translation_provider=?, translation_status=?
                WHERE dedupe_key=?
                """,
                (title_zh, summary_zh, _iso(), provider, status, dedupe_key),
            )
            conn.commit()

    def fill_missing_translations(
        self,
        items: Iterable[dict[str, Any]],
        translator: Callable[[dict[str, Any]], tuple[str, str]] | None = None,
    ) -> dict[str, int]:
        title_count = 0
        summary_count = 0
        failed = 0
        seen: set[str] = set()
        for item in items:
            key = str(item.get("dedupe_key") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            has_title = bool(_clean_text(item.get("title_zh")))
            has_summary = bool(_clean_text(item.get("summary_zh")))
            if has_title and has_summary:
                continue
            try:
                if translator:
                    title_zh, summary_zh = translator(item)
                    provider = "LLM"
                else:
                    classification = classify_news_item(
                        str(item.get("original_title") or item.get("title") or ""),
                        str(item.get("summary") or ""),
                        str(item.get("raw_text") or ""),
                        source=str(item.get("source") or ""),
                    )
                    title_zh = _clean_text(item.get("title_zh")) or _simple_title_zh(
                        str(item.get("original_title") or item.get("title") or ""),
                        str(item.get("symbol") or ""),
                    )
                    summary_zh = _clean_text(item.get("summary_zh")) or _summary_zh(
                        str(item.get("symbol") or ""),
                        str(item.get("original_title") or item.get("title") or ""),
                        str(item.get("summary") or ""),
                        str(item.get("raw_text") or ""),
                        classification,
                    )
                    provider = "LOCAL_RULES"
                if not title_zh and not summary_zh:
                    failed += 1
                    continue
                self.update_translation(
                    key,
                    title_zh=title_zh or "",
                    summary_zh=summary_zh or "待生成摘要。",
                    provider=provider,
                    status="已生成" if title_zh and summary_zh else "待翻译",
                )
                if title_zh and not has_title:
                    title_count += 1
                if summary_zh and not has_summary:
                    summary_count += 1
            except Exception:
                failed += 1
        return {"title": title_count, "summary": summary_count, "failed": failed}

    def prune(self, retention_days: int = NEWS_RETENTION_DAYS) -> None:
        cutoff = _now_utc() - timedelta(days=retention_days)
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM news_radar_items
                WHERE impact_level != '重大' AND COALESCE(published_at, fetched_at) < ?
                """,
                (cutoff.isoformat(),),
            )
            conn.commit()


class FMPNewsClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("FMP_API_KEY", "")
        self.base_url = os.getenv("FMP_BASE_URL", "https://financialmodelingprep.com/stable").rstrip("/")

    def _get_json(self, endpoint: str, params: dict[str, Any]) -> Any:
        if not self.api_key:
            raise RuntimeError("缺少 FMP 新闻接口密钥")
        params = {**params, "apikey": self.api_key}
        url = f"{self.base_url}/{endpoint.lstrip('/')}?{urllib.parse.urlencode(params)}"
        queue = get_fmp_request_queue()
        try:
            def _read() -> Any:
                request = urllib.request.Request(url, headers={"User-Agent": "ZHX-Research/1.0"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    payload = response.read().decode("utf-8")
                    return json.loads(payload)

            data = queue.submit(_read, timeout_seconds=30)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 402, 403):
                raise NewsEndpointUnavailable("当前套餐不可用") from exc
            raise
        if isinstance(data, dict):
            message = str(data.get("Error Message") or data.get("error") or data.get("message") or "")
            if any(token in message.lower() for token in ("not available", "upgrade", "plan", "permission")):
                raise NewsEndpointUnavailable("当前套餐不可用")
        return data

    def _try_endpoints(self, candidates: Iterable[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for endpoint, params in candidates:
            try:
                data = self._get_json(endpoint, params)
                if isinstance(data, list):
                    return [row for row in data if isinstance(row, dict)]
                if isinstance(data, dict):
                    for key in ("data", "news", "content"):
                        value = data.get(key)
                        if isinstance(value, list):
                            return [row for row in value if isinstance(row, dict)]
            except NewsEndpointUnavailable:
                raise
            except Exception as exc:
                last_error = exc
                continue
        if last_error:
            raise last_error
        return []

    def fetch_stock_news(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        clean_symbol = _clean_symbol(symbol)
        return self._try_endpoints(
            [
                ("stock-news", {"symbols": clean_symbol, "limit": limit}),
                ("news/stock", {"symbols": clean_symbol, "limit": limit}),
                ("news/stock", {"symbol": clean_symbol, "limit": limit}),
            ]
        )

    def fetch_general_news(self, limit: int = 30) -> list[dict[str, Any]]:
        return self._try_endpoints(
            [
                ("news/general-latest", {"limit": limit}),
                ("general-news", {"limit": limit}),
            ]
        )

    def fetch_press_releases(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        clean_symbol = _clean_symbol(symbol)
        return self._try_endpoints(
            [
                ("press-releases", {"symbol": clean_symbol, "limit": limit}),
                ("press-releases", {"symbols": clean_symbol, "limit": limit}),
            ]
        )


def _symbol_scope_key(symbol: str, scope: str) -> str:
    return f"{scope}:{_clean_symbol(symbol)}"


def refresh_symbol_news(
    symbol: str,
    *,
    scope: str = "default",
    store: NewsRadarStore | None = None,
    client: FMPNewsClient | None = None,
    force: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    store = store or NewsRadarStore()
    client = client or FMPNewsClient()
    clean_symbol = _clean_symbol(symbol)
    scope_key = _symbol_scope_key(clean_symbol, scope)
    ttl = NEWS_TTL_HOURS.get(scope, NEWS_TTL_HOURS["default"])
    if not force and not store.should_refresh(scope_key, ttl):
        return {"symbol": clean_symbol, "status": "cache", "count": 0, "message": "使用缓存"}

    fetched_at = _now_utc()
    try:
        raw_items = client.fetch_stock_news(clean_symbol, limit=limit)
        normalized = [normalize_news_record(clean_symbol, item, fetched_at=fetched_at) for item in raw_items]
        count = store.upsert_many(normalized)
        store.set_fetch_status(scope_key, "ok", f"写入 {count} 条新闻")
        store.prune()
        return {"symbol": clean_symbol, "status": "ok", "count": count, "message": f"写入 {count} 条新闻"}
    except NewsEndpointUnavailable as exc:
        store.set_fetch_status(scope_key, "unavailable", str(exc))
        return {"symbol": clean_symbol, "status": "unavailable", "count": 0, "message": str(exc)}
    except Exception as exc:
        store.set_fetch_status(scope_key, "error", str(exc))
        return {"symbol": clean_symbol, "status": "error", "count": 0, "message": f"数据源错误：{exc}"}


def refresh_symbols_news(
    symbols: Iterable[str],
    *,
    scope: str = "default",
    store: NewsRadarStore | None = None,
    client: FMPNewsClient | None = None,
    force: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    store = store or NewsRadarStore()
    client = client or FMPNewsClient()
    results = []
    for symbol in dict.fromkeys(_clean_symbol(s) for s in symbols if _clean_symbol(s)):
        results.append(refresh_symbol_news(symbol, scope=scope, store=store, client=client, force=force, limit=limit))
    return results


def refresh_general_market_news(
    *,
    store: NewsRadarStore | None = None,
    client: FMPNewsClient | None = None,
    force: bool = False,
    limit: int = 30,
) -> dict[str, Any]:
    store = store or NewsRadarStore()
    client = client or FMPNewsClient()
    scope_key = "general:market"
    if not force and not store.should_refresh(scope_key, NEWS_TTL_HOURS["default"]):
        return {"status": "cache", "count": 0, "message": "使用缓存"}
    fetched_at = _now_utc()
    try:
        raw_items = client.fetch_general_news(limit=limit)
        normalized = [normalize_news_record("MARKET", item, fetched_at=fetched_at) for item in raw_items]
        count = store.upsert_many(normalized)
        store.set_fetch_status(scope_key, "ok", f"写入 {count} 条市场新闻")
        store.prune()
        return {"status": "ok", "count": count, "message": f"写入 {count} 条市场新闻"}
    except NewsEndpointUnavailable as exc:
        store.set_fetch_status(scope_key, "unavailable", str(exc))
        return {"status": "unavailable", "count": 0, "message": str(exc)}
    except Exception as exc:
        store.set_fetch_status(scope_key, "error", str(exc))
        return {"status": "error", "count": 0, "message": f"数据源错误：{exc}"}


def _price_history(symbol: str, *, cache_model: CacheReadModel | None = None) -> pd.DataFrame:
    cache_model = cache_model or CacheReadModel()
    try:
        df = cache_model.get_price_history(_clean_symbol(symbol))
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    frame = df.copy()
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date")
    return frame


def _close_series(df: pd.DataFrame) -> pd.Series:
    for column in ("adjusted_close", "adj_close", "adjClose", "close"):
        if column in df.columns:
            return pd.to_numeric(df[column], errors="coerce")
    return pd.Series(dtype=float)


def build_news_price_context(
    symbol: str,
    lookback_days: int = 7,
    *,
    store: NewsRadarStore | None = None,
    cache_model: CacheReadModel | None = None,
) -> dict[str, Any]:
    store = store or NewsRadarStore()
    clean_symbol = _clean_symbol(symbol)
    since = _now_utc() - timedelta(days=lookback_days)
    news = store.list_news(symbols=[clean_symbol], since=since, limit=100)
    major = [item for item in news if item.get("impact_level") == "重大"]
    positive = [item for item in major if item.get("sentiment_label") == "正面"]
    negative = [item for item in major if item.get("sentiment_label") == "负面"]

    df = _price_history(clean_symbol, cache_model=cache_model)
    closes = _close_series(df).dropna()
    price_change_1d = None
    price_change_5d = None
    if len(closes) >= 2:
        price_change_1d = float(closes.iloc[-1] / closes.iloc[-2] - 1)
    if len(closes) >= 6:
        price_change_5d = float(closes.iloc[-1] / closes.iloc[-6] - 1)

    direction = price_change_5d if price_change_5d is not None else price_change_1d
    if direction is None:
        label = "数据不足"
        explanation = "缺少足够历史价格，暂时无法判断新闻与价格是否匹配。"
    elif positive and direction > 0:
        label = "新闻与价格一致"
        explanation = "新闻偏正面，近期股价上涨，方向一致。"
    elif positive and direction <= 0:
        label = "利好未兑现"
        explanation = "新闻偏正面，但股价没有上涨，可能是预期过高或板块拖累。"
    elif negative and direction < 0:
        label = "新闻与价格一致"
        explanation = "新闻偏负面，近期股价下跌，新闻与价格方向一致。"
    elif negative and direction >= 0:
        label = "利空未发酵"
        explanation = "新闻偏负面，但股价没有下跌，可能是利空出尽或市场不买账。"
    elif not major and abs(direction) >= 0.05:
        label = "价格波动无明确新闻解释"
        explanation = "近期股价波动较大，但缓存中没有对应的重大新闻解释。"
    else:
        label = "数据不足"
        explanation = "近期新闻和价格变化都不突出，暂时没有明确结论。"

    return {
        "symbol": clean_symbol,
        "price_change_1d": price_change_1d,
        "price_change_5d": price_change_5d,
        "major_news_count": len(major),
        "positive_news_count": len(positive),
        "negative_news_count": len(negative),
        "news_price_match_label": label,
        "explanation": explanation,
    }


def _portfolio_symbols() -> set[str]:
    try:
        store = PortfolioPositionStore()
        rows = store.list_active_positions() if hasattr(store, "list_active_positions") else []
    except Exception:
        rows = []
    symbols: set[str] = set()
    for row in rows or []:
        value = getattr(row, "symbol", None)
        if value is None and isinstance(row, dict):
            value = row.get("symbol")
        clean = _clean_symbol(value)
        if clean:
            symbols.add(clean)
    return symbols


def _watchlist_symbols() -> set[str]:
    try:
        rows = load_watchlist()
    except Exception:
        rows = []
    symbols: set[str] = set()
    for row in rows or []:
        value = row
        if isinstance(row, dict):
            value = row.get("symbol") or row.get("ticker")
        clean = _clean_symbol(value)
        if clean:
            symbols.add(clean)
    return symbols


def available_news_symbols() -> dict[str, set[str]]:
    watchlist = _watchlist_symbols()
    portfolio = _portfolio_symbols()
    core: set[str] = set()
    try:
        entries = load_watchlist_entries()
    except Exception:
        entries = []
    for row in entries:
        try:
            symbol = _clean_symbol(row.get("symbol") or row.get("ticker"))
            if row.get("is_core") or row.get("core") or row.get("role") == "core":
                core.add(symbol)
        except Exception:
            continue
    return {
        "watchlist": watchlist,
        "portfolio": portfolio,
        "core": {s for s in core if s},
        "all": watchlist | portfolio,
    }


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "数据不足"
    try:
        return f"{float(value) * 100:+.2f}%"
    except Exception:
        return "数据不足"


def source_link_text(item: dict[str, Any]) -> str:
    url = _clean_text(item.get("url"))
    return f"[查看原文]({url})" if url else MISSING_URL_TEXT


def news_display_rows(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        title_zh = _clean_text(item.get("title_zh"))
        original_title = _clean_text(item.get("original_title") or item.get("title"))
        rows.append(
            {
                "股票": _clean_symbol(item.get("symbol")),
                "中文标题": title_zh or original_title or "待翻译",
                "原文标题": original_title or "原文标题缺失",
                "事件类型": _clean_text(item.get("event_type")) or "待判断",
                "情绪": _clean_text(item.get("sentiment_label")) or "待判断",
                "影响等级": _clean_text(item.get("impact_level")) or "低",
                "来源": _clean_text(item.get("source") or item.get("site")) or "未知来源",
                "发布时间": _clean_text(item.get("published_at")) or "时间缺失",
                "原文链接": source_link_text(item),
            }
        )
    return rows


def price_context_display_rows(contexts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for context in contexts:
        rows.append(
            {
                "股票": _clean_symbol(context.get("symbol")),
                "过去 1 日": _fmt_pct(context.get("price_change_1d")),
                "过去 5 日": _fmt_pct(context.get("price_change_5d")),
                "重大新闻": int(context.get("major_news_count") or 0),
                "正面新闻": int(context.get("positive_news_count") or 0),
                "负面新闻": int(context.get("negative_news_count") or 0),
                "一致性判断": _clean_text(context.get("news_price_match_label")) or "数据不足",
                "解释": _clean_text(context.get("explanation")) or "数据不足",
            }
        )
    return rows


def trade_news_check(symbol: str, lookback_days: int = 7, *, store: NewsRadarStore | None = None) -> dict[str, Any]:
    store = store or NewsRadarStore()
    clean_symbol = _clean_symbol(symbol)
    since = _now_utc() - timedelta(days=lookback_days)
    news = store.list_news(symbols=[clean_symbol], since=since, limit=50)
    news_30d = store.list_news(symbols=[clean_symbol], since=_now_utc() - timedelta(days=30), limit=200)
    major = [item for item in news if item.get("impact_level") == "重大"]
    major_30d = [item for item in news_30d if item.get("impact_level") == "重大"]
    negative = [item for item in major if item.get("sentiment_label") == "负面"]
    positive = [item for item in major if item.get("sentiment_label") == "正面"]
    context = build_news_price_context(clean_symbol, lookback_days=lookback_days, store=store)
    if negative:
        summary = f"过去 {lookback_days} 天存在重大负面新闻，建议先复核。"
    elif major:
        summary = f"过去 {lookback_days} 天有重大新闻，需要确认是否改变原假设。"
    else:
        summary = f"过去 {lookback_days} 天无重大负面新闻。"
    headlines = [
        _clean_text(item.get("title_zh")) or _clean_text(item.get("original_title") or item.get("title"))
        for item in major[:5]
    ]
    return {
        "symbol": clean_symbol,
        "lookback_days": lookback_days,
        "major_news_count": len(major),
        "positive_news_count": len(positive),
        "negative_news_count": len(negative),
        "major_news_7d": len(major),
        "major_news_30d": len(major_30d),
        "negative_major_7d": len(negative),
        "has_major_negative_7d": bool(negative),
        "news_price_match_label": context.get("news_price_match_label"),
        "summary": summary,
        "headlines": [headline for headline in headlines if headline],
    }


def portfolio_news_badge(symbol: str, *, store: NewsRadarStore | None = None) -> str:
    check = trade_news_check(symbol, lookback_days=7, store=store)
    if check["negative_news_count"] > 0:
        return "重大负面"
    if check["positive_news_count"] > 0:
        return "正面催化"
    if check["major_news_count"] > 0:
        return "待复核"
    return "无重大新闻"


def weekend_news_review(
    symbols: Iterable[str],
    *,
    store: NewsRadarStore | None = None,
    lookback_days: int = 7,
) -> dict[str, Any]:
    store = store or NewsRadarStore()
    clean_symbols = [_clean_symbol(symbol) for symbol in symbols if _clean_symbol(symbol)]
    since = _now_utc() - timedelta(days=lookback_days)
    news = store.list_news(symbols=clean_symbols, since=since, limit=500)
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for item in news:
        by_symbol.setdefault(_clean_symbol(item.get("symbol")), []).append(item)
    negative_counts = {
        symbol: sum(1 for item in items if item.get("sentiment_label") == "负面")
        for symbol, items in by_symbol.items()
    }
    positive_counts = {
        symbol: sum(1 for item in items if item.get("sentiment_label") == "正面")
        for symbol, items in by_symbol.items()
    }
    contexts = [build_news_price_context(symbol, lookback_days=lookback_days, store=store) for symbol in clean_symbols]
    unexplained = [
        context["symbol"]
        for context in contexts
        if context.get("news_price_match_label") == "价格波动无明确新闻解释"
    ]
    return {
        "major_news": [item for item in news if item.get("impact_level") == "重大"],
        "negative_concentration": sorted(negative_counts.items(), key=lambda kv: kv[1], reverse=True),
        "positive_concentration": sorted(positive_counts.items(), key=lambda kv: kv[1], reverse=True),
        "unexplained_price_moves": unexplained,
    }


def export_cache_snapshot(path: Path | str, *, store: NewsRadarStore | None = None) -> Path:
    store = store or NewsRadarStore()
    items = store.list_news(limit=10000)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
