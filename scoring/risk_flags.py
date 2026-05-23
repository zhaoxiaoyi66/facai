from __future__ import annotations

from dataclasses import dataclass

from scoring.valuation import is_high_valuation


@dataclass(frozen=True)
class RiskFlag:
    label: str
    severity: str
    detail: str


def build_risk_flags(snapshot: dict, technicals: dict) -> list[RiskFlag]:
    flags: list[RiskFlag] = []

    fcf = snapshot.get("free_cash_flow")
    if fcf is not None and fcf < 0:
        flags.append(RiskFlag("自由现金流为负", "high", "自由现金流低于 0。"))

    debt_to_equity = snapshot.get("debt_to_equity")
    total_debt = snapshot.get("total_debt")
    total_cash = snapshot.get("total_cash")
    if (debt_to_equity is not None and debt_to_equity > 150) or (
        total_debt is not None and total_cash is not None and total_cash > 0 and total_debt / total_cash > 3
    ):
        flags.append(RiskFlag("债务偏高", "high", "相对于权益或现金，债务负担偏高。"))

    net_debt_to_ebitda = snapshot.get("net_debt_to_ebitda")
    if net_debt_to_ebitda is not None and net_debt_to_ebitda > 4:
        flags.append(RiskFlag("净债务/EBITDA 偏高", "high", "净债务相对 EBITDA 偏高，财务弹性较弱。"))

    current_ratio = snapshot.get("current_ratio")
    if current_ratio is not None and current_ratio < 1:
        flags.append(RiskFlag("短期偿债压力", "medium", "流动比率低于 1，需要检查短期债务和现金流。"))

    if is_high_valuation(snapshot):
        flags.append(RiskFlag("估值偏高", "medium", "估值倍数高于 MVP 阈值。"))

    revenue_growth = snapshot.get("revenue_growth")
    if revenue_growth is not None and revenue_growth < 0:
        flags.append(RiskFlag("收入增长放缓", "medium", "收入增长为负。"))
    elif revenue_growth is None:
        flags.append(
            RiskFlag(
                "收入放缓情况未知",
                "info",
                "TODO：接入季度收入历史后再判断是否放缓。",
            )
        )

    operating_margin = snapshot.get("operating_margin")
    profit_margin = snapshot.get("profit_margin")
    prior_operating_margin = snapshot.get("prior_operating_margin")
    prior_profit_margin = snapshot.get("prior_profit_margin")
    if (operating_margin is not None and operating_margin < 0) or (profit_margin is not None and profit_margin < 0):
        flags.append(RiskFlag("利润率承压", "medium", "利润率为负或存在压力。"))
    elif (
        operating_margin is not None
        and prior_operating_margin is not None
        and operating_margin < prior_operating_margin - 0.03
    ) or (
        profit_margin is not None
        and prior_profit_margin is not None
        and profit_margin < prior_profit_margin - 0.03
    ):
        flags.append(RiskFlag("利润率收缩", "medium", "利润率较上一期明显下降。"))
    elif operating_margin is None and profit_margin is None:
        flags.append(
            RiskFlag(
                "利润率变化未知",
                "info",
                "TODO：接入季度利润率历史后再判断是否收缩。",
            )
        )

    drawdown = technicals.get("drawdown_from_high_pct")
    weak_fundamentals = (revenue_growth is None or revenue_growth < 0.05) and (fcf is None or fcf <= 0)
    if drawdown is not None and drawdown <= -40 and weak_fundamentals:
        flags.append(
            RiskFlag(
                "大幅回撤但缺少基本面支撑",
                "high",
                "价格明显低于 52 周高点，且基本面偏弱或缺失。",
            )
        )

    rsi = technicals.get("rsi14")
    if rsi is not None and rsi >= 70:
        flags.append(RiskFlag("RSI 超买", "medium", "RSI14 大于或等于 70。"))

    pct_above_ema200 = technicals.get("pct_above_ema200")
    if pct_above_ema200 is not None and pct_above_ema200 >= 30:
        flags.append(RiskFlag("价格显著高于 EMA200", "medium", "价格相对 200 日趋势线明显延伸。"))

    price = technicals.get("price")
    ema200 = technicals.get("ema200")
    if price is not None and ema200 is not None and price < ema200 and weak_fundamentals:
        flags.append(
            RiskFlag(
                "跌破 EMA200 且基本面偏弱",
                "high",
                "价格低于长期趋势线，基本面暂时无法抵消技术面的弱势。",
            )
        )

    return flags


def calculate_balance_sheet_risk_score(snapshot: dict) -> float:
    score = 10.0

    # Balance sheet rule: elevated debt-to-equity lowers add-on confidence.
    debt_to_equity = snapshot.get("debt_to_equity")
    if debt_to_equity is None:
        score -= 2
    elif debt_to_equity > 200:
        score -= 6
    elif debt_to_equity > 100:
        score -= 3

    # Balance sheet rule: cash greater than debt deserves credit; heavy debt relative to cash loses credit.
    total_debt = snapshot.get("total_debt")
    total_cash = snapshot.get("total_cash")
    if total_debt is None or total_cash is None:
        score -= 1
    elif total_cash >= total_debt:
        score += 1
    elif total_cash > 0 and total_debt / total_cash > 3:
        score -= 3

    # Balance sheet rule: net debt to EBITDA above 4x deserves a stronger risk penalty.
    net_debt_to_ebitda = snapshot.get("net_debt_to_ebitda")
    if net_debt_to_ebitda is not None:
        if net_debt_to_ebitda > 5:
            score -= 4
        elif net_debt_to_ebitda > 3:
            score -= 2

    # Balance sheet rule: a current ratio below 1 can signal short-term liquidity pressure.
    current_ratio = snapshot.get("current_ratio")
    if current_ratio is not None and current_ratio < 1:
        score -= 2

    return round(max(0, min(score, 10)), 1)


def missing_fundamental_fields(snapshot: dict) -> list[str]:
    fields = [
        "free_cash_flow",
        "total_revenue",
        "net_income",
        "revenue_growth",
        "earnings_growth",
        "operating_margin",
        "profit_margin",
        "debt_to_equity",
        "net_debt_to_ebitda",
        "current_ratio",
        "free_cash_flow_yield",
        "return_on_invested_capital",
    ]
    return [field for field in fields if snapshot.get(field) is None]
