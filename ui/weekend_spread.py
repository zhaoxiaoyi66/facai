from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from data.weekend_spread import build_weekend_spread_rows, load_binance_symbol_mapping
from settings import load_watchlist


RISK_NOTICE = (
    "V1 仅用于周末价差观察，不构成套利建议。Binance 映射价格不等于真实美股可成交价格；"
    "价差可能来自流动性、点差、资金费率、映射误差或币种单位差异。"
)


def render() -> None:
    st.markdown(
        """
        <section class="zhx-page-head">
          <div>
            <span class="zhx-eyebrow">ZHX RESEARCH</span>
            <h1>周末价差观察</h1>
            <p>对照周五美股收盘价与 Binance 映射价格，只做观察和风险提醒。</p>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )
    st.warning(RISK_NOTICE)
    mapping = load_binance_symbol_mapping()
    controls = st.columns([1, 1, 1, 1])
    mapped_only = controls[0].checkbox("仅显示有 mapping", value=False)
    focus_only = controls[1].checkbox("仅重点/异常", value=False)
    abnormal_only = controls[2].checkbox("仅异常价差", value=False)
    force_refresh = controls[3].button("刷新 Binance 数据", width="stretch")

    rows = build_weekend_spread_rows(load_watchlist(), mapping=mapping, force_refresh=force_refresh)
    filtered_rows = _filter_rows(rows, mapped_only=mapped_only, focus_only=focus_only, abnormal_only=abnormal_only)
    configured_count = sum(1 for item in mapping.values() if item.get("enabled", True) and item.get("binance_symbol"))
    st.caption(f"观察池 {len(rows)} 只；已配置 Binance 映射 {configured_count} 只；当前显示 {len(filtered_rows)} 只。")
    st.dataframe(_display_frame(filtered_rows), width="stretch", hide_index=True)
    _render_details(filtered_rows)
    with st.expander("映射与风险说明", expanded=False):
        st.markdown(
            "\n".join(
                [
                    "- 未配置映射的股票显示“暂无映射”，不会请求或伪造 Binance 价格。",
                    "- 映射未确认时会显示“需人工确认映射”，价差只作为观察，不是套利判断。",
                    "- quote_currency 不是 USD/USDT 或 unit_multiplier 不明确时，不计算正式价差。",
                    "- V1 不输出套利建议、买入、卖出或对冲指令。",
                    f"- 当前映射：{escape(_mapping_summary(mapping))}",
                ]
            )
        )


def _filter_rows(
    rows: list[dict],
    *,
    mapped_only: bool,
    focus_only: bool,
    abnormal_only: bool,
) -> list[dict]:
    result = list(rows)
    if mapped_only:
        result = [row for row in result if row.get("binance_symbol")]
    if abnormal_only:
        result = [row for row in result if row.get("alert_level") == "ABNORMAL"]
    elif focus_only:
        result = [row for row in result if row.get("alert_level") in {"FOCUS", "ABNORMAL"}]
    return result


def _display_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "Ticker"),
        ("stock_name", "名称"),
        ("friday_close", "周五收盘"),
        ("friday_close_date", "收盘日期"),
        ("binance_symbol", "Binance 映射"),
        ("binance_market_type", "市场"),
        ("binance_last_price", "Binance 最新"),
        ("spread_pct", "价差"),
        ("spread_direction", "方向"),
        ("alert_level_cn", "提醒"),
        ("mapping_status", "映射状态"),
        ("liquidity_warning", "流动性提示"),
        ("updated_at", "更新时间"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    for money_col in ("周五收盘", "Binance 最新"):
        display[money_col] = display[money_col].map(_money_text)
    display["价差"] = display["价差"].map(_percent_text)
    display["Binance 映射"] = display["Binance 映射"].replace("", "暂无映射")
    display["更新时间"] = display["更新时间"].replace("", "暂缺")
    return display


def _render_details(rows: list[dict]) -> None:
    with st.expander("查看价差详情", expanded=False):
        if not rows:
            st.caption("暂无可展示数据。")
            return
        for row in rows:
            st.markdown(
                f"""
                <section class="weekend-spread-detail">
                  <b>{escape(str(row.get("ticker") or ""))}</b>
                  <span>bid {escape(_money_text(row.get("binance_bid")))} / ask {escape(_money_text(row.get("binance_ask")))}</span>
                  <span>bid-ask {_percent_text(row.get("binance_spread_pct"))}</span>
                  <span>24h volume {escape(_plain_number(row.get("binance_volume_24h")))}</span>
                  <span>funding {_funding_text(row.get("funding_rate"))}</span>
                  <span>{escape(str(row.get("fx_note") or ""))}</span>
                  <small>{escape(str(row.get("mapping_risk") or ""))}</small>
                </section>
                """,
                unsafe_allow_html=True,
            )


def _mapping_summary(mapping: dict[str, dict]) -> str:
    items = []
    for ticker, config in sorted(mapping.items()):
        symbol = str(config.get("binance_symbol") or "").strip().upper()
        if not symbol:
            continue
        confidence = str(config.get("mapping_confidence") or "manual_required")
        items.append(f"{ticker}->{symbol}({confidence})")
    return ", ".join(items) or "暂无"


def _money_text(value: object) -> str:
    try:
        if value in (None, ""):
            return "暂缺"
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "暂缺"


def _percent_text(value: object) -> str:
    try:
        if value in (None, ""):
            return "暂缺"
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "暂缺"


def _funding_text(value: object) -> str:
    try:
        if value in (None, ""):
            return "暂缺"
        return f"{float(value) * 100:+.4f}%"
    except (TypeError, ValueError):
        return "暂缺"


def _plain_number(value: object) -> str:
    try:
        if value in (None, ""):
            return "暂缺"
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "暂缺"
