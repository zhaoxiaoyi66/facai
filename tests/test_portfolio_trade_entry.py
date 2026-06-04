from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import pytest

import data.portfolio_trade_entry as portfolio_trade_entry
from data.decision_log import TradeJournalStore
from data.portfolio import PortfolioPositionStore
from data.portfolio_trade_entry import submit_portfolio_buy_add


def _report(decision: str = "ALLOW_BUY") -> dict:
    return {
        "ticker": "NVDA",
        "decision": decision,
        "current_price": 100,
        "buy_zone": [90, 110],
        "watch_zone": [110, 130],
        "chase_zone": [140, 999],
        "final_score": 88,
        "valuation_score": 72,
        "core_max_pct": 20,
        "trade_max_pct": 8,
        "allowed_add_pct": 3,
        "block_reasons": ["当前价进入追高禁止区"] if decision == "BLOCK_CHASE" else [],
        "data_status": "OK",
        "is_stale": False,
    }


def _base_values(**overrides):
    values = {
        "quantity": 2,
        "price": 100,
        "position_tier": "A",
        "decision_mood": "plan_execution",
        "buy_reason": "按计划执行",
        "target_sell_price": 180,
    }
    values.update(overrides)
    return values


def test_portfolio_buy_add_allowed_creates_journal_and_syncs_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add("NVDA", _base_values(), path=path, radar_report=_report())

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        position = PortfolioPositionStore(path).get_position("NVDA")
        assert entry is not None
        assert entry["action_type"] == "buy"
        assert entry["position_class"] == "A"
        assert entry["target_sell_price"] == 180
        assert position is not None
        assert position["quantity"] == 2
        assert position["average_cost"] == 100
        assert position["position_tier"] == "A"
        assert position["planned_sell_price"] == 180


def test_portfolio_buy_add_records_hkt_trade_time(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime(2026, 6, 4, 15, 30, 12, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    monkeypatch.setattr(portfolio_trade_entry, "_hkt_now", lambda: fixed)

    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add("NVDA", _base_values(), path=path, radar_report=_report())

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["trade_date"] == "2026-06-04"
        assert entry["created_at"] == fixed.isoformat()
        assert entry["gate_checked_at"] == fixed.isoformat()


def test_portfolio_add_uses_add_action_for_existing_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 1, "average_cost": 90, "position_tier": "A"})

        result = submit_portfolio_buy_add("NVDA", _base_values(quantity=1, price=110), path=path, radar_report=_report())

        position = PortfolioPositionStore(path).get_position("NVDA")
        assert result["entry"]["action_type"] == "add"
        assert position is not None
        assert position["quantity"] == 2
        assert round(position["average_cost"], 2) == 100


def test_blocked_gate_saves_journal_but_does_not_sync_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add("NVDA", _base_values(), path=path, radar_report=_report("BLOCK_CHASE"))

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("NVDA") is None


def test_observation_only_saves_journal_but_does_not_sync_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add(
            "NVDA",
            _base_values(radar_observation_only=True),
            path=path,
            radar_report=_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_observation_only"]
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("NVDA") is None


def test_fomo_mood_blocks_sync_even_when_radar_allows_buy() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add(
            "NVDA",
            _base_values(decision_mood="fomo"),
            path=path,
            radar_report=_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["mood_gate_blocked"]
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("NVDA") is None


def test_position_tier_is_required_for_portfolio_buy_add() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        with pytest.raises(ValueError):
            submit_portfolio_buy_add("NVDA", _base_values(position_tier="UNCLASSIFIED"), path=path, radar_report=_report())
        with pytest.raises(ValueError):
            submit_portfolio_buy_add("NVDA", _base_values(position_tier=""), path=path, radar_report=_report())

        assert TradeJournalStore(path).list_entries("NVDA") == []


def test_portfolio_row_does_not_render_archive_entry() -> None:
    from ui.portfolio import _position_row_html

    html = _position_row_html({"symbol": "NVDA", "positionTier": "A", "quantity": 2, "averageCost": 100})

    assert "portfolio-archive-link" not in html
    assert "portfolioArchiveConfirm" not in html
    assert "归档" not in html
    assert "加仓" in html


def test_portfolio_ui_has_no_archive_or_direct_position_save_path() -> None:
    import inspect
    import ui.portfolio as portfolio

    source = inspect.getsource(portfolio)

    assert "portfolioArchiveConfirm" not in source
    assert "portfolio_archive_symbol" not in source
    assert "deactivate_position" not in source
    assert "save_position(" not in source
    assert not hasattr(portfolio, "_render_deactivate_dialog_if_needed")
    assert not hasattr(portfolio, "_save_position_from_form")
