from __future__ import annotations

import json
import sqlite3
import inspect
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from data.action_fusion import evaluate_action_fusion
from data import ai_stock_radar as radar_data
from data import dashboard_row_builder
from data.ai_stock_radar import (
    RadarScores,
    RadarZone,
    build_ai_stock_radar_list_row,
    build_ai_stock_radar_report,
    build_technical_entry_zone,
    normalize_radar_inputs,
)
from data.buy_setup_quality import setup_quality_note, setup_quality_status
from data.sector_localization import get_ticker_research_track, localize_sector
from data.trade_gate import buy_gate_entry_fields, evaluate_buy_gate
from ui import ai_stock_radar as radar_ui
from ui import watchlist as watchlist_ui
from ui.ai_stock_radar import select_radar_symbols


NOW = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


def test_setup_quality_status_uses_setup_score_as_single_canonical_score() -> None:
    assert setup_quality_status(82) == "HIGH_QUALITY_SETUP"
    assert setup_quality_status(72) == "STARTER_REASONABLE"
    assert setup_quality_status(64) == "SETUP_WATCH"
    assert setup_quality_status(55) == "WEAK_SETUP"
    assert setup_quality_status(42) == "HIGH_RISK_SETUP"
    assert setup_quality_status(None) == "DATA_INSUFFICIENT"

    note = setup_quality_note(82, volume_acceptance_score=42)
    assert "Setup 综合 82" in note
    assert "量价未确认 / 承接不足" in note


def _db(tmpdir: str) -> Path:
    return Path(tmpdir) / "radar.sqlite"


def _insert_quote(path: Path, symbol: str, price: float, fetched_at: str = "2026-05-30T11:00:00+00:00") -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quote_snapshots (
                ticker TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO quote_snapshots VALUES (?, ?, ?)",
            (symbol.upper(), json.dumps({"current_price": price}), fetched_at),
        )
        conn.commit()


def _scores(**overrides: float) -> RadarScores:
    values = {
        "final_score": 82,
        "quality_score": 80,
        "growth_score": 75,
        "valuation_score": 60,
        "technical_score": 65,
        "risk_score": 70,
    }
    values.update(overrides)
    return RadarScores(**values)


def _buy_zone() -> RadarZone:
    return RadarZone(lower=90, upper=100, label="discipline buy zone")


def _watch_zone() -> RadarZone:
    return RadarZone(lower=100, upper=115, label="watch zone")


def test_dashboard_active_plan_target_maps_to_manual_rr_target() -> None:
    result = dashboard_row_builder._manual_target_fields(
        {"plan_status": "active", "target_sell_price": 130},
        price=100,
    )

    assert result == {
        "manual_target_price": 130.0,
        "manual_target_source": "stock_plan.target_sell_price",
    }


def test_dashboard_inactive_or_low_plan_target_is_ignored() -> None:
    assert dashboard_row_builder._manual_target_fields({"plan_status": "cancelled", "target_sell_price": 130}, 100) == {}
    assert dashboard_row_builder._manual_target_fields({"plan_status": "expired", "target_sell_price": 130}, 100) == {}
    assert dashboard_row_builder._manual_target_fields({"plan_status": "active", "target_sell_price": 99}, 100) == {}


def test_report_active_plan_target_maps_to_manual_rr_target(monkeypatch) -> None:
    class DummyPlanStore:
        def get_plan(self, _symbol: str) -> dict:
            return {"plan_status": "active", "target_sell_price": 130}

    monkeypatch.setattr(radar_ui, "StockPlanStore", DummyPlanStore)

    result = radar_ui._report_manual_target_fields("NOW", {"current_price": 100})

    assert result == {
        "manual_target_price": 130.0,
        "manual_target_source": "stock_plan.target_sell_price",
    }


def test_radar_summary_localizes_backend_reasons() -> None:
    summary = radar_data._summary(
        "MSFT",
        "WAIT",
        0,
        ["current price is below the discipline buy zone lower bound; review fundamentals"],
    )

    assert "当前价格低于主击球区下沿" in summary
    assert "current price" not in summary
    assert "review fundamentals" not in summary


def test_radar_summary_allow_buy_is_chinese() -> None:
    summary = radar_data._summary("NVDA", "ALLOW_BUY", 3, [])

    assert "主击球区" in summary
    assert "price is inside" not in summary


def test_report_summary_uses_action_fusion_portfolio_context_for_holding_status() -> None:
    action_result = evaluate_action_fusion(
        ticker="NOW",
        context={
            "ticker": "NOW",
            "current_price": 103,
            "observation_low": 98,
            "observation_high": 108,
            "quality_score": 82,
            "valuation_score": 65,
            "volume_price_status": "FORMING",
            "volume_price_score": 58,
        },
        portfolio_context={
            "current_shares": 100,
            "portfolio_weight": 5.8,
            "target_weight": 12.0,
            "max_weight": 16.0,
            "portfolio_updated_at": "2026-06-12T17:09:22+00:00",
            "role": "ai_software_core",
        },
    )

    html = radar_ui._executive_summary_card_html(
        {
            "ticker": "NOW",
            "company_name": "ServiceNow",
            "decision": "WAIT",
            "final_score": 82,
            "data_confidence": "高",
        },
        {},
        {},
        {"ticker": "NOW"},
        action_result,
    )

    assert "我的持仓" in html
    assert "已有持仓" in html
    assert "组合仓位" in html
    assert "5.8%" in html
    assert "未持仓 / 仅研究观察" not in html
    assert action_result.portfolio_updated_at == "2026-06-12T17:09:22+00:00"


def test_report_header_uses_localized_investment_conclusion() -> None:
    action_result = evaluate_action_fusion(
        ticker="GLW",
        context={
            "ticker": "GLW",
            "current_price": 55,
            "observation_low": 45,
            "observation_high": 50,
            "volume_price_status": "OVEREXTENDED_SUPPORT_READ",
            "quality_score": 76,
        },
        portfolio_context={},
    )

    html = radar_ui._research_header_html(
        {
            "ticker": "GLW",
            "company_name": "Corning",
            "current_price": 55,
            "decision": "BLOCK_CHASE",
            "chase_above_price": 50,
            "confirmation_price": 52,
            "invalidation_price": 44,
        },
        {},
        {},
        {},
        "追高风险",
        pd.DataFrame(),
        action_result,
    )

    assert "投资结论" not in html
    assert "追高风险" in html
    assert "追高风险区" in html
    assert "当前区间" in html
    assert "$55.00" in html
    assert "BLOCK_CHASE" not in html
    assert "OVEREXTENDED_SUPPORT_READ" not in html


def test_report_summary_is_conclusion_first_and_shows_position_actions() -> None:
    action_result = evaluate_action_fusion(
        ticker="MSFT",
        context={
            "ticker": "MSFT",
            "current_price": 390,
            "observation_low": 380,
            "observation_high": 410,
            "confirmation_price": 415,
            "invalidation_price": 370,
            "quality_score": 88,
            "volume_price_status": "FORMING",
            "volume_price_score": 52,
        },
        portfolio_context={
            "current_shares": 12,
            "avg_cost": 320,
            "market_value": 4680,
            "unrealized_pnl": 840,
            "unrealized_pnl_pct": 21.9,
            "portfolio_weight": 0.058,
            "target_weight": 0.08,
            "max_weight": 0.12,
        },
    )

    html = radar_ui._executive_summary_card_html(
        {
            "ticker": "MSFT",
            "company_name": "Microsoft",
            "current_price": 390,
            "decision": "WAIT",
            "confirmation_price": 415,
            "invalidation_price": 370,
            "near_term_repair_zone_low": 380,
            "near_term_repair_zone_high": 410,
            "quality_score": 88,
            "valuation_score": 60,
            "technical_score": 65,
            "risk_score": 70,
        },
        {},
        {},
        {"ticker": "MSFT"},
        action_result,
    )

    assert "决策摘要" in html
    assert "技术回踩带" in html
    assert "MSFT 当前价 $390.00，位于技术回踩带" in html
    assert "我的持仓：12 股｜成本 $320.00｜浮盈亏 +$840.00 / +21.9%" in html
    assert "已有持仓动作" in html
    assert "无持仓动作" not in html


def test_report_summary_surfaces_acceptance_state() -> None:
    action_result = evaluate_action_fusion(
        ticker="NOW",
        context={
            "ticker": "NOW",
            "current_price": 104.15,
            "observation_low": 97.5,
            "observation_high": 108.0,
            "quality_score": 82,
            "volume_price_status": "FORMING",
            "volume_price_score": 48,
        },
        portfolio_context={"current_shares": 160},
    )
    buy_zone_context = {
        "current_action": "WAIT_CONFIRMATION",
        "primary_zone": "PULLBACK_UPPER_WATCH",
        "primary_zone_text": "买区上沿 / 修复观察区",
        "current_subzone": "ACCEPTANCE_OBSERVATION_ZONE",
        "current_price": 104.15,
        "left_probe_zone_high": 99.28,
        "observe_zone_high": 104.65,
        "acceptance_state": "WEAK_ACCEPTANCE",
        "acceptance_state_text": "承接不足",
        "entry_quality": "EDGE_OBSERVE",
        "confirmation_score": 48,
        "volume_acceptance_score": 48,
        "risk_reward_score": 82,
        "confirmation_price": 105.12,
        "current_add_limit_percent": 0,
    }
    conclusion = {
        "action_text": "持有观察 / 当前不建议新增",
        "buy_zone_display": {
            "acceptance_state_text": "承接不足",
            "entry_quality_text": "边缘观察",
            "current_subzone_display_text": "承接观察区上沿",
            "momentum_note": "RSI 74，价格贴近布林上轨，追高风险升高。",
        },
    }

    html = radar_ui._executive_summary_card_html(
        {"ticker": "NOW", "current_price": 104.15, "decision": "WAIT"},
        {},
        {},
        {"ticker": "NOW"},
        action_result,
        portfolio_context={"has_position": True, "shares": 160, "action_for_existing_position": "持有观察 / 当前不建议新增"},
        conclusion=conclusion,
        buy_zone_context=buy_zone_context,
    )

    assert "当前动作" in html
    assert "持仓与额度" in html
    assert "下一步" in html
    assert "承接不足" in html
    assert "边缘观察" in html
    assert "动能辅助：RSI 74，价格贴近布林上轨，追高风险升高。" in html
    assert "当前价不新增" in html
    assert "未设置计划上限" in html
    assert "组合持仓页可设置" in html
    assert "赔率较好，但承接不足。当前属于高赔率观察，不是立即买入。" in html
    assert html.count("<li>") == 4
    assert "若无持仓" not in html


def test_report_position_panel_for_no_position_only_shows_no_position_action() -> None:
    action_result = evaluate_action_fusion(
        ticker="GLW",
        context={
            "ticker": "GLW",
            "current_price": 55,
            "observation_low": 45,
            "observation_high": 50,
            "volume_price_status": "OVEREXTENDED_SUPPORT_READ",
            "quality_score": 76,
        },
        portfolio_context={},
    )

    html = radar_ui._executive_summary_card_html(
        {
            "ticker": "GLW",
            "company_name": "Corning",
            "current_price": 55,
            "decision": "BLOCK_CHASE",
            "chase_above_price": 50,
            "quality_score": 76,
        },
        {},
        {},
        {"ticker": "GLW"},
        action_result,
    )

    assert "我的持仓：未持仓" in html
    assert "无持仓动作" in html
    assert "已有持仓动作" not in html
    assert "追高风险区" in html
    assert "不追买，等待回到观察区" in html


def test_report_data_health_marks_missing_sources_as_temporarily_missing() -> None:
    html = radar_ui._data_health_card_html(
        {
            "quote_updated_at": None,
            "financials_updated_at": "2026-05-30T11:00:00+00:00",
            "score_updated_at": None,
            "portfolio_updated_at": None,
            "missing_fields": ["current_price", "daily_bars"],
            "stale_fields": [],
        }
    )

    assert "暂缺" in html
    assert "当前价格" in html
    assert "历史K线" in html
    assert "current_price" not in html
    assert "daily_bars" not in html


def test_report_data_health_filters_gaps_already_resolved_on_page() -> None:
    data_health = radar_ui._data_health_context(
        {
            "ticker": "NOW",
            "current_price": 102.15,
            "final_score": 64.1,
            "debug": {
                "data_missing_fields": [
                    "current_price",
                    "final_score",
                    "portfolio_position",
                    "daily_bars",
                ]
            },
        },
        {"currentPrice": 102.15},
        {},
        {},
        {
            "has_position": True,
            "portfolio_updated_at": "2026-06-12T17:09:22+00:00",
        },
    )

    assert "current_price" not in data_health["missing_fields"]
    assert "final_score" not in data_health["missing_fields"]
    assert "portfolio_position" not in data_health["missing_fields"]
    assert "daily_bars" in data_health["missing_fields"]
    assert data_health["portfolio_updated_at"] == "2026-06-12T17:09:22+00:00"


def test_report_data_health_uses_unified_price_source_label() -> None:
    data_health = radar_ui._data_health_context(
        {"ticker": "NOW", "current_price": 102.37, "final_score": 64.1},
        {
            "currentPrice": 102.37,
            "price_session": "LAST_CLOSE",
            "price_as_of": "2026-06-16",
            "last_close_synced_at": "2026-06-17T12:00:00+00:00",
        },
        {},
        {},
        {"has_position": False},
    )
    html = radar_ui._data_health_card_html(data_health)

    assert data_health["price_is_close_or_intraday"] == "昨夜收盘 06/16"
    assert "价格口径" in html
    assert "昨夜收盘 06/16" in html


