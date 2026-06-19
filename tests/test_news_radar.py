from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from data.news_radar import (
    NewsEndpointUnavailable,
    NewsRadarStore,
    build_news_price_context,
    classify_news_item,
    news_display_rows,
    refresh_general_market_news,
    refresh_symbol_news,
    trade_news_check,
)


class FakeNewsClient:
    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def fetch_stock_news(self, symbol: str, *, limit: int = 50) -> list[dict]:
        self.calls.append((symbol, limit))
        if self.error:
            raise self.error
        return list(self.rows)


class FakeMarketNewsClient:
    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls = 0

    def fetch_general_news(self, *, limit: int = 50) -> list[dict]:
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.rows)


def test_news_dedupes_by_symbol_title_and_source(tmp_path) -> None:
    store = NewsRadarStore(tmp_path / "news.sqlite")
    raw = {
        "symbol": "NVDA",
        "title": "Nvidia raises guidance",
        "site": "FMP",
        "publishedDate": "2026-06-18T12:00:00-04:00",
        "url": "https://example.test/a",
    }

    first = store.upsert_news("NVDA", [raw])
    second = store.upsert_news("NVDA", [dict(raw, url="https://example.test/b")])

    assert first["inserted"] == 1
    assert second["updated"] == 1
    assert len(store.list_news(symbols=["NVDA"])) == 1


def test_news_keywords_classify_positive_negative_and_low_value() -> None:
    positive = classify_news_item("Nvidia beats estimates and raises guidance on AI demand")
    negative = classify_news_item("Oracle cut guidance after margin pressure and customer loss")
    low_value = classify_news_item("Why shares are trading higher in mixed trading market update")

    assert positive["event_type"] == "财报"
    assert positive["sentiment_label"] == "正面"
    assert positive["impact_level"] == "重大"
    assert negative["sentiment_label"] == "负面"
    assert negative["impact_level"] == "重大"
    assert low_value["event_type"] == "低价值复述"
    assert low_value["impact_level"] == "低"


def test_news_price_context_identifies_good_news_not_confirmed_by_price(tmp_path) -> None:
    store = NewsRadarStore(tmp_path / "news.sqlite")
    now = datetime(2026, 6, 19, tzinfo=timezone.utc)
    store.upsert_news(
        "NVDA",
        [
            {
                "symbol": "NVDA",
                "title": "Nvidia raises guidance on data center demand",
                "site": "FMP",
                "publishedDate": "2026-06-18T10:00:00+00:00",
            }
        ],
    )
    history = pd.DataFrame(
        {
            "date": pd.date_range("2026-06-12", periods=6, freq="D"),
            "close": [110, 109, 108, 107, 105, 100],
        }
    )

    context = build_news_price_context("NVDA", lookback_days=7, store=store, history=history, now=now)

    assert context["positive_news_count"] == 1
    assert context["negative_news_count"] == 0
    assert context["news_price_match_label"] == "利好未兑现"
    assert context["price_change_5d"] < 0


def test_refresh_uses_cache_without_calling_client(tmp_path) -> None:
    store = NewsRadarStore(tmp_path / "news.sqlite")
    now = datetime(2026, 6, 19, tzinfo=timezone.utc)
    store.mark_fetch_status("NVDA", scope="watchlist", status="ok", message="", now=now - timedelta(hours=1))
    client = FakeNewsClient(rows=[{"symbol": "NVDA", "title": "Should not fetch"}])

    result = refresh_symbol_news("NVDA", client=client, store=store, scope="watchlist", now=now, ttl_hours=12)

    assert result["status"] == "cache_hit"
    assert result["requested"] is False
    assert client.calls == []


def test_refresh_endpoint_unavailable_degrades_without_raising(tmp_path) -> None:
    store = NewsRadarStore(tmp_path / "news.sqlite")
    client = FakeNewsClient(error=NewsEndpointUnavailable("当前套餐不可用"))

    result = refresh_symbol_news("NOW", client=client, store=store, force=True)

    assert result["status"] == "unavailable"
    assert result["message"] == "当前套餐不可用"
    status = store.get_fetch_status("NOW", "watchlist")
    assert status is not None
    assert status["message"] == "当前套餐不可用"


def test_market_news_endpoint_unavailable_degrades_without_raising(tmp_path) -> None:
    store = NewsRadarStore(tmp_path / "news.sqlite")
    client = FakeMarketNewsClient(error=NewsEndpointUnavailable("当前套餐不可用"))

    result = refresh_general_market_news(client=client, store=store, force=True)

    assert result["status"] == "unavailable"
    assert result["message"] == "当前套餐不可用"
    assert client.calls == 1


def test_trade_news_check_handles_zero_major_news(tmp_path) -> None:
    store = NewsRadarStore(tmp_path / "news.sqlite")
    context = trade_news_check("ORCL", store=store)

    assert context["major_news_7d"] == 0
    assert context["has_major_negative_7d"] is False
    assert "无重大负面新闻" in context["summary"]


def test_ui_display_rows_do_not_expose_internal_fields(tmp_path) -> None:
    store = NewsRadarStore(tmp_path / "news.sqlite")
    store.upsert_news(
        "NVDA",
        [
            {
                "symbol": "NVDA",
                "title": "Nvidia beats estimates",
                "site": "FMP",
                "publishedDate": "2026-06-18",
                "text": "raw body",
            }
        ],
    )

    rows = news_display_rows(store.list_news(symbols=["NVDA"]))

    assert rows
    joined_keys = " ".join(rows[0].keys())
    assert "event_type" not in joined_keys
    assert "sentiment_label" not in joined_keys
    assert "impact_level" not in joined_keys
    assert "None" not in " ".join(str(value) for value in rows[0].values())
