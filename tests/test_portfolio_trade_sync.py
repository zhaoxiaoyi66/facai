from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from data.decision_log import TradeJournalStore
from data.portfolio import PortfolioPositionStore, PortfolioSettingsStore
from data.portfolio_trade_sync import (
    apply_trade_to_portfolio,
    preview_trade_portfolio_effect,
    unsynced_trade_counts_by_symbol,
)
from data.trade_safety_gate import trade_sync_policy
from data.portfolio_view_model import build_portfolio_view_model


def _db(tmpdir: str) -> Path:
    return Path(tmpdir) / "portfolio_trade_sync.sqlite"


def _insert_history(path: Path, ticker: str, close: float, fetched_at: str) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                close REAL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (ticker, date)
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO price_history VALUES (?, ?, ?, ?)",
            (ticker.upper(), "2026-05-30", close, fetched_at),
        )
        conn.commit()


def _radar_allowed() -> dict:
    return {
        "radarDecision": "ALLOW_BUY",
        "radarBlocked": False,
        "radarObservationOnly": False,
        "radarBlockReasons": [],
        "gateCheckedAt": "2026-05-30T12:00:00+00:00",
        "positionClass": "A",
        "corePositionMinPct": 0.6,
        "tradingPositionMaxPct": 0.4,
    }


def test_buy_trade_sync_creates_or_increases_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "NOW",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 10, "price": 500, **_radar_allowed()},
        )

        preview = preview_trade_portfolio_effect(entry["id"], path)
        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("NOW")

        assert preview["currentQuantity"] == 0
        assert preview["afterQuantity"] == 10
        assert result["status"] == "success"
        assert position["quantity"] == 10
        assert position["average_cost"] == 500


def test_legacy_radar_blocked_price_zone_buy_can_sync_when_snapshot_is_complete() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "NVDA",
            {
                "trade_date": "2026-05-30",
                "action_type": "buy",
                "quantity": 10,
                "price": 210,
                "radarDecision": "BLOCK_CHASE",
                "radarBlocked": True,
                "radarBlockReasons": ["当前价进入追高禁止区"],
                "moodGateBlocked": False,
                "positionGateBlocked": False,
                "gateCheckedAt": "2026-05-30T12:00:00+00:00",
                "positionClass": "A",
            },
        )

        preview = preview_trade_portfolio_effect(entry["id"], path)
        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("NVDA")
        counts = unsynced_trade_counts_by_symbol(path)

        assert preview["status"] == "ready"
        assert result["status"] == "success"
        assert position is not None
        assert position["quantity"] == 10
        assert counts.get("NVDA", 0) == 0


def test_buy_with_incomplete_planned_ladder_snapshot_can_sync_as_advisory() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "NOK",
            {
                "trade_date": "2026-05-30",
                "action_type": "buy",
                "quantity": 50,
                "price": 4.8,
                "radarDecision": "BLOCK_CHASE",
                "radarDataStatus": "OK",
                "radarIsStale": False,
                "radarBlocked": False,
                "radarBlockReasons": ["current price is in or above chase zone"],
                "gateCheckedAt": "2026-05-30T12:00:00+00:00",
                "positionClass": "C",
                "plannedLadderBuy": True,
                "planMatchStatus": "allow_planned_add",
                "buyPlanId": "NOK",
            },
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("NOK")

        assert result["status"] == "success"
        assert position is not None
        assert position["quantity"] == 50


def test_data_missing_radar_buy_can_sync_with_valid_planned_ladder_snapshot() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "NOK",
            {
                "trade_date": "2026-05-30",
                "action_type": "buy",
                "quantity": 50,
                "price": 4.8,
                "radarDecision": "DATA_MISSING",
                "radarDataStatus": "DATA_MISSING",
                "radarIsStale": False,
                "radarBlocked": False,
                "radarBlockReasons": [],
                "gateCheckedAt": "2026-05-30T12:00:00+00:00",
                "positionClass": "C",
                "plannedLadderBuy": True,
                "planMatchStatus": "allow_planned_add",
                "buyPlanId": "NOK",
                "buyPlanLevel": "第一笔买入",
                "planTriggerPrice": 5,
                "planRemainingQuantity": 100,
                "planMaxPositionPct": 12,
                "planBlockReasons": [],
            },
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("NOK")

        assert result["status"] == "success"
        assert position is not None
        assert position["quantity"] == 50