def test_report_data_health_classifies_critical_optional_and_not_applicable_fields() -> None:
    report = {
        "ticker": "MSFT",
        "current_price": 412,
        "final_score": 76,
        "daily_ohlcv": {"close": 412, "volume": 20_000_000},
        "ma20": 405,
        "ma50": 398,
        "ma200": 360,
        "avg_volume_20d": 18_000_000,
        "volume_ratio": 1.11,
        "atr_14": 7.5,
        "rsi_14": 58,
        "swing_high": 430,
        "swing_low": 390,
        "support_zone_low": 390,
        "support_zone_high": 400,
        "resistance_zone_low": 428,
        "distance_to_invalidation": 4.2,
        "distance_to_resistance": 3.8,
        "reward_risk_ratio": 1.4,
        "debug": {
            "data_missing_fields": [
                "daily_bars",
                "analyst_targets",
                "shares",
                "avg_cost",
                "portfolio_updated_at",
            ]
        },
    }

    data_health = radar_ui._data_health_context(
        report,
        {"currentPrice": 412, "volume": 20_000_000},
        {},
        {},
        {"has_position": False},
    )
    html = radar_ui._data_health_card_html(data_health)

    assert data_health["health_level"] == "低"
    assert "daily_bars" in data_health["critical_missing_fields"]
    assert "analyst_targets" in data_health["optional_missing_fields"]
    assert "shares" in data_health["not_applicable_fields"]
    assert "avg_cost" not in data_health["missing_fields"]
    assert "portfolio_updated_at" not in data_health["missing_fields"]
    assert "数据健康等级" in html
    assert "关键缺口" in html
    assert "可选缺口" in html
    assert "查看字段明细" in html


def test_report_technical_context_derives_buy_zone_inputs_from_cached_rows() -> None:
    technicals = radar_ui._enrich_technical_context(
        "MSFT",
        {
            "current_price": 104,
            "previous_close": 100,
            "volume": 2_000_000,
            "avg_volume_20d": 1_000_000,
            "ma20": 108,
            "ma50": 102,
            "ma200": 90,
            "atr_14": 4,
            "rsi_14": 56,
            "recent_swing_low": 96,
            "recent_swing_high": 116,
            "confirmation_price": 116,
            "invalidation_price": 94,
        },
        {},
        {},
        {},
    )

    assert technicals["daily_ohlcv"]["close"] == 104
    assert technicals["daily_ohlcv"]["volume"] == 2_000_000
    assert round(technicals["day_change_pct"], 1) == 4.0
    assert technicals["volume_ratio"] == 2.0
    assert technicals["support_zone_low"] == 90
    assert technicals["support_zone_high"] == 102
    assert technicals["resistance_zone_high"] == 116
    assert round(technicals["distance_to_invalidation"], 1) == 10.6
    assert round(technicals["reward_risk_ratio"], 1) == 1.2


def test_report_volume_snapshot_backfills_technicals_and_health_gaps() -> None:
    technicals = radar_ui._apply_volume_snapshot_to_technicals(
        {"daily_ohlcv": {"close": 205.19}},
        {
            "latest_volume": 105_422_923,
            "volume_ma20": 175_713_022.4,
            "volume_ratio": 0.6,
            "volume_source": "daily_cache",
        },
    )
    report = {
        "ticker": "NVDA",
        "current_price": 205.19,
        "final_score": 91,
        "daily_ohlcv": technicals["daily_ohlcv"],
        "latest_volume": technicals["latest_volume"],
        "avg_volume_20d": technicals["avg_volume_20d"],
        "volume_ratio": technicals["volume_ratio"],
        "effective_technical_entry_zone_low": 194.34,
        "effective_technical_entry_zone_high": 211.82,
        "debug": {"data_missing_fields": ["buy_zone.upper", "volume_acceptance", "volume_ratio"]},
    }
    buy_zone_context = {
        "current_action": "WAIT_PULLBACK",
        "missing_fields": [],
        "pullback_zone_low": 194.34,
        "pullback_zone_high": 211.82,
        "latest_volume": 105_422_923,
        "volume_ratio": 0.6,
    }

    data_health = radar_ui._data_health_context(
        report,
        {},
        {},
        {},
        {"has_position": False},
        buy_zone_context,
    )

    assert technicals["daily_ohlcv"]["volume"] == 105_422_923
    assert technicals["volume_source"] == "daily_cache"
    assert "volume_acceptance" not in data_health["missing_fields"]
    assert "volume_ratio" not in data_health["missing_fields"]
    assert "buy_zone.upper" not in data_health["missing_fields"]


def test_list_row_volume_snapshot_clears_volume_data_gaps() -> None:
    history = pd.DataFrame(
        [
            {"date": f"2026-05-{day:02d}", "open": 100, "high": 106, "low": 96, "close": 102, "volume": 1_000_000}
            for day in range(1, 21)
        ]
        + [{"date": "2026-05-21", "open": 102, "high": 108, "low": 101, "close": 104, "volume": 1_500_000}]
    )
    technicals = {
        "current_price": 104,
        "support_watch_zone_low": 96,
        "support_watch_zone_high": 108,
        "confirmation_price": 106,
        "invalidation_price": 95,
        "ema20": 103,
        "ema50": 101,
        "atr14": 5,
    }
    volume_snapshot = radar_ui._volume_price_acceptance_snapshot(
        {"ticker": "NOW", "current_price": 104},
        technicals,
        {},
        history,
    )
    row = {
        "ticker": "NOW",
        "current_price": 104,
        "data_status": "OK",
        "missing_entry_fields": ["volume_acceptance", "volume_ratio"],
        "buy_zone_context": {"current_action": "WAIT_CONFIRMATION"},
    }

    radar_ui._apply_volume_snapshot_to_list_row(row, volume_snapshot)

    assert row["volume_ratio"] is not None
    assert row["volume_price_status"] != "DATA_MISSING"
    assert row["missing_entry_fields"] == []
    assert radar_ui._research_queue_view(row)["data_quality_text"] != "量价数据缺口"


def test_list_buy_zone_context_rebuilds_stale_context_when_volume_snapshot_is_available() -> None:
    history = pd.DataFrame(
        [
            {"date": f"2026-05-{day:02d}", "open": 100, "high": 106, "low": 96, "close": 102, "volume": 1_000_000}
            for day in range(1, 21)
        ]
        + [{"date": "2026-05-21", "open": 102, "high": 108, "low": 101, "close": 104, "volume": 1_500_000}]
    )
    technicals = {
        "current_price": 104,
        "support_watch_zone_low": 96,
        "support_watch_zone_high": 108,
        "technical_pullback_zone_low": 96,
        "technical_pullback_zone_high": 108,
        "confirmation_price": 106,
        "invalidation_price": 95,
        "ema20": 103,
        "ema50": 101,
        "ema200": 90,
        "atr14": 5,
        "recent_swing_low": 96,
        "recent_swing_high": 112,
    }
    volume_snapshot = radar_ui._volume_price_acceptance_snapshot(
        {"ticker": "NOW", "current_price": 104},
        technicals,
        {},
        history,
    )

    context = radar_ui._list_buy_zone_context(
        {"ticker": "NOW", "current_price": 104, "buy_zone_context": {"current_action": "WAIT_CONFIRMATION"}},
        {},
        {},
        technicals,
        history=history,
        volume_snapshot=volume_snapshot,
    )

    assert context.get("volume_ratio") is not None


def test_report_localizes_short_history_missing_field() -> None:
    assert radar_ui._field_display_label("daily_ohlcv_window") == "日线样本不足"


def test_report_roe_falls_back_to_net_income_over_equity() -> None:
    assert radar_ui._roe_value({"net_income": 25_000_000, "total_equity": 100_000_000}) == 0.25


def test_report_localizes_backend_english_copy() -> None:
    html = radar_ui._text_card_html(
        "核心风险",
        [
            "Revenue growth",
            "Gross Margin / unit economics",
            "EV/Sales growth",
            "Price vs 52-week high",
            "Negative FCF",
            "High leverage",
            "FCF trajectory",
            "final score below 70; core position is not allowed.",
        ],
        limit=10,
    )

    assert "收入高增长" in html
    assert "毛利率 / 单位经济性" in html
    assert "EV/Sales 估值扩张" in html
    assert "距离52周高点回撤" in html
    assert "自由现金流为负" in html
    assert "杠杆偏高" in html
    assert "自由现金流路径不确定" in html
    assert "公司综合评分低于70，仅作为风险背景；买入时机仍以 setup_score 与量价承接复核" in html
    assert "不建议作为核心仓" not in html
    assert "禁止核心仓买入" not in html
    assert "Revenue growth" not in html
    assert "final score below 70" not in html


def test_report_uses_setup_quality_copy_instead_of_core_position_copy() -> None:
    report = {"ticker": "ADBE", "final_score": 64, "risk_score": 62}
    buy_zone_context = {
        "core_position_allowed": False,
        "setup_score": 64,
        "volume_acceptance_score": 48,
        "core_position_reason": "旧字段应被展示层忽略",
    }

    core_notice = radar_ui._core_position_notice(report, buy_zone_context)
    risk_notice = radar_ui._risk_gate_notice(report)

    assert core_notice == "观察级 Setup：综合分 < 70"
    assert "买入时机仍以 setup_score" in risk_notice
    assert "非核心仓候选" not in core_notice
    assert "系统不建议作为核心仓" not in risk_notice
    assert "禁止核心仓买入" not in core_notice
    assert "禁止核心仓买入" not in risk_notice


def test_report_confirmation_is_review_trigger_not_buy_signal() -> None:
    conclusion = radar_ui._trade_conclusion(
        {
            "ticker": "CRWV",
            "current_price": 101,
            "confirmation_price": 104.93,
            "invalidation_price": 92,
            "final_score": 64,
            "risk_score": 40,
        }
    )

    assert conclusion["confirm_text"] == "重新评估线：放量站上 $104.93 后重新评估，不等于直接买入"
    assert conclusion["buy_premise_text"] == "重新评估线 + 综合评分回到70以上 + 风险复核完成"


def test_report_range_chart_explains_primary_and_reference_zones() -> None:
    report = {
        "ticker": "MSFT",
        "current_price": 98,
        "near_term_repair_zone_low": 90,
        "near_term_repair_zone_high": 105,
        "valuation_reference_zone_low": 95,
        "valuation_reference_zone_high": 110,
        "confirmation_price": 112,
        "invalidation_price": 85,
        "final_score": 72,
        "risk_score": 68,
    }
    conclusion = radar_ui._trade_conclusion(report)
    html = radar_ui._range_chart_html(report, conclusion)

    assert conclusion["primary_zone_text"] == "近端修复观察区"
    assert "价格行动地图" in html
    assert "技术回踩带" in html
    assert "重新评估线用于重新判断，不等于直接买入" in html
    assert "参考区间：近端修复观察区" in html


def test_list_core_status_prefers_unified_buy_zone_context() -> None:
    assert radar_ui._core_status({"decision": "ALLOW_BUY", "buy_zone_context": {"current_action": "BLOCK_CHASE"}}) == "防追高"
    assert radar_ui._core_status({"decision": "BLOCK_CHASE", "buy_zone_context": {"current_action": "ALLOW_SMALL_BUY"}}) == "可买"
    assert radar_ui._core_status({"buy_zone_context": {"current_action": "DATA_INSUFFICIENT"}}) == "数据不足"
    assert radar_ui._core_status({"decision": "AVOID", "buy_zone_context": {"current_action": "DATA_MISSING"}}) == "数据不足"
    assert radar_ui._core_status({"decision": "AVOID", "buy_zone_context": {"current_action": "NO_BUY_ZONE"}}) == "未生成买区"
    assert radar_ui._core_status({"decision": "AVOID"}) == "回避"


def test_decision_does_not_turn_missing_buy_zone_into_avoid() -> None:
    for action in ("DATA_INSUFFICIENT", "DATA_MISSING", "NO_BUY_ZONE", "ZONE_MISSING"):
        decision = radar_data.calculate_decision(
            current_price=100,
            scores=_scores(risk_score=10),
            buy_zone=_buy_zone(),
            chase_zone=_chase_zone(),
            data_status="OK",
            block_reasons=[],
            buy_zone_context={"current_action": action},
        )

        assert decision == "DATA_MISSING"


def test_watchlist_prefers_canonical_buy_zone_status_over_avoid() -> None:
    html = watchlist_ui._decision_badge_html(
        {
            "decision": "AVOID",
            "buy_zone_context": {"current_action": "DATA_INSUFFICIENT"},
        }
    )

    assert "数据不足" in html
    assert "回避" not in html


def test_watchlist_held_missing_data_shows_pause_add_instead_of_avoid() -> None:
    html = watchlist_ui._decision_badge_html(
        {
            "decision": "AVOID",
            "buy_zone_context": {"current_action": "DATA_INSUFFICIENT"},
        },
        held=True,
    )

    assert "暂停加仓" in html
    assert "数据不足" in html
    assert "回避" not in html


def test_watchlist_star_sort_keeps_original_order_within_groups() -> None:
    entries = [{"ticker": "NOW"}, {"ticker": "NVDA"}, {"ticker": "ADBE"}, {"ticker": "CRM"}]
    marks = {
        "NVDA": {"is_starred": True},
        "CRM": {"is_starred": True},
    }

    sorted_entries = watchlist_ui._sort_watchlist_entries_by_star(entries, marks)

    assert [entry["ticker"] for entry in sorted_entries] == ["NVDA", "CRM", "NOW", "ADBE"]


