from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape
import json
import math

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from data.entry_display import format_buy_zone, format_zone_status
from ui.dashboard_tables import _buy_point_label_tone, _entry_rating_chip_text, _entry_rating_display_parts
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
    entry_label, entry_grade, _entry_raw = _entry_rating_display_parts(row)
    entry_display = _entry_rating_chip_text(entry_label, entry_grade)
    badges = [
        drawer_deps.badge_span_html(row.get("qualityRating"), drawer_deps.badge_color_for_cell("qualityRating", row.get("qualityRating"), row)),
        drawer_deps.badge_span_html(entry_display, _buy_point_label_tone(entry_label)),
        drawer_deps.badge_span_html(row.get("riskRating"), drawer_deps.badge_color_for_cell("riskRating", row.get("riskRating"), row)),
        drawer_deps.badge_span_html(row.get("action"), drawer_deps.badge_color_for_cell("action", row.get("action"), row)),
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
        f'{_drawer_radar_entry_card_html(row)}'
        f'{_drawer_structure_entry_card_html(row)}'
        f'{_drawer_next_action_html(row, drawer_deps)}'
        f'{_drawer_detail_basis_html(row, drawer_deps, summary, entry_display)}'
        '</aside>'
    )


def _drawer_decision_summary_html(row: pd.Series, deps: DashboardDrawerDeps | None = None) -> str:
    drawer_deps = _drawer_deps(deps)
    symbol = str(row.get("symbol") or "该股票")
    model = model_type_label(row.get("modelType"))
    action = _drawer_compact_action_text(row.get("finalAction") or row.get("action") or "只观察")
    quality = str(row.get("qualityRating") or "N/A")
    entry_label, entry_grade, _entry_raw = _entry_rating_display_parts(row)
    entry = _entry_rating_chip_text(entry_label, entry_grade) or str(row.get("entryRating") or "N/A")
    risk = str(row.get("riskRating") or "N/A")
    summary = row.get("humanReadableSummary")
    if not isinstance(summary, dict):
        summary = {}
    conclusion = _decision_conclusion_text(row, symbol, model, action)
    why = _decision_why_text(row, quality, entry, risk, summary)
    return (
        '<div class="drawer-decision-card">'
        '<div class="drawer-card-title">决策摘要</div>'
        f'<div class="drawer-decision-headline">{escape(conclusion)}</div>'
        f'<p>{escape(why)}</p>'
        '<div class="drawer-decision-grid">'
        f'<span><b>当前动作</b><strong>{escape(action)}</strong></span>'
        f'<span><b>Radar 状态</b><strong>{escape(_drawer_radar_status_text(row))}</strong></span>'
        f'<span><b>数据可信度</b><strong>{escape(_drawer_data_status_text(row))}</strong></span>'
        '</div>'
        '</div>'
    )


