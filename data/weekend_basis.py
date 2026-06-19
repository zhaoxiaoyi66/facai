from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from settings import PROJECT_ROOT


BASIS_STATES = {
    "OBSERVE",
    "ENTRY_CANDIDATE",
    "ALLOW_SHORT",
    "SHORT_OPEN",
    "WAIT_BROKER_OPEN",
    "HEDGE_DUE",
    "HEDGE_LOCKED",
    "EXIT_READY",
    "CLOSED",
    "FAILED",
    "BLOCK_MAPPING",
    "BLOCK_LIQUIDITY",
    "BLOCK_DATA",
}

DEFAULT_BASIS_TRADES_PATH = PROJECT_ROOT / "data" / "cache" / "weekend_basis_trades.json"


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
    required_net_locked_bps: float = 80.0
    exit_basis_threshold_bps: float = 25.0


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
    close: float | None = None
    quote_age_seconds: float = 0.0
    volume: float | None = None
    source: str = ""

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * 10_000.0 if self.mid > 0 else 999_999.0


@dataclass(frozen=True)
class WeekendBasisMapping:
    ticker: str
    broker_symbol: str
    binance_symbol: str
    mapping_multiplier: float = 1.0
    currency: str = "USD"
    min_qty: float = 0.0
    contract_type: str = "usdm_futures"
    is_confirmed: bool = False
    confirmed_at: str = ""
    confirmed_by: str = ""
    notes: str = ""


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


def normalize_weekend_basis_mapping(ticker: str, raw: dict[str, Any] | None) -> WeekendBasisMapping:
    config = raw or {}
    confidence = str(config.get("mapping_confidence") or "").strip().lower()
    is_confirmed = bool(config.get("is_confirmed")) or confidence == "confirmed"
    return WeekendBasisMapping(
        ticker=str(ticker or config.get("ticker") or "").strip().upper(),
        broker_symbol=str(config.get("broker_symbol") or ticker or "").strip().upper(),
        binance_symbol=str(config.get("binance_symbol") or "").strip().upper(),
        mapping_multiplier=float(_number(config.get("mapping_multiplier") or config.get("unit_multiplier")) or 1.0),
        currency=str(config.get("currency") or config.get("quote_currency") or "USD").strip().upper(),
        min_qty=float(_number(config.get("min_qty")) or 0.0),
        contract_type=str(config.get("contract_type") or config.get("market_type") or "usdm_futures").strip().lower(),
        is_confirmed=is_confirmed,
        confirmed_at=str(config.get("confirmed_at") or ""),
        confirmed_by=str(config.get("confirmed_by") or ""),
        notes=str(config.get("notes") or config.get("risk_note") or ""),
    )


