from __future__ import annotations

import streamlit as st

from settings import DEFAULT_TICKERS, WATCHLIST_PATH, load_watchlist, save_watchlist
from ui.theme import render_page_header, render_section_title


def render() -> None:
    render_page_header("观察名单", "维护你的股票池；保存后 dashboard 会按这份名单重新加载。")

    current = load_watchlist()
    edited = st.text_area("股票代码", value="\n".join(current), height=320)

    action_cols = st.columns(3)
    with action_cols[0]:
        if st.button("保存观察名单", type="primary"):
            saved = save_watchlist(edited)
            st.success(f"已保存 {len(saved)} 个股票代码到 {WATCHLIST_PATH}。")
            st.cache_data.clear()
    with action_cols[1]:
        if st.button("恢复初始股票池"):
            saved = save_watchlist(DEFAULT_TICKERS)
            st.success(f"已恢复 {len(saved)} 个初始股票代码。")
            st.cache_data.clear()
            st.rerun()
    with action_cols[2]:
        st.download_button(
            "下载配置",
            data=WATCHLIST_PATH.read_text(encoding="utf-8"),
            file_name="watchlist.yaml",
            mime="text/yaml",
        )

    render_section_title("当前文件")
    st.code(str(WATCHLIST_PATH), language="text")