def _drawer_radar_entry_card_html(row: pd.Series) -> str:
    label = _drawer_clean_text(row.get("entry_display_label")) or "暂无 Radar 纪律买区"
    hint = _drawer_clean_text(row.get("entry_action_hint"))
    reason = _drawer_clean_text(row.get("entry_display_reason"))
    buy_zone = row.get("radar_buy_zone") or row.get("buy_zone") or {}
    price_position = _drawer_clean_text(row.get("radar_price_position") or row.get("price_position") or row.get("zone_status"))
    missing_fields = _drawer_text_list(row.get("missing_entry_fields"))
    technical_low = row.get("technical_entry_zone_low") or row.get("radar_technical_entry_zone_low")
    technical_high = row.get("technical_entry_zone_high") or row.get("radar_technical_entry_zone_high")
    technical_reason = _drawer_clean_text(row.get("technical_entry_reason") or row.get("radar_technical_entry_reason"))
    technical_source = _drawer_clean_text(row.get("technical_entry_source") or row.get("radar_technical_entry_source"))
    technical_missing_fields = _drawer_text_list(row.get("technical_entry_missing_fields"))
    if not technical_missing_fields:
        technical_missing_fields = _drawer_text_list(row.get("radar_technical_entry_missing_fields"))
    technical_missing_reason = _drawer_clean_text(
        row.get("technical_entry_missing_reason") or row.get("radar_technical_entry_missing_reason")
    )
    technical_structure_status = _drawer_clean_text(
        row.get("technical_structure_status") or row.get("radar_technical_structure_status")
    )
    technical_structure_label = _drawer_clean_text(
        row.get("technical_structure_label") or row.get("radar_technical_structure_label")
    )
    technical_structure_reason = _drawer_clean_text(
        row.get("technical_structure_reason") or row.get("radar_technical_structure_reason")
    )
    technical_repair_low = row.get("technical_repair_zone_low") or row.get("radar_technical_repair_zone_low")
    technical_repair_high = row.get("technical_repair_zone_high") or row.get("radar_technical_repair_zone_high")
    support_watch_low = row.get("support_watch_zone_low") or row.get("radar_support_watch_zone_low")
    support_watch_high = row.get("support_watch_zone_high") or row.get("radar_support_watch_zone_high")
    confirmation_price = row.get("confirmation_price") or row.get("radar_confirmation_price")
    invalidation_price = row.get("invalidation_price") or row.get("radar_invalidation_price")
    next_technical_steps = _drawer_text_list(row.get("next_technical_steps"))
    if not next_technical_steps:
        next_technical_steps = _drawer_text_list(row.get("radar_next_technical_steps"))
    entry_context_status = _drawer_clean_text(row.get("entry_context_status") or row.get("radar_entry_context_status"))
    valuation_deep_zone = str(
        row.get("valuation_deep_zone_label")
        or row.get("radar_valuation_deep_zone_label")
        or format_buy_zone(buy_zone)
    ).strip()
    chase_above = row.get("chase_above_price")
    current_price = _drawer_number(row.get("price"))
    overlap = _drawer_zone_overlaps_chase(technical_low, technical_high, chase_above)

    conclusion = _drawer_entry_current_conclusion_html(
        row,
        entry_context_status=entry_context_status,
        price_position=price_position,
        label=label,
        hint=hint,
        reason=reason,
    )
    lines = []
    if reason:
        lines.append("判断原因：" + reason)
    technical_available = _drawer_technical_zone_available(technical_low, technical_high)
    if technical_reason and technical_available:
        source_suffix = f"（{technical_source}）" if technical_source else ""
        lines.append("技术区说明：" + technical_reason + source_suffix)
    elif not technical_available:
        missing_text = technical_missing_reason or technical_reason or _drawer_technical_missing_reason(technical_missing_fields)
        lines.append("技术回踩区暂缺：" + _strip_missing_prefix(missing_text))
    if technical_structure_label or technical_structure_reason:
        lines.append(
            "技术结构："
            + (technical_structure_label or _drawer_technical_structure_label(technical_structure_status))
            + ("；" + technical_structure_reason if technical_structure_reason else "")
        )
    if next_technical_steps:
        lines.append("下一步：" + "；".join(next_technical_steps[:3]))
    if overlap:
        lines.append("技术回踩区与追高禁区重叠；超过追高线部分不作为新增参考。")
        lines.append(
            "有效技术复核区："
            + _drawer_zone_range_text(technical_low, _drawer_effective_technical_high(technical_high, chase_above))
        )
    if price_position == "BELOW_BUY_ZONE":
        if entry_context_status == "BELOW_TECHNICAL_PULLBACK_ZONE":
            lines.extend(_drawer_broken_structure_review_lines())
        else:
            lines.extend(_drawer_below_valuation_reference_lines())
    if missing_fields:
        lines.append("缺失字段：" + "、".join(missing_fields))
    zone_table = _drawer_entry_zone_structure_html(
        row,
        buy_zone=buy_zone,
        technical_low=technical_low,
        technical_high=technical_high,
        valuation_deep_zone=valuation_deep_zone,
        chase_above=chase_above,
        current_price=current_price,
        entry_context_status=entry_context_status,
        price_position=price_position,
        overlap=overlap,
        technical_missing_reason=technical_missing_reason or technical_reason or _drawer_technical_missing_reason(technical_missing_fields),
        technical_structure_status=technical_structure_status,
        technical_structure_label=technical_structure_label,
        technical_structure_reason=technical_structure_reason,
        technical_repair_low=technical_repair_low,
        technical_repair_high=technical_repair_high,
        support_watch_low=support_watch_low,
        support_watch_high=support_watch_high,
        confirmation_price=confirmation_price,
        invalidation_price=invalidation_price,
        next_technical_steps=next_technical_steps,
        notes=lines,
    )
    return conclusion + zone_table


