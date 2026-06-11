from __future__ import annotations

from data.sell_review import evaluate_sell_review_flags, format_sell_review_label, summarize_sell_review_flags


def test_sell_below_target_price_is_flagged() -> None:
    review = evaluate_sell_review_flags(
        {
            "action_type": "sell",
            "sell_price": 90,
            "target_sell_price": 120,
            "position_tier": "B",
        }
    )

    assert review["below_target_sell"] is True
    assert "低于买入目标价卖出" in review["labels"]


def test_sell_inside_or_below_buy_zone_is_flagged() -> None:
    in_buy_zone = evaluate_sell_review_flags(
        {
            "action_type": "trim",
            "sell_price": 100,
            "buy_zone": {"lower": 90, "upper": 110},
        }
    )
    below_buy_zone = evaluate_sell_review_flags(
        {
            "action_type": "trim",
            "sell_price": 80,
            "zone_status": "BELOW_BUY_ZONE",
        }
    )

    assert in_buy_zone["sell_in_buy_zone"] is True
    assert below_buy_zone["sell_in_buy_zone"] is True


def test_sell_review_prefers_sell_context_snapshot_for_buy_zone() -> None:
    review = evaluate_sell_review_flags(
        {
            "action_type": "trim",
            "sell_price": 120,
            "position_tier": "A",
            "raw_entry": {
                "sell_context_snapshot": {
                    "sell_price": 120,
                    "target_sell_price": 150,
                    "position_tier": "A",
                    "zone_status": "IN_BUY_ZONE",
                    "holding_days_reference": 9,
                }
            },
        }
    )

    assert review["below_target_sell"] is True
    assert review["sell_in_buy_zone"] is True
    assert review["a_class_short_hold"] is True
    assert review["suspected_sell_fly"] is True


def test_a_class_short_hold_and_missing_reentry_are_flagged() -> None:
    review = evaluate_sell_review_flags(
        {
            "action_type": "sell",
            "sell_price": 120,
            "position_tier": "A",
            "holding_days": 7,
        }
    )

    assert review["a_class_short_hold"] is True
    assert review["a_class_missing_reentry"] is True
    assert review["suspected_sell_fly"] is True
    assert "核心仓卖出需复盘" in review["labels"]


def test_emotional_sell_is_flagged_from_mood_and_reason_keywords() -> None:
    by_mood = evaluate_sell_review_flags({"action_type": "trim", "sell_price": 100, "decision_mood": "panic_sell"})
    by_text = evaluate_sell_review_flags({"action_type": "trim", "sell_price": 100, "notes": "宏观恐慌，害怕回撤"})

    assert by_mood["emotional_sell"] is True
    assert by_text["emotional_sell"] is True


def test_full_exit_without_review_is_flagged() -> None:
    review = evaluate_sell_review_flags({"action_type": "sell", "sell_price": 100, "notes": ""})

    assert review["full_exit_without_review"] is True
    assert "清仓无复盘" in review["labels"]


def test_planned_c_class_event_exit_is_not_marked_as_sell_fly() -> None:
    review = evaluate_sell_review_flags(
        {
            "action_type": "sell",
            "sell_price": 90,
            "target_sell_price": 120,
            "position_tier": "C",
            "sell_reason_type": "no_post_earnings_reaction",
            "notes": "财报后无反应，按计划退出",
        }
    )

    assert review["below_target_sell"] is False
    assert review["suspected_sell_fly"] is False
    assert review["labels"] == []


def test_old_records_with_missing_fields_do_not_crash() -> None:
    review = evaluate_sell_review_flags({"action_type": "trim"})

    assert review["suspected_sell_fly"] is False
    assert "sell_price" in review["data_missing_fields"]
    assert format_sell_review_label(review) == "数据不足"


def test_sparse_a_class_legacy_record_is_data_insufficient_not_sell_fly() -> None:
    review = evaluate_sell_review_flags({"action_type": "trim", "position_tier": "A"})

    assert review["suspected_sell_fly"] is False
    assert review["labels"] == []
    assert format_sell_review_label(review) == "数据不足"


def test_a_class_below_target_still_marks_sell_fly() -> None:
    review = evaluate_sell_review_flags(
        {
            "action_type": "trim",
            "position_tier": "A",
            "sell_price": 100,
            "target_sell_price": 120,
        }
    )

    assert review["below_target_sell"] is True
    assert review["suspected_sell_fly"] is True
    assert "核心仓卖出需复盘" in review["labels"]


def test_a_class_concrete_reentry_does_not_mark_missing_reentry() -> None:
    review = evaluate_sell_review_flags(
        {
            "action_type": "trim",
            "position_tier": "A",
            "sell_price": 100,
            "target_sell_price": 120,
            "reentry_pullback_price": 90,
            "reentry_buy_back_pct_on_pullback": 50,
            "reentry_thesis_invalidation": "thesis broken",
        }
    )

    assert review["a_class_missing_reentry"] is False
    assert "A类卖出缺少具体回补计划" not in review["labels"]


def test_summary_counts_review_flags() -> None:
    summary = summarize_sell_review_flags(
        [
            {"action_type": "sell", "sell_price": 90, "target_sell_price": 120, "position_tier": "A"},
            {"action_type": "trim", "sell_price": 100, "decision_mood": "panic_sell"},
            {"action_type": "trim", "sell_price": 100, "zone_status": "IN_BUY_ZONE"},
        ]
    )

    assert summary["below_target_sell_count"] == 1
    assert summary["emotional_sell_count"] == 1
    assert summary["buy_zone_sell_count"] == 1
    assert summary["suspected_sell_fly_count"] == 2
    assert summary["a_class_suspected_sell_fly_count"] == 1
