from __future__ import annotations

from datetime import date, timedelta
from html import escape
from pathlib import Path
import re
from typing import Any

import streamlit as st

from data.decision_log import TradeJournalStore
from data.discipline_review import (
    DEFAULT_PRINCIPLE_RULES,
    EQUITY_SOURCE_ACCOUNT_SNAPSHOT,
    EQUITY_FILL_AUTO,
    EQUITY_FILL_MANUAL,
    EQUITY_SOURCE_NOT_FOUND,
    EQUITY_SOURCE_PORTFOLIO,
    EQUITY_SOURCE_PREVIOUS_REVIEW,
    MISTAKE_REVIEW_STATUSES,
    MISTAKE_TAG_OPTIONS,
    PERIODIC_RETURN_TYPES,
    DisciplineReviewStore,
    build_mistake_review_summary,
    build_period_mistake_review_summary,
    build_periodic_return_review_summary,
    build_portfolio_discipline_summary,
    build_rule_library_from_mistakes,
    build_trade_review_conclusion,
    default_period_dates,
)
from data.investment_principles import (
    DEFAULT_NOTES_PATH,
    NOTE_TAG_OPTIONS,
    add_investment_note,
    delete_investment_note,
    filter_investment_notes,
    load_investment_notes,
    toggle_investment_note_pin,
    update_investment_note,
)
from data.portfolio import PortfolioPositionStore
from data.news_radar import trade_news_check
from data.prices import CACHE_PATH
from data.trade_intent import (
    BUY_BEHAVIOR_OPTIONS,
    SELL_BEHAVIOR_OPTIONS,
    STOCK_STAGE_OPTIONS,
    TradeIntentStore,
    build_trade_intent_review_stats,
)
from ui.theme import render_page_header, render_section_title


MISTAKE_TAG_GROUPS = {
    "交易纪律类": ["未设止损", "忘记持仓", "隔夜暴露", "计划外交易", "情绪交易"],
    "仓位管理类": ["仓位过大", "加仓过急", "没有分批", "核心仓卖飞", "战术仓失控"],
    "技术执行类": ["追高", "买早", "卖早", "破位未止损", "未等确认"],
    "认知偏差类": ["FOMO", "锚定成本", "过度自信", "亏损不认错", "看错逻辑"],
}

QUICK_MISTAKE_TAG_OPTIONS = [
    "追高",
    "买早",
    "卖飞",
    "忘记持仓",
    "没设止损",
    "仓位过大",
    "加仓太急",
    "计划外交易",
    "FOMO",
    "空强势标的",
    "没按计划执行",
    "小仓乱买",
]


def render(path: Path = CACHE_PATH) -> None:
    _render_styles()
    render_page_header("投资笔记", "记录投资金句、交易错误和自己的认知变化。")
    discipline_store = DisciplineReviewStore(path)
    _render_today_note_capture()
    _render_investment_note_library()
    mistake_rows = discipline_store.list_mistake_reviews()
    _render_trade_mistake_notebook(discipline_store, mistake_rows)

    with st.expander("简要统计 / 高级复盘", expanded=False):
        _render_mistake_overview_strip(mistake_rows)
        _render_next_defenses(mistake_rows)
        trade_store = TradeJournalStore(path)
        position_store = PortfolioPositionStore(path)
        entries = trade_store.list_entries()
        positions = position_store.list_active_positions()
        discipline_store.capture_current_account_equity_snapshot()
        periodic_rows = discipline_store.list_periodic_return_reviews()
        periodic_context = _build_periodic_return_context(discipline_store, periodic_rows)
        _render_trade_review_overview(mistake_rows, periodic_context)
        _render_periodic_return_reviews(discipline_store, periodic_rows, mistake_rows, periodic_context)
        _render_periodic_review_conclusion(mistake_rows, periodic_context)
        _render_rule_library(mistake_rows)
        with st.expander("组合纪律体检", expanded=False):
            _render_portfolio_discipline(discipline_store, positions, entries)
        _render_discipline_stats(discipline_store, entries)


def _render_today_note_capture(path: Path = DEFAULT_NOTES_PATH) -> None:
    st.markdown("### 今日记录")
    with st.container(border=True):
        with st.form("investment-note-capture-form", clear_on_submit=True):
            text = st.text_area(
                "笔记正文",
                height=112,
                placeholder="写下今天看到的一句话、一个原则、一个提醒……",
                label_visibility="collapsed",
            )
            note = st.text_area("解释 / 备注，可选", height=68, placeholder="这句话对你的交易或研究有什么提醒？")
            cols = st.columns([1.4, 1, 1])
            tags = cols[0].multiselect("标签", NOTE_TAG_OPTIONS, placeholder="选择标签")
            source = cols[1].text_input("来源，可选", placeholder="书、文章、播客、自己")
            related_symbol = cols[2].text_input("关联股票，可选", placeholder="例如 NVDA")
            submitted = st.form_submit_button("保存笔记", type="primary", width="stretch", help="在输入框中使用 Ctrl+Enter 也可以提交。")
        st.caption("快捷键：Ctrl+Enter 保存。")
    if not submitted:
        return
    try:
        add_investment_note(text, note=note, tags=tags, source=source, related_symbol=related_symbol, path=path)
        st.success("笔记已保存。")
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))


def _render_investment_note_library(path: Path = DEFAULT_NOTES_PATH) -> None:
    payload = load_investment_notes(path)
    notes = payload.get("notes") or []
    st.markdown("### 投资金句库")
    filter_cols = st.columns([1.7, 1.2, .8])
    search = filter_cols[0].text_input("搜索", placeholder="搜索正文、备注、来源或股票", key="investment-note-search")
    tags = filter_cols[1].multiselect("标签筛选", NOTE_TAG_OPTIONS, key="investment-note-tag-filter")
    pinned_only = filter_cols[2].checkbox("只看置顶", value=False, key="investment-note-pinned-filter")
    filtered = filter_investment_notes(notes, search=search, tags=tags, pinned_only=pinned_only)
    if not filtered:
        st.info("还没有符合筛选条件的笔记。可以先在上方写下今天看到的一句话。")
        return
    columns = st.columns(3)
    for index, item in enumerate(filtered):
        with columns[index % 3]:
            _render_investment_note_card(item, path=path)


def _render_investment_note_card(item: dict[str, Any], *, path: Path = DEFAULT_NOTES_PATH) -> None:
    note_id = str(item.get("id") or "")
    edit_key = f"investment-note-editing-{note_id}"
    with st.container(border=True):
        pin_mark = "置顶 · " if bool(item.get("pinned")) else ""
        st.caption(f"{pin_mark}{_display_date(item.get('created_at'))}")
        st.markdown(f"**{str(item.get('text') or '').strip()}**")
        if str(item.get("note") or "").strip():
            st.caption(str(item.get("note") or "").strip())
        meta_parts = []
        tags = [str(tag) for tag in item.get("tags") or [] if str(tag).strip()]
        if tags:
            meta_parts.append(" / ".join(tags))
        if str(item.get("source") or "").strip():
            meta_parts.append(f"来源：{item.get('source')}")
        if str(item.get("related_symbol") or "").strip():
            meta_parts.append(f"股票：{item.get('related_symbol')}")
        if meta_parts:
            st.caption("｜".join(meta_parts))

        action_cols = st.columns(3)
        if action_cols[0].button("取消置顶" if bool(item.get("pinned")) else "置顶", key=f"investment-note-pin-{note_id}"):
            toggle_investment_note_pin(note_id, path=path)
            st.rerun()
        if action_cols[1].button("编辑", key=f"investment-note-edit-{note_id}"):
            st.session_state[edit_key] = not bool(st.session_state.get(edit_key))
        if action_cols[2].button("删除", key=f"investment-note-delete-{note_id}"):
            delete_investment_note(note_id, path=path)
            st.success("笔记已删除。")
            st.rerun()

        if st.session_state.get(edit_key):
            with st.form(f"investment-note-edit-form-{note_id}"):
                text = st.text_area("正文", value=str(item.get("text") or ""), height=88)
                note = st.text_area("解释 / 备注", value=str(item.get("note") or ""), height=68)
                edit_cols = st.columns([1.3, 1, 1])
                tags = edit_cols[0].multiselect("标签", NOTE_TAG_OPTIONS, default=[tag for tag in tags if tag in NOTE_TAG_OPTIONS])
                source = edit_cols[1].text_input("来源", value=str(item.get("source") or ""))
                related_symbol = edit_cols[2].text_input("关联股票", value=str(item.get("related_symbol") or ""))
                submitted = st.form_submit_button("保存修改", type="primary")
            if submitted:
                update_investment_note(
                    note_id,
                    text=text,
                    note=note,
                    tags=tags,
                    source=source,
                    related_symbol=related_symbol,
                    pinned=bool(item.get("pinned")),
                    path=path,
                )
                st.session_state[edit_key] = False
                st.success("笔记已更新。")
                st.rerun()