def _drawer_entry_current_conclusion_html(
    row: pd.Series,
    *,
    entry_context_status: str,
    price_position: str,
    label: str,
    hint: str,
    reason: str,
) -> str:
    status = _drawer_entry_primary_status_text(entry_context_status, price_position)
    action = _drawer_compact_action_text(row.get("finalAction") or row.get("action") or hint or "只观察")
    current_add = _drawer_number(row.get("currentAddLimitPercent"))
    allowed = "是" if _drawer_bool(row.get("isActionable")) or (current_add is not None and current_add > 0) else "否"
    summary_reason = _drawer_entry_summary_reason(
        entry_context_status=entry_context_status,
        price_position=price_position,
        label=label,
        hint=hint,
        reason=reason,
    )
    return _drawer_card_html(
        "当前结论",
        status,
        [
            "当前状态：" + status,
            "当前动作：" + action,
            "是否允许新增：" + allowed,
            "原因：" + summary_reason,
        ],
    )


def _drawer_entry_zone_structure_html(
    row: pd.Series,
    *,
    buy_zone: object,
    technical_low: object,
    technical_high: object,
    valuation_deep_zone: str,
    chase_above: object,
    current_price: float | None,
    entry_context_status: str,
    price_position: str,
    overlap: bool,
    technical_missing_reason: str,
    technical_structure_status: str,
    technical_structure_label: str,
    technical_structure_reason: str,
    technical_repair_low: object,
    technical_repair_high: object,
    support_watch_low: object,
    support_watch_high: object,
    confirmation_price: object,
    invalidation_price: object,
    next_technical_steps: list[str],
    notes: list[str],
) -> str:
    effective_high = _drawer_effective_technical_high(technical_high, chase_above) if overlap else technical_high
    technical_available = _drawer_technical_zone_available(technical_low, technical_high)
    technical_range = _drawer_zone_range_text(technical_low, technical_high) if technical_available else "暂缺"
    if overlap and technical_available:
        technical_range = f"原 {technical_range}；有效 {_drawer_zone_range_text(technical_low, effective_high)}"
    technical_relation = _drawer_zone_relationship(current_price, technical_low, effective_high) if technical_available else (
        "缺失原因：" + _strip_missing_prefix(technical_missing_reason or "缺 EMA / ATR / swing / K线")
    )
    technical_usage = "近端复核区" if technical_available else "当前使用：深度估值区 / 暂不提供近端技术参考"
    structure_label = technical_structure_label or _drawer_technical_structure_label(technical_structure_status)
    structure_reason = _strip_missing_prefix(technical_structure_reason) if technical_structure_reason else "等待技术结构确认"
    repair_available = _drawer_technical_zone_available(technical_repair_low, technical_repair_high)
    support_available = _drawer_technical_zone_available(support_watch_low, support_watch_high)
    next_step = next_technical_steps[0] if next_technical_steps else "等待收盘确认和相对强弱修复"
    rows = [
        (
            "技术结构",
            structure_label or "待确认",
            structure_reason,
            "判断当前是回踩、修复、破位还是筑底",
        ),
        (
            "技术回踩区",
            technical_range,
            technical_relation,
            technical_usage,
        ),
        (
            "修复观察区",
            _drawer_zone_range_text(technical_repair_low, technical_repair_high) if repair_available else "暂缺",
            _drawer_zone_relationship(current_price, technical_repair_low, technical_repair_high)
            if repair_available
            else "缺 EMA20 / EMA50 / EMA200",
            "弱趋势修复观察，不是自动买点",
        ),
        (
            "支撑观察区",
            _drawer_zone_range_text(support_watch_low, support_watch_high) if support_available else "暂缺",
            _drawer_zone_relationship(current_price, support_watch_low, support_watch_high)
            if support_available
            else "缺 recent swing low",
            "失效线附近的承接观察",
        ),
        (
            "确认线",
            _drawer_money_text(confirmation_price),
            next_step,
            "收盘站回后再复核",
        ),
        (
            "失效线",
            _drawer_money_text(invalidation_price),
            "跌破后转为破位复核" if _drawer_number(invalidation_price) is not None else "暂缺",
            "不把下跌自动当买点",
        ),
        (
            "深度估值区",
            valuation_deep_zone or format_buy_zone(buy_zone),
            _drawer_buy_zone_relationship(current_price, buy_zone),
            "极端安全区，不是当前近端买点",
        ),
        (
            "追高禁区",
            "> " + _drawer_money_text(chase_above) if _drawer_number(chase_above) is not None else "N/A",
            _drawer_chase_relationship(current_price, chase_above, overlap),
            "超过后禁止新增",
        ),
        (
            "最终纪律判断",
            _drawer_entry_context_status_text(entry_context_status, price_position),
            _drawer_compact_action_text(row.get("finalAction") or row.get("action") or ""),
            "交易纪律结果，不等同于自动买入",
        ),
    ]
    body = "".join(
        "<tr>"
        f"<td>{escape(kind)}</td>"
        f"<td>{escape(zone)}</td>"
        f"<td>{escape(relation)}</td>"
        f"<td>{escape(usage)}</td>"
        "</tr>"
        for kind, zone, relation, usage in rows
    )
    notes_html = ""
    if notes:
        note_items = "".join(f"<li>{escape(str(item))}</li>" for item in notes if item)
        notes_html = f'<details class="drawer-low-priority"><summary>一致性提示</summary><ul>{note_items}</ul></details>'
    return (
        '<div class="drawer-card drawer-entry-zone-card">'
        '<div class="drawer-card-title">买区结构</div>'
        '<div class="drawer-card-headline">技术回踩区 / 深度估值区 / 追高禁区分开展示</div>'
        '<table class="drawer-entry-zone-table">'
        '<thead><tr><th>类型</th><th>区间 / 价格</th><th>当前关系</th><th>用途</th></tr></thead>'
        f"<tbody>{body}</tbody>"
        "</table>"
        f"{notes_html}"
        "</div>"
    )


