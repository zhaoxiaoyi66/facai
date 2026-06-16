from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
import time
from typing import Any

import pandas as pd
import streamlit as st

from data.action_fusion import action_fusion_card_html, evaluate_action_fusion
from data.ai_stock_radar import RADAR_REPORT_VERSION, RadarScores, build_ai_stock_radar_list_row, build_ai_stock_radar_report
from data.buy_zone_display import build_buy_zone_display
from data.buy_zone_engine import build_buy_zone_context
from data.entry_display import format_buy_zone, format_zone_status
from data.market_context import build_market_context, build_market_history
from data.portfolio_targets import build_action_fusion_portfolio_context
from data.sector_localization import format_company_track, get_ticker_research_track
from data.stock_plan import StockPlanStore
from data.volume_price_acceptance import evaluate_volume_price_acceptance
from settings import load_watchlist
from ui.theme import render_page_header


REPORT_ROW_TTL_SECONDS = 120
QUOTE_TTL_SECONDS = 120
HISTORY_TTL_SECONDS = 600
PORTFOLIO_TTL_SECONDS = 30
_FALLBACK_REPORT_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}

AI_INFRA_BUSINESS_TYPE_BY_TICKER = {
    "CRWV": "AI_INFRA_CLOUD",
    "NBIS": "GPU_CLOUD",
}
AI_INFRA_MODEL_ALIASES = {
    "AI_CLOUD_INFRA": "AI_INFRA_CLOUD",
    "AI_INFRA_HIGH_RISK": "GPU_CLOUD",
    "AI_INFRA_CLOUD": "AI_INFRA_CLOUD",
    "GPU_CLOUD": "GPU_CLOUD",
    "NEOCLOUD": "NEOCLOUD",
}
AI_INFRA_DISPLAY = {
    "AI_INFRA_CLOUD": "AI云基础设施",
    "GPU_CLOUD": "GPU云 / Neocloud",
    "NEOCLOUD": "Neocloud 云算力",
}
AI_INFRA_FIELD_SPECS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("revenue_growth", "收入高增长", ("revenue_growth", "revenueGrowth", "revenue_growth_yoy", "revenueGrowthYoy"), "ratio"),
    ("revenue_backlog", "收入积压 / RPO", ("revenue_backlog", "revenueBacklog", "contracted_backlog", "contractedBacklog", "aiCloudContractedBacklog"), "money"),
    ("backlog_to_ev", "收入积压 / EV", ("backlog_to_ev", "backlogToEv", "backlog_ev_ratio", "backlogEvRatio"), "multiple"),
    ("active_power_gw", "已投运电力容量", ("active_power_gw", "activePowerGw", "active_power", "activePower"), "gw"),
    ("contracted_power_gw", "已签约电力容量", ("contracted_power_gw", "contractedPowerGw", "contracted_power", "contractedPower"), "gw"),
    ("capex", "资本开支", ("capex", "capital_expenditures", "capitalExpenditures", "capex_commitments", "capexCommitments", "aiCloudCapexCommitments"), "money"),
    ("operating_cash_flow", "经营现金流", ("operating_cash_flow", "operatingCashFlow"), "money"),
    ("free_cash_flow_after_capex", "扣资本开支后自由现金流", ("free_cash_flow_after_capex", "freeCashFlowAfterCapex", "free_cash_flow", "freeCashFlow"), "money"),
    ("net_debt", "净债务", ("net_debt", "netDebt", "aiCloudNetDebt"), "money"),
    ("interest_expense_to_revenue", "利息费用 / 收入", ("interest_expense_to_revenue", "interestExpenseToRevenue", "interest_burden", "interestBurden", "aiCloudInterestBurden"), "ratio"),
    ("adjusted_ebitda_margin", "调整后 EBITDA 利润率", ("adjusted_ebitda_margin", "adjustedEbitdaMargin"), "ratio"),
    ("customer_concentration", "客户集中度", ("customer_concentration", "customerConcentration", "aiCloudCustomerConcentration"), "ratio"),
    ("financing_risk", "融资风险", ("financing_risk", "financingRisk", "debt_maturity", "debtMaturity", "aiCloudDebtMaturity"), "text"),
    ("data_center_delivery_risk", "数据中心交付风险", ("data_center_delivery_risk", "dataCenterDeliveryRisk"), "text"),
    ("gpu_supplier_dependency", "GPU 供应商依赖", ("gpu_supplier_dependency", "gpuSupplierDependency", "nvidia_supply_exposure", "nvidiaSupplyExposure", "aiCloudNvidiaSupplyExposure"), "text"),
)


@dataclass
class PerfStage:
    name: str
    elapsed_ms: float
    cache_hit: bool | None = None
    external_api: bool = False
    note: str = ""


@dataclass
class PerfProbe:
    started_at: float = field(default_factory=time.perf_counter)
    stages: list[PerfStage] = field(default_factory=list)

    def add(
        self,
        name: str,
        elapsed_ms: float,
        *,
        cache_hit: bool | None = None,
        external_api: bool = False,
        note: str = "",
    ) -> None:
        self.stages.append(
            PerfStage(
                name=name,
                elapsed_ms=elapsed_ms,
                cache_hit=cache_hit,
                external_api=external_api,
                note=note,
            )
        )

    @property
    def total_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000


@dataclass
class StockReportContext:
    symbol: str
    row: dict[str, Any]
    snapshot: dict[str, Any]
    technicals: dict[str, Any]
    report: dict[str, Any]
    market: dict[str, Any]
    history: pd.DataFrame
    buy_zone_context: dict[str, Any]
    buy_zone_display: dict[str, Any]
    action_result: Any
    conclusion: dict[str, Any]
    portfolio_context: dict[str, Any]
    data_health: dict[str, Any]
    performance: PerfProbe
    history_loaded: bool = False


def render() -> None:
    perf = PerfProbe()
    stage_start = time.perf_counter()
    _render_styles()
    render_page_header("AI Stock Radar", "只读本地缓存，生成单票纪律雷达；价格到达和评分通过都不是自动交易信号。")
    perf.add("页面基础渲染", (time.perf_counter() - stage_start) * 1000, cache_hit=None, external_api=False)
    stage_start = time.perf_counter()
    tickers, source = select_radar_symbols(load_watchlist())
    perf.add("radar ticker / selected ticker 读取", (time.perf_counter() - stage_start) * 1000, cache_hit=None, external_api=False)
    if not tickers:
        st.info("观察池为空。")
        return

    view = _selected_radar_view()
    selected = _selected_symbol(tickers)
    if view == "report":
        _render_report_view(selected, tickers, perf)
        return
    _render_list(tickers, "", source)


