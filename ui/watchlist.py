from __future__ import annotations

from html import escape
from urllib.parse import quote

import streamlit as st

from data.ai_stock_radar import build_ai_stock_radar_list_row
from data.portfolio import PortfolioPositionStore
from data.watchlist_store import DEFAULT_WATCHLIST_STATUS
from data.watchlist_store import DEFAULT_WATCHLIST_THEME
from data.watchlist_store import WATCHLIST_STATUS_LABELS
from data.watchlist_store import WATCHLIST_STATUSES
from data.watchlist_store import WATCHLIST_THEMES
from data.watchlist_store import add_watchlist_symbol
from data.watchlist_store import batch_add_watchlist_symbols
from data.watchlist_store import format_watchlist_status
from data.watchlist_store import load_watchlist_entries
from data.watchlist_store import normalize_watchlist_symbol
from data.watchlist_store import remove_watchlist_symbol
from data.watchlist_store import save_watchlist_entries
from data.watchlist_store import update_watchlist_symbol
from data.watchlist_stars import WatchlistStarStore
from settings import DEFAULT_TICKERS, WATCHLIST_PATH
from ui.theme import render_page_header, render_section_title


_DECISION_LABELS = {
    "ALLOW_BUY": "小仓观察建议",
    "WAIT": "等待",
    "BLOCK_CHASE": "追高风险提醒",
    "AVOID": "回避",
    "DATA_MISSING": "数据不足",
}
_DATA_STATUS_LABELS = {
    "ok": "正常",
    "stale": "缓存过期",
    "missing": "缺数据",
    "data_missing": "缺数据",
}


def render() -> None:
    render_page_header("观察池", "研究池 / Radar 股票池；添加股票不会生成交易，也不会改变组合持仓。")
    _render_styles()

    entries = load_watchlist_entries(WATCHLIST_PATH, default_symbols=DEFAULT_TICKERS)
    active_positions = _active_positions_by_symbol()
    star_store = WatchlistStarStore()

    _render_quick_add(active_positions)
    _render_pool(entries, active_positions, star_store)
    _render_edit_panel()
    _render_advanced_management()


def _render_quick_add(active_positions: dict[str, dict]) -> None:
    render_section_title("快速添加")
    with st.form("watchlist_quick_add", clear_on_submit=True):
        top = st.columns([1.1, 1.2, 1.35])
        ticker = top[0].text_input("股票代码", placeholder="例如 NVDA / BRK.B")
        status = top[1].selectbox(
            "观察状态",
            list(WATCHLIST_STATUSES),
            format_func=format_watchlist_status,
            index=list(WATCHLIST_STATUSES).index(DEFAULT_WATCHLIST_STATUS),
        )
        theme = top[2].selectbox(
            "主题/分类",
            list(WATCHLIST_THEMES),
            index=list(WATCHLIST_THEMES).index(DEFAULT_WATCHLIST_THEME),
        )
        reason = st.text_input("添加理由", placeholder="例如：等待财报复核 / 观察 AI 基建订单质量")
        note = st.text_area("备注", height=72, placeholder="只记录研究背景，不代表买入计划。")
        submitted = st.form_submit_button("加入观察池", type="primary")

    if not submitted:
        return

    try:
        symbol = normalize_watchlist_symbol(ticker)
        result = add_watchlist_symbol(
            symbol,
            status=status,
            theme=theme,
            added_reason=reason,
            note=note,
            path=WATCHLIST_PATH,
        )
    except ValueError as exc:
        st.error(str(exc))
        return

    held_note = " 当前已持仓；本操作只更新观察池，不改变持仓。" if symbol in active_positions else ""
    if result["action"] == "added":
        st.success(f"{symbol} 已加入观察池。{held_note}")
    else:
        st.info(f"{symbol} 已存在，已更新状态/备注。{held_note}")
    st.cache_data.clear()
    st.rerun()


