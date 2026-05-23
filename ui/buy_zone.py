from __future__ import annotations

from html import escape
import json
from types import SimpleNamespace

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from buy_zone import BuyZoneInputs, calculate_buy_zone_ladder
from buy_zone_engine import (
    BuyZoneEstimate,
    buy_zone_with_manual_override,
    clear_buy_zone_override_values,
    generate_buy_zone,
    has_buy_zone_override,
)
from data.providers import get_market_data_provider
from data.stock_plan import StockPlanStore
from formatting import format_currency, format_percent
from indicators.technicals import add_technical_indicators, latest_technical_snapshot
from position_plan_engine import PositionPlanSuggestion, generate_position_plan
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
    "no_chase": "禁止追高",
    "fair_observation": "合理观察",
    "tranche_buy": "可分批区",
    "heavy_buy": "重仓击球区",
    "below_heavy_buy": "低于重仓区",
    "data_insufficient": "数据不足",
}
ZONE_TONES = {
    "no_chase": "red",
    "fair_observation": "blue",
    "tranche_buy": "green",
    "heavy_buy": "green",
    "below_heavy_buy": "green",
    "data_insufficient": "gray",
}
CONFIDENCE_TONES = {"high": "green", "medium": "blue", "low": "orange"}


def render() -> None:
    _render_styles()
    render_page_header(
        "买区计划",
        "系统根据评分、估值、风险和技术位置自动生成买区；你可以手动覆盖。",
    )

    tickers = load_watchlist()
    if not tickers:
        st.warning("观察池为空，先到观察池添加股票。")
        _render_valuation_sandbox()
        return

    plan_store = StockPlanStore()
    rows = _load_buy_zone_rows(tuple(tickers))
    rows = [_apply_manual_plan(row, plan_store.get_plan(str(row["symbol"]))) for row in rows]

    _render_summary(rows)
    active_filter = _render_filters(rows)
    visible_rows = _filter_rows(rows, active_filter)
    _render_buy_zone_table(visible_rows, plan_store)
    _render_client_buy_zone_drawers(visible_rows)
    _render_valuation_sandbox()


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
            stock_data = {**snapshot, **technicals}
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
    updated = dict(row)
    updated.update(_zone_plan_fields(active_zone, plan_suggestion, source, has_buy_zone_override(plan)))
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
    base.update(_zone_plan_fields(zone, plan, source, manual))
    return base


def _zone_plan_fields(zone: BuyZoneEstimate, plan: PositionPlanSuggestion, source: str, manual: bool) -> dict:
    return {
        "currentZone": zone.currentZone,
        "zoneLabel": ZONE_LABELS.get(zone.currentZone, zone.currentZone),
        "noChaseAbove": zone.noChaseAbove,
        "fairValueLow": zone.fairValueLow,
        "fairValueHigh": zone.fairValueHigh,
        "trancheBuyLow": zone.trancheBuyLow,
        "trancheBuyHigh": zone.trancheBuyHigh,
        "heavyBuyBelow": zone.heavyBuyBelow,
        "nextBuyPrice": plan.firstBuyPrice,
        "currentAddLimitPercent": plan.currentAddLimitPercent,
        "maxPortfolioWeightPercent": plan.maxPortfolioWeightPercent,
        "confidence": zone.confidence,
        "method": zone.method,
        "inputsUsed": zone.inputsUsed,
        "keyReasons": zone.keyReasons,
        "warnings": zone.warnings,
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
        "进入可分批区": sum(1 for row in rows if row["currentZone"] in {"tranche_buy", "heavy_buy", "below_heavy_buy"}),
        "接近击球区": sum(1 for row in rows if row["currentZone"] == "fair_observation"),
        "禁止追高": sum(1 for row in rows if row["currentZone"] == "no_chase"),
        "待手动配置": sum(1 for row in rows if row["currentZone"] == "data_insufficient"),
        "数据置信度低": sum(1 for row in rows if row["confidence"] == "low" or row.get("dataConfidence") == "low"),
    }
    cards = "".join(
        f'<div class="buy-zone-summary-card"><span>{escape(label)}</span><strong>{value}</strong></div>'
        for label, value in summary.items()
    )
    st.markdown(f'<section class="buy-zone-summary">{cards}</section>', unsafe_allow_html=True)


def _render_filters(rows: list[dict]) -> str:
    options = ["全部", "进入可分批区", "接近击球区", "等回踩", "禁止追高", "数据置信度低", "已手动覆盖"]
    return st.radio("买区筛选", options, horizontal=True, label_visibility="collapsed", key="buy-zone-filter")