def test_stale_radar_buy_can_sync_with_valid_planned_ladder_snapshot() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "NOK",
            {
                "trade_date": "2026-05-30",
                "action_type": "add",
                "quantity": 50,
                "price": 4.8,
                "radarDecision": "BLOCK_CHASE",
                "radarDataStatus": "OK",
                "radarIsStale": True,
                "radarBlocked": False,
                "radarBlockReasons": [],
                "gateCheckedAt": "2026-05-30T12:00:00+00:00",
                "positionClass": "C",
                "plannedLadderBuy": True,
                "planMatchStatus": "allow_planned_add",
                "buyPlanId": "NOK",
                "buyPlanLevel": "第一笔买入",
                "planTriggerPrice": 5,
                "planRemainingQuantity": 100,
                "planMaxPositionPct": 12,
                "planBlockReasons": [],
            },
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("NOK")

        assert result["status"] == "success"
        assert position is not None
        assert position["quantity"] == 50


def test_planned_ladder_snapshot_with_block_reasons_can_sync_as_advisory() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "NOK",
            {
                "trade_date": "2026-05-30",
                "action_type": "buy",
                "quantity": 50,
                "price": 4.8,
                "radarDecision": "BLOCK_CHASE",
                "radarDataStatus": "OK",
                "radarIsStale": False,
                "radarBlocked": False,
                "radarBlockReasons": [],
                "gateCheckedAt": "2026-05-30T12:00:00+00:00",
                "positionClass": "C",
                "plannedLadderBuy": True,
                "planMatchStatus": "allow_planned_add",
                "buyPlanId": "NOK",
                "buyPlanLevel": "第一笔买入",
                "planTriggerPrice": 5,
                "planRemainingQuantity": 100,
                "planMaxPositionPct": 12,
                "planBlockReasons": ["计划仍有阻断原因"],
            },
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("NOK")

        assert result["status"] == "success"
        assert position is not None
        assert position["quantity"] == 50


def test_buy_with_incomplete_starter_snapshot_can_sync_as_advisory() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "AVGO",
            {
                "trade_date": "2026-05-30",
                "action_type": "buy",
                "quantity": 25,
                "price": 406,
                "radarDecision": "BLOCK_CHASE",
                "radarDataStatus": "OK",
                "radarIsStale": False,
                "radarBlocked": False,
                "radarBlockReasons": ["current price is in or above chase zone"],
                "gateCheckedAt": "2026-05-30T12:00:00+00:00",
                "positionClass": "A",
                "entryMode": "starter_position",
                "starterPosition": True,
                "starterMatchStatus": "allow_starter_position",
                "starterMaxPct": 7,
            },
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("AVGO")

        assert result["status"] == "success"
        assert position is not None
        assert position["quantity"] == 25


def test_wait_radar_buy_with_valid_starter_snapshot_can_sync() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "AVGO",
            {
                "trade_date": "2026-05-30",
                "action_type": "buy",
                "quantity": 25,
                "price": 406,
                "radarDecision": "WAIT",
                "radarDataStatus": "OK",
                "radarIsStale": False,
                "radarBlocked": False,
                "radarBlockReasons": [
                    "current price is above the discipline buy zone",
                    "财报后大跌 / 高波动：不等同于追高，但仍需遵守底仓上限。",
                ],
                "gateCheckedAt": "2026-05-30T12:00:00+00:00",
                "positionClass": "A",
                "entryMode": "starter_position",
                "starterPosition": True,
                "starterMatchStatus": "allow_starter_position",
                "starterMaxPct": 7,
                "starterPositionBeforePct": 0,
                "starterPositionAfterPct": 6.8,
                "starterBlockReasons": [],
            },
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("AVGO")

        assert result["status"] == "success"
        assert position is not None
        assert position["quantity"] == 25


