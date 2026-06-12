from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen


CNN_FEAR_GREED_GRAPH_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CNN_FEAR_GREED_REFERER = "https://www.cnn.com/markets/fear-and-greed"
CNN_FEAR_GREED_TIMEOUT_SECONDS = 4


@dataclass(frozen=True)
class FearGreedReading:
    value: float
    rating: str | None
    observation_date: str
    source: str
    raw_payload: str


def fetch_cnn_fear_greed(
    *,
    fetcher: Any | None = None,
    url: str = CNN_FEAR_GREED_GRAPH_URL,
    timeout_seconds: int = CNN_FEAR_GREED_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> FearGreedReading:
    payload = fetcher(url) if fetcher else _read_cnn_graphdata(url, timeout_seconds=timeout_seconds)
    parsed = _parse_json_payload(payload)
    value = _extract_value(parsed)
    if value is None:
        raise RuntimeError("CNN Fear & Greed graphdata did not include a usable score")
    current = _current_payload(parsed)
    return FearGreedReading(
        value=value,
        rating=_extract_rating(parsed),
        observation_date=_extract_observation_date(parsed, now=now or datetime.now(timezone.utc)),
        source="CNN Fear & Greed graphdata",
        raw_payload=_compact_payload(current or parsed),
    )


def _read_cnn_graphdata(url: str, *, timeout_seconds: int) -> Any:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": CNN_FEAR_GREED_REFERER,
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8")


def _parse_json_payload(payload: Any) -> Any:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def _current_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key in ("fear_and_greed", "fearAndGreed", "fearGreed", "current"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return payload


def _extract_value(payload: Any) -> float | None:
    if isinstance(payload, dict):
        candidates = [payload, _current_payload(payload)]
        for candidate in candidates:
            value = _number(
                candidate.get("score")
                or candidate.get("value")
                or candidate.get("index")
                or candidate.get("y")
            )
            if value is not None:
                return value
        historical = payload.get("fear_and_greed_historical") or payload.get("historical") or payload.get("data")
        if isinstance(historical, dict):
            historical = historical.get("data")
        if isinstance(historical, list):
            for row in reversed(historical):
                if isinstance(row, dict):
                    value = _number(row.get("y") or row.get("value") or row.get("score"))
                    if value is not None:
                        return value
    return None


def _extract_rating(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for candidate in (payload, _current_payload(payload)):
        rating = candidate.get("rating") or candidate.get("status") or candidate.get("classification")
        if rating not in (None, ""):
            return str(rating)
    return None


def _extract_observation_date(payload: Any, *, now: datetime) -> str:
    timestamp = None
    current = _current_payload(payload)
    if current:
        timestamp = current.get("timestamp") or current.get("asOf") or current.get("date") or current.get("x")
    if timestamp in (None, "") and isinstance(payload, dict):
        historical = payload.get("fear_and_greed_historical") or payload.get("historical") or payload.get("data")
        if isinstance(historical, dict):
            historical = historical.get("data")
        if isinstance(historical, list) and historical:
            last = next((row for row in reversed(historical) if isinstance(row, dict)), {})
            timestamp = last.get("x") or last.get("timestamp") or last.get("date")
    return _timestamp_or_date_to_date(timestamp) or now.date().isoformat()


def _timestamp_or_date_to_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    number = _number(value)
    if number is not None:
        seconds = number / 1000 if number > 10_000_000_000 else number
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10] if len(text) >= 10 else None


def _compact_payload(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)[:2000]
    except TypeError:
        return str(payload)[:2000]


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
