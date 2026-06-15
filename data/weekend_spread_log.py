from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from settings import PROJECT_ROOT


DEFAULT_WEEKEND_SPREAD_LOG_PATH = PROJECT_ROOT / ".cache" / "weekend_spread_log.json"

HIT_CAPTURE_THRESHOLD = 0.5
PARTIAL_CAPTURE_THRESHOLD = 0.2
DEFAULT_ESTIMATED_COST_PCT = 0.0

MONDAY_REFERENCE_TYPES = {
    "MONDAY_OVERNIGHT_OPEN",
    "MONDAY_PREMARKET_OPEN",
    "MONDAY_RTH_OPEN",
    "MANUAL",
}


def current_week_id(current_date: date | None = None) -> str:
    today = current_date or datetime.now(timezone.utc).date()
    iso_year, iso_week, _ = today.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def read_weekend_spread_store(path: Path = DEFAULT_WEEKEND_SPREAD_LOG_PATH) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {"samples": [], "summaries": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, OSError):
        return {"samples": [], "summaries": []}
    if not isinstance(payload, dict):
        return {"samples": [], "summaries": []}
    samples = payload.get("samples")
    summaries = payload.get("summaries")
    return {
        "samples": list(samples) if isinstance(samples, list) else [],
        "summaries": list(summaries) if isinstance(summaries, list) else [],
    }


