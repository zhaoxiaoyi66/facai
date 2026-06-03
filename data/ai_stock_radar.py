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
    core_max_pct: float
    trade_max_pct: float
    allowed_add_pct: float
    price_position: str
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
    position_plan = calculate_position_plan(score_input, decision, risk_incomplete=risk_incomplete)
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
        path=path,
    )
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
        price_position=price_position,
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
    position_plan = calculate_position_plan(score_input, decision, risk_incomplete=risk_incomplete)
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
        "price_position": price_position,
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


SCORE_FIELD_USAGE = {
    "quality_score": ["gross_margin", "net_margin", "fcf_margin", "roe"],
    "growth_score": ["revenue_growth", "gain_20d_pct", "gain_60d_pct"],
    "valuation_score": ["forward_pe", "trailing_pe", "enterprise_to_revenue", "free_cash_flow_yield", "fcf_margin"],
    "technical_score": ["current_price", "fifty_two_week_high", "fifty_two_week_low", "rsi14", "gain_20d_pct"],
    "risk_score": ["net_debt_to_ebitda", "current_ratio", "debt", "cash", "is_stale"],
    "final_score": ["quality_score", "growth_score", "valuation_score", "technical_score", "risk_score"],
    "price_zones": ["manual_zone", "stock_action_plan", "current_price", "fifty_two_week_high", "fifty_two_week_low", "valuation_score"],
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
        return ["current_price"]
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
