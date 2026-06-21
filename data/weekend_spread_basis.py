from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile

import pandas as pd

from data.binance_provider import BinanceHTTPPriceProvider, BinancePriceProvider, CachedBinancePriceProvider
from data.cache_read_model import CacheReadModel
from data.providers import get_secret
from data.us_market_session import US_EASTERN, HONG_KONG, _is_trading_day
from data.weekend_spread import is_binance_symbol_ignored, load_binance_symbol_ignore, load_binance_symbol_mapping
from data.weekend_spread_backtest import normalize_klines
from data.weekend_spread_monitor import fetch_bulk_usdm_prices
from settings import PROJECT_ROOT


DEFAULT_BASIS_DB_PATH = PROJECT_ROOT / "data" / "cache" / "weekend_spread_basis.sqlite3"
DEFAULT_BASIS_TASK_NAME = "facai_weekend_spread_basis_collector"
BINANCE_VISION_FUTURES_DAILY_KLINE_URL = "https://data.binance.vision/data/futures/um/daily/klines"
QUALITY_SUFFICIENT = "可用"
QUALITY_LIMITED = "样本较少"
QUALITY_INSUFFICIENT = "数据异常"
QUALITY_TIME_MISALIGNED = "时间未对齐"
QUALITY_UNAVAILABLE = "未采集"
BASIS_SOURCE = "weekend_spread_open_market_basis"


@dataclass(frozen=True)
class BasisSample:
    sample_time_et: str
    sample_time_hkt: str
    ticker: str
    binance_symbol: str
    binance_price: float
    stock_spot_price: float
    stock_spot_source: str
    binance_source: str
    basis_pct: float
    price_time_diff_seconds: float | None
    market_session: str
    sample_quality: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BasisProfile:
    ticker: str
    normal_basis_5d_pct: float | None
    normal_basis_20d_pct: float | None
    normal_basis_median_pct: float | None
    normal_basis_iqr_pct: float | None
    normal_basis_mad_pct: float | None
    sample_count: int
    trading_days_count: int
    latest_sample_time: str
    aligned_sample_count: int
    misaligned_sample_count: int
    basis_quality: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HistoricalBasisBar:
    ts: datetime
    close: float
    source: str = ""


