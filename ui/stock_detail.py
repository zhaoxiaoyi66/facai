from __future__ import annotations

from datetime import datetime
from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from buy_zone_engine import (
    BuyZoneEstimate,
    buy_zone_with_manual_override,
    clear_buy_zone_override_values,
    effective_buy_zone_plan,
    generate_buy_zone,
    has_buy_zone_override,
)
from data.decision_log import save_decision_snapshot_from_bundle
from data.fundamentals import FundamentalCache
from data.disclosure_pipeline import DisclosurePipeline
from data.portfolio_view_model import build_portfolio_view_model
from data.providers import get_market_data_provider
from data.stock_plan import StockPlanStore
from formatting import format_compact_number, format_currency, format_large_number, format_multiple, format_percent
from indicators.technicals import add_technical_indicators, latest_technical_snapshot
from position_plan_engine import PositionPlanSuggestion, generate_position_plan
from scoring.final_decision_adapter import build_final_decision_bundle
from scoring.metric_sources import fcf_margin_metric, fcf_margin_source_note
from scoring.total_score import calculate_total_score
from settings import load_watchlist
from ui import dashboard as dashboard_ui
from ui.metric_labels import (
    action_label,
    confidence_label,
    metric_label,
    model_type_label,
    resolution_status_label,
    source_type_label,
)
from ui.theme import render_section_title


MANUAL_TEXT = "建议人工复核"
NEAR_BUY_TRIGGER_THRESHOLD_PCT = 15.0
FAIR_OBSERVATION_NOT_BUY_LABEL = "合理观察，未到买点"
DEEP_DISCOUNT_ZONE_LABEL = "极端恐慌区"


def render() -> None:
    _render_detail_styles()
    _render_research_header()

    ticker = _select_ticker()
    if not ticker:
        st.warning("请输入股票代码后继续。")
        return

    control_cols = st.columns([1, 1, 5])
    refresh_token_key = f"stock_detail_refresh_token_{ticker}"
    with control_cols[0]:
        if st.button("刷新此股票", key=f"stock-detail-refresh-{ticker}", width="stretch"):
            st.session_state[refresh_token_key] = datetime.now().isoformat()
            st.session_state["selected_detail_symbol"] = ticker
            st.rerun()
    record_signal_slot = control_cols[1].empty()

    with st.spinner(f"正在加载 {ticker} 详情..."):
        snapshot, history, technicals, score, refreshed_at = _load_detail(ticker, st.session_state.get(refresh_token_key))

    plan_store = StockPlanStore()
    plan = plan_store.get_plan(ticker)
    stock_data = {**snapshot, **technicals}
    buy_zone = generate_buy_zone(ticker, stock_data, score, score.scoring_model)
    effective_buy_zone = buy_zone_with_manual_override(buy_zone, plan)
    effective_plan = effective_buy_zone_plan(plan, effective_buy_zone)
    plan_suggestion = generate_position_plan(ticker, effective_buy_zone, score)
    final_decision = build_final_decision_bundle(score, buy_zone, manual_plan_override=plan, symbol=ticker)

    with record_signal_slot.container():
        _render_record_signal_button(ticker, snapshot, technicals, final_decision)
    _render_conclusion_card(ticker, snapshot, technicals, score, refreshed_at, effective_buy_zone, final_decision)
    _render_current_position_summary(_portfolio_row_for_ticker(ticker))
    _render_decision_summary(score, effective_buy_zone, plan_suggestion, final_decision)
    _render_buy_zone(ticker, plan_store, plan, effective_buy_zone, buy_zone, score)
    _render_action_plan_form(ticker, plan_store, plan, plan_suggestion, effective_buy_zone, final_decision)
    _render_research_memo(ticker, plan_store, plan)
    _render_explanation_cards(score, snapshot, technicals, effective_plan)
    _render_industry_metrics(score.scoring_model, snapshot, score)
    _render_missing_data_notice(ticker, score, snapshot)
    _render_manual_override_form(ticker, snapshot, score, technicals, history)
    _render_raw_metrics(snapshot, history, technicals, score, ticker)


