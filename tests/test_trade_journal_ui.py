from __future__ import annotations

import inspect
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from data.decision_log import TradeJournalStore
from data.trade_activity import build_daily_trade_activity, build_monthly_trade_calendar, group_trade_decisions
from data.trade_performance import summarize_trade_performance
from ui import trade_journal


def _trade(symbol: str, action: str, at: str, *, quantity: float = 1, price: float = 100, **extra) -> dict:
    return {
        "symbol": symbol,
        "action_type": action,
        "trade_date": at[:10],
        "created_at": at,
        "quantity": quantity,
        "price": price,
        **extra,
    }


def test_signal_stats_row_hides_unknown_group_key() -> None:
    html = trade_journal._stats_row_html({"group": "unknown", "sampleCount": 1}, {})

    assert "未标记" in html
    assert ">unknown<" not in html


def test_trade_activity_frequency_levels_by_decision_count() -> None:
    base = "2026-06-15"

    low = build_daily_trade_activity(base, [_trade("NVDA", "buy", f"{base}T09:00:00+08:00"), _trade("MSFT", "buy", f"{base}T10:00:00+08:00")])
    medium = build_daily_trade_activity(base, [_trade(f"T{i}", "buy", f"{base}T1{i}:00:00+08:00") for i in range(4)])
    high = build_daily_trade_activity(base, [_trade(f"H{i}", "buy", f"{base}T1{i}:00:00+08:00") for i in range(6)])
    critical = build_daily_trade_activity(base, [_trade(f"C{i}", "buy", f"{base}T{i:02d}:00:00+08:00") for i in range(8)])

    assert low["advisory_level"] == "LOW"
    assert medium["advisory_level"] == "MEDIUM"
    assert high["advisory_level"] == "HIGH"
    assert critical["advisory_level"] == "CRITICAL"


def test_trade_activity_groups_same_ticker_same_side_within_30_minutes() -> None:
    trades = [
        _trade("NVDA", "buy", "2026-06-15T09:00:00+08:00"),
        _trade("NVDA", "buy", "2026-06-15T09:20:00+08:00"),
        _trade("NVDA", "buy", "2026-06-15T10:10:00+08:00"),
    ]

    decisions = group_trade_decisions(trades)
    activity = build_daily_trade_activity("2026-06-15", trades)

    assert len(decisions) == 2
    assert activity["trade_record_count"] == 3
    assert activity["trade_decision_count"] == 2


def test_trade_activity_detects_reverse_loss_after_loss_and_late_night() -> None:
    trades = [
        _trade("NVDA", "buy", "2026-06-15T00:20:00+08:00"),
        _trade("NVDA", "sell", "2026-06-15T00:50:00+08:00", realized_pnl=-20),
        _trade("MSFT", "buy", "2026-06-15T01:10:00+08:00"),
        _trade("AAPL", "sell", "2026-06-15T01:40:00+08:00", advisory_level="HIGH_RISK"),
    ]

    activity = build_daily_trade_activity("2026-06-15", trades)

    assert activity["reverse_trade_count"] == 1
    assert activity["loss_sell_count"] == 1
    assert activity["trades_after_loss_count"] == 2
    assert activity["late_night_trade_count"] == 4
    assert activity["high_risk_advisory_count"] == 1
    assert activity["advisory_level"] in {"HIGH", "CRITICAL"}


def test_trade_activity_monthly_calendar_and_day_html_render_counts() -> None:
    trades = [
        _trade("NVDA", "buy", "2026-06-15T09:00:00+08:00", quantity=2, price=100),
        _trade("MSFT", "sell", "2026-06-15T10:00:00+08:00", quantity=1, price=200),
    ]

    calendar = build_monthly_trade_calendar(2026, 6, trades)
    html = trade_journal._trade_activity_calendar_html(calendar)
    day_html = trade_journal._trade_activity_day_table_html(trades)

    assert calendar["summary"]["trade_day_count"] == 1
    assert calendar["summary"]["monthly_trade_decision_count"] == 2
    assert "2 决策 / 2 记录" in html
    assert "tradeActivityDate=2026-06-15" in html
    assert "NVDA" in day_html
    assert "$200.00" in day_html
    assert "方向" in day_html
    assert "买入" in day_html
    assert "卖出" in day_html
    assert ">side<" not in day_html
    assert ">quantity<" not in day_html


def test_trade_activity_ui_localizes_advisory_levels() -> None:
    trades = [
        _trade("NVDA", "buy", "2026-06-15T09:00:00+08:00"),
        _trade("MSFT", "sell", "2026-06-15T10:00:00+08:00", advisory_level="HIGH_RISK"),
    ]

    calendar = build_monthly_trade_calendar(2026, 6, trades)
    html = trade_journal._trade_activity_calendar_html(calendar)
    day_html = trade_journal._trade_activity_day_table_html(trades)

    assert "正常" in html
    assert "高风险" in day_html
    assert ">LOW<" not in html
    assert "HIGH_RISK" not in day_html
    assert trade_journal._trade_advisory_level_text("NEW_INTERNAL_LEVEL") == "待复核"
    assert trade_journal._trade_advisory_level_text("人工风险") == "人工风险"


