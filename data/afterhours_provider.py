from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, time, timezone
import json
from pathlib import Path
from time import sleep
from typing import Any
from uuid import uuid4
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
    missing_reason: str = ""
    cache_status: str = ""
    week_id: str = ""
    friday_date: str = ""
    fetched_at: str = ""
    finalized_at: str = ""
    provider_name: str = ""
    anchor_status: str = ""
    error_message: str = ""


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
        return AfterhoursReference(
            symbol=str(symbol or "").upper(),
            error="afterhours_provider_not_configured",
            missing_reason="PROVIDER_MISSING",
            cache_status="NOT_FETCHED",
        )


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
        provider_name = _cache_provider_name(self.provider)
        cache_key = _anchor_cache_key(
            normalized,
            regular_close_date,
            anchor_type="friday_afterhours_close",
            provider_name=provider_name,
        )
        legacy_cache_keys = [
            _legacy_anchor_cache_key(normalized, regular_close_date),
            f"{normalized}:{regular_close_date or 'latest'}",
        ]
        cache_error = _read_json_error(self.cache_path)
        primary_cached = self._read(cache_key)
        legacy_snapshots = [snapshot for key in legacy_cache_keys if (snapshot := self._read(key)) is not None]
        legacy_cached = next(
            (snapshot for snapshot in legacy_snapshots if _valid_cached_reference(snapshot, regular_close_date) is not None),
            legacy_snapshots[0] if legacy_snapshots else None,
        )
        cached = _valid_cached_reference(primary_cached, regular_close_date) or _valid_cached_reference(
            legacy_cached, regular_close_date
        )
        cache_date_mismatch = (primary_cached is not None or legacy_cached is not None) and cached is None
        if not force_refresh and cached is not None:
            cached_snapshot = _decorate_snapshot(cached, regular_close_date=regular_close_date, cache_status="CACHE_HIT")
            self._write(cache_key, cached_snapshot)
            return cached_snapshot
        corrupt_snapshot = None
        if cache_error:
            corrupt_snapshot = _decorate_snapshot(
                AfterhoursReference(
                    symbol=normalized,
                    error="afterhours_cache_corrupt",
                    missing_reason="CACHE_CORRUPT",
                    cache_status="CACHE_CORRUPT",
                    error_message=cache_error,
                ),
                regular_close_date=regular_close_date,
                cache_status="CACHE_CORRUPT",
            )
            if isinstance(self.provider, NullAfterhoursProvider):
                return corrupt_snapshot
        mismatch_snapshot = None
        if cache_date_mismatch:
            mismatch_snapshot = _decorate_snapshot(
                AfterhoursReference(
                    symbol=normalized,
                    error="afterhours_cache_date_mismatch",
                    missing_reason="CACHE_DATE_MISMATCH",
                    cache_status="CACHE_DATE_MISMATCH",
                    error_message="cached afterhours reference_time does not match regular_close_date",
                ),
                regular_close_date=regular_close_date,
                cache_status="CACHE_DATE_MISMATCH",
            )
            if isinstance(self.provider, NullAfterhoursProvider):
                return mismatch_snapshot
        snapshot = self.provider.get_afterhours_reference(
            normalized,
            regular_close_date=regular_close_date,
            force_refresh=force_refresh,
        )
        if snapshot.data_quality != "MISSING":
            live_snapshot = _decorate_snapshot(snapshot, regular_close_date=regular_close_date, cache_status="API_LIVE")
            self._write(cache_key, live_snapshot)
            return live_snapshot
        if cached is not None:
            cached_snapshot = replace(
                cached,
                cache_status="CACHE_FALLBACK",
                error=snapshot.error,
                error_message=snapshot.error_message or snapshot.error,
                missing_reason="",
            )
            cached_snapshot = _decorate_snapshot(cached_snapshot, regular_close_date=regular_close_date, cache_status="CACHE_FALLBACK")
            self._write(cache_key, cached_snapshot)
            return cached_snapshot
        if corrupt_snapshot is not None and snapshot.missing_reason in {"PROVIDER_MISSING", ""}:
            return corrupt_snapshot
        if mismatch_snapshot is not None and snapshot.data_quality == "MISSING":
            return mismatch_snapshot
        return _decorate_snapshot(snapshot, regular_close_date=regular_close_date, cache_status=snapshot.cache_status or "CACHE_MISSING")

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
        tmp_path = self.cache_path.with_name(f".{self.cache_path.name}.{uuid4().hex}.tmp")
        try:
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            _replace_cache_file(tmp_path, self.cache_path)
        except OSError:
            _unlink_quietly(tmp_path)


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
            return AfterhoursReference(symbol="", error="missing_symbol", missing_reason="FIELD_NOT_PASSED")
        if not self.api_key:
            return AfterhoursReference(symbol=normalized, error="missing_fmp_api_key", missing_reason="API_KEY_MISSING")
        try:
            trade = self._first_row("aftermarket-trade", {"symbol": normalized})
            quote = self._first_row("aftermarket-quote", {"symbol": normalized})
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return AfterhoursReference(symbol=normalized, error=f"{type(exc).__name__}: {exc}", missing_reason="FETCH_FAILED")
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


