from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from data.market_context import build_market_context
from data.market_context import build_market_history
from data.buy_zone_engine import build_buy_zone_context
from data.prices import CACHE_PATH
from data.entry_display import build_entry_display as calculate_entry_display
from data.trade_gate import BuyGateResult as RadarBuyGateResult
from data.trade_gate import evaluate_buy_gate as evaluate_trade_buy_gate
from indicators.technicals import add_technical_indicators, latest_technical_snapshot


RADAR_DECISIONS = {"ALLOW_BUY", "WAIT", "BLOCK_CHASE", "AVOID", "DATA_MISSING"}
RADAR_REPORT_VERSION = "AI_STOCK_RADAR_V1_LOCAL_RULES"


@dataclass(frozen=True)
class RadarZone:
    lower: float | None = None
    upper: float | None = None
    label: str = ""


@dataclass(frozen=True)
class RadarScores:
    final_score: float | None = None
    quality_score: float | None = None
    growth_score: float | None = None
    valuation_score: float | None = None
    technical_score: float | None = None
    risk_score: float | None = None


@dataclass(frozen=True)
class RadarReport:
    ticker: str
    company_name: str
    current_price: float | None
    price_source: str
    data_updated_at: str | None
    is_stale: bool
    history_status: str
    history_latest_date: str | None
    history_ticker_key: str | None
    data_status: str
    final_score: float | None
    quality_score: float | None
    growth_score: float | None
    valuation_score: float | None
    technical_score: float | None
    risk_score: float | None
    decision: str
    buy_zone: dict[str, Any]
    watch_zone: dict[str, Any]
    chase_zone: dict[str, Any]
    buy_zone_context: dict[str, Any]
    core_max_pct: float
    trade_max_pct: float
    allowed_add_pct: float
    price_position: str
    entry_reference_low: float | None
    entry_reference_high: float | None
    next_action_price: float | None
    chase_above_price: float | None
    current_vs_entry_pct: float | None
    missing_entry_fields: list[str]
    technical_entry_zone_low: float | None
    technical_entry_zone_high: float | None
    effective_technical_entry_zone_low: float | None
    effective_technical_entry_zone_high: float | None
    technical_chase_overlap: bool
    technical_entry_source: str
    technical_entry_reason: str
    technical_entry_missing_fields: list[str]
    technical_entry_missing_reason: str
    technical_entry_confidence: str
    technical_structure_status: str
    technical_structure_label: str
    technical_pullback_zone_low: float | None
    technical_pullback_zone_high: float | None
    adaptive_pullback_zone_low: float | None
    adaptive_pullback_zone_high: float | None
    adaptive_pullback_label: str
    adaptive_pullback_type: str
    adaptive_pullback_confidence: str
    adaptive_pullback_reason: str
    adaptive_pullback_is_entry_signal: bool
    technical_repair_zone_low: float | None
    technical_repair_zone_high: float | None
    near_term_repair_zone_low: float | None
    near_term_repair_zone_high: float | None
    trend_reclaim_zone_low: float | None
    trend_reclaim_zone_high: float | None
    valuation_reference_zone_low: float | None
    valuation_reference_zone_high: float | None
    deep_support_zone_low: float | None
    deep_support_zone_high: float | None
    zone_semantic_label: str
    primary_entry_interpretation: str
    support_watch_zone_low: float | None
    support_watch_zone_high: float | None
    confirmation_price: float | None
    invalidation_price: float | None
    technical_structure_reason: str
    technical_missing_fields: list[str]
    next_technical_steps: list[str]
    technical_position: str
    entry_context_status: str
    nearest_support_price: float | None
    ema20: float | None
    ema50: float | None
    ema100: float | None
    ema200: float | None
    atr14: float | None
    recent_swing_low: float | None
    recent_breakout_level: float | None
    entry_display_label: str
    entry_display_reason: str
    entry_action_hint: str
    block_reasons: list[str]
    summary: str
    bull_points: list[str]
    risk_points: list[str]
    watch_points: list[str]
    debug: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def evaluate_radar_buy_gate(
    report: RadarReport | dict[str, Any],
    *,
    action_type: str,
    position_bucket: str = "trade",
    planned_after_position_pct: float | None = None,
    decision_mood: str = "",
    observation_only: bool = False,
    buy_reason: str = "",
) -> RadarBuyGateResult:
    return evaluate_trade_buy_gate(
        report,
        action_type=action_type,
        position_bucket=position_bucket,
        planned_after_position_pct=planned_after_position_pct,
        decision_mood=decision_mood,
        observation_only=observation_only,
        buy_reason=buy_reason,
    )

def build_ai_stock_radar_report(
    ticker: str,
    *,
    company_name: str = "",
    path: Path = CACHE_PATH,
    scores: RadarScores | dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
    technicals: dict[str, Any] | None = None,
    buy_zone: RadarZone | dict[str, Any] | None = None,
    watch_zone: RadarZone | dict[str, Any] | None = None,
    chase_zone: RadarZone | dict[str, Any] | None = None,
    bull_points: list[str] | None = None,
    risk_points: list[str] | None = None,
    watch_points: list[str] | None = None,
    now: datetime | None = None,
    quote_max_age_hours: float | None = 24,
    market: dict[str, Any] | None = None,
) -> RadarReport:
    symbol = _symbol(ticker)
    market = dict(market or build_market_context(symbol, path=path, now=now, quote_max_age_hours=quote_max_age_hours))
    current_price = _number(market.get("currentPrice"))
    metrics = _radar_metrics(snapshot, technicals, market) if snapshot is not None and technicals is not None else {}
    score_input = _score_input(scores, snapshot=snapshot, technicals=technicals, market=market)
    zones = calculate_price_zones(
        symbol,
        path=path,
        market=market,
        scores=score_input,
        buy_zone=buy_zone,
        watch_zone=watch_zone,
        chase_zone=chase_zone,
        metrics=metrics,
    )
    data_status = _data_status(market, score_input, zones["buy_zone"])
    price_position = calculate_price_position(current_price, zones["buy_zone"], zones["chase_zone"], data_status)
    block_reasons = _block_reasons(
        current_price,
        market,
        score_input,
        zones["buy_zone"],
        zones["chase_zone"],
        data_status,
        price_position=price_position,
    )
    decision = calculate_decision(
        current_price=current_price,
        scores=score_input,
        buy_zone=zones["buy_zone"],
        chase_zone=zones["chase_zone"],
        data_status=data_status,
        block_reasons=block_reasons,
    )
    risk_incomplete = _risk_fields_incomplete(metrics)
    technical_entry = build_technical_entry_zone(technicals or {}, data_status=data_status)
    entry_display = calculate_entry_display(
        current_price=current_price,
        buy_zone=zones["buy_zone"],
        chase_zone=zones["chase_zone"],
        technical_entry_zone=technical_entry,
        data_status=data_status,
        price_position=price_position,
        decision=decision,
        final_score=score_input.final_score,
        quality_score=score_input.quality_score,
        valuation_score=score_input.valuation_score,
        risk_score=score_input.risk_score,
    )
    buy_zone_context = _build_buy_zone_context(
        symbol=symbol,
        current_price=current_price,
        scores=score_input,
        metrics=metrics,
        technicals=technicals or {},
        technical_entry=technical_entry,
        entry_display=entry_display,
    )
    decision = calculate_decision(
        current_price=current_price,
        scores=score_input,
        buy_zone=zones["buy_zone"],
        chase_zone=zones["chase_zone"],
        data_status=data_status,
        block_reasons=block_reasons,
        buy_zone_context=buy_zone_context,
    )
    position_plan = calculate_position_plan(score_input, decision, risk_incomplete=risk_incomplete)
    entry_display = calculate_entry_display(
        current_price=current_price,
        buy_zone=zones["buy_zone"],
        chase_zone=zones["chase_zone"],
        technical_entry_zone=technical_entry,
        data_status=data_status,
        price_position=price_position,
        decision=decision,
        final_score=score_input.final_score,
        quality_score=score_input.quality_score,
        valuation_score=score_input.valuation_score,
        risk_score=score_input.risk_score,
        buy_zone_context=buy_zone_context,
    )
    debug = build_radar_debug(
        symbol,
        market=market,
        metrics=metrics,
        scores=score_input,
        zones=zones,
        buy_zone=buy_zone,
        watch_zone=watch_zone,
        chase_zone=chase_zone,
        data_status=data_status,
        block_reasons=block_reasons,
        current_price=current_price,
        price_position=price_position,
        technical_entry=technical_entry,
        path=path,
    )
    debug.update(_score_gap_debug(scores))
    return RadarReport(
        ticker=symbol,
        company_name=company_name or symbol,
        current_price=current_price,
        price_source=str(market.get("priceSource") or "missing"),
        data_updated_at=market.get("fetchedAt"),
        is_stale=bool(market.get("isStale")),
        history_status=str(market.get("historyStatus") or "missing"),
        history_latest_date=market.get("historyLatestDate"),
        history_ticker_key=market.get("historyTickerKey"),
        data_status=data_status,
        final_score=score_input.final_score,
        quality_score=score_input.quality_score,
        growth_score=score_input.growth_score,
        valuation_score=score_input.valuation_score,
        technical_score=score_input.technical_score,
        risk_score=score_input.risk_score,
        decision=decision,
        buy_zone=_zone_dict(zones["buy_zone"]),
        watch_zone=_zone_dict(zones["watch_zone"]),
        chase_zone=_zone_dict(zones["chase_zone"]),
        buy_zone_context=buy_zone_context,
        core_max_pct=position_plan["core_max_pct"],
        trade_max_pct=position_plan["trade_max_pct"],
        allowed_add_pct=position_plan["allowed_add_pct"],
        price_position=price_position,
        entry_reference_low=entry_display["entry_reference_low"],
        entry_reference_high=entry_display["entry_reference_high"],
        next_action_price=entry_display["next_action_price"],
        chase_above_price=entry_display["chase_above_price"],
        current_vs_entry_pct=entry_display["current_vs_entry_pct"],
        missing_entry_fields=list(entry_display.get("missing_entry_fields") or []),
        technical_entry_zone_low=entry_display.get("technical_entry_zone_low"),
        technical_entry_zone_high=entry_display.get("technical_entry_zone_high"),
        effective_technical_entry_zone_low=entry_display.get("effective_technical_entry_zone_low"),
        effective_technical_entry_zone_high=entry_display.get("effective_technical_entry_zone_high"),
        technical_chase_overlap=bool(entry_display.get("technical_chase_overlap")),
        technical_entry_source=str(entry_display.get("technical_entry_source") or ""),
        technical_entry_reason=str(entry_display.get("technical_entry_reason") or ""),
        technical_entry_missing_fields=list(entry_display.get("technical_entry_missing_fields") or []),
        technical_entry_missing_reason=str(entry_display.get("technical_entry_missing_reason") or ""),
        technical_entry_confidence=str(entry_display.get("technical_entry_confidence") or ""),
        technical_structure_status=str(entry_display.get("technical_structure_status") or ""),
        technical_structure_label=str(entry_display.get("technical_structure_label") or ""),
        technical_pullback_zone_low=entry_display.get("technical_pullback_zone_low"),
        technical_pullback_zone_high=entry_display.get("technical_pullback_zone_high"),
        adaptive_pullback_zone_low=entry_display.get("adaptive_pullback_zone_low"),
        adaptive_pullback_zone_high=entry_display.get("adaptive_pullback_zone_high"),
        adaptive_pullback_label=str(entry_display.get("adaptive_pullback_label") or ""),
        adaptive_pullback_type=str(entry_display.get("adaptive_pullback_type") or ""),
        adaptive_pullback_confidence=str(entry_display.get("adaptive_pullback_confidence") or ""),
        adaptive_pullback_reason=str(entry_display.get("adaptive_pullback_reason") or ""),
        adaptive_pullback_is_entry_signal=bool(entry_display.get("adaptive_pullback_is_entry_signal")),
        technical_repair_zone_low=entry_display.get("technical_repair_zone_low"),
        technical_repair_zone_high=entry_display.get("technical_repair_zone_high"),
        near_term_repair_zone_low=entry_display.get("near_term_repair_zone_low"),
        near_term_repair_zone_high=entry_display.get("near_term_repair_zone_high"),
        trend_reclaim_zone_low=entry_display.get("trend_reclaim_zone_low"),
        trend_reclaim_zone_high=entry_display.get("trend_reclaim_zone_high"),
        valuation_reference_zone_low=entry_display.get("valuation_reference_zone_low"),
        valuation_reference_zone_high=entry_display.get("valuation_reference_zone_high"),
        deep_support_zone_low=entry_display.get("deep_support_zone_low"),
        deep_support_zone_high=entry_display.get("deep_support_zone_high"),
        zone_semantic_label=str(entry_display.get("zone_semantic_label") or ""),
        primary_entry_interpretation=str(entry_display.get("primary_entry_interpretation") or ""),
        support_watch_zone_low=entry_display.get("support_watch_zone_low"),
        support_watch_zone_high=entry_display.get("support_watch_zone_high"),
        confirmation_price=entry_display.get("confirmation_price"),
        invalidation_price=entry_display.get("invalidation_price"),
        technical_structure_reason=str(entry_display.get("technical_structure_reason") or ""),
        technical_missing_fields=list(entry_display.get("technical_missing_fields") or []),
        next_technical_steps=list(entry_display.get("next_technical_steps") or []),
        technical_position=str(entry_display.get("technical_position") or ""),
        entry_context_status=str(entry_display.get("entry_context_status") or price_position),
        nearest_support_price=technical_entry.get("nearest_support_price"),
        ema20=technical_entry.get("ema20"),
        ema50=technical_entry.get("ema50"),
        ema100=technical_entry.get("ema100"),
        ema200=technical_entry.get("ema200"),
        atr14=technical_entry.get("atr14"),
        recent_swing_low=technical_entry.get("recent_swing_low"),
        recent_breakout_level=technical_entry.get("recent_breakout_level"),
        entry_display_label=entry_display["entry_display_label"],
        entry_display_reason=entry_display["entry_display_reason"],
        entry_action_hint=entry_display["entry_action_hint"],
        block_reasons=block_reasons,
        summary=_summary(symbol, decision, position_plan["allowed_add_pct"], block_reasons),
        bull_points=list(bull_points or []),
        risk_points=list(risk_points or []),
        watch_points=list(watch_points or []),
        debug=debug,
    )


