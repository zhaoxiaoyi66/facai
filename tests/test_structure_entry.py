from __future__ import annotations

from data.structure_entry import (
    DATA_MISSING,
    DIP_ONLY,
    STRUCTURE_BROKEN,
    STRUCTURE_CONFIRMED,
    STRUCTURE_FORMING,
    THESIS_BROKEN,
    THESIS_INTACT,
    evaluate_structure_entry,
)


def _technicals(**overrides):
    values = {
        "price": 100,
        "close": 100,
        "high": 110,
        "low": 90,
        "ema20": 101,
        "ema50": 99,
        "ema200": 90,
        "recent_swing_low": 99,
        "atr14": 3,
        "volume_trend": 0,
    }
    values.update(overrides)
    return values


def test_price_decline_without_buyer_support_is_dip_only() -> None:
    advisor = evaluate_structure_entry(
        ticker="NOW",
        technicals=_technicals(ema20=110, ema50=120, ema200=105, recent_swing_low=115),
        decline_reason="unknown",
        thesis_status=THESIS_INTACT,
        relative_strength_status="相对中性",
    )

    assert advisor.structure_status == DIP_ONLY
    assert "结构证据还不完整" in advisor.action_hint
    assert "价格跌破关键支撑" in " ".join(advisor.structure_warnings)


def test_broken_thesis_marks_structure_broken() -> None:
    advisor = evaluate_structure_entry(
        ticker="ADBE",
        technicals=_technicals(price=105, close=105, high=106, low=96, ema20=101, ema50=99, ema200=90, volume_trend=0.25),
        decline_reason="macro",
        thesis_status=THESIS_BROKEN,
        relative_strength_status="强于 SPY/QQQ",
    )

    assert advisor.structure_status == STRUCTURE_BROKEN
    assert advisor.structure_score >= 60
    assert "主线逻辑已破坏" in " ".join(advisor.structure_warnings)


def test_macro_pullback_with_support_held_is_structure_forming() -> None:
    advisor = evaluate_structure_entry(
        ticker="AVGO",
        technicals=_technicals(volume_trend=-0.3),
        decline_reason="macro",
        thesis_status=THESIS_INTACT,
        relative_strength_status="弱于 SPY/QQQ",
    )

    assert advisor.structure_status == STRUCTURE_FORMING
    assert 60 <= advisor.structure_score < 80
    assert advisor.support_confirmation in {"有初步承接", "承接确认"}


def test_support_close_relative_strength_and_volume_confirm_structure() -> None:
    advisor = evaluate_structure_entry(
        ticker="NVDA",
        technicals=_technicals(price=106, close=106, high=107, low=96, ema20=102, ema50=100, ema200=92, volume_trend=0.25),
        decline_reason="sector",
        thesis_status=THESIS_INTACT,
        relative_strength_status="强于 SPY/QQQ",
    )

    assert advisor.structure_status == STRUCTURE_CONFIRMED
    assert advisor.structure_score >= 80
    assert advisor.close_confirmation == "收盘确认"


def test_missing_price_or_kline_data_returns_data_missing() -> None:
    no_price = evaluate_structure_entry(ticker="CRM", technicals={"ema20": 100}, decline_reason="unknown")
    no_technicals = evaluate_structure_entry(ticker="CRM", technicals={"price": 100}, decline_reason="unknown")

    assert no_price.structure_status == DATA_MISSING
    assert no_technicals.structure_status == DATA_MISSING


def test_unknown_thesis_with_weak_technicals_returns_data_missing() -> None:
    advisor = evaluate_structure_entry(
        ticker="ISRG",
        technicals=_technicals(
            price=100,
            close=100,
            high=104,
            low=90,
            ema20=120,
            ema50=130,
            ema200=101,
            recent_swing_low=95,
            volume_trend=-0.3,
        ),
        decline_reason="unknown",
        thesis_status="UNKNOWN",
        relative_strength_status="弱于 SPY/QQQ",
    )

    assert advisor.structure_status == DATA_MISSING
    assert "主线状态未维护" in " ".join(advisor.structure_warnings)
    assert "不能判定结构破坏" in " ".join(advisor.structure_warnings)


def test_clear_support_breakdown_still_marks_structure_broken() -> None:
    advisor = evaluate_structure_entry(
        ticker="ADBE",
        technicals=_technicals(
            price=100,
            close=100,
            high=120,
            low=99,
            ema20=120,
            ema50=130,
            ema200=110,
            recent_swing_low=115,
            volume_trend=-0.3,
        ),
        decline_reason="macro",
        thesis_status=THESIS_INTACT,
        relative_strength_status="弱于 SPY/QQQ",
    )

    assert advisor.structure_status == STRUCTURE_BROKEN
    assert "价格跌破关键支撑" in " ".join(advisor.structure_warnings)


def test_unknown_decline_reason_does_not_leak_internal_code() -> None:
    advisor = evaluate_structure_entry(
        ticker="CRM",
        technicals=_technicals(),
        decline_reason="NEW_DECLINE_REASON",
        thesis_status=THESIS_INTACT,
    )

    reasons = " ".join(advisor.structure_reasons)
    assert "下跌原因：未知。" in reasons
    assert "NEW_DECLINE_REASON" not in reasons
