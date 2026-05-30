from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from data.decision_log import TradeJournalStore
from data.portfolio import PortfolioPositionStore
from data.portfolio_reconciliation import build_portfolio_reconciliation
from data.portfolio_trade_sync import apply_trade_to_portfolio


def _db(tmpdir: str) -> Path:
    return Path(tmpdir) / "portfolio_reconciliation.sqlite"


def _row(rows: list[dict], symbol: str) -> dict:
    return {row["symbol"]: row for row in rows}[symbol]


def test_reconciliation_returns_ok_when_portfolio_matches_synced_journal() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "NOW",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 10, "price": 500},
        )
        apply_trade_to_portfolio(entry["id"], path)

        result = _row(build_portfolio_reconciliation(path), "NOW")

        assert result["status"] == "ok"
        assert result["positionQuantity"] == 10
        assert result["journalQuantity"] == 10
        assert result["quantityDiff"] == 0
        assert result["positionAverageCost"] == 500
        assert result["journalAverageCost"] == 500
        assert result["reasons"] == []


def test_reconciliation_warns_when_symbol_has_unsynced_trade() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        synced = TradeJournalStore(path).save_entry(
            "MSFT",
            {"trade_date": "2026-05-29", "action_type": "buy", "quantity": 5, "price": 300},
        )
        apply_trade_to_portfolio(synced["id"], path)
        TradeJournalStore(path).save_entry(
            "MSFT",
            {"trade_date": "2026-05-30", "action_type": "add", "quantity": 1, "price": 320},
        )

        result = _row(build_portfolio_reconciliation(path), "MSFT")

        assert result["status"] == "warning"
        assert result["unsyncedTradeCount"] == 1
        assert "unsynced_trades_exist" in result["reasons"]
        assert result["positionQuantity"] == 5
        assert result["journalQuantity"] == 5


def test_reconciliation_flags_quantity_mismatch() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "NVDA",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 10, "price": 100},
        )
        apply_trade_to_portfolio(entry["id"], path)
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 8, "average_cost": 100})

        result = _row(build_portfolio_reconciliation(path), "NVDA")

        assert result["status"] == "mismatch"
        assert result["quantityDiff"] == -2
        assert "quantity_mismatch" in result["reasons"]


def test_reconciliation_warns_on_average_cost_mismatch() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        first = TradeJournalStore(path).save_entry(
            "ADBE",
            {"trade_date": "2026-05-29", "action_type": "buy", "quantity": 10, "price": 400},
        )
        second = TradeJournalStore(path).save_entry(
            "ADBE",
            {"trade_date": "2026-05-30", "action_type": "add", "quantity": 10, "price": 500},
        )
        apply_trade_to_portfolio(first["id"], path)
        apply_trade_to_portfolio(second["id"], path)
        PortfolioPositionStore(path).save_position("ADBE", {"quantity": 20, "average_cost": 430})

        result = _row(build_portfolio_reconciliation(path), "ADBE")

        assert result["status"] == "warning"
        assert result["costDiff"] == -20
        assert "average_cost_mismatch" in result["reasons"]


def test_reconciliation_warns_when_position_has_no_synced_journal_source() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("HOOD", {"quantity": 12, "average_cost": 40})

        result = _row(build_portfolio_reconciliation(path), "HOOD")

        assert result["status"] == "warning"
        assert result["positionQuantity"] == 12
        assert result["journalQuantity"] is None
        assert "position_without_synced_journal" in result["reasons"]


def test_reconciliation_flags_synced_journal_without_active_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "COIN",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 4, "price": 250},
        )
        apply_trade_to_portfolio(entry["id"], path)
        PortfolioPositionStore(path).deactivate_position("COIN")

        result = _row(build_portfolio_reconciliation(path), "COIN")

        assert result["status"] == "mismatch"
        assert result["positionQuantity"] is None
        assert result["journalQuantity"] == 4
        assert "synced_journal_without_active_position" in result["reasons"]
