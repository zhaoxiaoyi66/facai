from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape
import json

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from ui.metric_labels import model_type_label, resolution_status_label


DRAWER_SYMBOL_SESSION_KEY = "dashboard_drawer_symbol"
DRAWER_FOCUS_SESSION_KEY = "dashboard_drawer_focus"


@dataclass(frozen=True)
class DashboardDrawerDeps:
    badge_span_html: Callable[[object, str, str], str]
    badge_color_for_cell: Callable[[str, object, pd.Series | None], str]
    translated_join: Callable[..., str]
    quality_negative_items: Callable[[pd.Series], list[str]]
    risk_items: Callable[[pd.Series], list[str]]
    resolution_value_text: Callable[[dict[str, object]], str]
    clean_resolution_explanation: Callable[[str], str]
    dedupe_text: Callable[[list[str]], list[str]]
    metric_resolution_groups: Callable[[object], dict[str, list[dict[str, object]]]]
    drawer_actionable_resolution_row: Callable[[dict[str, object]], str]
    drawer_calculated_resolution_row: Callable[[dict[str, object]], str]
    drawer_low_priority_resolution_row: Callable[[dict[str, object]], str]
    detail_groups: Sequence[tuple[str, Sequence[tuple[str, str]]]]


_DRAWER_DEPS: DashboardDrawerDeps | None = None


def configure_dashboard_drawer(deps: DashboardDrawerDeps) -> None:
    global _DRAWER_DEPS
    _DRAWER_DEPS = deps


def _drawer_deps(deps: DashboardDrawerDeps | None = None) -> DashboardDrawerDeps:
    if deps is not None:
        return deps
    if _DRAWER_DEPS is None:
        raise RuntimeError("Dashboard drawer dependencies are not configured.")
    return _DRAWER_DEPS


def queue_stock_detail_drawer(symbol: str, focus: str | None = None) -> None:
    st.session_state[DRAWER_SYMBOL_SESSION_KEY] = str(symbol or "").upper()
    if focus:
        st.session_state[DRAWER_FOCUS_SESSION_KEY] = str(focus)
    else:
        st.session_state.pop(DRAWER_FOCUS_SESSION_KEY, None)


def drawer_open_menu_html(symbol: str, label: str, focus: str | None = None) -> str:
    normalized_symbol = str(symbol or "").upper()
    safe_symbol = escape(normalized_symbol)
    focus_attr = f' data-dashboard-drawer-focus="{escape(focus)}"' if focus else ""
    focus_js = json.dumps(str(focus) if focus else None, ensure_ascii=False)
    onclick = (
        "event.preventDefault();event.stopPropagation();"
        f"if(window.__dashboardOpenDrawer){{window.__dashboardOpenDrawer({json.dumps(normalized_symbol, ensure_ascii=False)},{focus_js});}}"
        "return false;"
    )
    return (
        f'<button type="button" class="drawer-menu-link" data-dashboard-drawer-open="{safe_symbol}"{focus_attr} '
        f'onclick="{escape(onclick, quote=True)}" title="打开 {safe_symbol} 右侧详情面板">{escape(label)}</button>'
    )