class AlpacaRegularHoursBarProvider:
    provider_name = "ALPACA_REGULAR"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str = "https://data.alpaca.markets",
        feed: str | None = None,
        timeout_seconds: float = 12.0,
    ) -> None:
        self.api_key = api_key or get_secret("ALPACA_API_KEY_ID") or get_secret("ALPACA_API_KEY")
        self.api_secret = api_secret or get_secret("ALPACA_API_SECRET_KEY") or get_secret("ALPACA_SECRET_KEY")
        self.base_url = base_url.rstrip("/")
        self.feeds = _normalize_alpaca_feeds(feed or get_secret("ALPACA_BASIS_FEED") or get_secret("ALPACA_AFTERHOURS_FEED"))
        self.timeout_seconds = timeout_seconds
        self.last_error_reason = ""
        self.last_feed = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def get_stock_bars(
        self,
        symbol: str,
        *,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1m",
    ) -> list[dict[str, Any]]:
        if not self.is_configured:
            self.last_error_reason = "API_KEY_MISSING"
            return []
        normalized = str(symbol or "").strip().upper()
        if not normalized:
            self.last_error_reason = "MISSING_SYMBOL"
            return []
        timeframe = "1Min" if str(interval or "").lower() in {"1m", "1min", "1 min"} else str(interval)
        fetch_errors: list[str] = []
        for feed in self.feeds:
            params = {
                "timeframe": timeframe,
                "start": start_time.astimezone(timezone.utc).isoformat(),
                "end": end_time.astimezone(timezone.utc).isoformat(),
                "adjustment": "raw",
                "feed": feed,
                "sort": "asc",
                "limit": "10000",
            }
            try:
                payload = self._get_json(f"v2/stocks/{normalized}/bars", params)
            except HTTPError as exc:
                fetch_errors.append(f"{feed}:HTTPError:{exc.code}")
                continue
            except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                fetch_errors.append(f"{feed}:{type(exc).__name__}")
                continue
            rows = payload.get("bars") if isinstance(payload, dict) else []
            if isinstance(rows, list) and rows:
                self.last_feed = feed
                self.last_error_reason = ""
                return [dict(row) for row in rows if isinstance(row, dict)]
        self.last_error_reason = ";".join(fetch_errors) if fetch_errors else "NO_REGULAR_BARS"
        return []

    def _get_json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/{endpoint.lstrip('/')}?{urlencode(params)}",
            headers={
                "APCA-API-KEY-ID": self.api_key or "",
                "APCA-API-SECRET-KEY": self.api_secret or "",
                "User-Agent": "facai-weekend-spread-basis/1.0",
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return payload if isinstance(payload, dict) else {}


def calculate_basis_pct(binance_price: Any, stock_spot_price: Any) -> float | None:
    binance = _number(binance_price)
    spot = _number(stock_spot_price)
    if binance is None or spot is None or binance <= 0 or spot <= 0:
        return None
    return (binance / spot - 1.0) * 100.0


def calculate_adjusted_spread_pct(raw_spread_pct: Any, normal_basis_pct: Any) -> float | None:
    raw = _number(raw_spread_pct)
    basis = _number(normal_basis_pct)
    if raw is None or basis is None:
        return None
    return raw - basis


def is_open_market_basis_window(now: datetime | None = None) -> bool:
    current = _to_et(now or datetime.now(timezone.utc))
    if not _is_trading_day(current.date()):
        return False
    return time(10, 0) <= current.time() <= time(15, 30)


def collect_open_market_basis_once(
    symbols: Iterable[str] | None = None,
    *,
    mapping: dict[str, dict[str, Any]] | None = None,
    ignored: dict[str, dict[str, Any]] | None = None,
    cache: CacheReadModel | None = None,
    price_provider: BinancePriceProvider | None = None,
    price_map: dict[str, float] | None = None,
    db_path: Path = DEFAULT_BASIS_DB_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_et = _to_et(now or datetime.now(timezone.utc))
    if not is_open_market_basis_window(current_et):
        return {
            "ok": False,
            "collected_count": 0,
            "skipped_count": 0,
            "message": "当前不是美股正常交易时段，不能采集开市基差。请在美股 10:00-15:30 ET 期间采集。",
            "market_session": "closed",
            "sample_time_et": current_et.isoformat(),
        }

    mapping = mapping if mapping is not None else load_binance_symbol_mapping()
    ignored = ignored if ignored is not None else load_binance_symbol_ignore()
    cache = cache or CacheReadModel()
    selected = {str(item or "").strip().upper() for item in symbols or [] if str(item or "").strip()}
    active = _active_mapping_rows(mapping, ignored, selected)
    prices = {str(key or "").strip().upper(): float(value) for key, value in (price_map or {}).items() if _number(value) is not None}
    if price_map is None:
        prices = fetch_bulk_usdm_prices(price_provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider(), ttl_seconds=45))

    samples: list[dict[str, Any]] = []
    skipped = 0
    for ticker, config in active.items():
        binance_symbol = str(config.get("binance_symbol") or "").strip().upper()
        binance_price = _number(prices.get(binance_symbol))
        if binance_price is None or binance_price <= 0:
            skipped += 1
            continue
        spot = _stock_spot_snapshot(cache, ticker)
        stock_price = _number(spot.get("price"))
        basis = calculate_basis_pct(binance_price, stock_price)
        if basis is None:
            skipped += 1
            continue
        quote_time = _coerce_datetime(spot.get("time"))
        diff_seconds = abs((current_et.astimezone(timezone.utc) - quote_time.astimezone(timezone.utc)).total_seconds()) if quote_time else None
        quality = QUALITY_SUFFICIENT if diff_seconds is not None and diff_seconds <= 60 else QUALITY_TIME_MISALIGNED
        sample = BasisSample(
            sample_time_et=current_et.isoformat(),
            sample_time_hkt=current_et.astimezone(HONG_KONG).isoformat(),
            ticker=ticker,
            binance_symbol=binance_symbol,
            binance_price=float(binance_price),
            stock_spot_price=float(stock_price),
            stock_spot_source=str(spot.get("source") or "quote_snapshot"),
            binance_source="binance_usdm_futures",
            basis_pct=float(basis),
            price_time_diff_seconds=diff_seconds,
            market_session="regular",
            sample_quality=quality,
            created_at=datetime.now(timezone.utc).isoformat(),
        ).as_dict()
        samples.append(sample)

    if samples:
        save_basis_samples(samples, db_path=db_path)
        for ticker in {sample["ticker"] for sample in samples}:
            upsert_basis_profile(build_normal_basis_profile(ticker, db_path=db_path), db_path=db_path)

    return {
        "ok": True,
        "collected_count": len(samples),
        "skipped_count": skipped,
        "message": f"已采集 {len(samples)} 条开市基差样本，跳过 {skipped} 条。",
        "market_session": "regular",
        "sample_time_et": current_et.isoformat(),
        "samples": samples,
    }


def backfill_open_market_basis_history(
    symbols: Iterable[str] | None = None,
    *,
    mapping: dict[str, dict[str, Any]] | None = None,
    ignored: dict[str, dict[str, Any]] | None = None,
    binance_history_provider: Any | None = None,
    stock_history_provider: Any | None = None,
    db_path: Path = DEFAULT_BASIS_DB_PATH,
    now: datetime | None = None,
    lookback_trading_days: int = 5,
    sample_interval_minutes: int = 30,
    max_alignment_seconds: int = 60,
    max_candidate_gap_seconds: int = 300,
    resume: bool = True,
) -> dict[str, Any]:
    mapping = mapping if mapping is not None else load_binance_symbol_mapping()
    ignored = ignored if ignored is not None else load_binance_symbol_ignore()
    selected = {str(item or "").strip().upper() for item in symbols or [] if str(item or "").strip()}
    active = _active_mapping_rows(mapping, ignored, selected)
    if not active:
        return {
            "ok": False,
            "collected_count": 0,
            "skipped_count": 0,
            "misaligned_count": 0,
            "message": "没有可回填的开市基差标的。",
            "samples": [],
        }

    stock_provider = stock_history_provider or AlpacaRegularHoursBarProvider()
    if not _stock_history_provider_ready(stock_provider):
        return {
            "ok": False,
            "collected_count": 0,
            "skipped_count": len(active),
            "misaligned_count": 0,
            "message": "缺少可用的美股历史分钟线数据源，无法回填开市基差。请配置 Alpaca 常规盘分钟线权限，或在开市时实时采集。",
            "provider_status": str(getattr(stock_provider, "last_error_reason", "") or "API_KEY_MISSING"),
            "samples": [],
        }

    binance_provider = binance_history_provider or BinanceHTTPPriceProvider()
    current_et = _to_et(now or datetime.now(timezone.utc))
    trading_days = _recent_completed_trading_days(current_et, max(1, int(lookback_trading_days or 5)))
    sample_interval = max(1, int(sample_interval_minutes or 30))
    samples: list[dict[str, Any]] = []
    skipped = 0
    skipped_existing = 0
    misaligned = 0
    errors: list[str] = []
    completed_windows = 0

    for ticker, config in active.items():
        binance_symbol = str(config.get("binance_symbol") or "").strip().upper()
        if not binance_symbol:
            skipped += 1
            continue
        for day in trading_days:
            start_et = datetime.combine(day, time(10, 0), tzinfo=US_EASTERN)
            end_et = datetime.combine(day, time(15, 30), tzinfo=US_EASTERN)
            target_times = _basis_sample_times(start_et, end_et, sample_interval)
            if resume and _basis_day_sample_count(ticker, day, db_path=db_path) >= len(target_times):
                skipped_existing += len(target_times)
                completed_windows += 1
                continue
            try:
                binance_bars = _fetch_binance_history_bars(binance_provider, binance_symbol, start_et, end_et + timedelta(minutes=1))
                stock_bars = _fetch_stock_history_bars(stock_provider, ticker, start_et, end_et + timedelta(minutes=1))
            except Exception as exc:
                skipped += len(target_times)
                errors.append(f"{ticker}:{type(exc).__name__}")
                continue
            day_samples: list[dict[str, Any]] = []
            day_misaligned = 0
            for target in target_times:
                binance_bar = _nearest_bar(binance_bars, target, max_seconds=max_candidate_gap_seconds)
                stock_bar = _nearest_bar(stock_bars, target, max_seconds=max_candidate_gap_seconds)
                if binance_bar is None or stock_bar is None:
                    skipped += 1
                    continue
                diff_seconds = abs((binance_bar.ts.astimezone(timezone.utc) - stock_bar.ts.astimezone(timezone.utc)).total_seconds())
                quality = QUALITY_SUFFICIENT if diff_seconds <= max_alignment_seconds else QUALITY_TIME_MISALIGNED
                if quality == QUALITY_TIME_MISALIGNED:
                    day_misaligned += 1
                basis = calculate_basis_pct(binance_bar.close, stock_bar.close)
                if basis is None:
                    skipped += 1
                    continue
                day_samples.append(
                    BasisSample(
                        sample_time_et=target.isoformat(),
                        sample_time_hkt=target.astimezone(HONG_KONG).isoformat(),
                        ticker=ticker,
                        binance_symbol=binance_symbol,
                        binance_price=float(binance_bar.close),
                        stock_spot_price=float(stock_bar.close),
                        stock_spot_source=stock_bar.source or "historical_stock_1m",
                        binance_source=binance_bar.source or "binance_usdm_futures_1m",
                        basis_pct=float(basis),
                        price_time_diff_seconds=diff_seconds,
                        market_session="regular",
                        sample_quality=quality,
                        created_at=datetime.now(timezone.utc).isoformat(),
                    ).as_dict()
                )
            if day_samples:
                _delete_basis_samples_for_ticker_day(ticker, day, db_path=db_path)
                save_basis_samples(day_samples, db_path=db_path)
                upsert_basis_profile(build_normal_basis_profile(ticker, db_path=db_path, now=current_et), db_path=db_path)
                samples.extend(day_samples)
                misaligned += day_misaligned
                completed_windows += 1

    return {
        "ok": bool(samples) or skipped_existing > 0,
        "collected_count": len(samples),
        "skipped_count": skipped,
        "skipped_existing_count": skipped_existing,
        "misaligned_count": misaligned,
        "completed_windows": completed_windows,
        "trading_days": [day.isoformat() for day in trading_days],
        "message": (
            f"已回填 {len(samples)} 条历史开市基差样本，时间未对齐 {misaligned} 条，跳过 {skipped} 条，已存在跳过 {skipped_existing} 条。"
            if samples
            else (
                f"历史开市基差已是最新：已存在样本 {skipped_existing} 条，无需重复回填。"
                if skipped_existing > 0
                else "没有回填到可用历史开市基差样本。请检查 Binance / 美股分钟线权限。"
            )
        ),
        "errors": errors[:10],
        "samples": samples,
    }


def install_open_market_basis_task(*, task_name: str = DEFAULT_BASIS_TASK_NAME, interval_minutes: int = 5) -> dict[str, Any]:
    if os.name != "nt":
        return {"ok": False, "message": "当前环境不支持 Windows 任务计划，请在美股开市时手动采集。"}
    python_exe = _windowless_python_executable()
    script = PROJECT_ROOT / "tools" / "weekend_spread_basis_collector.py"
    if not python_exe.exists() or not script.exists():
        return {"ok": False, "message": "开市基差采集任务路径不存在，无法安装。"}
    arguments = "tools\\weekend_spread_basis_collector.py --once --source scheduler --quiet"
    ps_script = "\n".join(
        [
            f"$Action = New-ScheduledTaskAction -Execute '{_ps_quote(str(python_exe))}' -Argument '{_ps_quote(arguments)}' -WorkingDirectory '{_ps_quote(str(PROJECT_ROOT))}'",
            f"$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes {max(1, int(interval_minutes))}) -RepetitionDuration (New-TimeSpan -Days 3650)",
            "$Settings = New-ScheduledTaskSettingsSet -Hidden -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)",
            f"Register-ScheduledTask -TaskName '{_ps_quote(task_name)}' -Action $Action -Trigger $Trigger -Settings $Settings -Description 'facai weekend spread open-market basis collector silent task' -Force | Out-Null",
        ]
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=flags,
    )
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "").strip()
        return {"ok": False, "message": f"开市基差采集任务安装失败：{error or '未返回错误原因'}"}
    mode = "静默模式" if python_exe.name.lower() == "pythonw.exe" else "隐藏窗口模式"
    return {"ok": True, "message": f"已安装开市基差采集任务（每 5 分钟，{mode}）。"}


