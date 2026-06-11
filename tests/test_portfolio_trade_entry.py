from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import pytest

import data.portfolio_trade_entry as portfolio_trade_entry
from data.decision_log import TradeJournalStore
from data.portfolio import PortfolioPositionStore
from data.portfolio_trade_entry import submit_portfolio_buy_add
from data.stock_plan import StockPlanStore, get_buy_plan_status


def _report(decision: str = "ALLOW_BUY") -> dict:
    return {
        "ticker": "NVDA",
        "decision": decision,
        "current_price": 100,
        "buy_zone": [90, 110],
        "watch_zone": [110, 130],
        "chase_zone": [140, 999],
        "final_score": 88,
        "valuation_score": 72,
        "core_max_pct": 20,
        "trade_max_pct": 8,
        "allowed_add_pct": 3,
        "block_reasons": ["当前价进入追高禁止区"] if decision == "BLOCK_CHASE" else [],
        "data_status": "OK",
        "is_stale": False,
    }


def _base_values(**overrides):
    values = {
        "quantity": 2,
        "price": 100,
        "position_tier": "A",
        "decision_mood": "plan_execution",
        "buy_reason": "按计划执行",
        "target_sell_price": 180,
    }
    values.update(overrides)
    return values


def _save_ladder_plan(path: Path, symbol: str = "NOK", **overrides) -> dict:
    values = {
        "target_position_pct": 12,
        "invalidation_condition": "跌破财报 thesis 或事件失效则停止加仓",
        "notes": "下跌分批买入，按计划执行",
        "buy_plan_tranches": [
            {"label": "第一笔买入", "price": 5, "shares": 100, "note": "第一档"},
            {"label": "第二笔买入", "price": 4, "shares": 100, "note": "第二档"},
        ],
    }
    values.update(overrides)
    StockPlanStore(path).save_plan(symbol, values)
    _set_plan_timestamps(
        path,
        symbol,
        created_at="2026-06-01T00:00:00+00:00",
        updated_at="2026-06-01T00:00:00+00:00",
        material_updated_at="2026-06-01T00:00:00+00:00",
    )
    return StockPlanStore(path).get_plan(symbol)


def _set_plan_timestamps(
    path: Path,
    symbol: str,
    *,
    created_at: str | None = None,
    updated_at: str | None = None,
    material_updated_at: str | None = None,
) -> None:
    assignments = []
    values: list[object] = []
    if created_at is not None:
        assignments.append("created_at = ?")
        values.append(created_at)
    if updated_at is not None:
        assignments.append("updated_at = ?")
        values.append(updated_at)
        if material_updated_at is None:
            material_updated_at = updated_at
    if material_updated_at is not None:
        assignments.append("material_updated_at = ?")
        values.append(material_updated_at)
    if not assignments:
        return
    values.append(symbol.upper())
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            f"UPDATE stock_action_plans SET {', '.join(assignments)} WHERE ticker = ?",
            values,
        )
        conn.commit()


def _blocked_chase_report(**overrides) -> dict:
    report = _report("BLOCK_CHASE")
    report.update(
        {
            "ticker": "NOK",
            "current_price": 4.8,
            "valuation_score": 42,
            "final_score": 64,
            "trade_max_pct": 0,
            "allowed_add_pct": 0,
            "block_reasons": [
                "current price is above the discipline buy zone",
                "current price is in or above chase zone",
            ],
        }
    )
    report.update(overrides)
    return report


