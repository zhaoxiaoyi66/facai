from __future__ import annotations

from datetime import date, timedelta
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st

from data.decision_log import TradeJournalStore
from data.discipline_review import (
    EQUITY_FILL_AUTO,
    EQUITY_FILL_MANUAL,
    EQUITY_SOURCE_NOT_FOUND,
    EQUITY_SOURCE_PORTFOLIO,
    MISTAKE_REVIEW_STATUSES,
    MISTAKE_TAG_OPTIONS,
    PERIODIC_RETURN_TYPES,
    DisciplineReviewStore,
    build_mistake_review_summary,
    build_periodic_return_review_summary,
    build_portfolio_discipline_summary,
    default_period_dates,
)
from data.portfolio import PortfolioPositionStore
from data.prices import CACHE_PATH
from data.trade_intent import (
    BUY_BEHAVIOR_OPTIONS,
    SELL_BEHAVIOR_OPTIONS,
    STOCK_STAGE_OPTIONS,
    TradeIntentStore,
    build_trade_intent_review_stats,
)
from ui.theme import render_page_header, render_section_title


def render(path: Path = CACHE_PATH) -> None:
    _render_styles()
    render_page_header("纪律复盘", "记录投资原则、组合纪律和交易错误复盘。")
    discipline_store = DisciplineReviewStore(path)
    trade_store = TradeJournalStore(path)
    position_store = PortfolioPositionStore(path)
    entries = trade_store.list_entries()
    positions = position_store.list_active_positions()
    discipline_store.capture_current_account_equity_snapshot()

    _render_principles_card(discipline_store)
    _render_mistake_reviews(discipline_store)
    _render_periodic_return_reviews(discipline_store)
    with st.expander("组合纪律体检", expanded=False):
        _render_portfolio_discipline(discipline_store, positions, entries)
    _render_discipline_stats(discipline_store, entries)


def _render_principles_card(store: DisciplineReviewStore) -> None:
    render_section_title("我的投资原则", "个人纪律备忘，不参与 Setup 评分，也不会阻止交易。")
    st.markdown('<div class="discipline-principle-note">个人纪律备忘，不参与评分，也不影响交易。</div>', unsafe_allow_html=True)
    current = store.get_principles()
    with st.form("discipline-principles-form"):
        text = st.text_area("原则文本", value=current, height=100)
        cols = st.columns([1, 1, 4])
        save = cols[0].form_submit_button("保存原则", width="stretch")
        reset = cols[1].form_submit_button("重置默认", width="stretch")
    if save:
        store.save_principles(text)
        st.success("投资原则已保存。")
        st.rerun()
    if reset:
        store.reset_principles()
        st.success("已恢复默认投资原则。")
        st.rerun()


def _render_portfolio_discipline(store: DisciplineReviewStore, positions: list[dict], entries: list[dict]) -> None:
    render_section_title("组合纪律体检", "聚焦持仓数量、集中度和小仓数量。")
    settings = store.get_settings()
    summary = build_portfolio_discipline_summary(positions, entries, settings)
    cards = [
        ("当前持仓", str(summary["current_holding_count"]), f"目标 {summary['target_holding_min']}-{summary['target_holding_max']} 只"),
        ("Top 1 仓位", f"{summary['top1_weight_pct']:.1f}%", "按持仓成本估算"),
        ("Top 3 仓位", f"{summary['top3_weight_pct']:.1f}%", "集中度参考"),
        ("小仓数量", str(summary["small_position_count"]), f"低于 {settings['small_position_threshold_pct']}%"),
        ("本周新开仓", str(summary["new_position_count_this_week"]), "只做频率提醒"),
        ("本周计划外", str(summary["unplanned_trade_count_this_week"]), "按情绪标签粗略识别"),
    ]
    st.markdown(_card_grid_html(cards), unsafe_allow_html=True)
    with st.expander("纪律目标设置", expanded=False):
        with st.form("discipline-settings-form"):
            cols = st.columns(5)
            target_min = cols[0].number_input("目标持仓下限", min_value=1, max_value=50, value=int(settings["target_holding_min"]))
            target_max = cols[1].number_input("目标持仓上限", min_value=1, max_value=50, value=int(settings["target_holding_max"]))
            small_threshold = cols[2].number_input(
                "小仓阈值 %",
                min_value=0.1,
                max_value=50.0,
                value=float(settings["small_position_threshold_pct"]),
                step=0.5,
            )
            core_min = cols[3].number_input("目标核心仓下限", min_value=0, max_value=20, value=int(settings["target_core_min"]))
            core_max = cols[4].number_input("目标核心仓上限", min_value=0, max_value=20, value=int(settings["target_core_max"]))
            if st.form_submit_button("保存纪律目标", width="stretch"):
                store.save_settings(
                    {
                        "target_holding_min": target_min,
                        "target_holding_max": target_max,
                        "small_position_threshold_pct": small_threshold,
                        "target_core_min": core_min,
                        "target_core_max": core_max,
                    }
                )
                st.success("纪律目标已保存。")
                st.rerun()