def _replace_cache_file(tmp_path: Path, target_path: Path) -> None:
    delays = (0.05, 0.15, 0.35)
    for attempt in range(len(delays) + 1):
        try:
            tmp_path.replace(target_path)
            return
        except PermissionError:
            if attempt >= len(delays):
                raise
            sleep(delays[attempt])


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


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
    if mid is not None and _is_afterhours_session(quote_time, regular_close_date):
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

    reason = "NO_AFTERHOURS_QUOTE" if quote_row else "NO_AFTERHOURS_TRADE"
    return AfterhoursReference(symbol=normalized, error="afterhours_reference_missing", missing_reason=reason)


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
    text = str(value).strip()
    try:
        if text.replace(".", "", 1).isdigit():
            number = float(text)
            if number > 1_000_000_000_000_000:
                number /= 1_000_000_000
            elif number > 1_000_000_000_000:
                number /= 1_000
            return datetime.fromtimestamp(number, tz=timezone.utc).astimezone(ET)
    except (OSError, OverflowError, ValueError):
        return None
    text = text.replace("Z", "+00:00")
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
        missing_reason=str(raw.get("missing_reason") or ""),
        cache_status=str(raw.get("cache_status") or ""),
        week_id=str(raw.get("week_id") or ""),
        friday_date=str(raw.get("friday_date") or ""),
        fetched_at=str(raw.get("fetched_at") or ""),
        finalized_at=str(raw.get("finalized_at") or ""),
        provider_name=str(raw.get("provider_name") or ""),
        anchor_status=str(raw.get("anchor_status") or ""),
        error_message=str(raw.get("error_message") or raw.get("error") or ""),
    )


def _valid_cached_reference(snapshot: AfterhoursReference | None, regular_close_date: str) -> AfterhoursReference | None:
    if snapshot is None:
        return None
    if snapshot.reference_price is None:
        return snapshot
    if regular_close_date and snapshot.reference_time and not _is_afterhours_session(snapshot.reference_time, regular_close_date):
        return None
    return snapshot


def _anchor_cache_key(
    symbol: str,
    regular_close_date: str,
    *,
    anchor_type: str = "friday_afterhours_close",
    provider_name: str = "",
) -> str:
    date_text = regular_close_date or "latest"
    week_id = _week_id(regular_close_date) if regular_close_date else "latest"
    normalized_symbol = str(symbol or "").strip().upper()
    normalized_anchor = str(anchor_type or "friday_afterhours_close").strip().lower()
    normalized_provider = str(provider_name or "unknown_provider").strip().lower()
    return f"{week_id}:{date_text}:{normalized_anchor}:{normalized_provider}:{normalized_symbol}"


