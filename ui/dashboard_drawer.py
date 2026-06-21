from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape
import json
import math
from urllib.parse import quote

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from data.advisory_compat import advisory_reason_list
from data.action_fusion import ActionFusionResult, action_fusion_card_html
from data.buy_plan_alerts import (
    ALERT_ACTIVE,
    ALERT_TRIGGERED,
    BuyPlanAlertStore,
    buy_plan_alert_message,
    buy_plan_alert_status_label,
    buy_plan_alert_table_label,
)
from data.buy_zone_display import build_buy_zone_display
from data.entry_display import format_buy_zone, format_zone_status
from data.pullback_acceptance import pullback_acceptance_context_lines
from ui.dashboard_tables import (
    _buy_point_label_tone,
    _entry_rating_chip_text,
    _entry_rating_display_parts,
)
from ui.metric_labels import model_type_label, resolution_status_label
from ui.price_source_display import price_source_label_from_row


DRAWER_SYMBOL_SESSION_KEY = "dashboard_drawer_symbol"
DRAWER_FOCUS_SESSION_KEY = "dashboard_drawer_focus"
DRAWER_REPORT_PAGE_QUERY = "ai-radar"
DRAWER_REPORT_VIEW_QUERY = "report"


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
        raise RuntimeError("个股详情抽屉依赖尚未配置。")
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


def drawer_report_href(symbol: str) -> str:
    normalized_symbol = str(symbol or "").strip().upper()
    safe_symbol = quote(normalized_symbol)
    return f"?page={DRAWER_REPORT_PAGE_QUERY}&view={DRAWER_REPORT_VIEW_QUERY}&ticker={safe_symbol}#radar-report"


def build_drawer_actions(symbol: str) -> list[dict[str, object]]:
    normalized_symbol = str(symbol or "").strip().upper()
    safe_symbol = quote(normalized_symbol)
    return [
        {
            "action": "open_report",
            "label": "查看完整研报",
            "href": drawer_report_href(normalized_symbol),
            "target": "_self",
            "variant": "primary",
            "session_updates": {
                "ai_radar_selected_ticker": normalized_symbol,
                "radar_report_ticker": normalized_symbol,
                DRAWER_SYMBOL_SESSION_KEY: "",
            },
        },
        {
            "action": "record_signal",
            "label": "记录当前信号",
            "href": f"?page=dashboard&recordSignal={safe_symbol}#watchlist-table",
            "target": "_self",
            "variant": "secondary",
            "session_updates": {},
        },
    ]


def _drawer_actions_html(symbol: str, is_starred: bool = False) -> str:
    items: list[str] = []
    safe_symbol = quote(str(symbol or "").strip().upper())
    star_label = "取消星标" if is_starred else "标为星标"
    items.append(
        f'<a class="drawer-action-link is-secondary dashboard-star-action" '
        f'href="?page=dashboard&toggleStar={safe_symbol}#watchlist-table" target="_self" '
        f'onclick="event.stopPropagation();" data-dashboard-drawer-action="toggle_star">'
        f'{escape(star_label)}</a>'
    )
    for action in build_drawer_actions(symbol):
        action_id = str(action["action"])
        href = str(action["href"])
        label = str(action["label"])
        target = str(action.get("target") or "_self")
        variant = str(action.get("variant") or "secondary")
        extra_class = ""
        if action_id == "open_report":
            onclick = (
                "event.preventDefault();event.stopPropagation();"
                "if(window.__dashboardCloseDrawer){window.__dashboardCloseDrawer();}"
                "window.location.assign(this.href);"
                "return false;"
            )
            extra_class = " dashboard-open-report-action"
        elif action_id == "record_signal":
            onclick = "event.stopPropagation();"
            extra_class = " dashboard-record-action"
        else:
            onclick = "event.stopPropagation();"
        items.append(
            f'<a class="drawer-action-link is-{escape(variant)}{extra_class}" '
            f'href="{escape(href, quote=True)}" target="{escape(target, quote=True)}" '
            f'data-dashboard-drawer-action="{escape(action_id)}" onclick="{escape(onclick, quote=True)}">'
            f'{escape(label)}</a>'
        )
    return f'<div class="drawer-signal-actions">{"".join(items)}</div>'


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
              if (target.closest(".dashboard-record-action, .dashboard-refresh-action, .dashboard-star-action")) {{
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
    symbol = str(row.get("symbol") or "").upper()
    safe_symbol = escape(symbol)
    primary_decision = build_drawer_primary_decision(row)
    current_price = _drawer_current_price(row)
    alert = _drawer_buy_plan_alert(row, symbol, current_price)
    price_source_html = _drawer_price_source_html(row)
    badges = [
        drawer_deps.badge_span_html("⭐ 星标关注", "yellow") if bool(row.get("isStarred")) else "",
        drawer_deps.badge_span_html(row.get("qualityRating"), drawer_deps.badge_color_for_cell("qualityRating", row.get("qualityRating"), row)),
        drawer_deps.badge_span_html(primary_decision["badge_zone"], _buy_point_label_tone(primary_decision["badge_zone"])),
        drawer_deps.badge_span_html(primary_decision.get("acceptance_state_text"), _acceptance_badge_tone(primary_decision.get("acceptance_state_text"))),
        drawer_deps.badge_span_html(row.get("riskRating"), drawer_deps.badge_color_for_cell("riskRating", row.get("riskRating"), row)),
        drawer_deps.badge_span_html(primary_decision["action_text"], drawer_deps.badge_color_for_cell("action", primary_decision["action_text"], row)),
        drawer_deps.badge_span_html(buy_plan_alert_table_label(alert), "orange") if alert else "",
    ]
    return (
        '<div class="drawer-backdrop"></div>'
        '<aside class="stock-drawer">'
        '<a class="drawer-close-link" href="#" data-dashboard-drawer-close="1" title="关闭右侧详情面板">×</a>'
        '<div class="drawer-topline">快速决策</div>'
        '<div class="drawer-head">'
        '<div>'
        f'<div class="drawer-symbol">{safe_symbol}</div>'
        f'<div class="drawer-company">{escape(str(row.get("companyName") or "公司名待补充"))}</div>'
        '</div>'
        '<div class="drawer-price-wrap">'
        f'<div class="drawer-price">{escape(_drawer_money_text(current_price))}</div>'
        f'{price_source_html}'
        '</div>'
        '</div>'
        '<div class="drawer-meta-grid">'
        f'<span>模型：{escape(model_type_label(row.get("modelType")))}</span>'
        f'<span>市值：{escape(_drawer_display_text(row.get("marketCap")))}</span>'
        f'<span>统一买区：{escape(str(primary_decision.get("acceptance_state_text") or primary_decision.get("badge_zone") or "待复核"))}</span>'
        f'<span>数据：{escape(_drawer_data_status_text(row))}</span>'
        '</div>'
        f'<div class="drawer-badges">{"".join(badge for badge in badges if badge)}</div>'
        f'{_drawer_quick_decision_html(row, primary_decision)}'
        f'{_drawer_buy_plan_alert_html(symbol, current_price, alert)}'
        f'{_drawer_actions_html(symbol, bool(row.get("isStarred")))}'
        '</aside>'
    )


def _drawer_quick_decision_html(row: pd.Series, decision: dict[str, object] | None = None) -> str:
    decision = decision or build_drawer_primary_decision(row)
    if _drawer_low_data_confidence(row, decision):
        momentum_note = _drawer_short_sentence(decision.get("momentum_note"), 42)
        momentum_html = (
            '<div class="drawer-decision-grid">'
            '<span><b>动能辅助</b>'
            f'<strong>{escape(momentum_note)}</strong>'
            '</span>'
            '</div>'
            if momentum_note
            else ""
        )
        return (
            '<div class="drawer-decision-card drawer-quick-decision-card">'
            '<div class="drawer-card-title">快速决策</div>'
            '<p class="drawer-single-line-note">数据可信度低，先复核关键数据。</p>'
            f'{momentum_html}'
            '</div>'
        )
    field_items = [
        ("当前动作", _drawer_short_sentence(decision["action_text"], 32)),
        ("当前子区", _drawer_short_sentence(decision.get("current_subzone_display_text") or decision["zone_text"], 32)),
        ("主原因", _drawer_short_sentence(decision["main_reason"], 38)),
        ("动能辅助", _drawer_short_sentence(decision.get("momentum_note"), 42)),
        ("下一步", _drawer_short_sentence(decision["next_step"], 42)),
    ]
    fields_html = "".join(
        "<span>"
        f"<b>{escape(label)}</b>"
        f"<strong>{escape(str(value))}</strong>"
        "</span>"
        for label, value in field_items
        if value
    )
    return (
        '<div class="drawer-decision-card drawer-quick-decision-card">'
        '<div class="drawer-card-title">快速决策</div>'
        '<div class="drawer-decision-grid">'
        f'{fields_html}'
        '</div>'
        '</div>'
    )


def _drawer_current_price(row: pd.Series | dict) -> float | None:
    return _drawer_number(
        row.get("price")
        or row.get("currentPrice")
        or row.get("current_price")
    )


def _drawer_price_source_html(row: pd.Series | dict) -> str:
    label, title = price_source_label_from_row(row)
    return f'<div class="drawer-price-source" title="{escape(title)}">{escape(label)}</div>'


def _drawer_buy_plan_alert(row: pd.Series | dict, symbol: str, current_price: object) -> dict | None:
    value = row.get("buyPlanAlert")
    if isinstance(value, dict) and value:
        return dict(value)
    status = str(row.get("buyPlanAlertStatus") or "").strip().upper()
    price = _drawer_number(row.get("buyPlanAlertPrice"))
    shares = _drawer_number(row.get("buyPlanAlertShares"))
    if status and price is not None and shares is not None:
        return {
            "symbol": str(symbol or "").strip().upper(),
            "planned_buy_price": price,
            "planned_buy_shares": int(shares),
            "note": row.get("buyPlanAlertNote") or "",
            "status": status,
            "status_label": buy_plan_alert_status_label(status),
        }
    clean_symbol = str(symbol or "").strip().upper()
    if not clean_symbol:
        return None
    try:
        return BuyPlanAlertStore().check_and_update(clean_symbol, current_price)
    except Exception:
        return None


def _drawer_buy_plan_alert_html(symbol: str, current_price: object, alert: dict | None) -> str:
    clean_symbol = str(symbol or "").strip().upper()
    current_text = _drawer_money_text(current_price)
    status = str((alert or {}).get("status") or "").strip().upper()
    planned_price = _drawer_number((alert or {}).get("planned_buy_price"))
    planned_shares = int(_drawer_number((alert or {}).get("planned_buy_shares")) or 0)
    note = str((alert or {}).get("note") or "").strip()
    if status == ALERT_TRIGGERED:
        status_html = (
            '<div class="drawer-buy-alert-status is-triggered">'
            '<strong>已到计划价</strong>'
            f"<span>{escape(buy_plan_alert_message(alert, current_price))}</span>"
            "</div>"
        )
    elif status == ALERT_ACTIVE:
        status_html = (
            '<div class="drawer-buy-alert-status is-active">'
            '<strong>等待触发</strong>'
            f"<span>{escape(buy_plan_alert_message(alert, current_price))}</span>"
            "</div>"
        )
    else:
        status_html = (
            '<div class="drawer-buy-alert-status is-empty">'
            '<strong>未设置计划买入提醒</strong>'
            "<span>填写价格和股数后，系统只做提醒，不会自动下单。</span>"
            "</div>"
        )
    cancel_html = ""
    if status in {ALERT_ACTIVE, ALERT_TRIGGERED}:
        cancel_html = (
            f'<a class="drawer-alert-cancel" href="?page=dashboard&cancelBuyPlanAlert={escape(quote(clean_symbol), quote=True)}#watchlist-table" '
            'target="_self" onclick="event.stopPropagation();">取消提醒</a>'
        )
    return (
        '<section class="drawer-buy-alert-card">'
        '<div class="drawer-card-title">计划买入提醒</div>'
        f'<div class="drawer-alert-current">当前价：<strong>{escape(current_text)}</strong></div>'
        f"{status_html}"
        '<form class="drawer-buy-alert-form" method="get" action="">'
        '<input type="hidden" name="page" value="dashboard">'
        f'<input type="hidden" name="saveBuyPlanAlert" value="{escape(clean_symbol, quote=True)}">'
        '<label><span>计划买入价</span>'
        f'<input type="number" min="0.01" step="0.01" name="plannedBuyPrice" value="{escape(_drawer_input_number(planned_price), quote=True)}" required>'
        '</label>'
        '<label><span>计划买入股数</span>'
        f'<input type="number" min="1" step="1" name="plannedBuyShares" value="{escape(str(planned_shares or ""), quote=True)}" required>'
        '</label>'
        '<label class="drawer-alert-note"><span>备注，可选</span>'
        f'<input type="text" name="buyPlanNote" maxlength="80" value="{escape(note, quote=True)}" placeholder="例如：跌到观察区下沿再买">'
        '</label>'
        '<div class="drawer-alert-actions">'
        '<button type="submit">保存提醒</button>'
        f'{cancel_html}'
        '</div>'
        '</form>'
        '</section>'
    )


def _drawer_low_data_confidence(row: pd.Series | dict, decision: dict[str, object]) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            row.get("dataStatus"),
            row.get("dataConfidence"),
            decision.get("action_code"),
            decision.get("missing_fields_text"),
        )
    )
    return any(token in text for token in ("低", "缺", "DATA_INSUFFICIENT", "MISSING", "missing"))


