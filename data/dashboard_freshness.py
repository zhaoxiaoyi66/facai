from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Iterable

from data.macro_regime import MacroRegimeSnapshot
from data.prices import CACHE_PATH


@dataclass(frozen=True)
class DashboardFreshnessItem:
    key: str
    label: str
    status_text: str
    tone: str
    updated_at: str | None
    source: str
    detail: str


@dataclass(frozen=True)
class DashboardFreshnessSnapshot:
    items: tuple[DashboardFreshnessItem, ...]
    generated_at: str

    def item(self, key: str) -> DashboardFreshnessItem | None:
        return next((item for item in self.items if item.key == key), None)


def build_dashboard_data_freshness(
    tickers: Iterable[str],
    *,
    path: Path = CACHE_PATH,
    macro_regime: MacroRegimeSnapshot | None = None,
    now: datetime | None = None,
) -> DashboardFreshnessSnapshot:
    current = _as_utc(now or datetime.now(timezone.utc))
    symbols = _normalize_symbols(tickers)
    quote_rows = _quote_rows(path, symbols)
    history_rows = _history_rows(path, symbols)
    disclosure_updated_at = _latest_disclosure_updated_at(path, symbols)

    items = (
        _price_item(quote_rows, symbols, current),
        _technical_item(history_rows, symbols, current),
        _fundamental_item(quote_rows, disclosure_updated_at, symbols, current),
        _macro_item(macro_regime, current),
    )
    return DashboardFreshnessSnapshot(items=items, generated_at=current.isoformat())


def dashboard_data_freshness_strip_html(
    snapshot: DashboardFreshnessSnapshot,
    *,
    last_refresh_result: dict[str, Any] | None = None,
    last_macro_refresh_result: dict[str, Any] | None = None,
) -> str:
    pills = "".join(_freshness_pill_html(item) for item in snapshot.items)
    details = "".join(_freshness_detail_row_html(item) for item in snapshot.items)
    refresh_note = _last_refresh_note(last_refresh_result, last_macro_refresh_result)
    refresh_html = f'<div class="dashboard-freshness-refresh">{escape(refresh_note)}</div>' if refresh_note else ""
    return (
        '<section class="dashboard-freshness-strip">'
        '<div class="dashboard-freshness-main">'
        '<strong>数据新鲜度</strong>'
        f'<div class="dashboard-freshness-items">{pills}</div>'
        "</div>"
        '<details class="dashboard-freshness-detail">'
        "<summary>详情</summary>"
        f'<div class="dashboard-freshness-detail-grid">{details}</div>'
        f"{refresh_html}"
        "</details>"
        "</section>"
    )


def _price_item(
    quote_rows: list[dict[str, Any]],
    symbols: list[str],
    now: datetime,
) -> DashboardFreshnessItem:
    timestamps = [
        _first_datetime(
            row.get("payload", {}).get("quote_updated_at"),
            row.get("payload", {}).get("price_updated_at"),
            row.get("fetched_at"),
        )
        for row in quote_rows
    ]
    latest = _latest_datetime(timestamps)
    cached_count = len([item for item in timestamps if item is not None])
    if latest is None:
        return DashboardFreshnessItem("price", "价格", "缺失", "missing", None, "quote_cache", "没有可用 quote 缓存")
    age_seconds = max(0.0, (now - latest).total_seconds())
    if age_seconds <= 15 * 60:
        tone = "fresh"
        status_text = _relative_time_text(latest, now)
    elif age_seconds <= 4 * 3600:
        tone = "warn"
        status_text = _relative_time_text(latest, now)
    else:
        tone = "stale"
        status_text = "过期"
    return DashboardFreshnessItem(
        "price",
        "价格",
        status_text,
        tone,
        latest.isoformat(),
        "quote_cache",
        f"{cached_count}/{len(symbols) or cached_count} 只股票有 quote 更新时间",
    )


def _technical_item(
    history_rows: list[dict[str, Any]],
    symbols: list[str],
    now: datetime,
) -> DashboardFreshnessItem:
    if not history_rows:
        return DashboardFreshnessItem("technical", "技术", "缺失", "missing", None, "technical_cache", "没有可用日线/技术缓存")
    latest_row = max(history_rows, key=lambda row: (_parse_date(row.get("date")) or date.min, _parse_datetime(row.get("fetched_at")) or datetime.min.replace(tzinfo=timezone.utc)))
    bar_date = _parse_date(latest_row.get("date"))
    fetched_at = _parse_datetime(latest_row.get("fetched_at"))
    cached_count = len({str(row.get("symbol") or "").upper() for row in history_rows if row.get("symbol")})
    if bar_date is None:
        return DashboardFreshnessItem("technical", "技术", "缺失", "missing", None, "technical_cache", "日线缓存缺少 bar 日期")
    day_delta = (now.date() - bar_date).days
    if day_delta <= 0:
        tone = "fresh"
        status_text = "今日"
    elif day_delta == 1:
        tone = "fresh"
        status_text = "昨日收盘"
    elif day_delta <= 7:
        tone = "warn"
        status_text = f"{day_delta}天前"
    else:
        tone = "stale"
        status_text = "过期"
    return DashboardFreshnessItem(
        "technical",
        "技术",
        status_text,
        tone,
        (fetched_at or datetime.combine(bar_date, datetime.min.time(), tzinfo=timezone.utc)).isoformat(),
        "technical_cache",
        f"最新日线 {bar_date.isoformat()}，{cached_count}/{len(symbols) or cached_count} 只股票有日线缓存",
    )


