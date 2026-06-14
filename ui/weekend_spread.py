from __future__ import annotations

from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from data.weekend_spread import (
    DEFAULT_LOCAL_MAPPING_PATH,
    build_mapping_diagnostics,
    build_weekend_spread_rows,
    load_binance_symbol_mapping,
)
from data.weekend_spread_log import (
    build_history_stats,
    generate_weekly_summary,
    get_weekly_log_snapshot,
    record_spread_samples,
    update_monday_outcome,
)
from settings import load_watchlist


RISK_NOTICE = (
    "V1 仅用于周末价差观察和历史统计，不构成套利建议。Binance 映射价格不等于真实美股可成交价格；"
    "价差可能来自流动性、点差、资金费率、映射误差或币种单位差异。"
)


def render() -> None:
    st.markdown(
        """
        <section class="zhx-page-head">
          <div>
            <span class="zhx-eyebrow">ZHX RESEARCH</span>
            <h1>周末价差观察台</h1>
            <p>对照周五美股收盘价与 Binance 映射价格，记录周末峰值，并在周一做信号验证。</p>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )
    st.warning(RISK_NOTICE)

    mapping = load_binance_symbol_mapping()
    force_refresh = st.button("刷新 Binance 价格", width="stretch")
    rows = build_weekend_spread_rows(load_watchlist(), mapping=mapping, force_refresh=force_refresh)
    log_snapshot = get_weekly_log_snapshot()
    mapping_counts = _mapping_counts(rows, mapping)

    _render_kpis(rows, mapping_counts, log_snapshot)
    _render_data_status(rows, mapping_counts, DEFAULT_LOCAL_MAPPING_PATH)

    controls = st.columns([1.3, 1, 1, 1])
    scope = controls[0].radio("显示范围", ["重点/有数据", "全部观察池", "暂无 mapping"], index=0, horizontal=True)
    confirmed_only = controls[1].checkbox("仅 confirmed", value=False)
    focus_only = controls[2].checkbox("仅重点/异常", value=False)
    abnormal_only = controls[3].checkbox("仅异常价差", value=False)

    filtered_rows = _filter_rows(
        rows,
        scope=scope,
        confirmed_only=confirmed_only,
        focus_only=focus_only,
        abnormal_only=abnormal_only,
    )
    st.caption(
        f"观察池 {mapping_counts['universe_total']} 只；"
        f"观察池映射 {mapping_counts['universe_mapping_count']} 只；"
        f"本地配置映射 {mapping_counts['local_mapping_count']} 只；"
        f"主表行数 {len(filtered_rows)}。"
    )
    st.dataframe(_display_frame(filtered_rows), width="stretch", hide_index=True)

    no_mapping_rows = [row for row in rows if not row.get("binance_symbol")]
    if no_mapping_rows and scope != "暂无 mapping":
        with st.expander(f"暂无 mapping 股票（{len(no_mapping_rows)}）", expanded=False):
            st.dataframe(_display_frame(no_mapping_rows), width="stretch", hide_index=True)

    _render_recording_controls(rows)
    _render_weekly_outcomes(log_snapshot)
    _render_history_stats()
    _render_details(filtered_rows)
    _render_mapping_diagnostics(mapping)
    with st.expander("映射与风险说明", expanded=False):
        st.markdown(
            "\n".join(
                [
                    "- 未配置映射的股票显示“暂无映射”，不会请求或伪造 Binance 价格。",
                    "- 映射未确认时会显示“需人工确认映射”，价差只作观察，不是套利判断。",
                    "- quote_currency 不是 USD/USDT 或 unit_multiplier 不明确时，不计算正式价差。",
                    "- 周一验证结果只叫“信号验证结果”，不是确定套利成功。",
                    "- V1 不输出套利建议、买入、卖出或对冲指令。",
                    f"- 当前映射：{escape(_mapping_summary(mapping))}",
                ]
            )
        )


def _render_kpis(rows: list[dict], mapping_counts: dict[str, int], log_snapshot: dict) -> None:
    abnormal_count = sum(1 for row in rows if row.get("alert_level") == "ABNORMAL")
    status = _binance_status_text(rows, mapping_counts["universe_mapping_count"])
    cols = st.columns(6)
    cols[0].metric("本周已记录样本", int(log_snapshot.get("sample_count") or 0))
    cols[1].metric("观察池映射", f"{mapping_counts['universe_mapping_count']} / {mapping_counts['universe_total']}")
    cols[2].metric("本周最大溢价", _percent_text(log_snapshot.get("max_premium_pct")))
    cols[3].metric("本周最大折价", _percent_text(log_snapshot.get("max_discount_pct")))
    cols[4].metric("异常价差", abnormal_count)
    cols[5].metric("Binance 数据", status)


def _render_data_status(rows: list[dict], mapping_counts: dict[str, int], local_mapping_path: Path) -> None:
    latest = _latest_updated_at(rows)
    spot_status = _market_data_status(rows, "spot")
    futures_status = _market_data_status(rows, "usdm_futures")
    local_text = "local mapping 已加载" if local_mapping_path.exists() else "未发现 local mapping"
    st.info(
        " | ".join(
            [
                f"Spot 价格源：{spot_status}",
                "Spot 候选扫描：按需诊断",
                f"Futures 数据源：{futures_status}",
                f"本地配置映射总数：{mapping_counts['local_mapping_count']}",
                local_text,
                f"最后刷新：{latest or '暂缺'}",
            ]
        )
    )


def _render_recording_controls(rows: list[dict]) -> None:
    st.subheader("本周记录")
    cols = st.columns(2)
    if cols[0].button("记录当前快照", width="stretch"):
        samples = record_spread_samples(rows)
        st.success(f"已记录 {len(samples)} 条有映射快照。")
    if cols[1].button("生成本周总结", width="stretch"):
        summaries = generate_weekly_summary()
        st.success(f"已生成 {len(summaries)} 条本周总结。")


def _render_weekly_outcomes(log_snapshot: dict) -> None:
    summaries = list(log_snapshot.get("summaries") or [])
    with st.expander("周一验证", expanded=bool(summaries)):
        if not summaries:
            st.caption("本周还没有总结。先记录快照并生成本周总结。")
            return
        st.dataframe(_summary_frame(summaries), width="stretch", hide_index=True)
        labels = [f"{item.get('ticker')} | {item.get('week_id')}" for item in summaries]
        selected = st.selectbox("选择验证标的", labels)
        selected_summary = summaries[labels.index(selected)]
        reference_type = st.selectbox(
            "验证价格类型",
            ["MONDAY_PREMARKET_OPEN", "MONDAY_RTH_OPEN", "MONDAY_OVERNIGHT_OPEN", "MANUAL"],
        )
        monday_price = st.number_input("周一验证价（非 Binance 实时价）", min_value=0.0, value=0.0, step=0.01)
        estimated_cost_pct = st.number_input("估算成本（%）", min_value=0.0, value=0.0, step=0.05)
        notes = st.text_input("验证备注", value="")
        if st.button("保存周一验证", width="stretch"):
            if monday_price <= 0:
                st.warning("请输入有效的周一验证价。")
            else:
                updated = update_monday_outcome(
                    str(selected_summary.get("ticker") or ""),
                    week_id=str(selected_summary.get("week_id") or ""),
                    monday_reference_price=monday_price,
                    reference_type=reference_type,
                    estimated_cost_pct=estimated_cost_pct,
                    notes=notes,
                )
                if updated:
                    st.success(f"已保存信号验证结果：{updated.get('outcome_status')}")
                else:
                    st.warning("未找到可更新的本周总结。")


def _render_history_stats() -> None:
    with st.expander("历史规律", expanded=False):
        stats = build_history_stats()
        if not stats:
            st.caption("暂无历史验证记录。")
            return
        st.dataframe(_history_frame(stats), width="stretch", hide_index=True)


def _filter_rows(
    rows: list[dict],
    *,
    scope: str,
    confirmed_only: bool,
    focus_only: bool,
    abnormal_only: bool,
) -> list[dict]:
    if scope == "暂无 mapping":
        result = [row for row in rows if not row.get("binance_symbol")]
    elif scope == "全部观察池":
        result = list(rows)
    else:
        result = [
            row
            for row in rows
            if row.get("binance_symbol") or row.get("spread_pct") is not None or row.get("alert_level") in {"FOCUS", "ABNORMAL"}
        ]
    if confirmed_only:
        result = [row for row in result if row.get("mapping_confidence") == "confirmed"]
    if abnormal_only:
        result = [row for row in result if row.get("alert_level") == "ABNORMAL"]
    elif focus_only:
        result = [row for row in result if row.get("alert_level") in {"FOCUS", "ABNORMAL"}]
    return result


def _mapping_counts(rows: list[dict], mapping: dict[str, dict]) -> dict[str, int]:
    local_mapping_count = sum(1 for item in mapping.values() if item.get("enabled", True) and item.get("binance_symbol"))
    universe_mapping_count = sum(1 for row in rows if row.get("binance_symbol"))
    return {
        "local_mapping_count": local_mapping_count,
        "universe_mapping_count": universe_mapping_count,
        "universe_total": len(rows),
    }


def _display_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "Ticker"),
        ("friday_close", "周五收盘"),
        ("friday_close_date", "收盘日期"),
        ("binance_symbol", "Binance 映射"),
        ("binance_last_price", "Binance 最新"),
        ("spread_pct", "价差"),
        ("spread_direction", "方向"),
        ("alert_level_cn", "提醒"),
        ("mapping_status", "映射状态"),
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


def _summary_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "Ticker"),
        ("week_id", "周"),
        ("sample_count", "样本数"),
        ("max_premium_pct", "最大溢价"),
        ("max_discount_pct", "最大折价"),
        ("max_abs_spread_pct", "最大绝对价差"),
        ("max_abs_spread_direction", "峰值方向"),
        ("monday_gap_pct", "周一跳空"),
        ("capture_ratio", "捕捉比例"),
        ("net_edge_pct", "净边际"),
        ("outcome_status", "信号验证结果"),
        ("data_quality", "数据质量"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    for col in ("最大溢价", "最大折价", "最大绝对价差", "周一跳空", "净边际"):
        display[col] = display[col].map(_percent_text)
    display["捕捉比例"] = display["捕捉比例"].map(_ratio_text)
    return display


def _history_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "Ticker"),
        ("sample_weeks", "样本周数"),
        ("hit_count", "HIT"),
        ("partial_count", "PARTIAL"),
        ("miss_count", "MISS"),
        ("hit_rate", "命中率"),
        ("avg_max_abs_spread_pct", "平均最大价差"),
        ("avg_capture_ratio", "平均捕捉比例"),
        ("avg_net_edge_pct", "平均净边际"),
        ("common_failure_reason", "常见失败原因"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    display["命中率"] = display["命中率"].map(_ratio_text)
    display["平均最大价差"] = display["平均最大价差"].map(_percent_text)
    display["平均捕捉比例"] = display["平均捕捉比例"].map(_ratio_text)
    display["平均净边际"] = display["平均净边际"].map(_percent_text)
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
                  <small>{escape(str(row.get("error") or ""))}</small>
                </section>
                """,
                unsafe_allow_html=True,
            )


