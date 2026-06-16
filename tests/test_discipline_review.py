from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from data.decision_log import TradeJournalStore
from data.discipline_review import (
    DEFAULT_PRINCIPLES,
    EQUITY_FILL_AUTO,
    EQUITY_FILL_MANUAL,
    EQUITY_SOURCE_PREVIOUS_REVIEW,
    EQUITY_SOURCE_PORTFOLIO,
    DisciplineReviewStore,
    build_discipline_review_stats,
    build_mistake_review_summary,
    build_periodic_return_review_summary,
    build_portfolio_discipline_summary,
    default_period_dates,
    get_current_account_nav,
)
from data.portfolio import PortfolioPositionStore, PortfolioSettingsStore
from ui import discipline_review


def _path(tmpdir: str) -> Path:
    return Path(tmpdir) / "decision_log.sqlite"


def _insert_quote(path: Path, symbol: str, price: float) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quote_snapshots (
                ticker TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO quote_snapshots VALUES (?, ?, ?)",
            (symbol.upper(), json.dumps({"current_price": price}), "2026-06-16T08:00:00+00:00"),
        )
        conn.commit()


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
                "scene_or_symbol": "SPACX 合约空单",
                "loss_impact_text": "800U",
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

        assert saved["scene_or_symbol"] == "SPACX 合约空单"
        assert saved["symbol"] == "SPACX 合约空单"
        assert saved["market_type"] == "其他"
        assert saved["loss_amount"] is None
        assert saved["loss_impact_text"] == "800U"
        assert saved["mistake_tags"] == ["没设止损", "没设止盈", "忘记持仓"]
        assert rows[0]["reflection"] == "这是流程错误。"
        assert "短线想做回落" in discipline_review._mistake_event_summary(rows[0])
        assert "没有设置止盈止损" in discipline_review._mistake_event_summary(rows[0])
        assert "800U" in discipline_review._mistake_impact_summary(rows[0])
        assert "隔夜后亏损" in discipline_review._mistake_impact_summary(rows[0])
        assert "睡前检查持仓" in discipline_review._mistake_next_defense(rows[0])
        assert "合约单必须有保护单" in discipline_review._mistake_next_defense(rows[0])


def test_mistake_reviews_store_usd_loss_and_impact_summary_separately() -> None:
    with TemporaryDirectory() as tmpdir:
        store = DisciplineReviewStore(_path(tmpdir))

        saved = store.save_mistake_review(
            {
                "review_date": "2026-06-16",
                "scene_or_symbol": "NVDA 追高",
                "loss_amount_usd": 500.25,
                "impact_summary": "卖飞后上涨约 10%，但金额字段只记录 USD 数字。",
                "trigger_event": "看到盘中拉升后追入。",
                "mistake_tags": ["追涨杀跌", "怕错过"],
                "reflection": "这是怕错过，不是计划交易。",
                "next_defense": "追高前先等 15 分钟。",
            }
        )

        assert saved["loss_amount"] == 500.25
        assert saved["loss_amount_usd"] == 500.25
        assert saved["loss_impact_text"] == "卖飞后上涨约 10%，但金额字段只记录 USD 数字。"
        assert saved["impact_summary"] == "卖飞后上涨约 10%，但金额字段只记录 USD 数字。"


def test_mistake_review_summary_counts_recent_loss_and_repeated_errors() -> None:
    rows = [
        {"review_date": "2026-06-16", "loss_amount": 100, "mistake_tags": ["没设止损"], "review_status": "已记录"},
        {"review_date": "2026-06-15", "loss_amount": 200, "mistake_tags": ["没设止损"], "review_status": "已形成规则"},
        {"review_date": "2026-06-14", "loss_amount": 300, "mistake_tags": ["没设止损", "隔夜暴露"], "review_status": "已设置防线"},
        {"review_date": "2026-06-13", "loss_impact_text": "卖飞约10%", "mistake_tags": ["怕错过"], "review_status": "已记录"},
        {"review_date": "2026-05-01", "loss_amount": 500, "mistake_tags": ["怕错过"], "review_status": "已记录"},
    ]

    summary = build_mistake_review_summary(rows, current_date="2026-06-16")

    assert summary["total_count"] == 5
    assert summary["recent_30_count"] == 4
    assert summary["recent_30_loss_amount"] == 600
    assert summary["recent_30_loss_amount_text"] == "$600.00"
    assert summary["most_common_mistake_type"] == "没设止损"
    assert summary["unruled_count"] == 3
    assert summary["repeated_mistake_types"] == ["没设止损"]