def _fundamental_item(
    quote_rows: list[dict[str, Any]],
    disclosure_updated_at: datetime | None,
    symbols: list[str],
    now: datetime,
) -> DashboardFreshnessItem:
    payload_timestamps = [
        _first_datetime(
            row.get("payload", {}).get("fundamental_updated_at"),
            row.get("payload", {}).get("fundamentalUpdatedAt"),
            row.get("payload", {}).get("financial_statement_updated_at"),
            row.get("payload", {}).get("financialStatementUpdatedAt"),
            row.get("payload", {}).get("disclosure_updated_at"),
            row.get("payload", {}).get("disclosureUpdatedAt"),
        )
        for row in quote_rows
    ]
    latest = _latest_datetime([*payload_timestamps, disclosure_updated_at])
    if latest is None:
        return DashboardFreshnessItem("fundamental", "基本面", "缺失", "missing", None, "fundamental_cache / disclosure_cache", "未找到基本面或披露更新时间")
    age_days = max(0, (now.date() - latest.date()).days)
    if age_days <= 3:
        tone = "fresh"
        status_text = _relative_time_text(latest, now) if age_days == 0 else f"{age_days}天前"
    elif age_days <= 30:
        tone = "warn"
        status_text = f"{age_days}天前"
    else:
        tone = "stale"
        status_text = "过期"
    payload_count = len([item for item in payload_timestamps if item is not None])
    disclosure_note = "，含披露缓存" if disclosure_updated_at is not None else ""
    return DashboardFreshnessItem(
        "fundamental",
        "基本面",
        status_text,
        tone,
        latest.isoformat(),
        "fundamental_cache / disclosure_cache",
        f"{payload_count}/{len(symbols) or payload_count} 只股票有基本面更新时间{disclosure_note}",
    )


def _macro_item(
    macro_regime: MacroRegimeSnapshot | None,
    now: datetime,
) -> DashboardFreshnessItem:
    if macro_regime is None or not macro_regime.indicators:
        return DashboardFreshnessItem("macro", "宏观", "缺失", "missing", None, "macro_cache", "未加载宏观缓存")
    latest = _first_datetime(macro_regime.updated_at) or _latest_datetime(
        [
            _first_datetime(item.fetched_at, item.updated_at)
            for item in macro_regime.indicators
        ]
    )
    status = str(macro_regime.data_status or "缺失")
    if status == "缺失":
        tone = "missing"
        status_text = "缺失"
    elif status == "过期" or macro_regime.is_stale:
        tone = "stale"
        status_text = "过期"
    elif status == "部分可用":
        tone = "warn"
        status_text = "部分可用"
    elif latest is not None and (now - latest).total_seconds() <= 2 * 3600:
        tone = "fresh"
        status_text = _relative_time_text(latest, now)
    else:
        tone = "warn"
        status_text = _relative_time_text(latest, now) if latest is not None else status
    return DashboardFreshnessItem(
        "macro",
        "宏观",
        status_text,
        tone,
        latest.isoformat() if latest else None,
        "macro_cache",
        f"大盘环境 {macro_regime.regime}，数据 {macro_regime.data_status}，置信度 {macro_regime.confidence}",
    )


def _freshness_pill_html(item: DashboardFreshnessItem) -> str:
    return (
        f'<span class="dashboard-freshness-pill {escape(item.tone)}" title="{escape(item.detail)}">'
        f'<b>{escape(item.label)}</b>{escape(item.status_text)}'
        "</span>"
    )


def _freshness_detail_row_html(item: DashboardFreshnessItem) -> str:
    updated = _format_timestamp(item.updated_at)
    return (
        "<div>"
        f"<b>{escape(item.label)}</b>"
        f"<span>{escape(item.status_text)}｜{escape(item.source)}｜{escape(updated)}</span>"
        f"<em>{escape(item.detail)}</em>"
        "</div>"
    )