def build_cached_ai_stock_radar_report(
    ticker: str,
    *,
    path: Path = CACHE_PATH,
    now: datetime | None = None,
    quote_max_age_hours: float | None = 24,
) -> RadarReport:
    symbol = _symbol(ticker)
    snapshot = _read_cached_snapshot(path, symbol)
    history = build_market_history(symbol, path=path, now=now, quote_max_age_hours=quote_max_age_hours)
    technicals: dict[str, Any] | None = None
    if history is not None and not history.empty:
        technicals = latest_technical_snapshot(add_technical_indicators(history))
    snapshot = dict(snapshot or {"ticker": symbol, "symbol": symbol})
    snapshot.setdefault("ticker", symbol)
    snapshot.setdefault("symbol", symbol)
    company_name = str(snapshot.get("company_name") or snapshot.get("companyName") or symbol)
    return build_ai_stock_radar_report(
        symbol,
        company_name=company_name,
        path=path,
        snapshot=snapshot if technicals else None,
        technicals=technicals,
        now=now,
        quote_max_age_hours=quote_max_age_hours,
    )


def _read_cached_snapshot(path: Path, symbol: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with closing(sqlite3.connect(path)) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'quote_snapshots'"
        ).fetchone()
        if not table:
            return None
        row = conn.execute("SELECT payload_json FROM quote_snapshots WHERE ticker = ?", (symbol,)).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row[0] or "{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def build_ai_stock_radar_list_row(
    ticker: str,
    *,
    company_name: str = "",
    path: Path = CACHE_PATH,
    scores: RadarScores | dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
    technicals: dict[str, Any] | None = None,
    buy_zone: RadarZone | dict[str, Any] | None = None,
    watch_zone: RadarZone | dict[str, Any] | None = None,
    chase_zone: RadarZone | dict[str, Any] | None = None,
    now: datetime | None = None,
    quote_max_age_hours: float | None = 24,
) -> dict[str, Any]:
    symbol = _symbol(ticker)
    market = build_market_context(symbol, path=path, now=now, quote_max_age_hours=quote_max_age_hours)
    current_price = _number(market.get("currentPrice"))
    metrics = _radar_metrics(snapshot, technicals, market) if snapshot is not None and technicals is not None else {}
    score_input = _score_input(scores, snapshot=snapshot, technicals=technicals, market=market)
    zones = calculate_price_zones(
        symbol,
        path=path,
        market=market,
        scores=score_input,
        buy_zone=buy_zone,
        watch_zone=watch_zone,
        chase_zone=chase_zone,
        metrics=metrics,
    )
    data_status = _data_status(market, score_input, zones["buy_zone"])
    price_position = calculate_price_position(current_price, zones["buy_zone"], zones["chase_zone"], data_status)
    block_reasons = _block_reasons(
        current_price,
        market,
        score_input,
        zones["buy_zone"],
        zones["chase_zone"],
        data_status,
        price_position=price_position,
    )
    decision = calculate_decision(
        current_price=current_price,
        scores=score_input,
        buy_zone=zones["buy_zone"],
        chase_zone=zones["chase_zone"],
        data_status=data_status,
        block_reasons=block_reasons,
    )
    risk_incomplete = _risk_fields_incomplete(metrics)
    technical_entry = build_technical_entry_zone(technicals or {}, data_status=data_status)
    entry_display = calculate_entry_display(
        current_price=current_price,
        buy_zone=zones["buy_zone"],
        chase_zone=zones["chase_zone"],
        technical_entry_zone=technical_entry,
        data_status=data_status,
        price_position=price_position,
        decision=decision,
        final_score=score_input.final_score,
        quality_score=score_input.quality_score,
        valuation_score=score_input.valuation_score,
        risk_score=score_input.risk_score,
    )
    buy_zone_context = _build_buy_zone_context(
        symbol=symbol,
        current_price=current_price,
        scores=score_input,
        metrics=metrics,
        technicals=technicals or {},
        technical_entry=technical_entry,
        entry_display=entry_display,
    )
    decision = calculate_decision(
        current_price=current_price,
        scores=score_input,
        buy_zone=zones["buy_zone"],
        chase_zone=zones["chase_zone"],
        data_status=data_status,
        block_reasons=block_reasons,
        buy_zone_context=buy_zone_context,
    )
    position_plan = calculate_position_plan(score_input, decision, risk_incomplete=risk_incomplete)
    entry_display = calculate_entry_display(
        current_price=current_price,
        buy_zone=zones["buy_zone"],
        chase_zone=zones["chase_zone"],
        technical_entry_zone=technical_entry,
        data_status=data_status,
        price_position=price_position,
        decision=decision,
        final_score=score_input.final_score,
        quality_score=score_input.quality_score,
        valuation_score=score_input.valuation_score,
        risk_score=score_input.risk_score,
        buy_zone_context=buy_zone_context,
    )
    return {
        "ticker": symbol,
        "company_name": company_name or symbol,
        "current_price": current_price,
        "price_source": market.get("priceSource") or "missing",
        "data_updated_at": market.get("fetchedAt"),
        "is_stale": bool(market.get("isStale")),
        "history_status": market.get("historyStatus") or "missing",
        "decision": decision,
        "final_score": score_input.final_score,
        "buy_zone_context": buy_zone_context,
        "buy_zone": _zone_dict(zones["buy_zone"]),
        "core_max_pct": position_plan["core_max_pct"],
        "trade_max_pct": position_plan["trade_max_pct"],
        "price_position": price_position,
        **entry_display,
        **_score_gap_debug(scores),
        "nearest_support_price": technical_entry.get("nearest_support_price"),
        "ema20": technical_entry.get("ema20"),
        "ema50": technical_entry.get("ema50"),
        "ema100": technical_entry.get("ema100"),
        "ema200": technical_entry.get("ema200"),
        "atr14": technical_entry.get("atr14"),
        "recent_swing_low": technical_entry.get("recent_swing_low"),
        "recent_breakout_level": technical_entry.get("recent_breakout_level"),
        "block_reasons": block_reasons,
        "data_status": data_status,
    }


def _build_buy_zone_context(
    *,
    symbol: str,
    current_price: float | None,
    scores: RadarScores,
    metrics: dict[str, Any],
    technicals: dict[str, Any],
    technical_entry: dict[str, Any],
    entry_display: dict[str, Any],
) -> dict[str, Any]:
    source = {
        **(metrics or {}),
        **(technicals or {}),
        **_buy_zone_context_technical_fields(technical_entry or {}),
        **(entry_display or {}),
        "ticker": symbol,
        "current_price": current_price,
        "final_score": scores.final_score,
        "risk_score": scores.risk_score,
    }
    volume_snapshot = {
        "volume_price_status": source.get("volume_price_status") or source.get("volumePriceStatus"),
        "volume_price_score": source.get("volume_price_score") or source.get("volumePriceScore"),
        "volume_ratio": source.get("volume_ratio") or source.get("volumeRatio"),
    }
    try:
        return build_buy_zone_context(source, volume_snapshot=volume_snapshot).to_dict()
    except Exception:
        return {}


