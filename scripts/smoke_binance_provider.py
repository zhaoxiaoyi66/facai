from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.binance_provider import BinanceHTTPPriceProvider
from data.weekend_spread import load_binance_symbol_mapping


def main() -> int:
    mapping = load_binance_symbol_mapping()
    provider = BinanceHTTPPriceProvider()
    results = []
    for ticker, config in sorted(mapping.items()):
        if not config.get("enabled", True) or not config.get("binance_symbol"):
            continue
        symbol = str(config.get("binance_symbol") or "").strip().upper()
        market_type = str(config.get("market_type") or "usdm_futures")
        validation = provider.validate_symbol(symbol, market_type=market_type)
        snapshot = provider.get_last_price(symbol, market_type=market_type) if validation.exists else None
        results.append(
            {
                "ticker": ticker,
                "symbol": symbol,
                "market_type": market_type,
                "validation": {
                    "exists": validation.exists,
                    "status": validation.status,
                    "quote_currency": validation.quote_currency,
                    "price_available": validation.price_available,
                    "book_available": validation.book_available,
                    "volume_available": validation.volume_available,
                    "funding_available": validation.funding_available,
                    "error_message": validation.error_message,
                },
                "price": None
                if snapshot is None
                else {
                    "last_price": snapshot.last_price,
                    "bid": snapshot.bid,
                    "ask": snapshot.ask,
                    "volume_24h": snapshot.volume_24h,
                    "funding_rate": snapshot.funding_rate,
                    "updated_at": snapshot.updated_at,
                    "error": snapshot.error,
                },
            }
        )
    print(json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
