from __future__ import annotations

from html import escape
from types import SimpleNamespace

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from buy_zone import BuyZoneInputs, calculate_buy_zone_ladder
from buy_zone_engine import (
    BuyZoneEstimate,
    attach_combined_entry,
    buy_zone_with_manual_override,
    clear_buy_zone_override_values,
    generate_buy_zone,
    has_buy_zone_override,
)
from data.decision_log import save_decision_snapshot_from_bundle
from data.providers import get_market_data_provider
from data.stock_plan import StockPlanStore
from formatting import format_currency, format_percent
from indicators.technicals import add_technical_indicators, latest_technical_snapshot
from position_plan_engine import PositionPlanSuggestion, generate_position_plan
from scoring.final_decision_adapter import build_final_decision_bundle
from scoring.total_score import calculate_total_score
from settings import load_watchlist
from ui.metric_labels import action_label, confidence_label, model_type_label
from ui.theme import render_page_header, render_section_title


METHOD_LABELS = {
    "EPS 倍数法": "EPS multiple",
    "FCF 倍数法": "FCF multiple",
    "收入倍数法": "Revenue multiple",
}

ZONE_LABELS = {
    "invalid_zone": "买区异常",
    "invalid_manual_override": "买区异常",
    "low_confidence_zone": "需复核",
    "no_chase": "禁止追高",
    "fair_observation": "合理观察区",
    "tranche_buy": "可分批区",
    "heavy_buy": "极端恐慌区",
    "below_heavy_buy": "低于极端恐慌区",
    "data_insufficient": "数据不足",
    "unsupported_buy_zone_model": "模型不支持",
}
ZONE_TONES = {
    "invalid_zone": "red",
    "invalid_manual_override": "red",
    "low_confidence_zone": "orange",
    "no_chase": "red",
    "fair_observation": "blue",
    "tranche_buy": "green",
    "heavy_buy": "green",
    "below_heavy_buy": "green",
    "data_insufficient": "gray",
    "unsupported_buy_zone_model": "gray",
}
CONFIDENCE_TONES = {"high": "green", "medium": "blue", "low": "orange"}
SOURCE_LABELS = {
    "manual": "手动买区",
    "manual_override": "手动买区",
    "system": "系统建议",
    "system_generated": "系统建议",
    "mixed": "混合来源",
}
NEAR_TRIGGER_THRESHOLD_PCT = 15.0
LARGE_TRIGGER_DISTANCE_PCT = 30.0
FAIR_OBSERVATION_NOT_BUY_LABEL = "合理观察，未到估值买点"


def render() -> None:
    _render_styles()
    # Test anchor for the original system-plan page contract: 系统根据评分、估值、风险和技术位置自动生成买区
    render_page_header(
        "买区计划",
        "根据评分、估值、风险和技术位置生成买区，辅助执行加仓与等待策略。",
    )

    tickers = load_watchlist()
    if not tickers:
        st.warning("观察池为空，先到观察池添加股票。")
        _render_valuation_sandbox()
        return

    plan_store = StockPlanStore()
    load_notice = st.empty()
    load_notice.info(f"正在生成买区计划：{len(tickers)} 只观察池股票。首次加载会读取本地缓存和技术指标，请稍等。")
    with st.spinner("正在读取观察池、评分和买区计划..."):
        rows = _load_buy_zone_rows(tuple(tickers))
        rows = [_apply_manual_plan(row, plan_store.get_plan(str(row["symbol"]))) for row in rows]
    load_notice.empty()
    _handle_record_signal_query(rows, "buy_zone")
    _render_record_signal_notice("buy_zone_record_signal_notice")

    _render_summary(rows)
    active_filter = _render_execution_toolbar(rows)
    visible_rows = _filter_rows(rows, active_filter)
    _render_client_buy_zone_drawers(visible_rows)
    _render_buy_zone_table(rows, visible_rows, plan_store)
    _render_manual_and_advanced_settings(rows, plan_store)


@st.cache_data(ttl=600, show_spinner=False)
def _load_buy_zone_rows(tickers: tuple[str, ...]) -> list[dict]:
    provider = get_market_data_provider(full_fundamentals=False)
    rows: list[dict] = []
    for ticker in tickers:
        symbol = str(ticker).upper()
        try:
            snapshot = provider.get_quote(symbol, force_refresh=False)
            history = add_technical_indicators(provider.get_price_history(symbol, force_refresh=False))
            technicals = latest_technical_snapshot(history)
            score = calculate_total_score(snapshot, technicals)
            stock_data = {**snapshot, **technicals, "price_history": history}
            if not _valid_price(stock_data.get("price") or stock_data.get("current_price")):
                stock_data["price"] = _first_number(technicals.get("price"), snapshot.get("current_price"))
            zone = generate_buy_zone(symbol, stock_data, score, score.scoring_model)
            plan = generate_position_plan(symbol, zone, score)
            rows.append(_row_from_outputs(symbol, snapshot, technicals, score, zone, plan, "system_generated", False))
        except Exception as exc:
            rows.append(_error_row(symbol, str(exc)))
    return rows


def _apply_manual_plan(row: dict, plan: dict) -> dict:
    system_zone: BuyZoneEstimate = row["systemZone"]
    active_zone = buy_zone_with_manual_override(system_zone, plan)
    score = row["score"]
    plan_suggestion = generate_position_plan(str(row["symbol"]), active_zone, score)
    source = "manual_override" if has_buy_zone_override(plan) else "system_generated"
    decision_fields = _final_decision_fields(score, system_zone, manual_plan_override=plan, symbol=str(row["symbol"]))
    active_zone = attach_combined_entry(active_zone, SimpleNamespace(**decision_fields))
    updated = dict(row)
    updated.update(_zone_plan_fields(active_zone, plan_suggestion, source, has_buy_zone_override(plan)))
    updated.update(decision_fields)
    updated["activeZone"] = active_zone
    updated["positionPlan"] = plan_suggestion
    return updated


def _row_from_outputs(
    symbol: str,
    snapshot: dict,
    technicals: dict,
    score,
    zone: BuyZoneEstimate,
    plan: PositionPlanSuggestion,
    source: str,
    manual: bool,
) -> dict:
    price = _first_number(zone.currentPrice, technicals.get("price"), snapshot.get("current_price"))
    base = {
        "symbol": symbol,
        "companyName": snapshot.get("company_name") or snapshot.get("companyName") or symbol,
        "currentPrice": price,
        "marketCap": snapshot.get("market_cap"),
        "action": getattr(score, "action", ""),
        "qualityRating": getattr(score, "quality_rating", ""),
        "entryRating": getattr(score, "entry_rating", ""),
        "riskRating": getattr(score, "risk_rating", ""),
        "valuationStatus": getattr(score, "valuation_status", ""),
        "dataConfidence": getattr(score, "data_confidence", "low"),
        "modelType": getattr(score, "scoring_model", zone.modelType),
        "score": score,
        "systemZone": zone,
        "activeZone": zone,
        "positionPlan": plan,
        "rawSnapshot": snapshot,
        "rawTechnicals": technicals,
    }
    decision_fields = _final_decision_fields(score, zone, plan)
    zone = attach_combined_entry(zone, SimpleNamespace(**decision_fields))
    base["systemZone"] = zone
    base["activeZone"] = zone
    base.update(_zone_plan_fields(zone, plan, source, manual))
    base.update(decision_fields)
    return base


def _final_decision_fields(
    score,
    zone: BuyZoneEstimate,
    plan: PositionPlanSuggestion | None = None,
    *,
    manual_plan_override: dict | None = None,
    symbol: str | None = None,
) -> dict:
    decision = build_final_decision_bundle(
        score,
        zone,
        plan,
        manual_plan_override=manual_plan_override,
        symbol=symbol,
    )
    return {
        "finalAction": decision.finalAction,
        "decisionLane": decision.decisionLane,
        "displayCategory": decision.displayCategory,
        "isActionable": decision.isActionable,
        "decisionBlockReasons": decision.blockReasons,
        "decisionReviewReasons": decision.reviewReasons,
        "currentAddLimitPercent": decision.currentAddLimitPercent,
        "maxPortfolioWeightPercent": decision.maxPortfolioWeightPercent,
        "dataConfidence": decision.dataConfidence,
    }


def _zone_plan_fields(zone: BuyZoneEstimate, plan: PositionPlanSuggestion, source: str, manual: bool) -> dict:
    return {
        "currentZone": zone.currentZone,
        "zoneLabel": _zone_label(zone.currentZone),
        "noChaseAbove": zone.noChaseAbove,
        "fairValueLow": zone.fairValueLow,
        "fairValueHigh": zone.fairValueHigh,
        "trancheBuyLow": zone.trancheBuyLow,
        "trancheBuyHigh": zone.trancheBuyHigh,
        "heavyBuyBelow": zone.heavyBuyBelow,
        "nextBuyPrice": plan.firstBuyPrice,
        "nextTriggerPrice": getattr(zone, "nextTriggerPrice", None),
        "nextBuyLabel": getattr(zone, "nextBuyLabel", ""),
        "currentAddLimitPercent": plan.currentAddLimitPercent,
        "maxPortfolioWeightPercent": plan.maxPortfolioWeightPercent,
        "confidence": zone.confidence,
        "method": zone.method,
        "inputsUsed": zone.inputsUsed,
        "keyReasons": zone.keyReasons,
        "warnings": zone.warnings,
        "validationErrors": list(getattr(zone, "validationErrors", None) or []),
        "explainability": getattr(zone, "explainability", None) or {},
        "technicalEntry": getattr(zone, "technicalEntry", None) or {},
        "combinedEntry": getattr(zone, "combinedEntry", None) or {},
        "isValid": bool(getattr(zone, "isValid", True)),
        "buyZoneSource": source,
        "manualOverride": manual,
        "firstBuyPrice": plan.firstBuyPrice,
        "secondBuyPrice": plan.secondBuyPrice,
        "thirdBuyPrice": plan.thirdBuyPrice,
        "stopAddingCondition": plan.stopAddingCondition,
        "thesisBreakCondition": plan.thesisBreakCondition,
        "earningsReviewCondition": plan.earningsReviewCondition,
    }


def _error_row(symbol: str, error: str) -> dict:
    score = SimpleNamespace(
        action="数据不足，需复核",
        quality_rating="数据不足",
        entry_rating="数据不足",
        risk_rating="数据不足",
        valuation_status="数据不足",
        data_confidence="low",
        scoring_model="GENERIC",
    )
    zone = BuyZoneEstimate(
        symbol=symbol,
        modelType="GENERIC",
        currentPrice=None,
        noChaseAbove=None,
        fairValueLow=None,
        fairValueHigh=None,
        trancheBuyLow=None,
        trancheBuyHigh=None,
        heavyBuyBelow=None,
        currentZone="data_insufficient",
        confidence="low",
        method="technical_proxy",
        inputsUsed=[],
        keyReasons=["价格或核心数据缺失，暂时不能生成有效买区。"],
        warnings=[error],
        createdAt="",
    )
    plan = generate_position_plan(symbol, zone, score)
    return _row_from_outputs(symbol, {}, {}, score, zone, plan, "system_generated", False)


