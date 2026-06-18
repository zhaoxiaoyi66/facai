from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


def select_latest_history_key(rows: Iterable[tuple[Any, Any, Any]], symbol: object) -> str | None:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return None
    ranked = sorted(
        rows,
        key=lambda row: (
            _parse_datetime(row[2] if len(row) > 2 else None),
            _parse_datetime(row[1] if len(row) > 1 else None),
            1 if str(row[0] if row else "").upper() == normalized else 0,
        ),
        reverse=True,
    )
    if not ranked:
        return None
    ticker = ranked[0][0] if ranked[0] else None
    return str(ticker) if ticker else None


def _parse_datetime(value: object) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
