from __future__ import annotations


def format_currency(value: float | None, digits: int = 2) -> str:
    if _is_missing(value):
        return "N/A"
    return f"${value:,.{digits}f}"


def format_large_number(value: float | None) -> str:
    if _is_missing(value):
        return "N/A"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"${value:,.0f}"


def format_compact_number(value: float | None) -> str:
    if _is_missing(value):
        return "N/A"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.2f}T"
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    return f"{value:,.0f}"


def format_percent(value: float | None, digits: int = 1, already_percent: bool = True) -> str:
    if _is_missing(value):
        return "N/A"
    percent = value if already_percent else value * 100
    return f"{percent:,.{digits}f}%"


def format_multiple(value: float | None) -> str:
    if _is_missing(value):
        return "N/A"
    return f"{value:,.1f}x"


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value)
    except Exception:
        return False