def _drawer_short_sentence(value: object, limit: int = 40) -> str:
    text = _drawer_clean_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip("，；。 ") + "…"


def _drawer_input_number(value: object) -> str:
    number = _drawer_number(value)
    if number is None:
        return ""
    return f"{number:g}"


def build_drawer_primary_decision(row: pd.Series | dict) -> dict[str, object]:
    context = _drawer_buy_zone_context(row)
    display = _drawer_buy_zone_display(row) or build_buy_zone_display(context, row, mode="drawer")
    action_code = str(display.get("action_code") or context.get("current_action") or "").strip().upper() or "DATA_INSUFFICIENT"
    is_data_insufficient = action_code == "DATA_INSUFFICIENT"
    missing_labels = _drawer_missing_field_labels(_drawer_text_list(context.get("missing_fields")))
    if not context:
        missing_labels = _dedupe_text(["统一买区上下文", *missing_labels])
    action_text = str(display.get("main_action_text") or display.get("badge_label") or "数据不足，不给买区")
    acceptance_state_text = str(display.get("acceptance_state_text") or display.get("acceptance_badge_text") or "")
    entry_quality_text = str(display.get("entry_quality_text") or "")
    current_subzone_display_text = str(display.get("current_subzone_display_text") or display.get("current_subzone_text") or "")
    zone_text = str(display.get("zone_text") or "暂不生成")
    main_reason = (
        "技术承接数据不足"
        if is_data_insufficient
        else _drawer_clean_text(display.get("technical_action_text"))
        or _drawer_clean_text(display.get("explanation"))
        or "以技术结构、量能承接和风险收益为准"
    )
    position_action = str(display.get("account_action_text") or "")
    next_step = str(display.get("next_step_text") or "")
    momentum_note = str(display.get("momentum_note") or "")
    conflict_notice = _drawer_conflict_notice(row, action_code)
    missing_fields_text = " / ".join(missing_labels)
    headline = str(display.get("main_conclusion_text") or "").strip()
    if not headline:
        headline = "｜".join(part for part in (acceptance_state_text, current_subzone_display_text, entry_quality_text, action_text) if part)
    if not headline:
        headline = "｜".join(part for part in (acceptance_state_text, action_text, display.get("badge_hint"), main_reason) if part)
    badge_zone = "数据不足" if is_data_insufficient else zone_text
    return {
        "action_code": action_code,
        "action_text": action_text,
        "acceptance_state_text": acceptance_state_text,
        "entry_quality_text": entry_quality_text,
        "current_subzone_display_text": current_subzone_display_text,
        "headline": headline,
        "main_reason": main_reason,
        "zone_text": zone_text,
        "badge_zone": badge_zone,
        "position_action": position_action,
        "next_step": next_step,
        "momentum_note": momentum_note,
        "conflict_notice": conflict_notice,
        "missing_fields_text": missing_fields_text,
        "buy_zone_display": display,
    }