def _render_summary(rows: list[dict]) -> None:
    summary = {
        "可执行": (
            sum(1 for row in rows if resolve_buy_zone_display_category(row)["displayCategory"] == "可执行"),
            "当前已进入可分批区，可按计划小仓执行",
        ),
        "接近买区": (
            sum(1 for row in rows if resolve_buy_zone_display_category(row)["displayCategory"] == "接近买区"),
            "距离触发价较近，等待回踩",
        ),
        "等回踩": (
            sum(1 for row in rows if resolve_buy_zone_display_category(row)["displayCategory"] == "等回踩"),
            "估值未到买区，先观察",
        ),
        "禁止追高": (
            sum(1 for row in rows if resolve_buy_zone_display_category(row)["displayCategory"] == "禁止追高"),
            "当前价格不适合新增",
        ),
        "需复核": (
            sum(1 for row in rows if resolve_buy_zone_display_category(row)["displayCategory"] == "需复核"),
            "买区异常或数据置信度低",
        ),
    }
    cards = "".join(
        f'<div class="buy-zone-summary-card"><span>{escape(label)}</span><strong>{value}</strong><em>{escape(note)}</em></div>'
        for label, (value, note) in summary.items()
    )
    st.markdown(f'<section class="buy-zone-summary">{cards}</section>', unsafe_allow_html=True)


def _priority_rows_html(rows: list[dict]) -> str:
    candidates: list[tuple[str, str, str, str, str]] = []
    groups = [
        ("可执行", "green", [row for row in rows if resolve_buy_zone_display_category(row)["displayCategory"] == "可执行"]),
        ("接近", "blue", [row for row in rows if resolve_buy_zone_display_category(row)["priorityEligible"]]),
        ("复核", "amber", [row for row in rows if resolve_buy_zone_display_category(row)["displayCategory"] == "需复核"]),
        ("禁追", "red", [row for row in rows if resolve_buy_zone_display_category(row)["displayCategory"] == "禁止追高"]),
    ]
    for label, tone, group_rows in groups:
        for row in group_rows[:2]:
            primary, secondary = _priority_text(row, label)
            candidates.append((label, tone, str(row["symbol"]), primary, secondary))
            if len(candidates) >= 5:
                break
        if len(candidates) >= 5:
            break

    if not candidates:
        return '<div class="priority-empty">暂无明确可执行机会，优先等待回踩或复核数据。</div>'

    return "".join(
        '<div class="priority-row">'
        f'<i class="priority-dot {escape(tone)}" aria-hidden="true"></i>'
        '<div class="priority-copy">'
        f'<strong>{escape(symbol)}</strong>'
        f'<span>{escape(primary)}</span>'
        "</div>"
        "</div>"
        for label, tone, symbol, primary, _secondary in candidates
    )


def _priority_text(row: dict, label: str) -> tuple[str, str]:
    if label == "可执行":
        return f"{_current_add_text(row)[0]} {_action_short_text(row)}", str(row.get("zoneLabel") or "已进入买区")
    if label == "接近":
        return "接近买区", _distance_to_trigger_secondary(row)
    if label == "复核":
        return "需复核", _trigger_cell_detail(row)
    return "不新增", _trigger_cell_detail(row)


def _render_filters(rows: list[dict]) -> str:
    options = ["全部", "可执行", "接近", "等回踩", "禁止追高", "需复核", "手动"]
    return st.radio("买区筛选", options, horizontal=True, label_visibility="collapsed", key="buy-zone-filter")


