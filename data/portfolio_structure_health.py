from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import Any

from data.macro_regime import (
    MacroRegimeSnapshot,
    REGIME_PANIC,
    REGIME_RISK_OFF,
    REGIME_STRESS,
)


STATUS_HEALTHY = "健康"
STATUS_AGGRESSIVE = "偏激进"
STATUS_IMBALANCED = "失衡"
STATUS_DANGER = "危险"

STATUS_RANK = {
    STATUS_HEALTHY: 0,
    STATUS_AGGRESSIVE: 1,
    STATUS_IMBALANCED: 2,
    STATUS_DANGER: 3,
}
RANK_STATUS = {value: key for key, value in STATUS_RANK.items()}


@dataclass(frozen=True)
class PortfolioStructureCheck:
    status: str
    cash_pct: float | None
    tier_pct: dict[str, float]
    largest_position_pct: float | None
    top3_position_pct: float | None
    trading_position_pct: float | None
    has_leverage: bool
    macro_regime: str | None
    reasons: list[str] = field(default_factory=list)
    action_hints: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def build_portfolio_structure_check(
    portfolio_view: dict[str, Any] | None,
    *,
    macro_regime: MacroRegimeSnapshot | str | None = None,
) -> PortfolioStructureCheck:
    view = portfolio_view or {}
    summary = dict(view.get("summary") or {})
    settings = dict(view.get("settings") or {})
    rows = [dict(row) for row in (view.get("rows") or []) if isinstance(row, dict)]

    market_value = _number(summary.get("marketValue"))
    total_value = _number(summary.get("totalPortfolioValue"))
    cash_balance = _number(summary.get("cashBalance"))
    settings_cash = _number(settings.get("cash_balance"))

    if cash_balance is None and settings_cash is not None:
        cash_balance = settings_cash
    if total_value is None:
        if market_value is not None and cash_balance is not None:
            total_value = market_value + cash_balance
        elif market_value is not None and market_value > 0:
            total_value = market_value

    cash_pct = _pct(cash_balance, total_value)
    has_leverage = _bool_value(summary.get("hasLeverage"), settings.get("has_leverage")) or (
        cash_pct is not None and cash_pct < 0
    )
    position_pcts = [_row_pct(row, total_value) for row in rows]
    position_pcts = [pct for pct in position_pcts if pct is not None]
    tier_pct = _tier_weights(rows, total_value)
    largest_position_pct = max(position_pcts) if position_pcts else None
    top3_position_pct = sum(sorted(position_pcts, reverse=True)[:3]) if position_pcts else None
    trading_position_pct = tier_pct.get("C", 0.0)
    regime = _macro_regime_text(macro_regime)

    rank = STATUS_RANK[STATUS_HEALTHY]
    reasons: list[str] = []
    hints: list[str] = ["现金也是仓位。"]

    if not rows:
        reasons.append("暂无真实持仓，结构风险来自现金和待建仓计划。")
    if cash_pct is None:
        rank = max(rank, STATUS_RANK[STATUS_AGGRESSIVE])
        reasons.append("现金比例无法计算，需要设置组合总资产或现金余额。")
    elif cash_pct < 5:
        rank = max(rank, STATUS_RANK[STATUS_DANGER])
        reasons.append("现金比例低于 5%，失去修正能力。")
    elif cash_pct < 10:
        rank = max(rank, STATUS_RANK[STATUS_AGGRESSIVE])
        reasons.append("现金比例低于 10%，修正空间偏窄。")

    if largest_position_pct is not None and largest_position_pct > 30:
        rank = max(rank, STATUS_RANK[STATUS_DANGER])
        reasons.append("单票仓位超过 30%，集中度危险。")
    if top3_position_pct is not None and top3_position_pct > 60:
        rank = max(rank, STATUS_RANK[STATUS_IMBALANCED])
        reasons.append("前三大持仓超过 60%，组合集中。")
    if trading_position_pct is not None and trading_position_pct > 20:
        rank = max(rank, STATUS_RANK[STATUS_IMBALANCED])
        reasons.append("C 类交易仓超过 20%，交易型仓位过高。")
        hints.append("优先处理 C 类 / 脆弱仓位。")
    elif trading_position_pct is not None and trading_position_pct > 12:
        rank = max(rank, STATUS_RANK[STATUS_AGGRESSIVE])
        reasons.append("C 类交易仓偏高，需要控制新增节奏。")

    a_pct = tier_pct.get("A", 0.0)
    if position_pcts and a_pct < 35:
        rank = max(rank, STATUS_RANK[STATUS_IMBALANCED])
        reasons.append("A 类核心仓占比偏低，缺核心资产压舱。")

    if has_leverage:
        rank = max(rank, STATUS_RANK[STATUS_DANGER])
        reasons.append("组合存在杠杆或现金为负，风险等级上调。")

    if regime in {REGIME_STRESS, REGIME_PANIC} and trading_position_pct and trading_position_pct > 20:
        rank = max(rank, STATUS_RANK[STATUS_DANGER])
        reasons.append("宏观压力环境叠加 C 类过高，结构偏危险。")
        hints.append("不要在恐慌中先砍核心仓。")
    elif regime == REGIME_RISK_OFF and trading_position_pct and trading_position_pct > 20:
        rank = max(rank, STATUS_RANK[STATUS_IMBALANCED])
        reasons.append("风险收缩环境下 C 类仓位偏高。")
        hints.append("不要在恐慌中先砍核心仓。")

    hints.append("当前结构要配得上自己的风险承受能力。")
    if not reasons:
        reasons.append("现金、核心仓和交易仓比例暂未触发结构警报。")

    return PortfolioStructureCheck(
        status=RANK_STATUS[rank],
        cash_pct=cash_pct,
        tier_pct={key: round(value, 2) for key, value in tier_pct.items()},
        largest_position_pct=_round(largest_position_pct),
        top3_position_pct=_round(top3_position_pct),
        trading_position_pct=_round(trading_position_pct),
        has_leverage=has_leverage,
        macro_regime=regime,
        reasons=_dedupe(reasons),
        action_hints=_dedupe(hints),
        stats={
            "total_value": total_value,
            "market_value": market_value,
            "cash_balance": cash_balance,
            "position_count": len(rows),
        },
    )