def build_basis_opportunity(
    *,
    ticker: str,
    mapping: dict[str, Any] | WeekendBasisMapping | None,
    broker_anchor_price: float | None,
    binance_quotes: Iterable[BasisQuote | dict[str, Any]],
    broker_overnight_bars: Iterable[BrokerOvernightBar | dict[str, Any]] | None = None,
    now: datetime | None = None,
    config: BasisStrategyConfig | None = None,
) -> dict[str, Any]:
    cfg = config or BasisStrategyConfig()
    normalized_mapping = mapping if isinstance(mapping, WeekendBasisMapping) else normalize_weekend_basis_mapping(ticker, mapping)
    result = _base_live_result(normalized_mapping)
    anchor = _number(broker_anchor_price)
    result["broker_anchor_price"] = anchor
    result["min_binance_short_price"] = _min_binance_short_price(anchor, cfg)
    if anchor is None or anchor <= 0:
        result.update({"status": "BLOCK_DATA", "data_quality": "NO_PRICE_ANCHOR", "warning": "缺少 broker anchor price"})
        return result
    quotes = normalize_basis_quotes(binance_quotes)
    if not quotes:
        result.update({"status": "BLOCK_DATA", "data_quality": "BINANCE_QUOTE_UNAVAILABLE", "warning": "缺少 Binance bid/ask 报价"})
        return result
    current_quote = _latest_quote_at(quotes, now)
    premiums = [(quote.bid / anchor - 1.0) * 10_000.0 for quote in quotes if quote.ts <= current_quote.ts]
    rolling_high = max(premiums) if premiums else None
    current_premium = (current_quote.bid / anchor - 1.0) * 10_000.0
    pullback = (rolling_high - current_premium) if rolling_high is not None else None
    percentile = _percentile_rank(premiums, current_premium)
    max_broker_buy_price = _max_broker_buy_price(current_quote.bid, cfg)
    result.update(
        {
            "status": "OBSERVE",
            "binance_symbol": normalized_mapping.binance_symbol,
            "broker_symbol": normalized_mapping.broker_symbol,
            "binance_entry_bid": current_quote.bid,
            "binance_entry_ask": current_quote.ask,
            "entry_premium_bps": current_premium,
            "rolling_high_premium_bps": rolling_high,
            "relative_high_rank": percentile,
            "pullback_bps": pullback,
            "min_binance_short_price": result["min_binance_short_price"],
            "max_broker_buy_price": max_broker_buy_price,
            "expected_net_locked_bps": None,
        }
    )
    if not _quote_liquidity_pass(current_quote, cfg):
        result.update({"status": "BLOCK_LIQUIDITY", "data_quality": "WIDE_SPREAD" if current_quote.spread_bps > cfg.max_binance_spread_bps else "LOW_DEPTH", "warning": "Binance spread/depth 不满足入场要求"})
        return result
    signal = _current_entry_signal(quotes, current_quote, anchor, cfg)
    if not signal["passes"]:
        result.update({"status": "ENTRY_CANDIDATE" if signal["candidate"] else "OBSERVE", "warning": signal["reason"]})
        return result
    broker_bars = normalize_broker_overnight_bars(broker_overnight_bars or [])
    hedge_bar = _first_valid_broker_bar(broker_bars, cfg) if broker_bars else None
    if hedge_bar is not None:
        result["expected_net_locked_bps"] = (current_quote.bid / hedge_bar.ask - 1.0) * 10_000.0 - cfg.fees_bps - cfg.funding_bps - cfg.slippage_bps
    result.update({"status": "ALLOW_SHORT", "data_quality": "OK", "warning": ""})
    return result


def create_weekend_basis_trade(
    *,
    week_id: str,
    ticker: str,
    mapping: dict[str, Any] | WeekendBasisMapping,
    broker_anchor_price: float,
    binance_entry_bid: float,
    binance_entry_ask: float | None = None,
    binance_short_qty: float = 0.0,
    entry_ts: datetime | str | None = None,
    entry_fee_bps: float = 0.0,
    trade_id: str | None = None,
    entry_premium_bps: float | None = None,
    warning: str = "",
) -> dict[str, Any]:
    normalized_mapping = mapping if isinstance(mapping, WeekendBasisMapping) else normalize_weekend_basis_mapping(ticker, mapping)
    ts = _iso_time(entry_ts or datetime.now(timezone.utc))
    entry_bid = float(binance_entry_bid)
    entry_ask = _number(binance_entry_ask)
    premium = entry_premium_bps if entry_premium_bps is not None else (entry_bid / broker_anchor_price - 1.0) * 10_000.0
    qty = float(_number(binance_short_qty) or 0.0)
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "trade_id": trade_id or _trade_id(week_id, ticker, ts),
        "week_id": week_id,
        "ticker": str(ticker or "").strip().upper(),
        "status": "SHORT_OPEN",
        "binance_symbol": normalized_mapping.binance_symbol,
        "broker_symbol": normalized_mapping.broker_symbol,
        "mapping_multiplier": normalized_mapping.mapping_multiplier,
        "entry_ts": ts,
        "binance_entry_price": entry_bid,
        "binance_entry_bid": entry_bid,
        "binance_entry_ask": entry_ask,
        "binance_short_qty": qty,
        "entry_notional": entry_bid * qty if qty else None,
        "entry_fee_bps": float(_number(entry_fee_bps) or 0.0),
        "entry_premium_bps": premium,
        "broker_hedge_ts": "",
        "broker_hedge_price": None,
        "broker_hedge_bid": None,
        "broker_hedge_ask": None,
        "broker_shares": None,
        "locked_spread_bps": None,
        "net_locked_bps": None,
        "residual_basis_bps": None,
        "exit_ts": "",
        "binance_exit_price": None,
        "broker_exit_price": None,
        "realized_pnl": None,
        "realized_pnl_bps": None,
        "data_quality": "OK",
        "warning": warning,
        "created_at": now_iso,
        "updated_at": now_iso,
    }


