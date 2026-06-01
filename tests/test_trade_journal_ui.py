from __future__ import annotations

from ui import trade_journal


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
    }

    html = trade_journal._entry_reentry_plan_html(entry)

    assert trade_journal._entry_has_concrete_reentry_plan(entry) is True
    assert "<b>回补计划</b>" in html
    assert "回踩买回" in html