def _filter_rows(rows: list[dict], active_filter: str) -> list[dict]:
    if active_filter == "进入可分批区":
        return [row for row in rows if row["currentZone"] in {"tranche_buy", "heavy_buy", "below_heavy_buy"}]
    if active_filter == "接近击球区":
        return [row for row in rows if row["currentZone"] == "fair_observation"]
    if active_filter == "等回踩":
        return [row for row in rows if row.get("action") in {"等回踩", "只观察"}]
    if active_filter == "禁止追高":
        return [row for row in rows if row["currentZone"] == "no_chase" or row.get("action") == "禁止追高"]
    if active_filter == "数据置信度低":
        return [row for row in rows if row["confidence"] == "low" or row.get("dataConfidence") == "low"]
    if active_filter == "已手动覆盖":
        return [row for row in rows if row["manualOverride"]]
    return rows


def _render_buy_zone_table(rows: list[dict], plan_store: StockPlanStore) -> None:
    render_section_title("买区总表", "默认读取观察池，系统建议优先，手动覆盖优先于系统建议")
    if not rows:
        st.info("当前筛选下没有股票。")
        return

    header = """
    <div class="buy-zone-grid buy-zone-grid-head">
      <span>股票代码</span><span>当前价格</span><span>当前区间</span><span>操作建议</span>
      <span>当前新增</span><span>组合上限</span><span>下一买入价</span><span>禁止追高价</span>
      <span>重仓区</span><span>数据置信度</span><span>买区来源</span><span>操作</span>
    </div>
    """
    body = "".join(_buy_zone_row_html(row) for row in rows)
    st.markdown(f'<section class="buy-zone-table">{header}{body}</section>', unsafe_allow_html=True)

    action_cols = st.columns([1, 1, 4])
    with action_cols[0]:
        symbol = st.selectbox("手动覆盖股票", [str(row["symbol"]) for row in rows], key="buy-zone-manual-symbol")
    selected = next((row for row in rows if row["symbol"] == symbol), None)
    with action_cols[1]:
        if selected and st.button("恢复系统建议", width="stretch"):
            plan_store.save_plan(symbol, clear_buy_zone_override_values(plan_store.get_plan(symbol)))
            _load_buy_zone_rows.clear()
            st.toast(f"{symbol} 已恢复系统建议")
            st.rerun()
    with action_cols[2]:
        st.caption("点击表格里的“详情”可打开右侧 BuyZoneDrawer；手动覆盖先在单股详情或下方高级设置保存。")


def _buy_zone_row_html(row: dict) -> str:
    symbol = str(row["symbol"])
    source_label = "手动买区" if row["manualOverride"] else "系统建议"
    source_tone = "blue" if row["manualOverride"] else "gray"
    return (
        '<div class="buy-zone-grid buy-zone-row">'
        f'<strong>{escape(symbol)}</strong>'
        f'<span class="num">{escape(_money(row.get("currentPrice")))}</span>'
        f'{_badge(row["zoneLabel"], ZONE_TONES.get(row["currentZone"], "gray"))}'
        f'{_badge(action_label(row.get("action")), _action_tone(str(row.get("action") or "")))}'
        f'<span>{_pct_limit(row.get("currentAddLimitPercent"))}</span>'
        f'<span>{_pct_limit(row.get("maxPortfolioWeightPercent"))}</span>'
        f'<span class="num">{escape(_money(row.get("nextBuyPrice")))}</span>'
        f'<span class="num">{escape(_money(row.get("noChaseAbove")))}</span>'
        f'<span class="num">{escape(_money(row.get("heavyBuyBelow")))}</span>'
        f'{_badge(confidence_label(row.get("confidence")), CONFIDENCE_TONES.get(row.get("confidence"), "gray"))}'
        f'{_badge(source_label, source_tone)}'
        f'<a class="buy-zone-detail-link" href="#" data-buy-zone-drawer-open="{escape(symbol)}">详情</a>'
        "</div>"
    )