def test_list_row_shows_buy_point_gap_without_avoid_semantics() -> None:
    row = {
        "ticker": "MSFT",
        "company_name": "Microsoft Corporation",
        "sector": "云平台｜AI软件",
        "current_price": 390,
        "decision": "AVOID",
        "data_status": "OK",
        "buy_zone_context": {
            "current_action": "DATA_INSUFFICIENT",
            "missing_fields": ["daily_ohlcv", "volume_ratio", "support_zone"],
        },
    }

    html = radar_ui._list_row_html(row, "")

    assert "数据不足" in html
    assert "日线 OHLCV" in html
    assert "量比" in html
    assert "支撑区" in html
    assert "技术数据缺口" in html
    assert "回避" not in html
    assert 'class="missing"' in html


def test_list_row_distinguishes_missing_buy_zone_from_avoid() -> None:
    row = {
        "ticker": "NOW",
        "decision": "AVOID",
        "data_status": "OK",
        "buy_zone_context": {
            "current_action": "ZONE_MISSING",
            "missing_fields": ["support_zone", "resistance_zone"],
        },
    }

    html = radar_ui._list_row_html(row, "")

    assert "买区未生成" in html
    assert "支撑区" in html
    assert "压力区" in html
    assert "回避" not in html


def test_research_queue_data_insufficient_is_not_wait_or_avoid() -> None:
    view = radar_ui._research_queue_view(
        {
            "ticker": "IBM",
            "decision": "AVOID",
            "buy_zone_context": {
                "current_action": "DATA_INSUFFICIENT",
                "missing_fields": ["daily_ohlcv", "volume_ratio"],
            },
        }
    )

    assert view["status_text"] == "数据不足"
    assert view["status_text"] not in {"等待", "回避"}
    assert "数据不足" in view["summary_text"]


def test_research_queue_near_buy_zone_status() -> None:
    view = radar_ui._research_queue_view(
        {
            "ticker": "NVDA",
            "current_price": 102,
            "buy_zone_display": {"action_code": "ALLOW_SMALL_BUY"},
            "buy_zone_context": {
                "current_action": "ALLOW_SMALL_BUY",
                "left_probe_zone_low": 100,
                "left_probe_zone_high": 105,
                "setup_score": 73,
            },
        }
    )

    assert view["status_text"] == "接近买区"
    assert view["distance_text"] == "区内"
    assert view["priority_score"] >= 90


def test_research_queue_wait_pullback_when_above_zone() -> None:
    view = radar_ui._research_queue_view(
        {
            "ticker": "MSFT",
            "current_price": 112,
            "buy_zone_display": {"action_code": "WAIT_PULLBACK"},
            "buy_zone_context": {
                "current_action": "WAIT_PULLBACK",
                "left_probe_zone_low": 100,
                "left_probe_zone_high": 105,
                "setup_score": 64,
            },
        }
    )

    assert view["status_text"] == "等待回落"
    assert "等回落" in view["summary_text"]


def test_research_queue_far_above_zone_is_low_priority() -> None:
    view = radar_ui._research_queue_view(
        {
            "ticker": "ADBE",
            "current_price": 150,
            "buy_zone_display": {"action_code": "WAIT_PULLBACK"},
            "buy_zone_context": {
                "current_action": "WAIT_PULLBACK",
                "left_probe_zone_low": 100,
                "left_probe_zone_high": 105,
                "setup_score": 60,
            },
        }
    )

    assert view["status_text"] == "低优先级"
    assert "低优先级" in view["summary_text"]


def test_research_queue_wait_confirmation_with_confirm_line() -> None:
    view = radar_ui._research_queue_view(
        {
            "ticker": "NOW",
            "current_price": 104,
            "buy_zone_display": {"action_code": "WAIT_CONFIRMATION"},
            "buy_zone_context": {
                "current_action": "WAIT_CONFIRMATION",
                "left_probe_zone_low": 100,
                "left_probe_zone_high": 105,
                "confirmation_price": 110,
                "setup_score": 66,
            },
        }
    )

    assert view["status_text"] == "等待确认"
    assert view["next_trigger_text"] == "站上 $110.00 后重新评估"


def test_research_priority_score_sorts_queue() -> None:
    rows = [
        {
            "ticker": "LOW",
            "current_price": 150,
            "buy_zone_display": {"action_code": "WAIT_PULLBACK"},
            "buy_zone_context": {"current_action": "WAIT_PULLBACK", "left_probe_zone_low": 100, "left_probe_zone_high": 105},
        },
        {
            "ticker": "NEAR",
            "current_price": 102,
            "buy_zone_display": {"action_code": "ALLOW_SMALL_BUY"},
            "buy_zone_context": {"current_action": "ALLOW_SMALL_BUY", "left_probe_zone_low": 100, "left_probe_zone_high": 105, "setup_score": 75},
        },
        {
            "ticker": "DATA",
            "buy_zone_display": {"action_code": "DATA_INSUFFICIENT"},
            "buy_zone_context": {"current_action": "DATA_INSUFFICIENT", "missing_fields": ["daily_ohlcv"]},
        },
    ]

    sorted_rows = radar_ui._sort_rows(rows)

    assert sorted_rows[0]["ticker"] == "NEAR"
    assert radar_ui._research_queue_view(sorted_rows[1])["status_text"] == "数据不足"
    assert radar_ui._research_queue_view(sorted_rows[-1])["status_text"] == "低优先级"


def test_research_queue_legacy_row_shows_stale_format() -> None:
    view = radar_ui._research_queue_view(
        {
            "ticker": "OLD",
            "entry_display_label": "回踩观察",
            "primary_entry_interpretation": "旧格式",
        }
    )

    assert view["status_text"] == "数据不足"
    assert view["data_quality_text"] == "旧格式待刷新"
    assert "旧格式待刷新" in view["summary_text"]


def test_research_queue_uses_buy_zone_display_as_primary_source() -> None:
    view = radar_ui._research_queue_view(
        {
            "ticker": "NOW",
            "buy_zone_display": {"action_code": "DATA_INSUFFICIENT"},
            "buy_zone_context": {"current_action": "ALLOW_SMALL_BUY", "left_probe_zone_low": 100, "left_probe_zone_high": 105},
        }
    )

    assert view["status_text"] == "数据不足"


def test_research_queue_labels_volume_only_gap_as_volume_price_gap() -> None:
    view = radar_ui._research_queue_view(
        {
            "ticker": "NOW",
            "current_price": 104.15,
            "data_status": "OK",
            "buy_zone_context": {
                "current_action": "WAIT_CONFIRMATION",
                "left_probe_zone_low": 97.5,
                "left_probe_zone_high": 99.32,
                "setup_score": 62,
                "missing_fields": [],
            },
            "missing_entry_fields": ["volume_acceptance", "volume_ratio"],
            "ema20": 107.37,
            "ema50": 105.12,
            "atr14": 8.56,
        }
    )

    assert view["data_quality_text"] == "量价数据缺口"
    assert "技术数据缺口" not in view["data_quality_text"]
    assert radar_ui._field_list_display(["volume_acceptance", "volume_ratio"]) == "量价承接、量比"


def test_research_queue_keeps_structural_missing_fields_as_technical_gap() -> None:
    view = radar_ui._research_queue_view(
        {
            "ticker": "DATA",
            "buy_zone_display": {"action_code": "DATA_INSUFFICIENT"},
            "buy_zone_context": {
                "current_action": "DATA_INSUFFICIENT",
                "missing_fields": ["daily_ohlcv", "volume_ratio"],
            },
        }
    )

    assert view["data_quality_text"] == "技术数据缺口"


def test_list_buy_zone_context_prefers_cached_canonical_context() -> None:
    cached_context = {
        "current_action": "WAIT_PULLBACK",
        "setup_score": 69.5,
        "missing_fields": [],
        "volume_ratio": 0.64,
    }
    transient_context = {
        "current_action": "DATA_INSUFFICIENT",
        "setup_score": 0,
        "missing_fields": ["volume_ratio", "volume_acceptance"],
    }

    context = radar_ui._list_buy_zone_context(
        {"buy_zone_context": transient_context},
        {"buyZoneContext": cached_context},
        {},
        {},
    )

    assert context == cached_context
    assert radar_ui._core_status({"buy_zone_context": context}) == "观察"


def test_crwv_report_uses_ai_cloud_infra_display_framework() -> None:
    report = {
        "ticker": "CRWV",
        "current_price": 101,
        "business_model_type": "AI_CLOUD_INFRA",
        "revenue_growth": 0.62,
    }
    html = radar_ui._ai_cloud_infra_card_html({"ticker": "CRWV"}, {}, report)

    assert "AI 云基础设施专项框架" in html
    assert "AI云基础设施" in html
    assert "收入高增长" in html
    assert "62.0%" in html
    assert "收入积压 / RPO" in html
    assert "暂缺" in html
    assert "AI_CLOUD_INFRA" not in html


def test_crwv_data_health_tracks_ai_cloud_missing_fields_and_sources() -> None:
    data_health = radar_ui._data_health_context(
        {"ticker": "CRWV", "current_price": 101, "business_model_type": "AI_CLOUD_INFRA", "final_score": 63},
        {"currentPrice": 101, "priceSource": "quote_snapshot", "fetchedAt": "2026-06-13T10:00:00+00:00"},
        {"market_cap": 2_000_000_000, "financial_source": "fundamental_cache"},
        {"ticker": "CRWV"},
        {"has_position": False},
    )
    html = radar_ui._data_health_card_html(data_health)

    assert data_health["quote_source"] == "报价缓存"
    assert data_health["market_cap_source"] == "基本面缓存"
    assert data_health["volume_ratio_formula"] == "成交量 / 20日均量"
    assert "revenue_backlog" in data_health["missing_fields"]
    assert "收入积压 / RPO" in html
    assert "报价缓存" in html
    assert "量比公式" in html


def test_crwv_catalysts_include_index_and_financing_events() -> None:
    html = radar_ui._catalyst_card_html({"ticker": "CRWV"}, {}, {"ticker": "CRWV"})

    assert "后续催化 / 风险事项" in html
    assert "纳入 Nasdaq-100" in html
    assert "不代表基本面自动改善" in html
    assert "Senior Notes 融资" in html


def _chase_zone() -> RadarZone:
    return RadarZone(lower=120, label="chase zone")


def _cached_snapshot(**overrides: float | str) -> dict:
    values: dict = {
        "company_name": "Nvidia",
        "forward_pe": 18,
        "enterprise_to_revenue": 6,
        "free_cash_flow_yield": 0.08,
        "fcf_margin": 0.22,
        "gross_margin": 0.72,
        "net_margin": 0.28,
        "roe": 0.35,
        "revenue_growth": 0.25,
        "current_ratio": 2.0,
        "debt": 10,
        "cash": 20,
    }
    values.update(overrides)
    return values


def _cached_technicals(**overrides: float) -> dict:
    values = {
        "price": 95,
        "fifty_two_week_high": 200,
        "fifty_two_week_low": 100,
        "ema20": 94,
        "ema50": 92,
        "ema100": 88,
        "ema200": 80,
        "atr14": 4,
        "recent_swing_low": 88,
        "recent_swing_high": 108,
        "recent_breakout_level": 100,
        "ema50_slope_20d_pct": 1.0,
        "ema200_slope_20d_pct": 0.5,
        "rsi14": 48,
        "gain_20d_pct": 4,
        "gain_60d_pct": 8,
        "volume_price_status": "FORMING",
        "volume_price_score": 60,
        "volume_ratio": 1.1,
        "chase_price": 130,
    }
    values.update(overrides)
    return values


def test_price_inside_discipline_buy_zone_can_allow_buy() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 88.3)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=88.3),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "ALLOW_BUY"
        assert report.allowed_add_pct > 0
        assert report.buy_zone_context["current_action"] == "ALLOW_SMALL_BUY"
        assert report.to_dict()["ticker"] == "NVDA"


def test_ai_radar_list_page_is_research_entry_not_backend_table() -> None:
    source = inspect.getsource(radar_ui._render_list)
    row_source = inspect.getsource(radar_ui._list_row_html)
    href_source = inspect.getsource(radar_ui._report_view_href)

    assert "Radar 研究入口" in source
    assert "Radar" in source
    assert "研究优先级" in source
    assert "距买区" in source
    assert "下一触发" in source
    assert "刷新 / 重建买区上下文" in source
    assert ">查看</a>" in row_source
    assert "#radar-report" in href_source
    assert "Block reasons" not in source
    assert "allowed_add_pct" not in source
    assert "<th>总分</th>" not in source


def test_ai_radar_render_uses_dedicated_report_view_not_list_append() -> None:
    source = inspect.getsource(radar_ui.render)

    assert '_selected_radar_view()' in source
    assert 'if view == "report":' in source
    assert "_render_report_view(selected, tickers, perf)" in source
    assert '_render_list(tickers, "", source)' in source
    assert "_render_report(selected)" not in source


def test_ai_radar_report_html_can_skip_appendix_for_fast_first_paint() -> None:
    report = {
        "ticker": "NOW",
        "company_name": "ServiceNow",
        "current_price": 102.15,
        "decision": "WAIT",
        "final_score": 64.1,
        "data_status": "OK",
    }

    html = radar_ui._report_html(report, {}, {}, {}, {}, pd.DataFrame(), include_appendix=False)

    assert "投资结论" not in html
    assert "决策摘要" in html
    assert "附录数据" not in html
    assert "核心财务摘要" not in html