def _buy_zone_context_technical_fields(technical_entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "technical_entry_zone_low": technical_entry.get("low"),
        "technical_entry_zone_high": technical_entry.get("high"),
        "effective_technical_entry_zone_low": technical_entry.get("low"),
        "effective_technical_entry_zone_high": technical_entry.get("high"),
        "technical_pullback_zone_low": technical_entry.get("technical_pullback_zone_low"),
        "technical_pullback_zone_high": technical_entry.get("technical_pullback_zone_high"),
        "near_term_repair_zone_low": technical_entry.get("near_term_repair_zone_low"),
        "near_term_repair_zone_high": technical_entry.get("near_term_repair_zone_high"),
        "deep_support_zone_low": technical_entry.get("deep_support_zone_low"),
        "deep_support_zone_high": technical_entry.get("deep_support_zone_high"),
        "support_watch_zone_low": technical_entry.get("support_watch_zone_low"),
        "support_watch_zone_high": technical_entry.get("support_watch_zone_high"),
        "confirmation_price": technical_entry.get("confirmation_price"),
        "invalidation_price": technical_entry.get("invalidation_price"),
        "ma20": technical_entry.get("ema20"),
        "ma50": technical_entry.get("ema50"),
        "ma200": technical_entry.get("ema200"),
        "atr_14": technical_entry.get("atr14"),
        "recent_swing_high": technical_entry.get("recent_swing_high"),
        "recent_swing_low": technical_entry.get("recent_swing_low"),
    }


def calculate_quality_score(metrics: dict[str, Any]) -> float | None:
    usable = [
        _number(metrics.get("gross_margin")),
        _number(metrics.get("net_margin")),
        _number(metrics.get("fcf_margin")),
        _number(metrics.get("roe")),
    ]
    if all(value is None for value in usable):
        return None
    score = 45.0
    gross_margin = _number(metrics.get("gross_margin"))
    net_margin = _number(metrics.get("net_margin"))
    fcf_margin = _number(metrics.get("fcf_margin"))
    roe = _number(metrics.get("roe"))
    if gross_margin is not None:
        score += _bounded(gross_margin * 100, 20, 80) * 0.22
    if net_margin is not None:
        score += _bounded(net_margin * 100, -20, 40) * 0.28
    if fcf_margin is not None:
        score += _bounded(fcf_margin * 100, -20, 35) * 0.32
    if roe is not None:
        score += _bounded(roe * 100, -20, 40) * 0.18
    score -= _missing_penalty(usable, penalty=5.0)
    return round(_bounded(score, 0, 100), 1)


def calculate_growth_score(metrics: dict[str, Any]) -> float | None:
    revenue_growth = _number(metrics.get("revenue_growth"))
    gain_20d = _number(metrics.get("gain_20d_pct"))
    gain_60d = _number(metrics.get("gain_60d_pct"))
    if revenue_growth is None and gain_20d is None and gain_60d is None:
        return None
    score = 50.0
    if revenue_growth is not None:
        score += _bounded(revenue_growth * 100, -20, 50) * 0.7
    if gain_20d is not None:
        score += _bounded(gain_20d, -30, 30) * 0.15
    if gain_60d is not None:
        score += _bounded(gain_60d, -50, 50) * 0.1
    score -= _missing_penalty([revenue_growth], penalty=10.0)
    return round(_bounded(score, 0, 100), 1)


def calculate_valuation_score(metrics: dict[str, Any]) -> float | None:
    forward_pe = _number(metrics.get("forward_pe"))
    trailing_pe = _number(metrics.get("trailing_pe"))
    ev_sales = _number(metrics.get("enterprise_to_revenue"))
    fcf_yield = _number(metrics.get("free_cash_flow_yield"))
    fcf_margin = _number(metrics.get("fcf_margin"))
    if all(value is None for value in (forward_pe, trailing_pe, ev_sales, fcf_yield, fcf_margin)):
        return None
    score = 55.0
    pe = forward_pe if forward_pe is not None else trailing_pe
    if pe is not None:
        score += (35 - min(max(pe, 0), 80)) * 0.55
    if ev_sales is not None:
        score += (8 - min(max(ev_sales, 0), 30)) * 1.1
    if fcf_yield is not None:
        score += _bounded(fcf_yield * 100, -5, 12) * 2.0
    elif fcf_margin is not None:
        score += _bounded(fcf_margin * 100, -20, 35) * 0.35
    score -= _missing_penalty([pe, ev_sales, fcf_yield], penalty=6.0)
    return round(_bounded(score, 0, 100), 1)


def calculate_technical_score(metrics: dict[str, Any]) -> float | None:
    price = _number(metrics.get("current_price"))
    high = _number(metrics.get("fifty_two_week_high"))
    low = _number(metrics.get("fifty_two_week_low"))
    rsi = _number(metrics.get("rsi14"))
    gain_20d = _number(metrics.get("gain_20d_pct"))
    if price is None and rsi is None and gain_20d is None:
        return None
    score = 50.0
    if price is not None and high and low and high > low:
        range_pos = (price - low) / (high - low)
        score += (0.45 - range_pos) * 45
    if rsi is not None:
        if rsi >= 75:
            score -= 18
        elif rsi >= 65:
            score -= 8
        elif 40 <= rsi <= 58:
            score += 8
        elif rsi < 30:
            score -= 6
    if gain_20d is not None and gain_20d > 25:
        score -= 10
    return round(_bounded(score, 0, 100), 1)


def calculate_risk_score(metrics: dict[str, Any]) -> float | None:
    net_debt_to_ebitda = _number(metrics.get("net_debt_to_ebitda"))
    current_ratio = _number(metrics.get("current_ratio"))
    debt = _number(metrics.get("debt"))
    cash = _number(metrics.get("cash"))
    if _risk_fields_incomplete(metrics):
        score = 58.0
    else:
        score = 70.0
    if net_debt_to_ebitda is not None:
        score -= max(0.0, net_debt_to_ebitda - 2.0) * 12
    elif debt is not None and cash is not None and debt > cash:
        score -= 10
    if current_ratio is not None and current_ratio < 1:
        score -= 12
    if bool(metrics.get("is_stale")):
        score -= 18
    return round(_bounded(score, 0, 100), 1)


def calculate_final_score(
    *,
    quality_score: float | None,
    growth_score: float | None,
    valuation_score: float | None,
    technical_score: float | None,
    risk_score: float | None,
) -> float | None:
    components = (quality_score, growth_score, valuation_score, technical_score, risk_score)
    if any(_number(value) is None for value in components):
        return None
    final = (
        _value(quality_score) * 0.24
        + _value(growth_score) * 0.18
        + _value(valuation_score) * 0.28
        + _value(technical_score) * 0.14
        + _value(risk_score) * 0.16
    )
    return round(_bounded(final, 0, 100), 1)


def calculate_price_zones(
    symbol: str,
    *,
    path: Path = CACHE_PATH,
    market: dict[str, Any] | None = None,
    scores: RadarScores | None = None,
    buy_zone: RadarZone | dict[str, Any] | None = None,
    watch_zone: RadarZone | dict[str, Any] | None = None,
    chase_zone: RadarZone | dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, RadarZone]:
    explicit = {
        "buy_zone": _zone(buy_zone),
        "watch_zone": _zone(watch_zone),
        "chase_zone": _zone(chase_zone),
    }
    plan = _load_plan(path, _symbol(symbol))
    zones = {
        "buy_zone": explicit["buy_zone"] or _plan_buy_zone(plan),
        "watch_zone": explicit["watch_zone"] or _plan_watch_zone(plan),
        "chase_zone": explicit["chase_zone"] or _plan_chase_zone(plan),
    }
    return zones


def calculate_valuation_reference_zones(
    market: dict[str, Any],
    scores: RadarScores,
    metrics: dict[str, Any],
) -> dict[str, RadarZone]:
    current_price = _number(market.get("currentPrice"))
    high = _number(metrics.get("fifty_two_week_high"))
    low = _number(metrics.get("fifty_two_week_low"))
    valuation_score = _number(scores.valuation_score)
    if current_price is None or high is None or low is None or high <= low or valuation_score is None:
        return {
            "buy_zone": RadarZone(label="missing_discipline_buy_zone"),
            "watch_zone": RadarZone(label="missing_watch_zone"),
            "chase_zone": RadarZone(label="missing_chase_zone"),
        }
    span = high - low
    if valuation_score >= 70:
        buy_upper_ratio = 0.45
    elif valuation_score >= 50:
        buy_upper_ratio = 0.35
    else:
        buy_upper_ratio = 0.25
    buy_lower_ratio = max(0.08, buy_upper_ratio - 0.16)
    watch_upper_ratio = min(0.68, buy_upper_ratio + 0.18)
    chase_lower_ratio = max(0.72, watch_upper_ratio + 0.14)
    return {
        "buy_zone": RadarZone(
            lower=round(low + span * buy_lower_ratio, 2),
            upper=round(low + span * buy_upper_ratio, 2),
            label="valuation_reference_zone",
        ),
        "watch_zone": RadarZone(
            lower=round(low + span * buy_upper_ratio, 2),
            upper=round(low + span * watch_upper_ratio, 2),
            label="valuation_watch_reference_zone",
        ),
        "chase_zone": RadarZone(
            lower=round(low + span * chase_lower_ratio, 2),
            upper=None,
            label="valuation_chase_reference_zone",
        ),
    }


def _zones_have_buy_upper(zones: dict[str, RadarZone]) -> bool:
    buy = zones.get("buy_zone") or RadarZone()
    watch = zones.get("watch_zone") or RadarZone()
    chase = zones.get("chase_zone") or RadarZone()
    if buy.upper is None:
        return False
    if buy.lower is not None and buy.lower > buy.upper:
        return False
    if watch.lower is not None and watch.upper is not None and watch.lower > watch.upper:
        return False
    if watch.upper is not None and watch.upper < buy.upper:
        return False
    if chase.lower is not None and chase.lower < buy.upper:
        return False
    return True


