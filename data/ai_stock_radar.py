from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from data.market_context import build_market_context
from data.market_context import build_market_history
from data.prices import CACHE_PATH
from data.trade_gate import evaluate_buy_gate as evaluate_trade_buy_gate
from indicators.technicals import add_technical_indicators, latest_technical_snapshot


RADAR_DECISIONS = {"ALLOW_BUY", "WAIT", "BLOCK_CHASE", "AVOID", "DATA_MISSING"}
RADAR_REPORT_VERSION = "AI_STOCK_RADAR_V1_LOCAL_RULES"
BUY_MOOD_BLOCKERS = {"fomo", "anxiety", "bottom_fishing_impulse", "revenge_trade", "regret_chase"}


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
    core_max_pct: float
    trade_max_pct: float
    allowed_add_pct: float
    block_reasons: list[str]
    summary: str
    bull_points: list[str]
    risk_points: list[str]
    watch_points: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class RadarBuyGateResult:
    can_continue: bool
    can_sync_to_portfolio: bool
    status: str
    reasons: list[str]
    required_actions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    action = str(action_type or "").strip().lower()
    if action not in {"buy", "add"}:
        return RadarBuyGateResult(True, True, "not_applicable", [], [])
    data = report.to_dict() if isinstance(report, RadarReport) else dict(report)
    decision = str(data.get("decision") or "")
    reasons: list[str] = []
    required: list[str] = []
    if decision in {"DATA_MISSING", "BLOCK_CHASE"}:
        reasons.extend(str(item) for item in (data.get("block_reasons") or []))
        if not reasons:
            reasons.append("Radar 结论禁止新增")
    elif decision == "AVOID":
        reasons.append("Radar 结论为 AVOID，禁止新增核心仓")
    elif decision == "WAIT" and not observation_only:
        reasons.append("Radar 结论为 WAIT，默认不允许真实买入/加仓")
        required.append("如只是复盘观察，请标记为仅记录观察/非真实交易")
    mood = str(decision_mood or "").strip().lower()
    if mood in BUY_MOOD_BLOCKERS:
        reasons.append("情绪交易风险：FOMO / 焦虑 / 抄底冲动 / 复仇交易不能绕过 Radar 门禁")
    limit_reason = _position_limit_reason(data, position_bucket, planned_after_position_pct)
    if limit_reason:
        reasons.append(limit_reason)
    if decision == "ALLOW_BUY" and not str(buy_reason or "").strip():
        required.append("ALLOW_BUY 仍需填写买入理由")
    can_continue = not reasons and not required
    if observation_only and decision == "WAIT" and not mood and not limit_reason:
        can_continue = True
    return RadarBuyGateResult(
        can_continue=can_continue,
        can_sync_to_portfolio=can_continue and not observation_only,
        status="pass" if can_continue else "blocked",
        reasons=reasons,
        required_actions=required,
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
) -> RadarReport:
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
    block_reasons = _block_reasons(current_price, market, score_input, zones["buy_zone"], zones["chase_zone"], data_status)
    decision = calculate_decision(
        current_price=current_price,
        scores=score_input,
        buy_zone=zones["buy_zone"],
        chase_zone=zones["chase_zone"],
        data_status=data_status,
        block_reasons=block_reasons,
    )
    position_plan = calculate_position_plan(score_input, decision)
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
        core_max_pct=position_plan["core_max_pct"],
        trade_max_pct=position_plan["trade_max_pct"],
        allowed_add_pct=position_plan["allowed_add_pct"],
        block_reasons=block_reasons,
        summary=_summary(symbol, decision, position_plan["allowed_add_pct"], block_reasons),
        bull_points=list(bull_points or []),
        risk_points=list(risk_points or []),
        watch_points=list(watch_points or []),
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
    block_reasons = _block_reasons(current_price, market, score_input, zones["buy_zone"], zones["chase_zone"], data_status)
    decision = calculate_decision(
        current_price=current_price,
        scores=score_input,
        buy_zone=zones["buy_zone"],
        chase_zone=zones["chase_zone"],
        data_status=data_status,
        block_reasons=block_reasons,
    )
    position_plan = calculate_position_plan(score_input, decision)
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
        "buy_zone": _zone_dict(zones["buy_zone"]),
        "core_max_pct": position_plan["core_max_pct"],
        "trade_max_pct": position_plan["trade_max_pct"],
        "block_reasons": block_reasons,
        "data_status": data_status,
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
    ev_sales = _first_metric_number(metrics, "enterprise_to_revenue", "ev_to_sales", "enterpriseToRevenue")
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
    high = _first_metric_number(metrics, "fifty_two_week_high", "52_week_high")
    low = _first_metric_number(metrics, "fifty_two_week_low", "52_week_low")
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
    score = 70.0
    net_debt_to_ebitda = _number(metrics.get("net_debt_to_ebitda"))
    current_ratio = _number(metrics.get("current_ratio"))
    debt = _number(metrics.get("debt"))
    cash = _number(metrics.get("cash"))
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
    if _zones_have_buy_upper(zones):
        return zones
    derived = _derive_price_zones(market or {}, scores or RadarScores(), metrics or {})
    return derived if _zones_have_buy_upper(derived) else zones


