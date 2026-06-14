from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BINANCE_CACHE_PATH = Path(__file__).resolve().parents[1] / ".cache" / "binance_price_cache.json"


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


class BinancePriceProvider:
    def get_last_price(
        self,
        symbol: str,
        *,
        market_type: str = "usdm_futures",
        force_refresh: bool = False,
    ) -> BinancePriceSnapshot:
        raise NotImplementedError


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
        spot_base_url: str = "https://api.binance.com",
        futures_base_url: str = "https://fapi.binance.com",
        timeout_seconds: float = 5.0,
    ) -> None:
        self.spot_base_url = spot_base_url.rstrip("/")
        self.futures_base_url = futures_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

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
        if not self._symbol_exists(self.spot_base_url, "/api/v3/exchangeInfo", symbol):
            return _error_snapshot(symbol, "invalid_symbol", market_type="spot")
        price_payload = self._get_json(self.spot_base_url, "/api/v3/ticker/price", {"symbol": symbol})
        book = self._get_json(self.spot_base_url, "/api/v3/ticker/bookTicker", {"symbol": symbol})
        ticker = self._get_json(self.spot_base_url, "/api/v3/ticker/24hr", {"symbol": symbol})
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
        if not self._symbol_exists(self.futures_base_url, "/fapi/v1/exchangeInfo", symbol):
            return _error_snapshot(symbol, "invalid_symbol", market_type="usdm_futures")
        price_payload = self._get_json(self.futures_base_url, "/fapi/v2/ticker/price", {"symbol": symbol})
        book = self._get_json(self.futures_base_url, "/fapi/v1/ticker/bookTicker", {"symbol": symbol})
        ticker = self._get_json(self.futures_base_url, "/fapi/v1/ticker/24hr", {"symbol": symbol})
        funding = self._get_json(self.futures_base_url, "/fapi/v1/premiumIndex", {"symbol": symbol})
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

    def _symbol_exists(self, base_url: str, path: str, symbol: str) -> bool:
        payload = self._get_json(base_url, path, {"symbol": symbol})
        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        if isinstance(symbols, list):
            return any(str(item.get("symbol") or "").upper() == symbol for item in symbols if isinstance(item, dict))
        return str(payload.get("symbol") or "").upper() == symbol

    def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}{path}?{urlencode(params)}"
        request = Request(url, headers={"User-Agent": "facai-weekend-spread/1.2"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return payload if isinstance(payload, dict) else {}


def normalize_market_type(market_type: str) -> str:
    value = str(market_type or "").strip().lower().replace("-", "_")
    if value in {"spot", "binance_spot"}:
        return "spot"
    if value in {"futures", "future", "usdt_m_futures", "usdm", "usdm_futures", "binance_futures"}:
        return "usdm_futures"
    return "usdm_futures"


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