def _render_mapping_diagnostics(mapping: dict[str, dict]) -> None:
    with st.expander("映射诊断", expanded=False):
        validate = st.button("校验 symbol 映射", width="stretch")
        diagnostics = build_mapping_diagnostics(
            load_watchlist(),
            mapping=mapping,
            validate=validate,
            include_candidates=validate,
        )
        st.dataframe(_diagnostics_frame(diagnostics), width="stretch", hide_index=True)
        if validate:
            st.caption("候选 symbol 仅表示 Binance 上存在相似代码，不代表真实映射美股，需要人工确认。")


def _diagnostics_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "Ticker"),
        ("configured_symbol", "配置 symbol"),
        ("market_type", "市场"),
        ("mapping_confidence", "映射置信"),
        ("validation_status", "校验状态"),
        ("last_validated_at", "校验时间"),
        ("price_available", "价格"),
        ("book_available", "买卖盘"),
        ("volume_available", "成交量"),
        ("funding_available", "资金费率"),
        ("candidate_scan_status", "候选扫描"),
        ("candidate_scan_message", "候选说明"),
        ("risk_note", "风险备注"),
        ("candidates", "候选"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    for col in ("价格", "买卖盘", "成交量", "资金费率"):
        display[col] = display[col].map(lambda value: "可用" if bool(value) else "暂缺")
    display["候选"] = display["候选"].map(_candidate_text)
    display["配置 symbol"] = display["配置 symbol"].replace("", "暂无映射")
    display["校验时间"] = display["校验时间"].replace("", "未校验")
    return display


def _candidate_text(value: object) -> str:
    if not isinstance(value, list) or not value:
        return ""
    labels = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "")
        market_type = str(item.get("market_type") or "")
        quote = str(item.get("quote_currency") or "")
        if symbol:
            labels.append(f"{symbol}({market_type}/{quote})")
    return ", ".join(labels)


