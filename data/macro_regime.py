from __future__ import annotations

import json
import sqlite3
import csv
import io
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd

from data.cache_read_model import CacheReadModel
from data.market_context import build_market_context
from data.prices import CACHE_PATH
from settings import load_watchlist


FEAR_GREED = "fear_greed"
VIX = "vix"
HY_OAS = "hy_oas"
TEN_YEAR_YIELD = "ten_year_yield"
YIELD_CURVE_10Y2Y = "yield_curve_10y2y"
MARKET_TREND = "market_trend"
MARKET_BREADTH = "market_breadth"
DOLLAR_INDEX = "dollar_index"
HYG_CREDIT_PROXY = "hyg_credit_proxy"
SENTIMENT_PROXY = "sentiment_proxy"
CORE_MACRO_INDICATORS = {VIX, TEN_YEAR_YIELD, YIELD_CURVE_10Y2Y, MARKET_TREND, MARKET_BREADTH}
AUXILIARY_MACRO_INDICATORS = {HY_OAS, FEAR_GREED, DOLLAR_INDEX, HYG_CREDIT_PROXY, SENTIMENT_PROXY}

FRED_VIX_SERIES = "VIXCLS"
FRED_HY_OAS_SERIES = "BAMLH0A0HYM2"
FRED_TEN_YEAR_SERIES = "DGS10"
FRED_10Y2Y_SERIES = "T10Y2Y"
FRED_DOLLAR_INDEX_SERIES = "DTWEXBGS"
VIX_MARKET_SYMBOLS = ("AVIX", "^VIX", "VIX")
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
FRED_DOWNLOAD_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?cosd=1900-01-01&coed=9999-12-31&id={series_id}"
CNN_FEAR_GREED_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
MACRO_REQUEST_TIMEOUT_SECONDS = 4
FRED_PRIMARY_TIMEOUT_SECONDS = 2
FRED_FALLBACK_TIMEOUT_SECONDS = 1
FEAR_GREED_TIMEOUT_SECONDS = 2
MACRO_REFRESH_MAX_WORKERS = 8
FRED_CIRCUIT_FAILURE_THRESHOLD = 2
FRED_CIRCUIT_OPEN_HOURS = 8
FRED_PROVIDER = "fred"
FEAR_GREED_PROVIDER = "cnn_fear_greed"

REGIME_RISK_ON = "风险偏好"
REGIME_NEUTRAL = "中性"
REGIME_RISK_OFF = "风险收缩"
REGIME_STRESS = "压力环境"
REGIME_PANIC = "恐慌环境"
REGIME_DATA_GAP = "数据不足"

INDICATOR_LABELS = {
    FEAR_GREED: "恐惧与贪婪指数",
    VIX: "VIX 波动率指数",
    HY_OAS: "美高收益债信用利差",
    TEN_YEAR_YIELD: "10年美债收益率",
    YIELD_CURVE_10Y2Y: "美债10Y-2Y利差",
    MARKET_TREND: "大盘趋势",
    MARKET_BREADTH: "市场宽度",
    DOLLAR_INDEX: "美元指数",
    HYG_CREDIT_PROXY: "信用风险代理",
    SENTIMENT_PROXY: "内部情绪代理",
}


@dataclass(frozen=True)
class MacroIndicatorSnapshot:
    indicator: str
    value: float | None
    change_1d: float | None = None
    change_5d: float | None = None
    change_20d: float | None = None
    percentile_1y: float | None = None
    percentile_5y: float | None = None
    source: str = "cache/manual"
    updated_at: str | None = None
    observation_date: str | None = None
    fetched_at: str | None = None
    is_stale: bool = False
    error: str | None = None
    raw_payload: str | None = None
    regime: str = REGIME_DATA_GAP
    risk_score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    action_hints: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return INDICATOR_LABELS.get(self.indicator, self.indicator)


@dataclass(frozen=True)
class MacroRegimeSnapshot:
    regime: str
    risk_score: float
    indicators: list[MacroIndicatorSnapshot]
    reasons: list[str]
    action_hints: list[str]
    updated_at: str | None = None
    is_stale: bool = False
    source: str = "local cache"
    confidence: str = "低"
    data_status: str = "缺失"

    def indicator(self, name: str) -> MacroIndicatorSnapshot | None:
        normalized = _normalize_indicator(name)
        return next((item for item in self.indicators if item.indicator == normalized), None)