def test_ai_radar_appendix_html_is_separate_from_core_report() -> None:
    report = {
        "ticker": "NOW",
        "company_name": "ServiceNow",
        "current_price": 102.15,
        "decision": "WAIT",
        "final_score": 64.1,
        "data_status": "OK",
    }

    html = radar_ui._report_appendix_html(
        report,
        {},
        {"forward_pe": 30},
        {},
        {},
        pd.DataFrame(),
        {"missing_fields": [], "stale_fields": []},
    )

    assert "附录数据" in html
    assert "核心财务摘要" in html
    assert "数据健康" in html


def test_ai_radar_runtime_cache_reports_hit_and_reuses_loader() -> None:
    radar_ui._clear_report_runtime_cache()
    calls = {"count": 0}

    def loader() -> dict:
        calls["count"] += 1
        return {"value": calls["count"]}

    first_perf = radar_ui.PerfProbe()
    second_perf = radar_ui.PerfProbe()

    first = radar_ui._runtime_cached(("unit", "NOW"), 60, loader, first_perf, "unit stage")
    second = radar_ui._runtime_cached(("unit", "NOW"), 60, loader, second_perf, "unit stage")

    assert first == {"value": 1}
    assert second == {"value": 1}
    assert calls["count"] == 1
    assert first_perf.stages[-1].cache_hit is False
    assert second_perf.stages[-1].cache_hit is True
    radar_ui._clear_report_runtime_cache()


def test_ai_radar_list_links_open_report_view() -> None:
    row = {
        "ticker": "MSFT",
        "company_name": "Microsoft Corporation",
        "sector": "云平台｜AI软件",
        "current_price": 390,
        "data_status": "OK",
    }

    html = radar_ui._list_row_html(row, "")

    assert "view=report" in html
    assert "ticker=MSFT" in html
    assert "#radar-report" in html


def test_ai_radar_report_view_has_return_link_and_missing_state() -> None:
    toolbar = radar_ui._report_view_toolbar_html("MSFT", "Microsoft Corporation", "刚刚")
    missing = radar_ui._report_not_found_html("ZZZZ")

    assert "返回 Radar 列表" in toolbar
    assert "view=list" in toolbar
    assert "MSFT" in toolbar
    assert "Microsoft Corporation" in toolbar
    assert "未找到 ZZZZ 的股票研报" in missing
    assert "返回 Radar 列表" in missing


def test_ai_radar_query_params_support_deep_link_and_list_return() -> None:
    with patch.object(radar_ui.st, "query_params", {"ticker": "MSFT", "radarFilter": "near"}):
        assert radar_ui._selected_radar_view() == "report"
        assert radar_ui._selected_symbol(["MSFT"]) == "MSFT"
        assert "view=list" in radar_ui._list_view_href()
        assert "radarFilter=near" in radar_ui._list_view_href()

    with patch.object(radar_ui.st, "query_params", {"page": "ai-radar", "view": "report", "ticker": "NVDA"}):
        assert radar_ui._selected_radar_view() == "report"
        assert radar_ui._selected_symbol(["NVDA"]) == "NVDA"

    with patch.object(radar_ui.st, "query_params", {"view": "list", "ticker": "MSFT"}):
        assert radar_ui._selected_radar_view() == "list"


def test_ai_radar_report_html_uses_research_report_sections() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)
        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            company_name="Nvidia",
            scores=_scores(),
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        ).to_dict()

    html = radar_ui._report_html(report, {}, _cached_snapshot(), _cached_technicals(), {}, pd.DataFrame())

    assert "AI 股票雷达研究" in html
    assert "决策摘要" in html
    assert "技术回踩带" in html
    assert "价格行动地图" in html
    assert "买入时机评分卡" in html
    assert "看多逻辑" in html
    assert "核心风险" in html
    assert "关键监控点" in html
    assert "关键指标（今日）" in html
    assert "核心财务摘要" in html
    assert "市场表现" in html
    assert "数据完整度" in html
    assert "后续催化 / 风险事项" in html
    assert "附录" in html
    assert html.count("决策摘要") == 1
    assert 'class="ai-radar-folded-section"' in html
    assert "触发条件" in html
    assert "交易含义" in html
    assert "AI Stock Radar Research" not in html
    assert "Research notes" not in html
    assert "Segment strength" not in html
    assert "Buyback discipline" not in html
    assert "这是评分因子，不是原始字段" not in html
    assert "N/A" not in html
    assert "是否允许新增" not in html
    assert "阻止原因" not in html
    assert "DATA_MISSING" not in html
    assert "目标价区间与估值/技术区间图" not in html


def test_ai_radar_report_uses_news_title_only_with_news_cache() -> None:
    report = {
        "ticker": "MSFT",
        "company_name": "Microsoft Corporation",
        "current_price": 390.74,
        "decision": "WAIT",
        "data_status": "OK",
        "watch_points": ["Segment strength: internal factor"],
    }
    no_news_html = radar_ui._report_html(report, {}, {}, {}, {}, pd.DataFrame())

    assert "后续催化 / 风险事项" in no_news_html
    assert "近期新闻 / 催化" not in no_news_html
    assert "Segment strength" not in no_news_html
    assert "暂无本地新闻缓存；以下为系统根据财务、估值、技术和量价结构生成的待跟踪事项" not in no_news_html

    with_news_html = radar_ui._report_html(
        report,
        {},
        {"recent_news": [{"date": "2026-06-12", "source": "Cache", "title": "Guidance update", "impact": "正面"}]},
        {},
        {},
        pd.DataFrame(),
    )

    assert "近期新闻 / 催化" in with_news_html
    assert "2026-06-12" in with_news_html
    assert "事件：" in with_news_html
    assert "交易含义：" in with_news_html


def test_ai_radar_report_msft_near_repair_below_valuation_is_not_broken_buy_zone() -> None:
    report = {
        "ticker": "MSFT",
        "company_name": "Microsoft Corporation",
        "current_price": 390.74,
        "decision": "WAIT",
        "final_score": 82,
        "data_status": "OK",
        "summary": "wait. current price is below the discipline buy zone lower bound; review fundamentals",
        "entry_display_label": "低于估值参考",
        "entry_action_hint": "等待结构确认",
        "near_term_repair_zone_low": 377.84,
        "near_term_repair_zone_high": 415.02,
        "valuation_reference_zone_low": 394.12,
        "valuation_reference_zone_high": 425.99,
        "trend_reclaim_zone_low": 415.02,
        "trend_reclaim_zone_high": 425.99,
        "confirmation_price": 415.02,
        "invalidation_price": 377.84,
        "technical_structure_status": "WEAK_TREND_REPAIR",
    }

    html = radar_ui._report_html(report, {}, {}, {}, {}, pd.DataFrame())

    assert "近端修复观察区" in html
    assert "买区数据" in html
    assert "技术承接数据不足" in html
    assert "$377.84 - $415.02" in html
    assert "跌破纪律买区" not in html
    assert "current price is below" not in html
    assert report["decision"] == "WAIT"


def test_ai_radar_report_uses_dashboard_row_price_and_radar_zone_aliases() -> None:
    report = {
        "ticker": "MSFT",
        "company_name": "Microsoft Corporation",
        "price": "$390.74",
        "decision": "WAIT",
        "final_score": 82,
        "data_status": "OK",
        "entry_display_label": "价值复核",
        "entry_action_hint": "结构待确认",
        "radar_near_term_repair_zone_low": 377.84,
        "radar_near_term_repair_zone_high": 415.02,
        "radar_valuation_reference_zone_low": 394.12,
        "radar_valuation_reference_zone_high": 425.99,
        "radar_trend_reclaim_zone_low": 415.02,
        "radar_trend_reclaim_zone_high": 425.99,
        "radar_confirmation_price": 415.02,
        "radar_invalidation_price": 377.84,
        "technical_structure_status": "WEAK_TREND_REPAIR",
    }

    html = radar_ui._report_html(report, {}, {}, {}, {}, pd.DataFrame())

    assert "最新价" in html
    assert "$390.74" in html
    assert "近端修复观察区" in html
    assert "买区数据" in html
    assert "技术承接数据不足" in html
    assert "$377.84 - $415.02" in html
    assert "区间待补" not in html
    assert "跌破纪律买区" not in html
    assert report["decision"] == "WAIT"


def test_report_header_shows_price_source_label() -> None:
    html = radar_ui._research_header_html(
        {
            "ticker": "NOW",
            "company_name": "ServiceNow",
            "current_price": 102.37,
        },
        {},
        {
            "price_session": "LAST_CLOSE",
            "price_as_of": "2026-06-16",
            "last_close_synced_at": "2026-06-17T12:00:00+00:00",
        },
        {},
        "等待确认",
        pd.DataFrame(),
    )

    assert "$102.37" in html
    assert "昨夜收盘" in html
    assert "数据日期：2026-06-16" in html
    assert "刷新时间：06/17 20:00 HKT" in html


def test_ai_radar_upper_pullback_zone_copy_is_repair_watch_not_main_batting_zone() -> None:
    report = {
        "ticker": "IBM",
        "company_name": "International Business Machines Corporation",
        "current_price": 272.0,
        "decision": "WAIT",
        "final_score": 78,
        "data_status": "OK",
        "fifty_two_week_high": 332.46,
    }
    buy_zone_context = {
        "pullback_zone_low": 253.17,
        "pullback_zone_high": 273.56,
        "left_probe_zone_low": 253.17,
        "left_probe_zone_high": 260.31,
        "observe_zone_high": 268.46,
        "zone_position": 0.923,
        "current_action": "WAIT_CONFIRMATION",
        "confirmation_price": 276.0,
        "breakout_reevaluation_price": 332.46,
        "invalidation_price": 249.0,
        "chase_price": 310.0,
    }

    card_html = radar_ui._batting_zone_card_html(report, {}, buy_zone_context)
    chart_html = radar_ui._range_chart_html(report, {}, buy_zone_context)

    assert "买区上沿 / 修复观察区" in card_html
    assert "不是主动买点" in card_html
    assert "主击球区" not in card_html
    assert "左侧试仓候选区" in chart_html
    assert "承接观察区" in chart_html
    assert "修复观察区" in chart_html
    assert "当前位于修复观察区" in chart_html
    assert "主击球区：" not in chart_html


def test_ai_radar_range_chart_separates_invalidation_overlap_from_left_probe() -> None:
    report = {
        "ticker": "NOW",
        "company_name": "ServiceNow",
        "current_price": 105.81,
        "decision": "WAIT",
        "final_score": 78,
        "data_status": "OK",
    }
    buy_zone_context = {
        "pullback_zone_low": 94.60,
        "pullback_zone_high": 108.09,
        "left_probe_zone_low": 97.50,
        "left_probe_zone_high": 99.32,
        "observe_zone_high": 104.72,
        "invalidation_price": 97.50,
        "invalidation_risk_zone_low": 94.60,
        "invalidation_risk_zone_high": 97.50,
        "current_action": "WAIT_CONFIRMATION",
    }

    chart_html = radar_ui._range_chart_html(report, {}, buy_zone_context)

    assert "左侧试仓候选区" in chart_html
    assert "$97.50 - $99.32" in chart_html
    assert "结构失效风险区" in chart_html
    assert "$94.60 - $97.50" in chart_html
    assert "小仓观察参考" not in chart_html
    assert "允许小仓观察" not in chart_html


def test_ai_radar_report_shows_volume_price_acceptance_card() -> None:
    report = {
        "ticker": "NVDA",
        "company_name": "Nvidia",
        "current_price": 205.19,
        "decision": "WAIT",
        "final_score": 82,
        "data_status": "OK",
        "volumePriceAcceptance": {
            "volume_price_status": "FORMING",
            "volume_price_score": 48,
            "volume_ratio": 0.60,
            "volume_ma20": 175_713_022,
            "candle_signal_cn": "小阳整理",
            "support_signal_cn": "守住支撑",
            "confirmation_signal_cn": "未站上确认线",
            "distribution_count_10d": 0,
            "zone_source": "radar",
            "acceptance_reason_cn": "支撑暂时守住。",
        },
    }

    html = radar_ui._report_html(report, {}, {}, {}, {}, pd.DataFrame())

    assert "量价承接" in html
    assert "初步承接，尚未确认" in html
    assert "未放量站上确认线，不构成买入确认" in html
    assert "zone_source" not in html
    assert report["decision"] == "WAIT"


def test_ai_radar_report_volume_price_overextended_is_not_dip_confirmation() -> None:
    report = {
        "ticker": "MRVL",
        "current_price": 279.7,
        "decision": "BLOCK_CHASE",
        "final_score": 76,
        "data_status": "OK",
        "volumePriceAcceptance": {
            "volume_price_status": "OVEREXTENDED_SUPPORT_READ",
            "volume_price_score": 56,
            "volume_ratio": 0.74,
            "volume_ma20": 10_000_000,
            "candle_signal_cn": "小阳整理",
            "support_signal_cn": "支撑读数可参考",
            "confirmation_signal_cn": "未站上确认线",
            "distribution_count_10d": 0,
            "zone_source": "radar",
            "acceptance_reason_cn": "支撑读数不错。",
        },
    }

    html = radar_ui._report_html(report, {}, {}, {}, {}, pd.DataFrame())

    assert "脱离观察区" in html
    assert "价格已脱离回踩观察区，承接读数不构成低吸依据" in html
    assert report["decision"] == "BLOCK_CHASE"