def test_trade_activity_ui_is_advisory_only_and_advanced_calendar_tab_exists() -> None:
    render_source = inspect.getsource(trade_journal.render)
    editor_source = inspect.getsource(trade_journal._render_editor)

    assert "交易日历" in render_source
    assert "_render_trade_activity_calendar(real_entries)" in render_source
    assert "_render_daily_trade_activity_advisory" in editor_source
    assert "userConfirmedDailyTradeAdvisory" in editor_source
    assert "quantity_error" in editor_source


def test_new_trade_entry_actions_are_sell_trim_only() -> None:
    assert set(trade_journal.SELL_ENTRY_ACTION_OPTIONS.values()) == {"sell", "trim"}
    assert "buy" not in trade_journal.SELL_ENTRY_ACTION_OPTIONS.values()
    assert "add" not in trade_journal.SELL_ENTRY_ACTION_OPTIONS.values()


def test_trade_journal_sell_editor_empty_position_copy_is_chinese() -> None:
    source = inspect.getsource(trade_journal._render_editor)

    assert "当前没有可卖出的启用持仓。" in source
    assert "active 持仓" not in source


def test_trade_journal_signal_labels_do_not_show_raw_internal_codes() -> None:
    labels = [
        trade_journal._signal_reason_label("NEW_BLOCK_REASON"),
        trade_journal._final_action_label("NEW_FINAL_ACTION"),
        trade_journal._lane_label("NEW_DECISION_LANE"),
        trade_journal._discipline_message_text("NEW_DISCIPLINE_MESSAGE"),
        trade_journal._discipline_status_text("NEW_DISCIPLINE_STATUS"),
        trade_journal._sell_reason_text("NEW_SELL_REASON"),
        trade_journal._radar_decision_text("NEW_RADAR_DECISION"),
        trade_journal._radar_data_status_text("NEW_RADAR_DATA_STATUS"),
    ]

    assert labels == ["其他原因", "未记录", "未记录", "未记录", "—", "—", "待复核", "待复核"]
    assert trade_journal._signal_reason_label("人工备注") == "人工备注"
    assert trade_journal._fundamental_change_labels_for_entry({"fundamental_change_types": ["NEW_FUNDAMENTAL_CHANGE"]}) == ["其他"]
    assert trade_journal._sell_reason_tag_labels_for_entry({"sell_reason_tags": ["NEW_SELL_REASON_TAG"]}) == ["其他"]
    assert trade_journal._fundamental_change_text({"fundamental_change_types": ["NEW_FUNDAMENTAL_CHANGE"]}) == "其他"
    assert trade_journal._performance_action_text("NEW_PERFORMANCE_ACTION") == "未记录"
    assert trade_journal._mood_text("NEW_MOOD") == "未记录"
    assert trade_journal._volume_price_zone_source_text("NEW_ZONE_SOURCE") == "未记录"
    assert trade_journal._fundamental_change_labels_for_entry({"fundamental_change_types": ["人工变化"]}) == ["人工变化"]
    assert trade_journal._sell_reason_tag_labels_for_entry({"sell_reason_tags": ["人工标签"]}) == ["人工标签"]
    assert trade_journal._performance_action_text("人工动作") == "人工动作"
    assert trade_journal._mood_text("人工心态") == "人工心态"
    assert trade_journal._volume_price_zone_source_text("人工来源") == "人工来源"
    assert trade_journal._radar_decision_text("人工判断") == "人工判断"
    assert trade_journal._radar_data_status_text("人工状态") == "人工状态"
    for label in labels:
        assert "NEW_" not in label


def test_trade_journal_ledger_failure_uses_clear_missing_error_reason(monkeypatch) -> None:
    deleted: list[int] = []
    store = SimpleNamespace(delete_entry=lambda entry_id: deleted.append(entry_id))
    monkeypatch.setattr(trade_journal, "apply_trade_to_portfolio", lambda _entry_id: {"status": "error"})

    level, message = trade_journal._apply_portfolio_ledger_or_remove(
        store,
        {"id": 42, "symbol": "NVDA", "action_type": "buy"},
    )

    assert level == "error"
    assert message == "NVDA 入账失败：未返回错误原因。交易日志未保存。"
    assert deleted == [42]
    assert "未知错误" not in message