def test_block_chase_radar_buy_can_sync_with_starter_snapshot_warning() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "AVGO",
            {
                "trade_date": "2026-05-30",
                "action_type": "buy",
                "quantity": 25,
                "price": 406,
                "radarDecision": "BLOCK_CHASE",
                "radarDataStatus": "OK",
                "radarIsStale": False,
                "radarBlocked": False,
                "radarBlockReasons": ["current price is in or above chase zone"],
                "gateCheckedAt": "2026-05-30T12:00:00+00:00",
                "positionClass": "A",
                "entryMode": "starter_position",
                "starterPosition": True,
                "starterMatchStatus": "allow_starter_position",
                "starterMaxPct": 7,
                "starterPositionBeforePct": 0,
                "starterPositionAfterPct": 6.8,
                "starterBlockReasons": [],
            },
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("AVGO")

        assert result["status"] == "success"
        assert position is not None
        assert position["quantity"] == 25


def test_starter_snapshot_with_block_reasons_can_sync_as_advisory() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "AVGO",
            {
                "trade_date": "2026-05-30",
                "action_type": "buy",
                "quantity": 25,
                "price": 406,
                "radarDecision": "WAIT",
                "radarDataStatus": "OK",
                "radarIsStale": False,
                "radarBlocked": False,
                "radarBlockReasons": ["current price is above the discipline buy zone"],
                "gateCheckedAt": "2026-05-30T12:00:00+00:00",
                "positionClass": "A",
                "entryMode": "starter_position",
                "starterPosition": True,
                "starterMatchStatus": "allow_starter_position",
                "starterMaxPct": 7,
                "starterPositionBeforePct": 0,
                "starterPositionAfterPct": 6.8,
                "starterBlockReasons": ["缺少后续加仓计划"],
            },
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("AVGO")

        assert result["status"] == "success"
        assert position is not None
        assert position["quantity"] == 25


def test_radar_observation_only_buy_syncs_with_advisory_snapshot() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "MSFT",
            {
                "trade_date": "2026-05-30",
                "action_type": "add",
                "quantity": 3,
                "price": 420,
                "radarDecision": "ALLOW_BUY",
                "radarBlocked": False,
                "radarObservationOnly": True,
                "radarBlockReasons": [],
                "gateCheckedAt": "2026-05-30T12:00:00+00:00",
            },
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("MSFT")

        assert result["status"] == "success"
        assert position is not None
        assert position["quantity"] == 3


def test_missing_radar_gate_buy_can_sync_when_basic_fields_exist() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "TSLA",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 1, "price": 250, "positionClass": "B"},
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("TSLA")

        assert bool(entry["radar_blocked"]) is False
        assert bool(entry["radar_advisory_only"]) is True
        assert entry["radar_decision"] == "DATA_MISSING"
        assert result["status"] == "success"
        assert position is not None
        assert position["quantity"] == 1


def test_radar_passed_buy_can_still_sync_to_portfolio() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "AAPL",
            {
                "trade_date": "2026-05-30",
                "action_type": "buy",
                "quantity": 2,
                "price": 190,
                **_radar_allowed(),
            },
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("AAPL")

        assert result["status"] == "success"
        assert position["quantity"] == 2
        assert position["average_cost"] == 190


def test_editing_buy_entry_to_radar_price_zone_block_keeps_advisory_sync() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        store = TradeJournalStore(path)
        entry = store.save_entry(
            "AMD",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 2, "price": 120, **_radar_allowed()},
        )
        updated = store.update_entry(
            entry["id"],
            "AMD",
            {
                "trade_date": "2026-05-30",
                "action_type": "buy",
                "quantity": 2,
                "price": 120,
                "radarDecision": "BLOCK_CHASE",
                "radarBlocked": True,
                "radarBlockReasons": ["当前价进入追高禁止区"],
                "gateCheckedAt": "2026-05-30T12:10:00+00:00",
                "positionClass": "A",
                },
            )

        result = apply_trade_to_portfolio(updated["id"], path)
        position = PortfolioPositionStore(path).get_position("AMD")

        assert bool(updated["radar_blocked"]) is False
        assert bool(updated["radar_advisory_only"]) is True
        assert bool(updated["gate_hard_blocked"]) is False
        assert result["status"] == "success"
        assert position is not None
        assert position["quantity"] == 2


