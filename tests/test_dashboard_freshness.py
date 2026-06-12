from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from data.dashboard_freshness import (
    build_dashboard_data_freshness,
    dashboard_data_freshness_strip_html,
)
from data.disclosure_store import DisclosureStore
from data.fundamentals import FundamentalCache
from data.macro_regime import MacroIndicatorSnapshot, MacroRegimeSnapshot, VIX
from data.prices import PriceCache


def test_dashboard_freshness_reports_four_cache_layers(tmp_path) -> None:
    path = tmp_path / "cache.sqlite"
    now = datetime(2026, 6, 12, 8, 0, tzinfo=timezone.utc)
    _seed_quote(
        path,
        "NVDA",
        {
            "ticker": "NVDA",
            "current_price": 100.0,
            "quote_updated_at": (now - timedelta(minutes=10)).isoformat(),
            "fundamental_updated_at": (now - timedelta(days=5)).isoformat(),
        },
        fetched_at=(now - timedelta(minutes=10)).isoformat(),
    )
    _seed_history(path, "NVDA", date_text="2026-06-11", fetched_at=(now - timedelta(hours=1)).isoformat())
    macro = MacroRegimeSnapshot(
        regime="风险收缩",
        risk_score=55,
        indicators=[
            MacroIndicatorSnapshot(
                indicator=VIX,
                value=21.2,
                updated_at=(now - timedelta(minutes=3)).isoformat(),
            )
        ],
        reasons=[],
        action_hints=[],
        updated_at=(now - timedelta(minutes=3)).isoformat(),
        confidence="中",
        data_status="部分可用",
    )

    snapshot = build_dashboard_data_freshness(["NVDA"], path=path, macro_regime=macro, now=now)
    items = {item.key: item for item in snapshot.items}

    assert items["price"].status_text == "10分钟前"
    assert items["technical"].status_text == "昨日收盘"
    assert items["fundamental"].status_text == "5天前"
    assert items["macro"].status_text == "部分可用"

    html = dashboard_data_freshness_strip_html(snapshot)
    assert "数据新鲜度" in html
    assert "价格" in html
    assert "技术" in html
    assert "基本面" in html
    assert "宏观" in html


def test_old_fundamental_cache_does_not_make_price_stale(tmp_path) -> None:
    path = tmp_path / "cache.sqlite"
    now = datetime(2026, 6, 12, 8, 0, tzinfo=timezone.utc)
    _seed_quote(
        path,
        "NOW",
        {
            "ticker": "NOW",
            "current_price": 200.0,
            "quote_updated_at": (now - timedelta(minutes=1)).isoformat(),
            "fundamental_updated_at": (now - timedelta(days=60)).isoformat(),
        },
        fetched_at=(now - timedelta(minutes=1)).isoformat(),
    )

    snapshot = build_dashboard_data_freshness(["NOW"], path=path, now=now)
    items = {item.key: item for item in snapshot.items}

    assert items["price"].tone == "fresh"
    assert items["price"].status_text == "刚刚"
    assert items["fundamental"].tone == "stale"
    assert items["fundamental"].status_text == "过期"


def test_old_technical_cache_is_expired_without_affecting_quote(tmp_path) -> None:
    path = tmp_path / "cache.sqlite"
    now = datetime(2026, 6, 12, 8, 0, tzinfo=timezone.utc)
    _seed_quote(
        path,
        "ADBE",
        {
            "ticker": "ADBE",
            "current_price": 300.0,
            "quote_updated_at": (now - timedelta(minutes=2)).isoformat(),
        },
        fetched_at=(now - timedelta(minutes=2)).isoformat(),
    )
    _seed_history(path, "ADBE", date_text="2026-05-20", fetched_at=(now - timedelta(days=20)).isoformat())

    snapshot = build_dashboard_data_freshness(["ADBE"], path=path, now=now)
    items = {item.key: item for item in snapshot.items}

    assert items["price"].tone == "fresh"
    assert items["technical"].tone == "stale"
    assert items["technical"].status_text == "过期"


def test_disclosure_cache_counts_as_fundamental_freshness(tmp_path) -> None:
    path = tmp_path / "cache.sqlite"
    now = datetime(2026, 6, 12, 8, 0, tzinfo=timezone.utc)
    _seed_quote(
        path,
        "CRM",
        {
            "ticker": "CRM",
            "current_price": 250.0,
            "quote_updated_at": (now - timedelta(minutes=6)).isoformat(),
        },
        fetched_at=(now - timedelta(minutes=6)).isoformat(),
    )
    _seed_disclosure(path, "CRM", updated_at=(now - timedelta(days=4)).isoformat())

    snapshot = build_dashboard_data_freshness(["CRM"], path=path, now=now)
    fundamental = snapshot.item("fundamental")

    assert fundamental is not None
    assert fundamental.status_text == "4天前"
    assert "disclosure_cache" in fundamental.source


def _seed_quote(path, ticker: str, payload: dict, *, fetched_at: str) -> None:
    FundamentalCache(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO quote_snapshots (ticker, payload_json, fetched_at)
            VALUES (?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                payload_json = excluded.payload_json,
                fetched_at = excluded.fetched_at
            """,
            (ticker, json.dumps(payload), fetched_at),
        )


def _seed_history(path, ticker: str, *, date_text: str, fetched_at: str) -> None:
    PriceCache(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO price_history (ticker, date, close, fetched_at)
            VALUES (?, ?, ?, ?)
            """,
            (ticker, date_text, 100.0, fetched_at),
        )


def _seed_disclosure(path, ticker: str, *, updated_at: str) -> None:
    DisclosureStore(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO disclosure_metric_values (
                symbol, metricKey, value, sourceType, confidence, reviewStatus, updatedAt
            )
            VALUES (?, 'rpo_growth', 0.12, 'FMP', 'high', 'approved', ?)
            """,
            (ticker, updated_at),
        )