def _acceptance_badge_tone(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "gray"
    if "明显承接" in text:
        return "green"
    if "初步承接" in text:
        return "blue"
    if "承接不足" in text or "放量未确认" in text:
        return "yellow"
    if "飞刀风险" in text or "结构破坏" in text:
        return "red"
    return "gray"


def _drawer_quick_decision(row: pd.Series | dict) -> dict[str, object]:
    return build_drawer_primary_decision(row)


def _drawer_buy_zone_context(row: pd.Series) -> dict[str, object]:
    for key in ("buyZoneContext", "buy_zone_context"):
        value = row.get(key)
        if isinstance(value, dict):
            return dict(value)
    return {}


def _drawer_buy_zone_display(row: pd.Series | dict) -> dict[str, object]:
    for key in ("buyZoneDisplay", "buy_zone_display"):
        value = row.get(key)
        if isinstance(value, dict):
            return dict(value)
    return {}


def _drawer_primary_zone_bounds(context: dict[str, object]) -> tuple[float | None, float | None]:
    low = (
        _drawer_number(context.get("pullback_zone_low"))
        or _drawer_number(context.get("support_zone_low"))
        or _drawer_number(context.get("primary_zone_low"))
    )
    high = (
        _drawer_number(context.get("pullback_zone_high"))
        or _drawer_number(context.get("support_zone_high"))
        or _drawer_number(context.get("primary_zone_high"))
    )
    return low, high


def _drawer_primary_zone_text(context: dict[str, object], low: float | None, high: float | None) -> str:
    if low is not None or high is not None:
        return _drawer_zone_range_text(low, high)
    return (
        _drawer_clean_text(context.get("primary_zone_text"))
        or _drawer_clean_text(context.get("primary_zone"))
        or "暂不生成"
    )


def _drawer_canonical_action_text(action_code: str, *, has_position: bool) -> str:
    if action_code == "DATA_INSUFFICIENT":
        return "数据不足"
    mapping = {
        "WAIT_PULLBACK": "等待回踩",
        "WAIT_CONFIRMATION": "区内看承接",
        "ALLOW_SMALL_BUY": "小仓观察建议",
        "ALLOW_ADD_ON_PULLBACK": "小仓观察建议",
        "BLOCK_CHASE": "追高风险提醒",
        "RISK_REVIEW": "风险复核提醒",
        "AVOID": "暂不参与",
    }
    return mapping.get(action_code, _drawer_compact_action_text(action_code))


def _drawer_position_action_text(action_code: str, *, has_position: bool) -> str:
    if action_code == "DATA_INSUFFICIENT":
        return "持有观察，不建议加仓" if has_position else "数据不足，等待数据补齐"
    if has_position:
        if action_code == "ALLOW_ADD_ON_PULLBACK":
            return "回踩复核，谨慎观察"
        if action_code == "RISK_REVIEW":
            return "风险复核提醒"
        if action_code == "BLOCK_CHASE":
            return "持有观察，不追高"
        return "持有观察"
    if action_code == "ALLOW_SMALL_BUY":
        return "小仓观察建议"
    if action_code == "BLOCK_CHASE":
        return "追高风险提醒"
    return _drawer_canonical_action_text(action_code, has_position=False)


def _drawer_next_step_text(
    action_code: str,
    context: dict[str, object],
    missing_labels: list[str],
    *,
    has_position: bool,
) -> str:
    if action_code == "DATA_INSUFFICIENT":
        missing = " / ".join(missing_labels) if missing_labels else "历史K线 / 量能 / 支撑压力"
        return f"补齐{missing}后重新判断"
    confirm = _drawer_confirmation_line_text(context)
    if confirm:
        return confirm
    if action_code == "WAIT_PULLBACK":
        return "等待回到技术回踩带并观察承接"
    if action_code == "WAIT_CONFIRMATION":
        return "等待量价确认后重新判断"
    if action_code == "BLOCK_CHASE":
        return "等待回到观察区，不追高"
    if has_position:
        return "按技术回踩带和仓位纪律复核"
    return "按技术回踩带等待"


def _drawer_confirmation_line_text(context: dict[str, object]) -> str:
    price = _drawer_number(
        context.get("confirmation_price")
        or context.get("confirm_price")
        or context.get("confirmation_line")
        or context.get("confirm_line")
    )
    if price is None:
        return ""
    return f"放量站上 {_drawer_money_text(price)} 后重新评估，不等于直接买入"


def _drawer_position_state(row: pd.Series) -> tuple[bool, float | None]:
    containers: list[object] = [
        row,
        row.get("portfolioContext"),
        row.get("portfolio_context"),
        row.get("actionFusion"),
    ]
    for container in containers:
        if not isinstance(container, (dict, pd.Series)):
            continue
        for key in (
            "current_shares",
            "shares",
            "quantity",
            "position_shares",
            "positionShares",
            "currentPositionShares",
            "current_position_quantity",
        ):
            shares = _drawer_number(container.get(key))
            if shares is not None and shares > 0:
                return True, shares
    for container in containers:
        if not isinstance(container, (dict, pd.Series)):
            continue
        for key in ("portfolio_weight", "portfolioWeight", "current_weight", "currentWeight"):
            weight = _drawer_number(container.get(key))
            if weight is not None and weight > 0:
                return True, None
    return False, None


def _drawer_missing_field_labels(fields: list[str]) -> list[str]:
    labels: list[str] = []
    for field in fields:
        normalized = str(field or "").strip().lower()
        if not normalized:
            continue
        if normalized in {"buy_zone_context", "buyzonecontext"}:
            labels.append("统一买区上下文")
        elif "daily" in normalized or "ohlcv" in normalized or "history" in normalized:
            labels.append("历史K线")
        elif "volume" in normalized or "turnover" in normalized:
            labels.append("成交量/量比")
        elif normalized.startswith("ma") or "moving_average" in normalized or normalized.startswith("ema"):
            labels.append("均线")
        elif "atr" in normalized:
            labels.append("ATR")
        elif "rsi" in normalized:
            labels.append("RSI")
        elif "support" in normalized or "resistance" in normalized or "swing" in normalized:
            labels.append("支撑压力")
        elif "price" in normalized:
            labels.append("当前价格")
        else:
            labels.append(_technical_missing_field_label(normalized))
    return _dedupe_text(labels)


def _drawer_conflict_notice(row: pd.Series, action_code: str) -> str:
    if action_code != "DATA_INSUFFICIENT":
        return ""
    text = " ".join(
        _drawer_visible_strings(
            [
                row.get("finalAction"),
                row.get("action"),
                row.get("entry_display_label"),
                row.get("entry_action_hint"),
                row.get("entryRating"),
                row.get("buyZoneActionText"),
                row.get("actionFusion"),
            ]
        )
    )
    conflict_tokens = ("可加仓", "允许买入", "允许小仓", "进入买点", "买区内", "估值买点", "价值复核", "ALLOW")
    if any(token in text for token in conflict_tokens):
        return "结论冲突提示：技术承接数据不足，历史估值参考只作风险提示，不改变主结论。"
    return ""


def _drawer_visible_strings(values: object) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, int, float, bool)):
        text = _drawer_clean_text(values)
        return [text] if text else []
    if isinstance(values, dict):
        result: list[str] = []
        for value in values.values():
            result.extend(_drawer_visible_strings(value))
        return result
    if isinstance(values, (list, tuple, set)):
        result = []
        for value in values:
            result.extend(_drawer_visible_strings(value))
        return result
    return []


def _drawer_action_fusion_card_html(row: pd.Series) -> str:
    payload = row.get("actionFusion")
    if not isinstance(payload, dict) or not payload:
        return _drawer_action_fusion_fallback_html()
    payload = dict(payload)
    if "advisory_warnings_cn" not in payload and "blocker_bullets_cn" in payload:
        payload["advisory_warnings_cn"] = payload.pop("blocker_bullets_cn")
    try:
        result = ActionFusionResult(**payload)
    except TypeError:
        return _drawer_action_fusion_fallback_html()
    return action_fusion_card_html(result)


def _drawer_action_fusion_fallback_html() -> str:
    return (
        '<section class="action-fusion-card">'
        '<div class="action-fusion-kicker">系统建议</div>'
        '<div class="action-fusion-headline">数据待补 · 低</div>'
        '<p>暂无系统建议；原因：本地缓存缺失。</p>'
        '<div class="action-fusion-grid">'
        '<div><b>待确认事项</b><ul><li>补齐本地买区 / 技术 / 量价缓存后再复核。</li></ul></div>'
        '</div>'
        '<small>历史融合提示仅作辅助依据展示，不改变买区主建议、研究状态或组合同步。</small>'
        '</section>'
    )


