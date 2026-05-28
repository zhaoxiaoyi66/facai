from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from buy_zone_engine import BuyZoneEstimate, attach_combined_entry, attach_technical_entry, generate_buy_zone
from data.technical_entry_model import (
    HEALTHY_PULLBACK_STATE,
    SHORT_TERM_EXTENDED_STATE,
    TACTICAL_OBSERVATION_STATE,
    TREND_BREAK_REVIEW_STATE,
    build_technical_entry_model,
)


def _history(closes: list[float]) -> pd.DataFrame:
    close = pd.Series(closes, dtype="float")
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=len(closes), freq="B"),
            "close": close,
            "high": close * 1.01,
            "low": close * 0.99,
        }
    )


def _buy_zone(current_zone: str = "tranche_buy", current_price: float = 100) -> BuyZoneEstimate:
    return BuyZoneEstimate(
        symbol="TST",
        modelType="SEMICONDUCTOR",
        currentPrice=current_price,
        noChaseAbove=130,
        fairValueLow=90,
        fairValueHigh=120,
        trancheBuyLow=78,
        trancheBuyHigh=88,
        heavyBuyBelow=65,
        currentZone=current_zone,
        confidence="high",
        method="blended",
        inputsUsed=[],
        keyReasons=[],
        warnings=[],
        createdAt="now",
        nextTriggerPrice=88,
        isValid=True,
        validationErrors=[],
    )


def test_technical_entry_model_marks_short_term_chase_without_overriding_final_decision() -> None:
    closes = [100 + i * 0.15 for i in range(200)] + [135 + i * 3.0 for i in range(20)]
    final_decision = SimpleNamespace(finalAction="等回踩", decisionLane="wait", isActionable=False)

    result = build_technical_entry_model("NVDA", closes[-1], _history(closes), _buy_zone("no_chase", closes[-1]), final_decision)

    assert result.technicalState == SHORT_TERM_EXTENDED_STATE
    assert result.technicalNoChaseAbove == round(closes[-1], 2)
    assert result.technicalEntryPrice is not None
    assert any("短期追高" in reason for reason in result.technicalReasons)
    assert all("价值击球区" not in reason for reason in result.technicalReasons)


def test_technical_entry_model_uses_tactical_observation_when_fair_but_not_tranche() -> None:
    closes = [100 + i * 0.05 for i in range(170)] + [108, 110, 112, 111, 110] * 10
    current_price = 110
    zone = _buy_zone("fair_observation", current_price)

    result = build_technical_entry_model("MSFT", current_price, _history(closes), zone, None)

    assert result.technicalState == TACTICAL_OBSERVATION_STATE
    assert result.technicalEntryPrice is not None
    assert result.technicalEntryPrice < current_price
    assert any("战术观察价" in reason for reason in result.technicalReasons)
    assert all("价值击球区" not in reason for reason in result.technicalReasons)


def test_technical_entry_model_marks_healthy_pullback_near_ma50() -> None:
    closes = [100 + i * 0.2 for i in range(180)] + [136, 138, 140, 142, 144, 146, 148, 147, 146, 145] * 4
    current_price = closes[-1]
    zone = _buy_zone("tranche_buy", current_price)
    final_decision = SimpleNamespace(finalAction="可小仓分批", decisionLane="actionable", isActionable=True)

    result = build_technical_entry_model("MSFT", current_price, _history(closes), zone, final_decision)

    assert result.technicalState == HEALTHY_PULLBACK_STATE
    assert result.technicalEntryPrice is not None
    assert result.technicalReviewPrice is not None
    assert any("健康回踩" in reason for reason in result.technicalReasons)


def test_technical_entry_model_marks_trend_break_and_caps_blocked_decision_confidence() -> None:
    closes = [180 - i * 0.4 for i in range(220)]
    current_price = closes[-1]
    final_decision = SimpleNamespace(finalAction="禁止追高", decisionLane="blocked", isActionable=False)

    result = build_technical_entry_model("ADBE", current_price, _history(closes), _buy_zone("fair_observation", current_price), final_decision)

    assert result.technicalState == TREND_BREAK_REVIEW_STATE
    assert result.technicalTrend in {"broken_trend", "downtrend"}
    assert result.technicalConfidence in {"low", "medium"}
    assert any("最终结论已阻断" in reason for reason in result.technicalReasons)


def test_buy_zone_output_includes_technical_entry_when_history_is_available() -> None:
    closes = [100 + i * 0.12 for i in range(220)]
    history = _history(closes)
    zone = generate_buy_zone(
        "MSFT",
        {
            "price": closes[-1],
            "price_to_fcf": 28,
            "free_cash_flow_yield": 0.036,
            "price_to_sales": 11,
            "revenue_growth": 0.14,
            "gross_margin": 0.68,
            "operating_margin": 0.43,
            "price_history": history,
        },
        {"scoring_model": "MEGA_CAP_PLATFORM", "data_confidence": "high"},
        "MEGA_CAP_PLATFORM",
    )

    assert zone.technicalEntry
    assert zone.technicalEntry["ma50"] is not None
    assert zone.technicalEntry["ma100"] is not None
    assert zone.technicalEntry["technicalEntryPrice"] is not None
    assert zone.technicalEntry["technicalConfidence"] in {"medium", "high"}
    assert zone.combinedEntry
    assert zone.combinedEntry["valuationEntryPrice"] is not None
    assert zone.combinedEntry["combinedTriggerPrice"] <= zone.combinedEntry["valuationEntryPrice"]