def test_new_sell_entry_queues_intent_dialog_before_save() -> None:
    source = inspect.getsource(trade_journal._render_editor)

    assert "_render_pending_sell_intent_dialog(store)" in source
    assert "_queue_sell_intent(symbol, action_type, entry_values)" in source
    assert "_save_entry(store, symbol, entry_values)" not in source
    assert "_update_entry(store" in source


def test_trade_journal_row_shows_intent_review_tags_without_pass_fail_copy() -> None:
    html = trade_journal._entry_review_tags_html(
        {
            "action_type": "buy",
            "trade_intent_review": {
                "discipline_tags": ["组合精简"],
                "attention_flags": ["怕错过风险"],
            },
        }
    )

    assert "有交易意图记录" in html
    assert "有纪律标签" in html
    assert "有复盘关注点" in html
    assert "\u901a\u8fc7" not in html
    assert "\u672a\u901a\u8fc7" not in html


def test_new_trade_entry_uses_active_position_dropdown() -> None:
    source = inspect.getsource(trade_journal._render_editor)

    assert "_active_sell_positions" in source
    assert "SELL_ENTRY_ACTION_OPTIONS" in source
    assert "买入/加仓请前往组合持仓页操作" in source
    assert "selectbox(" in source
    assert '"持仓"' in source


def test_sell_position_dropdown_label_includes_position_context() -> None:
    label = trade_journal._sell_position_option_label(
        {
            "symbol": "NVDA",
            "quantity": 210,
            "averageCost": 215,
            "positionTier": "A",
            "plannedSellPrice": 300,
        }
    )

    assert "NVDA" in label
    assert "持有 210" in label
    assert "均价 $215.00" in label
    assert "A类" in label
    assert "目标卖出价 $300.00" in label


def test_sell_reference_context_includes_position_and_radar_state(monkeypatch) -> None:
    class FakeReport:
        def to_dict(self) -> dict:
            return {
                "buy_zone": {"lower": 90, "upper": 110},
                "price_position": "IN_BUY_ZONE",
            }

    monkeypatch.setattr(trade_journal, "build_cached_ai_stock_radar_report", lambda symbol: FakeReport())

    context = trade_journal._sell_reference_context(
        "NVDA",
        {
            "currentPrice": 100,
            "averageCost": 80,
            "unrealizedPnl": 200,
            "unrealizedPnlPct": 25,
            "positionTier": "A",
            "plannedSellPrice": 120,
            "createdAt": (date.today() - timedelta(days=12)).isoformat(),
        },
    )

    assert context["currentPrice"] == 100
    assert context["averageCost"] == 80
    assert context["unrealizedPnl"] == 200
    assert context["positionTier"] == "A"
    assert context["holdingDays"] == 12
    assert context["belowTargetSellPrice"] is True
    assert context["inBuyZoneOrBelow"] is True


def test_a_class_sell_reference_alerts_flag_core_risks() -> None:
    alerts = trade_journal._sell_reference_alerts(
        {
            "positionTier": "A",
            "belowTargetSellPrice": True,
            "inBuyZoneOrBelow": True,
            "holdingDays": 5,
        }
    )

    assert any("低于目标价" in item for item in alerts)
    assert any("仍在买区或低于买区" in item for item in alerts)
    assert any("持仓天数偏短" in item for item in alerts)
    assert any("具体回补计划" in item for item in alerts)


def test_sell_form_keeps_signal_id_in_advanced_section_and_has_quantity_shortcuts() -> None:
    source = inspect.getsource(trade_journal._render_editor)

    assert "_render_sell_quantity_shortcuts" in source
    assert "复盘信息，可选" in source
    assert "trade_cols[3].text_input" not in source


def test_sell_form_renders_structured_sell_reason_advisory() -> None:
    source = inspect.getsource(trade_journal._render_structured_sell_reason_editor)

    assert "原因标签（多选）" in source
    assert "补充说明（可选）" in source
    assert "卖出理由（必填）" not in source
    assert "估值压缩 / 风险溢价原因" not in source
    assert "流动性冲击 / 市场恐慌原因" not in source
    assert "仓位风险原因" not in source
    assert "sellContextType" not in source
    assert "这可能是在流动性较差或风险溢价上升时卖出核心资产" in source
    assert "情绪性卖出容易造成卖飞" in source
    assert "只用于复盘，不改变风险提示" in source


def test_sell_form_uses_compact_workflow_sections() -> None:
    editor_source = inspect.getsource(trade_journal._render_editor)
    activity_source = inspect.getsource(trade_journal._render_daily_trade_activity_advisory)
    summary_source = inspect.getsource(trade_journal._render_sell_reference_card)

    assert "### 成交信息" in editor_source
    assert "### 系统摘要" in editor_source
    assert "### 提交按钮" in editor_source
    assert "成交备注（可选）" in editor_source
    assert "情绪标签" not in editor_source
    assert "_render_trading_discipline_check(" not in editor_source
    assert 'st.expander("成交后仓位变化详情", expanded=False)' not in editor_source
    assert "### 提交确认" not in editor_source
    assert "卖出后剩余持仓" in summary_source
    assert "系统摘要" in summary_source
    assert "卖出后处理预案" in inspect.getsource(trade_journal._sell_reference_hint)
    assert "和回补计划" not in inspect.getsource(trade_journal._sell_reference_hint)
    assert "卖出类型 / 提醒" not in editor_source
    assert "今日交易较多" in activity_source
    assert "st.checkbox" not in activity_source
    assert "我已阅读今日交易频率提醒" not in activity_source


