from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from data.binance_provider import BinanceHTTPPriceProvider, BinancePriceProvider, CachedBinancePriceProvider, normalize_market_type
from data.cache_read_model import CacheReadModel
from settings import CONFIG_DIR


DEFAULT_MAPPING_PATH = CONFIG_DIR / "binance_symbol_mapping.example.json"
DEFAULT_LOCAL_MAPPING_PATH = CONFIG_DIR / "binance_symbol_mapping.local.json"
RISK_TEXT = "Binance 映射价格不等于真实美股可成交价格；V1 仅用于观察，不构成套利建议。"
NO_MAPPING_TEXT = "暂无映射"
MAPPING_REVIEW_TEXT = "需人工确认映射"
MAPPING_CONFIRMED_TEXT = "映射已确认"
UNIT_REVIEW_TEXT = "需确认映射单位"
DEFAULT_USDM_MAPPING_RISK_NOTE = "候选 symbol 按 ticker+USDT 自动生成；如 Binance 未上线该合约，请单独修改或删除。"


def load_binance_symbol_mapping(
    path: Path = DEFAULT_MAPPING_PATH,
    *,
    local_path: Path | None = DEFAULT_LOCAL_MAPPING_PATH,
) -> dict[str, dict[str, Any]]:
    if local_path is not None and local_path.exists():
        return _load_mapping_file(local_path)
    return _load_mapping_file(path)


def _load_mapping_file(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}
    raw = payload.get("mappings") if isinstance(payload, dict) else {}
    if raw is None and isinstance(payload, dict):
        raw = payload
    if not isinstance(raw, dict):
        return {}
    mapping: dict[str, dict[str, Any]] = {}
    for ticker, config in raw.items():
        normalized_ticker = str(ticker or "").strip().upper()
        if not normalized_ticker:
            continue
        normalized = _normalize_mapping_config(config)
        if normalized is not None:
            mapping[normalized_ticker] = normalized
    return mapping


def upsert_local_binance_symbol_mapping(
    ticker: str,
    binance_symbol: str,
    *,
    market_type: str = "usdm_futures",
    quote_currency: str = "USDT",
    unit_multiplier: float = 1,
    mapping_confidence: str = "candidate",
    risk_note: str = "",
    enabled: bool = True,
    path: Path = DEFAULT_LOCAL_MAPPING_PATH,
) -> dict[str, dict[str, Any]]:
    normalized_ticker = str(ticker or "").strip().upper()
    normalized_symbol = str(binance_symbol or "").strip().upper()
    if not normalized_ticker:
        raise ValueError("ticker_required")
    if not normalized_symbol:
        raise ValueError("binance_symbol_required")
    if normalize_market_type(str(market_type or "")) != "usdm_futures":
        raise ValueError("stock_mapping_requires_usdm_futures")
    existing = load_binance_symbol_mapping(path, local_path=None)
    existing[normalized_ticker] = _normalize_mapping_config(
        {
            "enabled": enabled,
            "binance_symbol": normalized_symbol,
            "market_type": market_type,
            "quote_currency": quote_currency,
            "unit_multiplier": unit_multiplier,
            "mapping_confidence": mapping_confidence,
            "risk_note": risk_note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ) or {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"mappings": {key: _mapping_config_for_file(value) for key, value in existing.items()}}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return existing


def upsert_default_usdm_futures_mappings(
    tickers: Iterable[str],
    *,
    path: Path = DEFAULT_LOCAL_MAPPING_PATH,
    overwrite: bool = False,
) -> dict[str, Any]:
    existing = load_binance_symbol_mapping(path, local_path=None)
    created = 0
    skipped = 0
    for ticker in _normalize_tickers(tickers):
        current = existing.get(ticker)
        if current and current.get("binance_symbol") and not overwrite:
            skipped += 1
            continue
        existing[ticker] = _normalize_mapping_config(
            {
                "enabled": True,
                "binance_symbol": f"{ticker}USDT",
                "market_type": "usdm_futures",
                "quote_currency": "USDT",
                "unit_multiplier": 1,
                "mapping_confidence": "candidate",
                "risk_note": DEFAULT_USDM_MAPPING_RISK_NOTE,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ) or {}
        created += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"mappings": {key: _mapping_config_for_file(value) for key, value in existing.items()}}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "mapping": existing,
        "created": created,
        "skipped": skipped,
        "total": len(existing),
    }


