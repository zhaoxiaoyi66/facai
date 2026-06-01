from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from data.decision_log import TradeJournalStore
from data.sell_fly_review import build_sell_fly_review_results


CURRENT_DATE = "2026-05-30"


def _store(tmpdir: str) -> TradeJournalStore:
    return TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")


def _insert_history(tmpdir: str, symbol: str, closes: list[tuple[str, float]]) -> None:
    db_path = Path(tmpdir) / "decision_log.sqlite"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                ticker TEXT,
                date TEXT,
                close REAL,
                fetched_at TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO price_history VALUES (?, ?, ?, ?)",
            [(symbol.upper(), day, close, "2026-05-30T00:00:00Z") for day, close in closes],
        )
        conn.commit()


def _save_trade(store: TradeJournalStore, action: str, **overrides) -> None:
    values = {
        "trade_date": "2026-05-20",
        "action_type": action,
        "quantity": 10,
        "price": 100,
    }
    values.update(overrides)
    store.save_entry("NVDA", values)


def _review(tmpdir: str) -> list[dict]:
    return build_sell_fly_review_results(Path(tmpdir) / "decision_log.sqlite", CURRENT_DATE)


def _result(results: list[dict], horizon: str) -> dict:
    return next(item for item in results if item["horizon"] == horizon)


def test_sell_after_10d_rally_over_8pct_marks_suspected_sell_fly() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save_trade(store, "sell")
        _insert_history(
            tmpdir,
            "NVDA",
            [
                ("2026-05-20", 100),
                ("2026-05-22", 104),
                ("2026-05-26", 109),
                ("2026-05-30", 105),
            ],
        )

        ten_day = _result(_review(tmpdir), "10d")

        assert ten_day["suspectedSellFly"] is True
        assert ten_day["maxPriceAfterSell"] == 109
        assert ten_day["maxReturnAfterSellPct"] == 9
        assert ten_day["endPrice"] == 105
        assert ten_day["endReturnPct"] == 5
        assert ten_day["reason"] == "suspected_sell_fly_10d_gt_8pct"


def test_sell_without_follow_through_rally_is_not_sell_fly() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save_trade(store, "trim")
        _insert_history(
            tmpdir,
            "NVDA",
            [
                ("2026-05-21", 99),
                ("2026-05-25", 98),
                ("2026-05-30", 101),
            ],
        )

        ten_day = _result(_review(tmpdir), "10d")

        assert ten_day["suspectedSellFly"] is False
        assert ten_day["maxReturnAfterSellPct"] == 1
        assert ten_day["reason"] == "no_sell_fly_10d"


def test_missing_price_history_returns_missing_without_crashing() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save_trade(store, "sell")

        results = _review(tmpdir)

        assert len(results) == 3
        assert {item["reason"] for item in results} == {"missing_price_history"}
        assert all(item["suspectedSellFly"] is False for item in results)


def test_buy_add_skip_are_not_reviewed() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        for action in ("buy", "add", "skip"):
            _save_trade(store, action)
        _insert_history(tmpdir, "NVDA", [("2026-05-21", 120)])

        assert _review(tmpdir) == []


def test_blocker_and_post_sell_rally_marks_violated_discipline() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save_trade(
            store,
            "sell",
            positionClass="A",
            corePositionPct=0.7,
            tradingPositionPct=0.3,
            unrealizedGainPct=0.1,
            plannedSellPct=1.0,
            sellReasonType="macro",
            thesisBroken=False,
            positionOverLimit=False,
            hasReentryPlan=True,
        )
        _insert_history(tmpdir, "NVDA", [("2026-05-21", 103), ("2026-05-24", 110)])

        ten_day = _result(_review(tmpdir), "10d")

        assert ten_day["suspectedSellFly"] is True
        assert ten_day["violatedDiscipline"] is True
        assert "a_class_core_clear_requires_thesis_break" in ten_day["disciplineSnapshot"]["blockers"]
        assert ten_day["reason"] == "suspected_sell_fly_with_discipline_blocker"


def test_sell_fly_snapshot_requires_concrete_reentry_plan_not_legacy_checkbox() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        entry = store.save_entry(
            "NVDA",
            {
                "trade_date": "2026-05-20",
                "action_type": "sell",
                "quantity": 10,
                "price": 100,
                "hasReentryPlan": True,
                "reentryThesisInvalidation": "thesis broken",
            },
        )
        with closing(sqlite3.connect(Path(tmpdir) / "decision_log.sqlite")) as conn:
            conn.execute("UPDATE trade_journal_entries SET has_reentry_plan = 1 WHERE id = ?", (entry["id"],))
            conn.commit()
        _insert_history(tmpdir, "NVDA", [("2026-05-21", 103), ("2026-05-24", 110)])

        ten_day = _result(_review(tmpdir), "10d")

        assert ten_day["disciplineSnapshot"]["hasReentryPlan"] is False
        assert ten_day["disciplineSnapshot"]["reentryThesisInvalidation"] == "thesis broken"


def test_sell_fly_snapshot_includes_concrete_reentry_plan_fields() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save_trade(
            store,
            "sell",
            reentryPullbackPrice=95,
            reentryBreakoutPrice=105,
            reentryTimeStopDays=5,
            reentryBuyBackPctOnPullback=50,
            reentryBuyBackPctOnBreakout=30,
            reentryPlanText="buy back on pullback or reclaim",
        )
        _insert_history(tmpdir, "NVDA", [("2026-05-21", 103), ("2026-05-24", 104)])

        ten_day = _result(_review(tmpdir), "10d")
        snapshot = ten_day["disciplineSnapshot"]

        assert snapshot["hasReentryPlan"] is True
        assert snapshot["reentryPullbackPrice"] == 95
        assert snapshot["reentryBreakoutPrice"] == 105
        assert snapshot["reentryTimeStopDays"] == 5
        assert snapshot["reentryBuyBackPctOnPullback"] == 0.5
        assert snapshot["reentryBuyBackPctOnBreakout"] == 0.3


def test_five_and_twenty_day_horizons_are_calculated_but_not_primary_flags() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save_trade(store, "sell")
        _insert_history(
            tmpdir,
            "NVDA",
            [
                ("2026-05-21", 109),
                ("2026-05-25", 111),
                ("2026-05-30", 112),
            ],
        )

        results = _review(tmpdir)

        assert {item["horizon"] for item in results} == {"5d", "10d", "20d"}
        assert _result(results, "5d")["suspectedSellFly"] is False
        assert _result(results, "20d")["suspectedSellFly"] is False
        assert _result(results, "10d")["suspectedSellFly"] is True
