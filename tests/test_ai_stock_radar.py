from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data.ai_stock_radar import RadarScores, RadarZone, build_ai_stock_radar_report
from data.trade_gate import evaluate_buy_gate
from ui.ai_stock_radar import select_radar_symbols


NOW = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


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
        "rsi14": 48,
        "gain_20d_pct": 4,
        "gain_60d_pct": 8,
    }
    values.update(overrides)
    return values


def test_price_inside_discipline_buy_zone_can_allow_buy() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "ALLOW_BUY"
        assert report.allowed_add_pct > 0
        assert report.block_reasons == []
        assert report.to_dict()["ticker"] == "NVDA"


def test_price_above_buy_zone_blocks_chase_with_reason() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 125)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
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
        assert "current price is above the discipline buy zone" in report.block_reasons
        assert "current price is in or above chase zone" in report.block_reasons


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
        assert "final score below 70; core position is not allowed" in report.block_reasons


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
        _insert_quote(path, "MISS", 95)
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

        assert report.decision == "DATA_MISSING"
        assert report.allowed_add_pct == 0
        assert report.data_status == "MISSING_VALUATION"
        assert report.block_reasons


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


def test_low_valuation_score_cannot_get_heavy_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            scores=_scores(valuation_score=35),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision != "ALLOW_BUY"
        assert report.core_max_pct <= 3
        assert report.trade_max_pct <= 1
        assert report.allowed_add_pct == 0
        assert "valuation score below 40; heavy position is not allowed" in report.block_reasons


def test_high_final_score_with_low_valuation_cannot_get_high_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            scores=_scores(final_score=90, valuation_score=35),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "WAIT"
        assert report.core_max_pct <= 2
        assert report.trade_max_pct <= 1
        assert report.allowed_add_pct == 0


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

        buy = report.buy_zone
        watch = report.watch_zone
        chase = report.chase_zone
        assert buy["lower"] < buy["upper"]
        assert buy["upper"] <= watch["lower"]
        assert watch["lower"] <= watch["upper"]
        assert watch["upper"] < chase["lower"]


def test_price_below_discipline_buy_zone_has_block_reason() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 80)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            scores=_scores(),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "WAIT"
        assert report.allowed_add_pct == 0
        assert report.block_reasons
        assert "current price is below the planned discipline zone; review data before acting" in report.block_reasons


def test_low_final_score_cannot_get_core_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)

        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
            scores=_scores(final_score=65),
            buy_zone=_buy_zone(),
            watch_zone=_watch_zone(),
            chase_zone=_chase_zone(),
            now=NOW,
        )

        assert report.decision == "WAIT"
        assert report.core_max_pct == 0
        assert report.allowed_add_pct == 0
        assert "final score below 70; core position is not allowed" in report.block_reasons


def test_watchlist_empty_and_sample_fallback_do_not_override_real_symbols() -> None:
    assert select_radar_symbols([]) == ([], "empty watchlist")
    assert select_radar_symbols([], ["nvda"]) == (["NVDA"], "sample fallback")
    assert select_radar_symbols(["msft"], ["nvda"]) == (["MSFT"], "watchlist")


def test_buy_gate_blocks_block_chase_buy() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 125)
        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
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

        assert gate.status == "blocked"
        assert gate.can_continue is False
        assert gate.can_sync_to_portfolio is False
        assert gate.reasons


def test_buy_gate_blocks_data_missing_buy() -> None:
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
        assert gate.status == "blocked"
        assert gate.can_sync_to_portfolio is False


def test_buy_gate_block_chase_observation_only_still_marks_blocked_without_sync() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 125)
        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
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

        assert gate.is_blocked is True
        assert gate.can_sync_to_portfolio is False
        assert gate.is_observation_only is True
        assert gate.reasons


def test_buy_gate_allows_allow_buy_with_reason_under_position_limit() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)
        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
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
        assert gate.can_continue is True
        assert gate.can_sync_to_portfolio is True


def test_buy_gate_blocks_fomo_even_inside_buy_zone() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)
        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
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
        assert gate.status == "blocked"
        assert gate.can_sync_to_portfolio is False


def test_buy_gate_blocks_core_position_above_core_max_pct() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)
        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
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

        assert gate.status == "blocked"
        assert gate.can_sync_to_portfolio is False


def test_buy_gate_blocks_trade_position_above_trade_max_pct() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 95)
        report = build_ai_stock_radar_report(
            "NVDA",
            path=path,
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

        assert gate.status == "blocked"
        assert gate.can_continue is False
        assert gate.can_sync_to_portfolio is False
