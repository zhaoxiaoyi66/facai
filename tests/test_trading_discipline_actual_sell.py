from __future__ import annotations

from data.trading_discipline import evaluate_trading_discipline


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

