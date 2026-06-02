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
    assert "provider.get_price_history" not in refresh_source
    assert 'snapshot["current_price"] = market_price' in price_source
    assert "setdefault(\"current_price\"" not in price_source