def _mapping_summary(mapping: dict[str, dict]) -> str:
    items = []
    for ticker, config in sorted(mapping.items()):
        symbol = str(config.get("binance_symbol") or "").strip().upper()
        if not symbol:
            continue
        confidence = str(config.get("mapping_confidence") or "manual_required")
        items.append(f"{ticker}->{symbol}({confidence})")
    return ", ".join(items) or "暂无"


def _binance_status_text(rows: list[dict], universe_mapping_count: int) -> str:
    if universe_mapping_count <= 0:
        return "观察池暂无映射"
    if any(row.get("status") == "OK" for row in rows):
        return "可用"
    if any(row.get("status") == "BINANCE_UNAVAILABLE" for row in rows):
        return "数据不可用"
    return "待确认"


def _market_data_status(rows: list[dict], market_type: str) -> str:
    market_rows = [row for row in rows if row.get("binance_market_type") == market_type and row.get("binance_symbol")]
    if not market_rows:
        return "暂无映射"
    if any(row.get("status") == "OK" for row in market_rows):
        return "可用"
    if any(row.get("status") == "BINANCE_UNAVAILABLE" for row in market_rows):
        return "数据不可用"
    if any(row.get("status") == "INVALID_SYMBOL" for row in market_rows):
        return "symbol 无效"
    return "待确认"


def _latest_updated_at(rows: list[dict]) -> str:
    values = [str(row.get("updated_at") or "") for row in rows if row.get("updated_at")]
    return max(values) if values else ""


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


def _ratio_text(value: object) -> str:
    try:
        if value in (None, ""):
            return "暂缺"
        return f"{float(value) * 100:.1f}%"
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
