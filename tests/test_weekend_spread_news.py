from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from data.weekend_spread_news import (
    WINDOW_AFTER_P0,
    WINDOW_PRE_ANCHOR,
    WINDOW_PRE_OVERNIGHT,
    WeekendSpreadNewsStore,
    build_weekend_spread_news_context,
    normalize_weekend_spread_news_record,
    refresh_weekend_spread_news,
    source_link_text,
    split_news_by_weekend_windows,
    weekend_spread_news_windows,
)


ET = ZoneInfo("America/New_York")


def _sample(premium: float = 3.2) -> dict:
    return {
        "ticker": "NVDA",
        "week_id": "2026-W24",
        "friday_afterhours_time": "2026-06-12T19:59:00-04:00",
        "contract_sample_time": "2026-06-14T12:00:00-04:00",
        "p2_first_valid_time": "2026-06-14T20:01:00-04:00",
        "friday_afterhours_close": 205.42,
        "binance_price": 209.90,
        "broker_open_close": 209.51,
        "binance_premium_pct": premium,
    }


def _store(tmp_path) -> WeekendSpreadNewsStore:
    return WeekendSpreadNewsStore(tmp_path / "weekend_spread_news.sqlite")


def _item(symbol: str, title: str, published: str, *, source: str = "FMP", url: str = "https://example.com/news") -> dict:
    return normalize_weekend_spread_news_record(
        symbol,
        {
            "title": title,
            "publishedDate": published,
            "source": source,
            "url": url,
            "text": f"{title} body",
        },
        fetched_at=datetime(2026, 6, 15, tzinfo=ET),
    )


def test_weekend_news_windows_split_three_segments() -> None:
    windows = weekend_spread_news_windows(_sample())
    items = [
        _item("NVDA", "pre anchor", "2026-06-12T18:00:00-04:00"),
        _item("NVDA", "after p0", "2026-06-13T10:00:00-04:00"),
        _item("NVDA", "pre overnight", "2026-06-14T15:00:00-04:00"),
    ]

    buckets = split_news_by_weekend_windows(items, windows)

    assert [row["original_title"] for row in buckets[WINDOW_PRE_ANCHOR]] == ["pre anchor"]
    assert [row["original_title"] for row in buckets[WINDOW_AFTER_P0]] == ["after p0"]
    assert [row["original_title"] for row in buckets[WINDOW_PRE_OVERNIGHT]] == ["pre overnight"]


def test_no_after_p0_news_is_no_news_explanation(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_news(_item("NVDA", "premarket recap", "2026-06-12T18:00:00-04:00"))

    context = build_weekend_spread_news_context("NVDA", _sample(), store=store)

    assert context["gap_explanation_label"] == "无新闻解释"
    assert context["news_count_after_p0"] == 0


def test_positive_news_and_premium_is_direction_consistent(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_news(_item("NVDA", "Nvidia raises guidance on AI demand", "2026-06-13T10:00:00-04:00"))

    context = build_weekend_spread_news_context("NVDA", _sample(premium=2.5), store=store)

    assert context["gap_explanation_label"] == "新闻方向一致"
    assert context["major_news_after_p0"] == 1
    assert context["positive_news_after_p0"] == 1


def test_negative_news_and_discount_is_direction_consistent(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_news(_item("NVDA", "Nvidia cuts guidance after margin pressure", "2026-06-13T10:00:00-04:00"))

    context = build_weekend_spread_news_context("NVDA", _sample(premium=-2.5), store=store)

    assert context["gap_explanation_label"] == "新闻方向一致"
    assert context["negative_news_after_p0"] == 1


def test_opinion_article_is_not_treated_as_basic_news_explanation(tmp_path) -> None:
    store = _store(tmp_path)
    item = _item(
        "NOW",
        "ServiceNow: The AI Threat Is Overstated",
        "2026-06-13T10:00:00-04:00",
        source="Seeking Alpha",
    )
    store.upsert_news(item)

    context = build_weekend_spread_news_context("NOW", {**_sample(), "ticker": "NOW"}, store=store)

    assert item["event_type"] == "观点文章"
    assert context["gap_explanation_label"] == "观点文章，不足以解释价差"


def test_news_url_link_and_missing_url_text() -> None:
    linked = _item("NVDA", "Nvidia partnership", "2026-06-13T10:00:00-04:00", url="https://example.com/a")
    missing = _item("NVDA", "Nvidia partnership", "2026-06-13T10:00:00-04:00", url="")

    assert source_link_text(linked) == "[查看原文](https://example.com/a)"
    assert source_link_text(missing) == "原文链接缺失"


def test_refresh_writes_weekend_spread_cache_only(tmp_path) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_stock_news(self, symbol: str, limit: int = 30):
            self.calls += 1
            return [
                {
                    "symbol": symbol,
                    "title": "Nvidia raises guidance on AI demand",
                    "publishedDate": "2026-06-13T10:00:00-04:00",
                    "source": "FMP",
                    "url": "https://example.com/nvda",
                }
            ]

        def fetch_press_releases(self, symbol: str, limit: int = 30):
            self.calls += 1
            raise Exception("HTTP Error 404: Not Found")

    store = _store(tmp_path)
    client = FakeClient()

    result = refresh_weekend_spread_news("NVDA", _sample(), store=store, client=client, force=True)
    cached = store.list_news("NVDA")

    assert result["status"] == "ok"
    assert client.calls == 2
    assert result["unavailable"] == ["Press Releases"]
    assert len(cached) == 1
    assert cached[0]["url"] == "https://example.com/nvda"