def test_ai_radar_report_volume_price_gap_down_is_not_positive_acceptance() -> None:
    report = {
        "ticker": "ADBE",
        "current_price": 204.02,
        "decision": "WAIT",
        "final_score": 78,
        "data_status": "OK",
        "volumePriceAcceptance": {
            "volume_price_status": "UNCONFIRMED",
            "volume_price_score": 27,
            "volume_ratio": 3.56,
            "volume_ma20": 4_000_000,
            "candle_signal_cn": "下影线承接",
            "support_signal_cn": "支撑仍需复核",
            "confirmation_signal_cn": "未站上确认线",
            "distribution_count_10d": 1,
            "zone_source": "radar",
            "acceptance_reason_cn": "高量跳空下跌，量价承接仍需复核。",
        },
    }

    html = radar_ui._report_html(report, {}, {}, {}, {}, pd.DataFrame())

    assert "量价未确认" in html
    assert "高量跳空下跌，量价承接仍需复核" in html
    assert "承接确认" not in html
    assert "承接形成中" not in html
    assert report["decision"] == "WAIT"


def test_ai_radar_report_only_marks_breakdown_when_price_breaks_invalidation() -> None:
    report = {
        "ticker": "ADBE",
        "company_name": "Adobe Inc.",
        "current_price": 190.0,
        "decision": "WAIT",
        "final_score": 78,
        "data_status": "OK",
        "summary": "wait. current price is below the discipline buy zone lower bound; review fundamentals",
        "near_term_repair_zone_low": 192.85,
        "near_term_repair_zone_high": 203.29,
        "valuation_reference_zone_low": 210.0,
        "valuation_reference_zone_high": 240.0,
        "invalidation_price": 196.9,
        "confirmation_price": 241.15,
        "technical_structure_status": "WEAK_TREND_REPAIR",
    }

    html = radar_ui._report_html(report, {}, {}, {}, {}, pd.DataFrame())

    assert "破位复核区" in html
    assert "跌破 $196.90 后下切复核" in html
    assert "技术承接数据不足" in html
    assert "current price is below" not in html
    assert report["decision"] == "WAIT"


def test_ai_radar_report_optional_gaps_do_not_override_main_conclusion() -> None:
    report = {
        "ticker": "NOW",
        "company_name": "ServiceNow",
        "sector": "企业SaaS｜工作流自动化",
        "current_price": 103.0,
        "decision": "WAIT",
        "final_score": 80,
        "data_status": "OK",
        "near_term_repair_zone_low": 100.0,
        "near_term_repair_zone_high": 108.0,
        "debug": {"data_missing_fields": ["vwap", "relative_strength_vs_QQQ"]},
    }

    html = radar_ui._report_html(report, {}, {}, {}, {}, pd.DataFrame())

    assert "近端修复观察区" in html
    assert "资料缺口" not in html
    assert "待补数据" not in html
    assert report["decision"] == "WAIT"


def test_ai_radar_report_volume_prefers_quote_over_daily_cache() -> None:
    history = pd.DataFrame(
        {
            "date": pd.date_range("2026-05-01", periods=20),
            "close": [100 + index for index in range(20)],
            "volume": [18_500_000] * 19 + [8_000_000],
        }
    )

    rows = radar_ui._key_metric_rows(
        {"current_price": 390},
        {"quoteVolume": 13_200_000},
        {},
        {},
        history,
    )

    values = dict(rows)
    assert values["成交量"] == "13.2M"
    assert values["20日均量"] == "18.0M"
    assert values["量比"] == "0.73x"
    assert values["成交量来源"] == "报价缓存"


def test_ai_radar_report_volume_falls_back_to_latest_daily_bar() -> None:
    history = pd.DataFrame(
        {
            "date": pd.date_range("2026-05-01", periods=20),
            "close": [100 + index for index in range(20)],
            "volume": [10] * 19 + [30],
        }
    )

    rows = radar_ui._key_metric_rows({"current_price": 390}, {}, {}, {}, history)

    values = dict(rows)
    assert values["成交量"] == "30"
    assert values["20日均量"] == "11"
    assert values["量比"] == "2.73x"
    assert values["成交量来源"] == "日线缓存"


def test_ai_radar_report_header_uses_daily_volume_when_quote_missing() -> None:
    history = pd.DataFrame(
        {
            "date": pd.date_range("2026-05-01", periods=20),
            "close": [100 + index for index in range(20)],
            "volume": [10] * 19 + [6_611_028],
        }
    )
    report = {"ticker": "WDAY", "company_name": "Workday", "current_price": 210}

    html = radar_ui._research_header_html(report, {}, {}, {}, "等待确认", history)

    assert "成交量" in html
    assert "<span>成交量</span><strong>6.6M" in html


def test_ai_radar_report_volume_can_fallback_to_volume_price_acceptance_snapshot() -> None:
    volume = radar_ui.resolve_volume_snapshot(
        "TEST",
        {},
        pd.DataFrame(),
        {
            "latest_volume": 2_400_000,
            "volume_ma20": 3_000_000,
            "volume_regime_cn": "缩量",
        },
    )

    assert volume["latest_volume"] == 2_400_000
    assert volume["volume_ratio"] == 0.8
    assert volume["volume_source"] == "volume_price_acceptance"
    assert radar_ui._volume_source_label(volume["volume_source"]) == "量价模块"


def test_ai_radar_report_volume_snapshot_marks_missing_only_when_all_sources_missing() -> None:
    volume = radar_ui.resolve_volume_snapshot("MISS", {}, pd.DataFrame(), {})

    assert volume["latest_volume"] is None
    assert volume["volume_source"] == "unavailable"


def test_ai_radar_report_volume_missing_is_specific_data_gap() -> None:
    report = {
        "ticker": "MSFT",
        "company_name": "Microsoft Corporation",
        "sector": "云平台｜AI软件",
        "current_price": 390,
        "decision": "WAIT",
        "final_score": 82,
        "data_status": "OK",
    }

    html = radar_ui._report_html(report, {}, {}, {}, {}, pd.DataFrame())

    assert "暂无成交量数据" in html
    assert "成交量缺失" in html
    assert "成交量" in html
    assert "daily_bar.volume" not in html
    assert "volume、daily_bar.volume" not in html
    assert report["decision"] == "WAIT"


def test_ai_radar_report_position_action_uses_buy_zone_display() -> None:
    report = {
        "ticker": "NOW",
        "company_name": "Workday, Inc.",
        "current_price": 102.15,
        "decision": "ALLOW_BUY",
        "final_score": 68,
        "data_status": "OK",
    }
    buy_zone_context = {
        "current_action": "ALLOW_SMALL_BUY",
        "primary_zone_text": "回踩买区",
        "pullback_zone_low": 99.29,
        "pullback_zone_high": 108.33,
        "existing_position_action_text": "已有持仓：允许回踩复核加仓，但不能一次打满。",
    }
    buy_zone_display = {
        "main_action_text": "持有观察 / 当前不建议新增",
        "account_action_text": "已有 100 股，当前新增额度为 0，系统不建议新增",
        "badge_label": "击球区内",
        "zone_text": "$99.29 - $108.33",
    }
    portfolio_context = {
        "has_position": True,
        "shares": 100,
        "avg_cost": 106,
        "market_value": 10_215,
        "unrealized_pnl": -385,
        "unrealized_pnl_pct": -3.6,
        "portfolio_weight": 0.058,
        "action_for_existing_position": "已有持仓：允许回踩复核加仓，但不能一次打满。",
    }
    action_result = SimpleNamespace(action_code="ALLOW_SMALL_BUY", action_cn="小仓观察参考")

    html = radar_ui._report_html(
        report,
        {},
        {},
        {},
        {},
        pd.DataFrame(),
        action_result=action_result,
        portfolio_context=portfolio_context,
        buy_zone_context=buy_zone_context,
        buy_zone_display=buy_zone_display,
        include_appendix=False,
    )

    assert "当前价不新增" in html
    assert "未设置计划上限" in html
    assert "组合持仓页可设置" in html
    assert "允许回踩复核加仓" not in html


def test_ai_radar_fallback_action_copy_uses_advisory_language() -> None:
    action_result = SimpleNamespace(action_code="ALLOW_SMALL_BUY")

    rating = radar_ui._rating_text({}, action_result, {})
    localized = radar_ui._localize_report_text("ALLOW_BUY")

    assert rating == "小仓观察建议"
    assert localized == "小仓观察建议"
    assert "允许" not in rating
    assert "允许" not in localized


def test_ai_radar_report_completeness_localizes_raw_missing_fields() -> None:
    report = {
        "ticker": "MSFT",
        "company_name": "Microsoft Corporation",
        "sector": "云平台｜AI软件",
        "current_price": 390,
        "decision": "WAIT",
        "final_score": 82,
        "data_status": "OK",
        "debug": {
            "data_missing_fields": [
                "forward_pe",
                "roe",
                "sector / industry",
                "news_cache",
            ]
        },
    }

    html = radar_ui._data_completeness_html(
        report,
        "高",
        {"volume_source": "daily_cache", "latest_volume": 12_000_000},
    )

    assert "远期市盈率" in html
    assert "净资产收益率" in html
    assert "新闻缓存" in html
    assert "行业 / 赛道信息" not in html
    assert "forward_pe" not in html
    assert "roe" not in html
    assert "sector / industry" not in html
    assert "news_cache" not in html


def test_ai_radar_debug_html_localizes_fields_without_name_error() -> None:
    html = radar_ui._debug_html(
        {"data_missing_fields": ["forward_pe", "sector / industry"]},
        {"ticker": "MSFT", "company_name": "Microsoft Corporation"},
    )

    assert "远期市盈率" in html
    assert "forward_pe" not in html
    assert "sector / industry" not in html


def test_ai_radar_report_daily_volume_prevents_volume_gap() -> None:
    report = {
        "ticker": "MSFT",
        "company_name": "Microsoft Corporation",
        "sector": "云平台｜AI软件",
        "current_price": 390,
        "decision": "WAIT",
        "final_score": 82,
        "data_status": "OK",
        "debug": {"data_missing_fields": ["volume"]},
    }
    volume = {"volume_source": "daily_cache", "latest_volume": 12_000_000}

    html = radar_ui._data_completeness_html(report, "高", volume)

    assert "成交量缺失" not in html
    assert "volume" not in html


def test_data_missing_is_downgraded_to_confidence_and_missing_groups() -> None:
    row = {
        "ticker": "MSFT",
        "company_name": "Microsoft",
        "sector": "Technology / Software",
        "current_price": 390,
        "decision": "DATA_MISSING",
        "data_status": "MISSING_SCORE",
        "debug": {"data_missing_fields": ["forward_pe", "enterprise_to_revenue", "ema50"]},
    }

    html = radar_ui._list_row_html(row, "")

    assert "DATA_MISSING" not in html
    assert "技术数据缺口" in html


def test_list_data_confidence_does_not_show_price_gap_when_current_price_exists() -> None:
    row = {
        "ticker": "MSFT",
        "company_name": "Microsoft",
        "sector": "Technology / Software",
        "current_price": 390,
        "data_status": "MISSING_SCORE",
        "debug": {"data_missing_fields": ["current_price", "forward_pe", "ema50"]},
    }

    html = radar_ui._list_row_html(row, "")

    assert "价格缺口" not in html
    assert "技术数据缺口" in html


def test_list_data_confidence_shows_price_gap_only_when_price_missing() -> None:
    row = {
        "ticker": "MSFT",
        "company_name": "Microsoft",
        "sector": "Technology / Software",
        "current_price": None,
        "data_status": "MISSING_PRICE",
        "debug": {"data_missing_fields": ["current_price"]},
    }

    html = radar_ui._list_row_html(row, "")

    assert "价格缺失" in html


def test_list_data_confidence_shows_stale_price_not_price_gap() -> None:
    row = {
        "ticker": "MSFT",
        "company_name": "Microsoft",
        "sector": "Technology / Software",
        "current_price": 390,
        "data_status": "STALE",
        "is_stale": True,
        "debug": {"data_missing_fields": ["current_price_stale"]},
    }

    html = radar_ui._list_row_html(row, "")

    assert "价格过期" in html
    assert "价格缺口" not in html


def test_list_company_aliases_render_company_name() -> None:
    assert radar_ui._company_name_from_sources("MSFT", None, {"companyName": "Microsoft Corp."}) == "Microsoft Corp."
    assert radar_ui._company_name_from_sources("MSFT", None, {"company_name": "Microsoft Corporation"}) == "Microsoft Corporation"
    assert radar_ui._company_name_from_sources("MSFT", None, {"name": "Microsoft"}) == "Microsoft"


def test_list_sector_aliases_render_track() -> None:
    assert radar_ui._sector_track_from_sources(None, {"sector": "Technology", "industry": "Software - Application"}) == "科技｜应用软件"
    assert radar_ui._sector_track_from_sources(None, {"industry": "Software - Infrastructure"}) == "软件基础设施"
    assert radar_ui._sector_track_from_sources({"model": "SaaS"}, {}) == "赛道待补"


def test_sector_localization_supports_provider_and_ticker_research_tracks() -> None:
    assert localize_sector("Technology", "Software - Application") == "科技｜应用软件"
    assert localize_sector("Healthcare", "Biotechnology") == "医疗健康｜生物科技"
    assert localize_sector("Financial Services", "Capital Markets") == "金融服务｜资本市场"
    assert localize_sector("Communication Services", "Internet Content & Information") == "通信服务｜互联网内容与信息"
    assert localize_sector("Utilities", "Independent Power Producers") == "公用事业｜独立电力生产商"
    assert localize_sector("Utilities", "Renewable Utilities") == "公用事业｜可再生公用事业"
    assert get_ticker_research_track("MSFT", "Technology", "Software - Infrastructure") == "云平台｜AI软件"
    assert get_ticker_research_track("NOW", "Technology", "Software - Application") == "企业SaaS｜工作流自动化"
    assert get_ticker_research_track("NVO", "Healthcare", "Biotechnology") == "GLP-1｜生物医药"
    assert get_ticker_research_track("COIN", "Financial Services", "Capital Markets") == "加密交易平台"
    assert get_ticker_research_track("UNKNOWN", "Consumer Cyclical", "Internet Retail") == "可选消费｜互联网零售"