def _render_trade_mistake_notebook(store: DisciplineReviewStore, rows: list[dict[str, Any]]) -> None:
    st.markdown("### 交易错题本")
    st.caption("记录真实犯错的交易，不做拦截，只做复盘。")
    if st.button("记录一笔错误", type="primary", key="show-trade-mistake-form"):
        st.session_state["trade_mistake_form_open"] = not bool(st.session_state.get("trade_mistake_form_open"))
    if st.session_state.get("trade_mistake_form_open"):
        _render_trade_mistake_form(store)
    active_rows = [row for row in rows if str(row.get("review_status") or "") != MISTAKE_REVIEW_STATUSES[-1]]
    archived_rows = [row for row in rows if str(row.get("review_status") or "") == MISTAKE_REVIEW_STATUSES[-1]]
    if not active_rows:
        st.info("还没有未归档的交易错误记录。")
    else:
        columns = st.columns(2)
        for index, row in enumerate(active_rows):
            with columns[index % 2]:
                _render_trade_mistake_card(store, row)
    if archived_rows:
        with st.expander(f"已归档错误 {len(archived_rows)} 条", expanded=False):
            columns = st.columns(2)
            for index, row in enumerate(archived_rows):
                with columns[index % 2]:
                    _render_trade_mistake_card(store, row, archived=True)


TRADE_MISTAKE_TYPES = ["追涨", "杀跌", "仓位过重", "没按计划", "FOMO", "止损错误", "卖飞", "其他"]


def _render_trade_mistake_form(store: DisciplineReviewStore, *, row: dict[str, Any] | None = None) -> None:
    row = row or {}
    row_id = int(row.get("id") or 0)
    form_key = f"trade-mistake-form-{row_id or 'new'}"
    with st.form(form_key, clear_on_submit=not row_id):
        cols = st.columns([.9, .9, 1.1, .9])
        review_date = cols[0].date_input("日期", value=_date_value(row.get("review_date"), date.today()))
        symbol = cols[1].text_input("股票", value=str(row.get("symbol") or "").strip(), placeholder="例如 NVDA")
        existing_tags = [str(tag) for tag in row.get("mistake_tags") or []]
        default_type = existing_tags[0] if existing_tags and existing_tags[0] in TRADE_MISTAKE_TYPES else "追涨"
        mistake_type = cols[2].selectbox("错误类型", TRADE_MISTAKE_TYPES, index=TRADE_MISTAKE_TYPES.index(default_type))
        loss_amount = cols[3].number_input("亏损金额，可选", min_value=0.0, value=float(row.get("loss_amount") or 0), step=10.0, format="%.2f")
        reflection = st.text_area("错误一句话", value=str(row.get("reflection") or ""), height=72, placeholder="这次真正错在哪里？")
        next_defense = st.text_area("正确做法一句话", value=str(row.get("next_defense") or ""), height=72, placeholder="下次遇到类似情况应该怎么做？")
        submitted = st.form_submit_button("保存错误记录", type="primary")
    if not submitted:
        return
    if not str(reflection or "").strip():
        st.error("请写下错误一句话。")
        return
    if not str(next_defense or "").strip():
        st.error("请写下正确做法一句话。")
        return
    values = {
        "review_date": review_date,
        "symbol": symbol,
        "scene_or_symbol": symbol or mistake_type,
        "loss_amount_usd": loss_amount,
        "mistake_tags": [mistake_type],
        "reflection": reflection,
        "next_defense": next_defense,
        "review_status": row.get("review_status") or MISTAKE_REVIEW_STATUSES[0],
    }
    if row_id:
        store.update_mistake_review(row_id, values)
        st.success("错误记录已更新。")
    else:
        store.save_mistake_review(values)
        st.success("错误记录已保存。")
        st.session_state["trade_mistake_form_open"] = False
    st.rerun()


def _render_trade_mistake_card(store: DisciplineReviewStore, row: dict[str, Any], *, archived: bool = False) -> None:
    row_id = int(row.get("id") or 0)
    edit_key = f"trade-mistake-editing-{row_id}"
    with st.container(border=True):
        tags = " / ".join(str(tag) for tag in row.get("mistake_tags") or []) or "其他"
        symbol = str(row.get("symbol") or row.get("scene_or_symbol") or "").strip() or "未填写股票"
        st.caption(f"{row.get('review_date') or ''}｜{tags}{'｜已归档' if archived else ''}")
        st.markdown(f"**{symbol}**")
        st.markdown(f"错误：{str(row.get('reflection') or '未填写')}")
        st.caption(f"正确做法：{str(row.get('next_defense') or '未填写')}")
        if _loss_amount_value(row) > 0:
            st.caption(f"亏损金额：{_loss_amount_text(row)}")
        cols = st.columns(3)
        if cols[0].button("编辑", key=f"trade-mistake-edit-{row_id}"):
            st.session_state[edit_key] = not bool(st.session_state.get(edit_key))
        if cols[1].button("删除", key=f"trade-mistake-delete-{row_id}"):
            store.delete_mistake_review(row_id)
            st.success("错误记录已删除。")
            st.rerun()
        if not archived and cols[2].button("归档", key=f"trade-mistake-archive-{row_id}"):
            store.archive_mistake_review(row_id)
            st.success("错误记录已归档。")
            st.rerun()
        if st.session_state.get(edit_key):
            _render_trade_mistake_form(store, row=row)


def _render_mistake_overview_strip(rows: list[dict[str, Any]]) -> None:
    summary = build_mistake_review_summary(rows)
    total = int(summary.get("total_count") or 0)
    recent = int(summary.get("recent_30_count") or 0)
    recent_loss = float(summary.get("recent_30_loss_amount") or 0)
    unclosed = int(summary.get("unruled_count") or 0)
    items = [
        ("错题总数", str(total)),
        ("最近 30 天错题", str(recent)),
        ("最近 30 天损失", f"${recent_loss:,.2f}" if recent_loss > 0 else "无损失记录"),
        ("未闭环防线", str(unclosed)),
    ]
    st.markdown(_mistake_overview_strip_html(items), unsafe_allow_html=True)


def _render_quick_mistake_capture(store: DisciplineReviewStore) -> None:
    render_section_title("快速记录一次错误", "30 秒把错误收进复盘记录，重点是写下下一次怎么防。")
    with st.form("mistake-review-form", clear_on_submit=True):
        cols = st.columns([1, 2.4, 1.2])
        review_date = cols[0].date_input("日期", value=date.today())
        scene_or_symbol = cols[1].text_input(
            "标的 / 场景",
            placeholder="例如：SPACEX 空单、NOW 买早、NVDA 卖飞",
        )
        loss_amount_usd = cols[2].number_input("损失金额", min_value=0.0, value=0.0, step=10.0, format="%.2f")
        cols[2].caption("单位：USD，可填 0")
        mistake_tags = _render_quick_mistake_tag_inputs()
        st.markdown(
            f'<div class="mistake-principle-reminder">{escape(principle_reminder_for_mistake_tags(mistake_tags))}</div>',
            unsafe_allow_html=True,
        )
        reflection = st.text_area(
            "一句话反思",
            height=72,
            placeholder="这次真正的问题是什么？",
        )
        next_defense = st.text_area(
            "下次防线",
            height=72,
            placeholder="下次遇到类似情况，我必须怎么做？",
        )
        trigger_event = ""
        impact_summary = ""
        result_text = ""
        with st.expander("补充详细复盘", expanded=False):
            trigger_event = st.text_area("事件经过", height=82, placeholder="这件事是怎么发生的？我当时做了什么？")
            impact_summary = st.text_area("结果 / 影响", height=82, placeholder="造成了什么结果？亏损、卖飞、错过机会，还是破坏了纪律？")
            emotion = st.text_input("当时情绪", placeholder="例如：怕错过、急着证明、想扳回")
            violated_plan = st.checkbox("是否违反原计划", value=False)
            needs_reminder = st.checkbox("是否需要交易前提醒", value=False)
            attach_news_summary = st.checkbox("附加交易前 7 天相关新闻摘要", value=False)
            detail_parts = []
            if emotion.strip():
                detail_parts.append(f"当时情绪：{emotion.strip()}")
            detail_parts.append(f"是否违反原计划：{'是' if violated_plan else '否'}")
            detail_parts.append(f"是否需要交易前提醒：{'是' if needs_reminder else '否'}")
            result_text = "\n".join(detail_parts)

        submitted = st.form_submit_button("收进复盘", type="primary", width="stretch")
    if not submitted:
        return
    if not str(scene_or_symbol or "").strip():
        st.error("请先填写标的 / 场景。")
        return
    if not str(reflection or "").strip():
        st.error("请写一句话反思。")
        return
    if not str(next_defense or "").strip():
        st.error("请写下次防线。")
        return
    if attach_news_summary:
        news_summary = _trade_news_summary_for_mistake(scene_or_symbol)
        if news_summary:
            impact_summary = "\n".join(part for part in [impact_summary, news_summary] if str(part or "").strip())
    store.save_mistake_review(
        {
            "review_date": review_date,
            "scene_or_symbol": scene_or_symbol,
            "loss_amount_usd": loss_amount_usd,
            "impact_summary": impact_summary,
            "trigger_event": trigger_event,
            "result_text": result_text,
            "mistake_tags": mistake_tags,
            "reflection": reflection,
            "next_defense": next_defense,
            "review_status": "已记录",
        }
    )
    st.success("已收进交易复盘。重点不是责备自己，而是下次别重复。")
    st.info("这次错误已经沉淀为下次防线。")
    st.rerun()