def calculate_position_plan(scores: RadarScores, decision: str, *, risk_incomplete: bool = False) -> dict[str, float]:
    if decision in {"DATA_MISSING", "BLOCK_CHASE", "AVOID"}:
        return {"core_max_pct": 0.0, "trade_max_pct": 0.0, "allowed_add_pct": 0.0}
    risk_modifier = _bounded((_value(scores.risk_score) - 35) / 45, 0.2, 1.0)
    if risk_incomplete:
        risk_modifier = min(risk_modifier, 0.45)
    final_score = _value(scores.final_score)
    valuation_score = _value(scores.valuation_score)
    core_max = max(0.0, min(8.0, (final_score - 65) * 0.35)) * risk_modifier
    trade_max = max(0.0, min(3.0, (valuation_score - 35) * 0.09)) * risk_modifier
    if final_score < 70:
        core_max = 0.0
    if valuation_score < 40:
        core_max = min(core_max, 2.0)
        trade_max = min(trade_max, 1.0)
    if risk_incomplete:
        core_max = min(core_max, 1.0)
        trade_max = min(trade_max, 1.0)
    allowed_add = min(3.0, max(core_max, trade_max)) if decision == "ALLOW_BUY" else 0.0
    return {
        "core_max_pct": round(_bounded(core_max, 0, 8), 1),
        "trade_max_pct": round(_bounded(trade_max, 0, 3), 1),
        "allowed_add_pct": round(_bounded(allowed_add, 0, 3), 1),
    }


def calculate_decision(
    *,
    current_price: float | None,
    scores: RadarScores,
    buy_zone: RadarZone,
    chase_zone: RadarZone,
    data_status: str,
    block_reasons: list[str],
    buy_zone_context: dict[str, Any] | None = None,
) -> str:
    if data_status in {"STALE", "MISSING_PRICE"}:
        return "DATA_MISSING"
    if current_price is None:
        return "DATA_MISSING"
    context_action = str((buy_zone_context or {}).get("current_action") or "").strip().upper()
    if not context_action or context_action in {"DATA_INSUFFICIENT", "DATA_MISSING", "NO_BUY_ZONE", "ZONE_MISSING"}:
        return "DATA_MISSING"
    if _value(scores.risk_score) < 35:
        return "AVOID"
    if context_action == "BLOCK_CHASE":
        return "BLOCK_CHASE"
    if context_action in {"RISK_REVIEW", "AVOID"}:
        return "AVOID"
    if context_action in {"WAIT_PULLBACK", "WAIT_CONFIRMATION"}:
        return "WAIT"
    if context_action in {"ALLOW_SMALL_BUY", "ALLOW_ADD_ON_PULLBACK"}:
        return "WAIT" if _decision_blocking_reasons(block_reasons) else "ALLOW_BUY"
    return "WAIT" if _decision_blocking_reasons(block_reasons) else "ALLOW_BUY"


def _decision_blocking_reasons(block_reasons: list[str]) -> list[str]:
    sizing_only_markers = (
        "missing discipline buy zone",
        "current price is below the discipline buy zone",
        "current price is above the discipline buy zone",
        "current price is in or above chase zone",
        "final score below 70",
        "core position is not allowed",
        "综合评分低于70",
        "系统不建议作为核心仓",
        "valuation score below 40",
        "heavy position is not allowed",
    )
    return [
        reason
        for reason in block_reasons
        if not any(marker in str(reason).lower() for marker in sizing_only_markers)
    ]


def calculate_price_position(
    current_price: float | None,
    buy_zone: RadarZone,
    chase_zone: RadarZone,
    data_status: str = "OK",
) -> str:
    if data_status != "OK" or current_price is None or buy_zone.upper is None:
        return "ZONE_MISSING"
    if chase_zone.lower is not None and current_price >= chase_zone.lower:
        return "IN_CHASE_ZONE"
    if buy_zone.lower is not None and current_price < buy_zone.lower:
        return "BELOW_BUY_ZONE"
    if current_price <= buy_zone.upper:
        return "IN_BUY_ZONE"
    return "ABOVE_BUY_ZONE"