def build_weekend_spread_rows(
    tickers: Iterable[str],
    *,
    mapping: dict[str, Any] | None = None,
    provider: BinancePriceProvider | None = None,
    cache: CacheReadModel | None = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    normalized = _normalize_tickers(tickers)
    effective_mapping = _normalize_mapping(mapping or load_binance_symbol_mapping())
    price_provider = provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider())
    read_model = cache or CacheReadModel()
    rows: list[dict[str, Any]] = []
    for ticker in normalized:
        friday_close, friday_date, close_source = _friday_close(read_model, ticker)
        quote = read_model.get_quote_payload(ticker) or {}
        stock_name = str(quote.get("companyName") or quote.get("company_name") or quote.get("name") or ticker)
        mapping_config = effective_mapping.get(ticker)
        if not mapping_config or not mapping_config.get("enabled", True) or not mapping_config.get("binance_symbol"):
            rows.append(
                _base_row(
                    ticker,
                    stock_name,
                    friday_close,
                    friday_date,
                    close_source,
                    mapping_config,
                    status="NO_MAPPING",
                )
            )
            continue
        binance_symbol = str(mapping_config.get("binance_symbol") or "").strip().upper()
        if normalize_market_type(str(mapping_config.get("market_type") or "")) != "usdm_futures":
            row = _base_row(
                ticker,
                stock_name,
                friday_close,
                friday_date,
                close_source,
                mapping_config,
                status="SPOT_DISABLED",
            )
            row["binance_symbol"] = binance_symbol
            row["error"] = "stock_mapping_requires_usdm_futures"
            rows.append(row)
            continue
        if friday_close is None:
            rows.append(
                _base_row(
                    ticker,
                    stock_name,
                    None,
                    friday_date,
                    close_source,
                    mapping_config,
                    status="MISSING_FRIDAY_CLOSE",
                )
            )
            continue
        unit_error = _unit_mapping_error(mapping_config)
        if unit_error:
            row = _base_row(
                ticker,
                stock_name,
                friday_close,
                friday_date,
                close_source,
                mapping_config,
                status="UNIT_UNCONFIRMED",
            )
            row["binance_symbol"] = binance_symbol
            row["error"] = unit_error
            rows.append(row)
            continue
        manual_snapshot = _manual_override_snapshot(mapping_config)
        snapshot = manual_snapshot or _snapshot_to_dict(
            price_provider.get_last_price(
                binance_symbol,
                market_type=str(mapping_config.get("market_type") or "usdm_futures"),
                force_refresh=force_refresh,
            )
        )
        last_price = _number(snapshot.get("last_price"))
        if last_price is None:
            status = "INVALID_SYMBOL" if str(snapshot.get("error") or "") == "invalid_symbol" else "BINANCE_UNAVAILABLE"
            row = _base_row(
                ticker,
                stock_name,
                friday_close,
                friday_date,
                close_source,
                mapping_config,
                status=status,
            )
            row["updated_at"] = snapshot.get("updated_at") or ""
            row["error"] = snapshot.get("error") or "binance_price_missing"
            rows.append(row)
            continue
        unit_multiplier = float(_number(mapping_config.get("unit_multiplier")) or 1.0)
        adjusted_price = last_price / unit_multiplier
        spread_pct = (adjusted_price / friday_close - 1.0) * 100.0
        alert = classify_spread(spread_pct)
        bid = _number(snapshot.get("bid"))
        ask = _number(snapshot.get("ask"))
        bid_ask_spread_pct = _bid_ask_spread_pct(bid, ask)
        funding_rate = _number(snapshot.get("funding_rate"))
        volume_24h = _number(snapshot.get("volume_24h"))
        liquidity_warning = _liquidity_warning(bid_ask_spread_pct, volume_24h, funding_rate)
        rows.append(
            {
                **_base_row(
                    ticker,
                    stock_name,
                    friday_close,
                    friday_date,
                    close_source,
                    mapping_config,
                    status="OK",
                ),
                "binance_last_price": last_price,
                "adjusted_binance_price": adjusted_price,
                "binance_bid": bid,
                "binance_ask": ask,
                "binance_spread_pct": bid_ask_spread_pct,
                "binance_volume_24h": volume_24h,
                "funding_rate": funding_rate,
                "spread_pct": spread_pct,
                "spread_direction": _spread_direction(spread_pct),
                "alert_level": alert["level"],
                "alert_level_cn": alert["label"],
                "liquidity_warning": liquidity_warning,
                "updated_at": snapshot.get("updated_at") or "",
                "source": snapshot.get("source") or "binance_futures",
                "manual_override": bool(snapshot.get("manual_override")),
            }
        )
    return rows


