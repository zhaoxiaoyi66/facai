from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data.buy_execution_context import (
    STRUCTURE_MISSING,
    STRUCTURE_PARTIAL,
    STRUCTURE_STALE,
    build_buy_execution_advisory_context,
    buy_execution_advisory_context_html,
)


def _now() -> datetime:
    return datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)


def _radar_with_technical_map(**overrides) -> dict:
    report = {
        "ticker": "ADBE",
        "current_price": 202.0,
        "data_updated_at": "2026-06-13T10:00:00+00:00",
        "history_status": "available",
        "history_latest_date": "2026-06-12",
        "technical_structure_status": "WEAK_TREND_REPAIR",
        "technical_structure_label": "弱趋势修复中",
        "near_term_repair_zone_low": 192.85,
        "near_term_repair_zone_high": 203.29,
        "trend_reclaim_zone_low": 230.0,
        "trend_reclaim_zone_high": 245.0,
        "confirmation_price": 241.15,
        "invalidation_price": 196.90,
        "next_technical_steps": ["等待收盘重新站回关键均线"],
    }
    report.update(overrides)
    return report


def test_buy_execution_context_reuses_radar_technical_structure_snapshot() -> None:
    with TemporaryDirectory() as tmpdir:
        context = build_buy_execution_advisory_context(
            "ADBE",
            path=Path(tmpdir) / "cache.sqlite",
            radar_report=_radar_with_technical_map(),
            now=_now(),
        )

    html = buy_execution_advisory_context_html(context)
    assert context.structure_hint.status == STRUCTURE_PARTIAL
    assert context.structure_hint.source == "radar_technical_structure"
    assert "结构待确认" in html
    assert "回踩承接确认" in html
    assert "量价承接" in html
    assert "待补数据" not in html
    assert "技术：" in html
    assert "Radar：" in html
    assert "宏观：" in html


def test_buy_execution_context_marks_partial_when_auxiliary_confirmation_is_missing() -> None:
    report = {
        "ticker": "NOW",
        "current_price": 103.0,
        "data_updated_at": "2026-06-13T10:00:00+00:00",
        "history_status": "available",
        "history_latest_date": "2026-06-12",
        "ema50": 105.0,
        "recent_swing_low": 98.0,
    }
    with TemporaryDirectory() as tmpdir:
        context = build_buy_execution_advisory_context(
            "NOW",
            path=Path(tmpdir) / "cache.sqlite",
            radar_report=report,
            now=_now(),
        )

    assert context.structure_hint.status == STRUCTURE_PARTIAL
    assert "relative_strength" in context.structure_hint.missing_fields
    assert "volume" in context.structure_hint.missing_fields


def test_buy_execution_context_marks_stale_when_technical_cache_is_old() -> None:
    report = _radar_with_technical_map(history_status="stale_history")
    with TemporaryDirectory() as tmpdir:
        context = build_buy_execution_advisory_context(
            "ADBE",
            path=Path(tmpdir) / "cache.sqlite",
            radar_report=report,
            now=_now(),
        )

    assert context.structure_hint.status == STRUCTURE_STALE
    assert context.technical_freshness == "过期"
    assert "更新技术" in "；".join(context.structure_hint.next_steps + context.structure_hint.warnings)


def test_buy_execution_context_only_marks_missing_when_core_context_is_absent() -> None:
    with TemporaryDirectory() as tmpdir:
        context = build_buy_execution_advisory_context(
            "MISS",
            path=Path(tmpdir) / "cache.sqlite",
            radar_report={"ticker": "MISS"},
            now=_now(),
        )

    assert context.structure_hint.status == STRUCTURE_MISSING
    assert {"price", "K-line", "EMA", "swing"}.issubset(set(context.structure_hint.missing_fields))


def test_buy_execution_context_acceptance_warns_when_chase_context_leaves_observation_zone() -> None:
    report = _radar_with_technical_map(
        current_price=210.0,
        near_term_repair_zone_high=203.29,
        decision="BLOCK_CHASE",
        price_position="IN_CHASE_ZONE",
    )
    with TemporaryDirectory() as tmpdir:
        context = build_buy_execution_advisory_context(
            "ADBE",
            path=Path(tmpdir) / "cache.sqlite",
            radar_report=report,
            now=_now(),
        )

    html = buy_execution_advisory_context_html(context)

    assert "价格已脱离回踩观察区" in html
    assert "Radar 仍为追高语境" in html