def build_technical_entry_zone(technicals: dict[str, Any], *, data_status: str = "OK") -> dict[str, Any]:
    price = _first_number(technicals, "price", "current_price", "currentPrice")
    ema20 = _first_number(technicals, "ema20", "EMA20")
    ema50 = _first_number(technicals, "ema50", "EMA50")
    ema100 = _first_number(technicals, "ema100", "EMA100")
    ema200 = _first_number(technicals, "ema200", "EMA200")
    atr14 = _first_number(technicals, "atr14", "ATR14")
    recent_swing_low = _first_number(technicals, "recent_swing_low", "recentSwingLow")
    recent_swing_high = _first_number(technicals, "recent_swing_high", "recentSwingHigh")
    recent_breakout_level = _first_number(technicals, "recent_breakout_level", "recentBreakoutLevel")
    ema50_slope = _first_number(technicals, "ema50_slope_20d_pct", "ema50Slope20dPct")
    ema200_slope = _first_number(technicals, "ema200_slope_20d_pct", "ema200Slope20dPct")
    gain_20d_pct = _first_number(technicals, "gain_20d_pct", "gain20dPct")
    volume_trend = _first_number(technicals, "volume_trend", "volumeTrend")
    missing = [
        label
        for label, value in (
            ("current_price", price),
            ("ema20", ema20),
            ("ema50", ema50),
            ("ema200", ema200),
        )
        if value is None
    ]
    base = {
        "low": None,
        "high": None,
        "source": "",
        "reason": "",
        "missing_fields": missing,
        "missing_reason": "",
        "confidence": "missing" if missing else "unknown",
        "nearest_support_price": None,
        "ema20": ema20,
        "ema50": ema50,
        "ema100": ema100,
        "ema200": ema200,
        "atr14": atr14,
        "recent_swing_low": recent_swing_low,
        "recent_swing_high": recent_swing_high,
        "recent_breakout_level": recent_breakout_level,
        "technical_structure_status": "DATA_MISSING" if missing else "",
        "technical_structure_label": "数据不足" if missing else "",
        "technical_pullback_zone_low": None,
        "technical_pullback_zone_high": None,
        "adaptive_pullback_zone_low": None,
        "adaptive_pullback_zone_high": None,
        "adaptive_pullback_label": "",
        "adaptive_pullback_type": "",
        "adaptive_pullback_confidence": "missing" if missing else "",
        "adaptive_pullback_reason": "",
        "adaptive_pullback_is_entry_signal": False,
        "technical_repair_zone_low": None,
        "technical_repair_zone_high": None,
        "near_term_repair_zone_low": None,
        "near_term_repair_zone_high": None,
        "trend_reclaim_zone_low": None,
        "trend_reclaim_zone_high": None,
        "deep_support_zone_low": None,
        "deep_support_zone_high": None,
        "support_watch_zone_low": None,
        "support_watch_zone_high": None,
        "confirmation_price": None,
        "invalidation_price": None,
        "technical_structure_reason": "",
        "technical_missing_fields": missing,
        "next_technical_steps": [],
    }
    if data_status != "OK":
        reason = "技术回踩区暂缺：Radar 数据缺失或 stale，需先更新价格/技术缓存"
        return base | {
            "source": "data_unavailable",
            "reason": reason,
            "missing_fields": list(dict.fromkeys([*missing, "data_status"])),
            "missing_reason": reason,
            "confidence": "missing",
            "technical_structure_status": "DATA_MISSING",
            "technical_structure_label": "数据不足",
            "technical_structure_reason": reason,
            "technical_missing_fields": list(dict.fromkeys([*missing, "data_status"])),
            "next_technical_steps": ["先点击更新价格 / 更新技术，补齐有效缓存。"],
        }
    if missing:
        reason = "技术回踩区暂缺：缺 K 线历史 / EMA，不能生成技术回踩区"
        return base | {
            "source": "missing_technical_data",
            "reason": reason,
            "missing_reason": reason,
            "confidence": "missing",
            "technical_structure_status": "DATA_MISSING",
            "technical_structure_label": "数据不足",
            "technical_structure_reason": reason,
            "technical_missing_fields": missing,
            "next_technical_steps": ["点击更新技术，补齐 K 线、EMA 和 ATR。"],
        }
    assert price is not None and ema20 is not None and ema50 is not None and ema200 is not None
    buffer = _technical_zone_buffer(price, atr14)
    near_term_repair_zone = _near_term_repair_zone(price, ema20, ema50, recent_swing_low, atr14)
    trend_reclaim_zone = _trend_reclaim_zone(price, ema100, ema200, recent_breakout_level, atr14)
    repair_zone = near_term_repair_zone
    support_watch_zone = _technical_observation_zone((recent_swing_low,), buffer * 0.6)
    deep_support_zone = support_watch_zone
    confirmation_price = _technical_confirmation_price(price, ema20, ema50, ema200, recent_swing_high)
    invalidation_price = _technical_invalidation_price(price, recent_swing_low, ema200, buffer)
    adaptive_common = {
        "adaptive_pullback_zone_low": None,
        "adaptive_pullback_zone_high": None,
        "adaptive_pullback_label": "",
        "adaptive_pullback_type": "",
        "adaptive_pullback_confidence": "",
        "adaptive_pullback_reason": "",
        "adaptive_pullback_is_entry_signal": False,
    }
    breakdown_evidence = _technical_breakdown_evidence(
        price=price,
        ema200=ema200,
        recent_swing_low=recent_swing_low,
        ema50_slope=ema50_slope,
        ema200_slope=ema200_slope,
        gain_20d_pct=gain_20d_pct,
        volume_trend=volume_trend,
    )
    if breakdown_evidence:
        reason = "技术结构：破位复核；" + "；".join(breakdown_evidence)
        adaptive = _adaptive_pullback_zone(
            status="BREAKDOWN_REVIEW",
            price=price,
            atr14=atr14,
            ema20=ema20,
            ema50=ema50,
            recent_swing_low=recent_swing_low,
            recent_swing_high=recent_swing_high,
            near_term_repair_zone=near_term_repair_zone,
            support_watch_zone=support_watch_zone,
            confirmation_price=confirmation_price,
            invalidation_price=invalidation_price,
        )
        return base | {
            **adaptive,
            "source": "breakdown_review",
            "reason": reason,
            "missing_fields": [],
            "missing_reason": reason,
            "confidence": "review",
            "technical_structure_status": "BREAKDOWN_REVIEW",
            "technical_structure_label": "破位复核",
            "technical_repair_zone_low": repair_zone[0],
            "technical_repair_zone_high": repair_zone[1],
            "near_term_repair_zone_low": near_term_repair_zone[0],
            "near_term_repair_zone_high": near_term_repair_zone[1],
            "trend_reclaim_zone_low": trend_reclaim_zone[0],
            "trend_reclaim_zone_high": trend_reclaim_zone[1],
            "deep_support_zone_low": deep_support_zone[0],
            "deep_support_zone_high": deep_support_zone[1],
            "support_watch_zone_low": support_watch_zone[0],
            "support_watch_zone_high": support_watch_zone[1],
            "confirmation_price": confirmation_price,
            "invalidation_price": invalidation_price,
            "technical_structure_reason": reason,
            "technical_missing_fields": [],
            "next_technical_steps": [
                "不把下跌自动当买点。",
                "等待重新站回关键均线或重新构建支撑。",
                "复核相对强弱和放量下杀是否缓和。",
            ],
        }
    if price < ema200 or ema50 < ema200:
        status = (
            "RANGE_BASE_BUILDING"
            if _range_base_building(price, ema20, recent_swing_low, gain_20d_pct, volume_trend)
            else "WEAK_TREND_REPAIR"
        )
        label = "区间筑底" if status == "RANGE_BASE_BUILDING" else "弱趋势修复中"
        reason = (
            f"技术结构：{label}；价格或 EMA50 低于 EMA200，不自动生成技术买点，当前不是技术买点；"
            "等待重新站回关键均线并收盘确认"
        )
        adaptive = _adaptive_pullback_zone(
            status=status,
            price=price,
            atr14=atr14,
            ema20=ema20,
            ema50=ema50,
            recent_swing_low=recent_swing_low,
            recent_swing_high=recent_swing_high,
            near_term_repair_zone=near_term_repair_zone,
            support_watch_zone=support_watch_zone,
            confirmation_price=confirmation_price,
            invalidation_price=invalidation_price,
        )
        return base | {
            **adaptive,
            "source": "trend_review",
            "reason": reason,
            "missing_fields": [],
            "missing_reason": reason,
            "confidence": "review",
            "technical_structure_status": status,
            "technical_structure_label": label,
            "technical_repair_zone_low": repair_zone[0],
            "technical_repair_zone_high": repair_zone[1],
            "near_term_repair_zone_low": near_term_repair_zone[0],
            "near_term_repair_zone_high": near_term_repair_zone[1],
            "trend_reclaim_zone_low": trend_reclaim_zone[0],
            "trend_reclaim_zone_high": trend_reclaim_zone[1],
            "deep_support_zone_low": deep_support_zone[0],
            "deep_support_zone_high": deep_support_zone[1],
            "support_watch_zone_low": support_watch_zone[0],
            "support_watch_zone_high": support_watch_zone[1],
            "confirmation_price": confirmation_price,
            "invalidation_price": invalidation_price,
            "technical_structure_reason": reason,
            "technical_missing_fields": [],
            "next_technical_steps": [
                "收盘重新站回 EMA20 / EMA50 / EMA200。",
                "不再创新低，并观察支撑是否被反复守住。",
                "确认相对强于 QQQ / 同行业后再复核。",
                "若跌破 recent swing low 或放量下杀，转为破位复核。",
            ],
        }
    slope_confirmed = (ema50_slope is None or ema50_slope >= -0.5) and (ema200_slope is None or ema200_slope >= -0.5)
    if not slope_confirmed:
        reason = "技术结构：弱趋势修复中；EMA50 / EMA200 斜率未确认向上，先等趋势修复"
        adaptive = _adaptive_pullback_zone(
            status="WEAK_TREND_REPAIR",
            price=price,
            atr14=atr14,
            ema20=ema20,
            ema50=ema50,
            recent_swing_low=recent_swing_low,
            recent_swing_high=recent_swing_high,
            near_term_repair_zone=near_term_repair_zone,
            support_watch_zone=support_watch_zone,
            confirmation_price=confirmation_price,
            invalidation_price=invalidation_price,
        )
        return base | {
            **adaptive,
            "source": "trend_review",
            "reason": reason,
            "missing_fields": [],
            "missing_reason": reason,
            "confidence": "review",
            "technical_structure_status": "WEAK_TREND_REPAIR",
            "technical_structure_label": "弱趋势修复中",
            "technical_repair_zone_low": repair_zone[0],
            "technical_repair_zone_high": repair_zone[1],
            "near_term_repair_zone_low": near_term_repair_zone[0],
            "near_term_repair_zone_high": near_term_repair_zone[1],
            "trend_reclaim_zone_low": trend_reclaim_zone[0],
            "trend_reclaim_zone_high": trend_reclaim_zone[1],
            "deep_support_zone_low": deep_support_zone[0],
            "deep_support_zone_high": deep_support_zone[1],
            "support_watch_zone_low": support_watch_zone[0],
            "support_watch_zone_high": support_watch_zone[1],
            "confirmation_price": confirmation_price,
            "invalidation_price": invalidation_price,
            "technical_structure_reason": reason,
            "technical_missing_fields": [],
            "next_technical_steps": [
                "等待 EMA50 / EMA200 斜率企稳。",
                "收盘站回 EMA20 / EMA50 后再复核。",
            ],
        }
    supports = [value for value in (ema20, ema50, recent_swing_low, recent_breakout_level) if value is not None and value > 0]
    nearby_supports = [value for value in supports if value <= price * 1.08]
    if not nearby_supports:
        missing_support = []
        if recent_swing_low is None:
            missing_support.append("recent_swing_low")
        if recent_breakout_level is None:
            missing_support.append("recent_breakout_level")
        reason = "技术回踩区暂缺：缺少 EMA / swing 附近支撑，不能生成技术回踩区"
        return base | {
            "source": "missing_nearby_support",
            "reason": reason,
            "missing_fields": missing_support or ["nearby_support"],
            "missing_reason": reason,
            "confidence": "missing",
            "technical_structure_status": "DATA_MISSING",
            "technical_structure_label": "数据不足",
            "technical_structure_reason": reason,
            "technical_missing_fields": missing_support or ["nearby_support"],
            "next_technical_steps": ["补齐 swing low / breakout level，或等待新的支撑位形成。"],
        }
    low = max(0.01, min(nearby_supports) - buffer)
    high = max(nearby_supports) + buffer * 0.5
    nearest = min(nearby_supports, key=lambda value: abs(price - value))
    pullback_low = round(low, 2)
    pullback_high = round(high, 2)
    reason = "强趋势结构下，技术回踩区参考 EMA20 / EMA50 / 近期支撑，并用 ATR 做缓冲"
    return base | {
        "low": pullback_low,
        "high": pullback_high,
        "source": "ema_pullback",
        "reason": reason,
        "missing_fields": [],
        "missing_reason": "",
        "confidence": "high" if atr14 is not None else "medium",
        "nearest_support_price": round(nearest, 2),
        "technical_structure_status": "UPTREND_PULLBACK",
        "technical_structure_label": "强趋势回踩",
        "technical_pullback_zone_low": pullback_low,
        "technical_pullback_zone_high": pullback_high,
        "adaptive_pullback_zone_low": pullback_low,
        "adaptive_pullback_zone_high": pullback_high,
        "adaptive_pullback_label": "技术回踩区",
        "adaptive_pullback_type": "UPTREND_PULLBACK",
        "adaptive_pullback_confidence": "high" if atr14 is not None else "medium",
        "adaptive_pullback_reason": "强趋势近端回踩复核区",
        "adaptive_pullback_is_entry_signal": True,
        "technical_repair_zone_low": repair_zone[0],
        "technical_repair_zone_high": repair_zone[1],
        "near_term_repair_zone_low": near_term_repair_zone[0],
        "near_term_repair_zone_high": near_term_repair_zone[1],
        "trend_reclaim_zone_low": trend_reclaim_zone[0],
        "trend_reclaim_zone_high": trend_reclaim_zone[1],
        "deep_support_zone_low": deep_support_zone[0],
        "deep_support_zone_high": deep_support_zone[1],
        "support_watch_zone_low": support_watch_zone[0],
        "support_watch_zone_high": support_watch_zone[1],
        "confirmation_price": confirmation_price,
        "invalidation_price": invalidation_price,
        "technical_structure_reason": reason,
        "technical_missing_fields": [],
        "next_technical_steps": [
            "回踩区内观察止跌和缩量。",
            "收盘守住 EMA20 / EMA50 或 recent swing low。",
            "确认相对强弱没有明显恶化。",
        ],
    }


def _technical_zone_buffer(price: float, atr14: float | None) -> float:
    atr = _number(atr14)
    if atr is not None and atr > 0:
        return min(price * 0.06, max(atr * 0.6, price * 0.012))
    return price * 0.018


def _technical_observation_zone(values: tuple[float | None, ...], buffer: float) -> tuple[float | None, float | None]:
    usable = [value for value in values if value is not None and value > 0]
    if not usable:
        return None, None
    return round(max(0.01, min(usable) - buffer), 2), round(max(usable) + buffer * 0.5, 2)


def _adaptive_pullback_zone(
    *,
    status: str,
    price: float,
    atr14: float | None,
    ema20: float | None,
    ema50: float | None,
    recent_swing_low: float | None,
    recent_swing_high: float | None,
    near_term_repair_zone: tuple[float | None, float | None],
    support_watch_zone: tuple[float | None, float | None],
    confirmation_price: float | None,
    invalidation_price: float | None,
) -> dict[str, Any]:
    status = str(status or "").upper()
    label_by_status = {
        "WEAK_TREND_REPAIR": ("WEAK_TREND_REVIEW", "弱趋势复核区", "弱趋势下观察承接，不是自动买点"),
        "BREAKDOWN_REVIEW": ("BREAKDOWN_RETEST", "破位反抽复核区", "不是买点，只有重新站回后才复核"),
        "RANGE_BASE_BUILDING": ("RANGE_SUPPORT", "箱体支撑观察区", "观察缩量回踩和放量突破"),
    }
    adaptive_type, label, reason = label_by_status.get(
        status, ("TECHNICAL_REFERENCE", "技术回踩参考区", "技术结构参考区，不是自动买点")
    )
    atr = _number(atr14)
    buffer = atr * 0.5 if atr is not None and atr > 0 else max(price * 0.018, 0.01)
    base_zone = near_term_repair_zone
    if status == "RANGE_BASE_BUILDING" and _zone_complete(support_watch_zone):
        base_zone = support_watch_zone
    if status == "BREAKDOWN_REVIEW":
        base_zone = _technical_observation_zone((ema20, ema50, recent_swing_low), buffer)
    low, high = base_zone
    if not _zone_complete((low, high)):
        low, high = support_watch_zone
    if not _zone_complete((low, high)):
        low, high = _technical_observation_zone((price, ema20, ema50, recent_swing_low, recent_swing_high), buffer)
    low = _number(low)
    high = _number(high)
    if low is None or high is None:
        return {
            "adaptive_pullback_zone_low": None,
            "adaptive_pullback_zone_high": None,
            "adaptive_pullback_label": "",
            "adaptive_pullback_type": "",
            "adaptive_pullback_confidence": "missing",
            "adaptive_pullback_reason": "缺少价格、均线或支撑位，暂时无法生成技术回踩参考区",
            "adaptive_pullback_is_entry_signal": False,
        }
    if low > high:
        low, high = high, low
    max_distance = buffer * 5 if atr is not None and atr > 0 else price * 0.12
    low = max(low, price - max_distance)
    high = min(high, price + max_distance)
    if confirmation_price is not None:
        high = min(high, confirmation_price + buffer)
    if invalidation_price is not None and low < invalidation_price - buffer:
        low = max(invalidation_price, low)
    if low > high:
        low, high = high, low
    return {
        "adaptive_pullback_zone_low": round(max(0.01, low), 2),
        "adaptive_pullback_zone_high": round(max(0.01, high), 2),
        "adaptive_pullback_label": label,
        "adaptive_pullback_type": adaptive_type,
        "adaptive_pullback_confidence": "review",
        "adaptive_pullback_reason": reason,
        "adaptive_pullback_is_entry_signal": False,
    }


