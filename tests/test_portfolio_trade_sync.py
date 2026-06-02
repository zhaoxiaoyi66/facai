from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from data.decision_log import TradeJournalStore
from data.portfolio import PortfolioPositionStore, PortfolioSettingsStore
from data.portfolio_trade_sync import (
    apply_trade_to_portfolio,
    preview_trade_portfolio_effect,
    unsynced_trade_counts_by_symbol,
)
from data.trade_safety_gate import trade_sync_policy
from data.portfolio_view_model import build_portfolio_view_model


def _db(tmpdir: str) -> Path:
    return Path(tmpdir) / "portfolio_trade_sync.sqlite"


def _insert_history(path: Path, ticker: str, close: float, fetched_at: str) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                close REAL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (ticker, date)
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO price_history VALUES (?, ?, ?, ?)",
            (ticker.upper(), "2026-05-30", close, fetched_at),
        )
        conn.commit()


def test_buy_trade_sync_creates_or_increases_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "NOW",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 10, "price": 500},
        )

        preview = preview_trade_portfolio_effect(entry["id"], path)
        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("NOW")

        assert preview["currentQuantity"] == 0
        assert preview["afterQuantity"] == 10
        assert result["status"] == "success"
        assert position["quantity"] == 10
        assert position["average_cost"] == 500


def test_trade_sync_preview_uses_market_context_price() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioSettingsStore(path).save_settings({"total_portfolio_value": 10000})
        _insert_history(path, "CRWV", 60, "2026-05-28T10:00:00+00:00")
        _insert_history(path, "FMP:CRWV", 70, "2026-05-30T10:00:00+00:00")
        entry = TradeJournalStore(path).save_entry(
            "CRWV",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 2, "price": 50},
        )

        preview = preview_trade_portfolio_effect(entry["id"], path)

        assert preview["afterMarketValue"] == 140
        assert preview["afterPositionPct"] == pytest.approx(1.4)


def test_add_trade_sync_updates_weighted_average_cost() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("MSFT", {"quantity": 10, "average_cost": 100})
        entry = TradeJournalStore(path).save_entry(
            "MSFT",
            {"trade_date": "2026-05-30", "action_type": "add", "quantity": 10, "price": 200},
        )

        apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("MSFT")

        assert position["quantity"] == 20
        assert position["average_cost"] == 150


def test_buy_sync_reopens_archived_position_from_zero() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        position_store = PortfolioPositionStore(path)
        position_store.save_position("NVDA", {"quantity": 158, "average_cost": 100})
        position_store.deactivate_position("NVDA")
        entry = TradeJournalStore(path).save_entry(
            "NVDA",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 1, "price": 120},
        )

        preview = preview_trade_portfolio_effect(entry["id"], path)
        result = apply_trade_to_portfolio(entry["id"], path)
        position = position_store.get_position("NVDA")

        assert preview["currentQuantity"] == 0
        assert preview["afterQuantity"] == 1
        assert result["status"] == "success"
        assert position["is_active"] == 1
        assert position["quantity"] == 1
        assert position["average_cost"] == 120


def test_sell_trade_sync_reduces_position_without_changing_average_cost() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 10, "average_cost": 100})
        entry = TradeJournalStore(path).save_entry(
            "NVDA",
            {"trade_date": "2026-05-30", "action_type": "sell", "quantity": 4, "price": 200},
        )

        apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("NVDA")

        assert position["quantity"] == 6
        assert position["average_cost"] == 100


def test_blocked_sell_cannot_sync_to_portfolio() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 158, "average_cost": 100})
        entry = TradeJournalStore(path).save_entry(
            "NVDA",
            {
                "trade_date": "2026-05-30",
                "action_type": "sell",
                "quantity": 100,
                "price": 200,
                "currentPositionQuantity": 158,
                "positionClass": "A",
                "corePositionPct": 0.6,
                "tradingPositionPct": 0.4,
                "plannedSellPct": 0.1,
                "sellReasonType": "macro",
                "thesisBroken": False,
                "positionOverLimit": False,
                "reentryPullbackPrice": 180,
                "reentryBreakoutPrice": 205,
                "reentryPlanText": "回踩或重新站回卖出价时分批买回",
            },
        )

        preview = preview_trade_portfolio_effect(entry["id"], path)
        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("NVDA")

        assert entry["discipline_status"] == "blocked"
        assert preview["status"] == "failed"
        assert preview["syncStatus"] == "failed"
        assert "BLOCK" in preview["error"]
        assert result["status"] == "failed"
        assert "纪律门禁 BLOCK" in result["error"]
        assert position["quantity"] == 158
        assert position["average_cost"] == 100