def _render_pool(entries: list[dict], active_positions: dict[str, dict], star_store: WatchlistStarStore | None = None) -> None:
    render_section_title("当前观察池")
    if not entries:
        st.info("观察池为空。先用快速添加加入研究标的。")
        return

    star_store = star_store or WatchlistStarStore()
    star_marks = star_store.get_marks([entry.get("ticker") for entry in entries])
    filter_choice = _render_star_filter_controls(star_marks)
    entries = _sort_watchlist_entries_by_star(entries, star_marks)
    if filter_choice == "starred":
        entries = [entry for entry in entries if _entry_is_starred(entry, star_marks)]

    st.caption("星标只影响排序和显示，不影响买点评分。买入仍看 Setup、承接和风险收益。")

    widths = [0.9, 0.85, 1.05, 0.75, 0.9, 1.75, 0.85, 0.9, 1.2, 0.75, 0.65, 0.65]
    header = st.columns(widths)
    labels = ["Ticker", "状态", "主题", "持仓", "Radar", "买点", "数据", "加入时间", "备注", "查看", "编辑", "移除"]
    for col, label in zip(header, labels):
        col.markdown(f'<span class="watchlist-th">{escape(label)}</span>', unsafe_allow_html=True)

    for entry in entries:
        ticker = entry["ticker"]
        radar = _safe_radar_summary(ticker)
        held = ticker in active_positions
        is_starred = _entry_is_starred(entry, star_marks)
        row = st.columns(widths)
        star_prefix = "⭐ " if is_starred else ""
        row[0].markdown(f'<strong class="watchlist-ticker">{escape(star_prefix + ticker)}</strong>', unsafe_allow_html=True)
        star_action = "取消星标" if is_starred else "标星"
        if row[0].button(star_action, key=f"watchlist-star:{ticker}", help="只影响排序和显示"):
            star_store.toggle_star(ticker)
            st.rerun()
        row[1].markdown(_status_badge_html(entry.get("status")), unsafe_allow_html=True)
        row[2].caption(str(entry.get("theme") or "未设置"))
        row[3].caption("当前已持仓" if held else "未持仓")
        row[4].markdown(_decision_badge_html(radar, held=held), unsafe_allow_html=True)
        row[5].markdown(_entry_display_html(radar), unsafe_allow_html=True)
        row[6].caption(_data_status_label(radar.get("data_status")))
        row[7].caption(_date_text(entry.get("added_at")))
        row[8].caption(str(entry.get("note") or entry.get("added_reason") or ""))
        row[9].markdown(f"[查看 Radar](?page=ai-radar&symbol={quote(ticker)}#radar-report)")
        if row[10].button("编辑", key=f"watchlist-edit:{ticker}"):
            st.session_state["watchlist_edit_symbol"] = ticker
            st.rerun()
        if row[11].button("移除", key=f"watchlist-remove:{ticker}"):
            result = remove_watchlist_symbol(ticker, path=WATCHLIST_PATH)
            if result["action"] == "removed":
                if held:
                    st.warning(f"{ticker} 已从观察池移除；该股票仍在组合持仓中。")
                else:
                    st.success(f"{ticker} 已从观察池移除。")
                st.cache_data.clear()
                st.rerun()


def _render_star_filter_controls(star_marks: dict[str, dict]) -> str:
    current = str(st.session_state.get("watchlist_star_filter") or "all")
    left, middle, _spacer = st.columns([0.16, 0.16, 0.68], gap="small")
    if left.button("全部", key="watchlist-filter-all", width="stretch", type="primary" if current == "all" else "secondary"):
        st.session_state["watchlist_star_filter"] = "all"
        st.rerun()
    starred_count = sum(1 for mark in star_marks.values() if mark.get("is_starred"))
    if middle.button(f"星标 {starred_count}", key="watchlist-filter-starred", width="stretch", type="primary" if current == "starred" else "secondary"):
        st.session_state["watchlist_star_filter"] = "starred"
        st.rerun()
    return current


def _sort_watchlist_entries_by_star(entries: list[dict], star_marks: dict[str, dict]) -> list[dict]:
    indexed = list(enumerate(entries))
    indexed.sort(key=lambda item: (not _entry_is_starred(item[1], star_marks), item[0]))
    return [entry for _index, entry in indexed]


def _entry_is_starred(entry: dict, star_marks: dict[str, dict]) -> bool:
    symbol = str(entry.get("ticker") or entry.get("symbol") or "").strip().upper()
    return bool(star_marks.get(symbol, {}).get("is_starred"))