def write_weekend_spread_store(
    payload: dict[str, list[dict[str, Any]]],
    path: Path = DEFAULT_WEEKEND_SPREAD_LOG_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def record_spread_samples(
    rows: Iterable[dict[str, Any]],
    *,
    path: Path = DEFAULT_WEEKEND_SPREAD_LOG_PATH,
    week_id: str | None = None,
    observed_at: str | None = None,
    source: str = "weekend_spread_page",
) -> list[dict[str, Any]]:
    timestamp = observed_at or datetime.now(timezone.utc).isoformat()
    effective_week_id = week_id or current_week_id()
    samples: list[dict[str, Any]] = []
    for row in rows:
        if not str(row.get("binance_symbol") or "").strip():
            continue
        samples.append(_sample_from_row(row, week_id=effective_week_id, observed_at=timestamp, source=source))
    if not samples:
        return []
    store = read_weekend_spread_store(path)
    store["samples"].extend(samples)
    write_weekend_spread_store(store, path)
    return samples


def generate_weekly_summary(
    *,
    path: Path = DEFAULT_WEEKEND_SPREAD_LOG_PATH,
    week_id: str | None = None,
) -> list[dict[str, Any]]:
    store = read_weekend_spread_store(path)
    effective_week_id = week_id or current_week_id()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in store["samples"]:
        if sample.get("week_id") == effective_week_id:
            grouped[str(sample.get("ticker") or "").upper()].append(sample)

    existing_by_ticker = {
        str(item.get("ticker") or "").upper(): item
        for item in store["summaries"]
        if item.get("week_id") == effective_week_id
    }
    summaries = []
    for ticker, samples in sorted(grouped.items()):
        if not ticker:
            continue
        summary = _summary_for_samples(effective_week_id, ticker, samples)
        _preserve_outcome_fields(summary, existing_by_ticker.get(ticker))
        summaries.append(summary)
    existing = [
        item
        for item in store["summaries"]
        if item.get("week_id") != effective_week_id or str(item.get("ticker") or "").upper() not in grouped
    ]
    store["summaries"] = existing + summaries
    write_weekend_spread_store(store, path)
    return summaries


def update_monday_outcome(
    ticker: str,
    *,
    monday_reference_price: float | None,
    path: Path = DEFAULT_WEEKEND_SPREAD_LOG_PATH,
    week_id: str | None = None,
    reference_type: str = "MANUAL",
    reference_time: str | None = None,
    estimated_cost_pct: float = DEFAULT_ESTIMATED_COST_PCT,
    notes: str = "",
) -> dict[str, Any] | None:
    effective_week_id = week_id or current_week_id()
    normalized_ticker = str(ticker or "").strip().upper()
    if not normalized_ticker:
        return None
    store = read_weekend_spread_store(path)
    summaries = store["summaries"]
    summary = next(
        (
            item
            for item in summaries
            if item.get("week_id") == effective_week_id and str(item.get("ticker") or "").upper() == normalized_ticker
        ),
        None,
    )
    if summary is None:
        generated = generate_weekly_summary(path=path, week_id=effective_week_id)
        store = read_weekend_spread_store(path)
        summaries = store["summaries"]
        summary = next((item for item in generated if str(item.get("ticker") or "").upper() == normalized_ticker), None)
    if summary is None:
        return None

    price = _number(monday_reference_price)
    regular_close = _number(summary.get("regular_close_price") or summary.get("friday_close_price"))
    afterhours_price = _number(summary.get("afterhours_reference_price"))
    primary_anchor = str(summary.get("primary_spread_anchor") or "")
    primary_base = afterhours_price if primary_anchor == "AFTERHOURS_REFERENCE" and afterhours_price else regular_close
    peak_abs = _number(summary.get("max_abs_spread_pct"))
    monday_gap_pct = None
    monday_gap_from_regular_close_pct = None
    monday_gap_from_afterhours_pct = None
    direction_hit = False
    capture_ratio = None
    net_edge_pct = None
    if price is not None:
        monday_gap_from_regular_close_pct = _percent_change(price, regular_close)
        monday_gap_from_afterhours_pct = _percent_change(price, afterhours_price)
        monday_gap_pct = _percent_change(price, primary_base)
    signed_peak = _signed_peak_spread(summary)
    if monday_gap_pct is not None and peak_abs is not None and peak_abs > 0 and signed_peak is not None:
        direction_hit = _sign(monday_gap_pct) == _sign(signed_peak)
        capture_ratio = abs(monday_gap_pct) / abs(signed_peak)
        net_edge_pct = abs(monday_gap_pct) - float(estimated_cost_pct or 0.0)

    summary.update(
        {
            "monday_reference_type": reference_type if reference_type in MONDAY_REFERENCE_TYPES else "MANUAL",
            "monday_reference_time": reference_time or datetime.now(timezone.utc).isoformat(),
            "monday_reference_price": price,
            "monday_gap_pct": monday_gap_pct,
            "monday_gap_from_regular_close_pct": monday_gap_from_regular_close_pct,
            "monday_gap_from_afterhours_pct": monday_gap_from_afterhours_pct,
            "direction_hit": direction_hit,
            "capture_ratio": capture_ratio,
            "estimated_cost_pct": float(estimated_cost_pct or 0.0),
            "net_edge_pct": net_edge_pct,
            "outcome_status": _outcome_status(summary, price, direction_hit, capture_ratio, net_edge_pct),
            "notes": notes,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    store["summaries"] = [
        summary
        if item.get("week_id") == effective_week_id and str(item.get("ticker") or "").upper() == normalized_ticker
        else item
        for item in summaries
    ]
    write_weekend_spread_store(store, path)
    return summary


def build_history_stats(path: Path = DEFAULT_WEEKEND_SPREAD_LOG_PATH) -> list[dict[str, Any]]:
    store = read_weekend_spread_store(path)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for summary in store["summaries"]:
        ticker = str(summary.get("ticker") or "").upper()
        if ticker:
            grouped[ticker].append(summary)

    rows: list[dict[str, Any]] = []
    for ticker, summaries in sorted(grouped.items()):
        outcomes = Counter(str(item.get("outcome_status") or "") for item in summaries)
        valid_outcomes = outcomes["HIT"] + outcomes["PARTIAL"] + outcomes["MISS"]
        hit_rate = outcomes["HIT"] / valid_outcomes if valid_outcomes else None
        max_spreads = [_number(item.get("max_abs_spread_pct")) for item in summaries]
        captures = [_number(item.get("capture_ratio")) for item in summaries]
        net_edges = [_number(item.get("net_edge_pct")) for item in summaries]
        failure_reasons = [
            _failure_reason(item)
            for item in summaries
            if str(item.get("outcome_status") or "") in {"MISS", "INVALID"}
        ]
        rows.append(
            {
                "ticker": ticker,
                "sample_weeks": len({item.get("week_id") for item in summaries if item.get("week_id")}),
                "hit_count": outcomes["HIT"],
                "partial_count": outcomes["PARTIAL"],
                "miss_count": outcomes["MISS"],
                "invalid_count": outcomes["INVALID"],
                "hit_rate": hit_rate,
                "avg_max_abs_spread_pct": _average(max_spreads),
                "avg_capture_ratio": _average(captures),
                "avg_net_edge_pct": _average(net_edges),
                "common_failure_reason": Counter(failure_reasons).most_common(1)[0][0] if failure_reasons else "",
            }
        )
    return rows


def get_weekly_log_snapshot(
    *,
    path: Path = DEFAULT_WEEKEND_SPREAD_LOG_PATH,
    week_id: str | None = None,
) -> dict[str, Any]:
    effective_week_id = week_id or current_week_id()
    store = read_weekend_spread_store(path)
    samples = [item for item in store["samples"] if item.get("week_id") == effective_week_id]
    summaries = [item for item in store["summaries"] if item.get("week_id") == effective_week_id]
    valid_spreads = [_sample_spread(item) for item in samples]
    valid_spreads = [value for value in valid_spreads if value is not None]
    premiums = [value for value in valid_spreads if value > 0]
    discounts = [value for value in valid_spreads if value < 0]
    return {
        "week_id": effective_week_id,
        "sample_count": len(samples),
        "summary_count": len(summaries),
        "max_premium_pct": max(premiums) if premiums else None,
        "max_discount_pct": min(discounts) if discounts else None,
        "history_stats": build_history_stats(path),
        "summaries": summaries,
    }


def _sample_from_row(row: dict[str, Any], *, week_id: str, observed_at: str, source: str) -> dict[str, Any]:
    return {
        "id": uuid4().hex,
        "week_id": week_id,
        "ticker": str(row.get("ticker") or "").upper(),
        "stock_name": str(row.get("stock_name") or ""),
        "friday_close_date": str(row.get("friday_close_date") or ""),
        "friday_close_price": _number(row.get("friday_close")),
        "regular_close_date": str(row.get("regular_close_date") or row.get("friday_close_date") or ""),
        "regular_close_price": _number(row.get("regular_close_price") or row.get("friday_close")),
        "afterhours_reference_price": _number(row.get("afterhours_reference_price")),
        "afterhours_reference_time": str(row.get("afterhours_reference_time") or ""),
        "afterhours_reference_source": str(row.get("afterhours_reference_source") or ""),
        "afterhours_missing_reason": str(row.get("afterhours_missing_reason") or ""),
        "afterhours_cache_status": str(row.get("afterhours_cache_status") or ""),
        "afterhours_gap_pct": _number(row.get("afterhours_gap_pct")),
        "spread_vs_regular_close_pct": _number(row.get("spread_vs_regular_close_pct")),
        "spread_vs_afterhours_pct": _number(row.get("spread_vs_afterhours_pct")),
        "primary_spread_anchor": str(row.get("primary_spread_anchor") or ""),
        "primary_spread_pct": _number(row.get("primary_spread_pct") or row.get("spread_pct")),
        "binance_symbol": str(row.get("binance_symbol") or "").upper(),
        "market_type": str(row.get("binance_market_type") or ""),
        "mapping_confidence": str(row.get("mapping_confidence") or "").lower(),
        "binance_last_price": _number(row.get("binance_last_price")),
        "adjusted_binance_price": _number(row.get("adjusted_binance_price")),
        "bid": _number(row.get("binance_bid")),
        "ask": _number(row.get("binance_ask")),
        "bid_ask_spread_pct": _number(row.get("binance_spread_pct")),
        "volume_24h": _number(row.get("binance_volume_24h")),
        "funding_rate": _number(row.get("funding_rate")),
        "spread_pct": _number(row.get("primary_spread_pct") or row.get("spread_pct")),
        "spread_direction": str(row.get("spread_direction") or ""),
        "alert_level": str(row.get("alert_level") or ""),
        "liquidity_warning": str(row.get("liquidity_warning") or ""),
        "mapping_risk": str(row.get("mapping_risk") or ""),
        "data_quality": _sample_quality(row),
        "observed_at": observed_at,
        "source": source,
    }


def _summary_for_samples(week_id: str, ticker: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [sample for sample in samples if _sample_spread(sample) is not None]
    premiums = [sample for sample in valid if (_sample_spread(sample) or 0.0) > 0]
    discounts = [sample for sample in valid if (_sample_spread(sample) or 0.0) < 0]
    max_premium = max(premiums, key=lambda item: _sample_spread(item) or 0.0) if premiums else None
    max_discount = min(discounts, key=lambda item: _sample_spread(item) or 0.0) if discounts else None
    max_abs = max(valid, key=lambda item: abs(_sample_spread(item) or 0.0)) if valid else None
    first = samples[0]
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": _existing_summary_id(first, week_id, ticker),
        "week_id": week_id,
        "ticker": ticker,
        "friday_close_date": first.get("friday_close_date") or "",
        "friday_close_price": first.get("friday_close_price"),
        "regular_close_date": first.get("regular_close_date") or first.get("friday_close_date") or "",
        "regular_close_price": first.get("regular_close_price") or first.get("friday_close_price"),
        "afterhours_reference_price": first.get("afterhours_reference_price"),
        "afterhours_reference_time": first.get("afterhours_reference_time") or "",
        "afterhours_reference_source": first.get("afterhours_reference_source") or "",
        "afterhours_missing_reason": first.get("afterhours_missing_reason") or "",
        "afterhours_cache_status": first.get("afterhours_cache_status") or "",
        "afterhours_gap_pct": first.get("afterhours_gap_pct"),
        "primary_spread_anchor": first.get("primary_spread_anchor") or "",
        "max_premium_pct": _sample_spread(max_premium) if max_premium else None,
        "max_premium_time": max_premium.get("observed_at") if max_premium else "",
        "max_premium_price": max_premium.get("adjusted_binance_price") if max_premium else None,
        "max_discount_pct": _sample_spread(max_discount) if max_discount else None,
        "max_discount_time": max_discount.get("observed_at") if max_discount else "",
        "max_discount_price": max_discount.get("adjusted_binance_price") if max_discount else None,
        "max_abs_spread_pct": abs(_sample_spread(max_abs) or 0.0) if max_abs else None,
        "max_abs_spread_direction": max_abs.get("spread_direction") if max_abs else "",
        "sample_count": len(samples),
        "data_quality": _summary_quality(samples),
        "monday_reference_type": "",
        "monday_reference_time": "",
        "monday_reference_price": None,
        "monday_gap_pct": None,
        "monday_gap_from_regular_close_pct": None,
        "monday_gap_from_afterhours_pct": None,
        "direction_hit": None,
        "capture_ratio": None,
        "estimated_cost_pct": DEFAULT_ESTIMATED_COST_PCT,
        "net_edge_pct": None,
        "outcome_status": "INVALID",
        "notes": "",
        "updated_at": now,
    }


def _preserve_outcome_fields(summary: dict[str, Any], existing: dict[str, Any] | None) -> None:
    if not existing:
        return
    for key in (
        "monday_reference_type",
        "monday_reference_time",
        "monday_reference_price",
        "monday_gap_pct",
        "monday_gap_from_regular_close_pct",
        "monday_gap_from_afterhours_pct",
        "direction_hit",
        "capture_ratio",
        "estimated_cost_pct",
        "net_edge_pct",
        "outcome_status",
        "notes",
    ):
        if existing.get(key) not in (None, ""):
            summary[key] = existing.get(key)


def _existing_summary_id(first_sample: dict[str, Any], week_id: str, ticker: str) -> str:
    return str(first_sample.get("summary_id") or f"{week_id}:{ticker}")


def _sample_quality(row: dict[str, Any]) -> str:
    if row.get("manual_override"):
        return "MANUAL_OVERRIDE"
    if row.get("status") != "OK" or _sample_spread(row) is None:
        return "DATA_INSUFFICIENT"
    if str(row.get("mapping_confidence") or "").lower() != "confirmed":
        return "MAPPING_UNCONFIRMED"
    warning = str(row.get("liquidity_warning") or "")
    if "不足" in warning or "偏宽" in warning:
        return "LIQUIDITY_RISK"
    if str(row.get("primary_spread_anchor") or "") == "REGULAR_CLOSE_FALLBACK":
        return "REGULAR_CLOSE_FALLBACK"
    return "OK"


def _summary_quality(samples: list[dict[str, Any]]) -> str:
    qualities = {str(item.get("data_quality") or "") for item in samples}
    for quality in ("DATA_INSUFFICIENT", "MANUAL_OVERRIDE", "MAPPING_UNCONFIRMED", "LIQUIDITY_RISK", "REGULAR_CLOSE_FALLBACK"):
        if quality in qualities:
            return quality
    return "OK" if "OK" in qualities else "DATA_INSUFFICIENT"


def _outcome_status(
    summary: dict[str, Any],
    monday_reference_price: float | None,
    direction_hit: bool,
    capture_ratio: float | None,
    net_edge_pct: float | None,
) -> str:
    if (
        monday_reference_price is None
        or _number(summary.get("max_abs_spread_pct")) is None
        or str(summary.get("data_quality") or "") not in {"OK", "REGULAR_CLOSE_FALLBACK"}
    ):
        return "INVALID"
    if direction_hit and capture_ratio is not None and capture_ratio >= HIT_CAPTURE_THRESHOLD and (net_edge_pct or 0.0) > 0:
        return "HIT"
    if direction_hit and capture_ratio is not None and capture_ratio >= PARTIAL_CAPTURE_THRESHOLD:
        return "PARTIAL"
    return "MISS"


def _signed_peak_spread(summary: dict[str, Any]) -> float | None:
    peak = _number(summary.get("max_abs_spread_pct"))
    if peak is None:
        return None
    direction = str(summary.get("max_abs_spread_direction") or "")
    if "折价" in direction:
        return -abs(peak)
    return abs(peak)


def _failure_reason(summary: dict[str, Any]) -> str:
    if str(summary.get("data_quality") or "") != "OK":
        return str(summary.get("data_quality") or "DATA_QUALITY")
    if not summary.get("direction_hit"):
        return "DIRECTION_MISS"
    return str(summary.get("outcome_status") or "UNKNOWN")


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sample_spread(sample: dict[str, Any] | None) -> float | None:
    if not sample:
        return None
    primary = _number(sample.get("primary_spread_pct"))
    return primary if primary is not None else _number(sample.get("spread_pct"))


def _percent_change(current: Any, base: Any) -> float | None:
    current_number = _number(current)
    base_number = _number(base)
    if current_number is None or base_number is None or base_number <= 0:
        return None
    return (current_number / base_number - 1.0) * 100.0


def _average(values: Iterable[float | None]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0