def _drawer_decision_summary_html(row: pd.Series, deps: DashboardDrawerDeps | None = None) -> str:
    drawer_deps = _drawer_deps(deps)
    symbol = str(row.get("symbol") or "该股票")
    model = model_type_label(row.get("modelType"))
    action = _drawer_compact_action_text(row.get("finalAction") or row.get("action") or "等待回踩")
    quality = _drawer_display_text(row.get("qualityRating"), "待补")
    entry_label, entry_grade, _entry_raw = _entry_rating_display_parts(row)
    entry = _entry_rating_chip_text(entry_label, entry_grade) or _drawer_display_text(row.get("entryRating"), "待补")
    risk = _drawer_display_text(row.get("riskRating"), "待补")
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
        f'<span><b>研究状态</b><strong>{escape(_drawer_radar_status_text(row))}</strong></span>'
        f'<span><b>数据可信度</b><strong>{escape(_drawer_data_status_text(row))}</strong></span>'
        '</div>'
        '</div>'
    )


def _drawer_radar_entry_card_html(row: pd.Series) -> str:
    label = _drawer_clean_text(row.get("entry_display_label")) or "暂无买区建议"
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
    adaptive_pullback_low = row.get("adaptive_pullback_zone_low") or row.get("radar_adaptive_pullback_zone_low")
    adaptive_pullback_high = row.get("adaptive_pullback_zone_high") or row.get("radar_adaptive_pullback_zone_high")
    adaptive_pullback_label = _drawer_clean_text(
        row.get("adaptive_pullback_label") or row.get("radar_adaptive_pullback_label")
    )
    adaptive_pullback_reason = _drawer_clean_text(
        row.get("adaptive_pullback_reason") or row.get("radar_adaptive_pullback_reason")
    )
    technical_repair_low = row.get("technical_repair_zone_low") or row.get("radar_technical_repair_zone_low")
    technical_repair_high = row.get("technical_repair_zone_high") or row.get("radar_technical_repair_zone_high")
    near_term_repair_low = row.get("near_term_repair_zone_low") or row.get("radar_near_term_repair_zone_low")
    near_term_repair_high = row.get("near_term_repair_zone_high") or row.get("radar_near_term_repair_zone_high")
    trend_reclaim_low = row.get("trend_reclaim_zone_low") or row.get("radar_trend_reclaim_zone_low")
    trend_reclaim_high = row.get("trend_reclaim_zone_high") or row.get("radar_trend_reclaim_zone_high")
    deep_support_low = row.get("deep_support_zone_low") or row.get("radar_deep_support_zone_low")
    deep_support_high = row.get("deep_support_zone_high") or row.get("radar_deep_support_zone_high")
    zone_semantic_label = _drawer_clean_text(row.get("zone_semantic_label") or row.get("radar_zone_semantic_label"))
    primary_interpretation = _drawer_clean_text(
        row.get("primary_entry_interpretation") or row.get("radar_primary_entry_interpretation")
    )
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

    focus_line = _drawer_primary_entry_focus_text(
        entry_context_status=entry_context_status,
        price_position=price_position,
        technical_low=technical_low,
        technical_high=technical_high,
        chase_above=chase_above,
        near_term_repair_low=near_term_repair_low,
        near_term_repair_high=near_term_repair_high,
        trend_reclaim_low=trend_reclaim_low,
        trend_reclaim_high=trend_reclaim_high,
        confirmation_price=confirmation_price,
        invalidation_price=invalidation_price,
        technical_missing_reason=technical_missing_reason or technical_reason or _drawer_technical_missing_reason(technical_missing_fields),
        overlap=overlap,
    )
    conclusion = _drawer_entry_current_conclusion_html(
        row,
        entry_context_status=entry_context_status,
        price_position=price_position,
        label=label,
        hint=hint,
        reason=reason,
        focus_line=focus_line,
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
        lines.append("技术回踩区与追高风险区重叠；超过追高线部分不作为新增参考。")
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
        adaptive_pullback_low=adaptive_pullback_low,
        adaptive_pullback_high=adaptive_pullback_high,
        adaptive_pullback_label=adaptive_pullback_label,
        adaptive_pullback_reason=adaptive_pullback_reason,
        technical_repair_low=technical_repair_low,
        technical_repair_high=technical_repair_high,
        support_watch_low=support_watch_low,
        support_watch_high=support_watch_high,
        near_term_repair_low=near_term_repair_low,
        near_term_repair_high=near_term_repair_high,
        trend_reclaim_low=trend_reclaim_low,
        trend_reclaim_high=trend_reclaim_high,
        deep_support_low=deep_support_low,
        deep_support_high=deep_support_high,
        zone_semantic_label=zone_semantic_label,
        primary_interpretation=primary_interpretation,
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
    focus_line: str = "",
) -> str:
    status = _drawer_entry_primary_status_text(entry_context_status, price_position)
    action = _drawer_compact_action_text(hint or row.get("finalAction") or row.get("action") or "等待回踩")
    summary_reason = _drawer_entry_summary_reason(
        entry_context_status=entry_context_status,
        price_position=price_position,
        label=label,
        hint=hint,
        reason=reason,
    )
    lines = [
        "当前状态：" + status,
        "系统建议：" + action,
        "交易权限：由用户确认；买区提示不改变买入权限。",
        "一句话结论：" + summary_reason,
    ]
    if focus_line:
        lines.append("当前最重要观察区：" + focus_line)
    return _drawer_card_html("当前结论", status, lines)


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
    adaptive_pullback_low: object,
    adaptive_pullback_high: object,
    adaptive_pullback_label: str,
    adaptive_pullback_reason: str,
    technical_repair_low: object,
    technical_repair_high: object,
    support_watch_low: object,
    support_watch_high: object,
    near_term_repair_low: object,
    near_term_repair_high: object,
    trend_reclaim_low: object,
    trend_reclaim_high: object,
    deep_support_low: object,
    deep_support_high: object,
    zone_semantic_label: str,
    primary_interpretation: str,
    confirmation_price: object,
    invalidation_price: object,
    next_technical_steps: list[str],
    notes: list[str],
) -> str:
    effective_high = _drawer_effective_technical_high(technical_high, chase_above) if overlap else technical_high
    technical_available = _drawer_technical_zone_available(technical_low, technical_high)
    technical_row_label = "技术回踩区"
    adaptive_available = _drawer_technical_zone_available(adaptive_pullback_low, adaptive_pullback_high)
    adaptive_used = False
    if not technical_available and adaptive_available:
        technical_low = adaptive_pullback_low
        technical_high = adaptive_pullback_high
        effective_high = adaptive_pullback_high
        technical_available = True
        adaptive_used = True
        technical_row_label = adaptive_pullback_label or "技术回踩参考区"
    technical_range = _drawer_zone_range_text(technical_low, technical_high) if technical_available else "暂缺"
    if overlap and technical_available:
        technical_range = f"原 {technical_range}；有效 {_drawer_zone_range_text(technical_low, effective_high)}"
    technical_relation = _drawer_zone_relationship(current_price, technical_low, effective_high) if technical_available else (
        "缺失原因：" + _strip_missing_prefix(technical_missing_reason or "缺 EMA / ATR / swing / K线")
    )
    technical_usage = (adaptive_pullback_reason or "弱趋势下观察承接，不是自动买点") if adaptive_used else ("近端复核区" if technical_available else "当前使用：估值参考 / 暂不提供近端技术买点")
    structure_label = technical_structure_label or _drawer_technical_structure_label(technical_structure_status)
    structure_reason = _strip_missing_prefix(technical_structure_reason) if technical_structure_reason else "等待技术结构确认"
    near_term_repair_low = near_term_repair_low if _drawer_number(near_term_repair_low) is not None else technical_repair_low
    near_term_repair_high = near_term_repair_high if _drawer_number(near_term_repair_high) is not None else technical_repair_high
    deep_support_low = deep_support_low if _drawer_number(deep_support_low) is not None else support_watch_low
    deep_support_high = deep_support_high if _drawer_number(deep_support_high) is not None else support_watch_high
    near_repair_available = _drawer_technical_zone_available(near_term_repair_low, near_term_repair_high)
    trend_reclaim_available = _drawer_technical_zone_available(trend_reclaim_low, trend_reclaim_high)
    deep_support_available = _drawer_technical_zone_available(deep_support_low, deep_support_high)
    valuation_label = zone_semantic_label or _drawer_valuation_zone_label(current_price, buy_zone)
    next_step = next_technical_steps[0] if next_technical_steps else "等待收盘确认和相对强弱修复"
    rows = [
        (
            "技术结构",
            structure_label or "待确认",
            structure_reason,
            "判断当前是回踩、修复、破位还是筑底",
        ),
        (
            technical_row_label,
            technical_range,
            technical_relation,
            technical_usage,
        ),
        (
            "近端修复观察区",
            _drawer_zone_range_text(near_term_repair_low, near_term_repair_high) if near_repair_available else "暂缺",
            _drawer_zone_relationship(current_price, near_term_repair_low, near_term_repair_high)
            if near_repair_available
            else "缺 EMA20 / EMA50 / EMA200",
            "观察短线止跌和承接，不是自动买点",
        ),
        (
            "趋势确认区",
            _drawer_zone_range_text(trend_reclaim_low, trend_reclaim_high) if trend_reclaim_available else "暂缺",
            _drawer_zone_relationship(current_price, trend_reclaim_low, trend_reclaim_high)
            if trend_reclaim_available
            else "缺 EMA100 / EMA200",
            "确认中期趋势修复",
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
            valuation_label,
            valuation_deep_zone or format_buy_zone(buy_zone),
            _drawer_buy_zone_relationship(current_price, buy_zone),
            _drawer_valuation_zone_usage(valuation_label, primary_interpretation),
        ),
        (
            "深度支撑区",
            _drawer_zone_range_text(deep_support_low, deep_support_high) if deep_support_available else "暂缺",
            _drawer_zone_relationship(current_price, deep_support_low, deep_support_high)
            if deep_support_available
            else "缺 recent swing low",
            "极端回撤支撑，不是常规目标",
        ),
        (
            "追高风险区",
            "> " + _drawer_money_text(chase_above) if _drawer_number(chase_above) is not None else "暂缺",
            _drawer_chase_relationship(current_price, chase_above, overlap),
            "超过后系统不建议新增",
        ),
        (
            "最终纪律判断",
            _drawer_entry_context_status_text(entry_context_status, price_position),
            _drawer_compact_action_text(row.get("finalAction") or row.get("action") or ""),
            "交易纪律结果，不等同于自动买入",
        ),
    ]
    key_rows = _drawer_select_key_entry_zone_rows(
        rows,
        entry_context_status=entry_context_status,
        price_position=price_position,
        technical_available=technical_available,
    )
    key_body = _drawer_entry_zone_table_html(key_rows)
    full_body = _drawer_entry_zone_table_html(rows)
    notes_html = ""
    if notes:
        note_items = "".join(f"<li>{escape(str(item))}</li>" for item in notes if item)
        notes_html = f'<details class="drawer-low-priority"><summary>一致性提示</summary><ul>{note_items}</ul></details>'
    return (
        '<div class="drawer-card drawer-entry-zone-card">'
        '<div class="drawer-card-title">买区结构</div>'
        '<div class="drawer-card-headline">关键区间</div>'
        '<table class="drawer-entry-zone-table">'
        '<thead><tr><th>类型</th><th>区间 / 价格</th><th>当前关系</th><th>用途</th></tr></thead>'
        f"<tbody>{key_body}</tbody>"
        "</table>"
        '<details class="drawer-low-priority">'
        '<summary>查看完整买区结构</summary>'
        '<table class="drawer-entry-zone-table">'
        '<thead><tr><th>类型</th><th>区间 / 价格</th><th>当前关系</th><th>用途</th></tr></thead>'
        f"<tbody>{full_body}</tbody>"
        "</table>"
        "</details>"
        f"{notes_html}"
        "</div>"
    )


