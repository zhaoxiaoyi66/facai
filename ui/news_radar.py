from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape

import pandas as pd
import streamlit as st

from data.news_radar import (
    NewsRadarStore,
    available_news_symbols,
    build_news_price_context,
    news_display_rows,
    price_context_display_rows,
    refresh_general_market_news,
    refresh_symbols_news,
    trade_news_check,
    weekend_news_review,
)
from ui.theme import render_page_header, render_section_title


SCOPE_OPTIONS = {
    "持仓": "portfolio",
    "观察池": "watchlist",
    "核心仓": "core",
    "全部": "all",
}
IMPACT_OPTIONS = ["全部", "重大", "中等", "低"]
SENTIMENT_OPTIONS = ["全部", "正面", "负面", "中性", "待判断"]
RANGE_OPTIONS = {"最近 1 天": 1, "最近 7 天": 7, "最近 30 天": 30}


def render() -> None:
    _render_styles()
    render_page_header("新闻雷达", "把 FMP 新闻变成持仓、观察池和交易前后的复核材料。")
    store = NewsRadarStore()
    symbol_groups = available_news_symbols()
    selected_scope_label, selected_scope, selected_symbols = _render_filters(symbol_groups)

    if not selected_symbols:
        st.info("当前范围没有可检查股票。请先补充观察池或持仓。")
        return

    _render_refresh_bar(selected_scope_label, selected_scope, selected_symbols, store)
    filtered_news = _filtered_news(store, selected_symbols)
    _render_top_stats(store, symbol_groups)
    _render_major_event_cards(filtered_news)
    _render_price_context(selected_symbols, store)
    _render_trade_check_tool(selected_symbols, store)
    _render_weekend_review(selected_symbols, store)
    _render_market_news(store)
    _render_regular_news(filtered_news)


def _render_filters(symbol_groups: dict[str, list[str]]) -> tuple[str, str, list[str]]:
    cols = st.columns([1, 1, 1, 1.2])
    scope_label = cols[0].selectbox("范围", list(SCOPE_OPTIONS), index=0, key="news-radar-scope")
    impact_label = cols[1].selectbox("影响等级", IMPACT_OPTIONS, index=0, key="news-radar-impact")
    sentiment_label = cols[2].selectbox("情绪", SENTIMENT_OPTIONS, index=0, key="news-radar-sentiment")
    range_label = cols[3].selectbox("时间范围", list(RANGE_OPTIONS), index=1, key="news-radar-range")
    st.session_state["news_radar_impact"] = impact_label
    st.session_state["news_radar_sentiment"] = sentiment_label
    st.session_state["news_radar_days"] = RANGE_OPTIONS[range_label]
    scope = SCOPE_OPTIONS[scope_label]
    symbols = symbol_groups.get(scope) or []
    return scope_label, scope, symbols


def _render_refresh_bar(scope_label: str, scope: str, symbols: list[str], store: NewsRadarStore) -> None:
    cols = st.columns([1.2, 1, 1])
    cols[0].caption(f"当前范围：{scope_label} · {len(symbols)} 只股票。页面默认读取缓存，点击按钮才请求 FMP。")
    if cols[1].button("刷新新闻", type="primary", width="stretch"):
        result = refresh_symbols_news(symbols, store=store, scope=scope, force=True, limit=50)
        if result.get("unavailable"):
            st.warning(
                f"已请求 {result['requested']} 只股票；{result['unavailable']} 只返回当前套餐不可用，"
                f"新增 {result['inserted']} 条，更新 {result['updated']} 条。"
            )
        elif result.get("error"):
            st.warning(
                f"已请求 {result['requested']} 只股票；{result['error']} 只请求失败，"
                f"新增 {result['inserted']} 条，更新 {result['updated']} 条。"
            )
        else:
            st.success(f"新闻刷新完成：新增 {result['inserted']} 条，更新 {result['updated']} 条。")
    if cols[2].button("只读缓存", width="stretch"):
        st.info("当前页面已使用本地新闻缓存，没有发起新的 FMP 请求。")


