from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from settings import PROJECT_ROOT


DEFAULT_WEEKEND_SPREAD_SNAPSHOT_PATH = PROJECT_ROOT / "data" / "cache" / "weekend_spread_snapshot.json"
SNAPSHOT_TTL = timedelta(hours=24)
FAILURE_TTL = timedelta(minutes=15)


def mapping_hash(mapping: dict[str, Any]) -> str:
    normalized: dict[str, Any] = {}
    for ticker, config in sorted((mapping or {}).items()):
        if not isinstance(config, dict):
            continue
        normalized[str(ticker or "").upper()] = {
            "enabled": bool(config.get("enabled", True)),
            "binance_symbol": str(config.get("binance_symbol") or "").upper(),
            "market_type": str(config.get("market_type") or ""),
            "quote_currency": str(config.get("quote_currency") or ""),
            "unit_multiplier": config.get("unit_multiplier"),
            "mapping_confidence": str(config.get("mapping_confidence") or ""),
        }
    return _stable_hash(normalized)


def universe_hash(tickers: Iterable[str]) -> str:
    normalized = [str(ticker or "").strip().upper() for ticker in tickers if str(ticker or "").strip()]
    return _stable_hash(normalized)


def read_weekend_spread_snapshot(
    *,
    mapping: dict[str, Any],
    tickers: Iterable[str],
    path: Path = DEFAULT_WEEKEND_SPREAD_SNAPSHOT_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = _read_payload(path)
    if not payload:
        return _status("MISSING", rows=[], message="no snapshot cache")

    rows = list(payload.get("rows") or [])
    generated_at = str(payload.get("generated_at") or "")
    expected_mapping_hash = mapping_hash(mapping)
    expected_universe_hash = universe_hash(tickers)
    state = "FRESH"
    message = "snapshot cache is fresh"

    if str(payload.get("mapping_hash") or "") != expected_mapping_hash:
        state = "MAPPING_CHANGED"
        message = "local mapping changed"
    elif str(payload.get("universe_hash") or "") != expected_universe_hash:
        state = "UNIVERSE_CHANGED"
        message = "watchlist changed"
    elif _is_stale(generated_at, now=now):
        state = "STALE"
        message = "snapshot cache is stale"

    return {
        "cache_state": state,
        "cache_message": message,
        "rows": rows,
        "generated_at": generated_at,
        "data_status": str(payload.get("data_status") or "OK"),
        "mapping_hash": str(payload.get("mapping_hash") or ""),
        "universe_hash": str(payload.get("universe_hash") or ""),
        "last_failure": dict(payload.get("last_failure") or {}),
    }


def write_weekend_spread_snapshot(
    rows: list[dict[str, Any]],
    *,
    mapping: dict[str, Any],
    tickers: Iterable[str],
    path: Path = DEFAULT_WEEKEND_SPREAD_SNAPSHOT_PATH,
    generated_at: datetime | None = None,
    data_status: str = "OK",
) -> dict[str, Any]:
    timestamp = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    payload = {
        "version": 1,
        "generated_at": timestamp,
        "data_status": data_status,
        "mapping_hash": mapping_hash(mapping),
        "universe_hash": universe_hash(tickers),
        "rows": list(rows or []),
        "last_failure": {},
    }
    _write_payload(path, payload)
    return payload


def write_weekend_spread_failure(
    *,
    error_message: str,
    path: Path = DEFAULT_WEEKEND_SPREAD_SNAPSHOT_PATH,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    payload = _read_payload(path)
    timestamp = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    failure = {
        "generated_at": timestamp,
        "data_status": "REFRESH_FAILED",
        "error_message": str(error_message or "refresh failed"),
    }
    payload["last_failure"] = failure
    _write_payload(path, payload)
    return payload


def annotate_cached_rows(rows: list[dict[str, Any]], *, cache_state: str, generated_at: str = "") -> list[dict[str, Any]]:
    source = cache_source_text(cache_state)
    annotated: list[dict[str, Any]] = []
    for row in rows or []:
        item = dict(row)
        item["cache_state"] = cache_state
        item["cache_generated_at"] = generated_at
        item["data_source_text"] = source
        annotated.append(item)
    return annotated


def cache_source_text(cache_state: str) -> str:
    return {
        "FRESH": "缓存",
        "STALE": "过期缓存",
        "MAPPING_CHANGED": "映射已变化",
        "UNIVERSE_CHANGED": "观察池已变化",
        "REFRESH_FAILED": "刷新失败，使用上次成功缓存",
        "API_LIVE": "API 实时",
    }.get(str(cache_state or ""), "缓存")


def has_successful_price(rows: list[dict[str, Any]]) -> bool:
    return any(row.get("status") == "OK" and row.get("binance_last_price") is not None for row in rows or [])


def is_provider_failure(rows: list[dict[str, Any]]) -> bool:
    mapped = [row for row in rows or [] if row.get("binance_symbol")]
    if not mapped:
        return False
    failure_statuses = {"BINANCE_UNAVAILABLE", "PRICE_UNAVAILABLE"}
    return all(row.get("status") in failure_statuses for row in mapped)


def _status(cache_state: str, *, rows: list[dict[str, Any]], message: str) -> dict[str, Any]:
    return {
        "cache_state": cache_state,
        "cache_message": message,
        "rows": rows,
        "generated_at": "",
        "data_status": "",
        "mapping_hash": "",
        "universe_hash": "",
        "last_failure": {},
    }


def _read_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _is_stale(generated_at: str, *, now: datetime | None = None) -> bool:
    parsed = _parse_datetime(generated_at)
    if parsed is None:
        return True
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return current - parsed > SNAPSHOT_TTL


def _parse_datetime(value: str) -> datetime | None:
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


def _stable_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