def _drawer_entry_zone_table_html(rows: list[tuple[str, str, str, str]]) -> str:
    return "".join(
        "<tr>"
        f"<td>{escape(kind)}</td>"
        f"<td>{escape(zone)}</td>"
        f"<td>{escape(relation)}</td>"
        f"<td>{escape(usage)}</td>"
        "</tr>"
        for kind, zone, relation, usage in rows
    )


def _drawer_select_key_entry_zone_rows(
    rows: list[tuple[str, str, str, str]],
    *,
    entry_context_status: str,
    price_position: str,
    technical_available: bool,
) -> list[tuple[str, str, str, str]]:
    status = str(entry_context_status or "").strip()
    price_status = str(price_position or "").strip()
    if status in {"VALUATION_REVIEW_TECHNICAL_UNCONFIRMED", "VALUE_REVIEW_NEAR_TERM_REPAIR"}:
        preferred = ["近端修复观察区", "确认线", "趋势确认区"]
    elif status == "IN_TECHNICAL_PULLBACK_ZONE":
        preferred = ["技术回踩区", "追高风险区", "确认线"]
    elif status == "IN_CHASE_ZONE" or price_status == "IN_CHASE_ZONE":
        preferred = ["追高风险区", "技术回踩区", "最终纪律判断"]
    elif status in {"BELOW_TECHNICAL_PULLBACK_ZONE", "BELOW_VALUATION_REFERENCE", "BELOW_DISCIPLINE_BUY_ZONE"} or (
        price_status == "BELOW_BUY_ZONE"
    ):
        preferred = ["近端修复观察区", "失效线", "确认线"]
    elif status == "ZONE_MISSING" or price_status == "ZONE_MISSING":
        preferred = ["技术回踩区", "技术结构", "最终纪律判断"]
    elif technical_available:
        preferred = ["技术回踩区", "确认线", "追高风险区"]
    else:
        preferred = ["近端修复观察区", "确认线", "趋势确认区"]

    selected: list[tuple[str, str, str, str]] = []
    for label in preferred:
        row = _drawer_find_entry_zone_row(rows, label)
        if row and (label in {"技术结构", "技术回踩区", "最终纪律判断"} or _drawer_entry_zone_row_has_value(row)):
            selected.append(row)

    for row in rows:
        if len(selected) >= 3:
            break
        if row in selected:
            continue
        if _drawer_entry_zone_row_has_value(row):
            selected.append(row)
    return selected[:3] or rows[:3]


def _drawer_find_entry_zone_row(
    rows: list[tuple[str, str, str, str]], label: str
) -> tuple[str, str, str, str] | None:
    for row in rows:
        if row[0] == label:
            return row
    return None


def _drawer_entry_zone_row_has_value(row: tuple[str, str, str, str]) -> bool:
    return _drawer_has_display_value(row[1])