def test_edit_trade_entry_locks_symbol_and_action_type() -> None:
    source = inspect.getsource(trade_journal._render_editor)

    assert 'top_cols[0].text_input("股票代码", value=symbol, disabled=True' in source
    assert 'top_cols[1].text_input("操作类型", value=action_default, disabled=True' in source
    assert 'selectbox("操作类型", list(ACTION_OPTIONS)' not in source


def test_sell_quantity_cannot_exceed_current_position() -> None:
    assert trade_journal._sell_quantity_validation_error("sell", 11, 10)
    assert trade_journal._sell_quantity_validation_error("trim", 11, 10)
    assert trade_journal._sell_quantity_validation_error("sell", 10, 10) == ""
    assert trade_journal._sell_quantity_validation_error("buy", 11, 10) == ""


def test_edit_closed_sell_uses_original_quantity_as_available_position() -> None:
    original_sell = {"symbol": "ADBE", "action_type": "sell", "quantity": 10}

    assert (
        trade_journal._sell_quantity_validation_error(
            "sell",
            10,
            0,
            editing_entry=original_sell,
            symbol="ADBE",
        )
        == ""
    )
    assert (
        trade_journal._sell_quantity_validation_error(
            "sell",
            11,
            0,
            editing_entry=original_sell,
            symbol="ADBE",
        )
        == "修改后的卖出数量超过还原后可用持仓，请检查数量。"
    )


def test_new_sell_without_current_position_is_still_rejected() -> None:
    assert "已有" in trade_journal._sell_quantity_validation_error("sell", 1, 0)


def test_edit_sell_rejects_symbol_or_action_change() -> None:
    original_sell = {"symbol": "ADBE", "action_type": "sell", "quantity": 10}

    assert (
        trade_journal._sell_edit_identity_error(original_sell, "MSFT", "sell")
        == "历史交易不支持直接修改 ticker/account，请删除后重建。"
    )
    assert (
        trade_journal._sell_edit_identity_error(original_sell, "ADBE", "buy")
        == "历史交易不支持直接修改交易方向，请删除后重建。"
    )


def test_edit_closed_sell_price_updates_original_record_and_performance() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "decision_log.sqlite"
        store = TradeJournalStore(db_path)
        store.save_entry(
            "ADBE",
            {"trade_date": "2026-05-01", "action_type": "buy", "quantity": 10, "price": 100},
        )
        sell = store.save_entry(
            "ADBE",
            {"trade_date": "2026-05-02", "action_type": "sell", "quantity": 10, "price": 120},
        )

        updated = store.update_entry(
            int(sell["id"]),
            "ADBE",
            {"trade_date": "2026-05-02", "action_type": "sell", "quantity": 10, "price": 125},
        )

        sell_entries = [entry for entry in store.list_entries("ADBE") if entry["action_type"] == "sell"]
        assert len(sell_entries) == 1
        assert updated["price"] == 125
        summary = summarize_trade_performance(entries=store.list_entries("ADBE"), filters={})
        [trade] = summary["realized_trades"]
        assert trade["realized_pnl"] == 250


def test_reentry_plan_suggestion_uses_cached_radar_snapshot_boundary() -> None:
    source = inspect.getsource(trade_journal._build_reentry_plan_suggestion)

    assert "build_cached_ai_stock_radar_report" in source
    assert "build_market_context" not in source
    assert "build_market_history" not in source
    assert "build_buy_zone_context" not in source
    assert "generate_buy_zone" not in source
    assert '"daily_ohlcv": history' not in source
    assert "CacheReadModel" not in source


def test_reentry_plan_suggestion_prefers_unified_buy_zone_context_levels() -> None:
    pullback, breakout = trade_journal._reentry_levels_from_buy_zone_context(
        {
            "pullback_zone_low": 90,
            "pullback_zone_high": 95,
            "support_zone_high": 88,
            "confirmation_price": 112,
            "chase_price": 130,
        },
        sell_price=105,
        current_price=100,
    )

    assert pullback == 95
    assert breakout == 112