def render_client_stock_detail_drawers(table: pd.DataFrame, deps: DashboardDrawerDeps | None = None) -> None:
    drawer_deps = _drawer_deps(deps)
    drawer_payload: dict[str, str] = {}
    for _, row in table.iterrows():
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            drawer_payload[symbol] = drawer_html(row, drawer_deps)
    if not drawer_payload:
        return
    auto_open_symbol = str(st.session_state.pop(DRAWER_SYMBOL_SESSION_KEY, "") or "").upper()
    auto_open_focus = st.session_state.pop(DRAWER_FOCUS_SESSION_KEY, None)
    components.html(
        f"""
        <script>
        (() => {{
          const win = window.parent;
          const doc = win.document;
          win.__dashboardDrawerPayload = {json.dumps(drawer_payload, ensure_ascii=False)};
          const autoOpenSymbol = {json.dumps(auto_open_symbol, ensure_ascii=False)};
          const autoOpenFocus = {json.dumps(auto_open_focus, ensure_ascii=False)};
          let root = doc.getElementById("dashboard-client-drawer-root");
          if (!root) {{
            root = doc.createElement("div");
            root.id = "dashboard-client-drawer-root";
            doc.body.appendChild(root);
          }}
          function closeDrawer() {{
            root.classList.remove("is-open");
            root.innerHTML = "";
            doc.body.classList.remove("dashboard-drawer-open");
          }}
          function showDrawerMessage(message) {{
            const note = root.querySelector("[data-dashboard-drawer-action-note]");
            if (!note) {{
              return;
            }}
            note.textContent = message || "";
            if (message) {{
              note.removeAttribute("hidden");
            }} else {{
              note.setAttribute("hidden", "hidden");
            }}
          }}
          function focusDrawerSection(focusKey) {{
            if (!focusKey) {{
              return;
            }}
            const target = root.querySelector(`[data-drawer-section="${{focusKey}}"]`);
            if (target && typeof target.scrollIntoView === "function") {{
              target.scrollIntoView({{behavior: "smooth", block: "start"}});
              target.classList.add("drawer-section-pulse");
              win.setTimeout(() => target.classList.remove("drawer-section-pulse"), 900);
            }}
          }}
          function openDrawer(symbol, focusKey) {{
            const key = String(symbol || "").toUpperCase();
            const html = win.__dashboardDrawerPayload && win.__dashboardDrawerPayload[key];
            if (!html) {{
              return;
            }}
            root.innerHTML = html;
            root.classList.add("is-open");
            doc.body.classList.add("dashboard-drawer-open");
            focusDrawerSection(focusKey);
          }}
          root.onclick = (event) => {{
            const rawTarget = event.target;
            const target = rawTarget instanceof win.Element ? rawTarget : rawTarget && rawTarget.parentElement;
            if (!(target instanceof win.Element)) {{
              return;
            }}
            const closer = target.closest("[data-dashboard-drawer-close]");
            const backdrop = target.closest(".drawer-backdrop");
            if (closer || backdrop) {{
              event.preventDefault();
              event.stopPropagation();
              closeDrawer();
              return;
            }}
            const messageAction = target.closest("[data-dashboard-drawer-message]");
            if (messageAction) {{
              event.preventDefault();
              event.stopPropagation();
              focusDrawerSection(messageAction.getAttribute("data-dashboard-drawer-focus"));
              showDrawerMessage(messageAction.getAttribute("data-dashboard-drawer-message"));
            }}
          }};
          win.__dashboardOpenDrawer = openDrawer;
          win.__dashboardCloseDrawer = closeDrawer;
          if (autoOpenSymbol) {{
            win.setTimeout(() => openDrawer(autoOpenSymbol, autoOpenFocus), 0);
          }}
          if (win.__dashboardDrawerClickHandler) {{
            doc.removeEventListener("click", win.__dashboardDrawerClickHandler, true);
          }}
          win.__dashboardDrawerClickHandler = (event) => {{
              const rawTarget = event.target;
              const target = rawTarget instanceof win.Element ? rawTarget : rawTarget && rawTarget.parentElement;
              if (!(target instanceof win.Element)) {{
                return;
              }}
              if (target.closest(".dashboard-record-action")) {{
                return;
              }}
              const opener = target.closest("[data-dashboard-drawer-open]");
              if (opener) {{
                event.preventDefault();
                event.stopPropagation();
                const open = win.__dashboardOpenDrawer || openDrawer;
                open(
                  opener.getAttribute("data-dashboard-drawer-open"),
                  opener.getAttribute("data-dashboard-drawer-focus")
                );
                return;
              }}
              const closer = target.closest("[data-dashboard-drawer-close]");
              if (closer) {{
                event.preventDefault();
                event.stopPropagation();
                closeDrawer();
                return;
              }}
              if (target.classList.contains("drawer-backdrop")) {{
                event.preventDefault();
                event.stopPropagation();
                closeDrawer();
                return;
              }}
              const messageAction = target.closest("[data-dashboard-drawer-message]");
              if (messageAction) {{
                event.preventDefault();
                event.stopPropagation();
                focusDrawerSection(messageAction.getAttribute("data-dashboard-drawer-focus"));
                showDrawerMessage(messageAction.getAttribute("data-dashboard-drawer-message"));
                return;
              }}
          }};
          doc.addEventListener("click", win.__dashboardDrawerClickHandler, true);
          if (!win.__dashboardDrawerKeydownBound) {{
            win.__dashboardDrawerKeydownBound = true;
            doc.addEventListener("keydown", (event) => {{
              if (event.key === "Escape") {{
                const close = win.__dashboardCloseDrawer || closeDrawer;
                close();
              }}
            }});
          }}
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def render_stock_detail_drawer(table: pd.DataFrame, deps: DashboardDrawerDeps | None = None) -> None:
    drawer_deps = _drawer_deps(deps)
    selected = st.session_state.get(DRAWER_SYMBOL_SESSION_KEY)
    if not selected:
        return
    matches = table[table["symbol"].astype(str) == str(selected)]
    if matches.empty:
        st.session_state.pop(DRAWER_SYMBOL_SESSION_KEY, None)
        return
    row = matches.iloc[0]
    st.markdown(drawer_html(row, drawer_deps), unsafe_allow_html=True)
    st.caption("右侧详情面板只做快速查看；数据补全和复核操作请进入专门页面执行，避免刷新总览。")


def drawer_html(row: pd.Series, deps: DashboardDrawerDeps | None = None) -> str:
    drawer_deps = _drawer_deps(deps)
    summary = row.get("humanReadableSummary")
    if not isinstance(summary, dict):
        summary = {}
    symbol = str(row.get("symbol") or "").upper()
    safe_symbol = escape(symbol)
    badges = [
        drawer_deps.badge_span_html(row.get("qualityRating"), drawer_deps.badge_color_for_cell("qualityRating", row.get("qualityRating"), row)),
        drawer_deps.badge_span_html(row.get("entryRating"), drawer_deps.badge_color_for_cell("entryRating", row.get("entryRating"), row)),
        drawer_deps.badge_span_html(row.get("riskRating"), drawer_deps.badge_color_for_cell("riskRating", row.get("riskRating"), row)),
        drawer_deps.badge_span_html(row.get("action"), drawer_deps.badge_color_for_cell("action", row.get("action"), row)),
    ]
    explanation_cards = [
        _drawer_card_html("公司质量解释", str(row.get("qualityRating") or "N/A"), [
            "主要加分：" + drawer_deps.translated_join(row.get("keyPositiveDrivers"), limit=4),
            "主要扣分：" + drawer_deps.translated_join(drawer_deps.quality_negative_items(row), limit=4),
            str(summary.get("quality") or ""),
        ]),
        _drawer_card_html("买点解释", str(row.get("entryRating") or "N/A"), [
            str(summary.get("valuation") or ""),
            str(summary.get("technical") or ""),
            str(summary.get("entry") or ""),
            _entry_context_note(row),
        ]),
        _drawer_card_html("风险解释", str(row.get("riskRating") or "N/A"), [
            "风险来源：" + drawer_deps.translated_join(drawer_deps.risk_items(row), limit=4),
            str(summary.get("risk") or ""),
            _risk_context_note(row),
        ]),
    ]
    return (
        '<div class="drawer-backdrop"></div>'
        '<aside class="stock-drawer">'
        '<a class="drawer-close-link" href="#" data-dashboard-drawer-close="1" title="关闭右侧详情面板">×</a>'
        '<div class="drawer-topline">右侧详情面板</div>'
        '<div class="drawer-head">'
        '<div>'
        f'<div class="drawer-symbol">{safe_symbol}</div>'
        f'<div class="drawer-company">{escape(str(row.get("companyName") or "公司名待补充"))}</div>'
        '</div>'
        f'<div class="drawer-price">{escape(str(row.get("price") or "N/A"))}</div>'
        '</div>'
        '<div class="drawer-meta-grid">'
        f'<span>模型：{escape(model_type_label(row.get("modelType")))}</span>'
        f'<span>市值：{escape(str(row.get("marketCap") or "N/A"))}</span>'
        f'<span>当前新增：{escape(str(row.get("currentAddLimit") or row.get("maxSuggestedPosition") or "N/A"))}</span>'
        f'<span>数据：{escape(str(row.get("dataStatus") or "N/A"))}</span>'
        '</div>'
        f'<div class="drawer-badges">{"".join(badges)}</div>'
        f'<div class="drawer-signal-actions"><a href="?page=dashboard&recordSignal={safe_symbol}" target="_self">记录当前信号</a></div>'
        f'{_drawer_decision_summary_html(row, drawer_deps)}'
        f'{_drawer_position_guidance_html(row)}'
        f'<div class="drawer-section">{"".join(explanation_cards)}</div>'
        f'{_drawer_industry_metrics_html(row, drawer_deps)}'
        '<div class="drawer-section-title">数据复核状态</div>'
        f'{drawer_review_summary_html(row)}'
        '<div data-drawer-section="resolution">'
        '<div class="drawer-section-title">数据补全状态</div>'
        f'{_drawer_resolution_html(row, drawer_deps)}'
        '</div>'
        '<details class="drawer-raw"><summary>原始指标</summary>'
        f'{_drawer_raw_metrics_html(row, drawer_deps)}'
        '</details>'
        '</aside>'
    )


def _drawer_decision_summary_html(row: pd.Series, deps: DashboardDrawerDeps | None = None) -> str:
    drawer_deps = _drawer_deps(deps)
    symbol = str(row.get("symbol") or "该股票")
    model = model_type_label(row.get("modelType"))
    action = str(row.get("action") or "只观察")
    quality = str(row.get("qualityRating") or "N/A")
    entry = str(row.get("entryRating") or "N/A")
    risk = str(row.get("riskRating") or "N/A")
    summary = row.get("humanReadableSummary")
    if not isinstance(summary, dict):
        summary = {}
    conclusion = _decision_conclusion_text(row, symbol, model, action)
    why = _decision_why_text(row, quality, entry, risk, summary)
    wait_items = "".join(f"<li>{escape(item)}</li>" for item in _waiting_conditions(row, drawer_deps))
    return (
        '<div class="drawer-decision-card">'
        '<div class="drawer-card-title">当前结论</div>'
        f'<div class="drawer-decision-headline">{escape(conclusion)}</div>'
        f'<p>{escape(why)}</p>'
        '<div class="drawer-decision-grid">'
        f'<span><b>当前新增建议</b><strong>{escape(str(row.get("currentAddLimit") or row.get("maxSuggestedPosition") or "N/A"))}</strong></span>'
        f'<span><b>组合仓位上限</b><strong>{escape(str(row.get("maxPortfolioWeight") or "N/A"))}</strong></span>'
        '</div>'
        '<div class="drawer-waiting"><b>等待条件</b><ul>'
        f'{wait_items or "<li>等待估值、趋势或关键经营数据进一步确认。</li>"}'
        '</ul></div>'
        '</div>'
    )


def _drawer_position_guidance_html(row: pd.Series) -> str:
    return (
        '<div class="drawer-position-card" data-drawer-section="position">'
        '<div>'
        '<span>当前新增建议</span>'
        f'<strong>{escape(str(row.get("currentAddLimit") or row.get("maxSuggestedPosition") or "N/A"))}</strong>'
        '<em>由买点评级、估值位置和当前趋势决定。</em>'
        '</div>'
        '<div>'
        '<span>组合仓位上限</span>'
        f'<strong>{escape(str(row.get("maxPortfolioWeight") or "N/A"))}</strong>'
        '<em>由公司质量和基本面风险决定。</em>'
        '</div>'
        '</div>'
    )


def _drawer_industry_metrics_html(row: pd.Series, deps: DashboardDrawerDeps | None = None) -> str:
    drawer_deps = _drawer_deps(deps)
    model_type = str(row.get("modelType") or "")
    if model_type != "MEGA_CAP_PLATFORM":
        return ""
    resolution_rows = row.get("metricResolutionStatus")
    rows = [item for item in resolution_rows if isinstance(item, dict)] if isinstance(resolution_rows, (list, tuple)) else []
    definitions = [
        ("cloudRevenueGrowth", "云业务增长", "需抓取IR / 8-K", "关注云业务收入增速和订单延续性。"),
        ("azureCloudGrowth", "Azure / Cloud 增速", "需抓取IR / 8-K", "关注 Azure 或核心云业务增速。"),
        ("aiCapexOverbuildRisk", "AI资本开支压力", "规则推导", "基于 Capex、FCF 和 AI 投入压力推导。"),
        ("fcfMargin", "FCF转化率", "已计算", "优先使用自由现金流 / 收入。"),
        ("buybackDiscipline", "回购纪律", "规则推导", "基于回购金额、FCF覆盖和股本变化推导。"),
        ("segmentStrength", "分部业务强度", "规则推导", "基于分部增长、利润率和平台业务表现推导。"),
        ("regulatoryRisk", "监管风险", "半自动低置信度", "监管/反垄断风险需保留复核。"),
    ]
    items = []
    for key, label, fallback_status, fallback_explanation in definitions:
        item = _find_metric_resolution(rows, key)
        if item:
            status = resolution_status_label(item.get("resolutionStatus"))
            value = drawer_deps.resolution_value_text(item).split("｜", 1)[0]
            explanation = drawer_deps.clean_resolution_explanation(str(item.get("explanation") or fallback_explanation))
        else:
            status = fallback_status
            value = "待补齐" if "抓取" in fallback_status else "规则推导"
            explanation = fallback_explanation
        items.append(
            "<li>"
            f"<span>{escape(label)}</span>"
            f"<strong>{escape(value)}</strong>"
            f"<em>{escape(status)}｜{escape(explanation)}</em>"
            "</li>"
        )
    return (
        '<div class="drawer-section-title">行业专属指标</div>'
        '<div class="drawer-industry-card"><ul>'
        f'{"".join(items)}'
        '</ul></div>'
    )


def _find_metric_resolution(rows: list[dict[str, object]], metric_key: str) -> dict[str, object] | None:
    target = metric_key.lower()
    for item in rows:
        key = str(item.get("metricKey") or "").lower()
        display = str(item.get("displayName") or "").lower()
        if key == target:
            return item
        if target == "azurecloudgrowth" and ("azure" in display or "cloud growth" in display):
            return item
        if target == "cloudrevenuegrowth" and "cloud revenue" in display:
            return item
    return None


def _decision_conclusion_text(row: pd.Series, symbol: str, model: str, action: str) -> str:
    quality = str(row.get("qualityRating") or "")
    risk = str(row.get("riskRating") or "")
    entry = str(row.get("entryRating") or "")
    if _is_high_quality_text(quality) and risk == "低" and _is_observe_or_wait_action(action, entry):
        return f"{symbol} 是高质量{model}，但当前买点一般，适合{_short_action_for_sentence(action)}。"
    if "数据" in str(row.get("dataStatus") or "") or row.get("dataConfidence") == "low":
        return f"{symbol} 的评分仍受数据置信度限制，先复核关键数据再提高仓位。"
    return f"{symbol} 当前动作是{_short_action_for_sentence(action)}，先按仓位纪律执行。"


def _decision_why_text(row: pd.Series, quality: str, entry: str, risk: str, summary: dict[str, str]) -> str:
    action = str(row.get("action") or "")
    if _is_high_quality_text(quality) and risk == "低" and _is_observe_or_wait_action(action, entry):
        return f"公司风险低，但买点评级为 {entry}，当前新增仓位受限；这不是公司质量问题，而是买点不够理想。"
    parts = [str(summary.get("entry") or ""), str(summary.get("technical") or ""), str(summary.get("valuation") or "")]
    text = " ".join(part for part in parts if part).strip()
    return text or "当前建议由质量、买点、风险、估值和数据置信度综合得出。"


def _waiting_conditions(row: pd.Series, deps: DashboardDrawerDeps | None = None) -> list[str]:
    drawer_deps = _drawer_deps(deps)
    model_type = str(row.get("modelType") or "")
    action = str(row.get("action") or "")
    items: list[str] = []
    if _is_observe_or_wait_action(action, str(row.get("entryRating") or "")):
        items.append("重新站上 EMA200 或趋势明显修复")
        items.append("估值回落到更舒服区间")
    if model_type == "MEGA_CAP_PLATFORM":
        items.append("Azure / Cloud / AI 收入继续兑现")
    if row.get("dataConfidence") in {"low", "medium"}:
        items.append("关键经营指标和复核队列完成确认")
    return drawer_deps.dedupe_text(items)[:4]


def _entry_context_note(row: pd.Series) -> str:
    action = str(row.get("action") or "")
    if _is_high_quality_text(str(row.get("qualityRating") or "")) and _is_observe_or_wait_action(action, str(row.get("entryRating") or "")):
        return "只观察不是因为公司质量差，而是因为当前买点不够理想。"
    return ""


def _risk_context_note(row: pd.Series) -> str:
    if str(row.get("riskRating") or "") == "低":
        return "风险评级低代表公司基本面风险较低，不等于当前价格值得追。"
    return ""


def _is_high_quality_text(value: str) -> bool:
    return value.startswith(("A+", "A", "A-", "B+"))


def _is_observe_or_wait_action(action: str, entry: str) -> bool:
    return any(token in action for token in ("只观察", "等回踩", "待复核")) or entry.startswith(("B", "C", "D"))


def _short_action_for_sentence(action: str) -> str:
    if "等回踩" in action:
        return "等回踩"
    if "小仓" in action:
        return "小仓观察"
    if "分批" in action:
        return "分批执行"
    if "禁止" in action:
        return "禁止追高"
    if "复核" in action:
        return "待复核"
    return action or "只观察"


def _drawer_card_html(title: str, headline: str, lines: list[str]) -> str:
    items = "".join(f"<li>{escape(str(line))}</li>" for line in lines if line)
    return (
        '<div class="drawer-card">'
        f'<div class="drawer-card-title">{escape(title)}</div>'
        f'<div class="drawer-card-headline">{escape(str(headline))}</div>'
        f"<ul>{items}</ul>"
        "</div>"
    )


def _drawer_resolution_html(row: pd.Series, deps: DashboardDrawerDeps | None = None) -> str:
    drawer_deps = _drawer_deps(deps)
    groups = drawer_deps.metric_resolution_groups(row.get("metricResolutionStatus"))
    if not groups:
        return '<div class="drawer-muted">暂无补全状态</div>'
    blocks: list[str] = []
    key_items = groups.get("关键待补齐", [])
    if key_items:
        rows = "".join(drawer_deps.drawer_actionable_resolution_row(item) for item in key_items[:6])
        blocks.append(f'<div class="drawer-resolution priority-high"><b>关键待补齐</b><ul>{rows}</ul></div>')

    auto_items = groups.get("可自动补齐", [])
    if auto_items:
        rows = "".join(drawer_deps.drawer_actionable_resolution_row(item) for item in auto_items[:6])
        blocks.append(f'<div class="drawer-resolution"><b>可自动补齐</b><ul>{rows}</ul></div>')

    calculated_items = groups.get("已计算摘要", [])
    if calculated_items:
        rows = "".join(drawer_deps.drawer_calculated_resolution_row(item) for item in calculated_items[:5])
        extra = ""
        if len(calculated_items) > 5:
            extra_rows = "".join(drawer_deps.drawer_calculated_resolution_row(item) for item in calculated_items[5:])
            extra = f'<details class="drawer-low-priority"><summary>展开全部已计算指标</summary><ul>{extra_rows}</ul></details>'
        blocks.append(f'<div class="drawer-resolution"><b>已计算摘要</b><ul>{rows}</ul>{extra}</div>')

    low_items = groups.get("低优先级 / 仅解释项", [])
    if low_items:
        rows = "".join(drawer_deps.drawer_low_priority_resolution_row(item) for item in low_items[:12])
        blocks.append(
            '<details class="drawer-resolution drawer-low-priority">'
            '<summary>低优先级 / 仅解释项</summary>'
            f'<ul>{rows}</ul>'
            '</details>'
        )
    return "".join(blocks) if blocks else '<div class="drawer-muted">暂无关键待补齐项。</div>'


def drawer_review_summary_html(row: pd.Series) -> str:
    summary = row.get("reviewQueueSummary")
    if not isinstance(summary, dict) or not summary.get("total"):
        summary = row.get("disclosureReviewSummary")
    if not isinstance(summary, dict) or not summary.get("total"):
        return '<div class="drawer-muted">当前无待复核的自动抽取数据。部分指标仍可通过规则推导或人工补充完善。</div>'
    primary_items = [
        ("待确认", summary.get("pending_review", 0)),
        ("需要补齐", summary.get("needs_data", 0)),
        ("低置信度推导", summary.get("derived_low_confidence", 0)),
        ("定性风险", summary.get("qualitative_risk", 0)),
        ("AI建议修正", summary.get("ai_recommend_correct", 0)),
        ("AI建议驳回", summary.get("ai_recommend_reject", 0)),
        ("证据不足", summary.get("ai_not_enough_evidence", 0)),
    ]
    all_items = [
        *primary_items,
        ("已确认", summary.get("approved", 0)),
        ("已驳回", summary.get("rejected", 0)),
        ("AI自动确认", summary.get("auto_approved_by_ai", 0)),
        ("需要人工判断", summary.get("ai_needs_human_review", 0)),
    ]
    visible_items = [(label, int(value or 0)) for label, value in primary_items if int(value or 0) > 0]
    if not visible_items:
        visible_items = [("暂无关键复核项", 0)]
    counts = "".join(f"<li><span>{escape(label)}</span><strong>{value}</strong></li>" for label, value in visible_items)
    all_counts = "".join(f"<li><span>{escape(label)}</span><strong>{int(value or 0)}</strong></li>" for label, value in all_items)
    critical = row.get("criticalPendingReviewMetrics")
    note = ""
    if isinstance(critical, (list, tuple, set)) and [item for item in critical if item]:
        note = "<p>有关键数据待复核，评分置信度受限。</p>"
    if int(summary.get("ai_recommend_correct") or 0) or int(summary.get("ai_recommend_reject") or 0):
        note += "<p>存在AI标记的异常数据，评分需复核。</p>"
    action_bar = drawer_review_action_bar_html(str(row.get("symbol") or ""))
    return (
        '<div class="drawer-review-summary" data-drawer-section="review">'
        f'<ul>{counts}</ul>{note}'
        '<details class="drawer-low-priority"><summary>展开全部状态</summary>'
        f'<ul>{all_counts}</ul>'
        '</details>'
        f'{action_bar}'
        '</div>'
    )


def drawer_review_action_bar_html(symbol: str) -> str:
    safe_symbol = escape(str(symbol or "").upper())
    review_message = f"{safe_symbol} 的复核摘要已在当前面板中展示；如需批量处理，请在左侧进入数据复核中心的一键自动处理。"
    sync_message = f"{safe_symbol} 的复核队列同步属于后台批量动作，已保留在数据复核中心执行，当前不会切走总览。"
    fill_message = f"{safe_symbol} 的自动补全建议已在数据补全状态中展示；批量抓取 SEC / IR 建议在数据复核中心统一运行。"
    return (
        '<div class="drawer-review-actions">'
        '<button type="button" data-dashboard-drawer-focus="review" data-dashboard-drawer-message="'
        f'{escape(review_message)}">查看复核项</button>'
        '<button type="button" data-dashboard-drawer-message="'
        f'{escape(sync_message)}">同步复核队列</button>'
        '<button type="button" class="primary" data-dashboard-drawer-focus="resolution" data-dashboard-drawer-message="'
        f'{escape(fill_message)}">自动补全数据</button>'
        '<div class="drawer-action-note" data-dashboard-drawer-action-note hidden></div>'
        '</div>'
    )


def _drawer_raw_metrics_html(row: pd.Series, deps: DashboardDrawerDeps | None = None) -> str:
    drawer_deps = _drawer_deps(deps)
    blocks = []
    for group_name, metrics in drawer_deps.detail_groups:
        items = "".join(
            f'<li><span>{escape(label)}</span><strong>{escape(str(row.get(key, "N/A")))}</strong></li>'
            for key, label in metrics
            if not (key == "fcfMargin" and row.get(key) == "N/A")
        )
        blocks.append(f'<div class="drawer-metric-group"><b>{escape(group_name)}</b><ul>{items}</ul></div>')
    return "".join(blocks)