def _drawer_entry_primary_status_text(entry_context_status: str, price_position: str) -> str:
    status = str(entry_context_status or "").strip()
    if status == "IN_TECHNICAL_PULLBACK_ZONE":
        return "技术回踩区内"
    if status == "ABOVE_TECHNICAL_PULLBACK_ZONE":
        return "买区外"
    if status == "BELOW_TECHNICAL_PULLBACK_ZONE":
        return "跌破结构区"
    if status == "IN_CHASE_ZONE" or price_position == "IN_CHASE_ZONE":
        return "追高区"
    if status == "IN_DISCIPLINE_BUY_ZONE" or price_position == "IN_BUY_ZONE":
        return "买区内"
    if status in {"BELOW_DISCIPLINE_BUY_ZONE", "BELOW_VALUATION_REFERENCE"} or price_position == "BELOW_BUY_ZONE":
        return "低于估值参考"
    if status == "ZONE_MISSING" or price_position == "ZONE_MISSING":
        return "数据不足"
    return format_zone_status(price_position)


def _drawer_entry_summary_reason(
    *,
    entry_context_status: str,
    price_position: str,
    label: str,
    hint: str,
    reason: str,
) -> str:
    status = str(entry_context_status or "").strip()
    if status == "IN_TECHNICAL_PULLBACK_ZONE":
        return "进入技术回踩区，但未满足 ALLOW_BUY，需确认趋势修复和估值风险。"
    if status == "ABOVE_TECHNICAL_PULLBACK_ZONE":
        return "价格仍高于技术回踩区，等待更好的近端复核位置。"
    if status == "BELOW_TECHNICAL_PULLBACK_ZONE":
        return "价格跌破技术结构参考区，先复核基本面和趋势是否恶化。"
    if status == "IN_CHASE_ZONE" or price_position == "IN_CHASE_ZONE":
        return "价格进入追高禁区，禁止新增。"
    if price_position == "BELOW_BUY_ZONE":
        return "当前低于估值参考，不等于结构破坏；需等待 EMA、相对强弱和收盘确认。"
    return reason or hint or label or "暂无说明。"


def _drawer_zone_overlaps_chase(technical_low: object, technical_high: object, chase_above: object) -> bool:
    high = _drawer_number(technical_high)
    chase = _drawer_number(chase_above)
    return high is not None and chase is not None and high > chase


def _drawer_technical_zone_available(technical_low: object, technical_high: object) -> bool:
    return _drawer_number(technical_low) is not None and _drawer_number(technical_high) is not None


def _drawer_technical_structure_label(status: str) -> str:
    return {
        "UPTREND_PULLBACK": "强趋势回踩",
        "WEAK_TREND_REPAIR": "弱趋势修复中",
        "BREAKDOWN_REVIEW": "破位复核",
        "RANGE_BASE_BUILDING": "区间筑底",
        "DATA_MISSING": "数据不足",
    }.get(str(status or "").strip(), "待确认")


def _drawer_technical_missing_reason(fields: list[str]) -> str:
    if fields:
        labels = [_technical_missing_field_label(field) for field in fields]
        return "缺 " + " / ".join(labels)
    return "缺 EMA / ATR / swing / K线"


