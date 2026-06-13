from __future__ import annotations

from data.action_fusion import (
    ALLOW_SMALL_BUY,
    BLOCK_CHASE,
    BREAKDOWN_REVIEW,
    DATA_INSUFFICIENT,
    EVENT_REVIEW,
    HOLD_NO_ADD,
    WAIT_CONFIRMATION,
    evaluate_action_fusion,
)


def _base(**overrides) -> dict:
    context = {
        "current_price": 100,
        "decision": "WAIT",
        "price_position": "IN_BUY_ZONE",
        "observation_low": 95,
        "observation_high": 105,
        "confirmation_price": 110,
        "invalidation_price": 92,
        "valuation_zone_low": 90,
        "valuation_zone_high": 108,
        "quality_score": 78,
        "valuation_score": 72,
        "volume_price_status": "FORMING",
        "volume_price_score": 52,
        "volume_ratio": 0.72,
        "volume_regime_cn": "缩量",
    }
    context.update(overrides)
    return context


def test_adbe_cheap_but_extreme_gap_down_is_event_review() -> None:
    result = evaluate_action_fusion(
        ticker="ADBE",
        context=_base(
            current_price=202,
            observation_low=192,
            observation_high=203,
            volume_price_status="UNCONFIRMED",
            volume_price_score=27,
            volume_ratio=3.56,
            volume_regime_cn="爆量",
            gap_down=True,
        ),
    )

    assert result.action_code == EVENT_REVIEW
    assert "无确认摊低" in result.buy_plan_cn
    assert result.action_code != ALLOW_SMALL_BUY


def test_mrvl_detached_from_observation_zone_blocks_chase() -> None:
    result = evaluate_action_fusion(
        ticker="MRVL",
        context=_base(
            current_price=118,
            observation_high=110,
            decision="BLOCK_CHASE",
            price_position="IN_CHASE_ZONE",
            volume_price_status="OVEREXTENDED_SUPPORT_READ",
        ),
    )

    assert result.action_code == BLOCK_CHASE
    assert "脱离回踩观察区" in " ".join(result.blocker_bullets_cn)


def test_msft_near_repair_low_forming_waits_confirmation() -> None:
    result = evaluate_action_fusion(
        ticker="MSFT",
        context=_base(
            current_price=390,
            observation_low=377,
            observation_high=415,
            confirmation_price=413,
            volume_price_status="FORMING",
            volume_price_score=46,
            volume_regime_cn="量能普通",
        ),
    )

    assert result.action_code == WAIT_CONFIRMATION
    assert "等待放量站上确认线" in " ".join(result.blocker_bullets_cn)


def test_nvda_overweight_holds_no_add_even_when_acceptance_is_good() -> None:
    result = evaluate_action_fusion(
        ticker="NVDA",
        context=_base(volume_price_status="ACCEPTANCE_CONFIRMED", volume_price_score=84),
        portfolio_context={"current_shares": 100, "portfolio_weight": 42.0, "max_weight": 20.0},
    )

    assert result.action_code == HOLD_NO_ADD
    assert "仓位" in result.position_advice_cn


def test_failed_volume_price_acceptance_triggers_breakdown_review() -> None:
    result = evaluate_action_fusion(
        ticker="FAIL",
        context=_base(volume_price_status="FAILED", volume_price_score=20),
    )

    assert result.action_code == BREAKDOWN_REVIEW
    assert "量价承接失败" in " ".join(result.blocker_bullets_cn)


def test_confirmed_acceptance_with_low_weight_allows_small_buy() -> None:
    result = evaluate_action_fusion(
        ticker="OK",
        context=_base(volume_price_status="ACCEPTANCE_CONFIRMED", volume_price_score=86, volume_ratio=1.4),
        portfolio_context={"portfolio_weight": 1.0, "target_weight": 5.0, "max_weight": 10.0},
    )

    assert result.action_code == ALLOW_SMALL_BUY


def test_critical_data_missing_is_data_insufficient() -> None:
    result = evaluate_action_fusion(
        ticker="MISS",
        context={"critical_data_missing": True, "current_price": None},
    )

    assert result.action_code == DATA_INSUFFICIENT