class MacroRegimeStore:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def save_indicator(self, snapshot: MacroIndicatorSnapshot) -> None:
        fetched_at = snapshot.fetched_at or snapshot.updated_at or datetime.now(timezone.utc).isoformat()
        updated_at = snapshot.updated_at or fetched_at
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(
                """
                INSERT INTO macro_indicator_snapshots (
                    indicator, value, change_1d, change_5d, change_20d,
                    percentile_1y, percentile_5y, source, updated_at, observation_date,
                    fetched_at, is_stale, error, raw_payload, meta_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(indicator) DO UPDATE SET
                    value = excluded.value,
                    change_1d = excluded.change_1d,
                    change_5d = excluded.change_5d,
                    change_20d = excluded.change_20d,
                    percentile_1y = excluded.percentile_1y,
                    percentile_5y = excluded.percentile_5y,
                    source = excluded.source,
                    updated_at = excluded.updated_at,
                    observation_date = excluded.observation_date,
                    fetched_at = excluded.fetched_at,
                    is_stale = excluded.is_stale,
                    error = excluded.error,
                    raw_payload = excluded.raw_payload,
                    meta_json = excluded.meta_json
                """,
                (
                    _normalize_indicator(snapshot.indicator),
                    snapshot.value,
                    snapshot.change_1d,
                    snapshot.change_5d,
                    snapshot.change_20d,
                    snapshot.percentile_1y,
                    snapshot.percentile_5y,
                    snapshot.source,
                    updated_at,
                    snapshot.observation_date,
                    fetched_at,
                    1 if snapshot.is_stale else 0,
                    snapshot.error,
                    snapshot.raw_payload,
                    json.dumps(
                        {
                            "reasons": snapshot.reasons,
                            "action_hints": snapshot.action_hints,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.commit()

    def record_indicator_error(
        self,
        indicator: str,
        error: str,
        *,
        source: str = "refresh error",
        now: datetime | None = None,
    ) -> None:
        normalized = _normalize_indicator(indicator)
        fetched_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        existing = self.load_indicator(normalized, now=now, stale_after_hours=24 * 3650)
        with closing(sqlite3.connect(self.path)) as conn:
            if existing is not None:
                conn.execute(
                    """
                    UPDATE macro_indicator_snapshots
                    SET error = ?, meta_json = ?
                    WHERE indicator = ?
                    """,
                    (
                        error,
                        json.dumps(
                            {
                                "reasons": [*existing.reasons, f"刷新失败：{error}"],
                                "action_hints": existing.action_hints,
                            },
                            ensure_ascii=False,
                        ),
                        normalized,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO macro_indicator_snapshots (
                        indicator, value, source, updated_at, fetched_at, is_stale, error, meta_json
                    )
                    VALUES (?, NULL, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(indicator) DO UPDATE SET
                        source = excluded.source,
                        error = excluded.error,
                        meta_json = excluded.meta_json
                    """,
                    (
                        normalized,
                        source,
                        fetched_at,
                        fetched_at,
                        error,
                        json.dumps({"reasons": [f"刷新失败：{error}"], "action_hints": []}, ensure_ascii=False),
                    ),
                )
            conn.commit()

    def record_refresh_log(self, result: dict[str, Any]) -> None:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(
                """
                INSERT INTO macro_refresh_log (
                    started_at, finished_at, duration_seconds, overall_status,
                    refreshed_count, failed_count, stale_count, result_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.get("started_at"),
                    result.get("finished_at"),
                    result.get("duration_seconds"),
                    result.get("overall_status") or result.get("status"),
                    int(result.get("refreshed_count") or 0),
                    int(result.get("failed_count") or 0),
                    int(result.get("stale_count") or 0),
                    json.dumps(result, ensure_ascii=False),
                ),
            )
            conn.commit()

    def is_provider_circuit_open(self, provider: str, *, now: datetime | None = None) -> tuple[bool, str | None]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with closing(sqlite3.connect(self.path)) as conn:
            if not _table_exists(conn, "macro_provider_health"):
                return False, None
            row = conn.execute(
                """
                SELECT circuit_open_until, last_error
                FROM macro_provider_health
                WHERE provider = ?
                """,
                (provider,),
            ).fetchone()
        if not row or not row[0]:
            return False, None
        open_until = _parse_datetime(row[0])
        if open_until is None or open_until <= current:
            return False, None
        return True, str(row[1] or "") or None

    def record_provider_success(self, provider: str) -> None:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(
                """
                INSERT INTO macro_provider_health (
                    provider, failure_count, last_failure_at, circuit_open_until, last_error
                )
                VALUES (?, 0, NULL, NULL, NULL)
                ON CONFLICT(provider) DO UPDATE SET
                    failure_count = 0,
                    circuit_open_until = NULL,
                    last_error = NULL
                """,
                (provider,),
            )
            conn.commit()

    def record_provider_failure(
        self,
        provider: str,
        error: str,
        *,
        now: datetime | None = None,
        failure_threshold: int = FRED_CIRCUIT_FAILURE_THRESHOLD,
        open_for_hours: int = FRED_CIRCUIT_OPEN_HOURS,
    ) -> None:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(
                """
                INSERT INTO macro_provider_health (
                    provider, failure_count, last_failure_at, circuit_open_until, last_error
                )
                VALUES (?, 1, ?, NULL, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    failure_count = macro_provider_health.failure_count + 1,
                    last_failure_at = excluded.last_failure_at,
                    last_error = excluded.last_error
                """,
                (provider, current.isoformat(), error),
            )
            row = conn.execute(
                "SELECT failure_count FROM macro_provider_health WHERE provider = ?",
                (provider,),
            ).fetchone()
            failure_count = int(row[0] or 0) if row else 1
            circuit_open_until = (
                (current + timedelta(hours=open_for_hours)).isoformat()
                if failure_count >= failure_threshold
                else None
            )
            if circuit_open_until:
                conn.execute(
                    "UPDATE macro_provider_health SET circuit_open_until = ? WHERE provider = ?",
                    (circuit_open_until, provider),
                )
            conn.commit()

    def load_indicator(
        self,
        indicator: str,
        *,
        now: datetime | None = None,
        stale_after_hours: float = 36,
    ) -> MacroIndicatorSnapshot | None:
        normalized = _normalize_indicator(indicator)
        with closing(sqlite3.connect(self.path)) as conn:
            if not _table_exists(conn, "macro_indicator_snapshots"):
                return None
            row = conn.execute(
                """
                SELECT indicator, value, change_1d, change_5d, change_20d,
                       percentile_1y, percentile_5y, source, updated_at, meta_json,
                       observation_date, fetched_at, is_stale, error, raw_payload
                FROM macro_indicator_snapshots
                WHERE indicator = ?
                """,
                (normalized,),
            ).fetchone()
        if not row:
            return None
        meta = _json_dict(row[9])
        updated_at = str(row[8] or "") or None
        return MacroIndicatorSnapshot(
            indicator=normalized,
            value=_number(row[1]),
            change_1d=_number(row[2]),
            change_5d=_number(row[3]),
            change_20d=_number(row[4]),
            percentile_1y=_number(row[5]),
            percentile_5y=_number(row[6]),
            source=str(row[7] or "cache/manual"),
            updated_at=updated_at,
            observation_date=str(row[10] or "") or None,
            fetched_at=str(row[11] or "") or None,
            is_stale=bool(row[12]) or _is_stale(str(row[11] or updated_at or "") or None, stale_after_hours, now=now),
            error=str(row[13] or "") or None,
            raw_payload=str(row[14] or "") or None,
            reasons=list(meta.get("reasons") or []),
            action_hints=list(meta.get("action_hints") or []),
        )

    def _ensure_schema(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS macro_indicator_snapshots (
                    indicator TEXT PRIMARY KEY,
                    value REAL,
                    change_1d REAL,
                    change_5d REAL,
                    change_20d REAL,
                    percentile_1y REAL,
                    percentile_5y REAL,
                    source TEXT,
                    updated_at TEXT NOT NULL,
                    meta_json TEXT
                )
                """
            )
            existing = _table_columns(conn, "macro_indicator_snapshots")
            for column, definition in {
                "change_1d": "REAL",
                "change_5d": "REAL",
                "change_20d": "REAL",
                "percentile_1y": "REAL",
                "percentile_5y": "REAL",
                "source": "TEXT",
                "updated_at": "TEXT",
                "observation_date": "TEXT",
                "fetched_at": "TEXT",
                "is_stale": "INTEGER DEFAULT 0",
                "error": "TEXT",
                "raw_payload": "TEXT",
                "meta_json": "TEXT",
            }.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE macro_indicator_snapshots ADD COLUMN {column} {definition}")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS macro_refresh_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT,
                    finished_at TEXT,
                    duration_seconds REAL,
                    overall_status TEXT,
                    refreshed_count INTEGER,
                    failed_count INTEGER,
                    stale_count INTEGER,
                    result_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS macro_provider_health (
                    provider TEXT PRIMARY KEY,
                    failure_count INTEGER DEFAULT 0,
                    last_failure_at TEXT,
                    circuit_open_until TEXT,
                    last_error TEXT
                )
                """
            )
            conn.commit()


def refresh_macro_indicators(
    path: Path = CACHE_PATH,
    *,
    provider: Any | None = None,
    fred_fetcher: Any | None = None,
    fear_greed_fetcher: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    started_at = current.isoformat()
    started_timer = perf_counter()
    store = MacroRegimeStore(path)
    treasury_loader = _TreasurySnapshotLoader(provider=provider, now=current)
    loaders = {
        VIX: lambda: _fetch_vix_snapshot(path, provider=provider, fred_fetcher=fred_fetcher, store=store, now=current),
        HY_OAS: lambda: _fetch_cached_or_fred_snapshot(
            HY_OAS, FRED_HY_OAS_SERIES, store=store, fred_fetcher=fred_fetcher, now=current
        ),
        TEN_YEAR_YIELD: lambda: _fetch_treasury_or_fred_snapshot(
            TEN_YEAR_YIELD,
            FRED_TEN_YEAR_SERIES,
            treasury_loader=treasury_loader,
            store=store,
            fred_fetcher=fred_fetcher,
            now=current,
        ),
        YIELD_CURVE_10Y2Y: lambda: _fetch_treasury_or_fred_snapshot(
            YIELD_CURVE_10Y2Y,
            FRED_10Y2Y_SERIES,
            treasury_loader=treasury_loader,
            store=store,
            fred_fetcher=fred_fetcher,
            now=current,
        ),
        MARKET_TREND: lambda: _fetch_market_trend_snapshot(path, provider=provider, now=current),
        MARKET_BREADTH: lambda: _fetch_market_breadth_snapshot(path, now=current),
        DOLLAR_INDEX: lambda: _fetch_optional_cached_indicator(DOLLAR_INDEX, store=store, now=current),
        FEAR_GREED: lambda: _fetch_cached_or_fear_greed_snapshot(
            store=store, fear_greed_fetcher=fear_greed_fetcher, now=current
        ),
    }
    indicators: dict[str, dict[str, Any]] = {}
    indicator_results: list[dict[str, Any]] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=min(MACRO_REFRESH_MAX_WORKERS, len(loaders))) as executor:
        futures = {indicator: executor.submit(_run_macro_loader, indicator, loader) for indicator, loader in loaders.items()}

    for indicator, future in futures.items():
        indicator, snapshot, exc, duration_seconds = future.result()
        try:
            if exc is not None:
                raise exc
            store.save_indicator(snapshot)
            result = _macro_indicator_refresh_result(
                snapshot,
                status=_macro_refresh_status_for_snapshot(snapshot),
                duration_seconds=duration_seconds,
            )
        except Exception as exc:
            message = _short_error(exc)
            store.record_indicator_error(indicator, message, now=current)
            cached = store.load_indicator(indicator, now=current)
            if _refresh_snapshot_value_usable(cached):
                result = _macro_indicator_refresh_result(
                    cached,
                    status="stale" if cached.is_stale else "cached_fallback",
                    error=message,
                    duration_seconds=duration_seconds,
                    used_cache=True,
                )
            else:
                result = _macro_indicator_refresh_result(
                    cached,
                    indicator=indicator,
                    status="failed",
                    error=message,
                    duration_seconds=duration_seconds,
                    used_cache=cached is not None and cached.value is not None,
                )
            errors.append(f"{indicator}: {message}")
        indicators[indicator] = result
        indicator_results.append(result)

    _append_macro_proxy_result(
        HYG_CREDIT_PROXY,
        indicators,
        indicator_results,
        errors,
        lambda: _fetch_hyg_credit_proxy_snapshot(path, provider=provider, now=current),
        store=store,
        now=current,
        when=_macro_refresh_result_missing(indicators.get(HY_OAS)),
    )
    _append_macro_proxy_result(
        SENTIMENT_PROXY,
        indicators,
        indicator_results,
        errors,
        lambda: _build_sentiment_proxy_snapshot(indicators, now=current),
        store=store,
        now=current,
        when=_macro_refresh_result_missing(indicators.get(FEAR_GREED)),
    )

    core_results = [item for item in indicator_results if item["indicator"] in CORE_MACRO_INDICATORS]
    status = _macro_refresh_overall_status(core_results)
    refreshed_count = sum(1 for item in indicator_results if item["status"] == "success")
    failed_count = sum(1 for item in indicator_results if item["status"] == "failed")
    stale_count = sum(1 for item in indicator_results if item["status"] == "stale" or item.get("is_stale"))
    finished_at = datetime.now(timezone.utc).isoformat()
    result = {
        "status": status,
        "overall_status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(perf_counter() - started_timer, 3),
        "refreshed_count": refreshed_count,
        "failed_count": failed_count,
        "stale_count": stale_count,
        "fetchedAt": finished_at,
        "indicators": indicators,
        "indicator_results": indicator_results,
        "error": "; ".join(errors) if errors else None,
    }
    store.record_refresh_log(result)
    return result


def _run_macro_loader(
    indicator: str,
    loader: Any,
) -> tuple[str, MacroIndicatorSnapshot | None, Exception | None, float]:
    started = perf_counter()
    try:
        return indicator, loader(), None, perf_counter() - started
    except Exception as exc:
        return indicator, None, exc, perf_counter() - started


def _macro_indicator_refresh_result(
    snapshot: MacroIndicatorSnapshot | None,
    *,
    status: str,
    indicator: str | None = None,
    error: str | None = None,
    duration_seconds: float = 0.0,
    used_cache: bool = False,
) -> dict[str, Any]:
    normalized = _normalize_indicator(indicator or (snapshot.indicator if snapshot is not None else ""))
    return {
        "indicator": normalized,
        "label": INDICATOR_LABELS.get(normalized, normalized),
        "category": "core" if normalized in CORE_MACRO_INDICATORS else "auxiliary",
        "status": status,
        "value": snapshot.value if snapshot is not None else None,
        "observation_date": snapshot.observation_date if snapshot is not None else None,
        "fetched_at": snapshot.fetched_at if snapshot is not None else None,
        "source": snapshot.source if snapshot is not None else "refresh error",
        "error": error or (snapshot.error if snapshot is not None else None),
        "duration_seconds": round(max(duration_seconds, 0.0), 3),
        "used_cache": used_cache or status in {"cached_fallback", "stale"},
        "is_stale": bool(snapshot.is_stale) if snapshot is not None else status == "failed",
    }


def _macro_refresh_overall_status(results: list[dict[str, Any]]) -> str:
    if results and all(item.get("status") == "success" for item in results):
        return "success"
    usable = [
        item
        for item in results
        if item.get("status") in {"success", "cached_fallback", "stale"} and item.get("value") is not None
    ]
    return "partial" if usable else "failed"


def _macro_refresh_status_for_snapshot(snapshot: MacroIndicatorSnapshot) -> str:
    source = str(snapshot.source or "").lower()
    if "cache" in source or "cached" in source or source.startswith("缓存"):
        return "stale" if snapshot.is_stale else "cached_fallback"
    return "success"


def _refresh_snapshot_value_usable(snapshot: MacroIndicatorSnapshot | None) -> bool:
    if snapshot is None or snapshot.value is None:
        return False
    value = _number(snapshot.value)
    if snapshot.indicator == VIX and (value is None or value <= 0):
        return False
    return value is not None


def _macro_refresh_result_missing(result: dict[str, Any] | None) -> bool:
    if not result:
        return True
    status = str(result.get("status") or "")
    value = _number(result.get("value"))
    if status in {"success", "cached_fallback"} and value is not None:
        if str(result.get("indicator") or result.get("label") or "").lower() == VIX and value <= 0:
            return True
        return False
    return True


def _append_macro_proxy_result(
    indicator: str,
    indicators: dict[str, dict[str, Any]],
    indicator_results: list[dict[str, Any]],
    errors: list[str],
    loader: Any,
    *,
    store: MacroRegimeStore,
    now: datetime,
    when: bool,
) -> None:
    if not when:
        return
    started = perf_counter()
    snapshot: MacroIndicatorSnapshot | None = None
    try:
        snapshot = loader()
        store.save_indicator(snapshot)
        result = _macro_indicator_refresh_result(
            snapshot,
            status=_macro_refresh_status_for_snapshot(snapshot),
            duration_seconds=perf_counter() - started,
        )
    except Exception as exc:
        message = _short_error(exc)
        store.record_indicator_error(indicator, message, now=now)
        cached = store.load_indicator(indicator, now=now)
        result = _macro_indicator_refresh_result(
            cached,
            indicator=indicator,
            status="failed",
            error=message,
            duration_seconds=perf_counter() - started,
            used_cache=cached is not None and cached.value is not None,
        )
        errors.append(f"{indicator}: {message}")
    indicators[indicator] = result
    indicator_results.append(result)


def load_macro_regime(path: Path = CACHE_PATH, *, now: datetime | None = None) -> MacroRegimeSnapshot:
    store = MacroRegimeStore(path)
    market_vix = _load_vix_snapshot(path, now=now)
    stored_vix = store.load_indicator(VIX, now=now)
    indicators = [
        market_vix if market_vix is not None and not market_vix.is_stale else stored_vix or market_vix,
        store.load_indicator(FEAR_GREED, now=now),
        store.load_indicator(HY_OAS, now=now),
        store.load_indicator(TEN_YEAR_YIELD, now=now),
        store.load_indicator(YIELD_CURVE_10Y2Y, now=now),
        store.load_indicator(MARKET_TREND, now=now),
        store.load_indicator(MARKET_BREADTH, now=now),
        store.load_indicator(DOLLAR_INDEX, now=now),
        store.load_indicator(HYG_CREDIT_PROXY, now=now),
        store.load_indicator(SENTIMENT_PROXY, now=now),
    ]
    return evaluate_macro_regime([item for item in indicators if item is not None], now=now)


def evaluate_macro_regime(
    indicators: list[MacroIndicatorSnapshot] | dict[str, MacroIndicatorSnapshot],
    *,
    now: datetime | None = None,
) -> MacroRegimeSnapshot:
    items = list(indicators.values()) if isinstance(indicators, dict) else list(indicators)
    normalized_items = [_with_indicator_regime(item) for item in items]
    by_name = {item.indicator: item for item in normalized_items}
    vix = by_name.get(VIX)
    fear = by_name.get(FEAR_GREED)
    hy = by_name.get(HY_OAS)
    credit_proxy = by_name.get(HYG_CREDIT_PROXY)
    sentiment_proxy = by_name.get(SENTIMENT_PROXY)
    ten_year = by_name.get(TEN_YEAR_YIELD)
    yield_curve = by_name.get(YIELD_CURVE_10Y2Y)
    market_trend = by_name.get(MARKET_TREND)
    market_breadth = by_name.get(MARKET_BREADTH)
    data_status, confidence = _macro_data_status(normalized_items)
    vix_value = _usable_value(vix)
    fear_value = _usable_value(fear)
    hy_value = _usable_value(hy)
    credit_proxy_value = _usable_value(credit_proxy)
    sentiment_proxy_value = _usable_value(sentiment_proxy)
    sentiment_value = fear_value if fear_value is not None else sentiment_proxy_value
    ten_year_value = _usable_value(ten_year)
    curve_value = _usable_value(yield_curve)
    trend_value = _usable_value(market_trend)
    breadth_value = _usable_value(market_breadth)
    credit_widening = _credit_spread_widening(hy)
    credit_proxy_pressure = _credit_proxy_pressure(credit_proxy)
    credit_pressure = credit_widening or credit_proxy_pressure
    credit_stress = (hy_value is not None and hy_value >= 4.5) or _credit_proxy_stress(credit_proxy)
    qqq_below_50 = _indicator_flag(market_trend, "qqq_below_50")
    spy_below_200 = _indicator_flag(market_trend, "spy_below_200")
    breadth_weak = breadth_value is not None and breadth_value < 40
    any_stale = any(item.is_stale for item in normalized_items if item.value is not None)
    reasons: list[str] = []

    if not normalized_items or all(_usable_value(item) is None for item in normalized_items):
        gap_reasons = ["宏观指标缺失，不能把缺数据当成风险偏好。"]
        if any_stale:
            gap_reasons.append("宏观指标已过期，不能把过期数据当成风险偏好。")
        return MacroRegimeSnapshot(
            regime=REGIME_DATA_GAP,
            risk_score=60,
            indicators=normalized_items,
            reasons=gap_reasons,
            action_hints=_action_hints(REGIME_DATA_GAP),
            updated_at=None,
            is_stale=True,
            confidence=confidence,
            data_status=data_status,
        )

    if any_stale:
        reasons.append("部分宏观指标已过期，不能据此判断为风险偏好。")
    if vix_value is not None:
        reasons.append(f"VIX 当前 {vix_value:.1f}。")
    if hy_value is not None:
        reasons.append(f"美高收益债信用利差当前 {hy_value:.1f}%。")
    if credit_widening:
        reasons.append("信用利差走阔，风险偏好收缩。")
    if hy_value is None and credit_proxy_value is not None:
        reasons.append(_credit_proxy_reason_text(credit_proxy))
    if fear_value is not None:
        reasons.append(f"恐惧与贪婪指数当前 {fear_value:.0f}。")
    elif sentiment_proxy_value is not None:
        reasons.append(_sentiment_proxy_reason_text(sentiment_proxy))
    if ten_year_value is not None:
        reasons.append(f"10年美债收益率当前 {ten_year_value:.2f}%。")
        if _rate_rising_fast(ten_year):
            reasons.append("10年美债快速上行，成长股估值承压。")
    if curve_value is not None:
        reasons.append(f"美债10Y-2Y利差当前 {curve_value:.2f}%。")
    if qqq_below_50:
        reasons.append("QQQ 跌破 50 日均线，AI/软件不追涨。")
    if spy_below_200:
        reasons.append("SPY 跌破 200 日均线，只允许 A 类计划内买入提示。")
    if breadth_weak:
        reasons.append("观察池市场宽度恶化，C 类暂停新增提示。")

    if (
        vix_value is not None
        and vix_value > 30
        and sentiment_value is not None
        and sentiment_value <= 25
        and (credit_pressure or credit_stress)
    ):
        regime = REGIME_PANIC
    elif (
        vix_value is not None
        and vix_value > 25
        and (credit_pressure or credit_stress or qqq_below_50)
    ):
        regime = REGIME_STRESS
    elif (
        (vix_value is not None and vix_value > 20)
        or credit_pressure
        or (hy_value is not None and hy_value >= 4.0)
        or _credit_proxy_pressure(credit_proxy)
        or qqq_below_50
        or spy_below_200
        or breadth_weak
    ):
        regime = REGIME_RISK_OFF
    elif (
        not any_stale
        and vix_value is not None
        and vix_value < 15
        and _credit_spread_tightening(hy)
        and (sentiment_value is None or 45 <= sentiment_value <= 80)
    ):
        regime = REGIME_RISK_ON
    else:
        regime = REGIME_NEUTRAL

    if any_stale and regime == REGIME_RISK_ON:
        regime = REGIME_NEUTRAL
    risk_score = _macro_risk_score(
        vix_value,
        fear_value,
        hy_value,
        credit_widening,
        any_stale,
        ten_year=ten_year_value,
        ten_year_rising=_rate_rising_fast(ten_year),
        credit_proxy=credit_proxy_value,
        qqq_below_50=qqq_below_50,
        spy_below_200=spy_below_200,
        breadth_weak=breadth_weak,
    )
    hints = _action_hints(regime)
    hints = _dedupe(
        [
            *hints,
            *(["成长股估值压力上升，AI/软件只等回踩，不追涨。"] if _rate_rising_fast(ten_year) else []),
            *(["QQQ 跌破 50 日均线，AI/软件不追涨。"] if qqq_below_50 else []),
            *(["SPY 跌破 200 日均线，只允许 A 类计划内买入提示。"] if spy_below_200 else []),
            *(["观察池宽度恶化，C 类暂停新增提示。"] if breadth_weak else []),
        ]
    )
    return MacroRegimeSnapshot(
        regime=regime,
        risk_score=risk_score,
        indicators=normalized_items,
        reasons=_dedupe(reasons),
        action_hints=hints,
        updated_at=_latest_updated_at(normalized_items),
        is_stale=any_stale,
        confidence=confidence,
        data_status=data_status,
    )


def macro_regime_status_text(snapshot: MacroRegimeSnapshot) -> str:
    vix = _indicator_value_text(snapshot.indicator(VIX), empty="缺")
    ten_year = _indicator_value_text(snapshot.indicator(TEN_YEAR_YIELD), empty="缺", suffix="%")
    trend_hint = _trend_summary_text(snapshot.indicator(MARKET_TREND))
    breadth = _indicator_value_text(snapshot.indicator(MARKET_BREADTH), empty="缺")
    credit = _credit_summary_text(snapshot)
    sentiment = _sentiment_summary_text(snapshot)
    hint = snapshot.action_hints[0] if snapshot.action_hints else "按个股纪律执行。"
    return (
        f"大盘环境：{snapshot.regime}｜置信度：{snapshot.confidence}｜数据：{snapshot.data_status}"
        f"｜VIX {vix}｜10Y {ten_year}｜{trend_hint}｜市场宽度 {breadth}｜{credit}｜{sentiment}｜纪律提示：{hint}"
    )


def macro_regime_status_html(snapshot: MacroRegimeSnapshot) -> str:
    tone = _regime_tone(snapshot.regime)
    return (
        f'<div class="macro-regime-status {escape(tone)}">'
        f"<strong>{escape(macro_regime_status_text(snapshot))}</strong>"
        "</div>"
    )


def macro_regime_detail_html(snapshot: MacroRegimeSnapshot) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(item.label)}</td>"
        f"<td>{escape(_indicator_value_text(item, empty='缺', suffix='%' if item.indicator in {HY_OAS, TEN_YEAR_YIELD, YIELD_CURVE_10Y2Y} else ''))}</td>"
        f"<td>{escape(_change_text(item))}</td>"
        f"<td>{escape(item.source or 'cache/manual')}</td>"
        f"<td>{escape(_indicator_cache_status_text(item))}</td>"
        "</tr>"
        for item in snapshot.indicators
    )
    reasons = "".join(f"<li>{escape(reason)}</li>" for reason in snapshot.reasons) or "<li>暂无宏观判断原因。</li>"
    hints = "".join(f"<li>{escape(hint)}</li>" for hint in snapshot.action_hints) or "<li>按个股纪律执行。</li>"
    return (
        '<section class="macro-regime-detail">'
        f"<div><strong>大盘环境：{escape(snapshot.regime)}</strong><span>置信度：{escape(snapshot.confidence)}，只读提示，不改变买卖门禁。</span></div>"
        '<table><thead><tr><th>指标</th><th>当前值</th><th>近期变化</th><th>来源</th><th>状态</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
        f'<div class="macro-regime-detail-grid"><div><b>判断原因</b><ul>{reasons}</ul></div><div><b>纪律提示</b><ul>{hints}</ul></div></div>'
        "</section>"
    )


def macro_regime_trade_hint_text(snapshot: MacroRegimeSnapshot, *, context: str = "buy") -> str:
    prefix = "买入提示" if context == "buy" else "卖出提示"
    if snapshot.regime == REGIME_RISK_OFF:
        hint = "不追涨，A类等回踩；这只是提示，不改变允许新增仓位。"
    elif snapshot.regime == REGIME_STRESS:
        hint = "C类暂停新增，优先复核仓位和现金；这只是提示，不改变门禁。"
    elif snapshot.regime == REGIME_PANIC:
        hint = "只做计划内核心仓，避免情绪化交易；这只是提示，不改变门禁。"
    elif snapshot.regime == REGIME_DATA_GAP:
        hint = "宏观数据不足，先补齐再复核；这只是提示，不改变门禁。"
    else:
        hint = "按个股 Radar、买入计划和纪律门禁执行。"
    return f"大盘环境：{snapshot.regime}｜{prefix}：{hint}"


def _load_vix_snapshot(path: Path, *, now: datetime | None = None) -> MacroIndicatorSnapshot | None:
    for symbol in VIX_MARKET_SYMBOLS:
        context = build_market_context(
            symbol,
            path=path,
            now=now,
            quote_max_age_hours=24,
            history_max_age_hours=96,
        )
        value = _number(context.get("currentPrice"))
        if not _valid_vix_value(value):
            continue
        history = CacheReadModel(
            path,
            now=now,
            quote_max_age_hours=24,
            history_max_age_hours=96,
        ).get_price_history(symbol)
        changes = _history_changes(history, value)
        percentiles = _history_percentiles(history, value)
        return MacroIndicatorSnapshot(
            indicator=VIX,
            value=value,
            change_1d=changes.get("change_1d"),
            change_5d=changes.get("change_5d"),
            change_20d=changes.get("change_20d"),
            percentile_1y=percentiles.get("percentile_1y"),
            percentile_5y=percentiles.get("percentile_5y"),
            source=f"{symbol} local market cache",
            updated_at=str(context.get("fetchedAt") or "") or None,
            is_stale=bool(context.get("isStale")),
        )
    return None


def _fetch_vix_snapshot(
    path: Path,
    *,
    provider: Any | None,
    fred_fetcher: Any | None,
    store: MacroRegimeStore,
    now: datetime,
) -> MacroIndicatorSnapshot:
    errors: list[str] = []
    market_provider = provider
    if market_provider is None:
        try:
            from data.providers import get_market_data_provider

            market_provider = get_market_data_provider(full_fundamentals=False)
        except Exception as exc:
            errors.append(f"行情源初始化失败：{_short_error(exc)}")

    if market_provider is not None:
        for symbol in VIX_MARKET_SYMBOLS:
            try:
                quote = market_provider.get_quote(symbol, force_refresh=True)
                value = _number(_value_from_mapping(quote, "current_price", "price", "value"))
                if not _valid_vix_value(value):
                    errors.append(f"{symbol} 无有效报价")
                    continue
                history = market_provider.get_price_history(symbol, force_refresh=True)
                changes = _history_changes(history, value)
                percentiles = _history_percentiles(history, value)
                fetched_at = now.isoformat()
                return MacroIndicatorSnapshot(
                    indicator=VIX,
                    value=value,
                    change_1d=changes.get("change_1d"),
                    change_5d=changes.get("change_5d"),
                    change_20d=changes.get("change_20d"),
                    percentile_1y=percentiles.get("percentile_1y"),
                    percentile_5y=percentiles.get("percentile_5y"),
                    source=f"{symbol} 行情源",
                    updated_at=fetched_at,
                    fetched_at=fetched_at,
                    observation_date=_quote_observation_date(quote),
                    is_stale=False,
                )
            except Exception as exc:
                errors.append(f"{symbol}: {_short_error(exc)}")

    cached = store.load_indicator(VIX, now=now)
    if cached is not None and not cached.is_stale and _refresh_snapshot_value_usable(cached):
        return cached

    try:
        return _fetch_fred_snapshot_with_circuit(VIX, FRED_VIX_SERIES, store=store, fred_fetcher=fred_fetcher, now=now)
    except Exception as exc:
        errors.append(f"FRED {FRED_VIX_SERIES}: {_short_error(exc)}")
    raise RuntimeError("; ".join(errors) or "VIX 刷新失败")


class _TreasurySnapshotLoader:
    def __init__(self, *, provider: Any | None, now: datetime) -> None:
        self.provider = provider
        self.now = now
        self._lock = Lock()
        self._loaded = False
        self._snapshots: dict[str, MacroIndicatorSnapshot] = {}
        self._error: Exception | None = None

    def get(self, indicator: str) -> MacroIndicatorSnapshot:
        with self._lock:
            if not self._loaded:
                try:
                    self._snapshots = _fetch_fmp_treasury_snapshots(provider=self.provider, now=self.now)
                except Exception as exc:
                    self._error = exc
                self._loaded = True
        snapshot = self._snapshots.get(indicator)
        if snapshot is not None:
            return snapshot
        if self._error is not None:
            raise self._error
        raise RuntimeError("FMP Treasury has no usable rate data")


def _fetch_treasury_or_fred_snapshot(
    indicator: str,
    series_id: str,
    *,
    treasury_loader: _TreasurySnapshotLoader,
    store: MacroRegimeStore,
    fred_fetcher: Any | None,
    now: datetime,
) -> MacroIndicatorSnapshot:
    try:
        return treasury_loader.get(indicator)
    except Exception as treasury_error:
        try:
            return _fetch_fred_snapshot_with_circuit(
                indicator,
                series_id,
                store=store,
                fred_fetcher=fred_fetcher,
                now=now,
            )
        except Exception as fred_error:
            raise RuntimeError(f"FMP Treasury: {_short_error(treasury_error)}; FRED {series_id}: {_short_error(fred_error)}") from fred_error


def _fetch_fmp_treasury_snapshots(*, provider: Any | None, now: datetime) -> dict[str, MacroIndicatorSnapshot]:
    market_provider = provider
    if market_provider is None:
        from data.providers import get_market_data_provider

        market_provider = get_market_data_provider(full_fundamentals=False)
    if not hasattr(market_provider, "_get_json"):
        raise RuntimeError("market provider does not support FMP Treasury")
    payload = market_provider._get_json(  # noqa: SLF001 - existing low-level FMP endpoint.
        "treasury-rates",
        {
            "from": (now - timedelta(days=45)).date().isoformat(),
            "to": now.date().isoformat(),
        },
        timeout_seconds=3,
        retries=0,
        force_refresh=True,
    )
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        rows = [rows]
    parsed_rows = [row for row in rows if isinstance(row, dict)]
    parsed_rows.sort(key=lambda row: str(row.get("date") or row.get("observation_date") or ""))
    ten_year_rows: list[tuple[str, float]] = []
    curve_rows: list[tuple[str, float]] = []
    for row in parsed_rows:
        date = str(row.get("date") or row.get("observation_date") or "")[:10]
        ten_year = _number(_value_from_mapping(row, "year10", "year_10", "tenYear", "ten_year", "10y", "10Y"))
        two_year = _number(_value_from_mapping(row, "year2", "year_2", "twoYear", "two_year", "2y", "2Y"))
        if date and ten_year is not None:
            ten_year_rows.append((date, ten_year))
        if date and ten_year is not None and two_year is not None:
            curve_rows.append((date, round(ten_year - two_year, 3)))
    snapshots: dict[str, MacroIndicatorSnapshot] = {}
    if ten_year_rows:
        snapshots[TEN_YEAR_YIELD] = _series_snapshot_from_rows(
            TEN_YEAR_YIELD,
            ten_year_rows,
            source="FMP Treasury",
            now=now,
            raw_payload=payload,
        )
    if curve_rows:
        snapshots[YIELD_CURVE_10Y2Y] = _series_snapshot_from_rows(
            YIELD_CURVE_10Y2Y,
            curve_rows,
            source="FMP Treasury calculated",
            now=now,
            raw_payload=payload,
        )
    if not snapshots:
        raise RuntimeError("FMP Treasury has no usable 10Y/2Y values")
    return snapshots


def _series_snapshot_from_rows(
    indicator: str,
    rows: list[tuple[str, float]],
    *,
    source: str,
    now: datetime,
    raw_payload: Any | None = None,
) -> MacroIndicatorSnapshot:
    latest_date, latest_value = rows[-1]
    values = [value for _, value in rows]
    fetched_at = now.isoformat()
    return MacroIndicatorSnapshot(
        indicator=indicator,
        value=latest_value,
        change_1d=_series_change(values, 1),
        change_5d=_series_change(values, 5),
        change_20d=_series_change(values, 20),
        percentile_1y=_series_percentile(values[-252:], latest_value),
        percentile_5y=_series_percentile(values[-1260:], latest_value),
        source=source,
        updated_at=fetched_at,
        fetched_at=fetched_at,
        observation_date=latest_date,
        is_stale=_observation_date_stale(latest_date, now=now, max_days=7),
        raw_payload=_compact_raw_payload(raw_payload),
    )


def _fetch_fred_snapshot_with_circuit(
    indicator: str,
    series_id: str,
    *,
    store: MacroRegimeStore,
    fred_fetcher: Any | None,
    now: datetime,
) -> MacroIndicatorSnapshot:
    open_circuit, last_error = store.is_provider_circuit_open(FRED_PROVIDER, now=now)
    if open_circuit:
        raise RuntimeError(f"FRED circuit open, using cache; last error: {last_error or 'unknown'}")
    try:
        snapshot = _fetch_fred_snapshot(indicator, series_id, fred_fetcher=fred_fetcher, now=now)
    except Exception as exc:
        store.record_provider_failure(FRED_PROVIDER, _short_error(exc), now=now)
        raise
    store.record_provider_success(FRED_PROVIDER)
    return snapshot


def _fetch_cached_or_fred_snapshot(
    indicator: str,
    series_id: str,
    *,
    store: MacroRegimeStore,
    fred_fetcher: Any | None,
    now: datetime,
) -> MacroIndicatorSnapshot:
    cached = store.load_indicator(indicator, now=now)
    if cached is not None and not cached.is_stale and _refresh_snapshot_value_usable(cached):
        return cached
    return _fetch_fred_snapshot_with_circuit(indicator, series_id, store=store, fred_fetcher=fred_fetcher, now=now)


def _fetch_optional_cached_indicator(
    indicator: str,
    *,
    store: MacroRegimeStore,
    now: datetime,
) -> MacroIndicatorSnapshot:
    cached = store.load_indicator(indicator, now=now)
    if cached is not None and _refresh_snapshot_value_usable(cached):
        return cached
    raise RuntimeError(f"{INDICATOR_LABELS.get(indicator, indicator)} front refresh skipped; no usable cache")


def _fetch_cached_or_fear_greed_snapshot(
    *,
    store: MacroRegimeStore,
    fear_greed_fetcher: Any | None,
    now: datetime,
) -> MacroIndicatorSnapshot:
    cached = store.load_indicator(FEAR_GREED, now=now)
    if cached is not None and not cached.is_stale and _refresh_snapshot_value_usable(cached):
        return cached
    open_circuit, last_error = store.is_provider_circuit_open(FEAR_GREED_PROVIDER, now=now)
    if open_circuit:
        raise RuntimeError(f"CNN Fear & Greed circuit open, using proxy/cache; last error: {last_error or 'unknown'}")
    try:
        snapshot = _fetch_fear_greed_snapshot(fear_greed_fetcher=fear_greed_fetcher, now=now)
    except Exception as exc:
        store.record_provider_failure(
            FEAR_GREED_PROVIDER,
            _short_error(exc),
            now=now,
            failure_threshold=1,
            open_for_hours=FRED_CIRCUIT_OPEN_HOURS,
        )
        raise
    store.record_provider_success(FEAR_GREED_PROVIDER)
    return snapshot


def _fetch_fred_snapshot(
    indicator: str,
    series_id: str,
    *,
    fred_fetcher: Any | None,
    now: datetime,
) -> MacroIndicatorSnapshot:
    payload = _fetch_fred_payload(series_id, fred_fetcher=fred_fetcher)
    rows = _series_rows_from_payload(payload, value_key=series_id)
    if not rows:
        raise RuntimeError(f"FRED {series_id} 没有可用观测值")
    latest_date, latest_value = rows[-1]
    if indicator == VIX and not _valid_vix_value(latest_value):
        raise RuntimeError(f"FRED {series_id} returned invalid VIX value")
    values = [value for _, value in rows]
    fetched_at = now.isoformat()
    raw_payload = _compact_raw_payload(payload)
    return MacroIndicatorSnapshot(
        indicator=indicator,
        value=latest_value,
        change_1d=_series_change(values, 1),
        change_5d=_series_change(values, 5),
        change_20d=_series_change(values, 20),
        percentile_1y=_series_percentile(values[-252:], latest_value),
        percentile_5y=_series_percentile(values[-1260:], latest_value),
        source=f"FRED {series_id}",
        updated_at=fetched_at,
        fetched_at=fetched_at,
        observation_date=latest_date,
        is_stale=_observation_date_stale(latest_date, now=now, max_days=7),
        raw_payload=raw_payload,
    )


def _fetch_fred_payload(series_id: str, *, fred_fetcher: Any | None) -> Any:
    if fred_fetcher:
        return fred_fetcher(series_id)
    errors: list[str] = []
    encoded = quote(series_id)
    for url_template, timeout_seconds in (
        (FRED_CSV_URL, FRED_PRIMARY_TIMEOUT_SECONDS),
        (FRED_DOWNLOAD_CSV_URL, FRED_FALLBACK_TIMEOUT_SECONDS),
    ):
        url = url_template.format(series_id=encoded)
        try:
            return _read_url_text(url, timeout_seconds=timeout_seconds)
        except Exception as exc:
            errors.append(_short_error(exc))
    raise RuntimeError(f"FRED {series_id} CSV 拉取失败：" + "; ".join(errors))


def _fetch_fear_greed_snapshot(*, fear_greed_fetcher: Any | None, now: datetime) -> MacroIndicatorSnapshot:
    payload = (
        fear_greed_fetcher(CNN_FEAR_GREED_URL)
        if fear_greed_fetcher
        else _read_json(CNN_FEAR_GREED_URL, timeout_seconds=FEAR_GREED_TIMEOUT_SECONDS)
    )
    value = _extract_fear_greed_value(payload)
    if value is None:
        raise RuntimeError("CNN 恐惧与贪婪指数没有可用数值")
    observation_date = _extract_fear_greed_observation_date(payload, now=now)
    fetched_at = now.isoformat()
    return MacroIndicatorSnapshot(
        indicator=FEAR_GREED,
        value=value,
        source="CNN Fear & Greed JSON",
        updated_at=fetched_at,
        fetched_at=fetched_at,
        observation_date=observation_date,
        is_stale=_observation_date_stale(observation_date, now=now, max_days=2),
        raw_payload=_compact_raw_payload(payload),
    )


def _fetch_market_trend_snapshot(path: Path, *, provider: Any | None, now: datetime) -> MacroIndicatorSnapshot:
    errors: list[str] = []
    market_provider = provider
    if market_provider is None:
        try:
            from data.providers import get_market_data_provider

            market_provider = get_market_data_provider(full_fundamentals=False)
        except Exception as exc:
            errors.append(f"SPY/QQQ 行情源初始化失败：{_short_error(exc)}")
    if market_provider is not None:
        for symbol in ("SPY", "QQQ"):
            try:
                market_provider.get_price_history(symbol, force_refresh=True)
            except Exception as exc:
                errors.append(f"{symbol} 刷新失败：{_short_error(exc)}")
    spy = _trend_state_for_symbol("SPY", path)
    qqq = _trend_state_for_symbol("QQQ", path)
    if spy is None and qqq is None:
        raise RuntimeError("缺少 SPY/QQQ K 线缓存，无法计算大盘趋势")
    states = [item for item in (spy, qqq) if item is not None]
    risk_value = max(float(item["risk_value"]) for item in states)
    reasons: list[str] = []
    hints: list[str] = []
    for label, state in (("SPY", spy), ("QQQ", qqq)):
        if state is None:
            reasons.append(f"{label} 缺 K 线。")
            continue
        relation = []
        relation.append("高于50日" if state["above_50"] else "跌破50日")
        relation.append("高于200日" if state["above_200"] else "跌破200日")
        reasons.append(f"{label} 当前 {state['current']:.2f}，{','.join(relation)}。")
    if qqq and not qqq["above_50"]:
        hints.append("QQQ 跌破 50 日均线，AI/软件不追涨。")
    if spy and not spy["above_200"]:
        hints.append("SPY 跌破 200 日均线，只允许 A 类计划内买入提示。")
    return MacroIndicatorSnapshot(
        indicator=MARKET_TREND,
        value=risk_value,
        source="本地 SPY/QQQ K线缓存",
        updated_at=now.isoformat(),
        fetched_at=now.isoformat(),
        observation_date=_latest_state_date(states),
        is_stale=any(bool(item.get("is_stale")) for item in states),
        reasons=[*reasons, *errors],
        action_hints=hints,
        raw_payload=_compact_raw_payload({"SPY": spy, "QQQ": qqq}),
    )


def _fetch_market_breadth_snapshot(path: Path, *, now: datetime) -> MacroIndicatorSnapshot:
    tickers = [str(item).strip().upper() for item in load_watchlist() if str(item).strip()]
    states = [_trend_state_for_symbol(ticker, path) for ticker in tickers]
    available = [state for state in states if state is not None]
    if not available:
        raise RuntimeError("观察池缺少 K 线缓存，无法计算市场宽度")
    above_50 = sum(1 for state in available if state["above_50"])
    above_200 = sum(1 for state in available if state["above_200"])
    pct_above_50 = round(above_50 / len(available) * 100, 1)
    pct_above_200 = round(above_200 / len(available) * 100, 1)
    reasons = [
        f"观察池 {len(available)}/{len(tickers)} 只有可用 K 线。",
        f"高于50日均线比例 {pct_above_50:.1f}%。",
        f"高于200日均线比例 {pct_above_200:.1f}%。",
    ]
    hints = ["观察池宽度恶化，C 类暂停新增提示。"] if pct_above_50 < 40 else []
    return MacroIndicatorSnapshot(
        indicator=MARKET_BREADTH,
        value=pct_above_50,
        change_20d=round(pct_above_50 - pct_above_200, 1),
        source="本地观察池 K线缓存",
        updated_at=now.isoformat(),
        fetched_at=now.isoformat(),
        observation_date=_latest_state_date(available),
        is_stale=any(bool(item.get("is_stale")) for item in available),
        reasons=reasons,
        action_hints=hints,
        raw_payload=_compact_raw_payload({"tickerCount": len(tickers), "availableCount": len(available), "pctAbove50": pct_above_50, "pctAbove200": pct_above_200}),
    )


def _fetch_hyg_credit_proxy_snapshot(
    path: Path,
    *,
    provider: Any | None,
    now: datetime,
) -> MacroIndicatorSnapshot:
    errors: list[str] = []
    market_provider = provider
    if market_provider is None:
        try:
            from data.providers import get_market_data_provider

            market_provider = get_market_data_provider(full_fundamentals=False)
        except Exception as exc:
            errors.append(f"HYG 行情源初始化失败：{_short_error(exc)}")
    if market_provider is not None:
        for symbol in ("HYG", "LQD", "IEF"):
            try:
                market_provider.get_price_history(symbol, force_refresh=True)
            except Exception as exc:
                errors.append(f"{symbol} 刷新失败：{_short_error(exc)}")

    hyg = _trend_state_for_symbol("HYG", path)
    if hyg is None:
        raise RuntimeError("缺少 HYG K 线缓存，无法生成信用风险代理")
    hyg_change_20d = _history_return_pct("HYG", path, days=20)
    hyg_vs_lqd_20d = _relative_return_pct("HYG", "LQD", path, days=20)
    hyg_vs_ief_20d = _relative_return_pct("HYG", "IEF", path, days=20)
    risk_value = float(hyg["risk_value"])
    reasons: list[str] = []
    hints: list[str] = []
    if hyg.get("above_200") is False:
        risk_value = max(risk_value, 78)
        reasons.append("HYG 跌破 200 日均线，信用风险代理偏紧。")
        hints.append("信用代理转弱，非核心新增要降速。")
    elif hyg.get("above_50") is False:
        risk_value = max(risk_value, 62)
        reasons.append("HYG 跌破 50 日均线，信用风险代理转弱。")
        hints.append("不追涨，等待信用代理修复。")
    else:
        reasons.append("HYG 仍在 50/200 日均线上方，信用代理稳定。")
    if hyg_change_20d is not None and hyg_change_20d <= -5:
        risk_value = max(risk_value, 66)
        reasons.append(f"HYG 20 日跌幅 {hyg_change_20d:.1f}%，信用风险升温。")
    if hyg_vs_lqd_20d is not None and hyg_vs_lqd_20d <= -3:
        risk_value = max(risk_value, 64)
        reasons.append(f"HYG 相对 LQD 20 日走弱 {hyg_vs_lqd_20d:.1f}%。")
    if hyg_vs_ief_20d is not None and hyg_vs_ief_20d <= -3:
        risk_value = max(risk_value, 64)
        reasons.append(f"HYG 相对 IEF 20 日走弱 {hyg_vs_ief_20d:.1f}%。")
    if risk_value < 60:
        hints.append("信用 proxy 稳定，仍按个股纪律执行。")
    payload = {
        "proxy": "HYG",
        "hyg": hyg,
        "hyg_change_20d": hyg_change_20d,
        "hyg_vs_lqd_20d": hyg_vs_lqd_20d,
        "hyg_vs_ief_20d": hyg_vs_ief_20d,
        "errors": errors,
    }
    return MacroIndicatorSnapshot(
        indicator=HYG_CREDIT_PROXY,
        value=round(risk_value, 1),
        change_20d=hyg_change_20d,
        source="HYG 信用风险代理",
        updated_at=now.isoformat(),
        fetched_at=now.isoformat(),
        observation_date=str(hyg.get("latest_date") or "") or None,
        is_stale=bool(hyg.get("is_stale")),
        reasons=[*reasons, *errors],
        action_hints=_dedupe(hints),
        raw_payload=_compact_raw_payload(payload),
    )


def _build_sentiment_proxy_snapshot(
    indicators: dict[str, dict[str, Any]],
    *,
    now: datetime,
) -> MacroIndicatorSnapshot:
    vix = _number((indicators.get(VIX) or {}).get("value"))
    trend = _number((indicators.get(MARKET_TREND) or {}).get("value"))
    breadth = _number((indicators.get(MARKET_BREADTH) or {}).get("value"))
    credit = _number((indicators.get(HYG_CREDIT_PROXY) or {}).get("value"))
    score = 50.0
    reasons: list[str] = []
    if vix is not None:
        if vix > 30:
            score -= 25
            reasons.append("VIX 高于 30，情绪代理偏恐慌。")
        elif vix > 25:
            score -= 18
            reasons.append("VIX 高于 25，情绪代理偏恐惧。")
        elif vix > 20:
            score -= 10
            reasons.append("VIX 高于 20，情绪代理偏谨慎。")
        elif vix < 15:
            score += 8
            reasons.append("VIX 低位，情绪代理偏稳定。")
    if trend is not None:
        if trend >= 70:
            score -= 15
            reasons.append("大盘趋势压力较高。")
        elif trend >= 58:
            score -= 8
            reasons.append("大盘趋势转弱。")
    if breadth is not None:
        if breadth < 25:
            score -= 18
            reasons.append("市场宽度显著恶化。")
        elif breadth < 40:
            score -= 10
            reasons.append("市场宽度偏弱。")
        elif breadth >= 60:
            score += 8
            reasons.append("市场宽度较健康。")
    if credit is not None:
        if credit >= 75:
            score -= 15
            reasons.append("信用 proxy 压力较高。")
        elif credit >= 60:
            score -= 10
            reasons.append("信用 proxy 转弱。")
        elif credit < 40:
            score += 5
            reasons.append("信用 proxy 稳定。")
    usable_inputs = [item for item in (vix, trend, breadth, credit) if item is not None]
    if len(usable_inputs) < 2:
        raise RuntimeError("缺少 VIX/趋势/宽度/信用 proxy，无法生成内部情绪代理")
    score = round(max(0.0, min(100.0, score)), 1)
    label = _sentiment_proxy_label(score)
    return MacroIndicatorSnapshot(
        indicator=SENTIMENT_PROXY,
        value=score,
        source="内部情绪代理",
        updated_at=now.isoformat(),
        fetched_at=now.isoformat(),
        observation_date=now.date().isoformat(),
        reasons=[f"情绪代理：{label}。", *reasons],
        action_hints=["情绪代理只做提示，不改变买卖门禁。"],
        raw_payload=_compact_raw_payload(
            {
                "vix": vix,
                "market_trend": trend,
                "market_breadth": breadth,
                "credit_proxy": credit,
                "label": label,
            }
        ),
    )


def _trend_state_for_symbol(symbol: str, path: Path) -> dict[str, Any] | None:
    history = build_market_context(symbol, path=path, quote_max_age_hours=36, history_max_age_hours=120)
    frame = CacheReadModel(path, quote_max_age_hours=36, history_max_age_hours=120).get_price_history(symbol)
    if frame is None or frame.empty:
        from data.market_context import build_market_history

        frame = build_market_history(symbol, path=path, quote_max_age_hours=36, history_max_age_hours=120)
    closes = _numeric_closes(frame)
    if closes.empty or len(closes) < 50:
        return None
    current = _number(history.get("currentPrice")) or _number(closes.iloc[-1])
    if current is None:
        return None
    ma50 = float(closes.tail(50).mean())
    ma200 = float(closes.tail(200).mean()) if len(closes) >= 200 else None
    above_50 = current >= ma50
    above_200 = True if ma200 is None else current >= ma200
    risk_value = 25
    if not above_50:
        risk_value = 58
    if ma200 is not None and not above_200:
        risk_value = 78
    return {
        "symbol": symbol.upper(),
        "current": round(float(current), 4),
        "ma50": round(ma50, 4),
        "ma200": round(ma200, 4) if ma200 is not None else None,
        "above_50": above_50,
        "above_200": above_200,
        "risk_value": risk_value,
        "latest_date": _latest_history_date(frame),
        "is_stale": bool(history.get("isStale")),
    }


def _history_return_pct(symbol: str, path: Path, *, days: int) -> float | None:
    frame = CacheReadModel(path, quote_max_age_hours=36, history_max_age_hours=120).get_price_history(symbol)
    if frame is None or frame.empty:
        return None
    closes = _numeric_closes(frame)
    if closes.empty or len(closes) <= days:
        return None
    current = _number(closes.iloc[-1])
    base = _number(closes.iloc[-days - 1])
    if current is None or base is None or base == 0:
        return None
    return round((current - base) / base * 100, 1)


def _relative_return_pct(symbol: str, benchmark: str, path: Path, *, days: int) -> float | None:
    left = _history_return_pct(symbol, path, days=days)
    right = _history_return_pct(benchmark, path, days=days)
    if left is None or right is None:
        return None
    return round(left - right, 1)


def _latest_history_date(frame: pd.DataFrame) -> str | None:
    if frame is None or frame.empty or "date" not in frame.columns:
        return None
    try:
        return pd.to_datetime(frame["date"]).max().date().isoformat()
    except Exception:
        return None


def _latest_state_date(states: list[dict[str, Any]]) -> str | None:
    dates = [str(item.get("latest_date") or "") for item in states if item.get("latest_date")]
    return max(dates) if dates else None


def _with_indicator_regime(snapshot: MacroIndicatorSnapshot) -> MacroIndicatorSnapshot:
    regime, score, reasons, hints = _indicator_regime(snapshot)
    return MacroIndicatorSnapshot(
        indicator=_normalize_indicator(snapshot.indicator),
        value=snapshot.value,
        change_1d=snapshot.change_1d,
        change_5d=snapshot.change_5d,
        change_20d=snapshot.change_20d,
        percentile_1y=snapshot.percentile_1y,
        percentile_5y=snapshot.percentile_5y,
        source=snapshot.source,
        updated_at=snapshot.updated_at,
        observation_date=snapshot.observation_date,
        fetched_at=snapshot.fetched_at,
        is_stale=snapshot.is_stale,
        error=snapshot.error,
        raw_payload=snapshot.raw_payload,
        regime=regime,
        risk_score=score,
        reasons=_dedupe([*snapshot.reasons, *reasons]),
        action_hints=_dedupe([*snapshot.action_hints, *hints]),
    )


def _indicator_regime(snapshot: MacroIndicatorSnapshot) -> tuple[str, float, list[str], list[str]]:
    if snapshot.value is None:
        return REGIME_DATA_GAP, 60, [f"{snapshot.label}缺失。"], ["补齐数据后再判断。"]
    if snapshot.is_stale:
        return REGIME_DATA_GAP, 55, [f"{snapshot.label}数据过期。"], ["过期数据不能当成风险偏好。"]
    value = float(snapshot.value)
    if snapshot.indicator == VIX and not _valid_vix_value(value):
        return REGIME_DATA_GAP, 60, ["VIX 报价无效。"], ["补齐有效 VIX 后再判断大盘波动。"]
    if snapshot.indicator == VIX:
        if value > 30:
            return REGIME_PANIC, 90, ["VIX 高于 30。"], ["只做计划内核心仓。"]
        if value > 25:
            return REGIME_STRESS, 78, ["VIX 高于 25。"], ["降低主动新增节奏。"]
        if value > 20:
            return REGIME_RISK_OFF, 65, ["VIX 高于 20。"], ["不追涨。"]
        if value < 15:
            return REGIME_RISK_ON, 25, ["VIX 低位。"], ["仍按个股纪律执行。"]
        return REGIME_NEUTRAL, 40, ["VIX 中性。"], ["按计划执行。"]
    if snapshot.indicator == FEAR_GREED:
        if value <= 20:
            return REGIME_PANIC, 85, ["恐惧与贪婪指数极低。"], ["避免恐慌杀跌。"]
        if value <= 35:
            return REGIME_RISK_OFF, 62, ["市场情绪偏恐惧。"], ["等待确认。"]
        if value >= 80:
            return REGIME_RISK_ON, 35, ["市场情绪偏贪婪。"], ["不因情绪追涨。"]
        return REGIME_NEUTRAL, 40, ["市场情绪正常。"], ["按计划执行。"]
    if snapshot.indicator == HY_OAS:
        if value >= 7:
            return REGIME_PANIC, 90, ["信用利差进入恐慌区。"], ["优先控制风险。"]
        if value >= 5:
            return REGIME_STRESS, 78, ["信用利差偏高。"], ["减少非核心新增。"]
        if value >= 4 or _credit_spread_widening(snapshot):
            return REGIME_RISK_OFF, 64, ["信用利差走阔或偏高。"], ["不追涨。"]
        return REGIME_NEUTRAL, 35, ["信用利差未显示压力。"], ["按计划执行。"]
    if snapshot.indicator == HYG_CREDIT_PROXY:
        if value >= 75:
            return REGIME_STRESS, 78, ["HYG 信用代理显示压力较高。"], ["减少非核心新增。"]
        if value >= 60:
            return REGIME_RISK_OFF, 64, ["HYG 信用代理转弱。"], ["不追涨。"]
        return REGIME_NEUTRAL, 35, ["HYG 信用代理稳定。"], ["按计划执行。"]
    if snapshot.indicator == SENTIMENT_PROXY:
        if value <= 20:
            return REGIME_PANIC, 82, ["内部情绪代理偏恐慌。"], ["避免情绪化杀跌。"]
        if value <= 35:
            return REGIME_RISK_OFF, 62, ["内部情绪代理偏恐惧。"], ["等待确认。"]
        if value >= 80:
            return REGIME_RISK_ON, 35, ["内部情绪代理偏贪婪。"], ["不因情绪追涨。"]
        return REGIME_NEUTRAL, 40, ["内部情绪代理中性。"], ["按计划执行。"]
    if snapshot.indicator == TEN_YEAR_YIELD:
        if _rate_rising_fast(snapshot):
            return REGIME_RISK_OFF, 62, ["10年美债快速上行。"], ["成长股估值承压，AI/软件不追涨。"]
        if value >= 5:
            return REGIME_RISK_OFF, 64, ["10年美债收益率偏高。"], ["压低成长股估值预期。"]
        return REGIME_NEUTRAL, 35, ["10年美债收益率未显示极端压力。"], ["按个股纪律执行。"]
    if snapshot.indicator == YIELD_CURVE_10Y2Y:
        if value < -0.5:
            return REGIME_RISK_OFF, 60, ["10Y-2Y 曲线深度倒挂。"], ["关注衰退和盈利下修风险。"]
        if value < 0:
            return REGIME_NEUTRAL, 45, ["10Y-2Y 曲线倒挂。"], ["复核盈利周期。"]
        return REGIME_NEUTRAL, 35, ["10Y-2Y 曲线未显示明显压力。"], ["按计划执行。"]
    if snapshot.indicator == MARKET_TREND:
        spy_below_200 = _indicator_flag(snapshot, "spy_below_200")
        qqq_below_50 = _indicator_flag(snapshot, "qqq_below_50")
        if spy_below_200:
            return REGIME_RISK_OFF, 72, ["SPY 跌破 200 日均线。"], ["只允许 A 类计划内买入提示。"]
        if qqq_below_50:
            return REGIME_RISK_OFF, 62, ["QQQ 跌破 50 日均线。"], ["AI/软件不追涨。"]
        return REGIME_NEUTRAL, 30, ["SPY/QQQ 趋势未显示系统性破位。"], ["按个股纪律执行。"]
    if snapshot.indicator == MARKET_BREADTH:
        if value < 25:
            return REGIME_STRESS, 76, ["观察池市场宽度显著恶化。"], ["C类暂停新增，先复核风险。"]
        if value < 40:
            return REGIME_RISK_OFF, 62, ["观察池市场宽度恶化。"], ["C类暂停新增提示。"]
        return REGIME_NEUTRAL, 35, ["观察池市场宽度尚可。"], ["按计划执行。"]
    if snapshot.indicator == DOLLAR_INDEX:
        if snapshot.change_20d is not None and snapshot.change_20d >= 3:
            return REGIME_RISK_OFF, 55, ["美元指数短期走强。"], ["复核美元走强对海外收入和风险资产的压力。"]
        return REGIME_NEUTRAL, 30, ["美元指数未作为核心压力信号。"], ["低优先级参考。"]
    return REGIME_DATA_GAP, 50, [], []


def _macro_risk_score(
    vix: float | None,
    fear: float | None,
    hy: float | None,
    credit_widening: bool,
    stale: bool,
    *,
    ten_year: float | None = None,
    ten_year_rising: bool = False,
    credit_proxy: float | None = None,
    qqq_below_50: bool = False,
    spy_below_200: bool = False,
    breadth_weak: bool = False,
) -> float:
    scores: list[float] = []
    if vix is not None:
        scores.append(max(0, min(100, (vix - 12) * 4.5)))
    if fear is not None:
        scores.append(max(0, min(100, 100 - fear)))
    if hy is not None:
        scores.append(max(0, min(100, (hy - 2.5) * 22)))
    if credit_widening:
        scores.append(65)
    if credit_proxy is not None:
        scores.append(max(0, min(100, credit_proxy)))
    if ten_year is not None:
        scores.append(max(0, min(100, (ten_year - 3.5) * 35)))
    if ten_year_rising:
        scores.append(62)
    if qqq_below_50:
        scores.append(62)
    if spy_below_200:
        scores.append(76)
    if breadth_weak:
        scores.append(64)
    if stale:
        scores.append(55)
    if not scores:
        return 60
    return round(sum(scores) / len(scores), 1)


def _action_hints(regime: str) -> list[str]:
    return {
        REGIME_RISK_ON: ["按个股纪律执行，不因大盘风险偏好追高。"],
        REGIME_NEUTRAL: ["按个股 Radar 和买入计划执行。"],
        REGIME_RISK_OFF: ["不追涨，A类等回踩。"],
        REGIME_STRESS: ["C类暂停新增，优先复核仓位和现金。"],
        REGIME_PANIC: ["只做计划内核心仓，避免情绪化追涨杀跌。"],
        REGIME_DATA_GAP: ["先补齐宏观指标，不把缺数据当成风险偏好。"],
    }.get(regime, ["按个股纪律执行。"])


def _credit_spread_widening(snapshot: MacroIndicatorSnapshot | None) -> bool:
    if snapshot is None or snapshot.value is None or snapshot.is_stale:
        return False
    return any(
        change is not None and change >= threshold
        for change, threshold in (
            (snapshot.change_1d, 0.10),
            (snapshot.change_5d, 0.20),
            (snapshot.change_20d, 0.35),
        )
    )


def _credit_spread_tightening(snapshot: MacroIndicatorSnapshot | None) -> bool:
    if snapshot is None or snapshot.value is None or snapshot.is_stale:
        return False
    if snapshot.value >= 4:
        return False
    changes = [snapshot.change_5d, snapshot.change_20d]
    return any(change is not None and change <= -0.10 for change in changes)


def _credit_proxy_pressure(snapshot: MacroIndicatorSnapshot | None) -> bool:
    value = _usable_value(snapshot)
    return value is not None and value >= 60


def _credit_proxy_stress(snapshot: MacroIndicatorSnapshot | None) -> bool:
    value = _usable_value(snapshot)
    return value is not None and value >= 75


def _credit_proxy_reason_text(snapshot: MacroIndicatorSnapshot | None) -> str:
    value = _usable_value(snapshot)
    if value is None:
        return "信用利差缺失，HYG 信用 proxy 暂不可用。"
    if value >= 75:
        return f"信用利差缺失，HYG 信用 proxy 压力较高（{value:.0f}）。"
    if value >= 60:
        return f"信用利差缺失，HYG 信用 proxy 转弱（{value:.0f}）。"
    return f"信用利差缺失，HYG 信用 proxy 稳定（{value:.0f}）。"


def _sentiment_proxy_reason_text(snapshot: MacroIndicatorSnapshot | None) -> str:
    value = _usable_value(snapshot)
    if value is None:
        return "CNN 恐惧与贪婪缺失，内部情绪代理暂不可用。"
    return f"CNN 恐惧与贪婪缺失，内部情绪代理{_sentiment_proxy_label(value)}（{value:.0f}）。"


def _sentiment_proxy_label(value: float) -> str:
    if value <= 25:
        return "偏恐慌"
    if value <= 40:
        return "偏恐惧"
    if value >= 75:
        return "偏贪婪"
    return "中性"


def _rate_rising_fast(snapshot: MacroIndicatorSnapshot | None) -> bool:
    if snapshot is None or snapshot.value is None or snapshot.is_stale:
        return False
    return any(
        change is not None and change >= threshold
        for change, threshold in (
            (snapshot.change_5d, 0.15),
            (snapshot.change_20d, 0.35),
        )
    )


def _indicator_flag(snapshot: MacroIndicatorSnapshot | None, key: str) -> bool:
    if snapshot is None or not snapshot.raw_payload:
        return False
    payload = _json_dict(snapshot.raw_payload)
    if snapshot.indicator == MARKET_TREND:
        spy = payload.get("SPY") if isinstance(payload.get("SPY"), dict) else {}
        qqq = payload.get("QQQ") if isinstance(payload.get("QQQ"), dict) else {}
        if key == "spy_below_200":
            return spy.get("above_200") is False
        if key == "spy_below_50":
            return spy.get("above_50") is False
        if key == "qqq_below_200":
            return qqq.get("above_200") is False
        if key == "qqq_below_50":
            return qqq.get("above_50") is False
    if isinstance(payload.get(key), bool):
        return bool(payload.get(key))
    return False


def _usable_value(snapshot: MacroIndicatorSnapshot | None) -> float | None:
    if snapshot is None:
        return None
    if snapshot.is_stale:
        return None
    if snapshot.indicator == VIX and not _valid_vix_value(snapshot.value):
        return None
    return _number(snapshot.value)


def _history_changes(history: pd.DataFrame, current_value: float) -> dict[str, float | None]:
    closes = _numeric_closes(history)
    return {
        "change_1d": _point_change(closes, current_value, 1),
        "change_5d": _point_change(closes, current_value, 5),
        "change_20d": _point_change(closes, current_value, 20),
    }


def _history_percentiles(history: pd.DataFrame, current_value: float) -> dict[str, float | None]:
    closes = _numeric_closes(history)
    return {
        "percentile_1y": _percentile(closes.tail(252), current_value),
        "percentile_5y": _percentile(closes.tail(1260), current_value),
    }


def _numeric_closes(history: pd.DataFrame) -> pd.Series:
    if history is None or history.empty or "close" not in history.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(history["close"], errors="coerce").dropna()


def _point_change(closes: pd.Series, current_value: float, days: int) -> float | None:
    if closes.empty or len(closes) <= days:
        return None
    base = _number(closes.iloc[-days - 1])
    return round(current_value - base, 2) if base is not None else None


def _percentile(closes: pd.Series, current_value: float) -> float | None:
    if closes.empty:
        return None
    return round(float((closes <= current_value).sum()) / float(len(closes)) * 100, 1)


def _indicator_value_text(snapshot: MacroIndicatorSnapshot | None, *, empty: str = "—", suffix: str = "") -> str:
    if snapshot is None or snapshot.value is None:
        return empty
    value = float(snapshot.value)
    if snapshot.indicator == VIX and not _valid_vix_value(value):
        return empty
    if snapshot.indicator == FEAR_GREED:
        text = f"{value:.0f}"
    elif snapshot.indicator == MARKET_TREND:
        text = _trend_summary_text(snapshot)
        suffix = ""
    elif snapshot.indicator == MARKET_BREADTH:
        text = f"{value:.1f}% 高于50日"
        suffix = ""
    else:
        text = f"{value:.1f}"
    return f"{text}{suffix}"


def _trend_summary_text(snapshot: MacroIndicatorSnapshot | None) -> str:
    if snapshot is None or snapshot.value is None:
        return "大盘趋势 缺"
    payload = _json_dict(snapshot.raw_payload)
    spy = payload.get("SPY") if isinstance(payload.get("SPY"), dict) else {}
    qqq = payload.get("QQQ") if isinstance(payload.get("QQQ"), dict) else {}
    if spy.get("above_200") is False:
        return "SPY跌破200日"
    if qqq.get("above_50") is False:
        return "QQQ跌破50日"
    if spy or qqq:
        return "趋势正常"
    return f"趋势风险 {float(snapshot.value):.0f}"


def _credit_summary_text(snapshot: MacroRegimeSnapshot) -> str:
    hy = snapshot.indicator(HY_OAS)
    hy_value = _usable_value(hy)
    if hy_value is not None:
        return f"信用利差 {hy_value:.1f}%"
    proxy = snapshot.indicator(HYG_CREDIT_PROXY)
    proxy_value = _usable_value(proxy)
    if proxy_value is None:
        return "信用缺失"
    if proxy_value >= 75:
        return "信用proxy承压"
    if proxy_value >= 60:
        return "信用proxy转弱"
    return "信用proxy稳定"


def _sentiment_summary_text(snapshot: MacroRegimeSnapshot) -> str:
    fear = snapshot.indicator(FEAR_GREED)
    fear_value = _usable_value(fear)
    if fear_value is not None:
        return f"恐惧贪婪 {fear_value:.0f}"
    proxy = snapshot.indicator(SENTIMENT_PROXY)
    proxy_value = _usable_value(proxy)
    if proxy_value is None:
        return "情绪缺失"
    return f"情绪代理{_sentiment_proxy_label(proxy_value)}"


def _indicator_cache_status_text(snapshot: MacroIndicatorSnapshot) -> str:
    if snapshot.value is None:
        return "缺失" + (f"：{snapshot.error}" if snapshot.error else "")
    if snapshot.is_stale:
        return "过期" + (f"：{snapshot.error}" if snapshot.error else "")
    if snapshot.error:
        return f"有效，最近刷新失败：{snapshot.error}"
    return "有效"


def _change_text(snapshot: MacroIndicatorSnapshot) -> str:
    parts = []
    for label, value in (("1日", snapshot.change_1d), ("5日", snapshot.change_5d), ("20日", snapshot.change_20d)):
        if value is not None:
            parts.append(f"{label} {value:+.2f}")
    return " / ".join(parts) if parts else "—"


def _regime_tone(regime: str) -> str:
    return {
        REGIME_RISK_ON: "ok",
        REGIME_NEUTRAL: "neutral",
        REGIME_RISK_OFF: "warning",
        REGIME_STRESS: "stress",
        REGIME_PANIC: "panic",
        REGIME_DATA_GAP: "missing",
    }.get(regime, "neutral")


def _latest_updated_at(items: list[MacroIndicatorSnapshot]) -> str | None:
    parsed = [_parse_datetime(item.updated_at) for item in items if item.updated_at]
    parsed = [item for item in parsed if item is not None]
    if not parsed:
        return None
    return max(parsed).isoformat()


def _is_stale(value: str | None, stale_after_hours: float, *, now: datetime | None = None) -> bool:
    parsed = _parse_datetime(value)
    if parsed is None:
        return True
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) - parsed > timedelta(hours=stale_after_hours)


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_dict(value: object) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _valid_vix_value(value: object) -> bool:
    numeric = _number(value)
    return numeric is not None and numeric > 0


def _normalize_indicator(value: object) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "fear & greed": FEAR_GREED,
        "fear_greed": FEAR_GREED,
        "cnn_fear_greed": FEAR_GREED,
        "vix": VIX,
        "^vix": VIX,
        "avix": VIX,
        "hy_oas": HY_OAS,
        "bamlh0a0hym2": HY_OAS,
        "hy spread": HY_OAS,
        "dgs10": TEN_YEAR_YIELD,
        "10y": TEN_YEAR_YIELD,
        "ten_year_yield": TEN_YEAR_YIELD,
        "t10y2y": YIELD_CURVE_10Y2Y,
        "yield_curve_10y2y": YIELD_CURVE_10Y2Y,
        "market_trend": MARKET_TREND,
        "market_breadth": MARKET_BREADTH,
        "dtwexbgs": DOLLAR_INDEX,
        "dollar_index": DOLLAR_INDEX,
        "hyg_credit_proxy": HYG_CREDIT_PROXY,
        "credit_proxy": HYG_CREDIT_PROXY,
        "sentiment_proxy": SENTIMENT_PROXY,
        "internal_sentiment_proxy": SENTIMENT_PROXY,
    }
    return aliases.get(text, text)


def _macro_data_status(items: list[MacroIndicatorSnapshot]) -> tuple[str, str]:
    usable_core = [
        item
        for item in items
        if item.indicator in CORE_MACRO_INDICATORS and _usable_value(item) is not None
    ]
    stale_core = [
        item
        for item in items
        if item.indicator in CORE_MACRO_INDICATORS and item.value is not None and item.is_stale
    ]
    if len(usable_core) >= 4:
        return "完整", "高"
    if len(usable_core) >= 2:
        return "部分可用", "中"
    if len(usable_core) == 1:
        return "部分可用", "低"
    if stale_core:
        return "过期", "低"
    return "缺失", "低"


def _read_url_text(url: str, *, timeout_seconds: int = MACRO_REQUEST_TIMEOUT_SECONDS) -> str:
    request = Request(url, headers={"User-Agent": "ZHX-Research/1.0"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8")


def _read_json(url: str, *, timeout_seconds: int = MACRO_REQUEST_TIMEOUT_SECONDS) -> Any:
    return json.loads(_read_url_text(url, timeout_seconds=timeout_seconds))


def _series_rows_from_payload(payload: Any, *, value_key: str) -> list[tuple[str, float]]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    rows: list[tuple[str, float]] = []
    if isinstance(payload, str):
        reader = csv.DictReader(io.StringIO(payload))
        for row in reader:
            date = str(row.get("observation_date") or row.get("DATE") or row.get("date") or "").strip()
            value = _number(row.get(value_key) or row.get("value") or row.get("VALUE"))
            if date and value is not None:
                rows.append((date, value))
        return rows
    if isinstance(payload, dict):
        records = payload.get("observations") or payload.get("data") or payload.get(value_key) or []
        if isinstance(records, dict):
            records = records.get("data") or []
    else:
        records = payload
    if isinstance(records, list):
        for row in records:
            if not isinstance(row, dict):
                continue
            date = str(row.get("date") or row.get("observation_date") or row.get("x") or "").strip()
            value = _number(row.get("value") or row.get("y") or row.get(value_key))
            if date and value is not None:
                rows.append((date[:10], value))
    return rows


def _series_change(values: list[float], days: int) -> float | None:
    if len(values) <= days:
        return None
    return round(values[-1] - values[-days - 1], 2)


def _series_percentile(values: list[float], current_value: float) -> float | None:
    if not values:
        return None
    return round(sum(1 for value in values if value <= current_value) / len(values) * 100, 1)


def _observation_date_stale(value: str | None, *, now: datetime, max_days: int) -> bool:
    if not value:
        return True
    try:
        observed = datetime.fromisoformat(str(value)[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return now.astimezone(timezone.utc) - observed > timedelta(days=max_days)


def _extract_fear_greed_value(payload: Any) -> float | None:
    if isinstance(payload, dict):
        candidates = [
            payload,
            payload.get("fear_and_greed"),
            payload.get("fearAndGreed"),
            payload.get("fearGreed"),
            payload.get("current"),
        ]
        for candidate in candidates:
            if isinstance(candidate, dict):
                value = _number(candidate.get("score") or candidate.get("value") or candidate.get("index"))
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


def _extract_fear_greed_observation_date(payload: Any, *, now: datetime) -> str:
    timestamp = None
    if isinstance(payload, dict):
        current = payload.get("fear_and_greed") or payload.get("fearAndGreed") or payload.get("current") or payload
        if isinstance(current, dict):
            timestamp = current.get("timestamp") or current.get("asOf") or current.get("date")
    parsed = _timestamp_or_date_to_date(timestamp)
    return parsed or now.date().isoformat()


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


def _quote_observation_date(quote: Any) -> str | None:
    if not isinstance(quote, dict):
        return None
    for key in ("date", "timestamp", "updated_at", "fetched_at"):
        parsed = _timestamp_or_date_to_date(quote.get(key))
        if parsed:
            return parsed
    return None


def _value_from_mapping(mapping: Any, *keys: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return None


def _compact_raw_payload(payload: Any, *, limit: int = 1200) -> str | None:
    try:
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    except TypeError:
        text = str(payload)
    text = text.strip()
    if not text:
        return None
    return text[:limit]


def _short_error(exc: Exception, limit: int = 120) -> str:
    message = str(exc).replace("\n", " ").strip()
    return message if len(message) <= limit else message[: limit - 1] + "…"


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result
