from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from data.providers import get_secret
from settings import PROJECT_ROOT


ET = ZoneInfo("America/New_York")
DEFAULT_TRADINGVIEW_CACHE_PATH = PROJECT_ROOT / "data" / "manual_import" / "tradingview_cache.json"
DEFAULT_TRADINGVIEW_CSV_DIR = PROJECT_ROOT / "data" / "manual_import" / "tradingview"

EVENT_FRIDAY_AFTERHOURS_CLOSE = "FRIDAY_AFTERHOURS_CLOSE"
EVENT_OVERNIGHT_FIRST_1M_CLOSE = "OVERNIGHT_FIRST_1M_CLOSE"
EVENT_TRADINGVIEW_1M_BAR = "TRADINGVIEW_1M_BAR"

PROVIDER_TRADINGVIEW_WEBHOOK = "TRADINGVIEW_WEBHOOK"
PROVIDER_TRADINGVIEW_CSV = "TRADINGVIEW_CSV"
PROVIDER_MANUAL_OVERNIGHT = "MANUAL_OVERNIGHT_1M"
PROVIDER_MANUAL_AFTERHOURS = "MANUAL_AFTERHOURS_1M"

SOURCE_TYPE_TV_ALERT = "TV_ALERT"
SOURCE_TYPE_MANUAL_CSV = "MANUAL_CSV"
SOURCE_TYPE_MANUAL_OVERNIGHT = "MANUAL_OVERNIGHT_1M"
SOURCE_TYPE_MANUAL_AFTERHOURS = "MANUAL_AFTERHOURS_1M"


@dataclass(frozen=True)
class SupplementalPrice:
    ok: bool
    symbol: str
    event_type: str
    close: float | None
    timestamp_et: datetime | None
    provider: str
    source_type: str
    quality: str
    reason: str = ""
    source_file: str = ""
    note: str = ""


def tradingview_webhook_secret_configured() -> bool:
    return bool(str(get_secret("TRADINGVIEW_WEBHOOK_SECRET") or "").strip())