def test_periodic_return_reviews_calculate_profit_and_return_rate() -> None:
    with TemporaryDirectory() as tmpdir:
        store = DisciplineReviewStore(_path(tmpdir))

        saved = store.save_periodic_return_review(
            {
                "period_type": "周复盘",
                "start_date": "2026-06-08",
                "end_date": "2026-06-14",
                "starting_equity": 170000,
                "ending_equity": 174000,
                "deposit_amount": 0,
                "withdrawal_amount": 0,
                "biggest_contributor": "NVDA",
                "biggest_drag": "NOK",
                "what_went_well": "减少无计划交易。",
                "what_went_wrong": "有一笔追涨。",
                "next_period_rule": "等待确认后再加仓。",
                "notes": "手动复盘。",
            }
        )

        assert saved["period_type"] == "周复盘"
        assert saved["profit_amount"] == 4000
        assert saved["return_rate"] == 0.023529
        assert store.list_periodic_return_reviews()[0]["biggest_contributor"] == "NVDA"


def test_periodic_return_reviews_support_edit_delete_and_zero_starting_equity() -> None:
    with TemporaryDirectory() as tmpdir:
        store = DisciplineReviewStore(_path(tmpdir))

        saved = store.save_periodic_return_review(
            {
                "period_type": "月复盘",
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
                "starting_equity": 0,
                "ending_equity": 1000,
                "deposit_amount": 0,
                "withdrawal_amount": 0,
            }
        )

        assert saved["profit_amount"] == 1000
        assert saved["return_rate"] is None

        updated = store.save_periodic_return_review(
            {
                "period_type": "月复盘",
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
                "starting_equity": 1000,
                "ending_equity": 900,
                "deposit_amount": 0,
                "withdrawal_amount": 0,
                "what_went_wrong": "回撤控制不足。",
            },
            review_id=int(saved["id"]),
        )

        assert updated["profit_amount"] == -100
        assert updated["return_rate"] == -0.1
        assert updated["what_went_wrong"] == "回撤控制不足。"

        store.delete_periodic_return_review(int(saved["id"]))
        assert store.list_periodic_return_reviews() == []


def test_default_period_dates_use_previous_complete_week_and_month() -> None:
    assert default_period_dates("周复盘", today="2026-06-16") == (date.fromisoformat("2026-06-08"), date.fromisoformat("2026-06-14"))
    assert default_period_dates("月复盘", today="2026-06-16") == (date.fromisoformat("2026-05-01"), date.fromisoformat("2026-05-31"))


def test_account_equity_prefill_reads_nearest_snapshot_without_faking_zero() -> None:
    with TemporaryDirectory() as tmpdir:
        store = DisciplineReviewStore(_path(tmpdir))
        store.save_account_equity_snapshot(
            {
                "snapshot_time": "2026-06-08T19:00:00",
                "account_equity": 170000,
                "source": EQUITY_SOURCE_PORTFOLIO,
            }
        )
        store.save_account_equity_snapshot(
            {
                "snapshot_time": "2026-06-14T20:00:00",
                "account_equity": 174000,
                "source": EQUITY_SOURCE_PORTFOLIO,
            }
        )

        prefill = store.build_periodic_return_prefill(start_date="2026-06-08", end_date="2026-06-14")

        assert prefill["starting_equity"] == 170000
        assert prefill["ending_equity"] == 174000
        assert prefill["starting_equity_snapshot_date"] == "2026-06-08"
        assert prefill["ending_equity_snapshot_date"] == "2026-06-14"

        missing = store.build_periodic_return_prefill(start_date="2026-06-01", end_date="2026-06-07")
        assert missing["starting_equity"] is None
        assert missing["ending_equity"] is None


def test_latest_only_snapshot_does_not_backfill_historical_period() -> None:
    with TemporaryDirectory() as tmpdir:
        store = DisciplineReviewStore(_path(tmpdir))
        store.save_account_equity_snapshot(
            {
                "snapshot_time": "2026-06-16T09:30:00",
                "account_equity": 174000,
                "source": EQUITY_SOURCE_PORTFOLIO,
            }
        )

        prefill = store.build_periodic_return_prefill(start_date="2026-06-08", end_date="2026-06-14")

        assert prefill["starting_equity"] is None
        assert prefill["ending_equity"] is None
        assert prefill["only_latest_available"] is True


def test_current_account_nav_uses_market_value_plus_cash() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _path(tmpdir)
        PortfolioSettingsStore(path).save_settings({"total_portfolio_value": 170000, "cash_balance": 25000})
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 10, "average_cost": 100})

        nav = get_current_account_nav(path)

        assert nav["account_nav"] == 26000
        assert nav["cash"] == 25000
        assert nav["market_value"] == 1000
        assert nav["source"] == EQUITY_SOURCE_PORTFOLIO


