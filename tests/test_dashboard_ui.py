from __future__ import annotations

import inspect

import pandas as pd

from data.watchlist_stars import WatchlistStarStore
from data.buy_plan_alerts import BuyPlanAlertStore
from ui import dashboard
from ui import dashboard_tables


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


def test_single_dashboard_row_refresh_uses_quote_only_fast_path() -> None:
    source = inspect.getsource(dashboard._refresh_single_dashboard_row)

    assert "refresh_symbols_by_mode([symbol], RefreshMode.PRICE_ONLY)" in source
    assert "get_market_data_provider(full_fundamentals=True)" not in source
    assert "provider.get_price_history" not in source
    assert "_sync_refreshed_symbols_to_dashboard_session" in source


def test_refresh_ticker_query_schedules_single_dashboard_row_refresh() -> None:
    params = {"refreshTicker": "now"}
    state = {}

    symbol = dashboard._consume_refresh_ticker_query(query_params=params, session_state=state)

    assert symbol == "NOW"
    assert state["dashboard_force_fmp_refresh_symbol"] == "NOW"
    assert "refreshTicker" not in params
    assert "_refresh_data_health_symbol" not in inspect.getsource(dashboard._handle_refresh_ticker_query)


def test_refresh_ticker_query_is_consumed_before_dashboard_table_load() -> None:
    source = inspect.getsource(dashboard.render)

    assert source.index("_consume_refresh_ticker_query()") < source.index('pop("dashboard_force_fmp_refresh_symbol"')
    assert "_handle_refresh_ticker_query()" not in source


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


def test_dashboard_star_marks_sort_before_unstarred_without_changing_row_fields(tmp_path) -> None:
    store = WatchlistStarStore(tmp_path / "cache.sqlite")
    store.set_star("NVDA", True)
    table = pd.DataFrame(
        [
            {"symbol": "NOW", "setup_score": 64, "action": "等待确认"},
            {"symbol": "NVDA", "setup_score": 64, "action": "等待确认"},
        ]
    )

    result = dashboard._apply_watchlist_star_marks(table, store)

    assert result["symbol"].tolist() == ["NVDA", "NOW"]
    assert bool(result.loc[result["symbol"] == "NVDA", "isStarred"].iloc[0]) is True
    assert bool(result.loc[result["symbol"] == "NOW", "isStarred"].iloc[0]) is False
    assert result.loc[result["symbol"] == "NVDA", "setup_score"].iloc[0] == 64
    assert "manual_rank" not in result.columns
    assert "conviction_score" not in result.columns


def test_dashboard_symbol_cell_only_marks_starred_rows() -> None:
    starred = pd.Series({"symbol": "NVDA", "isStarred": True})
    normal = pd.Series({"symbol": "NOW", "isStarred": False})

    starred_html = dashboard_tables._decision_table_cell_html(starred, {"key": "symbol"}, "NVDA")
    normal_html = dashboard_tables._decision_table_cell_html(normal, {"key": "symbol"}, "NOW")

    assert "⭐" in starred_html
    assert "NVDA" in starred_html
    assert "⭐" not in normal_html
    assert "☆" not in normal_html
    assert "toggleStar" not in starred_html
    assert "置顶" not in starred_html
    assert "pinned" not in starred_html


def test_dashboard_symbol_cell_shows_buy_alert_label_without_extra_column() -> None:
    row = pd.Series(
        {
            "symbol": "ORCL",
            "isStarred": False,
            "buyPlanAlertLabel": "买入提醒 $185.00",
            "buyPlanAlertStatus": "ACTIVE",
        }
    )

    html = dashboard_tables._decision_table_cell_html(row, {"key": "symbol"}, "ORCL")

    assert "ORCL" in html
    assert "买入提醒 $185.00" in html
    assert "watchlist-buy-alert" in html
    assert "⭐" not in html
    assert "☆" not in html


def test_dashboard_watchlist_columns_do_not_include_star_column() -> None:
    labels = [column["label"] for column in dashboard.WATCHLIST_COLUMNS]

    assert "星标" not in labels
    assert "计划买入提醒" not in labels
    assert labels[0] == "代码"


def test_dashboard_apply_buy_plan_alerts_triggers_and_sorts_first(tmp_path) -> None:
    store = BuyPlanAlertStore(tmp_path / "cache.sqlite")
    store.save_alert("ORCL", 185, 50)
    table = pd.DataFrame(
        [
            {"symbol": "NVDA", "price": "$200.00"},
            {"symbol": "ORCL", "price": "$184.90"},
        ]
    )

    result = dashboard._apply_buy_plan_alerts(table, store)

    assert result["symbol"].tolist()[0] == "ORCL"
    assert result.loc[result["symbol"] == "ORCL", "buyPlanAlertStatus"].iloc[0] == "TRIGGERED"
    assert result.loc[result["symbol"] == "ORCL", "buyPlanAlertLabel"].iloc[0] == "已到计划价"


def test_dashboard_star_action_is_lightweight_row_action(monkeypatch) -> None:
    class DummyStarStore:
        def is_starred(self, symbol: object) -> bool:
            return False

    monkeypatch.setattr(dashboard, "WatchlistStarStore", DummyStarStore)

    html = dashboard._dashboard_view_action_html("NVDA")

    assert "toggleStar=NVDA" in html
    assert "标星" in html
    assert "event.stopPropagation();" in html
    assert "dashboard-star-toggle" not in html


def test_dashboard_drawer_click_handler_ignores_star_action() -> None:
    source = inspect.getsource(dashboard.render_client_stock_detail_drawers)

    assert ".dashboard-star-action" in source
    assert ".dashboard-refresh-action" in source
