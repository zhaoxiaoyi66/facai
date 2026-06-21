"""新闻雷达页面。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import pandas as pd
import streamlit as st

from data.news_radar import (
    NewsRadarStore,
    available_news_symbols,
    build_news_price_context,
    price_context_display_rows,
    refresh_general_market_news,
    refresh_symbols_news,
    source_link_text,
    trade_news_check,
    weekend_news_review,
)

SCOPE_OPTIONS = {
    "持仓": "portfolio",
    "观察池": "watchlist",
    "核心仓": "core",
    "全部": "all",
}
IMPACT_OPTIONS = ["全部", "重大", "中等", "低"]
SENTIMENT_OPTIONS = ["全部", "正面", "负面", "中性", "待判断"]
RANGE_OPTIONS = {"最近 1 天": 1, "最近 7 天": 7, "最近 30 天": 30}

EVENT_TYPE_LABELS = {
    "earnings": "财报",
    "financial_results": "财报",
    "guidance": "指引",
    "rating": "评级调整",
    "rating_change": "评级调整",
    "analyst_rating": "评级调整",
    "price_target": "目标价调整",
    "target_price": "目标价调整",
    "m&a": "并购",
    "ma": "并购",
    "merger": "并购",
    "acquisition": "并购",
    "partnership": "合作/订单",
    "order": "合作/订单",
    "ai": "AI/数据中心",
    "ai_data_center": "AI/数据中心",
    "data_center": "AI/数据中心",
    "product": "产品/技术",
    "technology": "产品/技术",
    "regulatory": "监管/诉讼",
    "lawsuit": "监管/诉讼",
    "litigation": "监管/诉讼",
    "management": "管理层变动",
    "management_change": "管理层变动",
    "short_report": "做空报告",
    "macro": "宏观/板块",
    "sector": "宏观/板块",
    "opinion": "观点文章",
    "analyst_opinion": "观点文章",
    "article": "观点文章",
    "ordinary": "普通市场新闻",
    "general": "普通市场新闻",
    "market_news": "普通市场新闻",
    "low_value_repeat": "低价值复述",
}
SENTIMENT_LABELS = {
    "positive": "正面",
    "bullish": "正面",
    "negative": "负面",
    "bearish": "负面",
    "neutral": "中性",
    "pending": "待判断",
    "unknown": "待判断",
}
IMPACT_LABELS = {
    "major": "重大",
    "high": "重大",
    "medium": "中等",
    "moderate": "中等",
    "low": "低",
    "minor": "低",
}


def render() -> None:
    store = NewsRadarStore()
    symbol_groups = available_news_symbols()
    st.caption("ZHX RESEARCH")
    st.title("新闻雷达")
    st.write("追踪持仓和观察池的重大新闻，辅助复核交易逻辑。")

    scope_label, impact_filter, sentiment_filter, range_label = _render_filters()
    scope_key = SCOPE_OPTIONS[scope_label]
    symbols = sorted(symbol_groups.get(scope_key, set()) if scope_key != "all" else symbol_groups.get("all", set()))
    since = datetime.now(timezone.utc) - timedelta(days=RANGE_OPTIONS[range_label])
    items = _filtered_news(
        store,
        symbols=symbols if scope_key != "all" else None,
        since=since,
        impact_filter=impact_filter,
        sentiment_filter=sentiment_filter,
    )

    _render_action_bar(store, symbols=symbols, scope_key=scope_key, items=items)
    _render_stats(store, items, symbol_groups)

    major_items = [item for item in items if item.get("impact_level") == "重大"]
    if not major_items:
        st.info("当前筛选范围内没有重大新闻。")
    else:
        st.subheader("重大事件")
        for item in major_items[:12]:
            _render_news_card(item, store=store, symbol_groups=symbol_groups)

    _render_price_context(symbols, store=store)
    _render_trade_check(symbols, store=store)
    _render_regular_news(items)
    _render_weekend_review(symbols, store=store)
    _render_market_news(store)


def _render_filters() -> tuple[str, str, str, str]:
    cols = st.columns([1, 1, 1, 1])
    with cols[0]:
        scope_label = st.selectbox("范围", list(SCOPE_OPTIONS.keys()), index=0)
    with cols[1]:
        impact_filter = st.selectbox("影响等级", IMPACT_OPTIONS, index=0)
    with cols[2]:
        sentiment_filter = st.selectbox("情绪", SENTIMENT_OPTIONS, index=0)
    with cols[3]:
        range_label = st.selectbox("时间范围", list(RANGE_OPTIONS.keys()), index=1)
    return scope_label, impact_filter, sentiment_filter, range_label


def _filtered_news(
    store: NewsRadarStore,
    *,
    symbols: Iterable[str] | None,
    since: datetime,
    impact_filter: str,
    sentiment_filter: str,
) -> list[dict[str, Any]]:
    impacts = [] if impact_filter == "全部" else [impact_filter]
    sentiments = [] if sentiment_filter == "全部" else [sentiment_filter]
    return store.list_news(
        symbols=symbols,
        since=since,
        impact_levels=impacts,
        sentiment_labels=sentiments,
        limit=500,
    )


def _render_action_bar(
    store: NewsRadarStore,
    *,
    symbols: list[str],
    scope_key: str,
    items: list[dict[str, Any]],
) -> None:
    cols = st.columns([1.2, 1.2, 1.2, 2])
    with cols[0]:
        if st.button("刷新新闻", width="stretch"):
            if not symbols:
                st.warning("当前范围没有可刷新的股票。")
            else:
                results = refresh_symbols_news(symbols, scope=scope_key, store=store, force=True, limit=20)
                ok = sum(1 for item in results if item.get("status") == "ok")
                unavailable = sum(1 for item in results if item.get("status") == "unavailable")
                failed = sum(1 for item in results if item.get("status") == "error")
                st.success(f"刷新完成：成功 {ok} 个，套餐不可用 {unavailable} 个，失败 {failed} 个。")
    with cols[1]:
        if st.button("补全中文翻译", width="stretch"):
            result = store.fill_missing_translations(items)
            st.success(
                f"已补全 {result['title']} 条中文标题，"
                f"{result['summary']} 条中文摘要，失败 {result['failed']} 条。"
            )
    with cols[2]:
        if st.button("刷新市场新闻", width="stretch"):
            result = refresh_general_market_news(store=store, force=True, limit=30)
            status = result.get("message") or "已完成"
            if result.get("status") in {"ok", "cache"}:
                st.success(status)
            else:
                st.warning(status)
    with cols[3]:
        st.caption("页面默认读取缓存；只有点击刷新新闻才请求 FMP，点击补全中文翻译才写入翻译缓存。")


def _render_stats(store: NewsRadarStore, items: list[dict[str, Any]], symbol_groups: dict[str, set[str]]) -> None:
    now = datetime.now(timezone.utc)
    recent_24h = [
        item
        for item in items
        if _parse_datetime(item.get("published_at")) and now - _parse_datetime(item.get("published_at")) <= timedelta(days=1)
    ]
    portfolio = symbol_groups.get("portfolio", set())
    watchlist = symbol_groups.get("watchlist", set())
    portfolio_major = [
        item for item in items if item.get("impact_level") == "重大" and str(item.get("symbol")) in portfolio
    ]
    watchlist_major = [
        item for item in items if item.get("impact_level") == "重大" and str(item.get("symbol")) in watchlist
    ]
    pending = [item for item in items if item.get("sentiment_label") == "待判断" or item.get("impact_level") == "重大"]
    missing_translation = [
        item for item in items if not _clean(item.get("title_zh")) or not _clean(item.get("summary_zh"))
    ]
    cards = [
        ("持仓重大新闻", len(portfolio_major)),
        ("观察池重大新闻", len(watchlist_major)),
        ("待复核事件", len(pending)),
        ("过去 24 小时新闻", len(recent_24h)),
        ("缺中文翻译", len(missing_translation)),
    ]
    cols = st.columns(len(cards))
    for col, (label, value) in zip(cols, cards):
        with col:
            st.metric(label, value)


def _render_news_card(item: dict[str, Any], *, store: NewsRadarStore, symbol_groups: dict[str, set[str]]) -> None:
    symbol = _clean(item.get("symbol"))
    symbol_label = _news_symbol_label(symbol)
    event_type = _event_type_label(item.get("event_type"))
    sentiment = _sentiment_label(item.get("sentiment_label"))
    impact = _impact_label(item.get("impact_level"))
    title_zh, original_title, translation_note = _title_parts(item)
    summary = _summary_text(item)
    relevance = _relevance_reason(item, symbol_groups)
    price_context = build_news_price_context(symbol, store=store) if symbol and symbol != "MARKET" else None
    price_line = _price_reaction_line(price_context)
    tone = _card_tone(item, symbol_groups)

    with st.container(border=True):
        st.markdown(f"**{symbol_label}｜{event_type}｜{sentiment}｜{impact}**")
        st.markdown(f"### {title_zh}")
        if translation_note:
            st.caption(translation_note)
        if original_title and original_title != title_zh:
            st.caption(f"原文：{original_title}")
        st.write(f"摘要：{summary}")
        st.write(f"为什么重要：{relevance}")
        if price_line:
            st.write(f"价格反应：{price_line}")
        st.caption(_source_line(item))
        _render_news_details(item, relevance=relevance, price_context=price_context, tone=tone)


def _render_news_details(
    item: dict[str, Any],
    *,
    relevance: str,
    price_context: dict[str, Any] | None,
    tone: str,
) -> None:
    title = _clean(item.get("original_title") or item.get("title")) or "原文标题缺失"
    with st.expander("展开详情", expanded=False):
        details = _news_detail_rows(item, relevance=relevance, price_context=price_context, tone=tone)
        for label, value in details:
            st.write(f"**{label}：** {value}")


def _render_price_context(symbols: list[str], *, store: NewsRadarStore) -> None:
    st.subheader("新闻-价格一致性")
    if not symbols:
        st.info("当前范围没有股票可计算新闻-价格一致性。")
        return
    contexts = [build_news_price_context(symbol, store=store) for symbol in symbols[:40]]
    st.dataframe(pd.DataFrame(price_context_display_rows(contexts)), width="stretch", hide_index=True)


def _render_trade_check(symbols: list[str], *, store: NewsRadarStore) -> None:
    with st.expander("买入 / 卖出前新闻检查", expanded=False):
        if not symbols:
            st.info("当前范围没有可检查的股票。")
            return
        selected = st.selectbox("选择股票", symbols, key="news_radar_trade_check_symbol")
        check = trade_news_check(selected, store=store)
        st.write(check["summary"])
        st.write(f"新闻-价格一致性：{check.get('news_price_match_label') or '价格反应数据不足'}")
        if check.get("headlines"):
            st.write("关键标题：")
            for headline in check["headlines"]:
                st.write(f"- {headline}")


def _render_regular_news(items: list[dict[str, Any]]) -> None:
    regular = [item for item in items if item.get("impact_level") != "重大"]
    with st.expander("普通新闻列表", expanded=False):
        if not regular:
            st.info("当前筛选范围内没有普通新闻。")
            return
        for item in regular[:80]:
            title_zh, original_title, note = _title_parts(item)
            line = _source_line(item)
            st.markdown(f"**{_news_symbol_label(_clean(item.get('symbol')))}｜{title_zh}**")
            if note:
                st.caption(note)
            if original_title and original_title != title_zh:
                st.caption(f"原文：{original_title}")
            st.caption(line)


def _render_weekend_review(symbols: list[str], *, store: NewsRadarStore) -> None:
    with st.expander("周末新闻复盘", expanded=False):
        if not symbols:
            st.info("当前范围没有可复盘的股票。")
            return
        review = weekend_news_review(symbols, store=store)
        major = review.get("major_news", [])
        st.write(f"本周重大新闻：{len(major)} 条")
        if major:
            for item in major[:8]:
                title_zh, original_title, _ = _title_parts(item)
                st.markdown(f"- **{item.get('symbol')}**｜{title_zh} · {source_link_text(item)}")
        unexplained = review.get("unexplained_price_moves", [])
        if unexplained:
            st.write(f"价格波动无明确新闻解释：{', '.join(unexplained[:20])}")
        negative = [(symbol, count) for symbol, count in review.get("negative_concentration", []) if count]
        positive = [(symbol, count) for symbol, count in review.get("positive_concentration", []) if count]
        if negative:
            st.write("负面新闻集中： " + "，".join(f"{symbol} {count} 条" for symbol, count in negative[:5]))
        if positive:
            st.write("正面催化集中： " + "，".join(f"{symbol} {count} 条" for symbol, count in positive[:5]))


def _render_market_news(store: NewsRadarStore) -> None:
    with st.expander("市场新闻", expanded=False):
        items = store.list_news(symbols=["MARKET"], limit=50)
        if not items:
            st.info("尚无市场新闻缓存。点击“刷新市场新闻”后再查看。")
            return
        for item in items[:30]:
            title_zh, original_title, note = _title_parts(item)
            st.markdown(f"**{title_zh}**")
            if note:
                st.caption(note)
            if original_title and original_title != title_zh:
                st.caption(f"原文：{original_title}")
            st.caption(_source_line(item))


def _news_detail_rows(
    item: dict[str, Any],
    *,
    relevance: str | None = None,
    price_context: dict[str, Any] | None = None,
    tone: str | None = None,
) -> list[tuple[str, str]]:
    keywords = _keywords_text(item)
    price_line = _price_reaction_line(price_context)
    original_summary = _clean(item.get("summary") or item.get("raw_text") or item.get("original_text")) or "原始摘要缺失"
    return [
        ("原文标题", _clean(item.get("original_title") or item.get("title")) or "原文标题缺失"),
        ("原文链接", source_link_text(item)),
        ("原始来源", _clean(item.get("source") or item.get("site")) or "未知来源"),
        ("发布时间", _format_time(item.get("published_at"))),
        ("事件类型", _event_type_label(item.get("event_type"))),
        ("情绪判断", _sentiment_label(item.get("sentiment_label"))),
        ("影响等级", _impact_label(item.get("impact_level"))),
        ("关键词命中", keywords or "未命中明显关键词"),
        ("中文摘要", _summary_text(item)),
        ("为什么重要", relevance or _clean(item.get("relevance_reason_zh")) or "需要人工复核影响。"),
        ("新闻-价格一致性", price_line or "价格反应数据不足"),
        ("原始新闻摘要", original_summary),
    ]


def _title_parts(item: dict[str, Any]) -> tuple[str, str, str]:
    original_title = _clean(item.get("original_title") or item.get("title"))
    title_zh = _clean(item.get("title_zh"))
    if title_zh:
        return title_zh, original_title, ""
    return original_title or "待翻译", original_title, "待翻译"


def _summary_text(item: dict[str, Any]) -> str:
    summary = _clean(item.get("summary_zh"))
    if summary:
        return summary
    original = _clean(item.get("summary") or item.get("raw_text") or item.get("original_text"))
    if original and _has_chinese(original):
        return original[:80]
    return "待生成摘要。"


def _relevance_reason(item: dict[str, Any], symbol_groups: dict[str, set[str]]) -> str:
    symbol = _clean(item.get("symbol"))
    title = f"{item.get('original_title') or item.get('title') or ''} {item.get('summary') or ''}".lower()
    if symbol == "NVDA" and any(word in title for word in ("custom chip", "in-house chip", "google", "tpu", "asic")):
        return "属于客户自研芯片风险，需要区分长期竞争和短期需求。"
    if symbol == "NOW" and any(word in title for word in ("ai", "saas", "automation", "agent")):
        return "属于企业 AI 对 SaaS 护城河的复核项。"
    if symbol in symbol_groups.get("core", set()):
        return "这是你的核心仓，需要重点复核是否影响长期假设。"
    if symbol in symbol_groups.get("portfolio", set()):
        return "这是你的持仓，可能影响持仓逻辑。"
    if symbol in symbol_groups.get("watchlist", set()):
        return "这是观察池标的，可能影响买入等待逻辑。"
    return _clean(item.get("relevance_reason_zh")) or "需要人工复核影响。"


def _source_line(item: dict[str, Any]) -> str:
    source = _clean(item.get("source") or item.get("site")) or "未知来源"
    return f"{source} · {_format_time(item.get('published_at'))} · {source_link_text(item)}"


def _news_symbol_label(symbol: object) -> str:
    text = _clean(symbol)
    if not text:
        return "未标明股票"
    if text == "MARKET":
        return "市场新闻"
    return text


def _price_reaction_line(context: dict[str, Any] | None) -> str:
    if not context:
        return "价格反应数据不足"
    label = _clean(context.get("news_price_match_label")) or "价格反应数据不足"
    p1 = _fmt_pct(context.get("price_change_1d"))
    p5 = _fmt_pct(context.get("price_change_5d"))
    explanation = _clean(context.get("explanation")) or "价格变化样本不足，暂不能判断新闻与股价方向。"
    return f"{label}；过去 1 日 {p1}，过去 5 日 {p5}。{explanation}"


def _card_tone(item: dict[str, Any], symbol_groups: dict[str, set[str]]) -> str:
    symbol = _clean(item.get("symbol"))
    sentiment = _sentiment_label(item.get("sentiment_label"))
    impact = _impact_label(item.get("impact_level"))
    event_type = _event_type_label(item.get("event_type"))
    title = f"{item.get('original_title') or item.get('title') or ''} {item.get('summary') or ''}".lower()
    is_owned = symbol in symbol_groups.get("portfolio", set()) or symbol in symbol_groups.get("core", set())
    factual_negative = any(word in title for word in ("downgrade", "cut guidance", "lawsuit", "investigation", "sec", "doj"))
    if event_type == "观点文章" and not factual_negative:
        return "观点文章"
    if sentiment == "负面" and impact == "重大" and is_owned:
        return "重大负面"
    if sentiment == "正面" and impact == "重大":
        return "重大正面"
    if sentiment == "待判断":
        return "待判断"
    return "普通"


def _keywords_text(item: dict[str, Any]) -> str:
    raw = item.get("keywords_hit")
    if isinstance(raw, list):
        values = [str(x) for x in raw if str(x)]
    else:
        try:
            values = json_values = __import__("json").loads(raw or "[]")
            if not isinstance(json_values, list):
                values = []
        except Exception:
            values = [part.strip() for part in str(raw or "").split(",") if part.strip()]
    return "、".join(dict.fromkeys(values))


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _format_time(value: Any) -> str:
    dt = _parse_datetime(value)
    if not dt:
        return "时间缺失"
    hkt = timezone(timedelta(hours=8))
    return dt.astimezone(hkt).strftime("%m-%d %H:%M HKT")


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "价格数据不足"
    try:
        return f"{float(value) * 100:+.2f}%"
    except Exception:
        return "价格数据不足"


def _classification_label(value: Any, labels: dict[str, str], fallback: str) -> str:
    text = _clean(value)
    if not text:
        return fallback
    normalized = text.lower().replace("-", "_").replace(" ", "_")
    return labels.get(normalized, text)


def _event_type_label(value: Any) -> str:
    return _classification_label(value, EVENT_TYPE_LABELS, "待判断")


def _sentiment_label(value: Any) -> str:
    return _classification_label(value, SENTIMENT_LABELS, "待判断")


def _impact_label(value: Any) -> str:
    return _classification_label(value, IMPACT_LABELS, "低")


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"none", "nan", "n/a", "null", "unknown"}:
        return ""
    return text


def _has_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)