def _technical_missing_field_label(field: str) -> str:
    return {
        "current_price": "当前价格",
        "price": "当前价格",
        "ema20": "EMA20",
        "ema50": "EMA50",
        "ema100": "EMA100",
        "ema200": "EMA200",
        "atr14": "ATR",
        "recent_swing_low": "swing low",
        "recent_swing_high": "swing high",
        "recent_breakout_level": "breakout level",
        "nearby_support": "附近支撑",
        "data_status": "有效技术缓存",
    }.get(str(field), str(field))


def _strip_missing_prefix(text: str) -> str:
    value = _drawer_clean_text(text)
    for prefix in ("技术回踩区暂缺：", "技术回踩区暂缺:", "暂缺：", "暂缺:"):
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value or "缺 EMA / ATR / swing / K线"


def _drawer_effective_technical_high(technical_high: object, chase_above: object) -> object:
    high = _drawer_number(technical_high)
    chase = _drawer_number(chase_above)
    if high is None:
        return chase_above
    if chase is None:
        return technical_high
    return min(high, chase)


def _drawer_zone_relationship(current_price: float | None, low: object, high: object) -> str:
    low_number = _drawer_number(low)
    high_number = _drawer_number(high)
    if current_price is None or low_number is None or high_number is None:
        return "数据不足"
    if current_price < low_number:
        return "当前低于区间"
    if current_price <= high_number:
        return "当前在区内"
    return "当前高于区间"


def _drawer_buy_zone_relationship(current_price: float | None, buy_zone: object) -> str:
    low = None
    high = None
    if isinstance(buy_zone, dict):
        low = buy_zone.get("lower")
        high = buy_zone.get("upper")
    else:
        low = getattr(buy_zone, "lower", None)
        high = getattr(buy_zone, "upper", None)
    high_number = _drawer_number(high)
    relation = _drawer_zone_relationship(current_price, low, high)
    if current_price is not None and high_number:
        pct = ((current_price - high_number) / high_number) * 100
        if pct > 0:
            return f"当前高于 {pct:.1f}%"
    return relation


def _drawer_chase_relationship(current_price: float | None, chase_above: object, overlap: bool) -> str:
    chase = _drawer_number(chase_above)
    if current_price is None or chase is None:
        return "数据不足"
    if current_price > chase:
        return "当前已超过"
    if overlap:
        return "与技术区重叠"
    return "当前未超过"


def _drawer_entry_context_status_text(entry_context_status: str, price_position: str) -> str:
    status = str(entry_context_status or "").strip()
    if status == "IN_TECHNICAL_PULLBACK_ZONE":
        return "已进入技术回踩区上沿"
    if status == "ABOVE_TECHNICAL_PULLBACK_ZONE":
        return "高于技术回踩区，继续等回踩"
    if status == "BELOW_TECHNICAL_PULLBACK_ZONE":
        return "跌破结构区，先复核"
    if status == "IN_DISCIPLINE_BUY_ZONE":
        return "位于 Radar 纪律买区"
    if status in {"BELOW_DISCIPLINE_BUY_ZONE", "BELOW_VALUATION_REFERENCE"}:
        return "低于估值参考，等待结构确认"
    if status == "IN_CHASE_ZONE":
        return "进入追高区，禁止新增"
    return format_zone_status(price_position)


def _drawer_zone_range_text(low: object, high: object) -> str:
    low_text = _drawer_money_text(low)
    high_text = _drawer_money_text(high)
    if low_text != "N/A" and high_text != "N/A":
        return f"{low_text} - {high_text}"
    if high_text != "N/A":
        return "<= " + high_text
    if low_text != "N/A":
        return ">= " + low_text
    return "N/A"


