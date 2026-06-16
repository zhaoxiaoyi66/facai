from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from data.decision_log import TradeJournalStore
from data.discipline_review import (
    DEFAULT_PRINCIPLES,
    DisciplineReviewStore,
    build_discipline_review_stats,
    build_portfolio_discipline_summary,
)
from ui import discipline_review


def _path(tmpdir: str) -> Path:
    return Path(tmpdir) / "decision_log.sqlite"


def test_discipline_principles_can_be_saved_and_reset() -> None:
    with TemporaryDirectory() as tmpdir:
        store = DisciplineReviewStore(_path(tmpdir))

        assert store.get_principles() == DEFAULT_PRINCIPLES
        store.save_principles("少而硬，只做少数高质量机会。")
        assert store.get_principles() == "少而硬，只做少数高质量机会。"
        store.reset_principles()
        assert store.get_principles() == DEFAULT_PRINCIPLES


def test_trade_discipline_tags_are_persisted_without_touching_trade_validation() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _path(tmpdir)
        trade_store = TradeJournalStore(path)
        discipline_store = DisciplineReviewStore(path)
        entry = trade_store.save_entry(
            "NVDA",
            {"trade_date": "2026-06-15", "action_type": "buy", "quantity": 1, "price": 100},
        )

        saved = discipline_store.save_trade_tags(
            int(entry["id"]),
            ["plan_followed", "chase", "not_a_real_tag"],
            "复盘备注",
        )

        assert [row["tag"] for row in saved] == ["chase", "plan_followed"]
        assert all(row["notes"] == "复盘备注" for row in saved)


def test_discipline_stats_count_recent_tags_and_plan_ratio() -> None:
    entries = [
        {"id": 1, "trade_date": "2026-06-10", "action_type": "buy"},
        {"id": 2, "trade_date": "2026-06-15", "action_type": "sell"},
        {"id": 3, "trade_date": "2026-05-01", "action_type": "buy"},
    ]
    tags = [
        {"trade_entry_id": 1, "trade_date": "2026-06-10", "tag": "plan_followed"},
        {"trade_entry_id": 2, "trade_date": "2026-06-15", "tag": "participation_small_position"},
        {"trade_entry_id": 2, "trade_date": "2026-06-15", "tag": "panic_sell"},
        {"trade_entry_id": 3, "trade_date": "2026-05-01", "tag": "chase"},
    ]

    stats = build_discipline_review_stats(entries, tags, current_date="2026-06-16")

    assert stats["seven_days"]["trade_count"] == 2
    assert stats["seven_days"]["participation_small_position_count"] == 1
    assert stats["seven_days"]["panic_sell_count"] == 1
    assert stats["thirty_days"]["chase_count"] == 0
    assert stats["thirty_days"]["plan_followed_ratio"] == 50.0


def test_portfolio_discipline_summary_counts_concentration_and_unplanned_trades() -> None:
    positions = [
        {"symbol": "NVDA", "quantity": 10, "average_cost": 100},
        {"symbol": "NOW", "quantity": 5, "average_cost": 80},
        {"symbol": "ADBE", "quantity": 1, "average_cost": 10},
    ]
    entries = [
        {"symbol": "NVDA", "trade_date": "2026-06-15", "action_type": "buy", "decision_mood": "plan_execution"},
        {"symbol": "NOW", "trade_date": "2026-06-16", "action_type": "add", "decision_mood": "fomo"},
    ]

    summary = build_portfolio_discipline_summary(
        positions,
        entries,
        {"target_holding_min": 3, "target_holding_max": 5, "small_position_threshold_pct": 3, "target_core_min": 1, "target_core_max": 3},
        current_date="2026-06-16",
    )

    assert summary["current_holding_count"] == 3
    assert summary["small_position_count"] == 1
    assert summary["new_position_count_this_week"] == 2
    assert summary["unplanned_trade_count_this_week"] == 1


def test_dashboard_and_trade_entry_discipline_copy_are_advisory_only() -> None:
    card = discipline_review.dashboard_discipline_card_html(
        {
            "principle_first_line": "少而硬：做高信念集中的少量股票。",
            "portfolio": {
                "current_holding_count": 4,
                "target_holding_min": 3,
                "target_holding_max": 5,
                "small_position_count": 1,
                "unplanned_trade_count_this_week": 2,
            },
        }
    )
    hint = discipline_review.trade_entry_discipline_hint_html(64)

    assert "纪律提醒" in card
    assert "当前持仓 4 只 / 目标 3-5 只" in card
    assert "这笔交易会让组合更集中" in hint
    assert "当前 Setup 不是高质量买点" in hint
    assert "禁止" not in hint
    assert "\u95e8\u7981" not in hint


def test_app_registers_discipline_review_page() -> None:
    source = Path("app.py").read_text(encoding="utf-8")

    assert "PAGE_DISCIPLINE_REVIEW" in source
    assert "\"discipline-review\"" in source
    assert "discipline_review.render" in source
