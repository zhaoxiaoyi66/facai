from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from data.binance_provider import BinanceHTTPPriceProvider, BinancePriceProvider, CachedBinancePriceProvider
from data.cache_read_model import CacheReadModel
from data.weekend_spread import is_binance_symbol_ignored
from settings import PROJECT_ROOT


DEFAULT_BINANCE_EQUITY_SCAN_CACHE_PATH = PROJECT_ROOT / "data" / "cache" / "binance_equity_scan_cache.json"
SCAN_CACHE_TTL = timedelta(hours=24)

MAPPING_US_EQUITY_VERIFIED = "美股已验证"
MAPPING_ETF_VERIFIED = "ETF 已验证"
MAPPING_PENDING_VERIFICATION = "待校验映射"
MAPPING_OTHER_TRADFI = "其他 TradFi"
MAPPING_AVAILABLE = "映射可用"
MAPPING_PRICE_ANOMALY = "价格异常"
MAPPING_UNAVAILABLE = "不可用"
MAPPING_IGNORED = "已忽略"
MAPPING_AUTO_USABLE = MAPPING_AVAILABLE
MAPPING_PRICE_UNVERIFIED = MAPPING_PENDING_VERIFICATION
MAPPING_REVIEW = "异常复核"
MAPPING_ANCHOR_MISSING = "锚点缺失"
MAPPING_INVALID = "无效映射"
MAPPING_MANUAL_LOCKED = "人工锁定"
TRADFI_BUCKET_US_EQUITY = "US_EQUITY"
TRADFI_BUCKET_ETF = "ETF"
TRADFI_BUCKET_OTHER = "OTHER_TRADFI"

_USDT_SUFFIX = "USDT"
_TRADFI_KEYWORDS = {
    "EQUITY",
    "STOCK",
    "TRADFI",
    "TRADITIONAL",
    "ETF",
    "INDEX",
    "RWA",
    "COMMODITY",
    "KR_EQUITY",
    "PREMARKET",
    "PRE-IPO",
    "PREIPO",
}
_CRYPTO_BASE_DENYLIST = {
    "AAVE",
    "ADA",
    "ALGO",
    "APT",
    "ARB",
    "ATOM",
    "AVAX",
    "BCH",
    "BNB",
    "BTC",
    "DOGE",
    "DOT",
    "ENA",
    "ETH",
    "FIL",
    "GALA",
    "HBAR",
    "ICP",
    "INJ",
    "LINK",
    "LTC",
    "MATIC",
    "NEAR",
    "OP",
    "PEPE",
    "POL",
    "SAND",
    "SHIB",
    "SOL",
    "SUI",
    "TIA",
    "TON",
    "TRX",
    "UNI",
    "WIF",
    "XLM",
    "XRP",
}


@dataclass(frozen=True)
class BinanceEquityScanRecord:
    ticker: str
    binance_symbol: str
    market_type: str = "usdm_futures"
    source: str = "binance_exchange_info"
    detected_by: str = "binance_internal_category"
    underlying_type: str = ""
    underlying_sub_type: str = ""
    binance_category: str = ""
    tradfi_bucket: str = ""
    binance_status: str = ""
    mapping_quality: str = MAPPING_INVALID
    reason: str = ""
    binance_price: float | None = None
    stock_ref_price: float | None = None
    price_diff_pct: float | None = None
    manually_locked: bool = False
    is_watchlist: bool = False
    is_position: bool = False
    updated_at: str = ""


def parse_us_equity_ticker_from_binance_symbol(symbol: object) -> str | None:
    text = str(symbol or "").strip().upper()
    if not text.endswith(_USDT_SUFFIX):
        return None
    base = text[: -len(_USDT_SUFFIX)]
    if not base or not base.isalpha():
        return None
    if len(base) > 5:
        return None
    if base in _CRYPTO_BASE_DENYLIST:
        return None
    if base.endswith(("UP", "DOWN", "BULL", "BEAR")):
        return None
    return base