def _drawer_primary_entry_focus_text(
    *,
    entry_context_status: str,
    price_position: str,
    technical_low: object,
    technical_high: object,
    chase_above: object,
    near_term_repair_low: object,
    near_term_repair_high: object,
    trend_reclaim_low: object,
    trend_reclaim_high: object,
    confirmation_price: object,
    invalidation_price: object,
    technical_missing_reason: str,
    overlap: bool,
) -> str:
    status = str(entry_context_status or "").strip()
    price_status = str(price_position or "").strip()
    technical_available = _drawer_technical_zone_available(technical_low, technical_high)
    effective_high = _drawer_effective_technical_high(technical_high, chase_above) if overlap else technical_high
    confirmation = _drawer_money_text(confirmation_price)
    invalidation = _drawer_money_text(invalidation_price)

    if status in {"VALUATION_REVIEW_TECHNICAL_UNCONFIRMED", "VALUE_REVIEW_NEAR_TERM_REPAIR"}:
        parts = []
        near = _drawer_zone_range_text(near_term_repair_low, near_term_repair_high)
        trend = _drawer_zone_range_text(trend_reclaim_low, trend_reclaim_high)
        if _drawer_has_display_value(near):
            parts.append("近端观察 " + near)
        if _drawer_has_display_value(confirmation):
            parts.append("确认线 " + confirmation)
        elif _drawer_has_display_value(trend):
            parts.append("趋势确认 " + trend)
        return "；".join(parts)

    if status == "IN_TECHNICAL_PULLBACK_ZONE" and technical_available:
        parts = ["有效技术复核区 " + _drawer_zone_range_text(technical_low, effective_high)]
        chase = _drawer_money_text(chase_above)
        if _drawer_has_display_value(chase):
            parts.append("追高线 " + chase)
        return "；".join(parts)

    if status == "IN_CHASE_ZONE" or price_status == "IN_CHASE_ZONE":
        chase = _drawer_money_text(chase_above)
        if _drawer_has_display_value(chase):
            return "追高线 " + chase + "；等回踩"
        if technical_available:
            return "回踩等待区 " + _drawer_zone_range_text(technical_low, technical_high)

    if status in {"BELOW_TECHNICAL_PULLBACK_ZONE", "BELOW_VALUATION_REFERENCE", "BELOW_DISCIPLINE_BUY_ZONE"} or (
        price_status == "BELOW_BUY_ZONE"
    ):
        parts = []
        near = _drawer_zone_range_text(near_term_repair_low, near_term_repair_high)
        if _drawer_has_display_value(near):
            parts.append("近端观察 " + near)
        if _drawer_has_display_value(invalidation):
            parts.append("失效线 " + invalidation)
        if _drawer_has_display_value(confirmation):
            parts.append("确认线 " + confirmation)
        return "；".join(parts)

    if status == "ZONE_MISSING" or price_status == "ZONE_MISSING":
        return "缺失原因：" + _strip_missing_prefix(technical_missing_reason or "缺 EMA / ATR / swing / K线")

    if technical_available:
        return "技术回踩区 " + _drawer_zone_range_text(technical_low, effective_high)
    near = _drawer_zone_range_text(near_term_repair_low, near_term_repair_high)
    if _drawer_has_display_value(near):
        return "近端观察 " + near
    return ""


def _drawer_entry_primary_status_text(entry_context_status: str, price_position: str) -> str:
    status = str(entry_context_status or "").strip()
    if status == "IN_TECHNICAL_PULLBACK_ZONE":
        return "技术回踩区内"
    if status == "ABOVE_TECHNICAL_PULLBACK_ZONE":
        return "买区外"
    if status == "BELOW_TECHNICAL_PULLBACK_ZONE":
        return "跌破结构区"
    if status == "VALUATION_REVIEW_TECHNICAL_UNCONFIRMED":
        return "估值可复核"
    if status == "VALUE_REVIEW_NEAR_TERM_REPAIR":
        return "价值复核"
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
        return "进入技术回踩区，但量价/趋势确认不足，需复核趋势修复和估值风险。"
    if status == "ABOVE_TECHNICAL_PULLBACK_ZONE":
        return "价格仍高于技术回踩区，等待更好的近端复核位置。"
    if status == "BELOW_TECHNICAL_PULLBACK_ZONE":
        return "价格跌破技术结构参考区，先复核基本面和趋势是否恶化。"
    if status == "VALUATION_REVIEW_TECHNICAL_UNCONFIRMED":
        return "估值进入复核区，但技术结构仍在弱趋势修复中；等待短期均线和收盘确认。"
    if status == "VALUE_REVIEW_NEAR_TERM_REPAIR":
        return "当前估值已具备复核价值，价格位于近端修复观察区；趋势和结构尚未确认，系统建议先复核，不自动买入。"
    if status == "IN_CHASE_ZONE" or price_position == "IN_CHASE_ZONE":
        return "价格进入追高风险区，系统不建议新增。"
    if price_position == "BELOW_BUY_ZONE":
        return "当前低于估值参考，不等于结构破坏；需等待 EMA、相对强弱和收盘确认。"
    return reason or hint or label or "暂无说明。"


def _drawer_zone_overlaps_chase(technical_low: object, technical_high: object, chase_above: object) -> bool:
    high = _drawer_number(technical_high)
    chase = _drawer_number(chase_above)
    return high is not None and chase is not None and high > chase


def _drawer_technical_zone_available(technical_low: object, technical_high: object) -> bool:
    return _drawer_number(technical_low) is not None and _drawer_number(technical_high) is not None


def _drawer_valuation_zone_label(current_price: float | None, buy_zone: object) -> str:
    low = high = None
    if isinstance(buy_zone, dict):
        low = _drawer_number(buy_zone.get("lower"))
        high = _drawer_number(buy_zone.get("upper"))
    else:
        low = _drawer_number(getattr(buy_zone, "lower", None))
        high = _drawer_number(getattr(buy_zone, "upper", None))
    if current_price is None or low is None or high is None:
        return "估值参考区"
    if high <= current_price * 0.75:
        return "深度估值区"
    if low <= current_price * 1.08 or current_price <= high:
        return "估值参考区"
    return "估值复核区"


def _drawer_valuation_zone_usage(label: str, primary_interpretation: str) -> str:
    if primary_interpretation:
        return primary_interpretation
    if label == "深度估值区":
        return "极端安全区，不是当前近端买点"
    return "估值进入研究区，不等于自动买入"


def _drawer_technical_structure_label(status: str) -> str:
    return {
        "UPTREND_PULLBACK": "强趋势回踩",
        "WEAK_TREND_REPAIR": "弱趋势修复中",
        "BREAKDOWN_REVIEW": "破位复核",
        "RANGE_BASE_BUILDING": "区间筑底",
        "DATA_MISSING": "数据缺失",
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
    }.get(str(field), _drawer_unknown_display_text(field, "关键字段"))


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
    if status == "VALUATION_REVIEW_TECHNICAL_UNCONFIRMED":
        return "估值可复核，技术待确认"
    if status == "VALUE_REVIEW_NEAR_TERM_REPAIR":
        return "价值复核，结构待确认"
    if status == "IN_DISCIPLINE_BUY_ZONE":
        return "位于技术回踩带"
    if status in {"BELOW_DISCIPLINE_BUY_ZONE", "BELOW_VALUATION_REFERENCE"}:
        return "低于估值参考，等待结构确认"
    if status == "IN_CHASE_ZONE":
        return "进入追高风险区，系统不建议新增"
    return format_zone_status(price_position)


def _drawer_zone_range_text(low: object, high: object) -> str:
    low_text = _drawer_money_text(low)
    high_text = _drawer_money_text(high)
    if _drawer_has_display_value(low_text) and _drawer_has_display_value(high_text):
        return f"{low_text} - {high_text}"
    if _drawer_has_display_value(high_text):
        return "<= " + high_text
    if _drawer_has_display_value(low_text):
        return ">= " + low_text
    return "暂缺"


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
    is_data_missing = status_code == "DATA_MISSING" or status in {"数据不足", "数据缺失"}
    score_text = "待补数据" if numeric_score is None or is_data_missing else f"{numeric_score:.0f} 分"
    gaps: list[str] = []
    detail_lines: list[str] = []
    if _is_unknown_structure_text(decline):
        gaps.append("下跌原因未维护")
    else:
        detail_lines.append(f"下跌原因：{decline}")
    thesis_label = _structure_thesis_label(thesis)
    if _is_unknown_structure_text(thesis_label):
        gaps.append("主线状态未维护")
    else:
        detail_lines.append(f"主线状态：{thesis_label}")
    for label, value, gap in (
        ("技术承接", support, "缺 K 线或支撑数据"),
        ("收盘确认", close, "缺收盘确认数据"),
        ("相对强弱", relative, "缺 SPY/QQQ 相对强弱"),
        ("量能确认", volume, "缺成交量数据"),
    ):
        if _is_structure_missing_value(value):
            gaps.append(gap)
        else:
            detail_lines.append(f"{label}：{value}")
    if reasons:
        detail_lines.append("依据：" + "；".join(reasons[:2]))
    if warnings:
        detail_lines.append("风险：" + "；".join(warnings[:2]))
    lines = [
        "只读提示，不改变主结论和账户层新增额度。",
        "一句话提示：" + _structure_entry_summary_hint(status_code, status, is_data_missing, bool(gaps)),
    ]
    if gaps:
        lines.append("关键缺口：" + "；".join(_dedupe_text(gaps)[:4]))
    if steps:
        lines.append("下一步：" + "；".join(steps[:2]))
    detail_html = ""
    if detail_lines:
        items = "".join(f"<li>{escape(str(line))}</li>" for line in detail_lines if line)
        detail_html = f'<details class="drawer-low-priority"><summary>查看结构依据</summary><ul>{items}</ul></details>'
    items = "".join(f"<li>{escape(str(line))}</li>" for line in lines if line)
    return (
        '<div class="drawer-card">'
        '<div class="drawer-card-title">结构买入提示</div>'
        f'<div class="drawer-card-headline">{escape(status)}｜{escape(score_text)}</div>'
        f"<ul>{items}</ul>"
        f"{detail_html}"
        "</div>"
    )


