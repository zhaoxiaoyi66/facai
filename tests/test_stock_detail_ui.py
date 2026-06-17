from __future__ import annotations

import inspect
from types import SimpleNamespace

from ui import stock_detail


def test_stock_detail_load_uses_market_context_for_price_history() -> None:
    source = inspect.getsource(stock_detail._load_detail)

    assert "build_market_context" in source
    assert "build_market_history" in source
    assert "provider.get_price_history" not in source
    assert 'snapshot["current_price"] = market_price' in source
    assert "setdefault(\"current_price\"" not in source


def test_stock_detail_final_decision_uses_unified_buy_zone_context() -> None:
    source = inspect.getsource(stock_detail.render)

    assert "build_unified_buy_zone_context" in source
    assert '"daily_ohlcv": history' in source
    assert "buy_zone_context=buy_zone_context" in source


def test_stock_detail_decision_snapshot_uses_actionable_hierarchy() -> None:
    display = {
        "main_action_text": "仅观察 / 当前不建议新增",
        "current_price_action_text": "当前价不新增",
        "current_subzone_display_text": "修复观察区中段",
        "current_price_text": "$982.35",
        "setup_score": 55,
        "volume_acceptance_score": 48,
        "risk_reward_score": 55,
        "next_buy_range_low": 955.36,
        "next_buy_range_high": 966.01,
        "reclaim_line": 1095.0,
        "right_confirmation_low": 1122.38,
        "right_confirmation_high": 1144.82,
        "layer_break_line": 856.01,
        "structural_invalid_line": 711.02,
    }
    score = SimpleNamespace(action="只观察", max_portfolio_weight_percent=None)

    snapshot = stock_detail._stock_detail_decision_snapshot(display, score, None)

    assert snapshot["headline"] == "当前不新增：价格位于修复观察区中段，量价承接不足，赔率不够。"
    assert ("买入质量", "偏弱") in snapshot["tags"]
    assert ("下一触发", "回踩 $955.36 - $966.01 或站上 $1,095.00 后重评") in snapshot["tags"]
    assert snapshot["cards"][1]["lines"][1] == "右侧：站上 $1,095.00 后重评，放量站稳 $1,122.38 - $1,144.82 才确认"
    assert snapshot["cards"][2]["lines"][0] == "跌破 $856.01 后复核；跌破 $711.02 后系统不建议新增"


def test_stock_detail_price_hierarchy_labels_trigger_lines() -> None:
    display = {
        "next_buy_range_low": 955.36,
        "next_buy_range_high": 966.01,
        "primary_zone_range_text": "$978.19 - $985.80",
        "current_subzone_display_text": "修复观察区中段",
        "reclaim_line": 1095.0,
        "right_confirmation_low": 1122.38,
        "right_confirmation_high": 1144.82,
        "layer_break_line": 856.01,
        "structural_invalid_line": 711.02,
    }

    rows = stock_detail._price_hierarchy_rows(display)

    assert [row["title"] for row in rows] == ["左侧观察区", "当前所在区", "右侧修复线", "强确认区", "风险线"]
    assert rows[0]["value"] == "$955.36 - $966.01"
    assert rows[1]["value"] == "$978.19 - $985.80"
    assert rows[2]["value"] == "$1,095.00"
    assert rows[3]["value"] == "$1,122.38 - $1,144.82"
    assert rows[4]["value"] == "风险复核：跌破 $856.01；硬失效：跌破 $711.02"