def _render_list(tickers: list[str], selected: str, source: str) -> None:
    rows = _sort_rows([_list_row(ticker) for ticker in tickers])
    filter_key = _selected_radar_filter_key()
    filter_counts = _filter_counts(rows)
    summary_html = _research_summary_cards_html(rows)
    st.markdown(_filter_chips_html(filter_key, filter_counts), unsafe_allow_html=True)
    if st.button(
        "刷新 / 重建买区上下文",
        key="ai-radar-rebuild-buy-zone-context",
        help="清理本页运行期缓存，重新读取本地缓存并生成统一买区上下文。",
    ):
        _clear_report_runtime_cache()
        st.rerun()
    filtered_rows = _filter_rows(rows, filter_key)
    visible_rows = filtered_rows
    body = "".join(_list_row_html(row, selected) for row in visible_rows)
    st.markdown(
        (
            f"{summary_html}"
            '<section class="ai-radar-list-card">'
            f'<div class="ai-radar-section-head"><strong>Radar 研究入口</strong><span>{len(visible_rows)}/{len(filtered_rows)} 只｜来源：{escape(source)}｜只读缓存，不自动刷新</span></div>'
            '<p class="ai-radar-list-note">研究入口按接近买区、等待确认、等待回落和数据质量排序；完整评分、区间判断和风险依据请进入单股研报页。</p>'
            '<div class="ai-radar-table-wrap">'
            '<table class="ai-radar-table">'
            "<thead><tr>"
            "<th>Ticker</th><th>当前价</th><th>Radar 状态</th><th>研究优先级</th><th>距买区</th><th>下一触发</th><th>买区置信度</th><th>数据质量</th><th>更新时间</th><th>查看</th>"
            "</tr></thead>"
            f"<tbody>{body}</tbody>"
            "</table>"
            "</div>"
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _render_report_view(symbol: str, tickers: list[str], perf: PerfProbe | None = None) -> None:
    known = {ticker.upper() for ticker in tickers}
    if not symbol or symbol not in known:
        st.markdown(_report_not_found_html(symbol), unsafe_allow_html=True)
        return
    st.markdown(_report_view_toolbar_html(symbol, "加载中", "等待缓存读取"), unsafe_allow_html=True)
    _render_report(symbol, perf or PerfProbe())


def _render_report(symbol: str, perf: PerfProbe | None = None) -> None:
    perf = perf or PerfProbe()
    st.markdown('<div id="radar-report"></div>', unsafe_allow_html=True)
    shell = st.empty()
    shell.markdown(_report_loading_shell_html(symbol), unsafe_allow_html=True)
    _render_report_refresh_control(symbol)
    context = build_stock_report_context(symbol, perf=perf, load_history=False)
    shell.markdown(
        _report_html(
            context.report,
            context.market,
            context.snapshot,
            context.technicals,
            context.row,
            context.history,
            action_result=context.action_result,
            conclusion=context.conclusion,
            portfolio_context=context.portfolio_context,
            buy_zone_context=context.buy_zone_context,
            buy_zone_display=context.buy_zone_display,
            data_health=context.data_health,
            include_appendix=False,
            perf=context.performance,
        ),
        unsafe_allow_html=True,
    )
    _render_report_appendix_section(context)
    with st.expander("评分依据 / 数据诊断", expanded=False):
        st.markdown(_debug_html(context.report.get("debug") or {}, context.report), unsafe_allow_html=True)
    with st.expander("性能诊断", expanded=False):
        st.markdown(_performance_diagnostics_html(context.performance), unsafe_allow_html=True)


def build_stock_report_context(symbol: str, *, perf: PerfProbe | None = None, load_history: bool = False) -> StockReportContext:
    perf = perf or PerfProbe()
    symbol = str(symbol or "").strip().upper()
    row = _cached_report_row(symbol, perf)
    snapshot = _dict_value(row, "rawSnapshot") or {}
    technicals = _dict_value(row, "rawTechnicals") or {}
    perf.add("financials / fundamentals 读取", 0.0, cache_hit=True, external_api=False, note="来自 Radar 行缓存")
    market = _cached_market_context(symbol, perf)
    technicals = _enrich_technical_context(symbol, row or {}, snapshot, technicals, market)
    perf.add("technical_data 读取", 0.0, cache_hit=True, external_api=False, note="来自 Radar 行缓存 / 轻量派生")
    stage_start = time.perf_counter()
    report_obj = build_ai_stock_radar_report(
        symbol,
        company_name=str(_row_value(row, "companyName") or symbol),
        scores=None if snapshot and technicals else _scores_from_row(row),
        snapshot=snapshot,
        technicals=technicals,
        bull_points=_list_value(row, "keyPositiveDrivers"),
        risk_points=_list_value(row, "keyNegativeDrivers"),
        watch_points=_watch_points(row),
        market=market,
    )
    report = report_obj.to_dict()
    report.update(_report_technical_overlay(technicals))
    report.update(_report_manual_target_fields(symbol, report))
    perf.add("估值区间计算", (time.perf_counter() - stage_start) * 1000, cache_hit=False, external_api=False, note="复用已读取 quote")
    history = _cached_market_history(symbol, perf) if load_history else _empty_history_frame()
    if not load_history:
        perf.add("历史 K 线读取", 0.0, cache_hit=None, external_api=False, note="延后到附录按需加载")
    volume_snapshot = _volume_price_acceptance_snapshot(report, technicals, row or {}, history)
    technicals = _apply_volume_snapshot_to_technicals(technicals, volume_snapshot)
    report.update(_report_technical_overlay(technicals))
    stage_start = time.perf_counter()
    buy_zone_context = build_buy_zone_context(report, technicals=technicals, volume_snapshot=volume_snapshot).to_dict()
    perf.add("统一买区上下文生成", (time.perf_counter() - stage_start) * 1000, cache_hit=False, external_api=False)
    portfolio_snapshot = _cached_portfolio_context(symbol, perf)
    stage_start = time.perf_counter()
    action_result = _action_fusion_result_from_snapshots(report, technicals, row or {}, volume_snapshot, portfolio_snapshot)
    perf.add("action_fusion / trade_conclusion 生成", (time.perf_counter() - stage_start) * 1000, cache_hit=False, external_api=False)
    stage_start = time.perf_counter()
    portfolio_context = _portfolio_context(report, row or {}, action_result, buy_zone_context)
    perf.add("portfolio_context 展示组装", (time.perf_counter() - stage_start) * 1000, cache_hit=True, external_api=False, note="复用 Action Fusion snapshot")
    buy_zone_display = build_buy_zone_display(
        buy_zone_context,
        {
            **(row or {}),
            **(portfolio_context or {}),
            "actionFusion": action_result.to_dict() if hasattr(action_result, "to_dict") else {},
        },
        mode="report",
    )
    portfolio_context = _portfolio_context_with_buy_zone_display(portfolio_context, buy_zone_display)
    conclusion = _trade_conclusion(report, action_result, buy_zone_context, buy_zone_display)
    stage_start = time.perf_counter()
    data_health = _data_health_context(report, market, snapshot, row or {}, portfolio_context, buy_zone_context)
    perf.add("data_health 生成", (time.perf_counter() - stage_start) * 1000, cache_hit=False, external_api=False)
    return StockReportContext(
        symbol=symbol,
        row=row or {},
        snapshot=snapshot,
        technicals=technicals,
        report=report,
        market=market,
        history=history,
        buy_zone_context=buy_zone_context,
        buy_zone_display=buy_zone_display,
        action_result=action_result,
        conclusion=conclusion,
        portfolio_context=portfolio_context,
        data_health=data_health,
        performance=perf,
        history_loaded=load_history,
    )


def _render_report_refresh_control(symbol: str) -> None:
    columns = st.columns([1, 6])
    with columns[0]:
        if st.button("刷新研报缓存", key=f"ai-radar-refresh-{symbol}", help="清理本页运行期缓存，重新读取本地缓存。"):
            _clear_report_runtime_cache(symbol)
            st.rerun()


def _render_report_appendix_section(context: StockReportContext) -> None:
    load_appendix = st.toggle(
        "加载附录数据",
        value=False,
        key=f"ai-radar-load-appendix-{context.symbol}",
        help="附录包含行情明细、财务摘要、市场表现和数据完整度，默认不阻塞首屏。",
    )
    if not load_appendix:
        st.markdown(_appendix_lazy_placeholder_html(), unsafe_allow_html=True)
        context.performance.add("附录数据生成", 0.0, cache_hit=None, external_api=False, note="未加载")
        return
    history = _cached_market_history(context.symbol, context.performance)
    stage_start = time.perf_counter()
    appendix_html = _report_appendix_html(
        context.report,
        context.market,
        context.snapshot,
        context.technicals,
        context.row,
        history,
        context.data_health,
    )
    context.performance.add("附录数据生成", (time.perf_counter() - stage_start) * 1000, cache_hit=False, external_api=False, note="用户手动加载")
    st.markdown(appendix_html, unsafe_allow_html=True)


def _cached_report_row(symbol: str, perf: PerfProbe) -> dict[str, Any] | None:
    return _runtime_cached(
        ("report_row", symbol),
        REPORT_ROW_TTL_SECONDS,
        lambda: _single_report_row(symbol),
        perf,
        "Radar 行 / profile 读取",
    )


def _cached_market_context(symbol: str, perf: PerfProbe) -> dict[str, Any]:
    return _runtime_cached(
        ("market_context", symbol),
        QUOTE_TTL_SECONDS,
        lambda: build_market_context(symbol),
        perf,
        "quote 读取",
    )


def _cached_market_history(symbol: str, perf: PerfProbe) -> pd.DataFrame:
    return _runtime_cached(
        ("market_history", symbol),
        HISTORY_TTL_SECONDS,
        lambda: build_market_history(symbol),
        perf,
        "历史 K 线读取",
    )


def _cached_portfolio_context(symbol: str, perf: PerfProbe) -> dict[str, Any]:
    return _runtime_cached(
        ("portfolio_context", symbol),
        PORTFOLIO_TTL_SECONDS,
        lambda: build_action_fusion_portfolio_context(symbol),
        perf,
        "portfolio_context 读取",
    )


def _report_manual_target_fields(symbol: str, report: dict[str, Any]) -> dict[str, Any]:
    try:
        plan = StockPlanStore().get_plan(symbol)
    except Exception:
        return {}
    status = str(plan.get("plan_status") or plan.get("planStatus") or "").strip().lower()
    if status in {"completed", "cancelled", "expired"}:
        return {}
    target = _first_number(plan, "target_sell_price", "targetSellPrice")
    price = _first_number(report, "current_price", "currentPrice", "price")
    if target is None or (price is not None and target <= price * 1.0001):
        return {}
    return {
        "manual_target_price": target,
        "manual_target_source": "stock_plan.target_sell_price",
    }


def _enrich_technical_context(
    symbol: str,
    row: dict[str, Any],
    snapshot: dict[str, Any],
    technicals: dict[str, Any],
    market: dict[str, Any],
) -> dict[str, Any]:
    ctx = dict(technicals or {})
    ctx.setdefault("ticker", symbol)
    current_price = _first_number(ctx, row, snapshot, market, "current_price", "currentPrice", "price", "close")
    previous_close = _first_number(ctx, row, snapshot, market, "previous_close", "previousClose", "prev_close", "prevClose", "latestClose")
    latest_volume = _first_number(ctx, row, snapshot, market, "volume", "latest_volume", "latestVolume", "quoteVolume")
    avg_volume_20d = _first_number(ctx, row, snapshot, market, "avg_volume_20d", "avgVolume20d", "volume_ma20", "volumeMa20", "avg_volume", "avgVolume")
    ma20 = _first_number(ctx, row, snapshot, "ma20", "MA20", "ema20")
    ma50 = _first_number(ctx, row, snapshot, "ma50", "MA50", "ema50")
    ma200 = _first_number(ctx, row, snapshot, "ma200", "MA200", "ema200")
    atr14 = _first_number(ctx, row, snapshot, "atr_14", "atr14", "ATR14")
    rsi14 = _first_number(ctx, row, snapshot, "rsi_14", "rsi14", "RSI14")
    swing_low = _first_number(ctx, row, snapshot, "swing_low", "recent_swing_low", "recentSwingLow")
    swing_high = _first_number(ctx, row, snapshot, "swing_high", "recent_swing_high", "recentSwingHigh", "recent_breakout_level")
    support_low = _first_number(ctx, row, snapshot, "support_zone_low", "supportWatchZoneLow", "deep_support_zone_low", "support_watch_zone_low")
    support_high = _first_number(ctx, row, snapshot, "support_zone_high", "supportWatchZoneHigh", "deep_support_zone_high", "support_watch_zone_high")
    resistance_low = _first_number(ctx, row, snapshot, "resistance_zone_low", "resistanceZoneLow", "trend_reclaim_zone_low", "confirmation_price")
    resistance_high = _first_number(ctx, row, snapshot, "resistance_zone_high", "resistanceZoneHigh", "recent_swing_high", "recent_breakout_level", "chase_above_price")
    invalidation = _first_number(ctx, row, snapshot, "invalidation_price", "radar_invalidation_price", "invalid_line")

    if current_price is not None:
        ctx.setdefault("current_price", current_price)
        ctx.setdefault("price", current_price)
    if previous_close is not None:
        ctx.setdefault("previous_close", previous_close)
    if current_price is not None and previous_close not in (None, 0):
        ctx.setdefault("day_change_pct", (current_price / previous_close - 1.0) * 100.0)
        ctx.setdefault("daily_return_pct", (current_price / previous_close - 1.0) * 100.0)
    if latest_volume is not None:
        ctx.setdefault("volume", latest_volume)
        ctx.setdefault("latest_volume", latest_volume)
        ctx.setdefault("volume_source", "quote")
    if avg_volume_20d is not None:
        ctx.setdefault("avg_volume_20d", avg_volume_20d)
        ctx.setdefault("volume_ma20", avg_volume_20d)
    if latest_volume is not None and avg_volume_20d not in (None, 0):
        ctx.setdefault("volume_ratio", latest_volume / avg_volume_20d)
    for alias, value in (("ma20", ma20), ("ema20", ma20), ("ma50", ma50), ("ema50", ma50), ("ma200", ma200), ("ema200", ma200)):
        if value is not None:
            ctx.setdefault(alias, value)
    if atr14 is not None:
        ctx.setdefault("atr_14", atr14)
        ctx.setdefault("atr14", atr14)
        if current_price not in (None, 0):
            ctx.setdefault("atr_pct", atr14 / current_price * 100.0)
    if rsi14 is not None:
        ctx.setdefault("rsi_14", rsi14)
        ctx.setdefault("rsi14", rsi14)
    if swing_low is not None:
        ctx.setdefault("swing_low", swing_low)
        ctx.setdefault("recent_swing_low", swing_low)
    if swing_high is not None:
        ctx.setdefault("swing_high", swing_high)
        ctx.setdefault("recent_swing_high", swing_high)
    support_candidates = [value for value in (support_low, swing_low, ma50, ma200) if value is not None]
    if support_candidates:
        resolved_support_low = support_low if support_low is not None else min(support_candidates)
        resolved_support_high = support_high if support_high is not None else max(support_candidates)
        if atr14 is not None and support_low is None:
            resolved_support_low = min(resolved_support_low, max(0.0, resolved_support_high - atr14))
        ctx.setdefault("support_zone_low", resolved_support_low)
        ctx.setdefault("support_zone_high", resolved_support_high)
        ctx.setdefault("support_zone", {"low": resolved_support_low, "high": resolved_support_high})
    resistance_candidates = [value for value in (resistance_low, resistance_high, swing_high) if value is not None]
    if resistance_candidates:
        resolved_resistance_low = resistance_low if resistance_low is not None else min(resistance_candidates)
        resolved_resistance_high = resistance_high if resistance_high is not None else max(resistance_candidates)
        ctx.setdefault("resistance_zone_low", resolved_resistance_low)
        ctx.setdefault("resistance_zone_high", resolved_resistance_high)
        ctx.setdefault("resistance_zone", {"low": resolved_resistance_low, "high": resolved_resistance_high})
    if current_price is not None and invalidation is not None:
        ctx.setdefault("distance_to_invalidation", (current_price / invalidation - 1.0) * 100.0 if invalidation else None)
    resistance_ref = resistance_low or resistance_high or _first_number(ctx, row, snapshot, "confirmation_price", "chase_above_price")
    if current_price is not None and resistance_ref is not None:
        ctx.setdefault("distance_to_resistance", (resistance_ref / current_price - 1.0) * 100.0 if current_price else None)
    if current_price is not None and invalidation is not None and resistance_ref is not None:
        downside = max(current_price - invalidation, 0.01)
        upside = max(resistance_ref - current_price, 0.0)
        ctx.setdefault("reward_risk_ratio", upside / downside)
    rs20 = _first_number(ctx, row, snapshot, "relative_strength_vs_qqq_20d", "relativeStrengthVsQqq20d", "relative_strength_vs_QQQ", "relative_strength_20d")
    rs60 = _first_number(ctx, row, snapshot, "relative_strength_vs_qqq_60d", "relativeStrengthVsQqq60d", "relative_strength_60d")
    if rs20 is not None:
        ctx.setdefault("relative_strength_vs_qqq_20d", rs20)
    if rs60 is not None:
        ctx.setdefault("relative_strength_vs_qqq_60d", rs60)
    ctx.setdefault("daily_ohlcv", _daily_ohlcv_snapshot(ctx, row, snapshot, market))
    if any(
        ctx.get(key) not in (None, "")
        for key in (
            "ma20",
            "ma50",
            "ma200",
            "atr_14",
            "rsi_14",
            "support_zone_low",
            "resistance_zone_high",
        )
    ):
        ctx.setdefault("technical_data_source", "radar_cache")
    return ctx


def _apply_volume_snapshot_to_technicals(technicals: dict[str, Any], volume_snapshot: dict[str, Any]) -> dict[str, Any]:
    ctx = dict(technicals or {})
    volume = dict(volume_snapshot or {})
    latest_volume = _first_number(volume, "latest_volume", "latestVolume")
    volume_ma20 = _first_number(volume, "volume_ma20", "volumeMa20", "avg_volume_20d", "avgVolume20d")
    volume_ratio = _first_number(volume, "volume_ratio", "volumeRatio")
    if latest_volume is not None:
        ctx["latest_volume"] = latest_volume
        ctx.setdefault("volume", latest_volume)
        daily = dict(ctx.get("daily_ohlcv") or {})
        if _first_number(daily, "volume") is None:
            daily["volume"] = latest_volume
            ctx["daily_ohlcv"] = daily
    if volume_ma20 is not None:
        ctx["volume_ma20"] = volume_ma20
        ctx["avg_volume_20d"] = volume_ma20
    if volume_ratio is not None:
        ctx["volume_ratio"] = volume_ratio
    if volume.get("volume_source"):
        ctx["volume_source"] = volume.get("volume_source")
        ctx["volume_data_source"] = volume.get("volume_source")
    return ctx


def _apply_volume_snapshot_to_list_row(row: dict[str, Any], volume_snapshot: dict[str, Any]) -> None:
    volume = dict(volume_snapshot or {})
    latest_volume = _first_number(volume, "latest_volume", "latestVolume")
    volume_ma20 = _first_number(volume, "volume_ma20", "volumeMa20", "avg_volume_20d", "avgVolume20d")
    volume_ratio = _first_number(volume, "volume_ratio", "volumeRatio")
    volume_status = str(volume.get("volume_price_status") or volume.get("volumePriceStatus") or "").strip()
    volume_score = _first_number(volume, "volume_price_score", "volumePriceScore")
    if latest_volume is not None:
        row["latest_volume"] = latest_volume
        row.setdefault("volume", latest_volume)
    if volume_ma20 is not None:
        row["volume_ma20"] = volume_ma20
        row["avg_volume_20d"] = volume_ma20
    if volume_ratio is not None:
        row["volume_ratio"] = volume_ratio
    if volume_status:
        row["volume_price_status"] = volume_status
    if volume_score is not None:
        row["volume_price_score"] = volume_score
    if volume.get("volume_source"):
        row["volume_source"] = volume.get("volume_source")
        row["volume_data_source"] = volume.get("volume_source")
    if volume_ratio is not None and volume_status.upper() != "DATA_MISSING":
        _remove_resolved_volume_missing_fields(row)


def _remove_resolved_volume_missing_fields(row: dict[str, Any]) -> None:
    for key in ("missing_entry_fields", "technical_entry_missing_fields", "technical_missing_fields", "data_missing_fields"):
        value = row.get(key)
        if isinstance(value, list):
            row[key] = [field for field in value if not _is_volume_acceptance_gap_field(str(field))]
    debug = row.get("debug") if isinstance(row.get("debug"), dict) else None
    if debug and isinstance(debug.get("data_missing_fields"), list):
        debug["data_missing_fields"] = [
            field for field in debug.get("data_missing_fields") or [] if not _is_volume_acceptance_gap_field(str(field))
        ]


def _daily_ohlcv_snapshot(*sources: dict[str, Any]) -> dict[str, Any]:
    return {
        "open": _first_number(*sources, "open"),
        "high": _first_number(*sources, "high"),
        "low": _first_number(*sources, "low"),
        "close": _first_number(*sources, "close", "current_price", "currentPrice", "price"),
        "volume": _first_number(*sources, "volume", "latest_volume", "quoteVolume"),
    }


def _report_technical_overlay(technicals: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "daily_ohlcv",
        "ma20",
        "ma50",
        "ma200",
        "ema20",
        "ema50",
        "ema200",
        "avg_volume_20d",
        "volume_ma20",
        "volume_ratio",
        "atr_14",
        "atr14",
        "atr_pct",
        "rsi_14",
        "rsi14",
        "swing_high",
        "swing_low",
        "recent_swing_high",
        "recent_swing_low",
        "support_zone",
        "support_zone_low",
        "support_zone_high",
        "resistance_zone",
        "resistance_zone_low",
        "resistance_zone_high",
        "resistanceLevels",
        "resistance_levels",
        "technical_entry_model",
        "distance_to_invalidation",
        "distance_to_resistance",
        "reward_risk_ratio",
        "relative_strength_vs_qqq_20d",
        "relative_strength_vs_qqq_60d",
        "day_change_pct",
        "daily_return_pct",
        "previous_close",
        "latest_volume",
        "volume_source",
        "volume_data_source",
    )
    return {key: technicals.get(key) for key in keys if key in technicals and technicals.get(key) not in (None, "")}


def _runtime_cached(
    key: tuple[Any, ...],
    ttl_seconds: int,
    loader: Any,
    perf: PerfProbe,
    stage_name: str,
) -> Any:
    cache = _report_runtime_cache()
    now = time.time()
    item = cache.get(key)
    if item and now - float(item.get("stored_at", 0.0)) <= ttl_seconds:
        perf.add(stage_name, 0.0, cache_hit=True, external_api=False)
        return item.get("value")
    stage_start = time.perf_counter()
    value = loader()
    perf.add(stage_name, (time.perf_counter() - stage_start) * 1000, cache_hit=False, external_api=False, note="本地缓存读取")
    cache[key] = {"stored_at": now, "value": value}
    return value


def _report_runtime_cache() -> dict[tuple[Any, ...], dict[str, Any]]:
    try:
        cache = st.session_state.setdefault("ai_radar_report_runtime_cache", {})
        if isinstance(cache, dict):
            return cache
    except Exception:
        pass
    return _FALLBACK_REPORT_CACHE


def _clear_report_runtime_cache(symbol: str | None = None) -> None:
    cache = _report_runtime_cache()
    if not symbol:
        cache.clear()
        return
    normalized = str(symbol or "").strip().upper()
    for key in list(cache.keys()):
        if len(key) > 1 and str(key[1]).upper() == normalized:
            cache.pop(key, None)


def _empty_history_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def _report_view_toolbar_html(symbol: str, company: str, updated: str) -> str:
    return (
        '<section class="ai-radar-report-toolbar">'
        f'<a href="{escape(_list_view_href(), quote=True)}" target="_self">返回 Radar 列表</a>'
        '<div>'
        f'<strong>{escape(symbol)}</strong>'
        f'<span>{escape(company)}｜更新时间 {escape(updated)}</span>'
        "</div>"
        "</section>"
    )


def _report_not_found_html(symbol: str) -> str:
    text = symbol or "UNKNOWN"
    return (
        '<section class="ai-radar-report-missing">'
        f'<a href="{escape(_list_view_href(), quote=True)}" target="_self">返回 Radar 列表</a>'
        f"<strong>未找到 {escape(text)} 的股票研报</strong>"
        "<span>请返回列表选择观察池中的股票。</span>"
        "</section>"
    )


def _report_loading_shell_html(symbol: str) -> str:
    ticker = escape(str(symbol or ""))
    return (
        '<article class="ai-radar-research-report loading">'
        '<header class="ai-radar-research-header skeleton">'
        '<div class="ai-radar-title-block">'
        '<span>AI 股票雷达研究</span>'
        f"<h1>{ticker}</h1>"
        "<p>正在读取本地缓存</p>"
        "<em>先展示研报框架，重数据稍后加载。</em>"
        "</div>"
        '<div class="ai-radar-header-stats">'
        "<div><span>最新价</span><strong>加载中</strong></div>"
        "<div><span>当前区间</span><strong>加载中</strong></div>"
        "<div><span>总分</span><strong>加载中</strong></div>"
        "</div>"
        "</header>"
        '<section class="ai-radar-executive-card">'
        '<div class="ai-radar-section-title"><span>决策摘要</span><b>当前建议 / 关键价格 / 下一步</b></div>'
        '<p class="ai-radar-thesis">本页结论：正在读取本地缓存，先展示研报框架。</p>'
        '<aside class="ai-radar-position-context">'
        "<strong>我的持仓：加载中</strong>"
        "<span>动作建议：等待持仓缓存读取</span>"
        "</aside>"
        "</section>"
        "</article>"
    )


def _performance_diagnostics_html(perf: PerfProbe) -> str:
    rows = []
    for stage in perf.stages:
        cache = "命中" if stage.cache_hit is True else "未命中" if stage.cache_hit is False else "不适用"
        external = "是" if stage.external_api else "否"
        note = stage.note or "无"
        rows.append(
            "<tr>"
            f"<td>{escape(stage.name)}</td>"
            f"<td>{stage.elapsed_ms:.2f} ms</td>"
            f"<td>{escape(cache)}</td>"
            f"<td>{escape(external)}</td>"
            f"<td>{escape(note)}</td>"
            "</tr>"
        )
    return (
        '<section class="ai-radar-debug ai-radar-perf-debug">'
        f'<div class="ai-radar-debug-note">总耗时：{perf.total_ms:.2f} ms｜外部 API 请求：否（本页优先读取本地 cache.sqlite）</div>'
        '<table class="ai-radar-debug-table">'
        "<thead><tr><th>阶段</th><th>耗时</th><th>缓存</th><th>外部 API</th><th>说明</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )


def _list_row(ticker: str) -> dict[str, Any]:
    row = _dashboard_row(ticker) or _single_report_row(ticker)
    snapshot = _dict_value(row, "rawSnapshot")
    technicals = _dict_value(row, "rawTechnicals")
    company_name = _company_name_from_sources(ticker, row, snapshot or {})
    list_row = build_ai_stock_radar_list_row(
        ticker,
        company_name=company_name,
        scores=None if snapshot and technicals else _scores_from_row(row),
        snapshot=snapshot,
        technicals=technicals,
    )
    history = _list_market_history(ticker)
    volume_snapshot = _volume_price_acceptance_snapshot(list_row, technicals or {}, row or {}, history)
    technicals = _apply_volume_snapshot_to_technicals(technicals or {}, volume_snapshot)
    _apply_volume_snapshot_to_list_row(list_row, volume_snapshot)
    list_row["sector"] = _sector_track_from_sources(row, snapshot or {}, ticker)
    buy_zone_context = _list_buy_zone_context(
        list_row,
        row or {},
        snapshot or {},
        technicals or {},
        history=history,
        volume_snapshot=volume_snapshot,
    )
    if buy_zone_context:
        list_row["buy_zone_context"] = buy_zone_context
        buy_zone_display = build_buy_zone_display(buy_zone_context, {**(row or {}), **list_row}, mode="radar_list")
        list_row["buy_zone_display"] = buy_zone_display
        list_row["entry_display_label"] = buy_zone_display.get("entry_display_label") or list_row.get("entry_display_label")
        list_row["entry_action_hint"] = buy_zone_display.get("entry_action_hint") or list_row.get("entry_action_hint")
        list_row["entry_display_reason"] = buy_zone_display.get("entry_display_reason") or list_row.get("entry_display_reason")
        list_row["entry_context_status"] = buy_zone_display.get("entry_context_status") or list_row.get("entry_context_status")
    research = _research_queue_view(list_row)
    list_row["research_queue"] = research
    list_row["research_status"] = research["status_text"]
    list_row["research_priority_score"] = research["priority_score"]
    list_row["research_priority_text"] = research["priority_text"]
    list_row["research_buy_point_summary"] = research["summary_text"]
    list_row["research_distance_text"] = research["distance_text"]
    list_row["research_next_trigger"] = research["next_trigger_text"]
    list_row["research_confidence_text"] = research["confidence_text"]
    list_row["research_data_quality"] = research["data_quality_text"]
    return list_row


def _list_market_history(symbol: str) -> pd.DataFrame:
    return _runtime_cached(
        ("market_history", str(symbol or "").strip().upper()),
        HISTORY_TTL_SECONDS,
        lambda: build_market_history(symbol),
        PerfProbe(),
        "历史 K 线读取",
    )


def _list_buy_zone_context(
    list_row: dict[str, Any],
    row: dict[str, Any],
    snapshot: dict[str, Any],
    technicals: dict[str, Any],
    *,
    history: pd.DataFrame | None = None,
    volume_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    volume_snapshot = dict(volume_snapshot or {})
    has_volume_snapshot = _first_number(volume_snapshot, "volume_ratio", "volumeRatio") is not None or bool(
        volume_snapshot.get("volume_price_status") or volume_snapshot.get("volumePriceStatus")
    )
    for source in (row, snapshot, technicals, list_row):
        existing = _dict_value(source, "buy_zone_context") or _dict_value(source, "buyZoneContext")
        if existing:
            if has_volume_snapshot and _first_number(existing, "volume_ratio", "volumeRatio") is None:
                continue
            return dict(existing)
    try:
        if not volume_snapshot:
            volume_snapshot = _volume_price_acceptance_snapshot(
                list_row,
                technicals,
                row,
                history if history is not None else _empty_history_frame(),
            )
        return build_buy_zone_context(list_row, technicals=technicals, volume_snapshot=volume_snapshot).to_dict()
    except Exception:
        return {}


def _research_queue_view(row: dict[str, Any]) -> dict[str, Any]:
    display = _dict_value(row, "buy_zone_display") or _dict_value(row, "buyZoneDisplay") or {}
    context = _dict_value(row, "buy_zone_context") or _dict_value(row, "buyZoneContext") or {}
    action = _research_action_code(display, context, row)
    price = _first_number(row, context, "current_price", "currentPrice", "price", "close")
    zone_low, zone_high = _research_entry_zone_bounds(context)
    distance_pct = _research_distance_pct(price, zone_low, zone_high)
    zone_position = _first_number(context, display, "zone_position", "zonePosition")
    has_position = _row_has_position(row)
    data_quality = _research_data_quality_text(row, context, action)
    status_key, status_text = _research_status(action, price, zone_low, zone_high, distance_pct, zone_position, data_quality)
    confidence_text = _research_confidence_text(row, context, action)
    next_trigger = _research_next_trigger(status_key, context, price, zone_low, zone_high, action)
    summary = _research_buy_point_summary(status_key, action, distance_pct, zone_low, zone_high, next_trigger, context, data_quality, row)
    score = _research_priority_score(
        status_key=status_key,
        distance_pct=distance_pct,
        confidence_text=confidence_text,
        has_position=has_position,
        row=row,
        action=action,
    )
    return {
        "status_key": status_key,
        "status_text": status_text,
        "priority_score": score,
        "priority_text": _research_priority_text(score),
        "distance_text": _research_distance_text(price, zone_low, zone_high, distance_pct),
        "next_trigger_text": next_trigger,
        "confidence_text": confidence_text,
        "data_quality_text": data_quality,
        "summary_text": summary,
    }


def _research_action_code(display: dict[str, Any], context: dict[str, Any], row: dict[str, Any]) -> str:
    display_action = str(
        display.get("buy_zone_action")
        or display.get("action_code")
        or display.get("current_action")
        or display.get("entry_context_status")
        or ""
    ).strip().upper()
    if display_action:
        return display_action
    context_action = str(context.get("current_action") or context.get("currentAction") or "").strip().upper()
    if context_action:
        return context_action
    decision = str(row.get("decision") or "").strip().upper()
    if decision in {"DATA_INSUFFICIENT", "DATA_MISSING", "NO_BUY_ZONE", "ZONE_MISSING", "BLOCK_CHASE", "AVOID"}:
        return decision
    if _legacy_buy_zone_context_missing(row):
        return "ZONE_MISSING"
    return decision or "WAIT"


def _research_entry_zone_bounds(context: dict[str, Any]) -> tuple[float | None, float | None]:
    candidates = (
        ("left_probe_zone_low", "left_probe_zone_high"),
        ("core_left_entry_zone_low", "core_left_entry_zone_high"),
        ("primary_buy_zone_low", "primary_buy_zone_high"),
        ("pullback_zone_low", "pullback_zone_high"),
        ("support_zone_low", "support_zone_high"),
        ("primary_zone_low", "primary_zone_high"),
        ("technical_entry_zone_low", "technical_entry_zone_high"),
    )
    for low_key, high_key in candidates:
        low = _first_number(context, low_key)
        high = _first_number(context, high_key)
        if low is not None and high is not None and high >= low:
            return low, high
    return None, None


def _research_distance_pct(price: float | None, low: float | None, high: float | None) -> float | None:
    if price is None or low is None or high is None or low <= 0 or high <= 0:
        return None
    if low <= price <= high:
        return 0.0
    if price > high:
        return (price / high - 1.0) * 100.0
    return (price / low - 1.0) * 100.0


def _research_status(
    action: str,
    price: float | None,
    low: float | None,
    high: float | None,
    distance_pct: float | None,
    zone_position: float | None,
    data_quality: str,
) -> tuple[str, str]:
    if action in {"DATA_INSUFFICIENT", "DATA_MISSING"}:
        return "data", "数据不足"
    if action in {"NO_BUY_ZONE", "ZONE_MISSING"} or data_quality == "买区未生成":
        return "data", "数据不足"
    if action == "AVOID":
        return "avoid", "暂不研究"
    if action == "BLOCK_CHASE":
        return "low", "低优先级"
    if action in {"ALLOW_SMALL_BUY", "ALLOW_ADD_ON_PULLBACK"}:
        return "near", "接近买区"
    if action == "WAIT_CONFIRMATION":
        return "confirm", "等待确认"
    if action == "WAIT_PULLBACK":
        if low is not None and high is not None and price is not None and price > high:
            if distance_pct is not None and distance_pct > 22:
                return "low", "低优先级"
            return "pullback", "等待回落"
        if zone_position is not None and zone_position <= 0.35:
            return "near", "接近买区"
        return "confirm", "等待确认"
    if low is not None and high is not None and price is not None:
        if low <= price <= high:
            return ("near", "接近买区") if (zone_position is None or zone_position <= 0.35) else ("confirm", "等待确认")
        if price > high:
            return ("pullback", "等待回落") if (distance_pct is not None and distance_pct <= 22) else ("low", "低优先级")
    if data_quality in {"旧格式待刷新", "行情正常 / 买区缺失"}:
        return "data", "数据不足"
    return "low", "低优先级"


def _research_confidence_text(row: dict[str, Any], context: dict[str, Any], action: str) -> str:
    if action in {"DATA_INSUFFICIENT", "DATA_MISSING", "NO_BUY_ZONE", "ZONE_MISSING"}:
        return "不足"
    setup = _first_number(context, "setup_score", "setupScore")
    if setup is None:
        breakdown = context.get("confidence_breakdown") if isinstance(context.get("confidence_breakdown"), dict) else {}
        values = [_number(value) for value in breakdown.values()]
        values = [value for value in values if value is not None]
        setup = sum(values) / len(values) if values else None
    if setup is not None:
        if setup >= 72:
            return "高"
        if setup >= 55:
            return "中"
        return "低"
    return _data_confidence(row)


def _research_data_quality_text(row: dict[str, Any], context: dict[str, Any], action: str) -> str:
    price_state = _price_data_state(row)
    if price_state == "missing":
        return "价格缺失"
    if price_state == "stale":
        return "价格过期"
    if _legacy_buy_zone_context_missing(row):
        return "旧格式待刷新"
    if action in {"NO_BUY_ZONE", "ZONE_MISSING"}:
        return "买区未生成"
    if action in {"DATA_INSUFFICIENT", "DATA_MISSING"}:
        missing = _buy_zone_context_missing_fields(context) or _actionable_missing_fields(row)
        if missing:
            return _missing_fields_data_quality_text(missing)
        return "买区未生成"
    if not context:
        groups = _missing_groups(row)
        if any("技术" in group for group in groups):
            return "技术数据缺口"
        if any("量价" in group for group in groups):
            return "量价数据缺口"
        if groups:
            return groups[0]
        return "行情正常 / 买区缺失"
    if _data_confidence(row) == "高":
        return "数据完整"
    groups = _missing_groups(row)
    if any("技术" in group for group in groups):
        return "技术数据缺口"
    if any("量价" in group for group in groups):
        return "量价数据缺口"
    return "行情正常 / 买区缺失" if groups else "数据完整"


def _missing_fields_data_quality_text(fields: list[str]) -> str:
    if any(_is_technical_gap_field(field) for field in fields):
        return "技术数据缺口"
    if any(_is_volume_acceptance_gap_field(field) for field in fields):
        return "量价数据缺口"
    return "数据缺口"


def _research_next_trigger(
    status_key: str,
    context: dict[str, Any],
    price: float | None,
    low: float | None,
    high: float | None,
    action: str,
) -> str:
    if status_key == "data":
        return "刷新买区上下文"
    if status_key == "avoid":
        return "排除规则复核"
    if status_key == "pullback":
        return f"等回落至 {_money(high)}" if high is not None else "等回落到技术回踩带"
    confirmation = _first_number(context, "near_confirm_line", "confirmation_price", "confirm_price", "confirmation_line", "confirm_line")
    if status_key == "confirm":
        if confirmation is not None:
            return f"站上 {_money(confirmation)} 后重新评估"
        return "等量价承接确认"
    if status_key == "near":
        invalidation = _first_number(context, "invalidation_price", "invalidation_line", "buy_zone_failure_line", "suspend_new_line")
        if invalidation is not None:
            return f"守住 {_money(invalidation)}"
        return "观察承接K线"
    if action == "BLOCK_CHASE":
        return "等回落，不追高"
    return "暂不新增"


def _research_buy_point_summary(
    status_key: str,
    action: str,
    distance_pct: float | None,
    low: float | None,
    high: float | None,
    next_trigger: str,
    context: dict[str, Any],
    data_quality: str,
    row: dict[str, Any],
) -> str:
    if status_key == "data":
        if data_quality == "旧格式待刷新":
            return "旧格式待刷新，需重建买区上下文"
        if action in {"NO_BUY_ZONE", "ZONE_MISSING"}:
            missing = _buy_zone_context_missing_fields(context) or ["support_zone", "resistance_zone"]
            return f"买区未生成，缺少{_field_list_display(missing)}"
        missing = _buy_zone_context_missing_fields(context) or _actionable_missing_fields(row)
        missing_text = _field_list_display(missing) if missing else "技术承接数据"
        return f"数据不足，缺少{missing_text}"
    if status_key == "near":
        if distance_pct is None or distance_pct == 0:
            return "左侧买区附近，观察承接与量价"
        return f"距左侧买区 {_signed_pct(distance_pct)}，接近观察"
    if status_key == "confirm":
        if next_trigger.startswith("站上"):
            return f"{next_trigger}"
        return "观察区内，但缺量价确认"
    if status_key == "pullback":
        distance = _signed_pct(distance_pct) if distance_pct is not None else "偏高"
        return f"距左侧买区 {distance}，{next_trigger}"
    if status_key == "avoid":
        return "暂不研究，风险或排除规则优先"
    if action == "BLOCK_CHASE":
        return "当前追高语境，低优先级观察"
    return "当前远离买区，低优先级观察"


def _research_distance_text(price: float | None, low: float | None, high: float | None, distance_pct: float | None) -> str:
    if price is None or low is None or high is None or distance_pct is None:
        return "—"
    if low <= price <= high:
        return "区内"
    return _signed_pct(distance_pct)


def _research_priority_score(
    *,
    status_key: str,
    distance_pct: float | None,
    confidence_text: str,
    has_position: bool,
    row: dict[str, Any],
    action: str,
) -> float:
    base = {
        "near": 92.0,
        "confirm": 82.0,
        "pullback": 66.0,
        "data": 34.0,
        "low": 22.0,
        "avoid": 0.0,
    }.get(status_key, 20.0)
    if status_key == "pullback" and distance_pct is not None:
        base += max(-18.0, min(12.0, 12.0 - abs(distance_pct)))
    if status_key == "near" and distance_pct is not None:
        base += max(0.0, 6.0 - abs(distance_pct))
    if confidence_text == "高":
        base += 6.0
    elif confidence_text == "中":
        base += 3.0
    elif confidence_text == "不足":
        base -= 6.0
    if has_position:
        base += 7.0 if status_key in {"near", "confirm", "pullback", "data"} else 0.0
    final_score = _first_number(row, "final_score", "finalScore", "quality_score", "qualityScore")
    if status_key == "data" and final_score is not None and final_score >= 75:
        base += 8.0
    if action == "BLOCK_CHASE":
        base = min(base, 28.0)
    if status_key == "avoid":
        base = min(base, 5.0)
    return round(max(0.0, min(100.0, base)), 1)


def _research_priority_text(score: float) -> str:
    if score >= 82:
        return f"高 {score:.0f}"
    if score >= 55:
        return f"中 {score:.0f}"
    if score >= 30:
        return f"低 {score:.0f}"
    return f"很低 {score:.0f}"


def _research_view(row: dict[str, Any]) -> dict[str, Any]:
    existing = row.get("research_queue")
    return existing if isinstance(existing, dict) else _research_queue_view(row)


def _row_has_position(row: dict[str, Any]) -> bool:
    shares = _first_number(row, "current_shares", "currentShares", "quantity", "shares", "position_shares", "positionShares")
    weight = _first_number(row, "portfolio_weight", "portfolioWeight", "positionPct", "current_weight", "currentWeight")
    return bool((shares is not None and shares > 0) or (shares is None and weight is not None and weight > 0))


def _research_summary_cards_html(rows: list[dict[str, Any]]) -> str:
    counts = _filter_counts(rows)
    cards = [
        ("接近买区", counts.get("near", 0)),
        ("等待确认", counts.get("confirm", 0)),
        ("数据不足", counts.get("data", 0)),
        ("低优先级", sum(1 for row in rows if _research_view(row).get("status_key") == "low")),
    ]
    body = "".join(
        '<div class="ai-radar-research-card">'
        f"<span>{escape(label)}</span><strong>{escape(str(count))}</strong>"
        "</div>"
        for label, count in cards
    )
    return f'<section class="ai-radar-research-summary">{body}</section>'


def _company_name_from_sources(ticker: str, row: dict[str, Any] | None, snapshot: dict[str, Any]) -> str:
    value = (
        _first_present(snapshot, "companyName", "company_name", "name", "company")
        or _row_value(row, "companyName", "company_name", "name", "company")
        or ticker
    )
    return str(value or ticker).strip() or ticker


def _sector_track_from_sources(row: dict[str, Any] | None, snapshot: dict[str, Any], ticker: str = "") -> str:
    sector = _clean_text(
        _first_present(snapshot, "sector", "sectorName")
        or _row_value(row, "sector", "sectorName")
    )
    industry = _clean_text(
        _first_present(snapshot, "industry", "industry_group", "industryGroup", "business_model", "businessModel", "model")
        or _row_value(row, "industry", "industry_group", "industryGroup", "business_model", "businessModel", "model")
    )
    return get_ticker_research_track(ticker, sector, industry)


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -float(_research_view(row).get("priority_score") or 0),
            _updated_sort_key(row),
            str(row.get("ticker") or ""),
        ),
    )


def _updated_sort_key(row: dict[str, Any]) -> float:
    updated = pd.to_datetime(row.get("data_updated_at"), errors="coerce")
    if pd.isna(updated):
        return float("inf")
    try:
        return -float(updated.timestamp())
    except (AttributeError, OSError, OverflowError, ValueError):
        return float("inf")


def _selected_radar_filter_key() -> str:
    key = str(st.query_params.get("radarFilter", "all") or "all").strip()
    legacy = {"value": "near", "pullback": "near", "watch": "confirm", "chase": "high"}
    key = legacy.get(key, key)
    valid = {"all", "near", "confirm", "data", "held", "high"}
    return key if key in valid else "all"


def _filter_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        key: sum(1 for row in rows if _row_matches_filter(row, key))
        for key in ("all", "near", "confirm", "data", "held", "high")
    }


def _filter_rows(rows: list[dict[str, Any]], filter_key: str) -> list[dict[str, Any]]:
    if filter_key == "all":
        return rows
    return [row for row in rows if _row_matches_filter(row, filter_key)]


def _row_matches_filter(row: dict[str, Any], filter_key: str) -> bool:
    view = _research_view(row)
    status_key = str(view.get("status_key") or "")
    if filter_key == "all":
        return True
    if filter_key == "near":
        return status_key == "near"
    if filter_key == "confirm":
        return status_key == "confirm"
    if filter_key == "data":
        return status_key == "data"
    if filter_key == "held":
        return _row_has_position(row)
    if filter_key == "high":
        return float(view.get("priority_score") or 0) >= 75
    return False


def _filter_chips_html(active_key: str, counts: dict[str, int]) -> str:
    labels = [
        ("all", "全部"),
        ("near", "接近买区"),
        ("confirm", "等待确认"),
        ("data", "数据不足"),
        ("held", "已持仓"),
        ("high", "高优先级"),
    ]
    chips = "".join(
        '<a class="ai-radar-filter-chip {active}" href="?page=ai-radar&amp;view=list&amp;radarFilter={key}">'
        "<span>{label}</span><b>{count}</b></a>".format(
            active="active" if key == active_key else "",
            key=escape(key, quote=True),
            label=escape(label),
            count=escape(str(counts.get(key, 0))),
        )
        for key, label in labels
    )
    return f'<nav class="ai-radar-filter-chips">{chips}</nav>'


def _list_row_html(row: dict[str, Any], selected: str) -> str:
    ticker = str(row.get("ticker") or "")
    research = _research_view(row)
    radar_status = str(research.get("status_text") or _core_status(row))
    active = " active" if ticker == selected else ""
    report_href = _report_view_href(ticker)
    trigger_text = str(research.get("next_trigger_text") or research.get("summary_text") or "查看研报")
    summary_text = str(research.get("summary_text") or trigger_text)
    return (
        f'<tr class="{escape(_radar_status_tone(radar_status))}{active}">'
        f'<td><a class="ai-radar-ticker" href="{escape(report_href, quote=True)}" target="_self">{escape(ticker)}</a>{_company_track_html(row)}</td>'
        f'<td>{escape(_money(row.get("current_price")))}</td>'
        f'<td><span class="ai-radar-status-pill">{escape(radar_status)}</span></td>'
        f'<td><span class="ai-radar-priority-score">{escape(str(research.get("priority_text") or ""))}</span></td>'
        f'<td>{escape(str(research.get("distance_text") or "—"))}</td>'
        f'<td><span class="ai-radar-buy-point-reason" title="{escape(summary_text, quote=True)}">{escape(trigger_text)}</span></td>'
        f'<td>{escape(str(research.get("confidence_text") or "不足"))}</td>'
        f'<td><span class="ai-radar-data-quality">{escape(str(research.get("data_quality_text") or "数据待复核"))}</span></td>'
        f'<td>{escape(_short_time(row.get("data_updated_at")))}</td>'
        f'<td><a class="ai-radar-report-link" href="{escape(report_href, quote=True)}" target="_self">查看</a></td>'
        "</tr>"
    )


def _report_html(
    report: dict[str, Any],
    market: dict[str, Any],
    snapshot: dict[str, Any],
    technicals: dict[str, Any],
    row: dict[str, Any],
    history: pd.DataFrame | None,
    *,
    action_result: Any | None = None,
    conclusion: dict[str, Any] | None = None,
    portfolio_context: dict[str, Any] | None = None,
    buy_zone_context: dict[str, Any] | None = None,
    buy_zone_display: dict[str, Any] | None = None,
    data_health: dict[str, Any] | None = None,
    include_appendix: bool = True,
    perf: PerfProbe | None = None,
) -> str:
    decision = str(report.get("decision") or "")
    core_status = _core_status(report)
    confidence = _data_confidence(report)
    action_result = action_result or _action_fusion_result(report, technicals, row, history)
    buy_zone_context = buy_zone_context or build_buy_zone_context(report, technicals=technicals, volume_snapshot=_volume_price_acceptance_snapshot(report, technicals, row, history)).to_dict()
    portfolio_context = portfolio_context or _portfolio_context(report, row, action_result, buy_zone_context)
    buy_zone_display = buy_zone_display or build_buy_zone_display(buy_zone_context, {**(row or {}), **(portfolio_context or {})}, mode="report")
    portfolio_context = _portfolio_context_with_buy_zone_display(portfolio_context, buy_zone_display)
    conclusion = conclusion or _trade_conclusion(report, action_result, buy_zone_context, buy_zone_display)
    data_health = data_health or _data_health_context(report, market, snapshot, row, portfolio_context, buy_zone_context)
    stage_start = time.perf_counter()
    range_html = _range_chart_html(report, conclusion, buy_zone_context)
    if perf is not None:
        perf.add("图表渲染", (time.perf_counter() - stage_start) * 1000, cache_hit=False, external_api=False)
    appendix_html = (
        _report_appendix_html(report, market, snapshot, technicals, row, history, data_health)
        if include_appendix
        else ""
    )
    return (
        f'<article class="ai-radar-research-report {_decision_tone(decision)}">'
        f"{_research_header_html(report, market, snapshot, technicals, core_status, history, action_result, conclusion)}"
        f"{_executive_summary_card_html(report, snapshot, market, row, action_result, portfolio_context, conclusion, buy_zone_context)}"
        '<section class="ai-radar-visual-grid">'
        f"{range_html}"
        f"{_score_card_html(report, buy_zone_context)}"
        "</section>"
        f"{_ai_cloud_infra_card_html(row, snapshot, report)}"
        '<details class="ai-radar-folded-section">'
        '<summary>看多逻辑 / 核心风险</summary>'
        '<section class="ai-radar-opinion-grid two-col">'
        f'{_text_card_html("看多逻辑", report.get("bull_points") or [], subtitle="", limit=4)}'
        f'{_text_card_html("核心风险", report.get("risk_points") or [], subtitle="", limit=4)}'
        "</section>"
        "</details>"
        '<details class="ai-radar-folded-section">'
        '<summary>关键监控点 / 量价承接详情</summary>'
        '<section class="ai-radar-evidence-grid">'
        f"{_watch_points_table_html(report, row)}"
        f"{_volume_price_acceptance_card_html(report, technicals, row, history)}"
        "</section>"
        "</details>"
        f"{appendix_html}"
        '<footer class="ai-radar-report-foot">'
        f'<span>更新时间：{escape(_display_value(market.get("fetchedAt") or report.get("data_updated_at")))}</span>'
        f'<span>数据完整度：{escape(confidence)}</span>'
        f'<span>报告版本：{escape(RADAR_REPORT_VERSION)}</span>'
        "</footer>"
        "</article>"
    )


def _report_appendix_html(
    report: dict[str, Any],
    market: dict[str, Any],
    snapshot: dict[str, Any],
    technicals: dict[str, Any],
    row: dict[str, Any],
    history: pd.DataFrame | None,
    data_health: dict[str, Any],
) -> str:
    confidence = _data_confidence(report)
    return (
        '<section class="ai-radar-appendix">'
        '<details class="ai-radar-appendix-details">'
        '<summary>附录数据</summary>'
        '<section class="ai-radar-research-grid">'
        f'{_metric_table_card_html("关键指标（今日）", _key_metric_rows(report, market, snapshot, technicals, history))}'
        f'{_metric_table_card_html("核心财务摘要", _financial_metric_rows(snapshot))}'
        "</section>"
        '<section class="ai-radar-research-grid">'
        f'{_metric_table_card_html("市场表现", _performance_rows(history))}'
        f"{_catalyst_card_html(row, snapshot, report)}"
        "</section>"
        f"{_data_health_card_html(data_health)}"
        f"{_data_completeness_html(report, confidence, _volume_snapshot(market, snapshot, technicals, history))}"
        "</details>"
        "</section>"
    )


def _appendix_lazy_placeholder_html() -> str:
    return (
        '<section class="ai-radar-appendix lazy">'
        '<div class="ai-radar-appendix-placeholder">'
        '<strong>附录数据未加载</strong>'
        '<span>今日行情明细、财务摘要、市场表现和数据完整度详情已延后，点击上方“加载附录数据”后再读取。</span>'
        "</div>"
        "</section>"
    )


def _debug_html(debug: dict[str, Any], report: dict[str, Any] | None = None) -> str:
    report = report or {}
    if not debug:
        return '<section class="ai-radar-debug">暂无评分诊断。</section>'
    score_inputs = _dict_value(debug, "score_inputs") or {}
    score_labels = [
        ("quality_score", "质量"),
        ("growth_score", "成长"),
        ("valuation_score", "估值"),
        ("technical_score", "技术"),
        ("risk_score", "风险"),
        ("final_score", "总分"),
    ]
    rows = []
    for key, label in score_labels:
        item = _dict_value(score_inputs, key) or {}
        rows.append(
            "<tr>"
            f"<td>{escape(label)}</td>"
            f"<td>{escape(_field_list_display(item.get('used_fields'), report))}</td>"
            f"<td>{escape(_field_list_display(item.get('missing_fields'), report))}</td>"
            f"<td>{escape(_field_list_display(item.get('positive_fields'), report))}</td>"
            f"<td>{escape(_field_list_display(item.get('negative_fields'), report))}</td>"
            "</tr>"
        )
    zones = _dict_value(debug, "price_zones") or {}
    zone_sources = _dict_value(zones, "zone_sources") or {}
    below_reason = str(debug.get("below_buy_zone_reason") or "").strip()
    below_note = f'<div class="ai-radar-debug-note">{escape(below_reason)}</div>' if below_reason else ""
    return (
        '<section class="ai-radar-debug">'
        '<div class="ai-radar-debug-summary">'
        f'<div><span>数据状态</span><strong>{escape(_display_value(debug.get("data_status")))}</strong></div>'
        f'<div><span>区间状态</span><strong>{escape(_price_position_label(debug.get("price_position")))}</strong></div>'
        f'<div><span>距买区</span><strong>{escape(_signed_pct(debug.get("distance_to_buy_zone_pct")))}</strong></div>'
        f'<div><span>缺失字段</span><strong>{escape(_field_list_display(debug.get("data_missing_fields"), report))}</strong></div>'
        f'<div><span>区间来源</span><strong>{escape(str(zones.get("source") or "missing"))}</strong></div>'
        f'<div><span>字段别名风险</span><strong>{escape(_inline_list(debug.get("field_alias_notes")))}</strong></div>'
        '</div>'
        '<div class="ai-radar-debug-summary compact">'
        f'<div><span>技术回踩带</span><strong>{escape(str(zone_sources.get("buy_zone") or "missing"))}</strong></div>'
        f'<div><span>观察区</span><strong>{escape(str(zone_sources.get("watch_zone") or "missing"))}</strong></div>'
        f'<div><span>追高区</span><strong>{escape(str(zone_sources.get("chase_zone") or "missing"))}</strong></div>'
        f'<div><span>风险提示原因</span><strong>{escape(_inline_list(debug.get("block_reasons")))}</strong></div>'
        '</div>'
        f'{below_note}'
        '<table class="ai-radar-debug-table">'
        '<thead><tr><th>评分</th><th>使用字段</th><th>缺失字段</th><th>加分字段</th><th>扣分字段</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
        '</section>'
    )


def _research_header_html(
    report: dict[str, Any],
    market: dict[str, Any],
    snapshot: dict[str, Any],
    technicals: dict[str, Any],
    core_status: str,
    history: pd.DataFrame | None = None,
    action_result: Any | None = None,
    conclusion: dict[str, Any] | None = None,
) -> str:
    ticker = str(report.get("ticker") or "")
    company = str(report.get("company_name") or ticker)
    current_zone = _current_zone_label(report)
    track = get_ticker_research_track(
        ticker,
        _first_present(snapshot, "sector") or _first_present(report, "sector"),
        _first_present(snapshot, "industry") or _first_present(report, "industry"),
        {**snapshot, **report},
    )
    market_label = _clean_text(_first_present(snapshot, "country", "exchange")) or "本地缓存"
    meta = "｜".join(item for item in (track, market_label) if item) or "本地缓存研究视图"
    conclusion = conclusion or _trade_conclusion(report, action_result)
    volume = _volume_snapshot(market, snapshot, technicals, history)
    volume_text = _volume_display(volume)
    if volume.get("volume_ratio") is not None:
        volume_text = f"{volume_text}｜量比 {_volume_ratio_display(volume.get('volume_ratio'))}"
    stats = [
        ("最新价", _money(_report_current_price(report))),
        ("52周区间", _range_text(_first_number(snapshot, technicals, "fifty_two_week_low", "yearLow"), _first_number(snapshot, technicals, "fifty_two_week_high", "yearHigh"))),
        ("市值", _compact_money(_first_number(snapshot, "market_cap", "marketCap"))),
        ("成交量", volume_text),
        ("当前区间", str(conclusion.get("zone_text") or current_zone)),
        ("主建议", str(conclusion.get("action_text") or "等待复核")),
        ("总分", _number_text(report.get("final_score"))),
    ]
    stat_html = "".join(
        f"<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>" for label, value in stats
    )
    return (
        '<header class="ai-radar-research-header">'
        '<div class="ai-radar-title-block">'
        f"<span>AI 股票雷达研究</span>"
        f"<h1>{escape(ticker)}</h1>"
        f"<p>{escape(company)}</p>"
        f"<em>{escape(meta)}</em>"
        "</div>"
        f'<div class="ai-radar-header-stats">{stat_html}</div>'
        "</header>"
    )


def _summary_lines_html(lines: list[str]) -> str:
    return '<div class="ai-radar-summary-lines">' + "".join(f"<p>{escape(line)}</p>" for line in lines[:5]) + "</div>"


def _quality_grade(report: dict[str, Any]) -> str:
    explicit = _clean_text(_first_present(report, "quality_rating", "qualityRating"))
    if explicit:
        return explicit
    score = _number(_first_present(report, "quality_score", "qualityScore"))
    if score is None:
        return "暂无"
    if score >= 85:
        return "A+"
    if score >= 75:
        return "A"
    if score >= 65:
        return "B+"
    if score >= 55:
        return "B"
    return "C"


def _executive_summary_card_html(
    report: dict[str, Any],
    snapshot: dict[str, Any],
    market: dict[str, Any],
    row: dict[str, Any],
    action_result: Any,
    portfolio_context: dict[str, Any] | None = None,
    conclusion: dict[str, Any] | None = None,
    buy_zone_context: dict[str, Any] | None = None,
) -> str:
    conclusion = conclusion or _trade_conclusion(report, action_result)
    portfolio_context = portfolio_context or _portfolio_context(report, row, action_result)
    buy_zone_context = buy_zone_context or {}
    batting = _batting_zone_context(report, conclusion, buy_zone_context)
    buy_zone_display = conclusion.get("buy_zone_display") if isinstance(conclusion.get("buy_zone_display"), dict) else {}
    subzone_display = str(buy_zone_display.get("current_subzone_display_text") or _current_subzone_display_text(buy_zone_context) or batting.get("zone_label") or "观察区").strip()
    summary = _decision_summary_sentence(report, batting, portfolio_context, buy_zone_context, conclusion)
    key_prices = _decision_key_price_items(report, conclusion, buy_zone_context, buy_zone_display)
    acceptance_text = _buy_zone_acceptance_text(buy_zone_context, buy_zone_display)
    entry_quality_text = _entry_quality_text(buy_zone_context, buy_zone_display)
    main_headline = str(
        buy_zone_display.get("main_conclusion_text")
        or conclusion.get("main_conclusion_text")
        or "，".join(part for part in (acceptance_text, subzone_display, entry_quality_text, conclusion.get("action_text") or batting.get("operation")) if part)
        or subzone_display
    ).strip()
    key_price_html = "".join(
        "<div>"
        f"<span>{escape(label)}</span>"
        f"<strong>{escape(value)}</strong>"
        "</div>"
        for label, value in key_prices
        if value
    )
    next_steps = _decision_next_steps(batting, buy_zone_context)
    next_step_html = "".join(f"<li>{escape(item)}</li>" for item in next_steps if item)
    position_html = _position_context_panel_html(portfolio_context)
    position_action = (
        portfolio_context.get("action_for_existing_position")
        if portfolio_context.get("has_position")
        else portfolio_context.get("action_for_no_position")
    )
    return (
        '<section class="ai-radar-executive-card ai-radar-decision-summary-card">'
        '<div class="ai-radar-section-title"><span>决策摘要</span><b>当前建议 / 关键价格 / 下一步</b></div>'
        '<div class="ai-radar-decision-summary-head">'
        f'<div><span>{escape(str(report.get("ticker") or "UNKNOWN"))} / {_money(_report_current_price(report))}</span>'
        f'<strong>{escape(main_headline)}</strong></div>'
        f'<div><span>承接状态</span><strong>{escape(acceptance_text or "承接待确认")}</strong>'
        f'<em>{escape(entry_quality_text or "等确认")}</em></div>'
        f'<div><span>主建议</span><strong>{escape(str(conclusion.get("action_text") or batting.get("operation") or "等待复核"))}</strong></div>'
        f'<div><span>我的持仓动作</span><strong>{escape(str(position_action or "先观察"))}</strong></div>'
        "</div>"
        f'<p class="ai-radar-thesis">{escape(summary)}</p>'
        f"{position_html}"
        f'<div class="ai-radar-key-price-grid">{key_price_html}</div>'
        '<div class="ai-radar-next-step-card">'
        '<b>下一步</b>'
        f'<ol>{next_step_html}</ol>'
        "</div>"
        "</section>"
    )


def _decision_summary_sentence(
    report: dict[str, Any],
    batting: dict[str, str],
    portfolio_context: dict[str, Any],
    buy_zone_context: dict[str, Any],
    conclusion: dict[str, Any] | None = None,
) -> str:
    conclusion = conclusion or {}
    buy_zone_display = conclusion.get("buy_zone_display") if isinstance(conclusion.get("buy_zone_display"), dict) else {}
    ticker = str(report.get("ticker") or "该股票")
    current = batting.get("current_price") or _money(_report_current_price(report))
    zone = str(buy_zone_display.get("current_subzone_display_text") or _current_subzone_display_text(buy_zone_context) or batting.get("zone_label") or "观察区")
    action = (
        str(portfolio_context.get("action_for_existing_position") or "")
        if portfolio_context.get("has_position")
        else str(portfolio_context.get("action_for_no_position") or "")
    ).strip() or str(conclusion.get("action_text") or batting.get("operation") or "等待复核")
    reason = _decision_primary_reason(batting, buy_zone_context, buy_zone_display)
    acceptance_text = _buy_zone_acceptance_text(buy_zone_context, buy_zone_display)
    holding = ""
    shares = _number(portfolio_context.get("shares"))
    current_add = _first_number(buy_zone_context, "current_add_limit_percent", "currentAddLimitPercent")
    if shares is not None and shares > 0:
        holding = f"；已有 {_quantity_text(shares)} 股"
        if current_add is not None:
            holding += f"，当前新增额度为 {_number_text(current_add)}"
    if acceptance_text:
        return f"{ticker}：{acceptance_text}，{zone}，{action}。当前价 {current} 位于{zone}。{reason}{holding}。"
    return f"{ticker} 当前价 {current}，位于{zone}，{action}。{reason}{holding}。"


def _buy_zone_acceptance_text(
    buy_zone_context: dict[str, Any] | None,
    buy_zone_display: dict[str, Any] | None = None,
) -> str:
    display = buy_zone_display or {}
    context = buy_zone_context or {}
    text = str(display.get("acceptance_state_text") or context.get("acceptance_state_text") or "").strip()
    if text:
        return text
    state = str(display.get("acceptance_state") or context.get("acceptance_state") or "").strip().upper()
    return {
        "CLEAR_ACCEPTANCE": "明显承接",
        "FORMING_ACCEPTANCE": "初步承接",
        "WEAK_ACCEPTANCE": "承接不足",
        "HIGH_VOLUME_UNCONFIRMED": "放量未确认",
        "FALLING_KNIFE_RISK": "飞刀风险",
        "STRUCTURE_BROKEN": "结构破坏",
    }.get(state, "")


def _entry_quality_text(
    buy_zone_context: dict[str, Any] | None,
    buy_zone_display: dict[str, Any] | None = None,
) -> str:
    display = buy_zone_display or {}
    context = buy_zone_context or {}
    text = str(display.get("entry_quality_text") or "").strip()
    if text:
        return text
    quality = str(display.get("entry_quality") or context.get("entry_quality") or "").strip().upper()
    return {
        "GOOD_LEFT_SIDE": "舒服左侧",
        "EDGE_OBSERVE": "边缘观察",
        "WAIT_CONFIRMATION": "等确认",
        "HIGH_RISK": "高风险",
        "INVALID": "无效",
    }.get(quality, "")


def _current_subzone_display_text(
    buy_zone_context: dict[str, Any] | None,
    buy_zone_display: dict[str, Any] | None = None,
) -> str:
    display = buy_zone_display or {}
    context = buy_zone_context or {}
    explicit = str(display.get("current_subzone_display_text") or context.get("current_subzone_display_text") or "").strip()
    if explicit:
        return explicit
    subzone = str(display.get("current_subzone") or context.get("current_subzone") or "").strip().upper()
    base = {
        "DEEP_SUPPORT_ZONE": "深度承接区",
        "LEFT_PROBE": "左侧试仓候选区",
        "LEFT_PROBE_LOWER": "左侧试仓候选区",
        "LEFT_PROBE_MID": "左侧试仓候选区",
        "LEFT_PROBE_UPPER": "左侧试仓候选区",
        "ACCEPTANCE_OBSERVATION_ZONE": "承接观察区",
        "REPAIR_OBSERVATION_ZONE": "修复观察区",
        "REEVALUATION_ZONE": "重评区",
        "INVALIDATION_ZONE": "结构失效风险区",
        "CHASE_RISK_ZONE": "追高风险区",
        "ABOVE_TECHNICAL_PULLBACK_BAND": "等待回踩区",
        "OUTSIDE": "观察区外",
    }.get(subzone, "")
    if not base:
        primary = str(context.get("primary_zone") or "").strip().upper()
        if primary == "PULLBACK_WATCH":
            base = "承接观察区"
        elif primary == "PULLBACK_BUY":
            base = "左侧试仓候选区"
        elif primary in {"PULLBACK_UPPER_WATCH", "REPAIR_WATCH"}:
            base = "修复观察区"
        elif primary == "INVALIDATION":
            base = "结构失效风险区"
        elif primary == "CHASE_RISK":
            base = "追高风险区"
    position = str(display.get("current_subzone_position_label") or context.get("current_subzone_position_label") or "").strip().upper()
    if not position:
        position = _infer_current_subzone_position_label(context, subzone)
    suffix = {
        "LOWER_EDGE": "下沿",
        "MID_ZONE": "中段",
        "UPPER_EDGE": "上沿",
    }.get(position, "")
    if base and suffix and not base.endswith(suffix):
        return f"{base}{suffix}"
    return base


def _infer_current_subzone_position_label(context: dict[str, Any], subzone: str) -> str:
    current = _first_number(context, "current_price", "currentPrice")
    if current is None:
        return ""
    if subzone == "ACCEPTANCE_OBSERVATION_ZONE":
        low = _first_number(context, "left_probe_zone_high", "leftProbeZoneHigh")
        high = _first_number(context, "observe_zone_high", "observeZoneHigh")
    elif subzone == "REPAIR_OBSERVATION_ZONE":
        low = _first_number(context, "observe_zone_high", "observeZoneHigh")
        high = _first_number(context, "pullback_zone_high", "pullbackZoneHigh")
    elif subzone.startswith("LEFT_PROBE") or subzone == "LEFT_PROBE":
        explicit = str(context.get("left_probe_position_label") or "").strip().upper()
        if explicit:
            return explicit
        low = _first_number(context, "left_probe_zone_low", "leftProbeZoneLow")
        high = _first_number(context, "left_probe_zone_high", "leftProbeZoneHigh")
    else:
        return ""
    if low is None or high is None:
        return ""
    low, high = sorted((low, high))
    width = high - low
    if width <= 0 or current < low or current > high:
        return "OUTSIDE"
    position = (current - low) / width
    if position < 0.35:
        return "LOWER_EDGE"
    if position < 0.70:
        return "MID_ZONE"
    return "UPPER_EDGE"


def _decision_primary_reason(
    batting: dict[str, str],
    buy_zone_context: dict[str, Any],
    buy_zone_display: dict[str, Any] | None = None,
) -> str:
    display = buy_zone_display or {}
    acceptance = str(display.get("acceptance_state") or buy_zone_context.get("acceptance_state") or "").strip().upper()
    volume_score = _first_number(
        buy_zone_context,
        "volume_price_score",
        "volumePriceScore",
        "volume_acceptance_score",
        "volumeAcceptanceScore",
        "confirmation_score",
        "confirmationScore",
    )
    confirm = _first_number(
        buy_zone_context,
        "required_confirmation_price",
        "requiredConfirmationPrice",
        "confirmation_price",
        "confirmationPrice",
    )
    if acceptance == "WEAK_ACCEPTANCE":
        if volume_score is not None and confirm is not None:
            return f"量价承接 {_number_text(volume_score)}，尚未站上 {_money(confirm)}"
        if confirm is not None:
            return f"量价承接不足，尚未站上 {_money(confirm)}"
        return "量价承接不足，尚未形成主动买点"
    if acceptance == "FORMING_ACCEPTANCE":
        return "初步承接，但尚未完全站上确认线"
    if acceptance == "CLEAR_ACCEPTANCE":
        return "量价承接较清晰，仍需按仓位与风险复核"
    if acceptance == "HIGH_VOLUME_UNCONFIRMED":
        return "放量未确认，等收盘确认 / 事件复核"
    if acceptance == "FALLING_KNIFE_RISK":
        return "快速下跌或靠近失效线，存在飞刀风险"
    if acceptance == "STRUCTURE_BROKEN":
        return "价格跌破失效线，原左侧逻辑需重新复核"
    volume_state = str(batting.get("volume_state") or "").strip()
    status = str(batting.get("status") or "").strip()
    if status in {"买区上沿", "修复观察区"} or "修复观察" in str(batting.get("zone_label") or ""):
        return "当前价接近技术回踩带上沿，量价承接未确认"
    if "高于" in status or "追高" in status:
        return "当前价高于技术回踩带，系统提示追高风险"
    if "数据不足" in status:
        return "技术承接数据不足，需补齐后再复核"
    if "未确认" in volume_state or "不足" in volume_state:
        return "价格进入技术回踩带，但量价承接未确认"
    return "当前仍需等待量价确认和风险收益比复核"


def _decision_key_price_items(
    report: dict[str, Any],
    conclusion: dict[str, Any],
    buy_zone_context: dict[str, Any],
    buy_zone_display: dict[str, Any] | None = None,
) -> list[tuple[str, str]]:
    left_low = _first_number(buy_zone_context, "left_probe_zone_low")
    left_high = _first_number(buy_zone_context, "left_probe_zone_high")
    observe_high = _first_number(buy_zone_context, "observe_zone_high")
    pullback_high = _first_number(buy_zone_context, "pullback_zone_high")
    confirm = _first_number(conclusion, "confirm_price") or _first_number(buy_zone_context, "confirmation_price")
    invalidation = _first_number(conclusion, "invalidation_price") or _first_number(buy_zone_context, "invalidation_price")
    current_zone = _current_subzone_range(report, buy_zone_context)
    current_subzone = _current_subzone_display_text(buy_zone_context, buy_zone_display)
    current_text = f"{current_subzone}｜{current_zone}" if current_subzone and current_zone else current_subzone or current_zone
    return [
        ("左侧试仓候选", _range_text(left_low, left_high)),
        ("承接观察", _range_text(left_high, observe_high)),
        ("当前所在", current_text),
        ("重评线", f"站上 {_money(confirm)} 后重新评估" if confirm is not None else "暂缺"),
        ("失效复核", f"跌破 {_money(invalidation)}" if invalidation is not None else "暂缺"),
    ]


def _current_subzone_range(report: dict[str, Any], buy_zone_context: dict[str, Any]) -> str:
    current = _report_current_price(report)
    left_low = _first_number(buy_zone_context, "left_probe_zone_low")
    left_high = _first_number(buy_zone_context, "left_probe_zone_high")
    observe_high = _first_number(buy_zone_context, "observe_zone_high")
    pullback_high = _first_number(buy_zone_context, "pullback_zone_high")
    invalid_low = _first_number(buy_zone_context, "invalidation_risk_zone_low")
    invalid_high = _first_number(buy_zone_context, "invalidation_risk_zone_high")
    if current is not None:
        for low, high in ((left_low, left_high), (left_high, observe_high), (observe_high, pullback_high), (invalid_low, invalid_high)):
            if _price_in_range(current, low, high):
                return _range_text(low, high)
    low, high = _batting_zone_bounds(report, buy_zone_context)
    return _range_text(low, high)


def _decision_next_steps(batting: dict[str, str], buy_zone_context: dict[str, Any]) -> list[str]:
    left_low = _first_number(buy_zone_context, "left_probe_zone_low")
    left_high = _first_number(buy_zone_context, "left_probe_zone_high")
    confirm = _first_number(buy_zone_context, "confirmation_price")
    invalidation = _first_number(buy_zone_context, "invalidation_price")
    return [
        f"不新增：{batting.get('entry_condition') or '量价承接未确认'}",
        f"若回落：观察 {_range_text(left_low, left_high)} 是否承接",
        f"若上行：站上 {_money(confirm)} 后重新评估，不等于直接买入" if confirm is not None else "若上行：等待重新评估线补齐",
        f"若破位：跌破 {_money(invalidation)} 后复核" if invalidation is not None else "若破位：等待失效线补齐",
    ]


def _trade_conclusion(
    report: dict[str, Any],
    action_result: Any | None = None,
    buy_zone_context: dict[str, Any] | None = None,
    buy_zone_display: dict[str, Any] | None = None,
) -> dict[str, Any]:
    buy_zone_context = buy_zone_context or {}
    has_canonical_context = bool(str(buy_zone_context.get("current_action") or "").strip())
    buy_zone_display = buy_zone_display or (
        build_buy_zone_display(buy_zone_context, report, mode="report") if has_canonical_context else {}
    )
    confirm_price = _first_number(buy_zone_context, "confirmation_price") or _first_number(report, "confirmation_price", "radar_confirmation_price", "confirm_line")
    invalidation_price = _first_number(buy_zone_context, "invalidation_price") or _first_number(report, "invalidation_price", "radar_invalidation_price", "invalid_line")
    chase_price = _first_number(buy_zone_context, "chase_price") or _first_number(report, "chase_above_price", "radar_chase_above_price", "chase_price")
    batting_low, batting_high = _batting_zone_bounds(report, buy_zone_context)
    zone_selection = _zone_selection(report, buy_zone_context)
    zone_text = str(buy_zone_display.get("zone_text") or buy_zone_context.get("primary_zone_text") or _trade_zone_text(report))
    rating_text = str(buy_zone_display.get("badge_label") or _rating_text(report, action_result, buy_zone_context))
    action_text = str(buy_zone_display.get("main_action_text") or buy_zone_context.get("action_text") or _current_action_text(report, action_result))
    confirm_text = (
        f"重新评估线：放量站上 {_money(confirm_price)} 后重新评估，不等于直接买入"
        if confirm_price is not None
        else _next_step_sentence(report)
    )
    risk_line_text = f"跌破 {_money(invalidation_price)}" if invalidation_price is not None else "暂缺"
    next_review_trigger = confirm_text if confirm_price is not None else _next_step_sentence(report)
    confidence = str(getattr(action_result, "confidence_level", "") or _data_confidence(report) or "中")
    buy_premise_text = _buy_premise_text(report, buy_zone_context)
    return {
        "rating_text": rating_text,
        "action_text": action_text,
        "current_action": str(buy_zone_context.get("current_action") or ""),
        "zone_text": zone_text,
        "primary_zone_text": zone_selection["primary_zone_text"],
        "reference_zone_texts": zone_selection["reference_zone_texts"],
        "zone_selection_reason": zone_selection["zone_selection_reason"],
        "confirm_price": confirm_price,
        "confirm_text": confirm_text,
        "invalidation_price": invalidation_price,
        "chase_price": chase_price,
        "batting_zone_low": batting_low,
        "batting_zone_high": batting_high,
        "risk_line_text": risk_line_text,
        "next_review_trigger": next_review_trigger,
        "buy_premise_text": buy_premise_text,
        "confidence_level": confidence,
        "buy_zone_display": buy_zone_display,
        "main_conclusion_text": str(buy_zone_display.get("main_conclusion_text") or ""),
        "current_subzone_display_text": str(
            buy_zone_display.get("current_subzone_display_text")
            or _current_subzone_display_text(buy_zone_context, buy_zone_display)
            or ""
        ),
        "risk_reward_text": str(buy_zone_display.get("risk_reward_text") or ""),
    }


def _buy_premise_text(report: dict[str, Any], buy_zone_context: dict[str, Any] | None = None) -> str:
    buy_zone_context = buy_zone_context or {}
    final_score = _number(report.get("final_score"))
    risk_score = _number(report.get("risk_score"))
    parts = ["重新评估线"]
    if final_score is None or final_score < 70:
        parts.append("综合评分回到70以上")
    if risk_score is None or risk_score < 55:
        parts.append("风险复核完成")
    if len(parts) == 1:
        setup_score = _number(buy_zone_context.get("setup_score"))
        if setup_score is None or setup_score < 62:
            parts.append("setup score 达到小仓观察阈值")
        parts.extend(["量价继续确认", "风险复核完成"])
    return " + ".join(parts)


def _batting_zone_bounds(report: dict[str, Any], buy_zone_context: dict[str, Any] | None = None) -> tuple[float | None, float | None]:
    buy_zone_context = buy_zone_context or {}
    low = _first_number(buy_zone_context, "pullback_zone_low") or _first_number(
        report,
        "effective_technical_entry_zone_low",
        "radar_effective_technical_entry_zone_low",
        "technical_pullback_zone_low",
        "radar_technical_pullback_zone_low",
        "technical_entry_zone_low",
        "radar_technical_entry_zone_low",
    )
    high = _first_number(buy_zone_context, "pullback_zone_high") or _first_number(
        report,
        "effective_technical_entry_zone_high",
        "radar_effective_technical_entry_zone_high",
        "technical_pullback_zone_high",
        "radar_technical_pullback_zone_high",
        "technical_entry_zone_high",
        "radar_technical_entry_zone_high",
    )
    if low is None or high is None:
        low = low or _first_number(buy_zone_context, "support_zone_low") or _first_number(report, "deep_support_zone_low", "radar_deep_support_zone_low", "support_zone_low")
        high = high or _first_number(buy_zone_context, "support_zone_high") or _first_number(report, "deep_support_zone_high", "radar_deep_support_zone_high", "support_zone_high")
    if low is None or high is None:
        low = low or _first_number(report, "near_term_repair_zone_low", "radar_near_term_repair_zone_low", "technical_repair_zone_low", "radar_technical_repair_zone_low")
        high = high or _first_number(report, "near_term_repair_zone_high", "radar_near_term_repair_zone_high", "technical_repair_zone_high", "radar_technical_repair_zone_high")
    return low, high


def _batting_zone_context(
    report: dict[str, Any],
    conclusion: dict[str, Any] | None = None,
    buy_zone_context: dict[str, Any] | None = None,
) -> dict[str, str]:
    conclusion = conclusion or {}
    buy_zone_context = buy_zone_context or {}
    current = _report_current_price(report)
    low = _first_number(conclusion, "batting_zone_low") or _first_number(buy_zone_context, "pullback_zone_low")
    high = _first_number(conclusion, "batting_zone_high") or _first_number(buy_zone_context, "pullback_zone_high")
    if low is None or high is None:
        low, high = _batting_zone_bounds(report, buy_zone_context)
    confirm = _first_number(conclusion, "confirm_price") or _first_number(buy_zone_context, "confirmation_price") or _first_number(report, "confirmation_price", "radar_confirmation_price")
    invalidation = _first_number(conclusion, "invalidation_price") or _first_number(buy_zone_context, "invalidation_price") or _first_number(report, "invalidation_price", "radar_invalidation_price")
    chase = _first_number(conclusion, "chase_price") or _first_number(buy_zone_context, "chase_price") or _first_number(report, "chase_above_price", "radar_chase_above_price")
    action_code = str(buy_zone_context.get("current_action") or conclusion.get("current_action") or "").upper()
    zone_position = _first_number(buy_zone_context, "zone_position", "zonePosition")
    if action_code == "DATA_INSUFFICIENT":
        status = "数据不足"
    elif current is None:
        status = "价格暂缺"
    elif chase is not None and current >= chase:
        status = "追高区"
    elif low is None and high is None:
        status = "数据不足"
    elif zone_position is not None and zone_position > 0.75 and low is not None and high is not None and low <= current <= high:
        status = "买区上沿"
    elif low is not None and high is not None and low <= current <= high:
        status = "区内"
    elif high is not None and current > high:
        status = "高于技术回踩带"
    elif low is not None and current < low:
        status = "低于技术回踩带"
    else:
        status = "等待确认"
    entry_condition = _batting_entry_condition(status, action_code)
    operation = _batting_operation(status, str(conclusion.get("action_text") or ""), action_code)
    relative_text = _batting_relative_text(current, low, high, status)
    zone_label = _batting_zone_label(status)
    return {
        "zone_label": zone_label,
        "zone_range": _range_text(low, high),
        "current_price": _money(current),
        "distance_to_upper": _distance_to_level_text(current, high),
        "distance_to_lower": _distance_to_level_text(current, low),
        "status": status,
        "relative_text": relative_text,
        "entry_condition": entry_condition,
        "operation": operation,
        "reevaluation_line": f"放量站上 {_money(confirm)} 后重新判断" if confirm is not None else "暂缺",
        "invalidation_line": f"跌破 {_money(invalidation)} 后系统不建议新增" if invalidation is not None else "暂缺",
        "chase_line": f"≥ {_money(chase)} 追高风险提醒" if chase is not None else "暂缺",
        "volume_state": _batting_volume_state(report, buy_zone_context),
    }


def _batting_relative_text(current: float | None, low: float | None, high: float | None, status: str) -> str:
    if current is None:
        return "当前价暂缺"
    price = _money(current)
    if status == "区内":
        return f"当前价 {price}，在技术回踩带内"
    if status == "买区上沿":
        return f"当前价 {price}，位于买区上沿 / 修复观察区，不是主动买点"
    if status == "追高区":
        return f"当前价 {price}，已进入追高区"
    if status == "高于技术回踩带":
        return f"当前价 {price}，高于技术回踩带"
    if status == "低于技术回踩带":
        return f"当前价 {price}，低于技术回踩带"
    return f"当前价 {price}，技术回踩带数据不足"


def _batting_zone_label(status: str) -> str:
    if status == "买区上沿":
        return "修复观察区"
    if status == "数据不足":
        return "买区数据"
    if status == "追高区":
        return "追高风险区"
    if status == "低于技术回踩带":
        return "结构失效风险区"
    return "技术回踩带"


def _distance_to_level_text(current: float | None, level: float | None) -> str:
    if current is None or level in (None, 0):
        return "暂缺"
    return _signed_pct((current / level - 1.0) * 100.0)


def _batting_entry_condition(status: str, action_code: str) -> str:
    if status == "数据不足":
        return "先补齐技术承接数据"
    if status == "追高区":
        return "等待回到技术回踩带或重新形成低吸结构"
    if status == "高于技术回踩带":
        return "回到技术回踩带后观察量价承接"
    if status == "低于技术回踩带":
        return "先确认未跌破失效线并出现承接"
    if action_code in {"ALLOW_SMALL_BUY", "ALLOW_ADD_ON_PULLBACK"}:
        return "价格候选区，仍需量价确认"
    return "缩量回踩，承接未确认"


def _batting_operation(status: str, action_text: str, action_code: str) -> str:
    if action_text and any(token in action_text for token in ("当前不新增", "当前不建议新增", "持有观察", "不建议买入", "不建议加仓")):
        return action_text
    if status == "数据不足":
        return "先补数据，不给明确买区"
    if status == "追高区":
        return "不追，等待回到技术回踩带"
    if action_code in {"ALLOW_SMALL_BUY", "ALLOW_ADD_ON_PULLBACK"}:
        return "低风险观察参考，不能一次打满"
    if status == "高于技术回踩带":
        return "不追，等回踩到技术回踩带后观察承接"
    if status == "低于技术回踩带":
        return "先复核失效线，系统不建议新增"
    return action_text or "等待承接确认"


def _batting_volume_state(report: dict[str, Any], buy_zone_context: dict[str, Any]) -> str:
    status = str(_first_present(report, "volume_price_status", "volumePriceStatus") or "").upper()
    score = _first_number(report, "volume_price_score", "volumePriceScore") or _first_number(buy_zone_context, "volume_acceptance_score")
    if status:
        return _volume_price_status_label(status, score)
    if score is not None:
        return f"承接分 {_number_text(score)}"
    return "普通量，尚未形成强确认"


def _rating_text(report: dict[str, Any], action_result: Any | None, buy_zone_context: dict[str, Any] | None = None) -> str:
    context_action = str((buy_zone_context or {}).get("current_action") or "").upper()
    context_map = {
        "ALLOW_SMALL_BUY": "小仓观察建议",
        "ALLOW_ADD_ON_PULLBACK": "小仓观察建议",
        "WAIT_PULLBACK": "等待回踩",
        "WAIT_CONFIRMATION": "等待确认",
        "BLOCK_CHASE": "追高风险",
        "RISK_REVIEW": "暂不参与",
        "DATA_INSUFFICIENT": "数据不足",
        "AVOID": "暂不参与",
    }
    if context_action in context_map:
        return context_map[context_action]
    action_code = str(getattr(action_result, "action_code", "") or "").upper()
    action_map = {
        "ALLOW_SMALL_BUY": "小仓观察建议",
        "ADD_ON_PULLBACK": "小仓观察建议",
        "ADD_ON_BREAKOUT": "等待确认",
        "WAIT_CONFIRMATION": "等待确认",
        "HOLD_NO_ADD": "等待确认",
        "BLOCK_CHASE": "追高风险",
        "BREAKDOWN_REVIEW": "暂不参与",
        "EVENT_REVIEW": "暂不参与",
        "DATA_INSUFFICIENT": "数据不足",
        "REDUCE_RISK": "暂不参与",
    }
    if action_code in action_map:
        return action_map[action_code]
    decision = str(report.get("decision") or "").upper()
    return {
        "ALLOW_BUY": "小仓观察建议",
        "WAIT": "等待确认",
        "BLOCK_CHASE": "追高风险",
        "DATA_MISSING": "数据不足",
        "AVOID": "暂不参与",
    }.get(decision, "等待确认")


def _current_action_text(report: dict[str, Any], action_result: Any | None) -> str:
    action_code = str(getattr(action_result, "action_code", "") or "").upper()
    if action_code in {"BLOCK_CHASE"} or _trade_zone_text(report) in {"追高禁区", "追高风险区"}:
        return "不主动追买"
    if action_code in {"DATA_INSUFFICIENT"}:
        return "需人工判断"
    if action_code in {"BREAKDOWN_REVIEW", "EVENT_REVIEW", "REDUCE_RISK"}:
        return "不建议新增"
    if action_code in {"ALLOW_SMALL_BUY", "ADD_ON_PULLBACK"}:
        return "小仓观察"
    if action_code in {"HOLD_NO_ADD"}:
        return "持有观察"
    return "不主动追买"


def _trade_zone_text(report: dict[str, Any]) -> str:
    zone = _current_zone_label(report)
    return {
        "深度支撑区": "深度买区",
        "估值参考区": "合理买区",
        "买区内": "合理买区",
        "近端修复观察区": "修复观察区",
        "追高风险区": "追高风险区",
        "破位复核区": "风险失效区",
        "区间待补": "数据不足",
    }.get(zone, zone or "数据不足")


def _zone_selection(report: dict[str, Any], buy_zone_context: dict[str, Any] | None = None) -> dict[str, Any]:
    buy_zone_context = buy_zone_context or {}
    primary = str(buy_zone_context.get("primary_zone_text") or _current_zone_label(report))
    current = _report_current_price(report)
    references: list[str] = []
    if current is not None:
        for item in _range_chart_items(report, buy_zone_context):
            label = str(item.get("label") or "").strip()
            low, high = item.get("range") or (None, None)
            if label == "追高风险区":
                if low is not None and current >= low:
                    references.append(label)
                continue
            if _price_in_range(current, low, high):
                references.append(label)
    references = _dedupe_text(references)
    if primary and primary not in references and primary not in {"区间待补", "观察", "追高风险"}:
        references.insert(0, primary)
    if len(references) > 1:
        reason = (
            f"当前价同时落入多个参考区间，系统按交易动作优先级选择【{primary}】作为主区间。"
            f"参考区间：{'、'.join(references)}。"
        )
    elif references:
        reason = f"当前价落入【{references[0]}】，系统选择【{primary}】作为主区间。"
    else:
        reason = str(buy_zone_context.get("zone_selection_reason") or f"当前价未落入完整参考区间，系统按价格位置选择【{primary}】作为主区间。")
    if buy_zone_context.get("zone_selection_reason"):
        reason = str(buy_zone_context.get("zone_selection_reason"))
    return {
        "primary_zone_text": primary,
        "reference_zone_texts": references,
        "zone_selection_reason": reason,
    }


def _portfolio_context(
    report: dict[str, Any],
    row: dict[str, Any],
    action_result: Any | None,
    buy_zone_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shares = _first_number(row, "current_shares", "currentShares", "quantity", "shares")
    avg_cost = _first_number(row, "avg_cost", "avgCost", "averageCost", "average_cost")
    market_value = _first_number(row, "market_value", "marketValue")
    unrealized_pnl = _first_number(row, "unrealized_pnl", "unrealizedPnl")
    unrealized_pnl_pct = _first_number(row, "unrealized_pnl_pct", "unrealizedPnlPct")
    weight = _first_number(row, "portfolio_weight", "portfolioWeight", "positionPct")
    updated_at = _row_value(row, "portfolio_updated_at", "portfolioUpdatedAt", "updatedAt", "updated_at")
    if action_result is not None:
        shares = shares if shares is not None else _number(getattr(action_result, "current_shares", None))
        avg_cost = avg_cost if avg_cost is not None else _number(getattr(action_result, "avg_cost", None))
        market_value = market_value if market_value is not None else _number(getattr(action_result, "market_value", None))
        unrealized_pnl = unrealized_pnl if unrealized_pnl is not None else _number(getattr(action_result, "unrealized_pnl", None))
        unrealized_pnl_pct = unrealized_pnl_pct if unrealized_pnl_pct is not None else _number(getattr(action_result, "unrealized_pnl_pct", None))
        weight = weight if weight is not None else _number(getattr(action_result, "current_weight", None))
        updated_at = updated_at or getattr(action_result, "portfolio_updated_at", None)
    has_position = bool((shares is not None and shares > 0) or (shares is None and weight is not None and weight > 0))
    conclusion = _trade_conclusion(report, action_result, buy_zone_context)
    return {
        "shares": shares,
        "avg_cost": avg_cost,
        "market_value": market_value,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "portfolio_weight": weight,
        "portfolio_updated_at": updated_at,
        "has_position": has_position,
        "position_status_text": _position_status_text(shares, weight),
        "action_for_existing_position": str((buy_zone_context or {}).get("existing_position_action_text") or _action_for_existing_position(conclusion, action_result)),
        "action_for_no_position": str((buy_zone_context or {}).get("no_position_action_text") or _action_for_no_position(conclusion, action_result)),
    }


def _portfolio_context_with_buy_zone_display(
    portfolio_context: dict[str, Any] | None,
    buy_zone_display: dict[str, Any] | None,
) -> dict[str, Any]:
    updated = dict(portfolio_context or {})
    if not buy_zone_display:
        return updated
    account_action = str(
        buy_zone_display.get("account_action_text")
        or buy_zone_display.get("position_action_text")
        or ""
    ).strip()
    main_action = str(
        buy_zone_display.get("main_action_text")
        or buy_zone_display.get("display_action_text")
        or buy_zone_display.get("action_text")
        or ""
    ).strip()
    display_action = account_action or main_action
    if not display_action:
        return updated
    if updated.get("has_position"):
        updated["action_for_existing_position"] = display_action
    else:
        updated["action_for_no_position"] = display_action
    return updated


def _position_status_text(shares: float | None, weight: float | None) -> str:
    if (shares is not None and shares > 0) or (shares is None and weight is not None and weight > 0):
        suffix = f"｜仓位 {_ratio_pct(weight)}" if weight is not None else ""
        return f"已有持仓{suffix}"
    return "未持仓"


def _action_for_existing_position(conclusion: dict[str, Any], action_result: Any | None) -> str:
    rating = str(conclusion.get("rating_text") or "")
    action_code = str(getattr(action_result, "action_code", "") or "").upper()
    if rating in {"禁止追高", "追高风险", "暂不参与", "数据不足"}:
        return "持有观察，系统不建议加仓"
    if action_code in {"ALLOW_SMALL_BUY", "ADD_ON_PULLBACK"}:
        return "回踩复核，是否新增由用户确认"
    return "持有观察，未到加仓确认位"


def _action_for_no_position(conclusion: dict[str, Any], action_result: Any | None) -> str:
    rating = str(conclusion.get("rating_text") or "")
    if rating in {"允许小仓", "小仓观察", "小仓观察建议"}:
        return "小仓观察建议，等待量价继续确认"
    if rating in {"禁止追高", "追高风险"}:
        return "不追买，等待回到观察区"
    if rating in {"暂不参与", "数据不足"}:
        return "暂不参与，先补数据或等事件复核"
    return "不追买，等待确认价或更低买区"


def _position_context_panel_html(context: dict[str, Any]) -> str:
    if context.get("has_position"):
        headline = (
            f"我的持仓：{_quantity_text(context.get('shares'))} 股"
            f"｜成本 {_money(context.get('avg_cost'))}"
            f"｜浮盈亏 {_signed_money(context.get('unrealized_pnl'))} / {_signed_pct(context.get('unrealized_pnl_pct'))}"
        )
    else:
        headline = "我的持仓：未持仓"
    if context.get("has_position"):
        rows = [
            ("市值", _compact_money(context.get("market_value"))),
            ("组合仓位", _ratio_pct(context.get("portfolio_weight"))),
            ("已有持仓动作", str(context.get("action_for_existing_position") or "持有观察")),
        ]
    else:
        rows = [
            ("市值", _compact_money(context.get("market_value"))),
            ("组合仓位", _ratio_pct(context.get("portfolio_weight"))),
            ("无持仓动作", str(context.get("action_for_no_position") or "等待确认")),
        ]
    row_html = "".join(f"<div><span>{escape(label)}</span><b>{escape(value)}</b></div>" for label, value in rows)
    return (
        '<aside class="ai-radar-position-context">'
        f"<strong>{escape(headline)}</strong>"
        f'<span>动作建议：{escape(str(context.get("action_for_existing_position") if context.get("has_position") else context.get("action_for_no_position")))}</span>'
        f'<div class="ai-radar-position-context-grid">{row_html}</div>'
        "</aside>"
    )


def _batting_zone_card_html(
    report: dict[str, Any],
    conclusion: dict[str, Any],
    buy_zone_context: dict[str, Any] | None = None,
) -> str:
    batting = _batting_zone_context(report, conclusion, buy_zone_context)
    zone_label = str(batting.get("zone_label") or "技术回踩带")
    rows = [
        (zone_label, str(batting.get("zone_range") or "暂缺")),
        ("当前价", str(batting.get("current_price") or "暂缺")),
        ("距上沿", str(batting.get("distance_to_upper") or "暂缺")),
        ("距下沿", str(batting.get("distance_to_lower") or "暂缺")),
        ("状态", str(batting.get("status") or "暂缺")),
        ("量能", str(batting.get("volume_state") or "暂缺")),
        ("承接条件", str(batting.get("entry_condition") or "等待承接确认")),
        ("当前建议", str(batting.get("operation") or "等待确认")),
    ]
    body = "".join(f"<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>" for label, value in rows)
    return (
        '<aside class="ai-radar-batting-card">'
        f'<div class="ai-radar-section-title"><span>技术回踩带</span><b>{escape(zone_label)} / 位置 / 建议</b></div>'
        f'<p>{escape(str(batting.get("relative_text") or "技术回踩带数据不足"))}</p>'
        f'<div class="ai-radar-batting-grid">{body}</div>'
        "</aside>"
    )


def _conclusion_first_sentence(report: dict[str, Any], conclusion: dict[str, Any], buy_zone_context: dict[str, Any] | None = None) -> str:
    ticker = str(report.get("ticker") or "该股票")
    batting = _batting_zone_context(report, conclusion, buy_zone_context)
    return f"本页结论：{ticker} {batting.get('relative_text')}，{batting.get('operation')}。"


def _executive_judgments(
    report: dict[str, Any],
    action_result: Any,
    portfolio_context: dict[str, Any],
    conclusion: dict[str, Any],
    buy_zone_context: dict[str, Any] | None = None,
) -> list[str]:
    batting = _batting_zone_context(report, conclusion, buy_zone_context)
    position_action = (
        f"已有持仓，{portfolio_context.get('action_for_existing_position')}。"
        if portfolio_context.get("has_position")
        else f"未持仓，{portfolio_context.get('action_for_no_position')}。"
    )
    return [
        f"{batting.get('zone_label')}在 {batting.get('zone_range')}，{batting.get('relative_text')}，距上沿 {batting.get('distance_to_upper')}。",
        f"入场条件：{batting.get('entry_condition')}；重新评估线：{batting.get('reevaluation_line')}。",
        f"{batting.get('invalidation_line')}；{position_action}",
    ]


def _quality_quality_sentence(report: dict[str, Any]) -> str:
    support, drag = _score_support_and_drag(report)
    return f"主要支撑来自{support}，主要限制来自{drag}。"


def _holding_context_text(row: dict[str, Any], action_result: Any | None = None) -> str:
    shares = _first_number(row, "current_shares", "currentShares", "quantity", "shares")
    weight = _first_number(row, "portfolio_weight", "portfolioWeight", "positionPct")
    if action_result is not None:
        if shares is None:
            shares = _number(getattr(action_result, "current_shares", None))
        if weight is None:
            weight = _number(getattr(action_result, "current_weight", None))
    if (shares is not None and shares > 0) or (shares is None and weight is not None and weight > 0):
        suffix = f"｜仓位 {_ratio_pct(weight)}" if weight is not None else ""
        return f"已有持仓{suffix}"
    return "未持仓 / 仅研究观察"


def _invalidation_sentence(report: dict[str, Any]) -> str:
    invalid = _first_number(report, "invalidation_price", "radar_invalidation_price")
    if invalid is not None:
        return f"跌破失效线 {_money(invalid)} 后转入破位复核。"
    return "失效线暂缺，先以支撑破位和量价承接失败复核。"


def _research_summary_lines(report: dict[str, Any], snapshot: dict[str, Any], market: dict[str, Any]) -> list[str]:
    ticker = str(report.get("ticker") or "该股票")
    company = str(report.get("company_name") or ticker)
    status = _core_status(report)
    score = _number_text(report.get("final_score"))
    data_confidence = _data_confidence(report)
    summary = _localized_report_summary(report)
    return [
        summary
        or f"{company} 当前处于“{status}”语境，Radar 总分 {score}；列表只给入口，单股页用于复核区间、风险和重新评估线。",
        f"价格位置：{_entry_sentence(report)}",
        f"核心判断：{_decision_to_sentence(report)}",
        f"下一步重点：{_next_step_sentence(report)}",
        f"数据完整度：{data_confidence}；缺失字段放在报告末尾，不让辅助数据主导结论。",
    ]

    ticker = str(report.get("ticker") or "该股票")
    company = str(report.get("company_name") or ticker)
    status = _core_status(report)
    score = _number_text(report.get("final_score"))
    data_confidence = _data_confidence(report)
    summary = str(report.get("summary") or "").strip()
    lines = [
        summary if summary else f"{company} 当前处于“{status}”语境，Radar 总分 {score}，适合先按研究清单复核而不是看状态码交易。",
        f"价格位置：{_entry_sentence(report)}",
        f"核心判断：{_decision_to_sentence(report)}",
        f"下一步重点：{_next_step_sentence(report)}",
        f"数据完整度：{data_confidence}；缺失项放在报告末尾，不让内部缺数据状态主导结论。",
    ]
    return [line for line in lines if line]


def _range_chart_html(
    report: dict[str, Any],
    conclusion: dict[str, Any] | None = None,
    buy_zone_context: dict[str, Any] | None = None,
) -> str:
    buy_zone_context = buy_zone_context or {}
    conclusion = conclusion or _trade_conclusion(report, buy_zone_context=buy_zone_context)
    ranges = _range_chart_items(report, buy_zone_context)
    adaptive_low = _first_number(report, "adaptive_pullback_zone_low", "radar_adaptive_pullback_zone_low")
    adaptive_high = _first_number(report, "adaptive_pullback_zone_high", "radar_adaptive_pullback_zone_high")
    if ranges and ranges[0]["range"] == (None, None) and (adaptive_low is not None or adaptive_high is not None):
        ranges[0] = {
            "label": _adaptive_pullback_label(report),
            "range": (adaptive_low, adaptive_high),
            "tone": "blue",
        }
    current = _report_current_price(report)
    values = [value for item in ranges for value in item["range"] if value is not None]
    if current is not None:
        values.append(current)
    if not values:
        return _empty_card_html("价格行动地图", "缺少价格和区间数据，暂时无法绘制。")
    low = min(values)
    high = max(values)
    padding = max((high - low) * 0.08, 1.0)
    low -= padding
    high += padding
    marker_left = _range_position(current, low, high) if current is not None else None
    marker = (
        f'<i class="ai-radar-current-marker" style="left:{marker_left:.2f}%"><b>现价 {_money(current)}</b></i>'
        if marker_left is not None
        else ""
    )
    rows = []
    buy_zone_display = conclusion.get("buy_zone_display") if isinstance(conclusion.get("buy_zone_display"), dict) else {}
    current_subzone = _current_subzone_display_text(buy_zone_context, buy_zone_display)
    primary_zone = str(current_subzone or conclusion.get("primary_zone_text") or _current_zone_label(report))
    for item in ranges:
        item_low, item_high = item["range"]
        if item_low is None and item_high is None:
            continue
        current_class = " current" if _range_item_is_current(item["label"], primary_zone) else ""
        start = _range_position(item_low if item_low is not None else low, low, high)
        end = _range_position(item_high if item_high is not None else high, low, high)
        width = max(end - start, 1.2)
        rows.append(
            f'<div class="ai-radar-range-row{current_class}">'
            f'<span>{escape(item["label"])}</span>'
            f'<div class="ai-radar-range-track"><i class="{escape(item["tone"])}" style="left:{start:.2f}%;width:{width:.2f}%"></i>{marker}</div>'
            f'<b>{escape(_range_text(item_low, item_high))}</b>'
            f'<em>{escape(_range_action_text(item["label"]))}</em>'
            "</div>"
        )
    batting = _batting_zone_context(report, conclusion, buy_zone_context)
    reference_text = "、".join(label for label in (conclusion.get("reference_zone_texts") or []) if label not in {"主击球区 / 回踩击球区", "技术回踩带"}) or "暂无"
    zone_label = str(current_subzone or batting.get("zone_label") or "观察区")
    reason = _decision_primary_reason(batting, buy_zone_context, buy_zone_display)
    explanation = (
        f"当前位于{zone_label}，价格已回到技术带，但{reason}，尚不构成主动买点。"
        f"重新评估线用于重新判断，不等于直接买入。"
        f"参考区间：{reference_text}。失效线：{batting.get('invalidation_line')}。"
    )
    return (
        '<section class="ai-radar-card ai-radar-range-card">'
        f'<div class="ai-radar-section-title"><span>价格行动地图</span><b>{escape(zone_label)} / 五个核心子区</b></div>'
        f'<div class="ai-radar-range-axis"><span>{escape(_money(low))}</span><span>{escape(_money(high))}</span></div>'
        f'{"".join(rows)}'
        f'<p class="ai-radar-range-explain">{escape(explanation)}</p>'
        "</section>"
    )


def _range_chart_items(report: dict[str, Any], buy_zone_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    buy_zone_context = buy_zone_context or {}
    pullback_low = _first_number(buy_zone_context, "pullback_zone_low") or _first_number(report, "effective_technical_entry_zone_low", "radar_effective_technical_entry_zone_low", "technical_pullback_zone_low", "radar_technical_pullback_zone_low", "technical_entry_zone_low", "radar_technical_entry_zone_low")
    pullback_high = _first_number(buy_zone_context, "pullback_zone_high") or _first_number(report, "effective_technical_entry_zone_high", "radar_effective_technical_entry_zone_high", "technical_pullback_zone_high", "radar_technical_pullback_zone_high", "technical_entry_zone_high", "radar_technical_entry_zone_high")
    left_probe_low = _first_number(buy_zone_context, "left_probe_zone_low")
    left_probe_high = _first_number(buy_zone_context, "left_probe_zone_high")
    observe_high = _first_number(buy_zone_context, "observe_zone_high")
    invalidation_risk_low = _first_number(buy_zone_context, "invalidation_risk_zone_low")
    invalidation_risk_high = _first_number(buy_zone_context, "invalidation_risk_zone_high")
    support_low = _first_number(buy_zone_context, "support_zone_low") or _first_number(report, "deep_support_zone_low", "radar_deep_support_zone_low", "invalidation_price", "radar_invalidation_price")
    confirmation = _first_number(buy_zone_context, "confirmation_price") or _first_number(report, "confirmation_price", "radar_confirmation_price")
    invalidation = _first_number(buy_zone_context, "invalidation_price") or _first_number(report, "invalidation_price", "radar_invalidation_price")
    items: list[dict[str, Any]] = []
    if pullback_low is not None and pullback_high is not None and observe_high is not None:
        if left_probe_low is not None and left_probe_high is not None:
            items.append({"label": "左侧试仓候选区", "range": (left_probe_low, left_probe_high), "tone": "blue"})
        observation_start = left_probe_high or invalidation_risk_high or pullback_low
        if observation_start is None or observe_high is None or observation_start <= observe_high:
            items.append({"label": "承接观察区", "range": (observation_start, observe_high), "tone": "slate"})
        if observe_high is None or pullback_high is None or observe_high <= pullback_high:
            items.append({"label": "修复观察区", "range": (observe_high, pullback_high), "tone": "amber"})
        if confirmation is not None:
            items.append({"label": "重评区", "range": (confirmation, None), "tone": "green"})
        if invalidation_risk_low is not None and invalidation_risk_high is not None:
            items.append({"label": "结构失效风险区", "range": (invalidation_risk_low, invalidation_risk_high), "tone": "red"})
    else:
        items.append({"label": "技术回踩带", "range": (pullback_low, pullback_high), "tone": "blue"})
        if confirmation is not None:
            items.append({"label": "重评区", "range": (confirmation, None), "tone": "green"})
        if invalidation is not None:
            items.append({"label": "结构失效风险区", "range": (support_low or invalidation, invalidation), "tone": "red"})
    return items


def _range_action_text(label: str) -> str:
    if "修复观察" in label:
        return "持有观察，不主动新增"
    if "左侧试仓" in label:
        return "价格候选区，需量价确认"
    if "承接观察" in label:
        return "等承接确认"
    if "结构失效" in label:
        return "跌破后建议复核，不建议新增"
    if "重评" in label:
        return "站上后重新评估，不等于直接买入"
    if "趋势临界" in label:
        return "破位后重新评估"
    if "深度恐慌" in label:
        return "基本面复核"
    if "深度" in label or "承接" in label:
        return "等承接确认"
    if "估值" in label:
        return "仅作仓位参考"
    if "回踩" in label:
        return "价格候选区，需量价确认"
    if "重新评估" in label:
        return "站上后重新评估，不等于直接买入"
    if "修复" in label or "确认" in label:
        return "等待确认"
    if "追高" in label:
        return "追高风险提醒"
    return "不建议新增" if "失效" in label else "等待确认"


def _range_item_is_current(label: str, zone_text: str) -> bool:
    if not zone_text:
        return False
    pairs = (
        ("深度", "深度"),
        ("承接", "承接"),
        ("估值", "合理"),
        ("修复", "修复"),
        ("回踩", "回踩"),
        ("趋势", "趋势"),
        ("确认", "确认"),
        ("追高", "追高"),
        ("失效", "风险"),
    )
    return any(label_key in label and zone_key in zone_text for label_key, zone_key in pairs)


def _adaptive_pullback_label(report: dict[str, Any]) -> str:
    label = str(report.get("adaptive_pullback_label") or report.get("radar_adaptive_pullback_label") or "").strip()
    return label or "技术回踩参考区"


def _range_position(value: float | None, low: float, high: float) -> float:
    if value is None or high <= low:
        return 0.0
    return max(0.0, min(100.0, (value - low) / (high - low) * 100))


def _score_card_html(report: dict[str, Any], buy_zone_context: dict[str, Any] | None = None) -> str:
    buy_zone_context = buy_zone_context or {}
    items = [
        ("Setup", buy_zone_context.get("setup_score")),
        ("技术结构", buy_zone_context.get("technical_structure_score")),
        ("量能承接", buy_zone_context.get("volume_acceptance_score")),
        ("风险收益", buy_zone_context.get("risk_reward_score")),
    ]
    body = "".join(f"<div><span>{escape(label)}</span><strong>{escape(_number_text(value))}</strong></div>" for label, value in items)
    core_note = _core_position_notice(report, buy_zone_context)
    setup_note = _setup_score_note(buy_zone_context)
    return (
        '<section class="ai-radar-card score">'
        '<div class="ai-radar-section-title"><span>Setup 评分卡</span><b>结构 / 量能 / 盈亏比</b></div>'
        f'<div class="ai-radar-score-grid">{body}</div>'
        f'<p class="ai-radar-setup-explain">{escape(setup_note)}</p>'
        f'<span class="ai-radar-core-badge">{escape(core_note)}</span>'
        "</section>"
    )


def _score_explanation(report: dict[str, Any]) -> str:
    final_score = _number(report.get("final_score"))
    level = "可观察但非强买区"
    if final_score is not None and final_score >= 85:
        level = "质量较强，仍需价格与量价确认"
    elif final_score is not None and final_score < 65:
        level = "仅适合复核，不构成主动买入"
    support, drag = _score_support_and_drag(report)
    gate = _gate_reason_text(report)
    score_text = _number_text(final_score)
    return f"综合评分 {score_text}，用于核心仓资格、仓位上限和风险提示，不直接生成买区。主要支撑来自{support}，主要限制来自{drag}。{gate}"


def _setup_score_note(buy_zone_context: dict[str, Any]) -> str:
    if not buy_zone_context:
        return "Setup 分暂缺；买区应由技术结构、量价承接和风险收益比共同决定。"
    score = _number(buy_zone_context.get("setup_score"))
    if score is not None and score >= 70:
        return "观察级 setup，仍需量价确认和账户额度配合。"
    return "观察级 setup，量价未确认，不建议新增。"


def _core_position_notice(report: dict[str, Any], buy_zone_context: dict[str, Any]) -> str:
    if buy_zone_context and buy_zone_context.get("core_position_allowed") is False:
        return "非核心仓候选：综合分 < 70"
    final_score = _number(report.get("final_score"))
    if final_score is not None and final_score < 70:
        return "非核心仓候选：综合分 < 70"
    return "核心仓候选：仍需量价承接和风险收益比确认"


def _score_support_and_drag(report: dict[str, Any]) -> tuple[str, str]:
    score_items = [
        ("质量", _number(report.get("quality_score"))),
        ("成长", _number(report.get("growth_score"))),
        ("估值安全边际", _number(report.get("valuation_score"))),
        ("技术确认", _number(report.get("technical_score"))),
        ("风险控制", _number(report.get("risk_score"))),
    ]
    present = [(label, value) for label, value in score_items if value is not None]
    if not present:
        return "暂无明确加分项", "数据不足"
    support = max(present, key=lambda item: item[1])[0]
    drag = min(present, key=lambda item: item[1])[0]
    return support, drag


def _gate_reason_text(report: dict[str, Any]) -> str:
    reasons = _dedupe_text(
        [
            _localize_report_text(str(item))
            for item in [
                *(report.get("block_reasons") or []),
                *(report.get("decisionBlockReasons") or []),
                *(report.get("decisionReviewReasons") or []),
                *(report.get("review_reasons") or []),
            ]
            if str(item).strip()
        ]
    )
    if reasons:
        return f"风险复核提示：{reasons[0]}。"
    return "风险复核提示：未触发强买确认。"


def _risk_gate_notice(report: dict[str, Any]) -> str:
    final_score = _number(report.get("final_score"))
    risk_score = _number(report.get("risk_score"))
    if final_score is not None and final_score < 70:
        return "风险提示：综合评分低于70，系统不建议作为核心仓。"
    if risk_score is not None and risk_score < 55:
        return "风险提示：风险评分偏低，仍需风险复核。"
    return "风险提示：未满足强买条件，仍需量价与风险复核。"


def _volume_price_acceptance_card_html(
    report: dict[str, Any],
    technicals: dict[str, Any],
    row: dict[str, Any],
    history: pd.DataFrame | None,
) -> str:
    snapshot = _volume_price_acceptance_snapshot(report, technicals, row, history)
    status = str(snapshot.get("volume_price_status") or snapshot.get("volumePriceStatus") or "DATA_MISSING")
    score = _number(snapshot.get("volume_price_score", snapshot.get("volumePriceScore")))
    volume_ratio = _number(snapshot.get("volume_ratio", snapshot.get("volumeRatio")))
    volume_regime_cn = _display_value(snapshot.get("volume_regime_cn") or snapshot.get("volumeRegimeCn"))
    reason = _volume_price_reason_text(status, score, snapshot.get("acceptance_reason_cn") or snapshot.get("reason_cn") or snapshot.get("volumePriceReasonCn"))
    support_signal = _display_value(snapshot.get("support_signal_cn") or snapshot.get("supportSignalCn"))
    confirmation_signal = _display_value(snapshot.get("confirmation_signal_cn") or snapshot.get("confirmationSignalCn"))
    rows = [
        ("承接状态", _volume_price_status_label(status, score)),
        ("量能", f"{volume_regime_cn}｜{_volume_ratio_display(volume_ratio)}"),
        ("确认 / 支撑", f"{confirmation_signal}｜{support_signal}"),
    ]
    body = "".join(f"<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>" for label, value in rows)
    return (
        '<section class="ai-radar-card ai-radar-volume-price-card">'
        '<div class="ai-radar-section-title"><span>量价承接</span><b>承接质量</b></div>'
        f'<div class="ai-radar-volume-summary">{body}</div>'
        f'<p class="ai-radar-empty-note">{escape(reason)}</p>'
        "</section>"
    )


def _action_fusion_card_html(
    report: dict[str, Any],
    technicals: dict[str, Any],
    row: dict[str, Any],
    history: pd.DataFrame | None,
) -> str:
    result = _action_fusion_result(report, technicals, row, history)
    return action_fusion_card_html(result)


def _action_fusion_result(
    report: dict[str, Any],
    technicals: dict[str, Any],
    row: dict[str, Any],
    history: pd.DataFrame | None,
) -> Any:
    volume_snapshot = _volume_price_acceptance_snapshot(report, technicals, row, history)
    portfolio_snapshot = build_action_fusion_portfolio_context(str(report.get("ticker") or row.get("ticker") or ""))
    return _action_fusion_result_from_snapshots(report, technicals, row, volume_snapshot, portfolio_snapshot)


def _action_fusion_result_from_snapshots(
    report: dict[str, Any],
    technicals: dict[str, Any],
    row: dict[str, Any],
    volume_snapshot: dict[str, Any],
    portfolio_snapshot: dict[str, Any],
) -> Any:
    return evaluate_action_fusion(
        ticker=str(report.get("ticker") or row.get("ticker") or ""),
        context={
            **report,
            **technicals,
            **row,
            "volume_price_status": volume_snapshot.get("volume_price_status") or volume_snapshot.get("volumePriceStatus"),
            "volume_price_score": volume_snapshot.get("volume_price_score") or volume_snapshot.get("volumePriceScore"),
            "volume_ratio": volume_snapshot.get("volume_ratio") or volume_snapshot.get("volumeRatio"),
            "volume_regime_cn": volume_snapshot.get("volume_regime_cn") or volume_snapshot.get("volumeRegimeCn"),
            "volume_price_reason_cn": volume_snapshot.get("acceptance_reason_cn")
            or volume_snapshot.get("volumePriceReasonCn")
            or volume_snapshot.get("reason_cn"),
        },
        portfolio_context=portfolio_snapshot,
    )


def _volume_price_acceptance_snapshot(
    report: dict[str, Any],
    technicals: dict[str, Any],
    row: dict[str, Any],
    history: pd.DataFrame | None,
) -> dict[str, Any]:
    for source in (report, row, technicals):
        snapshot = source.get("volumePriceAcceptance") if isinstance(source, dict) else None
        if isinstance(snapshot, dict) and snapshot:
            return _enrich_volume_price_snapshot(snapshot, history)
        snapshot = source.get("volume_price_acceptance") if isinstance(source, dict) else None
        if isinstance(snapshot, dict) and snapshot:
            return _enrich_volume_price_snapshot(snapshot, history)
    entry_context = _volume_price_entry_context(report, row, technicals)
    snapshot = evaluate_volume_price_acceptance(
        ticker=str(report.get("ticker") or row.get("ticker") or ""),
        daily_bars=history,
        technicals=technicals,
        entry_context=entry_context,
    )
    return snapshot.to_dict()


def _enrich_volume_price_snapshot(snapshot: dict[str, Any], history: pd.DataFrame | None) -> dict[str, Any]:
    enriched = dict(snapshot)
    if _first_number(enriched, "latest_volume", "latestVolume") is not None:
        return enriched
    volume = resolve_volume_snapshot("", {}, history, enriched)
    if volume.get("latest_volume") is not None:
        enriched["latest_volume"] = volume.get("latest_volume")
        enriched["volume_source"] = volume.get("volume_source")
    return enriched


def _volume_price_entry_context(*sources: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in sources:
        if isinstance(source, dict):
            merged.update(source)
    return {
        **merged,
        "current_price": _report_current_price(merged),
        "observation_low": _first_number(
            merged,
            "near_term_repair_zone_low",
            "radar_near_term_repair_zone_low",
            "effective_technical_entry_zone_low",
            "radar_effective_technical_entry_zone_low",
            "technical_pullback_zone_low",
            "radar_technical_pullback_zone_low",
        ),
        "observation_high": _first_number(
            merged,
            "near_term_repair_zone_high",
            "radar_near_term_repair_zone_high",
            "effective_technical_entry_zone_high",
            "radar_effective_technical_entry_zone_high",
            "technical_pullback_zone_high",
            "radar_technical_pullback_zone_high",
        ),
        "support_line": _first_number(merged, "support_watch_zone_low", "radar_support_watch_zone_low", "recent_swing_low", "radar_recent_swing_low"),
        "invalid_line": _first_number(merged, "invalidation_price", "radar_invalidation_price"),
        "confirm_line": _first_number(merged, "confirmation_price", "radar_confirmation_price"),
        "price_position": merged.get("price_position") or merged.get("radar_price_position"),
        "decision": merged.get("decision") or merged.get("radar_decision"),
    }


def _volume_price_status_label(status: str, score: float | None = None) -> str:
    normalized = str(status or "").strip().upper()
    if normalized == "ACCEPTANCE_CONFIRMED":
        return "承接确认"
    if normalized == "FORMING":
        return "初步承接，尚未确认" if score is not None and score < 55 else "承接形成中"
    if normalized == "UNCONFIRMED":
        return "量价未确认"
    if normalized == "FAILED":
        return "承接失败"
    if normalized == "OVEREXTENDED_SUPPORT_READ":
        return "脱离观察区"
    if normalized == "DATA_MISSING":
        return "数据不足"
    return normalized or "数据不足"


def _volume_price_reason_text(status: str, score: float | None, reason: Any) -> str:
    normalized = str(status or "").strip().upper()
    if normalized == "FORMING" and score is not None and score < 55:
        return "初步承接，尚未确认；未放量站上确认线，不构成买入确认。"
    if normalized == "OVEREXTENDED_SUPPORT_READ":
        return "价格已脱离回踩观察区，承接读数不构成低吸依据。"
    if normalized == "FAILED":
        return "放量跌破支撑/失效线，暂停加仓。"
    text = _display_value(reason)
    return text if text != "暂无" else "量价承接用于复核当前结构，不能单独作为买入依据。"


def _zones_card_html(report: dict[str, Any]) -> str:
    items = [
        ("技术回踩带", report.get("buy_zone")),
        ("观察区", report.get("watch_zone")),
        ("追高区", report.get("chase_zone")),
    ]
    body = "".join(f"<div><span>{escape(label)}</span><strong>{escape(_zone_text(zone))}</strong></div>" for label, zone in items)
    return f'<article class="ai-radar-card zones"><h3>技术回踩带 / 观察区 / 追高风险区</h3>{body}</article>'


def _metric_table_card_html(title: str, rows: list[tuple[str, str]]) -> str:
    body = "".join(f"<tr><td>{escape(label)}</td><td>{escape(value)}</td></tr>" for label, value in rows)
    return (
        '<section class="ai-radar-card ai-radar-metric-card">'
        f'<div class="ai-radar-section-title"><span>{escape(title)}</span><b>只读缓存</b></div>'
        f'<table class="ai-radar-metric-table"><tbody>{body}</tbody></table>'
        "</section>"
    )


def _key_metric_rows(
    report: dict[str, Any],
    market: dict[str, Any],
    snapshot: dict[str, Any],
    technicals: dict[str, Any],
    history: pd.DataFrame | None = None,
) -> list[tuple[str, str]]:
    volume = _volume_snapshot(market, snapshot, technicals, history)
    return [
        ("最新价", _money(_report_current_price(report))),
        ("报价来源", _quote_source_text(market, snapshot, report)),
        ("日内涨跌幅", _signed_pct(_first_number(snapshot, technicals, market, "change_pct", "changePercent", "day_change_pct"))),
        ("成交量", _volume_display(volume)),
        ("20日均量", _compact_number(volume.get("volume_ma20"))),
        ("量比", _volume_ratio_display(volume.get("volume_ratio"))),
        ("量比口径", "成交量 / 20日均量"),
        ("成交量来源", _volume_source_label(volume.get("volume_source"))),
        ("成交量时间", _display_value(volume.get("volume_date"))),
        ("52周高低", _range_text(_first_number(snapshot, technicals, "fifty_two_week_low", "yearLow"), _first_number(snapshot, technicals, "fifty_two_week_high", "yearHigh"))),
        ("市值来源", _market_cap_source_text(snapshot, report)),
        ("市盈率 / 远期市盈率", f"{_multiple(_first_number(snapshot, 'pe', 'trailing_pe', 'price_to_earnings'))} / {_multiple(_first_number(snapshot, 'forward_pe', 'forwardPE'))}"),
        ("企业价值 / 销售额", _multiple(_first_number(snapshot, "enterprise_to_revenue", "enterpriseToRevenue", "ev_to_sales"))),
        ("自由现金流收益率", _ratio_pct(_first_number(snapshot, "free_cash_flow_yield", "fcf_yield"))),
        ("毛利率", _ratio_pct(_first_number(snapshot, "gross_margin", "grossMargin"))),
        ("净利率", _ratio_pct(_first_number(snapshot, "net_margin", "profit_margin", "netMargin"))),
        ("净资产收益率", _ratio_pct(_roe_value(snapshot))),
    ]


def _financial_metric_rows(snapshot: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        ("营收", _compact_money(_first_number(snapshot, "total_revenue", "revenue", "revenue_ttm"))),
        ("同比增速", _ratio_pct(_first_number(snapshot, "revenue_growth", "revenueGrowth"))),
        ("毛利率", _ratio_pct(_first_number(snapshot, "gross_margin", "grossMargin"))),
        ("净利率", _ratio_pct(_first_number(snapshot, "net_margin", "profit_margin", "netMargin"))),
        ("经营现金流", _compact_money(_first_number(snapshot, "operating_cash_flow", "operatingCashFlow"))),
        ("自由现金流", _compact_money(_first_number(snapshot, "free_cash_flow", "freeCashFlow"))),
        ("现金及短投", _compact_money(_first_number(snapshot, "total_cash", "cash", "cashAndShortTermInvestments", "cashAndEquivalents"))),
        ("总债务", _compact_money(_first_number(snapshot, "total_debt", "debt", "totalDebt"))),
        ("净资产收益率", _ratio_pct(_roe_value(snapshot))),
    ]


def _roe_value(snapshot: dict[str, Any]) -> float | None:
    explicit = _first_number(snapshot, "roe", "return_on_equity", "returnOnEquity", "returnOnEquityTTM", "roe_ttm")
    if explicit is not None:
        return explicit
    net_income = _first_number(snapshot, "net_income", "netIncome", "net_income_ttm", "netIncomeTTM")
    equity = _first_number(snapshot, "total_equity", "shareholders_equity", "totalStockholdersEquity", "stockholdersEquity")
    if net_income is None or equity in (None, 0):
        return None
    return net_income / equity


def _ai_cloud_infra_card_html(row: dict[str, Any], snapshot: dict[str, Any], report: dict[str, Any]) -> str:
    model_type = _business_model_type(report, row, snapshot)
    if model_type not in AI_INFRA_DISPLAY:
        return ""
    rows = [("业务模型", _business_model_display(model_type))]
    rows.extend(_ai_cloud_infra_metric_rows(row, snapshot, report))
    body = "".join(f"<tr><td>{escape(label)}</td><td>{escape(value)}</td></tr>" for label, value in rows)
    return (
        '<section class="ai-radar-card ai-radar-ai-infra-card">'
        '<div class="ai-radar-section-title"><span>AI 云基础设施专项框架</span><b>只读展示，不纳入本轮评分</b></div>'
        f'<table class="ai-radar-metric-table"><tbody>{body}</tbody></table>'
        "</section>"
    )


def _ai_cloud_infra_metric_rows(row: dict[str, Any], snapshot: dict[str, Any], report: dict[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    sources = (snapshot, report, row)
    for canonical, label, aliases, value_type in AI_INFRA_FIELD_SPECS:
        value = _first_metric_value(sources, aliases)
        rows.append((label, _display_ai_infra_value(value, value_type)))
    return rows


def _ai_cloud_infra_missing_fields(row: dict[str, Any], snapshot: dict[str, Any], report: dict[str, Any]) -> list[str]:
    if _business_model_type(report, row, snapshot) not in AI_INFRA_DISPLAY:
        return []
    sources = (snapshot, report, row)
    return [
        canonical
        for canonical, _label, aliases, _value_type in AI_INFRA_FIELD_SPECS
        if _first_metric_value(sources, aliases) is None
    ]


def _first_metric_value(sources: tuple[dict[str, Any], ...], aliases: tuple[str, ...]) -> Any:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in aliases:
            if key in source and source.get(key) not in (None, ""):
                return source.get(key)
    return None


def _display_ai_infra_value(value: Any, value_type: str) -> str:
    if value in (None, ""):
        return "暂缺"
    if value_type == "money":
        return _compact_money(value)
    if value_type == "ratio":
        return _ratio_pct(value)
    if value_type == "multiple":
        return _multiple(value)
    if value_type == "gw":
        number = _number(value)
        return "暂缺" if number is None else f"{number:.2f} GW"
    return _display_value(value)


def _business_model_type(*sources: dict[str, Any]) -> str:
    candidates: list[Any] = []
    ticker = ""
    for source in sources:
        if not isinstance(source, dict):
            continue
        ticker = ticker or str(source.get("ticker") or source.get("symbol") or "").strip().upper()
        candidates.extend(
            [
                _first_present(source, "business_model_type", "businessModelType", "modelType", "scoring_model", "scoringModel"),
                _first_present(source, "business_model", "businessModel", "model"),
            ]
        )
    for value in candidates:
        normalized = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
        if not normalized:
            continue
        if normalized in AI_INFRA_MODEL_ALIASES:
            return AI_INFRA_MODEL_ALIASES[normalized]
        if "NEOCLOUD" in normalized or "NEO_CLOUD" in normalized:
            return "NEOCLOUD"
        if "GPU" in normalized and "CLOUD" in normalized:
            return "GPU_CLOUD"
        if "AI" in normalized and ("CLOUD" in normalized or "INFRA" in normalized):
            return "AI_INFRA_CLOUD"
    return AI_INFRA_BUSINESS_TYPE_BY_TICKER.get(ticker, "")


def _business_model_display(model_type: str) -> str:
    return AI_INFRA_DISPLAY.get(str(model_type or "").upper(), "暂缺")


def _performance_rows(history: pd.DataFrame) -> list[tuple[str, str]]:
    return [
        ("1日", _signed_pct(_history_return(history, 1))),
        ("5日", _signed_pct(_history_return(history, 5))),
        ("1月", _signed_pct(_history_return(history, 21))),
        ("3月", _signed_pct(_history_return(history, 63))),
        ("年初至今", _signed_pct(_history_ytd_return(history))),
        ("1年", _signed_pct(_history_return(history, 252))),
    ]


def _volume_snapshot(
    market: dict[str, Any],
    snapshot: dict[str, Any],
    technicals: dict[str, Any],
    history: pd.DataFrame | None,
) -> dict[str, Any]:
    quote = {**(technicals or {}), **(snapshot or {}), **(market or {})}
    volume_price_result = (
        quote.get("volumePriceAcceptance")
        or quote.get("volume_price_acceptance")
        or quote.get("volume_price_result")
        or {}
    )
    return resolve_volume_snapshot("", quote, history, volume_price_result)


def resolve_volume_snapshot(
    ticker: str,
    quote: dict[str, Any] | None,
    daily_bars: pd.DataFrame | None,
    volume_price_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(quote or {})
    volume_payload = dict(volume_price_result or {})
    quote_volume = _first_number(payload, "quoteVolume", "quote_volume", "volume", "latest_volume", "latestVolume", "regularMarketVolume")
    daily_volume, daily_date = _latest_daily_volume_info(daily_bars)
    vpa_volume = _first_number(volume_payload, "latest_volume", "latestVolume")
    volume_ma20 = (
        _daily_volume_ma20(daily_bars)
        or _first_number(volume_payload, "volume_ma20", "volumeMa20")
        or _first_number(payload, "volume_ma20", "volumeMa20", "avg_volume", "avgVolume", "averageVolume")
    )

    if quote_volume is not None and quote_volume > 0:
        volume = quote_volume
        source = "quote"
        volume_date = _display_value(payload.get("fetchedAt") or payload.get("updated_at") or payload.get("updatedAt"))
    elif daily_volume is not None and daily_volume > 0:
        volume = daily_volume
        source = "daily_cache"
        volume_date = _display_value(daily_date)
    elif vpa_volume is not None and vpa_volume > 0:
        volume = vpa_volume
        source = "volume_price_acceptance"
        volume_date = _display_value(volume_payload.get("volume_price_checked_at") or volume_payload.get("volumePriceCheckedAt"))
    else:
        volume = None
        source = "unavailable"
        volume_date = "暂无"

    ratio = volume / volume_ma20 if volume is not None and volume_ma20 else None
    return {
        "ticker": ticker,
        "latest_volume": volume,
        "volume_ma20": volume_ma20,
        "volume_ratio": ratio,
        "volume_source": source,
        "volume_date": volume_date,
        "volume_regime_cn": _display_value(volume_payload.get("volume_regime_cn") or volume_payload.get("volumeRegimeCn")),
    }


def _latest_daily_volume(history: pd.DataFrame | None) -> float | None:
    volume, _date = _latest_daily_volume_info(history)
    return volume


def _latest_daily_volume_info(history: pd.DataFrame | None) -> tuple[float | None, Any]:
    if history is None or history.empty or "volume" not in history:
        return None, None
    frame = history.copy()
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame = frame.dropna(subset=["volume"])
    frame = frame[frame["volume"] > 0]
    if frame.empty:
        return None, None
    row = frame.iloc[-1]
    return float(row["volume"]), row.get("date")


def _daily_volume_ma20(history: pd.DataFrame | None) -> float | None:
    if history is None or history.empty or "volume" not in history:
        return None
    volumes = pd.to_numeric(history["volume"], errors="coerce").dropna()
    volumes = volumes[volumes > 0]
    if volumes.empty:
        return None
    window = volumes.tail(20)
    return float(window.mean()) if not window.empty else None


def _volume_display(volume: dict[str, Any]) -> str:
    if volume.get("latest_volume") is None:
        return "暂无成交量数据"
    return _compact_number(volume.get("latest_volume"))


def _volume_ratio_display(value: Any) -> str:
    number = _number(value)
    return "暂无" if number is None else f"{number:.2f}x"


def _volume_source_label(value: Any) -> str:
    return {
        "quote": "报价缓存",
        "daily_cache": "日线缓存",
        "volume_price_acceptance": "量价模块",
        "unavailable": "暂无",
    }.get(str(value or ""), "暂无")


def _quote_source_text(market: dict[str, Any], snapshot: dict[str, Any], row: dict[str, Any]) -> str:
    raw = (
        _first_present(market, "quote_source", "quoteSource", "priceSource", "source")
        or _first_present(snapshot, "quote_source", "quoteSource", "priceSource", "source")
        or _first_present(row, "quote_source", "quoteSource", "priceSource")
    )
    if raw:
        return _source_label(raw)
    if _report_current_price(row) is not None or _first_number(market, "currentPrice", "current_price", "price") is not None:
        return "本地报价缓存"
    return "暂缺"


def _market_cap_source_text(snapshot: dict[str, Any], row: dict[str, Any]) -> str:
    raw = _first_present(snapshot, "market_cap_source", "marketCapSource", "source") or _first_present(row, "market_cap_source", "marketCapSource")
    if raw:
        return _source_label(raw)
    if _first_number(snapshot, "market_cap", "marketCap") is not None or _first_number(row, "market_cap", "marketCap") is not None:
        return "基本面缓存"
    return "暂缺"


def _financial_source_text(snapshot: dict[str, Any], row: dict[str, Any]) -> str:
    raw = (
        _first_present(snapshot, "financial_source", "financialSource", "fundamental_source", "fundamentalSource")
        or _first_present(row, "financial_source", "financialSource", "fundamental_source", "fundamentalSource")
    )
    if raw:
        return _source_label(raw)
    financial_keys = ("total_revenue", "revenue", "gross_margin", "operating_cash_flow", "free_cash_flow", "net_debt")
    if any(_first_present(snapshot, key) is not None for key in financial_keys):
        return "基本面缓存"
    return "暂缺"


def _financial_period_text(snapshot: dict[str, Any], row: dict[str, Any]) -> str:
    return _display_value(
        _first_present(snapshot, "financial_period", "financialPeriod", "fiscal_period", "fiscalPeriod", "latest_quarter", "latestQuarter", "period", "date", "filing_date", "fillingDate")
        or _first_present(row, "financial_period", "financialPeriod", "fiscal_period", "fiscalPeriod", "latest_quarter", "latestQuarter", "period", "date", "filing_date", "fillingDate")
    )


def _price_type_text(market: dict[str, Any], snapshot: dict[str, Any]) -> str:
    raw = str(
        _first_present(market, "price_is_close_or_intraday", "priceIsCloseOrIntraday", "price_type", "priceType")
        or _first_present(snapshot, "price_is_close_or_intraday", "priceIsCloseOrIntraday", "price_type", "priceType")
        or ""
    ).strip().lower()
    if raw:
        if "intraday" in raw or "盘中" in raw:
            return "盘中 / 报价快照"
        if "close" in raw or "收盘" in raw:
            return "收盘价 / 日线缓存"
        return _localize_report_text(raw)
    source = str(_first_present(market, "priceSource", "source") or "").lower()
    if "history" in source or "daily" in source or "close" in source:
        return "收盘价 / 日线缓存"
    if _first_number(market, "currentPrice", "current_price", "price") is not None:
        return "盘中 / 报价快照"
    return "暂缺"


def _source_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "暂缺"
    normalized = text.lower()
    mapping = {
        "quote": "报价缓存",
        "quote_snapshot": "报价缓存",
        "daily_cache": "日线缓存",
        "price_history": "日线缓存",
        "fmp": "FMP",
        "fmp_cache": "FMP 缓存",
        "fundamental_cache": "基本面缓存",
        "snapshot": "本地快照",
        "manual": "人工录入",
    }
    return mapping.get(normalized, _localize_report_text(text))


def _catalyst_card_html(row: dict[str, Any], snapshot: dict[str, Any], report: dict[str, Any]) -> str:
    items, has_news_cache = _catalyst_items(row, snapshot, report)
    if has_news_cache:
        return _text_card_html("近期新闻 / 催化", items, subtitle="本地新闻缓存", limit=6)
    return _text_card_html("后续催化 / 风险事项", items, subtitle="待跟踪事项", limit=6)


def _catalyst_items(row: dict[str, Any], snapshot: dict[str, Any], report: dict[str, Any]) -> tuple[list[str], bool]:
    candidates: list[Any] = []
    for source in (row, snapshot, report):
        for key in ("recent_news", "recentNews", "news", "catalysts", "keyCatalysts", "events"):
            candidates.extend(_list_value(source, key))
    cleaned = [_format_news_or_event_item(item) for item in candidates]
    cleaned = [item for item in _dedupe_text(cleaned) if item and item.lower() not in {"n/a", "none"}]
    event_items = _recent_event_items(row, snapshot, report)
    if cleaned:
        return _dedupe_text([*event_items, *cleaned])[:6], True
    return _dedupe_text([*event_items, *_fallback_catalyst_items(report, row)])[:6], False


def _recent_event_items(row: dict[str, Any], snapshot: dict[str, Any], report: dict[str, Any]) -> list[str]:
    items: list[str] = []
    for source in (row, snapshot, report):
        items.extend(_list_value(source, "index_events"))
        items.extend(_list_value(source, "indexEvents"))
        items.extend(_list_value(source, "financing_events"))
        items.extend(_list_value(source, "financingEvents"))
        items.extend(_list_value(source, "major_customer_contracts"))
        items.extend(_list_value(source, "majorCustomerContracts"))
    next_earnings = _display_value(
        _first_present(row, "next_earnings_date", "nextEarningsDate")
        or _first_present(snapshot, "next_earnings_date", "nextEarningsDate")
        or _first_present(report, "next_earnings_date", "nextEarningsDate")
    )
    if next_earnings != "暂无":
        items.append(f"财报事件：下次财报日期 {next_earnings}。")
    ticker = str(report.get("ticker") or row.get("ticker") or snapshot.get("ticker") or "").strip().upper()
    if ticker == "CRWV":
        items.extend(
            [
                "指数事件：纳入 Nasdaq-100 可带来短期量能和被动资金催化，但不代表基本面自动改善。",
                "融资事件：Senior Notes 融资补充资金，但提高利息负担和杠杆压力。",
                "大客户合同：重点跟踪 Meta / OpenAI / Anthropic / Microsoft 等合同的收入确认、交付和集中度风险。",
            ]
        )
        if next_earnings == "暂无":
            items.append("财报事件：下次财报日期暂缺。")
    return [_localize_report_text(str(item).strip()) for item in _dedupe_text(items) if str(item).strip()]


def _format_news_or_event_item(item: Any) -> str:
    if isinstance(item, dict):
        date = _display_value(item.get("date") or item.get("published_at") or item.get("publishedAt"))
        source = _display_value(item.get("source") or item.get("publisher"))
        event = _display_value(item.get("title") or item.get("event") or item.get("headline") or item.get("summary"))
        impact = _display_value(item.get("impact_direction") or item.get("impactDirection") or item.get("impact") or "待判断")
        meaning = _display_value(item.get("trading_meaning") or item.get("tradingMeaning") or item.get("note") or "先观察是否改变收入、利润率或关键技术位。")
        return f"{date}｜{source}｜事件：{event}｜影响方向：{impact}｜交易含义：{meaning}"
    text = _localize_report_text(str(item or "").strip())
    return f"日期：暂无｜来源：本地缓存｜事件：{text}｜影响方向：待判断｜交易含义：先观察是否改变收入、利润率或关键技术位。" if text else ""


def _fallback_catalyst_items(report: dict[str, Any], row: dict[str, Any]) -> list[str]:
    confirm = _money(_first_number(report, row, "confirmation_price", "radar_confirmation_price"))
    invalid = _money(_first_number(report, row, "invalidation_price", "radar_invalidation_price"))
    next_earnings = _display_value(_first_present(row, "next_earnings_date", "nextEarningsDate") or _first_present(report, "next_earnings_date", "nextEarningsDate"))
    return [
        f"财报 / 指引：下一财报 {next_earnings}，重点看收入、利润率和现金流指引。",
        f"重新评估线：观察是否放量站上确认线 {confirm}，触发后重新评估，不等于直接买入。",
        f"风险失效：若放量跌破失效线 {invalid}，转入破位复核。",
    ]


def _watch_points_table_html(report: dict[str, Any], row: dict[str, Any]) -> str:
    volume = _dict_value(row, "volumePriceAcceptance") or _dict_value(report, "volumePriceAcceptance") or {}
    volume_status = _volume_price_status_label(
        str(volume.get("volume_price_status") or volume.get("volumePriceStatus") or "DATA_MISSING"),
        _number(volume.get("volume_price_score") or volume.get("volumePriceScore")),
    )
    volume_ratio = _volume_ratio_display(_first_number(volume, "volume_ratio", "volumeRatio"))
    zone = _current_zone_label(report)
    confirm = _money(_first_number(report, row, "confirmation_price", "radar_confirmation_price"))
    invalid = _money(_first_number(report, row, "invalidation_price", "radar_invalidation_price"))
    forward_pe = _multiple(_first_number(report, row, "forward_pe", "forwardPE"))
    rows = [
        ("量价承接", f"{volume_status}｜量比 {volume_ratio}", f"重新评估线：放量站上确认线 {confirm}", "触发后重新评估，不等于直接买入"),
        ("趋势修复", zone, f"重新评估线：收盘重新站回关键均线 / 确认线 {confirm}", "确认修复后再提高复核优先级"),
        ("估值位置", f"远期市盈率 {forward_pe}", "进入估值参考区但未追高", "估值只代表可研究，不代表自动买入"),
        ("风险控制", f"失效线 {invalid}", "放量跌破支撑或失效线", "暂停加仓，进入破位复核"),
    ]
    body = "".join(
        "<tr>"
        f"<td>{escape(item)}</td>"
        f"<td>{escape(current)}</td>"
        f"<td>{escape(trigger)}</td>"
        f"<td>{escape(meaning)}</td>"
        "</tr>"
        for item, current, trigger, meaning in rows
    )
    return (
        '<section class="ai-radar-card ai-radar-monitor-card">'
        '<div class="ai-radar-section-title"><span>关键监控点</span><b>当前状态 / 触发 / 含义</b></div>'
        '<table class="ai-radar-monitor-table">'
        '<thead><tr><th>监控项</th><th>当前状态</th><th>触发条件</th><th>交易含义</th></tr></thead>'
        f"<tbody>{body}</tbody></table>"
        "</section>"
    )


def _research_watch_points(report: dict[str, Any], row: dict[str, Any]) -> list[str]:
    volume = _dict_value(row, "volumePriceAcceptance") or _dict_value(report, "volumePriceAcceptance") or {}
    revenue_growth = _ratio_pct(_first_number(report, row, "revenue_growth", "revenueGrowth"))
    gross_margin = _ratio_pct(_first_number(report, row, "gross_margin", "grossMargin"))
    net_margin = _ratio_pct(_first_number(report, row, "net_margin", "profit_margin", "netMargin"))
    forward_pe = _multiple(_first_number(report, row, "forward_pe", "forwardPE"))
    ev_sales = _multiple(_first_number(report, row, "enterprise_to_revenue", "enterpriseToRevenue", "ev_to_sales"))
    zone = _current_zone_label(report)
    volume_ratio = _volume_ratio_display(_first_number(volume, "volume_ratio", "volumeRatio"))
    volume_status = _volume_price_status_label(
        str(volume.get("volume_price_status") or volume.get("volumePriceStatus") or "DATA_MISSING"),
        _number(volume.get("volume_price_score") or volume.get("volumePriceScore")),
    )
    confirm = _money(_first_number(report, row, "confirmation_price", "radar_confirmation_price"))
    invalid = _money(_first_number(report, row, "invalidation_price", "radar_invalidation_price"))
    return [
        f"增长质量：当前读数：收入同比 {revenue_growth}。为什么重要：收入增速决定成长股估值支撑。触发条件：若连续两个季度低于系统阈值或指引下修，降低成长复核优先级。交易含义：增长未坏前，回调更多看作估值或技术修复。",
        f"利润率稳定性：当前读数：毛利率 {gross_margin}，净利率 {net_margin}。为什么重要：利润率决定现金流质量。触发条件：若利润率连续下滑或低于同业，复核盈利质量。交易含义：利润率稳定时优先等待价格和量价确认，不因短线波动直接否定。",
        f"估值压力：当前读数：远期市盈率 {forward_pe}，EV/Sales {ev_sales}。为什么重要：估值决定安全垫。触发条件：若进入追高区或历史高估区，不追价。交易含义：估值进入参考区也只代表可复核，不等于自动买入。",
        f"技术承接：当前读数：位于 {zone}，量比 {volume_ratio}，量价状态 {volume_status}。为什么重要：技术承接决定回踩是否成立。触发条件：未放量站上确认线 {confirm} 前，不构成买入确认。交易含义：先看量价读数，再考虑分批。",
        f"失效条件：当前读数：失效线 {invalid}。为什么重要：失效线用于区分修复和破位。触发条件：若放量跌破支撑或失效线，不建议加仓，进入破位复核。交易含义：失效后不做无确认摊低。",
    ]


def _data_completeness_html(report: dict[str, Any], confidence: str, volume: dict[str, Any] | None = None) -> str:
    missing = _missing_group_text(report)
    if volume and volume.get("volume_source") == "unavailable":
        missing = "、".join(_dedupe_text([*(missing.split("、") if missing else []), "成交量缺失"]))
    missing = missing or "暂无关键缺口"
    impact = _data_impact_sentence(report, confidence)
    detail = _data_missing_detail_text(report, volume)
    return (
        '<section class="ai-radar-card ai-radar-data-quality">'
        '<div class="ai-radar-section-title"><span>数据完整度</span><b>缺数据不压倒主结论</b></div>'
        '<div class="ai-radar-data-quality-grid">'
        f"<div><span>完整度</span><strong>{escape(confidence)}</strong></div>"
        f"<div><span>缺失项</span><strong>{escape(missing)}</strong></div>"
        f"<div><span>对结论影响</span><strong>{escape(impact)}</strong></div>"
        f"<div><span>字段明细</span><strong>{escape(detail)}</strong></div>"
        "</div>"
        "</section>"
    )


def _text_card_html(title: str, items: list[Any], *, subtitle: str = "研究依据", limit: int = 6) -> str:
    cleaned = [_localize_report_text(str(item).strip()) for item in items if str(item).strip()]
    if not cleaned:
        cleaned = ["暂无明确内容，先保持复查。"]
    body = "".join(f"<li>{escape(item)}</li>" for item in cleaned[:limit])
    subtitle_html = f"<b>{escape(subtitle)}</b>" if subtitle else ""
    return f'<section class="ai-radar-card"><div class="ai-radar-section-title"><span>{escape(title)}</span>{subtitle_html}</div><ul>{body}</ul></section>'


def _inline_list(value: Any) -> str:
    if not value:
        return "无"
    if isinstance(value, (list, tuple, set)):
        cleaned = [_localize_report_text(str(item).strip()) for item in value if str(item).strip()]
        return "、".join(cleaned[:8]) if cleaned else "无"
    return _localize_report_text(str(value))


def _core_status(row: dict[str, Any]) -> str:
    display = _dict_value(row, "buy_zone_display") or _dict_value(row, "buyZoneDisplay")
    if display:
        display_action = str(
            display.get("buy_zone_action")
            or display.get("action_code")
            or display.get("current_action")
            or display.get("entry_context_status")
            or ""
        ).strip().upper()
        if display_action in {"DATA_INSUFFICIENT", "DATA_MISSING"}:
            return "数据不足"
        if display_action in {"NO_BUY_ZONE", "ZONE_MISSING"}:
            return "未生成买区"
        if display_action in {"ALLOW_SMALL_BUY", "ALLOW_ADD_ON_PULLBACK"}:
            return "可买"
        if display_action in {"WAIT_PULLBACK", "WAIT_CONFIRMATION"}:
            return "等待"
        if display_action == "BLOCK_CHASE":
            return "防追高"
        if display_action == "RISK_REVIEW":
            return "风控复核"
        if display_action == "PAUSE_BUY":
            return "风控复核"
        if display_action == "AVOID":
            return "回避"
        label = str(display.get("badge_label") or display.get("technical_action_text") or "").strip()
        if label in {"不给买区", "技术承接数据不足"}:
            return "数据不足"
        if label in {"暂不生成", "区间待补"}:
            return "未生成买区"
        return label or "观察"
    context_status = _buy_zone_context_core_status(_dict_value(row, "buy_zone_context") or _dict_value(row, "buyZoneContext"))
    if context_status:
        return context_status
    entry_label = str(row.get("entry_display_label") or "").strip()
    interpretation = str(row.get("primary_entry_interpretation") or row.get("zone_semantic_label") or "").strip()
    price_position = str(row.get("price_position") or "")
    decision = str(row.get("decision") or "")
    structure = str(row.get("technical_structure_status") or "")
    combined = f"{entry_label} {interpretation}"
    if decision in {"DATA_MISSING", "DATA_INSUFFICIENT"}:
        return "数据不足"
    if decision in {"NO_BUY_ZONE", "ZONE_MISSING"}:
        return "未生成买区"
    if price_position == "IN_CHASE_ZONE" or decision == "BLOCK_CHASE":
        return "防追高"
    if "价值复核" in combined or "估值吸引" in combined:
        return "价值复核"
    if "近端复核" in combined or "近端修复" in combined:
        return "近端复核"
    if price_position == "IN_BUY_ZONE":
        return "可买"
    if structure == "BREAKDOWN_REVIEW":
        return "风控复核"
    if structure == "WEAK_TREND_REPAIR":
        return "等待"
    if price_position in {"ABOVE_BUY_ZONE", "WAIT"}:
        return "观察"
    if decision == "AVOID":
        return "回避"
    return "观察"


def _buy_zone_context_core_status(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    action = str(context.get("current_action") or context.get("currentAction") or "").strip().upper()
    if action in {"DATA_INSUFFICIENT", "DATA_MISSING"}:
        return "数据不足"
    if action in {"NO_BUY_ZONE", "ZONE_MISSING"}:
        return "未生成买区"
    if action == "BLOCK_CHASE":
        return "防追高"
    if action == "RISK_REVIEW":
        return "风控复核"
    if action == "PAUSE_BUY":
        return "风控复核"
    if action in {"ALLOW_SMALL_BUY", "ALLOW_ADD_ON_PULLBACK"}:
        return "可买"
    if action == "WAIT_CONFIRMATION":
        return "等待"
    if action == "WAIT_PULLBACK":
        return "观察"
    if action == "AVOID":
        return "回避"
    return ""


def _buy_point_reason_text(row: dict[str, Any]) -> str:
    context = _dict_value(row, "buy_zone_context") or _dict_value(row, "buyZoneContext")
    action = str((context or {}).get("current_action") or (context or {}).get("currentAction") or "").strip().upper()
    if action in {"DATA_INSUFFICIENT", "DATA_MISSING"}:
        missing = _buy_zone_context_missing_fields(context or {}) or _actionable_missing_fields(row)
        return f"数据不足：{_field_list_display(missing, row) if missing else '技术承接数据'}"
    if action in {"NO_BUY_ZONE", "ZONE_MISSING"}:
        missing = _buy_zone_context_missing_fields(context or {}) or ["support_zone", "resistance_zone"]
        return f"未生成买区：{_field_list_display(missing, row)}"

    display = _dict_value(row, "buy_zone_display") or _dict_value(row, "buyZoneDisplay")
    if display:
        acceptance_text = str(display.get("acceptance_state_text") or display.get("acceptance_badge_text") or "").strip()
        action_text = str(display.get("main_action_text") or display.get("display_action_text") or "").strip()
        if acceptance_text and action_text:
            return f"{acceptance_text} / {action_text}"
        reason = str(
            display.get("entry_display_reason")
            or display.get("badge_hint")
            or display.get("technical_reason")
            or display.get("technical_action_text")
            or ""
        ).strip()
        if reason:
            return reason

    reason = str(row.get("entry_display_reason") or row.get("entry_action_hint") or "").strip()
    if reason:
        return reason
    if _legacy_buy_zone_context_missing(row):
        return "旧记录缺少买区上下文，需刷新 / 重建"
    return _report_status_text(row)


def _legacy_buy_zone_context_missing(row: dict[str, Any]) -> bool:
    if _dict_value(row, "buy_zone_context") or _dict_value(row, "buyZoneContext"):
        return False
    if _dict_value(row, "buy_zone_display") or _dict_value(row, "buyZoneDisplay"):
        return False
    return bool(row.get("entry_display_label") or row.get("primary_entry_interpretation") or row.get("zone_semantic_label"))


def _radar_status_tone(status: str) -> str:
    if status in {"数据不足", "未生成买区"}:
        return "missing"
    if status in {"可买", "接近买区"}:
        return "allow"
    if status in {"等待", "观察", "等待确认", "等待回落", "价值复核", "近端复核"}:
        return "wait"
    if status in {"防追高", "低优先级"}:
        return "blocked"
    if status == "风控复核":
        return "risk"
    if status in {"回避", "暂不研究"}:
        return "avoid"
    return "wait"


def _company_track_html(row: dict[str, Any]) -> str:
    ticker = str(row.get("ticker") or "").strip().upper()
    company = str(row.get("company_name") or "").strip()
    if company.upper() == ticker:
        company = ""
    track = _clean_text(row.get("sector"))
    company_display, track_display = format_company_track(company, track, None, ticker)
    return (
        '<div class="ai-radar-company-cell">'
        f"<strong>{escape(company_display)}</strong>"
        f"<span>{escape(track_display)}</span>"
        "</div>"
    )


def _report_status_text(row: dict[str, Any]) -> str:
    price_state = _price_data_state(row)
    if price_state == "missing":
        return "需补数据"
    if price_state == "stale" or bool(row.get("is_stale")):
        return "研报过期"
    if _data_confidence(row) in {"高", "中"}:
        return "已生成"
    return "需补数据"


def _data_confidence_html(row: dict[str, Any]) -> str:
    confidence = _data_confidence(row)
    groups = _missing_groups(row)
    optional_groups = _optional_missing_groups(row)
    summary = _missing_group_summary(groups) or _optional_group_summary(optional_groups)
    detail_items = [*groups, *optional_groups]
    detail = "、".join(detail_items) if detail_items else "关键数据完整"
    text = confidence if not summary else f"{confidence}｜{summary}"
    return (
        f'<span class="ai-radar-data-confidence {escape(confidence)}" title="{escape(detail, quote=True)}">'
        f"{escape(text)}</span>"
    )


def _missing_group_summary(groups: list[str]) -> str:
    if not groups:
        return ""
    if len(groups) == 1:
        return groups[0]
    return f"{len(groups)}项缺口"


def _data_confidence(row: dict[str, Any]) -> str:
    missing = _missing_groups(row)
    data_status = str(row.get("data_status") or "")
    price_state = _price_data_state(row)
    if price_state == "missing":
        return "不足"
    if data_status == "OK" and not bool(row.get("is_stale")) and not missing:
        return "高"
    if price_state == "stale" or data_status == "STALE":
        return "低"
    if data_status == "MISSING_SCORE" or len(missing) >= 3:
        return "低"
    return "中"


def _missing_groups(row: dict[str, Any]) -> list[str]:
    fields = _actionable_missing_fields(row)
    status = str(row.get("data_status") or "")
    if status and status != "OK":
        fields.append(status)
    text = " ".join(str(item).lower() for item in fields)
    price_state = _price_data_state(row, text)
    groups: list[str] = []
    if any(_is_volume_acceptance_gap_field(field) for field in fields):
        groups.append("量价缺口")
    if any(token in text for token in ("valuation", "forward_pe", "enterprise_to_revenue", "free_cash_flow_yield", "fcf")):
        groups.append("估值缺口")
    if any(
        token in text
        for token in (
            "technical",
            "ema",
            "atr",
            "swing",
            "history",
            "price_history",
            "buy_zone",
            "ohlcv",
            "ma20",
            "ma50",
            "ma200",
            "support",
            "resistance",
        )
    ):
        groups.append("技术缺口")
    if any(token in text for token in ("disclosure", "filing", "kpi", "sec")):
        groups.append("披露缺口")
    if _profile_missing(row):
        groups.append("资料缺口")
    if price_state == "stale":
        groups.append("价格过期")
    elif price_state == "missing":
        groups.append("价格缺失")
    if any(token in text for token in ("score", "quality", "growth", "risk")):
        groups.append("评分缺口")
    return _dedupe_text(groups)


def _is_volume_acceptance_gap_field(field: str) -> bool:
    text = str(field or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text in {
        "volume_acceptance",
        "volume_price_acceptance",
        "volume_price_status",
        "volume_price_score",
        "volume_ratio",
        "volumeratio",
    }


def _is_technical_gap_field(field: str) -> bool:
    text = str(field or "").strip().lower().replace("-", "_").replace(" ", "_")
    if _is_volume_acceptance_gap_field(text):
        return False
    return any(
        token in text
        for token in (
            "technical",
            "ema",
            "atr",
            "swing",
            "history",
            "price_history",
            "buy_zone",
            "ohlcv",
            "ma20",
            "ma50",
            "ma200",
            "support",
            "resistance",
        )
    )


def _actionable_missing_fields(row: dict[str, Any]) -> list[str]:
    return [
        str(field)
        for field in _all_missing_fields(row)
        if not _is_optional_gap_field(str(field))
    ]


def _all_missing_fields(row: dict[str, Any]) -> list[str]:
    debug = row.get("debug") if isinstance(row.get("debug"), dict) else {}
    fields = []
    fields.extend(debug.get("data_missing_fields") or [])
    fields.extend(row.get("data_missing_fields") or [])
    fields.extend(row.get("missing_entry_fields") or [])
    fields.extend(row.get("technical_entry_missing_fields") or [])
    fields.extend(row.get("technical_missing_fields") or [])
    context = _dict_value(row, "buy_zone_context") or _dict_value(row, "buyZoneContext")
    if context:
        fields.extend(_buy_zone_context_missing_fields(context))
    return [str(field) for field in fields if str(field).strip()]


def _optional_missing_groups(row: dict[str, Any]) -> list[str]:
    fields = [field for field in _all_missing_fields(row) if _is_optional_gap_field(field)]
    groups: list[str] = []
    text = " ".join(field.lower() for field in fields)
    if "vwap" in text:
        groups.append("可选：已用日线替代 VWAP")
    if any(token in text for token in ("relative_strength", "relative strength", "rs_vs", "spy", "qqq", "benchmark")):
        groups.append("可选：相对强弱缺失")
    return _dedupe_text(groups)


def _optional_group_summary(groups: list[str]) -> str:
    if not groups:
        return ""
    return "可选项缺失"


def _is_optional_gap_field(field: str) -> bool:
    text = str(field or "").strip().lower()
    if not text:
        return False
    return any(
        token in text
        for token in (
            "vwap",
            "relative_strength",
            "relative strength",
            "rs_vs",
            "rs_",
            "spy_relative",
            "qqq_relative",
            "benchmark",
            "news_cache",
            "news cache",
            "recent_news",
        )
    )


def _profile_missing(row: dict[str, Any]) -> bool:
    profile = _resolved_profile(row)
    return bool(not profile["company"] or not profile["track"])


def _resolved_profile(row: dict[str, Any]) -> dict[str, str]:
    ticker = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
    snapshot = _dict_value(row, "rawSnapshot") or {}
    company = str(
        _first_present(snapshot, "companyName", "company_name", "name", "company")
        or row.get("company_name")
        or row.get("companyName")
        or row.get("name")
        or row.get("company")
        or ""
    ).strip()
    if company.upper() == ticker:
        company = ""
    track = _sector_track_from_sources(row, snapshot, ticker)
    if track == "赛道待补":
        track = ""
    return {"company": company, "track": track}


def _price_data_state(row: dict[str, Any], field_text: str | None = None) -> str:
    status = str(row.get("data_status") or "").upper()
    text = field_text
    if text is None:
        debug = row.get("debug") if isinstance(row.get("debug"), dict) else {}
        fields = []
        fields.extend(debug.get("data_missing_fields") or [])
        fields.extend(row.get("data_missing_fields") or [])
        text = " ".join(str(item).lower() for item in fields)
    price = _number(row.get("current_price"))
    if bool(row.get("is_stale")) or status == "STALE" or "current_price_stale" in text or "price_stale" in text:
        return "stale"
    if price is None and (status == "MISSING_PRICE" or "current_price" in text or "price" in text or "quote" in text):
        return "missing"
    return "ok"


def _missing_group_text(row: dict[str, Any]) -> str:
    groups = _missing_groups(row)
    return "、".join(groups[:4])


def _data_impact_sentence(report: dict[str, Any], confidence: str) -> str:
    if confidence == "高":
        return "关键价格、评分和区间数据较完整，可直接阅读结论。"
    if confidence == "中":
        return "结论可读，但估值或技术细节需要结合 Drawer / 缓存继续复核。"
    return "只做方向性研究入口，需先补价格、评分或技术缓存。"


def _data_health_context(
    report: dict[str, Any],
    market: dict[str, Any],
    snapshot: dict[str, Any],
    row: dict[str, Any],
    portfolio_context: dict[str, Any],
    buy_zone_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    buy_zone_context = buy_zone_context or {}
    raw_missing_fields = _dedupe_text(
        [
            *_all_missing_fields(report),
            *_all_missing_fields(row),
            *_buy_zone_context_missing_fields(buy_zone_context),
            *_technical_health_missing_fields(report, row, buy_zone_context),
            *_ai_cloud_infra_missing_fields(row, snapshot, report),
        ]
    )
    not_applicable_fields = _not_applicable_health_fields(raw_missing_fields, portfolio_context)
    missing_fields = _filter_resolved_health_missing_fields(raw_missing_fields, report, market, row, portfolio_context, buy_zone_context)
    critical_missing_fields = _dedupe_text([field for field in missing_fields if _is_critical_health_field(field)])
    optional_missing_fields = _dedupe_text([field for field in missing_fields if field not in critical_missing_fields])
    stale_fields: list[str] = []
    if _price_data_state(report) == "stale" or bool(report.get("is_stale")):
        stale_fields.append("当前价格")
    if str(report.get("data_status") or "").upper() == "STALE":
        stale_fields.append("评分 / 区间")
    health_level = "高"
    if critical_missing_fields:
        health_level = "低"
    elif optional_missing_fields or stale_fields:
        health_level = "中"
    return {
        "health_level": health_level,
        "quote_updated_at": _first_present(market, "fetchedAt", "updated_at", "updatedAt")
        or _first_present(snapshot, "quote_updated_at", "price_updated_at", "fetched_at", "updated_at"),
        "quote_source": _quote_source_text(market, snapshot, report),
        "price_is_close_or_intraday": _price_type_text(market, snapshot),
        "market_cap_source": _market_cap_source_text(snapshot, report),
        "volume_avg_period": "20日均量",
        "volume_ratio_formula": "成交量 / 20日均量",
        "financial_period": _financial_period_text(snapshot, report),
        "financial_source": _financial_source_text(snapshot, report),
        "financials_updated_at": _first_present(snapshot, "financials_updated_at", "financial_statement_updated_at", "fundamental_updated_at", "updated_at", "fetched_at"),
        "score_updated_at": _first_present(report, "score_updated_at", "data_updated_at", "updated_at"),
        "portfolio_updated_at": portfolio_context.get("portfolio_updated_at"),
        "critical_missing_fields": critical_missing_fields,
        "optional_missing_fields": optional_missing_fields,
        "not_applicable_fields": not_applicable_fields,
        "missing_fields": missing_fields,
        "stale_fields": _dedupe_text(stale_fields),
        "primary_sources": _dedupe_text(
            [
                _quote_source_text(market, snapshot, report),
                _market_cap_source_text(snapshot, report),
                _financial_source_text(snapshot, report),
            ]
        ),
    }


def _filter_resolved_health_missing_fields(
    fields: list[str],
    report: dict[str, Any],
    market: dict[str, Any],
    row: dict[str, Any],
    portfolio_context: dict[str, Any],
    buy_zone_context: dict[str, Any] | None = None,
) -> list[str]:
    buy_zone_context = buy_zone_context or {}
    price_resolved = (
        _report_current_price(report) is not None
        or _report_current_price(row) is not None
        or _first_number(market, "currentPrice", "current_price", "price") is not None
    )
    score_resolved = _number(_first_present(report, "final_score", "finalScore", "total_score", "totalScore")) is not None
    volume_resolved = (
        _first_number(buy_zone_context, report, row, "volume_ratio", "volumeRatio") is not None
        or _first_number(buy_zone_context, report, row, "latest_volume", "volume") is not None
    )
    buy_zone_resolved = (
        _first_number(buy_zone_context, report, row, "pullback_zone_low", "effective_technical_entry_zone_low", "support_zone_low", "deep_support_zone_low") is not None
        and _first_number(buy_zone_context, report, row, "pullback_zone_high", "effective_technical_entry_zone_high", "support_zone_high", "deep_support_zone_high") is not None
    )
    filtered: list[str] = []
    for field in fields:
        text = str(field or "").strip().lower()
        label = _field_display_label(field)
        if price_resolved and (
            "current_price" in text
            or "price_missing" in text
            or "缺当前价格" in str(field)
            or label in {"当前价格", "最新价"}
        ):
            continue
        if score_resolved and (text in {"score", "final_score", "total_score", "radar_score"} or "评分" in label):
            continue
        if volume_resolved and text in {"volume_ratio", "volumeratio", "volume_acceptance", "volume_price_acceptance"}:
            continue
        if buy_zone_resolved and ("buy_zone" in text or "buyzone" in text):
            continue
        if any(
            token in text
            for token in (
                "portfolio",
                "position",
                "shares",
                "quantity",
                "avg_cost",
                "average_cost",
                "market_value",
                "unrealized",
            )
        ):
            continue
        filtered.append(str(field))
    return _dedupe_text(filtered)


def _buy_zone_context_missing_fields(buy_zone_context: dict[str, Any]) -> list[str]:
    fields = [str(item) for item in (buy_zone_context.get("missing_fields") or []) if str(item).strip()]
    if str(buy_zone_context.get("current_action") or "").upper() == "DATA_INSUFFICIENT" and not fields:
        fields.append("technical_acceptance")
    return fields


def _technical_health_missing_fields(
    report: dict[str, Any],
    row: dict[str, Any],
    buy_zone_context: dict[str, Any],
) -> list[str]:
    sources = (buy_zone_context, report, row)
    checks = {
        "daily_ohlcv": _has_daily_ohlcv(report) or _has_daily_ohlcv(row),
        "ma20": _first_number(*sources, "ma20", "ema20") is not None,
        "ma50": _first_number(*sources, "ma50", "ema50") is not None,
        "ma200": _first_number(*sources, "ma200", "ema200") is not None,
        "avg_volume_20d": _first_number(*sources, "avg_volume_20d", "volume_ma20") is not None,
        "volume_ratio": _first_number(*sources, "volume_ratio", "volumeRatio") is not None,
        "atr_14": _first_number(*sources, "atr_14", "atr14") is not None,
        "rsi_14": _first_number(*sources, "rsi_14", "rsi14") is not None,
        "swing_high": _first_number(*sources, "swing_high", "recent_swing_high", "recent_breakout_level") is not None,
        "swing_low": _first_number(*sources, "swing_low", "recent_swing_low") is not None,
        "support_zone": _first_number(*sources, "support_zone_low", "deep_support_zone_low", "support_watch_zone_low") is not None,
        "resistance_zone": _first_number(*sources, "resistance_zone_low", "resistance_zone_high", "recent_swing_high", "recent_breakout_level", "confirmation_price") is not None,
        "distance_to_invalidation": _first_number(*sources, "distance_to_invalidation") is not None
        or (_report_current_price(report) is not None and _first_number(report, row, "invalidation_price") is not None),
        "distance_to_resistance": _first_number(*sources, "distance_to_resistance") is not None,
        "reward_risk_ratio": _first_number(*sources, "reward_risk_ratio") is not None,
    }
    return [field for field, available in checks.items() if not available]


def _has_daily_ohlcv(row: dict[str, Any]) -> bool:
    daily = row.get("daily_ohlcv")
    if isinstance(daily, dict):
        return _first_number(daily, "close") is not None and _first_number(daily, "volume") is not None
    return _first_number(row, "close", "current_price", "currentPrice", "price") is not None and _first_number(row, "volume", "latest_volume") is not None


def _not_applicable_health_fields(fields: list[str], portfolio_context: dict[str, Any]) -> list[str]:
    if bool(portfolio_context.get("has_position")):
        return []
    result: list[str] = []
    for field in fields:
        text = str(field or "").strip().lower()
        if any(token in text for token in ("portfolio", "position", "shares", "quantity", "avg_cost", "average_cost", "market_value", "unrealized")):
            result.append(field)
    result.extend(["shares", "avg_cost", "portfolio_updated_at"])
    return _dedupe_text(result)


def _is_critical_health_field(field: str) -> bool:
    text = str(field or "").strip().lower()
    label = _field_display_label(field)
    critical_tokens = (
        "daily_bars",
        "daily_ohlcv",
        "price_history",
        "history",
        "volume",
        "avg_volume",
        "volume_ratio",
        "ma20",
        "ma50",
        "ma200",
        "ema20",
        "ema50",
        "ema200",
        "atr",
        "rsi",
        "swing",
        "support",
        "resistance",
        "invalidation",
        "reward_risk",
        "technical_acceptance",
    )
    return any(token in text for token in critical_tokens) or label in {"历史K线", "成交量", "技术数据", "EMA20", "EMA50", "EMA200", "ATR14"}


def _data_health_card_html(data_health: dict[str, Any]) -> str:
    summary_rows = [
        ("数据健康等级", _display_value(data_health.get("health_level") or "高")),
        ("关键缺口", _field_list_display(data_health.get("critical_missing_fields"))),
        ("可选缺口", _field_list_display(data_health.get("optional_missing_fields"))),
        ("过期字段", _field_list_display(data_health.get("stale_fields"))),
        ("主要数据来源", "、".join(str(item) for item in data_health.get("primary_sources") or []) or "暂缺"),
        ("更新时间", _health_time(data_health.get("quote_updated_at") or data_health.get("score_updated_at"))),
    ]
    detail_rows = [
        ("quote_source", "报价来源", _display_value(data_health.get("quote_source"))),
        ("quote_updated_at", "报价更新时间", _health_time(data_health.get("quote_updated_at"))),
        ("price_is_close_or_intraday", "价格口径", _display_value(data_health.get("price_is_close_or_intraday"))),
        ("market_cap_source", "市值来源", _display_value(data_health.get("market_cap_source"))),
        ("volume_avg_period", "均量周期", _display_value(data_health.get("volume_avg_period"))),
        ("volume_ratio_formula", "量比公式", _display_value(data_health.get("volume_ratio_formula"))),
        ("financial_source", "财务来源", _display_value(data_health.get("financial_source"))),
        ("financial_period", "财务周期", _display_value(data_health.get("financial_period"))),
        ("financials_updated_at", "财务更新时间", _health_time(data_health.get("financials_updated_at"))),
        ("score_updated_at", "评分更新时间", _health_time(data_health.get("score_updated_at"))),
        ("portfolio_updated_at", "持仓更新时间", _health_time(data_health.get("portfolio_updated_at"))),
        ("critical_missing_fields", "关键缺口", _field_list_display(data_health.get("critical_missing_fields"))),
        ("optional_missing_fields", "可选缺口", _field_list_display(data_health.get("optional_missing_fields"))),
        ("not_applicable_fields", "不适用字段", _field_list_display(data_health.get("not_applicable_fields"))),
        ("missing_fields", "全部暂缺字段", _field_list_display(data_health.get("missing_fields"))),
        ("stale_fields", "过期字段", _field_list_display(data_health.get("stale_fields"))),
    ]
    summary = "".join(f"<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>" for label, value in summary_rows)
    detail_body = "".join(f"<tr><td>{escape(label)}</td><td>{escape(value)}</td></tr>" for _key, label, value in detail_rows)
    return (
        '<section class="ai-radar-card ai-radar-metric-card ai-radar-data-health-card">'
        '<div class="ai-radar-section-title"><span>数据健康</span><b>摘要 / 详情</b></div>'
        f'<div class="ai-radar-data-health-summary">{summary}</div>'
        '<details class="ai-radar-data-health-details">'
        '<summary>查看字段明细</summary>'
        f'<table class="ai-radar-metric-table"><tbody>{detail_body}</tbody></table>'
        '</details>'
        "</section>"
    )


def _health_time(value: Any) -> str:
    text = _short_time(value)
    return "暂缺" if text == "暂无" else text


def _entry_sentence(report: dict[str, Any]) -> str:
    if _price_below_valuation_inside_near_repair(report):
        return "价格低于估值参考区下沿，但仍处于近端修复观察区；当前未过趋势确认线，等待量价承接或重新站回关键均线。"
    if _current_zone_label(report) == "破位复核区":
        return "价格跌破支撑/失效线，需复核基本面、财报冲击或趋势破坏。"
    current_zone = _current_zone_label(report)
    label = _localize_report_text(str(report.get("entry_display_label") or "").strip())
    hint = _localize_report_text(str(report.get("entry_action_hint") or report.get("entry_display_reason") or "").strip())
    if label and hint:
        return f"{current_zone}：{label}；{hint}"
    if label or hint:
        return f"{current_zone}：{label or hint}"
    return f"{current_zone}：缺少明确买区提示，先按观察处理。"


def _decision_to_sentence(report: dict[str, Any]) -> str:
    current_zone = _current_zone_label(report)
    if current_zone == "近端修复观察区":
        return "当前不是追高，也不是确认买点；重点看支撑是否守住、收盘能否站回确认线，以及相对强弱是否修复。"
    if current_zone == "破位复核区":
        return "当前进入破位复核语境；只有确认基本面未恶化且重新站回关键位后，区间判断才重新有效。"
    if current_zone == "追高风险区":
        return "当前处于追高语境；系统建议等待回踩或新的确认线，不把上涨本身当作买点。"

    status = _core_status(report)
    decision = str(report.get("decision") or "")
    if status in {"价值复核", "近端复核"}:
        return "估值或近端区间已值得复核，但仍需要技术结构、承接和基本面确认。"
    if status == "买区内":
        return "价格进入纪律参考区，仍需结合仓位计划和承接确认。"
    if status == "追高风险":
        return "价格处于追高语境，系统建议等待回踩或新的确认线。"
    if decision == "DATA_MISSING":
        return "数据存在缺口，但报告仍给出可读结论，缺口在末尾说明。"
    return "当前以观察和复核为主，不把单一状态码当交易指令。"


def _next_step_sentence(report: dict[str, Any]) -> str:
    steps = report.get("next_technical_steps") or []
    if steps:
        return _localize_report_text(str(steps[0]))
    if report.get("confirmation_price"):
        return f"观察能否重新站上确认线 {_money(report.get('confirmation_price'))}。"
    if report.get("invalidation_price"):
        return f"观察是否守住失效线 {_money(report.get('invalidation_price'))}。"
    return "等待价格、技术和基本面缓存进一步补齐。"


def _current_zone_label(report: dict[str, Any]) -> str:
    price = _report_current_price(report)
    if price is None:
        return "区间待补"

    invalidation = _first_number(report, "invalidation_price", "radar_invalidation_price")
    structure = str(report.get("technical_structure_status") or "").strip().upper()
    if invalidation is not None and price < invalidation:
        return "破位复核区"
    if structure == "BREAKDOWN_REVIEW":
        return "破位复核区"

    chase = _first_number(report, "chase_above_price", "radar_chase_above_price")
    if chase is not None and price >= chase:
        return "追高风险区"

    if _price_in_range(
        price,
        _first_number(report, "near_term_repair_zone_low", "radar_near_term_repair_zone_low"),
        _first_number(report, "near_term_repair_zone_high", "radar_near_term_repair_zone_high"),
    ):
        return "近端修复观察区"
    if _price_in_range(
        price,
        _first_number(report, "valuation_reference_zone_low", "radar_valuation_reference_zone_low"),
        _first_number(report, "valuation_reference_zone_high", "radar_valuation_reference_zone_high"),
    ):
        return "估值参考区"
    if _price_in_range(
        price,
        _first_number(report, "deep_support_zone_low", "radar_deep_support_zone_low"),
        _first_number(report, "deep_support_zone_high", "radar_deep_support_zone_high"),
    ):
        return "深度支撑区"
    if _price_in_range(
        price,
        _first_number(report, "trend_reclaim_zone_low", "radar_trend_reclaim_zone_low"),
        _first_number(report, "trend_reclaim_zone_high", "radar_trend_reclaim_zone_high", "confirmation_price", "radar_confirmation_price"),
    ):
        return "趋势确认区"

    price_position = str(report.get("price_position") or "").strip().upper()
    if price_position == "IN_CHASE_ZONE":
        return "追高风险区"
    if price_position == "IN_BUY_ZONE":
        return "纪律参考区"
    return _core_status(report)


def _price_in_range(price: float, low: float | None, high: float | None) -> bool:
    if low is None or high is None:
        return False
    lower, upper = sorted((low, high))
    return lower <= price <= upper


def _price_below_valuation_inside_near_repair(report: dict[str, Any]) -> bool:
    price = _report_current_price(report)
    valuation_low = _first_number(report, "valuation_reference_zone_low", "radar_valuation_reference_zone_low")
    if price is None or valuation_low is None or price >= valuation_low:
        return False
    return _current_zone_label(report) == "近端修复观察区"


def _report_current_price(report: dict[str, Any]) -> float | None:
    return _first_number(report, "current_price", "currentPrice", "price")


def _localized_report_summary(report: dict[str, Any]) -> str:
    if _price_below_valuation_inside_near_repair(report):
        return "价格低于估值参考区下沿，但仍处于近端修复观察区；当前未过趋势确认线，等待量价承接或重新站回关键均线。"
    if _current_zone_label(report) == "破位复核区":
        return "价格跌破支撑/失效线，需复核基本面、财报冲击或趋势破坏。"

    raw_summary = str(report.get("summary") or "").strip()
    if not raw_summary:
        return ""
    lower = raw_summary.lower()
    internal_markers = (
        "discipline buy zone",
        "current price is below",
        "current price is above",
        "block_chase",
        "data_missing",
    )
    if any(marker in lower for marker in internal_markers):
        return ""
    return _localize_report_text(raw_summary)


FIELD_DISPLAY_LABELS = {
    "sector": "行业 / 赛道信息",
    "industry": "行业 / 赛道信息",
    "sector / industry": "行业 / 赛道信息",
    "industry_group": "行业 / 赛道信息",
    "industrygroup": "行业 / 赛道信息",
    "business_model": "行业 / 赛道信息",
    "businessmodel": "行业 / 赛道信息",
    "model": "行业 / 赛道信息",
    "company": "公司名称",
    "company_name": "公司名称",
    "companyname": "公司名称",
    "name": "公司名称",
    "market_cap": "市值",
    "marketcap": "市值",
    "mktcap": "市值",
    "company_market_cap": "市值",
    "volume": "成交量",
    "daily_bar.volume": "成交量",
    "latest_volume": "成交量",
    "forward_pe": "远期市盈率",
    "forwardpe": "远期市盈率",
    "normalized_pe": "标准化市盈率",
    "normalizedpe": "标准化市盈率",
    "roe": "净资产收益率",
    "return_on_equity": "净资产收益率",
    "daily_bars": "历史K线",
    "daily_ohlcv": "日线 OHLCV",
    "daily_ohlcv_window": "日线样本不足",
    "dailybars": "历史K线",
    "price_history": "历史K线",
    "technicals": "技术数据",
    "technical": "技术数据",
    "fundamentals": "基本面数据",
    "fundamental": "基本面数据",
    "valuation": "估值数据",
    "news_cache": "新闻缓存",
    "newscache": "新闻缓存",
    "recent_news": "新闻缓存",
    "enterprise_to_revenue": "EV/Sales",
    "enterprisetorevenue": "EV/Sales",
    "free_cash_flow_yield": "自由现金流收益率",
    "fcf_margin": "自由现金流率",
    "gross_margin": "毛利率",
    "net_margin": "净利率",
    "current_price": "当前价格",
    "current_price_stale": "价格过期",
    "price_stale": "价格过期",
    "ema20": "EMA20",
    "ema50": "EMA50",
    "ema200": "EMA200",
    "atr14": "ATR14",
    "atr_14": "ATR14",
    "atr_pct": "ATR 占比",
    "rsi14": "RSI14",
    "rsi_14": "RSI14",
    "avg_volume_20d": "20日均量",
    "volume_ratio": "量比",
    "volume_acceptance": "量价承接",
    "volume_price_acceptance": "量价承接",
    "volume_price_status": "量价状态",
    "volume_price_score": "量价评分",
    "ma20": "MA20",
    "ma50": "MA50",
    "ma200": "MA200",
    "swing_high": "阶段高点",
    "swing_low": "阶段低点",
    "support_zone": "支撑区",
    "support_zone_low": "支撑区下沿",
    "support_zone_high": "支撑区上沿",
    "resistance_zone": "压力区",
    "resistance_zone_low": "压力区下沿",
    "resistance_zone_high": "压力区上沿",
    "distance_to_invalidation": "距失效线",
    "distance_to_resistance": "距压力位",
    "reward_risk_ratio": "风险收益比",
    "vwap": "VWAP（日线替代）",
    "relative_strength": "相对强弱",
    "relative_strength_vs_qqq": "相对强弱（QQQ）",
    "relative_strength_vs_spy": "相对强弱（SPY）",
    "revenue_growth": "收入高增长",
    "revenue_backlog": "收入积压 / RPO",
    "backlog_to_ev": "收入积压 / EV",
    "active_power_gw": "已投运电力容量",
    "contracted_power_gw": "已签约电力容量",
    "capex": "资本开支",
    "operating_cash_flow": "经营现金流",
    "free_cash_flow_after_capex": "扣资本开支后自由现金流",
    "net_debt": "净债务",
    "interest_expense_to_revenue": "利息费用 / 收入",
    "adjusted_ebitda_margin": "调整后 EBITDA 利润率",
    "customer_concentration": "客户集中度",
    "financing_risk": "融资风险",
    "data_center_delivery_risk": "数据中心交付风险",
    "gpu_supplier_dependency": "GPU 供应商依赖",
    "quote_source": "报价来源",
    "market_cap_source": "市值来源",
    "volume_avg_period": "均量周期",
    "volume_ratio_formula": "量比公式",
    "financial_period": "财务周期",
    "financial_source": "财务来源",
    "price_is_close_or_intraday": "价格口径",
}


def _field_display_label(field: Any) -> str:
    raw = str(field or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("-", "_").replace(" ", "_")
    lookup_keys = [raw, raw.lower(), normalized, normalized.lower()]
    for key in lookup_keys:
        if key in FIELD_DISPLAY_LABELS:
            return FIELD_DISPLAY_LABELS[key]
    text = normalized.lower()
    if "sector" in text or "industry" in text or "business_model" in text:
        return "行业 / 赛道信息"
    if "company" in text:
        return "公司名称"
    if "market" in text and "cap" in text:
        return "市值"
    if "volume" in text:
        return "成交量"
    if "forward" in text and "pe" in text:
        return "远期市盈率"
    if text == "pe" or "price_to_earnings" in text:
        return "市盈率"
    if "roe" in text or "return_on_equity" in text:
        return "净资产收益率"
    if "daily" in text or "history" in text:
        return "历史K线"
    if "support" in text:
        return "支撑区"
    if "resistance" in text:
        return "压力区"
    if "invalidation" in text:
        return "失效线"
    if "reward_risk" in text:
        return "风险收益比"
    if "technical" in text or "ema" in text or "atr" in text or "swing" in text or "ma20" in text or "ma50" in text or "ma200" in text:
        return "技术数据"
    if "fundamental" in text:
        return "基本面数据"
    if "valuation" in text:
        return "估值数据"
    if "news" in text:
        return "新闻缓存"
    localized = _localize_report_text(raw)
    if localized != raw:
        return localized
    if raw.isascii():
        return "数据字段"
    return raw


def _field_list_display(value: Any, row: dict[str, Any] | None = None) -> str:
    if not value:
        return "无"
    values = value if isinstance(value, (list, tuple, set)) else [value]
    labels = [_field_display_label(item) for item in values if str(item).strip()]
    if row is not None:
        profile = _resolved_profile(row)
        labels = [
            label
            for label in labels
            if not (
                (label == "行业 / 赛道信息" and profile["track"])
                or (label == "公司名称" and profile["company"])
            )
        ]
    labels = _dedupe_text([label for label in labels if label])
    return "、".join(labels[:8]) if labels else "无"


def _localize_report_text(text: str) -> str:
    replacements = {
        "AI Stock Radar Research": "AI 股票雷达研究",
        "Research notes": "研究依据",
        "wait": "等待",
        "WAIT_CONFIRMATION": "等待确认",
        "BLOCK_CHASE": "追高风险提示",
        "DATA_INSUFFICIENT": "数据不足",
        "DATA_MISSING": "数据不足",
        "ALLOW_BUY": "小仓观察建议",
        "AVOID": "暂不参与",
        "HOLD_NO_ADD": "仓位偏高，不建议继续加",
        "POSITION_LIMITED": "仓位接近上限，建议控制节奏",
        "current price is below the discipline buy zone lower bound": "当前价格低于技术回踩带下沿",
        "current price is above the discipline buy zone": "当前价格高于技术回踩带",
        "current price is in or above chase zone": "当前价格已接近或进入追高线",
        "review fundamentals": "重新检查基本面与技术承接",
        "Revenue growth": "收入高增长",
        "Revenue Growth": "收入高增长",
        "revenue growth": "收入高增长",
        "Gross Margin / unit economics": "毛利率 / 单位经济性",
        "Gross margin / unit economics": "毛利率 / 单位经济性",
        "gross margin / unit economics": "毛利率 / 单位经济性",
        "Gross Margin": "毛利率",
        "gross margin": "毛利率",
        "unit economics": "单位经济性",
        "EV/Sales growth": "EV/Sales 估值扩张",
        "ev/sales growth": "EV/Sales 估值扩张",
        "Price vs 52-week high": "距离52周高点回撤",
        "price vs 52-week high": "距离52周高点回撤",
        "Negative FCF": "自由现金流为负",
        "negative FCF": "自由现金流为负",
        "High leverage": "杠杆偏高",
        "high leverage": "杠杆偏高",
        "FCF trajectory": "自由现金流路径不确定",
        "fcf trajectory": "自由现金流路径不确定",
        "final score below 70; core position is not allowed.": "综合评分低于70，不建议作为核心仓",
        "final score below 70; core position is not allowed": "综合评分低于70，不建议作为核心仓",
        "Operating Margin": "经营利润率",
        "FCF Margin": "自由现金流率",
        "ROIC": "投入资本回报率",
        "Forward PE": "远期市盈率",
        "normalized PE": "标准化市盈率",
        "Drawdown": "回撤",
        "technical setup": "技术结构",
        "Net Cash": "净现金",
        "Balance Sheet": "资产负债表",
        "Segment strength": "业务增长质量",
        "Buyback discipline": "回购纪律",
        "Historical valuation percentile": "历史估值分位",
        "Capex concern discount": "资本开支折价",
        "AI capex overbuild risk": "AI 资本开支过热风险",
        "Regulatory risk": "监管风险",
        "sector / industry": "行业 / 赛道信息",
        "market_cap": "市值",
        "marketCap": "市值",
        "volume": "成交量",
        "forward_pe": "远期市盈率",
        "roe": "净资产收益率",
        "daily_bars": "历史K线",
        "technicals": "技术数据",
        "fundamentals": "基本面数据",
        "valuation": "估值数据",
        "news_cache": "新闻缓存",
        "N/A": "暂无",
    }
    result = str(text or "")
    for source, target in replacements.items():
        result = result.replace(source, target)
    return result


def _display_value(value: Any) -> str:
    text = str(value or "").strip()
    return _localize_report_text(text) if text else "暂无"


def _data_missing_detail_text(report: dict[str, Any], volume: dict[str, Any] | None = None) -> str:
    fields = [str(item).strip() for item in _actionable_missing_fields(report) if str(item).strip()]
    fields.extend(str(item).strip() for item in _all_missing_fields(report) if _is_optional_gap_field(str(item)))
    if volume and volume.get("volume_source") != "unavailable":
        fields = [field for field in fields if _field_display_label(field) != "成交量"]
    if volume and volume.get("volume_source") == "unavailable":
        fields.append("volume")
    groups = _missing_groups(report)
    if any("资料缺口" in group for group in groups):
        if not (report.get("company_name") or report.get("companyName") or report.get("name") or report.get("company")):
            fields.append("company_name")
        if not (
            report.get("sector")
            or report.get("industry")
            or report.get("industry_group")
            or report.get("industryGroup")
            or report.get("business_model")
            or report.get("businessModel")
            or report.get("model")
        ):
            fields.append("sector / industry")
    return _field_list_display(_dedupe_text(fields), report)


def _average_score(*values: Any) -> float | None:
    numbers = [_number(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _history_return(history: pd.DataFrame, days: int) -> float | None:
    if history is None or history.empty or "close" not in history:
        return None
    closes = pd.to_numeric(history["close"], errors="coerce").dropna()
    if len(closes) <= days:
        return None
    current = float(closes.iloc[-1])
    base = float(closes.iloc[-days - 1])
    if base == 0:
        return None
    return (current - base) / base * 100


def _history_ytd_return(history: pd.DataFrame) -> float | None:
    if history is None or history.empty or "close" not in history or "date" not in history:
        return None
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"])
    if frame.empty:
        return None
    current = float(frame.iloc[-1]["close"])
    year = frame.iloc[-1]["date"].year
    ytd = frame[frame["date"].dt.year == year]
    if ytd.empty:
        return None
    base = float(ytd.iloc[0]["close"])
    if base == 0:
        return None
    return (current - base) / base * 100


def _selected_radar_view() -> str:
    view = str(st.query_params.get("view", "") or "").strip().lower()
    if view == "list":
        return "list"
    if view == "report" or _query_symbol():
        return "report"
    return "list"


def _selected_symbol(tickers: list[str]) -> str:
    query_symbol = _query_symbol()
    if query_symbol:
        if st.session_state.get("ai_radar_selected_ticker") != query_symbol:
            st.session_state["ai_radar_selected_ticker"] = query_symbol
        return query_symbol
    stored = str(st.session_state.get("ai_radar_selected_ticker") or "").strip().upper()
    return stored if stored in {ticker.upper() for ticker in tickers} else ""


def _query_symbol() -> str:
    return str(st.query_params.get("ticker") or st.query_params.get("symbol") or "").strip().upper()


def _report_view_href(ticker: str) -> str:
    filter_key = _selected_radar_filter_key()
    return f"?page=ai-radar&view=report&ticker={escape(ticker, quote=True)}&radarFilter={escape(filter_key, quote=True)}#radar-report"


def _list_view_href() -> str:
    filter_key = _selected_radar_filter_key()
    return f"?page=ai-radar&view=list&radarFilter={escape(filter_key, quote=True)}"


def select_radar_symbols(watchlist: list[str], sample_symbols: list[str] | None = None) -> tuple[list[str], str]:
    real = _normalize_symbols(watchlist)
    if real:
        return real, "watchlist"
    sample = _normalize_symbols(sample_symbols or [])
    if sample:
        return sample, "sample fallback"
    return [], "empty watchlist"


def _normalize_symbols(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    symbols: list[str] = []
    for value in values:
        symbol = str(value or "").strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def _single_report_row(symbol: str) -> dict[str, Any] | None:
    cached = _dashboard_row(symbol)
    if cached:
        return cached
    try:
        from data.fundamentals import FundamentalCache
        from ui import dashboard as dashboard_ui

        return dashboard_ui._load_cached_dashboard_row(FundamentalCache(), symbol)
    except Exception:
        return None


def _dashboard_row(symbol: str) -> dict[str, Any] | None:
    table = st.session_state.get("dashboard_table_cache")
    if not isinstance(table, pd.DataFrame) or table.empty:
        return None
    symbol_upper = symbol.upper()
    matches = table[table["symbol"].astype(str).str.upper() == symbol_upper] if "symbol" in table.columns else pd.DataFrame()
    if matches.empty:
        return None
    return dict(matches.iloc[0].to_dict())


def _scores_from_row(row: dict[str, Any] | None) -> RadarScores | None:
    if not row:
        return None
    return RadarScores(
        final_score=_number(_row_value(row, "totalScore")),
        quality_score=_number(_row_value(row, "qualityScore", "quality_score")),
        growth_score=_number(_row_value(row, "growthScore", "growth_score")),
        valuation_score=_number(_row_value(row, "valuationScore", "entryScore", "valuation_score")),
        technical_score=_number(_row_value(row, "technicalScore", "technical_score")),
        risk_score=_number(_row_value(row, "riskScore", "risk_score")),
    )


def _watch_points(row: dict[str, Any] | None) -> list[str]:
    if not row:
        return []
    points = []
    points.extend(_list_value(row, "missingDataExplanation"))
    points.extend(_list_value(row, "decisionReviewReasons"))
    points.extend(_list_value(row, "decisionBlockReasons"))
    return points


def _row_value(row: dict[str, Any] | None, *keys: str) -> Any:
    if not row:
        return None
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _dict_value(row: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    value = _row_value(row, key)
    return value if isinstance(value, dict) else None


def _list_value(row: dict[str, Any] | None, key: str) -> list[str]:
    value = _row_value(row, key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if value else []


def _entry_display_html(row: dict[str, Any]) -> str:
    label = str(row.get("entry_display_label") or "暂无参考买区").strip()
    hint = str(row.get("entry_action_hint") or row.get("entry_display_reason") or "").strip()
    if not hint:
        hint = "只读参考，不改变主结论"
    return (
        '<div class="ai-radar-entry-ref">'
        f'<strong>{escape(label)}</strong>'
        f'<span>{escape(hint)}</span>'
        "</div>"
    )


def _decision_label(value: str) -> str:
    return value if value else "UNKNOWN"


def _data_status_label(value: str) -> str:
    return {
        "OK": "正常",
        "STALE": "价格可能过期",
        "MISSING_PRICE": "缺价格",
        "MISSING_SCORE": "缺评分",
        "MISSING_BUY_ZONE": "缺买区上下文",
    }.get(value, value or "未知")


def _price_position_label(value: Any) -> str:
    return format_zone_status(value)


def _decision_tone(value: str) -> str:
    return {
        "ALLOW_BUY": "allow",
        "WAIT": "wait",
        "BLOCK_CHASE": "block",
        "AVOID": "avoid",
        "DATA_MISSING": "missing",
    }.get(value, "wait")


def _zone_text(value: Any) -> str:
    return format_buy_zone(value)


def _empty_card_html(title: str, message: str) -> str:
    return (
        '<section class="ai-radar-card">'
        f'<div class="ai-radar-section-title"><span>{escape(title)}</span><b>暂无图表</b></div>'
        f"<p class=\"ai-radar-empty-note\">{escape(message)}</p>"
        "</section>"
    )


def _money(value: Any) -> str:
    number = _number(value)
    return "暂无" if number is None else f"${number:,.2f}"


def _signed_money(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "暂无"
    prefix = "+" if number >= 0 else "-"
    return f"{prefix}${abs(number):,.2f}"


def _quantity_text(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "暂缺"
    if abs(number - round(number)) < 0.0001:
        return f"{number:,.0f}"
    return f"{number:,.2f}"


def _compact_money(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "暂无"
    abs_value = abs(number)
    if abs_value >= 1_000_000_000_000:
        return f"${number / 1_000_000_000_000:.2f}T"
    if abs_value >= 1_000_000_000:
        return f"${number / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"${number / 1_000_000:.1f}M"
    return f"${number:,.0f}"


def _compact_number(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "暂无"
    abs_value = abs(number)
    if abs_value >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if abs_value >= 1_000:
        return f"{number / 1_000:.1f}K"
    return f"{number:,.0f}"


def _pct(value: Any) -> str:
    number = _number(value)
    return "暂无" if number is None else f"{number:.1f}%"


def _signed_pct(value: Any) -> str:
    number = _number(value)
    return "暂无" if number is None else f"{number:+.1f}%"


def _number_text(value: Any) -> str:
    number = _number(value)
    return "暂无" if number is None else f"{number:.1f}"


def _multiple(value: Any) -> str:
    number = _number(value)
    return "暂无" if number is None else f"{number:.1f}x"


def _ratio_pct(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "暂无"
    if abs(number) <= 1:
        number *= 100
    return f"{number:.1f}%"


def _range_text(low: Any, high: Any) -> str:
    low_number = _number(low)
    high_number = _number(high)
    if low_number is None and high_number is None:
        return "暂无"
    if low_number is None:
        return f"≤ {_money(high_number)}"
    if high_number is None:
        return f"≥ {_money(low_number)}"
    return f"{_money(low_number)} - {_money(high_number)}"


def _short_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "暂无"
    return text.replace("T", " ")[:16]


def _number(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str):
        value = (
            value.strip()
            .replace("$", "")
            .replace(",", "")
            .replace("%", "")
            .replace("x", "")
            .replace("X", "")
        )
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _first_number(*sources: Any) -> float | None:
    if not sources:
        return None
    if len(sources) > 1 and all(isinstance(source, dict) for source in sources[:-1]) and isinstance(sources[-1], str):
        keys = (str(sources[-1]),)
        containers = sources[:-1]
    else:
        containers = [source for source in sources if isinstance(source, dict)]
        keys = tuple(str(source) for source in sources if isinstance(source, str))
    for container in containers:
        for key in keys:
            if key in container:
                number = _number(container.get(key))
                if number is not None:
                    return number
    return None


def _first_present(container: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in container and container.get(key) not in (None, ""):
            return container.get(key)
    return None


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"none", "nan", "n/a", "unknown"} else text


def _dedupe_text(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _render_styles() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stMainBlockContainer"],
        div.block-container {
            margin-left:252px !important;
            margin-right:24px !important;
            width:calc(100vw - 300px) !important;
            max-width:calc(100vw - 300px) !important;
        }
        @media (max-width: 980px) {
            [data-testid="stMainBlockContainer"],
            div.block-container {
                margin-left:0 !important;
                margin-right:0 !important;
                width:100% !important;
                max-width:100% !important;
            }
        }
        .ai-radar-filter-chips {
            display:flex;
            align-items:center;
            flex-wrap:wrap;
            gap:8px;
            margin:6px 0 12px;
        }
        .ai-radar-filter-chip {
            display:inline-flex;
            align-items:center;
            gap:6px;
            min-height:30px;
            padding:0 12px;
            border:1px solid #D8E0EA;
            border-radius:999px;
            background:#FFFFFF;
            color:#334155 !important;
            text-decoration:none !important;
            font-size:12px;
            font-weight:760;
        }
        .ai-radar-filter-chip b {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            min-width:20px;
            height:20px;
            padding:0 5px;
            border-radius:999px;
            background:#EEF2F7;
            color:#0B1F3A;
            font-size:11px;
        }
        .ai-radar-filter-chip.active {
            background:#0B1F3A;
            border-color:#0B1F3A;
            color:#FFFFFF !important;
        }
        .ai-radar-filter-chip.active b {
            background:rgba(255,255,255,0.16);
            color:#FFFFFF;
        }
        .ai-radar-list-card {
            border:1px solid #D8E0EA;
            background:#FFFFFF;
            border-radius:10px;
            margin-top:12px;
            overflow:hidden;
            box-shadow:0 10px 28px rgba(15, 23, 42, 0.06);
        }
        .ai-radar-section-head {
            display:flex;
            justify-content:space-between;
            align-items:center;
            padding:12px 16px;
            border-bottom:1px solid #E6EDF5;
            background:#F8FAFC;
        }
        .ai-radar-section-head strong { font-size:15px; color:#0B1F3A; letter-spacing:0; }
        .ai-radar-section-head span { font-size:12px; color:#64748B; }
        .ai-radar-list-note {
            margin:0;
            padding:10px 16px;
            border-bottom:1px solid #EEF2F7;
            color:#64748B;
            font-size:12px;
            background:#FFFFFF;
        }
        .ai-radar-list-note-compact {
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:12px;
            background:#F8FAFC;
        }
        .ai-radar-list-note-compact a {
            color:#0B1F3A !important;
            font-weight:800;
            text-decoration:none !important;
        }
        .ai-radar-research-summary {
            display:grid;
            grid-template-columns:repeat(4, minmax(0, 1fr));
            gap:10px;
            margin:8px 0 12px;
        }
        .ai-radar-research-card {
            border:1px solid #D8E0EA;
            border-radius:10px;
            background:#FFFFFF;
            padding:10px 12px;
            box-shadow:0 8px 20px rgba(15, 23, 42, 0.045);
        }
        .ai-radar-research-card span {
            display:block;
            color:#64748B;
            font-size:12px;
            font-weight:720;
        }
        .ai-radar-research-card strong {
            display:block;
            margin-top:4px;
            color:#0B1F3A;
            font-size:24px;
            line-height:1;
            font-weight:860;
        }
        .ai-radar-report-toolbar,
        .ai-radar-report-missing {
            display:flex;
            align-items:center;
            gap:14px;
            max-width:1280px;
            margin:8px auto 12px;
            padding:11px 14px;
            border:1px solid #D8E0EA;
            border-radius:10px;
            background:#FFFFFF;
            box-shadow:0 8px 22px rgba(15, 23, 42, 0.045);
        }
        .ai-radar-report-toolbar > a,
        .ai-radar-report-missing > a {
            display:inline-flex;
            align-items:center;
            min-height:28px;
            padding:0 10px;
            border:1px solid #D8E0EA;
            border-radius:999px;
            color:#0B1F3A !important;
            text-decoration:none !important;
            font-size:12px;
            font-weight:760;
            background:#F8FAFC;
        }
        .ai-radar-report-toolbar > div {
            display:flex;
            flex-direction:column;
            min-width:0;
            gap:2px;
        }
        .ai-radar-report-toolbar strong,
        .ai-radar-report-missing strong {
            color:#0B1F3A;
            font-size:16px;
            line-height:1.1;
            font-weight:820;
        }
        .ai-radar-report-toolbar span,
        .ai-radar-report-missing span {
            color:#64748B;
            font-size:12px;
        }
        .ai-radar-report-missing {
            flex-direction:column;
            align-items:flex-start;
        }
        .ai-radar-table-wrap { overflow-x:auto; }
        .ai-radar-table {
            width:100%;
            border-collapse:collapse;
            font-size:12px;
        }
        .ai-radar-table th {
            text-align:left;
            color:#64748B;
            background:#F8FAFC;
            padding:9px 12px;
            border-bottom:1px solid #E2E8F0;
            white-space:nowrap;
            font-weight:700;
        }
        .ai-radar-table td {
            padding:10px 12px;
            border-bottom:1px solid #EEF2F7;
            color:#1E293B;
            vertical-align:middle;
        }
        .ai-radar-table tr:hover td,
        .ai-radar-table tr.active td { background:#F8FBFF; }
        .ai-radar-ticker {
            color:#0B1F3A !important;
            font-weight:800;
            text-decoration:none !important;
            font-size:13px;
        }
        .ai-radar-status-pill,
        .ai-radar-data-confidence,
        .ai-radar-report-status,
        .ai-radar-report-link {
            display:inline-flex;
            align-items:center;
            min-height:24px;
            padding:2px 9px;
            border-radius:999px;
            border:1px solid #D8E0EA;
            background:#F8FAFC;
            color:#334155;
            font-weight:700;
            white-space:nowrap;
            text-decoration:none !important;
        }
        .ai-radar-data-confidence.高 { background:#ECFDF3; color:#166534; border-color:#BBE5C6; }
        .ai-radar-data-confidence.中 { background:#EFF6FF; color:#1D4E89; border-color:#BFDBFE; }
        .ai-radar-data-confidence.低 { background:#FFFBEB; color:#92400E; border-color:#FDE68A; }
        .ai-radar-data-confidence.不足 { background:#FFF1F2; color:#9F1239; border-color:#F4C7CE; }
        .ai-radar-company-cell {
            display:flex;
            flex-direction:column;
            gap:2px;
            min-width:0;
        }
        .ai-radar-company-cell strong {
            color:#0B1F3A;
            font-size:12px;
            font-weight:780;
        }
        .ai-radar-company-cell span,
        .ai-radar-muted {
            color:#64748B;
            font-size:11px;
        }
        .ai-radar-report-status {
            background:#F8FAFC;
            color:#334155;
            border-color:#E2E8F0;
        }
        .ai-radar-buy-point-reason {
            display:inline-block;
            max-width:260px;
            color:#334155;
            font-size:12px;
            line-height:1.45;
        }
        .ai-radar-priority-score,
        .ai-radar-data-quality {
            display:inline-flex;
            align-items:center;
            min-height:22px;
            padding:2px 8px;
            border-radius:999px;
            background:#F8FAFC;
            border:1px solid #E2E8F0;
            color:#334155;
            font-size:11px;
            font-weight:760;
            white-space:nowrap;
        }
        .ai-radar-report-link {
            background:transparent;
            color:#0B1F3A !important;
            border-color:transparent;
            padding:0;
            min-height:auto;
        }
        .ai-radar-report-link:hover { text-decoration:underline !important; }
        .ai-radar-research-report {
            max-width:1280px;
            margin:18px auto 0;
            padding:0;
            border:1px solid #D8E0EA;
            background:#FFFFFF;
            border-radius:12px;
            overflow:hidden;
            box-shadow:0 18px 46px rgba(15, 23, 42, 0.08);
        }
        .ai-radar-research-report.loading {
            box-shadow:0 14px 34px rgba(15, 23, 42, 0.06);
        }
        .ai-radar-research-header {
            position:relative;
            display:grid;
            grid-template-columns:minmax(320px, 1fr) minmax(340px, 430px);
            gap:22px 28px;
            padding:30px 32px 26px;
            background:
                linear-gradient(135deg, rgba(11,31,58,0.98) 0%, rgba(17,45,78,0.98) 58%, rgba(21,68,101,0.96) 100%);
            color:#FFFFFF;
        }
        .ai-radar-research-header.skeleton {
            min-height:260px;
        }
        .ai-radar-title-block span,
        .ai-radar-title-block em {
            display:block;
            color:#BED3EA;
            font-size:12px;
            font-style:normal;
        }
        .ai-radar-title-block span {
            font-weight:850;
            letter-spacing:.12em;
            text-transform:uppercase;
        }
        .ai-radar-title-block h1 {
            margin:8px 0 0;
            font-size:56px;
            line-height:1;
            letter-spacing:0;
        }
        .ai-radar-title-block p {
            margin:9px 0 6px;
            color:#F8FAFC;
            font-size:18px;
            font-weight:750;
            line-height:1.3;
        }
        .ai-radar-title-block em {
            max-width:720px;
            color:#C7D7EA;
            font-size:13px;
            line-height:1.5;
        }
        .ai-radar-header-stats {
            grid-column:1 / -1;
            display:grid;
            grid-template-columns:repeat(6, minmax(0, 1fr));
            gap:12px;
        }
        .ai-radar-header-stats div {
            min-height:72px;
            display:flex;
            flex-direction:column;
            justify-content:center;
            background:rgba(255,255,255,0.085);
            border:1px solid rgba(216,224,234,0.18);
            border-radius:12px;
            padding:12px 14px;
        }
        .ai-radar-header-stats span {
            display:block;
            color:#AFC4DC;
            font-size:11px;
            font-weight:780;
            margin-bottom:6px;
        }
        .ai-radar-header-stats strong {
            color:#FFFFFF;
            font-size:16px;
            line-height:1.25;
            font-weight:850;
        }
        .ai-radar-research-section,
        .ai-radar-research-grid,
        .ai-radar-opinion-grid,
        .ai-radar-visual-grid,
        .ai-radar-evidence-grid,
        .ai-radar-data-quality,
        .ai-radar-executive-card,
        .ai-radar-appendix,
        .ai-radar-report-foot {
            margin:14px 18px;
        }
        .ai-radar-research-grid,
        .ai-radar-opinion-grid,
        .ai-radar-visual-grid,
        .ai-radar-evidence-grid {
            display:grid;
            gap:12px;
        }
        .ai-radar-research-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); }
        .ai-radar-opinion-grid { grid-template-columns:repeat(3, minmax(0, 1fr)); }
        .ai-radar-opinion-grid.two-col { grid-template-columns:repeat(2, minmax(0, 1fr)); }
        .ai-radar-visual-grid { grid-template-columns:minmax(0, 1.6fr) minmax(280px, .8fr); }
        .ai-radar-evidence-grid { grid-template-columns:minmax(0, 1.35fr) minmax(280px, .75fr); }
        .ai-radar-card,
        .ai-radar-summary-section,
        .ai-radar-executive-card {
            border:1px solid #E2E8F0;
            background:#FFFFFF;
            border-radius:10px;
            padding:14px;
        }
        .ai-radar-executive-card {
            padding:18px;
            border-color:#D6E0EC;
            box-shadow:0 10px 26px rgba(15, 23, 42, 0.06);
        }
        .ai-radar-decision-summary-head,
        .ai-radar-key-price-grid {
            display:grid;
            grid-template-columns:repeat(3, minmax(0, 1fr));
            gap:10px;
            margin-bottom:12px;
        }
        .ai-radar-decision-summary-head div,
        .ai-radar-key-price-grid div {
            background:#F8FAFC;
            border:1px solid #E8EEF5;
            border-radius:8px;
            padding:10px;
        }
        .ai-radar-decision-summary-head span,
        .ai-radar-key-price-grid span {
            display:block;
            color:#64748B;
            font-size:11px;
            margin-bottom:4px;
            font-weight:750;
        }
        .ai-radar-decision-summary-head strong,
        .ai-radar-key-price-grid strong {
            display:block;
            color:#0B1F3A;
            font-size:14px;
            line-height:1.35;
            font-weight:860;
        }
        .ai-radar-next-step-card {
            margin-top:12px;
            background:#F8FBFF;
            border:1px solid #D6E0EC;
            border-radius:8px;
            padding:12px 14px;
        }
        .ai-radar-next-step-card b {
            display:block;
            color:#0B1F3A;
            font-size:13px;
            margin-bottom:6px;
        }
        .ai-radar-next-step-card ol {
            margin:0;
            padding-left:18px;
            color:#334155;
            font-size:13px;
            line-height:1.65;
        }
        .ai-radar-thesis {
            margin:0 0 14px;
            color:#0B1F3A;
            font-size:16px;
            line-height:1.65;
            font-weight:700;
        }
        .ai-radar-batting-card {
            margin:0 0 14px;
            padding:14px;
            border:1px solid #CBD5E1;
            border-left:4px solid #0F766E;
            border-radius:9px;
            background:#F8FBFF;
        }
        .ai-radar-batting-card p {
            margin:0 0 10px;
            color:#0B1F3A;
            font-size:15px;
            line-height:1.45;
            font-weight:850;
        }
        .ai-radar-batting-grid {
            display:grid;
            grid-template-columns:repeat(4, minmax(0, 1fr));
            gap:8px;
        }
        .ai-radar-batting-grid div {
            border-top:1px solid #E2E8F0;
            padding-top:8px;
        }
        .ai-radar-batting-grid span {
            display:block;
            color:#64748B;
            font-size:11px;
            margin-bottom:3px;
        }
        .ai-radar-batting-grid strong {
            display:block;
            color:#0B1F3A;
            font-size:12px;
            line-height:1.35;
            font-weight:840;
        }
        .ai-radar-position-context {
            margin:0 0 14px;
            padding:13px 14px;
            border:1px solid #D6E0EC;
            border-left:4px solid #1D4ED8;
            border-radius:8px;
            background:#F8FBFF;
        }
        .ai-radar-position-context > strong {
            display:block;
            color:#0B1F3A;
            font-size:14px;
            line-height:1.45;
            margin-bottom:5px;
        }
        .ai-radar-position-context > span {
            display:block;
            color:#334155;
            font-size:13px;
            line-height:1.55;
            margin-bottom:10px;
        }
        .ai-radar-position-context-grid {
            display:grid;
            grid-template-columns:repeat(4, minmax(0, 1fr));
            gap:8px;
        }
        .ai-radar-position-context-grid div {
            border-top:1px solid #E2E8F0;
            padding-top:8px;
        }
        .ai-radar-position-context-grid span {
            display:block;
            color:#64748B;
            font-size:11px;
            margin-bottom:3px;
        }
        .ai-radar-position-context-grid b {
            display:block;
            color:#0B1F3A;
            font-size:12px;
            line-height:1.35;
            font-weight:820;
        }
        .ai-radar-exec-grid {
            display:grid;
            grid-template-columns:repeat(auto-fit, minmax(170px, 1fr));
            gap:10px;
            margin-bottom:12px;
        }
        .ai-radar-exec-grid div {
            background:#F8FAFC;
            border:1px solid #E8EEF5;
            border-radius:8px;
            padding:10px;
        }
        .ai-radar-exec-grid span {
            display:block;
            color:#64748B;
            font-size:11px;
            margin-bottom:4px;
        }
        .ai-radar-exec-grid strong {
            color:#0B1F3A;
            font-size:13px;
            line-height:1.45;
        }
        .ai-radar-observation-list {
            margin:0;
            padding-left:18px;
            color:#334155;
            font-size:13px;
            line-height:1.7;
        }
        .ai-radar-conclusion-list {
            margin:0;
            padding-left:20px;
            color:#26364D;
            font-size:13px;
            line-height:1.72;
        }
        .ai-radar-header-decision {
            align-self:start;
            background:rgba(248,250,252,0.10);
            border:1px solid rgba(216,224,234,0.22);
            border-radius:14px;
            padding:16px;
            box-shadow:0 18px 36px rgba(0,0,0,0.12);
        }
        .ai-radar-header-kicker {
            display:block;
            color:#AFC4DC;
            font-size:11px;
            font-weight:850;
            letter-spacing:.12em;
            text-transform:uppercase;
            margin-bottom:8px;
        }
        .ai-radar-header-decision > strong {
            display:block;
            color:#FFFFFF;
            font-size:24px;
            line-height:1.18;
            font-weight:900;
            margin-bottom:14px;
        }
        .ai-radar-header-decision-grid {
            display:grid;
            grid-template-columns:repeat(2, minmax(0, 1fr));
            gap:10px;
            padding-top:12px;
            border-top:1px solid rgba(216,224,234,0.18);
        }
        .ai-radar-header-decision-grid span {
            display:block;
            color:#AFC4DC;
            font-size:10px;
            font-weight:760;
            margin-bottom:5px;
        }
        .ai-radar-header-decision-grid b {
            display:block;
            color:#F8FAFC;
            font-size:13px;
            line-height:1.25;
            font-weight:850;
        }
        .ai-radar-appendix {
            border-top:1px solid #E8EEF5;
            padding-top:12px;
        }
        .ai-radar-folded-section {
            margin:14px 18px;
            border:1px solid #E2E8F0;
            border-radius:10px;
            background:#FFFFFF;
            padding:0;
        }
        .ai-radar-folded-section summary {
            cursor:pointer;
            list-style:none;
            padding:12px 14px;
            color:#0B1F3A;
            font-size:13px;
            font-weight:850;
        }
        .ai-radar-folded-section summary::-webkit-details-marker { display:none; }
        .ai-radar-folded-section summary::before {
            content:"+";
            display:inline-flex;
            width:18px;
            color:#64748B;
            font-weight:900;
        }
        .ai-radar-folded-section[open] summary::before { content:"-"; }
        .ai-radar-appendix.lazy {
            border-top:0;
            padding-top:0;
            max-width:1280px;
            margin:14px auto;
        }
        .ai-radar-appendix-placeholder {
            border:1px dashed #CBD5E1;
            border-radius:8px;
            background:#F8FAFC;
            padding:12px 14px;
            color:#475569;
            font-size:12px;
            line-height:1.55;
        }
        .ai-radar-appendix-placeholder strong {
            display:block;
            color:#0B1F3A;
            font-size:13px;
            margin-bottom:3px;
        }
        .ai-radar-appendix-placeholder span {
            display:block;
        }
        .ai-radar-appendix-details {
            color:#334155;
        }
        .ai-radar-appendix-details summary {
            cursor:pointer;
            color:#475569;
            font-size:12px;
            font-weight:850;
            letter-spacing:.04em;
            list-style:none;
            padding:4px 0 8px;
        }
        .ai-radar-appendix-details summary::-webkit-details-marker { display:none; }
        .ai-radar-appendix-details summary::before {
            content:"＋";
            display:inline-block;
            width:18px;
            color:#64748B;
            font-weight:800;
        }
        .ai-radar-appendix-details[open] summary::before { content:"－"; }
        .ai-radar-appendix-title {
            color:#64748B;
            font-size:12px;
            font-weight:850;
            letter-spacing:.04em;
            margin:0 0 4px;
        }
        .ai-radar-section-title {
            display:flex;
            justify-content:space-between;
            gap:12px;
            align-items:baseline;
            margin-bottom:10px;
        }
        .ai-radar-section-title span {
            color:#0B1F3A;
            font-size:14px;
            font-weight:850;
        }
        .ai-radar-section-title b {
            color:#64748B;
            font-size:11px;
            font-weight:700;
        }
        .ai-radar-summary-lines p {
            margin:7px 0;
            color:#26364D;
            font-size:14px;
            line-height:1.65;
        }
        .ai-radar-range-axis {
            display:flex;
            justify-content:space-between;
            color:#64748B;
            font-size:11px;
            margin-bottom:8px;
        }
        .ai-radar-range-row {
            display:grid;
            grid-template-columns:112px minmax(160px, 1fr) 142px 112px;
            gap:10px;
            align-items:center;
            padding:7px 0;
            border-top:1px solid #EEF2F7;
            font-size:12px;
        }
        .ai-radar-range-row.current {
            margin:0 -8px;
            padding:8px;
            border-radius:8px;
            background:#F8FBFF;
            border-top:1px solid #D6E0EC;
        }
        .ai-radar-range-row span { color:#334155; font-weight:700; }
        .ai-radar-range-row b { color:#0F172A; font-weight:750; text-align:right; }
        .ai-radar-range-row em {
            color:#475569;
            font-style:normal;
            font-weight:760;
            text-align:right;
        }
        .ai-radar-range-track {
            position:relative;
            height:10px;
            border-radius:999px;
            background:#E8EEF5;
        }
        .ai-radar-range-track > i:not(.ai-radar-current-marker) {
            position:absolute;
            top:2px;
            height:6px;
            border-radius:999px;
        }
        .ai-radar-range-track i.blue { background:#2563EB; }
        .ai-radar-range-track i.slate { background:#64748B; }
        .ai-radar-range-track i.green { background:#16A34A; }
        .ai-radar-range-track i.amber { background:#D97706; }
        .ai-radar-range-track i.orange { background:#EA580C; }
        .ai-radar-range-track i.red { background:#DC2626; }
        .ai-radar-current-marker {
            position:absolute;
            top:-6px;
            width:2px;
            height:22px;
            background:#0B1F3A;
            border-radius:2px;
        }
        .ai-radar-current-marker b {
            position:absolute;
            top:-20px;
            left:-42px;
            color:#0B1F3A;
            background:#FFFFFF;
            border:1px solid #CBD5E1;
            border-radius:6px;
            padding:1px 5px;
            font-size:10px;
            white-space:nowrap;
        }
        .ai-radar-range-explain,
        .ai-radar-setup-explain,
        .ai-radar-score-explain {
            margin:12px 0 0;
            color:#334155;
            font-size:13px;
            line-height:1.65;
            border-top:1px solid #EEF2F7;
            padding-top:10px;
        }
        .ai-radar-core-badge {
            display:inline-flex;
            margin-top:11px;
            color:#92400E;
            background:#FFFBEB;
            border:1px solid #FDE68A;
            border-radius:999px;
            padding:5px 9px;
            font-size:12px;
            line-height:1.25;
            font-weight:820;
        }
        .ai-radar-ai-infra-card {
            margin:0 18px 16px;
            background:#F8FBFF;
            border-color:#DDE8F6;
        }
        .ai-radar-score-grid {
            display:grid;
            grid-template-columns:repeat(4, minmax(0, 1fr));
            gap:9px;
        }
        .ai-radar-data-quality-grid {
            display:grid;
            grid-template-columns:repeat(3, minmax(0, 1fr));
            gap:9px;
        }
        .ai-radar-score-grid div,
        .ai-radar-data-quality-grid div {
            background:#F8FAFC;
            border:1px solid #E8EEF5;
            border-radius:8px;
            padding:9px 10px;
        }
        .ai-radar-score-grid span,
        .ai-radar-data-quality-grid span {
            display:block;
            color:#64748B;
            font-size:11px;
            margin-bottom:4px;
        }
        .ai-radar-score-grid strong,
        .ai-radar-data-quality-grid strong {
            color:#0B1F3A;
            font-size:16px;
        }
        .ai-radar-data-health-summary {
            display:grid;
            grid-template-columns:repeat(3, minmax(0, 1fr));
            gap:9px;
            margin-bottom:10px;
        }
        .ai-radar-data-health-summary div {
            background:#F8FAFC;
            border:1px solid #E8EEF5;
            border-radius:8px;
            padding:9px 10px;
        }
        .ai-radar-data-health-summary span {
            display:block;
            color:#64748B;
            font-size:11px;
            font-weight:760;
            margin-bottom:4px;
        }
        .ai-radar-data-health-summary strong {
            display:block;
            color:#0B1F3A;
            font-size:13px;
            line-height:1.35;
            font-weight:850;
        }
        .ai-radar-data-health-details summary {
            cursor:pointer;
            color:#334155;
            font-size:12px;
            font-weight:820;
            margin:6px 0 8px;
        }
        .ai-radar-volume-summary {
            display:grid;
            grid-template-columns:repeat(3, minmax(0, 1fr));
            gap:9px;
            margin-bottom:10px;
        }
        .ai-radar-volume-summary div {
            background:#F8FAFC;
            border:1px solid #E8EEF5;
            border-radius:8px;
            padding:10px 11px;
        }
        .ai-radar-volume-summary span {
            display:block;
            color:#64748B;
            font-size:11px;
            font-weight:760;
            margin-bottom:5px;
        }
        .ai-radar-volume-summary strong {
            display:block;
            color:#0B1F3A;
            font-size:13px;
            line-height:1.35;
            font-weight:850;
        }
        .ai-radar-opinion-grid.two-col .ai-radar-card {
            padding:13px 14px;
        }
        .ai-radar-opinion-grid.two-col .ai-radar-section-title {
            margin-bottom:7px;
        }
        .ai-radar-opinion-grid.two-col .ai-radar-card ul {
            line-height:1.58;
        }
        .ai-radar-card ul {
            margin:0;
            padding-left:18px;
            color:#334155;
            font-size:13px;
            line-height:1.7;
        }
        .ai-radar-metric-table {
            width:100%;
            border-collapse:separate;
            border-spacing:0;
            font-size:12px;
        }
        .ai-radar-monitor-table {
            width:100%;
            border-collapse:collapse;
            font-size:12px;
        }
        .ai-radar-monitor-table th {
            color:#64748B;
            background:#F8FAFC;
            padding:8px 7px;
            text-align:left;
            border-top:1px solid #EEF2F7;
            border-bottom:1px solid #EEF2F7;
        }
        .ai-radar-monitor-table td {
            color:#24364D;
            padding:9px 7px;
            border-bottom:1px solid #EEF2F7;
            vertical-align:top;
            line-height:1.45;
        }
        .ai-radar-monitor-table td:first-child {
            color:#0B1F3A;
            font-weight:800;
        }
        .ai-radar-metric-table td {
            padding:9px 8px;
            border-top:1px solid #EEF2F7;
            line-height:1.35;
        }
        .ai-radar-metric-table tr:first-child td { border-top:1px solid #E2E8F0; }
        .ai-radar-metric-table td:first-child { color:#64748B; font-weight:760; width:52%; }
        .ai-radar-metric-table td:last-child {
            color:#0F172A;
            text-align:right;
            font-weight:820;
            font-variant-numeric:tabular-nums;
        }
        .ai-radar-empty-note { color:#64748B; font-size:13px; margin:0; }
        .ai-radar-report-foot {
            display:flex;
            gap:14px;
            flex-wrap:wrap;
            color:#64748B;
            font-size:12px;
            border-top:1px solid #E8EEF5;
            padding:12px 0 16px;
        }
        @media (max-width: 980px) {
            .ai-radar-research-header,
            .ai-radar-header-stats,
            .ai-radar-header-decision-grid,
            .ai-radar-research-grid,
            .ai-radar-opinion-grid,
            .ai-radar-visual-grid,
            .ai-radar-evidence-grid,
            .ai-radar-exec-grid,
            .ai-radar-batting-grid,
            .ai-radar-position-context-grid,
            .ai-radar-score-grid,
            .ai-radar-data-health-summary,
            .ai-radar-volume-summary,
            .ai-radar-data-quality-grid { grid-template-columns:1fr; }
            .ai-radar-research-header { padding:24px 18px; }
            .ai-radar-title-block h1 { font-size:42px; }
            .ai-radar-range-row { grid-template-columns:1fr; }
            .ai-radar-range-row b,
            .ai-radar-range-row em { text-align:left; }
        }
        /*
           Legacy selectors below are kept for old tests and debug blocks; the
           active page uses ai-radar-research-* classes above.
        */
        .ai-radar-list-card,
        .ai-radar-report {
            border: 1px solid #E2E8F0;
            background: #FFFFFF;
            border-radius: 8px;
            margin-top: 12px;
            overflow: hidden;
        }
        .ai-radar-section-head {
            display:flex;
            justify-content:space-between;
            align-items:center;
            padding:10px 14px;
            border-bottom:1px solid #E8EEF5;
        }
        .ai-radar-section-head strong { font-size:14px; color:#0F172A; }
        .ai-radar-section-head span { font-size:12px; color:#64748B; }
        .ai-radar-table-wrap { overflow-x:auto; }
        .ai-radar-table {
            width:100%;
            border-collapse:collapse;
            font-size:12px;
        }
        .ai-radar-table th {
            text-align:left;
            color:#64748B;
            background:#F8FAFC;
            padding:8px 10px;
            border-bottom:1px solid #E2E8F0;
            white-space:nowrap;
        }
        .ai-radar-table td {
            padding:8px 10px;
            border-bottom:1px solid #EEF2F7;
            color:#1E293B;
            vertical-align:top;
        }
        .ai-radar-table tr.active td { background:#F8FBFF; }
        .ai-radar-table a { color:#0F172A; font-weight:750; text-decoration:none; }
        .ai-radar-reasons { max-width:340px; color:#64748B !important; }
        .ai-radar-entry-ref {
            min-width:150px;
            max-width:230px;
            line-height:1.25;
        }
        .ai-radar-entry-ref strong {
            display:block;
            color:#0F172A;
            font-size:12px;
            font-weight:750;
        }
        .ai-radar-entry-ref span {
            display:block;
            color:#64748B;
            font-size:11px;
            margin-top:3px;
        }
        .ai-radar-decision {
            display:inline-flex;
            padding:2px 8px;
            border-radius:999px;
            background:#F1F5F9;
            border:1px solid #E2E8F0;
            font-weight:700;
            white-space:nowrap;
        }
        tr.allow .ai-radar-decision { background:#F0FDF4; color:#166534; border-color:#BBE5C6; }
        tr.block td { background:#FFF8F8; }
        tr.block .ai-radar-decision,
        tr.avoid .ai-radar-decision { background:#FFF1F2; color:#9F1239; border-color:#F4C7CE; }
        tr.missing td { background:#F8FAFC; }
        tr.missing .ai-radar-decision { background:#F1F5F9; color:#475569; border-color:#CBD5E1; }
        .ai-radar-report {
            max-width: 1080px;
            margin: 16px auto 0;
            padding: 14px;
        }
        .ai-radar-report-top {
            display:grid;
            grid-template-columns: repeat(4, minmax(140px, 1fr));
            gap:8px;
            margin-bottom:12px;
        }
        .ai-radar-report-top div,
        .ai-radar-card {
            border:1px solid #E2E8F0;
            background:#FBFCFE;
            border-radius:8px;
            padding:10px;
        }
        .ai-radar-report-top .wide { grid-column: 1 / -1; }
        .ai-radar-report-top span,
        .ai-radar-card h3,
        .ai-radar-score-grid span,
        .ai-radar-card.zones span {
            display:block;
            color:#64748B;
            font-size:12px;
            font-weight:650;
            margin:0 0 4px;
        }
        .ai-radar-report-top strong {
            color:#0F172A;
            font-size:15px;
            line-height:1.25;
        }
        .ai-radar-report.block { border-left:4px solid #B91C1C; }
        .ai-radar-report.allow { border-left:4px solid #15803D; }
        .ai-radar-report.wait,
        .ai-radar-report.missing { border-left:4px solid #D97706; }
        .ai-radar-report-grid {
            display:grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap:10px;
        }
        .ai-radar-card h3 {
            color:#0F172A;
            font-size:13px;
            margin-bottom:8px;
        }
        .ai-radar-score-grid {
            display:grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap:8px;
        }
        .ai-radar-score-grid div,
        .ai-radar-card.zones div {
            border-top:1px solid #E8EEF5;
            padding-top:8px;
        }
        .ai-radar-score-grid strong,
        .ai-radar-card.zones strong {
            color:#0F172A;
            font-size:16px;
        }
        .ai-radar-card ul {
            margin:0;
            padding-left:18px;
            color:#334155;
            font-size:13px;
            line-height:1.55;
        }
        .ai-radar-report-foot {
            display:flex;
            gap:14px;
            flex-wrap:wrap;
            color:#64748B;
            font-size:12px;
            margin-top:12px;
            border-top:1px solid #E8EEF5;
            padding-top:10px;
        }
        .ai-radar-debug {
            border:1px solid #E2E8F0;
            background:#FFFFFF;
            border-radius:8px;
            padding:10px;
        }
        .ai-radar-debug-summary {
            display:grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap:8px;
            margin-bottom:8px;
        }
        .ai-radar-debug-summary div {
            background:#F8FAFC;
            border:1px solid #E8EEF5;
            border-radius:6px;
            padding:8px;
        }
        .ai-radar-debug-summary span {
            display:block;
            font-size:11px;
            color:#64748B;
            margin-bottom:3px;
        }
        .ai-radar-debug-summary strong {
            color:#0F172A;
            font-size:12px;
            line-height:1.35;
        }
        .ai-radar-debug-note {
            border:1px solid #F3D19E;
            background:#FFFBEB;
            color:#78350F;
            border-radius:6px;
            padding:8px 10px;
            font-size:12px;
            line-height:1.4;
            margin:6px 0 8px;
        }
        .ai-radar-debug-table {
            width:100%;
            border-collapse:collapse;
            font-size:12px;
        }
        .ai-radar-debug-table th,
        .ai-radar-debug-table td {
            border-top:1px solid #E8EEF5;
            padding:7px 8px;
            text-align:left;
            vertical-align:top;
        }
        .ai-radar-debug-table th {
            color:#64748B;
            background:#F8FAFC;
            font-weight:700;
        }
        @media (max-width: 900px) {
            .ai-radar-report-top,
            .ai-radar-report-grid,
            .ai-radar-debug-summary { grid-template-columns:1fr; }
        }
        /* Keep the active research-view polish after legacy debug selectors. */
        .ai-radar-list-card {
            border:1px solid #D8E0EA;
            border-radius:10px;
            box-shadow:0 10px 28px rgba(15, 23, 42, 0.06);
        }
        .ai-radar-section-head {
            padding:12px 16px;
            background:#F8FAFC;
            border-bottom:1px solid #E6EDF5;
        }
        .ai-radar-section-head strong { font-size:15px; color:#0B1F3A; }
        .ai-radar-table th { padding:9px 12px; font-weight:700; }
        .ai-radar-table td { padding:10px 12px; vertical-align:middle; }
        .ai-radar-table tr:hover td { background:#F8FBFF; }
        .ai-radar-research-report .ai-radar-card,
        .ai-radar-research-report .ai-radar-summary-section {
            border:1px solid #E2E8F0;
            background:#FFFFFF;
            border-radius:10px;
            padding:14px;
        }
        .ai-radar-research-report .ai-radar-score-grid div,
        .ai-radar-research-report .ai-radar-data-quality-grid div {
            background:#F8FAFC;
            border:1px solid #E8EEF5;
            border-radius:8px;
            padding:9px 10px;
        }
        .ai-radar-research-report .ai-radar-report-foot {
            margin:14px 18px;
            padding:12px 0 16px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