def _drawer_structure_entry_card_html(row: pd.Series) -> str:
    advisor = row.get("structureEntryAdvisor")
    if not isinstance(advisor, dict):
        advisor = {}
    status_code = str(advisor.get("structure_status") or row.get("structureStatus") or "").strip()
    status = str(advisor.get("status_label") or _structure_status_label(status_code) or "数据不足")
    score = advisor.get("structure_score", row.get("structureScore"))
    decline = _drawer_clean_text(advisor.get("decline_reason"))
    thesis = str(advisor.get("thesis_status") or "UNKNOWN")
    support = _drawer_clean_text(advisor.get("support_confirmation"))
    close = _drawer_clean_text(advisor.get("close_confirmation"))
    relative = _drawer_clean_text(advisor.get("relative_strength_status"))
    volume = _drawer_clean_text(advisor.get("volume_confirmation"))
    reasons = _drawer_text_list(advisor.get("structure_reasons") or row.get("structureReasons"))
    warnings = _drawer_text_list(advisor.get("structure_warnings") or row.get("structureWarnings"))
    steps = _drawer_text_list(advisor.get("next_confirmation_steps") or row.get("structureNextSteps"))
    numeric_score = _drawer_number(score)
    is_data_missing = status_code == "DATA_MISSING" or status == "数据不足"
    score_text = "待补数据" if is_data_missing else ("N/A" if numeric_score is None else f"{numeric_score:.0f} 分")
    gaps: list[str] = []
    lines = [
        "只读提示，不改变 ALLOW_BUY / 买入门禁 / allowed_add_pct。",
    ]
    if _is_unknown_structure_text(decline):
        gaps.append("下跌原因未维护")
    else:
        lines.append(f"下跌原因：{decline}")
    thesis_label = _structure_thesis_label(thesis)
    if _is_unknown_structure_text(thesis_label):
        gaps.append("主线状态未维护")
    else:
        lines.append(f"主线状态：{thesis_label}")
    for label, value, gap in (
        ("技术承接", support, "缺 K 线或支撑数据"),
        ("收盘确认", close, "缺收盘确认数据"),
        ("相对强弱", relative, "缺 SPY/QQQ 相对强弱"),
        ("量能确认", volume, "缺成交量数据"),
    ):
        if _is_structure_missing_value(value):
            gaps.append(gap)
        else:
            lines.append(f"{label}：{value}")
    if gaps:
        lines.append("关键缺口：" + "；".join(_dedupe_text(gaps)[:4]))
    if reasons:
        lines.append("依据：" + "；".join(reasons[:2]))
    if warnings:
        lines.append("风险：" + "；".join(warnings[:2]))
    if steps:
        lines.append("下一步：" + "；".join(steps[:2]))
    return _drawer_card_html("结构买入提示", f"{status}｜{score_text}", lines)


def _structure_status_label(value: object) -> str:
    return {
        "STRUCTURE_CONFIRMED": "结构确认",
        "STRUCTURE_FORMING": "结构形成中",
        "DIP_ONLY": "只是下跌",
        "STRUCTURE_BROKEN": "结构破坏",
        "DATA_MISSING": "数据不足",
    }.get(str(value or ""), "")


def _structure_thesis_label(value: object) -> str:
    return {
        "INTACT": "主线仍在",
        "WEAKENING": "主线走弱",
        "BROKEN": "主线破坏",
        "UNKNOWN": "主线待维护",
    }.get(str(value or "").upper(), _drawer_clean_text(value) or "主线待维护")


def _drawer_next_action_html(row: pd.Series, deps: DashboardDrawerDeps | None = None) -> str:
    drawer_deps = _drawer_deps(deps)
    action = _drawer_compact_action_text(row.get("finalAction") or row.get("action") or "")
    current_add = str(row.get("currentAddLimit") or row.get("maxSuggestedPosition") or "N/A")
    max_weight = str(row.get("maxPortfolioWeight") or "N/A")
    waiting = _waiting_conditions(row, drawer_deps)
    if not waiting:
        waiting = ["等待估值、趋势或关键经营数据进一步确认。"]
    wait_items = "".join(f"<li>{escape(item)}</li>" for item in waiting)
    block_reason = _drawer_primary_block_reason(row)
    return (
        '<div class="drawer-position-card" data-drawer-section="position">'
        '<div>'
        '<span>下一步动作</span>'
        f'<strong>{escape(action)}</strong>'
        f'<em>{escape(block_reason)}</em>'
        '</div>'
        '<div>'
        '<span>当前新增建议</span>'
        f'<strong>{escape(current_add)}</strong>'
        f'<em>组合仓位上限：{escape(max_weight)}</em>'
        '</div>'
        '</div>'
        '<div class="drawer-card drawer-next-action-card">'
        '<div class="drawer-card-title">等待条件</div>'
        f'<ul>{wait_items}</ul>'
        '</div>'
    )


