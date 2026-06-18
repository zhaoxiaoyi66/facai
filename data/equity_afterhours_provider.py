from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
import json
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from data.afterhours_provider import (
    AfterhoursProvider,
    AfterhoursReference,
    CachedAfterhoursProvider,
    FMPAfterhoursProvider,
    NullAfterhoursProvider,
)
from data.providers import get_secret


ET = ZoneInfo("America/New_York")
POLYGON_BASE_URL = "https://api.polygon.io"
ALPHAVANTAGE_BASE_URL = "https://www.alphavantage.co/query"
ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"


class PolygonOpenCloseAfterhoursProvider(AfterhoursProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = POLYGON_BASE_URL,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.api_key = api_key or _polygon_api_key()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def get_afterhours_reference(
        self,
        symbol: str,
        *,
        regular_close_date: str = "",
        force_refresh: bool = False,
    ) -> AfterhoursReference:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            return AfterhoursReference(symbol="", error="missing_symbol", missing_reason="FIELD_NOT_PASSED")
        if not regular_close_date:
            return AfterhoursReference(symbol=normalized, error="missing_regular_close_date", missing_reason="FIELD_NOT_PASSED")
        if not self.api_key:
            return AfterhoursReference(symbol=normalized, error="missing_polygon_api_key", missing_reason="API_KEY_MISSING")
        try:
            payload = self._get_json(f"v1/open-close/{normalized}/{regular_close_date}", {"adjusted": "true"})
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return AfterhoursReference(symbol=normalized, error=f"{type(exc).__name__}: {exc}", missing_reason="FETCH_FAILED")
        price = _first_number(
            payload.get("afterHours"),
            payload.get("afterhours"),
            payload.get("after_hours"),
            payload.get("afterHoursPrice"),
        )
        if price is None:
            return AfterhoursReference(symbol=normalized, error="afterhours_reference_missing", missing_reason="NO_AFTERHOURS_TRADE")
        return AfterhoursReference(
            symbol=normalized,
            reference_price=price,
            reference_time=_session_close_timestamp(regular_close_date),
            reference_source="POLYGON_OPEN_CLOSE_AFTERHOURS",
            last_trade=price,
            data_quality="HIGH",
        )

    def _get_json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        query = urlencode({**params, "apiKey": self.api_key or ""})
        request = Request(f"{self.base_url}/{endpoint.lstrip('/')}?{query}", headers={"User-Agent": "facai-afterhours/1.0"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return payload if isinstance(payload, dict) else {}


class PolygonTradesAfterhoursProvider(AfterhoursProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = POLYGON_BASE_URL,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.api_key = api_key or _polygon_api_key()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def get_afterhours_reference(
        self,
        symbol: str,
        *,
        regular_close_date: str = "",
        force_refresh: bool = False,
    ) -> AfterhoursReference:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            return AfterhoursReference(symbol="", error="missing_symbol", missing_reason="FIELD_NOT_PASSED")
        if not regular_close_date:
            return AfterhoursReference(symbol=normalized, error="missing_regular_close_date", missing_reason="FIELD_NOT_PASSED")
        if not self.api_key:
            return AfterhoursReference(symbol=normalized, error="missing_polygon_api_key", missing_reason="API_KEY_MISSING")
        try:
            payload = self._get_json(
                f"v3/trades/{normalized}",
                {
                    "timestamp.gte": _session_start(regular_close_date).astimezone(timezone.utc).isoformat(),
                    "timestamp.lte": _session_end(regular_close_date).astimezone(timezone.utc).isoformat(),
                    "sort": "timestamp",
                    "order": "desc",
                    "limit": "50000",
                },
            )
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return AfterhoursReference(symbol=normalized, error=f"{type(exc).__name__}: {exc}", missing_reason="FETCH_FAILED")
        rows = _trade_candidates(payload.get("results") if isinstance(payload, dict) else [], regular_close_date)
        if not rows:
            return AfterhoursReference(symbol=normalized, error="afterhours_reference_missing", missing_reason="NO_AFTERHOURS_TRADE")
        chosen = _choose_trade(rows, regular_close_date)
        quality = "HIGH" if _is_near_afterhours_close(chosen.timestamp, regular_close_date) else "MEDIUM"
        source = "POLYGON_TRADES_1955_2000" if quality == "HIGH" else "POLYGON_AFTERHOURS_LAST_TRADE"
        return AfterhoursReference(
            symbol=normalized,
            reference_price=chosen.price,
            reference_time=chosen.timestamp.isoformat(),
            reference_source=source,
            last_trade=chosen.price,
            volume=chosen.size,
            data_quality=quality,
        )

    def _get_json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        query = urlencode({**params, "apiKey": self.api_key or ""})
        request = Request(f"{self.base_url}/{endpoint.lstrip('/')}?{query}", headers={"User-Agent": "facai-afterhours/1.0"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return payload if isinstance(payload, dict) else {}


class PolygonQuoteMidAfterhoursProvider(AfterhoursProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = POLYGON_BASE_URL,
        timeout_seconds: float = 8.0,
        max_spread_pct: float = 1.0,
    ) -> None:
        self.api_key = api_key or _polygon_api_key()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_spread_pct = max_spread_pct

    def get_afterhours_reference(
        self,
        symbol: str,
        *,
        regular_close_date: str = "",
        force_refresh: bool = False,
    ) -> AfterhoursReference:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            return AfterhoursReference(symbol="", error="missing_symbol", missing_reason="FIELD_NOT_PASSED")
        if not regular_close_date:
            return AfterhoursReference(symbol=normalized, error="missing_regular_close_date", missing_reason="FIELD_NOT_PASSED")
        if not self.api_key:
            return AfterhoursReference(symbol=normalized, error="missing_polygon_api_key", missing_reason="API_KEY_MISSING")
        try:
            payload = self._get_json(
                f"v3/quotes/{normalized}",
                {
                    "timestamp.gte": _session_start(regular_close_date).astimezone(timezone.utc).isoformat(),
                    "timestamp.lte": _session_end(regular_close_date).astimezone(timezone.utc).isoformat(),
                    "sort": "timestamp",
                    "order": "desc",
                    "limit": "50000",
                },
            )
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return AfterhoursReference(symbol=normalized, error=f"{type(exc).__name__}: {exc}", missing_reason="FETCH_FAILED")
        rows = _quote_candidates(payload.get("results") if isinstance(payload, dict) else [], regular_close_date)
        for row in rows:
            mid = _quote_mid(row.bid, row.ask)
            spread = _quote_spread_pct(row.bid, row.ask, mid)
            if mid is None or spread is None or spread > self.max_spread_pct:
                continue
            quality = "HIGH" if _is_near_afterhours_close(row.timestamp, regular_close_date) else "MEDIUM"
            return AfterhoursReference(
                symbol=normalized,
                reference_price=mid,
                reference_time=row.timestamp.isoformat(),
                reference_source="POLYGON_QUOTE_MID",
                bid=row.bid,
                ask=row.ask,
                mid=mid,
                volume=row.size,
                data_quality=quality,
            )
        return AfterhoursReference(symbol=normalized, error="afterhours_reference_missing", missing_reason="NO_AFTERHOURS_QUOTE")

    def _get_json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        query = urlencode({**params, "apiKey": self.api_key or ""})
        request = Request(f"{self.base_url}/{endpoint.lstrip('/')}?{query}", headers={"User-Agent": "facai-afterhours/1.0"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return payload if isinstance(payload, dict) else {}


class AlphaVantageAfterhoursProvider(AfterhoursProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = ALPHAVANTAGE_BASE_URL,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.api_key = api_key or get_secret("ALPHAVANTAGE_API_KEY")
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def get_afterhours_reference(
        self,
        symbol: str,
        *,
        regular_close_date: str = "",
        force_refresh: bool = False,
    ) -> AfterhoursReference:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            return AfterhoursReference(symbol="", error="missing_symbol", missing_reason="FIELD_NOT_PASSED")
        if not regular_close_date:
            return AfterhoursReference(symbol=normalized, error="missing_regular_close_date", missing_reason="FIELD_NOT_PASSED")
        if not self.api_key:
            return AfterhoursReference(symbol=normalized, error="missing_alphavantage_api_key", missing_reason="API_KEY_MISSING")
        try:
            payload = self._get_json(
                {
                    "function": "TIME_SERIES_INTRADAY",
                    "symbol": normalized,
                    "interval": "1min",
                    "extended_hours": "true",
                    "outputsize": "full",
                    "apikey": self.api_key or "",
                }
            )
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return AfterhoursReference(symbol=normalized, error=f"{type(exc).__name__}: {exc}", missing_reason="FETCH_FAILED")
        series = _alpha_time_series(payload)
        rows = _alpha_candidates(series, regular_close_date)
        if not rows:
            return AfterhoursReference(symbol=normalized, error="afterhours_reference_missing", missing_reason="NO_AFTERHOURS_TRADE")
        chosen = rows[0]
        quality = "HIGH" if _is_near_afterhours_close(chosen.timestamp, regular_close_date) else "MEDIUM"
        return AfterhoursReference(
            symbol=normalized,
            reference_price=chosen.price,
            reference_time=chosen.timestamp.isoformat(),
            reference_source="ALPHAVANTAGE_INTRADAY_EXTENDED",
            last_trade=chosen.price,
            volume=chosen.size,
            data_quality=quality,
        )

    def _get_json(self, params: dict[str, str]) -> dict[str, Any]:
        request = Request(f"{self.base_url}?{urlencode(params)}", headers={"User-Agent": "facai-afterhours/1.0"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return payload if isinstance(payload, dict) else {}


class AlpacaAfterhoursProvider(AfterhoursProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str = ALPACA_DATA_BASE_URL,
        feed: str | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.api_key = api_key or get_secret("ALPACA_API_KEY_ID") or get_secret("ALPACA_API_KEY")
        self.api_secret = api_secret or get_secret("ALPACA_API_SECRET_KEY") or get_secret("ALPACA_SECRET_KEY")
        self.base_url = base_url.rstrip("/")
        self.feeds = _normalize_alpaca_feeds(feed or get_secret("ALPACA_AFTERHOURS_FEED"))
        self.feed = self.feeds[0]
        self.timeout_seconds = timeout_seconds

    def get_afterhours_reference(
        self,
        symbol: str,
        *,
        regular_close_date: str = "",
        force_refresh: bool = False,
    ) -> AfterhoursReference:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            return AfterhoursReference(symbol="", error="missing_symbol", missing_reason="FIELD_NOT_PASSED")
        if not regular_close_date:
            return AfterhoursReference(symbol=normalized, error="missing_regular_close_date", missing_reason="FIELD_NOT_PASSED")
        if not self.api_key or not self.api_secret:
            return AfterhoursReference(symbol=normalized, error="missing_alpaca_api_key", missing_reason="API_KEY_MISSING")
        start = datetime.fromisoformat(regular_close_date).replace(tzinfo=ET, hour=19, minute=55, second=0, microsecond=0)
        end = datetime.fromisoformat(regular_close_date).replace(tzinfo=ET, hour=20, minute=0, second=0, microsecond=0)
        permission_denied = False
        fetch_errors: list[str] = []
        for feed in self.feeds:
            try:
                payload = self._get_json(
                    f"v2/stocks/{normalized}/bars",
                    {
                        "timeframe": "1Min",
                        "start": start.astimezone(timezone.utc).isoformat(),
                        "end": end.astimezone(timezone.utc).isoformat(),
                        "adjustment": "raw",
                        "feed": feed,
                        "sort": "asc",
                        "limit": "1000",
                    },
                )
            except HTTPError as exc:
                permission_denied = permission_denied or exc.code in {401, 403}
                fetch_errors.append(f"{feed}:HTTPError:{exc.code}")
                continue
            except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                fetch_errors.append(f"{feed}:{type(exc).__name__}:{exc}")
                continue
            rows = _alpaca_bar_candidates(payload.get("bars") if isinstance(payload, dict) else [], regular_close_date)
            if not rows:
                continue
            chosen = rows[-1]
            quality = "HIGH" if _is_near_afterhours_close(chosen.timestamp, regular_close_date) else "MEDIUM"
            return AfterhoursReference(
                symbol=normalized,
                reference_price=chosen.price,
                reference_time=chosen.timestamp.isoformat(),
                reference_source="ALPACA_AFTERHOURS",
                last_trade=chosen.price,
                volume=chosen.size,
                data_quality=quality,
                provider_name=f"ALPACA_AFTERHOURS_{feed.upper()}",
            )
        if permission_denied and len(fetch_errors) == len(self.feeds):
            return AfterhoursReference(symbol=normalized, error="; ".join(fetch_errors), missing_reason="ALPACA_AFTERHOURS_PERMISSION")
        if fetch_errors and len(fetch_errors) == len(self.feeds):
            return AfterhoursReference(symbol=normalized, error="; ".join(fetch_errors), missing_reason="FETCH_FAILED")
        return AfterhoursReference(symbol=normalized, error="afterhours_reference_missing", missing_reason="NO_ALPACA_AFTERHOURS_BAR")

    def _get_json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/{endpoint.lstrip('/')}?{urlencode(params)}",
            headers={
                "APCA-API-KEY-ID": self.api_key or "",
                "APCA-API-SECRET-KEY": self.api_secret or "",
                "User-Agent": "facai-afterhours/1.0",
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return payload if isinstance(payload, dict) else {}


class MultiProviderAfterhoursProvider(AfterhoursProvider):
    def __init__(self, providers: Iterable[AfterhoursProvider] | None = None) -> None:
        self.providers = list(providers) if providers is not None else [
            PolygonOpenCloseAfterhoursProvider(),
            PolygonTradesAfterhoursProvider(),
            PolygonQuoteMidAfterhoursProvider(),
            AlpacaAfterhoursProvider(),
            FMPAfterhoursProvider(),
            AlphaVantageAfterhoursProvider(),
        ]

    def get_afterhours_reference(
        self,
        symbol: str,
        *,
        regular_close_date: str = "",
        force_refresh: bool = False,
    ) -> AfterhoursReference:
        normalized = _normalize_symbol(symbol)
        misses: list[AfterhoursReference] = []
        for provider in self.providers:
            snapshot = provider.get_afterhours_reference(
                normalized,
                regular_close_date=regular_close_date,
                force_refresh=force_refresh,
            )
            if snapshot.reference_price is not None and snapshot.data_quality != "MISSING":
                return snapshot
            misses.append(snapshot)
        return _collapse_missing(normalized, misses)


@dataclass(frozen=True)
class _TradeCandidate:
    price: float
    timestamp: datetime
    size: float | None = None


@dataclass(frozen=True)
class _QuoteCandidate:
    bid: float
    ask: float
    timestamp: datetime
    size: float | None = None


def default_afterhours_provider() -> AfterhoursProvider:
    return CachedAfterhoursProvider(MultiProviderAfterhoursProvider())


def _polygon_api_key() -> str:
    return get_secret("POLYGON_API_KEY") or get_secret("MASSIVE_API_KEY")


def _normalize_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def _normalize_alpaca_feeds(value: Any) -> list[str]:
    raw = str(value or "").strip()
    candidates = [item.strip().lower() for item in raw.split(",") if item.strip()] if raw else []
    candidates.extend(["sip", "boats", "iex"])
    result: list[str] = []
    for item in candidates:
        if item not in {"sip", "boats", "iex"} or item in result:
            continue
        result.append(item)
    return result or ["sip"]


def _session_start(regular_close_date: str) -> datetime:
    return datetime.fromisoformat(regular_close_date).replace(tzinfo=ET, hour=16, minute=0, second=0, microsecond=0)


def _session_end(regular_close_date: str) -> datetime:
    return datetime.fromisoformat(regular_close_date).replace(tzinfo=ET, hour=20, minute=0, second=0, microsecond=0)


def _session_close_timestamp(regular_close_date: str) -> str:
    return _session_end(regular_close_date).isoformat()


def _trade_candidates(raw_rows: Any, regular_close_date: str) -> list[_TradeCandidate]:
    rows: list[_TradeCandidate] = []
    if not isinstance(raw_rows, list):
        return rows
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        price = _first_number(raw.get("price"), raw.get("p"), raw.get("last"), raw.get("lastPrice"))
        timestamp = _first_timestamp(raw.get("timestamp"), raw.get("sip_timestamp"), raw.get("participant_timestamp"), raw.get("trf_timestamp"), raw.get("t"))
        if price is None or timestamp is None or not _is_afterhours_session(timestamp, regular_close_date):
            continue
        rows.append(_TradeCandidate(price=price, timestamp=timestamp, size=_first_number(raw.get("size"), raw.get("s"), raw.get("volume"))))
    return sorted(rows, key=lambda item: item.timestamp, reverse=True)


def _quote_candidates(raw_rows: Any, regular_close_date: str) -> list[_QuoteCandidate]:
    rows: list[_QuoteCandidate] = []
    if not isinstance(raw_rows, list):
        return rows
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        bid = _first_number(raw.get("bid_price"), raw.get("bidPrice"), raw.get("bid"), raw.get("bp"))
        ask = _first_number(raw.get("ask_price"), raw.get("askPrice"), raw.get("ask"), raw.get("ap"))
        timestamp = _first_timestamp(raw.get("timestamp"), raw.get("sip_timestamp"), raw.get("participant_timestamp"), raw.get("t"))
        if bid is None or ask is None or timestamp is None or not _is_afterhours_session(timestamp, regular_close_date):
            continue
        rows.append(_QuoteCandidate(bid=bid, ask=ask, timestamp=timestamp, size=_first_number(raw.get("size"), raw.get("s"))))
    return sorted(rows, key=lambda item: item.timestamp, reverse=True)


def _alpha_time_series(payload: dict[str, Any]) -> dict[str, Any]:
    for key, value in payload.items():
        if str(key).lower().startswith("time series") and isinstance(value, dict):
            return value
    return {}


def _alpha_candidates(series: dict[str, Any], regular_close_date: str) -> list[_TradeCandidate]:
    rows: list[_TradeCandidate] = []
    for timestamp_text, raw in series.items():
        if not isinstance(raw, dict):
            continue
        timestamp = _parse_timestamp(timestamp_text)
        price = _first_number(raw.get("4. close"), raw.get("close"))
        if timestamp is None or price is None or not _is_afterhours_session(timestamp, regular_close_date):
            continue
        rows.append(_TradeCandidate(price=price, timestamp=timestamp, size=_first_number(raw.get("5. volume"), raw.get("volume"))))
    return sorted(rows, key=lambda item: item.timestamp, reverse=True)


def _alpaca_bar_candidates(raw_rows: Any, regular_close_date: str) -> list[_TradeCandidate]:
    rows: list[_TradeCandidate] = []
    if not isinstance(raw_rows, list):
        return rows
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        price = _first_number(raw.get("c"), raw.get("close"))
        timestamp = _first_timestamp(raw.get("t"), raw.get("timestamp"))
        if price is None or timestamp is None or not _is_afterhours_session(timestamp, regular_close_date):
            continue
        rows.append(_TradeCandidate(price=price, timestamp=timestamp, size=_first_number(raw.get("v"), raw.get("volume"))))
    return sorted(rows, key=lambda item: item.timestamp)


def _choose_trade(rows: list[_TradeCandidate], regular_close_date: str) -> _TradeCandidate:
    near_close = [row for row in rows if _is_near_afterhours_close(row.timestamp, regular_close_date)]
    return near_close[0] if near_close else rows[0]


def _is_afterhours_session(timestamp: datetime, regular_close_date: str) -> bool:
    local = timestamp.astimezone(ET)
    if regular_close_date and local.date().isoformat() != regular_close_date:
        return False
    return time(16, 0) <= local.time() <= time(20, 0)


def _is_near_afterhours_close(timestamp: datetime, regular_close_date: str) -> bool:
    local = timestamp.astimezone(ET)
    if regular_close_date and local.date().isoformat() != regular_close_date:
        return False
    return time(19, 55) <= local.time() <= time(20, 0)


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1_000_000_000_000_000:
            number /= 1_000_000_000
        elif number > 1_000_000_000_000:
            number /= 1_000
        return datetime.fromtimestamp(number, tz=timezone.utc).astimezone(ET)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed.astimezone(ET)


def _first_timestamp(*values: Any) -> datetime | None:
    for value in values:
        parsed = _parse_timestamp(value)
        if parsed is not None:
            return parsed
    return None


def _quote_mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2.0


def _quote_spread_pct(bid: float | None, ask: float | None, mid: float | None) -> float | None:
    if bid is None or ask is None or mid is None or mid <= 0:
        return None
    return (ask - bid) / mid * 100.0


def _collapse_missing(symbol: str, misses: list[AfterhoursReference]) -> AfterhoursReference:
    if not misses:
        return AfterhoursReference(symbol=symbol, error="afterhours_provider_not_configured", missing_reason="PROVIDER_MISSING")
    reasons = [miss.missing_reason for miss in misses if miss.missing_reason]
    errors = [miss.error for miss in misses if miss.error]
    if any(reason not in {"API_KEY_MISSING", "PROVIDER_MISSING"} for reason in reasons):
        reason = next(reason for reason in reasons if reason not in {"API_KEY_MISSING", "PROVIDER_MISSING"})
    elif reasons:
        reason = reasons[0]
    else:
        reason = "NO_AFTERHOURS_TRADE"
    return AfterhoursReference(
        symbol=symbol,
        error="; ".join(errors[:3]),
        missing_reason=reason,
        data_quality="MISSING",
    )


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
