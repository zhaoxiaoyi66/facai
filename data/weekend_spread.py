from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from data.binance_provider import BinanceHTTPPriceProvider, BinancePriceProvider
from data.cache_read_model import CacheReadModel
from settings import CONFIG_DIR


DEFAULT_MAPPING_PATH = CONFIG_DIR / "binance_symbol_mapping.json"
RISK_TEXT = "Binance 映射价格不等于真实美股可成交价格；V1 仅用于观察，不构成套利建议。"
LIQUIDITY_WARNING = "未接入流动性、点差和资金费率校验。"


def load_binance_symbol_mapping(path: Path = DEFAULT_MAPPING_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}
    raw = payload.get("mappings") if isinstance(payload, dict) else {}
    if raw is None and isinstance(payload, dict):
        raw = payload
    if not isinstance(raw, dict):
        return {}
    mapping: dict[str, str] = {}
    for ticker, symbol in raw.items():
        normalized_ticker = str(ticker or "").strip().upper()
        normalized_symbol = str(symbol or "").strip().upper()
        if normalized_ticker and normalized_symbol:
            mapping[normalized_ticker] = normalized_symbol
    return mapping


def build_weekend_spread_rows(
    tickers: Iterable[str],
    *,
    mapping: dict[str, str] | None = None,
    provider: BinancePriceProvider | None = None,
    cache: CacheReadModel | None = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    normalized = _normalize_tickers(tickers)
    effective_mapping = {str(k).upper(): str(v).upper() for k, v in (mapping or load_binance_symbol_mapping()).items()}
    price_provider = provider or BinanceHTTPPriceProvider()
    read_model = cache or CacheReadModel()
    rows: list[dict[str, Any]] = []
    for ticker in normalized:
        friday_close, friday_date = _friday_close(read_model, ticker)
        binance_symbol = effective_mapping.get(ticker)
        quote = read_model.get_quote_payload(ticker) or {}
        stock_name = str(quote.get("companyName") or quote.get("company_name") or quote.get("name") or ticker)
        if not binance_symbol:
            rows.append(_base_row(ticker, stock_name, friday_close, friday_date, "", status="NO_MAPPING"))
            continue
        if friday_close is None:
            rows.append(_base_row(ticker, stock_name, None, friday_date, binance_symbol, status="MISSING_FRIDAY_CLOSE"))
            continue
        snapshot = _snapshot_to_dict(price_provider.get_last_price(binance_symbol, force_refresh=force_refresh))
        last_price = _number(snapshot.get("last_price"))
        if last_price is None:
            row = _base_row(ticker, stock_name, friday_close, friday_date, binance_symbol, status="BINANCE_UNAVAILABLE")
            row["updated_at"] = snapshot.get("updated_at") or ""
            row["error"] = snapshot.get("error") or "binance_price_missing"
            rows.append(row)
            continue
        spread_pct = (last_price / friday_close - 1.0) * 100.0
        alert = classify_spread(spread_pct)
        rows.append(
            {
                **_base_row(ticker, stock_name, friday_close, friday_date, binance_symbol, status="OK"),
                "binance_last_price": last_price,
                "spread_pct": spread_pct,
                "spread_direction": _spread_direction(spread_pct),
                "alert_level": alert["level"],
                "alert_level_cn": alert["label"],
                "updated_at": snapshot.get("updated_at") or "",
            }
        )
    return rows


def classify_spread(spread_pct: float | None) -> dict[str, str]:
    value = abs(float(spread_pct or 0.0))
    if value < 0.5:
        return {"level": "IGNORE", "label": "忽略"}
    if value < 1.2:
        return {"level": "OBSERVE", "label": "观察"}
    if value <= 2.0:
        return {"level": "FOCUS", "label": "重点关注"}
    return {"level": "ABNORMAL", "label": "异常复核"}


def _base_row(
    ticker: str,
    stock_name: str,
    friday_close: float | None,
    friday_date: str,
    binance_symbol: str,
    *,
    status: str,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "stock_name": stock_name,
        "friday_close": friday_close,
        "friday_close_date": friday_date,
        "binance_symbol": binance_symbol,
        "binance_last_price": None,
        "spread_pct": None,
        "spread_direction": "",
        "alert_level": "UNAVAILABLE" if status != "NO_MAPPING" else "NO_MAPPING",
        "alert_level_cn": _status_label(status),
        "liquidity_warning": LIQUIDITY_WARNING,
        "mapping_risk": "暂无映射" if status == "NO_MAPPING" else RISK_TEXT,
        "updated_at": "",
        "status": status,
        "error": "",
    }


def _friday_close(cache: CacheReadModel, ticker: str) -> tuple[float | None, str]:
    history = cache.get_price_history(ticker)
    if history is None or history.empty or "date" not in history or "close" not in history:
        return None, ""
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).sort_values("date")
    fridays = frame[frame["date"].dt.weekday == 4]
    if fridays.empty:
        return None, ""
    latest = fridays.iloc[-1]
    return float(latest["close"]), latest["date"].date().isoformat()


def _snapshot_to_dict(snapshot: Any) -> dict[str, Any]:
    if isinstance(snapshot, dict):
        return dict(snapshot)
    if is_dataclass(snapshot):
        return asdict(snapshot)
    return {
        "symbol": getattr(snapshot, "symbol", ""),
        "last_price": getattr(snapshot, "last_price", None),
        "updated_at": getattr(snapshot, "updated_at", ""),
        "error": getattr(snapshot, "error", ""),
    }


def _status_label(status: str) -> str:
    return {
        "NO_MAPPING": "暂无映射",
        "MISSING_FRIDAY_CLOSE": "周五收盘价缺失",
        "BINANCE_UNAVAILABLE": "数据不可用",
    }.get(status, "数据不可用")


def _spread_direction(spread_pct: float) -> str:
    if spread_pct > 0:
        return "Binance 高于周五收盘"
    if spread_pct < 0:
        return "Binance 低于周五收盘"
    return "基本持平"


def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in tickers:
        ticker = str(item or "").strip().upper()
        if not ticker or ticker in seen:
            continue
        result.append(ticker)
        seen.add(ticker)
    return result


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