def test_trade_sync_preview_uses_market_context_price() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioSettingsStore(path).save_settings({"total_portfolio_value": 10000})
        _insert_history(path, "CRWV", 60, "2026-05-28T10:00:00+00:00")
        _insert_history(path, "FMP:CRWV", 70, "2026-05-30T10:00:00+00:00")
        entry = TradeJournalStore(path).save_entry(
            "CRWV",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 2, "price": 50, **_radar_allowed()},
        )

        preview = preview_trade_portfolio_effect(entry["id"], path)

        assert preview["afterMarketValue"] == 140
        assert preview["afterPositionPct"] == pytest.approx(1.4)


def test_add_trade_sync_updates_weighted_average_cost() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("MSFT", {"quantity": 10, "average_cost": 100})
        entry = TradeJournalStore(path).save_entry(
            "MSFT",
            {"trade_date": "2026-05-30", "action_type": "add", "quantity": 10, "price": 200, **_radar_allowed()},
        )

        apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("MSFT")

        assert position["quantity"] == 20
        assert position["average_cost"] == 150


def test_buy_sync_reopens_archived_position_from_zero() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        position_store = PortfolioPositionStore(path)
        position_store.save_position("NVDA", {"quantity": 158, "average_cost": 100})
        position_store.deactivate_position("NVDA")
        entry = TradeJournalStore(path).save_entry(
            "NVDA",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 1, "price": 120, **_radar_allowed()},
        )

        preview = preview_trade_portfolio_effect(entry["id"], path)
        result = apply_trade_to_portfolio(entry["id"], path)
        position = position_store.get_position("NVDA")

        assert preview["currentQuantity"] == 0
        assert preview["afterQuantity"] == 1
        assert result["status"] == "success"
        assert position["is_active"] == 1
        assert position["quantity"] == 1
        assert position["average_cost"] == 120


def test_sell_trade_sync_reduces_position_without_changing_average_cost() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 10, "average_cost": 100})
        entry = TradeJournalStore(path).save_entry(
            "NVDA",
            {"trade_date": "2026-05-30", "action_type": "sell", "quantity": 4, "price": 200},
        )

        apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("NVDA")

        assert position["quantity"] == 6
        assert position["average_cost"] == 100


def test_full_exit_must_come_from_sell_trade_sync() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 5, "average_cost": 100, "position_tier": "A"})
        entry = TradeJournalStore(path).save_entry(
            "NVDA",
            {"trade_date": "2026-05-30", "action_type": "sell", "quantity": 5, "price": 150},
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("NVDA")

        assert result["status"] == "success"
        assert position is not None
        assert position["quantity"] == 0
        assert position["is_active"] is False
        assert PortfolioPositionStore(path).list_active_positions() == []

        view = build_portfolio_view_model(path, {"NVDA": 150})
        assert view["summary"]["positionCount"] == 0
        assert view["rows"] == []


def test_model_warning_sell_can_sync_to_portfolio() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 158, "average_cost": 100})
        entry = TradeJournalStore(path).save_entry(
            "NVDA",
            {
                "trade_date": "2026-05-30",
                "action_type": "sell",
                "quantity": 100,
                "price": 200,
                "currentPositionQuantity": 158,
                "positionClass": "A",
                "corePositionPct": 0.6,
                "tradingPositionPct": 0.4,
                "plannedSellPct": 0.1,
                "sellReasonType": "macro",
                "thesisBroken": False,
                "positionOverLimit": False,
                "reentryPullbackPrice": 180,
                "reentryBreakoutPrice": 205,
                "reentryPlanText": "回踩或重新站回卖出价时分批买回",
            },
        )

        preview = preview_trade_portfolio_effect(entry["id"], path)
        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("NVDA")

        assert entry["discipline_status"] == "warning"
        assert entry["blockers"] == []
        assert entry["sell_warning_level"] == "HIGH_RISK"
        assert entry["sell_blocked"] is False
        assert entry["user_confirmed_sell_warning"] is False
        assert preview["status"] == "ready"
        assert result["status"] == "success"
        assert position["quantity"] == 58
        assert position["average_cost"] == 100