def _derive_price_zones(
    market: dict[str, Any],
    scores: RadarScores,
    metrics: dict[str, Any],
) -> dict[str, RadarZone]:
    current_price = _number(market.get("currentPrice"))
    high = _first_metric_number(metrics, "fifty_two_week_high", "52_week_high")
    low = _first_metric_number(metrics, "fifty_two_week_low", "52_week_low")
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
            label="derived_discipline_buy_zone",
        ),
        "watch_zone": RadarZone(
            lower=round(low + span * buy_upper_ratio, 2),
            upper=round(low + span * watch_upper_ratio, 2),
            label="derived_watch_zone",
        ),
        "chase_zone": RadarZone(
            lower=round(low + span * chase_lower_ratio, 2),
            upper=None,
            label="derived_chase_zone",
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


def calculate_position_plan(scores: RadarScores, decision: str) -> dict[str, float]:
    if decision in {"DATA_MISSING", "BLOCK_CHASE", "AVOID"}:
        return {"core_max_pct": 0.0, "trade_max_pct": 0.0, "allowed_add_pct": 0.0}
    risk_modifier = _bounded((_value(scores.risk_score) - 35) / 45, 0.2, 1.0)
    final_score = _value(scores.final_score)
    valuation_score = _value(scores.valuation_score)
    core_max = max(0.0, min(8.0, (final_score - 65) * 0.35)) * risk_modifier
    trade_max = max(0.0, min(3.0, (valuation_score - 35) * 0.09)) * risk_modifier
    if final_score < 70:
        core_max = 0.0
    if valuation_score < 40:
        core_max = min(core_max, 2.0)
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
) -> str:
    if data_status != "OK":
        return "DATA_MISSING"
    if current_price is None:
        return "DATA_MISSING"
    if _value(scores.risk_score) < 35:
        return "AVOID"
    if chase_zone.lower is not None and current_price >= chase_zone.lower:
        return "BLOCK_CHASE"
    if buy_zone.upper is not None and current_price > buy_zone.upper:
        return "WAIT"
    if _value(scores.final_score) < 70:
        return "WAIT"
    if _value(scores.valuation_score) < 40:
        return "WAIT"
    return "WAIT" if block_reasons else "ALLOW_BUY"


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
    snapshot = snapshot or {}
    technicals = technicals or {}
    return {
        **snapshot,
        **technicals,
        "current_price": _first_metric_number(market, "currentPrice", "current_price") or _first_metric_number(technicals, "price", "current_price"),
        "price_source": market.get("priceSource"),
        "is_stale": bool(market.get("isStale")),
        "history_status": market.get("historyStatus"),
        "history_latest_date": market.get("historyLatestDate"),
        "fifty_two_week_high": _first_metric_number(technicals, "fifty_two_week_high", "52_week_high") or _first_metric_number(snapshot, "fifty_two_week_high", "52_week_high"),
        "fifty_two_week_low": _first_metric_number(technicals, "fifty_two_week_low", "52_week_low") or _first_metric_number(snapshot, "fifty_two_week_low", "52_week_low"),
        "enterprise_to_revenue": _first_metric_number(snapshot, "enterprise_to_revenue", "ev_to_sales", "enterpriseToRevenue"),
        "fcf_margin": _first_metric_number(snapshot, "fcf_margin", "free_cash_flow_margin"),
        "gross_margin": _first_metric_number(snapshot, "gross_margin"),
        "net_margin": _first_metric_number(snapshot, "net_margin"),
        "roe": _first_metric_number(snapshot, "roe", "return_on_equity"),
        "debt": _first_metric_number(snapshot, "debt", "total_debt"),
        "cash": _first_metric_number(snapshot, "cash", "cash_and_equivalents"),
    }


