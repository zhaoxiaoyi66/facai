from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from data.decision_log import TradeJournalStore
from data.trading_discipline_stats import build_trading_discipline_stats


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
        assert summary["disciplineScore"] == 100
        assert summary["disciplineLevel"] == "normal"
        assert summary["shouldPauseTrading"] is False
        assert summary["suggestedAction"] == "纪律正常"


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
            reentryPullbackPrice=95,
            reentryBreakoutPrice=102,
            reentryPlanText="回踩或重新站回卖出价时分批买回",
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
        assert summary["disciplineLevel"] in {"danger", "stop"}
        assert summary["shouldPauseTrading"] is True
        assert any("纪律阻断" in item for item in summary["mainViolations"])
        assert any("纪律阻断" in warning for warning in summary["warnings"])


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


def test_now_style_risk_count_is_included_in_discipline_stats() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save(
            store,
            "2026-05-27",
            "trim",
            decision_mood="macro_fear",
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

        assert summary["nowStyleRiskCount"] == 1
        assert summary["overTradingLevel"] == "danger"
        assert summary["disciplineLevel"] == "stop"
        assert summary["disciplineScore"] <= 70
        assert summary["shouldPauseTrading"] is True
        assert any("NOW 式错误风险" in item for item in summary["mainViolations"])
        assert any("NOW 式错误风险" in warning for warning in summary["warnings"])


def test_no_reentry_sell_penalizes_discipline_score_heavily() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save(
            store,
            "2026-05-27",
            "trim",
            positionClass="B",
            corePositionPct=0.0,
            tradingPositionPct=1.0,
            unrealizedGainPct=0.2,
            plannedSellPct=0.1,
            sellReasonType="technical",
            thesisBroken=False,
            positionOverLimit=False,
            hasReentryPlan=False,
        )

        summary = _summary(tmpdir)

        assert summary["noReentryPlanSellCount"] == 1
        assert summary["disciplineScore"] <= 78
        assert summary["disciplineLevel"] == "danger"
        assert summary["shouldPauseTrading"] is True
        assert any("无回补计划卖出" in item for item in summary["mainViolations"])


def test_reentry_checkbox_without_concrete_plan_penalizes_discipline_score() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save(
            store,
            "2026-05-27",
            "trim",
            positionClass="B",
            corePositionPct=0.0,
            tradingPositionPct=1.0,
            unrealizedGainPct=0.2,
            plannedSellPct=0.1,
            sellReasonType="technical",
            thesisBroken=False,
            positionOverLimit=False,
            hasReentryPlan=True,
            reentryThesisInvalidation="thesis broken",
        )

        summary = _summary(tmpdir)

        assert summary["noReentryPlanSellCount"] == 1
        assert summary["disciplineLevel"] == "danger"
        assert summary["shouldPauseTrading"] is True


def test_suspected_sell_fly_penalizes_discipline_score() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save(store, "2026-05-26", "sell", quantity=10, price=100)
        _insert_history(
            tmpdir,
            "NVDA",
            [
                ("2026-05-27", 102),
                ("2026-05-28", 109),
                ("2026-05-30", 106),
            ],
        )

        summary = _summary(tmpdir)

        assert summary["suspectedSellFlyCount"] == 1
        assert summary["disciplineScore"] < 100
        assert any("疑似卖飞" in item for item in summary["mainViolations"])

def test_open_reentry_obligations_are_included_even_from_prior_week() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save(
            store,
            "2026-05-20",
            "trim",
            positionClass="B",
            corePositionPct=0.0,
            tradingPositionPct=1.0,
            unrealizedGainPct=0.2,
            plannedSellPct=0.1,
            sellReasonType="technical",
            thesisBroken=False,
            positionOverLimit=False,
            reentryPullbackPrice=95,
            reentryBreakoutPrice=105,
            reentryTimeStopDays=5,
            reentryPlanText="回踩或重新站回卖出价时分批买回",
        )

        summary = build_trading_discipline_stats(Path(tmpdir) / "decision_log.sqlite", "2026-06-01")

        assert summary["totalTradesThisWeek"] == 0
        assert summary["reentryObligationCount"] == 1
        assert summary["reentryObligationOverdueCount"] == 1
        assert summary["overTradingLevel"] == "danger"
        assert any("回补计划" in warning for warning in summary["warnings"])


def test_stacked_violations_should_pause_trading() -> None:
    with TemporaryDirectory() as tmpdir:
        store = _store(tmpdir)
        _save(
            store,
            "2026-05-27",
            "sell",
            decision_mood="panic_sell",
            positionClass="A",
            corePositionPct=0.7,
            tradingPositionPct=0.3,
            unrealizedGainPct=0.1,
            plannedSellPct=1.0,
            sellReasonType="macro",
            thesisBroken=False,
            positionOverLimit=False,
            hasReentryPlan=False,
        )

        summary = _summary(tmpdir)

        assert summary["disciplineLevel"] in {"danger", "stop"}
        assert summary["shouldPauseTrading"] is True
        assert summary["pauseReason"]
        assert len(summary["mainViolations"]) >= 3
