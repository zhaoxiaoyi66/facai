from __future__ import annotations

import pandas as pd

from data.volume_price_acceptance import (
    ACCEPTANCE_CONFIRMED,
    FAILED,
    FORMING,
    OVEREXTENDED_SUPPORT_READ,
    UNCONFIRMED,
    evaluate_volume_price_acceptance,
    volume_price_acceptance_hint_html,
)


def _bars(*, close: float, open_: float, high: float, low: float, volume: float | None = 1_000_000) -> pd.DataFrame:
    rows = []
    for idx in range(24):
        rows.append(
            {
                "date": f"2026-05-{idx + 1:02d}",
                "open": 100,
                "high": 105,
                "low": 99,
                "close": 103,
                "volume": 1_000_000,
            }
        )
    rows.append(
        {
            "date": "2026-06-01",
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
    return pd.DataFrame(rows)


def _context(**overrides) -> dict:
    context = {
        "current_price": 103,
        "observation_low": 95,
        "observation_high": 110,
        "support_line": 100,
        "invalid_line": 95,
        "confirm_line": 120,
        "ema20": 104,
        "ema50": 108,
    }
    context.update(overrides)
    return context


def test_shrink_pullback_holding_support_is_forming() -> None:
    snapshot = evaluate_volume_price_acceptance(
        daily_bars=_bars(close=103, open_=104, high=105, low=99, volume=700_000),
        technicals=_context(),
    )

    assert snapshot.volume_price_status == FORMING
    assert "不构成买入确认" in snapshot.acceptance_reason_cn
    assert "缩量" in snapshot.volume_signal_cn
    assert "守住" in snapshot.support_signal_cn or "收回" in snapshot.support_signal_cn


def test_forming_low_score_uses_cautious_label() -> None:
    snapshot = evaluate_volume_price_acceptance(
        daily_bars=_bars(close=100.5, open_=100.8, high=102, low=99, volume=900_000),
        technicals=_context(current_price=100.5, ema20=None, ema50=None),
    )

    assert snapshot.volume_price_status == FORMING
    assert snapshot.volume_price_score < 55
    assert snapshot.status_label == "初步承接，尚未确认"
    assert "不构成买入确认" in snapshot.acceptance_reason_cn


def test_volume_breakout_above_confirm_line_is_confirmed() -> None:
    snapshot = evaluate_volume_price_acceptance(
        daily_bars=_bars(close=122, open_=116, high=123, low=115, volume=1_500_000),
        technicals=_context(current_price=122, observation_high=125, confirm_line=120, ema20=118, ema50=116),
    )

    assert snapshot.volume_price_status == ACCEPTANCE_CONFIRMED
    assert snapshot.volume_ratio and snapshot.volume_ratio >= 1.2
    assert "确认线" in snapshot.confirmation_signal_cn


def test_high_volume_break_below_support_fails() -> None:
    snapshot = evaluate_volume_price_acceptance(
        daily_bars=_bars(close=94, open_=101, high=102, low=93, volume=1_700_000),
        technicals=_context(current_price=94),
    )

    assert snapshot.volume_price_status == FAILED
    assert "失效线" in snapshot.support_signal_cn or "支撑" in snapshot.support_signal_cn


def test_plain_price_inside_observation_zone_is_unconfirmed() -> None:
    snapshot = evaluate_volume_price_acceptance(
        daily_bars=_bars(close=104.2, open_=104.4, high=106, low=103.8, volume=1_000_000),
        technicals=_context(current_price=104.2),
    )

    assert snapshot.volume_price_status == UNCONFIRMED
    assert "量价承接不足" in snapshot.acceptance_reason_cn


def test_price_above_observation_zone_is_overextended_support_read() -> None:
    snapshot = evaluate_volume_price_acceptance(
        daily_bars=_bars(close=118, open_=112, high=119, low=109, volume=1_300_000),
        technicals=_context(current_price=118, observation_high=110, confirm_line=115),
    )

    assert snapshot.volume_price_status == OVEREXTENDED_SUPPORT_READ
    assert "脱离回踩观察区" in snapshot.acceptance_reason_cn


def test_volume_price_acceptance_prefers_upstream_observation_zone() -> None:
    snapshot = evaluate_volume_price_acceptance(
        daily_bars=_bars(close=103, open_=104, high=105, low=99, volume=700_000),
        technicals=_context(
            observation_low=95,
            observation_high=110,
            near_term_repair_zone_low=80,
            near_term_repair_zone_high=90,
        ),
    )

    assert snapshot.volume_price_status == FORMING
    assert snapshot.zone_source == "upstream"


def test_volume_price_acceptance_marks_radar_zone_source_when_no_explicit_zone() -> None:
    snapshot = evaluate_volume_price_acceptance(
        daily_bars=_bars(close=103, open_=104, high=105, low=99, volume=700_000),
        technicals={
            "current_price": 103,
            "near_term_repair_zone_low": 95,
            "near_term_repair_zone_high": 110,
            "support_line": 100,
            "invalid_line": 95,
            "confirm_line": 120,
        },
    )

    assert snapshot.volume_price_status == FORMING
    assert snapshot.zone_source == "radar"


def test_volume_missing_does_not_confirm_acceptance() -> None:
    frame = _bars(close=122, open_=116, high=123, low=115, volume=None)
    frame["volume"] = None

    snapshot = evaluate_volume_price_acceptance(
        daily_bars=frame,
        technicals=_context(current_price=122, observation_high=125, confirm_line=120, ema20=118, ema50=116),
    )

    assert snapshot.volume_price_status != ACCEPTANCE_CONFIRMED
    assert snapshot.volume_signal_cn == "量能缺失"


def test_high_volume_gap_down_low_close_is_not_forming() -> None:
    snapshot = evaluate_volume_price_acceptance(
        daily_bars=_bars(close=96, open_=97, high=99, low=95, volume=1_800_000),
        technicals=_context(current_price=96, support_line=98, invalid_line=94, observation_low=95),
    )

    assert snapshot.volume_price_status in {FAILED, UNCONFIRMED}
    assert snapshot.volume_price_status != FORMING


def test_distribution_day_inside_zone_is_not_optimistic_forming() -> None:
    snapshot = evaluate_volume_price_acceptance(
        daily_bars=_bars(close=101, open_=106, high=107, low=100, volume=1_600_000),
        technicals=_context(current_price=101, support_line=100, invalid_line=96, observation_low=98, observation_high=110),
    )

    assert snapshot.volume_price_status == UNCONFIRMED
    assert "放量派发" in snapshot.risk_deductions


def test_hint_html_is_advisory_only() -> None:
    snapshot = evaluate_volume_price_acceptance(
        daily_bars=_bars(close=103, open_=104, high=105, low=99, volume=700_000),
        technicals=_context(),
    )

    html = volume_price_acceptance_hint_html(snapshot)

    assert "量价承接" in html
    assert "不改变买入权限" in html