def build_mapping_diagnostics(
    tickers: Iterable[str],
    *,
    mapping: dict[str, Any] | None = None,
    provider: BinancePriceProvider | None = None,
    validate: bool = False,
    include_candidates: bool = False,
) -> list[dict[str, Any]]:
    effective_mapping = _normalize_mapping(mapping or load_binance_symbol_mapping())
    price_provider = provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider())
    rows: list[dict[str, Any]] = []
    for ticker in _normalize_tickers(tickers):
        config = effective_mapping.get(ticker)
        if not config or not config.get("binance_symbol"):
            row = {
                "ticker": ticker,
                "configured_symbol": "",
                "market_type": "",
                "mapping_confidence": "",
                "validation_status": "暂无映射",
                "last_validated_at": "",
                "exists": False,
                "quote_currency": "",
                "base_asset": "",
                "price_available": False,
                "book_available": False,
                "volume_available": False,
                "funding_available": False,
                "risk_note": "",
                "candidates": [],
                "candidate_scan_status": "",
                "candidate_scan_message": "",
                "error_message": "",
            }
            if include_candidates:
                scan = discover_binance_symbol_candidates(ticker, provider=price_provider)
                row["candidates"] = scan["candidates"]
                row["candidate_scan_status"] = scan["data_source_status"]
                row["candidate_scan_message"] = _candidate_scan_message(scan)
            rows.append(row)
            continue
        market_type = str(config.get("market_type") or "usdm_futures")
        symbol = str(config.get("binance_symbol") or "").strip().upper()
        row = {
            "ticker": ticker,
            "configured_symbol": symbol,
            "market_type": market_type,
            "mapping_confidence": str(config.get("mapping_confidence") or "unverified"),
            "validation_status": str(config.get("validation_status") or "未校验"),
            "last_validated_at": str(config.get("last_validated_at") or ""),
            "exists": False,
            "quote_currency": str(config.get("quote_currency") or ""),
            "base_asset": "",
            "price_available": False,
            "book_available": False,
            "volume_available": False,
            "funding_available": False,
            "risk_note": str(config.get("risk_note") or ""),
            "candidates": [],
            "candidate_scan_status": "",
            "candidate_scan_message": "",
            "error_message": "",
        }
        if validate:
            validation = price_provider.validate_symbol(symbol, market_type=market_type)
            validation_dict = _validation_to_dict(validation)
            row.update(
                {
                    "validation_status": _validation_status_text(validation_dict, row["mapping_confidence"]),
                    "last_validated_at": validation_dict.get("updated_at") or "",
                    "exists": bool(validation_dict.get("exists")),
                    "quote_currency": validation_dict.get("quote_currency") or row["quote_currency"],
                    "base_asset": validation_dict.get("base_asset") or "",
                    "price_available": bool(validation_dict.get("price_available")),
                    "book_available": bool(validation_dict.get("book_available")),
                    "volume_available": bool(validation_dict.get("volume_available")),
                    "funding_available": bool(validation_dict.get("funding_available")),
                    "error_message": validation_dict.get("error_message") or "",
                }
            )
        if include_candidates:
            scan = discover_binance_symbol_candidates(
                ticker,
                market_type=market_type,
                provider=price_provider,
            )
            row["candidates"] = scan["candidates"]
            row["candidate_scan_status"] = scan["data_source_status"]
            row["candidate_scan_message"] = _candidate_scan_message(scan)
        rows.append(row)
    return rows


