from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import pandas as pd

from data.binance_provider import BinanceHTTPPriceProvider, CachedBinancePriceProvider, normalize_market_type
from data.cache_read_model import CacheReadModel
from data.weekend_spread import DEFAULT_LOCAL_MAPPING_PATH, load_binance_symbol_mapping
from data.weekend_spread_backtest import recent_weekend_windows


DEFAULT_MIN_SAMPLES = 5


def audit_weekend_basis_mappings(
    tickers: Iterable[str],
    *,
    mapping: dict[str, Any] | None = None,
    binance_provider: Any | None = None,
    broker_history_provider: Any | None = None,
    now: datetime | None = None,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> list[dict[str, Any]]:
    """Audit candidate basis mappings without confirming them."""

    normalized_tickers = _normalize_tickers(tickers)
    effective_mapping = _normalize_mapping(load_binance_symbol_mapping() if mapping is None else mapping)
    provider = binance_provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider())
    history_provider = broker_history_provider or CacheReadModel()
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sample_floor = max(1, int(min_samples or DEFAULT_MIN_SAMPLES))
    return [
        _audit_one_mapping(
            ticker,
            dict(effective_mapping.get(ticker) or {}),
            provider=provider,
            broker_history_provider=history_provider,
            now=timestamp,
            min_samples=sample_floor,
        )
        for ticker in normalized_tickers
    ]


