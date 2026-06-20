from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import pandas as pd

from indicators.technicals import add_technical_indicators, latest_technical_snapshot


@dataclass(frozen=True)
class CalculatedMetric:
    metricKey: str
    snapshotKey: str
    value: float | None
    unit: str | None
    formula: str
    sourceType: str = "CALCULATED"
    confidence: str = "high"
    reason: str | None = None
    period: str | None = None

    @property
    def is_available(self) -> bool:
        return self.value is not None


def calculate_metrics(
    snapshot: dict,
    technicals: dict | None = None,
    price_history: pd.DataFrame | None = None,
) -> list[CalculatedMetric]:
    technicals = technicals or {}
    metrics = [
        _ratio_metric(
            "sbcToRevenue",
            "sbc_ratio",
            _first_number(snapshot, "stock_based_compensation", "stockBasedCompensation", "share_based_compensation"),
            _first_number(snapshot, "total_revenue", "revenue"),
            "stockBasedCompensation / revenue",
            "percent",
        ),
        _difference_metric(
            "netDebt",
            "net_debt",
            _first_number(snapshot, "total_debt", "totalDebt"),
            _first_number(snapshot, "total_cash", "cashAndCashEquivalents", "cash_and_cash_equivalents"),
            "totalDebt - cashAndCashEquivalents",
            "usd",
        ),
        _ratio_metric(
            "netDebtToEbitda",
            "net_debt_to_ebitda",
            _net_debt(snapshot),
            _first_number(snapshot, "ebitda", "adjustedEbitda", "manualAdjustedEbitda"),
            "netDebt / EBITDA",
            "x",
        ),
        _ratio_metric(
            "interestCoverage",
            "interest_coverage",
            _first_number(snapshot, "ebit", "operating_income", "operatingIncome"),
            _abs_number(snapshot, "interest_expense", "interestExpense"),
            "EBIT / interestExpense",
            "x",
        ),
        _ratio_metric(
            "fcfMargin",
            "fcf_margin",
            _first_number(snapshot, "free_cash_flow", "freeCashFlow"),
            _first_number(snapshot, "total_revenue", "revenue"),
            "freeCashFlow / revenue",
            "percent",
        ),
        _ratio_metric(
            "directFcfMargin",
            "fcf_margin",
            _first_number(snapshot, "free_cash_flow", "freeCashFlow"),
            _first_number(snapshot, "total_revenue", "revenue"),
            "freeCashFlow / revenue",
            "percent",
        ),
        _ratio_minus_one_metric(
            "drawdownFrom52WeekHigh",
            "drawdown_from_high_pct",
            _first_number(snapshot, "current_price", "price") or _number(technicals.get("price")),
            _first_number(snapshot, "fifty_two_week_high", "yearHigh") or _number(technicals.get("fifty_two_week_high")),
            "currentPrice / fiftyTwoWeekHigh - 1",
            "percent",
            output_percent=True,
        ),
    ]
    metrics.extend(_technical_metrics(technicals, price_history))
    return metrics


def available_metric_values(
    snapshot: dict,
    technicals: dict | None = None,
    price_history: pd.DataFrame | None = None,
) -> dict[str, CalculatedMetric]:
    return {metric.metricKey: metric for metric in calculate_metrics(snapshot, technicals, price_history) if metric.is_available}


def apply_calculated_metrics_to_snapshot(snapshot: dict) -> dict:
    enriched = dict(snapshot)
    metric_sources = dict(enriched.get("metric_sources") or {})
    metric_statuses = dict(enriched.get("metric_statuses") or {})
    for metric in calculate_metrics(enriched):
        if metric.value is None:
            metric_statuses.setdefault(
                metric.snapshotKey,
                {"status": "calculation_unavailable", "reason": metric.reason, "sourceType": "CALCULATED"},
            )
            continue
        existing_source = _source_type(metric_sources.get(metric.snapshotKey))
        if enriched.get(metric.snapshotKey) is None or existing_source in {None, "estimated", "derivedFromMarket"}:
            enriched[metric.snapshotKey] = metric.value
            metric_sources[metric.snapshotKey] = {
                "sourceType": "calculated",
                "source": "calculated metrics",
                "formula": metric.formula,
                "confidence": metric.confidence,
            }
            metric_statuses[metric.snapshotKey] = {
                "status": "available",
                "sourceType": "CALCULATED",
                "confidence": metric.confidence,
            }
    if metric_sources:
        enriched["metric_sources"] = metric_sources
    if metric_statuses:
        enriched["metric_statuses"] = metric_statuses
    return enriched