def _drawer_pullback_acceptance_card_html(row: pd.Series) -> str:
    snapshot = row.get("pullbackAcceptance")
    if not isinstance(snapshot, dict):
        snapshot = {
            "acceptance_status": row.get("acceptanceStatus"),
            "acceptance_score": row.get("acceptanceScore"),
            "acceptance_reasons": row.get("acceptanceReasons"),
            "acceptance_warnings": row.get("acceptanceWarnings"),
            "next_acceptance_steps": row.get("acceptanceNextSteps"),
        }
    status_code = str(snapshot.get("acceptance_status") or snapshot.get("acceptanceStatus") or "").strip()
    status = str(snapshot.get("status_label") or _acceptance_status_label(status_code) or "数据不足")
    score = _drawer_number(snapshot.get("acceptance_score", snapshot.get("acceptanceScore")))
    score_text = "待补数据" if score is None or status_code == "DATA_MISSING" else f"{score:.0f} 分"
    support = _drawer_clean_text(snapshot.get("support_hold_status") or snapshot.get("supportHoldStatus"))
    close = _drawer_clean_text(snapshot.get("close_confirmation_status") or snapshot.get("closeConfirmationStatus"))
    volume = _drawer_clean_text(snapshot.get("volume_confirmation_status") or snapshot.get("volumeConfirmationStatus"))
    relative = _drawer_clean_text(
        snapshot.get("relative_strength_confirmation_status") or snapshot.get("relativeStrengthConfirmationStatus")
    )
    vwap = _drawer_clean_text(snapshot.get("vwap_confirmation_status") or snapshot.get("vwapConfirmationStatus"))
    reasons = _drawer_text_list(snapshot.get("acceptance_reasons") or snapshot.get("acceptanceReasons"))
    warnings = _drawer_text_list(snapshot.get("acceptance_warnings") or snapshot.get("acceptanceWarnings"))
    steps = _drawer_text_list(snapshot.get("next_acceptance_steps") or snapshot.get("nextAcceptanceSteps"))
    lines = [
        "只读提示：不改变主结论和账户层新增额度。",
        f"支撑：{support or '数据不足'}",
        f"收盘：{close or '数据不足'}",
        f"量能：{volume or '数据不足'}",
        f"相对强弱：{relative or '数据不足'}",
        f"VWAP：{vwap or '缺失，使用日线替代'}",
    ]
    if warnings:
        lines.append("风险：" + "；".join(warnings[:2]))
    context_lines = pullback_acceptance_context_lines(snapshot, _drawer_pullback_acceptance_context(row))
    lines.extend(context_lines)
    if steps:
        lines.append("下一步：" + "；".join(steps[:2]))
    detail_html = ""
    if reasons:
        items = "".join(f"<li>{escape(str(line))}</li>" for line in reasons[:4] if line)
        detail_html = f'<details class="drawer-low-priority"><summary>查看承接依据</summary><ul>{items}</ul></details>'
    items = "".join(f"<li>{escape(str(line))}</li>" for line in lines if line)
    return (
        '<div class="drawer-card">'
        '<div class="drawer-card-title">回踩承接确认</div>'
        f'<div class="drawer-card-headline">{escape(status)}｜{escape(score_text)}</div>'
        f"<ul>{items}</ul>"
        f"{detail_html}"
        "</div>"
    )


def _drawer_pullback_acceptance_context(row: pd.Series) -> dict[str, object]:
    context = row.to_dict()
    for key in ("rawSnapshot", "rawTechnicals"):
        raw = row.get(key)
        if isinstance(raw, dict):
            context.update(raw)
    return context


def _drawer_volume_price_acceptance_card_html(row: pd.Series) -> str:
    snapshot = row.get("volumePriceAcceptance")
    if not isinstance(snapshot, dict):
        snapshot = {
            "volume_price_status": row.get("volumePriceStatus"),
            "volume_price_score": row.get("volumePriceScore"),
            "acceptance_reason_cn": row.get("volumePriceReasonCn"),
        }
    score = _drawer_number(snapshot.get("volume_price_score", snapshot.get("volumePriceScore")))
    status_code = str(snapshot.get("volume_price_status") or snapshot.get("volumePriceStatus") or "").strip()
    status = str(snapshot.get("status_label") or _volume_price_status_label(status_code, score=score) or "数据不足")
    score_text = "待补数据" if score is None or status_code == "DATA_MISSING" else f"{score:.0f} 分"
    volume_ratio = _drawer_number(snapshot.get("volume_ratio", snapshot.get("volumeRatio")))
    volume_ma20 = _drawer_number(snapshot.get("volume_ma20", snapshot.get("volumeMa20")))
    volume_regime_cn = _drawer_clean_text(snapshot.get("volume_regime_cn") or snapshot.get("volumeRegimeCn")) or "量能待确认"
    volume_interpretation = _drawer_clean_text(
        snapshot.get("volume_interpretation_cn") or snapshot.get("volumeInterpretationCn")
    )
    close_position = _drawer_number(snapshot.get("close_position", snapshot.get("closePosition")))
    candle = _drawer_clean_text(snapshot.get("candle_signal_cn") or snapshot.get("candleSignalCn")) or "K线待确认"
    volume = _drawer_clean_text(snapshot.get("volume_signal_cn") or snapshot.get("volumeSignalCn")) or "量能待确认"
    support = _drawer_clean_text(snapshot.get("support_signal_cn") or snapshot.get("supportSignalCn")) or "支撑待确认"
    confirmation = _drawer_clean_text(snapshot.get("confirmation_signal_cn") or snapshot.get("confirmationSignalCn")) or "确认待补"
    reason = _drawer_clean_text(snapshot.get("acceptance_reason_cn") or snapshot.get("volumePriceReasonCn"))
    distribution_count = _drawer_number(snapshot.get("distribution_count_10d", snapshot.get("distributionCount10d")))
    deductions = _drawer_text_list(snapshot.get("risk_deductions") or snapshot.get("riskDeductions"))
    lines = [
        "只读提示：不改变买区主建议、研究状态或组合同步。",
        f"量能标签：{volume_regime_cn}",
        f"量比：{'缺失' if volume_ratio is None else f'{volume_ratio:.2f}x'}",
        f"20日均量：{_drawer_volume_text(volume_ma20)}",
        f"量能解释：{volume_interpretation}" if volume_interpretation else "",
        f"收盘位置：{'缺失' if close_position is None else f'{close_position:.0%}'}",
        f"K线：{candle}",
        f"量能：{volume}",
        f"支撑：{support}",
        f"确认：{confirmation}",
        f"10日派发日：{0 if distribution_count is None else int(distribution_count)}",
    ]
    if deductions:
        lines.append("风险扣分：" + "；".join(deductions[:3]))
    if reason:
        lines.append("结论：" + reason)
    items = "".join(f"<li>{escape(str(line))}</li>" for line in lines if line)
    return (
        '<div class="drawer-card">'
        '<div class="drawer-card-title">量价承接</div>'
        f'<div class="drawer-card-headline">{escape(status)}｜{escape(score_text)}</div>'
        f"<ul>{items}</ul>"
        "</div>"
    )


def _volume_price_status_label(value: object, *, score: float | None = None) -> str:
    if str(value or "") == "FORMING" and score is not None and score < 55:
        return "初步承接，尚未确认"
    return {
        "ACCEPTANCE_CONFIRMED": "已确认",
        "FORMING": "形成中",
        "UNCONFIRMED": "未确认",
        "FAILED": "失效",
        "OVEREXTENDED_SUPPORT_READ": "脱离观察区",
        "DATA_MISSING": "数据缺失",
    }.get(str(value or ""), _drawer_unknown_display_text(value, "数据待复核"))