def _render_edit_panel() -> None:
    symbol = st.session_state.get("watchlist_edit_symbol")
    if not symbol:
        return

    entries = load_watchlist_entries(WATCHLIST_PATH, default_symbols=DEFAULT_TICKERS)
    entry = next((item for item in entries if item["ticker"] == symbol), None)
    if not entry:
        st.session_state.pop("watchlist_edit_symbol", None)
        return

    with st.expander(f"编辑观察项：{symbol}", expanded=True):
        with st.form(f"watchlist_edit_form:{symbol}"):
            status = st.selectbox(
                "观察状态",
                list(WATCHLIST_STATUSES),
                format_func=format_watchlist_status,
                index=_option_index(WATCHLIST_STATUSES, entry.get("status"), DEFAULT_WATCHLIST_STATUS),
            )
            theme = st.selectbox(
                "主题/分类",
                list(WATCHLIST_THEMES),
                index=_option_index(WATCHLIST_THEMES, entry.get("theme"), DEFAULT_WATCHLIST_THEME),
            )
            reason = st.text_input("添加理由", value=str(entry.get("added_reason") or ""))
            note = st.text_area("备注", value=str(entry.get("note") or ""), height=88)
            controls = st.columns(2)
            save = controls[0].form_submit_button("保存修改", type="primary")
            cancel = controls[1].form_submit_button("取消")
        if cancel:
            st.session_state.pop("watchlist_edit_symbol", None)
            st.rerun()
        if save:
            update_watchlist_symbol(symbol, status=status, theme=theme, added_reason=reason, note=note, path=WATCHLIST_PATH)
            st.session_state.pop("watchlist_edit_symbol", None)
            st.success(f"{symbol} 观察信息已更新。")
            st.cache_data.clear()
            st.rerun()


def _render_advanced_management() -> None:
    with st.expander("高级管理", expanded=False):
        st.caption("批量管理只影响观察池，不创建交易日志，不改变组合持仓，不刷新价格。")
        batch = st.text_area("批量粘贴 ticker", height=140, placeholder="NVDA\nMSFT\nBRK.B")
        cols = st.columns(3)
        if cols[0].button("批量加入观察池"):
            result = batch_add_watchlist_symbols(batch, path=WATCHLIST_PATH)
            st.success(f"新增 {len(result['added'])}；更新 {len(result['updated'])}；无效 {len(result['invalid'])}。")
            if result["invalid"]:
                st.warning("无效代码：" + ", ".join(result["invalid"]))
            st.cache_data.clear()
            st.rerun()
        if cols[1].button("恢复初始观察池"):
            save_watchlist_entries([{"ticker": ticker, "status": "active"} for ticker in DEFAULT_TICKERS], WATCHLIST_PATH)
            st.success(f"已恢复 {len(DEFAULT_TICKERS)} 只初始观察标的。")
            st.cache_data.clear()
            st.rerun()
        config_text = WATCHLIST_PATH.read_text(encoding="utf-8") if WATCHLIST_PATH.exists() else ""
        cols[2].download_button("下载配置", data=config_text, file_name="watchlist.yaml", mime="text/yaml")
        st.code(str(WATCHLIST_PATH), language="text")


def _active_positions_by_symbol() -> dict[str, dict]:
    try:
        rows = PortfolioPositionStore().list_active_positions()
    except Exception:
        return {}
    return {str(row.get("symbol") or "").upper(): row for row in rows if _number(row.get("quantity")) > 0}


def _safe_radar_summary(ticker: str) -> dict:
    try:
        return build_ai_stock_radar_list_row(ticker)
    except Exception:
        return {
            "decision": "DATA_MISSING",
            "data_status": "missing",
            "block_reasons": ["缺少本地缓存"],
            "entry_display_label": "暂无参考买区：缺少本地缓存",
            "entry_action_hint": "补齐本地缓存后再复核",
        }


def _entry_display_html(row: dict) -> str:
    label = str(row.get("entry_display_label") or "暂无参考买区").strip()
    hint = str(row.get("entry_action_hint") or row.get("entry_display_reason") or "只读参考，仅作风险提示").strip()
    return (
        '<div class="watchlist-entry-ref">'
        f'<strong>{escape(label)}</strong>'
        f'<span>{escape(hint)}</span>'
        "</div>"
    )


def _status_badge_html(status: object) -> str:
    value = str(status or DEFAULT_WATCHLIST_STATUS)
    label = format_watchlist_status(value)
    tone = {
        "active": "ok",
        "waiting_buy_zone": "wait",
        "needs_review": "review",
        "paused": "muted",
        "rejected": "muted",
    }.get(value, "muted")
    return f'<span class="watchlist-badge {escape(tone)}">{escape(label)}</span>'