def test_trade_entry_detail_does_not_treat_invalidation_only_as_reentry_plan() -> None:
    entry = {
        "has_reentry_plan": 1,
        "reentry_thesis_invalidation": "thesis broken",
    }

    html = trade_journal._entry_reentry_plan_html(entry)

    assert trade_journal._entry_has_concrete_reentry_plan(entry) is False
    assert "未记录具体回补计划" in html
    assert "仅记录不回补条件" in html
    assert "<b>回补计划</b>" not in html


def test_trade_entry_detail_shows_concrete_reentry_plan() -> None:
    entry = {
        "has_reentry_plan": 1,
        "reentry_plan_text": "回踩买回",
        "reentry_pullback_price": 95,
        "reentry_buy_back_pct_on_pullback": 50,
        "reentry_thesis_invalidation": "thesis broken",
    }

    html = trade_journal._entry_reentry_plan_html(entry)

    assert trade_journal._entry_has_concrete_reentry_plan(entry) is True
    assert "<b>回补计划</b>" in html
    assert "回踩买回" in html


def test_buy_entry_detail_handles_legacy_missing_volume_price_snapshot() -> None:
    html = trade_journal._entry_discipline_snapshot_html(
        {
            "action_type": "buy",
            "position_class": "A",
            "radar_decision": "WAIT",
            "volume_price_status": None,
        }
    )

    assert "历史日志未记录量价快照" in html


def test_buy_entry_detail_displays_volume_price_snapshot() -> None:
    html = trade_journal._entry_discipline_snapshot_html(
        {
            "action_type": "buy",
            "position_class": "A",
            "radar_decision": "WAIT",
            "volume_price_status": "FORMING",
            "volume_price_score": 48,
            "volume_ratio": 0.6,
            "volume_regime_cn": "缩量",
            "volume_price_zone_source": "radar",
            "candle_signal_cn": "收盘改善",
            "volume_signal_cn": "缩量",
            "support_signal_cn": "支撑守住",
            "confirmation_signal_cn": "尚未确认",
            "distribution_count_10d": 1,
            "volume_price_reason_cn": "初步承接，尚未确认",
        }
    )

    assert "量价承接快照" in html
    assert "买区快照" in html
    assert "历史日志未记录量价快照" not in html


def test_buy_entry_radar_snapshot_is_localized_for_journal_detail() -> None:
    html = trade_journal._entry_radar_gate_snapshot_html(
        {
            "radar_decision": "DATA_MISSING",
            "radar_advisory_only": 1,
            "mood_gate_blocked": 0,
            "position_gate_blocked": 0,
            "radar_observation_only": 0,
            "gate_checked_at": "2026-06-18T01:00:00+00:00",
            "radar_advisory_warnings": ["买区提示缺失，需人工判断。"],
        }
    )

    assert "买区结论" in html
    assert "数据缺失" in html
    assert "买区提示" in html
    assert "Radar" not in html
    assert "DATA_MISSING" not in html


def test_b_class_gate_copy_does_not_use_a_class_core_language() -> None:
    result = SimpleNamespace(
        sellLevel="L3",
        maxAllowedSellPct=0.5,
        blockers=["b_class_low_sell_requires_downgrade_or_thesis"],
        warnings=[],
    )
    context = {
        "positionClass": "B",
        "currentQty": 100,
        "sellQty": 50,
        "actualSellPct": 0.5,
        "plannedSellPct": 0.5,
        "plannedSellQty": 50,
        "actualAfterQty": 50,
        "plannedAfterQty": 50,
        "plannedActualDiffPct": 0,
        "usesPlannedFallback": False,
        "coreRatioMin": 0,
        "coreMinQty": 0,
        "tradableQty": 100,
        "actualBreachesCore": False,
        "actualBreachQty": 0,
        "plannedBreachesCore": False,
        "plannedBreachQty": 0,
    }

    reasons = trade_journal._discipline_gate_reasons(result, context, 50)
    actions = trade_journal._discipline_gate_actions(result, context, 50)

    assert any("B 类" in item or "B类" in item for item in reasons + actions)
    assert not any("A 类核心" in item for item in reasons + actions)


def test_discipline_gate_conclusion_labels_are_chinese() -> None:
    assert trade_journal._discipline_gate_conclusion_label("PASS") == "风险较低"
    assert trade_journal._discipline_gate_conclusion_label("WARN") == "需要复核"
    assert trade_journal._discipline_gate_conclusion_label("FIX_REQUIRED") == "卖出前复核"
    assert trade_journal._discipline_gate_conclusion_label("BLOCK") == "高风险提醒"
    assert "通过" not in trade_journal._discipline_gate_conclusion_label("PASS")


def test_decision_mood_warning_uses_chinese_label() -> None:
    html = trade_journal._decision_mood_warning_html(
        {"action_type": "sell", "decision_mood": "panic_sell"}
    )

    assert "WARN" not in html
    assert "复核" in html