def test_legacy_blocker_json_sell_cannot_sync_even_without_status() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 158, "average_cost": 100})
        entry = TradeJournalStore(path).save_entry(
            "NVDA",
            {"trade_date": "2026-05-30", "action_type": "sell", "quantity": 50, "price": 200},
        )
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                """
                UPDATE trade_journal_entries
                SET discipline_status = NULL,
                    blockers_json = '["legacy_blocker"]'
                WHERE id = ?
                """,
                (entry["id"],),
            )
            conn.commit()

        preview = preview_trade_portfolio_effect(entry["id"], path)
        result = apply_trade_to_portfolio(entry["id"], path)
        counts = unsynced_trade_counts_by_symbol(path)
        position = PortfolioPositionStore(path).get_position("NVDA")

        assert preview["status"] == "failed"
        assert preview["syncStatus"] == "failed"
        assert "BLOCK" in preview["error"]
        assert result["status"] == "failed"
        assert "BLOCK" in result["error"]
        assert counts.get("NVDA", 0) == 0
        assert position["quantity"] == 158


def test_trade_sync_policy_blocks_parsed_blocker_lists() -> None:
    sell_policy = trade_sync_policy({"action_type": "sell", "blockers": ["legacy_blocker"]})
    buy_policy = trade_sync_policy({"action_type": "buy", "blockers": ["legacy_blocker"]})

    assert sell_policy["canSync"] is False
    assert "BLOCK" in sell_policy["reason"]
    assert buy_policy["canSync"] is True


def test_trim_cannot_sync_more_than_current_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("ADBE", {"quantity": 3, "average_cost": 400})
        entry = TradeJournalStore(path).save_entry(
            "ADBE",
            {"trade_date": "2026-05-30", "action_type": "trim", "quantity": 5, "price": 450},
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("ADBE")

        assert result["status"] == "failed"
        assert "超过当前组合持仓" in result["error"]
        assert position["quantity"] == 3


def test_same_trade_cannot_sync_twice() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "CRM",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 2, "price": 100},
        )

        first = apply_trade_to_portfolio(entry["id"], path)
        second = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("CRM")

        assert first["status"] == "success"
        assert second["status"] == "already_synced"
        assert position["quantity"] == 2


def test_skip_sync_does_not_change_portfolio() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("HOOD", {"quantity": 7, "average_cost": 50})
        entry = TradeJournalStore(path).save_entry(
            "HOOD",
            {"trade_date": "2026-05-30", "action_type": "skip", "quantity": 1, "price": 55},
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("HOOD")

        assert result["status"] == "success"
        assert result["actionType"] == "skip"
        assert position["quantity"] == 7


def test_unsynced_trade_does_not_change_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("VST", {"quantity": 8, "average_cost": 80})
        TradeJournalStore(path).save_entry(
            "VST",
            {"trade_date": "2026-05-30", "action_type": "add", "quantity": 2, "price": 90},
        )

        position = PortfolioPositionStore(path).get_position("VST")

        assert position["quantity"] == 8
        assert position["average_cost"] == 80


def test_portfolio_view_model_flags_unsynced_trades_for_symbol() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("ANET", {"quantity": 5, "average_cost": 300})
        entry = TradeJournalStore(path).save_entry(
            "ANET",
            {"trade_date": "2026-05-30", "action_type": "add", "quantity": 1, "price": 310},
        )

        before = build_portfolio_view_model(path, {"ANET": 320})
        counts = unsynced_trade_counts_by_symbol(path)
        apply_trade_to_portfolio(entry["id"], path)
        after = build_portfolio_view_model(path, {"ANET": 320})

        assert counts["ANET"] == 1
        assert before["rows"][0]["unsyncedTradeCount"] == 1
        assert after["rows"][0]["unsyncedTradeCount"] == 0


def test_blocked_sell_is_not_counted_as_actionable_unsynced_trade() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 158, "average_cost": 100})
        TradeJournalStore(path).save_entry(
            "NVDA",
            {
                "trade_date": "2026-05-30",
                "action_type": "sell",
                "quantity": 100,
                "price": 200,
                "currentPositionQuantity": 158,
                "positionClass": "A",
                "corePositionPct": 0.6,
                "tradingPositionPct": 0.4,
                "plannedSellPct": 0.1,
                "sellReasonType": "macro",
                "thesisBroken": False,
                "positionOverLimit": False,
            },
        )

        counts = unsynced_trade_counts_by_symbol(path)
        view = build_portfolio_view_model(path, {"NVDA": 210})

        assert counts.get("NVDA", 0) == 0
        assert view["rows"][0]["unsyncedTradeCount"] == 0
