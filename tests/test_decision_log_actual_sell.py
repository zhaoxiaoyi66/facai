from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from data.decision_log import TradeJournalStore


def test_trade_journal_snapshot_uses_actual_sell_ratio_from_quantity() -> None:
    with TemporaryDirectory() as tmpdir:
        store = TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")

        saved = store.save_entry(
            "NVDA",
            {
                "trade_date": "2026-05-30",
                "action_type": "sell",
                "quantity": 100,
                "price": 100,
                "currentPositionQuantity": 158,
                "positionClass": "A",
                "corePositionPct": 0.6,
                "tradingPositionPct": 0.4,
                "plannedSellPct": 0.1,
                "sellReasonType": "macro",
                "thesisBroken": False,
                "positionOverLimit": False,
                "reentryPullbackPrice": 95,
                "reentryBreakoutPrice": 102,
                "reentryPlanText": "回踩或重新站回卖出价时分批买回",
            },
        )

        assert saved["actual_sell_pct"] == 100 / 158
        assert saved["discipline_status"] == "warning"
        assert saved["blockers"] == []
        assert "planned_actual_sell_pct_mismatch" in saved["sell_warning_reasons"]
        assert "a_class_core_floor_breached" in saved["sell_warning_reasons"]
        assert saved["sell_blocked"] is False