def discover_binance_symbol_candidates(
    ticker: str,
    *,
    market_type: str | None = None,
    provider: BinancePriceProvider | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    query = str(ticker or "").strip().upper()
    updated_at = datetime.now(timezone.utc).isoformat()
    if not query:
        return {
            "candidates": [],
            "data_source_status": "OK",
            "error_message": "",
            "checked_market_types": [],
            "updated_at": updated_at,
            "provider_diagnostic_failed": False,
            "market_results": [],
        }
    price_provider = provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider())
    raw_market_type = str(market_type or "").strip().lower()
    if raw_market_type in {"", "unknown", "all"}:
        markets = ["usdm_futures"]
    else:
        markets = [normalize_market_type(raw_market_type)]

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    market_results: list[dict[str, Any]] = []
    for market in markets:
        try:
            if hasattr(price_provider, "find_symbol_candidates_with_status"):
                search_result = price_provider.find_symbol_candidates_with_status(query, market_type=market, limit=limit)
                result_dict = _search_result_to_dict(search_result, market)
                candidates = result_dict.get("candidates") or []
            else:
                candidates = price_provider.find_symbol_candidates(query, market_type=market, limit=limit)
                result_dict = {
                    "market_type": market,
                    "data_source_status": "OK",
                    "error_message": "",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "provider_diagnostic_failed": False,
                }
        except Exception:
            candidates = []
            result_dict = {
                "market_type": market,
                "data_source_status": "UNAVAILABLE",
                "error_message": "candidate provider failed",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "provider_diagnostic_failed": True,
            }
        result_dict["candidates"] = _candidate_dicts(candidates)
        market_results.append(result_dict)
        for row in _candidate_dicts(candidates):
            symbol = str(row.get("symbol") or "").strip().upper()
            candidate_market = str(row.get("market_type") or market).strip() or market
            if not symbol:
                continue
            key = (symbol, candidate_market)
            if key in seen:
                continue
            row["symbol"] = symbol
            row["market_type"] = candidate_market
            quote = str(row.get("quote_asset") or row.get("quote_currency") or "").strip().upper()
            row["quote_asset"] = quote
            row["quote_currency"] = quote
            row["status"] = "candidate"
            rows.append(row)
            seen.add(key)
            if len(rows) >= max(1, limit):
                return {
                    "candidates": rows,
                    "data_source_status": _aggregate_candidate_status(market_results),
                    "error_message": _aggregate_candidate_error(market_results),
                    "checked_market_types": markets,
                    "updated_at": _latest_candidate_updated_at(market_results, updated_at),
                    "provider_diagnostic_failed": any(bool(item.get("provider_diagnostic_failed")) for item in market_results),
                    "market_results": market_results,
                }
    return {
        "candidates": rows,
        "data_source_status": _aggregate_candidate_status(market_results),
        "error_message": _aggregate_candidate_error(market_results),
        "checked_market_types": markets,
        "updated_at": _latest_candidate_updated_at(market_results, updated_at),
        "provider_diagnostic_failed": any(bool(item.get("provider_diagnostic_failed")) for item in market_results),
        "market_results": market_results,
    }


def classify_spread(spread_pct: float | None) -> dict[str, str]:
    if spread_pct is None:
        return {"level": "DATA_INSUFFICIENT", "label": "数据不足"}
    value = abs(float(spread_pct))
    if value < 0.5:
        return {"level": "IGNORE", "label": "忽略"}
    if value < 1.2:
        return {"level": "OBSERVE", "label": "观察"}
    if value <= 2.0:
        return {"level": "FOCUS", "label": "重点关注"}
    return {"level": "ABNORMAL", "label": "异常复核"}