def _drawer_detail_basis_html(
    row: pd.Series,
    deps: DashboardDrawerDeps | None,
    summary: dict[str, str],
    entry_display: str,
) -> str:
    drawer_deps = _drawer_deps(deps)
    explanation_cards = [
        _drawer_card_html("公司质量解释", str(row.get("qualityRating") or "N/A"), [
            "主要加分：" + drawer_deps.translated_join(row.get("keyPositiveDrivers"), limit=4),
            "主要扣分：" + drawer_deps.translated_join(drawer_deps.quality_negative_items(row), limit=4),
            str(summary.get("quality") or ""),
        ]),
        _drawer_card_html("估值/计划参考解释", entry_display or str(row.get("entryRating") or "N/A"), [
            "该区域来自 legacy 估值参考 / combinedEntry，不等同于主表 Radar 纪律买区。",
            _clean_buy_point_summary_text(summary.get("valuation"), row),
            _clean_buy_point_summary_text(summary.get("technical"), row),
            _clean_buy_point_summary_text(summary.get("entry"), row),
            _entry_context_note(row),
        ]),
        _drawer_card_html("风险解释", str(row.get("riskRating") or "N/A"), [
            "风险来源：" + drawer_deps.translated_join(drawer_deps.risk_items(row), limit=4),
            str(summary.get("risk") or ""),
            _risk_context_note(row),
        ]),
    ]
    return (
        '<details class="drawer-raw drawer-detail-basis">'
        '<summary>详细依据</summary>'
        f'<div class="drawer-section">{"".join(explanation_cards)}</div>'
        f'{_drawer_industry_metrics_html(row, drawer_deps)}'
        '<div class="drawer-section-title">数据复核状态</div>'
        f'{drawer_review_summary_html(row)}'
        '<div data-drawer-section="resolution">'
        '<div class="drawer-section-title">数据补全状态</div>'
        f'{_drawer_resolution_html(row, drawer_deps)}'
        '</div>'
        '<details class="drawer-low-priority"><summary>原始指标</summary>'
        f'{_drawer_raw_metrics_html(row, drawer_deps)}'
        '</details>'
        '</details>'
    )


def _drawer_money_text(value: object) -> str:
    number = _drawer_number(value)
    return "N/A" if number is None else f"${number:,.2f}"


def _drawer_number(value: object) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _drawer_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "是"}
    return bool(value)


def _drawer_pct_text(value: object) -> str:
    number = _drawer_number(value)
    return "N/A" if number is None else f"{number:+.1f}%"


def _drawer_text_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [text for item in value if (text := _drawer_clean_text(item))]
    text = _drawer_clean_text(value)
    return [text] if text else []


def _drawer_clean_text(value: object) -> str:
    if value is None:
        return ""
    if _drawer_number(value) is None and not isinstance(value, str):
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null", "n/a"}:
        return ""
    return text


def _is_unknown_structure_text(value: object) -> bool:
    text = _drawer_clean_text(value)
    return not text or text.lower() in {"unknown", "未知"} or text in {"主线未知", "主线待维护"}


def _is_structure_missing_value(value: object) -> bool:
    text = _drawer_clean_text(value)
    if not text:
        return True
    return any(token in text for token in ("数据不足", "缺失", "待维护"))