def read_binance_equity_scan_cache(
    *,
    path: Path = DEFAULT_BINANCE_EQUITY_SCAN_CACHE_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = _read_json(path)
    if not payload:
        return {"cache_state": "MISSING", "records": [], "generated_at": ""}
    generated_at = str(payload.get("generated_at") or "")
    parsed = _parse_datetime(generated_at)
    cache_state = "FRESH"
    if parsed is None or (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - parsed > SCAN_CACHE_TTL:
        cache_state = "STALE"
    records = payload.get("records") if isinstance(payload.get("records"), list) else []
    return {
        "cache_state": cache_state,
        "records": [dict(item) for item in records if isinstance(item, dict)],
        "generated_at": generated_at,
        "error_message": str(payload.get("error_message") or ""),
    }


def write_binance_equity_scan_cache(
    records: Iterable[dict[str, Any] | BinanceEquityScanRecord],
    *,
    path: Path = DEFAULT_BINANCE_EQUITY_SCAN_CACHE_PATH,
    generated_at: datetime | None = None,
    error_message: str = "",
) -> dict[str, Any]:
    timestamp = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    rows = [record_to_dict(record) for record in records]
    payload = {
        "version": 1,
        "generated_at": timestamp,
        "records": rows,
        "error_message": str(error_message or ""),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def record_to_dict(record: dict[str, Any] | BinanceEquityScanRecord) -> dict[str, Any]:
    if isinstance(record, BinanceEquityScanRecord):
        return asdict(record)
    return dict(record)


def scan_binance_equity_mapped_symbols(
    *,
    provider: BinancePriceProvider | None = None,
    cache: CacheReadModel | None = None,
    exchange_symbols: Iterable[dict[str, Any]] | None = None,
    known_tickers: Iterable[str] | None = None,
    watchlist: Iterable[str] | None = None,
    position_symbols: Iterable[str] | None = None,
    manual_mapping: dict[str, dict[str, Any]] | None = None,
    ignored_mappings: dict[str, dict[str, Any]] | None = None,
    force_refresh: bool = False,
    max_symbols: int | None = None,
) -> list[dict[str, Any]]:
    price_provider = provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider(), ttl_seconds=60)
    read_model = cache or CacheReadModel()
    manual_mapping = {str(key or "").strip().upper(): dict(value or {}) for key, value in (manual_mapping or {}).items()}
    ignored_mappings = {str(key or "").strip().upper(): dict(value or {}) for key, value in (ignored_mappings or {}).items()}
    watchlist_set = _normalize_set(watchlist)
    position_set = _normalize_set(position_symbols)
    validation_universe = (
        _normalize_set(known_tickers)
        | _local_equity_universe(read_model)
        | set(manual_mapping)
        | watchlist_set
        | position_set
    )
    now = datetime.now(timezone.utc).isoformat()

    raw_symbols = list(exchange_symbols) if exchange_symbols is not None else price_provider.list_exchange_symbols(
        market_type="usdm_futures",
        force_refresh=force_refresh,
    )
    bulk_prices = _bulk_usdm_price_map(price_provider)
    records: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()
    for raw in raw_symbols:
        if not isinstance(raw, dict) or not _eligible_usdm_contract(raw):
            continue
        binance_symbol = str(raw.get("symbol") or "").strip().upper()
        ticker = parse_us_equity_ticker_from_binance_symbol(binance_symbol)
        if not ticker:
            continue
        if is_binance_symbol_ignored(ticker, binance_symbol, ignored_mappings):
            continue
        is_binance_tradfi = _is_internal_tradfi_contract(raw)
        if not is_binance_tradfi and ticker not in validation_universe:
            continue
        if ticker in seen_tickers:
            continue
        detected_by = "binance_internal_category" if is_binance_tradfi else "local_universe_fallback"
        record = _scan_one_symbol(
            ticker,
            binance_symbol,
            price_provider=price_provider,
            cache=read_model,
            manual_config=manual_mapping.get(ticker),
            exchange_record=raw,
            detected_by=detected_by,
            bulk_prices=bulk_prices,
            watchlist_set=watchlist_set,
            position_set=position_set,
            updated_at=now,
            force_refresh=force_refresh,
        )
        records.append(record)
        seen_tickers.add(ticker)
        if max_symbols is not None and len(records) >= max_symbols:
            break

    for ticker, config in manual_mapping.items():
        if ticker in seen_tickers or not config.get("enabled", True):
            continue
        symbol = str(config.get("binance_symbol") or f"{ticker}USDT").strip().upper()
        if not symbol:
            continue
        if is_binance_symbol_ignored(ticker, symbol, ignored_mappings):
            continue
        records.append(
            _scan_one_symbol(
                ticker,
                symbol,
                price_provider=price_provider,
                cache=read_model,
                manual_config=config,
                exchange_record=None,
                detected_by="manual_mapping",
                bulk_prices=bulk_prices,
                watchlist_set=watchlist_set,
                position_set=position_set,
                updated_at=now,
                force_refresh=force_refresh,
            )
        )
    return sorted(records, key=_scan_sort_key)


def scan_records_to_mapping(records: Iterable[dict[str, Any]], manual_mapping: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    manual_mapping = {str(key or "").strip().upper(): dict(value or {}) for key, value in (manual_mapping or {}).items()}
    result: dict[str, dict[str, Any]] = {ticker: dict(config) for ticker, config in manual_mapping.items()}
    for record in records:
        ticker = str(record.get("ticker") or "").strip().upper()
        symbol = str(record.get("binance_symbol") or "").strip().upper()
        if not ticker or not symbol:
            continue
        manual_config = manual_mapping.get(ticker, {})
        if _is_manual_locked(manual_config):
            locked = dict(manual_config)
            locked["binance_symbol"] = str(locked.get("binance_symbol") or symbol).strip().upper()
            locked["market_type"] = "usdm_futures"
            locked["manually_locked"] = True
            locked["mapping_status"] = MAPPING_MANUAL_LOCKED
            result[ticker] = locked
            continue
        quality = str(record.get("mapping_quality") or MAPPING_INVALID).strip()
        confidence = "auto_available" if quality in {MAPPING_AVAILABLE, MAPPING_US_EQUITY_VERIFIED, MAPPING_ETF_VERIFIED} else "manual_required"
        result[ticker] = {
            **manual_config,
            "enabled": quality != MAPPING_INVALID,
            "binance_symbol": symbol,
            "market_type": "usdm_futures",
            "quote_currency": "USDT",
            "unit_multiplier": 1,
            "mapping_confidence": confidence,
            "mapping_status": quality,
            "source": record.get("source") or "binance_exchange_info",
            "detected_by": record.get("detected_by") or "binance_internal_category",
            "underlying_type": record.get("underlying_type") or "",
            "underlying_sub_type": record.get("underlying_sub_type") or "",
            "binance_category": record.get("binance_category") or "",
            "tradfi_bucket": record.get("tradfi_bucket") or "",
            "binance_status": record.get("binance_status") or "",
            "binance_price": record.get("binance_price"),
            "stock_ref_price": record.get("stock_ref_price"),
            "diff_pct": record.get("price_diff_pct"),
            "updated_at": record.get("updated_at") or datetime.now(timezone.utc).isoformat(),
            "manually_locked": False,
            "risk_note": _mapping_note(quality),
        }
    return result


def _scan_one_symbol(
    ticker: str,
    binance_symbol: str,
    *,
    price_provider: BinancePriceProvider,
    cache: CacheReadModel,
    manual_config: dict[str, Any] | None,
    exchange_record: dict[str, Any] | None,
    detected_by: str,
    bulk_prices: dict[str, float],
    watchlist_set: set[str],
    position_set: set[str],
    updated_at: str,
    force_refresh: bool,
) -> dict[str, Any]:
    manual_config = manual_config or {}
    if binance_symbol in bulk_prices:
        binance_price = bulk_prices.get(binance_symbol)
        binance_status = "OK"
    else:
        snapshot = price_provider.get_last_price(binance_symbol, market_type="usdm_futures", force_refresh=force_refresh)
        binance_price = _number(getattr(snapshot, "last_price", None) if not isinstance(snapshot, dict) else snapshot.get("last_price"))
        binance_status = str(getattr(snapshot, "error", "") if not isinstance(snapshot, dict) else snapshot.get("error") or "")
    stock_ref = _stock_reference_price(cache, ticker)
    diff_pct = abs(binance_price / stock_ref - 1.0) * 100.0 if binance_price is not None and stock_ref else None
    raw = exchange_record or {}
    underlying_type = _field_text(raw.get("underlyingType") or raw.get("underlying_type"))
    underlying_sub_type = _field_text(raw.get("underlyingSubType") or raw.get("underlying_sub_type"))
    tradfi_bucket = _tradfi_bucket(raw, manual_config)
    quality, reason = _mapping_quality(
        manual_config=manual_config,
        binance_price=binance_price,
        stock_ref=stock_ref,
        diff_pct=diff_pct,
        binance_status=binance_status,
        tradfi_bucket=tradfi_bucket,
    )
    return record_to_dict(
        BinanceEquityScanRecord(
            ticker=ticker,
            binance_symbol=binance_symbol,
            source="binance_exchange_info" if raw else "local_mapping",
            detected_by=detected_by,
            underlying_type=underlying_type,
            underlying_sub_type=underlying_sub_type,
            binance_category=_binance_category(raw),
            tradfi_bucket=tradfi_bucket,
            binance_status=binance_status or str(raw.get("status") or "OK"),
            mapping_quality=quality,
            reason=reason,
            binance_price=binance_price,
            stock_ref_price=stock_ref,
            price_diff_pct=diff_pct,
            manually_locked=_is_manual_locked(manual_config),
            is_watchlist=ticker in watchlist_set,
            is_position=ticker in position_set,
            updated_at=updated_at,
        )
    )


def _mapping_quality(
    *,
    manual_config: dict[str, Any],
    binance_price: float | None,
    stock_ref: float | None,
    diff_pct: float | None,
    binance_status: str,
    tradfi_bucket: str,
) -> tuple[str, str]:
    confidence = str(manual_config.get("mapping_confidence") or "").strip().lower()
    if not manual_config.get("enabled", True) or confidence == "rejected":
        return MAPPING_INVALID, "已忽略或未启用"
    if _is_manual_locked(manual_config):
        if binance_price is None:
            return MAPPING_INVALID, _price_error_text(binance_status)
        return MAPPING_MANUAL_LOCKED, "人工锁定映射"
    if binance_price is None:
        return MAPPING_INVALID, _price_error_text(binance_status)
    return MAPPING_AVAILABLE, "Binance 合约价格读取成功，映射可用"


def _price_error_text(error: str) -> str:
    text = str(error or "").strip()
    if text == "invalid_symbol":
        return "Binance 合约无效"
    return "Binance 价格不可用"


def _bulk_usdm_price_map(provider: BinancePriceProvider) -> dict[str, float]:
    providers = [provider]
    wrapped = getattr(provider, "provider", None)
    if wrapped is not None:
        providers.append(wrapped)
    for candidate in providers:
        getter = getattr(candidate, "_get_market_payload", None)
        if not callable(getter):
            continue
        try:
            payload = getter("usdm_futures", "price", {})
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        result: dict[str, float] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            price = _number(item.get("price"))
            if symbol and price is not None:
                result[symbol] = price
        if result:
            return result
    return {}


def _eligible_usdm_contract(raw: dict[str, Any]) -> bool:
    symbol = str(raw.get("symbol") or "").strip().upper()
    if not symbol.endswith(_USDT_SUFFIX):
        return False
    if not parse_us_equity_ticker_from_binance_symbol(symbol):
        return False
    quote = str(raw.get("quoteAsset") or raw.get("quote_asset") or "").strip().upper()
    status = str(raw.get("status") or "").strip().upper()
    contract_type = str(raw.get("contractType") or raw.get("contract_type") or "").strip().upper()
    if quote and quote != "USDT":
        return False
    if status and status != "TRADING":
        return False
    if contract_type and "PERPETUAL" not in contract_type:
        return False
    return True


def _is_internal_tradfi_contract(raw: dict[str, Any]) -> bool:
    combined = f"{_field_text(raw.get('underlyingType') or raw.get('underlying_type'))} {_field_text(raw.get('underlyingSubType') or raw.get('underlying_sub_type'))}"
    return any(keyword in combined.upper() for keyword in _TRADFI_KEYWORDS)


def _tradfi_bucket(raw: dict[str, Any], manual_config: dict[str, Any] | None = None) -> str:
    manual_config = manual_config or {}
    configured = str(manual_config.get("tradfi_bucket") or "").strip().upper()
    if configured in {TRADFI_BUCKET_US_EQUITY, TRADFI_BUCKET_ETF, TRADFI_BUCKET_OTHER}:
        return configured
    combined = f"{_field_text(raw.get('underlyingType') or raw.get('underlying_type'))} {_field_text(raw.get('underlyingSubType') or raw.get('underlying_sub_type'))}".upper()
    if not combined.strip():
        category = str(manual_config.get("binance_category") or "").upper()
        if "ETF" in category:
            return TRADFI_BUCKET_ETF
        if category and any(text in category for text in ("指数", "商品", "RWA", "其他")):
            return TRADFI_BUCKET_OTHER
        return TRADFI_BUCKET_US_EQUITY
    if any(keyword in combined for keyword in ("INDEX", "COMMODITY", "KR_EQUITY", "COIN", "RWA", "PREMARKET", "PRE-IPO", "PREIPO")):
        return TRADFI_BUCKET_OTHER
    if "ETF" in combined:
        return TRADFI_BUCKET_ETF
    if any(keyword in combined for keyword in ("EQUITY", "STOCK", "TRADFI", "TRADITIONAL")):
        return TRADFI_BUCKET_US_EQUITY
    return ""


def _binance_category(raw: dict[str, Any]) -> str:
    combined = f"{_field_text(raw.get('underlyingType') or raw.get('underlying_type'))} {_field_text(raw.get('underlyingSubType') or raw.get('underlying_sub_type'))}".upper()
    if not any(keyword in combined for keyword in _TRADFI_KEYWORDS):
        return ""
    if "KR_EQUITY" in combined:
        return "KR Equity / 其他 TradFi"
    if "PREMARKET" in combined or "PRE-IPO" in combined or "PREIPO" in combined:
        return "Pre-market / 其他 TradFi"
    if "ETF" in combined:
        return "ETF"
    if "INDEX" in combined:
        return "指数 / 其他 TradFi"
    if "COMMODITY" in combined:
        return "商品 / 其他 TradFi"
    if "COIN" in combined or "RWA" in combined:
        return "RWA / 其他 TradFi"
    if "EQUITY" in combined or "STOCK" in combined:
        return "美股"
    return "其他 TradFi"


def _mapping_note(quality: str) -> str:
    if quality in {MAPPING_AVAILABLE, MAPPING_US_EQUITY_VERIFIED, MAPPING_ETF_VERIFIED}:
        return "Binance 合约价格读取成功，映射可用。"
    if quality == MAPPING_PENDING_VERIFICATION:
        return "Binance 价格可用，但尚未完成美股价格 / 盘后锚点校验。"
    if quality == MAPPING_OTHER_TRADFI:
        return "属于其他 TradFi 合约，不进入美股价差主表。"
    if quality == MAPPING_REVIEW:
        return "价格偏差过大或映射可能错配，需要复核。"
    if quality == MAPPING_INVALID:
        return "Binance 价格不可用或合约无效。"
    return "周末价差模块本地映射。"


def _field_text(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        return " / ".join(str(item or "").strip().upper() for item in value if str(item or "").strip())
    return str(value or "").strip().upper()


def _stock_reference_price(cache: CacheReadModel, ticker: str) -> float | None:
    quote = cache.get_quote_payload(ticker) or {}
    for key in ("current_price", "currentPrice", "price", "regularMarketPrice"):
        number = _number(quote.get(key))
        if number is not None:
            return number
    return cache.get_latest_close(ticker)


def _local_equity_universe(cache: CacheReadModel) -> set[str]:
    path = getattr(cache, "path", None)
    if path is None or not Path(path).exists():
        return set()
    tickers: set[str] = set()
    try:
        with sqlite3.connect(path) as conn:
            if _table_exists(conn, "quote_snapshots"):
                tickers.update(str(row[0] or "").strip().upper() for row in conn.execute("SELECT ticker FROM quote_snapshots").fetchall())
            if _table_exists(conn, "price_history"):
                for (ticker,) in conn.execute("SELECT DISTINCT ticker FROM price_history").fetchall():
                    clean = str(ticker or "").strip().upper()
                    if clean.startswith("FMP:"):
                        clean = clean.split(":", 1)[1]
                    tickers.add(clean)
    except sqlite3.Error:
        return set()
    return {ticker for ticker in tickers if ticker and ticker.isalpha() and not ticker.startswith("^")}


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)).fetchone()
    return bool(row)


def _normalize_set(values: Iterable[str] | None) -> set[str]:
    return {str(value or "").strip().upper() for value in values or [] if str(value or "").strip()}


def _is_manual_locked(config: dict[str, Any] | None) -> bool:
    if not isinstance(config, dict):
        return False
    confidence = str(config.get("mapping_confidence") or "").strip().lower()
    return confidence == "confirmed" or bool(config.get("manually_locked"))


def _scan_sort_key(record: dict[str, Any]) -> tuple[int, str]:
    quality = str(record.get("mapping_quality") or "")
    priority = {
        MAPPING_REVIEW: 0,
        MAPPING_US_EQUITY_VERIFIED: 1,
        MAPPING_ETF_VERIFIED: 1,
        MAPPING_MANUAL_LOCKED: 1,
        MAPPING_PENDING_VERIFICATION: 2,
        MAPPING_OTHER_TRADFI: 3,
        MAPPING_ANCHOR_MISSING: 4,
        MAPPING_INVALID: 5,
    }.get(quality, 4)
    return priority, str(record.get("ticker") or "")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
