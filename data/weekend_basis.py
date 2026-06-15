from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


BASIS_STATES = {
    "OBSERVE",
    "ENTRY_CANDIDATE",
    "SHORT_OPEN",
    "WAIT_BROKER_OPEN",
    "HEDGE_DUE",
    "HEDGE_LOCKED",
    "EXIT_READY",
    "CLOSED",
    "FAILED",
}


@dataclass(frozen=True)
class BasisStrategyConfig:
    entry_rule: str = "RELATIVE_HIGH_PULLBACK"
    min_entry_premium_bps: float = 120.0
    allowed_pullback_bps: float = 40.0
    min_pullback_bps: float = 8.0
    max_pullback_bps: float = 80.0
    min_percentile: float = 70.0
    max_binance_spread_bps: float = 35.0
    max_broker_spread_bps: float = 35.0
    max_alignment_seconds: int = 60
    min_depth_usd: float = 0.0
    fees_bps: float = 0.0
    funding_bps: float = 0.0
    slippage_bps: float = 0.0


@dataclass(frozen=True)
class BasisQuote:
    ts: datetime
    bid: float
    ask: float
    bid_size: float | None = None
    ask_size: float | None = None
    depth_usd: float | None = None
    source: str = ""
    estimated: bool = False

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * 10_000.0 if self.mid > 0 else 999_999.0


@dataclass(frozen=True)
class BrokerOvernightBar:
    ts: datetime
    bid: float
    ask: float
    quote_age_seconds: float = 0.0
    volume: float | None = None
    source: str = ""

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * 10_000.0 if self.mid > 0 else 999_999.0


def evaluate_basis_lock_strategy(
    *,
    ticker: str,
    binance_symbol: str,
    mapping_confidence: str,
    broker_anchor_price: float | None,
    binance_quotes: Iterable[BasisQuote],
    broker_overnight_bars: Iterable[BrokerOvernightBar] | None = None,
    config: BasisStrategyConfig | None = None,
) -> dict[str, Any]:
    cfg = config or BasisStrategyConfig()
    quotes = sorted([quote for quote in binance_quotes if _valid_quote(quote)], key=lambda item: item.ts)
    broker_bars = sorted([bar for bar in broker_overnight_bars or [] if _valid_broker_bar(bar)], key=lambda item: item.ts)
    anchor = _number(broker_anchor_price)
    warnings: list[str] = []
    data_quality = "OK"
    state = "OBSERVE"

    result = _base_result(ticker, binance_symbol, mapping_confidence)
    if anchor is None or anchor <= 0:
        result.update({"status": "FAILED", "data_quality": "NO_PRICE_ANCHOR", "warning": "缺少 broker anchor price"})
        return result
    result["broker_anchor_price"] = anchor
    if str(mapping_confidence or "").lower() != "confirmed":
        data_quality = "UNCONFIRMED_MAPPING"
        warnings.append("映射未确认，仅观察，不能作为正式交易信号")
    if not quotes:
        result.update({"status": "FAILED", "data_quality": "BINANCE_KLINE_UNAVAILABLE", "warning": "缺少 Binance bid/ask 报价"})
        return result
    if any(quote.estimated for quote in quotes):
        if data_quality == "OK":
            data_quality = "ESTIMATED_EXECUTION"
        warnings.append("Binance bid/ask 来自估算，只能观察")

    oracle = _oracle_observation(quotes, anchor)
    result.update(oracle)

    entry = _find_entry(quotes, anchor, cfg)
    if entry is None:
        result.update(
            {
                "status": state,
                "data_quality": data_quality,
                "warning": _join_warnings(warnings or ["未出现可执行 relative high 入场信号"]),
            }
        )
        return result

    state = "SHORT_OPEN"
    entry_quote = entry["quote"]
    result.update(
        {
            "entry_rule": cfg.entry_rule,
            "entry_ts": entry_quote.ts.isoformat(),
            "binance_entry_bid": entry_quote.bid,
            "entry_premium_bps": entry["premium_bps"],
            "relative_high_rank": entry["percentile"],
            "pullback_bps": entry["pullback_bps"],
            "rolling_high_premium_bps": entry["rolling_high_premium_bps"],
        }
    )

    if not broker_bars:
        result.update(
            {
                "status": "WAIT_BROKER_OPEN",
                "data_quality": "NO_BROKER_OVERNIGHT_BAR",
                "warning": _join_warnings(warnings + ["缺少券商 overnight 第一根有效 1m bar"]),
            }
        )
        return result

    hedge_bar = _first_valid_broker_bar(broker_bars, cfg)
    if hedge_bar is None:
        result.update(
            {
                "status": "HEDGE_DUE",
                "data_quality": "NO_BROKER_OVERNIGHT_BAR",
                "warning": _join_warnings(warnings + ["没有符合 spread/quote_age 要求的券商 overnight bar"]),
            }
        )
        return result

    aligned_quote = _nearest_quote(quotes, hedge_bar.ts, max_seconds=cfg.max_alignment_seconds)
    if aligned_quote is None:
        result.update(
            {
                "status": "HEDGE_DUE",
                "data_quality": "STALE_OR_MISALIGNED",
                "warning": _join_warnings(warnings + ["Binance 与 broker 对冲时间差超过 60 秒"]),
            }
        )
        return result

    gross_locked_bps = (entry_quote.bid / hedge_bar.ask - 1.0) * 10_000.0
    net_locked_bps = gross_locked_bps - cfg.fees_bps - cfg.funding_bps - cfg.slippage_bps
    residual_basis_bps = (aligned_quote.mid / hedge_bar.mid - 1.0) * 10_000.0
    if entry_quote.spread_bps > cfg.max_binance_spread_bps or hedge_bar.spread_bps > cfg.max_broker_spread_bps:
        warnings.append("WIDE_SPREAD")
        if data_quality == "OK":
            data_quality = "WIDE_SPREAD"
    if _low_depth(entry_quote, cfg):
        warnings.append("LOW_DEPTH")
        if data_quality == "OK":
            data_quality = "LOW_DEPTH"

    result.update(
        {
            "status": "HEDGE_LOCKED",
            "hedge_ts": hedge_bar.ts.isoformat(),
            "broker_hedge_bid": hedge_bar.bid,
            "broker_hedge_ask": hedge_bar.ask,
            "broker_hedge_mid": hedge_bar.mid,
            "binance_same_min_bid": aligned_quote.bid,
            "binance_same_min_ask": aligned_quote.ask,
            "binance_same_min_mid": aligned_quote.mid,
            "gross_locked_bps": gross_locked_bps,
            "net_locked_bps": net_locked_bps,
            "residual_basis_bps": residual_basis_bps,
            "realized_pnl_bps": None,
            "data_quality": data_quality,
            "warning": _join_warnings(warnings),
        }
    )
    return result