def _render_client_buy_zone_drawers(rows: list[dict]) -> None:
    payload = {str(row["symbol"]).upper(): _buy_zone_drawer_html(row) for row in rows}
    if not payload:
        return
    components.html(
        f"""
        <script>
        (() => {{
          const win = window.parent;
          const doc = win.document;
          win.__buyZoneDrawerPayload = {json.dumps(payload, ensure_ascii=False)};
          let root = doc.getElementById("buy-zone-client-drawer-root");
          if (!root) {{
            root = doc.createElement("div");
            root.id = "buy-zone-client-drawer-root";
            doc.body.appendChild(root);
          }}
          function closeDrawer() {{
            root.classList.remove("is-open");
            root.innerHTML = "";
            doc.body.classList.remove("dashboard-drawer-open");
          }}
          function openDrawer(symbol) {{
            const key = String(symbol || "").toUpperCase();
            const html = win.__buyZoneDrawerPayload && win.__buyZoneDrawerPayload[key];
            if (!html) return;
            root.innerHTML = html;
            root.classList.add("is-open");
            doc.body.classList.add("dashboard-drawer-open");
          }}
          if (!win.__buyZoneDrawerDelegationBound) {{
            win.__buyZoneDrawerDelegationBound = true;
            doc.addEventListener("click", (event) => {{
              const target = event.target instanceof win.Element ? event.target : event.target && event.target.parentElement;
              if (!(target instanceof win.Element)) return;
              const opener = target.closest("[data-buy-zone-drawer-open]");
              if (opener) {{
                event.preventDefault();
                openDrawer(opener.getAttribute("data-buy-zone-drawer-open"));
                return;
              }}
              if (target.closest("[data-buy-zone-drawer-close]") || target.classList.contains("buy-zone-drawer-backdrop")) {{
                event.preventDefault();
                closeDrawer();
              }}
            }}, true);
            doc.addEventListener("keydown", (event) => {{
              if (event.key === "Escape") closeDrawer();
            }});
          }}
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def _buy_zone_drawer_html(row: dict) -> str:
    symbol = str(row["symbol"])
    zone: BuyZoneEstimate = row["activeZone"]
    reasons = "".join(f"<li>{escape(reason)}</li>" for reason in (row.get("keyReasons") or [])[:6])
    warnings = "".join(f"<li>{escape(warning)}</li>" for warning in (row.get("warnings") or [])[:5])
    return (
        '<div class="buy-zone-drawer-backdrop"></div>'
        '<aside class="stock-drawer buy-zone-drawer">'
        '<a class="drawer-close-link" href="#" data-buy-zone-drawer-close="1" title="关闭">×</a>'
        '<div class="drawer-topline">BuyZoneDrawer</div>'
        f'<div class="drawer-head"><div><div class="drawer-symbol">{escape(symbol)}</div>'
        f'<div class="drawer-company">{escape(str(row.get("companyName") or ""))}</div></div>'
        f'<div class="drawer-price">{escape(_money(row.get("currentPrice")))}</div></div>'
        '<div class="drawer-badges">'
        f'{_badge(row["zoneLabel"], ZONE_TONES.get(row["currentZone"], "gray"))}'
        f'{_badge(action_label(row.get("action")), _action_tone(str(row.get("action") or "")))}'
        f'{_badge(model_type_label(row.get("modelType")), "gray")}'
        "</div>"
        '<div class="drawer-position-card">'
        '<div class="drawer-card-title">顶部结论</div>'
        f'<div class="drawer-decision-headline">当前处于 {escape(row["zoneLabel"])}，当前新增建议 {_pct_limit(row.get("currentAddLimitPercent"))}，组合仓位上限 {_pct_limit(row.get("maxPortfolioWeightPercent"))}。</div>'
        "</div>"
        '<div class="drawer-section-title">系统建议击球区</div>'
        f'{_price_ladder_html(row)}'
        '<div class="drawer-section-title">生成依据</div>'
        f'<div class="drawer-resolution"><b>输入</b><ul>{"".join(f"<li>{escape(str(item))}</li>" for item in row.get("inputsUsed", [])[:8]) or "<li>暂无可用输入</li>"}</ul></div>'
        f'<div class="drawer-resolution"><b>原因</b><ul>{reasons or "<li>暂无说明</li>"}</ul></div>'
        f'<div class="drawer-resolution"><b>提醒</b><ul>{warnings or "<li>暂无重大提醒</li>"}</ul></div>'
        '<div class="drawer-section-title">操作计划</div>'
        f'{_plan_html(row)}'
        '<div class="drawer-section-title">手动覆盖</div>'
        '<div class="drawer-muted">手动覆盖优先于系统建议。编辑和保存可在单股详情页完成；本页可快速恢复系统建议。</div>'
        "</aside>"
    )


def _price_ladder_html(row: dict) -> str:
    bands = [
        ("禁止追高", row.get("noChaseAbove")),
        ("合理观察区", f"{_money(row.get('fairValueLow'))} - {_money(row.get('fairValueHigh'))}"),
        ("可分批区", f"{_money(row.get('trancheBuyLow'))} - {_money(row.get('trancheBuyHigh'))}"),
        ("重仓击球区", row.get("heavyBuyBelow")),
    ]
    items = "".join(f"<li><span>{escape(label)}</span><b>{escape(_money(value) if not isinstance(value, str) else value)}</b></li>" for label, value in bands)
    return f'<div class="price-ladder"><ul>{items}</ul><div class="price-marker">当前价格：{escape(_money(row.get("currentPrice")))}</div></div>'


def _plan_html(row: dict) -> str:
    items = [
        ("第一笔买入价", _money(row.get("firstBuyPrice"))),
        ("第二笔买入价", _money(row.get("secondBuyPrice"))),
        ("第三笔买入价", _money(row.get("thirdBuyPrice"))),
        ("禁止追高价", _money(row.get("noChaseAbove"))),
        ("停止加仓条件", str(row.get("stopAddingCondition") or "")),
        ("财报复核条件", str(row.get("earningsReviewCondition") or "")),
    ]
    html = "".join(f"<li><span>{escape(label)}</span><b>{escape(value)}</b></li>" for label, value in items)
    return f'<div class="drawer-resolution plan-list"><ul>{html}</ul></div>'


def _render_valuation_sandbox() -> None:
    with st.expander("高级估值沙盒", expanded=False):
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
        metrics[3].metric("重仓买入区", format_currency(output["heavy_buy_zone_price"]))
        tranches = output["tranches"].rename(
            columns={"Tranche": "分批", "Buy Price": "买入价", "Allocation %": "分配比例", "Allocation $": "分配金额", "Estimated Shares": "估算股数"}
        )
        st.dataframe(tranches, width="stretch", hide_index=True)
        _render_price_ladder_chart(output)


def _render_price_ladder_chart(output: dict) -> None:
    labels = ["禁止追高", "试探", "正常买入", "重仓", "恐慌"]
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
        .buy-zone-summary {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 0.75rem;
            margin: 0.8rem 0 1.1rem;
        }
        .buy-zone-summary-card {
            border: 1px solid var(--dash-border, #E5E7EB);
            background: #fff;
            border-radius: 12px;
            padding: 0.85rem 1rem;
        }
        .buy-zone-summary-card span { color: #64748B; font-size: 0.8rem; font-weight: 700; }
        .buy-zone-summary-card strong { display:block; margin-top:0.25rem; font-size:1.25rem; color:#111827; }
        .buy-zone-table {
            border: 1px solid #E5E7EB;
            border-radius: 12px;
            overflow: hidden;
            background: #fff;
            margin-bottom: 0.85rem;
        }
        .buy-zone-grid {
            display: grid;
            grid-template-columns: 86px 96px 110px 126px 92px 92px 100px 104px 96px 104px 104px 74px;
            align-items: center;
            gap: 0.55rem;
            min-height: 44px;
            padding: 0 0.8rem;
            border-bottom: 1px solid #EEF2F7;
            font-size: 0.84rem;
        }
        .buy-zone-grid-head {
            position: sticky;
            top: 0;
            z-index: 1;
            background: #F8FAFC;
            color: #64748B;
            font-size: 0.76rem;
            font-weight: 800;
        }
        .buy-zone-row:hover { background: #F8FAFC; }
        .buy-zone-row .num { font-variant-numeric: tabular-nums; text-align: right; }
        .buy-zone-badge {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            min-height:24px;
            padding:0 8px;
            border-radius:999px;
            border:1px solid #E5E7EB;
            background:#F3F4F6;
            color:#4B5563;
            font-size:12px;
            font-weight:700;
            white-space:nowrap;
        }
        .buy-zone-badge.green { background:#EAF8F0; color:#166534; border-color:#BBF7D0; }
        .buy-zone-badge.blue { background:#EFF6FF; color:#1D4ED8; border-color:#BFDBFE; }
        .buy-zone-badge.yellow { background:#FEFCE8; color:#854D0E; border-color:#FDE68A; }
        .buy-zone-badge.orange { background:#FFF7ED; color:#C2410C; border-color:#FDBA74; }
        .buy-zone-badge.red { background:#FEF2F2; color:#B91C1C; border-color:#FECACA; }
        .buy-zone-detail-link {
            text-decoration:none;
            color:#1D4ED8;
            font-weight:800;
        }
        .buy-zone-drawer .price-ladder,
        .buy-zone-drawer .drawer-resolution {
            border:1px solid #E5E7EB;
            border-radius:12px;
            padding:0.8rem;
            margin-bottom:0.75rem;
            background:#fff;
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
            border-radius:10px;
            background:#EFF6FF;
            color:#1D4ED8;
            font-weight:800;
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
            border-radius:12px;
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
                grid-template-columns: 76px 86px 100px 112px 78px 78px 90px 94px 86px 92px 94px 62px;
                font-size:0.78rem;
                gap:0.4rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _badge(label: str, tone: str = "gray") -> str:
    return f'<span class="buy-zone-badge {escape(tone)}">{escape(str(label))}</span>'


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
