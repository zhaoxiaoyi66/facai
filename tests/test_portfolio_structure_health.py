from __future__ import annotations

from data.macro_regime import REGIME_STRESS
from data.portfolio_structure_health import (
    PortfolioStructureCheck,
    STATUS_DANGER,
    STATUS_HEALTHY,
    STATUS_IMBALANCED,
    build_portfolio_structure_check,
    portfolio_structure_check_strip_html,
)


def _view(rows, *, total=100_000, cash=None, has_leverage=False):
    market_value = sum(float(row.get("marketValue") or 0) for row in rows)
    if cash is None:
        cash = total - market_value
    return {
        "summary": {
            "marketValue": market_value,
            "totalPortfolioValue": total,
            "cashBalance": cash,
        },
        "settings": {"has_leverage": has_leverage},
        "rows": rows,
    }


def _row(symbol: str, market_value: float, tier: str, pct: float | None = None):
    payload = {
        "symbol": symbol,
        "marketValue": market_value,
        "positionTier": tier,
    }
    if pct is not None:
        payload["positionPct"] = pct
    return payload


def test_low_cash_is_dangerous() -> None:
    check = build_portfolio_structure_check(
        _view(
            [
                _row("A1", 24_000, "A"),
                _row("A2", 24_000, "A"),
                _row("B1", 24_000, "B"),
                _row("C1", 24_000, "C"),
            ],
            cash=4_000,
        )
    )

    assert check.status == STATUS_DANGER
    assert check.cash_pct == 4
    assert any("现金比例低于 5%" in reason for reason in check.reasons)


def test_single_position_concentration_is_dangerous() -> None:
    check = build_portfolio_structure_check(
        _view(
            [
                _row("NVDA", 31_000, "A"),
                _row("MSFT", 15_000, "A"),
                _row("CRM", 10_000, "B"),
            ]
        )
    )

    assert check.status == STATUS_DANGER
    assert check.largest_position_pct == 31
    assert any("单票仓位超过 30%" in reason for reason in check.reasons)


def test_c_class_trading_bucket_above_twenty_percent_is_imbalanced() -> None:
    check = build_portfolio_structure_check(
        _view(
            [
                _row("MSFT", 29_000, "A"),
                _row("CRM", 20_000, "B"),
                _row("MU", 21_000, "C"),
            ]
        )
    )

    assert check.status == STATUS_IMBALANCED
    assert check.trading_position_pct == 21
    assert any("C 类交易仓超过 20%" in reason for reason in check.reasons)


def test_macro_stress_with_high_c_class_bucket_is_dangerous() -> None:
    check = build_portfolio_structure_check(
        _view(
            [
                _row("MSFT", 36_000, "A"),
                _row("CRM", 14_000, "B"),
                _row("MU", 22_000, "C"),
            ]
        ),
        macro_regime=REGIME_STRESS,
    )

    assert check.status == STATUS_DANGER
    assert any("宏观压力环境叠加 C 类过高" in reason for reason in check.reasons)
    assert any("不要在恐慌中先砍核心仓" in hint for hint in check.action_hints)


def test_tier_weights_are_calculated_from_position_percentages() -> None:
    check = build_portfolio_structure_check(
        _view(
            [
                _row("MSFT", 20_000, "A", 20),
                _row("AVGO", 15_000, "A", 15),
                _row("CRM", 10_000, "B", 10),
                _row("MU", 5_000, "C", 5),
            ]
        )
    )

    assert check.status == STATUS_HEALTHY
    assert check.tier_pct == {"A": 35.0, "B": 10.0, "C": 5.0}
    assert check.top3_position_pct == 45


def test_strip_html_is_compact_chinese_status() -> None:
    check = build_portfolio_structure_check(
        _view(
            [
                _row("MSFT", 20_000, "A"),
                _row("CRM", 10_000, "B"),
                _row("MU", 5_000, "C"),
            ]
        )
    )

    html = portfolio_structure_check_strip_html(check)

    assert "仓位结构体检" in html
    assert "现金" in html
    assert "最大单票" in html


def test_strip_html_localizes_missing_percentages() -> None:
    check = PortfolioStructureCheck(
        status=STATUS_HEALTHY,
        cash_pct=None,
        tier_pct={},
        largest_position_pct=None,
        top3_position_pct=None,
        trading_position_pct=None,
        has_leverage=False,
        macro_regime=None,
    )

    html = portfolio_structure_check_strip_html(check)

    assert "待补" in html
    assert "N/A" not in html