def _zone_complete(zone: tuple[float | None, float | None]) -> bool:
    low, high = zone
    return _number(low) is not None and _number(high) is not None


def _near_term_repair_zone(
    price: float,
    ema20: float | None,
    ema50: float | None,
    recent_swing_low: float | None,
    atr14: float | None,
) -> tuple[float | None, float | None]:
    candidates = [price]
    candidates.extend(value for value in (ema20, ema50) if value is not None and value <= price * 1.12)
    if recent_swing_low is not None and recent_swing_low >= price * 0.92:
        candidates.append(recent_swing_low)
    low_buffer = _near_repair_low_buffer(price, atr14)
    high_buffer = _near_repair_high_buffer(price, atr14)
    return round(max(0.01, min(candidates) - low_buffer), 2), round(max(candidates) + high_buffer, 2)


def _trend_reclaim_zone(
    price: float,
    ema100: float | None,
    ema200: float | None,
    recent_breakout_level: float | None,
    atr14: float | None,
) -> tuple[float | None, float | None]:
    anchor = ema200 or ema100 or recent_breakout_level
    if anchor is None or anchor <= 0:
        return None, None
    lower_buffer = max(_value(atr14) * 1.2, price * 0.06)
    upper_buffer = min(_value(atr14) * 0.3, price * 0.03)
    low = max(0.01, anchor - lower_buffer)
    high = anchor + upper_buffer
    return round(low, 2), round(high, 2)


def _near_repair_low_buffer(price: float, atr14: float | None) -> float:
    atr = _number(atr14)
    if atr is not None and atr > 0:
        return min(price * 0.028, atr * 0.34)
    return price * 0.02


def _near_repair_high_buffer(price: float, atr14: float | None) -> float:
    atr = _number(atr14)
    if atr is not None and atr > 0:
        return min(price * 0.006, atr * 0.08)
    return price * 0.006


def _technical_confirmation_price(
    price: float,
    ema20: float | None,
    ema50: float | None,
    ema200: float | None,
    recent_swing_high: float | None,
) -> float | None:
    candidates = [value for value in (ema20, ema50, ema200, recent_swing_high) if value is not None and value > price]
    if candidates:
        return round(min(candidates), 2)
    fallback = max([value for value in (ema20, ema50, ema200) if value is not None], default=None)
    return round(fallback, 2) if fallback is not None else None


def _technical_invalidation_price(
    price: float,
    recent_swing_low: float | None,
    ema200: float | None,
    buffer: float,
) -> float | None:
    if recent_swing_low is not None:
        return round(recent_swing_low, 2)
    if ema200 is not None and ema200 < price:
        return round(max(0.01, ema200 - buffer), 2)
    return None


def _technical_breakdown_evidence(
    *,
    price: float,
    ema200: float,
    recent_swing_low: float | None,
    ema50_slope: float | None,
    ema200_slope: float | None,
    gain_20d_pct: float | None,
    volume_trend: float | None,
) -> list[str]:
    evidence: list[str] = []
    if recent_swing_low is not None and price < recent_swing_low:
        evidence.append("当前价跌破 recent swing low")
    if price < ema200 and (gain_20d_pct is not None and gain_20d_pct < -8) and (
        volume_trend is not None and volume_trend > 0.35
    ):
        evidence.append("跌破 EMA200 且放量下杀")
    return evidence


def _range_base_building(
    price: float,
    ema20: float | None,
    recent_swing_low: float | None,
    gain_20d_pct: float | None,
    volume_trend: float | None,
) -> bool:
    if recent_swing_low is None or price < recent_swing_low:
        return False
    if ema20 is None or price < ema20:
        return False
    if gain_20d_pct is None or volume_trend is None:
        return False
    return gain_20d_pct >= -5 and volume_trend <= 0.05


SCORE_FIELD_USAGE = {
    "quality_score": ["gross_margin", "net_margin", "fcf_margin", "roe"],
    "growth_score": ["revenue_growth", "gain_20d_pct", "gain_60d_pct"],
    "valuation_score": ["forward_pe", "trailing_pe", "enterprise_to_revenue", "free_cash_flow_yield", "fcf_margin"],
    "technical_score": ["current_price", "fifty_two_week_high", "fifty_two_week_low", "rsi14", "gain_20d_pct"],
    "risk_score": ["net_debt_to_ebitda", "current_ratio", "debt", "cash", "is_stale"],
    "final_score": ["quality_score", "growth_score", "valuation_score", "technical_score", "risk_score"],
    "price_zones": ["manual_zone", "stock_action_plan"],
    "position_plan": ["decision", "final_score", "valuation_score", "risk_score"],
}

RADAR_INPUT_ALIASES: dict[str, tuple[tuple[str, str], ...]] = {
    "current_price": (
        ("market", "currentPrice"),
        ("market", "current_price"),
        ("technicals", "current_price"),
        ("technicals", "price"),
        ("snapshot", "current_price"),
        ("snapshot", "currentPrice"),
    ),
    "fifty_two_week_high": (
        ("technicals", "fifty_two_week_high"),
        ("technicals", "fiftyTwoWeekHigh"),
        ("technicals", "52_week_high"),
        ("snapshot", "fifty_two_week_high"),
        ("snapshot", "fiftyTwoWeekHigh"),
        ("snapshot", "52_week_high"),
    ),
    "fifty_two_week_low": (
        ("technicals", "fifty_two_week_low"),
        ("technicals", "fiftyTwoWeekLow"),
        ("technicals", "52_week_low"),
        ("snapshot", "fifty_two_week_low"),
        ("snapshot", "fiftyTwoWeekLow"),
        ("snapshot", "52_week_low"),
    ),
    "forward_pe": (("snapshot", "forward_pe"), ("snapshot", "forwardPE"), ("snapshot", "forwardPe")),
    "trailing_pe": (("snapshot", "trailing_pe"), ("snapshot", "trailingPE"), ("snapshot", "trailingPe"), ("snapshot", "pe")),
    "enterprise_to_revenue": (
        ("snapshot", "enterprise_to_revenue"),
        ("snapshot", "enterpriseToRevenue"),
        ("snapshot", "ev_to_sales"),
        ("snapshot", "evSales"),
    ),
    "free_cash_flow_yield": (
        ("snapshot", "free_cash_flow_yield"),
        ("snapshot", "freeCashFlowYield"),
        ("snapshot", "fcf_yield"),
    ),
    "fcf_margin": (("snapshot", "fcf_margin"), ("snapshot", "free_cash_flow_margin"), ("snapshot", "freeCashFlowMargin")),
    "gross_margin": (("snapshot", "gross_margin"), ("snapshot", "grossMargin")),
    "net_margin": (
        ("snapshot", "net_margin"),
        ("snapshot", "netMargin"),
        ("snapshot", "profit_margin"),
        ("snapshot", "profitMargin"),
        ("snapshot", "netProfitMargin"),
        ("snapshot", "netProfitMarginTTM"),
        ("snapshot", "profitMargins"),
    ),
    "roe": (("snapshot", "roe"), ("snapshot", "return_on_equity"), ("snapshot", "returnOnEquity")),
    "revenue_growth": (("snapshot", "revenue_growth"), ("snapshot", "revenueGrowth")),
    "gain_20d_pct": (("technicals", "gain_20d_pct"), ("technicals", "gain20dPct"), ("snapshot", "gain_20d_pct")),
    "gain_60d_pct": (("technicals", "gain_60d_pct"), ("technicals", "gain60dPct"), ("snapshot", "gain_60d_pct")),
    "rsi14": (("technicals", "rsi14"), ("technicals", "rsi_14"), ("snapshot", "rsi14")),
    "net_debt_to_ebitda": (("snapshot", "net_debt_to_ebitda"), ("snapshot", "netDebtToEbitda")),
    "current_ratio": (("snapshot", "current_ratio"), ("snapshot", "currentRatio")),
    "debt": (("snapshot", "debt"), ("snapshot", "total_debt"), ("snapshot", "totalDebt")),
    "cash": (
        ("snapshot", "cash"),
        ("snapshot", "total_cash"),
        ("snapshot", "totalCash"),
        ("snapshot", "cash_and_equivalents"),
        ("snapshot", "cashAndEquivalents"),
    ),
    "is_stale": (("market", "isStale"), ("market", "is_stale")),
}


def build_radar_debug(
    symbol: str,
    *,
    market: dict[str, Any],
    metrics: dict[str, Any],
    scores: RadarScores,
    zones: dict[str, RadarZone],
    buy_zone: RadarZone | dict[str, Any] | None = None,
    watch_zone: RadarZone | dict[str, Any] | None = None,
    chase_zone: RadarZone | dict[str, Any] | None = None,
    data_status: str,
    block_reasons: list[str],
    current_price: float | None = None,
    price_position: str = "ZONE_MISSING",
    technical_entry: dict[str, Any] | None = None,
    path: Path = CACHE_PATH,
) -> dict[str, Any]:
    plan = _load_plan(path, _symbol(symbol))
    zone_debug = _price_zone_debug(
        zones,
        explicit_zones={"buy_zone": buy_zone, "watch_zone": watch_zone, "chase_zone": chase_zone},
        plan=plan,
        current_price=current_price,
        price_position=price_position,
    )
    return {
        "score_inputs": {
            "quality_score": _score_debug("quality_score", metrics),
            "growth_score": _score_debug("growth_score", metrics),
            "valuation_score": _score_debug("valuation_score", metrics),
            "technical_score": _score_debug("technical_score", metrics),
            "risk_score": _score_debug("risk_score", metrics),
            "final_score": _final_score_debug(scores),
        },
        "price_zones": zone_debug,
        "price_position": price_position,
        "distance_to_buy_zone_pct": zone_debug.get("distance_to_buy_zone_pct"),
        "below_buy_zone_reason": zone_debug.get("below_buy_zone_reason"),
        "wait_reason_is_below_buy_zone": price_position == "BELOW_BUY_ZONE",
        "technical_entry_zone": technical_entry or {},
        "position_plan": {
            "used_fields": ["decision", "final_score", "valuation_score", "risk_score"],
            "missing_fields": _missing_score_fields(scores, ["final_score", "valuation_score", "risk_score"]),
            "risk_incomplete": _risk_fields_incomplete(metrics),
        },
        "data_status": data_status,
        "data_missing_fields": _data_missing_fields(data_status, market, scores, zones.get("buy_zone") or RadarZone()),
        "block_reasons": list(block_reasons),
        "input_normalization": metrics.get("_normalization", {}),
        "field_alias_notes": _field_alias_notes(metrics),
    }


