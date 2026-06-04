from __future__ import annotations

from pathlib import Path

import pytest

from data.portfolio import PortfolioPositionStore
from data.watchlist_store import add_watchlist_symbol
from data.watchlist_store import batch_add_watchlist_symbols
from data.watchlist_store import get_watchlist_symbols
from data.watchlist_store import load_watchlist_entries
from data.watchlist_store import normalize_watchlist_symbol
from data.watchlist_store import remove_watchlist_symbol
from data.watchlist_store import save_watchlist_entries
from settings import load_watchlist


def test_old_ticker_list_format_loads_as_entries(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.yaml"
    path.write_text("tickers:\n  - nvda\n  - BRK.B\n", encoding="utf-8")

    entries = load_watchlist_entries(path)

    assert [entry["ticker"] for entry in entries] == ["NVDA", "BRK.B"]
    assert entries[0]["status"] == "active"
    assert entries[0]["note"] == ""


def test_new_metadata_format_loads_entries(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        'watchlist:\n'
        '  - ticker: "NVO"\n'
        '    status: "waiting_buy_zone"\n'
        '    theme: "医药器械"\n'
        '    added_reason: "等待估值回落"\n'
        '    note: "GLP-1 复核"\n',
        encoding="utf-8",
    )

    [entry] = load_watchlist_entries(path)

    assert entry["ticker"] == "NVO"
    assert entry["status"] == "waiting_buy_zone"
    assert entry["theme"] == "医药器械"
    assert entry["added_reason"] == "等待估值回落"


def test_add_symbol_normalizes_and_dedupes(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.yaml"

    added = add_watchlist_symbol(" nvda ", theme="AI 基建", note="first", path=path)
    updated = add_watchlist_symbol("NVDA", status="needs_review", theme="AI 基建", note="second", path=path)

    entries = load_watchlist_entries(path)
    assert added["action"] == "added"
    assert updated["action"] == "updated"
    assert len(entries) == 1
    assert entries[0]["ticker"] == "NVDA"
    assert entries[0]["status"] == "needs_review"
    assert entries[0]["note"] == "second"


def test_get_watchlist_symbols_keeps_radar_ticker_list_contract(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.yaml"
    save_watchlist_entries(
        [
            {"ticker": "NVDA", "status": "active"},
            {"ticker": "MSFT", "status": "paused"},
        ],
        path,
    )

    assert get_watchlist_symbols(path) == ["NVDA", "MSFT"]
    assert load_watchlist(path) == ["NVDA", "MSFT"]


def test_batch_paste_dedupes_and_reports_invalid_symbols(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.yaml"

    result = batch_add_watchlist_symbols("nvda\nNVDA\nbad ticker\nBRK.B", theme="AI 基建", path=path)

    assert result["added"] == ["NVDA", "BRK.B"]
    assert result["updated"] == []
    assert result["invalid"] == ["bad ticker"]
    assert get_watchlist_symbols(path) == ["NVDA", "BRK.B"]


def test_invalid_ticker_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_watchlist_symbol("bad ticker")


def test_add_watchlist_symbol_does_not_create_trade_or_change_portfolio(tmp_path: Path) -> None:
    watchlist_path = tmp_path / "watchlist.yaml"
    db_path = tmp_path / "cache.sqlite"
    portfolio = PortfolioPositionStore(db_path)
    portfolio.save_position("NVDA", {"quantity": 2, "average_cost": 100, "position_tier": "A"})

    add_watchlist_symbol("NVDA", theme="AI 基建", note="already held", path=watchlist_path)
    position = portfolio.get_position("NVDA")

    assert position is not None
    assert position["quantity"] == 2
    assert position["average_cost"] == 100
    assert get_watchlist_symbols(watchlist_path) == ["NVDA"]


def test_remove_watchlist_symbol_does_not_affect_portfolio(tmp_path: Path) -> None:
    watchlist_path = tmp_path / "watchlist.yaml"
    db_path = tmp_path / "cache.sqlite"
    portfolio = PortfolioPositionStore(db_path)
    portfolio.save_position("NVDA", {"quantity": 2, "average_cost": 100, "position_tier": "A"})
    add_watchlist_symbol("NVDA", path=watchlist_path)

    result = remove_watchlist_symbol("NVDA", path=watchlist_path)

    assert result["action"] == "removed"
    assert get_watchlist_symbols(watchlist_path) == []
    assert portfolio.get_position("NVDA") is not None
