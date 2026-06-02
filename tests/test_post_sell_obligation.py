from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from data.decision_log import TradeJournalStore
from data.post_sell_obligation import build_post_sell_obligations


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
            (ticker.upper(), "2026-06-01", close, fetched_at),
        )
        conn.commit()


def test_post_sell_obligation_marks_missing_reentry_plan() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "post_sell.sqlite"
        TradeJournalStore(path).save_entry(
            "NVDA",
            {
                "trade_date": "2026-05-30",
                "action_type": "sell",
                "quantity": 10,
                "price": 200,
            },
        )

        obligations = build_post_sell_obligations(path, current_date="2026-06-01")

        assert len(obligations) == 1
        assert obligations[0]["status"] == "missing_plan"
        assert obligations[0]["syncRequired"]


def test_post_sell_obligation_surfaces_time_stop_due() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "post_sell.sqlite"
        TradeJournalStore(path).save_entry(
            "MSFT",
            {
                "trade_date": "2026-05-25",
                "action_type": "trim",
                "quantity": 2,
                "price": 400,
                "reentryPullbackPrice": 380,
                "reentryBreakoutPrice": 420,
                "reentryTimeStopDays": 5,
                "reentryBuyBackPctOnPullback": 50,
                "reentryBuyBackPctOnBreakout": 30,
                "reentryThesisInvalidation": "thesis broken",
                "reentryPlanText": "回踩或重新站回卖出价时分批买回",
            },
        )

        obligation = build_post_sell_obligations(path, current_date="2026-06-01")[0]

        assert obligation["status"] == "overdue"
        assert obligation["triggers"] == ["time_stop_due"]
        assert obligation["timeStopDueDate"] == "2026-05-30"
        assert obligation["pullbackBuyBackPct"] == 0.5


def test_post_sell_obligation_does_not_count_checkbox_or_invalidation_as_plan() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "post_sell.sqlite"
        TradeJournalStore(path).save_entry(
            "MSFT",
            {
                "trade_date": "2026-05-30",
                "action_type": "trim",
                "quantity": 2,
                "price": 400,
                "hasReentryPlan": True,
                "reentryThesisInvalidation": "thesis broken",
            },
        )

        obligation = build_post_sell_obligations(path, current_date="2026-06-01")[0]

        assert obligation["hasReentryPlan"] is False
        assert obligation["status"] == "missing_plan"
        assert obligation["syncRequired"]


def test_post_sell_obligation_does_not_count_plan_text_only_as_plan() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "post_sell.sqlite"
        TradeJournalStore(path).save_entry(
            "MSFT",
            {
                "trade_date": "2026-05-30",
                "action_type": "trim",
                "quantity": 2,
                "price": 400,
                "hasReentryPlan": True,
                "reentryPlanText": "看情况买回",
            },
        )

        obligation = build_post_sell_obligations(path, current_date="2026-06-01")[0]

        assert obligation["hasReentryPlan"] is False
        assert obligation["status"] == "missing_plan"


def test_post_sell_obligation_uses_market_context_price() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "post_sell.sqlite"
        TradeJournalStore(path).save_entry(
            "CRWV",
            {
                "trade_date": "2026-05-30",
                "action_type": "sell",
                "quantity": 2,
                "price": 100,
                "reentryPullbackPrice": 80,
                "reentryBuyBackPctOnPullback": 50,
                "reentryThesisInvalidation": "thesis broken",
            },
        )
        _insert_history(path, "CRWV", 95, "2026-05-30T10:00:00+00:00")
        _insert_history(path, "FMP:CRWV", 75, "2026-06-01T10:00:00+00:00")

        obligation = build_post_sell_obligations(path, current_date="2026-06-01")[0]

        assert obligation["currentPrice"] == 75
        assert obligation["priceSource"] == "price_history"
        assert obligation["triggers"] == ["pullback_reentry"]
        assert obligation["status"] == "triggered"


def test_post_sell_obligation_ignores_buy_add_skip() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "post_sell.sqlite"
        store = TradeJournalStore(path)
        store.save_entry("NOW", {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 1, "price": 100})
        store.save_entry("NOW", {"trade_date": "2026-05-30", "action_type": "add", "quantity": 1, "price": 110})
        store.save_entry("NOW", {"trade_date": "2026-05-30", "action_type": "skip"})

        assert build_post_sell_obligations(path, current_date="2026-06-01") == []
