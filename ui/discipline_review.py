from __future__ import annotations

from datetime import date, timedelta
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st

from data.decision_log import TradeJournalStore
from data.discipline_review import (
    MISTAKE_REVIEW_STATUSES,
    MISTAKE_TAG_OPTIONS,
    PERIODIC_RETURN_TYPES,
    DisciplineReviewStore,
    build_mistake_review_summary,
    build_periodic_return_review_summary,
    build_portfolio_discipline_summary,
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
        cols = st.columns([1, 2, 2, 1])
        review_date = cols[0].date_input("日期", value=date.today())
        scene_or_symbol = cols[1].text_input("标的 / 场景")
        loss_impact_text = cols[2].text_input("损失金额 / 影响，可选", placeholder="例如 800U、500美元、卖飞、无实际亏损")
        review_status = cols[3].selectbox("复盘状态", MISTAKE_REVIEW_STATUSES, index=0)
        mistake_tags = st.multiselect("错误类型，多选", MISTAKE_TAG_OPTIONS, placeholder="选择错误类型")
        st.markdown("#### 复盘正文")
        body_cols = st.columns(2)
        trigger_event = body_cols[0].text_area("事件导火索", height=72, placeholder="这件事是怎么开始的？")
        action_taken = body_cols[1].text_area("当时操作", height=72, placeholder="我具体做了什么？")
        result_text = body_cols[0].text_area("结果", height=72, placeholder="造成了什么结果？")
        reflection = body_cols[1].text_area("反思", height=90, placeholder="真正的问题是什么？")
        improvement_rule = body_cols[0].text_area("改进规则", height=72, placeholder="以后必须遵守什么规则？")
        next_defense = body_cols[1].text_area("下次防线", height=72, placeholder="下一次如何防止重复？")
        if st.form_submit_button("保存这条错题", width="stretch"):
            store.save_mistake_review(
                {
                    "review_date": review_date,
                    "scene_or_symbol": scene_or_symbol,
                    "loss_impact_text": loss_impact_text,
                    "trigger_event": trigger_event,
                    "action_taken": action_taken,
                    "result_text": result_text,
                    "mistake_tags": mistake_tags,
                    "reflection": reflection,
                    "improvement_rule": improvement_rule,
                    "next_defense": next_defense,
                    "review_status": review_status,
                }
            )
            st.success("错误复盘已记录。")
            st.rerun()

    with st.expander("SPACX 示例模板", expanded=False):
        st.markdown(
            """
            - 标的 / 场景：SPACX 合约空单
            - 事件导火索：短线想做回落，开了一笔空单。
            - 当时操作：开仓后没有设置止盈、止损，也没有设置提醒。
            - 结果：早上醒来发现单子还在，亏损约 800U。
            - 损失金额 / 影响：800U
            - 错误类型：没设止损、没设止盈、忘记持仓、隔夜暴露、执行纪律问题
            - 反思：这笔亏损不是方向判断问题，而是流程错误。合约单没有保护单，本质上就是裸奔。
            - 改进规则：所有币安合约单，开仓后必须立刻设置止盈止损；没有止损，不允许隔夜。
            - 下次防线：睡前固定检查币安持仓、止盈、止损、杠杆和保证金。
            """
        )

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
    with st.form("periodic-return-review-form", clear_on_submit=not bool(editing_row)):
        st.markdown("#### 收益数据")
        date_cols = st.columns([1, 1, 1])
        period_type = date_cols[0].selectbox(
            "周期类型",
            PERIODIC_RETURN_TYPES,
            index=_option_index(PERIODIC_RETURN_TYPES, editing_row.get("period_type") if editing_row else None),
        )
        start_date = date_cols[1].date_input("开始日期", value=_date_value(editing_row.get("start_date") if editing_row else None, date.today()))
        end_date = date_cols[2].date_input("结束日期", value=_date_value(editing_row.get("end_date") if editing_row else None, date.today()))
        equity_cols = st.columns(4)
        starting_equity = equity_cols[0].number_input(
            "期初净资产",
            min_value=0.0,
            value=_float_value(editing_row.get("starting_equity") if editing_row else None),
            step=1000.0,
        )
        ending_equity = equity_cols[1].number_input(
            "期末净资产",
            min_value=0.0,
            value=_float_value(editing_row.get("ending_equity") if editing_row else None),
            step=1000.0,
        )
        deposit_amount = equity_cols[2].number_input(
            "本期入金",
            min_value=0.0,
            value=_float_value(editing_row.get("deposit_amount") if editing_row else None),
            step=1000.0,
        )
        withdrawal_amount = equity_cols[3].number_input(
            "本期出金",
            min_value=0.0,
            value=_float_value(editing_row.get("withdrawal_amount") if editing_row else None),
            step=1000.0,
        )
        preview_profit = ending_equity - starting_equity - deposit_amount + withdrawal_amount
        preview_return = None if starting_equity <= 0 else preview_profit / starting_equity
        st.caption(f"本期盈亏：{_profit_text(preview_profit)}；本期收益率：{_return_rate_text(preview_return)}。这是手动复盘口径，不做复杂时间加权收益率。")

        st.markdown("#### 复盘内容")
        review_cols = st.columns(2)
        biggest_contributor = review_cols[0].text_area("本期最大贡献", value=str(editing_row.get("biggest_contributor") or "") if editing_row else "", height=72)
        biggest_drag = review_cols[1].text_area("本期最大拖累", value=str(editing_row.get("biggest_drag") or "") if editing_row else "", height=72)
        what_went_well = review_cols[0].text_area("本期做对了什么", value=str(editing_row.get("what_went_well") or "") if editing_row else "", height=86)
        what_went_wrong = review_cols[1].text_area("本期做错了什么", value=str(editing_row.get("what_went_wrong") or "") if editing_row else "", height=86)
        next_period_rule = st.text_area("下期重点规则", value=str(editing_row.get("next_period_rule") or "") if editing_row else "", height=72)
        notes = st.text_area("备注，可选", value=str(editing_row.get("notes") or "") if editing_row else "", height=64)
        submit_label = "保存修改" if editing_row else "保存周期复盘"
        form_cols = st.columns([1, 1, 4])
        submitted = form_cols[0].form_submit_button(submit_label, width="stretch")
        cancel_edit = bool(editing_row) and form_cols[1].form_submit_button("取消编辑", width="stretch")
    if submitted:
        store.save_periodic_return_review(
            {
                "period_type": period_type,
                "start_date": start_date,
                "end_date": end_date,
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
            },
            review_id=int(editing_row["id"]) if editing_row else None,
        )
        st.session_state.pop("periodic-return-edit-id", None)
        st.success("周期复盘已保存。")
        st.rerun()
    if cancel_edit:
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
        "<thead><tr><th>日期</th><th>标的 / 场景</th><th>损失金额 / 影响</th><th>错误类型</th><th>一句话反思</th><th>复盘状态</th><th>操作</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _mistake_detail_html(row: dict[str, Any]) -> str:
    tags = "、".join(row.get("mistake_tags") or []) or "未记录"
    return f"""
    <section class="mistake-detail-card">
      <h4>{escape(str(row.get('review_date') or ''))} · {escape(_scene_or_symbol(row))}</h4>
      <dl>
        <dt>损失金额 / 影响</dt><dd>{escape(_loss_impact(row))}</dd>
        <dt>事件导火索</dt><dd>{escape(str(row.get('trigger_event') or '未记录'))}</dd>
        <dt>当时操作</dt><dd>{escape(str(row.get('action_taken') or '未记录'))}</dd>
        <dt>结果</dt><dd>{escape(str(row.get('result_text') or '未记录'))}</dd>
        <dt>错误类型</dt><dd>{escape(tags)}</dd>
        <dt>反思</dt><dd>{escape(str(row.get('reflection') or '未记录'))}</dd>
        <dt>改进规则</dt><dd>{escape(str(row.get('improvement_rule') or '未记录'))}</dd>
        <dt>下次防线</dt><dd>{escape(str(row.get('next_defense') or '未记录'))}</dd>
        <dt>创建时间</dt><dd>{escape(str(row.get('created_at') or ''))}</dd>
        <dt>更新时间</dt><dd>{escape(str(row.get('updated_at') or ''))}</dd>
      </dl>
    </section>
    """


def _mistake_option_label(rows: list[dict[str, Any]], review_id: int) -> str:
    row = next((item for item in rows if int(item.get("id") or 0) == int(review_id)), {})
    return f"#{review_id} · {row.get('review_date', '')} · {_scene_or_symbol(row)}"


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