def _score_debug(score_name: str, metrics: dict[str, Any]) -> dict[str, Any]:
    fields = SCORE_FIELD_USAGE[score_name]
    used = [field for field in fields if _field_number(metrics, field) is not None or _field_bool(metrics, field) is not None]
    missing = [field for field in fields if field not in used]
    positive, negative = _score_effect_fields(score_name, metrics)
    result = {
        "used_fields": used,
        "missing_fields": missing,
        "positive_fields": positive,
        "negative_fields": negative,
    }
    if score_name == "risk_score":
        result["risk_incomplete"] = _risk_fields_incomplete(metrics)
        if result["risk_incomplete"]:
            result["negative_fields"] = list(dict.fromkeys([*negative, "risk_fields_missing"]))
    return result


def _final_score_debug(scores: RadarScores) -> dict[str, Any]:
    values = {
        "quality_score": scores.quality_score,
        "growth_score": scores.growth_score,
        "valuation_score": scores.valuation_score,
        "technical_score": scores.technical_score,
        "risk_score": scores.risk_score,
    }
    used = [key for key, value in values.items() if _number(value) is not None]
    missing = [key for key in values if key not in used]
    return {
        "used_fields": used,
        "missing_fields": missing,
        "positive_fields": [key for key, value in values.items() if (_number(value) or 0) >= 70],
        "negative_fields": [key for key, value in values.items() if _number(value) is not None and (_number(value) or 0) < 50],
    }


def _score_effect_fields(score_name: str, metrics: dict[str, Any]) -> tuple[list[str], list[str]]:
    positive: list[str] = []
    negative: list[str] = []
    if score_name == "quality_score":
        for field in ("gross_margin", "net_margin", "fcf_margin", "roe"):
            value = _field_number(metrics, field)
            if value is None:
                continue
            (positive if value > 0 else negative).append(field)
    elif score_name == "growth_score":
        for field in ("revenue_growth", "gain_20d_pct", "gain_60d_pct"):
            value = _field_number(metrics, field)
            if value is None:
                continue
            (positive if value > 0 else negative).append(field)
    elif score_name == "valuation_score":
        pe = _field_number(metrics, "forward_pe") or _field_number(metrics, "trailing_pe")
        ev_sales = _field_number(metrics, "enterprise_to_revenue")
        fcf_yield = _field_number(metrics, "free_cash_flow_yield")
        fcf_margin = _field_number(metrics, "fcf_margin")
        if pe is not None:
            (positive if pe < 35 else negative).append("forward_pe/trailing_pe")
        if ev_sales is not None:
            (positive if ev_sales < 8 else negative).append("enterprise_to_revenue")
        if fcf_yield is not None:
            (positive if fcf_yield > 0 else negative).append("free_cash_flow_yield")
        elif fcf_margin is not None:
            (positive if fcf_margin > 0 else negative).append("fcf_margin")
    elif score_name == "technical_score":
        price = _field_number(metrics, "current_price")
        high = _field_number(metrics, "fifty_two_week_high")
        low = _field_number(metrics, "fifty_two_week_low")
        rsi = _field_number(metrics, "rsi14")
        gain_20d = _field_number(metrics, "gain_20d_pct")
        if price is not None and high and low and high > low:
            range_pos = (price - low) / (high - low)
            (positive if range_pos <= 0.45 else negative).append("52_week_position")
        if rsi is not None:
            if 40 <= rsi <= 58:
                positive.append("rsi14")
            elif rsi >= 65 or rsi < 30:
                negative.append("rsi14")
        if gain_20d is not None and gain_20d > 25:
            negative.append("gain_20d_pct")
    elif score_name == "risk_score":
        net_debt = _field_number(metrics, "net_debt_to_ebitda")
        current_ratio = _field_number(metrics, "current_ratio")
        debt = _field_number(metrics, "debt")
        cash = _field_number(metrics, "cash")
        if net_debt is not None and net_debt > 2:
            negative.append("net_debt_to_ebitda")
        if current_ratio is not None:
            (positive if current_ratio >= 1 else negative).append("current_ratio")
        if debt is not None and cash is not None:
            (positive if cash >= debt else negative).append("debt/cash")
        if bool(metrics.get("is_stale")):
            negative.append("is_stale")
    return positive, negative


def _price_zone_debug(
    zones: dict[str, RadarZone],
    *,
    explicit_zones: dict[str, RadarZone | dict[str, Any] | None],
    plan: dict[str, Any],
    current_price: float | None = None,
    price_position: str = "ZONE_MISSING",
) -> dict[str, Any]:
    sources = {
        name: _zone_source(name, zone, explicit_zones.get(name), plan)
        for name, zone in zones.items()
    }
    buy = zones.get("buy_zone") or RadarZone()
    return {
        "source": _overall_zone_source(sources),
        "zone_sources": sources,
        "price_position": price_position,
        "distance_to_buy_zone_pct": _distance_to_buy_zone_pct(current_price, buy, price_position),
        "below_buy_zone_reason": _below_buy_zone_reason() if price_position == "BELOW_BUY_ZONE" else "",
        "used_fields": SCORE_FIELD_USAGE["price_zones"],
        "missing_fields": _price_zone_missing_fields(zones),
    }


def _distance_to_buy_zone_pct(current_price: float | None, buy_zone: RadarZone, price_position: str) -> float | None:
    price = _number(current_price)
    if price is None:
        return None
    if price_position == "BELOW_BUY_ZONE" and buy_zone.lower:
        return round(((price - buy_zone.lower) / buy_zone.lower) * 100, 1)
    if price_position in {"ABOVE_BUY_ZONE", "IN_CHASE_ZONE"} and buy_zone.upper:
        return round(((price - buy_zone.upper) / buy_zone.upper) * 100, 1)
    if price_position == "IN_BUY_ZONE":
        return 0.0
    return None


def _below_buy_zone_reason() -> str:
    return (
        "Current price is below the discipline buy zone lower bound. "
        "This is not automatically a better buy point; review whether fundamentals deteriorated, "
        "earnings shocked the thesis, or the trend broke before treating it as actionable."
    )


def _zone_source(name: str, zone: RadarZone, explicit: RadarZone | dict[str, Any] | None, plan: dict[str, Any]) -> str:
    explicit_zone = _zone(explicit)
    if explicit_zone and _same_zone(zone, explicit_zone):
        return "manual_input"
    plan_zone = {
        "buy_zone": _plan_buy_zone,
        "watch_zone": _plan_watch_zone,
        "chase_zone": _plan_chase_zone,
    }[name](plan)
    if _same_zone(zone, plan_zone) and (plan_zone.lower is not None or plan_zone.upper is not None):
        return "stock_action_plan"
    if str(zone.label or "").startswith("derived_"):
        return "rules_derived"
    return "missing"


def _overall_zone_source(sources: dict[str, str]) -> str:
    if any(value == "manual_input" for value in sources.values()):
        return "manual_input"
    if any(value == "stock_action_plan" for value in sources.values()):
        return "stock_action_plan"
    if any(value == "rules_derived" for value in sources.values()):
        return "rules_derived"
    return "missing"


def _same_zone(left: RadarZone, right: RadarZone) -> bool:
    return _number(left.lower) == _number(right.lower) and _number(left.upper) == _number(right.upper)


def _price_zone_missing_fields(zones: dict[str, RadarZone]) -> list[str]:
    missing: list[str] = []
    buy = zones.get("buy_zone") or RadarZone()
    if buy.upper is None:
        missing.append("buy_zone.upper")
    return missing


def _data_missing_fields(data_status: str, market: dict[str, Any], scores: RadarScores, buy_zone: RadarZone) -> list[str]:
    if data_status == "STALE":
        return ["current_price_stale"]
    if data_status == "MISSING_PRICE":
        fields = ["current_price"]
        history_status = str(market.get("historyStatus") or market.get("history_status") or "").lower()
        if history_status in {"", "missing"} and _number(market.get("latestClose")) is None:
            fields.append("daily_bars")
        return fields
    if data_status == "MISSING_VALUATION":
        return _missing_valuation_fields()
    if data_status == "MISSING_SCORE":
        return _missing_score_fields(scores, SCORE_FIELD_USAGE["final_score"])
    if data_status == "MISSING_BUY_ZONE" or buy_zone.upper is None:
        return ["buy_zone.upper"]
    return []


def _missing_valuation_fields() -> list[str]:
    return ["forward_pe", "trailing_pe", "enterprise_to_revenue", "free_cash_flow_yield", "fcf_margin"]


def _missing_score_fields(scores: RadarScores, fields: list[str]) -> list[str]:
    return [field for field in fields if _number(getattr(scores, field, None)) is None]


def _field_alias_notes(metrics: dict[str, Any]) -> list[str]:
    normalization = metrics.get("_normalization") if isinstance(metrics.get("_normalization"), dict) else {}
    sources = normalization.get("canonical_sources") if isinstance(normalization.get("canonical_sources"), dict) else {}
    notes: list[str] = []
    for canonical, source in sources.items():
        raw_field = source.get("raw_field") if isinstance(source, dict) else None
        if raw_field and raw_field != canonical:
            notes.append(f"{raw_field} normalized to {canonical}.")
    if _risk_fields_incomplete(metrics):
        notes.append("risk fields missing; risk score uses conservative incomplete-risk treatment.")
    return notes


def _risk_fields_incomplete(metrics: dict[str, Any]) -> bool:
    if not metrics or "_normalization" not in metrics:
        return False
    return all(
        _field_number(metrics, field) is None
        for field in ("net_debt_to_ebitda", "current_ratio", "debt", "cash")
    )


def _field_number(metrics: dict[str, Any], field: str) -> float | None:
    return _number(metrics.get(field))


def _field_bool(metrics: dict[str, Any], field: str) -> bool | None:
    if field not in metrics or not isinstance(metrics.get(field), bool):
        return None
    return bool(metrics.get(field))


def _score_gap_debug(scores: RadarScores | dict[str, Any] | None) -> dict[str, Any]:
    return {
        "hard_missing_fields": _score_gap_list(scores, "hard_missing_fields", "hardMissingFields"),
        "not_disclosed_fields": _score_gap_list(scores, "not_disclosed_fields", "notDisclosedFields"),
        "not_applicable_fields": _score_gap_list(scores, "not_applicable_fields", "notApplicableFields"),
        "proxy_used_fields": _score_gap_list(scores, "proxy_used_fields", "proxyUsedFields", "proxy_metrics_used", "proxyMetricsUsed"),
        "confidence_penalty_reasons": _score_gap_list(scores, "confidence_penalty_reasons", "confidencePenaltyReasons"),
        "model_fit_notes": _score_gap_list(scores, "model_fit_notes", "modelFitNotes"),
    }