def test_list_company_track_uses_chinese_research_track_and_ticker_fallback() -> None:
    html = radar_ui._company_track_html(
        {
            "ticker": "MSFT",
            "company_name": "Microsoft Corporation",
            "sector": "云平台｜AI软件",
        }
    )
    assert "Microsoft Corporation" in html
    assert "云平台｜AI软件" in html
    assert "Technology / Software - Application" not in html

    fallback_html = radar_ui._company_track_html({"ticker": "XYZ", "company_name": "XYZ", "sector": "赛道待补"})
    assert "XYZ" in fallback_html
    assert "赛道待补" in fallback_html


def test_optional_vwap_and_relative_strength_gaps_do_not_make_medium_gap() -> None:
    row = {
        "ticker": "MSFT",
        "company_name": "Microsoft",
        "sector": "Technology / Software",
        "current_price": 390,
        "data_status": "OK",
        "technical_entry_missing_fields": ["vwap", "relative_strength_vs_QQQ"],
    }

    html = radar_ui._data_confidence_html(row)

    assert "高｜可选项缺失" in html
    assert "中" not in html
    assert "可选：已用日线替代 VWAP" in html
    assert "可选：相对强弱缺失" in html


def test_profile_missing_is_actionable_medium_gap() -> None:
    row = {
        "ticker": "MSFT",
        "company_name": "MSFT",
        "current_price": 390,
        "data_status": "OK",
    }

    html = radar_ui._data_confidence_html(row)

    assert "中｜资料缺口" in html


def test_ticker_research_track_prevents_profile_gap_when_provider_sector_missing() -> None:
    row = {
        "ticker": "MSFT",
        "company_name": "Microsoft Corporation",
        "current_price": 390,
        "data_status": "OK",
    }

    html = radar_ui._data_confidence_html(row)

    assert "资料缺口" not in html
    assert "高" in html


def test_glw_ticker_track_prevents_sector_gap_when_provider_sector_missing() -> None:
    row = {
        "ticker": "GLW",
        "company_name": "Corning Incorporated",
        "current_price": 179.2,
        "data_status": "OK",
        "debug": {"data_missing_fields": ["sector / industry"]},
    }

    completeness = radar_ui._data_completeness_html(
        row,
        "高",
        {"volume_source": "daily_cache", "latest_volume": 8_891_877},
    )
    confidence = radar_ui._data_confidence_html(row)

    assert "科技" in radar_ui._company_track_html(row)
    assert "行业 / 赛道信息" not in completeness
    assert "sector / industry" not in completeness
    assert "资料缺口" not in confidence


def test_crcl_without_price_or_history_stays_data_insufficient() -> None:
    with TemporaryDirectory() as tmpdir:
        report = build_ai_stock_radar_report("CRCL", path=_db(tmpdir), now=NOW)
        data = report.to_dict()

    missing = set(data["debug"]["data_missing_fields"])

    assert data["decision"] == "DATA_MISSING"
    assert data["current_price"] is None
    assert data["price_position"] == "ZONE_MISSING"
    assert {"current_price", "daily_bars"}.issubset(missing)


def test_list_row_uses_cached_report_fallback_when_dashboard_table_missing() -> None:
    cached_row = {
        "companyName": "Microsoft Corporation",
        "rawSnapshot": {
            "company_name": "Microsoft Corporation",
            "sector": "Technology",
            "industry": "Software - Infrastructure",
            "forward_pe": 25,
            "enterprise_to_revenue": 8,
            "free_cash_flow_yield": 0.04,
            "fcf_margin": 0.25,
            "gross_margin": 0.7,
            "net_margin": 0.3,
            "roe": 0.35,
            "revenue_growth": 0.12,
        },
        "rawTechnicals": {
            "price": 390,
            "ema20": 380,
            "ema50": 370,
            "ema200": 340,
            "atr14": 8,
            "recent_swing_low": 360,
            "fifty_two_week_high": 430,
            "fifty_two_week_low": 300,
        },
    }

    with patch.object(radar_ui, "_dashboard_row", return_value=None), patch.object(
        radar_ui, "_single_report_row", return_value=cached_row
    ), patch.object(radar_ui, "build_ai_stock_radar_list_row") as build_row:
        build_row.return_value = {
            "ticker": "MSFT",
            "company_name": "Microsoft Corporation",
            "current_price": 390,
            "data_status": "OK",
        }

        row = radar_ui._list_row("MSFT")

    assert row["company_name"] == "Microsoft Corporation"
    assert row["sector"] == "云平台｜AI软件"
    assert build_row.call_args.kwargs["snapshot"] == cached_row["rawSnapshot"]
    assert build_row.call_args.kwargs["technicals"] == cached_row["rawTechnicals"]


def test_price_above_buy_zone_blocks_chase_with_reason() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 125)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=125),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "BLOCK_CHASE"
        assert report.allowed_add_pct == 0
        assert "current price is above the discipline buy zone" in report.block_reasons
        assert "current price is in or above chase zone" in report.block_reasons


def test_price_position_in_buy_zone() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=95),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.price_position == "IN_BUY_ZONE"
        assert report.to_dict()["debug"]["price_position"] == "IN_BUY_ZONE"


def test_price_position_above_buy_zone_before_chase() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 110)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=110),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.price_position == "ABOVE_BUY_ZONE"
        assert report.decision == "WAIT"
        assert "current price is above the discipline buy zone" in report.block_reasons


def test_price_position_in_chase_zone() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 125)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=125),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.price_position == "IN_CHASE_ZONE"
        assert report.decision == "BLOCK_CHASE"


def test_entry_display_above_buy_zone_shows_wait_price_reference() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 110)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=110),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "WAIT"
        assert report.entry_reference_low == 90
        assert report.entry_reference_high == 100
        assert report.next_action_price == 100
        assert report.chase_above_price == 120
        assert report.current_vs_entry_pct == 10.0
        assert report.entry_display_label == "等待回踩"
        assert report.entry_context_status == "WAIT_CONFIRMATION"
        assert report.entry_display_reason


def test_entry_display_in_chase_zone_keeps_block_chase() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 125)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=125),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "BLOCK_CHASE"
        assert report.entry_display_label == "追高风险区"
        assert report.entry_context_status == "BLOCK_CHASE"


def test_entry_display_inside_buy_zone_low_score_still_allows_technical_small_buy() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 88.3)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=88.3),
            scores=_scores(final_score=65),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "ALLOW_BUY"
        assert report.buy_zone_context["primary_zone"] == "DEEP_ACCEPTANCE"
        assert report.entry_display_label == "区内观察"
        assert report.core_max_pct == 0
        assert report.allowed_add_pct > 0
        assert "公司综合评分低于70，仅作为风险背景；买入时机仍以 setup_score 与量价承接复核。" in report.block_reasons
        assert not any("核心仓" in reason for reason in report.block_reasons)


def test_entry_display_below_buy_zone_does_not_auto_allow_buy() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 80)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=80),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "WAIT"
        assert report.price_position == "BELOW_BUY_ZONE"
        assert report.entry_display_label == "区内看承接"
        assert report.entry_context_status == "WAIT_CONFIRMATION"
        assert report.buy_zone_context["current_layer_type"] == "LEFT_PROBE_CANDIDATE"
        assert report.allowed_add_pct == 0


def test_entry_display_missing_zone_shows_specific_missing_reason() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            scores=_scores(),
            now=NOW,
        )

        assert report.decision == "DATA_MISSING"
        assert report.entry_display_label == "数据不足"
        assert report.entry_context_status == "DATA_INSUFFICIENT"


def test_list_row_includes_entry_display_fields_without_changing_decision() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 110)

        row = build_ai_stock_radar_list_row(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=110),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert row["decision"] == "WAIT"
        assert row["entry_display_label"] == "等待回踩"
        assert row["entry_context_status"] == "WAIT_CONFIRMATION"
        assert row["entry_reference_high"] == 100
        assert row["current_vs_entry_pct"] == 10.0


def test_derived_deep_value_zone_can_show_technical_pullback_without_changing_decision() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NOK", 120)

        report = build_ai_stock_radar_report(
            "NOK",
            path=path,
            snapshot=_cached_snapshot(company_name="Nokia"),
            technicals=_cached_technicals(
                price=120,
                fifty_two_week_low=20,
                fifty_two_week_high=160,
                ema20=116,
                ema50=105,
                ema100=95,
                ema200=80,
                atr14=5,
                recent_swing_low=108,
                recent_breakout_level=114,
                ema50_slope_20d_pct=1.5,
                ema200_slope_20d_pct=0.8,
            ),
            scores=_scores(final_score=78, valuation_score=48, technical_score=72),
            now=NOW,
        )

        assert report.decision == "WAIT"
        assert report.price_position == "ZONE_MISSING"
        assert report.buy_zone == {"lower": None, "upper": None, "label": "discipline_buy_zone"}
        assert report.technical_entry_zone_low == 102.0
        assert report.technical_entry_zone_high == 117.5
        assert report.technical_structure_status == "UPTREND_PULLBACK"
        assert report.technical_structure_label == "强趋势回踩"
        assert report.technical_pullback_zone_low == 102.0
        assert report.technical_pullback_zone_high == 117.5
        assert report.confirmation_price == 116
        assert report.invalidation_price == 108
        assert report.entry_display_label == "等待回踩"
        assert report.technical_position == "ABOVE_TECHNICAL_PULLBACK_ZONE"
        assert report.entry_context_status == "WAIT_CONFIRMATION"
        assert report.entry_display_reason
        assert report.debug["technical_entry_zone"]["source"] == "ema_pullback"


def test_price_inside_technical_pullback_zone_updates_display_status_without_allowing_buy() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NOK", 113)

        report = build_ai_stock_radar_report(
            "NOK",
            path=path,
            snapshot=_cached_snapshot(company_name="Nokia"),
            technicals=_cached_technicals(
                price=113,
                fifty_two_week_low=20,
                fifty_two_week_high=160,
                ema20=116,
                ema50=105,
                ema100=95,
                ema200=80,
                atr14=5,
                recent_swing_low=108,
                recent_breakout_level=114,
                ema50_slope_20d_pct=1.5,
                ema200_slope_20d_pct=0.8,
            ),
            scores=_scores(final_score=78, valuation_score=48, technical_score=72),
            now=NOW,
        )

        assert report.decision == "WAIT"
        assert report.allowed_add_pct == 0
        assert report.price_position == "ZONE_MISSING"
        assert report.technical_position == "IN_TECHNICAL_PULLBACK_ZONE"
        assert report.entry_context_status == "WAIT_CONFIRMATION"
        assert report.entry_display_label == "区内看承接"


def test_technical_pullback_overlap_with_chase_is_truncated_for_display_only() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NOK", 113)

        report = build_ai_stock_radar_report(
            "NOK",
            path=path,
            snapshot=_cached_snapshot(company_name="Nokia"),
            technicals=_cached_technicals(
                price=113,
                fifty_two_week_low=20,
                fifty_two_week_high=160,
                ema20=116,
                ema50=105,
                ema100=95,
                ema200=80,
                atr14=5,
                recent_swing_low=108,
                recent_breakout_level=114,
                ema50_slope_20d_pct=1.5,
                ema200_slope_20d_pct=0.8,
            ),
            scores=_scores(final_score=78, valuation_score=48, technical_score=72),
            buy_zone=RadarZone(lower=30, upper=50, label="discipline_buy_zone"),
            chase_zone=RadarZone(lower=110, upper=None, label="chase_zone"),
            now=NOW,
        )

        assert report.decision == "BLOCK_CHASE"
        assert report.allowed_add_pct == 0
        assert report.entry_context_status == "BLOCK_CHASE"
        assert report.technical_chase_overlap is True
        assert report.technical_entry_zone_high and report.technical_entry_zone_high > 110
        assert report.effective_technical_entry_zone_high == 110
        assert report.entry_display_label == "追高风险区"


def test_technical_entry_zone_needs_trend_confirmation() -> None:
    zone = build_technical_entry_zone(
        {
            "price": 80,
            "ema20": 83,
            "ema50": 86,
            "ema200": 100,
            "atr14": 2,
            "recent_swing_low": 78,
        }
    )

    assert zone["low"] is None
    assert zone["high"] is None
    assert zone["source"] == "trend_review"
    assert zone["technical_structure_status"] == "WEAK_TREND_REPAIR"
    assert zone["technical_structure_label"] == "弱趋势修复中"
    assert zone["technical_repair_zone_low"] == 77.32
    assert zone["technical_repair_zone_high"] == 86.16
    assert zone["near_term_repair_zone_low"] == 77.32
    assert zone["near_term_repair_zone_high"] == 86.16
    assert zone["trend_reclaim_zone_low"] == 95.2
    assert zone["trend_reclaim_zone_high"] == 100.6
    assert zone["confirmation_price"] == 83
    assert zone["invalidation_price"] == 78
    assert "收盘重新站回 EMA20 / EMA50 / EMA200" in zone["next_technical_steps"][0]
    assert "不自动生成技术买点" in zone["reason"]


