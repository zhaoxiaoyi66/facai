from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd

from data.news_radar import (
    FMPNewsClient,
    MISSING_URL_TEXT,
    NewsEndpointUnavailable,
    NewsRadarStore,
    build_news_price_context,
    classify_news_item,
    news_display_rows,
    normalize_news_record,
    refresh_general_market_news,
    refresh_symbol_news,
    source_link_text,
    trade_news_check,
)
from ui.news_radar import _news_detail_rows, _price_reaction_line, _source_line, _title_parts


class FakeNewsClient:
    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def fetch_stock_news(self, symbol: str, limit: int = 20) -> list[dict]:
        self.calls.append((symbol, limit))
        if self.error:
            raise self.error
        return list(self.rows)


class FakeMarketNewsClient:
    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls = 0

    def fetch_general_news(self, limit: int = 30) -> list[dict]:
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.rows)


class FakeCacheModel:
    def __init__(self, history: pd.DataFrame) -> None:
        self.history = history

    def get_price_history(self, symbol: str) -> pd.DataFrame:
        return self.history.copy()


def test_fmp_news_url_fields_are_normalized() -> None:
    item = normalize_news_record(
        "NVDA",
        {
            "title": "Nvidia raises guidance on data center demand",
            "source": "FMP",
            "article_url": "https://example.test/nvda",
            "publishedDate": "2026-06-18T12:00:00+00:00",
        },
    )

    assert item["url"] == "https://example.test/nvda"
    assert source_link_text(item) == "[查看原文](https://example.test/nvda)"


def test_news_card_link_falls_back_when_url_is_missing() -> None:
    item = normalize_news_record("NOW", {"title": "ServiceNow AI threat is overstated", "source": "Seeking Alpha"})

    assert item["url"] == ""
    assert source_link_text(item) == MISSING_URL_TEXT
    assert MISSING_URL_TEXT in _source_line(item)


def test_fmp_news_missing_key_copy_hides_env_key_name() -> None:
    client = FMPNewsClient(api_key="")

    try:
        client._get_json("stock-news", {"symbols": "NVDA"})
    except RuntimeError as exc:
        message = str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("missing FMP key should raise")

    assert message == "缺少 FMP 新闻接口密钥"
    assert "FMP_API_KEY" not in message


def test_title_zh_missing_shows_original_title_and_pending_translation() -> None:
    item = normalize_news_record("IBM", {"title": "IBM announces quarterly dividend", "source": "FMP"})
    item["title_zh"] = ""

    title, original, note = _title_parts(item)

    assert title == "IBM announces quarterly dividend"
    assert original == "IBM announces quarterly dividend"
    assert note == "待翻译"


def test_translation_cache_hit_does_not_call_translator(tmp_path) -> None:
    store = NewsRadarStore(tmp_path / "news.sqlite")
    item = normalize_news_record(
        "NVDA",
        {
            "title": "Nvidia raises guidance on AI demand",
            "title_zh": "英伟达上调 AI 需求指引",
            "summary_zh": "新闻讨论英伟达 AI 需求和业绩指引改善。",
            "source": "FMP",
            "url": "https://example.test/a",
        },
    )
    store.upsert_news(item)
    calls = {"count": 0}

    def translator(row: dict) -> tuple[str, str]:
        calls["count"] += 1
        return "不应调用", "不应调用"

    result = store.fill_missing_translations(store.list_news(symbols=["NVDA"]), translator=translator)

    assert calls["count"] == 0
    assert result == {"title": 0, "summary": 0, "failed": 0}


def test_local_summary_is_specific_not_generic_template() -> None:
    item = normalize_news_record("NVDA", {"title": "Google custom chips challenge Nvidia AI demand", "source": "WSJ"})

    assert item["summary_zh"]
    assert "可能影响交易逻辑，需要复核是否破坏原假设" not in item["summary_zh"]
    assert "NVDA" in item["summary_zh"] or "AI" in item["summary_zh"] or "数据中心" in item["summary_zh"]


def test_seeking_alpha_and_motley_fool_are_classified_as_opinion_articles() -> None:
    seeking_alpha = classify_news_item("ServiceNow: The AI Threat Is Overstated", source="Seeking Alpha")
    fool = classify_news_item("Is Nvidia stock still a buy?", source="The Motley Fool")

    assert seeking_alpha.event_type == "观点文章"
    assert fool.event_type == "观点文章"
    assert seeking_alpha.impact_level != "重大"


def test_news_detail_rows_include_original_title_and_link() -> None:
    item = normalize_news_record(
        "NVDA",
        {
            "title": "Nvidia beats estimates",
            "source": "FMP",
            "url": "https://example.test/nvda",
            "summary": "Nvidia beat estimates on data center demand.",
        },
    )

    details = dict(_news_detail_rows(item, relevance="这是你的持仓，可能影响持仓逻辑。"))

    assert details["原文标题"] == "Nvidia beats estimates"
    assert details["原文链接"] == "[查看原文](https://example.test/nvda)"
    assert details["中文摘要"]


