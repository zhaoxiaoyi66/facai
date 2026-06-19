from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from data.binance_provider import BinanceHTTPPriceProvider, CachedBinancePriceProvider, BinancePriceProvider
from data.cache_read_model import CacheReadModel
from settings import PROJECT_ROOT


DEFAULT_BINANCE_EQUITY_SCAN_CACHE_PATH = PROJECT_ROOT / "data" / "cache" / "binance_equity_scan_cache.json"
SCAN_CACHE_TTL = timedelta(hours=24)

MAPPING_AUTO_USABLE = "自动可用"
MAPPING_REVIEW = "异常复核"
MAPPING_ANCHOR_MISSING = "锚点缺失"
MAPPING_INVALID = "无效映射"
MAPPING_MANUAL_LOCKED = "人工锁定"

_USDT_SUFFIX = "USDT"
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
    binance_status: str = ""
    detected_by: str = "auto_scan"
    mapping_quality: str = MAPPING_INVALID
    reason: str = ""
    binance_price: float | None = None
    stock_ref_price: float | None = None
    price_diff_pct: float | None = None
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
    force_refresh: bool = False,
    max_symbols: int | None = None,
) -> list[dict[str, Any]]:
    price_provider = provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider(), ttl_seconds=60)
    read_model = cache or CacheReadModel()
    manual_mapping = {str(key or "").strip().upper(): dict(value or {}) for key, value in (manual_mapping or {}).items()}
    watchlist_set = _normalize_set(watchlist)
    position_set = _normalize_set(position_symbols)
    local_universe = _normalize_set(known_tickers) | _local_equity_universe(read_model) | set(manual_mapping) | watchlist_set | position_set
    now = datetime.now(timezone.utc).isoformat()

    raw_symbols = list(exchange_symbols) if exchange_symbols is not None else price_provider.list_exchange_symbols(
        market_type="usdm_futures",
        force_refresh=force_refresh,
    )
    records: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()
    for raw in raw_symbols:
        if not isinstance(raw, dict):
            continue
        if not _eligible_usdm_contract(raw):
            continue
        binance_symbol = str(raw.get("symbol") or "").strip().upper()
        ticker = parse_us_equity_ticker_from_binance_symbol(binance_symbol)
        if not ticker or ticker not in local_universe:
            continue
        if ticker in seen_tickers:
            continue
        record = _scan_one_symbol(
            ticker,
            binance_symbol,
            price_provider=price_provider,
            cache=read_model,
            manual_config=manual_mapping.get(ticker),
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
        records.append(
            _scan_one_symbol(
                ticker,
                symbol,
                price_provider=price_provider,
                cache=read_model,
                manual_config=config,
                watchlist_set=watchlist_set,
                position_set=position_set,
                updated_at=now,
                force_refresh=force_refresh,
            )
        )
    return sorted(records, key=_scan_sort_key)


def scan_records_to_mapping(records: Iterable[dict[str, Any]], manual_mapping: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    manual_mapping = {str(key or "").strip().upper(): dict(value or {}) for key, value in (manual_mapping or {}).items()}
    for record in records:
        ticker = str(record.get("ticker") or "").strip().upper()
        symbol = str(record.get("binance_symbol") or "").strip().upper()
        if not ticker or not symbol:
            continue
        manual_config = manual_mapping.get(ticker, {})
        confidence = str(manual_config.get("mapping_confidence") or "").strip().lower()
        if confidence == "confirmed":
            result[ticker] = dict(manual_config)
            result[ticker]["binance_symbol"] = str(manual_config.get("binance_symbol") or symbol).strip().upper()
            result[ticker]["market_type"] = "usdm_futures"
            continue
        result[ticker] = {
            "enabled": record.get("mapping_quality") != MAPPING_INVALID,
            "binance_symbol": symbol,
            "market_type": "usdm_futures",
            "quote_currency": "USDT",
            "unit_multiplier": 1,
            "mapping_confidence": "candidate",
            "risk_note": "Binance 全市场自动扫描，价格正常即可用于观察；异常映射需人工复核。",
        }
    return result


def _scan_one_symbol(
    ticker: str,
    binance_symbol: str,
    *,
    price_provider: BinancePriceProvider,
    cache: CacheReadModel,
    manual_config: dict[str, Any] | None,
    watchlist_set: set[str],
    position_set: set[str],
    updated_at: str,
    force_refresh: bool,
) -> dict[str, Any]:
    manual_config = manual_config or {}
    snapshot = price_provider.get_last_price(binance_symbol, market_type="usdm_futures", force_refresh=force_refresh)
    binance_price = _number(getattr(snapshot, "last_price", None) if not isinstance(snapshot, dict) else snapshot.get("last_price"))
    binance_status = str(getattr(snapshot, "error", "") if not isinstance(snapshot, dict) else snapshot.get("error") or "")
    stock_ref = _stock_reference_price(cache, ticker)
    diff_pct = abs(binance_price / stock_ref - 1.0) * 100.0 if binance_price is not None and stock_ref else None
    quality, reason = _mapping_quality(
        manual_config=manual_config,
        binance_price=binance_price,
        stock_ref=stock_ref,
        diff_pct=diff_pct,
        binance_status=binance_status,
    )
    return record_to_dict(
        BinanceEquityScanRecord(
            ticker=ticker,
            binance_symbol=binance_symbol,
            binance_status=binance_status or "OK",
            detected_by="manual_mapping" if manual_config else "auto_scan",
            mapping_quality=quality,
            reason=reason,
            binance_price=binance_price,
            stock_ref_price=stock_ref,
            price_diff_pct=diff_pct,
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
) -> tuple[str, str]:
    confidence = str(manual_config.get("mapping_confidence") or "").strip().lower()
    if not manual_config.get("enabled", True) or confidence == "rejected":
        return MAPPING_INVALID, "已忽略或未启用"
    if confidence == "confirmed":
        if binance_price is None:
            return MAPPING_INVALID, _price_error_text(binance_status)
        return MAPPING_MANUAL_LOCKED, "人工锁定映射"
    if binance_price is None:
        return MAPPING_INVALID, _price_error_text(binance_status)
    if stock_ref is None:
        return MAPPING_ANCHOR_MISSING, "缺少股票参考价，暂不能做价格校验"
    if diff_pct is not None and diff_pct <= 30:
        return MAPPING_AUTO_USABLE, "Binance 价格可用，且与股票参考价偏差正常"
    return MAPPING_REVIEW, "Binance 价格与股票参考价偏差过大"


def _price_error_text(error: str) -> str:
    text = str(error or "").strip()
    if text == "invalid_symbol":
        return "Binance 合约无效"
    if not text:
        return "Binance 价格不可用"
    return "Binance 价格不可用"


def _eligible_usdm_contract(raw: dict[str, Any]) -> bool:
    symbol = str(raw.get("symbol") or "").strip().upper()
    if not parse_us_equity_ticker_from_binance_symbol(symbol):
        return False
    quote = str(raw.get("quoteAsset") or raw.get("quote_asset") or "").strip().upper()
    status = str(raw.get("status") or "").strip().upper()
    contract_type = str(raw.get("contractType") or raw.get("contract_type") or "").strip().upper()
    if quote and quote != "USDT":
        return False
    if status and status != "TRADING":
        return False
    if contract_type and contract_type != "PERPETUAL":
        return False
    return True


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


def _scan_sort_key(record: dict[str, Any]) -> tuple[int, str]:
    quality = str(record.get("mapping_quality") or "")
    priority = {
        MAPPING_REVIEW: 0,
        MAPPING_AUTO_USABLE: 1,
        MAPPING_MANUAL_LOCKED: 1,
        MAPPING_ANCHOR_MISSING: 2,
        MAPPING_INVALID: 3,
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
