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
        "primary_zone_range_text": "$978.19 - $985.80",
        "reclaim_line": 1095.0,
        "right_confirmation_low": 1122.38,
        "right_confirmation_high": 1144.82,
        "layer_break_line": 856.01,
        "structural_invalid_line": 711.02,
    }
    score = SimpleNamespace(action="只观察", max_portfolio_weight_percent=None)

    snapshot = stock_detail._stock_detail_decision_snapshot(display, score, None)

    assert snapshot["headline"] == "当前不新增：价格位于修复观察区中段，量能承接不足，既未回到左侧低吸区，也未站上右侧修复线。"
    assert ("买入质量", "偏弱") in snapshot["tags"]
    assert ("左侧路径", "$955.36 - $966.01") in snapshot["tags"]
    assert snapshot["cards"][0]["title"] == "左侧路径"
    assert snapshot["cards"][0]["lines"][0] == "回踩 $955.36 - $966.01，看承接；有承接才考虑试仓。"
    assert snapshot["cards"][1]["lines"][0] == "当前 $982.35，位于 $978.19 - $985.80 修复观察区中段，不追。"
    assert snapshot["cards"][2]["lines"][0] == "站上 $1,095.00 后重新评估；放量站稳 $1,122.38 - $1,144.82 后才考虑右侧确认。"
    assert snapshot["cards"][3]["lines"][0] == "跌破 $856.01 后复核；跌破 $711.02 后系统不建议新增。"


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

    assert [row["title"] for row in rows] == ["左侧低吸观察区", "当前所在区", "右侧修复线", "强确认区", "风险线"]
    assert rows[0]["value"] == "$955.36 - $966.01"
    assert rows[1]["value"] == "$978.19 - $985.80"
    assert rows[2]["value"] == "$1,095.00"
    assert rows[3]["value"] == "$1,122.38 - $1,144.82"
    assert rows[4]["value"] == "风险复核：跌破 $856.01；硬失效：跌破 $711.02"


def test_stock_detail_setup_quality_describes_mid_rr_as_general() -> None:
    display = {"setup_score": 55, "volume_acceptance_score": 48, "risk_reward_score": 55}

    assert stock_detail._setup_quality_decision_text(display) == "买入质量偏弱：承接不足是主阻断，风险收益一般，等待确认。"


def test_stock_detail_setup_quality_calls_rr_insufficient_only_below_45() -> None:
    display = {"setup_score": 48, "volume_acceptance_score": 48, "risk_reward_score": 42}

    assert stock_detail._setup_quality_decision_text(display) == "买入质量偏弱：赔率不足且承接未确认。"
