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
from settings import DEFAULT_TICKERS, WATCHLIST_PATH
from ui.theme import render_page_header, render_section_title


_DECISION_LABELS = {
    "ALLOW_BUY": "允许复核买入",
    "WAIT": "等待",
    "BLOCK_CHASE": "禁止追高",
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

    _render_quick_add(active_positions)
    _render_pool(entries, active_positions)
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


def _render_pool(entries: list[dict], active_positions: dict[str, dict]) -> None:
    render_section_title("当前观察池")
    if not entries:
        st.info("观察池为空。先用快速添加加入研究标的。")
        return

    header = st.columns([0.75, 0.9, 1.2, 0.8, 1.0, 0.95, 1.0, 1.5, 0.8, 0.75, 0.75])
    labels = ["Ticker", "状态", "主题", "持仓", "Radar", "数据", "加入时间", "备注", "查看", "编辑", "移除"]
    for col, label in zip(header, labels):
        col.markdown(f'<span class="watchlist-th">{escape(label)}</span>', unsafe_allow_html=True)

    for entry in entries:
        ticker = entry["ticker"]
        radar = _safe_radar_summary(ticker)
        held = ticker in active_positions
        row = st.columns([0.75, 0.9, 1.2, 0.8, 1.0, 0.95, 1.0, 1.5, 0.8, 0.75, 0.75])
        row[0].markdown(f'<strong class="watchlist-ticker">{escape(ticker)}</strong>', unsafe_allow_html=True)
        row[1].markdown(_status_badge_html(entry.get("status")), unsafe_allow_html=True)
        row[2].caption(str(entry.get("theme") or "未设置"))
        row[3].caption("当前已持仓" if held else "未持仓")
        row[4].markdown(_decision_badge_html(radar.get("decision")), unsafe_allow_html=True)
        row[5].caption(_data_status_label(radar.get("data_status")))
        row[6].caption(_date_text(entry.get("added_at")))
        row[7].caption(str(entry.get("note") or entry.get("added_reason") or ""))
        row[8].markdown(f"[查看 Radar](?page=ai-radar&symbol={quote(ticker)}#radar-report)")
        if row[9].button("编辑", key=f"watchlist-edit:{ticker}"):
            st.session_state["watchlist_edit_symbol"] = ticker
            st.rerun()
        if row[10].button("移除", key=f"watchlist-remove:{ticker}"):
            result = remove_watchlist_symbol(ticker, path=WATCHLIST_PATH)
            if result["action"] == "removed":
                if held:
                    st.warning(f"{ticker} 已从观察池移除；该股票仍在组合持仓中。")
                else:
                    st.success(f"{ticker} 已从观察池移除。")
                st.cache_data.clear()
                st.rerun()


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
        return {"decision": "DATA_MISSING", "data_status": "missing", "block_reasons": ["缺少本地缓存"]}


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


def _decision_badge_html(decision: object) -> str:
    value = str(decision or "DATA_MISSING")
    label = _DECISION_LABELS.get(value, "数据不足")
    tone = {
        "ALLOW_BUY": "ok",
        "WAIT": "wait",
        "BLOCK_CHASE": "block",
        "AVOID": "block",
        "DATA_MISSING": "muted",
    }.get(value, "muted")
    return f'<span class="watchlist-badge {escape(tone)}">{escape(label)}</span>'


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