def _base_row(
    ticker: str,
    stock_name: str,
    friday_close: float | None,
    friday_date: str,
    close_source: str,
    mapping_config: dict[str, Any] | None,
    *,
    status: str,
) -> dict[str, Any]:
    mapping_config = mapping_config or {}
    unit_multiplier = _number(mapping_config.get("unit_multiplier"))
    quote_currency = str(mapping_config.get("quote_currency") or "USDT").strip().upper()
    mapping_confidence = str(mapping_config.get("mapping_confidence") or "").strip().lower()
    return {
        "ticker": ticker,
        "stock_name": stock_name,
        "friday_close": friday_close,
        "friday_close_date": friday_date,
        "close_source": close_source,
        "binance_symbol": str(mapping_config.get("binance_symbol") or "").strip().upper(),
        "binance_market_type": str(mapping_config.get("market_type") or "unknown"),
        "binance_last_price": None,
        "binance_bid": None,
        "binance_ask": None,
        "binance_spread_pct": None,
        "binance_volume_24h": None,
        "funding_rate": None,
        "unit_multiplier": unit_multiplier,
        "quote_currency": quote_currency,
        "mapping_confidence": mapping_confidence,
        "fx_note": _fx_note(quote_currency),
        "spread_pct": None,
        "spread_direction": _status_direction(status),
        "alert_level": "NO_MAPPING" if status == "NO_MAPPING" else "DATA_INSUFFICIENT",
        "alert_level_cn": _status_label(status),
        "mapping_status": _mapping_status(status, mapping_confidence),
        "mapping_risk": _mapping_risk(mapping_config, status),
        "liquidity_warning": _liquidity_warning(None, None, None),
        "updated_at": "",
        "status": status,
        "source": "",
        "manual_override": False,
        "error": "",
    }


def _friday_close(cache: CacheReadModel, ticker: str) -> tuple[float | None, str, str]:
    history = cache.get_price_history(ticker)
    if history is None or history.empty or "date" not in history or "close" not in history:
        return None, "", "missing_history"
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).sort_values("date")
    if frame.empty:
        return None, "", "missing_history"
    target_friday = _latest_reference_friday()
    eligible = frame[frame["date"].dt.date <= target_friday]
    if eligible.empty:
        return None, "", "missing_friday_close"
    fridays = eligible[eligible["date"].dt.weekday == 4]
    if not fridays.empty:
        latest = fridays.iloc[-1]
        return float(latest["close"]), latest["date"].date().isoformat(), "friday_close"
    latest = eligible.iloc[-1]
    return float(latest["close"]), latest["date"].date().isoformat(), "previous_trading_day_before_friday"


def _latest_reference_friday(today: date | None = None) -> date:
    current = today or datetime.now(timezone.utc).date()
    days_since_friday = (current.weekday() - 4) % 7
    return current - timedelta(days=days_since_friday)


def _snapshot_to_dict(snapshot: Any) -> dict[str, Any]:
    if isinstance(snapshot, dict):
        return dict(snapshot)
    if is_dataclass(snapshot):
        return asdict(snapshot)
    return {
        "symbol": getattr(snapshot, "symbol", ""),
        "last_price": getattr(snapshot, "last_price", None),
        "bid": getattr(snapshot, "bid", None),
        "ask": getattr(snapshot, "ask", None),
        "volume_24h": getattr(snapshot, "volume_24h", None),
        "funding_rate": getattr(snapshot, "funding_rate", None),
        "updated_at": getattr(snapshot, "updated_at", ""),
        "source": getattr(snapshot, "source", ""),
        "market_type": getattr(snapshot, "market_type", ""),
        "manual_override": getattr(snapshot, "manual_override", False),
        "error": getattr(snapshot, "error", ""),
    }


