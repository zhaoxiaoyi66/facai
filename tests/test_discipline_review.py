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
    DEFAULT_PRINCIPLE_RULES,
    EQUITY_FILL_AUTO,
    EQUITY_FILL_MANUAL,
    EQUITY_SOURCE_PREVIOUS_REVIEW,
    EQUITY_SOURCE_PORTFOLIO,
    DisciplineReviewStore,
    build_discipline_review_stats,
    build_mistake_review_summary,
    build_period_mistake_review_summary,
    build_periodic_return_review_summary,
    build_portfolio_discipline_summary,
    build_rule_library_from_mistakes,
    build_trade_review_conclusion,
    default_period_dates,
    get_current_account_nav,
)
from data.investment_principles import (
    DEFAULT_QUOTE_TEXT,
    add_principle_quote,
    delete_principle_quote,
    load_investment_principles,
    principle_reminder_for_mistake_tags,
    update_principle_quote,
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


def test_discipline_principle_rules_can_be_saved_reordered_and_reset() -> None:
    with TemporaryDirectory() as tmpdir:
        store = DisciplineReviewStore(_path(tmpdir))
        rules = [
            {"title": "左侧买入", "content": "买点看承接、位置和风险收益。"},
            {"title": "现金也是仓位", "content": "等待也是操作。"},
        ]

        saved = store.save_principle_rules(rules)

        assert saved == rules
        assert store.get_principle_rules() == rules
        assert store.get_principles() == "1. 左侧买入\n买点看承接、位置和风险收益。\n2. 现金也是仓位\n等待也是操作。"
        store.reset_principles()
        assert store.get_principle_rules() == DEFAULT_PRINCIPLE_RULES


def test_investment_principles_local_store_starts_with_single_default_quote() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "investment_principles.local.json"

        payload = load_investment_principles(path)

        assert [quote["text"] for quote in payload["quotes"]] == [DEFAULT_QUOTE_TEXT]
        assert "不做空，只买高信念股" in [rule["title"] for rule in payload["core_rules"]]
        assert "持仓原则" in [rule["title"] for rule in payload["core_rules"]]


def test_investment_principles_do_not_ship_extra_default_quotes() -> None:
    source = Path("data/investment_principles.py").read_text(encoding="utf-8")
    forbidden_defaults = [
        "错过不是损失，失控才是损失",
        "市场奖励耐心",
        "买入前问赔率，卖出前问逻辑",
        "股价波动不是错误，违反系统才是错误",
    ]

    assert source.count(DEFAULT_QUOTE_TEXT) == 1
    for text in forbidden_defaults:
        assert text not in source


def test_investment_principles_local_file_is_ignored() -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "config/*.local.json" in gitignore
    assert "config/investment_principles.local.json" in gitignore
    assert "config/investment_principles.local.json" not in Path("config/investment_principles.example.json").read_text(encoding="utf-8")


def test_investment_principle_quotes_can_be_added_edited_and_deleted() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "investment_principles.local.json"

        added = add_principle_quote("只做自己能解释清楚的交易。", note="说不清就不要做。", tags="纪律,FOMO", path=path)
        updated = update_principle_quote(added["id"], text="只做自己能承受波动的交易。", note="承受不了就不是高信念。", tags=["纪律"], path=path)
        deleted = delete_principle_quote(updated["id"], path=path)
        payload = load_investment_principles(path)

        assert updated["text"] == "只做自己能承受波动的交易。"
        assert deleted["deleted"] is True
        assert [quote["text"] for quote in payload["quotes"]] == [DEFAULT_QUOTE_TEXT]


def test_deleting_only_default_quote_requires_confirmation() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "investment_principles.local.json"
        payload = load_investment_principles(path)
        quote_id = payload["quotes"][0]["id"]

        result = delete_principle_quote(quote_id, path=path)

        assert result["deleted"] is False
        assert result["requires_confirmation"] is True
        assert "这是当前唯一默认原则" in result["message"]


def test_default_quote_can_be_deleted_after_confirmation() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "investment_principles.local.json"
        payload = load_investment_principles(path)
        quote_id = payload["quotes"][0]["id"]

        result = delete_principle_quote(quote_id, confirm_default_delete=True, path=path)
        reloaded = load_investment_principles(path)

        assert result["deleted"] is True
        assert reloaded["quotes"] == []
        assert reloaded["default_quote_deleted"] is True


def test_mistake_tags_map_to_principle_reminders() -> None:
    assert "追逐对趋势的误解" in principle_reminder_for_mistake_tags(["追高", "FOMO"])
    assert "不参与感受型小仓" in principle_reminder_for_mistake_tags(["小仓乱买"])
    assert "卖出前先问逻辑是否改变" in principle_reminder_for_mistake_tags(["卖飞"])
    assert "持仓也是责任" in principle_reminder_for_mistake_tags(["忘记持仓"])
    assert "泡沫由真实趋势" in principle_reminder_for_mistake_tags(["其他错误"])


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


def test_quick_mistake_tags_are_supported_by_store() -> None:
    assert set(discipline_review.QUICK_MISTAKE_TAG_OPTIONS).issubset(set(discipline_review.MISTAKE_TAG_OPTIONS))


def test_mistake_review_allows_zero_loss_and_quick_fields() -> None:
    with TemporaryDirectory() as tmpdir:
        store = DisciplineReviewStore(_path(tmpdir))

        saved = store.save_mistake_review(
            {
                "review_date": "2026-06-18",
                "scene_or_symbol": "NOW 买早",
                "loss_amount_usd": 0,
                "mistake_tags": ["买早", "小仓乱买"],
                "reflection": "没等承接确认就动手。",
                "next_defense": "进入候选区也要等承接信号。",
            }
        )

        assert saved["loss_amount"] == 0
        assert saved["mistake_tags"] == ["买早", "小仓乱买"]
        assert saved["reflection"] == "没等承接确认就动手。"
        assert saved["next_defense"] == "进入候选区也要等承接信号。"


def test_recent_mistake_rows_only_returns_latest_five() -> None:
    rows = [
        {"id": index, "review_date": f"2026-06-{10 + index:02d}", "scene_or_symbol": f"错误 {index}"}
        for index in range(1, 7)
    ]

    recent = discipline_review._recent_mistake_rows(rows, limit=5)

    assert [row["id"] for row in recent] == [6, 5, 4, 3, 2]


def test_old_spacex_mistake_renders_as_card_and_next_defense() -> None:
    row = {
        "id": 1,
        "review_date": "2026-06-16",
        "scene_or_symbol": "SPACEX 空单",
        "loss_amount": 800,
        "mistake_tags": ["没设止损", "没设止盈", "忘记持仓", "隔夜暴露"],
        "reflection": "空强势股票太弱智了，明知道他强，还空，不是找爹我吗",
        "next_defense": "不能空强势标的",
        "review_status": "已记录",
    }

    card_html = discipline_review._mistake_card_html(row)
    rules = discipline_review._next_defense_rules([row], limit=5)
    rules_html = discipline_review._next_defense_cards_html(rules)

    assert "2026-06-16 · SPACEX 空单" in card_html
    assert "$800.00" in card_html
    assert "没设止盈" in card_html
    assert "空强势股票太弱智了" in card_html
    assert "不能空强势标的" in card_html
    assert rules[0]["rule_text"] == "不能空强势标的"
    assert "来源：2026-06-16 · SPACEX 空单" in rules_html


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


def test_trade_review_period_summary_conclusion_and_rule_library() -> None:
    rows = [
        {
            "review_date": "2026-06-16",
            "scene_or_symbol": "SPACEX空单",
            "loss_amount": 800,
            "mistake_tags": ["没设止损", "忘记持仓"],
            "review_status": "已记录",
            "next_defense": "不能空强势标的",
        },
        {
            "review_date": "2026-06-01",
            "scene_or_symbol": "旧记录",
            "loss_amount": 200,
            "mistake_tags": ["怕错过"],
            "review_status": "已形成规则",
            "improvement_rule": "追高前先等回踩",
        },
    ]

    summary = build_period_mistake_review_summary(rows, start_date="2026-06-10", end_date="2026-06-17")
    conclusion = build_trade_review_conclusion(profit_amount=-1200, return_rate=-0.01, mistake_summary=summary)
    rules = build_rule_library_from_mistakes(rows)

    assert summary["mistake_count"] == 1
    assert summary["loss_amount"] == 800
    assert summary["most_common_mistake_type"] == "忘记持仓"
    assert summary["unclosed_rule_count"] == 1
    assert "本期亏损 $1,200.00" in conclusion["summary"]
    assert "下次防线：不能空强势标的" in conclusion["summary"]
    assert rules[0]["rule_text"] == "不能空强势标的"
    assert rules[1]["rule_text"] == "追高前先等回踩"
    assert rules[0]["status"] == "待验证"


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


def test_discipline_review_page_uses_trade_review_labels_instead_of_manual_trade_tags() -> None:
    source = Path("ui/discipline_review.py").read_text(encoding="utf-8")

    assert "交易复盘" in source
    assert "记录交易错误、复盘交易行为，把每次失误沉淀成下一次防线。" in source
    assert "交易错题本" not in source
    assert "错误记录总数" in source
    assert "最近30天错误数" in source
    assert "最近30天损失金额" in source
    assert "未闭环防线" in source
    assert "快速记录一次错误" in source
    assert "收进复盘" in source
    assert "收进错题本" not in source
    assert "犯错行为" in source
    assert "一句话反思" in source
    assert "补充详细复盘" in source
    assert "当时情绪" in source
    assert "是否违反原计划" in source
    assert "是否需要交易前提醒" in source
    assert "更多错误类型" in source
    assert "最近复盘" in source
    assert "默认只显示最近 5 条" in source
    assert "下次防线" in source
    assert "高级统计 / 月度复盘" in source
    assert "周期与数据源" in source
    assert "收益结算" in source
    assert "周期收益结算" in source
    assert "本期复盘结论" in source
    assert "投资原则提醒" in source
    assert "今日原则" in source
    assert "先读原则，再记录错误。" in source
    assert "investment-core-rule-grid" in source
    assert "添加金句" in source
    assert "编辑当前" in source
    assert "查看全部投资原则" in source
    assert "保存本期复盘" in source
    assert "标的 / 场景" in source
    assert "损失金额" in source
    assert "单位：USD" in source
    assert "结果 / 影响" in source
    assert "事件经过" in source
    assert "下次防线" in source
    assert "已收进交易复盘。重点不是责备自己，而是下次别重复。" in source
    assert "这次错误已经沉淀为下次防线。" in source
    assert "还没有复盘记录。不是为了证明自己没错，而是把每次失误都留成证据。" in source
    assert "记录交易复盘后，系统会从你的反思里沉淀下次防线。" in source
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
    assert "原则文本" not in source
    assert "例如：800U、亏损500美元、卖飞约10%" not in source
    assert "添加错误复盘" not in source
    assert "保存这条错题" not in source
    assert "归档这条错误" not in source
    assert "保存标签" not in source
    assert "选择交易记录" not in source
    assert "交易纪律标签" not in source
    assert "通过 / 未通过" not in source
    assert "门禁" not in source

    render_body = source[source.index("def render(") : source.index("def _render_mistake_overview_strip")]
    assert render_body.index("_render_investment_principles_reminder") < render_body.index("_render_mistake_overview_strip")
    assert render_body.index("_render_mistake_overview_strip") < render_body.index("_render_quick_mistake_capture")
    assert render_body.index("_render_quick_mistake_capture") < render_body.index("_render_recent_mistakes")
    assert render_body.index("_render_recent_mistakes") < render_body.index("_render_next_defenses")
    assert render_body.index("高级统计 / 月度复盘") < render_body.index("_render_periodic_return_reviews")
    assert render_body.index("_render_periodic_return_reviews") < render_body.index("_render_periodic_review_conclusion")
    assert render_body.index("_render_periodic_review_conclusion") < render_body.index("_render_rule_library")
    assert render_body.index("_render_rule_library") < render_body.index("_render_portfolio_discipline")
    assert render_body.index("_render_portfolio_discipline") < render_body.index("_render_discipline_stats")
    assert "_render_principles_card" not in render_body


def test_app_registers_discipline_review_page() -> None:
    source = Path("app.py").read_text(encoding="utf-8")

    assert "PAGE_DISCIPLINE_REVIEW" in source
    assert 'PAGE_DISCIPLINE_REVIEW = "交易复盘"' in source
    assert '"交易错题本": PAGE_DISCIPLINE_REVIEW' in source
    assert '"discipline-review"' in source
    assert "discipline_review.render" in source
