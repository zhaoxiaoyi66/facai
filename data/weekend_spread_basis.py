from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
import sqlite3
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import pandas as pd

from data.binance_provider import BinanceHTTPPriceProvider, BinancePriceProvider, CachedBinancePriceProvider
from data.cache_read_model import CacheReadModel
from data.us_market_session import US_EASTERN, HONG_KONG, _is_trading_day
from data.weekend_spread import is_binance_symbol_ignored, load_binance_symbol_ignore, load_binance_symbol_mapping
from data.weekend_spread_monitor import fetch_bulk_usdm_prices
from settings import PROJECT_ROOT


DEFAULT_BASIS_DB_PATH = PROJECT_ROOT / "data" / "cache" / "weekend_spread_basis.sqlite3"
QUALITY_SUFFICIENT = "充足"
QUALITY_LIMITED = "较少"
QUALITY_INSUFFICIENT = "不足"
QUALITY_TIME_MISALIGNED = "时间未对齐"
QUALITY_UNAVAILABLE = "不可用"
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
            "message": "当前不是美股正常交易时段，不能采集开市基差。",
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
    if aligned_count >= 50 and aligned_days >= 5:
        return QUALITY_SUFFICIENT
    if aligned_count >= 10:
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
        basis_quality=QUALITY_INSUFFICIENT,
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


def _to_et(value: datetime) -> datetime:
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.astimezone(US_EASTERN)


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