def test_technical_structure_map_marks_ema50_below_ema200_as_repair() -> None:
    zone = build_technical_entry_zone(
        {
            "price": 105,
            "ema20": 104,
            "ema50": 96,
            "ema200": 112,
            "atr14": 3,
            "recent_swing_low": 100,
            "gain_20d_pct": 2,
            "volume_trend": 0.2,
        }
    )

    assert zone["low"] is None
    assert zone["high"] is None
    assert zone["technical_structure_status"] == "WEAK_TREND_REPAIR"
    assert zone["technical_repair_zone_low"] == 94.98
    assert zone["technical_repair_zone_high"] == 105.24
    assert zone["near_term_repair_zone_low"] == 94.98
    assert zone["near_term_repair_zone_high"] == 105.24
    assert zone["trend_reclaim_zone_low"] == 105.7
    assert zone["trend_reclaim_zone_high"] == 112.9
    assert zone["support_watch_zone_low"] == 98.92
    assert zone["support_watch_zone_high"] == 100.54
    assert "当前不是技术买点" in zone["reason"]


def test_technical_structure_map_marks_breakdown_below_swing_low() -> None:
    zone = build_technical_entry_zone(
        {
            "price": 92,
            "ema20": 98,
            "ema50": 104,
            "ema200": 110,
            "atr14": 4,
            "recent_swing_low": 95,
            "gain_20d_pct": -7,
            "volume_trend": 0.15,
        }
    )

    assert zone["low"] is None
    assert zone["high"] is None
    assert zone["source"] == "breakdown_review"
    assert zone["technical_structure_status"] == "BREAKDOWN_REVIEW"
    assert zone["technical_structure_label"] == "破位复核"
    assert zone["invalidation_price"] == 95
    assert "当前价跌破 recent swing low" in zone["technical_structure_reason"]
    assert "不把下跌自动当买点" in zone["next_technical_steps"][0]


def test_technical_structure_map_marks_range_base_building() -> None:
    zone = build_technical_entry_zone(
        {
            "price": 100,
            "ema20": 99,
            "ema50": 94,
            "ema200": 105,
            "atr14": 2,
            "recent_swing_low": 93,
            "gain_20d_pct": -1.5,
            "volume_trend": -0.12,
        }
    )

    assert zone["low"] is None
    assert zone["high"] is None
    assert zone["technical_structure_status"] == "RANGE_BASE_BUILDING"
    assert zone["technical_structure_label"] == "区间筑底"
    assert zone["near_term_repair_zone_low"] == 92.32
    assert zone["near_term_repair_zone_high"] == 100.16
    assert zone["trend_reclaim_zone_low"] == 99.0
    assert zone["trend_reclaim_zone_high"] == 105.6
    assert zone["support_watch_zone_low"] == 92.28
    assert zone["support_watch_zone_high"] == 93.36


def test_pltr_like_weak_trend_gets_structure_map_not_only_missing_zone() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "PLTR", 138)

        report = build_ai_stock_radar_report(
            "PLTR",
            path=path,
            snapshot=_cached_snapshot(company_name="Palantir"),
            technicals=_cached_technicals(
                price=138,
                fifty_two_week_low=120,
                fifty_two_week_high=190,
                ema20=142,
                ema50=135,
                ema100=145,
                ema200=150,
                atr14=6,
                recent_swing_low=132,
                recent_swing_high=160,
                gain_20d_pct=1.5,
                volume_trend=0.1,
            ),
            scores=_scores(final_score=82, valuation_score=72, technical_score=50),
            now=NOW,
        )

        assert report.technical_entry_zone_low is None
        assert report.technical_entry_zone_high is None
        assert report.technical_structure_status == "WEAK_TREND_REPAIR"
        assert report.technical_structure_label == "弱趋势修复中"
        assert report.technical_repair_zone_low == 129.96
        assert report.technical_repair_zone_high == 142.48
        assert report.near_term_repair_zone_low == 129.96
        assert report.near_term_repair_zone_high == 142.48
        assert report.trend_reclaim_zone_low == 141.72
        assert report.trend_reclaim_zone_high == 151.8
        assert report.confirmation_price == 142
        assert report.invalidation_price == 132
        assert "不自动生成技术买点" in report.technical_structure_reason
        assert report.decision in {"WAIT", "ALLOW_BUY", "BLOCK_CHASE"}


def test_now_like_weak_trend_splits_repair_and_trend_reclaim_zones() -> None:
    zone = build_technical_entry_zone(
        {
            "price": 103.08,
            "ema20": 108.29953636075712,
            "ema50": 105.28233793989949,
            "ema100": 112.58098700776137,
            "ema200": 130.4251765733172,
            "atr14": 8.485714285714284,
            "recent_swing_low": 85.44,
            "recent_breakout_level": 139.2,
            "gain_20d_pct": 1.2,
            "volume_trend": 0.12,
        }
    )

    assert zone["technical_structure_status"] == "WEAK_TREND_REPAIR"
    assert zone["low"] is None
    assert zone["high"] is None
    assert zone["near_term_repair_zone_low"] == 100.19
    assert zone["near_term_repair_zone_high"] == 108.92
    assert zone["trend_reclaim_zone_low"] == 120.24
    assert zone["trend_reclaim_zone_high"] == 132.97
    assert zone["deep_support_zone_low"] == 82.39
    assert zone["deep_support_zone_high"] == 86.97
    assert zone["confirmation_price"] == 105.28
    assert zone["invalidation_price"] == 85.44
    assert zone["adaptive_pullback_zone_low"] == 100.19
    assert zone["adaptive_pullback_zone_high"] == 108.92
    assert zone["adaptive_pullback_label"] == "弱趋势复核区"
    assert zone["adaptive_pullback_type"] == "WEAK_TREND_REVIEW"
    assert zone["adaptive_pullback_is_entry_signal"] is False


def test_breakdown_review_generates_adaptive_retest_zone() -> None:
    zone = build_technical_entry_zone(
        {
            "price": 88,
            "ema20": 94,
            "ema50": 100,
            "ema200": 96,
            "atr14": 4,
            "recent_swing_low": 90,
            "gain_20d_pct": -10,
            "volume_trend": 0.5,
        }
    )

    assert zone["technical_structure_status"] == "BREAKDOWN_REVIEW"
    assert zone["adaptive_pullback_zone_low"] == 88
    assert zone["adaptive_pullback_zone_high"] == 96
    assert zone["adaptive_pullback_label"] == "破位反抽复核区"
    assert zone["adaptive_pullback_type"] == "BREAKDOWN_RETEST"
    assert zone["adaptive_pullback_is_entry_signal"] is False


def test_range_base_generates_adaptive_support_watch_zone() -> None:
    zone = build_technical_entry_zone(
        {
            "price": 96,
            "ema20": 95,
            "ema50": 90,
            "ema200": 100,
            "atr14": 6,
            "recent_swing_low": 94,
            "gain_20d_pct": -1,
            "volume_trend": 0.0,
        }
    )

    assert zone["technical_structure_status"] == "RANGE_BASE_BUILDING"
    assert zone["adaptive_pullback_zone_low"] == 91.84
    assert zone["adaptive_pullback_zone_high"] == 95.08
    assert zone["adaptive_pullback_label"] == "箱体支撑观察区"
    assert zone["adaptive_pullback_type"] == "RANGE_SUPPORT"
    assert zone["adaptive_pullback_is_entry_signal"] is False


def test_technical_entry_zone_rejects_nan_inputs_with_missing_reason() -> None:
    zone = build_technical_entry_zone(
        {
            "price": float("nan"),
            "ema20": float("nan"),
            "ema50": 100,
            "ema200": 90,
            "atr14": float("nan"),
        }
    )

    assert zone["low"] is None
    assert zone["high"] is None
    assert zone["source"] == "missing_technical_data"
    assert zone["technical_structure_status"] == "DATA_MISSING"
    assert zone["technical_structure_label"] == "数据不足"
    assert "current_price" in zone["missing_fields"]
    assert "ema20" in zone["missing_fields"]
    assert zone["missing_reason"].startswith("技术回踩区暂缺")
    assert zone["confidence"] == "missing"


def test_cached_rules_high_quality_but_price_too_high_blocks_chase() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 190)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=190),
            now=NOW,
        )

        assert report.decision == "BLOCK_CHASE"
        assert report.allowed_add_pct == 0
        assert report.final_score and report.final_score >= 70
        assert "missing discipline buy zone" in report.block_reasons
        assert report.buy_zone_context["current_action"] == "BLOCK_CHASE"


def test_camel_case_fields_are_normalized_and_used_for_scores() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot={
                "companyName": "Nvidia",
                "forwardPE": 18,
                "enterpriseToRevenue": 6,
                "freeCashFlowYield": 0.08,
                "freeCashFlowMargin": 0.22,
                "grossMargin": 0.72,
                "netMargin": 0.28,
                "returnOnEquity": 0.35,
                "revenueGrowth": 0.25,
                "currentRatio": 2.0,
                "totalDebt": 10,
                "cashAndEquivalents": 20,
            },
            technicals={
                "price": 95,
                "fiftyTwoWeekHigh": 200,
                "fiftyTwoWeekLow": 100,
                "rsi14": 48,
                "gain20dPct": 4,
                "gain60dPct": 8,
            },
            now=NOW,
        )

        normalization = report.to_dict()["debug"]["input_normalization"]
        assert report.final_score is not None
        assert normalization["canonical_sources"]["revenue_growth"]["raw_field"] == "revenueGrowth"
        assert normalization["canonical_sources"]["gross_margin"]["raw_field"] == "grossMargin"
        assert normalization["canonical_sources"]["enterprise_to_revenue"]["raw_field"] == "enterpriseToRevenue"
        assert normalization["canonical_sources"]["fifty_two_week_high"]["raw_field"] == "fiftyTwoWeekHigh"


def test_canonical_fields_take_priority_over_aliases() -> None:
    metrics = normalize_radar_inputs(
        snapshot={
            "revenue_growth": 0.30,
            "revenueGrowth": -0.50,
            "enterprise_to_revenue": 5,
            "enterpriseToRevenue": 18,
        },
        technicals={},
        market={},
    )

    normalization = metrics["_normalization"]
    assert metrics["revenue_growth"] == 0.30
    assert metrics["enterprise_to_revenue"] == 5
    assert normalization["canonical_sources"]["revenue_growth"]["raw_field"] == "revenue_growth"
    assert normalization["canonical_sources"]["enterprise_to_revenue"]["raw_field"] == "enterprise_to_revenue"


def test_total_cash_is_normalized_as_cash() -> None:
    metrics = normalize_radar_inputs(
        snapshot={
            "total_cash": 123,
            "total_debt": 456,
        },
        technicals={},
        market={},
    )

    normalization = metrics["_normalization"]
    assert metrics["cash"] == 123
    assert metrics["debt"] == 456
    assert normalization["canonical_sources"]["cash"]["raw_field"] == "total_cash"


def test_profit_margin_is_normalized_as_net_margin() -> None:
    metrics = normalize_radar_inputs(
        snapshot={"profit_margin": 0.18},
        technicals={},
        market={},
    )

    normalization = metrics["_normalization"]
    assert metrics["net_margin"] == 0.18
    assert normalization["canonical_sources"]["net_margin"]["raw_field"] == "profit_margin"


def test_cached_rules_cheap_but_mediocre_company_does_not_allow_buy() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "WEAK", 115)

        report = build_ai_stock_radar_report(
            "WEAK",
            path=path,
            snapshot=_cached_snapshot(
                gross_margin=0.18,
                net_margin=-0.08,
                fcf_margin=-0.12,
                roe=-0.05,
                revenue_growth=-0.08,
                free_cash_flow_yield=0.1,
            ),
            technicals=_cached_technicals(price=115),
            now=NOW,
        )

        assert report.decision in {"WAIT", "AVOID"}
        assert report.allowed_add_pct == 0
        assert "公司综合评分低于70，仅作为风险背景；买入时机仍以 setup_score 与量价承接复核。" in report.block_reasons
        assert not any("核心仓" in reason for reason in report.block_reasons)
        assert not any("final score below 70" in reason for reason in report.block_reasons)


def test_missing_data_returns_data_missing_not_buy_signal() -> None:
    with TemporaryDirectory() as tmpdir:
        report = build_ai_stock_radar_report(
            "NVDA",
            path=_db(tmpdir),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "DATA_MISSING"
        assert report.data_status == "MISSING_PRICE"
        assert report.allowed_add_pct == 0
        assert report.block_reasons


def test_missing_valuation_metrics_returns_specific_data_missing_reason() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "MISS", 88.3)
        snapshot = _cached_snapshot()
        for key in ("forward_pe", "enterprise_to_revenue", "free_cash_flow_yield", "fcf_margin"):
            snapshot.pop(key)

        report = build_ai_stock_radar_report(
            "MISS",
            path=path,
            snapshot=snapshot,
            technicals=_cached_technicals(price=95),
            now=NOW,
        )

        assert report.decision == "ALLOW_BUY"
        assert report.allowed_add_pct == 0
        assert report.data_status == "OK"
        assert report.buy_zone_context["current_action"] == "ALLOW_SMALL_BUY"
        assert report.block_reasons


def test_missing_valuation_debug_lists_missing_fields() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "MISS", 95)
        snapshot = _cached_snapshot()
        for key in ("forward_pe", "enterprise_to_revenue", "free_cash_flow_yield", "fcf_margin"):
            snapshot.pop(key)

        report = build_ai_stock_radar_report(
            "MISS",
            path=path,
            snapshot=snapshot,
            technicals=_cached_technicals(price=88.3),
            now=NOW,
        )

        debug = report.to_dict()["debug"]
        assert report.data_status == "OK"
        assert "forward_pe" in debug["score_inputs"]["valuation_score"]["missing_fields"]
        assert "enterprise_to_revenue" in debug["score_inputs"]["valuation_score"]["missing_fields"]
        assert "free_cash_flow_yield" in debug["score_inputs"]["valuation_score"]["missing_fields"]