def _render_mistake_reviews(store: DisciplineReviewStore) -> None:
    render_section_title(
        "交易错题本",
        "记录每一次不该发生的交易错误。重点不是责备自己，而是把错误沉淀成下一次的防线。",
    )
    rows = store.list_mistake_reviews()
    summary = build_mistake_review_summary(rows)
    st.markdown(_mistake_summary_html(summary), unsafe_allow_html=True)
    repeated = summary.get("repeated_mistake_types") or []
    if repeated:
        st.warning(f"最近重复出现：{'、'.join(repeated)}，建议把它写成明确规则。")

    with st.form("mistake-review-form", clear_on_submit=True):
        st.markdown("#### 基本信息")
        cols = st.columns([1, 2, 2])
        review_date = cols[0].date_input("日期", value=date.today())
        scene_or_symbol = cols[1].text_input("标的 / 场景", placeholder="例如：SPACX 空单、NOK 清仓、NVDA 追高")
        loss_impact_text = cols[2].text_input("损失金额 / 影响，可选", placeholder="例如：800U、亏损500美元、卖飞约10%、无实际亏损但操作失控")
        mistake_tags = st.multiselect("错误类型，多选", MISTAKE_TAG_OPTIONS, placeholder="选择错误类型")
        st.markdown("#### 复盘正文")
        trigger_event = st.text_area("事件经过", height=88, placeholder="这件事是怎么发生的？我当时做了什么？造成了什么影响？")
        reflection = st.text_area("核心反思", height=82, placeholder="真正的问题是什么？是判断错了，还是流程、纪律、仓位、情绪出了问题？")
        next_defense = st.text_area("下次防线", height=82, placeholder="下次遇到类似情况，必须执行什么规则？如何防止重复犯错？")
        if st.form_submit_button("保存这条错题", width="stretch"):
            store.save_mistake_review(
                {
                    "review_date": review_date,
                    "scene_or_symbol": scene_or_symbol,
                    "loss_impact_text": loss_impact_text,
                    "trigger_event": trigger_event,
                    "mistake_tags": mistake_tags,
                    "reflection": reflection,
                    "next_defense": next_defense,
                }
            )
            st.success("错误复盘已记录。")
            st.rerun()

    filtered = _filter_mistake_reviews(rows)
    st.markdown(_mistake_table_html(filtered), unsafe_allow_html=True)
    if filtered:
        options = [int(row["id"]) for row in filtered]
        selected_id = st.selectbox("查看记录详情", options, format_func=lambda value: _mistake_option_label(filtered, value))
        detail = next((row for row in filtered if int(row.get("id") or 0) == int(selected_id)), None)
        if detail:
            st.markdown(_mistake_detail_html(detail), unsafe_allow_html=True)


def _render_periodic_return_reviews(store: DisciplineReviewStore) -> None:
    render_section_title(
        "周期收益复盘",
        "每周末或每月末记录一次账户表现，把收益、错误和下期规则沉淀下来。",
    )
    rows = store.list_periodic_return_reviews()
    summary = build_periodic_return_review_summary(rows)
    st.markdown(_periodic_summary_html(summary), unsafe_allow_html=True)

    editing_id = st.session_state.get("periodic-return-edit-id")
    editing_row = store.get_periodic_return_review(int(editing_id)) if editing_id else None
    state = _prepare_periodic_return_form_state(store, rows, editing_row)
    meta = dict(state["meta"])

    st.markdown("#### 收益数据")
    control_cols = st.columns([1.1, 1, 1, 1, 1])
    control_cols[0].selectbox("周期类型", PERIODIC_RETURN_TYPES, key=state["period_type_key"])
    control_cols[1].date_input("开始日期", key=state["start_date_key"])
    control_cols[2].date_input("结束日期", key=state["end_date_key"])
    if control_cols[3].button("从持仓记录重新读取", key=f"{state['prefix']}-reload", width="stretch"):
        st.session_state[state["reload_key"]] = True
        st.rerun()
    if control_cols[4].button("使用上一条复盘期末净资产作为期初净资产", key=f"{state['prefix']}-use-previous", width="stretch"):
        previous_ending = _previous_periodic_ending_equity(rows, editing_row, st.session_state[state["start_date_key"]])
        if previous_ending is not None:
            st.session_state[state["starting_equity_key"]] = _amount_input_value(previous_ending, allow_blank=True)
            meta["starting_source_label"] = EQUITY_FILL_MANUAL
            st.session_state[state["meta_key"]] = meta
            st.rerun()
        st.info("没有可复用的上一条复盘期末净资产。")

    st.caption(_periodic_source_note(meta))
    if meta.get("only_latest_available"):
        st.info("当前系统暂无历史账户快照，只能读取最新账户净资产。建议从今天开始保存每日 / 每次刷新快照。")

    equity_cols = st.columns(4)
    equity_cols[0].text_input("期初账户净资产", key=state["starting_equity_key"], placeholder="自动读取或手动填写")
    equity_cols[0].caption(_field_source_caption(meta, "starting"))
    equity_cols[1].text_input("期末账户净资产", key=state["ending_equity_key"], placeholder="自动读取或手动填写")
    equity_cols[1].caption(_field_source_caption(meta, "ending"))
    equity_cols[2].text_input("本期入金", key=state["deposit_key"], placeholder="默认 0")
    equity_cols[3].text_input("本期出金", key=state["withdrawal_key"], placeholder="默认 0")

    starting_equity = _parse_amount_text(st.session_state.get(state["starting_equity_key"]))
    ending_equity = _parse_amount_text(st.session_state.get(state["ending_equity_key"]))
    deposit_amount = _parse_amount_text(st.session_state.get(state["deposit_key"]), default=0.0)
    withdrawal_amount = _parse_amount_text(st.session_state.get(state["withdrawal_key"]), default=0.0)
    preview_profit = None if starting_equity is None or ending_equity is None else ending_equity - starting_equity - deposit_amount + withdrawal_amount
    preview_return = None if preview_profit is None or starting_equity is None or starting_equity <= 0 else preview_profit / starting_equity
    st.markdown(_periodic_preview_cards_html(preview_profit, preview_return, _periodic_source_card_text(meta)), unsafe_allow_html=True)

    with st.form(f"periodic-return-review-form-{state['prefix']}", clear_on_submit=False):
        st.markdown("#### 复盘内容")
        review_cols = st.columns(2)
        biggest_contributor = review_cols[0].text_area("本期最大贡献", key=state["biggest_contributor_key"], height=72)
        biggest_drag = review_cols[1].text_area("本期最大拖累", key=state["biggest_drag_key"], height=72)
        what_went_well = review_cols[0].text_area("本期做对了什么", key=state["what_went_well_key"], height=86)
        what_went_wrong = review_cols[1].text_area("本期做错了什么", key=state["what_went_wrong_key"], height=86)
        next_period_rule = st.text_area("下期重点规则", key=state["next_period_rule_key"], height=72)
        notes = st.text_area("备注，可选", key=state["notes_key"], height=64)
        submit_label = "保存修改" if editing_row else "保存周期复盘"
        form_cols = st.columns([1, 1, 4])
        submitted = form_cols[0].form_submit_button(submit_label, width="stretch")
        cancel_edit = bool(editing_row) and form_cols[1].form_submit_button("取消编辑", width="stretch")
    if submitted:
        if _has_invalid_amount_input(st.session_state.get(state["starting_equity_key"])):
            st.error("期初账户净资产请输入有效数字，或留空。")
            return
        if _has_invalid_amount_input(st.session_state.get(state["ending_equity_key"])):
            st.error("期末账户净资产请输入有效数字，或留空。")
            return
        if _has_invalid_amount_input(st.session_state.get(state["deposit_key"]), allow_blank=False):
            st.error("本期入金请输入有效数字。")
            return
        if _has_invalid_amount_input(st.session_state.get(state["withdrawal_key"]), allow_blank=False):
            st.error("本期出金请输入有效数字。")
            return
        starting_manual = _is_manual_equity_override(starting_equity, meta.get("starting_auto_value"), meta.get("starting_source_label"))
        ending_manual = _is_manual_equity_override(ending_equity, meta.get("ending_auto_value"), meta.get("ending_source_label"))
        store.save_periodic_return_review(
            {
                "period_type": st.session_state[state["period_type_key"]],
                "start_date": st.session_state[state["start_date_key"]],
                "end_date": st.session_state[state["end_date_key"]],
                "starting_equity": starting_equity,
                "ending_equity": ending_equity,
                "deposit_amount": deposit_amount,
                "withdrawal_amount": withdrawal_amount,
                "biggest_contributor": biggest_contributor,
                "biggest_drag": biggest_drag,
                "what_went_well": what_went_well,
                "what_went_wrong": what_went_wrong,
                "next_period_rule": next_period_rule,
                "notes": notes,
                "starting_equity_source": _saved_equity_source_label(meta.get("starting_snapshot_date"), starting_equity, starting_manual),
                "ending_equity_source": _saved_equity_source_label(meta.get("ending_snapshot_date"), ending_equity, ending_manual),
                "starting_equity_snapshot_date": meta.get("starting_snapshot_date") or "",
                "ending_equity_snapshot_date": meta.get("ending_snapshot_date") or "",
                "starting_equity_is_manual_override": starting_manual,
                "ending_equity_is_manual_override": ending_manual,
            },
            review_id=int(editing_row["id"]) if editing_row else None,
        )
        _clear_periodic_return_form_state(state)
        st.session_state.pop("periodic-return-edit-id", None)
        st.success("周期复盘已保存。")
        st.rerun()
    if cancel_edit:
        _clear_periodic_return_form_state(state)
        st.session_state.pop("periodic-return-edit-id", None)
        st.rerun()

    filtered = _filter_periodic_return_reviews(rows)
    st.markdown(_periodic_table_html(filtered), unsafe_allow_html=True)
    if filtered:
        options = [int(row["id"]) for row in filtered]
        selected_id = st.selectbox("选择周期复盘记录", options, format_func=lambda value: _periodic_option_label(filtered, value))
        detail = next((row for row in filtered if int(row.get("id") or 0) == int(selected_id)), None)
        if detail:
            action_cols = st.columns([1, 1, 1, 5])
            if action_cols[0].button("查看详情", key=f"periodic-detail-{selected_id}", width="stretch"):
                st.session_state["periodic-return-detail-id"] = selected_id
            if action_cols[1].button("编辑", key=f"periodic-edit-{selected_id}", width="stretch"):
                st.session_state["periodic-return-edit-id"] = selected_id
                st.rerun()
            if action_cols[2].button("删除", key=f"periodic-delete-{selected_id}", width="stretch"):
                store.delete_periodic_return_review(selected_id)
                st.session_state.pop("periodic-return-edit-id", None)
                st.session_state.pop("periodic-return-detail-id", None)
                st.success("周期复盘已删除。")
                st.rerun()
            if st.session_state.get("periodic-return-detail-id") == selected_id:
                st.markdown(_periodic_detail_html(detail), unsafe_allow_html=True)


