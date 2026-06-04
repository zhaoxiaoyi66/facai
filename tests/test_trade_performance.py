from __future__ import annotations

from data.trade_performance import calculate_holding_days
from data.trade_performance import build_manual_cost_basis_lot
from data.trade_performance import match_realized_trades
from data.trade_performance import summarize_trade_performance


def _entry(
    entry_id: int,
    symbol: str,
    action: str,
    quantity: float,
    price: float,
    trade_date: str,
    **values,
) -> dict:
    return {
        "id": entry_id,
        "symbol": symbol,
        "action_type": action,
        "quantity": quantity,
        "price": price,
        "trade_date": trade_date,
        "created_at": f"{trade_date}T09:30:00+08:00",
        **values,
    }


def test_single_buy_single_sell_calculates_realized_pnl_and_holding_days() -> None:
    summary = summarize_trade_performance(
        entries=[
            _entry(1, "NVDA", "buy", 10, 100, "2026-01-01", position_class="A"),
            _entry(2, "NVDA", "sell", 10, 120, "2026-01-11", position_class="A"),
        ]
    )

    [trade] = summary["realized_trades"]
    assert trade["realized_pnl"] == 200
    assert trade["realized_pnl_pct"] == 20
    assert trade["holding_days"] == 10
    assert summary["summary"]["total_realized_pnl"] == 200


def test_multiple_buys_single_sell_uses_fifo_matching() -> None:
    summary = summarize_trade_performance(
        entries=[
            _entry(1, "NVDA", "buy", 10, 100, "2026-01-01"),
            _entry(2, "NVDA", "add", 10, 110, "2026-01-05"),
            _entry(3, "NVDA", "trim", 15, 120, "2026-01-11"),
        ]
    )

    trade = summary["realized_trades"][0]
    assert trade["matched_quantity"] == 15
    assert trade["buy_avg_price"] == 103.3333
    assert trade["realized_pnl"] == 250
    assert len(trade["matched_lots"]) == 2


def test_one_buy_multiple_sells_tracks_remaining_lot() -> None:
    matched = match_realized_trades(
        [
            _entry(1, "MSFT", "buy", 10, 100, "2026-01-01"),
            _entry(2, "MSFT", "trim", 4, 110, "2026-01-03"),
            _entry(3, "MSFT", "trim", 3, 90, "2026-01-04"),
        ]
    )

    assert [row["realized_pnl"] for row in matched["realized_trades"]] == [40, -30]
    assert matched["open_lots"][0]["remaining_quantity"] == 3


def test_partial_trim_realized_pnl_uses_matched_quantity_only() -> None:
    summary = summarize_trade_performance(
        entries=[
            _entry(1, "NOW", "buy", 10, 100, "2026-01-01"),
            _entry(2, "NOW", "trim", 4, 125, "2026-01-06"),
        ]
    )

    trade = summary["realized_trades"][0]
    assert trade["realized_pnl"] == 100
    assert trade["matched_quantity"] == 4
    assert summary["open_lots"][0]["remaining_quantity"] == 6


def test_sell_below_target_sell_price_marks_discipline_issue() -> None:
    summary = summarize_trade_performance(
        entries=[
            _entry(1, "ISRG", "buy", 5, 400, "2026-01-01", position_class="A", target_sell_price=520),
            _entry(2, "ISRG", "sell", 5, 450, "2026-02-01", position_class="A"),
        ]
    )

    trade = summary["realized_trades"][0]
    assert trade["below_target_sell_price"] is True
    assert "低于买入目标价卖出" in trade["discipline_flags"]
    assert "核心仓卖出需复盘" in trade["discipline_flags"]


def test_a_class_sell_without_reentry_plan_marks_discipline_issue() -> None:
    summary = summarize_trade_performance(
        entries=[
            _entry(1, "NVDA", "buy", 5, 100, "2026-01-01", position_class="A"),
            _entry(2, "NVDA", "trim", 2, 120, "2026-01-10", position_class="A"),
        ]
    )

    flags = summary["realized_trades"][0]["discipline_flags"]
    assert "核心仓卖出需复盘" in flags
    assert "A类卖出缺少具体回补计划" in flags


def test_blocked_and_observation_only_records_do_not_count_realized_pnl() -> None:
    summary = summarize_trade_performance(
        entries=[
            _entry(1, "NVDA", "buy", 5, 100, "2026-01-01", radar_observation_only=1),
            _entry(2, "NVDA", "buy", 5, 100, "2026-01-02", radar_blocked=1),
            _entry(3, "NVDA", "sell", 5, 120, "2026-01-10", discipline_status="blocked"),
        ]
    )

    assert summary["realized_trades"] == []
    assert summary["summary"]["total_realized_pnl"] == 0


def test_sell_without_buy_lot_marks_missing_cost_basis() -> None:
    summary = summarize_trade_performance(entries=[_entry(1, "NVO", "sell", 3, 90, "2026-01-05")])

    trade = summary["realized_trades"][0]
    assert trade["cost_basis_missing"] is True
    assert trade["realized_pnl"] is None
    assert trade["cost_basis_source"] == "missing"
    assert trade["cost_basis_status"] == "missing"
    assert trade["included_in_performance"] is False
    assert summary["summary"]["missing_cost_count"] == 1
    assert summary["summary"]["missing_cost_quantity"] == 3
    assert summary["summary"]["missing_cost_amount"] == 270
    assert summary["unmatched_sells"][0]["reason"] == "缺买入成本"