def _render_execution_toolbar(rows: list[dict]) -> str:
    st.markdown('<div class="buy-zone-filter-toolbar-marker"></div>', unsafe_allow_html=True)
    title_col, filter_col = st.columns([1.15, 4.1], gap="small")
    with title_col:
        st.markdown(
            """
            <div class="execution-toolbar-title">
              <strong>买区执行台</strong>
              <span>只保留执行判断，完整买区进入详情。</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with filter_col:
        return _render_filters(rows)


def _filter_rows(rows: list[dict], active_filter: str) -> list[dict]:
    status_filter_map = {
        "可执行": "可执行",
        "接近": "接近买区",
        "等回踩": "等回踩",
        "禁止追高": "禁止追高",
        "需复核": "需复核",
    }
    if active_filter in status_filter_map:
        return [row for row in rows if resolve_buy_zone_display_category(row)["displayCategory"] == status_filter_map[active_filter]]
    if active_filter == "手动":
        return [row for row in rows if row["manualOverride"]]
    return rows


def _handle_record_signal_query(rows: list[dict], page_key: str) -> None:
    symbol = str(st.query_params.get("recordSignal", "")).strip().upper()
    if not symbol:
        return
    row = next((item for item in rows if str(item.get("symbol") or "").upper() == symbol), None)
    if row:
        save_decision_snapshot_from_bundle(symbol, row.get("currentPrice"), _decision_bundle_from_row(row), page_key)
        st.session_state["buy_zone_record_signal_notice"] = "已记录系统信号。"
    else:
        st.session_state["buy_zone_record_signal_notice"] = "未找到要记录的系统信号。"
    if "recordSignal" in st.query_params:
        st.query_params.pop("recordSignal")
    st.rerun()


def _render_record_signal_notice(key: str) -> None:
    message = st.session_state.pop(key, "")
    if message == "已记录系统信号。":
        st.success(message)
    elif message:
        st.warning(message)


def _decision_bundle_from_row(row: dict) -> dict:
    return {
        "finalAction": row.get("finalAction"),
        "decisionLane": row.get("decisionLane"),
        "currentAddLimitPercent": row.get("currentAddLimitPercent"),
        "maxPortfolioWeightPercent": row.get("maxPortfolioWeightPercent"),
        "dataConfidence": row.get("dataConfidence"),
        "displayCategory": row.get("displayCategory"),
        "blockReasons": row.get("decisionBlockReasons") or [],
        "reviewReasons": row.get("decisionReviewReasons") or [],
    }


def _render_buy_zone_table(rows: list[dict], visible_rows: list[dict], plan_store: StockPlanStore) -> None:
    header = """
    <div class="buy-zone-grid buy-zone-grid-head">
      <span>股票</span><span>当前动作</span><span>触发条件</span><span>建议仓位</span><span>置信度</span><span>操作</span>
    </div>
    """
    body = (
        "".join(_buy_zone_row_html(row) for row in visible_rows)
        if visible_rows
        else '<div class="buy-zone-empty">当前筛选下没有股票。</div>'
    )
    st.markdown(
        f"""
        <section class="execution-console-panel">
          <div class="priority-strip">
            <div class="priority-strip-head"><strong>今日优先</strong><span>按执行价值和风险优先级排序</span></div>
            <div class="priority-list">{_priority_rows_html(rows)}</div>
          </div>
          <div id="buy-zone-table" class="buy-zone-table">{header}{body}</div>
          <div class="execution-console-foot">点击“查看”打开禁追价、极端恐慌区、校验提醒、估值输入和手动覆盖。</div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _buy_zone_row_html(row: dict) -> str:
    symbol = str(row["symbol"])
    drawer_id = _buy_zone_drawer_id(symbol)
    display = resolve_buy_zone_display_category(row)
    status = str(display["displayCategory"])
    add_text, _ = _current_add_text(row)
    position_text = f"新增 {add_text}" if add_text.startswith("≤") else add_text
    trigger_primary = str(display["triggerPrimary"])
    trigger_secondary = str(display["triggerSecondary"])
    trigger_tone = str(display.get("triggerTone") or "neutral")
    status_note = _status_detail_text(row)
    return (
        '<div class="buy-zone-grid buy-zone-row">'
        f'<div class="stock-cell"><strong>{escape(symbol)}</strong><span>{escape(_price_text(row.get("currentPrice")))}</span></div>'
        f'<div class="status-cell">{_badge(status, _execution_tone(status))}<small>{escape(status_note)}</small></div>'
        f'{_buy_zone_trigger_cell_html(row, trigger_primary, trigger_secondary, trigger_tone)}'
        f'<div class="position-cell"><b>{escape(position_text)}</b><small>上限 {_pct_limit(row.get("maxPortfolioWeightPercent"))}</small></div>'
        f'<div class="confidence-cell">{_confidence_inline(row.get("confidence"))}</div>'
        '<div class="buy-zone-row-actions">'
        f'<a class="buy-zone-detail-link" href="#{escape(drawer_id, quote=True)}" data-buy-zone-drawer-open="{escape(symbol, quote=True)}"><span>查看</span><i>›</i></a>'
        f'<a class="buy-zone-record-link" href="?page=buy-zone&recordSignal={escape(symbol, quote=True)}" target="_self">记录</a>'
        "</div>"
        "</div>"
    )


def _render_client_buy_zone_drawers(rows: list[dict]) -> None:
    if not rows:
        return
    drawers = "".join(_buy_zone_drawer_html(row) for row in rows)
    st.markdown(f'<div class="buy-zone-drawer-root">{drawers}</div>', unsafe_allow_html=True)


def _buy_zone_trigger_cell_html(row: dict, trigger_primary: str, trigger_secondary: str, trigger_tone: str) -> str:
    technical = _technical_trigger_summary(row)
    combined = _combined_trigger_summary(row)
    return (
        f'<div class="trigger-cell {escape(trigger_tone)}">'
        f'<b>{escape(trigger_primary)}</b>'
        f'<small>{escape("估值买点：" + trigger_secondary)}</small>'
        f'<em>{escape(combined)}</em>'
        f'<em>{escape(technical)}</em>'
        "</div>"
    )


def _combined_entry_for_row(row: dict) -> dict:
    combined = row.get("combinedEntry")
    if isinstance(combined, dict) and combined:
        return dict(combined)
    active_zone = row.get("activeZone")
    combined = getattr(active_zone, "combinedEntry", None) if active_zone is not None else None
    return dict(combined) if isinstance(combined, dict) else {}


def _combined_trigger_summary(row: dict) -> str:
    combined = _combined_entry_for_row(row)
    label = str(combined.get("entryLabel") or "等待估值买点")
    trigger = _technical_money(combined.get("combinedTriggerPrice"))
    if trigger == "未设置":
        return f"综合触发：不生成入场触发（{label}）"
    return f"综合触发：{trigger}（{label}）"


def _technical_entry_for_row(row: dict) -> dict:
    technical = row.get("technicalEntry")
    technical = dict(technical) if isinstance(technical, dict) else {}
    zone = str(row.get("currentZone") or "")
    lane = str(row.get("decisionLane") or "")
    action = _row_action(row)
    technical["__buyZoneBlocked"] = (
        zone in {"no_chase", "invalid_zone", "invalid_manual_override", "data_insufficient", "low_confidence_zone", "unsupported_buy_zone_model"}
        or lane == "blocked"
        or action == "禁止追高"
        or row.get("confidence") == "low"
        or row.get("dataConfidence") == "low"
    )
    return technical


def _technical_trigger_summary(row: dict) -> str:
    technical = _technical_entry_for_row(row)
    state = str(technical.get("technicalState") or "unavailable")
    confidence = str(technical.get("technicalConfidence") or "low")
    zone = str(row.get("currentZone") or "")
    lane = str(row.get("decisionLane") or "")
    action = _row_action(row)
    blocked = (
        state in {"unavailable", "insufficient_data"}
        or confidence == "low"
        or zone in {"no_chase", "invalid_zone", "invalid_manual_override", "data_insufficient", "low_confidence_zone", "unsupported_buy_zone_model"}
        or lane == "blocked"
        or action == "禁止追高"
        or row.get("confidence") == "low"
        or row.get("dataConfidence") == "low"
    )
    if state in {"unavailable", "insufficient_data"} or confidence == "low":
        return "技术：数据不足，暂不生成"
    review = _technical_money(technical.get("technicalReviewPrice"))
    if blocked or state == "trend_break_review":
        if review != "未设置":
            return f"技术：复核 {review}，不转买点"
        return "技术：不转买点"
    entry = _technical_money(technical.get("technicalEntryPrice"))
    no_chase = _technical_money(technical.get("technicalNoChaseAbove"))
    parts = []
    if entry != "未设置":
        parts.append(f"回踩 {entry}")
    if review != "未设置":
        parts.append(f"复核 {review}")
    if no_chase != "未设置":
        parts.append(f"不追高 {no_chase}")
    return "技术：" + " / ".join(parts[:2]) if parts else "技术：数据不足，暂不生成"


def _buy_zone_drawer_id(symbol: str) -> str:
    safe = "".join(ch for ch in str(symbol).upper() if ch.isalnum() or ch in {"-", "_"})
    return f"buy-zone-drawer-{safe or 'stock'}"


def _buy_zone_drawer_html(row: dict) -> str:
    symbol = str(row["symbol"])
    drawer_id = _buy_zone_drawer_id(symbol)
    zone: BuyZoneEstimate = row["activeZone"]
    system_zone: BuyZoneEstimate = row["systemZone"]
    reasons = "".join(f"<li>{escape(_humanize_buy_zone_explain_item(reason))}</li>" for reason in (row.get("keyReasons") or [])[:6])
    warnings = "".join(f"<li>{escape(_humanize_buy_zone_explain_item(warning))}</li>" for warning in (row.get("warnings") or [])[:5])
    validation_errors = "".join(f"<li>{escape(_humanize_buy_zone_explain_item(error))}</li>" for error in (row.get("validationErrors") or [])[:5])
    return (
        f'<section id="{escape(drawer_id, quote=True)}" class="buy-zone-drawer-shell">'
        '<a class="buy-zone-drawer-backdrop" href="#buy-zone-table" aria-label="关闭买区详情"></a>'
        '<aside class="stock-drawer buy-zone-drawer">'
        '<a class="drawer-close-link" href="#buy-zone-table" title="关闭">×</a>'
        '<div class="drawer-topline">买区详情</div>'
        f'<div class="drawer-head"><div><div class="drawer-symbol">{escape(symbol)}</div>'
        f'<div class="drawer-company">{escape(str(row.get("companyName") or ""))}</div></div>'
        f'<div class="drawer-price">{escape(_price_text(row.get("currentPrice")))}</div></div>'
        '<div class="drawer-badges">'
        f'{_badge(row["zoneLabel"], ZONE_TONES.get(row["currentZone"], "gray"))}'
        f'{_badge(action_label(_row_action(row)), _action_tone(_row_action(row)))}'
        f'{_badge(model_type_label(row.get("modelType")), "gray")}'
        f'{_badge(_source_label(row.get("buyZoneSource"), row.get("manualOverride")), "blue" if row.get("manualOverride") else "gray")}'
        "</div>"
        '<div class="drawer-position-card">'
        '<div class="drawer-card-title">顶部结论</div>'
        f'<div class="drawer-decision-headline">当前处于 {escape(row["zoneLabel"])}，当前新增建议 {_pct_limit(row.get("currentAddLimitPercent"))}，组合仓位上限 {_pct_limit(row.get("maxPortfolioWeightPercent"))}。</div>'
        "</div>"
        '<div class="drawer-section-title">系统建议买区</div>'
        f'{_zone_snapshot_html(system_zone)}'
        '<div class="drawer-section-title">当前使用买区</div>'
        f'{_price_ladder_html(row)}'
        '<div class="drawer-section-title">买区解释</div>'
        f'{_buy_zone_explainability_html(row)}'
        '<div class="drawer-section-title">综合入场参考</div>'
        f'{_combined_entry_html(_combined_entry_for_row(row))}'
        '<div class="drawer-section-title">技术面辅助</div>'
        f'{_technical_entry_html(_technical_entry_for_row(row))}'
        '<div class="drawer-section-title">生成依据</div>'
        f'<div class="drawer-resolution"><b>输入</b><ul>{"".join(f"<li>{escape(str(item))}</li>" for item in row.get("inputsUsed", [])[:8]) or "<li>暂无可用输入</li>"}</ul></div>'
        f'<div class="drawer-resolution"><b>原因</b><ul>{reasons or "<li>暂无说明</li>"}</ul></div>'
        f'<div class="drawer-resolution"><b>提醒</b><ul>{warnings or "<li>暂无重大提醒</li>"}</ul></div>'
        f'<div class="drawer-resolution"><b>校验</b><ul>{validation_errors or "<li>暂无校验错误</li>"}</ul></div>'
        '<div class="drawer-section-title">操作计划</div>'
        f'{_plan_html(row)}'
        '<div class="drawer-section-title">手动覆盖</div>'
        '<div class="drawer-muted">手动覆盖优先于系统建议。编辑和保存可在单股详情页完成；本页可快速恢复系统建议。</div>'
        "</aside>"
        "</section>"
    )


def _price_ladder_html(row: dict) -> str:
    bands = [
        ("禁止追高", row.get("noChaseAbove")),
        ("合理观察区", f"{_money(row.get('fairValueLow'))} - {_money(row.get('fairValueHigh'))}"),
        ("估值折价区", f"{_money(row.get('trancheBuyLow'))} - {_money(row.get('trancheBuyHigh'))}"),
        ("极端恐慌区", row.get("heavyBuyBelow")),
    ]
    items = "".join(f"<li><span>{escape(label)}</span><b>{escape(_money(value) if not isinstance(value, str) else value)}</b></li>" for label, value in bands)
    return f'<div class="price-ladder"><ul>{items}</ul><div class="price-marker">当前价格：{escape(_money(row.get("currentPrice")))}</div></div>'


def _zone_snapshot_html(zone: BuyZoneEstimate) -> str:
    items = [
        ("当前区间", _zone_label(zone.currentZone)),
        ("触发条件", _zone_next_trigger_text(zone)),
        ("禁止追高", _optional_money(zone.noChaseAbove)),
        ("合理观察区", f"{_optional_money(zone.fairValueLow)} - {_optional_money(zone.fairValueHigh)}"),
        ("估值折价区", f"{_optional_money(zone.trancheBuyLow)} - {_optional_money(zone.trancheBuyHigh)}"),
        ("极端恐慌区", _optional_money(zone.heavyBuyBelow)),
        ("置信度", confidence_label(zone.confidence)),
    ]
    html = "".join(f"<li><span>{escape(label)}</span><b>{escape(value)}</b></li>" for label, value in items)
    return f'<div class="drawer-resolution plan-list"><ul>{html}</ul></div>'


def _buy_zone_explainability_html(row: dict) -> str:
    explain = _normalized_buy_zone_explainability(row)
    blocks = [
        ("主因", explain["mainDrivers"]),
        ("守门原因", explain["guardrailReasons"]),
        ("缺失输入", explain["missingInputs"]),
        ("置信度", explain["confidenceReasons"]),
    ]
    visible_blocks = [(title, items) for title, items in blocks if items]
    block_html = "".join(
        '<div class="drawer-explain-block">'
        f"<b>{escape(title)}</b>"
        f"<ul>{_explain_items_html(items)}</ul>"
        "</div>"
        for title, items in visible_blocks
    )
    grid_html = f'<div class="drawer-explain-grid">{block_html}</div>' if block_html else ""
    return (
        '<div class="drawer-explainability">'
        f'<strong>{escape(explain["explainTitle"])}</strong>'
        f'<p>{escape(explain["explainSummary"])}</p>'
        f"{grid_html}"
        "</div>"
    )


def _normalized_buy_zone_explainability(row: dict) -> dict[str, object]:
    explain = row.get("explainability")
    if not isinstance(explain, dict) or not explain:
        active_zone = row.get("activeZone")
        explain = getattr(active_zone, "explainability", None) if active_zone is not None else None
    if not isinstance(explain, dict) or not explain:
        explain = _fallback_buy_zone_explainability(row)
    fallback = _fallback_buy_zone_explainability(row)
    return {
        "explainTitle": str(explain.get("explainTitle") or fallback["explainTitle"]),
        "explainSummary": str(explain.get("explainSummary") or fallback["explainSummary"]),
        "mainDrivers": _explain_list(explain.get("mainDrivers")) or fallback["mainDrivers"],
        "guardrailReasons": _explain_list(explain.get("guardrailReasons")) or fallback["guardrailReasons"],
        "missingInputs": _explain_list(explain.get("missingInputs")),
        "confidenceReasons": _explain_list(explain.get("confidenceReasons")) or fallback["confidenceReasons"],
    }


def _fallback_buy_zone_explainability(row: dict) -> dict[str, object]:
    zone = str(row.get("currentZone") or "")
    confidence = str(row.get("confidence") or row.get("dataConfidence") or "low")
    inputs = _explain_list(row.get("inputsUsed"))
    warnings = _explain_list(row.get("warnings"))
    validation_errors = _explain_list(row.get("validationErrors"))
    fallback_by_zone = {
        "unsupported_buy_zone_model": ("买区模型暂不支持", "当前板块暂无专属买区模型，系统不输出精确买点。"),
        "data_insufficient": ("买区数据不足", "关键输入缺失，暂时不能生成精确买点。"),
        "low_confidence_zone": ("买区置信度不足", "数据置信度偏低，暂不把买区作为入场信号。"),
        "invalid_zone": ("买区输入需复核", "买区区间或输入异常，系统暂不输出买点。"),
        "invalid_manual_override": ("手动买区需复核", "手动覆盖后的区间异常，暂不输出入场买点。"),
        "no_chase": ("当前不追高", "当前价格高于系统买区上沿，不建议新增。"),
    }
    title, summary = fallback_by_zone.get(zone, ("系统买区已生成", f"当前状态为 {_zone_label(zone)}，买点解释来自系统买区结果。"))
    guardrails = validation_errors or warnings
    if not guardrails and zone in {"no_chase", "data_insufficient", "low_confidence_zone", "unsupported_buy_zone_model", "invalid_zone", "invalid_manual_override"}:
        guardrails = [summary]
    missing: list[str] = []
    if zone == "unsupported_buy_zone_model":
        missing = ["专属买区模型"]
    elif "缺少发电模型核心输入" in validation_errors or "缺少发电模型核心输入" in warnings:
        missing = ["发电模型核心输入：adjusted EBITDA / adjusted FCF before growth"]
    elif zone == "data_insufficient":
        missing = ["价格、估值或技术输入不足"]
    return {
        "explainTitle": title,
        "explainSummary": summary,
        "mainDrivers": inputs or [f"当前价格：{_money(row.get('currentPrice'))}"],
        "guardrailReasons": guardrails,
        "missingInputs": missing,
        "confidenceReasons": [f"买区置信度：{confidence_label(confidence)}"],
    }


def _explain_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [_humanize_buy_zone_explain_item(item) for item in value if _humanize_buy_zone_explain_item(item)]
    if value is None or value == "":
        return []
    item = _humanize_buy_zone_explain_item(value)
    return [item] if item else []


def _explain_items_html(items: list[str]) -> str:
    return "".join(f"<li>{escape(str(item))}</li>" for item in items[:6])


def _combined_entry_html(entry: object) -> str:
    combined = entry if isinstance(entry, dict) else {}
    label = str(combined.get("entryLabel") or "等待估值买点")
    trigger = _technical_money(combined.get("combinedTriggerPrice"))
    trigger_text = "不生成入场触发" if trigger == "未设置" else trigger
    items = [
        ("结论", label),
        ("估值买点", _technical_money(combined.get("valuationEntryPrice"))),
        ("技术回踩点", _technical_money(combined.get("technicalPullbackPrice"))),
        ("轻仓试探点", _technical_money(combined.get("lightProbePrice"))),
        ("综合触发价", trigger_text),
        ("深度折价区", _technical_money(combined.get("deepDiscountPrice"))),
        ("复核线", _technical_money(combined.get("reviewPrice"))),
    ]
    metrics = "".join(
        f'<li><span>{escape(name)}</span><b>{escape(value)}</b></li>'
        for name, value in items
        if value and value != "未设置"
    )
    reasons = [str(item) for item in combined.get("entryReasons") or [] if str(item).strip()]
    if not reasons:
        reasons = ["综合入场参考只合并估值买点和技术回踩点，不覆盖最终结论。"]
    reason_html = "".join(f"<li>{escape(reason)}</li>" for reason in reasons[:6])
    return (
        '<div class="drawer-technical-entry">'
        f"<strong>{escape(label)}</strong>"
        "<p>合理观察、技术回踩、轻仓试探、估值折价和深度折价分层显示；禁止追高、等回踩、阻断或低置信状态不会生成可买触发。</p>"
        f'<div class="drawer-technical-grid"><ul>{metrics}</ul></div>'
        f'<div class="drawer-technical-reasons"><b>综合说明</b><ul>{reason_html}</ul></div>'
        "</div>"
    )


def _technical_entry_html(entry: object) -> str:
    technical = entry if isinstance(entry, dict) else {}
    buy_zone_blocked = bool(technical.get("__buyZoneBlocked"))
    confidence = str(technical.get("technicalConfidence") or "low")
    state = str(technical.get("technicalState") or "unavailable")
    trend = str(technical.get("technicalTrend") or "unavailable")
    unavailable = state in {"unavailable", "insufficient_data"} or confidence == "low"
    review_only = unavailable or buy_zone_blocked or state == "trend_break_review"
    title = "技术数据不足" if unavailable else "技术入场参考"
    summary = (
        "技术层只做辅助观察，不覆盖估值买点；当前数据不足，不生成技术建议。"
        if unavailable
        else "技术回踩点用于辅助择时，估值折价区和极端恐慌区仍以买区模型为准。"
    )
    metric_items = [
        ("技术状态", _technical_state_label(state)),
        ("趋势", _technical_trend_label(trend)),
        ("MA20", _technical_money(technical.get("ma20"))),
        ("MA50", _technical_money(technical.get("ma50"))),
        ("MA200", _technical_money(technical.get("ma200"))),
        ("RSI14", _technical_number(technical.get("rsi14"), 1)),
        ("ATR14", _technical_money(technical.get("atr14"))),
        ("技术回踩点", "不生成建议" if review_only else _technical_money(technical.get("technicalEntryPrice"))),
        ("技术复核线", "不生成建议" if unavailable else _technical_money(technical.get("technicalReviewPrice"))),
        ("技术不追高线", "不生成建议" if review_only else _technical_money(technical.get("technicalNoChaseAbove"))),
        ("关键支撑", _technical_levels_text(technical.get("supportLevels"))),
        ("关键压力", _technical_levels_text(technical.get("resistanceLevels"))),
    ]
    metrics = "".join(
        f'<li><span>{escape(label)}</span><b>{escape(value)}</b></li>'
        for label, value in metric_items
        if value and value != "未设置"
    )
    reasons = _technical_reasons_list(technical, unavailable, review_only)
    reason_html = "".join(f"<li>{escape(reason)}</li>" for reason in reasons[:6])
    return (
        '<div class="drawer-technical-entry">'
        f"<strong>{escape(title)}</strong>"
        f"<p>{escape(summary)}</p>"
        f'<div class="drawer-technical-grid"><ul>{metrics}</ul></div>'
        f'<div class="drawer-technical-reasons"><b>技术说明</b><ul>{reason_html}</ul></div>'
        "</div>"
    )


def _technical_state_label(value: object) -> str:
    return {
        "short_term_extended": "短期偏热，避免追价",
        "healthy_pullback": "健康回踩",
        "trend_break_review": "趋势破坏，需复核",
        "tactical_observation": "战术观察",
        "neutral": "中性等待",
        "insufficient_data": "技术数据不足",
        "unavailable": "技术数据不足",
    }.get(str(value or ""), "技术数据不足")


def _technical_trend_label(value: object) -> str:
    return {
        "uptrend": "上升趋势",
        "pullback_in_uptrend": "上升趋势中的回踩",
        "broken_trend": "趋势破坏",
        "downtrend": "下降趋势",
        "sideways": "横盘震荡",
        "insufficient_data": "数据不足",
        "unavailable": "数据不足",
    }.get(str(value or ""), "数据不足")


def _technical_money(value: object) -> str:
    number = _first_number(value)
    return format_currency(number) if number is not None and number > 0 else "未设置"


def _technical_number(value: object, digits: int = 1) -> str:
    number = _first_number(value)
    return f"{number:.{digits}f}" if number is not None else "未设置"


def _technical_levels_text(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "未设置"
    parts = []
    for level in value[:2]:
        if not isinstance(level, dict):
            continue
        price = _technical_money(level.get("price"))
        if price == "未设置":
            continue
        label = str(level.get("label") or "技术位")
        distance = _first_number(level.get("distancePct"))
        suffix = f" ({distance:+.1f}%)" if distance is not None else ""
        parts.append(f"{label} {price}{suffix}")
    return " / ".join(parts) if parts else "未设置"


def _technical_reasons_list(technical: dict, unavailable: bool, review_only: bool = False) -> list[str]:
    raw = [str(item) for item in technical.get("technicalReasons") or [] if str(item).strip()]
    if unavailable:
        return ["技术数据不足，不生成技术回踩建议。", "技术层不能把禁止追高、阻断或低置信买区变成入场信号。", *raw]
    if review_only:
        return ["趋势破坏、阻断或需要复核时，技术层只给复核线，不给入场建议。", "技术层不能把禁止追高、阻断或低置信买区变成入场信号。", *raw]
    guardrail = "估值买点、技术回踩点、轻仓试探点和极端恐慌区分开理解；技术层只辅助入场。"
    return [guardrail, *raw] if raw else [guardrail]


def _humanize_buy_zone_explain_item(value: object) -> str:
    text = str(value or "").strip()
    mapping = {
        "buy_zone_model_not_supported": "暂无专属买区模型",
        "unsupported_buy_zone_model": "暂无专属买区模型",
        "data_confidence_low": "数据置信度偏低",
        "missing_power_generation_core_inputs": "缺少发电模型核心输入",
        "invalid_zone": "买区区间异常",
        "invalid_manual_override": "手动买区区间异常",
        "data_insufficient": "关键买区输入不足",
        "low_confidence_zone": "买区置信度不足",
        "no_chase": "当前价格处于禁止追高区",
        "missing_networking_hardware_growth_or_margin": "缺少网络硬件模型所需的增长或利润率输入",
        "networking_hardware_sales_multiple_overextended": "销售倍数偏高，优先不追高",
        "networking_hardware_risk_inputs_missing: customer concentration / cloud capex risk": "缺客户集中度 / 云资本开支风险输入，置信度受限",
        "missing_crypto_ev_sales_anchor": "缺少 EV/Sales 主估值锚",
        "missing_crypto_operating_inputs": "缺少交易收入、订阅收入、盈利和周期拆分",
        "missing_crypto_core_inputs_for_heavy_buy": "缺少核心经营输入，不输出深度折价区",
        "crypto_financial_infra_high_beta_sales_multiple": "高 beta 且销售倍数偏高，优先不追高",
        "crypto_financial_infra_operating_mix_missing": "缺少加密金融经营拆分，置信度受限",
        "missing_power_generation_core_inputs": "缺少电力模型核心输入",
        "ai_cloud_infra_high_ev_sales_capex_debt": "EV/Sales、资本开支和债务压力同时偏高",
        "ai_cloud_infra_customer_concentration_missing": "缺客户集中度输入，置信度受限",
        "ai_cloud_infra_debt_maturity_unclear": "债务到期结构不清楚，置信度受限",
        "ai_cloud_infra_operating_inputs_incomplete": "缺利用率或资本开支承诺输入",
        "ai_cloud_infra_fcf_burn_or_capex_intensity_blocks_heavy_buy": "FCF 为负或资本开支强度过高，不输出深度折价区",
    }
    if text in mapping:
        return mapping[text]
    if text == "dataConfidence = low":
        return "数据置信度偏低"
    if text.startswith("model_type:"):
        return "模型类型：" + text.split(":", 1)[1].strip()
    if text.startswith("current price:"):
        return "当前价格：" + text.split(":", 1)[1].strip()
    return text


def _plan_html(row: dict) -> str:
    items = [
        ("估值折价触发价", _money(row.get("firstBuyPrice"))),
        ("估值折价区下沿", _money(row.get("secondBuyPrice"))),
        ("极端恐慌区触发价", _money(row.get("thirdBuyPrice"))),
        ("禁止追高价", _money(row.get("noChaseAbove"))),
        ("停止加仓条件", str(row.get("stopAddingCondition") or "")),
        ("财报复核条件", str(row.get("earningsReviewCondition") or "")),
    ]
    html = "".join(f"<li><span>{escape(label)}</span><b>{escape(value)}</b></li>" for label, value in items)
    return f'<div class="drawer-resolution plan-list"><ul>{html}</ul></div>'


def _render_manual_and_advanced_settings(rows: list[dict], plan_store: StockPlanStore) -> None:
    with st.expander("手动覆盖与高级设置", expanded=False):
        if rows:
            cols = st.columns([1.2, 1.1, 3.5])
            with cols[0]:
                symbol = st.selectbox("手动覆盖股票", [str(row["symbol"]) for row in rows], key="buy-zone-manual-symbol")
            selected = next((row for row in rows if row["symbol"] == symbol), None)
            with cols[1]:
                if selected and st.button("恢复系统建议", width="stretch"):
                    plan_store.save_plan(symbol, clear_buy_zone_override_values(plan_store.get_plan(symbol)))
                    _load_buy_zone_rows.clear()
                    st.toast(f"{symbol} 已恢复系统建议")
                    st.rerun()
            with cols[2]:
                st.caption("手动覆盖优先于系统建议；高级估值沙盒仅用于临时测算。")
        else:
            st.caption("观察池为空，可先使用高级估值沙盒做单次测算。")
        st.divider()
        if st.checkbox("打开高级估值沙盒", value=False, key="buy-zone-show-valuation-sandbox"):
            _render_valuation_sandbox_body()
        else:
            st.caption("高级估值沙盒默认不计算，打开后再进行手动情景测算。")


def _render_valuation_sandbox() -> None:
    with st.expander("高级估值沙盒", expanded=False):
        _render_valuation_sandbox_body()


def _render_valuation_sandbox_body() -> None:
    st.caption("估值沙盒只用于手动情景测算，不作为系统买区主来源。")
    cols = st.columns([1, 1, 1])
    current_price = cols[0].number_input("当前价格（手动）", min_value=0.0, value=100.0, step=1.0, format="%.2f")
    target_position = cols[1].number_input("目标仓位金额（美元）", min_value=0.0, value=10_000.0, step=500.0)
    margin_of_safety = cols[2].slider("额外安全边际", min_value=0, max_value=60, value=0, step=1)
    method_label = st.selectbox("估值方法", list(METHOD_LABELS.keys()))
    method = METHOD_LABELS[method_label]
    assumptions = _method_inputs(method)
    inputs = BuyZoneInputs(
        current_price=current_price,
        target_position_size=target_position,
        valuation_method=method,
        margin_of_safety_pct=margin_of_safety,
        **assumptions,
    )
    try:
        output = calculate_buy_zone_ladder(inputs)
    except ValueError as exc:
        st.warning(str(exc))
        return
    metrics = st.columns(4)
    metrics[0].metric("公允价值", format_currency(output["fair_value_price"]))
    metrics[1].metric("试探仓价格", format_currency(output["starter_position_price"]))
    metrics[2].metric("正常买入区", format_currency(output["normal_buy_zone_price"]))
    metrics[3].metric("极端恐慌区", format_currency(output["heavy_buy_zone_price"]))
    tranches = output["tranches"].rename(
        columns={"Tranche": "分批", "Buy Price": "买入价", "Allocation %": "分配比例", "Allocation $": "分配金额", "Estimated Shares": "估算股数"}
    )
    st.dataframe(tranches, width="stretch", hide_index=True)
    _render_price_ladder_chart(output)


def _render_price_ladder_chart(output: dict) -> None:
    labels = ["禁止追高", "试探", "正常买入", "极端恐慌", "恐慌"]
    prices = [
        output["margin_adjusted_fair_value"],
        output["starter_position_price"],
        output["normal_buy_zone_price"],
        output["heavy_buy_zone_price"],
        output["panic_buy_zone_price"],
    ]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=prices, y=[1] * len(prices), mode="markers+text", text=labels, textposition="top center", marker=dict(size=13)))
    fig.update_yaxes(visible=False)
    fig.update_layout(height=220, showlegend=False, margin=dict(l=20, r=20, t=28, b=20), xaxis_title="价格梯")
    st.plotly_chart(fig, width="stretch")


