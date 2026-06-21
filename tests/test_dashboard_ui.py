from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pandas as pd

from data.watchlist_stars import WatchlistStarStore
from data.buy_plan_alerts import BuyPlanAlertStore
from ui import dashboard
from ui import dashboard_drawer
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


def test_dashboard_market_price_keeps_fresh_price_only_quote(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard,
        "build_market_context",
        lambda _ticker: {"currentPrice": 95.0, "priceSource": "price_history"},
    )
    snapshot = {
        "current_price": 123.4,
        "refresh_mode": "PRICE_ONLY",
        "quote_updated_at": datetime.now(timezone.utc).isoformat(),
    }
    technicals = {"price": 95.0}

    dashboard._apply_market_price_to_snapshot("NVDA", snapshot, technicals)

    assert snapshot["current_price"] == 123.4
    assert snapshot["price"] == 123.4
    assert technicals["price"] == 123.4


def test_dashboard_cached_table_reuses_batch_portfolio_context() -> None:
    source = inspect.getsource(dashboard._build_cached_dashboard_table)

    assert "build_action_fusion_portfolio_contexts(tickers)" in source
    assert "action_fusion_portfolio_context=portfolio_contexts.get" in source


def test_dashboard_quality_negative_items_filters_drawdown_without_garbled_literal() -> None:
    row = pd.Series(
        {
            "keyNegativeDrivers": [
                "drawdown > 40%",
                "距高点回撤超过40%",
                f"{chr(0x9365)}{chr(0x74D2)}40",
                "估值偏高",
            ]
        }
    )

    assert dashboard._quality_negative_items(row) == ["估值偏高"]


def test_price_and_technical_refresh_actions_keep_dashboard_table_cache() -> None:
    source = inspect.getsource(dashboard._render_dashboard_header)
    price_branch = source.split('key="dashboard_refresh_price_only"', 1)[1].split('key="dashboard_refresh_daily_technical"', 1)[0]
    technical_branch = source.split('key="dashboard_refresh_daily_technical"', 1)[1].split('if st.button("刷新大盘环境"', 1)[0]

    assert "_clear_dashboard_table_cache()" not in price_branch
    assert "_clear_dashboard_table_cache()" not in technical_branch


def test_dashboard_header_uses_smart_market_refresh_as_primary_action() -> None:
    source = inspect.getsource(dashboard._render_dashboard_header)
    before_more = source.split('with st.popover("更多 ▾"', 1)[0]

    assert "智能刷新市场数据" in before_more
    assert "dashboard_smart_market_refresh" in before_more
    assert "更新价格" not in before_more
    assert "更新技术" not in before_more
    assert "仅更新价格" in source
    assert "重算技术指标" in source
    assert "强制全量刷新" in source
    assert "get_us_market_session_status" in source
    assert "latest_data_display_label" in source


def test_smart_refresh_feedback_uses_display_data_label(monkeypatch) -> None:
    monkeypatch.setattr(dashboard.st, "session_state", {})

    message, tone = dashboard._smart_refresh_feedback_message(
        {
            "session_status": "CLOSED_AFTER_SESSION",
            "session_label": "美股已收盘",
            "latest_data_display_label": "昨夜收盘 06/15",
            "result": {"refreshed_count": 28, "skipped_count": 0, "failed_count": 0},
        }
    )

    assert tone == "success"
    assert message.startswith("已同步昨夜收盘 06/15。")


def test_smart_refresh_skip_feedback_uses_latest_available_date(monkeypatch) -> None:
    monkeypatch.setattr(dashboard.st, "session_state", {})

    message, tone = dashboard._smart_refresh_feedback_message(
        {
            "session_status": "WEEKEND_OR_HOLIDAY",
            "session_label": "美股休市",
            "latest_data_display_label": "最新可用收盘 06/12",
            "result": {"mode": "SMART_SKIP"},
        }
    )

    assert tone == "info"
    assert message == "美股休市中，已使用最新可用收盘 06/12。"


def test_data_health_refresh_feedback_localizes_backend_status_fields() -> None:
    source = inspect.getsource(dashboard._render_data_health_refresh_result)

    assert "价格状态：" in source
    assert "日线状态：" in source
    assert "quoteStatus:" not in source
    assert "historyStatus:" not in source
    assert dashboard._refresh_part_status_label("refreshed") == "已更新"
    assert dashboard._refresh_part_status_label("not_run") == "未执行"
    assert dashboard._refresh_part_status_label("stale") == "待更新"
    assert dashboard._refresh_part_status_label(None) == "待补"
    assert dashboard._refresh_part_status_label("NEW_REFRESH_STATUS") == "待补"


def test_dashboard_refresh_status_labels_do_not_show_raw_internal_codes() -> None:
    assert dashboard._macro_indicator_label("NEW_MACRO_INDICATOR") == "未知指标"
    assert dashboard._macro_indicator_label("人工指标") == "人工指标"
    assert dashboard._macro_refresh_status_label("NEW_MACRO_STATUS") == "未知"
    assert dashboard._macro_refresh_indicator_status_label("NEW_INDICATOR_STATUS") == "未知"
    assert dashboard._macro_refresh_status_label("人工复核") == "人工复核"


def test_dashboard_legacy_na_placeholders_are_not_rendered() -> None:
    source = inspect.getsource(dashboard)

    assert '"N/A"' not in source
    assert "'N/A'" not in source
    assert dashboard._dashboard_display_text("N/A") == "待补"
    assert dashboard._dashboard_display_text(None, "未记录") == "未记录"
    assert dashboard._dashboard_placeholder("N/A") is True
    assert dashboard._pct_text(None) == "暂无"
    assert dashboard._dashboard_last_updated_text("N/A") == "未记录"
    assert dashboard._format_billions("N/A") == "暂无"
    assert dashboard._format_plain_number("N/A") == "暂无"
    assert dashboard._average_percent_column(pd.DataFrame({"x": ["N/A"]}), "x") == "暂无"
    assert dashboard._action_with_position(pd.Series({})) == "待补"
    assert "N/A" not in dashboard._detail_metric_html("估值", "N/A")