def evaluate_open_trade(
    trade: dict[str, Any],
    *,
    current_binance_quote: BasisQuote | dict[str, Any] | None = None,
    broker_bar: BrokerOvernightBar | dict[str, Any] | None = None,
    now: datetime | None = None,
    config: BasisStrategyConfig | None = None,
) -> dict[str, Any]:
    cfg = config or BasisStrategyConfig()
    result = dict(trade or {})
    status = str(result.get("status") or "").upper()
    if status == "SHORT_OPEN":
        quote = _coerce_basis_quote(current_binance_quote)
        if quote is not None:
            entry = _number(result.get("binance_entry_price"))
            result["unrealized_short_pnl_bps"] = (entry / quote.ask - 1.0) * 10_000.0 if entry and quote.ask > 0 else None
        bar = _coerce_broker_bar(broker_bar)
        if bar is None:
            result["status"] = "WAIT_BROKER_OPEN"
            result["warning"] = "等待券商 overnight 第一根有效 1m bar"
        else:
            max_buy = max_broker_buy_price(result, cfg)
            result["status"] = "HEDGE_DUE"
            result["broker_hedge_bid"] = bar.bid
            result["broker_hedge_ask"] = bar.ask
            result["max_broker_buy_price"] = max_buy
            result["hedge_ok"] = max_buy is None or bar.ask <= max_buy
            if not result["hedge_ok"]:
                result["warning"] = "BROKER_PRICE_ABOVE_LIMIT"
    elif status == "HEDGE_LOCKED":
        quote = _coerce_basis_quote(current_binance_quote)
        bar = _coerce_broker_bar(broker_bar)
        if quote is not None and bar is not None:
            residual = (quote.mid / bar.mid - 1.0) * 10_000.0
            result["residual_basis_bps"] = residual
            if abs(residual) <= cfg.exit_basis_threshold_bps and quote.spread_bps <= cfg.max_binance_spread_bps and bar.spread_bps <= cfg.max_broker_spread_bps:
                result["status"] = "EXIT_READY"
    result["updated_at"] = _iso_time(now or datetime.now(timezone.utc))
    return result


