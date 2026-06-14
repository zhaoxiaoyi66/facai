from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.binance_provider import DEFAULT_SPOT_BASE_URLS, DEFAULT_USDM_BASE_URL


USDM_BASE_URL = os.environ.get("BINANCE_USDM_BASE_URL") or DEFAULT_USDM_BASE_URL
TIMEOUT_SECONDS = float(os.environ.get("BINANCE_DIAG_TIMEOUT_SECONDS") or 4.0)


def run_diagnostics() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for base_url in _spot_base_urls():
        checks.extend(
            [
                _check("spot", base_url, "/api/v3/exchangeInfo", {"symbol": "BTCUSDT"}, expects_symbols=True),
                _check("spot", base_url, "/api/v3/ticker/price", {"symbol": "BTCUSDT"}),
                _check("spot", base_url, "/api/v3/ticker/bookTicker", {"symbol": "BTCUSDT"}),
                _check("spot", base_url, "/api/v3/ticker/24hr", {"symbol": "BTCUSDT"}),
            ]
        )
    checks.extend(
        [
            _check("usdm_futures", USDM_BASE_URL, "/fapi/v1/exchangeInfo", {}, expects_symbols=True),
            _check("usdm_futures", USDM_BASE_URL, "/fapi/v2/ticker/price", {"symbol": "BTCUSDT"}),
            _check("usdm_futures", USDM_BASE_URL, "/fapi/v1/ticker/bookTicker", {"symbol": "BTCUSDT"}),
            _check("usdm_futures", USDM_BASE_URL, "/fapi/v1/ticker/24hr", {"symbol": "BTCUSDT"}),
            _check("usdm_futures", USDM_BASE_URL, "/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"}),
        ]
    )
    return {
        "spot_base_urls": _spot_base_urls(),
        "usdm_base_url": USDM_BASE_URL,
        "timeout_seconds": TIMEOUT_SECONDS,
        "checks": checks,
    }


def main() -> int:
    print(json.dumps(run_diagnostics(), ensure_ascii=False, indent=2))
    return 0


def _check(
    market_type: str,
    base_url: str,
    path: str,
    params: dict[str, str],
    *,
    expects_symbols: bool = False,
) -> dict[str, Any]:
    url = _url(base_url, path, params)
    row = {
        "market_type": market_type,
        "base_url": base_url.rstrip("/"),
        "endpoint": path,
        "url": url,
        "http_status": None,
        "content_type": "",
        "response_size": 0,
        "json_parse_ok": False,
        "has_symbols": False,
        "symbol_count": None,
        "btcusdt_found": False,
        "sample_symbols": [],
        "price_value": None,
        "bid": None,
        "ask": None,
        "volume": None,
        "funding_rate": None,
        "error_type": "OK",
        "error_message": "",
        "raw_response_preview": "",
    }
    try:
        request = Request(url, headers={"User-Agent": "facai-binance-diagnostics/1.1"})
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
            text = raw.decode("utf-8", errors="replace")
            row.update(
                {
                    "http_status": getattr(response, "status", None),
                    "content_type": response.headers.get("Content-Type", ""),
                    "response_size": len(raw),
                    "raw_response_preview": text[:300],
                }
            )
        _parse_payload(row, text, expects_symbols=expects_symbols)
    except HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        text = raw.decode("utf-8", errors="replace")
        row.update(
            {
                "http_status": exc.code,
                "content_type": exc.headers.get("Content-Type", "") if exc.headers else "",
                "response_size": len(raw),
                "error_type": _http_error_type(exc.code),
                "error_message": str(exc),
                "raw_response_preview": text[:300],
            }
        )
    except TimeoutError as exc:
        row.update({"error_type": "TIMEOUT", "error_message": str(exc)})
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        row.update({"error_type": _url_error_type(reason), "error_message": str(reason)})
    except json.JSONDecodeError as exc:
        row.update({"error_type": "JSON_PARSE_ERROR", "error_message": str(exc)})
    except Exception as exc:
        row.update({"error_type": "UNKNOWN_ERROR", "error_message": f"{type(exc).__name__}: {exc}"})
    return row


def _parse_payload(row: dict[str, Any], text: str, *, expects_symbols: bool) -> None:
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        row.update({"json_parse_ok": False, "error_type": "JSON_PARSE_ERROR", "error_message": str(exc)})
        return
    row["json_parse_ok"] = True
    if expects_symbols:
        _parse_symbols_payload(row, payload)
        return
    if not isinstance(payload, dict):
        row.update({"error_type": "SCHEMA_MISMATCH", "error_message": "response is not an object"})
        return
    row["price_value"] = _number(payload.get("price") or payload.get("lastPrice"))
    row["bid"] = _number(payload.get("bidPrice"))
    row["ask"] = _number(payload.get("askPrice"))
    row["volume"] = _number(payload.get("volume"))
    row["funding_rate"] = _number(payload.get("lastFundingRate"))


def _parse_symbols_payload(row: dict[str, Any], payload: Any) -> None:
    if not isinstance(payload, dict) or "symbols" not in payload:
        row.update({"error_type": "SCHEMA_MISMATCH", "error_message": "response missing symbols"})
        return
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        row.update({"error_type": "SCHEMA_MISMATCH", "error_message": "symbols is not a list"})
        return
    row["has_symbols"] = True
    row["symbol_count"] = len(symbols)
    row["sample_symbols"] = [str(item.get("symbol") or "") for item in symbols[:5] if isinstance(item, dict)]
    row["btcusdt_found"] = any(isinstance(item, dict) and str(item.get("symbol") or "").upper() == "BTCUSDT" for item in symbols)
    if not symbols:
        row.update({"error_type": "EMPTY_SYMBOLS", "error_message": "symbols list is empty"})
    elif not row["btcusdt_found"]:
        row.update({"error_type": "SYMBOL_NOT_FOUND", "error_message": "BTCUSDT not found in symbols"})


def _spot_base_urls() -> list[str]:
    urls: list[str] = []
    for name in ("BINANCE_SPOT_DATA_BASE_URL", "BINANCE_SPOT_BASE_URL"):
        raw = os.environ.get(name)
        if raw:
            urls.extend(part.strip().rstrip("/") for part in raw.split(",") if part.strip())
    urls.extend(DEFAULT_SPOT_BASE_URLS)
    result: list[str] = []
    seen: set[str] = set()
    for item in urls:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _http_error_type(status_code: int) -> str:
    if status_code == 451:
        return "HTTP_451_OR_REGION_BLOCKED"
    if status_code == 403:
        return "HTTP_FORBIDDEN"
    if status_code == 429:
        return "RATE_LIMITED"
    return "NETWORK_ERROR"


def _url_error_type(reason: object) -> str:
    text = str(reason).lower()
    if isinstance(reason, TimeoutError) or "timed out" in text or "timeout" in text:
        return "TIMEOUT"
    return "NETWORK_ERROR"


def _url(base_url: str, path: str, params: dict[str, str]) -> str:
    query = f"?{urlencode(params)}" if params else ""
    return f"{base_url.rstrip('/')}{path}{query}"


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
