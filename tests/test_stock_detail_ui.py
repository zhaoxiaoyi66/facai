from __future__ import annotations

import inspect

from ui import stock_detail


def test_stock_detail_load_uses_market_context_for_price_history() -> None:
    source = inspect.getsource(stock_detail._load_detail)

    assert "build_market_context" in source
    assert "build_market_history" in source
    assert "provider.get_price_history" not in source
    assert 'snapshot["current_price"] = market_price' in source
    assert "setdefault(\"current_price\"" not in source
