from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from data.decision_log import TradeJournalStore
from data.trading_discipline_stats import build_trading_discipline_stats


CURRENT_DATE = "2026-05-30"


def _store(tmpdir: str) -> TradeJournalStore:
    return TradeJournalStore(Path(tmpdir) / "decision_log.sqlite")


def _save(store: TradeJournalStore, day: str, action: str = "buy", **overrides) -> None:
    values = {"trade_date": day, "action_type": action, "quantity": 1, "price": 100}
    values.update(overrides)
    store.save_entry("NVDA", values)


def _summary(tmpdir: str) -> dict:
    return build_trading_discipline_stats(Path(tmpdir) / "decision_log.sqlite", CURRENT_DATE)


def test_empty_trade_log_returns_normal() -> None:
    with TemporaryDirectory() as tmpdir:
        summary = _summary(tmpdir)

        assert summary["overTradingLevel"] == "normal"
        assert summary["totalTradesThisWeek"] == 0
        assert summary["warnings"] == []


def test_six_trades_this_week_triggers_caution() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        for index in range(6):
            _save(store, f"2026-05-{25 + index:02d}")

        summary = _summary(tmpdir)

        assert summary["totalTradesThisWeek"] == 6
        assert summary["overTradingLevel"] == "caution"
        assert any("超过 5 次" in warning for warning in summary["warnings"])


def test_eleven_trades_this_week_triggers_danger() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        for index in range(11):
            _save(store, f"2026-05-{25 + (index % 6):02d}")

        summary = _summary(tmpdir)

        assert summary["totalTradesThisWeek"] == 11
        assert summary["overTradingLevel"] == "danger"
        assert any("超过 10 次" in warning for warning in summary["warnings"])


def test_multiple_a_class_sell_trim_triggers_danger() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        for day in ("2026-05-26", "2026-05-27"):
            _save(
                store,
                day,
                "trim",
                positionClass="A",
                corePositionPct=0.7,
                tradingPositionPct=0.3,
                unrealizedGainPct=0.5,
                plannedSellPct=0.1,
                sellReasonType="position_size",
                thesisBroken=False,
                positionOverLimit=True,
                hasReentryPlan=True,
            )

        summary = _summary(tmpdir)

        assert summary["aClassSellCountThisWeek"] == 2
        assert summary["overTradingLevel"] == "danger"


def test_macro_sell_triggers_caution() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save(
            store,
            "2026-05-27",
            "trim",
            positionClass="B",
            corePositionPct=0.5,
            tradingPositionPct=0.5,
            unrealizedGainPct=0.3,
            plannedSellPct=0.1,
            sellReasonType="macro",
            thesisBroken=False,
            positionOverLimit=False,
            hasReentryPlan=True,
        )

        summary = _summary(tmpdir)

        assert summary["macroSellCountThisWeek"] == 1
        assert summary["overTradingLevel"] == "caution"
        assert summary["disciplineWarningCount"] == 1


def test_no_reentry_plan_sell_triggers_danger() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save(
            store,
            "2026-05-27",
            "trim",
            positionClass="B",
            corePositionPct=0.5,
            tradingPositionPct=0.5,
            unrealizedGainPct=0.3,
            plannedSellPct=0.1,
            sellReasonType="technical",
            thesisBroken=False,
            positionOverLimit=False,
            hasReentryPlan=False,
        )

        summary = _summary(tmpdir)

        assert summary["noReentryPlanSellCount"] == 1
        assert summary["disciplineBlockerCount"] == 1
        assert summary["overTradingLevel"] == "danger"


def test_blocker_sell_triggers_danger() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save(
            store,
            "2026-05-27",
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

        summary = _summary(tmpdir)

        assert summary["disciplineBlockerCount"] == 1
        assert summary["overTradingLevel"] == "danger"
        assert any("blocker" in warning for warning in summary["warnings"])


def test_buy_add_skip_count_frequency_but_not_sell_discipline_violations() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save(store, "2026-05-27", "buy")
        _save(store, "2026-05-27", "add")
        _save(store, "2026-05-27", "skip")

        summary = _summary(tmpdir)

        assert summary["totalTradesThisWeek"] == 3
        assert summary["sellTrimCountThisWeek"] == 0
        assert summary["disciplineBlockerCount"] == 0
        assert summary["overTradingLevel"] == "normal"


def test_decision_mood_counts_are_included_in_discipline_stats() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save(store, "2026-05-27", "buy", decision_mood="fomo")
        _save(store, "2026-05-27", "trim", decision_mood="anxiety")
        _save(store, "2026-05-27", "sell", decision_mood="panic_sell")
        _save(store, "2026-05-27", "skip", decision_mood="revenge_trade")
        _save(store, "2026-05-27", "add", decision_mood="well_reasoned")
        _save(store, "2026-05-27", "buy", decision_mood="plan_execution")

        summary = _summary(tmpdir)

        assert summary["fomoTradeCount"] == 1
        assert summary["anxietyPanicTradeCount"] == 2
        assert summary["revengeTradeCount"] == 1
        assert summary["reasonedPlanTradeCount"] == 2
