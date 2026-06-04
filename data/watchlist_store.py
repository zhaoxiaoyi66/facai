from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WATCHLIST_PATH = PROJECT_ROOT / "config" / "watchlist.yaml"

WATCHLIST_STATUSES = ("active", "waiting_buy_zone", "needs_review", "paused", "rejected")
WATCHLIST_STATUS_LABELS = {
    "active": "正常观察",
    "waiting_buy_zone": "等待击球区",
    "needs_review": "需要复核",
    "paused": "暂停观察",
    "rejected": "已放弃",
}

WATCHLIST_THEMES = (
    "AI 基建",
    "医药器械",
    "软件 SaaS",
    "电力/核电",
    "金融/加密基础设施",
    "消费/其他",
)

DEFAULT_WATCHLIST_STATUS = "active"
DEFAULT_WATCHLIST_THEME = "消费/其他"

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,6})?$")


def normalize_watchlist_symbol(symbol: object) -> str:
    value = str(symbol or "").strip().upper()
    if not value:
        raise ValueError("股票代码不能为空")
    if not _SYMBOL_RE.match(value):
        raise ValueError("股票代码格式无效")
    return value


def load_watchlist_entries(
    path: Path = WATCHLIST_PATH,
    *,
    default_symbols: Iterable[str] | None = None,
) -> list[dict]:
    if not path.exists():
        return [_entry_from_symbol(symbol) for symbol in (default_symbols or [])]

    entries = _parse_watchlist_yaml(path.read_text(encoding="utf-8"))
    if not entries and default_symbols is not None:
        entries = [_entry_from_symbol(symbol) for symbol in default_symbols]
    return _dedupe_entries(entries)


def save_watchlist_entries(entries: Iterable[dict], path: Path = WATCHLIST_PATH) -> list[dict]:
    cleaned = _dedupe_entries(entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_watchlist_yaml(cleaned), encoding="utf-8")
    return cleaned


def get_watchlist_symbols(
    path: Path = WATCHLIST_PATH,
    *,
    default_symbols: Iterable[str] | None = None,
) -> list[str]:
    return [entry["ticker"] for entry in load_watchlist_entries(path, default_symbols=default_symbols)]


def add_watchlist_symbol(
    symbol: object,
    *,
    status: str = DEFAULT_WATCHLIST_STATUS,
    theme: str = DEFAULT_WATCHLIST_THEME,
    added_reason: str = "",
    note: str = "",
    path: Path = WATCHLIST_PATH,
    now: datetime | None = None,
) -> dict:
    ticker = normalize_watchlist_symbol(symbol)
    timestamp = _timestamp(now)
    entries = load_watchlist_entries(path)
    for entry in entries:
        if entry["ticker"] == ticker:
            updated = dict(entry)
            updated.update(
                {
                    "status": _clean_status(status),
                    "theme": _clean_theme(theme),
                    "added_reason": str(added_reason or "").strip(),
                    "note": str(note or "").strip(),
                    "updated_at": timestamp,
                }
            )
            entries[entries.index(entry)] = updated
            save_watchlist_entries(entries, path)
            return {"action": "updated", "entry": updated}

    entry = _entry_from_symbol(
        ticker,
        status=status,
        theme=theme,
        added_reason=added_reason,
        note=note,
        added_at=timestamp,
        updated_at=timestamp,
    )
    entries.append(entry)
    save_watchlist_entries(entries, path)
    return {"action": "added", "entry": entry}


def update_watchlist_symbol(
    symbol: object,
    *,
    status: str | None = None,
    theme: str | None = None,
    added_reason: str | None = None,
    note: str | None = None,
    path: Path = WATCHLIST_PATH,
    now: datetime | None = None,
) -> dict:
    ticker = normalize_watchlist_symbol(symbol)
    entries = load_watchlist_entries(path)
    for index, entry in enumerate(entries):
        if entry["ticker"] != ticker:
            continue
        updated = dict(entry)
        if status is not None:
            updated["status"] = _clean_status(status)
        if theme is not None:
            updated["theme"] = _clean_theme(theme)
        if added_reason is not None:
            updated["added_reason"] = str(added_reason or "").strip()
        if note is not None:
            updated["note"] = str(note or "").strip()
        updated["updated_at"] = _timestamp(now)
        entries[index] = updated
        save_watchlist_entries(entries, path)
        return {"action": "updated", "entry": updated}
    raise ValueError("股票不在观察池")


def remove_watchlist_symbol(symbol: object, *, path: Path = WATCHLIST_PATH) -> dict:
    ticker = normalize_watchlist_symbol(symbol)
    entries = load_watchlist_entries(path)
    remaining = [entry for entry in entries if entry["ticker"] != ticker]
    if len(remaining) == len(entries):
        return {"action": "missing", "ticker": ticker}
    save_watchlist_entries(remaining, path)
    return {"action": "removed", "ticker": ticker}


