from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.binance_provider import BinanceHTTPPriceProvider, BinancePriceProvider
from data.weekend_spread import DEFAULT_LOCAL_MAPPING_PATH, DEFAULT_MAPPING_PATH, load_binance_symbol_mapping


def run_smoke(
    mapping_path: Path = DEFAULT_LOCAL_MAPPING_PATH,
    *,
    provider: BinancePriceProvider | None = None,
) -> dict[str, Any]:
    mapping_path = Path(mapping_path)
    if not mapping_path.exists():
        return {
            "mapping_missing": True,
            "message": (
                f"Local mapping not found. Copy {DEFAULT_MAPPING_PATH} to {mapping_path}, "
                "then fill manually confirmed ticker -> Binance symbol mappings."
            ),
            "count": 0,
            "results": [],
        }

    mapping = load_binance_symbol_mapping(mapping_path, local_path=None)
    price_provider = provider or BinanceHTTPPriceProvider()
    results: list[dict[str, Any]] = []
    for ticker, config in sorted(mapping.items()):
        if not config.get("enabled", True) or not config.get("binance_symbol"):
            continue
        symbol = str(config.get("binance_symbol") or "").strip().upper()
        market_type = str(config.get("market_type") or "usdm_futures")
        row: dict[str, Any] = {
            "ticker": ticker,
            "binance_symbol": symbol,
            "market_type": market_type,
            "mapping_confidence": str(config.get("mapping_confidence") or "unverified"),
            "exists": False,
            "last_price": None,
            "bid": None,
            "ask": None,
            "bid_ask_spread_pct": None,
            "volume_24h": None,
            "funding_rate": None,
            "updated_at": "",
            "error_message": "",
        }
        try:
            validation = _to_dict(price_provider.validate_symbol(symbol, market_type=market_type))
            row.update(
                {
                    "exists": bool(validation.get("exists")),
                    "validation_status": validation.get("status") or "",
                    "quote_currency": validation.get("quote_currency") or str(config.get("quote_currency") or ""),
                    "base_asset": validation.get("base_asset") or "",
                    "price_available": bool(validation.get("price_available")),
                    "book_available": bool(validation.get("book_available")),
                    "volume_available": bool(validation.get("volume_available")),
                    "funding_available": bool(validation.get("funding_available")),
                    "updated_at": validation.get("updated_at") or "",
                    "error_message": validation.get("error_message") or "",
                }
            )
            if row["exists"]:
                snapshot = _to_dict(price_provider.get_last_price(symbol, market_type=market_type, force_refresh=True))
                row.update(
                    {
                        "last_price": _number(snapshot.get("last_price")),
                        "bid": _number(snapshot.get("bid")),
                        "ask": _number(snapshot.get("ask")),
                        "volume_24h": _number(snapshot.get("volume_24h")),
                        "funding_rate": _number(snapshot.get("funding_rate")),
                        "updated_at": snapshot.get("updated_at") or row["updated_at"],
                        "error_message": snapshot.get("error") or row["error_message"],
                    }
                )
                row["bid_ask_spread_pct"] = _bid_ask_spread_pct(row["bid"], row["ask"])
        except Exception as exc:
            row["error_message"] = f"{type(exc).__name__}: {exc}"
        results.append(row)

    return {
        "mapping_missing": False,
        "warning": "Candidate symbols do not prove real US stock equivalence; manual confirmation is required.",
        "count": len(results),
        "results": results,
    }


def main() -> int:
    print(json.dumps(run_smoke(), ensure_ascii=False, indent=2))
    return 0


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    return dict(getattr(value, "__dict__", {}) or {})


def _bid_ask_spread_pct(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    midpoint = (bid + ask) / 2.0
    if midpoint <= 0:
        return None
    return (ask - bid) / midpoint * 100.0


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
