from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from data.prices import CACHE_PATH
from settings import PROJECT_ROOT


CONFIG_PATH = PROJECT_ROOT / "config" / "portfolio_targets.yaml"
DEFAULT_TARGET_WEIGHT = 2.0
DEFAULT_MAX_WEIGHT = 4.0
DEFAULT_ROLE = "watch_only"


@dataclass(frozen=True)
class PortfolioTarget:
    ticker: str
    target_weight: float = DEFAULT_TARGET_WEIGHT
    max_weight: float = DEFAULT_MAX_WEIGHT
    role: str = DEFAULT_ROLE
    max_shares: float | None = None
    notes: str = ""

    def to_context(self) -> dict[str, Any]:
        return {
            "target_weight": self.target_weight,
            "max_weight": self.max_weight,
            "role": self.role,
            "max_shares": self.max_shares,
            "target_notes": self.notes,
        }


def load_portfolio_targets(path: Path = CONFIG_PATH) -> dict[str, PortfolioTarget]:
    if not path.exists():
        return {}
    parsed = _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))
    records = parsed.get("targets") if isinstance(parsed.get("targets"), dict) else parsed
    targets: dict[str, PortfolioTarget] = {}
    for raw_ticker, raw_values in records.items():
        ticker = str(raw_ticker or "").strip().upper()
        if not ticker or not isinstance(raw_values, dict):
            continue
        targets[ticker] = _target_from_mapping(ticker, raw_values)
    return targets


def get_portfolio_target(ticker: str, path: Path = CONFIG_PATH) -> PortfolioTarget:
    symbol = str(ticker or "").strip().upper()
    return load_portfolio_targets(path).get(symbol) or PortfolioTarget(ticker=symbol)


def apply_portfolio_target(
    ticker: str,
    portfolio_context: dict[str, Any] | None = None,
    *,
    config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    context = dict(portfolio_context or {})
    target = get_portfolio_target(ticker, config_path)
    target_context = target.to_context()
    for key, value in target_context.items():
        if key in {"target_weight", "max_weight", "role", "max_shares", "target_notes"}:
            context[key] = value
        elif context.get(key) in (None, ""):
            context[key] = value
    return context


def build_action_fusion_portfolio_context(
    ticker: str,
    *,
    path: Path = CACHE_PATH,
    config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    symbol = str(ticker or "").strip().upper()
    context: dict[str, Any] = {}
    try:
        from data.portfolio_view_model import build_portfolio_view_model

        view = build_portfolio_view_model(path)
        summary = dict(view.get("summary") or {})
        context["available_cash"] = summary.get("cashBalance")
        for row in view.get("rows") or []:
            if str(row.get("symbol") or "").upper() != symbol:
                continue
            context.update(
                {
                    "current_shares": row.get("quantity"),
                    "avg_cost": row.get("averageCost"),
                    "market_value": row.get("marketValue"),
                    "unrealized_pnl": row.get("unrealizedPnl"),
                    "unrealized_pnl_pct": row.get("unrealizedPnlPct"),
                    "portfolio_weight": row.get("positionPct"),
                    "target_weight": row.get("targetPositionPct"),
                    "max_weight": row.get("maxAcceptablePositionPct"),
                    "portfolio_updated_at": row.get("updatedAt"),
                }
            )
            break
    except Exception:
        context = {}
    return apply_portfolio_target(symbol, context, config_path=config_path)


def build_action_fusion_portfolio_contexts(
    tickers: Iterable[str],
    *,
    path: Path = CACHE_PATH,
    config_path: Path = CONFIG_PATH,
) -> dict[str, dict[str, Any]]:
    symbols = _normalize_symbols(tickers)
    contexts: dict[str, dict[str, Any]] = {symbol: {} for symbol in symbols}
    targets = load_portfolio_targets(config_path)
    try:
        from data.portfolio_view_model import build_portfolio_view_model

        view = build_portfolio_view_model(path)
        summary = dict(view.get("summary") or {})
        available_cash = summary.get("cashBalance")
        for context in contexts.values():
            context["available_cash"] = available_cash
        for row in view.get("rows") or []:
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol not in contexts:
                continue
            contexts[symbol].update(
                {
                    "current_shares": row.get("quantity"),
                    "avg_cost": row.get("averageCost"),
                    "market_value": row.get("marketValue"),
                    "unrealized_pnl": row.get("unrealizedPnl"),
                    "unrealized_pnl_pct": row.get("unrealizedPnlPct"),
                    "portfolio_weight": row.get("positionPct"),
                    "target_weight": row.get("targetPositionPct"),
                    "max_weight": row.get("maxAcceptablePositionPct"),
                    "portfolio_updated_at": row.get("updatedAt"),
                }
            )
    except Exception:
        contexts = {symbol: {} for symbol in symbols}
    return {symbol: _apply_loaded_portfolio_target(symbol, context, targets) for symbol, context in contexts.items()}


def _target_from_mapping(ticker: str, values: dict[str, Any]) -> PortfolioTarget:
    return PortfolioTarget(
        ticker=ticker,
        target_weight=_weight_to_percent(values.get("target_weight"), DEFAULT_TARGET_WEIGHT),
        max_weight=_weight_to_percent(values.get("max_weight"), DEFAULT_MAX_WEIGHT),
        role=str(values.get("role") or DEFAULT_ROLE).strip() or DEFAULT_ROLE,
        max_shares=_optional_number(values.get("max_shares")),
        notes=str(values.get("notes") or "").strip(),
    )


def _apply_loaded_portfolio_target(
    ticker: str,
    portfolio_context: dict[str, Any] | None,
    targets: dict[str, PortfolioTarget],
) -> dict[str, Any]:
    symbol = str(ticker or "").strip().upper()
    context = dict(portfolio_context or {})
    target = targets.get(symbol) or PortfolioTarget(ticker=symbol)
    for key, value in target.to_context().items():
        context[key] = value
    return context


def _normalize_symbols(tickers: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    symbols: list[str] = []
    for ticker in tickers:
        symbol = str(ticker or "").strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def _weight_to_percent(value: Any, default: float) -> float:
    number = _optional_number(value)
    if number is None:
        return default
    if 0 <= number <= 1:
        return round(number * 100, 4)
    return round(number, 4)


def _optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_simple_yaml_mapping(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        line_without_comment = raw_line.split("#", 1)[0].rstrip()
        if not line_without_comment.strip():
            continue
        indent = len(line_without_comment) - len(line_without_comment.lstrip(" "))
        line = line_without_comment.strip()
        if ":" not in line or line.startswith("-"):
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip().strip('"').strip("'")
        raw_value = raw_value.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if raw_value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(raw_value)
    return root


def _parse_scalar(raw_value: str) -> Any:
    value = raw_value.strip().strip('"').strip("'")
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
