from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data.price_alerts import PriceAlertStore, evaluate_price_alerts


NOW = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


def _db(tmpdir: str) -> Path:
    return Path(tmpdir) / "price_alerts.sqlite"


def _insert_quote(db_path: Path, symbol: str, price: float, fetched_at: str = "2026-05-30T11:00:00+00:00") -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quote_snapshots (
                ticker TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO quote_snapshots VALUES (?, ?, ?)",
            (symbol.upper(), json.dumps({"current_price": price}), fetched_at),
        )
        conn.commit()


def _insert_history(db_path: Path, ticker: str, close: float, fetched_at: str = "2026-05-30T10:00:00+00:00") -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (ticker, date)
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO price_history (ticker, date, open, high, low, close, volume, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker.upper(), "2026-05-30", close, close, close, close, 1000, fetched_at),
        )
        conn.commit()


def test_below_price_alert_triggers_once() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 195)
        store = PriceAlertStore(path)
        store.create_alert("NVDA", triggerDirection="below", triggerPrice=200, alertReason="第一笔买入价")

        first = evaluate_price_alerts(path, now=NOW)
        second = evaluate_price_alerts(path, now=NOW)

        assert first[0]["status"] == "triggered"
        assert first[0]["triggeredNow"] is True
        assert "buyZone" in first[0]["message"]
        assert "technicalEntry" in first[0]["message"]
        assert "combinedEntry" in first[0]["message"]
        assert "数据健康" in first[0]["message"]
        assert "交易纪律" in first[0]["message"]
        assert "价格到达不代表自动可以买" in first[0]["message"]
        assert second[0]["status"] == "triggered"
        assert second[0]["triggeredNow"] is False
        assert second[0]["triggeredAt"] == first[0]["triggeredAt"]
        assert first[0]["priceSource"] == "quote_snapshot"


def test_above_price_alert_triggers() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 240)
        PriceAlertStore(path).create_alert("NVDA", triggerDirection="above", triggerPrice=230, linkedPlanType="risk_review")

        alerts = evaluate_price_alerts(path, now=NOW)

        assert alerts[0]["status"] == "triggered"
        assert alerts[0]["triggerDirection"] == "above"


def test_price_alert_falls_back_to_market_context_latest_close() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_history(path, "FMP:NVDA", 195)
        PriceAlertStore(path).create_alert("NVDA", triggerDirection="below", triggerPrice=200)

        alerts = evaluate_price_alerts(path, now=NOW)

        assert alerts[0]["status"] == "triggered"
        assert alerts[0]["currentPrice"] == 195
        assert alerts[0]["priceSource"] == "price_history"
        assert alerts[0]["historyTickerKey"] == "FMP:NVDA"
        assert "current price" in alerts[0]["marketWarning"]


def test_price_alert_does_not_trigger_before_price_crosses() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 205)
        PriceAlertStore(path).create_alert("NVDA", triggerDirection="below", triggerPrice=200)

        alerts = evaluate_price_alerts(path, now=NOW)

        assert alerts[0]["status"] == "active"
        assert alerts[0]["triggeredAt"] is None


def test_stale_price_alert_is_marked_stale_when_triggered() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 195, "2026-05-28T11:00:00+00:00")
        PriceAlertStore(path).create_alert("NVDA", triggerDirection="below", triggerPrice=200)

        alerts = evaluate_price_alerts(path, now=NOW, quote_max_age_hours=24)

        assert alerts[0]["status"] == "triggered"
        assert alerts[0]["priceDataStale"] is True
        assert "价格数据可能过期" in alerts[0]["message"]


def test_archived_and_disabled_alerts_do_not_trigger() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 195)
        store = PriceAlertStore(path)
        archived = store.create_alert("NVDA", triggerDirection="below", triggerPrice=200)
        disabled = store.create_alert("MSFT", triggerDirection="below", triggerPrice=500, isActive=False)
        store.archive_alert(int(archived["id"]))
        _insert_quote(path, "MSFT", 450)

        alerts = evaluate_price_alerts(path, now=NOW)

        assert {alert["status"] for alert in alerts} == {"archived", "disabled"}
        assert all(alert["triggeredAt"] is None for alert in alerts)


def test_price_alert_can_be_edited_paused_enabled_archived_and_soft_deleted() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 195)
        store = PriceAlertStore(path)
        alert = store.create_alert("NVDA", triggerDirection="below", triggerPrice=190, alertReason="manual")

        edited = store.update_alert(int(alert["id"]), triggerPrice=200, alertReason="first buy")
        disabled = store.set_active(int(alert["id"]), False)
        paused = evaluate_price_alerts(path, now=NOW)
        enabled = store.set_active(int(alert["id"]), True)
        archived = store.archive_alert(int(alert["id"]))
        deleted = store.soft_delete_alert(int(alert["id"]))

        assert edited["triggerPrice"] == 200
        assert edited["alertReason"] == "first buy"
        assert disabled["status"] == "disabled"
        assert paused[0]["status"] == "disabled"
        assert enabled["status"] == "active"
        assert archived["status"] == "archived"
        assert deleted["status"] == "deleted"
        assert store.list_alerts() == []
        assert store.list_alerts(include_deleted=True)[0]["status"] == "deleted"