def normalize_basis_quotes(rows: Iterable[Any], *, estimated: bool = False, source: str = "") -> list[BasisQuote]:
    quotes: list[BasisQuote] = []
    for row in rows:
        quote = _basis_quote_from_row(row, estimated=estimated, source=source)
        if quote is not None:
            quotes.append(quote)
    deduped: dict[datetime, BasisQuote] = {quote.ts: quote for quote in quotes}
    return [deduped[key] for key in sorted(deduped)]


def normalize_broker_overnight_bars(rows: Iterable[Any]) -> list[BrokerOvernightBar]:
    bars: list[BrokerOvernightBar] = []
    for row in rows:
        bar = _broker_bar_from_row(row)
        if bar is not None:
            bars.append(bar)
    deduped: dict[datetime, BrokerOvernightBar] = {bar.ts: bar for bar in bars}
    return [deduped[key] for key in sorted(deduped)]


def _find_entry(quotes: list[BasisQuote], anchor: float, cfg: BasisStrategyConfig) -> dict[str, Any] | None:
    premiums: list[float] = []
    rolling_high = -999_999.0
    for quote in quotes:
        premium = (quote.bid / anchor - 1.0) * 10_000.0
        premiums.append(premium)
        rolling_high = max(rolling_high, premium)
        pullback = rolling_high - premium
        percentile = _percentile_rank(premiums, premium)
        if not _quote_liquidity_pass(quote, cfg):
            continue
        if cfg.entry_rule == "LIMIT_AT_TARGET_PREMIUM" and premium >= cfg.min_entry_premium_bps:
            return _entry_payload(quote, premium, pullback, rolling_high, percentile)
        if cfg.entry_rule != "RELATIVE_HIGH_PULLBACK":
            continue
        if rolling_high < cfg.min_entry_premium_bps:
            continue
        if premium < cfg.min_entry_premium_bps - cfg.allowed_pullback_bps:
            continue
        if pullback < cfg.min_pullback_bps or pullback > cfg.max_pullback_bps:
            continue
        if percentile < cfg.min_percentile:
            continue
        return _entry_payload(quote, premium, pullback, rolling_high, percentile)
    return None


def _entry_payload(quote: BasisQuote, premium: float, pullback: float, rolling_high: float, percentile: float) -> dict[str, Any]:
    return {
        "quote": quote,
        "premium_bps": premium,
        "pullback_bps": pullback,
        "rolling_high_premium_bps": rolling_high,
        "percentile": percentile,
    }


def _oracle_observation(quotes: list[BasisQuote], anchor: float) -> dict[str, Any]:
    best = max(quotes, key=lambda quote: (quote.bid / anchor - 1.0) * 10_000.0)
    return {
        "oracle_weekend_high_bid": best.bid,
        "oracle_weekend_high_time": best.ts.isoformat(),
        "oracle_weekend_high_premium_bps": (best.bid / anchor - 1.0) * 10_000.0,
        "oracle_note": "事后高点，不可交易",
    }