def batch_add_watchlist_symbols(
    raw_symbols: str | Iterable[str],
    *,
    status: str = DEFAULT_WATCHLIST_STATUS,
    theme: str = DEFAULT_WATCHLIST_THEME,
    path: Path = WATCHLIST_PATH,
    now: datetime | None = None,
) -> dict:
    tokens = _split_symbols(raw_symbols)
    added: list[str] = []
    updated: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        try:
            ticker = normalize_watchlist_symbol(token)
        except ValueError:
            invalid.append(str(token).strip())
            continue
        if ticker in seen:
            continue
        seen.add(ticker)
        result = add_watchlist_symbol(ticker, status=status, theme=theme, path=path, now=now)
        if result["action"] == "added":
            added.append(ticker)
        else:
            updated.append(ticker)
    return {"added": added, "updated": updated, "invalid": invalid}


def format_watchlist_status(status: object) -> str:
    return WATCHLIST_STATUS_LABELS.get(str(status or "").strip(), WATCHLIST_STATUS_LABELS[DEFAULT_WATCHLIST_STATUS])


def _split_symbols(raw_symbols: str | Iterable[str]) -> list[str]:
    if isinstance(raw_symbols, str):
        return raw_symbols.replace(",", "\n").replace(";", "\n").splitlines()
    return [str(symbol) for symbol in raw_symbols]


def _dedupe_entries(entries: Iterable[dict]) -> list[dict]:
    cleaned: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        try:
            normalized = _normalize_entry(entry)
        except ValueError:
            continue
        ticker = normalized["ticker"]
        if ticker in seen:
            continue
        cleaned.append(normalized)
        seen.add(ticker)
    return cleaned


def _normalize_entry(entry: dict) -> dict:
    ticker = normalize_watchlist_symbol(entry.get("ticker") or entry.get("symbol"))
    return {
        "ticker": ticker,
        "status": _clean_status(entry.get("status")),
        "theme": _clean_theme(entry.get("theme")),
        "added_reason": str(entry.get("added_reason") or entry.get("reason") or "").strip(),
        "note": str(entry.get("note") or "").strip(),
        "added_at": str(entry.get("added_at") or "").strip(),
        "updated_at": str(entry.get("updated_at") or "").strip(),
    }


def _entry_from_symbol(
    symbol: object,
    *,
    status: str = DEFAULT_WATCHLIST_STATUS,
    theme: str = "",
    added_reason: str = "",
    note: str = "",
    added_at: str = "",
    updated_at: str = "",
) -> dict:
    return {
        "ticker": normalize_watchlist_symbol(symbol),
        "status": _clean_status(status),
        "theme": _clean_theme(theme) if theme else "",
        "added_reason": str(added_reason or "").strip(),
        "note": str(note or "").strip(),
        "added_at": str(added_at or "").strip(),
        "updated_at": str(updated_at or "").strip(),
    }


def _clean_status(status: object) -> str:
    value = str(status or DEFAULT_WATCHLIST_STATUS).strip()
    if value not in WATCHLIST_STATUSES:
        raise ValueError("观察状态无效")
    return value


def _clean_theme(theme: object) -> str:
    value = str(theme or "").strip()
    if not value:
        return ""
    if value not in WATCHLIST_THEMES:
        raise ValueError("主题/分类无效")
    return value


def _timestamp(now: datetime | None = None) -> str:
    hkt = timezone(timedelta(hours=8))
    value = now or datetime.now(hkt)
    if value.tzinfo is None:
        value = value.replace(tzinfo=hkt)
    return value.astimezone(hkt).isoformat(timespec="seconds")


def _parse_watchlist_yaml(text: str) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    section = ""

    def flush_current() -> None:
        nonlocal current
        if current:
            entries.append(dict(current))
            current = None

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("tickers:"):
            flush_current()
            section = "tickers"
            inline = stripped.removeprefix("tickers:").strip()
            if inline:
                entries.extend(_entry_from_symbol(item) for item in _inline_list(inline))
            continue
        if stripped in {"watchlist:", "entries:"}:
            flush_current()
            section = "entries"
            continue
        if stripped.startswith("- "):
            flush_current()
            item = stripped[2:].strip()
            if section == "entries" and ":" in item:
                key, value = item.split(":", 1)
                current = {key.strip(): _parse_scalar(value)}
            else:
                entries.append(_entry_from_symbol(item))
            continue
        if section == "entries" and current is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current[key.strip()] = _parse_scalar(value)

    flush_current()
    return entries


def _inline_list(value: str) -> list[str]:
    return [item.strip().strip('"').strip("'") for item in value.strip("[]").split(",") if item.strip()]


def _parse_scalar(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    if stripped[0] in {"'", '"'}:
        try:
            parsed = json.loads(stripped)
            return str(parsed)
        except json.JSONDecodeError:
            return stripped.strip('"').strip("'")
    return stripped


def _dump_watchlist_yaml(entries: list[dict]) -> str:
    lines = ["watchlist:"]
    for entry in entries:
        lines.append(f"  - ticker: {_quote(entry['ticker'])}")
        lines.append(f"    status: {_quote(entry['status'])}")
        lines.append(f"    theme: {_quote(entry.get('theme') or '')}")
        lines.append(f"    added_reason: {_quote(entry.get('added_reason') or '')}")
        lines.append(f"    note: {_quote(entry.get('note') or '')}")
        lines.append(f"    added_at: {_quote(entry.get('added_at') or '')}")
        lines.append(f"    updated_at: {_quote(entry.get('updated_at') or '')}")
    return "\n".join(lines) + "\n"


def _quote(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)