def save_basis_samples(samples: Iterable[dict[str, Any]], *, db_path: Path = DEFAULT_BASIS_DB_PATH) -> int:
    rows = [dict(sample or {}) for sample in samples or []]
    if not rows:
        return 0
    _ensure_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        with conn:
            conn.executemany(
                """
                INSERT INTO weekend_spread_basis_samples (
                    sample_time_et, sample_time_hkt, ticker, binance_symbol, binance_price,
                    stock_spot_price, stock_spot_source, binance_source, basis_pct,
                    price_time_diff_seconds, market_session, sample_quality, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(row.get("sample_time_et") or ""),
                        str(row.get("sample_time_hkt") or ""),
                        str(row.get("ticker") or "").strip().upper(),
                        str(row.get("binance_symbol") or "").strip().upper(),
                        _number(row.get("binance_price")),
                        _number(row.get("stock_spot_price")),
                        str(row.get("stock_spot_source") or ""),
                        str(row.get("binance_source") or ""),
                        _number(row.get("basis_pct")),
                        _number(row.get("price_time_diff_seconds")),
                        str(row.get("market_session") or "regular"),
                        str(row.get("sample_quality") or ""),
                        str(row.get("created_at") or datetime.now(timezone.utc).isoformat()),
                    )
                    for row in rows
                ],
            )
    return len(rows)


def build_normal_basis_profile(
    ticker: str,
    *,
    db_path: Path = DEFAULT_BASIS_DB_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = str(ticker or "").strip().upper()
    frame = load_basis_samples(normalized, db_path=db_path)
    if frame.empty:
        return _empty_profile(normalized).as_dict()
    current_et = _to_et(now or datetime.now(timezone.utc))
    frame["sample_dt"] = pd.to_datetime(frame["sample_time_et"], errors="coerce", utc=True).dt.tz_convert(US_EASTERN)
    frame = frame.dropna(subset=["sample_dt"])
    frame["basis_pct"] = pd.to_numeric(frame["basis_pct"], errors="coerce")
    frame["price_time_diff_seconds"] = pd.to_numeric(frame["price_time_diff_seconds"], errors="coerce")
    frame = frame.dropna(subset=["basis_pct"])
    if frame.empty:
        return _empty_profile(normalized).as_dict()

    recent_20 = frame[frame["sample_dt"] >= current_et - timedelta(days=35)]
    recent_5 = frame[frame["sample_dt"] >= current_et - timedelta(days=10)]
    aligned_20 = recent_20[(recent_20["market_session"] == "regular") & (recent_20["price_time_diff_seconds"] <= 60)]
    aligned_5 = recent_5[(recent_5["market_session"] == "regular") & (recent_5["price_time_diff_seconds"] <= 60)]
    aligned_all = frame[(frame["market_session"] == "regular") & (frame["price_time_diff_seconds"] <= 60)]
    aligned_count = len(aligned_20)
    misaligned_count = len(recent_20) - aligned_count

    basis_20 = _median_or_none(aligned_20["basis_pct"].tolist())
    basis_5 = _median_or_none(aligned_5["basis_pct"].tolist())
    preferred = basis_20 if basis_20 is not None else basis_5
    basis_values = [float(value) for value in aligned_20["basis_pct"].tolist() if _number(value) is not None]
    iqr = _iqr(basis_values)
    mad = _mad(basis_values, preferred)
    days = sorted({value.date().isoformat() for value in aligned_20["sample_dt"].tolist() if pd.notna(value)})
    latest = frame["sample_dt"].max()
    quality = _profile_quality(aligned_count=aligned_count, aligned_days=len(days), recent_count=len(recent_20), misaligned_count=misaligned_count)
    if aligned_count == 0 and len(aligned_all) > 0:
        quality = QUALITY_INSUFFICIENT
    return BasisProfile(
        ticker=normalized,
        normal_basis_5d_pct=basis_5,
        normal_basis_20d_pct=basis_20,
        normal_basis_median_pct=preferred,
        normal_basis_iqr_pct=iqr,
        normal_basis_mad_pct=mad,
        sample_count=aligned_count,
        trading_days_count=len(days),
        latest_sample_time="" if pd.isna(latest) else latest.isoformat(),
        aligned_sample_count=aligned_count,
        misaligned_sample_count=max(misaligned_count, 0),
        basis_quality=quality,
    ).as_dict()


def load_basis_profiles(
    tickers: Iterable[str],
    *,
    db_path: Path = DEFAULT_BASIS_DB_PATH,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for ticker in tickers or []:
        normalized = str(ticker or "").strip().upper()
        if not normalized:
            continue
        profiles[normalized] = build_normal_basis_profile(normalized, db_path=db_path, now=now)
    return profiles


def load_cached_basis_profiles(
    tickers: Iterable[str],
    *,
    db_path: Path = DEFAULT_BASIS_DB_PATH,
) -> dict[str, dict[str, Any]]:
    normalized_tickers = [str(ticker or "").strip().upper() for ticker in tickers or [] if str(ticker or "").strip()]
    if not normalized_tickers:
        return {}
    if not Path(db_path).exists():
        return {ticker: _empty_profile(ticker).as_dict() for ticker in normalized_tickers}
    _ensure_schema(db_path)
    placeholders = ",".join("?" for _ in normalized_tickers)
    query = f"""
        SELECT ticker, normal_basis_5d_pct, normal_basis_20d_pct, normal_basis_median_pct,
               normal_basis_iqr_pct, normal_basis_mad_pct, sample_count, trading_days_count,
               latest_sample_time, basis_quality, updated_at
        FROM weekend_spread_basis_profiles
        WHERE ticker IN ({placeholders})
    """
    profiles = {ticker: _empty_profile(ticker).as_dict() for ticker in normalized_tickers}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(query, normalized_tickers).fetchall():
            ticker = str(row["ticker"] or "").strip().upper()
            profiles[ticker] = {
                "ticker": ticker,
                "normal_basis_5d_pct": _number(row["normal_basis_5d_pct"]),
                "normal_basis_20d_pct": _number(row["normal_basis_20d_pct"]),
                "normal_basis_median_pct": _number(row["normal_basis_median_pct"]),
                "normal_basis_iqr_pct": _number(row["normal_basis_iqr_pct"]),
                "normal_basis_mad_pct": _number(row["normal_basis_mad_pct"]),
                "sample_count": int(row["sample_count"] or 0),
                "trading_days_count": int(row["trading_days_count"] or 0),
                "latest_sample_time": str(row["latest_sample_time"] or ""),
                "aligned_sample_count": int(row["sample_count"] or 0),
                "misaligned_sample_count": 0,
                "basis_quality": str(row["basis_quality"] or QUALITY_UNAVAILABLE),
                "updated_at": str(row["updated_at"] or ""),
                "profile_cache_source": "weekend_spread_basis_profiles",
            }
    return profiles


def load_basis_samples(ticker: str | None = None, *, db_path: Path = DEFAULT_BASIS_DB_PATH) -> pd.DataFrame:
    if not Path(db_path).exists():
        return _empty_sample_frame()
    _ensure_schema(db_path)
    query = "SELECT * FROM weekend_spread_basis_samples"
    params: tuple[Any, ...] = ()
    normalized = str(ticker or "").strip().upper()
    if normalized:
        query += " WHERE ticker = ?"
        params = (normalized,)
    query += " ORDER BY sample_time_et"
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(query, conn, params=params)


def _basis_day_sample_count(ticker: str, day: date, *, db_path: Path = DEFAULT_BASIS_DB_PATH) -> int:
    if not Path(db_path).exists():
        return 0
    _ensure_schema(db_path)
    normalized = str(ticker or "").strip().upper()
    if not normalized:
        return 0
    with sqlite3.connect(db_path) as conn:
        return int(
            conn.execute(
                """
                SELECT COUNT(*) FROM weekend_spread_basis_samples
                WHERE ticker = ? AND sample_time_et LIKE ?
                """,
                (normalized, f"{day.isoformat()}%"),
            ).fetchone()[0]
            or 0
        )


def _delete_basis_samples_for_ticker_day(ticker: str, day: date, *, db_path: Path = DEFAULT_BASIS_DB_PATH) -> None:
    if not Path(db_path).exists():
        return
    _ensure_schema(db_path)
    normalized = str(ticker or "").strip().upper()
    if not normalized:
        return
    with sqlite3.connect(db_path) as conn:
        with conn:
            conn.execute(
                """
                DELETE FROM weekend_spread_basis_samples
                WHERE ticker = ? AND sample_time_et LIKE ?
                """,
                (normalized, f"{day.isoformat()}%"),
            )


def upsert_basis_profile(profile: dict[str, Any], *, db_path: Path = DEFAULT_BASIS_DB_PATH) -> None:
    _ensure_schema(db_path)
    ticker = str(profile.get("ticker") or "").strip().upper()
    if not ticker:
        return
    with sqlite3.connect(db_path) as conn:
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO weekend_spread_basis_profiles (
                    ticker, normal_basis_5d_pct, normal_basis_20d_pct, normal_basis_median_pct,
                    normal_basis_iqr_pct, normal_basis_mad_pct, sample_count, trading_days_count,
                    latest_sample_time, basis_quality, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    _number(profile.get("normal_basis_5d_pct")),
                    _number(profile.get("normal_basis_20d_pct")),
                    _number(profile.get("normal_basis_median_pct")),
                    _number(profile.get("normal_basis_iqr_pct")),
                    _number(profile.get("normal_basis_mad_pct")),
                    int(profile.get("sample_count") or 0),
                    int(profile.get("trading_days_count") or 0),
                    str(profile.get("latest_sample_time") or ""),
                    str(profile.get("basis_quality") or QUALITY_INSUFFICIENT),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )


def _ensure_schema(db_path: Path) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weekend_spread_basis_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_time_et TEXT NOT NULL,
                sample_time_hkt TEXT NOT NULL,
                ticker TEXT NOT NULL,
                binance_symbol TEXT NOT NULL,
                binance_price REAL,
                stock_spot_price REAL,
                stock_spot_source TEXT,
                binance_source TEXT,
                basis_pct REAL,
                price_time_diff_seconds REAL,
                market_session TEXT,
                sample_quality TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_weekend_basis_ticker_time ON weekend_spread_basis_samples(ticker, sample_time_et)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weekend_spread_basis_profiles (
                ticker TEXT PRIMARY KEY,
                normal_basis_5d_pct REAL,
                normal_basis_20d_pct REAL,
                normal_basis_median_pct REAL,
                normal_basis_iqr_pct REAL,
                normal_basis_mad_pct REAL,
                sample_count INTEGER,
                trading_days_count INTEGER,
                latest_sample_time TEXT,
                basis_quality TEXT,
                updated_at TEXT
            )
            """
        )


