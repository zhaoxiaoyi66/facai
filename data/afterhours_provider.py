from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from data.providers import get_secret
from settings import PROJECT_ROOT


DEFAULT_AFTERHOURS_CACHE_PATH = PROJECT_ROOT / ".cache" / "afterhours_reference_cache.json"
ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class AfterhoursReference:
    symbol: str
    reference_price: float | None = None
    reference_time: str = ""
    reference_source: str = ""
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    last_trade: float | None = None
    volume: float | None = None
    data_quality: str = "MISSING"
    error: str = ""


class AfterhoursProvider:
    def get_afterhours_reference(
        self,
        symbol: str,
        *,
        regular_close_date: str = "",
        force_refresh: bool = False,
    ) -> AfterhoursReference:
        raise NotImplementedError


class NullAfterhoursProvider(AfterhoursProvider):
    def get_afterhours_reference(
        self,
        symbol: str,
        *,
        regular_close_date: str = "",
        force_refresh: bool = False,
    ) -> AfterhoursReference:
        return AfterhoursReference(symbol=str(symbol or "").upper(), error="afterhours_provider_not_configured")


class CachedAfterhoursProvider(AfterhoursProvider):
    def __init__(
        self,
        provider: AfterhoursProvider | None = None,
        *,
        cache_path: Path = DEFAULT_AFTERHOURS_CACHE_PATH,
    ) -> None:
        self.provider = provider or FMPAfterhoursProvider()
        self.cache_path = cache_path

    def get_afterhours_reference(
        self,
        symbol: str,
        *,
        regular_close_date: str = "",
        force_refresh: bool = False,
    ) -> AfterhoursReference:
        normalized = str(symbol or "").strip().upper()
        cache_key = f"{normalized}:{regular_close_date or 'latest'}"
        if not force_refresh:
            cached = self._read(cache_key)
            if cached is not None:
                return cached
        snapshot = self.provider.get_afterhours_reference(
            normalized,
            regular_close_date=regular_close_date,
            force_refresh=force_refresh,
        )
        if snapshot.data_quality != "MISSING":
            self._write(cache_key, snapshot)
        return snapshot

    def _read(self, cache_key: str) -> AfterhoursReference | None:
        payload = _read_json(self.cache_path)
        raw = payload.get(cache_key) if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return None
        return _reference_from_dict(raw)

    def _write(self, cache_key: str, snapshot: AfterhoursReference) -> None:
        payload = _read_json(self.cache_path)
        if not isinstance(payload, dict):
            payload = {}
        payload[cache_key] = asdict(snapshot)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class FMPAfterhoursProvider(AfterhoursProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://financialmodelingprep.com/stable",
        timeout_seconds: float = 8.0,
    ) -> None:
        self.api_key = api_key or get_secret("FMP_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def get_afterhours_reference(
        self,
        symbol: str,
        *,
        regular_close_date: str = "",
        force_refresh: bool = False,
    ) -> AfterhoursReference:
        normalized = str(symbol or "").strip().upper()
        if not normalized:
            return AfterhoursReference(symbol="", error="missing_symbol")
        if not self.api_key:
            return AfterhoursReference(symbol=normalized, error="missing_fmp_api_key")
        try:
            trade = self._first_row("aftermarket-trade", {"symbol": normalized})
            quote = self._first_row("aftermarket-quote", {"symbol": normalized})
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return AfterhoursReference(symbol=normalized, error=f"{type(exc).__name__}: {exc}")
        return resolve_afterhours_reference(normalized, trade=trade, quote=quote, regular_close_date=regular_close_date)

    def _first_row(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        query = urlencode({**params, "apikey": self.api_key or ""})
        request = Request(f"{self.base_url}/{endpoint}?{query}", headers={"User-Agent": "facai-afterhours/1.0"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        if isinstance(payload, list):
            return next((row for row in payload if isinstance(row, dict)), {})
        return payload if isinstance(payload, dict) else {}


def default_afterhours_provider() -> AfterhoursProvider:
    return CachedAfterhoursProvider()


def resolve_afterhours_reference(
    symbol: str,
    *,
    trade: dict[str, Any] | None = None,
    quote: dict[str, Any] | None = None,
    regular_close_date: str = "",
) -> AfterhoursReference:
    normalized = str(symbol or "").strip().upper()
    trade_row = trade if isinstance(trade, dict) else {}
    quote_row = quote if isinstance(quote, dict) else {}

    trade_price = _first_number(
        trade_row.get("price"),
        trade_row.get("last"),
        trade_row.get("lastPrice"),
        trade_row.get("last_trade"),
    )
    trade_time = _first_text(
        trade_row.get("timestamp"),
        trade_row.get("time"),
        trade_row.get("date"),
        trade_row.get("datetime"),
    )
    trade_volume = _first_number(trade_row.get("size"), trade_row.get("volume"))
    if trade_price is not None and _is_afterhours_session(trade_time, regular_close_date):
        quality = "HIGH" if _is_near_afterhours_close(trade_time, regular_close_date) else "MEDIUM"
        return AfterhoursReference(
            symbol=normalized,
            reference_price=trade_price,
            reference_time=trade_time,
            reference_source="FMP_AFTERHOURS_TRADE",
            last_trade=trade_price,
            volume=trade_volume,
            data_quality=quality,
        )

    bid = _first_number(quote_row.get("bid"), quote_row.get("bidPrice"))
    ask = _first_number(quote_row.get("ask"), quote_row.get("askPrice"))
    quote_time = _first_text(quote_row.get("timestamp"), quote_row.get("time"), quote_row.get("date"), quote_row.get("datetime"))
    quote_volume = _first_number(quote_row.get("volume"), quote_row.get("bidSize"), quote_row.get("askSize"))
    mid = _quote_mid(bid, ask)
    if mid is not None:
        spread_pct = (ask - bid) / mid * 100.0 if bid is not None and ask is not None else None
        quality = "LOW" if spread_pct is not None and spread_pct > 1.0 else "HIGH"
        if quality == "HIGH" and not _is_near_afterhours_close(quote_time, regular_close_date):
            quality = "MEDIUM"
        return AfterhoursReference(
            symbol=normalized,
            reference_price=mid,
            reference_time=quote_time,
            reference_source="FMP_AFTERHOURS_QUOTE_MID",
            bid=bid,
            ask=ask,
            mid=mid,
            volume=quote_volume,
            data_quality=quality,
        )

    return AfterhoursReference(symbol=normalized, error="afterhours_reference_missing")


def _is_afterhours_session(timestamp: str, regular_close_date: str) -> bool:
    parsed = _parse_et_time(timestamp)
    if parsed is None:
        return True
    if regular_close_date and parsed.date().isoformat() != regular_close_date:
        return False
    return time(16, 0) <= parsed.time() <= time(20, 0)


def _is_near_afterhours_close(timestamp: str, regular_close_date: str) -> bool:
    parsed = _parse_et_time(timestamp)
    if parsed is None:
        return False
    if regular_close_date and parsed.date().isoformat() != regular_close_date:
        return False
    return time(19, 55) <= parsed.time() <= time(20, 0)


def _parse_et_time(value: str) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed.astimezone(ET)


def _quote_mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2.0


def _reference_from_dict(raw: dict[str, Any]) -> AfterhoursReference:
    return AfterhoursReference(
        symbol=str(raw.get("symbol") or ""),
        reference_price=_number(raw.get("reference_price")),
        reference_time=str(raw.get("reference_time") or ""),
        reference_source=str(raw.get("reference_source") or ""),
        bid=_number(raw.get("bid")),
        ask=_number(raw.get("ask")),
        mid=_number(raw.get("mid")),
        last_trade=_number(raw.get("last_trade")),
        volume=_number(raw.get("volume")),
        data_quality=str(raw.get("data_quality") or "MISSING"),
        error=str(raw.get("error") or ""),
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