def test_manual_zone_debug_marks_manual_source() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=95),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        zones_debug = report.to_dict()["debug"]["price_zones"]
        assert zones_debug["source"] == "manual_input"
        assert zones_debug["zone_sources"]["buy_zone"] == "manual_input"
        assert zones_debug["zone_sources"]["watch_zone"] == "manual_input"
        assert zones_debug["zone_sources"]["chase_zone"] == "manual_input"


def test_derived_zone_debug_marks_rules_source() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 120)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=120),
            now=NOW,
        )

        zones_debug = report.to_dict()["debug"]["price_zones"]
        assert zones_debug["source"] == "missing"
        assert zones_debug["zone_sources"]["buy_zone"] == "missing"
        assert report.buy_zone == {"lower": None, "upper": None, "label": "discipline_buy_zone"}


def test_stale_cache_cannot_allow_buy() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95, "2026-05-28T11:00:00+00:00")

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
            quote_max_age_hours=24,
        )

        assert report.decision == "DATA_MISSING"
        assert report.data_status == "STALE"
        assert report.is_stale is True
        assert report.allowed_add_pct == 0
        assert "缓存过期" in report.block_reasons[0]


def test_stale_debug_marks_price_as_unusable_for_allow_buy() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95, "2026-05-28T11:00:00+00:00")

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=95),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
            quote_max_age_hours=24,
        )

        debug = report.to_dict()["debug"]
        assert report.decision == "DATA_MISSING"
        assert "current_price_stale" in debug["data_missing_fields"]
        assert debug["data_status"] == "STALE"


def test_missing_quality_fields_do_not_silently_create_high_score() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "MISS", 88.3)
        snapshot = _cached_snapshot()
        for key in ("gross_margin", "net_margin", "fcf_margin", "roe"):
            snapshot.pop(key)

        report = build_ai_stock_radar_report(
            "MISS",
            path=path,
            snapshot=snapshot,
            technicals=_cached_technicals(price=88.3),
            now=NOW,
        )

        debug = report.to_dict()["debug"]
        assert report.quality_score is None
        assert report.final_score is None
        assert report.decision == "ALLOW_BUY"
        assert report.buy_zone_context["current_action"] == "ALLOW_SMALL_BUY"
        assert "gross_margin" in debug["score_inputs"]["quality_score"]["missing_fields"]
        assert "quality_score" in debug["score_inputs"]["final_score"]["missing_fields"]


def test_missing_all_risk_fields_uses_conservative_risk_score() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)
        snapshot = _cached_snapshot()
        for key in ("current_ratio", "debt", "cash"):
            snapshot.pop(key)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=snapshot,
            technicals=_cached_technicals(price=95),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        debug = report.to_dict()["debug"]
        assert report.risk_score == 58.0
        assert report.data_status == "OK"
        assert debug["score_inputs"]["risk_score"]["risk_incomplete"] is True
        assert "risk_fields_missing" in debug["score_inputs"]["risk_score"]["negative_fields"]
        assert "risk fields missing" in " ".join(debug["field_alias_notes"])


def test_missing_risk_fields_caps_position_even_when_other_scores_are_high() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 88.3)
        snapshot = _cached_snapshot(
            gross_margin=0.85,
            net_margin=0.45,
            fcf_margin=0.4,
            roe=0.5,
            revenue_growth=0.6,
            forward_pe=18,
            enterprise_to_revenue=5,
            free_cash_flow_yield=0.1,
        )
        for key in ("current_ratio", "debt", "cash"):
            snapshot.pop(key)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=snapshot,
            technicals=_cached_technicals(price=88.3, fifty_two_week_high=140, fifty_two_week_low=70),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "ALLOW_BUY"
        assert report.core_max_pct <= 1.0
        assert report.trade_max_pct <= 1.0
        assert report.allowed_add_pct <= 1.0
        assert report.to_dict()["debug"]["position_plan"]["risk_incomplete"] is True


def test_debug_explanation_does_not_change_buy_gate_result() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 88.3)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=88.3),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        gate = evaluate_buy_gate(
            report,
            action_type="buy",
            position_bucket="trade",
            planned_after_position_pct=1,
            decision_mood="planned_execution",
            buy_reason="inside discipline buy zone",
        )

        assert report.to_dict()["debug"]
        assert report.decision == "ALLOW_BUY"
        assert gate.status == "pass"
        assert gate.buy_zone_action == "ALLOW_SMALL_BUY"
        assert gate.can_sync_to_portfolio is True


def test_low_valuation_score_cannot_get_heavy_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 88.3)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=88.3),
            scores=_scores(valuation_score=35),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "ALLOW_BUY"
        assert report.core_max_pct <= 2
        assert report.trade_max_pct <= 1
        assert report.allowed_add_pct <= 2
        assert "valuation score below 40; heavy position is not allowed" in report.block_reasons


def test_high_final_score_with_low_valuation_cannot_get_high_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 88.3)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=88.3),
            scores=_scores(final_score=90, valuation_score=35),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "ALLOW_BUY"
        assert report.core_max_pct <= 2
        assert report.trade_max_pct <= 1
        assert report.allowed_add_pct <= 2


def test_derived_price_zones_have_legal_order() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 120)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=120),
            now=NOW,
        )

        assert report.buy_zone == {"lower": None, "upper": None, "label": "discipline_buy_zone"}
        assert report.watch_zone == {"lower": None, "upper": None, "label": "watch_zone"}
        assert report.chase_zone == {"lower": None, "upper": None, "label": "no_chase_above"}
        assert report.buy_zone_context["current_action"] in {"WAIT_CONFIRMATION", "ALLOW_SMALL_BUY", "BLOCK_CHASE"}


def test_price_below_discipline_buy_zone_has_block_reason() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 80)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=80),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "WAIT"
        assert report.price_position == "BELOW_BUY_ZONE"
        assert report.entry_context_status == "WAIT_CONFIRMATION"
        assert report.buy_zone_context["current_layer_type"] == "LEFT_PROBE_CANDIDATE"
        assert report.allowed_add_pct == 0
        assert report.block_reasons
        assert "current price is below the discipline buy zone lower bound" in report.block_reasons[0]
        assert "review fundamentals" in report.block_reasons[0]
        debug = report.to_dict()["debug"]
        assert debug["wait_reason_is_below_buy_zone"] is True
        assert debug["below_buy_zone_reason"]
        assert debug["distance_to_buy_zone_pct"] == -11.1


def test_missing_zone_returns_zone_missing_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 88.3)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=88.3),
            scores=_scores(),
            now=NOW,
        )

        assert report.decision == "ALLOW_BUY"
        assert report.price_position == "ZONE_MISSING"
        assert report.buy_zone_context["current_action"] == "ALLOW_SMALL_BUY"
        assert report.to_dict()["debug"]["price_position"] == "ZONE_MISSING"


def test_low_final_score_cannot_get_core_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 88.3)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=88.3),
            scores=_scores(final_score=65),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "ALLOW_BUY"
        assert report.core_max_pct == 0
        assert report.allowed_add_pct > 0
        assert "公司综合评分低于70，仅作为风险背景；买入时机仍以 setup_score 与量价承接复核。" in report.block_reasons
        assert not any("核心仓" in reason for reason in report.block_reasons)


def test_watchlist_empty_and_sample_fallback_do_not_override_real_symbols() -> None:
    assert select_radar_symbols([]) == ([], "empty watchlist")
    assert select_radar_symbols([], ["nvda"]) == (["NVDA"], "sample fallback")
    assert select_radar_symbols(["msft"], ["nvda"]) == (["MSFT"], "watchlist")


def test_missing_buy_gate_result_defaults_to_advisory_entry_fields() -> None:
    fields = buy_gate_entry_fields(None, action_type="buy")

    assert fields["radarDecision"] == "DATA_MISSING"
    assert fields["radarBlocked"] is False
    assert fields["radarBlockReasons"] == []
    assert fields["gateHardBlocked"] is False
    assert fields["radarAdvisoryOnly"] is True
    assert fields["radarAdvisoryWarnings"]
    assert fields["radarObservationOnly"] is False
    assert fields["gateCheckedAt"]


def test_buy_gate_treats_block_chase_as_advisory() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 125)
        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=125),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        gate = evaluate_buy_gate(
            report,
            action_type="buy",
            position_bucket="trade",
            planned_after_position_pct=1,
            decision_mood="",
            buy_reason="plan execution",
        )

        assert gate.status == "warning"
        assert gate.can_continue is True
        assert gate.can_sync_to_portfolio is True
        assert gate.reasons == []
        assert gate.advisory_warnings
        assert gate.radar_advisory_only is True


def test_buy_gate_treats_data_missing_as_advisory() -> None:
    with TemporaryDirectory() as tmpdir:
        report = build_ai_stock_radar_report(
            "NVDA",
            path=_db(tmpdir),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        gate = evaluate_buy_gate(
            report,
            action_type="add",
            position_bucket="trade",
            planned_after_position_pct=1,
            decision_mood="",
            buy_reason="plan execution",
        )

        assert report.decision == "DATA_MISSING"
        assert gate.status == "warning"
        assert gate.can_sync_to_portfolio is True
        assert gate.advisory_warnings
        assert gate.radar_advisory_only is True


def test_buy_gate_treats_price_zone_positions_as_advisory() -> None:
    for decision, price_position in (
        ("WAIT", "ABOVE_BUY_ZONE"),
        ("BLOCK_CHASE", "IN_CHASE_ZONE"),
        ("WAIT", "BELOW_BUY_ZONE"),
        ("DATA_MISSING", "ZONE_MISSING"),
    ):
        gate = evaluate_buy_gate(
            {
                "ticker": "NVDA",
                "decision": decision,
                "price_position": price_position,
                "current_price": 125,
                "buy_zone": [90, 100],
                "allowed_add_pct": 0,
                "core_max_pct": 20,
                "trade_max_pct": 8,
                "data_status": "DATA_MISSING" if decision == "DATA_MISSING" else "OK",
                "block_reasons": [f"{price_position} warning"],
            },
            action_type="buy",
            position_bucket="core",
            planned_after_position_pct=1.0,
            decision_mood="plan_execution",
            buy_reason="plan execution",
        )

        assert gate.status == "warning"
        assert gate.gate_hard_blocked is False
        assert gate.can_sync_to_portfolio is True
        assert gate.price_position == price_position
        assert gate.advisory_warnings
        assert gate.radar_advisory_only is True


def test_buy_gate_block_chase_observation_only_still_does_not_sync() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 125)
        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=80),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        gate = evaluate_buy_gate(
            report,
            action_type="buy",
            position_bucket="trade",
            planned_after_position_pct=1,
            decision_mood="",
            observation_only=True,
            buy_reason="watch only",
        )

        assert gate.is_blocked is False
        assert gate.can_sync_to_portfolio is False
        assert gate.is_observation_only is True
        assert gate.advisory_warnings


def test_buy_gate_allows_allow_buy_with_reason_under_position_limit() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 88.3)
        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=88.3),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        gate = evaluate_buy_gate(
            report,
            action_type="buy",
            position_bucket="core",
            planned_after_position_pct=1,
            decision_mood="planned_execution",
            buy_reason="inside discipline buy zone",
        )

        assert report.decision == "ALLOW_BUY"
        assert gate.status == "pass"
        assert gate.buy_zone_action == "ALLOW_SMALL_BUY"
        assert gate.can_continue is True
        assert gate.can_sync_to_portfolio is True


def test_buy_gate_treats_fomo_as_advisory_even_inside_buy_zone() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 88.3)
        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=88.3),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        gate = evaluate_buy_gate(
            report,
            action_type="add",
            position_bucket="trade",
            planned_after_position_pct=1,
            decision_mood="fomo",
            buy_reason="inside discipline buy zone",
        )

        assert report.decision == "ALLOW_BUY"
        assert gate.status == "warning"
        assert gate.can_sync_to_portfolio is True
        assert gate.gate_hard_blocked is False
        assert gate.mood_gate_blocked is False
        assert any("情绪" in item or "FOMO" in item for item in gate.advisory_warnings)


def test_buy_gate_treats_core_position_above_core_max_pct_as_advisory() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)
        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=95),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        gate = evaluate_buy_gate(
            report,
            action_type="buy",
            position_bucket="core",
            planned_after_position_pct=report.core_max_pct + 1,
            decision_mood="planned_execution",
            buy_reason="inside discipline buy zone",
        )

        assert gate.status == "warning"
        assert gate.can_sync_to_portfolio is True
        assert gate.gate_hard_blocked is False
        assert gate.position_gate_blocked is False
        assert any("仓位" in item or "参考上限" in item for item in gate.advisory_warnings)


def test_buy_gate_treats_trade_position_above_trade_max_pct_as_advisory() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)
        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            snapshot=_cached_snapshot(),
            technicals=_cached_technicals(price=95),
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        gate = evaluate_buy_gate(
            report,
            action_type="add",
            position_bucket="trade",
            planned_after_position_pct=report.trade_max_pct + 1,
            decision_mood="planned_execution",
            buy_reason="inside discipline buy zone",
        )

        assert gate.status == "warning"
        assert gate.can_continue is True
        assert gate.can_sync_to_portfolio is True
        assert gate.gate_hard_blocked is False
        assert any("仓位" in item or "参考上限" in item for item in gate.advisory_warnings)