def _active_mapping_rows(
    mapping: dict[str, dict[str, Any]],
    ignored: dict[str, dict[str, Any]],
    selected: set[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for ticker, config in (mapping or {}).items():
        normalized = str(ticker or "").strip().upper()
        symbol = str((config or {}).get("binance_symbol") or "").strip().upper()
        if not normalized or not symbol:
            continue
        if selected and normalized not in selected and symbol not in selected:
            continue
        if is_binance_symbol_ignored(normalized, symbol, ignored):
            continue
        result[normalized] = dict(config or {})
    return result


def _stock_spot_snapshot(cache: CacheReadModel, ticker: str) -> dict[str, Any]:
    snapshot = cache.get_quote_snapshot(ticker) or {}
    payload = snapshot.get("payload") if isinstance(snapshot, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    price = _first_number(payload.get("current_price"), payload.get("currentPrice"), payload.get("price"), payload.get("regularMarketPrice"))
    quote_time = _first_time(
        payload.get("quote_updated_at"),
        payload.get("updated_at"),
        payload.get("timestamp"),
        payload.get("lastUpdated"),
        payload.get("priceTime"),
        snapshot.get("fetched_at") if isinstance(snapshot, dict) else None,
    )
    source = str(payload.get("priceSource") or payload.get("source") or "quote_snapshot")
    return {"price": price, "time": quote_time, "source": source}


def _profile_quality(*, aligned_count: int, aligned_days: int, recent_count: int, misaligned_count: int) -> str:
    if recent_count > 0 and misaligned_count > recent_count / 2:
        return QUALITY_TIME_MISALIGNED
    if aligned_count >= 30 and aligned_days >= 3:
        return QUALITY_SUFFICIENT
    if aligned_count > 0:
        return QUALITY_LIMITED
    return QUALITY_INSUFFICIENT


def _empty_profile(ticker: str) -> BasisProfile:
    return BasisProfile(
        ticker=ticker,
        normal_basis_5d_pct=None,
        normal_basis_20d_pct=None,
        normal_basis_median_pct=None,
        normal_basis_iqr_pct=None,
        normal_basis_mad_pct=None,
        sample_count=0,
        trading_days_count=0,
        latest_sample_time="",
        aligned_sample_count=0,
        misaligned_sample_count=0,
        basis_quality=QUALITY_UNAVAILABLE,
    )


def _empty_sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "sample_time_et",
            "sample_time_hkt",
            "ticker",
            "binance_symbol",
            "binance_price",
            "stock_spot_price",
            "stock_spot_source",
            "binance_source",
            "basis_pct",
            "price_time_diff_seconds",
            "market_session",
            "sample_quality",
            "created_at",
        ]
    )


def _recent_completed_trading_days(now_et: datetime, count: int) -> list[date]:
    current = _to_et(now_et)
    candidate = current.date()
    if not (_is_trading_day(candidate) and current.time() >= time(15, 31)):
        candidate = candidate - timedelta(days=1)
    result: list[date] = []
    while len(result) < count:
        if _is_trading_day(candidate):
            result.append(candidate)
        candidate = candidate - timedelta(days=1)
    return sorted(result)


def _basis_sample_times(start_et: datetime, end_et: datetime, interval_minutes: int) -> list[datetime]:
    current = start_et
    result: list[datetime] = []
    step = timedelta(minutes=max(1, int(interval_minutes or 30)))
    while current <= end_et:
        result.append(current)
        current += step
    return result


def _stock_history_provider_ready(provider: Any) -> bool:
    if provider is None or not hasattr(provider, "get_stock_bars"):
        return False
    configured = getattr(provider, "is_configured", True)
    return bool(configured)


def _fetch_binance_history_bars(
    provider: Any,
    symbol: str,
    start_et: datetime,
    end_et: datetime,
) -> list[HistoricalBasisBar]:
    start_ms = _to_ms(start_et)
    end_ms = _to_ms(end_et)
    cursor = start_ms
    payload: list[Any] = []
    if provider is not None and hasattr(provider, "get_klines"):
        try:
            for _ in range(10):
                batch = provider.get_klines(
                    symbol,
                    market_type="usdm_futures",
                    interval="1m",
                    start_time_ms=cursor,
                    end_time_ms=end_ms,
                    limit=1000,
                )
                if not batch:
                    break
                payload.extend(batch)
                bars = normalize_klines(batch)
                if not bars:
                    break
                next_cursor = _to_ms(bars[-1].open_time + timedelta(minutes=1))
                if next_cursor <= cursor or next_cursor >= end_ms:
                    break
                cursor = next_cursor
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
            payload = []
    bars = [
        HistoricalBasisBar(ts=bar.open_time.astimezone(timezone.utc), close=float(bar.close), source="binance_usdm_futures_1m")
        for bar in normalize_klines(payload)
    ]
    if bars:
        return bars
    return _fetch_binance_vision_daily_klines(symbol, start_et, end_et)


def _fetch_binance_vision_daily_klines(symbol: str, start_et: datetime, end_et: datetime) -> list[HistoricalBasisBar]:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return []
    start_utc = start_et.astimezone(timezone.utc)
    end_utc = end_et.astimezone(timezone.utc)
    bars_by_time: dict[datetime, HistoricalBasisBar] = {}
    for day in _utc_dates_between(start_utc, end_utc):
        for bar in _download_binance_vision_daily_klines(normalized, day):
            ts = bar.ts.astimezone(timezone.utc)
            if start_utc <= ts < end_utc:
                bars_by_time[ts] = bar
    return [bars_by_time[key] for key in sorted(bars_by_time)]


def _download_binance_vision_daily_klines(symbol: str, day: date) -> list[HistoricalBasisBar]:
    url = f"{BINANCE_VISION_FUTURES_DAILY_KLINE_URL}/{symbol}/1m/{symbol}-1m-{day.isoformat()}.zip"
    request = Request(url, headers={"User-Agent": "facai-weekend-spread/1.2"})
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            return []
        text = archive.read(csv_names[0]).decode("utf-8")
    rows = csv.reader(io.StringIO(text))
    bars: list[HistoricalBasisBar] = []
    for row in rows:
        bar = _binance_vision_row_to_bar(row)
        if bar is not None:
            bars.append(bar)
    return bars


def _binance_vision_row_to_bar(row: list[str]) -> HistoricalBasisBar | None:
    if len(row) < 5:
        return None
    open_time = _number(row[0])
    close = _number(row[4])
    if open_time is None or close is None or close <= 0:
        return None
    # Binance Vision has used millisecond timestamps historically; keep this
    # tolerant in case a file is emitted with microsecond precision.
    timestamp = open_time / 1_000_000 if open_time > 10_000_000_000_000 else open_time / 1000
    try:
        ts = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
    return HistoricalBasisBar(ts=ts, close=float(close), source="binance_vision_usdm_futures_1m")


def _utc_dates_between(start_utc: datetime, end_utc: datetime) -> list[date]:
    current = start_utc.date()
    last = (end_utc - timedelta(microseconds=1)).date()
    result: list[date] = []
    while current <= last:
        result.append(current)
        current += timedelta(days=1)
    return result


def _fetch_stock_history_bars(
    provider: Any,
    ticker: str,
    start_et: datetime,
    end_et: datetime,
) -> list[HistoricalBasisBar]:
    rows = provider.get_stock_bars(
        ticker,
        start_time=start_et,
        end_time=end_et,
        interval="1m",
    )
    source = str(getattr(provider, "provider_name", "") or "historical_stock_1m")
    feed = str(getattr(provider, "last_feed", "") or "").strip()
    if feed:
        source = f"{source}_{feed}"
    bars: list[HistoricalBasisBar] = []
    for row in rows or []:
        bar = _normalize_historical_stock_bar(row, source=source)
        if bar is not None:
            bars.append(bar)
    deduped: dict[datetime, HistoricalBasisBar] = {bar.ts: bar for bar in bars}
    return [deduped[key] for key in sorted(deduped)]


def _normalize_historical_stock_bar(row: Any, *, source: str) -> HistoricalBasisBar | None:
    if isinstance(row, HistoricalBasisBar):
        return row
    if not isinstance(row, dict):
        return None
    ts = _first_time(
        row.get("t"),
        row.get("timestamp"),
        row.get("time"),
        row.get("bar_time"),
        row.get("start"),
    )
    close = _first_number(row.get("c"), row.get("close"), row.get("price"), row.get("lastPrice"))
    if ts is None or close is None or close <= 0:
        return None
    return HistoricalBasisBar(ts=ts.astimezone(timezone.utc), close=float(close), source=source)


def _nearest_bar(bars: list[HistoricalBasisBar], target_et: datetime, *, max_seconds: int) -> HistoricalBasisBar | None:
    if not bars:
        return None
    target_utc = target_et.astimezone(timezone.utc)
    closest = min(bars, key=lambda bar: abs((bar.ts.astimezone(timezone.utc) - target_utc).total_seconds()))
    diff = abs((closest.ts.astimezone(timezone.utc) - target_utc).total_seconds())
    return closest if diff <= max_seconds else None


def _to_ms(value: datetime) -> int:
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def _normalize_alpaca_feeds(value: Any) -> list[str]:
    raw = str(value or "").strip()
    candidates = [item.strip().lower() for item in raw.split(",") if item.strip()] if raw else []
    candidates.extend(["sip", "iex"])
    result: list[str] = []
    for item in candidates:
        if item not in {"sip", "iex"} or item in result:
            continue
        result.append(item)
    return result or ["sip", "iex"]


def _to_et(value: datetime) -> datetime:
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.astimezone(US_EASTERN)


def _windowless_python_executable() -> Path:
    scripts_dir = PROJECT_ROOT / ".venv" / "Scripts"
    if os.name == "nt":
        pythonw = scripts_dir / "pythonw.exe"
        if pythonw.exists():
            return pythonw
    bundled = scripts_dir / "python.exe"
    if bundled.exists():
        return bundled
    return Path(sys.executable)


def _ps_quote(value: str) -> str:
    return str(value).replace("'", "''")


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number = number / 1000.0
        return datetime.fromtimestamp(number, tz=timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _first_time(*values: Any) -> datetime | None:
    for value in values:
        parsed = _coerce_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _median_or_none(values: Iterable[Any]) -> float | None:
    numbers = [float(number) for value in values if (number := _number(value)) is not None]
    return float(median(numbers)) if numbers else None


def _iqr(values: list[float]) -> float | None:
    if len(values) < 4:
        return None
    series = pd.Series(values)
    return float(series.quantile(0.75) - series.quantile(0.25))


def _mad(values: list[float], center: float | None) -> float | None:
    if not values or center is None:
        return None
    return float(median([abs(value - center) for value in values]))
