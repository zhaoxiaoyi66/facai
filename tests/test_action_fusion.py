from __future__ import annotations

import pandas as pd

from data.action_fusion import (
    ALLOW_SMALL_BUY,
    BLOCK_CHASE,
    BREAKDOWN_REVIEW,
    CHASE_BLOCKED,
    DATA_INSUFFICIENT,
    EVENT_REVIEW_ONLY,
    EVENT_REVIEW,
    HOLD_NO_ADD,
    LEFT_ADD_ALLOWED,
    LEFT_NOT_ALLOWED,
    LEFT_PROBE_ALLOWED,
    LEFT_WAIT_BETTER_PRICE,
    POSITION_LIMITED,
    WAIT_CONFIRMATION,
    action_fusion_card_html,
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
    assert result.action_cn == "追高风险提示"
    assert "禁止追高" not in result.action_cn
    assert "不建议追高" in result.buy_plan_cn
    assert "脱离回踩观察区" in " ".join(result.advisory_warnings_cn)


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
    assert "等待放量站上确认线" in " ".join(result.advisory_warnings_cn)


def test_nvda_overweight_holds_no_add_even_when_acceptance_is_good() -> None:
    result = evaluate_action_fusion(
        ticker="NVDA",
        context=_base(volume_price_status="ACCEPTANCE_CONFIRMED", volume_price_score=84),
        portfolio_context={"current_shares": 100, "portfolio_weight": 42.0, "max_weight": 20.0},
    )

    assert result.action_code == HOLD_NO_ADD
    assert "仓位" in result.position_advice_cn


def test_nvda_near_target_waits_confirmation_without_adding() -> None:
    result = evaluate_action_fusion(
        ticker="NVDA",
        context=_base(volume_price_status="FORMING", volume_price_score=48),
        portfolio_context={
            "current_shares": 360,
            "portfolio_weight": 42.2,
            "target_weight": 45.0,
            "max_weight": 52.0,
            "role": "ai_core",
        },
    )

    assert result.action_code == WAIT_CONFIRMATION
    assert result.position_status_cn == "接近目标"
    assert result.position_action_cn == "只能等待"
    assert "等待确认，不追不加" in " ".join(result.advisory_warnings_cn)


def test_nvda_at_max_weight_holds_no_add() -> None:
    result = evaluate_action_fusion(
        ticker="NVDA",
        context=_base(volume_price_status="ACCEPTANCE_CONFIRMED", volume_price_score=86),
        portfolio_context={"portfolio_weight": 52.0, "target_weight": 45.0, "max_weight": 52.0},
    )

    assert result.action_code == HOLD_NO_ADD
    assert result.position_status_cn == "已达上限"
    assert "达到/超过上限" in " ".join(result.advisory_warnings_cn)


def test_repair_role_adds_low_ceiling_advisory() -> None:
    result = evaluate_action_fusion(
        ticker="ADBE",
        context=_base(volume_price_status="ACCEPTANCE_CONFIRMED", volume_price_score=88),
        portfolio_context={"portfolio_weight": 1.0, "target_weight": 3.0, "max_weight": 6.0, "role": "ai_software_repair"},
    )

    assert result.action_code == ALLOW_SMALL_BUY
    assert "修复仓以估值修复为主" in " ".join(result.advisory_warnings_cn)
    assert "核心仓" not in result.buy_plan_cn


def test_event_trade_role_stays_small_advisory() -> None:
    result = evaluate_action_fusion(
        ticker="CRCL",
        context=_base(volume_price_status="ACCEPTANCE_CONFIRMED", volume_price_score=88),
        portfolio_context={"portfolio_weight": 0.5, "target_weight": 2.0, "max_weight": 4.0, "role": "event_trade"},
    )

    assert result.action_code == ALLOW_SMALL_BUY
    assert "事件仓仅限小仓" in " ".join(result.advisory_warnings_cn)
    assert "核心仓" not in result.buy_plan_cn


def test_satellite_max_weight_blocks_add() -> None:
    result = evaluate_action_fusion(
        ticker="GLW",
        context=_base(volume_price_status="ACCEPTANCE_CONFIRMED", volume_price_score=88),
        portfolio_context={"portfolio_weight": 5.0, "target_weight": 3.0, "max_weight": 5.0, "role": "ai_infra_satellite"},
    )

    assert result.action_code == HOLD_NO_ADD
    assert result.position_status_cn == "已达上限"
    assert "卫星/观察仓" in " ".join(result.advisory_warnings_cn)


def test_failed_volume_price_acceptance_triggers_breakdown_review() -> None:
    result = evaluate_action_fusion(
        ticker="FAIL",
        context=_base(volume_price_status="FAILED", volume_price_score=20),
    )

    assert result.action_code == BREAKDOWN_REVIEW
    assert "量价承接失败" in " ".join(result.advisory_warnings_cn)


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


def test_action_fusion_warnings_are_advisory_not_hard_blocks() -> None:
    source = _base(
        current_price=118,
        observation_high=110,
        decision="ALLOW_BUY",
        allowed_add_pct=12.5,
        volume_price_status="OVEREXTENDED_SUPPORT_READ",
    )

    result = evaluate_action_fusion(ticker="ADV", context=source)
    payload = result.to_dict()

    assert result.action_code == BLOCK_CHASE
    assert source["decision"] == "ALLOW_BUY"
    assert source["allowed_add_pct"] == 12.5
    assert "advisory_warnings_cn" in payload
    assert "blocker_bullets_cn" not in payload
    assert "hard_blocked" not in payload


def test_left_side_observation_forming_low_weight_allows_probe() -> None:
    result = evaluate_action_fusion(
        ticker="MSFT",
        context=_base(current_price=100, volume_price_status="FORMING", volume_price_score=52),
        portfolio_context={"portfolio_weight": 0.0, "target_weight": 8.0, "max_weight": 12.0, "role": "ai_platform_core"},
    )

    assert result.action_code == WAIT_CONFIRMATION
    assert result.left_side_plan["action_code"] == LEFT_PROBE_ALLOWED
    assert result.left_side_allowed is True
    assert "20%-30%" in result.left_probe_size_cn


def test_left_side_existing_position_below_target_allows_small_add() -> None:
    result = evaluate_action_fusion(
        ticker="NOW",
        context=_base(volume_price_status="FORMING", volume_price_score=52),
        portfolio_context={"current_shares": 10, "portfolio_weight": 5.8, "target_weight": 12.0, "max_weight": 16.0, "role": "ai_software_core"},
    )

    assert result.left_side_plan["action_code"] == LEFT_ADD_ALLOWED
    assert result.left_side_allowed is True
    assert "不能一次打满" in result.left_side_warning_cn
    assert result.left_side_plan["left_cap_ratio"] == 0.75


def test_left_side_near_limit_is_position_limited() -> None:
    result = evaluate_action_fusion(
        ticker="NVDA",
        context=_base(volume_price_status="FORMING", volume_price_score=52),
        portfolio_context={"current_shares": 360, "portfolio_weight": 42.2, "target_weight": 45.0, "max_weight": 52.0, "role": "ai_core"},
    )

    assert result.left_side_plan["action_code"] == POSITION_LIMITED
    assert result.left_side_allowed is False
    assert "不继续加仓" in result.left_side_warning_cn


def test_left_side_existing_position_above_left_cap_waits_confirmation() -> None:
    result = evaluate_action_fusion(
        ticker="NOW",
        context=_base(volume_price_status="FORMING", volume_price_score=52),
        portfolio_context={"current_shares": 10, "portfolio_weight": 9.1, "target_weight": 12.0, "max_weight": 16.0, "role": "ai_software_core"},
    )

    assert result.left_side_plan["action_code"] == LEFT_WAIT_BETTER_PRICE
    assert result.left_side_allowed is False
    assert "75%" in result.left_side_warning_cn


def test_left_side_gap_down_event_review_only() -> None:
    result = evaluate_action_fusion(
        ticker="ADBE",
        context=_base(volume_price_status="UNCONFIRMED", volume_ratio=2.8, gap_down=True),
        portfolio_context={"portfolio_weight": 1.0, "target_weight": 3.0, "max_weight": 6.0, "role": "repair"},
    )

    assert result.action_code == EVENT_REVIEW
    assert result.left_side_plan["action_code"] == EVENT_REVIEW_ONLY
    assert result.left_side_allowed is False


def test_left_side_overextended_blocks_chase() -> None:
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
    assert result.left_side_plan["action_code"] == CHASE_BLOCKED
    assert result.left_side_allowed is False


def test_left_side_data_insufficient_is_not_allowed() -> None:
    result = evaluate_action_fusion(
        ticker="CRCL",
        context={"critical_data_missing": True, "current_price": None},
    )

    assert result.action_code == DATA_INSUFFICIENT
    assert result.left_side_plan["action_code"] == LEFT_NOT_ALLOWED
    assert result.left_side_allowed is False


def test_action_fusion_card_uses_advisory_wording() -> None:
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

    html = action_fusion_card_html(result)

    assert "待确认事项" in html
    assert "阻碍" not in html
    assert "blocker" not in html.lower()
    assert "左侧计划" in html
    assert "左侧也不追" in html


def test_action_fusion_card_displays_position_constraint() -> None:
    result = evaluate_action_fusion(
        ticker="NVDA",
        context=_base(volume_price_status="FORMING", volume_price_score=48),
        portfolio_context={"portfolio_weight": 42.2, "target_weight": 45.0, "max_weight": 52.0, "role": "ai_core"},
    )

    html = action_fusion_card_html(result)

    assert "仓位约束" in html
    assert "当前 42.2%" in html
    assert "目标 45.0%" in html
    assert "上限 52.0%" in html


def test_dashboard_drawer_missing_action_fusion_shows_safe_fallback() -> None:
    from ui.dashboard_drawer import _drawer_action_fusion_card_html

    html = _drawer_action_fusion_card_html(pd.Series({"symbol": "GLW"}))

    assert "数据待补" in html
    assert "暂无系统建议" in html
    assert "本地缓存缺失" in html
    assert "blocker" not in html.lower()


def test_action_fusion_visible_text_has_no_mojibake() -> None:
    result = evaluate_action_fusion(
        ticker="MSFT",
        context=_base(volume_price_status="FORMING", volume_price_score=52),
        portfolio_context={"portfolio_weight": 0.0, "target_weight": 8.0, "max_weight": 12.0, "role": "ai_platform_core"},
    )
    html = action_fusion_card_html(result)
    visible_text = " ".join(
        [
            result.action_cn,
            result.buy_plan_cn,
            result.position_advice_cn,
            result.left_side_warning_cn,
            html,
        ]
    )

    assert "系统建议" in visible_text
    assert "左侧计划" in visible_text
    assert not any(token in visible_text for token in ("\u934f", "\u93b6", "\u6d93", "\u74d2", "\u7edb"))


def test_action_fusion_prefers_nested_buy_zone_context() -> None:
    result = evaluate_action_fusion(
        ticker="MRVL",
        context=_base(
            current_price=118,
            decision="ALLOW_BUY",
            price_position="IN_BUY_ZONE",
            observation_low=90,
            observation_high=130,
            volume_price_status="FORMING",
            buy_zone_context={
                "current_action": "BLOCK_CHASE",
                "pullback_zone_low": 95,
                "pullback_zone_high": 105,
                "confirmation_price": 110,
                "invalidation_price": 92,
            },
        ),
    )

    assert result.action_code == BLOCK_CHASE
    assert result.watch_levels["observation_high"] == 105
    assert "不建议追高" in result.buy_plan_cn