def _legacy_anchor_cache_key(symbol: str, regular_close_date: str) -> str:
    date_text = regular_close_date or "latest"
    week_id = _week_id(regular_close_date) if regular_close_date else "latest"
    return f"{week_id}:{date_text}:{symbol}"


def _cache_provider_name(provider: Any) -> str:
    name = str(getattr(provider, "provider_name", "") or type(provider).__name__ or "unknown_provider")
    return name.replace(" ", "_")


def _decorate_snapshot(snapshot: AfterhoursReference, *, regular_close_date: str, cache_status: str) -> AfterhoursReference:
    fetched_at = datetime.now(timezone.utc).isoformat()
    anchor_status = _anchor_status(regular_close_date)
    finalized_at = fetched_at if anchor_status == "FINAL" and snapshot.reference_price is not None else snapshot.finalized_at
    return replace(
        snapshot,
        week_id=snapshot.week_id or _week_id(regular_close_date),
        friday_date=snapshot.friday_date or regular_close_date,
        fetched_at=snapshot.fetched_at or fetched_at,
        finalized_at=finalized_at or "",
        provider_name=snapshot.provider_name or _provider_name(snapshot.reference_source),
        anchor_status=snapshot.anchor_status or anchor_status,
        error_message=snapshot.error_message or snapshot.error,
        cache_status=cache_status,
    )


def _week_id(regular_close_date: str) -> str:
    try:
        parsed = datetime.fromisoformat(regular_close_date).date()
    except ValueError:
        return ""
    iso = parsed.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _anchor_status(regular_close_date: str) -> str:
    if not regular_close_date:
        return ""
    try:
        close_date = datetime.fromisoformat(regular_close_date).date()
    except ValueError:
        return ""
    final_cutoff = datetime.combine(close_date, time(20, 5), ET)
    return "FINAL" if datetime.now(timezone.utc).astimezone(ET) >= final_cutoff else "PROVISIONAL"


def _provider_name(reference_source: str) -> str:
    source = str(reference_source or "").upper()
    if source.startswith("POLYGON"):
        return "Polygon/Massive"
    if source.startswith("FMP"):
        return "FMP"
    if source.startswith("ALPHAVANTAGE"):
        return "AlphaVantage"
    return ""


def _read_json(path: Path) -> dict[str, Any]:
    payload, _error = _read_json_payload(path)
    return payload


def _read_json_error(path: Path) -> str:
    _payload, error = _read_json_payload(path)
    return error


def _read_json_payload(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, ""
    try:
        text = path.read_text(encoding="utf-8") or "{}"
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        recovered = _recover_cache_payload_from_text(text if "text" in locals() else "")
        if recovered:
            return recovered, ""
        return {}, f"JSONDecodeError: {exc}"
    except OSError as exc:
        return {}, f"OSError: {exc}"
    return (payload if isinstance(payload, dict) else {}), ""


def _recover_cache_payload_from_text(text: str) -> dict[str, Any]:
    if not text:
        return {}
    decoder = json.JSONDecoder()
    recovered: dict[str, Any] = {}
    index = 0
    while True:
        key_start = text.find('"', index)
        if key_start < 0:
            break
        key_end = text.find('"', key_start + 1)
        if key_end < 0:
            break
        key = text[key_start + 1 : key_end]
        colon = text.find(":", key_end + 1)
        if colon < 0:
            break
        brace = text.find("{", colon + 1)
        comma = text.find(",", colon + 1)
        next_quote = text.find('"', colon + 1)
        if brace < 0 or (comma >= 0 and comma < brace) or (next_quote >= 0 and next_quote < brace):
            index = key_end + 1
            continue
        try:
            value, end = decoder.raw_decode(text, brace)
        except json.JSONDecodeError:
            index = key_end + 1
            continue
        if isinstance(value, dict) and ":" in key:
            recovered[key] = value
        index = end
    return recovered


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
