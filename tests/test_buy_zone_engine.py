from __future__ import annotations

from data.buy_zone_engine import (
    ALLOW_SMALL_BUY,
    BLOCK_CHASE,
    DATA_INSUFFICIENT,
    RISK_REVIEW,
    WAIT_CONFIRMATION,
    build_buy_zone_context,
)
from ui import ai_stock_radar as radar_ui


def _base_source(**overrides):
    data = {
        "ticker": "MSFT",
        "current_price": 104,
        "final_score": 84,
        "risk_score": 72,
        "deep_support_zone_low": 90,
        "deep_support_zone_high": 98,
        "effective_technical_entry_zone_low": 100,
        "effective_technical_entry_zone_high": 106,
        "near_term_repair_zone_low": 107,
        "near_term_repair_zone_high": 116,
        "confirmation_price": 118,
        "invalidation_price": 96,
        "chase_above_price": 125,
        "ma20": 108,
        "ma50": 103,
        "ma200": 92,
        "atr_14": 4.2,
        "recent_swing_high": 119,
        "resistance_zone_high": 119,
    }
    data.update(overrides)
    return data


def _volume(**overrides):
    data = {
        "volume_price_status": "FORMING",
        "volume_price_score": 60,
        "volume_ratio": 0.82,
    }
    data.update(overrides)
    return data


def _daily_bars(count: int = 220, *, latest_volume: int = 2_400_000) -> list[dict[str, float]]:
    bars = []
    for index in range(count):
        close = 82.0 + index * 0.1
        bars.append(
            {
                "open": close - 0.25,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": latest_volume if index == count - 1 else 1_800_000 + (index % 7) * 25_000,
            }
        )
    return bars


def test_good_company_in_chase_zone_blocks_chase() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=130, final_score=92),
        volume_snapshot=_volume(volume_price_status="ACCEPTANCE_CONFIRMED", volume_price_score=82, volume_ratio=1.4),
    )

    assert context.primary_zone == "CHASE_RISK"
    assert context.current_action == BLOCK_CHASE
    assert context.action_text == "禁止追高"
    assert "好公司" not in context.zone_selection_reason


def test_pullback_zone_with_shrink_volume_and_good_risk_reward_allows_small_buy() -> None:
    context = build_buy_zone_context(_base_source(current_price=104), volume_snapshot=_volume())

    assert context.primary_zone == "PULLBACK_BUY"
    assert context.current_action == ALLOW_SMALL_BUY
    assert context.setup_score >= 62
    assert context.no_position_action_text == "未持仓：允许小仓观察，后续加仓必须等确认。"


def test_repair_watch_with_unconfirmed_volume_waits_confirmation() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=112),
        volume_snapshot=_volume(volume_price_status="UNCONFIRMED", volume_price_score=42, volume_ratio=1.0),
    )

    assert context.primary_zone == "REPAIR_WATCH"
    assert context.current_action == WAIT_CONFIRMATION
    assert context.action_text == "等待确认"


def test_below_invalidation_enters_risk_review_for_existing_and_no_position() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=94),
        volume_snapshot=_volume(volume_price_status="FAILED", volume_price_score=20, volume_ratio=1.8),
    )

    assert context.primary_zone == "INVALIDATION"
    assert context.current_action == RISK_REVIEW
    assert context.existing_position_action_text == "已有持仓：进入风控复核，暂停新增买入。"
    assert context.no_position_action_text == "未持仓：暂停买入，先复核失效风险。"


def test_low_final_score_does_not_auto_block_good_small_setup() -> None:
    context = build_buy_zone_context(_base_source(final_score=65), volume_snapshot=_volume())

    assert context.current_action == ALLOW_SMALL_BUY
    assert context.core_position_allowed is False
    assert "禁止核心仓买入" in context.core_position_reason
    assert "小仓观察" in context.no_position_action_text


def test_missing_technical_or_volume_data_is_data_insufficient() -> None:
    context = build_buy_zone_context({"ticker": "CRCL", "current_price": 80, "final_score": 90}, volume_snapshot={})

    assert context.current_action == DATA_INSUFFICIENT
    assert context.primary_zone_text == "技术承接数据不足"
    assert "daily_ohlcv" in context.missing_fields
    assert "volume_acceptance" in context.missing_fields
    assert context.support_zone_low is None
    assert context.pullback_zone_low is None


def test_empty_daily_ohlcv_snapshot_does_not_count_as_history() -> None:
    context = build_buy_zone_context(
        {
            "ticker": "CRCL",
            "daily_ohlcv": {"open": None, "high": None, "low": None, "close": None, "volume": None},
            "final_score": 40,
        },
        volume_snapshot={},
    )

    assert context.current_action == DATA_INSUFFICIENT
    assert "daily_ohlcv" in context.missing_fields
    assert context.technical_data_source == ""
    assert context.setup_score == 0