def test_current_account_nav_derives_cash_from_total_when_cash_is_missing() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _path(tmpdir)
        PortfolioSettingsStore(path).save_settings({"total_portfolio_value": 170000})
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 10, "average_cost": 100})
        _insert_quote(path, "NVDA", 90)

        nav = get_current_account_nav(path)

        assert nav["market_value"] == 900
        assert nav["cash"] == 169000
        assert nav["account_nav"] == 169900


def test_current_account_nav_subtracts_realized_loss_from_derived_cash() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _path(tmpdir)
        PortfolioSettingsStore(path).save_settings({"total_portfolio_value": 10000})
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 10, "average_cost": 100})
        _insert_quote(path, "NVDA", 90)
        journal = TradeJournalStore(path)
        journal.save_entry("MSFT", {"trade_date": "2026-01-01", "action_type": "buy", "quantity": 10, "price": 100})
        journal.save_entry("MSFT", {"trade_date": "2026-01-05", "action_type": "sell", "quantity": 10, "price": 80})

        nav = get_current_account_nav(path)

        assert nav["realized_pnl"] == -200
        assert nav["cash"] == 8800
        assert nav["market_value"] == 900
        assert nav["account_nav"] == 9700


def test_current_account_nav_uses_cash_when_no_stock_market_value() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _path(tmpdir)
        PortfolioSettingsStore(path).save_settings({"total_portfolio_value": 170000, "cash_balance": 25000})

        nav = get_current_account_nav(path)

        assert nav["market_value"] is None
        assert nav["cash"] == 25000
        assert nav["account_nav"] == 25000


def test_periodic_prefill_uses_previous_review_and_current_nav_when_snapshots_missing() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _path(tmpdir)
        store = DisciplineReviewStore(path)
        PortfolioSettingsStore(path).save_settings({"total_portfolio_value": 999999, "cash_balance": 24000})
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 10, "average_cost": 15000})

        prefill = store.build_periodic_return_prefill(
            start_date="2026-06-08",
            end_date="2026-06-14",
            previous_ending_equity=170000,
        )

        assert prefill["starting_equity"] == 170000
        assert prefill["starting_equity_source"] == EQUITY_SOURCE_PREVIOUS_REVIEW
        assert prefill["ending_equity"] == 174000
        assert prefill["ending_equity_source"] == EQUITY_SOURCE_PORTFOLIO


def test_capture_current_account_equity_snapshot_uses_market_value_plus_cash() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _path(tmpdir)
        store = DisciplineReviewStore(path)
        PortfolioSettingsStore(path).save_settings({"total_portfolio_value": 170000, "cash_balance": 25000})
        PortfolioPositionStore(path).save_position("NVDA", {"quantity": 10, "average_cost": 100})

        snapshot = store.capture_current_account_equity_snapshot()

        assert snapshot is not None
        assert snapshot["account_equity"] == 26000
        assert snapshot["cash"] == 25000
        assert snapshot["market_value"] == 1000
        assert snapshot["source"] == EQUITY_SOURCE_PORTFOLIO


def test_periodic_return_review_saves_equity_sources_and_manual_override() -> None:
    with TemporaryDirectory() as tmpdir:
        store = DisciplineReviewStore(_path(tmpdir))

        saved = store.save_periodic_return_review(
            {
                "period_type": "周复盘",
                "start_date": "2026-06-08",
                "end_date": "2026-06-14",
                "starting_equity": 170000,
                "ending_equity": 174500,
                "deposit_amount": 0,
                "withdrawal_amount": 0,
                "starting_equity_source": EQUITY_FILL_AUTO,
                "ending_equity_source": EQUITY_FILL_MANUAL,
                "starting_equity_snapshot_date": "2026-06-08",
                "ending_equity_snapshot_date": "2026-06-14",
                "starting_equity_is_manual_override": False,
                "ending_equity_is_manual_override": True,
            }
        )

        assert saved["starting_equity_source"] == EQUITY_FILL_AUTO
        assert saved["ending_equity_source"] == EQUITY_FILL_MANUAL
        assert saved["starting_equity_snapshot_date"] == "2026-06-08"
        assert saved["ending_equity_snapshot_date"] == "2026-06-14"
        assert saved["starting_equity_is_manual_override"] == 0
        assert saved["ending_equity_is_manual_override"] == 1


