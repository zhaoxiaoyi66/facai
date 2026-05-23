from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


ValuationMethod = Literal["EPS multiple", "FCF multiple", "Revenue multiple"]


@dataclass(frozen=True)
class BuyZoneInputs:
    current_price: float
    target_position_size: float
    valuation_method: ValuationMethod
    margin_of_safety_pct: float = 0.0
    forward_eps: float | None = None
    target_pe: float | None = None
    forward_fcf: float | None = None
    target_fcf_multiple: float | None = None
    forward_revenue: float | None = None
    target_ev_sales: float | None = None
    net_debt: float | None = None
    shares_outstanding: float | None = None


PRICE_LEVEL_RULES = [
    ("试探仓", 0.95, 0.25),
    ("正常买入区", 0.85, 0.25),
    ("重仓买入区", 0.75, 0.30),
    ("恐慌买入区", 0.65, 0.20),
]


def calculate_fair_value_per_share(inputs: BuyZoneInputs) -> float:
    if inputs.valuation_method == "EPS multiple":
        forward_eps = _require_positive(inputs.forward_eps, "forward_eps")
        target_pe = _require_positive(inputs.target_pe, "target_pe")
        return forward_eps * target_pe

    if inputs.valuation_method == "FCF multiple":
        forward_fcf = _require_positive(inputs.forward_fcf, "forward_fcf")
        target_multiple = _require_positive(inputs.target_fcf_multiple, "target_fcf_multiple")
        shares = _require_positive(inputs.shares_outstanding, "shares_outstanding")
        fair_value_equity = forward_fcf * target_multiple
        return fair_value_equity / shares

    if inputs.valuation_method == "Revenue multiple":
        forward_revenue = _require_positive(inputs.forward_revenue, "forward_revenue")
        target_ev_sales = _require_positive(inputs.target_ev_sales, "target_ev_sales")
        shares = _require_positive(inputs.shares_outstanding, "shares_outstanding")
        net_debt = _require_number(inputs.net_debt, "net_debt")
        fair_value_enterprise = forward_revenue * target_ev_sales
        equity_value = fair_value_enterprise - net_debt
        if equity_value <= 0:
            raise ValueError("收入倍数法计算出的股权价值小于或等于 0。")
        return equity_value / shares

    raise ValueError(f"不支持的估值方法：{inputs.valuation_method}")


def calculate_buy_zone_ladder(inputs: BuyZoneInputs) -> dict:
    fair_value = calculate_fair_value_per_share(inputs)
    margin_adjusted_fair_value = fair_value * (1 - inputs.margin_of_safety_pct / 100)

    tranches = []
    total_dollars = 0.0
    total_shares = 0.0
    panic_price = 0.0

    for label, fair_value_factor, allocation_pct in PRICE_LEVEL_RULES:
        buy_price = margin_adjusted_fair_value * fair_value_factor
        allocation_dollars = inputs.target_position_size * allocation_pct
        shares = allocation_dollars / buy_price if buy_price > 0 else 0.0
        total_dollars += allocation_dollars
        total_shares += shares
        if label == "恐慌买入区":
            panic_price = buy_price
        tranches.append(
            {
                "Tranche": label,
                "Buy Price": buy_price,
                "Allocation %": allocation_pct * 100,
                "Allocation $": allocation_dollars,
                "Estimated Shares": shares,
            }
        )

    weighted_average_cost = total_dollars / total_shares if total_shares > 0 else 0.0
    upside_to_fair_value = _pct_change(fair_value, inputs.current_price)
    downside_to_panic_price = _pct_change(panic_price, inputs.current_price)

    return {
        "valuation_method": inputs.valuation_method,
        "fair_value_price": fair_value,
        "margin_adjusted_fair_value": margin_adjusted_fair_value,
        "starter_position_price": tranches[0]["Buy Price"],
        "normal_buy_zone_price": tranches[1]["Buy Price"],
        "heavy_buy_zone_price": tranches[2]["Buy Price"],
        "panic_buy_zone_price": tranches[3]["Buy Price"],
        "tranches": pd.DataFrame(tranches),
        "total_shares": total_shares,
        "weighted_average_cost": weighted_average_cost,
        "upside_to_fair_value_pct": upside_to_fair_value,
        "downside_to_panic_price_pct": downside_to_panic_price,
    }


def _require_positive(value: float | None, field_name: str) -> float:
    number = _require_number(value, field_name)
    if number <= 0:
        raise ValueError(f"{field_name} 必须大于 0。")
    return number


def _require_number(value: float | None, field_name: str) -> float:
    if value is None:
        raise ValueError(f"{field_name} 为必填项。")
    return float(value)


def _pct_change(target_price: float, current_price: float) -> float | None:
    if current_price <= 0:
        return None
    return (target_price / current_price - 1) * 100