def portfolio_structure_check_strip_html(check: PortfolioStructureCheck) -> str:
    items = [
        ("现金", _pct_text(check.cash_pct)),
        ("A", _pct_text(check.tier_pct.get("A"))),
        ("B", _pct_text(check.tier_pct.get("B"))),
        ("C", _pct_text(check.tier_pct.get("C"))),
        ("最大单票", _pct_text(check.largest_position_pct)),
        ("前三大", _pct_text(check.top3_position_pct)),
        ("交易仓", _pct_text(check.trading_position_pct)),
        ("杠杆", "有" if check.has_leverage else "无"),
    ]
    item_html = "".join(
        f"<span><b>{escape(label)}</b>{escape(value)}</span>"
        for label, value in items
    )
    detail = "；".join([*(check.reasons[:3]), *(check.action_hints[:2])])
    return (
        f'<section class="portfolio-structure-strip {escape(_tone_for_status(check.status))}" title="{escape(detail)}">'
        '<div class="portfolio-structure-main">'
        f'<strong>仓位结构体检：{escape(check.status)}</strong>'
        f'<div class="portfolio-structure-items">{item_html}</div>'
        "</div>"
        f'<div class="portfolio-structure-hint">{escape(detail)}</div>'
        "</section>"
    )


def _tier_weights(rows: list[dict[str, Any]], total_value: float | None) -> dict[str, float]:
    weights = {"A": 0.0, "B": 0.0, "C": 0.0}
    for row in rows:
        tier = str(row.get("positionTier") or row.get("position_tier") or "").strip().upper()
        if tier not in weights:
            continue
        pct = _row_pct(row, total_value)
        if pct is not None:
            weights[tier] += pct
    return weights


def _row_pct(row: dict[str, Any], total_value: float | None) -> float | None:
    pct = _number(row.get("positionPct"))
    if pct is not None:
        return pct
    return _pct(_number(row.get("marketValue")), total_value)


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator * 100


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_value(*values: Any) -> bool:
    for value in values:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value:
            return True
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "y", "有", "杠杆"}:
            return True
    return False


def _macro_regime_text(macro_regime: MacroRegimeSnapshot | str | None) -> str | None:
    if macro_regime is None:
        return None
    if isinstance(macro_regime, str):
        return macro_regime
    return str(getattr(macro_regime, "regime", "") or "") or None


def _pct_text(value: float | None) -> str:
    if value is None:
        return "待补"
    return f"{value:.1f}%"


def _round(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _tone_for_status(status: str) -> str:
    if status == STATUS_DANGER:
        return "danger"
    if status == STATUS_IMBALANCED:
        return "imbalanced"
    if status == STATUS_AGGRESSIVE:
        return "aggressive"
    return "healthy"