def _score_gap_list(scores: RadarScores | dict[str, Any] | None, *keys: str) -> list[str]:
    if scores is None or isinstance(scores, RadarScores):
        return []
    for key in keys:
        if isinstance(scores, dict):
            value = scores.get(key)
        else:
            value = getattr(scores, key, None)
        if isinstance(value, list):
            return [str(item) for item in value if item]
    return []


def _score_input(
    scores: RadarScores | dict[str, Any] | None,
    *,
    snapshot: dict[str, Any] | None,
    technicals: dict[str, Any] | None,
    market: dict[str, Any],
) -> RadarScores:
    if scores is not None:
        if isinstance(scores, RadarScores):
            return scores
        return RadarScores(
            final_score=_first_number(scores, "final_score", "finalScore", "total_score", "totalScore"),
            quality_score=_first_number(scores, "quality_score", "qualityScore"),
            growth_score=_first_number(scores, "growth_score", "growthScore"),
            valuation_score=_first_number(scores, "valuation_score", "valuationScore", "entry_score", "entryScore"),
            technical_score=_first_number(scores, "technical_score", "technicalScore"),
            risk_score=_first_number(scores, "risk_score", "riskScore"),
        )
    if snapshot is None or technicals is None:
        return RadarScores()
    metrics = _radar_metrics(snapshot, technicals, market)
    quality = calculate_quality_score(metrics)
    growth = calculate_growth_score(metrics)
    valuation = calculate_valuation_score(metrics)
    technical = calculate_technical_score(metrics)
    risk = calculate_risk_score(metrics)
    return RadarScores(
        final_score=calculate_final_score(
            quality_score=quality,
            growth_score=growth,
            valuation_score=valuation,
            technical_score=technical,
            risk_score=risk,
        ),
        quality_score=quality,
        growth_score=growth,
        valuation_score=valuation,
        technical_score=technical,
        risk_score=risk,
    )


def _plan_buy_zone(plan: dict[str, Any]) -> RadarZone:
    upper = _first_number(plan, "first_buy_price", "tranche_buy_high")
    lower = _first_number(plan, "second_buy_price", "third_buy_price", "tranche_buy_low", "heavy_buy_below")
    return RadarZone(lower=lower, upper=upper, label="discipline_buy_zone")


def _plan_watch_zone(plan: dict[str, Any]) -> RadarZone:
    return RadarZone(
        lower=_number(plan.get("fair_value_low")),
        upper=_number(plan.get("fair_value_high")),
        label="watch_zone",
    )


def _plan_chase_zone(plan: dict[str, Any]) -> RadarZone:
    upper = _number(plan.get("no_chase_above"))
    return RadarZone(lower=upper, upper=None, label="no_chase_above")


def _load_plan(path: Path, symbol: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path)) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'stock_action_plans'"
        ).fetchone()
        if not table:
            return {}
        cursor = conn.execute("SELECT * FROM stock_action_plans WHERE ticker = ?", (symbol,))
        row = cursor.fetchone()
        columns = [description[0] for description in cursor.description] if cursor.description else []
    return dict(zip(columns, row)) if row else {}


def _radar_metrics(
    snapshot: dict[str, Any] | None,
    technicals: dict[str, Any] | None,
    market: dict[str, Any],
) -> dict[str, Any]:
    return normalize_radar_inputs(snapshot=snapshot, technicals=technicals, market=market)


def normalize_radar_inputs(
    *,
    snapshot: dict[str, Any] | None = None,
    technicals: dict[str, Any] | None = None,
    market: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = snapshot or {}
    technicals = technicals or {}
    market = market or {}
    sources = {"snapshot": snapshot, "technicals": technicals, "market": market}
    metrics: dict[str, Any] = {}
    normalization: dict[str, Any] = {"aliases": {}, "canonical_sources": {}, "missing_canonical_fields": []}
    for canonical, aliases in RADAR_INPUT_ALIASES.items():
        matches = _normalization_matches(canonical, aliases, sources)
        if not matches:
            normalization["missing_canonical_fields"].append(canonical)
            continue
        chosen = matches[0]
        metrics[canonical] = chosen["value"]
        normalization["aliases"][canonical] = matches
        normalization["canonical_sources"][canonical] = {
            "source": chosen["source"],
            "raw_field": chosen["raw_field"],
        }
    metrics["price_source"] = market.get("priceSource") or market.get("price_source")
    metrics["history_status"] = market.get("historyStatus") or market.get("history_status")
    metrics["history_latest_date"] = market.get("historyLatestDate") or market.get("history_latest_date")
    metrics["_normalization"] = normalization
    return metrics


def _normalization_matches(
    canonical: str,
    aliases: tuple[tuple[str, str], ...],
    sources: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for source_name, raw_field in aliases:
        source = sources.get(source_name) or {}
        if raw_field not in source:
            continue
        value = _normalized_input_value(canonical, source.get(raw_field))
        if value is None:
            continue
        matches.append(
            {
                "canonical_field": canonical,
                "source": source_name,
                "raw_field": raw_field,
                "value": value,
            }
        )
    return matches


def _normalized_input_value(canonical: str, value: Any) -> Any:
    if canonical == "is_stale":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        return None
    return _number(value)


def _data_status(market: dict[str, Any], scores: RadarScores, buy_zone: RadarZone) -> str:
    if market.get("isStale"):
        return "STALE"
    if _number(market.get("currentPrice")) is None:
        return "MISSING_PRICE"
    return "OK"


def _block_reasons(
    current_price: float | None,
    market: dict[str, Any],
    scores: RadarScores,
    buy_zone: RadarZone,
    chase_zone: RadarZone,
    data_status: str,
    *,
    price_position: str = "ZONE_MISSING",
) -> list[str]:
    reasons: list[str] = []
    if data_status != "OK":
        reasons.append(_data_block_reason(data_status, market))
        return reasons
    if current_price is None:
        reasons.append("missing current price")
        return reasons
    if buy_zone.upper is None:
        reasons.append("missing discipline buy zone")
    elif price_position == "BELOW_BUY_ZONE":
        reasons.append(
            "current price is below the discipline buy zone lower bound; review fundamentals, earnings shock, or trend breakdown before treating it as cheaper"
        )
    elif current_price > buy_zone.upper:
        reasons.append("current price is above the discipline buy zone")
    if chase_zone.lower is not None and current_price >= chase_zone.lower:
        reasons.append("current price is in or above chase zone")
    if _value(scores.valuation_score) < 40:
        reasons.append("valuation score below 40; heavy position is not allowed")
    if _value(scores.final_score) < 70:
        reasons.append("综合评分低于70，系统不建议作为核心仓；是否小仓观察取决于 setup 与量价承接。")
    return reasons


def _summary(symbol: str, decision: str, allowed_add_pct: float, block_reasons: list[str]) -> str:
    if decision == "ALLOW_BUY":
        return f"{symbol}：当前价位于主击球区内；本次最多新增 {allowed_add_pct:g}%。"
    if decision == "DATA_MISSING":
        return f"{symbol}：数据缺失或过期，不要当作买入信号。"
    if decision == "BLOCK_CHASE":
        return f"{symbol}：追高风险提示，等待回踩或重新评估。"
    if decision == "AVOID":
        return f"{symbol}：风险较高，本轮先不参与。"
    first = _summary_reason_text(block_reasons[0]) if block_reasons else "当前不在高质量主击球区。"
    return f"{symbol}：等待。{first}"


def _summary_reason_text(reason: str) -> str:
    text = str(reason or "").strip()
    lower = text.lower()
    if "current price is below the discipline buy zone lower bound" in lower:
        return "当前价格低于主击球区下沿，先复核基本面、财报冲击或趋势破位。"
    if "current price is above the discipline buy zone" in lower:
        return "当前价格高于主击球区，等待回踩或量价重新确认。"
    if "current price is in or above chase zone" in lower:
        return "当前价格处于追高风险区，等待回到观察区。"
    if "missing discipline buy zone" in lower:
        return "主击球区缺失，先补齐技术承接数据。"
    if "valuation score below 40" in lower:
        return "估值评分偏低，不支持重仓。"
    if "final score below 70" in lower or "core position is not allowed" in lower or "综合评分低于70" in text:
        return "综合评分低于70，系统不建议作为核心仓；小仓观察仍以技术承接和量价确认为准。"
    if "missing current price" in lower:
        return "当前价格缺失，需人工判断。"
    return text


def _data_block_reason(data_status: str, market: dict[str, Any]) -> str:
    if data_status == "STALE":
        return "缓存过期：价格数据可能过期"
    if data_status == "MISSING_PRICE":
        return "缺价格：本地 quote / price_history 没有可用 current price"
    if data_status == "MISSING_VALUATION":
        return "缺少估值指标：需要 PE、Forward PE、EV/Sales、FCF yield 或 FCF margin"
    if data_status == "MISSING_SCORE":
        return "缺财务指标：没有可用评分快照，Radar 不能给买入建议"
    if data_status == "MISSING_BUY_ZONE":
        return "缺击球区：没有纪律买入区"
    return "数据缺失：Radar 不能给买入建议"

def _zone(value: RadarZone | dict[str, Any] | None) -> RadarZone | None:
    if value is None:
        return None
    if isinstance(value, RadarZone):
        return value
    return RadarZone(
        lower=_first_number(value, "lower", "low", "below", "min"),
        upper=_first_number(value, "upper", "high", "above", "max"),
        label=str(value.get("label") or ""),
    )


def _zone_dict(zone: RadarZone) -> dict[str, Any]:
    return asdict(zone)


def _scores_complete(scores: RadarScores) -> bool:
    return all(
        _number(value) is not None
        for value in (
            scores.final_score,
            scores.quality_score,
            scores.growth_score,
            scores.valuation_score,
            scores.technical_score,
            scores.risk_score,
        )
    )


def _first_metric_number(mapping: dict[str, Any], *keys: str) -> float | None:
    return _first_number(mapping, *keys)


def _first_number(mapping: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = _number(mapping.get(key))
        if number is not None:
            return number
    return None


def _missing_penalty(values: list[object], *, penalty: float) -> float:
    return sum(1 for value in values if _number(value) is None) * penalty


def _bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _number(value: object) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _value(value: object) -> float:
    return _number(value) or 0.0


def _symbol(value: object) -> str:
    return str(value or "").strip().upper()
