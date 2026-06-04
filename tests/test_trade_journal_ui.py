from __future__ import annotations

import inspect

from ui import trade_journal


def test_new_trade_entry_actions_are_sell_trim_only() -> None:
    assert set(trade_journal.SELL_ENTRY_ACTION_OPTIONS.values()) == {"sell", "trim"}
    assert "buy" not in trade_journal.SELL_ENTRY_ACTION_OPTIONS.values()
    assert "add" not in trade_journal.SELL_ENTRY_ACTION_OPTIONS.values()


def test_new_trade_entry_uses_active_position_dropdown() -> None:
    source = inspect.getsource(trade_journal._render_editor)

    assert "_active_sell_positions" in source
    assert "SELL_ENTRY_ACTION_OPTIONS" in source
    assert "买入/加仓请前往组合持仓页操作" in source


def test_sell_quantity_cannot_exceed_current_position() -> None:
    assert trade_journal._sell_quantity_validation_error("sell", 11, 10)
    assert trade_journal._sell_quantity_validation_error("trim", 11, 10)
    assert trade_journal._sell_quantity_validation_error("sell", 10, 10) == ""
    assert trade_journal._sell_quantity_validation_error("buy", 11, 10) == ""


def test_reentry_plan_suggestion_uses_market_context_helpers() -> None:
    source = inspect.getsource(trade_journal._build_reentry_plan_suggestion)

    assert "build_market_context" in source
    assert "build_market_history" in source
    assert "CacheReadModel" not in source


def test_trade_entry_detail_does_not_treat_invalidation_only_as_reentry_plan() -> None:
    entry = {
        "has_reentry_plan": 1,
        "reentry_thesis_invalidation": "thesis broken",
    }

    html = trade_journal._entry_reentry_plan_html(entry)

    assert trade_journal._entry_has_concrete_reentry_plan(entry) is False
    assert "未记录具体回补计划" in html
    assert "仅记录不回补条件" in html
    assert "<b>回补计划</b>" not in html


def test_trade_entry_detail_shows_concrete_reentry_plan() -> None:
    entry = {
        "has_reentry_plan": 1,
        "reentry_plan_text": "回踩买回",
        "reentry_pullback_price": 95,
        "reentry_buy_back_pct_on_pullback": 50,
        "reentry_thesis_invalidation": "thesis broken",
    }

    html = trade_journal._entry_reentry_plan_html(entry)

    assert trade_journal._entry_has_concrete_reentry_plan(entry) is True
    assert "<b>回补计划</b>" in html
    assert "回踩买回" in html