def _last_refresh_note(
    last_refresh_result: dict[str, Any] | None,
    last_macro_refresh_result: dict[str, Any] | None,
) -> str:
    result = last_refresh_result if isinstance(last_refresh_result, dict) else None
    if not result:
        return ""
    mode = str(result.get("mode") or "刷新")
    mode_label = {
        "PRICE_ONLY": "更新价格",
        "DAILY_TECHNICAL": "更新技术",
        "FUNDAMENTALS_IF_EVENT": "财报后刷新基本面",
        "MACRO_ONLY": "刷新大盘环境",
        "FULL_REFRESH": "强制全量刷新",
    }.get(mode, mode)
    duration = result.get("duration_seconds")
    duration_text = f"{float(duration):.1f}s" if isinstance(duration, (int, float)) else "未知"
    note = (
        f"最近刷新：{mode_label}｜成功 {int(result.get('refreshed_count') or 0)}｜"
        f"跳过 {int(result.get('skipped_count') or 0)}｜失败 {int(result.get('failed_count') or 0)}｜用时 {duration_text}"
    )
    if mode == "MACRO_ONLY" and isinstance(last_macro_refresh_result, dict):
        macro_status = str(last_macro_refresh_result.get("overall_status") or last_macro_refresh_result.get("status") or "")
        if macro_status:
            note = f"{note}｜宏观 {macro_status}"
    return note


def _quote_rows(path: Path, symbols: list[str]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as conn:
        if not _table_exists(conn, "quote_snapshots"):
            return []
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            rows = conn.execute(
                f"SELECT ticker, payload_json, fetched_at FROM quote_snapshots WHERE ticker IN ({placeholders})",
                symbols,
            ).fetchall()
        else:
            rows = conn.execute("SELECT ticker, payload_json, fetched_at FROM quote_snapshots").fetchall()
    parsed = []
    for ticker, payload_json, fetched_at in rows:
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        parsed.append({"ticker": ticker, "payload": payload if isinstance(payload, dict) else {}, "fetched_at": fetched_at})
    return parsed


def _history_rows(path: Path, symbols: list[str]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    history_keys = _history_keys(symbols)
    with closing(sqlite3.connect(path)) as conn:
        if not _table_exists(conn, "price_history"):
            return []
        if history_keys:
            placeholders = ",".join("?" for _ in history_keys)
            rows = conn.execute(
                f"""
                SELECT ticker, MAX(date) AS latest_date, MAX(fetched_at) AS latest_fetch
                FROM price_history
                WHERE ticker IN ({placeholders})
                  AND close IS NOT NULL
                GROUP BY ticker
                """,
                history_keys,
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT ticker, MAX(date) AS latest_date, MAX(fetched_at) AS latest_fetch
                FROM price_history
                WHERE close IS NOT NULL
                GROUP BY ticker
                """
            ).fetchall()
    return [
        {"symbol": str(ticker or "").replace("FMP:", "").upper(), "date": latest_date, "fetched_at": latest_fetch}
        for ticker, latest_date, latest_fetch in rows
        if latest_date
    ]


def _latest_disclosure_updated_at(path: Path, symbols: list[str]) -> datetime | None:
    if not path.exists():
        return None
    candidates: list[datetime | None] = []
    with closing(sqlite3.connect(path)) as conn:
        if _table_exists(conn, "disclosure_metric_values"):
            if symbols:
                placeholders = ",".join("?" for _ in symbols)
                row = conn.execute(
                    f"SELECT MAX(updatedAt) FROM disclosure_metric_values WHERE symbol IN ({placeholders})",
                    symbols,
                ).fetchone()
            else:
                row = conn.execute("SELECT MAX(updatedAt) FROM disclosure_metric_values").fetchone()
            candidates.append(_parse_datetime(row[0] if row else None))
        if _table_exists(conn, "disclosure_fetch_logs"):
            if symbols:
                placeholders = ",".join("?" for _ in symbols)
                row = conn.execute(
                    f"SELECT MAX(fetchedAt) FROM disclosure_fetch_logs WHERE symbol IN ({placeholders})",
                    symbols,
                ).fetchone()
            else:
                row = conn.execute("SELECT MAX(fetchedAt) FROM disclosure_fetch_logs").fetchone()
            candidates.append(_parse_datetime(row[0] if row else None))
    return _latest_datetime(candidates)


def _normalize_symbols(tickers: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    symbols: list[str] = []
    for ticker in tickers:
        symbol = str(ticker or "").strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def _history_keys(symbols: list[str]) -> list[str]:
    keys: list[str] = []
    for symbol in symbols:
        keys.extend([symbol, f"FMP:{symbol}"])
    return keys


def _latest_datetime(values: Iterable[datetime | None]) -> datetime | None:
    parsed = [value for value in values if value is not None]
    return max(parsed) if parsed else None


def _first_datetime(*values: object) -> datetime | None:
    for value in values:
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed_date = _parse_date(text)
        if parsed_date is None:
            return None
        return datetime.combine(parsed_date, datetime.min.time(), tzinfo=timezone.utc)
    return _as_utc(parsed)


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _relative_time_text(updated_at: datetime | None, now: datetime) -> str:
    if updated_at is None:
        return "缺失"
    seconds = max(0, int((now - updated_at).total_seconds()))
    if seconds < 5 * 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60}分钟前"
    if seconds < 24 * 3600:
        return f"{seconds // 3600}小时前"
    return f"{seconds // (24 * 3600)}天前"


def _format_timestamp(value: str | None) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "无更新时间"
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None