def test_stock_plan_old_schema_adds_created_at_column_without_crashing() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                """
                CREATE TABLE stock_action_plans (
                    ticker TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO stock_action_plans (ticker, updated_at) VALUES (?, ?)",
                ("NOK", "2026-06-01T00:00:00+00:00"),
            )
            conn.commit()

        store = StockPlanStore(path)
        plan = store.get_plan("NOK")
        with closing(sqlite3.connect(path)) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(stock_action_plans)").fetchall()}

        assert "created_at" in columns
        assert "material_updated_at" in columns
        assert plan["ticker"] == "NOK"
        assert plan["created_at"] is None
        assert plan["material_updated_at"] is None
        assert plan["updated_at"] == "2026-06-01T00:00:00+00:00"


def test_stock_plan_save_preserves_created_at_and_updates_updated_at() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path)
        _set_plan_timestamps(
            path,
            "NOK",
            created_at="2026-06-01T00:00:00+00:00",
            updated_at="2026-06-01T00:00:00+00:00",
        )

        updated = StockPlanStore(path).save_plan(
            "NOK",
            {
                "target_position_pct": 8,
                "invalidation_condition": "thesis 失效停止",
                "buy_plan_tranches": [{"label": "第一笔买入", "price": 5, "shares": 50}],
            },
        )

        assert updated["created_at"] == "2026-06-01T00:00:00+00:00"
        assert updated["material_updated_at"] != "2026-06-01T00:00:00+00:00"
        assert updated["updated_at"] != "2026-06-01T00:00:00+00:00"


def test_stock_plan_save_can_preserve_material_updated_at_for_pause_notes() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path)
        _set_plan_timestamps(
            path,
            "NOK",
            created_at="2026-06-01T00:00:00+00:00",
            updated_at="2026-06-01T01:00:00+00:00",
            material_updated_at="2026-06-01T00:30:00+00:00",
        )

        plan = StockPlanStore(path).get_plan("NOK")
        plan["notes"] = "暂缓 / 不买：价格到位但需复核"
        updated = StockPlanStore(path).save_plan("NOK", plan)

        assert updated["created_at"] == "2026-06-01T00:00:00+00:00"
        assert updated["material_updated_at"] == "2026-06-01T00:30:00+00:00"
        assert updated["updated_at"] != "2026-06-01T01:00:00+00:00"


def test_stock_plan_saves_buy_plan_metadata_and_statuses() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        plan = StockPlanStore(path).save_plan(
            "AVGO",
            {
                "plan_type": "ladder_buy",
                "position_class": "A",
                "target_position_pct": 7,
                "target_sell_price": 460,
                "thesis": "A class core starter",
                "follow_up_plan": "add only at the next plan level",
                "invalidation_condition": "AI order thesis breaks",
                "buy_plan_tranches": [{"label": "first", "price": 400, "shares": 25, "note": "starter"}],
            },
        )

        waiting = get_buy_plan_status(plan, current_price=420)
        near = get_buy_plan_status(plan, current_price=411)
        triggered = get_buy_plan_status(plan, current_price=400)
        executed = get_buy_plan_status(plan, current_price=390, prior_level_quantities={"first": 25})

        assert plan["plan_type"] == "ladder_buy"
        assert plan["target_sell_price"] == 460
        assert plan["thesis"] == "A class core starter"
        assert waiting["status"] == "waiting"
        assert near["status"] == "near_trigger"
        assert triggered["status"] == "triggered"
        assert executed["status"] == "executed"


def test_stock_plan_status_requires_thesis_and_invalidation() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        plan = StockPlanStore(path).save_plan(
            "NOK",
            {
                "plan_type": "ladder_buy",
                "buy_plan_tranches": [{"label": "first", "price": 5, "shares": 100}],
            },
        )

        status = get_buy_plan_status(plan, current_price=4.9)

        assert status["status"] == "needs_review"
        assert "thesis" in status["message"]


def test_buy_plan_form_accepts_minimal_ladder_plan_values() -> None:
    from ui.portfolio import _validate_buy_plan_form_values

    _validate_buy_plan_form_values(
        "NOK",
        {
            "position_class": "B",
            "plan_type": "ladder_buy",
            "max_position_pct": 8,
            "target_sell_price": "",
            "thesis": "跌到计划区间分批买",
            "invalidation_condition": "thesis 破裂",
            "buy_plan_tranches": [{"label": "第 1 档", "price": 15.5, "shares": 200}],
        },
    )


def test_buy_plan_form_rejects_invalid_level_with_clear_error() -> None:
    from ui.portfolio import _validate_buy_plan_form_values

    with pytest.raises(ValueError, match="第 1 档触发价必须大于 0"):
        _validate_buy_plan_form_values(
            "NOK",
            {
                "position_class": "B",
                "plan_type": "ladder_buy",
                "max_position_pct": 8,
                "invalidation_condition": "thesis 破裂",
                "buy_plan_tranches": [{"label": "第 1 档", "price": 0, "shares": 200}],
            },
        )


def test_buy_plan_timing_status_marks_recent_plan_for_review() -> None:
    from ui.portfolio import _buy_plan_cooldown_status

    plan = {
        "created_at": "2026-06-04T01:00:00+00:00",
        "material_updated_at": "2026-06-04T01:45:00+00:00",
        "updated_at": "2026-06-04T01:59:00+00:00",
    }

    fresh = _buy_plan_cooldown_status(plan, now=datetime(2026, 6, 4, 2, 0, tzinfo=timezone.utc))
    old = _buy_plan_cooldown_status(plan, now=datetime(2026, 6, 4, 2, 20, tzinfo=timezone.utc))

    assert fresh["met"] is True
    assert fresh["fresh"] is True
    assert fresh["label"] == "临时计划执行标记"
    assert old["met"] is True
    assert old["fresh"] is False
    assert old["label"] == "计划时间已记录"


def test_buy_plan_form_keeps_event_fields_inside_event_trade_branch() -> None:
    import inspect
    import ui.portfolio as portfolio

    source = inspect.getsource(portfolio._render_buy_plan_form)
    assert '== "event_trade"' in source
    assert "事件名称" in source
    assert "无反应退出" in source


def test_creating_buy_plan_does_not_create_trade_log_or_change_portfolio() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        StockPlanStore(path).save_plan(
            "AVGO",
            {
                "plan_type": "starter_position",
                "position_class": "A",
                "target_position_pct": 7,
                "target_sell_price": 460,
                "thesis": "A class starter",
                "follow_up_plan": "add later by plan",
                "invalidation_condition": "thesis breaks",
                "buy_plan_tranches": [{"label": "first", "price": 400, "shares": 25}],
            },
        )

        assert TradeJournalStore(path).list_entries("AVGO") == []
        assert PortfolioPositionStore(path).get_position("AVGO") is None


def test_portfolio_buy_add_allowed_creates_journal_and_syncs_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add("NVDA", _base_values(), path=path, radar_report=_report())

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        position = PortfolioPositionStore(path).get_position("NVDA")
        assert entry is not None
        assert entry["action_type"] == "buy"
        assert entry["position_class"] == "A"
        assert entry["target_sell_price"] == 180
        assert position is not None
        assert position["quantity"] == 2
        assert position["average_cost"] == 100
        assert position["position_tier"] == "A"
        assert position["planned_sell_price"] == 180


def test_portfolio_buy_add_records_hkt_trade_time(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime(2026, 6, 4, 15, 30, 12, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    monkeypatch.setattr(portfolio_trade_entry, "_hkt_now", lambda: fixed)

    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add("NVDA", _base_values(), path=path, radar_report=_report())

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["trade_date"] == "2026-06-04"
        assert entry["created_at"] == fixed.isoformat()
        assert entry["gate_checked_at"] == fixed.isoformat()


def test_portfolio_add_uses_add_action_for_existing_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 1, "average_cost": 90, "position_tier": "A"})

        result = submit_portfolio_buy_add("NVDA", _base_values(quantity=1, price=110), path=path, radar_report=_report())

        position = PortfolioPositionStore(path).get_position("NVDA")
        assert result["entry"]["action_type"] == "add"
        assert position is not None
        assert position["quantity"] == 2
        assert round(position["average_cost"], 2) == 100


def test_blocked_gate_saves_journal_but_does_not_sync_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add("NVDA", _base_values(), path=path, radar_report=_report("BLOCK_CHASE"))

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("NVDA") is None


def test_planned_ladder_buy_can_sync_when_radar_blocks_chase_but_plan_matches() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path)

        result = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=50, price=4.8, position_tier="C", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        position = PortfolioPositionStore(path).get_position("NOK")
        assert entry is not None
        assert position is not None
        assert result["synced"] is True
        assert entry["radar_decision"] == "BLOCK_CHASE"
        assert not entry["radar_blocked"]
        assert entry["planned_ladder_buy"]
        assert entry["plan_match_status"] == "allow_planned_add"
        assert entry["buy_plan_level"] == "第一笔买入"
        assert entry["plan_trigger_price"] == 5
        assert entry["plan_planned_quantity"] == 100
        assert entry["plan_remaining_quantity"] == 100
        assert entry["plan_max_position_pct"] == 12
        assert position["quantity"] == 50


def test_planned_ladder_buy_allows_freshly_created_plan_and_marks_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime(2026, 6, 4, 10, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    monkeypatch.setattr(portfolio_trade_entry, "_hkt_now", lambda: fixed)
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path)
        _set_plan_timestamps(
            path,
            "NOK",
            created_at="2026-06-04T01:59:00+00:00",
            updated_at="2026-06-04T01:00:00+00:00",
            material_updated_at="2026-06-04T01:00:00+00:00",
        )

        result = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=50, price=4.8, position_tier="C", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        position = PortfolioPositionStore(path).get_position("NOK")
        assert entry is not None
        assert position is not None
        assert not entry["radar_blocked"]
        assert entry["plan_match_status"] == "allow_planned_add"
        assert entry["fresh_plan_execution"]
        assert entry["plan_recently_created_or_modified"]
        assert entry["plan_age_minutes"] == pytest.approx(1.0)
        assert result["synced"] is True


def test_planned_ladder_buy_allows_freshly_modified_plan_and_marks_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime(2026, 6, 4, 10, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    monkeypatch.setattr(portfolio_trade_entry, "_hkt_now", lambda: fixed)
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path)
        _set_plan_timestamps(
            path,
            "NOK",
            created_at="2026-06-04T01:00:00+00:00",
            updated_at="2026-06-04T01:59:00+00:00",
            material_updated_at="2026-06-04T01:59:00+00:00",
        )

        result = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=50, price=4.8, position_tier="C", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        position = PortfolioPositionStore(path).get_position("NOK")
        assert entry is not None
        assert position is not None
        assert not entry["radar_blocked"]
        assert entry["plan_match_status"] == "allow_planned_add"
        assert entry["fresh_plan_execution"]
        assert entry["plan_recently_created_or_modified"]
        assert entry["plan_age_minutes"] == pytest.approx(1.0)
        assert result["synced"] is True


def test_planned_ladder_buy_falls_back_to_updated_at_when_material_timestamp_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = datetime(2026, 6, 4, 10, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    monkeypatch.setattr(portfolio_trade_entry, "_hkt_now", lambda: fixed)
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path)
        _set_plan_timestamps(
            path,
            "NOK",
            created_at="2026-06-04T01:00:00+00:00",
            updated_at="2026-06-04T01:59:00+00:00",
            material_updated_at="2026-06-04T01:00:00+00:00",
        )
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("UPDATE stock_action_plans SET material_updated_at = NULL WHERE ticker = ?", ("NOK",))
            conn.commit()

        result = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=50, price=4.8, position_tier="C", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert not entry["radar_blocked"]
        assert entry["plan_match_status"] == "allow_planned_add"
        assert entry["fresh_plan_execution"]
        assert entry["plan_age_minutes"] == pytest.approx(1.0)
        assert result["synced"] is True


def test_planned_ladder_buy_keeps_non_material_note_update_as_review_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = datetime(2026, 6, 4, 10, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    monkeypatch.setattr(portfolio_trade_entry, "_hkt_now", lambda: fixed)
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path)
        _set_plan_timestamps(
            path,
            "NOK",
            created_at="2026-06-04T01:00:00+00:00",
            updated_at="2026-06-04T01:00:00+00:00",
            material_updated_at="2026-06-04T01:00:00+00:00",
        )
        plan = StockPlanStore(path).get_plan("NOK")
        plan["notes"] = "暂缓 / 不买：价格到位但需复核"
        StockPlanStore(path).save_plan("NOK", plan)

        result = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=50, price=4.8, position_tier="C", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        position = PortfolioPositionStore(path).get_position("NOK")
        assert entry is not None
        assert position is not None
        assert entry["plan_match_status"] == "allow_planned_add"
        assert not entry["fresh_plan_execution"]
        assert result["synced"] is True


def test_planned_ladder_buy_allows_missing_plan_timestamps_without_fresh_marker() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path)
        _set_plan_timestamps(path, "NOK", created_at="", updated_at="")

        result = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=50, price=4.8, position_tier="C", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        position = PortfolioPositionStore(path).get_position("NOK")
        assert entry is not None
        assert position is not None
        assert entry["plan_match_status"] == "allow_planned_add"
        assert not entry["fresh_plan_execution"]
        assert not entry["plan_recently_created_or_modified"]
        assert entry["plan_age_minutes"] is None
        assert result["synced"] is True


def test_planned_ladder_buy_does_not_sync_when_price_has_not_triggered_level() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path, buy_plan_tranches=[{"label": "第一笔买入", "price": 4, "shares": 100}])

        result = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=50, price=4.8, position_tier="C", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(current_price=4.8),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert not entry["planned_ladder_buy"]
        assert entry["plan_match_status"] == "not_triggered"
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("NOK") is None


def test_planned_ladder_buy_does_not_sync_when_quantity_exceeds_level() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path, buy_plan_tranches=[{"label": "第一笔买入", "price": 5, "shares": 10}])

        result = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=20, price=4.8, position_tier="C", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert entry["plan_match_status"] == "quantity_exceeds_level"
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("NOK") is None


def test_planned_ladder_buy_does_not_sync_when_after_position_exceeds_plan_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        portfolio_trade_entry,
        "preview_trade_values_portfolio_effect",
        lambda *args, **kwargs: {"afterPositionPct": 2.5},
    )
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(
            path,
            target_position_pct=0.01,
            buy_plan_tranches=[{"label": "第一笔买入", "price": 5, "shares": 100}],
        )

        result = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=50, price=4.8, position_tier="C", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert entry["plan_match_status"] == "position_exceeds_plan"
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("NOK") is None


def test_planned_ladder_buy_subtracts_previously_synced_same_level_quantity() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path, buy_plan_tranches=[{"label": "第一笔买入", "price": 5, "shares": 100}])

        first = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=60, price=4.8, position_tier="C", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )
        second = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=50, price=4.8, position_tier="C", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )

        second_entry = TradeJournalStore(path).get_entry(int(second["entry"]["id"]))
        position = PortfolioPositionStore(path).get_position("NOK")
        assert first["synced"] is True
        assert second_entry is not None
        assert second_entry["plan_match_status"] == "quantity_exceeds_level"
        assert second_entry["plan_remaining_quantity"] == 40
        assert second["sync"] is None
        assert position is not None
        assert position["quantity"] == 60


def test_planned_ladder_buy_blocked_and_observation_only_do_not_consume_level_quantity() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path, buy_plan_tranches=[{"label": "第一笔买入", "price": 5, "shares": 100}])

        blocked = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=100, price=4.8, position_tier="C", decision_mood="fomo", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )
        observed = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=100, price=4.8, position_tier="C", radar_observation_only=True, entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )
        allowed = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=100, price=4.8, position_tier="C", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )

        allowed_entry = TradeJournalStore(path).get_entry(int(allowed["entry"]["id"]))
        position = PortfolioPositionStore(path).get_position("NOK")
        assert blocked["sync"] is None
        assert observed["sync"] is None
        assert allowed_entry is not None
        assert allowed_entry["plan_remaining_quantity"] == 100
        assert allowed["synced"] is True
        assert position is not None
        assert position["quantity"] == 100


def test_planned_ladder_buy_does_not_sync_with_fomo_mood() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path)

        result = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=50, price=4.8, position_tier="C", decision_mood="fomo", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert not entry["planned_ladder_buy"]
        assert entry["plan_match_status"] == "mood_blocked"
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("NOK") is None


def test_planned_ladder_buy_does_not_sync_with_anxiety_mood() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path)

        result = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=50, price=4.8, position_tier="C", decision_mood="anxiety", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert not entry["planned_ladder_buy"]
        assert entry["plan_match_status"] == "mood_blocked"
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("NOK") is None


def test_planned_ladder_buy_does_not_sync_when_radar_data_missing_or_stale() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        _save_ladder_plan(path)

        result = submit_portfolio_buy_add(
            "NOK",
            _base_values(quantity=50, price=4.8, position_tier="C", entry_mode="planned_ladder_buy"),
            path=path,
            radar_report=_blocked_chase_report(decision="DATA_MISSING", data_status="DATA_MISSING", is_stale=True),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert not entry["planned_ladder_buy"]
        assert entry["plan_match_status"] == "data_missing"
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("NOK") is None


def test_a_class_starter_position_can_sync_when_small_and_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        portfolio_trade_entry,
        "preview_trade_values_portfolio_effect",
        lambda *args, **kwargs: {"currentQuantity": 0, "afterQuantity": 25, "afterPositionPct": 6.8},
    )
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add(
            "AVGO",
            _base_values(
                quantity=25,
                price=406,
                position_tier="A",
                entry_mode="starter_position",
                starter_thesis="A 类核心资产，AI 网络与定制芯片龙头",
                starter_add_plan="回到计划买区再加第二笔",
                starter_invalidation_condition="AI 订单或毛利率 thesis 破坏则停止",
                target_sell_price=460,
            ),
            path=path,
            radar_report=_blocked_chase_report(
                ticker="AVGO",
                current_price=406,
                decision="WAIT",
                valuation_score=42,
                block_reasons=["current price is above the discipline buy zone"],
            ),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        position = PortfolioPositionStore(path).get_position("AVGO")
        assert entry is not None
        assert result["synced"] is True
        assert position is not None
        assert not entry["radar_blocked"]
        assert entry["radar_decision"] == "WAIT"
        assert entry["entry_mode"] == "starter_position"
        assert entry["starter_position"]
        assert entry["starter_match_status"] == "allow_starter_position"
        assert entry["starter_position_after_pct"] == 6.8


def test_a_class_starter_position_in_chase_zone_requires_review_not_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        portfolio_trade_entry,
        "preview_trade_values_portfolio_effect",
        lambda *args, **kwargs: {"currentQuantity": 0, "afterQuantity": 25, "afterPositionPct": 6.8},
    )
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add(
            "AVGO",
            _base_values(
                quantity=25,
                price=406,
                position_tier="A",
                entry_mode="starter_position",
                starter_thesis="A 类核心资产，AI 网络与定制芯片龙头",
                starter_add_plan="回到计划买区再加第二笔",
                starter_invalidation_condition="AI 订单或毛利率 thesis 破坏则停止",
                target_sell_price=460,
            ),
            path=path,
            radar_report=_blocked_chase_report(ticker="AVGO", current_price=406, valuation_score=42),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert entry["starter_position"]
        assert entry["starter_match_status"] == "starter_review_required"
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("AVGO") is None


def test_a_class_starter_position_blocks_when_after_position_exceeds_starter_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        portfolio_trade_entry,
        "preview_trade_values_portfolio_effect",
        lambda *args, **kwargs: {"currentQuantity": 0, "afterQuantity": 25, "afterPositionPct": 7.2},
    )
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add(
            "AVGO",
            _base_values(
                quantity=25,
                price=406,
                position_tier="A",
                entry_mode="starter_position",
                starter_thesis="A 类核心资产",
                starter_add_plan="回买区再加",
                starter_invalidation_condition="thesis 破坏停止",
                target_sell_price=460,
            ),
            path=path,
            radar_report=_blocked_chase_report(ticker="AVGO", current_price=406, valuation_score=42),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert not entry["starter_position"]
        assert entry["starter_match_status"] == "starter_blocked"
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("AVGO") is None


def test_b_or_c_class_cannot_use_starter_position(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        portfolio_trade_entry,
        "preview_trade_values_portfolio_effect",
        lambda *args, **kwargs: {"currentQuantity": 0, "afterQuantity": 25, "afterPositionPct": 3.0},
    )
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add(
            "AVGO",
            _base_values(
                quantity=25,
                price=406,
                position_tier="B",
                entry_mode="starter_position",
                starter_thesis="想建底仓",
                starter_add_plan="回买区再加",
                starter_invalidation_condition="thesis 破坏停止",
                target_sell_price=460,
            ),
            path=path,
            radar_report=_blocked_chase_report(ticker="AVGO", current_price=406, valuation_score=42),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert entry["starter_match_status"] == "starter_blocked"
        assert result["sync"] is None


def test_starter_position_does_not_sync_with_fomo_mood(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        portfolio_trade_entry,
        "preview_trade_values_portfolio_effect",
        lambda *args, **kwargs: {"currentQuantity": 0, "afterQuantity": 25, "afterPositionPct": 3.0},
    )
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add(
            "AVGO",
            _base_values(
                quantity=25,
                price=406,
                position_tier="A",
                decision_mood="fomo",
                entry_mode="starter_position",
                starter_thesis="A 类核心资产",
                starter_add_plan="回买区再加",
                starter_invalidation_condition="thesis 破坏停止",
                target_sell_price=460,
            ),
            path=path,
            radar_report=_blocked_chase_report(
                ticker="AVGO",
                current_price=406,
                decision="WAIT",
                valuation_score=42,
                block_reasons=["current price is above the discipline buy zone"],
            ),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert entry["starter_match_status"] == "starter_blocked"
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("AVGO") is None


def test_starter_position_cannot_override_data_missing_or_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        portfolio_trade_entry,
        "preview_trade_values_portfolio_effect",
        lambda *args, **kwargs: {"currentQuantity": 0, "afterQuantity": 25, "afterPositionPct": 3.0},
    )
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add(
            "AVGO",
            _base_values(
                quantity=25,
                price=406,
                position_tier="A",
                entry_mode="starter_position",
                starter_thesis="A 类核心资产",
                starter_add_plan="回买区再加",
                starter_invalidation_condition="thesis 破坏停止",
                target_sell_price=460,
            ),
            path=path,
            radar_report=_blocked_chase_report(
                ticker="AVGO",
                current_price=406,
                decision="DATA_MISSING",
                data_status="DATA_MISSING",
                is_stale=True,
            ),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert entry["starter_match_status"] == "starter_blocked"
        assert result["sync"] is None


def test_starter_position_requires_thesis_add_plan_and_invalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        portfolio_trade_entry,
        "preview_trade_values_portfolio_effect",
        lambda *args, **kwargs: {"currentQuantity": 0, "afterQuantity": 25, "afterPositionPct": 3.0},
    )
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add(
            "AVGO",
            _base_values(quantity=25, price=406, position_tier="A", entry_mode="starter_position", target_sell_price=460),
            path=path,
            radar_report=_blocked_chase_report(ticker="AVGO", current_price=406, valuation_score=42),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_blocked"]
        assert entry["starter_match_status"] == "starter_blocked"
        reasons = json.loads(entry["radar_block_reasons_json"])
        assert not any("缺少 thesis" in reason for reason in reasons)
        assert any("缺少后续加仓计划" in reason for reason in reasons)
        assert any("缺少失效条件" in reason for reason in reasons)
        assert not any("未找到分批买入计划" in reason for reason in reasons)
        assert result["sync"] is None


def test_starter_position_uses_buy_reason_as_thesis_and_allows_small_valuation_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        portfolio_trade_entry,
        "preview_trade_values_portfolio_effect",
        lambda *args, **kwargs: {"currentQuantity": 0, "afterQuantity": 25, "afterPositionPct": 6.8},
    )
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add(
            "AVGO",
            _base_values(
                quantity=25,
                price=406,
                position_tier="A",
                entry_mode="starter_position",
                buy_reason="核心 AI 仓位，财报后大跌，先买 A 类底仓",
                starter_thesis="",
                starter_add_plan="回到纪律买区或下一档计划价再加第二笔",
                starter_invalidation_condition="AI 订单或毛利率 thesis 破坏则停止",
                target_sell_price=500,
            ),
            path=path,
            radar_report=_blocked_chase_report(
                ticker="AVGO",
                current_price=406,
                decision="WAIT",
                valuation_score=35,
                final_score=62,
                allowed_add_pct=0,
                one_day_change_pct=-15,
                block_reasons=[
                    "current price is above the discipline buy zone",
                    "valuation score below 40; heavy position is not allowed",
                    "final score below 70; core position is not allowed",
                ],
            ),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        position = PortfolioPositionStore(path).get_position("AVGO")
        assert entry is not None
        assert not entry["radar_blocked"]
        assert entry["starter_position"]
        assert entry["starter_match_status"] == "allow_starter_position"
        reasons = json.loads(entry["radar_block_reasons_json"])
        assert any("估值评分低于 40" in reason for reason in reasons)
        assert any("综合评分低于 70" in reason for reason in reasons)
        assert not any("缺少 thesis" in reason for reason in reasons)
        assert not any("未找到分批买入计划" in reason for reason in reasons)
        assert result["synced"] is True
        assert position is not None
        assert position["quantity"] == 25


def test_observation_only_saves_journal_but_does_not_sync_position() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add(
            "NVDA",
            _base_values(radar_observation_only=True),
            path=path,
            radar_report=_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["radar_observation_only"]
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("NVDA") is None


def test_portfolio_buy_gate_notice_translates_raw_reasons_to_chinese() -> None:
    from ui.portfolio import _portfolio_buy_gate_notice_html

    html = _portfolio_buy_gate_notice_html(
        {
            "symbol": "NOK",
            "gate": {
                "is_blocked": True,
                "allowed_add_pct": 0,
                "reasons": [
                    "current price is above the discipline buy zone",
                    "current price is in or above chase zone",
                    "valuation score below 40; heavy position is not allowed",
                    "final score below 70; core position is not allowed",
                    "买入后仓位 5.6% 超过 Radar 交易仓上限 0.0%。",
                ],
            },
            "planGate": {
                "plan_match_status": "no_plan",
                "plan_block_reasons": ["未找到分批买入计划。"],
            },
            "marketStatus": {
                "technical_status": "技术偏热 / 追高风险",
                "valuation_status": "估值仍偏高",
                "discipline_status": "当前允许新增仓位为 0%",
            },
        }
    )

    assert "NOK 已保存日志，未同步持仓" in html
    assert "这不是系统错误" in html
    assert "Radar 拦截原因" in html
    assert "当前市场状态" in html
    assert "当前价高于纪律买入区" in html
    assert "当前仍未进入纪律买入区" in html
    assert "估值评分低于 40" in html
    assert "综合评分低于 70" in html
    assert "当前 Radar 允许新增仓位为 0%" in html
    assert "技术偏热" in html
    assert "估值仍偏高" in html
    assert "未找到分批买入计划" in html
    assert "current price is above" not in html
    assert "heavy position is not allowed" not in html


def test_portfolio_buy_gate_notice_for_starter_does_not_show_missing_ladder_plan() -> None:
    from ui.portfolio import _portfolio_buy_gate_notice_html

    html = _portfolio_buy_gate_notice_html(
        {
            "symbol": "AVGO",
            "entryMode": "starter_position",
            "gate": {
                "is_blocked": True,
                "allowed_add_pct": 0,
                "reasons": [
                    "current price is above the discipline buy zone",
                    "valuation score below 40; heavy position is not allowed",
                ],
            },
            "planGate": {
                "plan_match_status": "no_plan",
                "plan_block_reasons": ["未找到分批买入计划。"],
            },
            "starterGate": {
                "starter_match_status": "starter_blocked",
                "starter_block_reasons": ["缺少后续加仓计划。", "缺少失效条件。"],
            },
            "marketStatus": {
                "technical_status": "财报后大跌 / 高波动",
                "valuation_status": "估值仍偏高",
                "discipline_status": "A 类底仓建仓需补齐资料",
            },
        }
    )

    assert "底仓检查结果" in html
    assert "缺少后续加仓计划" in html
    assert "缺少失效条件" in html
    assert "未找到分批买入计划" not in html
    assert "current price is above" not in html


def test_portfolio_buy_gate_notice_shows_post_earnings_drop_without_overheated_copy() -> None:
    from ui.portfolio import _portfolio_buy_gate_notice_html

    html = _portfolio_buy_gate_notice_html(
        {
            "symbol": "AVGO",
            "gate": {
                "is_blocked": True,
                "allowed_add_pct": 0,
                "reasons": [
                    "current price is above the discipline buy zone",
                    "current price is in or above chase zone",
                    "valuation score below 40; heavy position is not allowed",
                    "final score below 70; core position is not allowed",
                ],
            },
            "marketStatus": {
                "technical_status": "财报后大跌 / 高波动",
                "valuation_status": "估值仍偏高",
                "discipline_status": "当前允许新增仓位为 0%",
                "notes": ["不是系统错误；大跌不等于进入买区。"],
            },
        }
    )

    assert "AVGO 已保存日志，未同步持仓" in html
    assert "财报后大跌" in html
    assert "高波动" in html
    assert "估值仍偏高" in html
    assert "大跌不等于进入买区" in html
    assert "技术过热" not in html
    assert "current price is in or above chase zone" not in html


def test_portfolio_buy_entry_returns_market_status_for_big_drop_block() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"
        result = submit_portfolio_buy_add(
            "AVGO",
            _base_values(price=406, quantity=25, position_tier="A"),
            path=path,
            radar_report={
                "ticker": "AVGO",
                "decision": "BLOCK_CHASE",
                "current_price": 406,
                "buy_zone": [300, 350],
                "watch_zone": [350, 390],
                "chase_zone": [400, 999],
                "price_position": "IN_CHASE_ZONE",
                "final_score": 62,
                "valuation_score": 35,
                "core_max_pct": 0,
                "trade_max_pct": 0,
                "allowed_add_pct": 0,
                "one_day_change_pct": -15,
                "block_reasons": [
                    "current price is above the discipline buy zone",
                    "current price is in or above chase zone",
                    "valuation score below 40; heavy position is not allowed",
                    "final score below 70; core position is not allowed",
                ],
                "data_status": "OK",
                "is_stale": False,
            },
        )

        market_status = result["marketStatus"]
        assert market_status["technical_status"] == "财报后大跌 / 高波动"
        assert market_status["valuation_status"] == "估值仍偏高"
        assert market_status["discipline_status"] == "当前允许新增仓位为 0%"
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("AVGO") is None


def test_fomo_mood_blocks_sync_even_when_radar_allows_buy() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        result = submit_portfolio_buy_add(
            "NVDA",
            _base_values(decision_mood="fomo"),
            path=path,
            radar_report=_report(),
        )

        entry = TradeJournalStore(path).get_entry(int(result["entry"]["id"]))
        assert entry is not None
        assert entry["mood_gate_blocked"]
        assert result["sync"] is None
        assert PortfolioPositionStore(path).get_position("NVDA") is None


def test_position_tier_is_required_for_portfolio_buy_add() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cache.sqlite"

        with pytest.raises(ValueError):
            submit_portfolio_buy_add("NVDA", _base_values(position_tier="UNCLASSIFIED"), path=path, radar_report=_report())
        with pytest.raises(ValueError):
            submit_portfolio_buy_add("NVDA", _base_values(position_tier=""), path=path, radar_report=_report())

        assert TradeJournalStore(path).list_entries("NVDA") == []


def test_portfolio_row_does_not_render_archive_entry() -> None:
    from ui.portfolio import _position_row_html

    html = _position_row_html({"symbol": "NVDA", "positionTier": "A", "quantity": 2, "averageCost": 100})

    assert "portfolio-archive-link" not in html
    assert "portfolioArchiveConfirm" not in html
    assert "归档" not in html
    assert "加仓" in html


def test_portfolio_table_labels_system_reference_as_valuation_reference() -> None:
    import inspect
    import ui.portfolio as portfolio

    source = inspect.getsource(portfolio._render_positions_table)
    drawer_source = inspect.getsource(portfolio._drawer_html)

    assert "系统估值参考" in source
    assert "系统估值参考" in drawer_source


def test_portfolio_ui_has_no_archive_or_direct_position_save_path() -> None:
    import inspect
    import ui.portfolio as portfolio

    source = inspect.getsource(portfolio)

    assert "portfolioArchiveConfirm" not in source
    assert "portfolio_archive_symbol" not in source
    assert "deactivate_position" not in source
    assert "save_position(" not in source
    assert not hasattr(portfolio, "_render_deactivate_dialog_if_needed")
    assert not hasattr(portfolio, "_save_position_from_form")
