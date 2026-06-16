from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

import streamlit as st

from data.decision_log import TradeJournalStore
from data.discipline_review import (
    DEFAULT_PRINCIPLES,
    DISCIPLINE_TAG_LABELS,
    SELF_CHECK_QUESTIONS,
    DisciplineReviewStore,
    build_discipline_review_stats,
    build_portfolio_discipline_summary,
    label_for_tag,
)
from data.portfolio import PortfolioPositionStore
from data.prices import CACHE_PATH
from ui.theme import render_page_header, render_section_title


def render(path: Path = CACHE_PATH) -> None:
    _render_styles()
    render_page_header("纪律复盘", "记录个人投资原则、交易纪律标签和组合清晰度提醒。")
    discipline_store = DisciplineReviewStore(path)
    trade_store = TradeJournalStore(path)
    position_store = PortfolioPositionStore(path)
    entries = trade_store.list_entries()
    positions = position_store.list_active_positions()

    _render_principles_card(discipline_store)
    _render_self_check_questions()
    _render_portfolio_discipline(discipline_store, positions, entries)
    _render_trade_tag_editor(discipline_store, entries)
    _render_discipline_stats(discipline_store, entries)


def _render_principles_card(store: DisciplineReviewStore) -> None:
    render_section_title("我的投资原则", "这是个人纪律备忘，不参与 Setup 评分，也不会阻止交易。")
    current = store.get_principles()
    with st.form("discipline-principles-form"):
        text = st.text_area("原则文本", value=current, height=150)
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


def _render_self_check_questions() -> None:
    render_section_title("交易前纪律提醒", "只做提醒，不影响提交。")
    question_html = "".join(
        f"<li><span>{index}</span>{escape(question)}</li>"
        for index, question in enumerate(SELF_CHECK_QUESTIONS, start=1)
    )
    st.markdown(f'<section class="discipline-checklist"><ol>{question_html}</ol></section>', unsafe_allow_html=True)


