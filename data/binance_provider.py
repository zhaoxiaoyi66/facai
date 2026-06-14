from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BINANCE_CACHE_PATH = Path(__file__).resolve().parents[1] / ".cache" / "binance_price_cache.json"
DEFAULT_BINANCE_EXCHANGE_INFO_CACHE_PATH = Path(__file__).resolve().parents[1] / ".cache" / "binance_exchange_info_cache.json"
DEFAULT_SPOT_BASE_URLS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api-gcp.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
]
DEFAULT_USDM_BASE_URL = "https://fapi.binance.com"


@dataclass(frozen=True)
class BinancePriceSnapshot:
    symbol: str
    last_price: float | None
    updated_at: str
    bid: float | None = None
    ask: float | None = None
    volume_24h: float | None = None
    funding_rate: float | None = None
    market_type: str = "usdm_futures"
    source: str = "binance_usdm_futures"
    error: str = ""


@dataclass(frozen=True)
class BinanceSymbolValidation:
    symbol: str
    exists: bool
    market_type: str
    quote_currency: str = ""
    status: str = "unknown"
    base_asset: str = ""
    price_available: bool = False
    book_available: bool = False
    volume_available: bool = False
    funding_available: bool = False
    error_message: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class BinanceSymbolCandidate:
    symbol: str
    market_type: str
    base_asset: str = ""
    quote_currency: str = ""
    status: str = "candidate"


@dataclass(frozen=True)
class BinanceCandidateSearchResult:
    market_type: str
    candidates: list[BinanceSymbolCandidate]
    data_source_status: str = "OK"
    error_message: str = ""
    updated_at: str = ""
    symbol_count: int | None = None
    btcusdt_found: bool | None = None
    provider_diagnostic_failed: bool = False


class BinancePriceProvider:
    def get_last_price(
        self,
        symbol: str,
        *,
        market_type: str = "usdm_futures",
        force_refresh: bool = False,
    ) -> BinancePriceSnapshot:
        raise NotImplementedError

    def validate_symbol(self, symbol: str, *, market_type: str = "usdm_futures") -> BinanceSymbolValidation:
        raise NotImplementedError

    def find_symbol_candidates(
        self,
        query: str,
        *,
        market_type: str = "usdm_futures",
        limit: int = 10,
    ) -> list[BinanceSymbolCandidate]:
        raise NotImplementedError

    def find_symbol_candidates_with_status(
        self,
        query: str,
        *,
        market_type: str = "usdm_futures",
        limit: int = 10,
    ) -> BinanceCandidateSearchResult:
        candidates = self.find_symbol_candidates(query, market_type=market_type, limit=limit)
        return BinanceCandidateSearchResult(
            market_type=normalize_market_type(market_type),
            candidates=candidates,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )


