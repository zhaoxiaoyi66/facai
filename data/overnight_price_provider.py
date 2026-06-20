from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from data.providers import get_secret


class AlpacaBoatsOvernightProvider:
    provider_name = "ALPACA_BOATS"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str = "https://data.alpaca.markets",
        timeout_seconds: float = 12.0,
        now_provider: Any | None = None,
    ) -> None:
        self.api_key = api_key or get_secret("ALPACA_API_KEY_ID") or get_secret("ALPACA_API_KEY")
        self.api_secret = api_secret or get_secret("ALPACA_API_SECRET_KEY") or get_secret("ALPACA_SECRET_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.now_provider = now_provider
        self.last_error_reason = ""
        self.last_request_meta: dict[str, Any] = {}
        self.last_delay_suspected = False

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def get_overnight_bars(
        self,
        symbol: str,
        *,
        start_time_ms: int,
        end_time_ms: int,
        interval: str = "1m",
    ) -> list[dict[str, Any]]:
        if not self.is_configured:
            return []
        timeframe = "1Min" if str(interval or "").lower() in {"1m", "1min", "1 min"} else str(interval)
        start_dt = datetime.fromtimestamp(int(start_time_ms) / 1000, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(int(end_time_ms) / 1000, tz=timezone.utc)
        now_utc = self._now_utc()
        self.last_delay_suspected = now_utc < start_dt + timedelta(minutes=15)
        params = {
            "timeframe": timeframe,
            "start": _iso_from_ms(start_time_ms),
            "end": _iso_from_ms(end_time_ms),
            "adjustment": "raw",
            "feed": "boats",
            "sort": "asc",
            "limit": "10000",
        }
        self.last_request_meta = {
            "feed": "boats",
            "timeframe": timeframe,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "delay_suspected": self.last_delay_suspected,
        }
        payload = self._get_json(f"/v2/stocks/{str(symbol or '').strip().upper()}/bars", params)
        bars = payload.get("bars") if isinstance(payload, dict) else []
        if bars:
            self.last_error_reason = ""
        elif not self.last_error_reason:
            self.last_error_reason = "BOATS_DELAY_PENDING" if self.last_delay_suspected else "MISSING_BOATS_FIRST_1M"
        return [_alpaca_bar_to_broker_row(row) for row in bars or []]

    def _get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        query = urlencode(params)
        request = Request(
            f"{self.base_url}/{path.lstrip('/')}?{query}",
            headers={
                "APCA-API-KEY-ID": self.api_key or "",
                "APCA-API-SECRET-KEY": self.api_secret or "",
                "User-Agent": "facai-weekend-spread/1.0",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                self.last_error_reason = ""
                return json.loads(response.read().decode("utf-8") or "{}")
        except HTTPError as exc:
            self.last_error_reason = "NO_PERMISSION" if exc.code in {401, 403} else f"PROVIDER_ERROR:{exc.code}"
            return {}
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            self.last_error_reason = f"PROVIDER_ERROR:{type(exc).__name__}"
            return {}

    def _now_utc(self) -> datetime:
        if self.now_provider is not None:
            value = self.now_provider()
            if isinstance(value, datetime):
                return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)


class JsonFileOvernightProvider:
    provider_name = "IBKR_OVERNIGHT"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def get_overnight_bars(
        self,
        symbol: str,
        *,
        start_time_ms: int,
        end_time_ms: int,
        interval: str = "1m",
    ) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        except (OSError, json.JSONDecodeError):
            return []
        rows = payload.get(str(symbol or "").strip().upper()) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]


def default_overnight_price_provider() -> Any | None:
    selected = str(get_secret("OVERNIGHT_PRICE_PROVIDER") or "").strip().upper()
    if not selected:
        return None
    if selected == "ALPACA_BOATS":
        provider = AlpacaBoatsOvernightProvider()
        return provider if provider.is_configured else None
    if selected == "IBKR_OVERNIGHT":
        bars_path = get_secret("IBKR_OVERNIGHT_BARS_PATH")
        return JsonFileOvernightProvider(bars_path) if bars_path else None
    return None


def overnight_provider_config_status() -> dict[str, Any]:
    selected = str(get_secret("OVERNIGHT_PRICE_PROVIDER") or "").strip().upper()
    alpaca_key = bool(get_secret("ALPACA_API_KEY_ID") or get_secret("ALPACA_API_KEY"))
    alpaca_secret = bool(get_secret("ALPACA_API_SECRET_KEY") or get_secret("ALPACA_SECRET_KEY"))
    ibkr_path = str(get_secret("IBKR_OVERNIGHT_BARS_PATH") or "").strip()
    ibkr_exists = bool(ibkr_path and Path(ibkr_path).exists())
    return {
        "selected_provider": selected,
        "provider_display": selected or "未配置",
        "alpaca_configured": alpaca_key and alpaca_secret,
        "alpaca_key_exists": alpaca_key,
        "alpaca_secret_exists": alpaca_secret,
        "ibkr_configured": bool(ibkr_path),
        "ibkr_path": ibkr_path,
        "ibkr_path_exists": ibkr_exists,
    }


def build_overnight_provider_self_check(
    symbol: str = "NVDA",
    *,
    provider: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    from data.weekend_spread_backtest import get_first_valid_stock_bar_after_weekend, recent_weekend_windows

    config = overnight_provider_config_status()
    selected = str(config.get("selected_provider") or "").strip().upper()
    session_start_et = recent_weekend_windows(weeks=1, now=now)[0].end_et
    selected_provider = provider if provider is not None else default_overnight_price_provider()
    result: dict[str, Any]
    reason = ""
    if not selected:
        selected_provider = None
        reason = "美股夜盘数据源未配置"
    elif selected == "ALPACA_BOATS" and not config.get("alpaca_configured"):
        selected_provider = None
        reason = "API key 缺失"
    elif selected == "IBKR_OVERNIGHT" and not config.get("ibkr_configured"):
        selected_provider = None
        reason = "美股夜盘数据源未配置"
    elif selected not in {"ALPACA_BOATS", "IBKR_OVERNIGHT"}:
        selected_provider = None
        reason = "provider 报错：不支持的夜盘数据源"
    if selected_provider is None:
        if not selected or selected == "IBKR_OVERNIGHT":
            reason = "美股夜盘数据源未配置"
        elif selected == "ALPACA_BOATS":
            reason = "API key 缺失"
        else:
            reason = "provider 报错：不支持的夜盘数据源"
        result = {
            "ok": False,
            "requested_start": session_start_et.isoformat(),
            "requested_end": (session_start_et + timedelta(minutes=1)).isoformat(),
            "returned_bar_count": 0,
            "timestamp": "",
            "price": None,
            "provider": selected,
            "quality": "OVERNIGHT_PROVIDER_MISSING",
        }
    else:
        try:
            result = get_first_valid_stock_bar_after_weekend(
                str(symbol or "NVDA"),
                (session_start_et + timedelta(days=1)).date(),
                "overnight",
                15,
                broker_provider=selected_provider,
                anchor_source={},
                allow_anchor_fallback=False,
                require_exact_start=False,
            )
        except Exception as exc:
            result = {
                "ok": False,
                "requested_start": session_start_et.isoformat(),
                "requested_end": (session_start_et + timedelta(minutes=1)).isoformat(),
                "returned_bar_count": 0,
                "timestamp": "",
                "price": None,
                "provider": selected,
                "quality": "PROVIDER_ERROR",
            }
            reason = f"provider 报错：{type(exc).__name__}"
    if not reason:
        provider_error = str(getattr(selected_provider, "last_error_reason", "") or "")
        quality = str(result.get("quality") or "").strip().upper()
        if provider_error == "BOATS_DELAY_PENDING" or quality == "BOATS_DELAY_PENDING":
            reason = "BOATS 历史数据可能延迟，请 15 分钟后重试。"
        elif provider_error == "NO_PERMISSION" or quality == "ALPACA_BOATS_PERMISSION":
            reason = "Alpaca BOATS 权限不足，可能需要 Algo Trader Plus。"
        elif provider_error == "MISSING_BOATS_FIRST_1M" or quality == "MISSING_BOATS_FIRST_1M":
            reason = "缺少 BOATS 夜盘首分钟 1m K线。"
        elif provider_error.startswith("PROVIDER_ERROR"):
            reason = "provider 报错"
        elif quality == "OVERNIGHT_PROVIDER_MISSING":
            reason = "美股夜盘数据源未配置"
        elif not result.get("ok"):
            reason = "缺少美股夜盘首分钟 1m K线"
    raw_returned_bar_count = int(result.get("raw_returned_bar_count") or result.get("returned_bar_count") or 0)
    raw_first_bar_time = str(result.get("first_raw_bar_time") or result.get("raw_first_bar_time") or "")
    raw_first_bar_close = result.get("first_raw_bar_close", result.get("raw_first_bar_close"))
    raw_first_bar_time_et = str(result.get("first_raw_bar_time_et") or "")
    selected_bar_time = str(result.get("selected_bar_time") or result.get("bar_start_et") or result.get("timestamp") or "")
    selected_bar_close = result.get("selected_bar_close", result.get("price"))
    first_bar_close = result.get("overnight_first_1m_close") or result.get("price")
    first_minute_hit = bool(result.get("hit_first_minute"))
    opening_window_hit = bool(result.get("hit_opening_window") or result.get("ok"))
    delay_minutes = result.get("p2_delay_minutes")
    if opening_window_hit and not first_minute_hit:
        reason = "已读取夜盘开盘窗口内首个有效价，但不是 20:00 首分钟"
    elif not first_minute_hit and raw_first_bar_time:
        reason = "未命中夜盘首分钟，不可作为 P2"
    elif not first_minute_hit and not reason:
        reason = "夜盘首分钟无有效 1m K线，不适合开盘第一时间平单"
    if not opening_window_hit:
        if raw_first_bar_time:
            reason = "返回了后续 bar，但未命中夜盘首分钟"
        elif not reason:
            reason = "夜盘首分钟无有效 1m K线，不适合开盘第一时间平单"
    request_meta = dict(getattr(selected_provider, "last_request_meta", {}) or {})
    delay_suspected = bool(getattr(selected_provider, "last_delay_suspected", False) or request_meta.get("delay_suspected"))
    strict_p2_conclusion = (
        "命中夜盘首分钟，可作为 P2"
        if first_minute_hit
        else ("未命中夜盘首分钟，不可作为 P2" if raw_returned_bar_count else reason)
    )
    return {
        "ok": bool(result.get("ok")),
        "symbol": str(symbol or "NVDA").strip().upper(),
        "selected_provider": selected,
        "provider_display": selected or "未配置",
        "alpaca_configured": bool(config.get("alpaca_configured")),
        "ibkr_configured": bool(config.get("ibkr_configured")),
        "ibkr_path_exists": bool(config.get("ibkr_path_exists")),
        "requested_start": str(result.get("requested_start") or session_start_et.isoformat()),
        "requested_end": str(result.get("requested_end") or ""),
        "returned_bar_count": raw_returned_bar_count,
        "raw_returned_bar_count": raw_returned_bar_count,
        "first_bar_time": selected_bar_time if first_minute_hit else raw_first_bar_time,
        "first_bar_close": first_bar_close if first_bar_close is not None else (raw_first_bar_close if raw_first_bar_close is not None else ""),
        "raw_first_bar_time": raw_first_bar_time,
        "raw_first_bar_close": raw_first_bar_close if raw_first_bar_close is not None else "",
        "first_raw_bar_time": raw_first_bar_time,
        "first_raw_bar_close": raw_first_bar_close if raw_first_bar_close is not None else "",
        "first_raw_bar_time_et": raw_first_bar_time_et,
        "selected_bar_time": selected_bar_time if opening_window_hit else "",
        "selected_bar_close": selected_bar_close if opening_window_hit else "",
        "hit_first_minute": first_minute_hit,
        "hit_opening_window": opening_window_hit,
        "first_minute_hit": first_minute_hit,
        "opening_window_hit": opening_window_hit,
        "p2_delay_minutes": "" if delay_minutes is None else delay_minutes,
        "p2_sample_quality": str(result.get("p2_sample_quality") or ""),
        "strict_p2_conclusion": strict_p2_conclusion,
        "provider": str(result.get("provider") or selected or ""),
        "feed": str(request_meta.get("feed") or ("boats" if selected == "ALPACA_BOATS" else "")),
        "timeframe": str(request_meta.get("timeframe") or ("1Min" if selected == "ALPACA_BOATS" else "1m")),
        "boats_delay_suspected": delay_suspected,
        "quality": str(result.get("quality") or ""),
        "reason": reason,
    }


def _iso_from_ms(value: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()


def _alpaca_field(row: Any, *names: str) -> Any:
    if isinstance(row, dict):
        for name in names:
            if name in row:
                return row.get(name)
        return None
    for name in names:
        if hasattr(row, name):
            return getattr(row, name)
    return None


def _alpaca_bar_to_broker_row(row: Any) -> dict[str, Any]:
    close = _alpaca_field(row, "c", "close")
    return {
        "ts": _alpaca_field(row, "t", "timestamp", "time"),
        "bid": close,
        "ask": close,
        "close": close,
        "volume": _alpaca_field(row, "v", "volume"),
        "quote_age_seconds": 0,
        "source": "ALPACA_BOATS",
    }