def _render_portfolio_discipline(store: DisciplineReviewStore, positions: list[dict], entries: list[dict]) -> None:
    render_section_title("组合纪律检查", "聚焦持仓数量、集中度和小仓数量。")
    settings = store.get_settings()
    summary = build_portfolio_discipline_summary(positions, entries, settings)
    cards = [
        ("当前持仓", str(summary["current_holding_count"]), f"目标 {summary['target_holding_min']}-{summary['target_holding_max']} 只"),
        ("Top 1 仓位", f"{summary['top1_weight_pct']:.1f}%", "按持仓成本估算"),
        ("Top 3 仓位", f"{summary['top3_weight_pct']:.1f}%", "集中度参考"),
        ("小仓数量", str(summary["small_position_count"]), f"低于 {settings['small_position_threshold_pct']}%"),
        ("本周新开仓", str(summary["new_position_count_this_week"]), "只做频率提醒"),
        ("本周计划外", str(summary["unplanned_trade_count_this_week"]), "按情绪/标签粗略识别"),
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


def _render_trade_tag_editor(store: DisciplineReviewStore, entries: list[dict]) -> None:
    render_section_title("交易纪律标签", "标签用于复盘统计，不参与买入评分。")
    real_entries = [entry for entry in entries if str(entry.get("action_type") or "").lower() in {"buy", "add", "sell", "trim"}]
    if not real_entries:
        st.info("暂无可打标签的真实交易记录。")
        return
    options = {int(entry.get("id") or 0): _trade_option_label(entry) for entry in real_entries if int(entry.get("id") or 0) > 0}
    selected_id = st.selectbox("选择交易记录", list(options), format_func=lambda value: options.get(value, str(value)))
    current_rows = store.list_tags_for_trade(int(selected_id))
    current_tags = [str(row.get("tag") or "") for row in current_rows]
    labels = list(DISCIPLINE_TAG_LABELS.values())
    label_to_tag = {label: tag for tag, label in DISCIPLINE_TAG_LABELS.items()}
    default_labels = [DISCIPLINE_TAG_LABELS[tag] for tag in current_tags if tag in DISCIPLINE_TAG_LABELS]
    with st.form(f"discipline-tag-form-{selected_id}"):
        selected_labels = st.multiselect("纪律标签", labels, default=default_labels)
        notes = st.text_area("标签备注（可选）", value=_first_note(current_rows), height=72)
        if st.form_submit_button("保存标签", width="stretch"):
            store.save_trade_tags(int(selected_id), [label_to_tag[label] for label in selected_labels], notes)
            st.success("纪律标签已保存。")
            st.rerun()
    if current_rows:
        st.markdown(_tag_chip_html(current_tags), unsafe_allow_html=True)


def _render_discipline_stats(store: DisciplineReviewStore, entries: list[dict]) -> None:
    render_section_title("纪律复盘统计", "统计只来自交易日志和手动纪律标签。")
    tag_rows = store.list_trade_tags(days=30)
    stats = build_discipline_review_stats(entries, tag_rows)
    seven = stats["seven_days"]
    thirty = stats["thirty_days"]
    cards = [
        ("近 7 天交易", str(seven["trade_count"]), "真实成交记录"),
        ("近 30 天交易", str(thirty["trade_count"]), "真实成交记录"),
        ("参与感小仓", str(thirty["participation_small_position_count"]), "近 30 天标签"),
        ("追高", str(thirty["chase_count"]), "近 30 天标签"),
        ("Setup 低分仍买", str(thirty["low_setup_buy_count"]), "近 30 天标签"),
        ("符合计划占比", f"{thirty['plan_followed_ratio']:.1f}%", "按已打标签记录"),
    ]
    st.markdown(_card_grid_html(cards), unsafe_allow_html=True)
    if not tag_rows:
        st.info("暂无纪律标签。先给交易记录打标签后，这里会出现统计。")
        return
    selected_label = st.selectbox("按标签筛选", ["全部", *DISCIPLINE_TAG_LABELS.values()], key="discipline-tag-filter")
    selected_tag = "" if selected_label == "全部" else next(
        (tag for tag, label in DISCIPLINE_TAG_LABELS.items() if label == selected_label),
        "",
    )
    rows = [row for row in tag_rows if not selected_tag or row.get("tag") == selected_tag]
    st.markdown(_tagged_trade_table_html(rows[:50]), unsafe_allow_html=True)


def dashboard_discipline_card_html(snapshot: dict[str, Any]) -> str:
    portfolio = dict(snapshot.get("portfolio") or {})
    principle = str(snapshot.get("principle_first_line") or DEFAULT_PRINCIPLES.splitlines()[0])
    holding_count = int(portfolio.get("current_holding_count") or 0)
    target_min = int(portfolio.get("target_holding_min") or 3)
    target_max = int(portfolio.get("target_holding_max") or 5)
    small_count = int(portfolio.get("small_position_count") or 0)
    unplanned = int(portfolio.get("unplanned_trade_count_this_week") or 0)
    return f"""
    <section class="dashboard-discipline-card">
      <div>
        <span>纪律提醒</span>
        <strong>{escape(principle)}</strong>
      </div>
      <ul>
        <li>当前持仓 {holding_count} 只 / 目标 {target_min}-{target_max} 只</li>
        <li>小仓 {small_count} 只</li>
        <li>本周计划外交易 {unplanned} 次</li>
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


def _tag_chip_html(tags: list[str]) -> str:
    chips = "".join(f"<span>{escape(label_for_tag(tag))}</span>" for tag in tags)
    return f'<div class="discipline-tag-chip-row">{chips}</div>'


def _tagged_trade_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="discipline-empty">没有匹配的纪律标签记录。</div>'
    body = "".join(
        "<tr>"
        f"<td>{escape(str(row.get('trade_date') or ''))}</td>"
        f"<td>{escape(str(row.get('symbol') or ''))}</td>"
        f"<td>{escape(str(row.get('action_type') or ''))}</td>"
        f"<td>{escape(label_for_tag(row.get('tag')))}</td>"
        f"<td>{escape(str(row.get('notes') or ''))}</td>"
        "</tr>"
        for row in rows
    )
    return (
        '<div class="discipline-table-wrap"><table class="discipline-table">'
        "<thead><tr><th>日期</th><th>Ticker</th><th>操作</th><th>标签</th><th>备注</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _trade_option_label(entry: dict[str, Any]) -> str:
    quantity = entry.get("quantity")
    price = entry.get("price")
    return (
        f"#{entry.get('id')} · {entry.get('trade_date')} · {entry.get('symbol')} · "
        f"{entry.get('action_type')} · {quantity or '-'} @ {price or '-'}"
    )


def _first_note(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        note = str(row.get("notes") or "").strip()
        if note:
            return note
    return ""


def _render_styles() -> None:
    st.markdown(
        """
        <style>
        .discipline-checklist,
        .discipline-card-grid,
        .dashboard-discipline-card,
        .trade-entry-discipline-hint {
            border: 1px solid rgba(148,163,184,.24);
            border-radius: 8px;
            background: #fff;
            box-shadow: 0 14px 28px rgba(15,23,42,.05);
        }
        .discipline-checklist { padding: .8rem 1rem; margin-bottom: .9rem; }
        .discipline-checklist ol { margin: 0; padding: 0; list-style: none; display: grid; gap: .45rem; }
        .discipline-checklist li { display: flex; gap: .55rem; color: #334155; font-size: .9rem; }
        .discipline-checklist li span {
            display: inline-flex; align-items: center; justify-content: center;
            width: 1.35rem; height: 1.35rem; border-radius: 999px; background: #eef2ff; color: #3730a3; font-weight: 800;
        }
        .discipline-card-grid {
            display: grid; grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: .55rem; padding: .65rem; margin-bottom: .9rem;
        }
        .discipline-card-grid div { border-right: 1px solid rgba(148,163,184,.18); padding: .2rem .55rem; }
        .discipline-card-grid div:last-child { border-right: none; }
        .discipline-card-grid span, .discipline-card-grid em { display: block; color: #64748b; font-size: .76rem; font-style: normal; }
        .discipline-card-grid strong { display: block; color: #0f172a; font-size: 1.24rem; margin: .12rem 0; }
        .discipline-tag-chip-row { display: flex; flex-wrap: wrap; gap: .38rem; margin: .45rem 0 .9rem; }
        .discipline-tag-chip-row span {
            border: 1px solid #dbeafe; background: #eff6ff; color: #1d4ed8; border-radius: 999px;
            padding: .16rem .48rem; font-size: .78rem; font-weight: 800;
        }
        .discipline-table-wrap { overflow: auto; border: 1px solid rgba(148,163,184,.24); border-radius: 8px; background: #fff; }
        .discipline-table { width: 100%; border-collapse: collapse; font-size: .82rem; }
        .discipline-table th, .discipline-table td { padding: .5rem .58rem; border-bottom: 1px solid rgba(148,163,184,.16); text-align: left; }
        .discipline-table th { color: #64748b; background: #f8fafc; }
        .discipline-empty { border: 1px dashed #cbd5e1; border-radius: 8px; padding: .9rem; color: #64748b; background: #f8fafc; }
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
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
