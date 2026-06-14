from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class BinancePriceSnapshot:
    symbol: str
    last_price: float | None
    updated_at: str
    source: str = "binance_futures"
    error: str = ""


class BinancePriceProvider:
    def get_last_price(self, symbol: str, *, force_refresh: bool = False) -> BinancePriceSnapshot:
        raise NotImplementedError


class BinanceHTTPPriceProvider(BinancePriceProvider):
    def __init__(self, base_url: str = "https://fapi.binance.com", timeout_seconds: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def get_last_price(self, symbol: str, *, force_refresh: bool = False) -> BinancePriceSnapshot:
        normalized = str(symbol or "").strip().upper()
        if not normalized:
            return _error_snapshot(normalized, "missing_symbol")
        params = urlencode({"symbol": normalized})
        url = f"{self.base_url}/fapi/v1/ticker/price?{params}"
        try:
            request = Request(url, headers={"User-Agent": "facai-weekend-spread/1.0"})
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return _error_snapshot(normalized, f"{type(exc).__name__}: {exc}")
        price = _number(payload.get("price") if isinstance(payload, dict) else None)
        if price is None:
            return _error_snapshot(normalized, "price_missing")
        return BinancePriceSnapshot(
            symbol=normalized,
            last_price=price,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )


def _error_snapshot(symbol: str, error: str) -> BinancePriceSnapshot:
    return BinancePriceSnapshot(
        symbol=symbol,
        last_price=None,
        updated_at=datetime.now(timezone.utc).isoformat(),
        error=error,
    )


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
