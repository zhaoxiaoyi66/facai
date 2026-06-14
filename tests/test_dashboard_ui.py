from __future__ import annotations

import inspect

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