def _trade_news_summary_for_mistake(scene_or_symbol: object) -> str:
    symbol = _extract_symbol_from_scene(scene_or_symbol)
    if not symbol:
        return ""
    try:
        context = trade_news_check(symbol)
    except Exception:
        return f"交易前新闻摘要（{symbol}）：新闻缓存暂不可用。"
    headlines = [str(item) for item in (context.get("headlines") or []) if str(item).strip()]
    headline_text = "；".join(headlines[:3]) if headlines else "无关键标题"
    return (
        f"交易前新闻摘要（{symbol}）：7 天重大新闻 {int(context.get('major_news_7d') or 0)} 条，"
        f"重大正面 {int(context.get('positive_major_7d') or 0)} 条，"
        f"重大负面 {int(context.get('negative_major_7d') or 0)} 条，"
        f"一致性：{context.get('news_price_match_label') or '数据不足'}。"
        f"关键标题：{headline_text}"
    )


def _extract_symbol_from_scene(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    match = re.search(r"\b[A-Z][A-Z0-9.\-]{0,9}\b", text)
    return match.group(0) if match else ""


def _render_quick_mistake_tag_inputs() -> list[str]:
    quick_options = [tag for tag in QUICK_MISTAKE_TAG_OPTIONS if tag in MISTAKE_TAG_OPTIONS]
    selected = st.multiselect(
        "犯错行为",
        quick_options,
        placeholder="选择常用错误标签",
        key="quick-mistake-tags",
    )
    more_options = [tag for tag in MISTAKE_TAG_OPTIONS if tag not in quick_options]
    with st.expander("更多错误类型", expanded=False):
        selected.extend(
            st.multiselect(
                "其他错误类型",
                more_options,
                placeholder="选择更多错误类型",
                key="more-mistake-tags",
            )
        )
    return _dedupe_text(selected)


def _render_recent_mistakes(rows: list[dict[str, Any]]) -> None:
    render_section_title("最近复盘", "默认只显示最近 5 条，详情按需展开。")
    recent = _recent_mistake_rows(rows, limit=5)
    if not recent:
        st.info("还没有复盘记录。不是为了证明自己没错，而是把每次失误都留成证据。")
        return
    for row in recent:
        st.markdown(_mistake_card_html(row), unsafe_allow_html=True)
        with st.expander(f"查看详情 · {_scene_or_symbol(row)}", expanded=False):
            st.markdown(_mistake_detail_html(row), unsafe_allow_html=True)


def _render_next_defenses(rows: list[dict[str, Any]]) -> None:
    render_section_title("下次防线", "从错题里的下次防线提取，先做复盘提醒，不接入交易拦截。")
    rules = _next_defense_rules(rows, limit=5)
    if not rules:
        st.info("记录交易复盘后，系统会从你的反思里沉淀下次防线。")
        return
    st.markdown(_next_defense_cards_html(rules), unsafe_allow_html=True)


def _recent_mistake_rows(rows: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("review_date") or ""),
            str(row.get("created_at") or ""),
            int(row.get("id") or 0),
        ),
        reverse=True,
    )[:limit]