def test_neutral_decision_mood_is_available_and_displayed() -> None:
    assert list(trade_journal.DECISION_MOOD_OPTIONS.items())[1] == ("平静 / 无明显情绪", "NEUTRAL")
    assert trade_journal._decision_mood_text("NEUTRAL") == "平静 / 无明显情绪"
    assert trade_journal._decision_mood_label_for_entry({"decision_mood": "NEUTRAL"}) == "平静 / 无明显情绪"


def test_neutral_mood_text_is_supported_for_performance_groups() -> None:
    assert trade_journal._mood_text("NEUTRAL") == "平静 / 无明显情绪"


def test_c_class_event_exit_discipline_badge_shows_planned_exit() -> None:
    html = trade_journal._discipline_snapshot_badge(
        {
            "action_type": "sell",
            "position_class": "C",
            "sell_reason_type": "no_post_earnings_reaction",
            "discipline_status": "warning",
            "notes": "财报后无波动，按计划卖出，赌的就是财报，小亏几十U。",
        }
    )

    assert "计划内退出" in html
    assert "警告" not in html


def test_c_class_unstructured_event_exit_badge_shows_review() -> None:
    html = trade_journal._discipline_snapshot_badge(
        {
            "action_type": "sell",
            "position_class": "C",
            "sell_reason_type": "event_trade_done",
            "discipline_status": "warning",
            "notes": "赌财报。",
        }
    )

    assert "需复盘" in html
    assert "警告" not in html


def test_trade_performance_row_shows_missing_cost_basis_text() -> None:
    html = trade_journal._trade_performance_row_html(
        {
            "sell_date": "2026-06-04",
            "ticker": "XE",
            "action_type": "sell",
            "sell_quantity": 10,
            "sell_price": 80,
            "cost_basis_missing": True,
            "cost_basis_source": "missing",
            "cost_basis_status": "missing",
            "included_in_performance": False,
            "position_tier": "C",
            "sell_reason_type": "thesis_broken",
            "target_sell_price": 33,
            "sell_mood": "plan_execution",
            "reentry_plan_text": "thesis 破坏不回补",
            "discipline_flags": ["成本基准缺失"],
        }
    )

    assert "缺成本" in html
    assert "未计算" in html
    assert "缺日期" in html
    assert "缺 buy/add lot" in html
    assert "需补录成本" in html
    assert "未计入" in html
    assert "补录成本" in html
    assert "成本基准缺失" in html
    assert "目标价" in html
    assert "交易信息" in html
    assert "统计与纪律状态" in html


def test_trade_performance_row_shows_c_class_planned_event_exit_badges() -> None:
    html = trade_journal._trade_performance_row_html(
        {
            "sell_date": "2026-06-04",
            "ticker": "XE",
            "action_type": "sell",
            "sell_quantity": 200,
            "sell_price": 25.7,
            "buy_avg_price": 25.8,
            "realized_pnl": -20,
            "realized_pnl_pct": -0.39,
            "holding_days": 0,
            "cost_basis_missing": False,
            "cost_basis_source": "fifo",
            "cost_basis_status": "matched_fifo",
            "included_in_performance": True,
            "position_tier": "C",
            "sell_reason_type": "no_post_earnings_reaction",
            "event_trade_status": "planned_exit",
            "event_trade_note": "C类事件交易按计划退出。",
            "notes": "财报后无波动，按计划卖出",
            "discipline_flags": [],
        }
    )

    assert "计划内退出" in html
    assert "事件交易结束" in html
    assert "C类事件交易按计划退出" in html
    assert "纪律问题" in html
    assert "无" in html


def test_trade_performance_row_shows_compact_pnl_and_detail_fields() -> None:
    html = trade_journal._trade_performance_row_html(
        {
            "sell_date": "2026-06-04",
            "ticker": "NVDA",
            "action_type": "trim",
            "sell_quantity": 20,
            "sell_price": 230,
            "buy_avg_price": 215,
            "realized_pnl": 300,
            "realized_pnl_pct": 7,
            "holding_days": 18,
            "cost_basis_missing": False,
            "cost_basis_source": "fifo",
            "cost_basis_status": "matched_fifo",
            "included_in_performance": True,
            "position_tier": "A",
            "buy_mood": "plan_execution",
            "sell_mood": "well_reasoned",
            "sell_reason_type": "target_price",
            "target_sell_price": 300,
            "reentry_plan_text": "回踩买回",
            "discipline_flags": ["核心仓卖出需复盘"],
        }
    )

    assert "$300.00" in html
    assert "7.0%" in html
    assert "18" in html
    assert "A类" in html
    assert "目标价触发" in html
    assert "已计入" in html
    assert "核心仓需复盘" in html
    assert "FIFO buy/add lot" in html
    assert "回踩买回" in html