def _render_research_header() -> None:
    st.markdown(
        """
        <div class="stock-research-header">
            <div>
                <p>个股研究</p>
                <span>先看结论，再看买区、评分和数据可信度。</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _select_ticker() -> str:
    tickers = load_watchlist()
    symbol_from_query = str(st.query_params.get("symbol", "")).strip().upper()
    if symbol_from_query:
        st.session_state["stock_detail_symbol"] = symbol_from_query
    selected_from_dashboard = st.session_state.pop("selected_detail_symbol", None)
    if selected_from_dashboard:
        st.session_state["stock_detail_symbol"] = selected_from_dashboard

    default_symbol = st.session_state.get("stock_detail_symbol") or (tickers[0] if tickers else "")
    if default_symbol and default_symbol not in tickers:
        tickers = [default_symbol, *tickers]

    selected = ""
    selector_cols = st.columns([1.1, 1.1, 3.8])
    if tickers:
        index = tickers.index(default_symbol) if default_symbol in tickers else 0
        with selector_cols[0]:
            selected = st.selectbox("股票选择器", tickers, index=index, key="stock_detail_symbol", label_visibility="collapsed")
    with selector_cols[1]:
        custom = st.text_input("手动输入股票代码", value="", placeholder="例如 VST / NOW / NVDA", label_visibility="collapsed")
    return (custom or selected or "").strip().upper()


def _render_conclusion_card(
    ticker: str,
    snapshot: dict,
    technicals: dict,
    score,
    refreshed_at: str | None,
    buy_zone: BuyZoneEstimate | None = None,
    final_decision=None,
) -> None:
    company = snapshot.get("company_name") or ticker
    price = technicals.get("price") or snapshot.get("current_price")
    data_status = _data_status(score, snapshot)
    refreshed = _format_timestamp(refreshed_at)
    buy_point_html = _buy_point_status_pill_html(score, buy_zone)
    values = [
        ("操作建议", _final_action_text(score, final_decision)),
        ("当前新增", _position_limit_text(_final_current_add(score, final_decision))),
        ("组合仓位上限", _position_limit_text(_final_max_position(score, final_decision))),
        ("当前价格", format_currency(price)),
        ("市值", format_large_number(snapshot.get("market_cap"))),
        ("行业模型", model_type_label(score.scoring_model)),
        ("质量评级", score.quality_rating),
        ("买点状态", buy_point_html, True),
        ("风险评级", score.risk_rating),
        ("数据状态", data_status),
        ("最近刷新", refreshed),
    ]

    st.markdown(
        '<section class="detail-hero">'
        '<div>'
        f'<div class="detail-eyebrow">{escape(str(company))}</div>'
        f'<h2>{escape(ticker)}</h2>'
        f'<p>{escape(str(snapshot.get("sector") or "未知板块"))} · {escape(str(snapshot.get("industry") or "未知行业"))}</p>'
        "</div>"
        '<div class="detail-hero-grid">'
        + "".join(_hero_item_html(label, value, raw_html=bool(rest and rest[0])) for label, value, *rest in values)
        + "</div>"
        "</section>",
        unsafe_allow_html=True,
    )


def _render_record_signal_button(ticker: str, snapshot: dict, technicals: dict, final_decision) -> None:
    if st.button("记录当前信号", key=f"stock-detail-record-signal-{ticker}", width="stretch"):
        price = _first_number(technicals.get("price"), snapshot.get("current_price"), snapshot.get("price"))
        save_decision_snapshot_from_bundle(ticker, price, final_decision, "stock_detail")
        st.success("已记录系统信号。")


def _portfolio_row_for_ticker(ticker: str) -> dict | None:
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        return None
    try:
        rows = build_portfolio_view_model().get("rows", [])
    except Exception:
        return None
    return next((row for row in rows if str(row.get("symbol") or "").upper() == symbol), None)


def _render_current_position_summary(row: dict | None) -> None:
    if not row:
        return
    items = [
        ("持股数量", _portfolio_quantity_text(row.get("quantity"))),
        ("平均成本", _portfolio_money_text(row.get("averageCost"))),
        ("当前盈亏", _portfolio_pnl_text(row)),
        ("仓位占比", _portfolio_percent_text(row.get("positionPct"))),
        ("系统参考", _portfolio_system_reference_text(row)),
    ]
    st.markdown(
        '<section class="current-position-strip">'
        '<div class="current-position-title"><span>当前持仓</span><strong>'
        + escape(str(row.get("symbol") or ""))
        + "</strong></div>"
        + '<div class="current-position-grid">'
        + "".join(
            f'<div class="current-position-item"><span>{escape(label)}</span><b>{escape(value)}</b></div>'
            for label, value in items
        )
        + "</div></section>",
        unsafe_allow_html=True,
    )


def _portfolio_pnl_text(row: dict) -> str:
    return f"{_portfolio_money_text(row.get('unrealizedPnl'))} / {_portfolio_percent_text(row.get('unrealizedPnlPct'))}"


def _portfolio_system_reference_text(row: dict) -> str:
    action = _portfolio_system_action_text(row)
    max_position = _portfolio_percent_text(row.get("systemMaxPosition"))
    reason = _portfolio_reason_text(row)
    if reason != "—":
        return f"{action} · 上限 {max_position} · {reason}"
    return f"{action} · 上限 {max_position}"


def _portfolio_system_action_text(row: dict) -> str:
    lane = str(row.get("decisionLane") or "").strip()
    action = str(row.get("systemAction") or "").strip()
    if row.get("overweightSystem"):
        return "超系统上限"
    if lane == "review":
        return "待复核"
    if lane == "blocked":
        return "禁止追高"
    if lane == "actionable":
        return "可加仓"
    if lane == "wait":
        return "只观察"
    return action or "未生成"


def _portfolio_reason_text(row: dict) -> str:
    reasons = [*_portfolio_translated_reasons(row.get("blockReasons")), *_portfolio_translated_reasons(row.get("reviewReasons"))]
    return "，".join(reasons[:2]) if reasons else "—"


def _portfolio_translated_reasons(value: object) -> list[str]:
    labels = {
        "buy_zone": "买区阻断",
        "data_confidence": "数据置信度",
        "valuation_status": "估值状态",
        "entry_rating": "入场评级",
        "risk_rating": "风险评级",
    }
    items = value if isinstance(value, list) else []
    return [labels.get(str(item), str(item)) for item in items]


def _portfolio_quantity_text(value: object) -> str:
    number = _optional_float(value)
    if number is None:
        return "—"
    return f"{number:,.4g}"


def _portfolio_money_text(value: object) -> str:
    number = _optional_float(value)
    return format_currency(number) if number is not None else "—"


def _portfolio_percent_text(value: object) -> str:
    number = _optional_float(value)
    return format_percent(number) if number is not None else "—"


def _optional_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(*values) -> float | None:
    for value in values:
        number = _optional_float(value)
        if number is not None:
            return number
    return None


def _render_explanation_cards(score, snapshot: dict, technicals: dict, plan: dict) -> None:
    render_section_title("评分解释", "为什么是这个结论")
    columns = st.columns(3)

    with columns[0]:
        _explain_card(
            "公司质量解释",
            score.quality_rating,
            "主要加分项",
            score.key_positives or [],
            "主要扣分项",
            _quality_penalties(score, snapshot),
            score.missing_data or [],
        )

    with columns[1]:
        buy_reasons = _entry_explanation(score, snapshot, technicals, plan)
        _explain_card(
            "买点解释",
            _buy_point_status_text(score),
            "当前判断",
            buy_reasons,
            "需要等待",
            _entry_wait_items(score, technicals, plan),
            score.missing_data or [],
        )

    with columns[2]:
        risk_reasons = _risk_explanation(score)
        _explain_card(
            "风险解释",
            score.risk_rating,
            "主要风险",
            risk_reasons,
            "风险来源",
            _risk_source_labels(score),
            score.missing_data or [],
        )


def _explain_card(
    title: str,
    rating: str,
    positive_title: str,
    positives: list[str],
    risk_title: str,
    risks: list[str],
    missing: list[str],
) -> None:
    with st.container(border=True):
        st.markdown(f"#### {title}")
        st.markdown(_pill_html(rating, _rating_color(rating)), unsafe_allow_html=True)
        st.caption(positive_title)
        _render_short_list(positives[:3], "暂无明显加分项")
        st.caption(risk_title)
        _render_short_list(risks[:3], "暂无明显扣分项")
        st.caption("缺失数据项")
        st.caption(_missing_summary_text(missing))
        with st.expander("展开详情", expanded=False):
            st.caption(positive_title)
            _render_short_list(positives, "暂无明显加分项")
            st.caption(risk_title)
            _render_short_list(risks, "暂无明显扣分项")
            st.caption("缺失数据项")
            _render_short_list(missing, "关键字段可用")


def _missing_summary_text(missing: list[str]) -> str:
    if not missing:
        return "缺失 0 项"
    return f"缺失 {len(missing)} 项"


def _render_missing_data_notice(ticker: str, score, snapshot: dict) -> None:
    render_section_title("数据可信度与缺口", "按影响程度汇总，细节默认折叠")
    missing_summary = _missing_data_summary(score, snapshot)
    confidence = confidence_label(score.data_confidence or snapshot.get("dataConfidence") or "N/A")
    if missing_summary:
        st.markdown(_missing_data_summary_html(confidence, missing_summary), unsafe_allow_html=True)
        groups = _missing_gap_groups(score, missing_summary)
        st.markdown(_missing_gap_groups_html(groups, limit=3), unsafe_allow_html=True)
        if any(len(group["items"]) > 3 for group in groups):
            with st.expander("展开更多缺口分组", expanded=False):
                st.markdown(_missing_gap_groups_html(groups, limit=None), unsafe_allow_html=True)
    else:
        summary = snapshot.get("disclosureReviewSummary")
        pending = summary.get("pending_review", 0) if isinstance(summary, dict) else 0
        approved = summary.get("approved", 0) if isinstance(summary, dict) else 0
        missing_count = len(score.missing_data or [])
        st.markdown(
            _data_summary_html(
                confidence,
                pending,
                approved,
                missing_count,
            ),
            unsafe_allow_html=True,
        )
        st.caption("当前未读取到 missingDataSummary，已使用旧版数据状态摘要。")

    with st.expander("查看数据缺口明细", expanded=False):
        _render_missing_data_details(ticker, score, snapshot)


def _render_missing_data_details(ticker: str, score, snapshot: dict) -> None:
    st.caption("区分真缺失、未披露、供应商没有、需要 IR 抓取和需要分析师预期。")
    missing = score.missing_data or []
    missing_impacts = getattr(score, "missing_metric_impacts", None) or getattr(score, "missingMetricImpact", [])
    confidence = score.data_confidence or snapshot.get("dataConfidence")
    confidence_pct = snapshot.get("dataConfidencePct")
    if confidence:
        st.caption(
            f"数据置信度：{confidence_label(confidence)}"
            + (f"（{confidence_pct}%）" if confidence_pct is not None else "")
            + f"；代理置信度：{confidence_label(score.proxy_confidence)}；建议仓位上限：{_position_limit_text(score.max_suggested_position_percent)}"
        )

    proxy_rows = _proxy_status_rows(score)
    if proxy_rows:
        st.dataframe(pd.DataFrame(proxy_rows), hide_index=True, width="stretch")

    category_rows = _data_status_rows(snapshot)
    if category_rows:
        st.dataframe(pd.DataFrame(category_rows), hide_index=True, width="stretch")

    impact_rows = _missing_impact_rows(missing_impacts)
    fundamental_rows = _missing_impact_rows([row for row in missing_impacts if isinstance(row, dict) and row.get("affects") != "Technical"])
    technical_rows = _missing_impact_rows([row for row in missing_impacts if isinstance(row, dict) and row.get("affects") == "Technical"])

    if fundamental_rows:
        st.info(f"{ticker} 当前使用 {model_type_label(score.scoring_model)} 模型；缺失字段按影响等级展示，缺失不等于直接扣分。")
        st.dataframe(pd.DataFrame(fundamental_rows), hide_index=True, width="stretch")

    if technical_rows:
        st.caption("技术指标未计算项：不进入基本面缺失表，也不影响公司质量评级。")
        st.dataframe(pd.DataFrame(technical_rows), hide_index=True, width="stretch")

    if not missing and not category_rows and not impact_rows:
        st.success(f"{ticker} 当前 {model_type_label(score.scoring_model)} 模型的核心评分字段可用。")
        return

    if missing and not impact_rows:
        st.info(f"{ticker} 当前使用 {model_type_label(score.scoring_model)} 模型，部分字段缺失。缺失不会直接打 D，但会限制评分上限或降低置信度。")
        rows = [
            {
                "评分缺口": metric_label(item),
                "影响评分": _missing_impact(item),
                "处理建议": "建议人工复核或抓取IR资料" if _needs_manual_override(item, score.scoring_model) else "等待数据源补齐",
            }
            for item in missing[:12]
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _proxy_status_rows(score) -> list[dict]:
    rows: list[dict] = []
    if score.missing_industry_metrics:
        rows.append(
            {
                "分类": "缺少行业专属指标",
                "状态": confidence_label(score.proxy_confidence),
                "字段": "、".join(metric_label(item) for item in score.missing_industry_metrics),
                "说明": "当前未把代理指标当作完整行业数据",
            }
        )
    if score.proxy_metrics_used:
        rows.append(
            {
                "分类": "已使用代理指标",
                "状态": confidence_label(score.proxy_confidence),
                "字段": "、".join(metric_label(item) for item in score.proxy_metrics_used),
                "说明": "用于避免直接数据不足，但会降低 proxyConfidence",
            }
        )
    return rows


def _render_industry_metrics(model_type: str, snapshot: dict, score) -> None:
    render_section_title("行业专属指标", model_type_label(model_type))
    rows = _core_industry_metric_rows(model_type, snapshot, score)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _core_industry_metric_rows(model_type: str, snapshot: dict, score) -> list[dict]:
    if model_type == "SAAS_SOFTWARE":
        fcf_metric = fcf_margin_metric(snapshot)
        return [
            _metric_row("收入增速", format_percent(snapshot.get("revenue_growth"), already_percent=False), "核心增长"),
            _metric_row("FCF利润率", format_percent(fcf_metric.value, already_percent=False), fcf_margin_source_note(snapshot)),
            _metric_row("P/FCF", format_multiple(snapshot.get("price_to_fcf")), "估值"),
            _metric_row("FCF收益率", format_percent(snapshot.get("free_cash_flow_yield"), already_percent=False), "估值"),
            _metric_row("RPO / cRPO 增速", _manual_percent_metric(snapshot, "manualRpoGrowth", "rpo_growth"), "无则需人工补充"),
            _metric_row("SBC / revenue", _manual_percent_metric(snapshot, "manualSbcRatio", "sbc_ratio", "stock_based_compensation_ratio"), "无则需人工补充"),
        ]
    return _industry_metric_rows(model_type, snapshot, score)[:6]


def _render_manual_override_form(ticker: str, snapshot: dict, score, technicals: dict | None = None, history: pd.DataFrame | None = None) -> None:
    with st.expander("数据自动补齐 / 人工补充", expanded=False):
        st.caption("优先从 SEC、8-K、IR release 和 transcript 补行业专属指标。")
        _render_disclosure_pipeline_controls(ticker, snapshot, score, technicals, history)
        _render_sec_supplement_summary(snapshot)

        fields = _manual_override_fields(score.scoring_model)
        if not fields:
            st.caption("当前模型暂未配置专属人工补充表单。")
            return

        with st.form(f"manual-overrides-{ticker}"):
            st.caption("百分比字段可填 0.15 或 15%，系统会自动按比例保存。")
            values: dict[str, str] = {}
            columns = st.columns(2)
            for index, (label, key, is_percent) in enumerate(fields):
                with columns[index % 2]:
                    values[key] = st.text_input(label, value=_number_text(snapshot.get(key)), key=f"{ticker}-{key}")
                    if is_percent:
                        st.caption("按比例保存，例如 0.18 = 18%")

            notes = st.text_area("人工备注", value=snapshot.get("manualNarrativeNotes") or "", key=f"{ticker}-manual-notes")
            submitted = st.form_submit_button("保存补充数据", width="stretch")
            if submitted:
                parsed = {
                    key: _parse_optional_number(values.get(key, ""), is_percent=is_percent)
                    for _, key, is_percent in fields
                }
                parsed["manualNarrativeNotes"] = notes.strip() or None
                FundamentalCache().set_manual_overrides(ticker, **parsed)
                st.session_state[f"stock_detail_refresh_token_{ticker}"] = datetime.now().isoformat()
                st.success("已保存补充数据，评分会使用新的 manual override。")
                st.rerun()


def _render_disclosure_pipeline_controls(
    ticker: str,
    snapshot: dict,
    score,
    technicals: dict | None = None,
    history: pd.DataFrame | None = None,
) -> None:
    result_key = f"disclosure-pipeline-result-{ticker}"
    columns = st.columns([1.2, 3.8])
    with columns[0]:
        if st.button("自动补齐数据", key=f"auto-disclosure-{ticker}", width="stretch"):
            with st.status(f"正在自动补齐 {ticker} 数据...", expanded=True) as status:
                st.write("FMP：读取当前页面已有结构化字段")
                st.write("Calculated：自动计算 SBC/revenue、净债务、利息覆盖、FCF margin 和技术指标")
                st.write("SEC：抓取 companyfacts 和最近 filings")
                st.write("8-K / IR：扫描 Exhibit 99.1、earnings release / presentation 链接")
                st.write("Transcript：只在明确出现指标和数字时低置信度保存")
                result = DisclosurePipeline().run(
                    ticker,
                    model_type=score.scoring_model,
                    current_snapshot=snapshot,
                    current_technicals=technicals,
                    price_history=history,
                    force_refresh=True,
                )
                status.update(label=f"{ticker} 自动补齐完成", state="complete")
            st.session_state[result_key] = result
            st.session_state[f"stock_detail_refresh_token_{ticker}"] = datetime.now().isoformat()
            st.rerun()

    result = st.session_state.get(result_key)
    if result:
        saved = result.get("saved") or []
        missing = result.get("missing") or []
        not_disclosed = result.get("notDisclosed") or []
        resolutions = result.get("resolutions") or []
        message = f"上次自动补齐保存 {len(saved)} 条指标"
        if missing:
            message += f"，仍缺 {len(missing)} 项"
        if not_disclosed:
            message += f"，公司未披露 {len(not_disclosed)} 项"
        columns[1].success(message)
        if saved:
            preview = pd.DataFrame(
                [
                    {
                        "字段": metric_label(item.get("displayName") or item.get("metricKey")),
                        "数值": _format_disclosure_value(item.get("value"), item.get("unit")),
                        "期间": item.get("period") or "N/A",
                        "来源": source_type_label(item.get("sourceType")),
                        "置信度": confidence_label(item.get("confidence")),
                    }
                    for item in saved[:8]
                ]
            )
            st.dataframe(preview, hide_index=True, width="stretch")
        if resolutions:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "指标": metric_label(item.get("displayName") or item.get("metricKey")),
                            "状态": resolution_status_label(item.get("status")),
                            "尝试来源": source_type_label(item.get("sourceTried")),
                            "说明": item.get("reason"),
                            "建议动作": action_label(item.get("recommendedAction")),
                        }
                        for item in resolutions[:12]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )


def _render_sec_supplement_summary(snapshot: dict) -> None:
    status = snapshot.get("sec_supplement_status")
    note = snapshot.get("sec_supplement_note")
    rows = [
        {"自动补充项": "SEC收入", "数值": format_large_number(snapshot.get("sec_revenue")), "来源": "SEC companyfacts"},
        {"自动补充项": metric_label("GAAP operating margin"), "数值": format_percent(snapshot.get("operating_margin"), already_percent=False), "来源": _metric_source_label(snapshot, "operating_margin")},
        {"自动补充项": metric_label("FCF margin"), "数值": format_percent(snapshot.get("fcf_margin"), already_percent=False), "来源": _metric_source_label(snapshot, "fcf_margin")},
        {"自动补充项": metric_label("SBC / revenue"), "数值": format_percent(snapshot.get("sbc_ratio"), already_percent=False), "来源": _metric_source_label(snapshot, "sbc_ratio")},
        {"自动补充项": metric_label("RPO / cRPO growth"), "数值": format_percent(snapshot.get("rpo_growth"), already_percent=False), "来源": _metric_source_label(snapshot, "rpo_growth")},
        {"自动补充项": "递延收入增速", "数值": format_percent(snapshot.get("deferred_revenue_growth"), already_percent=False), "来源": snapshot.get("deferred_revenue_source") or "SEC companyfacts"},
    ]
    rows = [row for row in rows if row["数值"] != "N/A"]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    elif status:
        message = f"SEC 补充状态：{resolution_status_label(status)}"
        if note:
            message += f"；{note}"
        st.caption(message)
    _render_disclosure_metrics(snapshot)


def _render_disclosure_metrics(snapshot: dict) -> None:
    metrics = snapshot.get("disclosureMetrics")
    resolutions = snapshot.get("missingMetricResolutions")
    _render_disclosure_review_summary(snapshot)
    if not isinstance(metrics, list):
        metrics = []
    if not isinstance(resolutions, list):
        resolutions = []
    if not metrics and not resolutions:
        return
    render_section_title("披露数据来源", "自动抽取值均保留来源、原文和置信度")
    if metrics:
        rows = []
        for item in metrics:
            rows.append(
                {
                    "指标": metric_label(item.get("displayName") or item.get("metricKey")),
                    "数值": _format_disclosure_value(item.get("value"), item.get("unit")),
                    "来源": source_type_label(item.get("sourceType") or "N/A"),
                    "置信度": confidence_label(item.get("confidence") or "N/A"),
                    "复核状态": _review_status_label(item.get("reviewStatus") or "pending_review"),
                    "期间": item.get("period") or "N/A",
                    "原文片段": _truncate(item.get("extractedText") or "", 180),
                    "动作": "已保存",
                    "来源链接": item.get("sourceUrl") or "N/A",
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if resolutions:
        resolution_rows = [
            {
                "指标": metric_label(item.get("displayName") or item.get("metricKey")),
                "数值": "N/A",
                "来源": source_type_label(item.get("sourceTried") or "N/A"),
                "置信度": "N/A",
                "期间": "N/A",
                "说明": item.get("reason") or "",
                "状态": resolution_status_label(item.get("status")),
                "动作": action_label(item.get("recommendedAction")),
                "来源链接": "N/A",
            }
            for item in resolutions
        ]
        st.dataframe(pd.DataFrame(resolution_rows), hide_index=True, width="stretch")


def _render_disclosure_review_summary(snapshot: dict) -> None:
    summary = snapshot.get("disclosureReviewSummary")
    if not isinstance(summary, dict) or not summary.get("total"):
        return
    render_section_title("数据复核状态", "自动抽取值确认后才优先进入评分")
    rows = [
        {"状态": "已确认", "数量": summary.get("approved", 0)},
        {"状态": "待复核", "数量": summary.get("pending_review", 0)},
        {"状态": "已驳回", "数量": summary.get("rejected", 0)},
        {"状态": "手动修正", "数量": summary.get("manually_corrected", 0)},
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    critical = snapshot.get("criticalPendingReviewMetrics")
    if isinstance(critical, list) and critical:
        st.warning("有关键数据待复核，评分置信度受限：" + "、".join(str(item) for item in critical))


def _metric_source_label(snapshot: dict, key: str) -> str:
    sources = snapshot.get("metric_sources")
    if isinstance(sources, dict):
        raw = sources.get(key)
        if isinstance(raw, dict):
            if raw.get("sourceDocumentTitle") or raw.get("source"):
                return str(raw.get("sourceDocumentTitle") or raw.get("source"))
            return source_type_label(raw.get("sourceType") or "N/A")
        if raw:
            return source_type_label(raw)
    return "N/A"


def _review_status_label(value: object) -> str:
    return {
        "pending_review": "待复核",
        "approved": "已确认",
        "rejected": "已驳回",
        "manually_corrected": "手动修正",
        "stale": "已过期",
    }.get(str(value or ""), str(value or "N/A"))


def _data_status_rows(snapshot: dict) -> list[dict]:
    groups = [
        ("可用关键指标", "availableCriticalMetrics", "available", "可正常参与评分"),
        ("真缺失", "missingCriticalMetrics", "missing", "当前无值，不参与评分"),
        ("公司不披露", "notDisclosedMetrics", "not_disclosed", "不乱估，不直接扣大分，只降低置信度"),
        ("当前数据源没有", "vendorUnavailableMetrics", "vendor_unavailable", "FMP/标准供应商无字段，可人工补充"),
        ("可从 IR 抓取", "requiresIrScrapeMetrics", "requires_ir_scrape", "需要财报新闻稿 / 8-K 99.1 / 投资者演示"),
        ("需要分析师预期", "requiresEstimatesMetrics", "requires_estimates", "PEG / EPS 增速需要分析师预期"),
        ("估算值", "estimatedMetrics", "estimated", "可低权重参与，不当作财报披露"),
    ]
    rows: list[dict] = []
    for label, key, status, note in groups:
        values = snapshot.get(key)
        if not values:
            continue
        if isinstance(values, str):
            values = [values]
        rows.append({"分类": label, "状态": resolution_status_label(status), "字段": "、".join(metric_label(item) for item in values), "说明": note})
    return rows


def _manual_override_fields(model_type: str) -> list[tuple[str, str, bool]]:
    if model_type == "SAAS_SOFTWARE":
        return [
            ("订阅收入增速", "manualSubscriptionRevenueGrowth", True),
            ("non-GAAP 经营利润率", "manualNonGaapOperatingMargin", True),
            ("净留存率 / NRR", "manualNetRetention", True),
            ("RPO / cRPO 增速", "manualRpoGrowth", True),
            ("大客户增长", "manualLargeCustomerGrowth", True),
            ("股权激励/收入", "manualSbcRatio", True),
        ]
    if model_type == "POWER_GENERATION":
        return [
            ("调整后EBITDA", "manualAdjustedEbitda", False),
            ("调整后EBITDA增速", "manualAdjustedEbitdaGrowth", True),
            ("增长投资前调整后FCF", "manualAdjustedFcfBeforeGrowth", False),
            ("净债务/调整后EBITDA", "manualNetDebtToAdjustedEbitda", False),
            ("当年对冲覆盖率", "manualHedgeCoverageCurrentYear", True),
            ("次年对冲覆盖率", "manualHedgeCoverageNextYear", True),
            ("回购金额", "manualBuybackAmount", False),
            ("股本减少比例", "manualShareCountReduction", True),
        ]
    if model_type == "AI_INFRA_HIGH_RISK":
        return [
            ("现金可支撑月数", "manualCashRunwayMonths", False),
            ("Backlog / 已签约收入增速", "manualBacklogGrowth", True),
            ("客户集中风险 0-100", "manualCustomerConcentration", False),
            ("稀释风险 0-100", "manualDilutionRisk", False),
        ]
    return []


def _decision_summary_text(score, buy_zone: BuyZoneEstimate, final_decision=None) -> str:
    action = _final_action_text(score, final_decision)
    if buy_zone.currentZone in {"tranche_buy", "heavy_buy", "below_heavy_buy"} and action not in {"禁止追高", "剔除"}:
        return f"{score.scoring_model} 模型显示当前已经接近系统买区，但仍按风险等级控制新增仓位。"
    if score.risk_rating == "低" and action in {"只观察", "等回踩"}:
        return "风险评级低代表公司基本面风险较低，不等于当前价格值得追；当前动作主要受买点和估值约束。"
    if buy_zone.currentZone == "no_chase" or action == "禁止追高":
        return "公司质量和当前价格需要分开看：短线或估值偏热时，系统不建议追高新增。"
    return f"当前操作建议为「{action}」，系统买区用于约束新增仓位，而不是替代评分结论。"


def _decision_wait_items(score, buy_zone: BuyZoneEstimate) -> list[str]:
    items: list[str] = []
    if buy_zone.trancheBuyHigh is not None:
        items.append(f"回落到可分批区上沿 {format_currency(buy_zone.trancheBuyHigh)} 附近")
    if score.entry_rating and not str(score.entry_rating).startswith("A"):
        items.append("买点评级提升到 A- / A 区间")
    if score.overheat_score >= 40:
        items.append("过热分降温，RSI / 20日涨幅回到舒适区")
    if score.data_confidence in {"low", "medium"}:
        items.append("关键数据复核完成后再提高仓位")
    return items[:4]


def _buy_zone_label(zone: str) -> str:
    return {
        "no_chase": "禁止追高区",
        "fair_observation": "合理观察区",
        "tranche_buy": "可分批区",
        "heavy_buy": DEEP_DISCOUNT_ZONE_LABEL,
        "below_heavy_buy": f"低于{DEEP_DISCOUNT_ZONE_LABEL}",
        "data_insufficient": "数据不足区",
    }.get(zone, "正常评估")


def _buy_zone_method_label(method: str) -> str:
    return {
        "valuation_multiple": "估值倍数",
        "fcf_yield": "FCF收益率",
        "growth_adjusted": "增长调整",
        "technical_proxy": "技术代理",
        "blended": "综合估值",
        "manual_override": "手动买区",
    }.get(method, method)


def _buy_zone_source(plan: dict) -> str:
    if has_buy_zone_override(plan):
        return "manual"
    if _plan_number(plan, "first_buy_price") is not None:
        return "mixed"
    return "system"


def _buy_zone_section_title(source: str) -> tuple[str, str]:
    if source == "manual":
        return "手动买区", "当前使用手动买区"
    if source == "mixed":
        return "买区计划", "系统买区 + 手动操作计划"
    return "系统建议击球区", "当前使用系统建议"


def _buy_zone_next_trigger(plan: dict, active_zone: BuyZoneEstimate, source: str) -> tuple[str, float | None]:
    if source == "manual":
        first_buy = _plan_number(plan, "first_buy_price")
        if first_buy is not None:
            return "第一买入触发价", first_buy
    next_price = getattr(active_zone, "nextTriggerPrice", None)
    next_label = getattr(active_zone, "nextBuyLabel", "") or _distance_to_zone(active_zone.currentPrice, active_zone.to_plan_fields())
    return next_label, next_price


def _plan_or_suggestion(plan: dict, field: str, fallback):
    value = plan.get(field)
    return value if value is not None and value != "" else fallback


def _plan_number(plan: dict, field: str) -> float | None:
    try:
        value = plan.get(field)
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _toggle_session_flag(key: str) -> None:
    st.session_state[key] = not st.session_state.get(key, False)


def _final_action_text(score, final_decision=None) -> str:
    return str(getattr(final_decision, "finalAction", None) or getattr(score, "action", None) or "只观察")


def _final_current_add(score, final_decision=None, plan_suggestion: PositionPlanSuggestion | None = None):
    if final_decision is not None and hasattr(final_decision, "currentAddLimitPercent"):
        return getattr(final_decision, "currentAddLimitPercent")
    if plan_suggestion is not None:
        return plan_suggestion.currentAddLimitPercent
    return getattr(score, "current_add_limit_percent", getattr(score, "max_suggested_position_percent", None))


def _final_max_position(score, final_decision=None, plan_suggestion: PositionPlanSuggestion | None = None):
    if final_decision is not None and hasattr(final_decision, "maxPortfolioWeightPercent"):
        return getattr(final_decision, "maxPortfolioWeightPercent")
    if plan_suggestion is not None:
        return plan_suggestion.maxPortfolioWeightPercent
    return getattr(score, "max_portfolio_weight_percent", None)


def _render_decision_summary(score, buy_zone: BuyZoneEstimate, plan_suggestion: PositionPlanSuggestion, final_decision=None) -> None:
    render_section_title("当前结论", "先定动作，再看触发条件")
    wait_items = _decision_wait_items(score, buy_zone)
    trigger = getattr(buy_zone, "nextBuyLabel", "") or _distance_to_zone(buy_zone.currentPrice, buy_zone.to_plan_fields())
    st.markdown(
        '<section class="research-card conclusion-card">'
        f'<div class="conclusion-main">{escape(_final_action_text(score, final_decision))}</div>'
        f'<p>{escape(_decision_summary_text(score, buy_zone, final_decision))}</p>'
        '<div class="conclusion-grid">'
        f'<div><span>当前新增建议</span><strong>{escape(_position_limit_text(_final_current_add(score, final_decision, plan_suggestion)))}</strong></div>'
        f'<div><span>组合仓位上限</span><strong>{escape(_position_limit_text(_final_max_position(score, final_decision, plan_suggestion)))}</strong></div>'
        f'<div><span>下一触发条件</span><strong>{escape(trigger)}</strong></div>'
        '</div>'
        '<div class="wait-list">'
        + "".join(f'<span>{escape(item)}</span>' for item in wait_items)
        + '</div>'
        '</section>',
        unsafe_allow_html=True,
    )


def _render_buy_zone(
    ticker: str,
    plan_store: StockPlanStore,
    plan: dict,
    active_zone: BuyZoneEstimate,
    system_zone: BuyZoneEstimate,
    score,
) -> None:
    source = _buy_zone_source(plan)
    manual = source == "manual"
    if source == "system":
        title, title_suffix = "系统建议击球区", "当前使用系统建议"
    else:
        title, title_suffix = _buy_zone_section_title(source)
    render_section_title(title, title_suffix)
    price = active_zone.currentPrice
    confidence = getattr(active_zone, "buyZoneConfidence", None) or active_zone.confidence
    next_label, next_price = _buy_zone_next_trigger(plan, active_zone, source)
    validation_errors = list(getattr(active_zone, "validationErrors", None) or [])
    rows = [
        ("当前价格", format_currency(price), _buy_zone_label(active_zone.currentZone), "current"),
        ("禁止追高价", _above_text(active_zone.noChaseAbove), _zone_status(price, lower=active_zone.noChaseAbove, mode="above"), "no-chase"),
        ("合理观察区", _range_text(active_zone.fairValueLow, active_zone.fairValueHigh), _zone_status(price, active_zone.fairValueLow, active_zone.fairValueHigh), "observe"),
        ("可分批区", _range_text(active_zone.trancheBuyLow, active_zone.trancheBuyHigh), _zone_status(price, active_zone.trancheBuyLow, active_zone.trancheBuyHigh), "tranche"),
        (DEEP_DISCOUNT_ZONE_LABEL, _below_text(active_zone.heavyBuyBelow), _zone_status(price, upper=active_zone.heavyBuyBelow, mode="below"), "heavy"),
    ]
    st.markdown(
        '<section class="research-card buy-zone-panel">'
        '<div class="buy-zone-meta">'
        f'<div><span>买点状态</span><strong>{_buy_point_status_pill_html(score, active_zone)}</strong></div>'
        f'<div><span>系统买区位置</span><strong>{escape(_buy_zone_label(active_zone.currentZone))}</strong></div>'
        f'<div><span>买区置信度</span><strong>{escape(confidence_label(confidence))}</strong></div>'
        f'<div><span>方法</span><strong>{escape(_buy_zone_method_label(active_zone.method))}</strong></div>'
        f'<div><span>下一触发</span><strong>{escape(next_label)}{(" / " + escape(format_currency(next_price))) if next_price is not None else ""}</strong></div>'
        '</div>'
        '<div class="buy-zone-ladder">'
        + "".join(_zone_row_html(label, value, status, state) for label, value, status, state in rows)
        + '</div>'
        '</section>',
        unsafe_allow_html=True,
    )
    if st.session_state.get(f"stock-plan-editing-{ticker}", False):
        st.info("有未保存的操作计划变更。保存后将更新顶部下一触发价。")
    with st.expander("展开买区依据", expanded=False):
        _render_short_list(active_zone.keyReasons[:5], "暂无生成依据")
        if active_zone.warnings:
            st.warning("；".join(active_zone.warnings[:3]))
        if validation_errors:
            st.error("；".join(validation_errors[:3]))

    action_cols = st.columns([1, 1, 4])
    if manual:
        if action_cols[0].button("恢复系统建议", key=f"restore-system-zone-{ticker}", width="stretch"):
            plan_store.save_plan(ticker, clear_buy_zone_override_values(plan))
            st.success("已恢复系统建议买区。")
            st.rerun()
    else:
        if action_cols[0].button("保存为手动买区", key=f"save-system-zone-{ticker}", width="stretch"):
            plan_store.save_plan(ticker, {**plan, **system_zone.to_plan_fields()})
            st.success("已保存为手动买区，之后会优先使用手动值。")
            st.rerun()
    action_cols[1].caption("可在下方操作计划里编辑区间；保存后会刷新上方买区摘要。")


def _render_action_plan_form(
    ticker: str,
    plan_store: StockPlanStore,
    plan: dict,
    suggestion: PositionPlanSuggestion,
    active_zone: BuyZoneEstimate,
    final_decision=None,
) -> None:
    render_section_title("操作计划", "系统建议，可按需编辑")
    edit_key = f"stock-plan-editing-{ticker}"
    summary_rows = [
        ("第一笔买入价", _format_plan_currency(_plan_or_suggestion(plan, "first_buy_price", suggestion.firstBuyPrice))),
        ("第二笔买入价", _format_plan_currency(_plan_or_suggestion(plan, "second_buy_price", suggestion.secondBuyPrice))),
        ("极端恐慌区触发价", _format_plan_currency(_plan_or_suggestion(plan, "third_buy_price", suggestion.thirdBuyPrice))),
        ("停止加仓条件", plan.get("stop_adding_condition") or suggestion.stopAddingCondition),
        ("财报复核点", plan.get("earnings_review_points") or suggestion.earningsReviewCondition),
    ]
    header_cols = st.columns([4, 1])
    with header_cols[0]:
        st.markdown(_plan_summary_html(summary_rows), unsafe_allow_html=True)
    with header_cols[1]:
        st.button(
            "编辑计划",
            key=f"stock-plan-edit-button-{ticker}",
            on_click=_toggle_session_flag,
            args=(edit_key,),
            width="stretch",
        )

    if not st.session_state.get(edit_key, False):
        return

    with st.form(f"stock-plan-{ticker}"):
        st.info("有未保存的操作计划变更。保存后将更新顶部下一触发价。")
        top = st.columns(2)
        target_position_pct = top[0].text_input(
            "组合仓位上限 %",
            value=_number_text(_plan_or_suggestion(plan, "target_position_pct", _final_max_position(None, final_decision, suggestion))),
        )
        planned_position_pct = top[1].text_input(
            "当前新增建议 %",
            value=_number_text(_plan_or_suggestion(plan, "planned_position_pct", _final_current_add(None, final_decision, suggestion))),
        )

        buy_cols = st.columns(3)
        first_buy_price = buy_cols[0].text_input("第一笔买入价", value=_number_text(_plan_or_suggestion(plan, "first_buy_price", suggestion.firstBuyPrice)))
        second_buy_price = buy_cols[1].text_input("第二笔买入价", value=_number_text(_plan_or_suggestion(plan, "second_buy_price", suggestion.secondBuyPrice)))
        third_buy_price = buy_cols[2].text_input("第三笔买入价", value=_number_text(_plan_or_suggestion(plan, "third_buy_price", suggestion.thirdBuyPrice)))

        zone_cols = st.columns(3)
        no_chase_above = zone_cols[0].text_input("禁止追高价", value=_number_text(_plan_or_suggestion(plan, "no_chase_above", active_zone.noChaseAbove)))
        fair_value_low = zone_cols[1].text_input("合理观察区下沿", value=_number_text(_plan_or_suggestion(plan, "fair_value_low", active_zone.fairValueLow)))
        fair_value_high = zone_cols[2].text_input("合理观察区上沿", value=_number_text(_plan_or_suggestion(plan, "fair_value_high", active_zone.fairValueHigh)))

        tranche_cols = st.columns(3)
        tranche_buy_low = tranche_cols[0].text_input("可分批区下沿", value=_number_text(_plan_or_suggestion(plan, "tranche_buy_low", active_zone.trancheBuyLow)))
        tranche_buy_high = tranche_cols[1].text_input("可分批区上沿", value=_number_text(_plan_or_suggestion(plan, "tranche_buy_high", active_zone.trancheBuyHigh)))
        heavy_buy_below = tranche_cols[2].text_input("极端恐慌区低于", value=_number_text(_plan_or_suggestion(plan, "heavy_buy_below", active_zone.heavyBuyBelow)))

        stop_adding_condition = st.text_area("停止加仓条件", value=plan.get("stop_adding_condition") or suggestion.stopAddingCondition)
        invalidation_condition = st.text_area("止损 / 逻辑破坏条件", value=plan.get("invalidation_condition") or suggestion.thesisBreakCondition)
        earnings_review_points = st.text_area("财报复核点", value=plan.get("earnings_review_points") or suggestion.earningsReviewCondition)
        notes = st.text_area("备注", value=plan.get("notes") or "")

        submitted = st.form_submit_button("保存操作计划", width="stretch")
        if submitted:
            values = {
                "target_position_pct": target_position_pct,
                "planned_position_pct": planned_position_pct,
                "first_buy_price": first_buy_price,
                "second_buy_price": second_buy_price,
                "third_buy_price": third_buy_price,
                "no_chase_above": no_chase_above,
                "fair_value_low": fair_value_low,
                "fair_value_high": fair_value_high,
                "tranche_buy_low": tranche_buy_low,
                "tranche_buy_high": tranche_buy_high,
                "heavy_buy_below": heavy_buy_below,
                "stop_adding_condition": stop_adding_condition,
                "invalidation_condition": invalidation_condition,
                "earnings_review_points": earnings_review_points,
                "notes": notes,
            }
            plan_store.save_plan(ticker, values)
            st.session_state[edit_key] = False
            st.success("操作计划已保存，顶部买区摘要已更新。")
            st.rerun()


def _render_research_memo(ticker: str, plan_store: StockPlanStore, plan: dict) -> None:
    memo = _parse_research_memo(plan.get("notes"))
    edit_key = f"stock-research-memo-editing-{ticker}"
    heading_cols = st.columns([4, 1])
    with heading_cols[0]:
        render_section_title("研究备忘录", "记录投资假设、观察点和下次复核条件")
    with heading_cols[1]:
        st.write("")
        if st.button("编辑备忘录", key=f"research-memo-edit-{ticker}", width="stretch"):
            st.session_state[edit_key] = not st.session_state.get(edit_key, False)

    st.markdown(_research_memo_html(memo), unsafe_allow_html=True)

    if not st.session_state.get(edit_key, False):
        return

    with st.form(f"research-memo-form-{ticker}"):
        st.caption("复用操作计划 notes 字段保存，不新增数据库表或 schema。")
        left, right = st.columns(2)
        with left:
            thesis = st.text_area(
                "投资假设",
                value=memo["thesis"],
                placeholder="为什么这只股票值得关注？",
                height=78,
            )
            refutation = st.text_area(
                "反证条件",
                value=memo["refutation"],
                placeholder="什么情况说明原判断可能错了？",
                height=78,
            )
        with right:
            observation = st.text_area(
                "当前观察点",
                value=memo["observation"],
                placeholder="接下来重点看什么数据或事件？",
                height=78,
            )
            next_review = st.text_area(
                "下次复核条件",
                value=memo["next_review"],
                placeholder="财报后 / 股价到某区间 / 数据更新后复核",
                height=78,
            )
        notes = st.text_area("备注 / 上次复盘摘要", value=memo["notes"], height=88)
        submitted = st.form_submit_button("保存备忘录", width="stretch")
        if submitted:
            plan_store.save_plan(
                ticker,
                {
                    **plan,
                    "notes": _compose_research_memo(
                        thesis=thesis,
                        observation=observation,
                        refutation=refutation,
                        next_review=next_review,
                        notes=notes,
                    ),
                },
            )
            st.session_state[edit_key] = False
            st.success("研究备忘录已保存。")
            st.rerun()


def _render_raw_metrics(snapshot: dict, history: pd.DataFrame, technicals: dict, score, ticker: str) -> None:
    render_section_title("原始指标", "默认折叠，必要时再复核")
    with st.expander("展开原始指标", expanded=False):
        st.plotly_chart(_price_chart(history, ticker), width="stretch")
        st.plotly_chart(_rsi_chart(history), width="stretch")
        fundamentals = pd.DataFrame(_raw_fundamental_rows(snapshot))
        st.dataframe(fundamentals, hide_index=True, width="stretch")

        if snapshot.get("data_quality_notes"):
            st.info("部分 FMP 端点暂时不可用：" + "；".join(snapshot["data_quality_notes"][:4]))
        if score.risk_flags:
            flags = pd.DataFrame(
                [{"风险旗标": flag.label, "严重程度": _severity_label(flag.severity), "说明": flag.detail} for flag in score.risk_flags]
            )
            st.dataframe(flags, hide_index=True, width="stretch")


@st.cache_data(ttl=60 * 60)
def _load_detail(ticker: str, refresh_token: str | None = None):
    force_refresh = bool(refresh_token)
    provider = get_market_data_provider(full_fundamentals=True)
    snapshot = provider.get_quote(ticker, force_refresh=force_refresh)
    history = add_technical_indicators(provider.get_price_history(ticker, force_refresh=force_refresh))
    technicals = latest_technical_snapshot(history)
    score = calculate_total_score(snapshot, technicals)
    refreshed_at = FundamentalCache().get_snapshot_fetched_at(ticker)
    return snapshot, history, technicals, score, refreshed_at


def _industry_metric_rows(model_type: str, snapshot: dict, score) -> list[dict]:
    fcf_metric = fcf_margin_metric(snapshot)
    if model_type == "SAAS_SOFTWARE":
        return [
            _metric_row("收入增速", format_percent(snapshot.get("revenue_growth"), already_percent=False), "核心增长"),
            _metric_row("毛利率", format_percent(snapshot.get("gross_margin"), already_percent=False), "经营质量"),
            _metric_row("经营利润率", format_percent(snapshot.get("operating_margin"), already_percent=False), "GAAP profitability"),
            _metric_row("FCF margin", format_percent(fcf_metric.value, already_percent=False), fcf_margin_source_note(snapshot)),
            _metric_row("P/S", format_multiple(snapshot.get("price_to_sales")), "估值"),
            _metric_row("EV/FCF", _format_ev_fcf(snapshot), "估值"),
            _metric_row("FCF Yield", format_percent(snapshot.get("free_cash_flow_yield"), already_percent=False), "估值"),
            _metric_row("RPO / ARR / cRPO", _manual_percent_metric(snapshot, "manualRpoGrowth", "rpo_growth", "manualArrGrowth"), "无则需人工补充"),
            _metric_row("SBC", _manual_percent_metric(snapshot, "manualSbcRatio", "sbc_ratio", "stock_based_compensation_ratio"), "无则需人工补充"),
        ]
    if model_type == "POWER_GENERATION":
        return [
            _metric_row("Adjusted EBITDA", _large_or_manual(snapshot, "adjustedEbitda", "manualAdjustedEbitda"), "电力模型核心"),
            _metric_row("Adjusted FCF before growth", _large_or_manual(snapshot, "adjustedFcfBeforeGrowth", "manualAdjustedFcfBeforeGrowth"), "电力模型核心"),
            _metric_row("Market Cap / Adjusted FCF", _market_cap_to_adjusted_fcf(snapshot), "估值"),
            _metric_row("EV / Adjusted EBITDA", _ev_to_adjusted_ebitda(snapshot), "估值"),
            _metric_row("Net Debt / Adjusted EBITDA", _manual_metric(snapshot, "manualNetDebtToAdjustedEbitda", "net_debt_to_ebitda"), "杠杆"),
            _metric_row("Hedge Coverage", _manual_metric(snapshot, "manualHedgeCoverageCurrentYear", "hedgeCoverageCurrentYear"), "无则需人工补充"),
            _metric_row("Buyback", _large_or_manual(snapshot, "buybackAmount", "manualBuybackAmount"), "资本回报"),
            _metric_row("Share Count Reduction", _manual_metric(snapshot, "shareCountReduction", "manualShareCountReduction"), "资本回报"),
        ]
    if model_type == "SEMICONDUCTOR":
        return [
            _metric_row("收入增速", format_percent(snapshot.get("revenue_growth"), already_percent=False), "周期与需求"),
            _metric_row("毛利率", format_percent(snapshot.get("gross_margin"), already_percent=False), "产品力"),
            _metric_row("经营利润率", format_percent(snapshot.get("operating_margin"), already_percent=False), "盈利能力"),
            _metric_row("FCF margin", format_percent(fcf_metric.value, already_percent=False), fcf_margin_source_note(snapshot)),
            _metric_row("P/S", format_multiple(snapshot.get("price_to_sales")), "估值"),
            _metric_row("Forward PE", format_multiple(snapshot.get("forward_pe")), "估值"),
            _metric_row("EV/EBITDA", format_multiple(snapshot.get("enterprise_to_ebitda")), "估值"),
            _metric_row("库存/周期状态", _manual_metric(snapshot, "manualInventoryRisk", "manualSemiconductorCycleRisk"), "无则需人工补充"),
        ]
    if model_type == "AI_INFRA_HIGH_RISK":
        return [
            _metric_row("收入增速", format_percent(snapshot.get("revenue_growth"), already_percent=False), "增长"),
            _metric_row("FCF", format_large_number(snapshot.get("free_cash_flow")), "现金流"),
            _metric_row("负债", format_large_number(snapshot.get("total_debt")), "资产负债"),
            _metric_row("客户集中风险", _manual_metric(snapshot, "manualCustomerConcentration"), "无则需人工补充"),
            _metric_row("稀释风险", _manual_metric(snapshot, "manualDilutionRisk"), "无则需人工补充"),
            _metric_row("Backlog / contracted revenue", _manual_metric(snapshot, "manualBacklogGrowth"), "无则需人工补充"),
        ]
    return [
        _metric_row("收入增速", format_percent(snapshot.get("revenue_growth"), already_percent=False), "通用指标"),
        _metric_row("经营利润率", format_percent(snapshot.get("operating_margin"), already_percent=False), "通用指标"),
        _metric_row("FCF Yield", format_percent(snapshot.get("free_cash_flow_yield"), already_percent=False), "通用指标"),
        _metric_row("P/S", format_multiple(snapshot.get("price_to_sales")), "通用指标"),
        _metric_row("Forward PE", format_multiple(snapshot.get("forward_pe")), "通用指标"),
        _metric_row("净债务/EBITDA", format_multiple(snapshot.get("net_debt_to_ebitda")), "通用指标"),
        _metric_row("数据完整度", f"{score.data_quality_pct:.1f}%", "评分置信度"),
    ]


def _raw_fundamental_rows(snapshot: dict) -> list[dict]:
    return [
        {"指标": "公司", "数值": snapshot.get("company_name") or "N/A"},
        {"指标": "行业", "数值": snapshot.get("industry") or "N/A"},
        {"指标": "Beta", "数值": _format_plain(snapshot.get("beta"))},
        {"指标": "市值", "数值": format_large_number(snapshot.get("market_cap"))},
        {"指标": "企业价值", "数值": format_large_number(snapshot.get("enterprise_value"))},
        {"指标": "流通股数", "数值": format_compact_number(snapshot.get("shares_outstanding"))},
        {"指标": "收入", "数值": format_large_number(snapshot.get("total_revenue"))},
        {"指标": "净利润", "数值": format_large_number(snapshot.get("net_income"))},
        {"指标": "自由现金流", "数值": format_large_number(snapshot.get("free_cash_flow"))},
        {"指标": "经营现金流", "数值": format_large_number(snapshot.get("operating_cash_flow"))},
        {"指标": "TTM市盈率", "数值": format_multiple(snapshot.get("trailing_pe"))},
        {"指标": "预期市盈率", "数值": format_multiple(snapshot.get("forward_pe"))},
        {"指标": "市销率", "数值": format_multiple(snapshot.get("price_to_sales"))},
        {"指标": "EV/销售额", "数值": format_multiple(snapshot.get("enterprise_to_revenue"))},
        {"指标": "EV/EBITDA", "数值": format_multiple(snapshot.get("enterprise_to_ebitda"))},
        {"指标": "P/FCF", "数值": format_multiple(snapshot.get("price_to_fcf"))},
        {"指标": "FCF收益率", "数值": format_percent(snapshot.get("free_cash_flow_yield"), already_percent=False)},
        {"指标": metric_label("FCF margin"), "数值": f"{format_percent(fcf_margin_metric(snapshot).value, already_percent=False)}（{fcf_margin_source_note(snapshot)}）"},
        {"指标": "收入增长", "数值": format_percent(snapshot.get("revenue_growth"), already_percent=False)},
        {"指标": "预期收入增长", "数值": format_percent(snapshot.get("forward_revenue_growth"), already_percent=False)},
        {"指标": "自由现金流增长", "数值": format_percent(snapshot.get("free_cash_flow_growth"), already_percent=False)},
        {"指标": "毛利率", "数值": format_percent(snapshot.get("gross_margin"), already_percent=False)},
        {"指标": "经营利润率", "数值": format_percent(snapshot.get("operating_margin"), already_percent=False)},
        {"指标": "净利率", "数值": format_percent(snapshot.get("profit_margin"), already_percent=False)},
        {"指标": "ROE", "数值": format_percent(snapshot.get("return_on_equity"), already_percent=False)},
        {"指标": "ROIC", "数值": format_percent(snapshot.get("return_on_invested_capital"), already_percent=False)},
        {"指标": "债务/权益", "数值": format_multiple(snapshot.get("debt_to_equity"))},
        {"指标": "净债务/EBITDA", "数值": format_multiple(snapshot.get("net_debt_to_ebitda"))},
        {"指标": "流动比率", "数值": format_multiple(snapshot.get("current_ratio"))},
        {"指标": "预期EPS", "数值": _format_plain(snapshot.get("forward_eps_estimate"))},
        {"指标": "预期收入", "数值": format_large_number(snapshot.get("forward_revenue_estimate"))},
    ]


def _entry_explanation(score, snapshot: dict, technicals: dict, plan: dict) -> list[str]:
    reasons = [
        f"操作建议：{score.action}",
        f"估值状态：{score.valuation_status}",
        f"追高状态：{score.overheat_status} / {score.overheat_action}",
        f"52周高点回撤：{format_percent(technicals.get('drawdown_from_high_pct'))}",
        _distance_to_zone(technicals.get("price"), plan),
    ]
    if score.overheat_reasons:
        reasons.append("追高触发原因：" + "；".join(score.overheat_reasons[:3]))
    return reasons


def _entry_wait_items(score, technicals: dict, plan: dict) -> list[str]:
    items: list[str] = []
    if score.action in {"等回踩", "只观察", "禁止追高"}:
        items.append("等待估值或技术结构进一步确认")
    if score.overheat_score >= 40:
        items.append("过热分仍未完全消化")
    price = technicals.get("price")
    tranche_high = plan.get("tranche_buy_high")
    if price is not None and tranche_high is not None and price > tranche_high:
        items.append(f"价格仍高于可分批区上沿 {format_percent((price - tranche_high) / price, already_percent=False)}")
    return items or ["当前无需额外等待条件，但仍按仓位计划执行"]


def _risk_explanation(score) -> list[str]:
    reasons = list(score.key_risks or [])
    reasons.extend(flag.label for flag in score.risk_flags)
    return [metric_label(item) for item in _dedupe(reasons)[:6]] or ["当前未触发明显高风险旗标"]


def _quality_penalties(score, snapshot: dict) -> list[str]:
    risks = list(score.key_risks or [])
    fcf_source = getattr(score, "fcf_margin_source_type", "")
    if fcf_source == "derivedFromMarket":
        risks.append("FCF margin 为市场反推值，不参与质量评分")
    if snapshot.get("operating_margin") is not None and snapshot.get("operating_margin") < 0.15:
        risks.append("GAAP 经营利润率未达到高质量阈值")
    return [metric_label(item) for item in _dedupe(risks)[:5]]


def _risk_source_labels(score) -> list[str]:
    labels: list[str] = []
    text = " ".join([*(score.key_risks or []), *(score.missing_data or []), *(flag.label for flag in score.risk_flags)])
    checks = [
        ("估值", ("valuation", "估值", "P/S", "PE", "FCF")),
        ("负债", ("debt", "leverage", "债", "杠杆")),
        ("现金流", ("FCF", "cash", "现金流")),
        ("周期", ("cycle", "周期", "inventory", "库存")),
        ("监管", ("regulatory", "监管")),
        ("客户集中", ("customer", "客户")),
        ("数据缺失", ("missing", "缺失", "manual")),
    ]
    for label, keywords in checks:
        if any(keyword in text for keyword in keywords):
            labels.append(label)
    return labels or ["暂无明确风险分类"]


def _missing_impact(item: str) -> str:
    lowered = item.lower()
    if any(token in lowered for token in ["growth", "margin", "roic", "arr", "rpo", "retention", "sbc", "ebitda", "fcf"]):
        return "公司质量 / 行业核心指标"
    if any(token in lowered for token in ["valuation", "p/s", "pe", "yield", "multiple"]):
        return "买点 / 估值"
    if any(token in lowered for token in ["risk", "debt", "leverage", "hedge", "regulatory", "customer", "dilution"]):
        return "风险"
    return "评分置信度"


def _missing_impact_rows(impacts: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for item in impacts:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "指标": metric_label(item.get("metric") or "N/A"),
                "影响等级": item.get("impactLevel") or "low",
                "影响范围": _affects_label(item.get("affects") or "Confidence Only"),
                "建议动作": action_label(item.get("action") or "manual_override_required"),
                "说明": item.get("explanation") or "",
            }
        )
    return rows


def _affects_label(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value if item]
    else:
        parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    labels = {
        "Quality": "公司质量",
        "Entry": "买点 / 估值",
        "Valuation": "买点 / 估值",
        "Risk": "风险",
        "Technical": "技术面",
        "Confidence Only": "评分置信度",
        "Confidence": "评分置信度",
    }
    translated = [labels.get(part, metric_label(part)) for part in parts]
    return " / ".join(translated) if translated else "评分置信度"


def _needs_manual_override(item: str, model_type: str) -> bool:
    lowered = item.lower()
    if model_type in {"POWER_GENERATION", "AI_INFRA_HIGH_RISK"}:
        return True
    return any(token in lowered for token in ["manual", "rpo", "arr", "retention", "sbc", "hedge", "backlog", "customer"])


def _metric_row(metric: str, value: str, note: str) -> dict:
    display = value if value not in {"N/A", "", None} else MANUAL_TEXT
    return {"指标": metric_label(metric), "数值": display, "说明": action_label(note)}


def _manual_metric(snapshot: dict, *keys: str) -> str:
    for key in keys:
        value = snapshot.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            return value
        return _format_plain(value)
    return MANUAL_TEXT


def _manual_percent_metric(snapshot: dict, *keys: str) -> str:
    for key in keys:
        value = snapshot.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            return value
        return format_percent(value, already_percent=False)
    return MANUAL_TEXT


def _large_or_manual(snapshot: dict, *keys: str) -> str:
    for key in keys:
        value = snapshot.get(key)
        if value is not None:
            return format_large_number(value)
    return MANUAL_TEXT


def _format_ev_fcf(snapshot: dict) -> str:
    value = snapshot.get("ev_to_fcf")
    if value is not None:
        return format_multiple(value)
    return format_multiple(snapshot.get("price_to_fcf"))


def _market_cap_to_adjusted_fcf(snapshot: dict) -> str:
    market_cap = snapshot.get("market_cap")
    adjusted_fcf = snapshot.get("adjustedFcfBeforeGrowth") or snapshot.get("manualAdjustedFcfBeforeGrowth")
    if market_cap is None or adjusted_fcf in {None, 0}:
        return MANUAL_TEXT
    return format_multiple(market_cap / adjusted_fcf)


def _ev_to_adjusted_ebitda(snapshot: dict) -> str:
    direct = snapshot.get("enterpriseValueToAdjustedEbitda") or snapshot.get("enterprise_to_ebitda")
    if direct is not None:
        return format_multiple(direct)
    enterprise_value = snapshot.get("enterprise_value")
    adjusted_ebitda = snapshot.get("adjustedEbitda") or snapshot.get("manualAdjustedEbitda")
    if enterprise_value is None or adjusted_ebitda in {None, 0}:
        return MANUAL_TEXT
    return format_multiple(enterprise_value / adjusted_ebitda)


def _zone_status(price: float | None, lower: float | None = None, upper: float | None = None, mode: str = "range") -> str:
    if price is None:
        return "缺少现价"
    if mode == "above":
        if lower is None:
            return "未设置"
        return "已进入禁止追高区" if price >= lower else "未触发"
    if mode == "below":
        if upper is None:
            return "未设置"
        return "已进入极端恐慌区" if price <= upper else "未触发"
    if lower is None or upper is None:
        return "未设置"
    if lower <= price <= upper:
        return "当前在区间内"
    if price > upper:
        return f"高于上沿 {format_percent((price - upper) / price, already_percent=False)}"
    return f"低于下沿 {format_percent((lower - price) / lower, already_percent=False)}"


def _distance_to_zone(price: float | None, plan: dict) -> str:
    high = plan.get("tranche_buy_high")
    low = plan.get("tranche_buy_low")
    if price is None:
        return "距离击球区：缺少现价"
    if high is None and low is None:
        return "距离击球区：尚未设置，需要人工配置。"
    if low is not None and high is not None and low <= price <= high:
        return "距离击球区：已在可分批区内"
    if high is not None and price > high:
        return f"距离击球区：还需回落 {format_percent((price - high) / price, already_percent=False)} 到可分批区上沿"
    if low is not None and price < low:
        return "距离击球区：已低于可分批区下沿，可复核是否进入更深击球区"
    return "距离击球区：区间未完整设置"


def _above_text(value: float | None) -> str:
    return f">{format_currency(value)}" if value is not None else "尚未设置，需要人工配置。"


def _below_text(value: float | None) -> str:
    return f"<{format_currency(value)}" if value is not None else "尚未设置，需要人工配置。"


def _range_text(low: float | None, high: float | None) -> str:
    if low is None and high is None:
        return "尚未设置，需要人工配置。"
    if low is None:
        return f"低于 {format_currency(high)}"
    if high is None:
        return f"高于 {format_currency(low)}"
    return f"{format_currency(low)} - {format_currency(high)}"


def _price_chart(history: pd.DataFrame, ticker: str) -> go.Figure:
    fig = go.Figure()
    if history.empty:
        fig.update_layout(title=f"{ticker}：暂无价格历史")
        return fig

    fig.add_trace(go.Scatter(x=history["date"], y=history["close"], name="收盘价", line=dict(color="#2563eb", width=2)))
    fig.add_trace(go.Scatter(x=history["date"], y=history["ema20"], name="EMA20", line=dict(color="#7c3aed", width=1.2)))
    fig.add_trace(go.Scatter(x=history["date"], y=history["ema50"], name="EMA50", line=dict(color="#f97316", width=1.5)))
    fig.add_trace(go.Scatter(x=history["date"], y=history["ema200"], name="EMA200", line=dict(color="#16a34a", width=1.5)))
    fig.update_layout(
        title=f"{ticker} 价格、EMA20、EMA50、EMA200",
        height=420,
        margin=dict(l=20, r=20, t=50, b=20),
        hovermode="x unified",
        legend=dict(orientation="h", y=1.02, x=0),
    )
    return fig


def _rsi_chart(history: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if history.empty or "rsi14" not in history.columns:
        fig.update_layout(title="暂无 RSI 数据")
        return fig

    fig.add_trace(go.Scatter(x=history["date"], y=history["rsi14"], name="RSI14", line=dict(color="#7c2d12", width=2)))
    fig.add_hline(y=70, line_dash="dash", line_color="#dc2626", annotation_text="超买")
    fig.add_hline(y=30, line_dash="dash", line_color="#16a34a", annotation_text="超卖")
    fig.update_layout(height=260, margin=dict(l=20, r=20, t=50, b=20), yaxis=dict(range=[0, 100]), showlegend=False)
    return fig


def _zone_row_html(label: str, value: str, status: str, state: str) -> str:
    return (
        f'<div class="buy-zone-row buy-zone-row-{escape(state)}">'
        f'<span>{escape(label)}</span>'
        f'<strong>{escape(value)}</strong>'
        f'<em>{escape(status)}</em>'
        "</div>"
    )


def _parse_research_memo(raw_notes: object) -> dict[str, str]:
    memo = {"thesis": "", "observation": "", "refutation": "", "next_review": "", "notes": ""}
    text = str(raw_notes or "").strip()
    if not text:
        return memo

    labels = {
        "投资假设": "thesis",
        "当前观察点": "observation",
        "反证条件": "refutation",
        "下次复核": "next_review",
        "下次复核条件": "next_review",
        "备注": "notes",
        "上次复盘摘要": "notes",
    }
    buffers = {key: [] for key in memo}
    current_key = ""
    parsed_any = False
    fallback: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current_key:
                buffers[current_key].append("")
            continue
        matched = False
        for label, key in labels.items():
            for delimiter in ("：", ":"):
                prefix = f"{label}{delimiter}"
                if stripped.startswith(prefix):
                    current_key = key
                    buffers[key].append(stripped[len(prefix) :].strip())
                    parsed_any = True
                    matched = True
                    break
            if matched:
                break
        if matched:
            continue
        if current_key:
            buffers[current_key].append(stripped)
        else:
            fallback.append(stripped)

    if not parsed_any:
        memo["notes"] = text
        return memo

    for key, lines in buffers.items():
        memo[key] = "\n".join(line for line in lines).strip()
    if fallback:
        memo["notes"] = "\n".join([memo["notes"], *fallback]).strip()
    return memo


def _compose_research_memo(
    thesis: str,
    observation: str,
    refutation: str,
    next_review: str,
    notes: str,
) -> str:
    sections = [
        ("投资假设", thesis),
        ("当前观察点", observation),
        ("反证条件", refutation),
        ("下次复核", next_review),
        ("备注", notes),
    ]
    return "\n\n".join(f"{label}：\n{str(value or '').strip()}" for label, value in sections).strip()


def _research_memo_html(memo: dict[str, str]) -> str:
    items = [
        ("投资假设", memo.get("thesis"), "为什么这只股票值得关注？"),
        ("当前观察点", memo.get("observation"), "接下来重点看什么数据或事件？"),
        ("反证条件", memo.get("refutation"), "什么情况说明原判断可能错了？"),
        ("下次复核", memo.get("next_review"), "财报后 / 股价到某区间 / 数据更新后复核"),
    ]
    has_saved = any(str(memo.get(key) or "").strip() for key in ("thesis", "observation", "refutation", "next_review", "notes"))
    last_review = str(memo.get("notes") or "").strip()
    return (
        '<section class="research-card memo-summary-card">'
        '<div class="memo-grid">'
        + "".join(_memo_item_html(label, value, placeholder) for label, value, placeholder in items)
        + "</div>"
        f'<div class="memo-last-review"><span>上次复盘摘要</span><strong>{escape(last_review or "暂无复盘摘要")}</strong></div>'
        f'<p class="memo-footnote">{escape("复用操作计划 notes 字段保存，不新增数据库。" if has_saved else "暂未记录。点击「编辑备忘录」后可保存到本地计划。")}</p>'
        "</section>"
    )


def _memo_item_html(label: str, value: object, placeholder: str) -> str:
    text = str(value or "").strip()
    is_placeholder = not text
    class_name = "memo-placeholder" if is_placeholder else ""
    return (
        '<div class="memo-item">'
        f"<span>{escape(label)}</span>"
        f'<strong class="{class_name}">{escape(text or placeholder)}</strong>'
        "</div>"
    )


def _missing_data_summary(score, snapshot: dict) -> dict[str, object]:
    expected_keys = {
        "blockingCount",
        "autoFillableCount",
        "estimatesRequiredCount",
        "companyNotDisclosedCount",
        "lowPriorityArchivedCount",
        "humanReviewRequiredCount",
        "keyBlockingMetrics",
        "recommendedNextAction",
    }
    candidates = [
        snapshot.get("missingDataSummary"),
        getattr(score, "missingDataSummary", None),
        getattr(score, "missing_data_summary", None),
    ]
    for candidate in candidates:
        if callable(candidate):
            candidate = candidate()
        if isinstance(candidate, dict) and candidate and any(key in candidate for key in expected_keys):
            return candidate
    return {}


def _missing_data_summary_html(confidence: str, summary: dict[str, object]) -> str:
    blocking = _summary_count(summary, "blockingCount")
    auto_fillable = _summary_count(summary, "autoFillableCount")
    estimates = _summary_count(summary, "estimatesRequiredCount")
    not_disclosed = _summary_count(summary, "companyNotDisclosedCount")
    low_priority = _summary_count(summary, "lowPriorityArchivedCount")
    human_review = _summary_count(summary, "humanReviewRequiredCount")
    blocking_metrics = _summary_key_metrics(summary)
    message = _missing_data_conclusion(summary)
    next_action = _recommended_next_action_label(summary.get("recommendedNextAction"))
    cards = [
        ("数据可信度", confidence, ""),
        ("关键缺口", f"{blocking} 项", "、".join(blocking_metrics[:3]) if blocking_metrics else "暂无关键阻塞缺口"),
        ("可自动补齐", f"{auto_fillable} 项", "可通过自动计算或披露补齐"),
        ("需外部预期", f"{estimates} 项", "主要影响买点 / 估值"),
        ("公司未披露 / 低优先级", f"{not_disclosed + low_priority} 项", f"未披露 {not_disclosed} / 低优先级 {low_priority}"),
        ("需人工判断", f"{human_review} 项", "需人工判断后再进入评分"),
    ]
    return (
        '<section class="research-card missing-summary-card">'
        f'<p class="missing-summary-message">{escape(message)}</p>'
        '<div class="missing-summary-grid">'
        + "".join(_missing_summary_item_html(label, value, detail) for label, value, detail in cards)
        + "</div>"
        f'<p class="missing-summary-action">建议动作：{escape(next_action)}</p>'
        "</section>"
    )


def _missing_summary_item_html(label: str, value: str, detail: str) -> str:
    return (
        "<div>"
        f"<span>{escape(label)}</span>"
        f"<strong>{escape(value)}</strong>"
        f"<em>{escape(detail)}</em>"
        "</div>"
    )


def _missing_gap_groups(score, summary: dict[str, object]) -> list[dict[str, object]]:
    rows = _missing_resolution_rows(score)
    key_metrics = _summary_key_metrics(summary)
    blocking_items = _dedupe([*key_metrics, *_rows_for_routes(rows, {"human_review_required"})])
    auto_items = _rows_for_routes(rows, {"auto_calculate", "ir_or_sec_extract", "proxy_available"})
    estimate_items = _rows_for_routes(rows, {"analyst_estimates_required"})
    company_low_items = _rows_for_routes(rows, {"company_not_disclosed", "low_priority_archive"})
    return [
        {
            "title": "影响评分 / 需优先处理",
            "count": _summary_count(summary, "blockingCount") or _summary_count(summary, "humanReviewRequiredCount"),
            "items": blocking_items,
            "empty": "暂无关键阻塞缺口",
        },
        {
            "title": "可自动补齐",
            "count": _summary_count(summary, "autoFillableCount"),
            "items": auto_items,
            "empty": "暂无可自动补齐项",
        },
        {
            "title": "需要外部预期",
            "count": _summary_count(summary, "estimatesRequiredCount"),
            "items": estimate_items,
            "empty": "暂无需要分析师预期的缺口",
        },
        {
            "title": "公司未披露或低优先级",
            "count": _summary_count(summary, "companyNotDisclosedCount") + _summary_count(summary, "lowPriorityArchivedCount"),
            "items": company_low_items,
            "empty": "暂无公司未披露或低优先级缺口",
        },
    ]


def _missing_gap_groups_html(groups: list[dict[str, object]], limit: int | None) -> str:
    return (
        '<section class="missing-gap-groups">'
        + "".join(_missing_gap_group_html(group, limit) for group in groups)
        + "</section>"
    )


def _missing_gap_group_html(group: dict[str, object], limit: int | None) -> str:
    items = list(group.get("items") or [])
    display_items = items[:limit] if limit is not None else items
    if not display_items:
        display_items = [str(group.get("empty") or "暂无")]
    remaining = max(0, len(items) - len(display_items))
    return (
        '<div class="research-card missing-gap-group">'
        f'<div class="missing-gap-heading"><span>{escape(str(group.get("title") or ""))}</span><strong>{_summary_number(group.get("count"))} 项</strong></div>'
        "<ul>"
        + "".join(f"<li>{escape(item)}</li>" for item in display_items)
        + "</ul>"
        + (f'<p>还有 {remaining} 项在详情中</p>' if remaining else "")
        + "</div>"
    )


def _missing_resolution_rows(score) -> list[dict[str, object]]:
    rows = getattr(score, "metric_resolution_statuses", None) or getattr(score, "metricResolutionStatus", None) or []
    if not isinstance(rows, list):
        return []
    resolved_statuses = {"available", "calculated", "not_applicable"}
    return [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("resolutionStatus") or "") not in resolved_statuses
    ]


def _rows_for_routes(rows: list[dict[str, object]], routes: set[str]) -> list[str]:
    return _dedupe(
        [
            _missing_row_label(row)
            for row in rows
            if str(row.get("missingResolutionRoute") or "") in routes
        ]
    )


def _missing_row_label(row: dict[str, object]) -> str:
    return metric_label(str(row.get("displayName") or row.get("metricKey") or "N/A"))


def _summary_key_metrics(summary: dict[str, object]) -> list[str]:
    raw = summary.get("keyBlockingMetrics") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return _dedupe([metric_label(str(item)) for item in raw if item])


def _missing_data_conclusion(summary: dict[str, object]) -> str:
    blocking = _summary_count(summary, "blockingCount")
    human_review = _summary_count(summary, "humanReviewRequiredCount")
    estimates = _summary_count(summary, "estimatesRequiredCount")
    not_disclosed = _summary_count(summary, "companyNotDisclosedCount")
    messages: list[str] = []
    if blocking > 0:
        messages.append("当前仍有关键数据缺口，评分上限受到限制；建议优先处理关键缺口后再提高仓位。")
    elif blocking == 0 and human_review == 0:
        messages.append("当前缺失项主要为非阻塞项，不直接影响核心评分。")
    else:
        messages.append("当前仍有需要人工判断的数据，建议处理后再提高仓位。")
    if estimates > 0:
        messages.append("部分估值指标依赖分析师预期，主要影响买点 / 估值判断，不影响公司质量评分。")
    if not_disclosed > 0:
        messages.append("部分公司专属 KPI 未披露，系统会降低置信度，但不会直接等同扣分。")
    return " ".join(messages)


def _recommended_next_action_label(value: object) -> str:
    return {
        "handle_high_impact_manual_review": "优先处理关键缺口",
        "run_auto_fill_or_refresh_disclosures": "运行自动补齐或刷新披露数据",
        "configure_analyst_estimates_for_valuation": "补充分析师预期后再判断估值",
        "no_user_action_required": "暂无必须处理动作",
        "no_missing_data": "核心缺口已处理",
    }.get(str(value or ""), "按缺口分组逐项处理")


def _summary_count(summary: dict[str, object], key: str) -> int:
    return _summary_number(summary.get(key))


def _summary_number(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _plan_summary_html(rows: list[tuple[str, object]]) -> str:
    return (
        '<section class="research-card plan-summary-card">'
        '<div class="plan-summary-grid">'
        + "".join(
            f'<div><span>{escape(label)}</span><strong>{escape(str(value or "N/A"))}</strong></div>'
            for label, value in rows
        )
        + "</div>"
        "</section>"
    )


def _data_summary_html(confidence: str, pending: int, approved: int, missing_count: int) -> str:
    return (
        '<section class="research-card data-summary-card">'
        f'<div><span>数据可信度</span><strong>{escape(confidence)}</strong></div>'
        f'<div><span>待复核</span><strong>{pending}</strong></div>'
        f'<div><span>已确认</span><strong>{approved}</strong></div>'
        f'<div><span>缺失关键项</span><strong>{missing_count}</strong></div>'
        "</section>"
    )


def _data_status(score, snapshot: dict | None = None) -> str:
    if score.data_confidence:
        base = confidence_label(score.data_confidence)
        if score.proxy_confidence and score.proxy_confidence != "high":
            return f"{base} / 代理 {confidence_label(score.proxy_confidence)}"
        return base
    if snapshot and snapshot.get("dataConfidence"):
        return f"{confidence_label(snapshot.get('dataConfidence'))} / {snapshot.get('dataConfidencePct', 'N/A')}%"
    if score.data_insufficient:
        return "数据不足，需复核"
    if score.missing_data:
        return "部分缺失，需补充 " + "、".join(metric_label(item) for item in score.missing_data[:3])
    return "核心数据可用"


def _format_timestamp(value: str | None) -> str:
    if not value:
        return "N/A"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%Y-%m-%d %H:%M")


def _position_limit_text(value: float | None) -> str:
    if value is None:
        return "N/A"
    if value <= 0:
        return "不建议新增"
    return f"≤{value:g}%"


def _format_plan_currency(value: object) -> str:
    try:
        return format_currency(float(value))
    except (TypeError, ValueError):
        return str(value or "N/A")


def _format_plain(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{value:.1f}"


def _format_disclosure_value(value: object, unit: object = None) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if unit == "percent":
        return format_percent(number, already_percent=False)
    if unit == "x":
        return format_multiple(number)
    if unit == "usd":
        return format_large_number(number)
    return f"{number:g}"


def _truncate(value: str, limit: int) -> str:
    clean = " ".join(str(value).split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1] + "…"


def _number_text(value: object) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _parse_optional_number(value: object, is_percent: bool = False) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    has_percent_sign = text.endswith("%")
    if has_percent_sign:
        text = text[:-1].strip()
    try:
        number = float(text)
    except ValueError:
        return None
    if has_percent_sign or (is_percent and abs(number) > 1.5):
        return number / 100
    return number


def _severity_label(severity: str) -> str:
    return {"high": "高", "medium": "中", "info": "信息"}.get(severity, severity)


def _render_short_list(items: list[str], empty: str) -> None:
    if not items:
        st.caption(empty)
        return
    for item in items[:6]:
        st.markdown(f"- {metric_label(item)}")


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _buy_point_status_parts(score, buy_zone: BuyZoneEstimate | None = None) -> tuple[str, str, str, str]:
    row = pd.Series(
        {
            "entryRating": getattr(score, "entry_rating", ""),
            "valuationStatus": getattr(score, "valuation_status", ""),
            "action": getattr(score, "action", ""),
        }
    )
    label, grade, raw = dashboard_ui._entry_rating_display_parts(row)
    sanity_label = _buy_point_sanity_label_for_zone(buy_zone)
    if sanity_label:
        label = sanity_label
        raw = sanity_label
    tone = dashboard_ui._buy_point_label_tone(label)
    return label, grade, raw, tone


def _buy_point_status_text(score, buy_zone: BuyZoneEstimate | None = None) -> str:
    label, grade, _raw, _tone = _buy_point_status_parts(score, buy_zone)
    return dashboard_ui._entry_rating_chip_text(label, grade)


def _buy_point_status_pill_html(score, buy_zone: BuyZoneEstimate | None = None) -> str:
    label, grade, raw, tone = _buy_point_status_parts(score, buy_zone)
    background, foreground, border = dashboard_ui.BADGE_STYLES.get(tone, dashboard_ui.BADGE_STYLES["gray"])
    display_text = dashboard_ui._entry_rating_chip_text(label, grade)
    return (
        f'<span class="detail-pill buy-point-pill" title="{escape(raw)}" '
        f'style="background:{background};color:{foreground};border-color:{border};">'
        f"<b>{escape(display_text)}</b></span>"
    )


def _buy_point_sanity_label_for_zone(buy_zone: BuyZoneEstimate | None) -> str | None:
    if buy_zone is None:
        return None
    zone = str(getattr(buy_zone, "currentZone", "") or "")
    price = _first_number(getattr(buy_zone, "currentPrice", None))
    if price is None or price <= 0:
        return None
    if zone in {"tranche_buy", "heavy_buy", "below_heavy_buy"}:
        return None
    tranche_low = _first_number(getattr(buy_zone, "trancheBuyLow", None))
    tranche_high = _first_number(getattr(buy_zone, "trancheBuyHigh", None))
    if _price_in_range(price, tranche_low, tranche_high):
        return None
    fair_low = _first_number(getattr(buy_zone, "fairValueLow", None))
    fair_high = _first_number(getattr(buy_zone, "fairValueHigh", None))
    if zone == "fair_observation" and _price_in_range(price, fair_low, fair_high):
        return FAIR_OBSERVATION_NOT_BUY_LABEL
    trigger = _first_number(getattr(buy_zone, "nextTriggerPrice", None), tranche_high)
    if trigger is None or trigger <= 0 or price <= trigger:
        return None
    distance = max((price - trigger) / price * 100, 0)
    if distance > NEAR_BUY_TRIGGER_THRESHOLD_PCT:
        return FAIR_OBSERVATION_NOT_BUY_LABEL
    return None


def _price_in_range(price: float, low: float | None, high: float | None) -> bool:
    if low is None or high is None:
        return False
    return low <= price <= high


def _rating_color(value: str) -> str:
    if "极贵" in value or "禁止追高" in value:
        return "deepred"
    if "偏贵" in value:
        return "orange"
    if "击球区" in value or "回撤买点" in value or "合理偏便宜" in value:
        return "green"
    if "接近" in value or "等回踩" in value:
        return "blue"
    if "只观察" in value or "待复核" in value:
        return "yellow"
    if value.startswith(("A", "B")) or value == "低":
        return "green"
    if "中高" in value:
        return "orange"
    if "高" in value or value.startswith("D"):
        return "red"
    if value.startswith("C") or "中" in value:
        return "yellow"
    return "blue"


def _pill_html(value: str, color: str) -> str:
    styles = {
        "green": ("#F4FAF6", "#166534", "#DDEBE2"),
        "blue": ("#F4F7FB", "#36516F", "#DCE6F2"),
        "yellow": ("#FCFAF0", "#7A5C12", "#EEE6C8"),
        "orange": ("#FBF7F1", "#7C4A1D", "#ECDCC8"),
        "red": ("#FBF5F5", "#8A1F1F", "#ECD5D5"),
        "deepred": ("#FDF1F1", "#6F1111", "#E7B9B9"),
    }
    background, foreground, border = styles.get(color, styles["blue"])
    return f'<span class="detail-pill" style="background:{background};color:{foreground};border-color:{border};">{escape(value)}</span>'


def _hero_item_html(label: str, value: object, raw_html: bool = False) -> str:
    value_html = str(value) if raw_html else escape(str(value))
    class_name = "detail-hero-item has-inline-pill" if raw_html else "detail-hero-item"
    return (
        f'<div class="{class_name}">'
        f'<span>{escape(label)}</span>'
        f"<strong>{value_html}</strong>"
        "</div>"
    )


def _render_detail_styles() -> None:
    st.markdown(
        """
        <style>
        .stock-research-header {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            margin: 0.15rem 0 0.7rem;
        }
        .stock-research-header p {
            margin: 0;
            color: #0f172a;
            font-size: 1.42rem;
            line-height: 1.15;
            font-weight: 760;
            letter-spacing: 0;
        }
        .stock-research-header span {
            display: block;
            margin-top: 0.25rem;
            color: #64748b;
            font-size: 0.86rem;
            line-height: 1.35;
        }
        .research-card {
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 0.65rem;
            background: rgba(255, 255, 255, 0.92);
            box-shadow: 0 12px 30px rgba(15, 23, 42, 0.04);
        }
        .detail-hero {
            display: grid;
            grid-template-columns: minmax(220px, 0.8fr) minmax(420px, 1.5fr);
            gap: 0.85rem;
            align-items: stretch;
            padding: 0.9rem;
            margin: 0.25rem 0 0.75rem;
            border: 1px solid rgba(15, 23, 42, 0.10);
            border-radius: 0.65rem;
            background: linear-gradient(135deg, rgba(255,255,255,0.98), rgba(248,250,252,0.96));
            box-shadow: 0 12px 30px rgba(15, 23, 42, 0.05);
        }
        .detail-eyebrow {
            color: #2563eb;
            font-size: 0.78rem;
            font-weight: 780;
            letter-spacing: 0;
        }
        .detail-hero h2 {
            margin: 0.2rem 0 0.2rem;
            color: #111827;
            font-size: 2rem;
            line-height: 1;
            font-weight: 820;
        }
        .detail-hero p {
            margin: 0;
            color: #667085;
            font-size: 0.92rem;
            line-height: 1.45;
        }
        .detail-hero-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.45rem;
        }
        .detail-hero-item {
            min-height: 3.6rem;
            padding: 0.48rem 0.55rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 0.5rem;
            background: rgba(255, 255, 255, 0.74);
        }
        .detail-hero-item span {
            display: block;
            color: #667085;
            font-size: 0.72rem;
            font-weight: 690;
            line-height: 1.2;
        }
        .detail-hero-item strong {
            display: block;
            margin-top: 0.24rem;
            color: #172033;
            font-size: 0.86rem;
            line-height: 1.25;
            font-weight: 760;
            overflow-wrap: anywhere;
        }
        .detail-hero-item.has-inline-pill {
            background: rgba(255, 255, 255, 0.68);
        }
        .detail-hero-item.has-inline-pill strong {
            margin-top: 0.28rem;
            overflow: hidden;
        }
        .current-position-strip {
            display: grid;
            grid-template-columns: minmax(150px, 0.38fr) minmax(0, 1fr);
            gap: 0.65rem;
            align-items: stretch;
            margin: -0.2rem 0 0.85rem;
            padding: 0.62rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 0.55rem;
            background: #FFFFFF;
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.04);
        }
        .current-position-title {
            display: flex;
            flex-direction: column;
            justify-content: center;
            min-height: 3.3rem;
            padding: 0.42rem 0.55rem;
            border-left: 3px solid #2563eb;
            background: #F8FAFC;
        }
        .current-position-title span,
        .current-position-item span {
            color: #64748b;
            font-size: 0.68rem;
            font-weight: 760;
            line-height: 1.2;
        }
        .current-position-title strong {
            margin-top: 0.18rem;
            color: #0f172a;
            font-size: 1rem;
            line-height: 1.1;
        }
        .current-position-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 0.42rem;
        }
        .current-position-item {
            min-height: 3.3rem;
            padding: 0.42rem 0.5rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            border-radius: 0.45rem;
            background: #FBFCFE;
        }
        .current-position-item b {
            display: block;
            margin-top: 0.18rem;
            color: #111827;
            font-size: 0.78rem;
            line-height: 1.25;
            font-weight: 780;
            overflow-wrap: anywhere;
        }
        .detail-pill {
            display: inline-flex;
            align-items: center;
            min-height: 18px;
            padding: 0.05rem 0.42rem;
            border: 1px solid;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 650;
            line-height: 1.35;
        }
        .detail-pill.buy-point-pill {
            gap: 0.24rem;
            min-height: 20px;
            max-width: 100%;
            padding: 0.04rem 0.42rem;
            border-radius: 999px;
            font-size: 11px;
            line-height: 1.25;
            white-space: nowrap;
        }
        .detail-pill.buy-point-pill b {
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            font-weight: 680;
        }
        .detail-pill.buy-point-pill em {
            flex: 0 0 auto;
            color: #64748b;
            font-size: 0.72rem;
            font-style: normal;
            font-weight: 690;
            opacity: 0.82;
        }
        [class*="st-key-stock-detail-refresh-"] button,
        [class*="st-key-stock-detail-record-signal"] button {
            min-height: 26px !important;
            height: 26px !important;
            padding: 0 0.56rem !important;
            border-radius: 4px !important;
            border-color: rgba(15, 23, 42, 0.10) !important;
            background: #FFFFFF !important;
            color: #52657F !important;
            box-shadow: none !important;
            font-size: 12px !important;
            font-weight: 700 !important;
        }
        [class*="st-key-stock-detail-refresh-"] button p,
        [class*="st-key-stock-detail-record-signal"] button p {
            font-size: 12px !important;
            font-weight: 700 !important;
            line-height: 1 !important;
        }
        [class*="st-key-stock-detail-refresh-"] button:hover,
        [class*="st-key-stock-detail-record-signal"] button:hover {
            border-color: rgba(15, 23, 42, 0.12) !important;
            background: #FFFFFF !important;
            color: #0F172A !important;
        }
        .conclusion-card,
        .buy-zone-panel,
        .plan-summary-card,
        .memo-summary-card,
        .missing-summary-card {
            padding: 0.78rem 0.86rem;
            margin-bottom: 0.75rem;
        }
        .conclusion-main {
            color: #0f172a;
            font-size: 1.08rem;
            font-weight: 760;
            line-height: 1.25;
        }
        .conclusion-card p {
            margin: 0.32rem 0 0.62rem;
            color: #475569;
            font-size: 0.88rem;
            line-height: 1.45;
        }
        .conclusion-grid,
        .buy-zone-meta,
        .plan-summary-grid,
        .data-summary-card {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.5rem;
        }
        .buy-zone-meta {
            grid-template-columns: repeat(5, minmax(0, 1fr));
        }
        .conclusion-grid div,
        .buy-zone-meta div,
        .plan-summary-grid div,
        .data-summary-card div {
            min-height: 3.1rem;
            padding: 0.48rem 0.55rem;
            border-radius: 0.5rem;
            background: rgba(248, 250, 252, 0.82);
            border: 1px solid rgba(15, 23, 42, 0.06);
        }
        .conclusion-grid span,
        .buy-zone-meta span,
        .plan-summary-grid span,
        .data-summary-card span {
            display: block;
            color: #64748b;
            font-size: 0.72rem;
            font-weight: 650;
            line-height: 1.2;
        }
        .conclusion-grid strong,
        .buy-zone-meta strong,
        .plan-summary-grid strong,
        .data-summary-card strong {
            display: block;
            margin-top: 0.25rem;
            color: #0f172a;
            font-size: 0.86rem;
            line-height: 1.28;
            font-weight: 720;
            overflow-wrap: anywhere;
        }
        .buy-zone-meta strong .buy-point-pill {
            display: inline-flex;
            align-items: center;
            width: auto;
            max-width: 100%;
            margin-top: -0.02rem;
            font-size: 0.78rem;
            line-height: 1;
        }
        .wait-list {
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
            margin-top: 0.65rem;
        }
        .wait-list span {
            padding: 0.18rem 0.5rem;
            border-radius: 999px;
            color: #475569;
            background: rgba(241, 245, 249, 0.9);
            border: 1px solid rgba(15, 23, 42, 0.06);
            font-size: 0.75rem;
            font-weight: 620;
        }
        .buy-zone-panel {
            display: grid;
            gap: 0.65rem;
        }
        .buy-zone-ladder {
            display: grid;
            gap: 0.3rem;
        }
        .buy-zone-row {
            display: grid;
            grid-template-columns: 116px minmax(120px, 1fr) minmax(120px, 1.1fr);
            align-items: center;
            min-height: 2.05rem;
            padding: 0.28rem 0.55rem;
            border-radius: 0.45rem;
            border: 1px solid rgba(15, 23, 42, 0.05);
            background: rgba(248, 250, 252, 0.6);
            gap: 0.5rem;
        }
        .buy-zone-row span {
            color: #334155;
            font-size: 0.78rem;
            font-weight: 690;
        }
        .buy-zone-row strong {
            color: #0f172a;
            font-size: 0.8rem;
            font-weight: 720;
        }
        .buy-zone-row em {
            color: #64748b;
            font-size: 0.76rem;
            font-style: normal;
            text-align: right;
        }
        .data-summary-card {
            padding: 0.72rem;
            margin-bottom: 0.4rem;
        }
        .missing-summary-card {
            display: grid;
            gap: 0.6rem;
        }
        .missing-summary-message {
            margin: 0;
            color: #334155;
            font-size: 0.86rem;
            line-height: 1.45;
            font-weight: 620;
        }
        .missing-summary-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.48rem;
        }
        .missing-summary-grid div {
            min-height: 4.1rem;
            padding: 0.5rem 0.58rem;
            border-radius: 0.5rem;
            background: rgba(248, 250, 252, 0.76);
            border: 1px solid rgba(15, 23, 42, 0.06);
        }
        .missing-summary-grid span,
        .missing-gap-heading span {
            display: block;
            color: #64748b;
            font-size: 0.72rem;
            font-weight: 670;
            line-height: 1.2;
        }
        .missing-summary-grid strong {
            display: block;
            margin-top: 0.25rem;
            color: #0f172a;
            font-size: 0.92rem;
            line-height: 1.2;
            font-weight: 760;
        }
        .missing-summary-grid em {
            display: block;
            margin-top: 0.2rem;
            color: #64748b;
            font-size: 0.72rem;
            line-height: 1.25;
            font-style: normal;
        }
        .missing-summary-action {
            margin: 0;
            color: #64748b;
            font-size: 0.76rem;
            line-height: 1.3;
        }
        .missing-gap-groups {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.5rem;
            margin: 0.5rem 0 0.65rem;
        }
        .missing-gap-group {
            padding: 0.58rem 0.62rem;
            box-shadow: none;
            background: rgba(255, 255, 255, 0.82);
        }
        .missing-gap-heading {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.4rem;
        }
        .missing-gap-heading strong {
            color: #334155;
            font-size: 0.72rem;
            font-weight: 720;
            white-space: nowrap;
        }
        .missing-gap-group ul {
            margin: 0.45rem 0 0;
            padding-left: 1rem;
            color: #475569;
            font-size: 0.75rem;
            line-height: 1.45;
        }
        .missing-gap-group li {
            margin: 0.12rem 0;
        }
        .missing-gap-group p {
            margin: 0.35rem 0 0;
            color: #94a3b8;
            font-size: 0.72rem;
        }
        .memo-summary-card {
            display: grid;
            gap: 0.55rem;
        }
        .memo-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.5rem;
        }
        .memo-item {
            min-height: 4.2rem;
            padding: 0.55rem 0.6rem;
            border-radius: 0.5rem;
            background: rgba(248, 250, 252, 0.7);
            border: 1px solid rgba(15, 23, 42, 0.06);
        }
        .memo-item span,
        .memo-last-review span {
            display: block;
            color: #64748b;
            font-size: 0.72rem;
            font-weight: 670;
            line-height: 1.2;
        }
        .memo-item strong {
            display: -webkit-box;
            margin-top: 0.28rem;
            color: #0f172a;
            font-size: 0.82rem;
            font-weight: 690;
            line-height: 1.35;
            overflow: hidden;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
        }
        .memo-item strong.memo-placeholder {
            color: #94a3b8;
            font-weight: 560;
        }
        .memo-last-review {
            padding: 0.5rem 0.6rem;
            border-radius: 0.5rem;
            background: rgba(255, 255, 255, 0.72);
            border: 1px solid rgba(15, 23, 42, 0.05);
        }
        .memo-last-review strong {
            display: block;
            margin-top: 0.24rem;
            color: #334155;
            font-size: 0.8rem;
            font-weight: 620;
            line-height: 1.35;
            overflow-wrap: anywhere;
        }
        .memo-footnote {
            margin: 0;
            color: #94a3b8;
            font-size: 0.72rem;
            line-height: 1.25;
        }
        @media (max-width: 900px) {
            .detail-hero {
                grid-template-columns: 1fr;
            }
            .current-position-strip {
                grid-template-columns: 1fr;
            }
            .detail-hero-grid,
            .current-position-grid,
            .conclusion-grid,
            .buy-zone-meta,
            .plan-summary-grid,
            .data-summary-card,
            .memo-grid,
            .missing-summary-grid,
            .missing-gap-groups {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .buy-zone-row {
                grid-template-columns: 1fr;
            }
            .buy-zone-row em {
                text-align: left;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