def test_legacy_blocker_json_sell_can_sync_as_advisory() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 158, "average_cost": 100})
        entry = TradeJournalStore(path).save_entry(
            "NVDA",
            {"trade_date": "2026-05-30", "action_type": "sell", "quantity": 50, "price": 200},
        )
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                """
                UPDATE trade_journal_entries
                SET discipline_status = NULL,
                    blockers_json = '["legacy_blocker"]'
                WHERE id = ?
                """,
                (entry["id"],),
            )
            conn.commit()

        preview = preview_trade_portfolio_effect(entry["id"], path)
        result = apply_trade_to_portfolio(entry["id"], path)
        counts = unsynced_trade_counts_by_symbol(path)
        position = PortfolioPositionStore(path).get_position("NVDA")

        assert preview["status"] == "ready"
        assert result["status"] == "success"
        assert counts.get("NVDA", 0) == 0
        assert position["quantity"] == 108


def test_trade_sync_policy_treats_sell_blocker_lists_as_advisory() -> None:
    sell_policy = trade_sync_policy({"action_type": "sell", "blockers": ["legacy_blocker"]})
    buy_policy = trade_sync_policy({"action_type": "buy", "blockers": ["legacy_blocker"], "radar_decision": "ALLOW_BUY", "gate_checked_at": "2026-05-30T12:00:00+00:00", "position_class": "A"})
    missing_tier_policy = trade_sync_policy({"action_type": "buy", "radar_decision": "ALLOW_BUY", "gate_checked_at": "2026-05-30T12:00:00+00:00"})
    missing_gate_policy = trade_sync_policy({"action_type": "buy", "blockers": ["legacy_blocker"], "position_class": "A"})
    radar_policy = trade_sync_policy({"action_type": "buy", "radar_blocked": 1})
    legacy_price_zone_policy = trade_sync_policy(
        {
            "action_type": "buy",
            "radar_blocked": 1,
            "radar_decision": "BLOCK_CHASE",
            "gate_checked_at": "2026-05-30T12:00:00+00:00",
            "position_class": "A",
            "mood_gate_blocked": 0,
            "position_gate_blocked": 0,
        }
    )
    observation_policy = trade_sync_policy({"action_type": "add", "radar_observation_only": 1})

    assert sell_policy["canSync"] is True
    assert sell_policy["reason"] == ""
    assert buy_policy["canSync"] is True
    assert missing_tier_policy["canSync"] is True
    assert missing_gate_policy["canSync"] is True
    assert missing_gate_policy["reason"] == ""
    assert radar_policy["canSync"] is True
    assert radar_policy["reason"] == ""
    assert legacy_price_zone_policy["canSync"] is True
    assert observation_policy["canSync"] is True


def test_trade_sync_policy_allows_advisory_radar_zone_warnings() -> None:
    policy = trade_sync_policy(
        {
            "action_type": "buy",
            "position_class": "A",
            "radar_decision": "BLOCK_CHASE",
            "gate_checked_at": "2026-05-30T12:00:00+00:00",
            "gate_hard_blocked": 0,
            "radar_advisory_only": 1,
            "radar_advisory_warnings_json": '["追高风险"]',
        }
    )

    assert policy["canSync"] is True
    assert policy["reason"] == ""


def test_trade_sync_policy_treats_hard_buy_gate_flag_as_advisory() -> None:
    policy = trade_sync_policy(
        {
            "action_type": "buy",
            "position_class": "A",
            "radar_decision": "ALLOW_BUY",
            "gate_checked_at": "2026-05-30T12:00:00+00:00",
            "gate_hard_blocked": 1,
            "radar_advisory_only": 0,
        }
    )

    assert policy["canSync"] is True
    assert policy["reason"] == ""