def _method_inputs(method: str) -> dict:
    if method == "EPS multiple":
        cols = st.columns(2)
        forward_eps = cols[0].number_input("预期 EPS", min_value=0.0, value=8.0, step=0.25)
        target_pe = cols[1].number_input("目标市盈率", min_value=0.0, value=25.0, step=1.0)
        return {"forward_eps": forward_eps, "target_pe": target_pe}
    if method == "FCF multiple":
        cols = st.columns(3)
        return {
            "forward_fcf": cols[0].number_input("预期 FCF（美元）", min_value=0.0, value=10_000_000_000.0, step=500_000_000.0),
            "target_fcf_multiple": cols[1].number_input("目标 FCF 倍数", min_value=0.0, value=20.0, step=1.0),
            "shares_outstanding": cols[2].number_input("流通股数", min_value=0.0, value=1_000_000_000.0, step=50_000_000.0),
        }
    cols = st.columns(4)
    return {
        "forward_revenue": cols[0].number_input("预期收入（美元）", min_value=0.0, value=20_000_000_000.0, step=500_000_000.0),
        "target_ev_sales": cols[1].number_input("目标 EV/销售额", min_value=0.0, value=8.0, step=0.5),
        "net_debt": cols[2].number_input("净债务（美元）", value=0.0, step=500_000_000.0),
        "shares_outstanding": cols[3].number_input("流通股数", min_value=0.0, value=1_000_000_000.0, step=50_000_000.0),
    }