def _validation_to_dict(validation: Any) -> dict[str, Any]:
    if isinstance(validation, dict):
        return dict(validation)
    if is_dataclass(validation):
        return asdict(validation)
    return {
        "symbol": getattr(validation, "symbol", ""),
        "exists": getattr(validation, "exists", False),
        "market_type": getattr(validation, "market_type", ""),
        "quote_currency": getattr(validation, "quote_currency", ""),
        "status": getattr(validation, "status", ""),
        "base_asset": getattr(validation, "base_asset", ""),
        "price_available": getattr(validation, "price_available", False),
        "book_available": getattr(validation, "book_available", False),
        "volume_available": getattr(validation, "volume_available", False),
        "funding_available": getattr(validation, "funding_available", False),
        "error_message": getattr(validation, "error_message", ""),
        "updated_at": getattr(validation, "updated_at", ""),
    }


def _candidate_dicts(candidates: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in candidates:
        if isinstance(item, dict):
            row = dict(item)
            row.setdefault("status", "candidate")
            result.append(row)
        elif is_dataclass(item):
            result.append(asdict(item))
        else:
            result.append(
                {
                    "symbol": getattr(item, "symbol", ""),
                    "market_type": getattr(item, "market_type", ""),
                    "base_asset": getattr(item, "base_asset", ""),
                    "quote_currency": getattr(item, "quote_currency", ""),
                    "status": getattr(item, "status", "candidate"),
                }
            )
    return result


def _search_result_to_dict(result: Any, market_type: str) -> dict[str, Any]:
    if isinstance(result, dict):
        payload = dict(result)
    elif is_dataclass(result):
        payload = asdict(result)
    else:
        payload = {
            "market_type": getattr(result, "market_type", market_type),
            "candidates": getattr(result, "candidates", []),
            "data_source_status": getattr(result, "data_source_status", "OK"),
            "error_message": getattr(result, "error_message", ""),
            "updated_at": getattr(result, "updated_at", ""),
            "provider_diagnostic_failed": getattr(result, "provider_diagnostic_failed", False),
        }
    payload["market_type"] = str(payload.get("market_type") or market_type)
    payload["data_source_status"] = str(payload.get("data_source_status") or "OK")
    payload["error_message"] = str(payload.get("error_message") or "")
    payload["updated_at"] = str(payload.get("updated_at") or "")
    payload["provider_diagnostic_failed"] = bool(payload.get("provider_diagnostic_failed"))
    payload["candidates"] = _candidate_dicts(payload.get("candidates") or [])
    return payload


def _aggregate_candidate_status(market_results: list[dict[str, Any]]) -> str:
    if not market_results:
        return "UNAVAILABLE"
    statuses = [str(item.get("data_source_status") or "UNAVAILABLE") for item in market_results]
    if any(status == "OK" for status in statuses):
        return "OK"
    for status in ("BLOCKED", "PARSE_ERROR", "SCHEMA_MISMATCH", "EMPTY", "UNAVAILABLE"):
        if status in statuses:
            return status
    return statuses[0]


def _aggregate_candidate_error(market_results: list[dict[str, Any]]) -> str:
    messages = [str(item.get("error_message") or "").strip() for item in market_results]
    return "；".join(message for message in messages if message)


def _latest_candidate_updated_at(market_results: list[dict[str, Any]], fallback: str) -> str:
    values = [str(item.get("updated_at") or "") for item in market_results if item.get("updated_at")]
    return values[-1] if values else fallback


def _candidate_scan_message(scan: dict[str, Any]) -> str:
    status = str(scan.get("data_source_status") or "")
    if status == "OK":
        if scan.get("candidates"):
            return "候选待确认"
        return "未发现候选 symbol"
    market_results = scan.get("market_results") if isinstance(scan.get("market_results"), list) else []
    markets = {str(item.get("market_type") or "") for item in market_results if isinstance(item, dict)}
    if status == "BLOCKED":
        return "Binance API 可能被网络或地区限制拦截"
    if status == "EMPTY":
        return "Binance exchangeInfo 返回空 symbols"
    if status == "SCHEMA_MISMATCH":
        return "Binance exchangeInfo 返回结构异常"
    if status == "PARSE_ERROR":
        return "Binance exchangeInfo 解析失败"
    if markets == {"spot"}:
        return "Spot 候选扫描未完成"
    if markets == {"usdm_futures"}:
        return "Futures 数据源不可用"
    return "Binance exchangeInfo 不可用，候选扫描未完成"


def _validation_status_text(validation: dict[str, Any], mapping_confidence: str) -> str:
    if not validation.get("exists"):
        status = str(validation.get("status") or "")
        if status == "invalid_symbol":
            return "symbol 无效"
        return "Binance 数据不可用"
    if str(mapping_confidence or "").lower() == "confirmed":
        return "confirmed"
    return "symbol 有效但映射未确认"


def _normalize_mapping(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for ticker, config in raw.items():
        normalized_ticker = str(ticker or "").strip().upper()
        normalized_config = _normalize_mapping_config(config)
        if normalized_ticker and normalized_config is not None:
            result[normalized_ticker] = normalized_config
    return result


def _normalize_mapping_config(config: Any) -> dict[str, Any] | None:
    if isinstance(config, str):
        symbol = config.strip().upper()
        if not symbol:
            return None
        return {
            "enabled": True,
            "binance_symbol": symbol,
            "market_type": "usdm_futures",
            "quote_currency": "USDT",
            "unit_multiplier": 1,
            "mapping_confidence": "confirmed",
            "risk_note": "旧版映射；请定期复核 symbol 与单位。",
        }
    if not isinstance(config, dict):
        return None
    normalized = dict(config)
    normalized["enabled"] = bool(normalized.get("enabled", True))
    normalized["binance_symbol"] = str(normalized.get("binance_symbol") or "").strip().upper()
    normalized["market_type"] = str(normalized.get("market_type") or "usdm_futures")
    normalized["quote_currency"] = str(normalized.get("quote_currency") or "USDT").strip().upper()
    normalized["unit_multiplier"] = _number(normalized.get("unit_multiplier")) or 1
    normalized["mapping_confidence"] = str(normalized.get("mapping_confidence") or "manual_required").strip().lower()
    normalized["risk_note"] = str(normalized.get("risk_note") or "")
    normalized["last_validated_at"] = str(normalized.get("last_validated_at") or "")
    normalized["validation_status"] = str(normalized.get("validation_status") or "")
    normalized["manual_override_enabled"] = bool(normalized.get("manual_override_enabled", False))
    normalized["manual_override_price"] = _number(normalized.get("manual_override_price"))
    return normalized


def _mapping_config_for_file(config: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "enabled": bool(config.get("enabled", True)),
        "binance_symbol": str(config.get("binance_symbol") or "").strip().upper(),
        "market_type": str(config.get("market_type") or "usdm_futures"),
        "quote_currency": str(config.get("quote_currency") or "USDT").strip().upper(),
        "unit_multiplier": _number(config.get("unit_multiplier")) or 1,
        "mapping_confidence": str(config.get("mapping_confidence") or "manual_required").strip().lower(),
        "risk_note": str(config.get("risk_note") or ""),
    }
    for optional_key in ("last_validated_at", "validation_status", "updated_at"):
        value = str(config.get(optional_key) or "").strip()
        if value:
            payload[optional_key] = value
    if config.get("manual_override_enabled") and _number(config.get("manual_override_price")) is not None:
        payload["manual_override_enabled"] = True
        payload["manual_override_price"] = _number(config.get("manual_override_price"))
    return payload


def _manual_override_snapshot(mapping_config: dict[str, Any]) -> dict[str, Any] | None:
    if not mapping_config.get("manual_override_enabled"):
        return None
    price = _number(mapping_config.get("manual_override_price"))
    if price is None or price <= 0:
        return None
    return {
        "symbol": str(mapping_config.get("binance_symbol") or "").strip().upper(),
        "last_price": price,
        "bid": None,
        "ask": None,
        "volume_24h": None,
        "funding_rate": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "manual_override_non_realtime",
        "manual_override": True,
        "error": "",
    }


def _unit_mapping_error(mapping_config: dict[str, Any]) -> str:
    quote_currency = str(mapping_config.get("quote_currency") or "").strip().upper()
    unit_multiplier = _number(mapping_config.get("unit_multiplier"))
    if quote_currency not in {"USD", "USDT"}:
        return "quote_currency_not_usd"
    if unit_multiplier is None or unit_multiplier <= 0:
        return "unit_multiplier_missing"
    return ""


def _bid_ask_spread_pct(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    midpoint = (bid + ask) / 2.0
    if midpoint <= 0:
        return None
    return (ask - bid) / midpoint * 100.0


def _liquidity_warning(
    bid_ask_spread_pct: float | None,
    volume_24h: float | None,
    funding_rate: float | None,
) -> str:
    warnings: list[str] = []
    if bid_ask_spread_pct is None:
        warnings.append("买卖价差暂缺")
    elif bid_ask_spread_pct > 0.25:
        warnings.append("流动性不足：买卖价差偏宽")
    if volume_24h is None:
        warnings.append("24h 成交量暂缺")
    elif volume_24h < 10_000:
        warnings.append("成交量不足")
    if funding_rate is not None and abs(funding_rate) >= 0.0005:
        warnings.append("资金费率可能吞噬价差")
    return "；".join(warnings) if warnings else "流动性字段正常"


def _mapping_status(status: str, mapping_confidence: str) -> str:
    if status == "NO_MAPPING":
        return NO_MAPPING_TEXT
    if status == "UNIT_UNCONFIRMED":
        return UNIT_REVIEW_TEXT
    if status == "SPOT_DISABLED":
        return "请改用合约映射"
    if status == "INVALID_SYMBOL":
        return "symbol 无效 / 映射待确认"
    if mapping_confidence == "confirmed":
        return MAPPING_CONFIRMED_TEXT
    return MAPPING_REVIEW_TEXT


def _mapping_risk(mapping_config: dict[str, Any], status: str) -> str:
    if status == "NO_MAPPING":
        return NO_MAPPING_TEXT
    if status == "SPOT_DISABLED":
        return "美股映射仅支持 USDT-M 合约；请将 market_type 改为 usdm_futures。"
    notes = [RISK_TEXT]
    if mapping_config.get("manual_override_enabled"):
        notes.append("手动覆盖 / 非实时 Binance 数据")
    confidence = str(mapping_config.get("mapping_confidence") or "").strip().lower()
    if confidence != "confirmed":
        notes.append(MAPPING_REVIEW_TEXT)
    risk_note = str(mapping_config.get("risk_note") or "").strip()
    if risk_note:
        notes.append(risk_note)
    return "；".join(notes)


def _fx_note(quote_currency: str) -> str:
    if quote_currency == "USD":
        return "USD 计价"
    if quote_currency == "USDT":
        return "USDT 近似 USD，仍需注意稳定币与资金费率差异"
    return "非 USD/USDT，需确认币种换算"


def _status_label(status: str) -> str:
    return {
        "NO_MAPPING": NO_MAPPING_TEXT,
        "MISSING_FRIDAY_CLOSE": "缺少周五收盘价",
        "BINANCE_UNAVAILABLE": "Binance 数据不可用",
        "UNIT_UNCONFIRMED": UNIT_REVIEW_TEXT,
        "SPOT_DISABLED": "现货映射已关闭",
        "INVALID_SYMBOL": "symbol 无效 / 映射待确认",
    }.get(status, "数据不足")


def _status_direction(status: str) -> str:
    if status == "NO_MAPPING":
        return "暂无映射"
    if status == "UNIT_UNCONFIRMED":
        return "映射待确认"
    if status == "SPOT_DISABLED":
        return "现货映射已关闭"
    if status == "INVALID_SYMBOL":
        return "symbol 无效"
    return "数据不足"


def _spread_direction(spread_pct: float) -> str:
    if spread_pct > 0:
        return "Binance 溢价"
    if spread_pct < 0:
        return "Binance 折价"
    return "基本持平"


def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in tickers:
        ticker = str(item or "").strip().upper()
        if not ticker or ticker in seen:
            continue
        result.append(ticker)
        seen.add(ticker)
    return result


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