class CachedBinancePriceProvider(BinancePriceProvider):
    def __init__(
        self,
        provider: BinancePriceProvider | None = None,
        *,
        cache_path: Path = DEFAULT_BINANCE_CACHE_PATH,
        ttl_seconds: int = 300,
    ) -> None:
        self.provider = provider or BinanceHTTPPriceProvider()
        self.cache_path = cache_path
        self.ttl_seconds = ttl_seconds

    def get_last_price(
        self,
        symbol: str,
        *,
        market_type: str = "usdm_futures",
        force_refresh: bool = False,
    ) -> BinancePriceSnapshot:
        normalized = str(symbol or "").strip().upper()
        normalized_market = normalize_market_type(market_type)
        if not normalized:
            return _error_snapshot(normalized, "missing_symbol", market_type=normalized_market)
        cache_key = f"{normalized_market}:{normalized}"
        if not force_refresh:
            cached = self._read_cached(cache_key)
            if cached is not None:
                return cached
        snapshot = self.provider.get_last_price(normalized, market_type=normalized_market, force_refresh=force_refresh)
        if not snapshot.error:
            self._write_cached(cache_key, snapshot)
        return snapshot

    def validate_symbol(self, symbol: str, *, market_type: str = "usdm_futures") -> BinanceSymbolValidation:
        return self.provider.validate_symbol(symbol, market_type=market_type)

    def find_symbol_candidates(
        self,
        query: str,
        *,
        market_type: str = "usdm_futures",
        limit: int = 10,
    ) -> list[BinanceSymbolCandidate]:
        return self.provider.find_symbol_candidates(query, market_type=market_type, limit=limit)

    def find_symbol_candidates_with_status(
        self,
        query: str,
        *,
        market_type: str = "usdm_futures",
        limit: int = 10,
    ) -> BinanceCandidateSearchResult:
        return self.provider.find_symbol_candidates_with_status(query, market_type=market_type, limit=limit)

    def _read_cached(self, cache_key: str) -> BinancePriceSnapshot | None:
        payload = _read_json(self.cache_path)
        raw = payload.get(cache_key) if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return None
        updated_at = _parse_datetime(raw.get("updated_at"))
        if updated_at is None:
            return None
        age = datetime.now(timezone.utc) - updated_at
        if age > timedelta(seconds=self.ttl_seconds):
            return None
        return _snapshot_from_dict(raw)

    def _write_cached(self, cache_key: str, snapshot: BinancePriceSnapshot) -> None:
        payload = _read_json(self.cache_path)
        if not isinstance(payload, dict):
            payload = {}
        payload[cache_key] = asdict(snapshot)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class BinanceHTTPPriceProvider(BinancePriceProvider):
    def __init__(
        self,
        *,
        spot_base_url: str | None = None,
        futures_base_url: str | None = None,
        timeout_seconds: float = 5.0,
        exchange_info_cache_path: Path | None = DEFAULT_BINANCE_EXCHANGE_INFO_CACHE_PATH,
        exchange_info_ttl_seconds: int = 86_400,
    ) -> None:
        self.spot_base_urls = _spot_base_urls(spot_base_url)
        self.spot_base_url = self.spot_base_urls[0]
        self.futures_base_url = (futures_base_url or os.environ.get("BINANCE_USDM_BASE_URL") or DEFAULT_USDM_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.exchange_info_cache_path = exchange_info_cache_path
        self.exchange_info_ttl_seconds = exchange_info_ttl_seconds

    def get_last_price(
        self,
        symbol: str,
        *,
        market_type: str = "usdm_futures",
        force_refresh: bool = False,
    ) -> BinancePriceSnapshot:
        normalized = str(symbol or "").strip().upper()
        normalized_market = normalize_market_type(market_type)
        if not normalized:
            return _error_snapshot(normalized, "missing_symbol", market_type=normalized_market)
        try:
            if normalized_market == "spot":
                return self._get_spot_snapshot(normalized)
            return self._get_usdm_futures_snapshot(normalized)
        except HTTPError as exc:
            if exc.code in {400, 404}:
                return _error_snapshot(normalized, "invalid_symbol", market_type=normalized_market)
            return _error_snapshot(normalized, f"HTTPError: {exc}", market_type=normalized_market)
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return _error_snapshot(normalized, f"{type(exc).__name__}: {exc}", market_type=normalized_market)

    def _get_spot_snapshot(self, symbol: str) -> BinancePriceSnapshot:
        if not self._symbol_record("spot", symbol):
            return _error_snapshot(symbol, "invalid_symbol", market_type="spot")
        price_payload = self._get_market_json("spot", "price", {"symbol": symbol})
        book = self._get_market_json("spot", "book", {"symbol": symbol})
        ticker = self._get_market_json("spot", "ticker", {"symbol": symbol})
        last_price = _number(price_payload.get("price")) or _number(ticker.get("lastPrice"))
        if last_price is None:
            return _error_snapshot(symbol, "price_missing", market_type="spot")
        return BinancePriceSnapshot(
            symbol=symbol,
            last_price=last_price,
            bid=_number(book.get("bidPrice")),
            ask=_number(book.get("askPrice")),
            volume_24h=_number(ticker.get("volume")),
            funding_rate=None,
            market_type="spot",
            source="binance_spot",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _get_usdm_futures_snapshot(self, symbol: str) -> BinancePriceSnapshot:
        if not self._symbol_record("usdm_futures", symbol):
            return _error_snapshot(symbol, "invalid_symbol", market_type="usdm_futures")
        price_payload = self._get_market_json("usdm_futures", "price", {"symbol": symbol})
        book = self._get_market_json("usdm_futures", "book", {"symbol": symbol})
        ticker = self._get_market_json("usdm_futures", "ticker", {"symbol": symbol})
        funding = self._get_market_json("usdm_futures", "funding", {"symbol": symbol})
        last_price = _number(price_payload.get("price")) or _number(ticker.get("lastPrice"))
        if last_price is None:
            return _error_snapshot(symbol, "price_missing", market_type="usdm_futures")
        return BinancePriceSnapshot(
            symbol=symbol,
            last_price=last_price,
            bid=_number(book.get("bidPrice")),
            ask=_number(book.get("askPrice")),
            volume_24h=_number(ticker.get("volume")),
            funding_rate=_number(funding.get("lastFundingRate")),
            market_type="usdm_futures",
            source="binance_usdm_futures",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def validate_symbol(self, symbol: str, *, market_type: str = "usdm_futures") -> BinanceSymbolValidation:
        normalized = str(symbol or "").strip().upper()
        normalized_market = normalize_market_type(market_type)
        now = datetime.now(timezone.utc).isoformat()
        if not normalized:
            return BinanceSymbolValidation(
                symbol=normalized,
                exists=False,
                market_type=normalized_market,
                status="missing_symbol",
                error_message="missing_symbol",
                updated_at=now,
            )
        try:
            record = self._symbol_record(normalized_market, normalized)
            if not record:
                return BinanceSymbolValidation(
                    symbol=normalized,
                    exists=False,
                    market_type=normalized_market,
                    status="invalid_symbol",
                    error_message="invalid_symbol",
                    updated_at=now,
                )
            price_payload = self._get_market_json(normalized_market, "price", {"symbol": normalized})
            book_payload = self._get_market_json(normalized_market, "book", {"symbol": normalized})
            ticker_payload = self._get_market_json(normalized_market, "ticker", {"symbol": normalized})
            funding_available = False
            if normalized_market == "usdm_futures":
                funding_payload = self._get_market_json(normalized_market, "funding", {"symbol": normalized})
                funding_available = _number(funding_payload.get("lastFundingRate")) is not None
            price_available = _number(price_payload.get("price")) is not None or _number(ticker_payload.get("lastPrice")) is not None
            return BinanceSymbolValidation(
                symbol=normalized,
                exists=True,
                market_type=normalized_market,
                quote_currency=str(record.get("quoteAsset") or record.get("quote_asset") or ""),
                status="valid" if price_available else "symbol_valid_price_missing",
                base_asset=str(record.get("baseAsset") or record.get("base_asset") or ""),
                price_available=price_available,
                book_available=_number(book_payload.get("bidPrice")) is not None and _number(book_payload.get("askPrice")) is not None,
                volume_available=_number(ticker_payload.get("volume")) is not None,
                funding_available=funding_available,
                updated_at=now,
            )
        except HTTPError as exc:
            status = "invalid_symbol" if exc.code in {400, 404} else "api_error"
            return BinanceSymbolValidation(
                symbol=normalized,
                exists=False,
                market_type=normalized_market,
                status=status,
                error_message=f"HTTPError: {exc}",
                updated_at=now,
            )
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return BinanceSymbolValidation(
                symbol=normalized,
                exists=False,
                market_type=normalized_market,
                status="api_error",
                error_message=f"{type(exc).__name__}: {exc}",
                updated_at=now,
            )

    def find_symbol_candidates(
        self,
        query: str,
        *,
        market_type: str = "usdm_futures",
        limit: int = 10,
    ) -> list[BinanceSymbolCandidate]:
        return self.find_symbol_candidates_with_status(query, market_type=market_type, limit=limit).candidates

    def find_symbol_candidates_with_status(
        self,
        query: str,
        *,
        market_type: str = "usdm_futures",
        limit: int = 10,
    ) -> BinanceCandidateSearchResult:
        normalized_query = str(query or "").strip().upper()
        normalized_market = normalize_market_type(market_type)
        now = datetime.now(timezone.utc).isoformat()
        if not normalized_query:
            return BinanceCandidateSearchResult(
                market_type=normalized_market,
                candidates=[],
                data_source_status="OK",
                updated_at=now,
            )
        try:
            payload = self._exchange_info(normalized_market, None)
        except HTTPError as exc:
            return BinanceCandidateSearchResult(
                market_type=normalized_market,
                candidates=[],
                data_source_status=_candidate_status_from_http(exc),
                error_message=f"HTTPError {exc.code}: {exc.reason}",
                updated_at=now,
            )
        except json.JSONDecodeError as exc:
            return BinanceCandidateSearchResult(
                market_type=normalized_market,
                candidates=[],
                data_source_status="PARSE_ERROR",
                error_message=f"JSONDecodeError: {exc}",
                updated_at=now,
            )
        except (URLError, TimeoutError, OSError) as exc:
            return BinanceCandidateSearchResult(
                market_type=normalized_market,
                candidates=[],
                data_source_status="UNAVAILABLE",
                error_message=f"{type(exc).__name__}: {exc}",
                updated_at=now,
            )
        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        if not isinstance(symbols, list):
            return BinanceCandidateSearchResult(
                market_type=normalized_market,
                candidates=[],
                data_source_status="SCHEMA_MISMATCH",
                error_message="exchangeInfo response missing symbols list",
                updated_at=now,
            )
        if not symbols:
            return BinanceCandidateSearchResult(
                market_type=normalized_market,
                candidates=[],
                data_source_status="EMPTY",
                error_message="exchangeInfo symbols list is empty",
                updated_at=now,
                symbol_count=0,
                btcusdt_found=False,
                provider_diagnostic_failed=True,
            )
        btcusdt_found = any(isinstance(item, dict) and str(item.get("symbol") or "").upper() == "BTCUSDT" for item in symbols)
        if not btcusdt_found:
            return BinanceCandidateSearchResult(
                market_type=normalized_market,
                candidates=[],
                data_source_status="UNAVAILABLE",
                error_message="provider diagnostic failed: BTCUSDT not found in exchangeInfo",
                updated_at=now,
                symbol_count=len(symbols),
                btcusdt_found=False,
                provider_diagnostic_failed=True,
            )
        candidates: list[BinanceSymbolCandidate] = []
        for item in symbols:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            if normalized_query not in symbol:
                continue
            candidates.append(
                BinanceSymbolCandidate(
                    symbol=symbol,
                    market_type=normalized_market,
                    base_asset=str(item.get("baseAsset") or ""),
                    quote_currency=str(item.get("quoteAsset") or ""),
                    status="candidate",
                )
            )
            if len(candidates) >= max(1, limit):
                break
        return BinanceCandidateSearchResult(
            market_type=normalized_market,
            candidates=candidates,
            data_source_status="OK",
            updated_at=now,
            symbol_count=len(symbols),
            btcusdt_found=True,
        )

    def _symbol_record(self, market_type: str, symbol: str) -> dict[str, Any] | None:
        payload = self._exchange_info(market_type, symbol)
        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        if isinstance(symbols, list):
            return next(
                (item for item in symbols if isinstance(item, dict) and str(item.get("symbol") or "").upper() == symbol),
                None,
            )
        if str(payload.get("symbol") or "").upper() == symbol:
            return payload
        return None

    def _exchange_info(self, market_type: str, symbol: str | None) -> dict[str, Any]:
        params = {"symbol": symbol} if symbol else {}
        cache_key = self._exchange_info_cache_key(market_type, symbol)
        cached = self._read_exchange_info_cache(cache_key)
        if cached is not None:
            return cached
        try:
            payload = self._get_market_json(market_type, "exchange_info", params)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
            stale = self._read_exchange_info_cache(cache_key, allow_expired=True)
            if stale is not None:
                return stale
            raise
        self._write_exchange_info_cache(cache_key, payload)
        return payload

    def _exchange_info_cache_key(self, market_type: str, symbol: str | None) -> str:
        normalized_market = normalize_market_type(market_type)
        normalized_symbol = str(symbol or "ALL").strip().upper()
        return f"{normalized_market}:{normalized_symbol}"

    def _endpoint(self, market_type: str, kind: str) -> tuple[str, str]:
        return self._endpoint_candidates(market_type, kind)[0]

    def _endpoint_candidates(self, market_type: str, kind: str) -> list[tuple[str, str]]:
        normalized_market = normalize_market_type(market_type)
        if normalized_market == "spot":
            path = {
                "exchange_info": "/api/v3/exchangeInfo",
                "price": "/api/v3/ticker/price",
                "book": "/api/v3/ticker/bookTicker",
                "ticker": "/api/v3/ticker/24hr",
            }[kind]
            return [(base_url, path) for base_url in self.spot_base_urls]
        path = {
            "exchange_info": (self.futures_base_url, "/fapi/v1/exchangeInfo"),
            "price": (self.futures_base_url, "/fapi/v2/ticker/price"),
            "book": (self.futures_base_url, "/fapi/v1/ticker/bookTicker"),
            "ticker": (self.futures_base_url, "/fapi/v1/ticker/24hr"),
            "funding": (self.futures_base_url, "/fapi/v1/premiumIndex"),
        }[kind]
        return [path]

    def _get_market_json(self, market_type: str, kind: str, params: dict[str, str]) -> dict[str, Any]:
        errors: list[Exception] = []
        for base_url, path in self._endpoint_candidates(market_type, kind):
            try:
                return self._get_json(base_url, path, params)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                errors.append(exc)
                continue
        if errors:
            raise errors[-1]
        raise RuntimeError("no Binance endpoint configured")

    def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict[str, Any]:
        query = f"?{urlencode(params)}" if params else ""
        url = f"{base_url.rstrip('/')}{path}{query}"
        request = Request(url, headers={"User-Agent": "facai-weekend-spread/1.2"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return payload if isinstance(payload, dict) else {}

    def _read_exchange_info_cache(self, cache_key: str, *, allow_expired: bool = False) -> dict[str, Any] | None:
        if self.exchange_info_cache_path is None:
            return None
        payload = _read_json(self.exchange_info_cache_path)
        raw = payload.get(cache_key) if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return None
        updated_at = _parse_datetime(raw.get("updated_at"))
        if updated_at is None:
            return None
        if not allow_expired and datetime.now(timezone.utc) - updated_at > timedelta(seconds=self.exchange_info_ttl_seconds):
            return None
        data = raw.get("payload")
        return data if isinstance(data, dict) else None

    def _write_exchange_info_cache(self, cache_key: str, payload: dict[str, Any]) -> None:
        if self.exchange_info_cache_path is None:
            return
        try:
            cache = _read_json(self.exchange_info_cache_path)
            if not isinstance(cache, dict):
                cache = {}
            symbols = payload.get("symbols") if isinstance(payload, dict) else None
            cache[cache_key] = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "market_type": cache_key.split(":", 1)[0],
                "base_url": self.futures_base_url if cache_key.startswith("usdm_futures:") else self.spot_base_url,
                "symbol_count": len(symbols) if isinstance(symbols, list) else None,
                "payload": payload,
            }
            self.exchange_info_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.exchange_info_cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return


def normalize_market_type(market_type: str) -> str:
    value = str(market_type or "").strip().lower().replace("-", "_")
    if value in {"spot", "binance_spot"}:
        return "spot"
    if value in {"futures", "future", "usdt_m_futures", "usdm", "usdm_futures", "binance_futures"}:
        return "usdm_futures"
    return "usdm_futures"


def _spot_base_urls(explicit_base_url: str | None = None) -> list[str]:
    if explicit_base_url:
        return [explicit_base_url.rstrip("/")]
    urls: list[str] = []
    for name in ("BINANCE_SPOT_DATA_BASE_URL", "BINANCE_SPOT_BASE_URL"):
        raw = os.environ.get(name)
        if not raw:
            continue
        urls.extend(part.strip().rstrip("/") for part in raw.split(",") if part.strip())
    urls.extend(DEFAULT_SPOT_BASE_URLS)
    return _dedupe_urls(urls)


def _dedupe_urls(urls: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in urls:
        normalized = str(item or "").strip().rstrip("/")
        if not normalized or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
    return result or [DEFAULT_SPOT_BASE_URLS[0]]


def _candidate_status_from_http(exc: HTTPError) -> str:
    if exc.code in {403, 451}:
        return "BLOCKED"
    if exc.code == 429:
        return "UNAVAILABLE"
    return "UNAVAILABLE"


def _error_snapshot(symbol: str, error: str, *, market_type: str = "usdm_futures") -> BinancePriceSnapshot:
    normalized_market = normalize_market_type(market_type)
    return BinancePriceSnapshot(
        symbol=symbol,
        last_price=None,
        updated_at=datetime.now(timezone.utc).isoformat(),
        market_type=normalized_market,
        source="binance_spot" if normalized_market == "spot" else "binance_usdm_futures",
        error=error,
    )


def _snapshot_from_dict(raw: dict[str, Any]) -> BinancePriceSnapshot:
    market_type = normalize_market_type(str(raw.get("market_type") or "usdm_futures"))
    return BinancePriceSnapshot(
        symbol=str(raw.get("symbol") or "").strip().upper(),
        last_price=_number(raw.get("last_price")),
        bid=_number(raw.get("bid")),
        ask=_number(raw.get("ask")),
        volume_24h=_number(raw.get("volume_24h")),
        funding_rate=_number(raw.get("funding_rate")),
        updated_at=str(raw.get("updated_at") or ""),
        market_type=market_type,
        source=str(raw.get("source") or ("binance_spot" if market_type == "spot" else "binance_usdm_futures")),
        error=str(raw.get("error") or ""),
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_datetime(value: Any) -> datetime | None:
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


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
