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
    source: str = "binance_futures"
    error: str = ""


class BinancePriceProvider:
    def get_last_price(self, symbol: str, *, force_refresh: bool = False) -> BinancePriceSnapshot:
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

    def get_last_price(self, symbol: str, *, force_refresh: bool = False) -> BinancePriceSnapshot:
        normalized = str(symbol or "").strip().upper()
        if not normalized:
            return _error_snapshot(normalized, "missing_symbol")
        if not force_refresh:
            cached = self._read_cached(normalized)
            if cached is not None:
                return cached
        snapshot = self.provider.get_last_price(normalized, force_refresh=force_refresh)
        if not snapshot.error:
            self._write_cached(snapshot)
        return snapshot

    def _read_cached(self, symbol: str) -> BinancePriceSnapshot | None:
        payload = _read_json(self.cache_path)
        raw = payload.get(symbol) if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return None
        updated_at = _parse_datetime(raw.get("updated_at"))
        if updated_at is None:
            return None
        age = datetime.now(timezone.utc) - updated_at
        if age > timedelta(seconds=self.ttl_seconds):
            return None
        return _snapshot_from_dict(raw)

    def _write_cached(self, snapshot: BinancePriceSnapshot) -> None:
        payload = _read_json(self.cache_path)
        if not isinstance(payload, dict):
            payload = {}
        payload[snapshot.symbol] = asdict(snapshot)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class BinanceHTTPPriceProvider(BinancePriceProvider):
    def __init__(self, base_url: str = "https://fapi.binance.com", timeout_seconds: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def get_last_price(self, symbol: str, *, force_refresh: bool = False) -> BinancePriceSnapshot:
        normalized = str(symbol or "").strip().upper()
        if not normalized:
            return _error_snapshot(normalized, "missing_symbol")
        try:
            ticker = self._get_json("/fapi/v1/ticker/24hr", {"symbol": normalized})
            book = self._get_json("/fapi/v1/ticker/bookTicker", {"symbol": normalized})
            funding = self._get_json("/fapi/v1/premiumIndex", {"symbol": normalized})
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return _error_snapshot(normalized, f"{type(exc).__name__}: {exc}")

        last_price = _number(ticker.get("lastPrice") if isinstance(ticker, dict) else None)
        if last_price is None:
            last_price = _number(ticker.get("price") if isinstance(ticker, dict) else None)
        if last_price is None:
            return _error_snapshot(normalized, "price_missing")
        return BinancePriceSnapshot(
            symbol=normalized,
            last_price=last_price,
            bid=_number(book.get("bidPrice") if isinstance(book, dict) else None),
            ask=_number(book.get("askPrice") if isinstance(book, dict) else None),
            volume_24h=_number(ticker.get("volume") if isinstance(ticker, dict) else None),
            funding_rate=_number(funding.get("lastFundingRate") if isinstance(funding, dict) else None),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self.base_url}{path}?{urlencode(params)}"
        request = Request(url, headers={"User-Agent": "facai-weekend-spread/1.1"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return payload if isinstance(payload, dict) else {}


def _error_snapshot(symbol: str, error: str) -> BinancePriceSnapshot:
    return BinancePriceSnapshot(
        symbol=symbol,
        last_price=None,
        updated_at=datetime.now(timezone.utc).isoformat(),
        error=error,
    )


def _snapshot_from_dict(raw: dict[str, Any]) -> BinancePriceSnapshot:
    return BinancePriceSnapshot(
        symbol=str(raw.get("symbol") or "").strip().upper(),
        last_price=_number(raw.get("last_price")),
        bid=_number(raw.get("bid")),
        ask=_number(raw.get("ask")),
        volume_24h=_number(raw.get("volume_24h")),
        funding_rate=_number(raw.get("funding_rate")),
        updated_at=str(raw.get("updated_at") or ""),
        source=str(raw.get("source") or "binance_futures"),
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