def _acceptance_status_label(value: object) -> str:
    return {
        "ACCEPTANCE_CONFIRMED": "承接确认",
        "ACCEPTANCE_FORMING": "承接形成中",
        "ACCEPTANCE_UNCONFIRMED": "承接未确认",
        "ACCEPTANCE_FAILED": "承接失败",
        "DATA_MISSING": "数据缺失",
    }.get(str(value or ""), _drawer_unknown_display_text(value, "数据待复核"))


def _structure_entry_summary_hint(status_code: str, status_label: str, is_data_missing: bool, has_gaps: bool) -> str:
    code = str(status_code or "").strip()
    if is_data_missing or has_gaps:
        return "关键数据不足，结构待确认；不要把缺数据当成结构破坏。"
    if code == "STRUCTURE_CONFIRMED" or status_label == "结构确认":
        return "结构较好，可结合仓位计划复核执行。"
    if code == "STRUCTURE_FORMING" or status_label == "结构形成中":
        return "结构正在形成，仍需收盘和相对强弱确认。"
    if code == "DIP_ONLY" or status_label == "只是下跌":
        return "价格下跌但承接证据不足，先观察买方是否出现。"
    if code == "STRUCTURE_BROKEN" or status_label == "结构破坏":
        return "已有结构破坏信号，先复核基本面和趋势。"
    return "结构状态待确认，先看下一步验证条件。"


def _structure_status_label(value: object) -> str:
    return {
        "STRUCTURE_CONFIRMED": "结构确认",
        "STRUCTURE_FORMING": "结构形成中",
        "DIP_ONLY": "只是下跌",
        "STRUCTURE_BROKEN": "结构破坏",
        "DATA_MISSING": "数据缺失",
    }.get(str(value or ""), "")


def _structure_thesis_label(value: object) -> str:
    return {
        "INTACT": "主线仍在",
        "WEAKENING": "主线走弱",
        "BROKEN": "主线破坏",
        "UNKNOWN": "主线待维护",
    }.get(str(value or "").upper(), _drawer_unknown_display_text(value, "主线待维护"))


def _drawer_next_action_html(row: pd.Series, deps: DashboardDrawerDeps | None = None) -> str:
    drawer_deps = _drawer_deps(deps)
    action = _drawer_compact_action_text(row.get("finalAction") or row.get("action") or "")
    current_add = _drawer_display_text(row.get("currentAddLimit") or row.get("maxSuggestedPosition"), "待补")
    max_weight = _drawer_display_text(row.get("maxPortfolioWeight"), "待补")
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
        _drawer_card_html("公司质量解释", _drawer_display_text(row.get("qualityRating"), "待补"), [
            "主要加分：" + drawer_deps.translated_join(row.get("keyPositiveDrivers"), limit=4),
            "主要扣分：" + drawer_deps.translated_join(drawer_deps.quality_negative_items(row), limit=4),
            str(summary.get("quality") or ""),
        ]),
        _drawer_card_html("历史估值参考，仅供辅助", entry_display or _drawer_display_text(row.get("entryRating"), "待补"), [
            "该参考不改变买入权限，买区建议以技术承接 buy_zone_context 为准。",
            "该区域来自历史估值参考 / 历史入口字段，不等同于主表买区建议。",
            _clean_buy_point_summary_text(summary.get("valuation"), row),
            _clean_buy_point_summary_text(summary.get("technical"), row),
            _clean_buy_point_summary_text(summary.get("entry"), row),
            _entry_context_note(row),
        ]),
        _drawer_card_html("风险解释", _drawer_display_text(row.get("riskRating"), "待补"), [
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
    return "暂缺" if number is None else f"${number:,.2f}"


def _drawer_volume_text(value: object) -> str:
    number = _drawer_number(value)
    if number is None:
        return "缺失"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    return f"{number:.0f}"


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
    return "暂缺" if number is None else f"{number:+.1f}%"


def _drawer_has_display_value(value: object) -> bool:
    text = str(value or "").strip()
    return text not in {"", "N/A", "NA", "暂缺", "待补", "> N/A", "<= N/A", ">= N/A", "> 暂缺", "<= 暂缺", ">= 暂缺"}


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


def _drawer_unknown_display_text(value: object, fallback: str) -> str:
    text = _drawer_clean_text(value)
    if not text:
        return fallback
    if all(ch.isascii() and (ch.isalnum() or ch in {"_", "-"}) for ch in text):
        return fallback
    return text


def _drawer_display_text(value: object, fallback: str = "待补") -> str:
    text = _drawer_clean_text(value)
    if not text or text.upper() in {"N/A", "NA", "NONE", "NULL"}:
        return fallback
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
        return "追高风险提醒"
    if any(token in text for token in ("可加仓", "可小仓", "可正常", "分批", "ALLOW")):
        return "小仓观察建议"
    if any(token in text for token in ("复核", "确认", "REVIEW")):
        return "等突破再评估"
    if any(token in text for token in ("只观察", "观察", "等回踩", "等待")):
        return "等待回踩"
    if any(token in text for token in ("暂不", "不建议新增", "WAIT", "AVOID")):
        return "暂不参与"
    return _drawer_unknown_display_text(text, "待复核")


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
        "VALUE_REVIEW_NEAR_TERM_REPAIR": "价值复核",
        "VALUATION_REVIEW_TECHNICAL_UNCONFIRMED": "估值可复核",
        "ZONE_MISSING": "无买区",
    }
    context_status = str(row.get("entry_context_status") or row.get("radar_entry_context_status") or "").strip()
    if context_status in mapping:
        return mapping[context_status]
    if status in mapping:
        return mapping[status]
    label = str(row.get("entry_display_label") or row.get("entryRating") or "").strip()
    if "追高" in label:
        return "追高区"
    if "跌破结构区" in label:
        return "跌破结构区"
    if "价值复核" in label:
        return "价值复核"
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
    reasons = advisory_reason_list(row)
    if reasons:
        return reasons[0]
    hint = str(row.get("entry_action_hint") or "").strip()
    if hint:
        return hint
    action = _drawer_compact_action_text(row.get("finalAction") or row.get("action") or "")
    if action in {"可加仓", "小仓观察建议"}:
        return "仍需按交易计划控制仓位。"
    if action in {"禁止新增", "追高风险提醒"}:
        return "当前为高风险新增场景，建议先复核。"
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
        return f"公司风险低，但历史估值参考为 {entry}，当前新增仓位受限；这不是公司质量问题，而是估值参考还没到。"
    parts = [_combined_entry_note(row), str(summary.get("technical") or ""), str(summary.get("valuation") or "")]
    if not parts[0]:
        parts.insert(0, str(summary.get("entry") or ""))
    text = " ".join(_clean_buy_point_summary_text(part, row) for part in parts if part).strip()
    return text or "当前建议由质量、历史估值参考、风险、估值和数据置信度综合得出。"


def _combined_entry_note(row: pd.Series) -> str:
    combined = row.get("combinedEntry")
    if not isinstance(combined, dict):
        return ""
    label = str(combined.get("entryLabel") or "").strip()
    return f"历史估值参考：{label}。" if label else ""


def _clean_buy_point_summary_text(text: object, row: pd.Series) -> str:
    value = str(text or "")
    entry_label, entry_grade, _entry_raw = _entry_rating_display_parts(row)
    display = _entry_rating_chip_text(entry_label, entry_grade)
    raw = str(row.get("entryRating") or "").strip()
    if raw and display:
        value = value.replace(f"买点评级为{raw}", f"历史估值参考为{display}")
        value = value.replace(f"买点评级为 {raw}", f"历史估值参考为 {display}")
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
        return "不主动新增不是因为公司质量差，而是因为当前历史估值参考不够理想；主表买区仍以纪律口径为准。"
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
        return "等待回踩"
    if "小仓" in action:
        return "小仓观察"
    if "分批" in action:
        return "分批执行"
    if "禁止" in action:
        return "追高风险提醒"
    if "复核" in action:
        return "等突破再评估"
    if "只观察" in action or "观察" in action:
        return "等待回踩"
    return _drawer_unknown_display_text(action, "等待回踩")


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
            f'<li><span>{escape(label)}</span><strong>{escape(_drawer_display_text(row.get(key), "待补"))}</strong></li>'
            for key, label in metrics
            if not (key == "fcfMargin" and row.get(key) == "N/A")
        )
        blocks.append(f'<div class="drawer-metric-group"><b>{escape(group_name)}</b><ul>{items}</ul></div>')
    return "".join(blocks)