def test_price_reaction_missing_context_uses_specific_copy() -> None:
    assert _price_reaction_line(None) == "价格反应数据不足"

    line = _price_reaction_line({"news_price_match_label": "", "explanation": ""})

    assert "价格反应数据不足" in line
    assert "价格数据不足" in line
    assert line != "数据不足"


def test_news_price_context_identifies_good_news_not_confirmed_by_price(tmp_path) -> None:
    store = NewsRadarStore(tmp_path / "news.sqlite")
    item = normalize_news_record(
        "NVDA",
        {
            "title": "Nvidia raises guidance on data center demand",
            "source": "FMP",
            "publishedDate": datetime.now(timezone.utc).isoformat(),
        },
    )
    store.upsert_news(item)
    history = pd.DataFrame(
        {
            "date": pd.date_range("2026-06-12", periods=6, freq="D"),
            "close": [110, 109, 108, 107, 105, 100],
        }
    )

    context = build_news_price_context("NVDA", lookback_days=7, store=store, cache_model=FakeCacheModel(history))

    assert context["positive_news_count"] == 1
    assert context["negative_news_count"] == 0
    assert context["news_price_match_label"] == "利好未兑现"
    assert context["price_change_5d"] < 0


def test_refresh_uses_cache_without_calling_client(tmp_path) -> None:
    store = NewsRadarStore(tmp_path / "news.sqlite")
    store.set_fetch_status("watchlist:NVDA", "ok", "刚刚刷新")
    client = FakeNewsClient(rows=[{"symbol": "NVDA", "title": "Should not fetch"}])

    result = refresh_symbol_news("NVDA", client=client, store=store, scope="watchlist", force=False)

    assert result["status"] == "cache"
    assert client.calls == []


def test_fetch_status_schema_migrates_legacy_scope_column(tmp_path) -> None:
    db_path = tmp_path / "news.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE news_radar_fetch_status (
                scope TEXT PRIMARY KEY,
                fetched_at TEXT,
                status TEXT,
                message TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO news_radar_fetch_status(scope, fetched_at, status, message) VALUES(?,?,?,?)",
            ("watchlist:NVDA", "2026-06-19T00:00:00+00:00", "ok", "legacy"),
        )
        conn.commit()

    store = NewsRadarStore(db_path)
    migrated = store.get_fetch_status("watchlist:NVDA")

    assert migrated is not None
    assert migrated["status"] == "ok"
    assert migrated["message"] == "legacy"

    store.set_fetch_status("watchlist:NVDA", "error", "updated")
    updated = store.get_fetch_status("watchlist:NVDA")
    assert updated is not None
    assert updated["status"] == "error"
    assert updated["message"] == "updated"


def test_refresh_endpoint_unavailable_degrades_without_raising(tmp_path) -> None:
    store = NewsRadarStore(tmp_path / "news.sqlite")
    client = FakeNewsClient(error=NewsEndpointUnavailable("当前套餐不可用"))

    result = refresh_symbol_news("NOW", client=client, store=store, force=True)

    assert result["status"] == "unavailable"
    assert result["message"] == "当前套餐不可用"
    status = store.get_fetch_status("default:NOW")
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

    assert context["major_news_count"] == 0
    assert context["negative_news_count"] == 0
    assert "无重大负面新闻" in context["summary"]


def test_ui_display_rows_do_not_expose_internal_fields(tmp_path) -> None:
    store = NewsRadarStore(tmp_path / "news.sqlite")
    store.upsert_news(
        normalize_news_record(
            "NVDA",
            {
                "title": "Nvidia beats estimates",
                "source": "FMP",
                "publishedDate": "2026-06-18",
                "text": "raw body",
                "url": "https://example.test/nvda",
            },
        )
    )

    rows = news_display_rows(store.list_news(symbols=["NVDA"]))

    assert rows
    joined_keys = " ".join(rows[0].keys())
    joined_values = " ".join(str(value) for value in rows[0].values())
    for forbidden in ("event_type", "sentiment_label", "impact_level", "None"):
        assert forbidden not in joined_keys
        assert forbidden not in joined_values


def test_regular_news_list_rows_also_have_original_links(tmp_path) -> None:
    store = NewsRadarStore(tmp_path / "news.sqlite")
    store.upsert_news(
        normalize_news_record(
            "IBM",
            {
                "title": "IBM announces a market update",
                "source": "FMP",
                "url": "https://example.test/ibm",
            },
        )
    )

    row = news_display_rows(store.list_news(symbols=["IBM"]))[0]

    assert row["原文链接"] == "[查看原文](https://example.test/ibm)"