def record_broker_hedge(
    trade: dict[str, Any],
    *,
    broker_hedge_ask: float,
    broker_hedge_bid: float | None = None,
    broker_shares: float | None = None,
    binance_same_min_bid: float | None = None,
    binance_same_min_ask: float | None = None,
    hedge_ts: datetime | str | None = None,
    config: BasisStrategyConfig | None = None,
) -> dict[str, Any]:
    cfg = config or BasisStrategyConfig()
    result = dict(trade or {})
    hedge_ask = float(broker_hedge_ask)
    hedge_bid = _number(broker_hedge_bid)
    if hedge_bid is None:
        hedge_bid = hedge_ask
    binance_bid = _number(binance_same_min_bid)
    binance_ask = _number(binance_same_min_ask)
    entry = _number(result.get("binance_entry_price") or result.get("binance_entry_bid"))
    result.update(
        {
            "status": "HEDGE_LOCKED",
            "broker_hedge_ts": _iso_time(hedge_ts or datetime.now(timezone.utc)),
            "broker_hedge_price": hedge_ask,
            "broker_hedge_bid": hedge_bid,
            "broker_hedge_ask": hedge_ask,
            "broker_shares": broker_shares if broker_shares is not None else result.get("binance_short_qty"),
            "locked_spread_bps": (entry / hedge_ask - 1.0) * 10_000.0 if entry and hedge_ask > 0 else None,
            "net_locked_bps": (entry / hedge_ask - 1.0) * 10_000.0 - cfg.fees_bps - cfg.funding_bps - cfg.slippage_bps if entry and hedge_ask > 0 else None,
            "realized_pnl": None,
            "realized_pnl_bps": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if binance_bid is not None and binance_ask is not None and binance_ask >= binance_bid:
        binance_mid = (binance_bid + binance_ask) / 2.0
        broker_mid = (hedge_bid + hedge_ask) / 2.0
        result["residual_basis_bps"] = (binance_mid / broker_mid - 1.0) * 10_000.0 if broker_mid > 0 else None
    return result


def close_weekend_basis_trade(
    trade: dict[str, Any],
    *,
    binance_exit_ask: float,
    broker_exit_bid: float,
    exit_ts: datetime | str | None = None,
    fees: float = 0.0,
    funding: float = 0.0,
    slippage: float = 0.0,
) -> dict[str, Any]:
    result = dict(trade or {})
    entry = _number(result.get("binance_entry_price") or result.get("binance_entry_bid"))
    qty = _number(result.get("binance_short_qty")) or 0.0
    hedge_price = _number(result.get("broker_hedge_price") or result.get("broker_hedge_ask"))
    shares = _number(result.get("broker_shares")) or qty
    exit_ask = float(binance_exit_ask)
    broker_bid = float(broker_exit_bid)
    realized = None
    realized_bps = None
    if entry is not None and hedge_price is not None and qty > 0 and shares > 0:
        realized = (entry - exit_ask) * qty + (broker_bid - hedge_price) * shares - fees - funding - slippage
        notional = entry * qty
        realized_bps = realized / notional * 10_000.0 if notional > 0 else None
    result.update(
        {
            "status": "CLOSED",
            "exit_ts": _iso_time(exit_ts or datetime.now(timezone.utc)),
            "binance_exit_price": exit_ask,
            "broker_exit_price": broker_bid,
            "realized_pnl": realized,
            "realized_pnl_bps": realized_bps,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return result


def max_broker_buy_price(trade: dict[str, Any], config: BasisStrategyConfig | None = None) -> float | None:
    cfg = config or BasisStrategyConfig()
    entry = _number(trade.get("binance_entry_price") or trade.get("binance_entry_bid"))
    return _max_broker_buy_price(entry, cfg)


def load_weekend_basis_trades(path: Path = DEFAULT_BASIS_TRADES_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("trades") if isinstance(payload, dict) else []
    return [dict(row) for row in rows or [] if isinstance(row, dict)]


def save_weekend_basis_trades(trades: Iterable[dict[str, Any]], path: Path = DEFAULT_BASIS_TRADES_PATH) -> list[dict[str, Any]]:
    rows = [dict(row) for row in trades or []]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "trades": rows}, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return rows


def upsert_weekend_basis_trade(trade: dict[str, Any], path: Path = DEFAULT_BASIS_TRADES_PATH) -> list[dict[str, Any]]:
    rows = load_weekend_basis_trades(path)
    trade_id = str(trade.get("trade_id") or "")
    replaced = False
    next_rows: list[dict[str, Any]] = []
    for row in rows:
        if trade_id and str(row.get("trade_id") or "") == trade_id:
            next_rows.append(dict(trade))
            replaced = True
        else:
            next_rows.append(row)
    if not replaced:
        next_rows.append(dict(trade))
    return save_weekend_basis_trades(next_rows, path)


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


def _base_live_result(mapping: WeekendBasisMapping) -> dict[str, Any]:
    return {
        "ticker": mapping.ticker,
        "broker_symbol": mapping.broker_symbol,
        "binance_symbol": mapping.binance_symbol,
        "mapping_multiplier": mapping.mapping_multiplier,
        "currency": mapping.currency,
        "contract_type": mapping.contract_type,
        "mapping_status": "confirmed" if mapping.is_confirmed else "unconfirmed",
        "status": "OBSERVE",
        "broker_anchor_price": None,
        "binance_entry_bid": None,
        "binance_entry_ask": None,
        "entry_premium_bps": None,
        "rolling_high_premium_bps": None,
        "relative_high_rank": None,
        "pullback_bps": None,
        "min_binance_short_price": None,
        "max_broker_buy_price": None,
        "expected_net_locked_bps": None,
        "data_quality": "OK",
        "warning": "",
    }


def _current_entry_signal(
    quotes: list[BasisQuote],
    current_quote: BasisQuote,
    anchor: float,
    cfg: BasisStrategyConfig,
) -> dict[str, Any]:
    premiums = [(quote.bid / anchor - 1.0) * 10_000.0 for quote in quotes if quote.ts <= current_quote.ts]
    rolling_high = max(premiums) if premiums else -999_999.0
    current = (current_quote.bid / anchor - 1.0) * 10_000.0
    pullback = rolling_high - current
    percentile = _percentile_rank(premiums, current)
    candidate = rolling_high >= cfg.min_entry_premium_bps
    if cfg.entry_rule == "LIMIT_AT_TARGET_PREMIUM":
        return {"passes": current >= cfg.min_entry_premium_bps, "candidate": candidate, "reason": "WAIT_TARGET_PREMIUM"}
    if rolling_high < cfg.min_entry_premium_bps:
        return {"passes": False, "candidate": False, "reason": "WAIT_PREMIUM"}
    if current < cfg.min_entry_premium_bps - cfg.allowed_pullback_bps:
        return {"passes": False, "candidate": True, "reason": "PULLBACK_TOO_DEEP"}
    if pullback < cfg.min_pullback_bps:
        return {"passes": False, "candidate": True, "reason": "WAIT_PULLBACK"}
    if pullback > cfg.max_pullback_bps:
        return {"passes": False, "candidate": True, "reason": "PULLBACK_TOO_WIDE"}
    if percentile < cfg.min_percentile:
        return {"passes": False, "candidate": True, "reason": "LOW_RELATIVE_RANK"}
    return {"passes": True, "candidate": True, "reason": ""}


def _latest_quote_at(quotes: list[BasisQuote], now: datetime | None) -> BasisQuote:
    if now is None:
        return quotes[-1]
    now_utc = _ensure_utc(now)
    eligible = [quote for quote in quotes if quote.ts <= now_utc]
    return eligible[-1] if eligible else quotes[-1]


def _coerce_basis_quote(value: BasisQuote | dict[str, Any] | None) -> BasisQuote | None:
    if value is None:
        return None
    if isinstance(value, BasisQuote):
        return value
    return _basis_quote_from_row(value, estimated=False, source=str(value.get("source") or "")) if isinstance(value, dict) else None


def _coerce_broker_bar(value: BrokerOvernightBar | dict[str, Any] | None) -> BrokerOvernightBar | None:
    if value is None:
        return None
    if isinstance(value, BrokerOvernightBar):
        return value
    return _broker_bar_from_row(value) if isinstance(value, dict) else None


def _min_binance_short_price(anchor: float | None, cfg: BasisStrategyConfig) -> float | None:
    if anchor is None or anchor <= 0:
        return None
    return anchor * (1.0 + cfg.min_entry_premium_bps / 10_000.0)


def _max_broker_buy_price(entry_price: float | None, cfg: BasisStrategyConfig) -> float | None:
    if entry_price is None or entry_price <= 0:
        return None
    return entry_price / (1.0 + cfg.required_net_locked_bps / 10_000.0)


def _iso_time(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return _ensure_utc(value).isoformat()
    parsed = _parse_time(value)
    return parsed.isoformat() if parsed is not None else str(value or "")


def _trade_id(week_id: str, ticker: str, entry_ts: str) -> str:
    safe_time = entry_ts.replace(":", "").replace("-", "").replace("+", "Z")
    return f"{str(week_id or '').strip()}-{str(ticker or '').strip().upper()}-{safe_time}"


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
        close=_number(row.get("close") or row.get("close_price") or row.get("closePrice") or row.get("last") or row.get("last_price") or row.get("lastPrice")),
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
