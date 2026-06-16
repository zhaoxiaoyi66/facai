from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from data.decision_log import TradeJournalStore
from data.discipline_review import (
    DEFAULT_PRINCIPLES,
    DisciplineReviewStore,
    build_discipline_review_stats,
    build_mistake_review_summary,
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


def test_trade_discipline_tags_are_kept_for_history_compatibility() -> None:
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


def test_discipline_stats_count_recent_tags_for_legacy_history() -> None:
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


def test_mistake_reviews_are_persisted_independently_from_trade_tags() -> None:
    with TemporaryDirectory() as tmpdir:
        store = DisciplineReviewStore(_path(tmpdir))

        saved = store.save_mistake_review(
            {
                "review_date": "2026-06-15",
                "market_type": "币安合约",
                "symbol": "SPACX",
                "loss_amount": 800,
                "trigger_event": "短线想做回落，开了一笔空单。",
                "action_taken": "没有设置止盈止损。",
                "result_text": "隔夜后亏损。",
                "mistake_tags": ["没设止损", "没设止盈", "忘记持仓", "not-real"],
                "reflection": "这是流程错误。",
                "improvement_rule": "合约单必须有保护单。",
                "next_defense": "睡前检查持仓。",
                "review_status": "已记录",
            }
        )

        rows = store.list_mistake_reviews()

        assert saved["symbol"] == "SPACX"
        assert saved["market_type"] == "币安合约"
        assert saved["loss_amount"] == 800
        assert saved["mistake_tags"] == ["没设止损", "没设止盈", "忘记持仓"]
        assert rows[0]["reflection"] == "这是流程错误。"


def test_mistake_review_summary_counts_recent_loss_and_repeated_errors() -> None:
    rows = [
        {"review_date": "2026-06-16", "loss_amount": 100, "mistake_tags": ["没设止损"], "review_status": "已记录"},
        {"review_date": "2026-06-15", "loss_amount": 200, "mistake_tags": ["没设止损"], "review_status": "已形成规则"},
        {"review_date": "2026-06-14", "loss_amount": 300, "mistake_tags": ["没设止损", "隔夜暴露"], "review_status": "已完成复盘"},
        {"review_date": "2026-05-01", "loss_amount": 500, "mistake_tags": ["怕错过"], "review_status": "已记录"},
    ]

    summary = build_mistake_review_summary(rows, current_date="2026-06-16")

    assert summary["total_count"] == 4
    assert summary["recent_30_count"] == 3
    assert summary["recent_30_loss_amount"] == 600
    assert summary["most_common_mistake_type"] == "没设止损"
    assert summary["unruled_count"] == 3
    assert summary["repeated_mistake_types"] == ["没设止损"]


def test_dashboard_and_trade_entry_discipline_copy_are_advisory_only() -> None:
    card = discipline_review.dashboard_discipline_card_html(
        {
            "trade_intent": {
                "trade_count": 8,
                "attention_trade_count": 3,
                "attention_flag_counts": {
                    "怕错过风险": 2,
                    "情绪卖出风险": 1,
                    "无回补预案": 1,
                },
            },
        }
    )
    hint = discipline_review.trade_entry_discipline_hint_html(64)

    assert "纪律提醒" in card
    assert "交易前先记录意图" in card
    assert "最近 30 天交易次数：8" in card
    assert "有复盘关注点：3" in card
    assert "怕错过风险：2" in card
    assert "情绪卖出风险：1" in card
    assert "无回补预案：1" in card
    assert "这笔交易会让组合更集中" in hint
    assert "当前 Setup 不是高质量买点" in hint
    assert "禁止" not in hint
    assert "门禁" not in hint


def test_discipline_review_page_uses_mistake_notebook_instead_of_manual_trade_tags() -> None:
    source = Path("ui/discipline_review.py").read_text(encoding="utf-8")

    assert "交易错题本" in source
    assert "添加错误复盘" in source
    assert "保存标签" not in source
    assert "选择交易记录" not in source
    assert "交易纪律标签" not in source
    assert "通过 / 未通过" not in source
    assert "门禁" not in source


def test_app_registers_discipline_review_page() -> None:
    source = Path("app.py").read_text(encoding="utf-8")

    assert "PAGE_DISCIPLINE_REVIEW" in source
    assert '"discipline-review"' in source
    assert "discipline_review.render" in source