def _first_valid_broker_bar(bars: list[BrokerOvernightBar], cfg: BasisStrategyConfig) -> BrokerOvernightBar | None:
    for bar in bars:
        if bar.quote_age_seconds > cfg.max_alignment_seconds:
            continue
        if bar.spread_bps > cfg.max_broker_spread_bps:
            continue
        return bar
    return None


def _nearest_quote(quotes: list[BasisQuote], target: datetime, *, max_seconds: int) -> BasisQuote | None:
    target_utc = _ensure_utc(target)
    nearest = min(quotes, key=lambda quote: abs((quote.ts - target_utc).total_seconds()))
    return nearest if abs((nearest.ts - target_utc).total_seconds()) <= max_seconds else None


def _basis_quote_from_row(row: Any, *, estimated: bool, source: str) -> BasisQuote | None:
    if isinstance(row, BasisQuote):
        return row
    if not isinstance(row, dict):
        return None
    ts = _parse_time(row.get("ts") or row.get("timestamp") or row.get("time") or row.get("open_time"))
    bid = _number(row.get("bid") or row.get("bid_price") or row.get("bidPrice"))
    ask = _number(row.get("ask") or row.get("ask_price") or row.get("askPrice"))
    if ts is None or bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return BasisQuote(
        ts=ts,
        bid=bid,
        ask=ask,
        bid_size=_number(row.get("bid_size") or row.get("bidSize")),
        ask_size=_number(row.get("ask_size") or row.get("askSize")),
        depth_usd=_number(row.get("depth_usd") or row.get("depthUsd")),
        source=str(row.get("source") or source),
        estimated=bool(row.get("estimated_execution") or estimated),
    )


def _broker_bar_from_row(row: Any) -> BrokerOvernightBar | None:
    if isinstance(row, BrokerOvernightBar):
        return row
    if not isinstance(row, dict):
        return None
    ts = _parse_time(row.get("ts") or row.get("timestamp") or row.get("time") or row.get("open_time"))
    bid = _number(row.get("bid") or row.get("bid_price") or row.get("bidPrice"))
    ask = _number(row.get("ask") or row.get("ask_price") or row.get("askPrice"))
    if ts is None or bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return BrokerOvernightBar(
        ts=ts,
        bid=bid,
        ask=ask,
        quote_age_seconds=float(_number(row.get("quote_age_seconds") or row.get("quoteAgeSeconds")) or 0.0),
        volume=_number(row.get("volume")),
        source=str(row.get("source") or ""),
    )


def _quote_liquidity_pass(quote: BasisQuote, cfg: BasisStrategyConfig) -> bool:
    if quote.spread_bps > cfg.max_binance_spread_bps:
        return False
    return not _low_depth(quote, cfg)


def _low_depth(quote: BasisQuote, cfg: BasisStrategyConfig) -> bool:
    return quote.depth_usd is not None and quote.depth_usd < cfg.min_depth_usd


def _valid_quote(quote: BasisQuote) -> bool:
    return quote.bid > 0 and quote.ask > 0 and quote.ask >= quote.bid


def _valid_broker_bar(bar: BrokerOvernightBar) -> bool:
    return bar.bid > 0 and bar.ask > 0 and bar.ask >= bar.bid


def _percentile_rank(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    return sum(1 for item in values if item <= value) / len(values) * 100.0


def _base_result(ticker: str, symbol: str, mapping_confidence: str) -> dict[str, Any]:
    return {
        "ticker": str(ticker or "").upper(),
        "binance_symbol": str(symbol or "").upper(),
        "mapping_confidence": str(mapping_confidence or ""),
        "status": "OBSERVE",
        "broker_anchor_price": None,
        "entry_rule": "",
        "entry_ts": "",
        "binance_entry_bid": None,
        "entry_premium_bps": None,
        "relative_high_rank": None,
        "pullback_bps": None,
        "rolling_high_premium_bps": None,
        "hedge_ts": "",
        "broker_hedge_bid": None,
        "broker_hedge_ask": None,
        "broker_hedge_mid": None,
        "binance_same_min_bid": None,
        "binance_same_min_ask": None,
        "binance_same_min_mid": None,
        "gross_locked_bps": None,
        "net_locked_bps": None,
        "residual_basis_bps": None,
        "realized_pnl_bps": None,
        "oracle_weekend_high_bid": None,
        "oracle_weekend_high_time": "",
        "oracle_weekend_high_premium_bps": None,
        "oracle_note": "",
        "data_quality": "OK",
        "warning": "",
    }


def _join_warnings(items: list[str]) -> str:
    return "；".join(dict.fromkeys([item for item in items if item]))


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_utc(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        number = _number(value)
        if number is None:
            return None
        parsed = datetime.fromtimestamp(number / 1000.0, timezone.utc)
    return _ensure_utc(parsed)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