def _dedupe_text(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _drawer_compact_action_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "待复核"
    if any(token in text for token in ("禁止", "追高", "阻止", "BLOCK")):
        return "禁止新增"
    if any(token in text for token in ("可加仓", "可小仓", "可正常", "分批", "ALLOW")):
        return "可加仓"
    if any(token in text for token in ("复核", "确认", "REVIEW")):
        return "待复核"
    if any(token in text for token in ("只观察", "观察", "等回踩", "等待")):
        return "只观察"
    if any(token in text for token in ("暂不", "不建议新增", "WAIT", "AVOID")):
        return "暂不处理"
    return text


def _drawer_radar_status_text(row: pd.Series) -> str:
    missing_fields = _drawer_text_list(row.get("missing_entry_fields"))
    if missing_fields:
        return "数据不足"
    status = str(row.get("radar_price_position") or row.get("price_position") or row.get("zone_status") or "").strip()
    mapping = {
        "IN_BUY_ZONE": "买区内",
        "ABOVE_BUY_ZONE": "买区外",
        "IN_CHASE_ZONE": "追高区",
        "BELOW_BUY_ZONE": "低于估值参考",
        "ZONE_MISSING": "无买区",
    }
    if status in mapping:
        return mapping[status]
    label = str(row.get("entry_display_label") or row.get("entryRating") or "").strip()
    if "追高" in label:
        return "追高区"
    if "跌破结构区" in label:
        return "跌破结构区"
    if "跌破买区" in label or "低于买区" in label or "低于估值参考" in label:
        return "低于估值参考"
    if "买区内" in label:
        return "买区内"
    if "等待回落" in label or "高于买区" in label:
        return "买区外"
    if "暂无" in label or "数据" in label:
        return "数据不足"
    return "待复核"


def _drawer_below_valuation_reference_lines() -> list[str]:
    return [
        "低于估值参考不等于结构破坏，也不等于自动买入。",
        "等待结构确认：EMA / 相对强弱 / 收盘确认；同时复核基本面、财报冲击和市场重新定价。",
    ]


def _drawer_broken_structure_review_lines() -> list[str]:
    return [
        "跌破结构区表示价格已跌破技术支撑参考，需要先复核趋势和基本面。",
        "复核清单：是否跌破 recent swing low / EMA200；相对强弱是否恶化；财报/指引是否恶化；是否只是市场错杀。",
    ]


def _drawer_data_status_text(row: pd.Series) -> str:
    text = str(row.get("dataStatus") or row.get("radar_data_status") or row.get("data_status") or "").strip()
    confidence = str(row.get("dataConfidence") or "").strip().lower()
    if "完整" in text or confidence == "high":
        return "完整"
    if "中" in text or confidence == "medium":
        return "中"
    if "低" in text or confidence == "low":
        return "低"
    if "不足" in text or "缺" in text or "missing" in text.lower():
        return "不足"
    return text or "待复核"


def _drawer_primary_block_reason(row: pd.Series) -> str:
    reasons = _drawer_text_list(row.get("blockReasons") or row.get("block_reasons") or row.get("radar_block_reasons"))
    if reasons:
        return reasons[0]
    hint = str(row.get("entry_action_hint") or "").strip()
    if hint:
        return hint
    action = _drawer_compact_action_text(row.get("finalAction") or row.get("action") or "")
    if action == "可加仓":
        return "仍需按交易计划控制仓位。"
    if action == "禁止新增":
        return "当前不满足纪律新增条件。"
    if action == "待复核":
        return "先复核数据、估值或买区条件。"
    return "保持观察，等待更清晰的买区或数据确认。"


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
        return f"{symbol} 是高质量{model}，但当前估值参考一般，适合{_short_action_for_sentence(action)}。"
    if "数据" in str(row.get("dataStatus") or "") or row.get("dataConfidence") == "low":
        return f"{symbol} 的评分仍受数据置信度限制，先复核关键数据再提高仓位。"
    return f"{symbol} 当前动作是{_short_action_for_sentence(action)}，先按仓位纪律执行。"


def _decision_why_text(row: pd.Series, quality: str, entry: str, risk: str, summary: dict[str, str]) -> str:
    action = str(row.get("action") or "")
    if _is_high_quality_text(quality) and risk == "低" and _is_observe_or_wait_action(action, entry):
        return f"公司风险低，但 legacy 估值参考为 {entry}，当前新增仓位受限；这不是公司质量问题，而是估值参考还没到。"
    parts = [_combined_entry_note(row), str(summary.get("technical") or ""), str(summary.get("valuation") or "")]
    if not parts[0]:
        parts.insert(0, str(summary.get("entry") or ""))
    text = " ".join(_clean_buy_point_summary_text(part, row) for part in parts if part).strip()
    return text or "当前建议由质量、legacy 估值参考、风险、估值和数据置信度综合得出。"


def _combined_entry_note(row: pd.Series) -> str:
    combined = row.get("combinedEntry")
    if not isinstance(combined, dict):
        return ""
    label = str(combined.get("entryLabel") or "").strip()
    return f"legacy 估值参考：{label}。" if label else ""


def _clean_buy_point_summary_text(text: object, row: pd.Series) -> str:
    value = str(text or "")
    entry_label, entry_grade, _entry_raw = _entry_rating_display_parts(row)
    display = _entry_rating_chip_text(entry_label, entry_grade)
    raw = str(row.get("entryRating") or "").strip()
    if raw and display:
        value = value.replace(f"买点评级为{raw}", f"legacy 估值参考为{display}")
        value = value.replace(f"买点评级为 {raw}", f"legacy 估值参考为 {display}")
    if display:
        value = value.replace("击球区附近", display)
    return value


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
        return "只观察不是因为公司质量差，而是因为当前 legacy 估值参考不够理想；主表 Radar 买区仍以 Radar 纪律口径为准。"
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