def _decision_badge_html(decision: object, *, held: bool = False) -> str:
    value, label, canonical_tone = _decision_badge_parts(decision, held=held)
    tone = {
        "ALLOW_BUY": "ok",
        "WAIT": "wait",
        "BLOCK_CHASE": "block",
        "AVOID": "block",
        "DATA_MISSING": "muted",
        "NO_BUY_ZONE": "muted",
    }.get(value, canonical_tone or "muted")
    return f'<span class="watchlist-badge {escape(tone)}">{escape(label)}</span>'


def _decision_badge_parts(source: object, *, held: bool = False) -> tuple[str, str, str]:
    row = source if isinstance(source, dict) else {}
    canonical_action = _canonical_buy_zone_action(row)
    if canonical_action in {"DATA_INSUFFICIENT", "DATA_MISSING"}:
        return "DATA_MISSING", "暂停加仓 / 数据不足" if held else "数据不足", "muted"
    if canonical_action in {"NO_BUY_ZONE", "ZONE_MISSING"}:
        return "NO_BUY_ZONE", "未生成买区", "muted"
    if canonical_action in {"ALLOW_SMALL_BUY", "ALLOW_ADD_ON_PULLBACK"}:
        return "ALLOW_BUY", "小仓观察建议", "ok"
    if canonical_action in {"WAIT_PULLBACK", "WAIT_CONFIRMATION"}:
        return "WAIT", "等待", "wait"
    if canonical_action == "BLOCK_CHASE":
        return "BLOCK_CHASE", "追高风险提醒", "block"
    if canonical_action == "RISK_REVIEW":
        return "WAIT", "风控复核", "wait"
    if canonical_action == "AVOID":
        return "AVOID", "回避", "block"

    decision = row.get("decision") if row else source
    value = str(decision or "DATA_MISSING").strip().upper()
    return value, _DECISION_LABELS.get(value, "数据不足"), ""


def _canonical_buy_zone_action(row: dict) -> str:
    display = row.get("buy_zone_display") or row.get("buyZoneDisplay")
    if isinstance(display, dict):
        for key in (
            "current_action",
            "currentAction",
            "action_status",
            "actionStatus",
            "action_code",
            "action",
            "buy_zone_action",
            "buyZoneAction",
            "entry_context_status",
        ):
            value = str(display.get(key) or "").strip().upper()
            if value:
                return value
    context = row.get("buy_zone_context") or row.get("buyZoneContext")
    if isinstance(context, dict):
        for key in ("current_action", "currentAction", "action_status", "actionStatus"):
            value = str(context.get(key) or "").strip().upper()
            if value:
                return value
    return ""


def _data_status_label(value: object) -> str:
    key = str(value or "missing")
    return _DATA_STATUS_LABELS.get(key, key or "缺数据")


def _date_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "旧格式"
    return text[:10]


def _option_index(options: tuple[str, ...] | list[str], value: object, fallback: str) -> int:
    cleaned = str(value or fallback)
    return list(options).index(cleaned) if cleaned in options else list(options).index(fallback)


def _number(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _render_styles() -> None:
    st.markdown(
        """
        <style>
        .watchlist-th {
            display:block;
            color:#64748B;
            font-size:11px;
            font-weight:700;
            letter-spacing:.02em;
            padding:2px 0 6px;
        }
        .watchlist-ticker {
            color:#0F172A;
            font-size:13px;
            letter-spacing:.02em;
        }
        .watchlist-entry-ref {
            line-height:1.25;
            max-width:240px;
        }
        .watchlist-entry-ref strong {
            display:block;
            color:#0F172A;
            font-size:12px;
            font-weight:750;
        }
        .watchlist-entry-ref span {
            display:block;
            color:#64748B;
            font-size:11px;
            margin-top:3px;
        }
        .watchlist-badge {
            display:inline-flex;
            align-items:center;
            min-height:22px;
            padding:2px 8px;
            border:1px solid #E2E8F0;
            border-radius:999px;
            color:#334155;
            background:#F8FAFC;
            font-size:12px;
            font-weight:700;
            white-space:nowrap;
        }
        .watchlist-badge.ok {
            color:#166534;
            border-color:#CDEFD7;
            background:#F3FBF5;
        }
        .watchlist-badge.wait,
        .watchlist-badge.review {
            color:#92400E;
            border-color:#F3DEB7;
            background:#FFFBEB;
        }
        .watchlist-badge.block {
            color:#9F1239;
            border-color:#F4C7CE;
            background:#FFF1F2;
        }
        .watchlist-badge.muted {
            color:#475569;
            border-color:#CBD5E1;
            background:#F8FAFC;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