def _filtered_news(store: NewsRadarStore, symbols: list[str]) -> list[dict]:
    days = int(st.session_state.get("news_radar_days") or 7)
    impact = str(st.session_state.get("news_radar_impact") or "全部")
    sentiment = str(st.session_state.get("news_radar_sentiment") or "全部")
    return store.list_news(
        symbols=symbols,
        since=datetime.now(timezone.utc) - timedelta(days=days),
        impact_levels=None if impact == "全部" else [impact],
        sentiment_labels=None if sentiment == "全部" else [sentiment],
        limit=200,
    )


def _render_top_stats(store: NewsRadarStore, symbol_groups: dict[str, list[str]]) -> None:
    now = datetime.now(timezone.utc)
    portfolio_major = store.list_news(
        symbols=symbol_groups.get("portfolio") or [],
        since=now - timedelta(days=7),
        impact_levels=["重大"],
    )
    watchlist_major = store.list_news(
        symbols=symbol_groups.get("watchlist") or [],
        since=now - timedelta(days=7),
        impact_levels=["重大"],
    )
    pending = [item for item in [*portfolio_major, *watchlist_major] if item.get("sentiment_label") == "待判断"]
    latest_24h = store.list_news(symbols=symbol_groups.get("all") or [], since=now - timedelta(days=1))
    cols = st.columns(4)
    cols[0].metric("持仓重大新闻", len(portfolio_major))
    cols[1].metric("观察池重大新闻", len(watchlist_major))
    cols[2].metric("待复核事件", len(pending))
    cols[3].metric("过去 24 小时新闻", len(latest_24h))


def _render_major_event_cards(news: list[dict]) -> None:
    major = [item for item in news if item.get("impact_level") == "重大"]
    render_section_title("重大事件", "只展示需要复核的高影响新闻，普通新闻默认折叠。")
    if not major:
        st.info("当前筛选范围内没有重大新闻。")
        return
    for item in major[:8]:
        tone = _event_tone(item)
        st.markdown(
            f"""
            <section class="news-event-card {tone}">
              <div class="news-event-meta">
                <b>{escape(str(item.get('symbol') or ''))}</b>
                <span>{escape(str(item.get('event_type') or '待复核'))}</span>
                <span>{escape(str(item.get('sentiment_label') or '待判断'))}</span>
                <span>{escape(str(item.get('impact_level') or '中等'))}</span>
              </div>
              <h4>{escape(str(item.get('title') or '标题待确认'))}</h4>
              <p>{escape(_event_reason(item))}</p>
              <small>{escape(str(item.get('source') or '来源待确认'))} · {escape(_time_text(item.get('published_at')))}</small>
            </section>
            """,
            unsafe_allow_html=True,
        )


def _render_price_context(symbols: list[str], store: NewsRadarStore) -> None:
    render_section_title("新闻与价格一致性", "观察新闻方向和过去价格表现是否互相印证。")
    contexts = [build_news_price_context(symbol, store=store, lookback_days=7) for symbol in symbols[:30]]
    rows = price_context_display_rows(contexts)
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info("暂无可展示的新闻与价格关系。")


def _render_trade_check_tool(symbols: list[str], store: NewsRadarStore) -> None:
    with st.expander("买入 / 卖出前新闻检查", expanded=False):
        symbol = st.selectbox("股票", symbols, key="news-radar-trade-check-symbol")
        context = trade_news_check(symbol, store=store)
        cols = st.columns(4)
        cols[0].metric("7 天重大新闻", int(context.get("major_news_7d") or 0))
        cols[1].metric("30 天重大新闻", int(context.get("major_news_30d") or 0))
        cols[2].metric("重大负面", int(context.get("negative_major_7d") or 0))
        cols[3].metric("一致性", str(context.get("news_price_match_label") or "数据不足"))
        if context.get("has_major_negative_7d"):
            st.warning("过去 7 天存在重大负面新闻，建议先复核原投资逻辑。")
        else:
            st.success("过去 7 天无重大负面新闻。")
        headlines = [headline for headline in context.get("headlines") or [] if headline]
        if headlines:
            st.caption("关键标题：" + "；".join(headlines[:3]))