def test_data_health_detail_groups_localize_final_decision_issue() -> None:
    html = dashboard._data_health_detail_groups_html(["NVDA finalDecision failed to build"])

    assert "NVDA" in html
    assert "决策结论异常" in html
    assert "finalDecision" not in html


def test_data_health_detail_groups_use_refresh_copy_instead_of_expired_copy() -> None:
    html = dashboard._data_health_detail_groups_html(["NVDA 价格需刷新", "CRM 历史待更新"])

    assert "价格缺失 / 需刷新" in html
    assert "历史缺失 / 待更新" in html
    assert "价格过期" not in html
    assert "历史过期" not in html
    assert dashboard._data_health_category_label("stale_quote") == "价格需刷新"
    assert dashboard._data_health_category_label("stale_history") == "历史待更新"
    assert dashboard._data_health_category_from_text("NVDA 价格过期") == "stale_quote"
    assert dashboard._data_health_category_from_text("CRM 历史过期") == "stale_history"


def test_risk_summary_strip_uses_chinese_blocker_label() -> None:
    source = inspect.getsource(dashboard._render_risk_radar_summary_strip)

    assert '"阻断项"' in source
    assert '"blocker"' not in source


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


def test_sync_refreshed_symbols_reports_cache_sync_progress() -> None:
    state = {
        "dashboard_table_cache": pd.DataFrame(
            [
                {"symbol": "NOW", "price": "old"},
                {"symbol": "MSFT", "price": "old"},
            ]
        ),
    }
    events = []

    dashboard._sync_refreshed_symbols_to_dashboard_session(
        ["NOW", "MSFT"],
        session_state=state,
        row_loader=lambda symbol: {"symbol": symbol, "price": f"new-{symbol}"},
        progress_callback=events.append,
    )

    assert [event["phase"] for event in events] == ["dashboard_cache_sync"] * 4
    assert [event["index"] for event in events] == [0, 1, 1, 2]
    assert events[-1]["total"] == 2
    assert state["dashboard_table_cache"]["price"].tolist() == ["new-NOW", "new-MSFT"]


def test_dashboard_refresh_clears_progress_after_cache_sync() -> None:
    source = inspect.getsource(dashboard._refresh_dashboard_cache_for_mode)

    assert "progress_callback=_render_sync_progress" in source
    assert "progress_slot.empty()" in source
    assert "_refresh_done_html(progress_total)" not in source


def test_daily_technical_skipped_symbols_are_synced_to_dashboard_table() -> None:
    result = {
        "mode": "DAILY_TECHNICAL",
        "ticker_results": [
            {"ticker": "NOW", "status": "skipped"},
            {"ticker": "MSFT", "status": "success"},
            {"ticker": "NVDA", "status": "failed"},
        ],
    }

    assert dashboard._successful_refresh_symbols(result) == ["NOW", "MSFT"]


def test_dashboard_cache_sync_skips_noop_daily_technical_skips() -> None:
    result = {
        "mode": "DAILY_TECHNICAL",
        "ticker_results": [
            {"ticker": "NOW", "status": "skipped", "source": ""},
            {"ticker": "MSFT", "status": "skipped", "source": "latest_close_sync"},
            {"ticker": "NVDA", "status": "success"},
        ],
    }

    assert dashboard._dashboard_cache_sync_symbols(result) == ["MSFT", "NVDA"]


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


def test_dashboard_price_market_cell_shows_price_source_label() -> None:
    row = pd.Series(
        {
            "price": "$102.37",
            "marketCap": "10.4B",
            "rawSnapshot": {
                "price_session": "LAST_CLOSE",
                "price_as_of": "2026-06-16",
                "last_close_synced_at": "2026-06-17T12:00:00+00:00",
            },
        }
    )

    html = dashboard_tables._decision_table_cell_html(row, {"key": "priceMarket"}, "NOW")

    assert "$102.37" in html
    assert "昨夜收盘" in html
    assert "数据日期：2026-06-16" in html
    assert "刷新时间：06/17 20:00 HKT" in html


def test_dashboard_table_badge_hides_unknown_internal_codes() -> None:
    assert dashboard_tables._short_badge_text("NEW_INTERNAL_BADGE_STATUS") == "待复核"
    assert dashboard_tables._short_badge_text("") == "待复核"
    assert dashboard_tables._short_badge_text("人工观察") == "人工观察"


def test_dashboard_drawer_price_source_label_uses_same_snapshot_mapping() -> None:
    row = pd.Series(
        {
            "rawSnapshot": {
                "refresh_mode": "PRICE_ONLY",
                "quote_updated_at": "2026-06-17T12:00:00+00:00",
            },
        }
    )

    html = dashboard_drawer._drawer_price_source_html(row)

    assert "最新报价" in html
    assert "刷新时间：06/17 20:00 HKT" in html


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


def test_dashboard_apply_buy_plan_alerts_records_trigger_source(tmp_path) -> None:
    store = BuyPlanAlertStore(tmp_path / "cache.sqlite")
    store.save_alert("ORCL", 185, 50)
    table = pd.DataFrame([{"symbol": "ORCL", "price": "$184.90"}])

    result = dashboard._apply_buy_plan_alerts(table, store, trigger_source="LAST_CLOSE")

    alert = result.loc[result["symbol"] == "ORCL", "buyPlanAlert"].iloc[0]
    assert alert["trigger_source"] == "LAST_CLOSE"


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
