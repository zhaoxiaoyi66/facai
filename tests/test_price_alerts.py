from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data.price_alerts import PriceAlertStore, evaluate_price_alerts, sync_buy_plan_price_alert
from data.price_alerts import _money


NOW = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


def test_price_alert_money_placeholder_is_chinese() -> None:
    assert _money(None) == "暂缺"
    assert _money("bad-price") == "暂缺"


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
        assert "买区" in first[0]["message"]
        assert "技术结构" in first[0]["message"]
        assert "历史入口字段" in first[0]["message"]
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
        assert alerts[0]["triggeredNow"] is True
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
        assert "报价快照缺失" in alerts[0]["marketWarning"]
        assert "current price" not in alerts[0]["marketWarning"]


def test_price_alert_does_not_trigger_before_price_crosses() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 205)
        PriceAlertStore(path).create_alert("NVDA", triggerDirection="below", triggerPrice=200)

        alerts = evaluate_price_alerts(path, now=NOW)

        assert alerts[0]["status"] == "active"
        assert alerts[0]["triggeredAt"] is None


def test_stale_price_alert_warns_without_triggering() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 195, "2026-05-28T11:00:00+00:00")
        PriceAlertStore(path).create_alert("NVDA", triggerDirection="below", triggerPrice=200)

        alerts = evaluate_price_alerts(path, now=NOW, quote_max_age_hours=24)

        assert alerts[0]["status"] == "active"
        assert alerts[0]["triggeredAt"] is None
        assert alerts[0]["triggeredNow"] is False
        assert alerts[0]["priceDataStale"] is True
        assert "价格数据可能过期" in alerts[0]["message"]
        assert "不作为触发信号" in alerts[0]["message"]


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


def test_editing_triggered_price_alert_rearms_changed_trigger() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 195)
        store = PriceAlertStore(path)
        alert = store.create_alert("NVDA", triggerDirection="below", triggerPrice=200)

        triggered = evaluate_price_alerts(path, now=NOW)[0]
        edited = store.update_alert(int(alert["id"]), triggerPrice=190)
        not_yet = evaluate_price_alerts(path, now=NOW)[0]
        _insert_quote(path, "NVDA", 185)
        triggered_again = evaluate_price_alerts(path, now=NOW)[0]

        assert triggered["status"] == "triggered"
        assert edited["status"] == "active"
        assert edited["triggeredAt"] is None
        assert not_yet["status"] == "active"
        assert not_yet["triggeredNow"] is False
        assert triggered_again["status"] == "triggered"
        assert triggered_again["triggeredNow"] is True


def test_buy_plan_sync_creates_and_updates_source_alert() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)

        first = sync_buy_plan_price_alert(
            path,
            symbol="ADBE",
            plan={
                "target_alert_price": 220,
                "alert_mode": "price_below",
                "plan_status": "active",
            },
        )
        updated = sync_buy_plan_price_alert(
            path,
            symbol="ADBE",
            plan={
                "target_alert_price": 215,
                "alert_mode": "price_near",
                "plan_status": "active",
            },
        )
        alerts = PriceAlertStore(path).list_alerts("ADBE")

        assert len(alerts) == 1
        assert first["id"] == updated["id"]
        assert updated["alertType"] == "BUY_PLAN_TRIGGER"
        assert updated["source"] == "buy_plan"
        assert updated["sourceId"] == "ADBE"
        assert updated["triggerDirection"] == "near"
        assert updated["triggerPrice"] == 215
        assert updated["linkedPlanType"] == "buy_plan"


def test_near_buy_plan_alert_triggers_within_threshold() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "ADBE", 224)
        sync_buy_plan_price_alert(
            path,
            symbol="ADBE",
            plan={
                "target_alert_price": 220,
                "alert_mode": "price_near",
                "near_threshold_pct": 2,
                "plan_status": "active",
            },
        )

        alerts = evaluate_price_alerts(path, now=NOW)

        assert alerts[0]["status"] == "triggered"
        assert alerts[0]["triggerDirection"] == "near"
        assert "接近 2% 以内" in alerts[0]["message"] or alerts[0]["triggeredNow"] is True


def test_radar_pullback_buy_plan_alert_can_trigger_from_entry_context() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NOK", 13.39)
        sync_buy_plan_price_alert(
            path,
            symbol="NOK",
            plan={
                "target_alert_price": 10,
                "alert_mode": "radar_pullback",
                "plan_status": "active",
            },
        )

        alerts = evaluate_price_alerts(
            path,
            now=NOW,
            entry_contexts={
                "NOK": {
                    "current_price": 13.39,
                    "technical_entry_zone_low": 12.19,
                    "technical_entry_zone_high": 13.43,
                }
            },
        )

        assert alerts[0]["status"] == "triggered"
        assert alerts[0]["alertReason"] == "计划买入：进入买区回踩区提醒"


def test_paused_buy_plan_sync_disables_alert() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        sync_buy_plan_price_alert(
            path,
            symbol="NOK",
            plan={
                "target_alert_price": 12,
                "alert_mode": "price_below",
                "plan_status": "active",
            },
        )

        paused = sync_buy_plan_price_alert(
            path,
            symbol="NOK",
            plan={
                "target_alert_price": 12,
                "alert_mode": "price_below",
                "plan_status": "paused",
            },
        )

        assert paused["status"] == "disabled"