def test_trade_performance_row_shows_sell_review_labels() -> None:
    html = trade_journal._trade_performance_row_html(
        {
            "sell_date": "2026-06-04",
            "ticker": "NVDA",
            "action_type": "sell",
            "sell_quantity": 20,
            "sell_price": 230,
            "buy_avg_price": 215,
            "realized_pnl": 300,
            "realized_pnl_pct": 7,
            "holding_days": 5,
            "cost_basis_missing": False,
            "cost_basis_source": "fifo",
            "cost_basis_status": "matched_fifo",
            "included_in_performance": True,
            "position_tier": "A",
            "target_sell_price": 300,
            "discipline_flags": ["核心仓卖出需复盘", "低于买入目标价卖出"],
            "sell_review": {
                "labels": ["核心仓卖出需复盘", "低于买入目标价卖出"],
                "suspected_sell_fly": True,
                "data_missing_fields": [],
            },
        }
    )

    assert "卖出复盘" in html
    assert "疑似卖飞" in html
    assert "低于买入目标价卖出" in html


def test_trade_journal_top_summary_uses_core_metrics_only() -> None:
    source = inspect.getsource(trade_journal._render_summary)

    assert "已实现盈亏" in source
    assert "胜率" in source
    assert "平均持仓天数" in source
    assert "疑似卖飞次数" in source
    assert "历史非成交" in source
    assert "ENTRIES" not in source
    assert "SYMBOLS" not in source
    assert "SKIPPED" not in source


def test_trade_performance_stats_default_collapsed() -> None:
    source = inspect.getsource(trade_journal._render_trade_performance_stats)

    assert 'st.expander("完整战绩统计", expanded=False)' in source
    assert 'st.expander("战绩统计", expanded=True)' not in source


def test_trade_performance_row_keeps_details_collapsed_in_one_row() -> None:
    html = trade_journal._trade_performance_row_html(
        {
            "sell_date": "2026-06-04",
            "ticker": "NVDA",
            "action_type": "trim",
            "sell_quantity": 20,
            "sell_price": 230,
            "buy_avg_price": 215,
            "realized_pnl": 300,
            "realized_pnl_pct": 7,
            "holding_days": 18,
            "cost_basis_missing": False,
            "cost_basis_source": "fifo",
            "cost_basis_status": "matched_fifo",
            "included_in_performance": True,
            "position_tier": "A",
            "sell_reason_type": "target_price",
            "discipline_flags": ["核心仓卖出需复盘"],
        }
    )

    assert '<details class="trade-row-detail-toggle">' in html
    assert "<summary>查看详情</summary>" in html
    assert "performance-detail-row" not in html
    assert html.count("<tr>") == 1


def test_trade_journal_separates_executed_ledger_from_historical_non_trades(monkeypatch) -> None:
    entries = [
        {"id": 1, "symbol": "AVGO", "action_type": "add"},
        {"id": 1, "symbol": "AVGO", "action_type": "add"},
        {"id": 2, "symbol": "AVGO", "action_type": "add", "radar_observation_only": 1},
        {"id": 3, "symbol": "AVGO", "action_type": "sell", "discipline_status": "blocked"},
        {"id": 4, "symbol": "AVGO", "action_type": "add"},
    ]

    def fake_status(entry_id: int) -> dict:
        return {"syncStatus": "synced"} if entry_id == 1 else {"syncStatus": "not_synced"}

    monkeypatch.setattr(trade_journal, "get_trade_portfolio_sync_status", fake_status)

    executed = trade_journal._executed_trade_entries(entries)
    historical = trade_journal._historical_non_trade_entries(entries, executed)

    assert [entry["id"] for entry in executed] == [1]
    assert [entry["id"] for entry in historical] == [2, 3, 4]
    assert trade_journal._historical_non_trade_reason(entries[2]) == "历史仅观察记录"
    assert trade_journal._historical_non_trade_reason(entries[3]) == "历史卖出风险提醒记录"
    assert trade_journal._historical_non_trade_reason(entries[4]) == "历史未入账记录"


def test_trade_entry_detail_shows_sell_review_snapshot() -> None:
    html = trade_journal._entry_sell_review_html(
        {
            "action_type": "sell",
            "price": 120,
            "target_sell_price": 150,
            "position_class": "A",
            "holding_days": 5,
        }
    )

    assert "卖出复盘" in html
    assert "低于目标价" in html
    assert "疑似卖飞风险" in html