def test_short_daily_ohlcv_does_not_fake_long_window_indicators() -> None:
    context = build_buy_zone_context(
        {
            "ticker": "SHORT",
            "daily_ohlcv": _daily_bars(count=10),
            "final_score": 85,
        },
        volume_snapshot={},
    )

    assert context.current_action == DATA_INSUFFICIENT
    assert "daily_ohlcv_window" in context.missing_fields
    assert "ma20" in context.missing_fields
    assert "ma50" in context.missing_fields
    assert "ma200" in context.missing_fields
    assert "atr_14" in context.missing_fields
    assert "volume_ratio" in context.missing_fields
    assert context.technical_data_source == "daily_ohlcv_partial"
    assert context.setup_score == 0


def test_daily_ohlcv_volume_fallback_prevents_false_data_insufficient() -> None:
    bars = _daily_bars()
    context = build_buy_zone_context(
        {
            "ticker": "NVDA",
            "daily_ohlcv": bars,
            "current_price": bars[-1]["close"],
            "final_score": 88,
        },
        volume_snapshot={},
    )

    assert context.current_action != DATA_INSUFFICIENT
    assert context.setup_score > 0
    assert context.latest_volume == bars[-1]["volume"]
    assert context.avg_volume_20d is not None
    assert context.volume_ratio is not None
    assert context.volume_source == "daily_ohlcv"
    assert "volume_acceptance" not in context.missing_fields


def test_daily_ohlcv_derives_uncomputed_ma_atr_rsi_and_zones() -> None:
    context = build_buy_zone_context(
        {
            "ticker": "MSFT",
            "daily_ohlcv": _daily_bars(),
            "final_score": 86,
        },
        volume_snapshot={},
    )

    assert context.current_action != DATA_INSUFFICIENT
    assert context.technical_structure_score > 0
    assert context.volume_acceptance_score > 0
    assert context.risk_reward_score > 0
    assert context.support_zone_low is not None
    assert context.support_zone_high is not None
    assert context.confirmation_price is not None
    assert context.chase_price is not None


def test_nvda_like_missing_quote_volume_uses_daily_ohlcv_volume() -> None:
    bars = _daily_bars(latest_volume=105_422_923)
    context = build_buy_zone_context(
        {
            "ticker": "NVDA",
            "current_price": 205.19,
            "final_score": 91,
            "deep_support_zone_low": 196.34,
            "deep_support_zone_high": 200.84,
            "effective_technical_entry_zone_low": 194.34,
            "effective_technical_entry_zone_high": 211.82,
            "confirmation_price": 206.59,
            "invalidation_price": 199.34,
            "ma20": 211.15,
            "ma50": 206.59,
            "ma200": 187.24,
            "atr_14": 8.33,
            "rsi_14": 45.2,
            "recent_swing_high": 232.28,
            "resistance_zone_high": 232.28,
            "daily_ohlcv": bars,
        },
        volume_snapshot={},
    )

    assert context.current_action != DATA_INSUFFICIENT
    assert context.setup_score > 0
    assert context.technical_structure_score > 0
    assert context.volume_acceptance_score > 0
    assert context.risk_reward_score > 0
    assert context.latest_volume == 105_422_923
    assert context.volume_source == "daily_ohlcv"


def test_missing_key_technical_acceptance_fields_do_not_generate_buy_zone() -> None:
    for key, expected_missing in (
        ("ma20", "ma20"),
        ("ma50", "ma50"),
        ("ma200", "ma200"),
        ("atr_14", "atr_14"),
    ):
        source = _base_source()
        source.pop(key)

        context = build_buy_zone_context(source, volume_snapshot=_volume())

        assert context.current_action == DATA_INSUFFICIENT
        assert expected_missing in context.missing_fields
        assert context.support_zone_low is None
        assert context.pullback_zone_low is None


def test_missing_resistance_zone_does_not_generate_buy_zone() -> None:
    source = _base_source()
    source.pop("resistance_zone_high")
    source.pop("recent_swing_high")
    source.pop("confirmation_price")

    context = build_buy_zone_context(source, volume_snapshot=_volume())

    assert context.current_action == DATA_INSUFFICIENT
    assert "resistance_zone" in context.missing_fields
    assert context.support_zone_low is None
    assert context.pullback_zone_low is None


def test_report_page_does_not_expose_buy_zone_raw_enum() -> None:
    context = build_buy_zone_context(_base_source(), volume_snapshot=_volume()).to_dict()
    conclusion = radar_ui._trade_conclusion(_base_source(), buy_zone_context=context)
    html = radar_ui._research_header_html(
        _base_source(),
        {},
        {},
        {},
        "观察",
        action_result=None,
        conclusion=conclusion,
    )

    assert "允许小仓观察" in html
    assert "ALLOW_SMALL_BUY" not in html
    assert "PULLBACK_BUY" not in html


def test_buy_zone_context_visible_text_has_no_mojibake() -> None:
    context = build_buy_zone_context(_base_source(), volume_snapshot=_volume())
    visible_text = " ".join(
        [
            context.action_text,
            context.primary_zone_text,
            context.existing_position_action_text,
            context.no_position_action_text,
            context.zone_selection_reason,
        ]
    )

    assert "允许小仓观察" in visible_text
    assert "买区由技术结构" in visible_text
    assert not any(token in visible_text for token in ("鍏", "鎶", "涓", "瓒", "绛"))