def _data_status(market: dict[str, Any], scores: RadarScores, buy_zone: RadarZone) -> str:
    if market.get("isStale"):
        return "STALE"
    if _number(market.get("currentPrice")) is None:
        return "MISSING_PRICE"
    if _number(scores.valuation_score) is None:
        return "MISSING_VALUATION"
    if not _scores_complete(scores):
        return "MISSING_SCORE"
    if buy_zone.upper is None:
        return "MISSING_BUY_ZONE"
    return "OK"


def _block_reasons(
    current_price: float | None,
    market: dict[str, Any],
    scores: RadarScores,
    buy_zone: RadarZone,
    chase_zone: RadarZone,
    data_status: str,
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
    elif current_price > buy_zone.upper:
        reasons.append("current price is above the discipline buy zone")
    elif buy_zone.lower is not None and current_price < buy_zone.lower:
        reasons.append("current price is below the planned discipline zone; review data before acting")
    if chase_zone.lower is not None and current_price >= chase_zone.lower:
        reasons.append("current price is in or above chase zone")
    if _value(scores.valuation_score) < 40:
        reasons.append("valuation score below 40; heavy position is not allowed")
    if _value(scores.final_score) < 70:
        reasons.append("final score below 70; core position is not allowed")
    return reasons


def _summary(symbol: str, decision: str, allowed_add_pct: float, block_reasons: list[str]) -> str:
    if decision == "ALLOW_BUY":
        return f"{symbol}: price is inside the discipline buy zone; max new add {allowed_add_pct:g}%."
    if decision == "DATA_MISSING":
        return f"{symbol}: data is missing or stale; do not treat this as a buy signal."
    if decision == "BLOCK_CHASE":
        return f"{symbol}: chase blocked; wait for plan review."
    if decision == "AVOID":
        return f"{symbol}: risk is too high for this radar pass."
    first = block_reasons[0] if block_reasons else "not inside the discipline buy zone"
    return f"{symbol}: wait. {first}."


def _position_limit_reason(data: dict[str, Any], position_bucket: str, planned_after_position_pct: float | None) -> str:
    after_pct = _number(planned_after_position_pct)
    if after_pct is None:
        return ""
    bucket = str(position_bucket or "").strip().lower()
    if bucket in {"core", "核心仓"}:
        limit = _number(data.get("core_max_pct"))
        label = "核心仓"
    elif bucket in {"trade", "trading", "交易仓"}:
        limit = _number(data.get("trade_max_pct"))
        label = "交易仓"
    else:
        return "未选择核心仓/交易仓，不能判断买入后是否超过 Radar 仓位上限"
    if limit is None:
        return f"缺少 {label} 上限，不能继续新增"
    if after_pct > limit:
        return f"买入后仓位 {after_pct:.1f}% 超过 Radar {label}上限 {limit:.1f}%"
    return ""


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
        return float(value)
    except (TypeError, ValueError):
        return None


def _value(value: object) -> float:
    return _number(value) or 0.0


def _symbol(value: object) -> str:
    return str(value or "").strip().upper()
