from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from data.weekend_spread import build_weekend_spread_rows, load_binance_symbol_mapping
from settings import load_watchlist


RISK_NOTICE = (
    "Binance 映射价格不等于真实美股可成交价格；周末价差可能来自流动性、点差、资金费率、映射误差。"
    "V1 仅用于观察，不构成套利建议。"
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
    rows = build_weekend_spread_rows(load_watchlist(), mapping=mapping)
    st.caption(f"观察池 {len(rows)} 只；已配置 Binance 映射 {len(mapping)} 只。")
    st.dataframe(_display_frame(rows), width="stretch", hide_index=True)
    with st.expander("映射与风险说明", expanded=False):
        st.markdown(
            "\n".join(
                [
                    "- 未配置映射的股票显示“暂无映射”，不会请求或伪造 Binance 价格。",
                    "- Binance 映射资产不等于美股现货，不能当作真实可成交价格。",
                    "- V1 不输出套利建议、买入、卖出或对冲指令。",
                    f"- 当前映射：{escape(', '.join(f'{k}->{v}' for k, v in sorted(mapping.items())) or '暂无')}",
                ]
            )
        )


def _display_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "Ticker"),
        ("stock_name", "名称"),
        ("friday_close", "周五收盘"),
        ("binance_symbol", "Binance 映射"),
        ("binance_last_price", "Binance 最新"),
        ("spread_pct", "价差"),
        ("spread_direction", "方向"),
        ("alert_level_cn", "提醒"),
        ("liquidity_warning", "流动性提示"),
        ("mapping_risk", "映射风险"),
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