def load_price_cache(path: Path = DEFAULT_TRADINGVIEW_CACHE_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        rows = payload.get("records") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def save_price_cache(records: Iterable[dict[str, Any]], path: Path = DEFAULT_TRADINGVIEW_CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "records": list(records),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except PermissionError:
        # Windows can briefly lock files while Streamlit reruns. Direct write is
        # less elegant but avoids crashing the page for a local cache update.
        path.write_text(text, encoding="utf-8")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def upsert_price_event(
    *,
    symbol: str,
    event_type: str,
    timestamp_et: str | datetime,
    close: Any,
    provider: str,
    source_type: str,
    path: Path = DEFAULT_TRADINGVIEW_CACHE_PATH,
    source: str = "",
    open_: Any = None,
    high: Any = None,
    low: Any = None,
    volume: Any = None,
    source_file: str = "",
    note: str = "",
) -> dict[str, Any]:
    normalized_symbol = _normalize_symbol(symbol)
    normalized_event = str(event_type or "").strip().upper()
    parsed_ts = parse_et_datetime(timestamp_et)
    close_value = _number(close)
    if not normalized_symbol:
        raise ValueError("股票代码缺失")
    if normalized_event not in {EVENT_FRIDAY_AFTERHOURS_CLOSE, EVENT_OVERNIGHT_FIRST_1M_CLOSE, EVENT_TRADINGVIEW_1M_BAR}:
        raise ValueError("价格事件类型不支持")
    if parsed_ts is None:
        raise ValueError("价格时间无法识别")
    if close_value is None or close_value <= 0:
        raise ValueError("收盘价必须大于 0")
    normalized_provider = str(provider or "").strip().upper()
    if not normalized_provider:
        raise ValueError("价格来源缺失")
    record = {
        "symbol": normalized_symbol,
        "event_type": normalized_event,
        "timestamp_et": parsed_ts.astimezone(ET).isoformat(),
        "close": close_value,
        "open": _number(open_),
        "high": _number(high),
        "low": _number(low),
        "volume": _number(volume),
        "provider": normalized_provider,
        "source_type": str(source_type or "").strip().upper(),
        "source": str(source or "").strip(),
        "source_file": str(source_file or "").strip(),
        "note": str(note or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    records = load_price_cache(path)
    key = _record_key(record)
    replaced = False
    for index, current in enumerate(records):
        if _record_key(current) == key:
            record["created_at"] = str(current.get("created_at") or record["created_at"])
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            records[index] = record
            replaced = True
            break
    if not replaced:
        record["updated_at"] = record["created_at"]
        records.append(record)
    save_price_cache(records, path)
    return record


def upsert_price_records(records_to_upsert: Iterable[dict[str, Any]], path: Path = DEFAULT_TRADINGVIEW_CACHE_PATH) -> int:
    records = load_price_cache(path)
    index_by_key = {_record_key(row): index for index, row in enumerate(records)}
    changed = 0
    for raw in records_to_upsert:
        record = dict(raw)
        key = _record_key(record)
        if not key.strip("|"):
            continue
        if key in index_by_key:
            existing_index = index_by_key[key]
            record["created_at"] = str(records[existing_index].get("created_at") or record.get("created_at") or datetime.now(timezone.utc).isoformat())
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            records[existing_index] = record
        else:
            record.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            record.setdefault("updated_at", record["created_at"])
            index_by_key[key] = len(records)
            records.append(record)
        changed += 1
    if changed:
        save_price_cache(records, path)
    return changed


def record_tradingview_webhook(payload: dict[str, Any], *, path: Path = DEFAULT_TRADINGVIEW_CACHE_PATH) -> dict[str, Any]:
    expected_secret = str(get_secret("TRADINGVIEW_WEBHOOK_SECRET") or "").strip()
    if not expected_secret:
        return {"ok": False, "reason": "TradingView Webhook 密钥未配置"}
    if str(payload.get("secret") or "").strip() != expected_secret:
        return {"ok": False, "reason": "TradingView Webhook 密钥不匹配"}
    try:
        record = upsert_price_event(
            symbol=str(payload.get("symbol") or ""),
            event_type=str(payload.get("event_type") or ""),
            timestamp_et=payload.get("timestamp_et") or payload.get("time") or payload.get("timestamp") or "",
            close=payload.get("close"),
            provider=PROVIDER_TRADINGVIEW_WEBHOOK,
            source_type=SOURCE_TYPE_TV_ALERT,
            source=str(payload.get("source") or "TradingView"),
            path=path,
        )
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "reason": "写入成功", "record": record}


def find_friday_afterhours_close(
    symbol: str,
    friday_date_et: date,
    *,
    path: Path = DEFAULT_TRADINGVIEW_CACHE_PATH,
) -> SupplementalPrice:
    normalized = _normalize_symbol(symbol)
    window_start = datetime.combine(friday_date_et, time(19, 55), ET)
    window_end = datetime.combine(friday_date_et, time(20, 0), ET)
    return _find_supplemental_price(
        normalized,
        EVENT_FRIDAY_AFTERHOURS_CLOSE,
        window_start,
        window_end,
        include_end=True,
        path=path,
        missing_reason="缺少周五盘后价格",
    )


def find_overnight_first_1m_close(
    symbol: str,
    session_start_et: datetime,
    *,
    path: Path = DEFAULT_TRADINGVIEW_CACHE_PATH,
) -> SupplementalPrice:
    normalized = _normalize_symbol(symbol)
    start = session_start_et.astimezone(ET)
    end = start + timedelta(minutes=1)
    result = _find_supplemental_price(
        normalized,
        EVENT_OVERNIGHT_FIRST_1M_CLOSE,
        start,
        end,
        include_end=False,
        path=path,
        missing_reason="缺少美股夜盘首分钟价格",
    )
    if not result.ok and _has_delayed_overnight_supplemental_record(normalized, start, path=path):
        return SupplementalPrice(
            ok=False,
            symbol=normalized,
            event_type=EVENT_OVERNIGHT_FIRST_1M_CLOSE,
            close=None,
            timestamp_et=None,
            provider="",
            source_type="",
            quality="MISSING_SUPPLEMENTAL_PRICE",
            reason="STRICT_OVERNIGHT_FIRST_MINUTE_MISSING",
        )
    return result


def _has_delayed_overnight_supplemental_record(symbol: str, start: datetime, *, path: Path) -> bool:
    end = start + timedelta(minutes=30)
    for row in load_price_cache(path):
        if _normalize_symbol(row.get("symbol")) != symbol:
            continue
        if str(row.get("event_type") or "").strip().upper() not in {EVENT_OVERNIGHT_FIRST_1M_CLOSE, EVENT_TRADINGVIEW_1M_BAR}:
            continue
        timestamp = parse_et_datetime(row.get("timestamp_et"))
        close = _number(row.get("close"))
        if timestamp is None or close is None or close <= 0:
            continue
        if start + timedelta(minutes=1) <= timestamp < end:
            return True
    return False


def upsert_manual_overnight_price(
    *,
    symbol: str,
    timestamp_et: str | datetime,
    close: Any,
    source: str,
    note: str = "",
    path: Path = DEFAULT_TRADINGVIEW_CACHE_PATH,
) -> dict[str, Any]:
    return upsert_price_event(
        symbol=symbol,
        event_type=EVENT_OVERNIGHT_FIRST_1M_CLOSE,
        timestamp_et=timestamp_et,
        close=close,
        provider=PROVIDER_MANUAL_OVERNIGHT,
        source_type=SOURCE_TYPE_MANUAL_OVERNIGHT,
        source=source,
        note=note,
        path=path,
    )


def webhook_status_summary(path: Path = DEFAULT_TRADINGVIEW_CACHE_PATH) -> dict[str, Any]:
    records = [
        row
        for row in load_price_cache(path)
        if str(row.get("provider") or "").strip().upper() == PROVIDER_TRADINGVIEW_WEBHOOK
    ]
    latest = _latest_record(records)
    latest_p0 = _latest_record([row for row in records if str(row.get("event_type") or "").upper() == EVENT_FRIDAY_AFTERHOURS_CLOSE])
    latest_p2 = _latest_record([row for row in records if str(row.get("event_type") or "").upper() == EVENT_OVERNIGHT_FIRST_1M_CLOSE])
    return {
        "secret_configured": tradingview_webhook_secret_configured(),
        "latest_symbol": str((latest or {}).get("symbol") or ""),
        "latest_p0": latest_p0,
        "latest_p2": latest_p2,
        "latest_write_ok": bool(latest),
    }


def scan_tradingview_csv_dir(directory: str | Path = DEFAULT_TRADINGVIEW_CSV_DIR) -> list[dict[str, Any]]:
    root = Path(directory)
    if not root.exists():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.csv")):
        symbol = symbol_from_csv_filename(path.name)
        results.append(
            {
                "file": str(path),
                "filename": path.name,
                "symbol": symbol or "",
                "status": "可导入" if symbol else "无法识别股票代码",
            }
        )
    return results


def import_tradingview_csv_dir(
    directory: str | Path = DEFAULT_TRADINGVIEW_CSV_DIR,
    *,
    cache_path: Path = DEFAULT_TRADINGVIEW_CACHE_PATH,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in scan_tradingview_csv_dir(directory):
        file_path = Path(str(row.get("file") or ""))
        if not row.get("symbol"):
            results.append({**row, "imported_rows": 0})
            continue
        results.append(import_tradingview_csv_file(file_path, cache_path=cache_path))
    return results


def import_tradingview_csv_file(
    path: str | Path,
    *,
    cache_path: Path = DEFAULT_TRADINGVIEW_CACHE_PATH,
) -> dict[str, Any]:
    file_path = Path(path)
    symbol = symbol_from_csv_filename(file_path.name)
    if not symbol:
        return {"file": str(file_path), "filename": file_path.name, "symbol": "", "status": "无法识别股票代码", "imported_rows": 0}
    try:
        df = pd.read_csv(file_path)
    except Exception as exc:
        return {"file": str(file_path), "filename": file_path.name, "symbol": symbol, "status": f"CSV 读取失败：{type(exc).__name__}", "imported_rows": 0}
    columns = {str(col).strip().lower(): col for col in df.columns}
    time_col = _first_existing(columns, ["time", "datetime", "date", "timestamp"])
    close_col = _first_existing(columns, ["close"])
    if time_col is None or close_col is None:
        return {"file": str(file_path), "filename": file_path.name, "symbol": symbol, "status": "缺少时间列或 close 列", "imported_rows": 0}
    open_col = _first_existing(columns, ["open"])
    high_col = _first_existing(columns, ["high"])
    low_col = _first_existing(columns, ["low"])
    volume_col = _first_existing(columns, ["volume"])
    pending: list[dict[str, Any]] = []
    for _, item in df.iterrows():
        ts = parse_et_datetime(item.get(time_col))
        close = _number(item.get(close_col))
        if ts is None or close is None or close <= 0:
            continue
        pending.append(
            {
                "symbol": symbol,
                "event_type": EVENT_TRADINGVIEW_1M_BAR,
                "timestamp_et": ts.astimezone(ET).isoformat(),
                "close": close,
                "open": _number(item.get(open_col)) if open_col is not None else None,
                "high": _number(item.get(high_col)) if high_col is not None else None,
                "low": _number(item.get(low_col)) if low_col is not None else None,
                "volume": _number(item.get(volume_col)) if volume_col is not None else None,
                "provider": PROVIDER_TRADINGVIEW_CSV,
                "source_type": SOURCE_TYPE_MANUAL_CSV,
                "source": "",
                "source_file": file_path.name,
                "note": "",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    imported = upsert_price_records(pending, cache_path)
    return {
        "file": str(file_path),
        "filename": file_path.name,
        "symbol": symbol,
        "status": "已导入" if imported else "没有可导入行",
        "imported_rows": imported,
    }


def symbol_from_csv_filename(filename: str) -> str:
    stem = Path(filename).stem.upper()
    tokens = [token for token in re.split(r"[^A-Z0-9]+", stem) if token]
    if not tokens:
        return ""
    ignored = {"TV", "NASDAQ", "NYSE", "AMEX", "ARCA", "US", "USD", "1M", "1MIN"}
    for token in tokens:
        if token in ignored or re.fullmatch(r"20\d{2}", token):
            continue
        if re.fullmatch(r"[A-Z]{1,6}", token):
            return token
    return ""


def parse_et_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time(0, 0), ET)
    else:
        text = str(value).strip()
        if not text or text.lower() in {"nan", "nat", "none"}:
            return None
        if re.fullmatch(r"\d+(\.\d+)?", text):
            raw = float(text)
            if raw > 10_000_000_000:
                raw /= 1000.0
            parsed = datetime.fromtimestamp(raw, timezone.utc)
        else:
            normalized = text.replace("Z", "+00:00")
            parsed = None
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                pass
            if parsed is None:
                try:
                    parsed_ts = pd.to_datetime(text, errors="coerce")
                    if pd.isna(parsed_ts):
                        return None
                    parsed = parsed_ts.to_pydatetime()
                except Exception:
                    return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed.astimezone(ET)


def _find_supplemental_price(
    symbol: str,
    event_type: str,
    window_start: datetime,
    window_end: datetime,
    *,
    include_end: bool,
    path: Path,
    missing_reason: str,
) -> SupplementalPrice:
    records = load_price_cache(path)
    candidates: list[dict[str, Any]] = []
    raw_bar_candidates: list[dict[str, Any]] = []
    for row in records:
        if _normalize_symbol(row.get("symbol")) != symbol:
            continue
        timestamp = parse_et_datetime(row.get("timestamp_et"))
        close = _number(row.get("close"))
        if timestamp is None or close is None or close <= 0:
            continue
        in_window = window_start <= timestamp < window_end or (include_end and timestamp == window_end)
        if not in_window:
            continue
        current_event = str(row.get("event_type") or "").strip().upper()
        if current_event == event_type:
            candidates.append(row)
        elif current_event == EVENT_TRADINGVIEW_1M_BAR:
            raw_bar_candidates.append(row)
    chosen = _choose_priority_record(candidates) or _choose_priority_record(raw_bar_candidates)
    if chosen:
        ts = parse_et_datetime(chosen.get("timestamp_et"))
        provider = str(chosen.get("provider") or "").strip().upper()
        source_type = str(chosen.get("source_type") or "").strip().upper()
        return SupplementalPrice(
            ok=True,
            symbol=symbol,
            event_type=event_type,
            close=_number(chosen.get("close")),
            timestamp_et=ts,
            provider=provider,
            source_type=source_type,
            quality=_quality_for_provider(provider, source_type),
            source_file=str(chosen.get("source_file") or ""),
            note=str(chosen.get("note") or ""),
        )
    if raw_bar_candidates:
        reason = "CSV 未覆盖夜盘首分钟" if event_type == EVENT_OVERNIGHT_FIRST_1M_CLOSE else missing_reason
    else:
        reason = missing_reason
    return SupplementalPrice(
        ok=False,
        symbol=symbol,
        event_type=event_type,
        close=None,
        timestamp_et=None,
        provider="",
        source_type="",
        quality="MISSING_SUPPLEMENTAL_PRICE",
        reason=reason,
    )


def _choose_priority_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not records:
        return None
    priority = {
        PROVIDER_TRADINGVIEW_WEBHOOK: 4,
        PROVIDER_TRADINGVIEW_CSV: 3,
        PROVIDER_MANUAL_OVERNIGHT: 2,
        PROVIDER_MANUAL_AFTERHOURS: 2,
    }
    return sorted(
        records,
        key=lambda row: (
            priority.get(str(row.get("provider") or "").strip().upper(), 0),
            parse_et_datetime(row.get("timestamp_et")) or datetime.min.replace(tzinfo=ET),
        ),
    )[-1]


def _quality_for_provider(provider: str, source_type: str) -> str:
    provider = str(provider or "").strip().upper()
    source_type = str(source_type or "").strip().upper()
    if provider == PROVIDER_TRADINGVIEW_WEBHOOK:
        return "TRADINGVIEW_WEBHOOK_SAMPLE"
    if provider == PROVIDER_TRADINGVIEW_CSV:
        return "TRADINGVIEW_CSV_SAMPLE"
    if provider == PROVIDER_MANUAL_OVERNIGHT or source_type == SOURCE_TYPE_MANUAL_OVERNIGHT:
        return "MANUAL_BROKER_SAMPLE"
    if provider == PROVIDER_MANUAL_AFTERHOURS or source_type == SOURCE_TYPE_MANUAL_AFTERHOURS:
        return "MANUAL_AFTERHOURS_SAMPLE"
    return provider or source_type or "SUPPLEMENTAL_SAMPLE"


def _latest_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not records:
        return None
    return sorted(records, key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""))[-1]


def _record_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            _normalize_symbol(row.get("symbol")),
            str(row.get("event_type") or "").strip().upper(),
            str(row.get("timestamp_et") or "").strip(),
            str(row.get("provider") or "").strip().upper(),
        ]
    )


def _first_existing(columns: dict[str, str], names: list[str]) -> str | None:
    for name in names:
        if name in columns:
            return columns[name]
    return None


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


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
