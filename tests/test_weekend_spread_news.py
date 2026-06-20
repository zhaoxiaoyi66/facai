from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from data.weekend_spread_news import (
    NEWS_MODE_CURRENT,
    WeekendSpreadNewsStore,
    build_weekend_spread_news_context,
    build_weekend_spread_news_status,
    current_shutdown_news_sample,
    current_shutdown_news_window,
    normalize_weekend_spread_news_record,
    refresh_weekend_spread_news,
    source_link_text,
    split_news_by_weekend_windows,
    weekend_spread_news_sample_key,
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


def test_weekend_news_window_uses_last_trading_day_close_to_next_overnight() -> None:
    windows = weekend_spread_news_windows(_sample())

    assert windows["window_start_et"] == datetime(2026, 6, 12, 16, 0, tzinfo=ET)
    assert windows["window_end_et"] == datetime(2026, 6, 14, 20, 1, tzinfo=ET)


def test_current_shutdown_window_uses_last_completed_close_to_now() -> None:
    windows = current_shutdown_news_window(now=datetime(2026, 6, 13, 12, 0, tzinfo=ET))

    assert windows["ok"] is True
    assert windows["mode"] == NEWS_MODE_CURRENT
    assert windows["window_start_et"] == datetime(2026, 6, 12, 16, 0, tzinfo=ET)
    assert windows["window_end_et"] == datetime(2026, 6, 13, 12, 0, tzinfo=ET)
    assert windows["is_current_shutdown_window"] is True


def test_current_shutdown_window_uses_thursday_when_friday_is_holiday() -> None:
    windows = current_shutdown_news_window(now=datetime(2026, 6, 20, 12, 0, tzinfo=ET))

    assert windows["ok"] is True
    assert windows["window_start_et"] == datetime(2026, 6, 18, 16, 0, tzinfo=ET)
    assert windows["window_end_et"] == datetime(2026, 6, 20, 12, 0, tzinfo=ET)


def test_current_shutdown_window_is_inactive_during_regular_session() -> None:
    windows = current_shutdown_news_window(now=datetime(2026, 6, 16, 10, 0, tzinfo=ET))

    assert windows["ok"] is False
    assert "当前不是休市窗口" in windows["reason"]


def test_current_shutdown_window_ends_after_next_overnight_open() -> None:
    windows = current_shutdown_news_window(now=datetime(2026, 6, 15, 8, 0, tzinfo=ET))

    assert windows["ok"] is True
    assert windows["window_start_et"] == datetime(2026, 6, 12, 16, 0, tzinfo=ET)
    assert windows["window_end_et"] == datetime(2026, 6, 14, 20, 0, tzinfo=ET)
    assert windows["window_ended"] is True


def test_current_shutdown_sample_does_not_use_historical_week_id() -> None:
    sample = current_shutdown_news_sample("GLW", premium_pct=3.2, now=datetime(2026, 6, 13, 12, 0, tzinfo=ET))
    windows = weekend_spread_news_windows(sample)

    assert sample["news_mode"] == NEWS_MODE_CURRENT
    assert "week_id" not in sample
    assert windows["mode"] == NEWS_MODE_CURRENT
    assert windows["window_start_et"] == datetime(2026, 6, 12, 16, 0, tzinfo=ET)


def test_current_and_historical_news_cache_keys_are_isolated() -> None:
    current = current_shutdown_news_sample("NVDA", premium_pct=3.2, now=datetime(2026, 6, 13, 12, 0, tzinfo=ET))
    historical = _sample()

    assert weekend_spread_news_sample_key("NVDA", current) != weekend_spread_news_sample_key("NVDA", historical)


def test_weekend_news_window_uses_thursday_when_friday_is_closed() -> None:
    sample = {
        **_sample(),
        "last_trading_day": "2026-06-18",
        "friday_afterhours_time": "2026-06-18T19:59:00-04:00",
        "p2_session_start_et": "2026-06-21T20:00:00-04:00",
        "p2_first_valid_time": "",
    }

    windows = weekend_spread_news_windows(sample, now=datetime(2026, 6, 20, 12, 0, tzinfo=ET))

    assert windows["window_start_et"] == datetime(2026, 6, 18, 16, 0, tzinfo=ET)
    assert windows["window_end_et"] == datetime(2026, 6, 20, 12, 0, tzinfo=ET)


def test_weekend_news_window_can_shift_for_monday_holiday() -> None:
    sample = {
        **_sample(),
        "last_trading_day": "2026-05-22",
        "friday_afterhours_time": "2026-05-22T19:59:00-04:00",
        "p2_session_start_et": "2026-05-25T20:00:00-04:00",
        "p2_first_valid_time": "",
    }

    windows = weekend_spread_news_windows(sample, now=datetime(2026, 5, 24, 12, 0, tzinfo=ET))

    assert windows["window_start_et"] == datetime(2026, 5, 22, 16, 0, tzinfo=ET)
    assert windows["window_end_et"] == datetime(2026, 5, 24, 12, 0, tzinfo=ET)


def test_weekend_news_window_includes_all_closed_market_news_without_segments() -> None:
    windows = weekend_spread_news_windows(_sample())
    items = [
        _item("NVDA", "pre anchor", "2026-06-12T18:00:00-04:00"),
        _item("NVDA", "after p0", "2026-06-13T10:00:00-04:00"),
        _item("NVDA", "pre overnight", "2026-06-14T15:00:00-04:00"),
        _item("NVDA", "outside", "2026-06-15T10:00:00-04:00"),
    ]

    buckets = split_news_by_weekend_windows(items, windows)

    all_titles = {row["original_title"] for rows in buckets.values() for row in rows}
    assert {"pre anchor", "after p0", "pre overnight"}.issubset(all_titles)
    assert "outside" not in all_titles


def test_no_after_p0_news_is_no_news_explanation(tmp_path) -> None:
    store = _store(tmp_path)

    context = build_weekend_spread_news_context("NVDA", _sample(), store=store)

    assert context["gap_explanation_label"] == "无新闻解释"
    assert context["news_count"] == 0


def test_positive_news_and_premium_is_direction_consistent(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_news(_item("NVDA", "Nvidia raises guidance on AI demand", "2026-06-13T10:00:00-04:00"))

    context = build_weekend_spread_news_context("NVDA", _sample(premium=2.5), store=store)

    assert context["gap_explanation_label"] == "新闻方向一致"
    assert context["major_news_count"] == 1
    assert context["positive_news_count"] == 1


def test_negative_news_and_discount_is_direction_consistent(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_news(_item("NVDA", "Nvidia cuts guidance after margin pressure", "2026-06-13T10:00:00-04:00"))

    context = build_weekend_spread_news_context("NVDA", _sample(premium=-2.5), store=store)

    assert context["gap_explanation_label"] == "新闻方向一致"
    assert context["negative_news_count"] == 1


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


def test_news_store_closes_sqlite_connections(tmp_path) -> None:
    db_path = tmp_path / "weekend_spread_news.sqlite"
    store = WeekendSpreadNewsStore(db_path)
    sample_key = "sample"

    store.upsert_news(_item("NVDA", "Nvidia raises guidance", "2026-06-13T10:00:00-04:00"))
    assert store.list_news("NVDA")
    store.set_fetch_status(sample_key, "ok", "done")
    assert store.get_fetch_status(sample_key)["status"] == "ok"
    assert store.should_refresh(sample_key, ttl_hours=6) is False
    store.prune()

    db_path.unlink()
    assert not db_path.exists()


def test_weekend_news_status_is_unchecked_before_refresh(tmp_path) -> None:
    store = _store(tmp_path)

    status = build_weekend_spread_news_status("NVDA", _sample(), store=store)

    assert status["news_status"] == "未检查"
    assert status["gap_news_explanation"] == "数据不足"


def test_refresh_success_with_no_news_is_no_relevant_news(tmp_path) -> None:
    class EmptyClient:
        def fetch_stock_news(self, symbol: str, limit: int = 30):
            return []

        def fetch_press_releases(self, symbol: str, limit: int = 30):
            return []

    store = _store(tmp_path)

    result = refresh_weekend_spread_news("NVDA", _sample(), store=store, client=EmptyClient(), force=True)
    status = build_weekend_spread_news_status("NVDA", _sample(), store=store)

    assert result["status"] == "ok"
    assert status["news_status"] == "无相关新闻"
    assert status["gap_news_explanation"] == "无新闻解释"


def test_refresh_failure_is_not_treated_as_no_news(tmp_path) -> None:
    class FailingClient:
        def fetch_stock_news(self, symbol: str, limit: int = 30):
            raise RuntimeError("network timeout")

        def fetch_press_releases(self, symbol: str, limit: int = 30):
            raise RuntimeError("permission denied")

    store = _store(tmp_path)

    result = refresh_weekend_spread_news("NVDA", _sample(), store=store, client=FailingClient(), force=True)
    status = build_weekend_spread_news_status("NVDA", _sample(), store=store)

    assert result["status"] == "error"
    assert status["news_status"] == "接口失败"
    assert status["gap_news_explanation"] == "数据不足"
    assert "network timeout" in status["fetch_error"]


def test_weekend_news_status_positive_news_matches_premium(tmp_path) -> None:
    class PositiveClient:
        def fetch_stock_news(self, symbol: str, limit: int = 30):
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
            return []

    store = _store(tmp_path)

    refresh_weekend_spread_news("NVDA", _sample(premium=2.5), store=store, client=PositiveClient(), force=True)
    status = build_weekend_spread_news_status("NVDA", _sample(premium=2.5), store=store)

    assert status["news_status"] == "新闻方向一致"
    assert status["gap_news_explanation"] == "新闻方向一致"
    assert status["major_news_count"] == 1


def test_weekend_news_status_opinion_article_keeps_separate_label(tmp_path) -> None:
    class OpinionClient:
        def fetch_stock_news(self, symbol: str, limit: int = 30):
            return [
                {
                    "symbol": symbol,
                    "title": "ServiceNow: The AI Threat Is Overstated",
                    "publishedDate": "2026-06-13T10:00:00-04:00",
                    "source": "Seeking Alpha",
                    "url": "https://example.com/now",
                }
            ]

        def fetch_press_releases(self, symbol: str, limit: int = 30):
            return []

    store = _store(tmp_path)
    sample = {**_sample(premium=2.5), "ticker": "NOW"}

    refresh_weekend_spread_news("NOW", sample, store=store, client=OpinionClient(), force=True)
    status = build_weekend_spread_news_status("NOW", sample, store=store)

    assert status["news_status"] == "观点文章"
    assert status["gap_news_explanation"] == "观点文章，不足以解释价差"
    assert status["opinion_news_count"] == 1
