from __future__ import annotations

import json
from typing import Any


ADVISORY_REASON_KEYS = (
    "advisoryReasons",
    "advisory_reasons",
    "advisoryWarnings",
    "advisory_warnings",
    "radarAdvisoryWarnings",
    "radar_advisory_warnings",
)
LEGACY_BLOCK_REASON_KEYS = (
    "blockReasons",
    "block_reasons",
    "radarBlockReasons",
    "radar_block_reasons",
)
REVIEW_REASON_KEYS = ("reviewReasons", "review_reasons")


def advisory_reason_list(source: Any, *, include_legacy: bool = True) -> list[str]:
    """Return advisory reasons, falling back to legacy block reason fields.

    The old database/view-model names are intentionally kept here so UI code can
    read one advisory-oriented contract instead of repeating block/gate aliases.
    """

    reasons = _first_reason_list(source, ADVISORY_REASON_KEYS)
    if reasons or not include_legacy:
        return reasons
    return legacy_block_reason_list(source)


def legacy_block_reason_list(source: Any) -> list[str]:
    return _first_reason_list(source, LEGACY_BLOCK_REASON_KEYS)


def review_reason_list(source: Any) -> list[str]:
    return _first_reason_list(source, REVIEW_REASON_KEYS)


def _first_reason_list(source: Any, names: tuple[str, ...]) -> list[str]:
    for name in names:
        values = _text_list(_read_value(source, name))
        if values:
            return values
    return []


def _read_value(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    if hasattr(source, "get"):
        try:
            return source.get(name)
        except Exception:
            pass
    return getattr(source, name, None)


def _text_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return [text]
            return _text_list(parsed)
        return [text]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []
