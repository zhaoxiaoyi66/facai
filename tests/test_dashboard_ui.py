from __future__ import annotations

import inspect

import pandas as pd

from ui import dashboard


def test_dashboard_loaders_use_market_context_for_price_history() -> None:
    cached_source = inspect.getsource(dashboard._load_cached_dashboard_row)
    refresh_source = inspect.getsource(dashboard._load_dashboard_row)
    price_source = inspect.getsource(dashboard._apply_market_price_to_snapshot)

    assert "build_market_history" in cached_source
    assert "build_market_history" in refresh_source
    assert "price_cache.get_history" not in cached_source
    assert "if force_refresh:" in refresh_source
    assert "provider.get_price_history(ticker, force_refresh=True)" in refresh_source
    assert 'snapshot["current_price"] = market_price' in price_source
    assert "setdefault(\"current_price\"" not in price_source


def test_dashboard_cached_table_reuses_batch_portfolio_context() -> None:
    source = inspect.getsource(dashboard._build_cached_dashboard_table)

    assert "build_action_fusion_portfolio_contexts(tickers)" in source
    assert "action_fusion_portfolio_context=portfolio_contexts.get" in source


def test_price_and_technical_refresh_buttons_keep_dashboard_table_cache() -> None:
    source = inspect.getsource(dashboard._render_dashboard_header)
    price_branch = source.split('key="dashboard_refresh_price_only"', 1)[1].split("with command_cols[1]", 1)[0]
    technical_branch = source.split('key="dashboard_refresh_daily_technical"', 1)[1].split("with command_cols[3]", 1)[0]

    assert "_clear_dashboard_table_cache()" not in price_branch
    assert "_clear_dashboard_table_cache()" not in technical_branch


def test_sync_refreshed_symbols_replaces_existing_rows_without_adding_hidden_positions() -> None:
    state = {
        "dashboard_table_cache_key": (("NOW", "MSFT"), dashboard.DASHBOARD_SCORE_SCHEMA_VERSION),
        "dashboard_table_cache": pd.DataFrame(
            [
                {"symbol": "NOW", "price": "old"},
                {"symbol": "MSFT", "price": "old"},
            ]
        ),
    }

    invalidated = dashboard._sync_refreshed_symbols_to_dashboard_session(
        ["NOW", "HELD"],
        tickers=("NOW", "MSFT"),
        session_state=state,
        row_loader=lambda symbol: {"symbol": symbol, "price": f"new-{symbol}"},
    )

    table = state["dashboard_table_cache"]
    assert "dashboard_table_cache" in invalidated
    assert table["symbol"].tolist() == ["NOW", "MSFT"]
    assert table.loc[table["symbol"] == "NOW", "price"].iloc[0] == "new-NOW"
    assert table.loc[table["symbol"] == "MSFT", "price"].iloc[0] == "old"