def _filter_mistake_reviews(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cols = st.columns([1, 1, 1, 1])
    tag = cols[0].selectbox("按错误类型筛选", ["全部", *MISTAKE_TAG_OPTIONS], key="mistake-tag-filter")
    status = cols[1].selectbox("按复盘状态筛选", ["全部", *MISTAKE_REVIEW_STATUSES], key="mistake-status-filter")
    recent_only = cols[2].checkbox("只看最近30天", value=False, key="mistake-recent-filter")
    loss_only = cols[3].checkbox("只看有损失金额 / 影响的记录", value=False, key="mistake-loss-filter")
    current = date.today()
    start = current - timedelta(days=29)
    result = []
    for row in rows:
        if tag != "全部" and tag not in (row.get("mistake_tags") or []):
            continue
        if status != "全部" and row.get("review_status") != status:
            continue
        if recent_only and not _date_in_range(row.get("review_date"), start, current):
            continue
        if loss_only and _loss_impact(row) == "-":
            continue
        result.append(row)
    return result


def _render_discipline_stats(store: DisciplineReviewStore, entries: list[dict]) -> None:
    render_section_title("纪律统计", "统计来自交易日志和交易意图记录；不要求手动给交易打标签。")
    periodic_summary = build_periodic_return_review_summary(store.list_periodic_return_reviews())
    periodic_cards = [
        ("周复盘记录数", str(periodic_summary.get("weekly_count") or 0), "手动记录"),
        ("月复盘记录数", str(periodic_summary.get("monthly_count") or 0), "手动记录"),
        ("最近4周累计收益", _profit_text(periodic_summary.get("recent_4_week_profit")), "周复盘口径"),
        ("最近3个月累计收益", _profit_text(periodic_summary.get("recent_3_month_profit")), "月复盘口径"),
        ("最大单周亏损", _profit_text(periodic_summary.get("max_weekly_loss")), "暂无记录则不显示亏损"),
        ("最大单月亏损", _profit_text(periodic_summary.get("max_monthly_loss")), "暂无记录则不显示亏损"),
    ]
    st.markdown(_card_grid_html(periodic_cards), unsafe_allow_html=True)
    intent_reviews = TradeIntentStore(store.path).list_intents()
    intent_stats = build_trade_intent_review_stats(entries, intent_reviews)
    intent_seven = intent_stats["seven_days"]
    intent_thirty = intent_stats["thirty_days"]
    flag_counts = intent_thirty["attention_flag_counts"]
    discipline_tag_counts = intent_thirty.get("discipline_tag_counts", {})
    stock_stage_counts = intent_thirty.get("stock_stage_counts", {})
    buy_behavior_counts = intent_thirty.get("buy_behavior_counts", {})
    sell_behavior_counts = intent_thirty.get("sell_behavior_counts", {})
    intent_cards = [
        ("最近 7 天交易次数", str(intent_seven["trade_count"]), "按交易日志"),
        ("最近 30 天交易次数", str(intent_thirty["trade_count"]), "按交易日志"),
        ("有复盘关注点", str(intent_thirty["attention_trade_count"]), "交易意图记录"),
        ("买入记录次数", str(intent_thirty["buy_review_count"]), "买入前记录"),
        ("卖出记录次数", str(intent_thirty["sell_review_count"]), "卖出前记录"),
        ("新增小仓风险", str(flag_counts.get("新增小仓风险", 0)), "最近 30 天"),
        ("怕错过风险", str(flag_counts.get("怕错过风险", 0)), "最近 30 天"),
        ("无下跌预案", str(flag_counts.get("无下跌预案", 0)), "最近 30 天"),
        ("无回补预案", str(flag_counts.get("无回补预案", 0)), "最近 30 天"),
        ("买点评分 < 70 仍买入", str(intent_thirty["low_setup_buy_count"]), "只做复盘"),
        ("量能承接 < 50 仍买入", str(intent_thirty["low_volume_acceptance_buy_count"]), "只做复盘"),
    ]
    st.markdown(_card_grid_html(intent_cards), unsafe_allow_html=True)
    discipline_sell_cards = [
        ("组合精简", str(discipline_tag_counts.get("组合精简", 0)), "纪律性卖出"),
        ("腾出仓位", str(discipline_tag_counts.get("腾出仓位", 0)), "纪律性卖出"),
        ("认知不匹配退出", str(discipline_tag_counts.get("认知不匹配退出", 0)), "纪律性卖出"),
        ("噪音过滤", str(discipline_tag_counts.get("噪音过滤", 0)), "纪律性卖出"),
        ("等待更好买点", str(discipline_tag_counts.get("等待更好买点", 0)), "纪律性卖出"),
        ("情绪卖出", str(flag_counts.get("情绪卖出风险", 0)), "风险性卖出"),
        ("卖出依据不清", str(flag_counts.get("卖出依据不清", 0)), "风险性卖出"),
        ("资金安排不清", str(flag_counts.get("资金安排不清", 0)), "风险性卖出"),
    ]
    st.markdown(_card_grid_html(discipline_sell_cards), unsafe_allow_html=True)
    stage_cards = [(option, str(stock_stage_counts.get(option, 0)), "股票阶段") for option in STOCK_STAGE_OPTIONS]
    buy_behavior_cards = [(option.split("：", 1)[0], str(buy_behavior_counts.get(option, 0)), "买入行为") for option in BUY_BEHAVIOR_OPTIONS]
    sell_behavior_cards = [(option.split("：", 1)[0], str(sell_behavior_counts.get(option, 0)), "卖出行为") for option in SELL_BEHAVIOR_OPTIONS]
    st.markdown(_card_grid_html(stage_cards), unsafe_allow_html=True)
    st.markdown(_card_grid_html(buy_behavior_cards), unsafe_allow_html=True)
    st.markdown(_card_grid_html(sell_behavior_cards), unsafe_allow_html=True)


def dashboard_discipline_card_html(snapshot: dict[str, Any]) -> str:
    intent = dict(snapshot.get("trade_intent") or {})
    flag_counts = dict(intent.get("attention_flag_counts") or {})
    periodic = dict(snapshot.get("periodic_returns") or {})
    reminders = []
    if not periodic.get("has_current_week_review"):
        reminders.append("本周尚未记录收益复盘。")
    if not periodic.get("has_current_month_review"):
        reminders.append("本月尚未记录收益复盘。")
    reminder_items = "".join(f"<li>{escape(item)}</li>" for item in reminders)
    return f"""
    <section class="dashboard-discipline-card">
      <div>
        <span>纪律提醒</span>
        <strong>交易前先记录意图：我为什么买？为什么卖？后面怎么处理？</strong>
      </div>
      <ul>
        <li>最近 30 天交易次数：{int(intent.get("trade_count") or 0)}</li>
        <li>有复盘关注点：{int(intent.get("attention_trade_count") or 0)}</li>
        <li>怕错过风险：{int(flag_counts.get("怕错过风险") or 0)}</li>
        <li>情绪卖出风险：{int(flag_counts.get("情绪卖出风险") or 0)}</li>
        <li>无回补预案：{int(flag_counts.get("无回补预案") or 0)}</li>
        {reminder_items}
      </ul>
    </section>
    """


def trade_entry_discipline_hint_html(setup_score: float | None = None) -> str:
    extra = ""
    if setup_score is not None and setup_score < 70:
        extra = " 当前 Setup 不是高质量买点，请确认这不是情绪买入或参与感小仓。"
    return (
        '<div class="trade-entry-discipline-hint">'
        "<strong>纪律提醒</strong>"
        f"<span>{escape('这笔交易会让组合更集中，还是更碎片化？' + extra)}</span>"
        "</div>"
    )


def _card_grid_html(cards: list[tuple[str, str, str]]) -> str:
    body = "".join(
        "<div>"
        f"<span>{escape(label)}</span>"
        f"<strong>{escape(value)}</strong>"
        f"<em>{escape(caption)}</em>"
        "</div>"
        for label, value, caption in cards
    )
    return f'<section class="discipline-card-grid">{body}</section>'


def _mistake_summary_html(summary: dict[str, Any]) -> str:
    cards = [
        ("错误记录总数", str(summary.get("total_count") or 0), "全部错题"),
        ("最近30天错误数", str(summary.get("recent_30_count") or 0), "按复盘日期"),
        ("最近30天损失 / 影响", str(summary.get("recent_30_loss_impact_text") or "暂无记录"), "手动记录口径"),
        ("最常见错误类型", str(summary.get("most_common_mistake_type") or "暂无"), f"{int(summary.get('most_common_mistake_count') or 0)} 次"),
        ("未形成规则", str(summary.get("unruled_count") or 0), "建议继续沉淀"),
    ]
    return _card_grid_html(cards)


def _mistake_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="discipline-empty">暂无符合条件的错误复盘记录。</div>'
    body = "".join(
        "<tr>"
        f"<td>{escape(str(row.get('review_date') or ''))}</td>"
        f"<td>{escape(_scene_or_symbol(row))}</td>"
        f"<td>{escape(_loss_impact(row))}</td>"
        f"<td>{escape('、'.join(row.get('mistake_tags') or []))}</td>"
        f"<td>{escape(_one_line(row.get('reflection')))}</td>"
        f"<td>{escape(str(row.get('review_status') or ''))}</td>"
        "<td>查看详情</td>"
        "</tr>"
        for row in rows[:80]
    )
    return (
        '<div class="discipline-table-wrap"><table class="discipline-table">'
        "<thead><tr><th>日期</th><th>标的 / 场景</th><th>损失金额 / 影响</th><th>错误类型</th><th>核心反思摘要</th><th>状态</th><th>操作</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _mistake_detail_html(row: dict[str, Any]) -> str:
    tags = "、".join(row.get("mistake_tags") or []) or "未记录"
    return f"""
    <section class="mistake-detail-card">
      <h4>{escape(str(row.get('review_date') or ''))} · {escape(_scene_or_symbol(row))}</h4>
      <dl>
        <dt>损失金额 / 影响</dt><dd>{escape(_loss_impact(row))}</dd>
        <dt>事件经过</dt><dd>{_detail_text_html(_mistake_event_summary(row))}</dd>
        <dt>错误类型</dt><dd>{escape(tags)}</dd>
        <dt>核心反思</dt><dd>{_detail_text_html(str(row.get('reflection') or '未记录'))}</dd>
        <dt>下次防线</dt><dd>{_detail_text_html(_mistake_next_defense(row))}</dd>
        <dt>创建时间</dt><dd>{escape(str(row.get('created_at') or ''))}</dd>
        <dt>更新时间</dt><dd>{escape(str(row.get('updated_at') or ''))}</dd>
      </dl>
    </section>
    """


def _mistake_option_label(rows: list[dict[str, Any]], review_id: int) -> str:
    row = next((item for item in rows if int(item.get("id") or 0) == int(review_id)), {})
    return f"#{review_id} · {row.get('review_date', '')} · {_scene_or_symbol(row)}"


def _mistake_event_summary(row: dict[str, Any]) -> str:
    parts = [
        _clean_detail_part(row.get("trigger_event")),
        _clean_detail_part(row.get("action_taken")),
        _clean_detail_part(row.get("result_text")),
    ]
    clean = [part for part in parts if part]
    return "\n".join(clean) if clean else "未记录"


def _mistake_next_defense(row: dict[str, Any]) -> str:
    parts = [
        _clean_detail_part(row.get("next_defense")),
        _clean_detail_part(row.get("improvement_rule")),
    ]
    clean = []
    for part in parts:
        if part and part not in clean:
            clean.append(part)
    return "\n".join(clean) if clean else "未记录"


def _clean_detail_part(value: object) -> str:
    return str(value or "").strip()


def _detail_text_html(value: object) -> str:
    return escape(str(value or "未记录")).replace("\n", "<br>")


def _periodic_summary_html(summary: dict[str, Any]) -> str:
    cards = [
        ("本周收益", _profit_with_return(summary.get("current_week_profit"), summary.get("current_week_return")), "周复盘"),
        ("本月收益", _profit_with_return(summary.get("current_month_profit"), summary.get("current_month_return")), "月复盘"),
        ("最近4周累计收益", _profit_text(summary.get("recent_4_week_profit")), "按最近4条周复盘"),
        ("最近4周最大亏损", _profit_text(summary.get("recent_4_week_max_loss")), "暂无记录则不显示亏损"),
    ]
    return _card_grid_html(cards)


def _filter_periodic_return_reviews(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    options = ["全部", "只看周复盘", "只看月复盘", "最近4周", "最近3个月", "今年"]
    selected = st.selectbox("周期收益复盘筛选", options, key="periodic-return-filter")
    current = date.today()
    if selected == "全部":
        return rows
    if selected == "只看周复盘":
        return [row for row in rows if row.get("period_type") == "周复盘"]
    if selected == "只看月复盘":
        return [row for row in rows if row.get("period_type") == "月复盘"]
    if selected == "最近4周":
        start = current - timedelta(days=27)
        return [row for row in rows if _date_in_range(row.get("end_date"), start, current)]
    if selected == "最近3个月":
        start = current - timedelta(days=92)
        return [row for row in rows if _date_in_range(row.get("end_date"), start, current)]
    if selected == "今年":
        start = date(current.year, 1, 1)
        return [row for row in rows if _date_in_range(row.get("end_date"), start, current)]
    return rows


def _periodic_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="discipline-empty">暂无周期收益复盘记录。</div>'
    body = "".join(
        "<tr>"
        f"<td>{escape(str(row.get('period_type') or ''))}</td>"
        f"<td>{escape(str(row.get('start_date') or ''))} 至 {escape(str(row.get('end_date') or ''))}</td>"
        f"<td>{escape(_money(row.get('starting_equity')))}</td>"
        f"<td>{escape(_money(row.get('ending_equity')))}</td>"
        f"<td>{escape(_profit_text(row.get('profit_amount')))}</td>"
        f"<td>{escape(_return_rate_text(row.get('return_rate')))}</td>"
        f"<td>{escape(_one_line(row.get('biggest_contributor')))}</td>"
        f"<td>{escape(_one_line(row.get('biggest_drag')))}</td>"
        "<td>查看 / 编辑 / 删除</td>"
        "</tr>"
        for row in rows[:80]
    )
    return (
        '<div class="discipline-table-wrap"><table class="discipline-table">'
        "<thead><tr><th>周期</th><th>起止日期</th><th>期初净资产</th><th>期末净资产</th><th>本期盈亏</th><th>收益率</th><th>最大贡献</th><th>最大拖累</th><th>操作</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _periodic_detail_html(row: dict[str, Any]) -> str:
    return f"""
    <section class="mistake-detail-card">
      <h4>{escape(str(row.get('period_type') or ''))} · {escape(str(row.get('start_date') or ''))} 至 {escape(str(row.get('end_date') or ''))}</h4>
      <dl>
        <dt>本期盈亏</dt><dd>{escape(_profit_text(row.get('profit_amount')))}</dd>
        <dt>收益率</dt><dd>{escape(_return_rate_text(row.get('return_rate')))}</dd>
        <dt>期初来源</dt><dd>{escape(str(row.get('starting_equity_source') or '未记录'))}</dd>
        <dt>期末来源</dt><dd>{escape(str(row.get('ending_equity_source') or '未记录'))}</dd>
        <dt>期初快照日期</dt><dd>{escape(str(row.get('starting_equity_snapshot_date') or '未记录'))}</dd>
        <dt>期末快照日期</dt><dd>{escape(str(row.get('ending_equity_snapshot_date') or '未记录'))}</dd>
        <dt>本期做对了什么</dt><dd>{escape(str(row.get('what_went_well') or '未记录'))}</dd>
        <dt>本期做错了什么</dt><dd>{escape(str(row.get('what_went_wrong') or '未记录'))}</dd>
        <dt>下期重点规则</dt><dd>{escape(str(row.get('next_period_rule') or '未记录'))}</dd>
        <dt>备注</dt><dd>{escape(str(row.get('notes') or '未记录'))}</dd>
        <dt>创建时间</dt><dd>{escape(str(row.get('created_at') or ''))}</dd>
        <dt>更新时间</dt><dd>{escape(str(row.get('updated_at') or ''))}</dd>
      </dl>
    </section>
    """


def _periodic_option_label(rows: list[dict[str, Any]], review_id: int) -> str:
    row = next((item for item in rows if int(item.get("id") or 0) == int(review_id)), {})
    return f"#{review_id} · {row.get('period_type', '')} · {row.get('start_date', '')} 至 {row.get('end_date', '')}"


def _prepare_periodic_return_form_state(
    store: DisciplineReviewStore,
    rows: list[dict[str, Any]],
    editing_row: dict[str, Any] | None,
) -> dict[str, Any]:
    prefix = f"periodic-return-{int(editing_row['id'])}" if editing_row else "periodic-return-new"
    keys = {
        "prefix": prefix,
        "period_type_key": f"{prefix}-period-type",
        "start_date_key": f"{prefix}-start-date",
        "end_date_key": f"{prefix}-end-date",
        "starting_equity_key": f"{prefix}-starting-equity",
        "ending_equity_key": f"{prefix}-ending-equity",
        "deposit_key": f"{prefix}-deposit",
        "withdrawal_key": f"{prefix}-withdrawal",
        "biggest_contributor_key": f"{prefix}-biggest-contributor",
        "biggest_drag_key": f"{prefix}-biggest-drag",
        "what_went_well_key": f"{prefix}-went-well",
        "what_went_wrong_key": f"{prefix}-went-wrong",
        "next_period_rule_key": f"{prefix}-next-rule",
        "notes_key": f"{prefix}-notes",
        "meta_key": f"{prefix}-meta",
        "signature_key": f"{prefix}-signature",
        "reload_key": f"{prefix}-reload-flag",
        "initialized_key": f"{prefix}-initialized",
        "last_period_type_key": f"{prefix}-last-period-type",
    }

    initial_period_type = str((editing_row or {}).get("period_type") or PERIODIC_RETURN_TYPES[0])
    if keys["period_type_key"] not in st.session_state:
        st.session_state[keys["period_type_key"]] = initial_period_type
    current_period_type = str(st.session_state.get(keys["period_type_key"]) or PERIODIC_RETURN_TYPES[0])
    default_start, default_end = default_period_dates(current_period_type, today=date.today())
    initial_start = _date_value((editing_row or {}).get("start_date"), default_start) if editing_row else default_start
    initial_end = _date_value((editing_row or {}).get("end_date"), default_end) if editing_row else default_end

    if not st.session_state.get(keys["initialized_key"]):
        st.session_state[keys["start_date_key"]] = initial_start
        st.session_state[keys["end_date_key"]] = initial_end
        st.session_state[keys["deposit_key"]] = _amount_input_value((editing_row or {}).get("deposit_amount"), allow_blank=False)
        st.session_state[keys["withdrawal_key"]] = _amount_input_value((editing_row or {}).get("withdrawal_amount"), allow_blank=False)
        st.session_state[keys["biggest_contributor_key"]] = str((editing_row or {}).get("biggest_contributor") or "")
        st.session_state[keys["biggest_drag_key"]] = str((editing_row or {}).get("biggest_drag") or "")
        st.session_state[keys["what_went_well_key"]] = str((editing_row or {}).get("what_went_well") or "")
        st.session_state[keys["what_went_wrong_key"]] = str((editing_row or {}).get("what_went_wrong") or "")
        st.session_state[keys["next_period_rule_key"]] = str((editing_row or {}).get("next_period_rule") or "")
        st.session_state[keys["notes_key"]] = str((editing_row or {}).get("notes") or "")
        st.session_state[keys["initialized_key"]] = True

    if st.session_state.get(keys["last_period_type_key"]) != current_period_type and not editing_row:
        st.session_state[keys["start_date_key"]] = default_start
        st.session_state[keys["end_date_key"]] = default_end
    st.session_state[keys["last_period_type_key"]] = current_period_type

    start_date = _date_value(st.session_state.get(keys["start_date_key"]), default_start)
    end_date = _date_value(st.session_state.get(keys["end_date_key"]), default_end)
    st.session_state[keys["start_date_key"]] = start_date
    st.session_state[keys["end_date_key"]] = end_date

    current_signature = f"{int(editing_row['id']) if editing_row else 'new'}|{current_period_type}|{start_date.isoformat()}|{end_date.isoformat()}"
    original_signature = None
    if editing_row:
        original_signature = (
            f"{int(editing_row['id'])}|"
            f"{editing_row.get('period_type') or PERIODIC_RETURN_TYPES[0]}|"
            f"{str(editing_row.get('start_date') or '')[:10]}|"
            f"{str(editing_row.get('end_date') or '')[:10]}"
        )

    if st.session_state.get(keys["signature_key"]) != current_signature or st.session_state.get(keys["reload_key"]):
        if editing_row and current_signature == original_signature:
            meta = _periodic_meta_from_saved_review(editing_row)
            st.session_state[keys["starting_equity_key"]] = _amount_input_value(editing_row.get("starting_equity"), allow_blank=True)
            st.session_state[keys["ending_equity_key"]] = _amount_input_value(editing_row.get("ending_equity"), allow_blank=True)
        else:
            prefill = store.build_periodic_return_prefill(start_date=start_date, end_date=end_date)
            meta = _periodic_meta_from_prefill(prefill)
            st.session_state[keys["starting_equity_key"]] = _amount_input_value(prefill.get("starting_equity"), allow_blank=True)
            st.session_state[keys["ending_equity_key"]] = _amount_input_value(prefill.get("ending_equity"), allow_blank=True)
        st.session_state[keys["meta_key"]] = meta
        st.session_state[keys["signature_key"]] = current_signature
        st.session_state[keys["reload_key"]] = False

    keys["meta"] = dict(st.session_state.get(keys["meta_key"]) or {})
    keys["all_keys"] = list(keys.values())
    return keys


def _periodic_meta_from_saved_review(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "starting_auto_value": _nullable_float(row.get("starting_equity")),
        "ending_auto_value": _nullable_float(row.get("ending_equity")),
        "starting_source_label": str(row.get("starting_equity_source") or EQUITY_SOURCE_NOT_FOUND),
        "ending_source_label": str(row.get("ending_equity_source") or EQUITY_SOURCE_NOT_FOUND),
        "starting_snapshot_date": str(row.get("starting_equity_snapshot_date") or ""),
        "ending_snapshot_date": str(row.get("ending_equity_snapshot_date") or ""),
        "only_latest_available": False,
    }


def _periodic_meta_from_prefill(prefill: dict[str, Any]) -> dict[str, Any]:
    return {
        "starting_auto_value": _nullable_float(prefill.get("starting_equity")),
        "ending_auto_value": _nullable_float(prefill.get("ending_equity")),
        "starting_source_label": EQUITY_FILL_AUTO if prefill.get("starting_snapshot") else EQUITY_SOURCE_NOT_FOUND,
        "ending_source_label": EQUITY_FILL_AUTO if prefill.get("ending_snapshot") else EQUITY_SOURCE_NOT_FOUND,
        "starting_snapshot_date": str(prefill.get("starting_equity_snapshot_date") or ""),
        "ending_snapshot_date": str(prefill.get("ending_equity_snapshot_date") or ""),
        "only_latest_available": bool(prefill.get("only_latest_available")),
    }


def _periodic_source_note(meta: dict[str, Any]) -> str:
    start_date_text = _display_snapshot_date(meta.get("starting_snapshot_date"))
    end_date_text = _display_snapshot_date(meta.get("ending_snapshot_date"))
    start_equity = _money(meta.get("starting_auto_value"))
    end_equity = _money(meta.get("ending_auto_value"))
    if meta.get("starting_snapshot_date") and meta.get("ending_snapshot_date"):
        return (
            "数据来源：已从持仓记录自动读取账户净资产"
            f"；期初快照：{start_date_text}  ${start_equity}"
            f"；期末快照：{end_date_text}  ${end_equity}"
        )
    if meta.get("starting_snapshot_date") or meta.get("ending_snapshot_date"):
        parts = ["数据来源：部分已自动读取，其余请手动填写"]
        if meta.get("starting_snapshot_date"):
            parts.append(f"期初快照：{start_date_text}  ${start_equity}")
        else:
            parts.append("期初快照：未找到")
        if meta.get("ending_snapshot_date"):
            parts.append(f"期末快照：{end_date_text}  ${end_equity}")
        else:
            parts.append("期末快照：未找到")
        return "；".join(parts)
    return "数据来源：未找到对应账户快照，请手动填写"


def _field_source_caption(meta: dict[str, Any], side: str) -> str:
    label = str(meta.get(f"{side}_source_label") or EQUITY_SOURCE_NOT_FOUND)
    if label == EQUITY_FILL_AUTO:
        snapshot_date = _display_snapshot_date(meta.get(f"{side}_snapshot_date"))
        return f"自动读取自持仓记录 · 快照日期 {snapshot_date}"
    if label == EQUITY_FILL_MANUAL:
        return "手动修改"
    return "未找到快照，可手动填写"


def _periodic_source_card_text(meta: dict[str, Any]) -> str:
    labels = {
        str(meta.get("starting_source_label") or EQUITY_SOURCE_NOT_FOUND),
        str(meta.get("ending_source_label") or EQUITY_SOURCE_NOT_FOUND),
    }
    labels.discard("")
    if labels == {EQUITY_FILL_AUTO}:
        return EQUITY_SOURCE_PORTFOLIO
    if EQUITY_FILL_MANUAL in labels:
        return EQUITY_FILL_MANUAL
    if EQUITY_FILL_AUTO in labels:
        return f"{EQUITY_SOURCE_PORTFOLIO} / {EQUITY_SOURCE_NOT_FOUND}"
    return EQUITY_SOURCE_NOT_FOUND


def _periodic_preview_cards_html(profit: float | None, return_rate: float | None, source_text: str) -> str:
    cards = [
        ("本期盈亏", _profit_text(profit), "按账户净资产口径计算"),
        ("本期收益率", _return_rate_text(return_rate), "期初净资产为分母"),
        ("数据来源", source_text or "未找到账户快照", "自动读取或手动修改"),
    ]
    return _card_grid_html(cards)


def _parse_amount_text(value: object, *, default: float | None = None) -> float | None:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return default
    return round(float(text), 2)


def _has_invalid_amount_input(value: object, *, allow_blank: bool = True) -> bool:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return not allow_blank
    try:
        return float(text) < 0
    except (TypeError, ValueError):
        return True


def _amount_input_value(value: object, *, allow_blank: bool) -> str:
    number = _nullable_float(value)
    if number is None:
        return "" if allow_blank else "0"
    if float(number).is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _nullable_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _previous_periodic_ending_equity(rows: list[dict[str, Any]], editing_row: dict[str, Any] | None, start_date: date) -> float | None:
    candidates = []
    for row in rows:
        if editing_row and int(row.get("id") or 0) == int(editing_row.get("id") or 0):
            continue
        row_end = _date_value(row.get("end_date"), start_date)
        ending_equity = _nullable_float(row.get("ending_equity"))
        if ending_equity is None or row_end >= start_date:
            continue
        candidates.append((row_end, ending_equity))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1] if candidates else None


def _is_manual_equity_override(value: float | None, baseline: object, source_label: object) -> bool:
    label = str(source_label or EQUITY_SOURCE_NOT_FOUND)
    baseline_value = _nullable_float(baseline)
    if value is None:
        return False
    if label == EQUITY_FILL_AUTO:
        return baseline_value is None or abs(value - baseline_value) > 0.005
    return True


def _saved_equity_source_label(snapshot_date: object, value: float | None, is_manual_override: bool) -> str:
    if value is None:
        return EQUITY_SOURCE_NOT_FOUND
    if is_manual_override:
        return EQUITY_FILL_MANUAL
    return EQUITY_FILL_AUTO if str(snapshot_date or "").strip() else EQUITY_SOURCE_NOT_FOUND


def _display_snapshot_date(value: object) -> str:
    text = str(value or "").strip()
    return text.replace("-", "/") if text else "未找到"


def _clear_periodic_return_form_state(state: dict[str, Any]) -> None:
    for key_name in (
        "period_type_key",
        "start_date_key",
        "end_date_key",
        "starting_equity_key",
        "ending_equity_key",
        "deposit_key",
        "withdrawal_key",
        "biggest_contributor_key",
        "biggest_drag_key",
        "what_went_well_key",
        "what_went_wrong_key",
        "next_period_rule_key",
        "notes_key",
        "meta_key",
        "signature_key",
        "reload_key",
        "initialized_key",
        "last_period_type_key",
    ):
        key = state.get(key_name)
        if key:
            st.session_state.pop(key, None)


def _date_in_range(value: object, start: date, end: date) -> bool:
    try:
        parsed = date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return False
    return start <= parsed <= end


def _money_value(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(value: object) -> str:
    number = _money_value(value)
    return "-" if number <= 0 else f"{number:,.2f}"


def _profit_text(value: object) -> str:
    if value is None or value == "":
        return "暂无记录"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "暂无记录"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:,.2f}"


def _return_rate_text(value: object) -> str:
    if value is None or value == "":
        return "无法计算"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "无法计算"
    return f"{number * 100:.2f}%"


def _profit_with_return(profit: object, return_rate: object) -> str:
    if profit is None:
        return "暂无记录"
    return f"{_profit_text(profit)} / {_return_rate_text(return_rate)}"


def _option_index(options: list[str], value: object) -> int:
    text = str(value or "")
    return options.index(text) if text in options else 0


def _date_value(value: object, default: date) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return default


def _float_value(value: object) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _scene_or_symbol(row: dict[str, Any]) -> str:
    return str(row.get("scene_or_symbol") or row.get("symbol") or "未填标的 / 场景").strip()


def _loss_impact(row: dict[str, Any]) -> str:
    text = str(row.get("loss_impact_text") or "").strip()
    if text:
        return text
    return _money(row.get("loss_amount"))


def _one_line(value: object, limit: int = 32) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if not text:
        return "-"
    return text if len(text) <= limit else f"{text[:limit]}..."


def _render_styles() -> None:
    st.markdown(
        """
        <style>
        .discipline-checklist,
        .discipline-card-grid,
        .dashboard-discipline-card,
        .trade-entry-discipline-hint,
        .mistake-detail-card {
            border: 1px solid rgba(148,163,184,.24);
            border-radius: 8px;
            background: #fff;
            box-shadow: 0 14px 28px rgba(15,23,42,.05);
        }
        .discipline-checklist { padding: .8rem 1rem; margin-bottom: .9rem; }
        .discipline-principle-note {
            text-align: right; color: #64748b; font-size: .78rem; font-weight: 800;
            margin: -.35rem 0 .35rem;
        }
        .discipline-checklist ol { margin: 0; padding: 0; list-style: none; display: grid; gap: .45rem; }
        .discipline-checklist li { display: flex; gap: .55rem; color: #334155; font-size: .9rem; }
        .discipline-checklist li span {
            display: inline-flex; align-items: center; justify-content: center;
            width: 1.35rem; height: 1.35rem; border-radius: 999px; background: #eef2ff; color: #3730a3; font-weight: 800;
        }
        .discipline-card-grid {
            display: grid; grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: .55rem; padding: .65rem; margin-bottom: .9rem;
        }
        .discipline-card-grid div { border-right: 1px solid rgba(148,163,184,.18); padding: .2rem .55rem; }
        .discipline-card-grid div:last-child { border-right: none; }
        .discipline-card-grid span, .discipline-card-grid em { display: block; color: #64748b; font-size: .76rem; font-style: normal; }
        .discipline-card-grid strong { display: block; color: #0f172a; font-size: 1.18rem; margin: .12rem 0; }
        .discipline-table-wrap { overflow: auto; border: 1px solid rgba(148,163,184,.24); border-radius: 8px; background: #fff; }
        .discipline-table { width: 100%; border-collapse: collapse; font-size: .82rem; }
        .discipline-table th, .discipline-table td { padding: .5rem .58rem; border-bottom: 1px solid rgba(148,163,184,.16); text-align: left; vertical-align: top; }
        .discipline-table th { color: #64748b; background: #f8fafc; }
        .discipline-empty { border: 1px dashed #cbd5e1; border-radius: 8px; padding: .9rem; color: #64748b; background: #f8fafc; margin: .7rem 0; }
        .mistake-detail-card { padding: .9rem 1rem; margin: .8rem 0; }
        .mistake-detail-card h4 { margin: 0 0 .55rem; color: #0f172a; }
        .mistake-detail-card dl { margin: 0; display: grid; grid-template-columns: 7rem 1fr; gap: .45rem .75rem; }
        .mistake-detail-card dt { color: #64748b; font-weight: 800; }
        .mistake-detail-card dd { margin: 0; color: #334155; }
        .dashboard-discipline-card {
            display: flex; justify-content: space-between; gap: 1rem; padding: .75rem .9rem; margin: .65rem 0;
        }
        .dashboard-discipline-card span { display:block; color:#64748b; font-size:.76rem; font-weight:800; }
        .dashboard-discipline-card strong { display:block; color:#0f172a; margin-top:.08rem; }
        .dashboard-discipline-card ul { margin:0; padding-left:1.1rem; color:#475569; font-size:.82rem; }
        .trade-entry-discipline-hint { padding: .62rem .72rem; margin: .45rem 0 .65rem; }
        .trade-entry-discipline-hint strong { display: block; color: #0f172a; }
        .trade-entry-discipline-hint span { display: block; color: #475569; font-size: .85rem; margin-top: .1rem; }
        @media (max-width: 900px) {
            .discipline-card-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .dashboard-discipline-card { display: block; }
            .mistake-detail-card dl { grid-template-columns: 1fr; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