def test_trim_cannot_sync_more_than_current_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("ADBE", {"quantity": 3, "average_cost": 400})
        entry = TradeJournalStore(path).save_entry(
            "ADBE",
            {"trade_date": "2026-05-30", "action_type": "trim", "quantity": 5, "price": 450},
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("ADBE")

        assert result["status"] == "failed"
        assert "超过当前组合持仓" in result["error"]
        assert position["quantity"] == 3


def test_same_trade_cannot_sync_twice() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        entry = TradeJournalStore(path).save_entry(
            "CRM",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 2, "price": 100, **_radar_allowed()},
        )

        first = apply_trade_to_portfolio(entry["id"], path)
        second = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("CRM")

        assert first["status"] == "success"
        assert second["status"] == "already_synced"
        assert position["quantity"] == 2


def test_synced_trade_entry_cannot_be_deleted_without_reconciliation() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        store = TradeJournalStore(path)
        entry = store.save_entry(
            "CRM",
            {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 2, "price": 100, **_radar_allowed()},
        )

        apply_trade_to_portfolio(entry["id"], path)

        assert store.delete_entry_block_reason(entry["id"])
        assert store.delete_entry(entry["id"]) is False
        assert store.get_entry(entry["id"]) is not None


def test_skip_sync_does_not_change_portfolio() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("HOOD", {"quantity": 7, "average_cost": 50})
        entry = TradeJournalStore(path).save_entry(
            "HOOD",
            {"trade_date": "2026-05-30", "action_type": "skip", "quantity": 1, "price": 55},
        )

        result = apply_trade_to_portfolio(entry["id"], path)
        position = PortfolioPositionStore(path).get_position("HOOD")

        assert result["status"] == "success"
        assert result["actionType"] == "skip"
        assert position["quantity"] == 7


def test_unsynced_trade_does_not_change_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("VST", {"quantity": 8, "average_cost": 80})
        TradeJournalStore(path).save_entry(
            "VST",
            {"trade_date": "2026-05-30", "action_type": "add", "quantity": 2, "price": 90},
        )

        position = PortfolioPositionStore(path).get_position("VST")

        assert position["quantity"] == 8
        assert position["average_cost"] == 80


def test_portfolio_view_model_flags_unsynced_trades_for_symbol() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("ANET", {"quantity": 5, "average_cost": 300})
        entry = TradeJournalStore(path).save_entry(
            "ANET",
            {"trade_date": "2026-05-30", "action_type": "add", "quantity": 1, "price": 310, **_radar_allowed()},
        )

        before = build_portfolio_view_model(path, {"ANET": 320})
        counts = unsynced_trade_counts_by_symbol(path)
        apply_trade_to_portfolio(entry["id"], path)
        after = build_portfolio_view_model(path, {"ANET": 320})

        assert counts["ANET"] == 1
        assert before["rows"][0]["unsyncedTradeCount"] == 1
        assert after["rows"][0]["unsyncedTradeCount"] == 0


def test_model_warning_sell_is_counted_as_actionable_unsynced_trade() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 158, "average_cost": 100})
        TradeJournalStore(path).save_entry(
            "NVDA",
            {
                "trade_date": "2026-05-30",
                "action_type": "sell",
                "quantity": 100,
                "price": 200,
                "currentPositionQuantity": 158,
                "positionClass": "A",
                "corePositionPct": 0.6,
                "tradingPositionPct": 0.4,
                "plannedSellPct": 0.1,
                "sellReasonType": "macro",
                "thesisBroken": False,
                "positionOverLimit": False,
            },
        )

        counts = unsynced_trade_counts_by_symbol(path)
        view = build_portfolio_view_model(path, {"NVDA": 210})

        assert counts.get("NVDA", 0) == 1
        assert view["rows"][0]["unsyncedTradeCount"] == 1