def _technical_metrics(technicals: dict, price_history: pd.DataFrame | None) -> list[CalculatedMetric]:
    snapshot = dict(technicals)
    if price_history is not None and not price_history.empty:
        try:
            history = add_technical_indicators(price_history)
            snapshot.update(latest_technical_snapshot(history))
        except Exception:
            pass

    rows = [
        _direct_metric("ema20", "ema20", snapshot.get("ema20"), "EMA(close, 20)", "usd"),
        _direct_metric("ema50", "ema50", snapshot.get("ema50"), "EMA(close, 50)", "usd"),
        _direct_metric("ema200", "ema200", snapshot.get("ema200"), "EMA(close, 200)", "usd"),
        _direct_metric("rsi14", "rsi14", snapshot.get("rsi14"), "RSI(close, 14)", "number"),
    ]

    return_20d = _return_20d_from_history(price_history)
    if return_20d is None:
        gain_20d_pct = _number(snapshot.get("gain_20d_pct"))
        return_20d = gain_20d_pct / 100 if gain_20d_pct is not None else None
    rows.append(
        CalculatedMetric(
            metricKey="return20d",
            snapshotKey="gain_20d_pct",
            value=return_20d * 100 if return_20d is not None else None,
            unit="percent",
            formula="currentPrice / closePrice20TradingDaysAgo - 1",
            reason=None if return_20d is not None else "missing price history or 20-day gain",
            period=_today(),
        )
    )
    return_60d = _return_from_history(price_history, 60)
    if return_60d is None:
        gain_60d_pct = _number(snapshot.get("gain_60d_pct"))
        return_60d = gain_60d_pct / 100 if gain_60d_pct is not None else None
    rows.append(
        CalculatedMetric(
            metricKey="return60d",
            snapshotKey="gain_60d_pct",
            value=return_60d * 100 if return_60d is not None else None,
            unit="percent",
            formula="currentPrice / closePrice60TradingDaysAgo - 1",
            reason=None if return_60d is not None else "missing price history or 60-day gain",
            period=_today(),
        )
    )
    volume_trend = _volume_trend_from_history(price_history, 20)
    if volume_trend is None:
        volume_trend = _number(snapshot.get("volume_trend"))
    rows.append(
        CalculatedMetric(
            metricKey="volumeTrend",
            snapshotKey="volume_trend",
            value=volume_trend,
            unit="percent",
            formula="currentVolume / avgVolume20d - 1",
            reason=None if volume_trend is not None else "missing historical volume",
            period=_today() if volume_trend is not None else None,
        )
    )
    return rows


def _return_20d_from_history(price_history: pd.DataFrame | None) -> float | None:
    return _return_from_history(price_history, 20)


def _return_from_history(price_history: pd.DataFrame | None, days: int) -> float | None:
    if price_history is None or price_history.empty or "close" not in price_history:
        return None
    closes = pd.to_numeric(price_history["close"], errors="coerce").dropna()
    if len(closes) < days + 1:
        return None
    previous = float(closes.iloc[-(days + 1)])
    current = float(closes.iloc[-1])
    if previous == 0:
        return None
    return current / previous - 1


def _volume_trend_from_history(price_history: pd.DataFrame | None, days: int) -> float | None:
    if price_history is None or price_history.empty or "volume" not in price_history:
        return None
    volumes = pd.to_numeric(price_history["volume"], errors="coerce").dropna()
    if len(volumes) < days + 1:
        return None
    current = float(volumes.iloc[-1])
    average = float(volumes.iloc[-(days + 1):-1].mean())
    if average == 0:
        return None
    return current / average - 1


def _ratio_metric(metric_key: str, snapshot_key: str, numerator: float | None, denominator: float | None, formula: str, unit: str) -> CalculatedMetric:
    if numerator is None:
        return CalculatedMetric(metric_key, snapshot_key, None, unit, formula, reason="missing numerator")
    if denominator is None:
        return CalculatedMetric(metric_key, snapshot_key, None, unit, formula, reason="missing denominator")
    if denominator == 0:
        return CalculatedMetric(metric_key, snapshot_key, None, unit, formula, reason="denominator is zero")
    return CalculatedMetric(metric_key, snapshot_key, numerator / denominator, unit, formula, period=_today())


def _ratio_minus_one_metric(
    metric_key: str,
    snapshot_key: str,
    numerator: float | None,
    denominator: float | None,
    formula: str,
    unit: str,
    output_percent: bool = False,
) -> CalculatedMetric:
    metric = _ratio_metric(metric_key, snapshot_key, numerator, denominator, formula, unit)
    if metric.value is None:
        return metric
    value = metric.value - 1
    if output_percent:
        value *= 100
    return CalculatedMetric(metric_key, snapshot_key, value, unit, formula, period=_today())


def _difference_metric(metric_key: str, snapshot_key: str, left: float | None, right: float | None, formula: str, unit: str) -> CalculatedMetric:
    if left is None:
        return CalculatedMetric(metric_key, snapshot_key, None, unit, formula, reason="missing total debt")
    if right is None:
        return CalculatedMetric(metric_key, snapshot_key, None, unit, formula, reason="missing cash and cash equivalents")
    return CalculatedMetric(metric_key, snapshot_key, left - right, unit, formula, period=_today())


def _direct_metric(metric_key: str, snapshot_key: str, value: object, formula: str, unit: str) -> CalculatedMetric:
    number = _number(value)
    return CalculatedMetric(
        metricKey=metric_key,
        snapshotKey=snapshot_key,
        value=number,
        unit=unit,
        formula=formula,
        reason=None if number is not None else "technical calculation not complete",
        period=_today() if number is not None else None,
    )


def _net_debt(snapshot: dict) -> float | None:
    direct = _first_number(snapshot, "net_debt", "netDebt")
    if direct is not None:
        return direct
    total_debt = _first_number(snapshot, "total_debt", "totalDebt")
    cash = _first_number(snapshot, "total_cash", "cashAndCashEquivalents", "cash_and_cash_equivalents")
    if total_debt is None or cash is None:
        return None
    return total_debt - cash


def _first_number(snapshot: dict, *keys: str) -> float | None:
    for key in keys:
        value = snapshot.get(key)
        if value is None:
            value = snapshot.get(_camel_to_snake(key))
        number = _number(value)
        if number is not None:
            return number
    return None


def _abs_number(snapshot: dict, *keys: str) -> float | None:
    value = _first_number(snapshot, *keys)
    return abs(value) if value is not None else None


def _number(value: object) -> float | None:
    try:
        if value in {None, ""}:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _source_type(raw: object) -> str | None:
    if isinstance(raw, dict):
        value = raw.get("sourceType") or raw.get("source_type")
        return str(value) if value else None
    if isinstance(raw, str):
        return raw
    return None


def _camel_to_snake(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper() and chars:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()
