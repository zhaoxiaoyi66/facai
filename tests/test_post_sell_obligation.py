from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from data.decision_log import TradeJournalStore
from data.post_sell_obligation import build_post_sell_obligations


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
                "reentryPlanText": "回踩或重新站回卖出价时分批买回",
            },
        )

        obligation = build_post_sell_obligations(path, current_date="2026-06-01")[0]

        assert obligation["status"] == "overdue"
        assert obligation["triggers"] == ["time_stop_due"]
        assert obligation["timeStopDueDate"] == "2026-05-30"
        assert obligation["pullbackBuyBackPct"] == 0.5


def test_post_sell_obligation_ignores_buy_add_skip() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "post_sell.sqlite"
        store = TradeJournalStore(path)
        store.save_entry("NOW", {"trade_date": "2026-05-30", "action_type": "buy", "quantity": 1, "price": 100})
        store.save_entry("NOW", {"trade_date": "2026-05-30", "action_type": "add", "quantity": 1, "price": 110})
        store.save_entry("NOW", {"trade_date": "2026-05-30", "action_type": "skip"})

        assert build_post_sell_obligations(path, current_date="2026-06-01") == []