def confirm_weekend_basis_mapping(
    ticker: str,
    audit_result: dict[str, Any],
    *,
    path: Path = DEFAULT_LOCAL_MAPPING_PATH,
    confirmed_by: str = "manual",
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    normalized = str(ticker or audit_result.get("ticker") or "").strip().upper()
    if not normalized:
        raise ValueError("ticker_required")
    if str(audit_result.get("audit_status") or "").strip().lower() != "verified_ready":
        raise ValueError("mapping_not_verified_ready")

    mapping = _read_local_mapping(path)
    current = dict(mapping.get(normalized) or {})
    merged = _mapping_from_audit(normalized, current, audit_result)
    merged["mapping_confidence"] = "confirmed"
    merged["validation_status"] = "confirmed"
    merged["confirmed_at"] = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    merged["confirmed_by"] = str(confirmed_by or "manual")
    merged["audit_summary"] = _audit_summary(audit_result)
    mapping[normalized] = merged
    _write_local_mapping(path, mapping)
    return mapping


def reject_weekend_basis_mapping(
    ticker: str,
    audit_result: dict[str, Any],
    *,
    path: Path = DEFAULT_LOCAL_MAPPING_PATH,
    rejected_by: str = "manual",
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    normalized = str(ticker or audit_result.get("ticker") or "").strip().upper()
    if not normalized:
        raise ValueError("ticker_required")

    mapping = _read_local_mapping(path)
    current = dict(mapping.get(normalized) or {})
    merged = _mapping_from_audit(normalized, current, audit_result)
    merged["mapping_confidence"] = "rejected"
    merged["validation_status"] = "rejected"
    merged["rejected_at"] = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    merged["rejected_by"] = str(rejected_by or "manual")
    merged["audit_summary"] = _audit_summary(audit_result)
    mapping[normalized] = merged
    _write_local_mapping(path, mapping)
    return mapping


def _audit_one_mapping(
    ticker: str,
    config: dict[str, Any],
    *,
    provider: Any,
    broker_history_provider: Any,
    now: datetime,
    min_samples: int,
) -> dict[str, Any]:
    symbol = str(config.get("binance_symbol") or "").strip().upper()
    broker_symbol = str(config.get("broker_symbol") or ticker).strip().upper()
    market_type = normalize_market_type(str(config.get("market_type") or "usdm_futures"))
    confidence = str(config.get("mapping_confidence") or "candidate").strip().lower()
    row = _base_row(ticker, broker_symbol, symbol, market_type, confidence, now)

    if not config or not config.get("enabled", True) or not symbol:
        row.update({"audit_status": "rejected", "warning": "NO_MAPPING"})
        return row

    validation = _to_dict(_safe_call(lambda: provider.validate_symbol(symbol, market_type=market_type)))
    if not bool(validation.get("exists")):
        row.update({"audit_status": "rejected", "warning": "SYMBOL_INVALID"})
        return row

    warnings: list[str] = []
    ratios = _price_ratios(
        broker_symbol=broker_symbol,
        symbol=symbol,
        market_type=market_type,
        config=config,
        provider=provider,
        broker_history_provider=broker_history_provider,
        now=now,
    )
    _apply_ratio_stats(row, ratios, min_samples=min_samples, warnings=warnings)

    weekend_ok = _recent_weekend_data_ok(provider, symbol=symbol, market_type=market_type, now=now)
    row["weekend_data_ok"] = weekend_ok
    row["weekend_spread_data_ok"] = weekend_ok
    if not weekend_ok:
        warnings.append("NO_RECENT_WEEKEND_BINANCE_DATA")

    liquidity = _liquidity_status(provider, symbol=symbol, market_type=market_type)
    row["liquidity_status"] = liquidity
    if liquidity != "OK":
        warnings.append(liquidity)

    quote_currency = str(config.get("quote_currency") or "USDT").strip().upper()
    currency = str(config.get("currency") or "USD").strip().upper()
    if quote_currency == "USDT" and currency in {"", "USD"}:
        warnings.append("USDT_ASSUMED_1_0")

    status = "verified_ready" if _passes_audit(row, min_samples=min_samples, warnings=warnings) else "rejected"
    row["audit_status"] = status
    row["warning"] = " / ".join(_dedupe(warnings))
    row["action"] = "可人工确认 mapping" if status == "verified_ready" else "需修正映射后重审"
    return row


def _base_row(
    ticker: str,
    broker_symbol: str,
    symbol: str,
    market_type: str,
    confidence: str,
    now: datetime,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "broker_symbol": broker_symbol,
        "binance_symbol": symbol,
        "market_type": market_type,
        "current_confidence": confidence,
        "audit_status": "rejected",
        "median_ratio": None,
        "median_abs_deviation_bps": None,
        "max_abs_deviation_bps": None,
        "sample_count": 0,
        "weekend_data_ok": False,
        "weekend_spread_data_ok": False,
        "liquidity_status": "UNKNOWN",
        "warning": "",
        "action": "运行 Mapping Audit",
        "updated_at": now.isoformat(),
    }


def _apply_ratio_stats(
    row: dict[str, Any],
    ratios: list[float],
    *,
    min_samples: int,
    warnings: list[str],
) -> None:
    if len(ratios) < min_samples:
        warnings.append("BROKER_PRICE_RATIO_SAMPLE_INSUFFICIENT")
        return

    med = median(ratios)
    deviations = [abs(ratio / med - 1.0) * 10_000.0 for ratio in ratios if med > 0]
    row.update(
        {
            "median_ratio": med,
            "median_abs_deviation_bps": median(deviations) if deviations else None,
            "max_abs_deviation_bps": max(deviations) if deviations else None,
            "sample_count": len(ratios),
        }
    )
    if _looks_like_multiplier_mismatch(med):
        warnings.append("MULTIPLIER_MISMATCH")


def _price_ratios(
    *,
    broker_symbol: str,
    symbol: str,
    market_type: str,
    config: dict[str, Any],
    provider: Any,
    broker_history_provider: Any,
    now: datetime,
) -> list[float]:
    broker_closes = _broker_close_by_date(broker_history_provider, broker_symbol)
    if not broker_closes:
        return []
    bars = _safe_call(
        lambda: provider.get_klines(
            symbol,
            market_type=market_type,
            interval="1d",
            start_time_ms=_to_ms(now - timedelta(days=21)),
            end_time_ms=_to_ms(now + timedelta(days=1)),
            limit=1000,
        )
    )
    binance_closes = _binance_close_by_date(bars)
    multiplier = _number(config.get("mapping_multiplier")) or _number(config.get("unit_multiplier")) or 1.0
    ratios: list[float] = []
    for date_key, broker_close in broker_closes.items():
        binance_close = binance_closes.get(date_key)
        if broker_close and broker_close > 0 and binance_close and binance_close > 0:
            ratios.append((binance_close / multiplier) / broker_close)
    return ratios


def _broker_close_by_date(provider: Any, symbol: str) -> dict[str, float]:
    payload = None
    if isinstance(provider, dict):
        for key in (symbol, str(symbol).upper(), str(symbol).lower()):
            if key in provider:
                payload = provider[key]
                break
    elif hasattr(provider, "get_price_history"):
        payload = provider.get_price_history(symbol)
    if payload is None:
        return {}

    rows = payload.to_dict("records") if isinstance(payload, pd.DataFrame) else list(payload or [])
    closes: dict[str, float] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        date_text = str(item.get("date") or item.get("datetime") or item.get("timestamp") or item.get("time") or "").strip()
        close = _number(item.get("close") or item.get("adjClose") or item.get("price"))
        if not date_text or close is None:
            continue
        parsed = pd.to_datetime(date_text, utc=True, errors="coerce")
        if pd.isna(parsed):
            continue
        closes[parsed.date().isoformat()] = close
    return closes


def _binance_close_by_date(payload: Any) -> dict[str, float]:
    rows = list(payload or []) if isinstance(payload, (list, tuple)) else []
    closes: dict[str, float] = {}
    for item in rows:
        if isinstance(item, dict):
            ts = item.get("open_time") or item.get("openTime") or item.get("timestamp")
            close = _number(item.get("close"))
        elif isinstance(item, (list, tuple)) and len(item) >= 5:
            ts = item[0]
            close = _number(item[4])
        else:
            continue
        parsed = _parse_ts(ts)
        if parsed is not None and close is not None:
            closes[parsed.date().isoformat()] = close
    return closes


def _recent_weekend_data_ok(provider: Any, *, symbol: str, market_type: str, now: datetime) -> bool:
    window = recent_weekend_windows(weeks=1, now=now)[0]
    start = window.start_et.astimezone(timezone.utc)
    # Include a boundary bar exactly at Sunday 20:00 ET in provider fixtures.
    end = window.end_et.astimezone(timezone.utc) + timedelta(minutes=1)
    payload = _safe_call(
        lambda: provider.get_klines(
            symbol,
            market_type=market_type,
            interval="1m",
            start_time_ms=_to_ms(start),
            end_time_ms=_to_ms(end),
            limit=1000,
        )
    )
    return bool(payload)


def _liquidity_status(provider: Any, *, symbol: str, market_type: str) -> str:
    snapshot = _to_dict(_safe_call(lambda: provider.get_last_price(symbol, market_type=market_type, force_refresh=False)))
    if not snapshot:
        return "LOW_DEPTH"
    bid = _number(snapshot.get("bid"))
    ask = _number(snapshot.get("ask"))
    volume = _number(snapshot.get("volume_24h"))
    if volume is None or volume <= 0:
        return "LOW_DEPTH"
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return "WIDE_SPREAD"
    mid = (bid + ask) / 2.0
    spread_bps = (ask - bid) / mid * 10_000.0 if mid > 0 else 999_999.0
    return "OK" if spread_bps <= 100 else "WIDE_SPREAD"


def _passes_audit(row: dict[str, Any], *, min_samples: int, warnings: list[str]) -> bool:
    hard_warnings = {
        "BROKER_PRICE_RATIO_SAMPLE_INSUFFICIENT",
        "MULTIPLIER_MISMATCH",
        "NO_RECENT_WEEKEND_BINANCE_DATA",
        "SYMBOL_INVALID",
    }
    if any(item in hard_warnings for item in warnings):
        return False
    median_ratio = _number(row.get("median_ratio"))
    median_dev = _number(row.get("median_abs_deviation_bps"))
    max_dev = _number(row.get("max_abs_deviation_bps"))
    return (
        int(row.get("sample_count") or 0) >= min_samples
        and median_ratio is not None
        and 0.99 <= median_ratio <= 1.01
        and median_dev is not None
        and median_dev <= 100
        and max_dev is not None
        and max_dev <= 300
        and bool(row.get("weekend_data_ok"))
    )


def _looks_like_multiplier_mismatch(ratio: float | None) -> bool:
    if ratio is None or ratio <= 0 or 0.99 <= ratio <= 1.01:
        return False
    return any(abs(ratio / target - 1.0) <= 0.05 for target in (0.1, 10.0, 100.0))


def _mapping_from_audit(ticker: str, current: dict[str, Any], audit_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(current.get("enabled", True)),
        "broker_symbol": str(audit_result.get("broker_symbol") or current.get("broker_symbol") or ticker).strip().upper(),
        "binance_symbol": str(audit_result.get("binance_symbol") or current.get("binance_symbol") or "").strip().upper(),
        "market_type": normalize_market_type(str(audit_result.get("market_type") or current.get("market_type") or "usdm_futures")),
        "quote_currency": str(current.get("quote_currency") or "USDT").strip().upper(),
        "currency": str(current.get("currency") or "USD").strip().upper(),
        "unit_multiplier": _number(current.get("unit_multiplier")) or 1,
        "mapping_multiplier": _number(current.get("mapping_multiplier")) or _number(current.get("unit_multiplier")) or 1,
        "min_qty": _number(current.get("min_qty")),
        "contract_type": str(current.get("contract_type") or "").strip(),
        "risk_note": str(current.get("risk_note") or "人工确认映射关系。"),
    }


def _audit_summary(audit_result: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "audit_status",
        "median_ratio",
        "median_abs_deviation_bps",
        "max_abs_deviation_bps",
        "sample_count",
        "weekend_data_ok",
        "liquidity_status",
        "warning",
    )
    return {key: audit_result.get(key) for key in keys}


def _read_local_mapping(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}
    raw = payload.get("mappings") if isinstance(payload, dict) else {}
    if not isinstance(raw, dict):
        return {}
    return {str(key).strip().upper(): dict(value) for key, value in raw.items() if isinstance(value, dict) and str(key).strip()}


def _write_local_mapping(path: Path, mapping: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mappings": mapping}, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _normalize_mapping(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for ticker, config in (raw or {}).items():
        normalized = str(ticker or "").strip().upper()
        if normalized and isinstance(config, dict):
            row = dict(config)
            row["binance_symbol"] = str(row.get("binance_symbol") or "").strip().upper()
            row["market_type"] = normalize_market_type(str(row.get("market_type") or "usdm_futures"))
            row["mapping_confidence"] = str(row.get("mapping_confidence") or "candidate").strip().lower()
            result[normalized] = row
    return result


def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for ticker in tickers:
        normalized = str(ticker or "").strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _safe_call(callback):
    try:
        return callback()
    except Exception:
        return None


def _to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    return {
        key: getattr(value, key)
        for key in dir(value)
        if not key.startswith("_") and not callable(getattr(value, key))
    }


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            if isinstance(value, (int, float)) or str(value).isdigit():
                number = float(value)
                parsed = datetime.fromtimestamp(number / 1000.0 if number > 10_000_000_000 else number, tz=timezone.utc)
            else:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (OSError, TypeError, ValueError):
            return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _to_ms(value: datetime) -> int:
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