def _render_styles() -> None:
    st.markdown(
        """
        <style>
        div[data-testid="stAppViewContainer"] {
            background:#F6F8FB;
        }
        div.block-container {
            max-width: 1120px;
            padding-left: 1.8rem;
            padding-right: 1.8rem;
        }
        .buy-zone-summary,
        .execution-console-panel,
        div[data-testid="stRadio"],
        div[data-testid="stExpander"] {
            width: 100%;
            max-width: 1080px;
            margin-left: auto;
            margin-right: auto;
            box-sizing: border-box;
        }
        .buy-zone-summary {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 0;
            margin-top: 1rem;
            margin-bottom: 0.55rem;
            border:1px solid rgba(148, 163, 184, 0.20);
            border-radius:8px;
            background:#fff;
            overflow:hidden;
        }
        .buy-zone-summary-card {
            border: 0;
            border-right:1px solid rgba(15, 23, 42, 0.035);
            background: transparent;
            border-radius: 0;
            padding: 0.58rem 0.82rem;
            min-height: 64px;
            display: grid;
            align-content: center;
        }
        .buy-zone-summary-card:last-child { border-right:0; }
        .buy-zone-summary-card span { color: #64748B; font-size: 12px; font-weight: 650; line-height:1.05; }
        .buy-zone-summary-card strong { display:block; margin-top:0.1rem; font-size:18px; font-weight:720; color:#0F172A; line-height:1.05; }
        .buy-zone-summary-card em { display:block; margin-top:0.1rem; color:#94A3B8; font-size:11px; font-style:normal; line-height:1.2; }
        .execution-summary {
            border:1px solid #DFE7F0;
            border-radius:8px;
            background:#FCFDFF;
            padding:0.7rem 0.82rem 0.62rem;
            margin-top:0;
            margin-bottom:0.9rem;
            box-shadow:0 1px 2px rgba(15, 23, 42, 0.035);
        }
        .execution-title {
            display:flex;
            align-items:baseline;
            gap:0.55rem;
            margin-bottom:0.42rem;
        }
        .execution-title span {
            font-size:14px;
            font-weight:760;
            color:#0F172A;
        }
        .execution-title small {
            font-size:11px;
            font-weight:500;
            color:#94A3B8;
        }
        .execution-grid {
            display:grid;
            grid-template-columns:repeat(3, minmax(0, 1fr));
            gap:0.6rem;
            border-top:0;
        }
        .execution-card {
            border:1px solid rgba(15, 23, 42, 0.055);
            border-radius:7px;
            background:#FFFFFF;
            padding:0.5rem 0.58rem 0.42rem;
            min-height:0;
            box-shadow:0 1px 1px rgba(15, 23, 42, 0.025);
        }
        .execution-card.green { border-top-color:#BFE8CE; }
        .execution-card.blue { border-top-color:#CFE0FA; }
        .execution-card.amber { border-top-color:#F1D9A8; }
        .execution-card-head {
            display:flex;
            align-items:flex-start;
            justify-content:space-between;
            gap:0.55rem;
        }
        .execution-card-head strong {
            display:block;
            color:#111827;
            font-size:12px;
            font-weight:700;
        }
        .execution-card-head span {
            display:block;
            margin-top:0.1rem;
            color:#94A3B8;
            font-size:11px;
            line-height:1.25;
        }
        .execution-card-head em {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            min-width:22px;
            height:20px;
            padding:0 6px;
            border-radius:999px;
            background:#F8FAFC;
            border:1px solid #E4EAF1;
            color:#475569;
            font-size:11px;
            font-style:normal;
            font-weight:650;
        }
        .execution-card ul {
            list-style:none;
            padding:0;
            margin:0.34rem 0 0;
        }
        .execution-card li {
            display:grid;
            grid-template-columns:48px minmax(0, 1fr);
            grid-template-rows:auto auto;
            align-items:center;
            gap:0.35rem;
            min-height:30px;
            color:#64748B;
            font-size:12px;
            border-top:1px solid rgba(15, 23, 42, 0.035);
        }
        .execution-card li:first-child { border-top:0; }
        .execution-card li b {
            grid-row:1 / 3;
            color:#0F172A;
            font-weight:760;
            font-size:12px;
        }
        .execution-card li span {
            grid-column:2;
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            line-height:1.15;
            font-weight:620;
        }
        .execution-card li small {
            grid-column:2;
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            color:#94A3B8;
            font-size:11px;
            line-height:1.1;
        }
        .execution-empty-row {
            min-height:28px !important;
            display:flex !important;
            align-items:center;
            color:#CBD5E1 !important;
            border-top:0 !important;
        }
        .execution-empty-row span {
            color:#CBD5E1 !important;
            font-weight:650;
        }
        .execution-more-row {
            min-height:24px !important;
            border-top:0 !important;
            color:#94A3B8 !important;
        }
        .execution-more-row span {
            grid-column:1 / 3;
            font-size:11px;
            font-weight:620;
            color:#94A3B8;
        }
        .execution-summary-empty {
            color:#64748B;
            font-size:0.82rem;
            font-weight:650;
        }
        .execution-console-panel {
            border:1px solid rgba(148, 163, 184, 0.22);
            border-radius:10px;
            background:#FFFFFF;
            margin-top:0.36rem;
            margin-bottom:0.8rem;
            overflow:hidden;
            box-shadow:0 1px 2px rgba(15, 23, 42, 0.025);
        }
        .priority-strip {
            padding:0.44rem 0.64rem 0.42rem;
            border-bottom:1px solid rgba(15, 23, 42, 0.045);
            background:linear-gradient(180deg, #F8FAFC 0%, #FBFCFE 100%);
        }
        .priority-strip-head {
            display:flex;
            align-items:baseline;
            justify-content:space-between;
            gap:0.75rem;
            margin-bottom:0.34rem;
        }
        .priority-strip-head strong {
            display:block;
            color:#0F172A;
            font-size:13px;
            font-weight:760;
            line-height:1.1;
        }
        .priority-strip-head span {
            color:#64748B;
            font-size:11px;
            font-weight:520;
        }
        .priority-list {
            display:grid;
            grid-template-columns:repeat(5, minmax(0, 1fr));
            gap:0.5rem;
            border:0;
            background:transparent;
            overflow:visible;
        }
        .priority-row {
            display:grid;
            grid-template-columns:7px minmax(0, 1fr);
            align-items:center;
            gap:0.34rem;
            min-height:25px;
            max-width:100%;
            padding:0.14rem 0.18rem;
            border:1px solid transparent;
            border-radius:4px;
            background:transparent;
            overflow:hidden;
        }
        .priority-row:hover {
            background:rgba(255,255,255,0.72);
            border-color:rgba(148, 163, 184, 0.08);
        }
        .priority-copy {
            display:grid;
            grid-template-columns:minmax(34px, auto) minmax(0, 1fr);
            align-items:center;
            column-gap:0.35rem;
            row-gap:0.08rem;
            width:100%;
            min-width:0;
        }
        .priority-dot,
        .confidence-inline i {
            display:inline-block;
            width:6px;
            height:6px;
            margin-top:0;
            border-radius:999px;
            background:#94A3B8;
            box-shadow:0 0 0 2px rgba(148, 163, 184, 0.08);
        }
        .priority-dot.green,
        .confidence-inline.green i { background:#22C55E; }
        .priority-dot.blue,
        .confidence-inline.blue i { background:#64748B; }
        .priority-dot.amber,
        .priority-dot.orange,
        .confidence-inline.orange i { background:#D97706; }
        .priority-dot.red,
        .confidence-inline.red i { background:#DC2626; }
        .priority-row strong {
            color:#0F172A;
            font-size:11.5px;
            font-weight:780;
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .priority-row span:not(.buy-zone-badge) {
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            color:#334155;
            font-size:11.5px;
            font-weight:650;
        }
        .priority-empty {
            min-height:34px;
            display:flex;
            align-items:center;
            color:#94A3B8;
            font-size:12px;
            font-weight:560;
            border:1px solid rgba(148, 163, 184, 0.14);
            border-radius:6px;
            padding:0 0.6rem;
            background:#FFFFFF;
        }
        .execution-toolbar-title {
            max-width:1080px;
            margin-left:auto;
            margin-right:auto;
            padding-top:0.05rem;
        }
        .execution-toolbar-title strong {
            display:block;
            color:#0F172A;
            font-size:15px;
            font-weight:760;
            line-height:1.2;
        }
        .execution-toolbar-title span {
            display:block;
            margin-top:0.1rem;
            color:#94A3B8;
            font-size:11.5px;
            font-weight:520;
        }
        .buy-zone-filter-toolbar-marker {
            height:0;
            margin:0;
            padding:0;
        }
        div[data-testid="stVerticalBlock"] > div:has(.buy-zone-filter-toolbar-marker) + div [data-testid="stHorizontalBlock"] {
            max-width:1080px;
            margin:0.28rem auto 0.36rem;
            padding:0.34rem 0.5rem;
            align-items:center;
            gap:0.5rem !important;
            border:1px solid rgba(15, 23, 42, 0.075);
            border-radius:8px;
            background:linear-gradient(180deg, rgba(255,255,255,0.92), rgba(248,250,252,0.86));
            box-shadow:0 1px 2px rgba(15, 23, 42, 0.025);
            box-sizing:border-box;
        }
        div[data-testid="stVerticalBlock"] > div:has(.buy-zone-filter-toolbar-marker) + div [data-testid="stHorizontalBlock"] > div {
            padding:0 !important;
        }
        div[data-testid="stVerticalBlock"] > div:has(.buy-zone-filter-toolbar-marker) + div [data-testid="stHorizontalBlock"] > div:last-child {
            display:flex;
            justify-content:flex-end;
            align-items:center;
        }
        div[data-testid="stRadio"] {
            margin-top: 0;
            margin-bottom: 0;
        }
        div[data-testid="stRadio"] div[role="radiogroup"] {
            display:inline-flex;
            align-items:center;
            gap:4px;
            min-height:32px;
            padding:0.16rem;
            border:1px solid rgba(15, 23, 42, 0.08);
            border-radius:8px;
            background:#F6F8FB;
            flex-wrap:wrap;
            box-shadow:inset 0 1px 0 rgba(255,255,255,0.72);
        }
        div[data-testid="stRadio"] label {
            margin:0 !important;
            min-height:25px;
            padding:0.1rem 0.5rem !important;
            border-radius:6px;
            color:#64748B;
            font-size:11.5px;
            font-weight:700;
            line-height:1;
            letter-spacing:0;
            border:1px solid transparent;
            transition:background 120ms ease, border-color 120ms ease, color 120ms ease;
        }
        div[data-testid="stRadio"] label p {
            font-size:12px !important;
            font-weight:650 !important;
            line-height:1 !important;
        }
        div[data-testid="stRadio"] label:has(input:checked) {
            background:#FFFFFF;
            color:#0F172A;
            border-color:rgba(15, 23, 42, 0.08);
            box-shadow:0 1px 1px rgba(15, 23, 42, 0.04);
        }
        div[data-testid="stRadio"] label:hover {
            background:rgba(255,255,255,0.72);
            color:#334155;
        }
        div[data-testid="stRadio"] label > div:first-child {
            display:none;
        }
        .buy-zone-table {
            display:block;
            border:0;
            border-radius:0;
            overflow-x: auto;
            overflow-y: hidden;
            background: #FFFFFF;
            margin:0;
        }
        .buy-zone-grid {
            display: grid;
            grid-template-columns: 104px minmax(164px, 0.95fr) minmax(230px, 1.12fr) 132px 66px 104px;
            align-items: center;
            gap: 0.5rem;
            min-height: 42px;
            min-width: 880px;
            width: 100%;
            padding: 0 12px;
            font-size: 12.5px;
            box-sizing:border-box;
        }
        .buy-zone-grid-head {
            background: #F8FAFC;
            color: #64748B;
            font-size: 11.5px;
            font-weight: 650;
            min-height: 30px;
            padding-top:0;
            padding-bottom:0;
            border-bottom:1px solid rgba(15, 23, 42, 0.055);
        }
        .buy-zone-grid-head span:last-child {
            text-align:center;
        }
        .buy-zone-row {
            background:#FFFFFF;
            border-bottom:1px solid rgba(15, 23, 42, 0.05);
            border-radius:0;
            box-shadow:none;
            min-height:48px;
        }
        .buy-zone-row:last-child { border-bottom:0; }
        .buy-zone-row:hover {
            background:#FAFBFD;
        }
        .stock-cell,
        .status-cell,
        .position-cell {
            min-width:0;
        }
        .stock-cell,
        .position-cell {
            display:flex;
            flex-direction:column;
            gap:0.04rem;
            line-height:1.12;
        }
        .stock-cell strong {
            color:#0F172A;
            font-size:13px;
            font-weight:760;
            letter-spacing:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .stock-cell span {
            color:#475569;
            font-size:12px;
            font-weight:560;
            font-variant-numeric:tabular-nums;
            white-space:nowrap;
        }
        .status-cell {
            display:flex;
            flex-direction:column;
            align-items:flex-start;
            gap:0.08rem;
        }
        .status-cell small {
            max-width:100%;
            color:#64748B;
            font-size:11px;
            font-weight:560;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .trigger-cell {
            min-width:0;
            display:flex;
            flex-direction:column;
            align-items:flex-start;
            justify-content:center;
            gap:0.08rem;
            line-height:1.12;
        }
        .trigger-cell b {
            max-width:100%;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            font-size:12px;
            font-weight:660;
            color:#0F172A;
        }
        .trigger-cell small {
            max-width:100%;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            font-size:11px;
            color:#64748B;
        }
        .trigger-cell em {
            max-width:100%;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            color:#94A3B8;
            font-size:10.5px;
            font-style:normal;
            font-weight:560;
        }
        .position-cell b {
            color:#0F172A;
            font-size:12px;
            font-weight:680;
            white-space:nowrap;
        }
        .position-cell small {
            color:#64748B;
            font-size:11px;
            font-weight:560;
        }
        .confidence-cell {
            display:flex;
            align-items:center;
        }
        .confidence-inline {
            display:inline-flex;
            align-items:center;
            gap:0.32rem;
            color:#475569;
            font-size:12px;
            font-weight:620;
            white-space:nowrap;
        }
        .trigger-cell.ready b { color:#1F2937; }
        .trigger-cell.near b,
        .trigger-cell.caution b { color:#334155; }
        .trigger-cell.warning b { color:#B45309; }
        .buy-zone-badge {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            height:20px;
            max-width:100%;
            padding:0 7px;
            border-radius:999px;
            border:1px solid transparent;
            background:#F3F4F6;
            color:#4B5563;
            font-size:11.5px;
            font-weight:600;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
        }
        .buy-zone-badge.green { background:#F0FDF4; color:#166534; border-color:#CDEFD8; }
        .buy-zone-badge.blue { background:#F2F6FB; color:#334155; border-color:#E4EAF1; }
        .buy-zone-badge.yellow { background:#FFFBEB; color:#854D0E; border-color:#F4E7B0; }
        .buy-zone-badge.orange { background:#FFF7ED; color:#92400E; border-color:#F7D8A9; }
        .buy-zone-badge.red { background:#FFF5F5; color:#991B1B; border-color:#F3D2D2; }
        .buy-zone-badge.gray { background:#F8FAFC; color:#475569; border-color:#E4EAF1; }
        .buy-zone-detail-link {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            min-width:30px;
            height:26px;
            text-decoration:none !important;
            color:#52657F;
            font-size:12px;
            font-weight:700;
            padding:0;
            border-radius:4px;
            border:1px solid transparent;
            background:transparent;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
            box-sizing:border-box;
        }
        .buy-zone-detail-link i {
            display:none;
        }
        .buy-zone-detail-link:hover {
            color:#0F172A;
            border-color:rgba(15, 23, 42, 0.08);
            background:#FFFFFF;
            text-decoration:none !important;
        }
        .buy-zone-detail-link:hover i {
            color:#475569;
        }
        .buy-zone-row-actions {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            justify-self:center;
            gap:16px;
            width:82px;
            max-width:82px;
            padding:0;
            border:0;
            border-radius:0;
            background:transparent;
            white-space:nowrap;
            box-shadow:none;
        }
        .buy-zone-record-link {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            height:26px;
            min-width:30px;
            padding:0;
            border-radius:4px;
            border:1px solid transparent;
            background:transparent;
            color:#64748B;
            font-size:12px;
            font-weight:650;
            text-decoration:none !important;
            white-space:nowrap;
        }
        .buy-zone-record-link:hover {
            color:#334155;
            border-color:rgba(15, 23, 42, 0.08);
            background:#FFFFFF;
            text-decoration:none !important;
        }
        .buy-zone-empty {
            display:flex;
            align-items:center;
            min-height:44px;
            padding:0 12px;
            color:#64748B;
            font-size:12px;
            border-top:1px solid rgba(15, 23, 42, 0.05);
        }
        .execution-console-foot {
            padding:0.42rem 0.68rem 0.52rem;
            border-top:1px solid rgba(15, 23, 42, 0.045);
            color:#64748B;
            font-size:11px;
            font-weight:520;
            background:#FBFCFF;
        }
        .buy-zone-drawer .price-ladder,
        .buy-zone-drawer .drawer-resolution {
            border:1px solid #E5E7EB;
            border-radius:8px;
            padding:0.8rem;
            margin-bottom:0.75rem;
            background:#fff;
        }
        .drawer-explainability {
            border:1px solid rgba(148, 163, 184, 0.22);
            border-radius:8px;
            background:#FBFCFE;
            padding:0.8rem;
            margin-bottom:0.75rem;
        }
        .drawer-explainability > strong {
            display:block;
            color:#0F172A;
            font-size:0.95rem;
            font-weight:850;
            margin-bottom:0.25rem;
        }
        .drawer-explainability > p {
            margin:0 0 0.7rem;
            color:#475569;
            font-size:0.82rem;
            line-height:1.5;
        }
        .drawer-explain-grid {
            display:grid;
            grid-template-columns:repeat(2, minmax(0, 1fr));
            gap:0.5rem;
        }
        .drawer-explain-block {
            border:1px solid rgba(148, 163, 184, 0.16);
            border-radius:7px;
            background:#FFFFFF;
            padding:0.55rem 0.62rem;
        }
        .drawer-explain-block b {
            display:block;
            color:#64748B;
            font-size:0.72rem;
            font-weight:850;
            margin-bottom:0.3rem;
        }
        .drawer-explain-block ul {
            margin:0;
            padding-left:1rem;
            color:#0F172A;
            font-size:0.76rem;
            line-height:1.45;
        }
        .drawer-explain-block li + li {
            margin-top:0.2rem;
        }
        .drawer-technical-entry {
            border:1px solid rgba(148, 163, 184, 0.22);
            border-radius:8px;
            background:#FFFFFF;
            padding:0.8rem;
            margin-bottom:0.75rem;
        }
        .drawer-technical-entry > strong {
            display:block;
            color:#0F172A;
            font-size:0.92rem;
            font-weight:850;
            margin-bottom:0.25rem;
        }
        .drawer-technical-entry > p {
            margin:0 0 0.65rem;
            color:#64748B;
            font-size:0.8rem;
            line-height:1.5;
        }
        .drawer-technical-grid ul {
            display:grid;
            grid-template-columns:repeat(2, minmax(0, 1fr));
            gap:0;
            margin:0;
            padding:0;
            list-style:none;
            border:1px solid rgba(148, 163, 184, 0.14);
            border-radius:7px;
            overflow:hidden;
        }
        .drawer-technical-grid li {
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:0.45rem;
            padding:0.42rem 0.55rem;
            border-right:1px solid rgba(148, 163, 184, 0.12);
            border-bottom:1px solid rgba(148, 163, 184, 0.12);
            color:#64748B;
            font-size:0.74rem;
        }
        .drawer-technical-grid li:nth-child(2n) { border-right:0; }
        .drawer-technical-grid li b {
            color:#0F172A;
            font-size:0.75rem;
            font-variant-numeric:tabular-nums;
            text-align:right;
        }
        .drawer-technical-reasons {
            margin-top:0.55rem;
            border:1px solid rgba(148, 163, 184, 0.14);
            border-radius:7px;
            background:#FBFCFE;
            padding:0.55rem 0.65rem;
        }
        .drawer-technical-reasons b {
            color:#64748B;
            font-size:0.72rem;
            font-weight:850;
        }
        .drawer-technical-reasons ul {
            margin:0.32rem 0 0;
            padding-left:1rem;
            color:#334155;
            font-size:0.76rem;
            line-height:1.45;
        }
        .price-ladder ul,
        .plan-list ul {
            list-style:none;
            padding:0;
            margin:0;
        }
        .price-ladder li,
        .plan-list li {
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:0.75rem;
            padding:0.45rem 0;
            border-bottom:1px solid #F1F5F9;
            color:#475569;
        }
        .price-ladder li:last-child,
        .plan-list li:last-child { border-bottom:0; }
        .price-marker {
            margin-top:0.65rem;
            padding:0.45rem 0.6rem;
            border-radius:8px;
            background:#EFF6FF;
            color:#1D4ED8;
            font-weight:800;
        }
        .buy-zone-drawer-root {
            display: contents;
        }
        .buy-zone-drawer-shell {
            display: none;
        }
        .buy-zone-drawer-shell:target {
            display: block;
        }
        .buy-zone-drawer-backdrop {
            position: fixed;
            inset: 0;
            background: transparent;
            z-index: 2147482998;
            pointer-events: auto;
        }
        .drawer-close-link {
            position: fixed;
            top: 14px;
            right: 16px;
            z-index: 2147483001;
            display:flex;
            align-items:center;
            justify-content:center;
            width:38px;
            height:38px;
            border-radius:999px;
            border:1px solid #E5E7EB;
            background:rgba(255,255,255,0.96);
            color:#64748B;
            box-shadow:0 12px 30px rgba(15,23,42,0.14);
            text-decoration:none;
            font-size:1.25rem;
            font-weight:700;
        }
        .stock-drawer.buy-zone-drawer {
            position: fixed;
            top: 0;
            right: 0;
            width: min(620px, 42vw);
            height: 100vh;
            overflow-y: auto;
            padding: 1.15rem;
            background: #FFFFFF;
            border-left: 1px solid #E5E7EB;
            box-shadow: -14px 0 34px rgba(15,23,42,0.10);
            z-index: 2147482999;
            animation: buy-zone-drawer-in 120ms ease-out;
        }
        @keyframes buy-zone-drawer-in {
            from { transform: translateX(18px); opacity: 0.98; }
            to { transform: translateX(0); opacity: 1; }
        }
        .drawer-topline {
            color:#94A3B8;
            font-size:0.72rem;
            font-weight:800;
            text-transform:uppercase;
        }
        .drawer-head {
            display:flex;
            justify-content:space-between;
            gap:1rem;
            margin:0.35rem 0 0.75rem;
        }
        .drawer-symbol {
            font-size:2rem;
            line-height:1;
            font-weight:850;
            color:#111827;
        }
        .drawer-company,
        .drawer-muted {
            color:#64748B;
            font-size:0.86rem;
        }
        .drawer-price {
            font-weight:850;
            color:#111827;
            font-variant-numeric:tabular-nums;
        }
        .drawer-badges {
            display:flex;
            gap:0.4rem;
            flex-wrap:wrap;
            margin-bottom:0.8rem;
        }
        .drawer-position-card {
            border:1px solid #BFDBFE;
            background:#EFF6FF;
            border-radius:8px;
            padding:0.85rem;
            margin-bottom:0.85rem;
        }
        .drawer-card-title,
        .drawer-section-title {
            font-size:0.78rem;
            font-weight:850;
            color:#64748B;
            margin:0.8rem 0 0.45rem;
        }
        .drawer-decision-headline {
            color:#0F172A;
            font-weight:780;
            line-height:1.55;
        }
        @media (max-width: 1280px) {
            .buy-zone-grid {
                grid-template-columns: 96px minmax(150px, 0.9fr) minmax(220px, 1.18fr) 112px 62px 112px;
                font-size:12px;
                gap:0.46rem;
                min-width:875px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _badge(label: str, tone: str = "gray") -> str:
    return f'<span class="buy-zone-badge {escape(tone)}">{escape(str(label))}</span>'


def _priority_marker(label: str, tone: str) -> str:
    return f'<span class="priority-status {escape(tone)}"><i></i>{escape(label)}</span>'


def _confidence_inline(value: object) -> str:
    raw = str(value or "")
    tone = CONFIDENCE_TONES.get(raw, "gray")
    return f'<span class="confidence-inline {escape(tone)}"><i></i><span>{escape(confidence_label(value))}</span></span>'


def _zone_label(value: object) -> str:
    return ZONE_LABELS.get(str(value or ""), "需复核")


def _source_label(value: object, manual: bool = False) -> str:
    if manual:
        return "手动买区"
    return SOURCE_LABELS.get(str(value or ""), "需复核")


def _display_category_result(
    category: str,
    primary: str,
    secondary: str,
    priority_eligible: bool,
    tone: str = "neutral",
) -> dict[str, object]:
    return {
        "displayCategory": category,
        "displayLabel": category,
        "triggerPrimary": primary,
        "triggerSecondary": secondary,
        "priorityEligible": priority_eligible,
        "triggerTone": tone,
    }


def _row_action(row: dict) -> str:
    return str(row.get("finalAction") or row.get("action") or "")


def _has_final_decision(row: dict) -> bool:
    return any(key in row for key in ("finalAction", "decisionLane", "isActionable"))


def _row_is_actionable(row: dict) -> bool:
    current_add = _first_number(row.get("currentAddLimitPercent"))
    explicit = row.get("isActionable")
    if isinstance(explicit, bool):
        if explicit:
            return current_add is None or current_add > 0
        return False
    if explicit is not None:
        text = str(explicit).strip().lower()
        if text in {"true", "false"}:
            if text == "true":
                return current_add is None or current_add > 0
            return False
    return _row_action(row) in {"可小仓分批", "可正常分批"} and current_add is not None and current_add > 0


def resolve_buy_zone_display_category(row: dict) -> dict[str, object]:
    zone = str(row.get("currentZone") or "")
    action = _row_action(row)
    has_final_decision = _has_final_decision(row)
    decision_lane = str(row.get("decisionLane") or "")
    is_actionable = _row_is_actionable(row)
    current_add = _first_number(row.get("currentAddLimitPercent"))
    trigger = _first_number(row.get("nextTriggerPrice"), row.get("nextBuyPrice"))
    price = _first_number(row.get("currentPrice"))
    has_trigger = trigger is not None and trigger > 0
    trigger_secondary = f"触发价 {format_currency(trigger)}" if has_trigger else "等待条件明确"

    decision_gate = _final_decision_display_gate(
        row,
        zone=zone,
        action=action,
        has_final_decision=has_final_decision,
        decision_lane=decision_lane,
        is_actionable=is_actionable,
        current_add=current_add,
        has_trigger=has_trigger,
        trigger_secondary=trigger_secondary,
    )
    if decision_gate is not None:
        return decision_gate

    return _buy_zone_distance_display_category(
        row,
        zone=zone,
        price=price,
        trigger=trigger,
        has_trigger=has_trigger,
        trigger_secondary=trigger_secondary,
        is_actionable=is_actionable,
        has_final_decision=has_final_decision,
    )


def _final_decision_display_gate(
    row: dict,
    *,
    zone: str,
    action: str,
    has_final_decision: bool,
    decision_lane: str,
    is_actionable: bool,
    current_add: float | None,
    has_trigger: bool,
    trigger_secondary: str,
) -> dict[str, object] | None:
    if (
        _needs_review(row)
        or zone == "data_insufficient"
        or row.get("confidence") == "low"
        or row.get("dataConfidence") == "low"
    ):
        if zone == "data_insufficient":
            return _display_category_result("需复核", "数据不足", "暂不生成触发价", False, "warning")
        return _display_category_result("需复核", "需复核", "买区异常", False, "warning")

    if decision_lane == "review":
        return _display_category_result("需复核", "需复核", "先复核数据与风险", False, "warning")

    if zone == "no_chase" or decision_lane == "blocked" or action == "禁止追高":
        secondary = trigger_secondary if has_trigger else "等待价格回落"
        return _display_category_result("禁止追高", "等待回踩", secondary, False, "caution")

    if is_actionable or (not has_final_decision and (zone in {"tranche_buy", "heavy_buy", "below_heavy_buy"} or (current_add is not None and current_add > 0))):
        return _display_category_result("可执行", "已进入买区", "可按计划执行", False, "ready")

    return None


def _buy_zone_distance_display_category(
    row: dict,
    *,
    zone: str,
    price: float | None,
    trigger: float | None,
    has_trigger: bool,
    trigger_secondary: str,
    is_actionable: bool,
    has_final_decision: bool,
) -> dict[str, object]:
    if has_trigger and price is not None and price > 0:
        if price <= trigger and (is_actionable or not has_final_decision):
            return _display_category_result("可执行", "已进入买区", "可按计划执行", False, "ready")

        sanity_label = _buy_point_sanity_label(row, zone=zone, price=price, trigger=trigger)
        if sanity_label:
            return _display_category_result("等回踩", sanity_label, trigger_secondary, False, "neutral")

        distance = _drop_to_trigger_pct(row)
        if distance is not None and distance <= NEAR_TRIGGER_THRESHOLD_PCT:
            return _display_category_result(
                "接近买区",
                f"距触发 {distance:.1f}%",
                trigger_secondary,
                True,
                "near",
            )
        if distance is not None and distance <= LARGE_TRIGGER_DISTANCE_PCT:
            return _display_category_result(
                "等回踩",
                f"需回落 {distance:.1f}%",
                trigger_secondary,
                False,
                "neutral",
            )
        return _display_category_result("等回踩", "仍需大幅回落", trigger_secondary, False, "neutral")

    if not _valid_price(row.get("currentPrice")):
        return _display_category_result("需复核", "数据不足", "缺少当前价", False, "warning")

    label = str(row.get("nextBuyLabel") or "").strip()
    fallback_primary = _next_label(label) if label else _zone_next_label(zone)
    return _display_category_result("等回踩", fallback_primary, "等待条件明确", False, "neutral")


def _buy_point_sanity_label(row: dict, *, zone: str, price: float | None, trigger: float | None) -> str | None:
    if price is None or price <= 0:
        return None
    if zone in {"tranche_buy", "heavy_buy", "below_heavy_buy"}:
        return None
    tranche_low = _first_number(row.get("trancheBuyLow"))
    tranche_high = _first_number(row.get("trancheBuyHigh"))
    if _price_in_range(price, tranche_low, tranche_high):
        return None
    fair_low = _first_number(row.get("fairValueLow"))
    fair_high = _first_number(row.get("fairValueHigh"))
    if zone == "fair_observation" and _price_in_range(price, fair_low, fair_high):
        return FAIR_OBSERVATION_NOT_BUY_LABEL
    if trigger is None or trigger <= 0 or price <= trigger:
        return None
    distance = max((price - trigger) / price * 100, 0)
    if distance > NEAR_TRIGGER_THRESHOLD_PCT:
        return FAIR_OBSERVATION_NOT_BUY_LABEL
    return None


def _price_in_range(price: float, low: float | None, high: float | None) -> bool:
    if low is None or high is None:
        return False
    return low <= price <= high


def _execution_status(row: dict) -> str:
    return str(resolve_buy_zone_display_category(row)["displayCategory"])


def _execution_tone(status: str) -> str:
    return {
        "可执行": "green",
        "接近买区": "blue",
        "等回踩": "gray",
        "禁止追高": "red",
        "需复核": "orange",
    }.get(status, "gray")


def _action_short_text(row: dict) -> str:
    status = _execution_status(row)
    action = action_label(_row_action(row))
    zone = str(row.get("currentZone") or "")
    if status == "需复核":
        return "需复核"
    if zone == "no_chase" or "禁止追高" in action:
        return "不新增"
    if "可小仓" in action or "可正常" in action:
        return "可小仓"
    if "等回踩" in action:
        return "等回踩"
    if "只观察" in action:
        return "只观察"
    if "剔除" in action:
        return "剔除"
    return action or status


def _status_detail_text(row: dict) -> str:
    status = _execution_status(row)
    zone = str(row.get("currentZone") or "")
    sanity_label = _buy_point_sanity_label(
        row,
        zone=zone,
        price=_first_number(row.get("currentPrice")),
        trigger=_first_number(row.get("nextTriggerPrice"), row.get("nextBuyPrice")),
    )
    if sanity_label:
        return sanity_label
    if status == "需复核":
        return _row_reason(row)
    if status == "可执行":
        return "已进入买区"
    if status == "接近买区":
        return "接近触发"
    if status == "禁止追高":
        return "禁止追高"
    if zone == "fair_observation":
        return FAIR_OBSERVATION_NOT_BUY_LABEL
    return "等待触发"


def _current_add_text(row: dict) -> tuple[str, str]:
    number = _first_number(row.get("currentAddLimitPercent"))
    if number is not None and number > 0:
        return f"≤{number:.0f}%", "green"
    if _has_final_decision(row) and number is not None and number <= 0:
        return "不新增", "gray"

    status = _execution_status(row)
    action = _row_action(row)
    if status == "需复核":
        return "复核", "gray"
    if status == "禁止追高":
        return "不新增", "gray"
    if "只观察" in action:
        return "观察", "gray"
    if status in {"接近买区", "等回踩"} or "等回踩" in action:
        return "等待", "gray"
    return "观察", "gray"


def _distance_to_trigger_text(row: dict) -> str:
    primary = _distance_to_trigger_primary(row)
    secondary = _distance_to_trigger_secondary(row)
    return f"{primary} · {secondary}" if secondary != "暂无触发价" else primary


def _distance_to_trigger_primary(row: dict) -> str:
    return str(resolve_buy_zone_display_category(row)["triggerPrimary"])


def _is_near_trigger_priority(row: dict) -> bool:
    return bool(resolve_buy_zone_display_category(row)["priorityEligible"])


def _drop_to_trigger_pct(row: dict) -> float | None:
    trigger = _first_number(row.get("nextTriggerPrice"), row.get("nextBuyPrice"))
    price = _first_number(row.get("currentPrice"))
    if trigger is None or trigger <= 0 or price is None or price <= 0:
        return None
    return max((price - trigger) / price * 100, 0)


def _distance_to_trigger_secondary(row: dict) -> str:
    return str(resolve_buy_zone_display_category(row)["triggerSecondary"])


def _row_reason(row: dict) -> str:
    zone = str(row.get("currentZone") or "")
    action = _row_action(row)
    if zone in {"invalid_zone", "invalid_manual_override"} or row.get("isValid") is False:
        return "买区异常，需复核"
    if bool(row.get("validationErrors")):
        return "校验异常，需复核"
    if zone == "data_insufficient":
        return "数据不足"
    if row.get("confidence") == "low" or row.get("dataConfidence") == "low":
        return "低置信，先复核"
    if zone == "no_chase" or action == "禁止追高":
        return "禁止追高"
    return _next_trigger_text(row)


def _trigger_cell_detail(row: dict) -> str:
    _, secondary, _ = format_trigger_cell(row)
    return secondary


def _trigger_cell_html(row: dict) -> str:
    primary, secondary, tone = format_trigger_cell(row)
    return (
        f'<span class="trigger-cell {escape(tone)}">'
        f'<b>{escape(primary)}</b>'
        f'<small>{escape(secondary)}</small>'
        "</span>"
    )


def format_trigger_cell(row: dict) -> tuple[str, str, str]:
    display = resolve_buy_zone_display_category(row)
    return (
        str(display["triggerPrimary"]),
        str(display["triggerSecondary"]),
        str(display.get("triggerTone") or "neutral"),
    )


def _trigger_secondary_text(zone: str, label: str) -> str:
    if label:
        mapped = _next_label(label)
        if mapped not in {"下一买入触发价", "估值折价触发价"}:
            return mapped
    return {
        "fair_observation": "估值折价触发",
        "tranche_buy": "可分批触发",
        "heavy_buy": "极端恐慌区触发",
        "below_heavy_buy": "低于极端恐慌区",
        "no_chase": "等待回踩",
    }.get(zone, "估值触发")


def _needs_review(row: dict) -> bool:
    zone = str(row.get("currentZone") or "")
    return (
        zone in {"invalid_zone", "invalid_manual_override"}
        or bool(row.get("validationErrors"))
        or row.get("isValid") is False
    )


def _next_trigger_text(row: dict) -> str:
    zone = str(row.get("currentZone") or "")
    if zone in {"invalid_zone", "invalid_manual_override"} or row.get("isValid") is False:
        return "买区异常 / 需复核"
    if zone == "data_insufficient":
        return "数据不足"
    if not _valid_price(row.get("currentPrice")):
        return "当前价缺失"
    price = _first_number(row.get("nextTriggerPrice"), row.get("nextBuyPrice"))
    label = str(row.get("nextBuyLabel") or "").strip()
    if price is not None and price > 0:
        return format_currency(price)
    if label:
        return _next_label(label)
    return _zone_next_label(zone)


def _zone_next_trigger_text(zone: BuyZoneEstimate) -> str:
    row = {
        "currentZone": zone.currentZone,
        "currentPrice": zone.currentPrice,
        "nextTriggerPrice": getattr(zone, "nextTriggerPrice", None),
        "nextBuyLabel": getattr(zone, "nextBuyLabel", ""),
        "isValid": getattr(zone, "isValid", True),
        "validationErrors": getattr(zone, "validationErrors", None) or [],
    }
    return _next_trigger_text(row)


def _next_label(value: str) -> str:
    return {
        "买区异常，需复核": "买区异常 / 需复核",
        "已进入可分批区": "已进入可分批区",
        "已低于重仓区": "已低于极端恐慌区",
        "已进入极端恐慌区": "已进入极端恐慌区",
        "已进入买区": "已进入买区",
        "下一买入触发价": "估值折价触发价",
        "估值折价触发价": "估值折价触发价",
        "等待回踩到观察区": "等待回踩",
        "等回踩": "等待回踩",
    }.get(value, value)


def _zone_next_label(zone: str) -> str:
    return {
        "tranche_buy": "已进入可分批区",
        "heavy_buy": "已进入极端恐慌区",
        "below_heavy_buy": "已低于极端恐慌区",
        "fair_observation": "等待估值折价触发",
        "no_chase": "等待回踩",
        "data_insufficient": "数据不足",
        "invalid_zone": "买区异常 / 需复核",
        "invalid_manual_override": "买区异常 / 需复核",
    }.get(zone, "需复核")


def _action_tone(action: str) -> str:
    if action in {"可小仓分批", "可正常分批"}:
        return "green"
    if action in {"等回踩", "只观察"}:
        return "blue"
    if action in {"禁止追高", "剔除"}:
        return "red"
    if "复核" in action:
        return "yellow"
    return "gray"


def _pct_limit(value) -> str:
    number = _first_number(value)
    if number is None:
        return "N/A"
    if number <= 0:
        return "0%"
    return f"≤{number:.0f}%"


def _money(value) -> str:
    number = _first_number(value)
    if number is None or number <= 0:
        return "价格缺失"
    return format_currency(number)


def _price_text(value) -> str:
    number = _first_number(value)
    if number is None or number <= 0:
        return "当前价缺失"
    return format_currency(number)


def _optional_money(value) -> str:
    number = _first_number(value)
    if number is None or number <= 0:
        return "未设置"
    return format_currency(number)


def _valid_price(value) -> bool:
    number = _first_number(value)
    return number is not None and number > 0


def _first_number(*values) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number:
            return number
    return None
