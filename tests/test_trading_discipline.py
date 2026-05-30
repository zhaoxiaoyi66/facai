from __future__ import annotations

from data.trading_discipline import evaluate_trading_discipline, load_trading_discipline_config


def _evaluate(**overrides):
    params = {
        "symbol": "NVDA",
        "positionClass": "A",
        "corePositionPct": 0.7,
        "tradingPositionPct": 0.3,
        "unrealizedGainPct": 0.1,
        "plannedAction": "trim",
        "plannedSellPct": 0.1,
        "sellReasonType": "technical",
        "thesisBroken": False,
        "positionOverLimit": False,
        "hasReentryPlan": True,
    }
    params.update(overrides)
    return evaluate_trading_discipline(**params)


def test_loads_structured_config_from_yaml() -> None:
    config = load_trading_discipline_config()

    assert config["position_classes"]["A"]["core_position_pct"] == 0.7
    assert config["sell_levels"]["L1"]["max_allowed_sell_pct"] == 0.1
    assert config["sell_levels"]["L5"]["can_sell_core"] is True


def test_a_class_cannot_clear_core_without_thesis_break() -> None:
    result = _evaluate(plannedSellPct=1.0, sellReasonType="macro", hasReentryPlan=False)

    assert result.disciplineStatus == "blocked"
    assert "a_class_core_clear_requires_thesis_break" in result.blockers
    assert "macro_risk_cannot_trigger_single_name_exit" in result.blockers


def test_a_class_gain_0_to_25_cannot_sell_core() -> None:
    result = _evaluate(plannedSellPct=0.4, sellReasonType="position_size", positionOverLimit=True)

    assert result.disciplineStatus == "blocked"
    assert "a_class_core_sale_blocked_while_gain_0_to_25_pct" in result.blockers
    assert "sell_level_does_not_allow_core_sale" in result.blockers


def test_technical_trim_requires_reentry_plan() -> None:
    result = _evaluate(hasReentryPlan=False)

    assert result.disciplineStatus == "blocked"
    assert result.sellLevel == "L1"
    assert result.requiresReentryPlan is True
    assert "reentry_plan_required_before_trim_or_sell" in result.blockers


def test_planned_sell_pct_cannot_exceed_level_limit() -> None:
    result = _evaluate(plannedSellPct=0.2, hasReentryPlan=True)

    assert result.disciplineStatus == "blocked"
    assert result.sellLevel == "L1"
    assert result.maxAllowedSellPct == 0.1
    assert "planned_sell_pct_exceeds_sell_level_limit" in result.blockers


def test_position_size_warning_allows_trading_bucket_trim() -> None:
    result = _evaluate(
        positionClass="B",
        corePositionPct=0.5,
        tradingPositionPct=0.5,
        unrealizedGainPct=0.4,
        plannedSellPct=0.2,
        sellReasonType="position_size",
        positionOverLimit=True,
        hasReentryPlan=False,
    )

    assert result.disciplineStatus == "warning"
    assert result.sellLevel == "L2"
    assert result.canSellCore is False
    assert "仓位过重" in result.warnings[0]


def test_thesis_broken_allows_core_sale_with_level_cap() -> None:
    result = _evaluate(
        unrealizedGainPct=0.5,
        plannedSellPct=0.6,
        sellReasonType="thesis_broken",
        thesisBroken=True,
        hasReentryPlan=False,
    )

    assert result.disciplineStatus == "warning"
    assert result.sellLevel == "L4"
    assert result.canSellCore is True
    assert not result.blockers


def test_percent_point_inputs_are_normalized() -> None:
    result = _evaluate(plannedSellPct=10, unrealizedGainPct=10)

    assert result.disciplineStatus == "warning"
    assert result.maxAllowedSellPct == 0.1
    assert result.blockers == []


def test_now_style_risk_blocks_a_class_core_sell_from_anxiety() -> None:
    result = _evaluate(plannedAction="sell", plannedSellPct=0.4, unrealizedGainPct=0.5, decisionMood="anxiety")

    assert result.disciplineStatus == "blocked"
    assert "now_style_error_risk" in result.blockers


def test_now_style_risk_warns_on_small_a_class_trim_from_macro_fear() -> None:
    result = _evaluate(plannedAction="trim", plannedSellPct=0.1, decisionMood="macro_fear")

    assert result.disciplineStatus == "warning"
    assert any("NOW 式错误风险" in warning for warning in result.warnings)
    assert "now_style_error_risk" not in result.blockers


def test_now_style_risk_only_applies_to_a_class_sell_or_trim() -> None:
    b_class = _evaluate(positionClass="B", plannedSellPct=0.1, decisionMood="panic_sell")
    buy = _evaluate(plannedAction="buy", plannedSellPct=0.1, decisionMood="anxiety")
    add = _evaluate(plannedAction="add", plannedSellPct=0.1, decisionMood="anxiety")
    skip = _evaluate(plannedAction="skip", plannedSellPct=0.1, decisionMood="anxiety")

    for result in (b_class, buy, add, skip):
        assert "now_style_error_risk" not in result.blockers
        assert not any("NOW 式错误风险" in warning for warning in result.warnings)