def test_position_snapshot_cost_basis_is_used_when_fifo_lot_is_missing() -> None:
    summary = summarize_trade_performance(
        entries=[
            _entry(
                1,
                "XE",
                "sell",
                4,
                80,
                "2026-01-05",
                pre_trade_avg_cost=50,
                pre_trade_position_tier="C",
                cost_basis_source="position_snapshot",
            )
        ]
    )

    trade = summary["realized_trades"][0]
    assert trade["cost_basis_missing"] is False
    assert trade["cost_basis_source"] == "position_snapshot"
    assert trade["cost_basis_status"] == "position_snapshot"
    assert trade["realized_pnl"] == 120
    assert trade["buy_avg_price"] == 50
    assert trade["holding_days"] is None
    assert trade["included_in_performance"] is True


def test_fifo_lot_takes_priority_over_position_snapshot() -> None:
    summary = summarize_trade_performance(
        entries=[
            _entry(1, "XE", "buy", 4, 40, "2026-01-01"),
            _entry(
                2,
                "XE",
                "sell",
                4,
                80,
                "2026-01-05",
                pre_trade_avg_cost=50,
                cost_basis_source="position_snapshot",
            ),
        ]
    )

    trade = summary["realized_trades"][0]
    assert trade["cost_basis_source"] == "fifo"
    assert trade["buy_avg_price"] == 40
    assert trade["realized_pnl"] == 160


def test_manual_opening_lot_is_used_only_for_performance_cost_basis() -> None:
    opening_lot = build_manual_cost_basis_lot(
        ticker="XE",
        quantity=4,
        avg_cost=45,
        buy_date="2026-01-01",
        position_tier="B",
        note="legacy import",
    )

    summary = summarize_trade_performance(
        entries=[_entry(1, "XE", "sell", 4, 80, "2026-01-05")],
        opening_lots=[opening_lot],
    )

    trade = summary["realized_trades"][0]
    assert trade["cost_basis_source"] == "manual_cost_basis"
    assert trade["position_tier"] == "B"
    assert trade["realized_pnl"] == 140


def test_holding_days_use_hkt_date_from_timestamp() -> None:
    assert calculate_holding_days("2026-01-01T23:30:00+00:00", "2026-01-03") == 1


def test_group_by_ticker_summary_is_correct() -> None:
    summary = summarize_trade_performance(
        entries=[
            _entry(1, "NVDA", "buy", 1, 100, "2026-01-01"),
            _entry(2, "NVDA", "sell", 1, 120, "2026-01-02"),
            _entry(3, "MSFT", "buy", 1, 100, "2026-01-01"),
            _entry(4, "MSFT", "sell", 1, 80, "2026-01-02"),
        ]
    )

    groups = {row["key"]: row for row in summary["groups"]["ticker"]}
    assert groups["NVDA"]["realized_pnl"] == 20
    assert groups["MSFT"]["realized_pnl"] == -20


def test_group_by_position_tier_summary_is_correct() -> None:
    summary = summarize_trade_performance(
        entries=[
            _entry(1, "NVDA", "buy", 1, 100, "2026-01-01", position_class="A"),
            _entry(2, "NVDA", "sell", 1, 120, "2026-01-11", position_class="A"),
            _entry(3, "NOK", "buy", 1, 10, "2026-01-01", position_class="C"),
            _entry(4, "NOK", "sell", 1, 9, "2026-01-05", position_class="C"),
        ]
    )

    groups = {row["key"]: row for row in summary["groups"]["position_tier"]}
    assert groups["A"]["realized_pnl"] == 20
    assert groups["A"]["average_holding_days"] == 10
    assert groups["A"]["discipline_issue_count"] == 1
    assert groups["C"]["realized_pnl"] == -1
    assert groups["C"]["average_holding_days"] == 4
    assert groups["C"]["average_loser"] == -1


def test_missing_position_tier_legacy_records_are_grouped_safely() -> None:
    summary = summarize_trade_performance(
        entries=[
            _entry(1, "BSX", "buy", 1, 100, "2026-01-01"),
            _entry(2, "BSX", "sell", 1, 110, "2026-01-03"),
        ]
    )

    groups = {row["key"]: row for row in summary["groups"]["position_tier"]}
    assert groups["等级缺失"]["realized_pnl"] == 10


def test_group_by_mood_summary_is_correct() -> None:
    summary = summarize_trade_performance(
        entries=[
            _entry(1, "NVDA", "buy", 1, 100, "2026-01-01", decision_mood="plan_execution"),
            _entry(2, "NVDA", "sell", 1, 120, "2026-01-02", decision_mood="macro_fear"),
        ]
    )

    assert summary["groups"]["buy_mood"][0]["key"] == "plan_execution"
    assert summary["groups"]["sell_mood"][0]["key"] == "macro_fear"