def _next_defense_rules(rows: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    return build_rule_library_from_mistakes(_recent_mistake_rows(rows, limit=max(limit * 3, limit)))[:limit]


def _mistake_overview_strip_html(items: list[tuple[str, str]]) -> str:
    body = "".join(
        f'<div class="mistake-strip-item"><span>{escape(label)}</span><strong>{escape(value)}</strong></div>'
        for label, value in items
    )
    return f'<section class="mistake-overview-strip">{body}</section>'


def _mistake_card_html(row: dict[str, Any]) -> str:
    tags = " / ".join(row.get("mistake_tags") or []) or "未填写"
    return f"""
    <section class="mistake-card">
      <div class="mistake-card-head">{escape(str(row.get('review_date') or ''))} · {escape(_scene_or_symbol(row))}</div>
      <div class="mistake-card-meta">错因：{escape(tags)}</div>
      <div class="mistake-card-meta">损失：{escape(_loss_amount_text(row))}</div>
      <p><strong>一句话反思：</strong>{escape(str(row.get('reflection') or '未记录'))}</p>
      <p><strong>下次防线：</strong>{escape(_mistake_next_defense(row))}</p>
    </section>
    """


def _next_defense_cards_html(rules: list[dict[str, Any]]) -> str:
    body = "".join(
        f"""
        <article class="next-defense-card">
          <div class="next-defense-label">规则</div>
          <strong>{escape(str(rule.get('rule_text') or rule.get('action') or '未记录'))}</strong>
          <p>来源：{escape(str(rule.get('source') or '历史错题'))}</p>
          <p>最近触发：{escape(str(rule.get('last_trigger_date') or '未记录'))}</p>
          <span>{escape(str(rule.get('status') or '待验证'))}</span>
        </article>
        """
        for rule in rules
    )
    return f'<section class="next-defense-grid">{body}</section>'


def _build_periodic_return_context(store: DisciplineReviewStore, rows: list[dict[str, Any]]) -> dict[str, Any]:
    editing_id = st.session_state.get("periodic-return-edit-id")
    editing_row = store.get_periodic_return_review(int(editing_id)) if editing_id else None
    state = _prepare_periodic_return_form_state(store, rows, editing_row)
    meta = dict(state["meta"])
    start_date = _date_value(st.session_state.get(state["start_date_key"]), date.today())
    end_date = _date_value(st.session_state.get(state["end_date_key"]), start_date)
    starting_equity = _parse_amount_text(st.session_state.get(state["starting_equity_key"]))
    ending_equity = _parse_amount_text(st.session_state.get(state["ending_equity_key"]))
    deposit_amount = _parse_amount_text(st.session_state.get(state["deposit_key"]), default=0.0)
    withdrawal_amount = _parse_amount_text(st.session_state.get(state["withdrawal_key"]), default=0.0)
    profit = None if starting_equity is None or ending_equity is None else ending_equity - starting_equity - deposit_amount + withdrawal_amount
    return_rate = None if profit is None or starting_equity is None or starting_equity <= 0 else profit / starting_equity
    return {
        "editing_row": editing_row,
        "state": state,
        "meta": meta,
        "period_type": st.session_state.get(state["period_type_key"]) or PERIODIC_RETURN_TYPES[0],
        "start_date": start_date,
        "end_date": end_date,
        "starting_equity": starting_equity,
        "ending_equity": ending_equity,
        "deposit_amount": deposit_amount,
        "withdrawal_amount": withdrawal_amount,
        "profit": round(profit, 2) if profit is not None else None,
        "return_rate": return_rate,
    }


def _render_trade_review_overview(mistake_rows: list[dict[str, Any]], context: dict[str, Any]) -> None:
    start_date = context["start_date"]
    end_date = context["end_date"]
    mistake_summary = build_period_mistake_review_summary(mistake_rows, start_date=start_date, end_date=end_date)
    starting_equity = context.get("starting_equity")
    ending_equity = context.get("ending_equity")
    profit = context.get("profit")
    return_rate = context.get("return_rate")
    cards = [
        ("期初净资产", _money(starting_equity) if starting_equity is not None else "缺期初快照", _field_source_caption(context["meta"], "starting")),
        ("期末净资产", _money(ending_equity) if ending_equity is not None else "缺期末净资产", _field_source_caption(context["meta"], "ending")),
        ("本期收益", _profit_text(profit) if profit is not None else "待结算", _calculation_blocker_text(starting_equity, ending_equity)),
        ("本期收益率", _return_rate_text(return_rate) if return_rate is not None else "待结算", "按期初净资产计算"),
        (
            "本期错误损失",
            _loss_amount_summary_text(mistake_summary.get("loss_amount")),
            f"{int(mistake_summary.get('mistake_count') or 0)} 次错误",
        ),
        ("未闭环规则", str(int(mistake_summary.get("unclosed_rule_count") or 0)), "待形成防线"),
    ]
    render_section_title("本期交易复盘总览", f"{context['period_type']}｜{start_date.isoformat()} 至 {end_date.isoformat()}")
    st.markdown(_card_grid_html(cards), unsafe_allow_html=True)


def _render_periodic_review_conclusion(mistake_rows: list[dict[str, Any]], context: dict[str, Any]) -> None:
    mistake_summary = build_period_mistake_review_summary(
        mistake_rows,
        start_date=context["start_date"],
        end_date=context["end_date"],
    )
    conclusion = build_trade_review_conclusion(
        profit_amount=context.get("profit"),
        return_rate=context.get("return_rate"),
        mistake_summary=mistake_summary,
    )
    render_section_title("本期复盘结论", "自动汇总当前周期内的收益、错误和下一条防线。")
    st.markdown(_review_conclusion_html(conclusion, mistake_summary), unsafe_allow_html=True)


def _render_rule_library(rows: list[dict[str, Any]]) -> None:
    render_section_title("规则库 / 下次防线", "从错题本里的下次防线提取，先做复盘提醒，不接入交易拦截。")
    rules = build_rule_library_from_mistakes(rows)
    st.markdown(_rule_library_html(rules), unsafe_allow_html=True)


def _calculation_blocker_text(starting_equity: float | None, ending_equity: float | None) -> str:
    if starting_equity is None:
        return "缺期初快照"
    if ending_equity is None:
        return "缺期末净资产"
    return "已可计算"


def _render_principles_card(store: DisciplineReviewStore) -> None:
    render_section_title("我的投资原则", "个人纪律备忘，不参与评分，也不阻止交易。")
    edit_key = "discipline_principles_editing"
    rules_key = "discipline_principle_edit_rules"
    revision_key = "discipline_principle_edit_revision"
    if not st.session_state.get(edit_key):
        rules = store.get_principle_rules()
        st.markdown(_principle_cards_html(rules), unsafe_allow_html=True)
        if st.button("编辑原则", key="discipline-principles-edit"):
            st.session_state[rules_key] = [dict(rule) for rule in rules]
            st.session_state[revision_key] = int(st.session_state.get(revision_key, 0)) + 1
            st.session_state[edit_key] = True
            st.rerun()
        return

    rules = [dict(rule) for rule in st.session_state.get(rules_key, store.get_principle_rules())]
    revision = int(st.session_state.get(revision_key, 0))
    st.caption("编辑模式：每条原则独立维护，保存后会回到卡片展示。")
    updated_rules: list[dict[str, str]] = []
    for index, rule in enumerate(rules):
        cols = st.columns([0.7, 2.5, 5.2, 0.8, 0.8, 0.8])
        cols[0].markdown(f'<div class="principle-edit-number">{index + 1:02d}</div>', unsafe_allow_html=True)
        title = cols[1].text_input(
            "标题",
            value=str(rule.get("title") or ""),
            key=f"discipline-principle-title-{revision}-{index}",
            label_visibility="collapsed",
            placeholder="原则标题",
        )
        content = cols[2].text_area(
            "内容",
            value=str(rule.get("content") or ""),
            key=f"discipline-principle-content-{revision}-{index}",
            label_visibility="collapsed",
            placeholder="原则内容",
            height=68,
        )
        updated_rules.append({"title": title, "content": content})
        if cols[3].button("上移", key=f"discipline-principle-up-{revision}-{index}", disabled=index == 0):
            updated_rules[index - 1], updated_rules[index] = updated_rules[index], updated_rules[index - 1]
            st.session_state[rules_key] = updated_rules + rules[index + 1 :]
            st.session_state[revision_key] = revision + 1
            st.rerun()
        if cols[4].button("下移", key=f"discipline-principle-down-{revision}-{index}", disabled=index >= len(rules) - 1):
            remainder = [dict(item) for item in rules[index + 1 :]]
            if remainder:
                next_rule = remainder.pop(0)
                st.session_state[rules_key] = updated_rules[:-1] + [next_rule, updated_rules[-1]] + remainder
                st.session_state[revision_key] = revision + 1
                st.rerun()
        if cols[5].button("删除", key=f"discipline-principle-delete-{revision}-{index}"):
            st.session_state[rules_key] = updated_rules[:-1] + rules[index + 1 :]
            st.session_state[revision_key] = revision + 1
            st.rerun()

    st.session_state[rules_key] = updated_rules
    cols = st.columns([1, 1, 1, 1, 4])
    if cols[0].button("新增原则", key="discipline-principles-add", width="stretch"):
        st.session_state[rules_key] = updated_rules + [{"title": "新原则", "content": ""}]
        st.session_state[revision_key] = revision + 1
        st.rerun()
    if cols[1].button("保存原则", key="discipline-principles-save", type="primary", width="stretch"):
        store.save_principle_rules(updated_rules)
        st.session_state[edit_key] = False
        st.session_state.pop(rules_key, None)
        st.success("投资原则已保存。")
        st.rerun()
    if cols[2].button("重置默认", key="discipline-principles-reset", width="stretch"):
        store.reset_principles()
        st.session_state[edit_key] = False
        st.session_state.pop(rules_key, None)
        st.success("已恢复默认投资原则。")
        st.rerun()
    if cols[3].button("取消编辑", key="discipline-principles-cancel", width="stretch"):
        st.session_state[edit_key] = False
        st.session_state.pop(rules_key, None)
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


def _render_mistake_reviews(store: DisciplineReviewStore, rows: list[dict[str, Any]]) -> None:
    render_section_title(
        "本期错误归因 / 交易复盘",
        "记录每一次不该发生的交易错误。重点不是责备自己，而是把错误沉淀成下一次的防线。",
    )
    summary = build_mistake_review_summary(rows)
    st.markdown(_mistake_summary_html(summary), unsafe_allow_html=True)
    repeated = summary.get("repeated_mistake_types") or []
    if repeated:
        st.warning(f"最近重复出现：{'、'.join(repeated)}，建议把它写成明确规则。")

    with st.form("mistake-review-form", clear_on_submit=True):
        st.markdown("#### 快速记录一次错误")
        cols = st.columns([1, 2.2, 1.4])
        review_date = cols[0].date_input("日期", value=date.today())
        scene_or_symbol = cols[1].text_input("标的 / 场景", placeholder="例如：SPACX 空单、NOK 清仓、NVDA 追高")
        loss_amount_usd = cols[2].number_input("损失金额", min_value=0.0, value=0.0, step=10.0, format="%.2f")
        cols[2].caption("单位：USD")
        mistake_tags = _render_mistake_tag_inputs()
        quick_reflection = st.text_area(
            "一句话反思",
            height=68,
            placeholder="用一句话写清楚真正的问题，例如：不能空强势标的。",
        )
        trigger_event = ""
        impact_summary = ""
        detailed_reflection = ""
        next_defense = ""
        archive_as_rule = False
        with st.expander("展开详细复盘", expanded=False):
            trigger_event = st.text_area("事件经过", height=82, placeholder="这次交易错误是怎么发生的？我当时做了什么？")
            impact_summary = st.text_area("结果 / 影响", height=82, placeholder="这次错误造成了什么结果？亏损、卖飞、错过机会，还是破坏了纪律？")
            detailed_reflection = st.text_area("核心反思", height=82, placeholder="真正的问题是什么？是判断错了，还是流程、纪律、仓位、情绪出了问题？")
            next_defense = st.text_area("下次防线", height=82, placeholder="下次遇到类似情况，必须执行哪条规则？")
            archive_as_rule = st.checkbox("是否归档为规则", value=False)
        if st.form_submit_button("记录这次错误", width="stretch"):
            store.save_mistake_review(
                {
                    "review_date": review_date,
                    "scene_or_symbol": scene_or_symbol,
                    "loss_amount_usd": loss_amount_usd,
                    "impact_summary": impact_summary,
                    "trigger_event": trigger_event,
                    "mistake_tags": mistake_tags,
                    "reflection": detailed_reflection or quick_reflection,
                    "next_defense": next_defense,
                    "review_status": "已形成规则" if archive_as_rule else "已记录",
                }
            )
            st.success("错误已记录。")
            st.rerun()

    st.markdown("#### 交易复盘列表")
    filtered = _filter_mistake_reviews(rows)
    st.markdown(_mistake_table_html(filtered), unsafe_allow_html=True)
    if filtered:
        with st.expander("查看复盘详情", expanded=False):
            options = [int(row["id"]) for row in filtered]
            selected_id = st.selectbox("选择错误记录", options, format_func=lambda value: _mistake_option_label(filtered, value))
            detail = next((row for row in filtered if int(row.get("id") or 0) == int(selected_id)), None)
            if detail:
                st.markdown(_mistake_detail_html(detail), unsafe_allow_html=True)


def _render_periodic_return_reviews(
    store: DisciplineReviewStore,
    rows: list[dict[str, Any]],
    mistake_rows: list[dict[str, Any]],
    context: dict[str, Any],
) -> None:
    render_section_title(
        "周期收益结算",
        "每周末或每月末记录一次账户表现，把收益、错误和下期规则沉淀下来。",
    )
    summary = build_periodic_return_review_summary(rows)
    st.markdown(_periodic_summary_html(summary), unsafe_allow_html=True)

    editing_row = context["editing_row"]
    state = context["state"]
    meta = dict(context["meta"])

    st.markdown("#### 1. 周期与数据源")
    control_cols = st.columns([1.1, 1, 1])
    control_cols[0].selectbox("复盘类型", PERIODIC_RETURN_TYPES, key=state["period_type_key"])
    control_cols[1].date_input("开始日期", key=state["start_date_key"])
    control_cols[2].date_input("结束日期", key=state["end_date_key"])
    action_cols = st.columns([1.2, 1.2, 1.2, 1.2, 2.2])
    if action_cols[0].button("读取账户净资产", type="primary", key=f"{state['prefix']}-reload", width="stretch"):
        _apply_periodic_return_prefill(store, state, rows, editing_row)
        st.rerun()
    if action_cols[1].button("用上期末值作为期初", key=f"{state['prefix']}-use-previous", width="stretch"):
        previous_ending = _previous_periodic_ending_equity(rows, editing_row, st.session_state[state["start_date_key"]])
        if previous_ending is not None:
            st.session_state[state["starting_equity_key"]] = _amount_input_value(previous_ending, allow_blank=True)
            meta["starting_auto_value"] = previous_ending
            meta["starting_source_label"] = EQUITY_SOURCE_PREVIOUS_REVIEW
            meta["starting_snapshot_date"] = ""
            st.session_state[state["meta_key"]] = meta
            st.session_state[state["feedback_key"]] = {
                "level": "success",
                "text": f"已使用上一条复盘期末净资产作为期初账户净资产：${_money(previous_ending)}。",
            }
            st.rerun()
        st.session_state[state["feedback_key"]] = {
            "level": "warning",
            "text": "没有可复用的上一条复盘期末净资产，请手动填写期初账户净资产。",
        }
        st.rerun()
    if action_cols[2].button("保存当前快照", key=f"{state['prefix']}-save-snapshot", width="stretch"):
        snapshot = store.capture_current_account_equity_snapshot()
        if snapshot:
            st.session_state[state["feedback_key"]] = {
                "level": "success",
                "text": (
                    f"已保存当前快照：${_money(snapshot.get('account_equity'))}，"
                    f"数据来源：{snapshot.get('source') or EQUITY_SOURCE_PORTFOLIO}。"
                ),
            }
        else:
            st.session_state[state["feedback_key"]] = {
                "level": "error",
                "text": "未找到组合持仓净资产，也未找到账户快照。请先在组合持仓页确认账户净资产是否已保存。",
            }
        st.rerun()
    if action_cols[3].button("一键生成本期复盘", key=f"{state['prefix']}-auto-review", width="stretch"):
        _apply_periodic_auto_review(store, state, rows, editing_row, mistake_rows)
        st.rerun()

    if not _render_periodic_feedback(state):
        st.markdown(_periodic_status_html(_periodic_source_note(meta), "muted"), unsafe_allow_html=True)
    st.caption("收益公式：本期收益 = 期末净资产 - 期初净资产 - 本期入金 + 本期出金")

    st.markdown("#### 2. 收益结算")
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
    st.markdown(
        _periodic_settlement_bar_html(starting_equity, ending_equity, deposit_amount, withdrawal_amount, preview_profit, preview_return, meta),
        unsafe_allow_html=True,
    )

    with st.form(f"periodic-return-review-form-{state['prefix']}", clear_on_submit=False):
        st.markdown("#### 3. 交易复盘")
        review_cols = st.columns(2)
        biggest_contributor = review_cols[0].text_area(
            "本期最大贡献",
            key=state["biggest_contributor_key"],
            height=72,
            placeholder="哪只股票、哪次决策或哪条主线贡献最大？",
        )
        biggest_drag = review_cols[1].text_area(
            "本期最大拖累",
            key=state["biggest_drag_key"],
            height=72,
            placeholder="哪只股票、哪次错误或哪类仓位拖累最大？",
        )
        what_went_well = review_cols[0].text_area(
            "本期做对了什么",
            key=state["what_went_well_key"],
            height=86,
            placeholder="本期哪些交易行为值得保留？",
        )
        what_went_wrong = review_cols[1].text_area(
            "本期做错了什么",
            key=state["what_went_wrong_key"],
            height=86,
            placeholder="本期哪些交易行为需要纠正？",
        )
        next_period_rule = st.text_area(
            "下期重点规则",
            key=state["next_period_rule_key"],
            height=112,
            placeholder="下周 / 下月必须执行的 1-3 条规则。",
        )
        notes = st.text_area("备注，可选", key=state["notes_key"], height=64, placeholder="其他补充，可选。")
        submit_label = "保存本期复盘"
        form_cols = st.columns([1.2, 1, 4.8])
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
                "starting_equity_source": _saved_equity_source_label(meta.get("starting_source_label"), starting_equity, starting_manual),
                "ending_equity_source": _saved_equity_source_label(meta.get("ending_source_label"), ending_equity, ending_manual),
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

    st.markdown("#### 4. 历史记录")
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
    loss_only = cols[3].checkbox("只看有损失金额的记录", value=False, key="mistake-loss-filter")
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
        if loss_only and _loss_amount_value(row) <= 0:
            continue
        result.append(row)
    return result


def _render_mistake_tag_inputs() -> list[str]:
    selected: list[str] = []
    cols = st.columns(2)
    for index, (group_name, options) in enumerate(MISTAKE_TAG_GROUPS.items()):
        with cols[index % 2]:
            values = st.multiselect(
                group_name,
                options,
                placeholder="选择错误类型",
                key=f"mistake-tags-{group_name}",
            )
            selected.extend(values)
    legacy_options = [option for option in MISTAKE_TAG_OPTIONS if option not in {item for group in MISTAKE_TAG_GROUPS.values() for item in group}]
    with st.expander("旧标签兼容", expanded=False):
        selected.extend(
            st.multiselect(
                "旧错误类型",
                legacy_options,
                placeholder="选择旧记录标签",
                key="mistake-tags-legacy",
            )
        )
    return _dedupe_text(selected)


def _dedupe_text(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _render_discipline_stats(store: DisciplineReviewStore, entries: list[dict]) -> None:
    render_section_title("纪律统计", "统计来自交易日志和交易意图记录；不要求手动给交易打标签。")
    periodic_summary = build_periodic_return_review_summary(store.list_periodic_return_reviews())
    periodic_cards = [
        ("周复盘记录数", str(periodic_summary.get("weekly_count") or 0), "手动记录"),
        ("月复盘记录数", str(periodic_summary.get("monthly_count") or 0), "手动记录"),
        ("最近4周累计收益", _profit_text(periodic_summary.get("recent_4_week_profit")), "周复盘口径"),
        ("最近3个月累计收益", _profit_text(periodic_summary.get("recent_3_month_profit")), "月复盘口径"),
        ("最大单周亏损", _profit_text(periodic_summary.get("max_weekly_loss")), "没有亏损记录则不显示亏损"),
        ("最大单月亏损", _profit_text(periodic_summary.get("max_monthly_loss")), "没有亏损记录则不显示亏损"),
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


def _principle_cards_html(rules: list[dict[str, Any]]) -> str:
    normalized = [
        {
            "title": str(rule.get("title") or "").strip() or "未命名原则",
            "content": str(rule.get("content") or "").strip(),
        }
        for rule in rules
        if str(rule.get("title") or rule.get("content") or "").strip()
    ]
    if not normalized:
        normalized = [dict(rule) for rule in DEFAULT_PRINCIPLE_RULES]
    cards = "".join(
        '<article class="principle-rule-card">'
        f'<span class="principle-rule-number">{index:02d}</span>'
        f"<strong>{escape(rule['title'])}</strong>"
        f"<p>{escape(rule['content'])}</p>"
        "</article>"
        for index, rule in enumerate(normalized, start=1)
    )
    return f'<section class="principle-rule-grid">{cards}</section>'


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


def _review_conclusion_html(conclusion: dict[str, str], mistake_summary: dict[str, Any]) -> str:
    items = [
        ("本期错误次数", f"{int(mistake_summary.get('mistake_count') or 0)} 次"),
        ("错误损失金额", _loss_amount_summary_text(mistake_summary.get("loss_amount"))),
        ("最大错误类型", str(mistake_summary.get("most_common_mistake_type") or "本期无错误记录")),
        ("未闭环规则", f"{int(mistake_summary.get('unclosed_rule_count') or 0)} 条"),
    ]
    metrics = "".join(
        f"<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>"
        for label, value in items
    )
    return f"""
    <section class="review-conclusion-card">
      <p>{escape(str(conclusion.get("summary") or ""))}</p>
      <div>{metrics}</div>
    </section>
    """


def _rule_library_html(rules: list[dict[str, Any]]) -> str:
    if not rules:
        return '<div class="discipline-empty">当前还没有可提取的下次防线。记录错误时写下“下次防线”后，会自动汇总到这里。</div>'
    body = "".join(
        "<tr>"
        f"<td>{escape(_one_line(rule.get('rule_text'), 42))}</td>"
        f"<td>{escape(str(rule.get('trigger') or '交易错误复盘'))}</td>"
        f"<td>{escape(_one_line(rule.get('action'), 42))}</td>"
        f"<td>{escape(str(rule.get('source') or '历史错误记录'))}</td>"
        f"<td>{escape(str(rule.get('status') or '待验证'))}</td>"
        f"<td>{escape(str(rule.get('last_trigger_date') or '未记录'))}</td>"
        "</tr>"
        for rule in rules[:80]
    )
    return (
        '<div class="discipline-table-wrap"><table class="discipline-table">'
        "<thead><tr><th>规则</th><th>触发条件</th><th>执行动作</th><th>来源错误</th><th>状态</th><th>最近触发</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _mistake_summary_html(summary: dict[str, Any]) -> str:
    cards = [
        ("错误记录总数", str(summary.get("total_count") or 0), "全部复盘"),
        ("最近30天错误数", str(summary.get("recent_30_count") or 0), "按复盘日期"),
        ("最近30天损失金额", str(summary.get("recent_30_loss_amount_text") or "最近30天无损失金额"), "USD 统计"),
        ("最常见错误类型", str(summary.get("most_common_mistake_type") or "本期无错误记录"), f"{int(summary.get('most_common_mistake_count') or 0)} 次"),
        ("未形成规则", str(summary.get("unruled_count") or 0), "建议继续沉淀"),
    ]
    return _card_grid_html(cards)


def _mistake_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="discipline-empty">当前筛选下没有错误复盘记录。</div>'
    body = "".join(
        "<tr>"
        f"<td>{escape(str(row.get('review_date') or ''))}</td>"
        f"<td>{escape(_scene_or_symbol(row))}</td>"
        f"<td>{escape(_loss_amount_text(row))}</td>"
        f"<td>{escape('、'.join(row.get('mistake_tags') or []))}</td>"
        f"<td>{escape(_one_line(row.get('reflection')))}</td>"
        f"<td>{escape(str(row.get('review_status') or ''))}</td>"
        "<td>查看详情</td>"
        "</tr>"
        for row in rows[:80]
    )
    return (
        '<div class="discipline-table-wrap"><table class="discipline-table">'
        "<thead><tr><th>日期</th><th>标的 / 场景</th><th>损失金额</th><th>错误类型</th><th>核心反思摘要</th><th>状态</th><th>操作</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _mistake_detail_html(row: dict[str, Any]) -> str:
    tags = "、".join(row.get("mistake_tags") or []) or "未记录"
    return f"""
    <section class="mistake-detail-card">
      <h4>{escape(str(row.get('review_date') or ''))} · {escape(_scene_or_symbol(row))}</h4>
      <dl>
        <dt>损失金额</dt><dd>{escape(_loss_amount_text(row))}</dd>
        <dt>事件经过</dt><dd>{_detail_text_html(_mistake_event_summary(row))}</dd>
        <dt>结果 / 影响</dt><dd>{_detail_text_html(_mistake_impact_summary(row))}</dd>
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
    ]
    clean = [part for part in parts if part]
    return "\n".join(clean) if clean else "未记录"


def _mistake_impact_summary(row: dict[str, Any]) -> str:
    parts = [
        _clean_detail_part(row.get("impact_summary")),
        _clean_detail_part(row.get("loss_impact_text")),
        _clean_detail_part(row.get("result_text")),
    ]
    clean = []
    seen = set()
    for part in parts:
        if not part or part in seen:
            continue
        seen.add(part)
        clean.append(part)
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
        ("最近4周最大亏损", _profit_text(summary.get("recent_4_week_max_loss")), "没有亏损记录则不显示亏损"),
    ]
    return _card_grid_html(cards)


def _filter_periodic_return_reviews(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    options = ["全部", "周复盘", "月复盘", "自定义"]
    selected = st.selectbox("历史记录筛选", options, key="periodic-return-filter")
    if selected == "全部":
        return rows
    if selected == "周复盘":
        return [row for row in rows if row.get("period_type") == "周复盘"]
    if selected == "月复盘":
        return [row for row in rows if row.get("period_type") == "月复盘"]
    if selected == "自定义":
        return [row for row in rows if row.get("period_type") == "自定义"]
    return rows


def _periodic_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="discipline-empty">尚未保存周期收益复盘。保存本期复盘后会显示在这里。</div>'
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
        "feedback_key": f"{prefix}-feedback",
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
            previous_ending = _previous_periodic_ending_equity(rows, editing_row, start_date)
            prefill = store.build_periodic_return_prefill(
                start_date=start_date,
                end_date=end_date,
                previous_ending_equity=previous_ending,
            )
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
        "starting_source_label": str(prefill.get("starting_equity_source") or EQUITY_SOURCE_NOT_FOUND),
        "ending_source_label": str(prefill.get("ending_equity_source") or EQUITY_SOURCE_NOT_FOUND),
        "starting_snapshot_date": str(prefill.get("starting_equity_snapshot_date") or ""),
        "ending_snapshot_date": str(prefill.get("ending_equity_snapshot_date") or ""),
        "only_latest_available": bool(prefill.get("only_latest_available")),
    }


def _apply_periodic_return_prefill(
    store: DisciplineReviewStore,
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    editing_row: dict[str, Any] | None,
) -> None:
    start_date = _date_value(st.session_state.get(state["start_date_key"]), date.today())
    end_date = _date_value(st.session_state.get(state["end_date_key"]), start_date)
    previous_ending = _previous_periodic_ending_equity(rows, editing_row, start_date)
    prefill = store.build_periodic_return_prefill(
        start_date=start_date,
        end_date=end_date,
        previous_ending_equity=previous_ending,
        use_current_nav_fallback=True,
    )
    meta = _periodic_meta_from_prefill(prefill)
    st.session_state[state["starting_equity_key"]] = _amount_input_value(prefill.get("starting_equity"), allow_blank=True)
    st.session_state[state["ending_equity_key"]] = _amount_input_value(prefill.get("ending_equity"), allow_blank=True)
    st.session_state[state["meta_key"]] = meta
    st.session_state[state["signature_key"]] = (
        f"{int(editing_row['id']) if editing_row else 'new'}|"
        f"{st.session_state.get(state['period_type_key']) or PERIODIC_RETURN_TYPES[0]}|"
        f"{start_date.isoformat()}|{end_date.isoformat()}"
    )
    st.session_state[state["reload_key"]] = False
    st.session_state[state["feedback_key"]] = _periodic_reload_feedback(prefill)


def _apply_periodic_auto_review(
    store: DisciplineReviewStore,
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    editing_row: dict[str, Any] | None,
    mistake_rows: list[dict[str, Any]],
) -> None:
    _apply_periodic_return_prefill(store, state, rows, editing_row)
    start_date = _date_value(st.session_state.get(state["start_date_key"]), date.today())
    end_date = _date_value(st.session_state.get(state["end_date_key"]), start_date)
    starting_equity = _parse_amount_text(st.session_state.get(state["starting_equity_key"]))
    ending_equity = _parse_amount_text(st.session_state.get(state["ending_equity_key"]))
    deposit_amount = _parse_amount_text(st.session_state.get(state["deposit_key"]), default=0.0)
    withdrawal_amount = _parse_amount_text(st.session_state.get(state["withdrawal_key"]), default=0.0)
    profit = None if starting_equity is None or ending_equity is None else ending_equity - starting_equity - deposit_amount + withdrawal_amount
    return_rate = None if profit is None or starting_equity is None or starting_equity <= 0 else profit / starting_equity
    mistake_summary = build_period_mistake_review_summary(mistake_rows, start_date=start_date, end_date=end_date)
    conclusion = build_trade_review_conclusion(
        profit_amount=round(profit, 2) if profit is not None else None,
        return_rate=return_rate,
        mistake_summary=mistake_summary,
    )
    if not str(st.session_state.get(state["what_went_wrong_key"]) or "").strip():
        st.session_state[state["what_went_wrong_key"]] = conclusion["mistake_summary"]
    if not str(st.session_state.get(state["next_period_rule_key"]) or "").strip():
        st.session_state[state["next_period_rule_key"]] = conclusion["next_defense"]
    if not str(st.session_state.get(state["notes_key"]) or "").strip():
        st.session_state[state["notes_key"]] = conclusion["summary"]
    if starting_equity is None or ending_equity is None:
        st.session_state[state["feedback_key"]] = {
            "level": "warning",
            "text": f"已生成本期复盘草稿，但{_calculation_blocker_text(starting_equity, ending_equity)}，收益暂时待结算。",
        }
        return
    st.session_state[state["feedback_key"]] = {
        "level": "success",
        "text": "已读取账户净资产、汇总本期错误，并生成本期复盘草稿。",
    }


def _periodic_reload_feedback(prefill: dict[str, Any]) -> dict[str, str]:
    start_value = _nullable_float(prefill.get("starting_equity"))
    end_value = _nullable_float(prefill.get("ending_equity"))
    start_source = _source_display_label(prefill.get("starting_equity_source"))
    end_source = _source_display_label(prefill.get("ending_equity_source"))
    if start_value is not None and end_value is not None:
        return {
            "level": "success",
            "text": (
                f"已读取账户净资产：期初 ${_money(start_value)}，期末 ${_money(end_value)}，"
                f"数据来源：期初 {start_source} / 期末 {end_source}。"
            ),
        }
    if end_value is not None:
        return {
            "level": "warning",
            "text": (
                f"期末已读取{end_source}：${_money(end_value)}；"
                "期初快照缺失，请手动填写或使用上期末值。"
            ),
        }
    if start_value is not None:
        return {
            "level": "warning",
            "text": (
                f"已读取期初账户净资产 ${_money(start_value)}，但未找到期末账户净资产。"
                "请保存当前快照，或手动填写期末账户净资产。"
            ),
        }
    return {
        "level": "error",
        "text": "未找到可用账户净资产数据。请先在组合持仓页保存账户快照，或手动填写。",
    }


def _render_periodic_feedback(state: dict[str, Any]) -> bool:
    feedback = st.session_state.get(state.get("feedback_key"))
    if not isinstance(feedback, dict):
        return False
    text = str(feedback.get("text") or "").strip()
    if not text:
        return False
    level = str(feedback.get("level") or "info")
    st.markdown(_periodic_status_html(text, level), unsafe_allow_html=True)
    return True


def _periodic_source_note(meta: dict[str, Any]) -> str:
    start_equity = _money(meta.get("starting_auto_value"))
    end_equity = _money(meta.get("ending_auto_value"))
    start_source = _source_detail_text(meta, "starting")
    end_source = _source_detail_text(meta, "ending")
    if meta.get("starting_auto_value") is not None and meta.get("ending_auto_value") is not None:
        return (
            f"期初已读取{start_source}：${start_equity}；"
            f"期末已读取{end_source}：${end_equity}。"
        )
    if meta.get("starting_auto_value") is not None or meta.get("ending_auto_value") is not None:
        parts = []
        if meta.get("starting_auto_value") is not None:
            parts.append(f"期初已读取{start_source}：${start_equity}")
        else:
            parts.append("期初快照缺失，请手动填写或使用上期末值")
        if meta.get("ending_auto_value") is not None:
            parts.append(f"期末已读取{end_source}：${end_equity}")
        else:
            parts.append("期末快照缺失，请保存当前快照或手动填写")
        return "；".join(parts)
    return "未找到账户净资产。请保存当前快照，或手动填写期初和期末。"


def _field_source_caption(meta: dict[str, Any], side: str) -> str:
    label = str(meta.get(f"{side}_source_label") or EQUITY_SOURCE_NOT_FOUND)
    prefix = "期初" if side == "starting" else "期末"
    if label == EQUITY_SOURCE_ACCOUNT_SNAPSHOT:
        return f"{prefix}：账户快照"
    if label == EQUITY_SOURCE_PORTFOLIO:
        return f"{prefix}：当前持仓汇总"
    if label == EQUITY_SOURCE_PREVIOUS_REVIEW:
        return f"{prefix}：上期复盘"
    if label == EQUITY_FILL_MANUAL:
        return f"{prefix}：手动填写"
    return f"{prefix}：未找到"


def _source_detail_text(meta: dict[str, Any], side: str) -> str:
    label = _source_display_label(meta.get(f"{side}_source_label"))
    snapshot_date = _display_snapshot_date(meta.get(f"{side}_snapshot_date"))
    if label == EQUITY_SOURCE_ACCOUNT_SNAPSHOT:
        return f"{label} {snapshot_date}"
    return label


def _periodic_source_card_text(meta: dict[str, Any]) -> str:
    labels = {
        str(meta.get("starting_source_label") or EQUITY_SOURCE_NOT_FOUND),
        str(meta.get("ending_source_label") or EQUITY_SOURCE_NOT_FOUND),
    }
    labels.discard("")
    if labels == {EQUITY_SOURCE_ACCOUNT_SNAPSHOT}:
        return EQUITY_SOURCE_ACCOUNT_SNAPSHOT
    if EQUITY_FILL_MANUAL in labels:
        return EQUITY_FILL_MANUAL
    display_labels = [_source_display_label(label) for label in labels]
    return " / ".join(sorted(display_labels)) if display_labels else EQUITY_SOURCE_NOT_FOUND


def _source_display_label(value: object) -> str:
    text = str(value or "").strip()
    if text == EQUITY_FILL_AUTO:
        return EQUITY_SOURCE_ACCOUNT_SNAPSHOT
    if text in {EQUITY_SOURCE_ACCOUNT_SNAPSHOT, EQUITY_SOURCE_PORTFOLIO, EQUITY_SOURCE_PREVIOUS_REVIEW, EQUITY_FILL_MANUAL}:
        return text
    return EQUITY_SOURCE_NOT_FOUND


def _periodic_status_html(text: str, level: str = "muted") -> str:
    tone = "error" if level == "error" else "warning" if level == "warning" else "success" if level == "success" else "muted"
    return f'<div class="periodic-status {tone}">{escape(text)}</div>'


def _settlement_money_text(value: float | None, missing: str) -> str:
    if value is None:
        return missing
    return f"${value:,.2f}"


def _settlement_profit_text(value: float | None) -> str:
    if value is None:
        return "待计算"
    sign = "+" if value > 0 else ""
    return f"{sign}${value:,.2f}"


def _settlement_return_text(value: float | None) -> str:
    if value is None:
        return "待计算"
    sign = "+" if value > 0 else ""
    return f"{sign}{value * 100:.2f}%"


def _periodic_settlement_bar_html(
    starting_equity: float | None,
    ending_equity: float | None,
    deposit_amount: float,
    withdrawal_amount: float,
    profit: float | None,
    return_rate: float | None,
    meta: dict[str, Any],
) -> str:
    items = [
        ("期初", _settlement_money_text(starting_equity, "未找到")),
        ("期末", _settlement_money_text(ending_equity, "未找到")),
        ("入金", _settlement_money_text(deposit_amount, "$0")),
        ("出金", _settlement_money_text(withdrawal_amount, "$0")),
        ("收益", _settlement_profit_text(profit)),
        ("收益率", _settlement_return_text(return_rate)),
    ]
    body = " ｜ ".join(f"<span>{escape(label)}：<strong>{escape(value)}</strong></span>" for label, value in items)
    source = escape(_periodic_source_card_text(meta))
    return f'<div class="periodic-settlement-bar">{body}<em>来源：{source}</em></div>'


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
    if label in {EQUITY_FILL_AUTO, EQUITY_SOURCE_ACCOUNT_SNAPSHOT, EQUITY_SOURCE_PORTFOLIO, EQUITY_SOURCE_PREVIOUS_REVIEW}:
        return baseline_value is None or abs(value - baseline_value) > 0.005
    return True


def _saved_equity_source_label(source_label: object, value: float | None, is_manual_override: bool) -> str:
    if value is None:
        return EQUITY_SOURCE_NOT_FOUND
    if is_manual_override:
        return EQUITY_FILL_MANUAL
    return _source_display_label(source_label)


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
        "feedback_key",
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
        return "未结算"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "未结算"
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
        return "未结算"
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


def _loss_amount_value(row: dict[str, Any]) -> float:
    try:
        return float(row.get("loss_amount_usd") if row.get("loss_amount_usd") is not None else row.get("loss_amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def _loss_amount_text(row: dict[str, Any]) -> str:
    value = _loss_amount_value(row)
    return f"${value:,.2f}" if value > 0 else "未填写"


def _loss_amount_summary_text(value: object) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"${number:,.2f}" if number > 0 else "本期无错误记录"


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
        .principle-rule-card,
        .dashboard-discipline-card,
        .trade-entry-discipline-hint,
        .review-conclusion-card,
        .mistake-detail-card {
            border: 1px solid rgba(148,163,184,.24);
            border-radius: 8px;
            background: #fff;
            box-shadow: 0 14px 28px rgba(15,23,42,.05);
        }
        .mistake-principle-reminder {
            margin:.42rem 0 .6rem; padding:.48rem .6rem; border-radius:8px;
            background:#fff7ed; border:1px solid rgba(251,146,60,.25); color:#9a3412; font-size:.84rem; font-weight:750;
        }
        .discipline-checklist { padding: .8rem 1rem; margin-bottom: .9rem; }
        .discipline-checklist ol { margin: 0; padding: 0; list-style: none; display: grid; gap: .45rem; }
        .discipline-checklist li { display: flex; gap: .55rem; color: #334155; font-size: .9rem; }
        .discipline-checklist li span {
            display: inline-flex; align-items: center; justify-content: center;
            width: 1.35rem; height: 1.35rem; border-radius: 999px; background: #eef2ff; color: #3730a3; font-weight: 800;
        }
        .principle-rule-grid {
            display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: .68rem; margin: .45rem 0 .75rem;
        }
        .principle-rule-card { padding: .82rem .92rem; min-height: 6rem; }
        .principle-rule-number {
            display: inline-flex; align-items: center; justify-content: center;
            min-width: 2.2rem; height: 1.45rem; border-radius: 999px;
            background: #eef2ff; color: #3730a3; font-size: .72rem; font-weight: 900; letter-spacing: .04em;
            margin-bottom: .42rem;
        }
        .principle-rule-card strong { display: block; color: #0f172a; font-size: .98rem; margin-bottom: .24rem; }
        .principle-rule-card p { margin: 0; color: #64748b; font-size: .84rem; line-height: 1.45; }
        .principle-edit-number {
            display: inline-flex; align-items: center; justify-content: center;
            width: 2rem; height: 2rem; border-radius: 999px; background: #f1f5f9;
            color: #334155; font-size: .78rem; font-weight: 900; margin-top: .18rem;
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
        .principle-summary-line {
            margin: .8rem 0 .4rem; padding: .6rem .75rem; border-radius: 8px;
            background: #f8fafc; border: 1px solid rgba(148,163,184,.22); color: #475569; font-size: .86rem;
        }
        .principle-summary-line strong { color: #0f172a; }
        .review-conclusion-card { padding: .85rem 1rem; margin: .5rem 0 .9rem; }
        .review-conclusion-card p { margin: 0 0 .65rem; color: #0f172a; font-weight: 700; line-height: 1.55; }
        .review-conclusion-card > div { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .5rem; }
        .review-conclusion-card > div > div { border: 1px solid rgba(148,163,184,.18); border-radius: 8px; padding: .55rem .62rem; background: #f8fafc; }
        .review-conclusion-card span { display: block; color: #64748b; font-size: .75rem; }
        .review-conclusion-card strong { display: block; color: #0f172a; margin-top: .1rem; font-size: .95rem; }
        .periodic-status {
            margin: .45rem 0 .75rem; padding: .46rem .65rem; border-radius: 6px;
            font-size: .82rem; color: #475569; background: #f8fafc; border: 1px solid rgba(148,163,184,.22);
        }
        .periodic-status.success { background: #f0fdf4; border-color: rgba(34,197,94,.22); color: #166534; }
        .periodic-status.warning { background: #fffbeb; border-color: rgba(245,158,11,.25); color: #92400e; }
        .periodic-status.error { background: #fef2f2; border-color: rgba(239,68,68,.24); color: #991b1b; }
        .periodic-settlement-bar {
            margin: .55rem 0 .9rem; padding: .58rem .72rem; border-radius: 8px;
            background: #f8fafc; border: 1px solid rgba(148,163,184,.22);
            color: #475569; font-size: .84rem; line-height: 1.7;
        }
        .periodic-settlement-bar strong { color: #0f172a; font-weight: 800; }
        .periodic-settlement-bar em { display: block; color: #64748b; font-size: .76rem; font-style: normal; margin-top: .1rem; }
        .mistake-overview-strip {
            display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: .5rem; margin: .35rem 0 .85rem;
        }
        .mistake-strip-item {
            border: 1px solid rgba(148,163,184,.22); border-radius: 8px;
            background: #fff; padding: .58rem .7rem;
        }
        .mistake-strip-item span { display:block; color:#64748b; font-size:.76rem; }
        .mistake-strip-item strong { display:block; color:#0f172a; font-size:1.02rem; margin-top:.08rem; }
        .mistake-card {
            border: 1px solid rgba(148,163,184,.22); border-radius: 8px;
            background: #fff; padding: .78rem .9rem; margin: .58rem 0;
            box-shadow: 0 10px 22px rgba(15,23,42,.04);
        }
        .mistake-card-head { color:#0f172a; font-weight:900; margin-bottom:.28rem; }
        .mistake-card-meta { color:#64748b; font-size:.82rem; margin:.12rem 0; }
        .mistake-card p { color:#334155; font-size:.86rem; line-height:1.5; margin:.38rem 0 0; }
        .next-defense-grid {
            display:grid; grid-template-columns: repeat(3, minmax(0, 1fr));
            gap:.62rem; margin:.5rem 0 .9rem;
        }
        .next-defense-card {
            border: 1px solid rgba(59,130,246,.22); border-radius: 8px;
            background:#f8fbff; padding:.75rem .82rem;
        }
        .next-defense-card .next-defense-label { color:#2563eb; font-size:.72rem; font-weight:900; margin-bottom:.22rem; }
        .next-defense-card strong { display:block; color:#0f172a; line-height:1.45; }
        .next-defense-card p { margin:.35rem 0 0; color:#64748b; font-size:.8rem; }
        .next-defense-card span {
            display:inline-block; margin-top:.45rem; padding:.12rem .42rem; border-radius:999px;
            color:#1d4ed8; background:#dbeafe; font-size:.72rem; font-weight:800;
        }
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
            .principle-rule-grid { grid-template-columns: 1fr; }
            .discipline-card-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .review-conclusion-card > div { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .dashboard-discipline-card { display: block; }
            .mistake-detail-card dl { grid-template-columns: 1fr; }
            .mistake-overview-strip,
            .next-defense-grid { grid-template-columns: 1fr; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
