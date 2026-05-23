from __future__ import annotations


def calculate_valuation_score(snapshot: dict, technicals: dict | None = None) -> float:
    technicals = technicals or {}
    score = 25.0

    # Valuation rule: high P/E multiples reduce score; unavailable P/E is not treated as a fake bargain.
    pe = snapshot.get("forward_pe") if snapshot.get("forward_pe") is not None else snapshot.get("trailing_pe")
    if pe is None:
        score -= 6
    elif pe > 70:
        score -= 12
    elif pe > 45:
        score -= 8
    elif pe > 30:
        score -= 4

    # Valuation rule: high price-to-sales can be risky for growth stocks if sentiment compresses.
    price_to_sales = snapshot.get("enterprise_to_revenue") or snapshot.get("price_to_sales")
    if price_to_sales is None:
        score -= 3
    elif price_to_sales > 20:
        score -= 10
    elif price_to_sales > 12:
        score -= 7
    elif price_to_sales > 8:
        score -= 4

    # Valuation rule: P/FCF and FCF yield add a cash-flow view once FMP paid data is available.
    price_to_fcf = snapshot.get("price_to_fcf")
    fcf_yield = snapshot.get("free_cash_flow_yield")
    if price_to_fcf is not None:
        if price_to_fcf > 60:
            score -= 5
        elif price_to_fcf > 35:
            score -= 3
        elif 0 < price_to_fcf <= 20:
            score += 2
    elif fcf_yield is None:
        score -= 1

    if fcf_yield is not None:
        if fcf_yield >= 0.05:
            score += 2
        elif fcf_yield < 0.015:
            score -= 2

    # Valuation rule: a meaningful drawdown can improve entry risk, but it is not a standalone buy signal.
    drawdown = technicals.get("drawdown_from_high_pct")
    if drawdown is None:
        score -= 2
    elif drawdown <= -30:
        score += 3
    elif drawdown <= -15:
        score += 2
    elif drawdown > -5:
        score -= 2

    return round(max(0, min(score, 25)), 1)


def is_high_valuation(snapshot: dict) -> bool:
    trailing_pe = snapshot.get("trailing_pe")
    forward_pe = snapshot.get("forward_pe")
    price_to_sales = snapshot.get("price_to_sales")
    enterprise_to_revenue = snapshot.get("enterprise_to_revenue")
    price_to_fcf = snapshot.get("price_to_fcf")
    return (
        (trailing_pe is not None and trailing_pe > 60)
        or (forward_pe is not None and forward_pe > 45)
        or (price_to_sales is not None and price_to_sales > 15)
        or (enterprise_to_revenue is not None and enterprise_to_revenue > 18)
        or (price_to_fcf is not None and price_to_fcf > 55)
    )


def classify_value_zone(valuation_score: float, technicals: dict, has_high_valuation_flag: bool) -> str:
    drawdown = technicals.get("drawdown_from_high_pct")
    rsi = technicals.get("rsi14")
    pct_above_ema200 = technicals.get("pct_above_ema200")

    if has_high_valuation_flag or (pct_above_ema200 is not None and pct_above_ema200 >= 30):
        return "高估区"
    if valuation_score >= 18 and drawdown is not None and drawdown <= -10 and (rsi is None or rsi < 70):
        return "买入区"
    if valuation_score >= 14:
        return "合理估值区"
    return "观察区"