def test_sell_context_snapshot_values_include_position_and_radar_context() -> None:
    result = trade_journal._sell_context_snapshot_values(
        symbol="nvda",
        action_type="trim",
        trade_date="2026-06-04",
        entry_values={
            "quantity": "2",
            "price": "220",
            "decision_mood": "well_reasoned",
            "preTradeQuantity": 10,
            "preTradeAvgCost": 180,
            "preTradeTotalCost": 1800,
            "preTradePositionTier": "A",
            "preTradeTargetSellPrice": 260,
            "positionClass": "A",
            "sellReasonType": "target_price",
            "sellContextType": "liquidity_shock",
            "fundamentalChangeType": ["demand_path_change"],
            "liquidityShockReason": "市场恐慌",
            "sellThesisNote": "先降风险，等流动性恢复再回补",
            "reentryPlanText": "回踩买回",
        },
        position_row={"createdAt": "2026-06-01T09:30:00+08:00"},
        sell_reference={
            "currentPrice": 220,
            "targetSellPrice": 260,
            "buyZone": {"lower": 200, "upper": 230},
            "zoneStatus": "IN_BUY_ZONE",
            "pricePosition": "IN_BUY_ZONE",
            "radarDecision": "WAIT",
            "dataStatus": "fresh",
            "isStale": False,
            "distanceToTarget": -15.38,
            "holdingDays": 3,
            "inBuyZoneOrBelow": True,
            "missingSnapshotFields": [],
        },
    )

    snapshot = result["sellContextSnapshot"]
    assert snapshot["ticker"] == "NVDA"
    assert snapshot["position_tier"] == "A"
    assert snapshot["target_sell_price"] == 260
    assert snapshot["buy_zone"] == {"lower": 200, "upper": 230}
    assert snapshot["zone_status"] == "IN_BUY_ZONE"
    assert snapshot["below_target_at_sell"] is True
    assert snapshot["in_or_below_buy_zone_at_sell"] is True
    assert snapshot["sell_context_type"] == "liquidity_shock"
    assert snapshot["fundamental_change_type"] == ["demand_path_change"]
    assert snapshot["liquidity_shock_reason"] == "市场恐慌"
    assert snapshot["sell_thesis_note"] == "先降风险，等流动性恢复再回补"


def test_structured_sell_reason_options_cover_required_categories() -> None:
    assert trade_journal.SELL_CONTEXT_TYPE_OPTIONS["其他"] == "other"
    assert trade_journal.FUNDAMENTAL_CHANGE_OPTIONS["财务质量恶化"] == "financial_quality_deterioration"
    assert trade_journal.FUNDAMENTAL_CHANGE_OPTIONS["指引下修"] == "guidance_cut"
    assert trade_journal.FUNDAMENTAL_CHANGE_OPTIONS["其他"] == "other"


def test_sell_reason_fields_are_optional_for_submission() -> None:
    missing_change = trade_journal._structured_sell_reason_validation_error(
        "trim",
        {
            "sellContextType": "fundamental_change",
            "fundamentalChangeType": [],
            "sellThesisNote": "收入路径已经变了",
        },
    )
    missing_note = trade_journal._structured_sell_reason_validation_error(
        "trim",
        {
            "sellContextType": "fundamental_change",
            "fundamentalChangeType": ["guidance_cut"],
            "sellThesisNote": "",
        },
    )

    assert missing_change == ""
    assert missing_note == ""


def test_trade_entry_detail_displays_sell_context_snapshot() -> None:
    html = trade_journal._entry_sell_review_html(
        {
            "action_type": "trim",
            "sell_context_snapshot": {
                "sell_price": 220,
                "target_sell_price": 260,
                "position_tier": "A",
                "sell_context_type": "valuation_compression",
                "fundamental_change_type": ["margin_deterioration"],
                "valuation_compression_reason": "风险溢价上升",
                "sell_thesis_note": "估值压缩但主线未破坏，等回补",
                "buy_zone": {"lower": 200, "upper": 230},
                "zone_status": "IN_BUY_ZONE",
                "holding_days_reference": 3,
                "missing_snapshot_fields": [],
            },
        }
    )

    assert "卖出时等级" in html
    assert "卖出时目标价" in html
    assert "卖出时买区" in html
    assert "卖出时区间状态" in html
    assert "卖出原因类型" in html
    assert "估值压缩 / 风险溢价上升" in html
    assert "利润率恶化" in html
    assert "估值压缩但主线未破坏" in html


def test_trade_performance_detail_displays_sell_context_type() -> None:
    html = trade_journal._trade_performance_detail_html(
        {
            "sell_price": 120,
            "sell_context_type": "liquidity_shock",
            "sell_reason_type": "macro",
            "included_in_performance": True,
        }
    )

    assert "卖出原因类型" in html
    assert "流动性冲击 / 市场恐慌" in html


def test_empty_sell_context_type_displays_as_not_recorded() -> None:
    assert trade_journal._sell_context_type_text("") == "未记录"


def test_trade_journal_news_check_uses_specific_price_reaction_fallback() -> None:
    function_source = inspect.getsource(trade_journal._render_sell_news_check)

    assert "价格反应数据不足" in function_source
    assert 'news_price_match_label") or "数据不足"' not in function_source