def _render_weekend_review(symbols: list[str], store: NewsRadarStore) -> None:
    with st.expander("周末新闻复盘", expanded=False):
        review = weekend_news_review(symbols, store=store)
        major = news_display_rows(review.get("major_news") or [])
        if major:
            st.markdown("**本周重大新闻**")
            st.dataframe(pd.DataFrame(major), width="stretch", hide_index=True)
        else:
            st.info("本周暂无缓存中的重大新闻。")
        focus_rows = _focus_rows(review)
        if focus_rows:
            st.markdown("**正负催化集中度**")
            st.dataframe(pd.DataFrame(focus_rows), width="stretch", hide_index=True)
        unexplained = review.get("unexplained_price_moves") or []
        if unexplained:
            st.caption("价格波动无明确新闻解释：" + "、".join(str(item) for item in unexplained[:12]))


def _render_regular_news(news: list[dict]) -> None:
    with st.expander("普通新闻列表", expanded=False):
        rows = news_display_rows(news)
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.info("当前筛选范围内没有缓存新闻。")


def _render_market_news(store: NewsRadarStore) -> None:
    with st.expander("宏观 / 市场新闻", expanded=False):
        cols = st.columns([1, 2])
        if cols[0].button("刷新市场新闻", width="stretch"):
            result = refresh_general_market_news(store=store, force=True, limit=50)
            if result.get("status") == "ok":
                st.success(f"市场新闻刷新完成：新增 {result['inserted']} 条，更新 {result['updated']} 条。")
            elif result.get("status") == "unavailable":
                st.warning("当前套餐不可用，已保留本地缓存。")
            else:
                st.warning(str(result.get("message") or "市场新闻刷新失败。"))
        cols[1].caption("默认只读本地缓存；点击刷新才请求 FMP General Market News。")
        rows = news_display_rows(
            store.list_news(symbols=["MARKET"], since=datetime.now(timezone.utc) - timedelta(days=7), limit=30)
        )
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.info("暂无缓存中的宏观 / 市场新闻。")


def _focus_rows(review: dict) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for symbol, count in (review.get("positive_focus") or [])[:5]:
        rows.append({"股票": symbol, "方向": "正面催化", "数量": count})
    for symbol, count in (review.get("negative_focus") or [])[:5]:
        rows.append({"股票": symbol, "方向": "负面新闻", "数量": count})
    return rows


def _event_reason(item: dict) -> str:
    sentiment = str(item.get("sentiment_label") or "待判断")
    impact = str(item.get("impact_level") or "中等")
    if impact == "重大" and sentiment == "负面":
        return "可能影响交易逻辑，需要复核是否破坏原假设。"
    if impact == "重大" and sentiment == "正面":
        return "可能形成正面催化，需要结合价格是否兑现。"
    return "事件方向不够明确，先作为待复核材料。"


def _event_tone(item: dict) -> str:
    sentiment = str(item.get("sentiment_label") or "")
    if sentiment == "负面":
        return "is-negative"
    if sentiment == "正面":
        return "is-positive"
    return "is-neutral"


def _time_text(value: object) -> str:
    if not value:
        return "时间待确认"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M HKT")


def _render_styles() -> None:
    st.markdown(
        """
        <style>
        .news-event-card {
            border: 1px solid rgba(148, 163, 184, 0.18);
            border-radius: 8px;
            background: #FFFFFF;
            padding: 0.85rem 0.95rem;
            margin: 0.55rem 0;
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.04);
        }
        .news-event-card.is-positive {
            border-color: rgba(22, 163, 74, 0.22);
            background: #F7FEFA;
        }
        .news-event-card.is-negative {
            border-color: rgba(220, 38, 38, 0.22);
            background: #FFF7F7;
        }
        .news-event-card h4 {
            margin: 0.45rem 0 0.35rem;
            font-size: 1rem;
            line-height: 1.35;
        }
        .news-event-card p {
            margin: 0 0 0.42rem;
            color: #475569;
            font-size: 0.86rem;
        }
        .news-event-card small {
            color: #64748B;
            font-size: 0.75rem;
        }
        .news-event-meta {
            display: flex;
            gap: 0.38rem;
            align-items: center;
            flex-wrap: wrap;
        }
        .news-event-meta b,
        .news-event-meta span {
            display: inline-flex;
            align-items: center;
            min-height: 22px;
            padding: 0 0.48rem;
            border-radius: 999px;
            border: 1px solid rgba(100, 116, 139, 0.16);
            background: #F8FAFC;
            color: #334155;
            font-size: 0.72rem;
            font-weight: 760;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
