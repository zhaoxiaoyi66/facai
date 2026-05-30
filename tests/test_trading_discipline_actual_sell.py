from __future__ import annotations

from data.trading_discipline import evaluate_trading_discipline
from ui.trade_journal import (
    _classification_ratio_defaults,
    _discipline_gate_conclusion,
    _discipline_gate_context,
    _discipline_percent,
    _normalized_core_gate_context,
    _pct_point_text,
    _quantity_text,
)


def _evaluate(**overrides):
    params = {
        "symbol": "NVDA",
        "positionClass": "A",
        "corePositionPct": 0.6,
        "tradingPositionPct": 0.4,
        "unrealizedGainPct": 0.5,
        "plannedAction": "sell",
        "plannedSellPct": 0.1,
        "actualSellPct": 0.1,
        "sellReasonType": "macro",
        "thesisBroken": False,
        "positionOverLimit": False,
        "hasReentryPlan": True,
    }
    params.update(overrides)
    return evaluate_trading_discipline(**params)


def test_actual_sell_ratio_blocks_a_class_macro_sell_even_if_plan_is_small() -> None:
    result = _evaluate(actualSellPct=100 / 158)

    assert result.disciplineStatus == "blocked"
    assert result.sellLevel == "L1"
    assert result.actualSellPct == round(100 / 158, 4)
    assert "planned_actual_sell_pct_mismatch" in result.blockers
    assert "a_class_macro_or_emotional_sell_exceeds_20_pct" in result.blockers
    assert "a_class_core_floor_breached" in result.blockers


def test_a_class_macro_sell_allows_at_most_twenty_percent_when_not_thesis_broken() -> None:
    result = _evaluate(plannedAction="trim", plannedSellPct=0.2, actualSellPct=0.2)

    assert result.sellLevel == "L1"
    assert result.maxAllowedSellPct == 0.2
    assert "a_class_macro_or_emotional_sell_exceeds_20_pct" not in result.blockers


def test_planned_sell_ratio_blocks_even_when_actual_quantity_is_safe() -> None:
    result = _evaluate(plannedSellPct=0.5, actualSellPct=30 / 158)

    assert result.disciplineStatus == "blocked"
    assert _discipline_gate_conclusion(result) == "BLOCK"
    assert result.actualSellPct == round(30 / 158, 4)
    assert "planned_actual_sell_pct_mismatch" in result.blockers
    assert "planned_sell_pct_exceeds_sell_level_limit" in result.blockers
    assert "planned_sell_pct_breaches_core_floor" in result.blockers
    assert "a_class_core_floor_breached" not in result.blockers


def test_evaluator_normalizes_core_pct_when_form_submits_sixty() -> None:
    result = _evaluate(
        corePositionPct=60,
        tradingPositionPct=40,
        plannedSellPct=30 / 158,
        actualSellPct=30 / 158,
    )

    assert result.actualSellPct == round(30 / 158, 4)
    assert "a_class_core_floor_breached" not in result.blockers


def test_gate_context_normalizes_core_pct_for_share_floor_display() -> None:
    context = _discipline_gate_context(
        position_class="A",
        current_quantity=158,
        trade_quantity=30,
        planned_sell_pct=10,
        actual_sell_pct=30 / 158,
        core_pct=60,
    )

    assert context["coreRatioMin"] == 0.6
    assert context["coreMinQty"] == 94.8
    assert _pct_point_text(context["coreRatioMin"]) == "60.0%"
    assert _pct_point_text(60) == "60.0%"
    assert _discipline_percent(60) == "60.0%"
    assert _quantity_text(context["coreMinQty"]) == "94.8"
    assert context["coreMinQty"] != 9480


def test_gate_context_tracks_actual_and_planned_sell_paths() -> None:
    context = _discipline_gate_context(
        position_class="A",
        current_quantity=158,
        trade_quantity=30,
        planned_sell_pct=50,
        actual_sell_pct=30 / 158,
        core_pct=60,
    )

    assert context["actualSellPct"] == 30 / 158
    assert context["plannedSellPct"] == 0.5
    assert context["plannedSellQty"] == 79
    assert context["plannedAfterQty"] == 79
    assert context["coreMinQty"] == 94.8
    assert context["actualBreachesCore"] is False
    assert context["plannedBreachesCore"] is True
    assert round(context["plannedBreachQty"], 1) == 15.8


def test_gate_context_falls_back_to_planned_pct_when_current_qty_missing() -> None:
    context = _discipline_gate_context(
        position_class="A",
        current_quantity=None,
        trade_quantity=30,
        planned_sell_pct=50,
        actual_sell_pct=None,
        core_pct=60,
    )

    assert context["usesPlannedFallback"] is True
    assert context["actualSellPct"] == 0.5
    assert context["plannedSellPct"] == 0.5


def test_classification_defaults_normalize_legacy_stock_plan_percent_values() -> None:
    core_pct, trading_pct = _classification_ratio_defaults(
        "A",
        editing_entry=None,
        stock_plan={"core_position_min_pct": 60, "trading_position_max_pct": 40},
    )

    assert core_pct == 0.6
    assert trading_pct == 0.4


def test_gate_render_context_re_normalizes_legacy_core_ratio() -> None:
    context = _normalized_core_gate_context(
        {
            "currentQty": 158,
            "sellQty": 50,
            "coreRatioMin": 60,
            "coreMinQty": 9480,
            "tradableQty": 0,
            "afterSellQty": 108,
            "remainingTradableQty": 0,
            "breachesCore": True,
            "breachQty": 9372,
        }
    )

    assert context["coreRatioMin"] == 0.6
    assert context["coreMinQty"] == 94.8
    assert context["tradableQty"] == 63.2
    assert context["afterSellQty"] == 108
    assert context["breachesCore"] is False
    assert context["breachQty"] == 0
