from __future__ import annotations

import inspect

from ui import buy_zone


def test_buy_zone_rows_use_market_context_for_price_history() -> None:
    source = inspect.getsource(buy_zone._load_buy_zone_rows)

    assert "build_market_context" in source
    assert "build_market_history" in source
    assert "provider.get_price_history" not in source
