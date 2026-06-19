from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from data.drawdown_profile import build_drawdown_profile, drawdown_profile_summary_text


def _history(closes: list[float], start: str = "2026-01-01") -> pd.DataFrame:
    start_date = datetime.fromisoformat(start)
    return pd.DataFrame(
        {
            "date": [start_date + timedelta(days=index) for index in range(len(closes))],
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1_000_000] * len(closes),
        }
    )


def test_drawdown_profile_calculates_current_max_and_recovered_drawdowns() -> None:
    profile = build_drawdown_profile(
        "NVDA",
        history=_history([100, 90, 105, 110, 88, 95]),
        years=2,
    )

    assert round(profile["current_drawdown_pct"], 4) == -13.6364
    assert round(profile["max_drawdown_2y_pct"], 4) == -20.0
    assert round(profile["max_recovered_drawdown_pct"], 4) == -10.0
    assert len(profile["episodes"]) == 2
    assert profile["episodes"][0]["recovered"] is True
    assert profile["episodes"][1]["recovered"] is False


def test_unrecovered_episode_does_not_count_as_max_effective_drawdown() -> None:
    profile = build_drawdown_profile(
        "NVDA",
        history=_history([100, 96, 101, 120, 84, 90]),
        years=2,
    )

    assert round(profile["max_drawdown_2y_pct"], 2) == -30.0
    assert round(profile["max_recovered_drawdown_pct"], 2) == -4.0


def test_drawdown_state_flags_trend_review_when_current_exceeds_effective_history() -> None:
    profile = build_drawdown_profile(
        "NVDA",
        history=_history([100, 96, 101, 120, 84, 90]),
        years=2,
    )

    assert profile["drawdown_state"] == "极限洗盘"
    assert "近 2 年最大有效回撤仅作背景参考" in profile["drawdown_state_reason"]


def test_new_high_pullback_stats_measure_days_after_new_high() -> None:
    profile = build_drawdown_profile(
        "NVDA",
        history=_history([100, 99, 101, 95, 103, 92, 104, 83, 105]),
        years=2,
    )

    stats = profile["new_high_pullback_stats"]
    assert stats["count_5pct_pullback"] >= 3
    assert stats["median_days_to_5pct_pullback"] == 1
    assert stats["count_10pct_pullback"] >= 2


def test_adjusted_close_is_preferred_for_drawdown_math() -> None:
    frame = _history([100, 90, 120])
    frame["adjusted_close"] = [100, 80, 120]

    profile = build_drawdown_profile("NVDA", history=frame, years=2)

    assert round(profile["max_drawdown_2y_pct"], 2) == -20.0
    assert round(profile["max_recovered_drawdown_pct"], 2) == -20.0
    assert profile["data_quality"]["price_column_used"] == "adjusted_close"
    assert profile["data_quality"]["latest_close_used"] == 120.0
    assert profile["data_quality"]["raw_close"] == 120.0
    assert profile["data_quality"]["adjusted_close"] == 120.0


def test_recent_recovered_drawdowns_are_reported_separately_from_two_year_background() -> None:
    profile = build_drawdown_profile(
        "NVDA",
        history=_history([100, 80, 105, 120, 114, 121, 130, 124, 131], start="2025-01-01"),
        years=2,
        now=datetime.fromisoformat("2025-01-09"),
    )

    assert round(profile["max_recovered_drawdown_pct"], 2) == -20.0
    assert round(profile["recent_6m_max_recovered_drawdown_pct"], 2) == -20.0
    assert round(profile["recent_12m_max_recovered_drawdown_pct"], 2) == -20.0
    assert round(profile["recovered_drawdown_p90_pct"], 2) <= -5.0


def test_state_classification_prefers_recent_thresholds_over_two_year_max_effective() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2024-01-01",
                    "2024-02-01",
                    "2024-03-01",
                    "2025-11-01",
                    "2025-11-10",
                    "2025-11-20",
                    "2025-12-01",
                    "2025-12-15",
                ]
            ),
            "open": [100, 70, 105, 120, 114, 121, 130, 117],
            "high": [100, 70, 105, 120, 114, 121, 130, 117],
            "low": [100, 70, 105, 120, 114, 121, 130, 117],
            "close": [100, 70, 105, 120, 114, 121, 130, 117],
            "volume": [1_000_000] * 8,
        }
    )
    profile = build_drawdown_profile(
        "NVDA",
        history=frame,
        years=2,
        now=datetime.fromisoformat("2025-12-15"),
    )

    assert profile["max_recovered_drawdown_pct"] <= -30
    assert round(profile["recent_6m_max_recovered_drawdown_pct"], 2) == -5.0
    assert profile["drawdown_state"] == "极限洗盘"
    assert "最大有效回撤" in profile["drawdown_state_reason"]


def test_data_quality_flags_adjusted_close_mismatch_as_review() -> None:
    frame = _history([100, 90, 120])
    frame["adjusted_close"] = [10, 9, 12]

    profile = build_drawdown_profile("NVDA", history=frame, years=2)

    assert profile["data_quality"]["data_quality_status"] == "疑似复权口径异常"
    assert profile["drawdown_state"] == "行情口径待复核"
    assert "行情口径待复核" in drawdown_profile_summary_text(profile)


def test_data_quality_flags_directional_price_multiplier_mismatch(monkeypatch) -> None:
    frame = _history([100, 110, 120])

    import data.drawdown_profile as module

    monkeypatch.setattr(module, "build_market_history", lambda symbol: frame)
    monkeypatch.setattr(
        module,
        "build_market_context",
        lambda symbol: {
            "currentPrice": 12.0,
            "priceSource": "quote_snapshot",
            "historyLatestDate": "2026-01-03",
        },
    )

    profile = build_drawdown_profile("TEST", years=2)

    assert profile["data_quality"]["quote_price_ratio"] == 10.0
    assert profile["data_quality"]["data_quality_status"] == "疑似拆股未处理"
    assert profile["suspected_price_multiplier_anomaly"] is True
    assert profile["drawdown_state"] == "行情口径待复核"


def test_data_quality_flags_high_absolute_price_scale_for_manual_review() -> None:
    profile = build_drawdown_profile(
        "LITE",
        history=_history([100, 120, 150, 200, 340], start="2026-01-01"),
        years=2,
    )

    assert profile["data_quality"]["data_quality_status"] == "待人工复核"
    assert profile["data_quality"]["price_scale_review_reason"] == "价格绝对值较高且显著高于近一年中位数"
    assert profile["drawdown_state"] == "行情口径待复核"


def test_data_insufficient_returns_chinese_status_and_no_none_text() -> None:
    profile = build_drawdown_profile("NVDA", history=_history([100]), years=2)
    text = drawdown_profile_summary_text(profile)

    assert profile["drawdown_state"] == "数据不足"
    assert "uptrend_max_recovered_drawdown_pct" in profile
    assert "数据不足" in text
    assert "None" not in text
    assert "drawdown_pct" not in text