def test_buy_zone_output_technical_entry_falls_back_when_history_missing_or_stale() -> None:
    base = {
        "price": 120,
        "price_to_fcf": 28,
        "free_cash_flow_yield": 0.036,
        "price_to_sales": 11,
        "revenue_growth": 0.14,
        "gross_margin": 0.68,
        "operating_margin": 0.43,
    }

    missing = generate_buy_zone("MSFT", base, {"scoring_model": "MEGA_CAP_PLATFORM", "data_confidence": "high"}, "MEGA_CAP_PLATFORM")
    stale = generate_buy_zone(
        "MSFT",
        {**base, "price_history": _history([100 + i * 0.1 for i in range(220)]), "historyStatus": "stale_history"},
        {"scoring_model": "MEGA_CAP_PLATFORM", "data_confidence": "high"},
        "MEGA_CAP_PLATFORM",
    )

    assert missing.technicalEntry["technicalState"] == "unavailable"
    assert missing.technicalEntry["technicalConfidence"] == "low"
    assert missing.technicalEntry["technicalEntryPrice"] is None
    assert stale.technicalEntry["technicalState"] == "unavailable"
    assert stale.technicalEntry["technicalConfidence"] == "low"


def test_attaching_technical_entry_does_not_change_blocked_final_decision_or_buy_zone_action() -> None:
    closes = [100 + i * 0.12 for i in range(220)]
    zone = generate_buy_zone(
        "MSFT",
        {
            "price": closes[-1],
            "price_to_fcf": 28,
            "free_cash_flow_yield": 0.036,
            "price_to_sales": 11,
            "revenue_growth": 0.14,
            "gross_margin": 0.68,
            "operating_margin": 0.43,
        },
        {"scoring_model": "MEGA_CAP_PLATFORM", "data_confidence": "high"},
        "MEGA_CAP_PLATFORM",
    )
    final_decision = SimpleNamespace(finalAction="禁止追高", decisionLane="blocked", isActionable=False)

    updated = attach_technical_entry(zone, _history(closes), final_decision)

    assert updated.action == zone.action
    assert updated.currentZone == zone.currentZone
    assert updated.noChaseAbove == zone.noChaseAbove
    assert updated.technicalEntry["technicalEntryPrice"] is not None
    assert any("最终结论已阻断" in reason for reason in updated.technicalEntry["technicalReasons"])
    assert updated.combinedEntry["combinedTriggerPrice"] is None
    assert "不转买点" in updated.combinedEntry["entryLabel"]


def test_combined_entry_labels_fair_zone_as_observation_not_batting_zone() -> None:
    zone = _buy_zone("fair_observation", 110)
    zone = attach_combined_entry(
        BuyZoneEstimate(
            **{
                **zone.to_dict(),
                "technicalEntry": {
                    "technicalState": "healthy_pullback",
                    "technicalEntryPrice": 105,
                    "technicalReviewPrice": 96,
                    "technicalConfidence": "medium",
                },
            }
        )
    )

    assert zone.combinedEntry["entryLabel"] == "合理观察，未到估值买点"
    assert zone.combinedEntry["combinedTriggerPrice"] == 88
    assert any("未到估值买点" in reason for reason in zone.combinedEntry["entryReasons"])
    assert all("击球区附近" not in reason for reason in zone.combinedEntry["entryReasons"])


def test_combined_entry_marks_trend_break_review() -> None:
    zone = _buy_zone("tranche_buy", 84)
    zone = attach_combined_entry(
        BuyZoneEstimate(
            **{
                **zone.to_dict(),
                "technicalEntry": {
                    "technicalState": "trend_break_review",
                    "technicalEntryPrice": 82,
                    "technicalReviewPrice": 79,
                    "technicalConfidence": "medium",
                },
            }
        )
    )

    assert zone.combinedEntry["entryLabel"] == "趋势破坏，需复核"
    assert zone.combinedEntry["combinedTriggerPrice"] is None
    assert zone.combinedEntry["reviewPrice"] == 79


def test_combined_entry_keeps_first_buy_distance_out_of_near_label() -> None:
    zone = _buy_zone("fair_observation", 120)
    zone = attach_combined_entry(
        BuyZoneEstimate(
            **{
                **zone.to_dict(),
                "fairValueLow": 100,
                "fairValueHigh": 130,
                "trancheBuyLow": 70,
                "trancheBuyHigh": 88,
                "nextTriggerPrice": 88,
                "technicalEntry": {
                    "technicalState": "neutral",
                    "technicalEntryPrice": 95,
                    "technicalReviewPrice": 82,
                    "technicalConfidence": "medium",
                },
            }
        )
    )

    assert zone.combinedEntry["entryLabel"] == "合理观察，未到估值买点"
    assert any("不得显示接近买点" in reason for reason in zone.combinedEntry["entryReasons"])