def test_periodic_return_summary_counts_weekly_monthly_and_dashboard_gaps() -> None:
    rows = [
        {"id": 1, "period_type": "周复盘", "start_date": "2026-06-08", "end_date": "2026-06-14", "profit_amount": 4000, "return_rate": 0.0235},
        {"id": 2, "period_type": "周复盘", "start_date": "2026-06-01", "end_date": "2026-06-07", "profit_amount": -500, "return_rate": -0.003},
        {"id": 3, "period_type": "月复盘", "start_date": "2026-06-01", "end_date": "2026-06-30", "profit_amount": 3500, "return_rate": 0.02},
    ]

    summary = build_periodic_return_review_summary(rows, current_date="2026-06-16")

    assert summary["weekly_count"] == 2
    assert summary["monthly_count"] == 1
    assert summary["has_current_week_review"] is False
    assert summary["has_current_month_review"] is True
    assert summary["recent_4_week_profit"] == 3500
    assert summary["recent_4_week_max_loss"] == -500
    assert summary["recent_3_month_profit"] == 3500
    assert summary["max_weekly_loss"] == -500
    assert summary["max_monthly_loss"] is None


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
            "periodic_returns": {
                "has_current_week_review": False,
                "has_current_month_review": False,
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
    assert "本周尚未记录收益复盘。" in card
    assert "本月尚未记录收益复盘。" in card
    assert "这笔交易会让组合更集中" in hint
    assert "当前 Setup 不是高质量买点" in hint
    assert "禁止" not in hint
    assert "门禁" not in hint


def test_discipline_review_page_uses_mistake_notebook_instead_of_manual_trade_tags() -> None:
    source = Path("ui/discipline_review.py").read_text(encoding="utf-8")

    assert "交易错题本" in source
    assert "周期收益复盘" in source
    assert "归档这条错误" in source
    assert "周期与数据源" in source
    assert "收益结算" in source
    assert "交易复盘" in source
    assert "历史记录" in source
    assert "保存本期复盘" in source
    assert "读取账户净资产" in source
    assert "保存当前快照" in source
    assert "用上期末值作为期初" in source
    assert "未找到账户净资产。请保存当前快照，或手动填写期初和期末。" in source
    assert "periodic-status" in source
    assert "periodic-settlement-bar" in source
    assert "待计算" in source
    assert "已读取账户净资产" in source
    assert "当前持仓汇总" in source
    assert "上一条复盘" in source
    assert "标的 / 场景" in source
    assert "错误档案" in source
    assert "错误复盘" in source
    assert "纠偏规则" in source
    assert "损失金额" in source
    assert "单位：USD" in source
    assert "结果 / 影响" in source
    assert "只看有损失金额的记录" in source
    assert "最近30天损失金额" in source
    assert "选择错误类型" in source
    assert "事件经过" in source
    assert "核心反思" in source
    assert "下次防线" in source
    assert "这次交易错误是怎么发生的？我当时做了什么？" in source
    assert "这次错误造成了什么结果？亏损、卖飞、错过机会，还是破坏了纪律？" in source
    assert "真正的问题是什么？是判断错了，还是流程、纪律、仓位、情绪出了问题？" in source
    assert "下次遇到类似情况，必须执行哪条规则？" in source
    assert "_render_self_check_questions" not in source
    assert "SELF_CHECK_QUESTIONS" not in source
    assert "交易前纪律提醒" not in source
    assert "市场类型" not in source
    assert "按市场类型筛选" not in source
    assert "SPACX 示例模板" not in source
    assert "with st.expander(\"SPACX" not in source
    assert "基本信息" not in source
    assert "复盘正文" not in source
    assert "周期选择" not in source
    assert "收益数据" not in source
    assert "复盘内容" not in source
    assert "保存周期复盘" not in source
    assert "使用上一条复盘期末净资产作为期初净资产" not in source
    assert "周期收益复盘筛选" not in source
    assert "损失金额 / 影响" not in source
    assert "例如：800U、亏损500美元、卖飞约10%" not in source
    assert "复盘状态\", MISTAKE_REVIEW_STATUSES" not in source
    assert "当时操作\", height" not in source
    assert "结果\", height" not in source
    assert "改进规则\", height" not in source
    assert "添加错误复盘" not in source
    assert "保存这条错题" not in source
    assert "保存标签" not in source
    assert "选择交易记录" not in source
    assert "交易纪律标签" not in source
    assert "通过 / 未通过" not in source
    assert "门禁" not in source

    render_body = source[source.index("def render(") : source.index("def _render_principles_card")]
    assert render_body.index("_render_principles_card") < render_body.index("_render_mistake_reviews")
    assert render_body.index("_render_mistake_reviews") < render_body.index("_render_periodic_return_reviews")
    assert render_body.index("_render_periodic_return_reviews") < render_body.index("_render_portfolio_discipline")
    assert render_body.index("_render_portfolio_discipline") < render_body.index("_render_discipline_stats")


def test_app_registers_discipline_review_page() -> None:
    source = Path("app.py").read_text(encoding="utf-8")

    assert "PAGE_DISCIPLINE_REVIEW" in source
    assert '"discipline-review"' in source
    assert "discipline_review.render" in source
