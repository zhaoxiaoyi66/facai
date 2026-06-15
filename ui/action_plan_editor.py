from __future__ import annotations

from html import escape
from typing import Any

import streamlit as st

from formatting import format_currency, format_percent


BUY_FIELDS = (
    ("first_buy_price", "第一笔买入价"),
    ("second_buy_price", "第二笔买入价"),
    ("third_buy_price", "深度折价买入价"),
)
TRANCHE_DEFINITIONS = (
    ("first", "第一笔买入", "first_buy_price"),
    ("second", "第二笔买入", "second_buy_price"),
    ("deep", "深度折价买入", "third_buy_price"),
)

LIMIT_FIELDS = (
    ("target_position_pct", "计划最大仓位 %"),
    ("planned_position_pct", "当前新增上限 %"),
)

ADVANCED_FIELDS = (
    ("fair_value_low", "合理观察区下沿"),
    ("fair_value_high", "合理观察区上沿"),
    ("tranche_buy_low", "估值折价区下沿"),
    ("tranche_buy_high", "估值折价区上沿"),
    ("heavy_buy_below", "极端恐慌区低于"),
)


def render_plan_reference_card(active_zone: Any, suggestion: Any, final_decision: Any = None) -> None:
    _render_plan_editor_styles()
    combined = _combined_entry(active_zone)
    technical = _technical_entry(active_zone)
    combined_trigger = _precision_value(
        active_zone,
        _first_value(combined.get("combinedTriggerPrice"), _attr(suggestion, "firstBuyPrice")),
        "nextTriggerPrice",
        "trancheBuyHigh",
    )
    deep_discount = _precision_value(
        active_zone,
        _first_value(
            combined.get("deepDiscountPrice"),
            _attr(active_zone, "heavyBuyBelow"),
            _attr(suggestion, "thirdBuyPrice"),
        ),
        "heavyBuyBelow",
    )
    rows = [
        ("估值观察区", _range_money(_attr(active_zone, "fairValueLow"), _attr(active_zone, "fairValueHigh"))),
        (
            "技术回踩点",
            _money(
                _first_value(
                    combined.get("technicalPullbackPrice"),
                    technical.get("technicalEntryPrice"),
                )
            ),
        ),
        (
            "综合触发价",
            _money(combined_trigger),
        ),
        (
            "深度折价区",
            _money(deep_discount),
        ),
    ]
    st.markdown(_compact_grid_html(rows, "plan-reference-card"), unsafe_allow_html=True)


def build_plan_portfolio_context(ticker: str, active_zone: Any, portfolio_view: dict | None) -> dict[str, Any]:
    rows = (portfolio_view or {}).get("rows") or []
    symbol = str(ticker or "").upper()
    row = next((item for item in rows if str(item.get("symbol") or "").upper() == symbol), {})
    summary = (portfolio_view or {}).get("summary") or {}
    return {
        "currentShares": row.get("quantity") or 0,
        "currentMarketValue": row.get("marketValue") or 0,
        "currentPrice": row.get("currentPrice") or _attr(active_zone, "currentPrice"),
        "totalPortfolioValue": summary.get("totalPortfolioValue"),
        "cashBalance": summary.get("cashBalance"),
    }


def render_plan_preview_card(plan: dict, suggestion: Any, active_zone: Any, final_decision: Any = None) -> None:
    _render_plan_editor_styles()
    system_values = _system_values(suggestion, active_zone, final_decision)
    rows = []
    for tranche in _plan_tranches(plan, system_values):
        rows.append((str(tranche["label"]), _tranche_summary(tranche)))
    for field, label in LIMIT_FIELDS:
        value = _plan_or_system(plan, field, system_values[field])
        rows.append((label, _value_with_source(value, _has_plan_value(plan, field), True)))
    rows.append(
        (
            "追高风险线",
            _value_with_source(
                _plan_or_system(plan, "no_chase_above", system_values["no_chase_above"]),
                _has_plan_value(plan, "no_chase_above"),
                False,
            ),
        )
    )
    st.markdown(_compact_grid_html(rows, "plan-preview-card"), unsafe_allow_html=True)


def render_buy_plan_editor(
    ticker: str,
    plan_store: Any,
    plan: dict,
    suggestion: Any,
    active_zone: Any,
    final_decision: Any = None,
    *,
    key_prefix: str,
    portfolio_context: dict[str, Any] | None = None,
) -> bool:
    _render_plan_editor_styles()
    system_values = _system_values(suggestion, active_zone, final_decision)
    defaults = _current_state_values(plan, system_values)
    _ensure_editor_state(key_prefix, defaults)

    action_cols = st.columns([1.2, 3.8])
    if action_cols[0].button("使用系统建议填充", key=f"{key_prefix}:fill-system", width="stretch"):
        _set_editor_state(key_prefix, _state_values(system_values, plan))
        st.toast(f"{ticker} 已填入系统建议，可继续手动调整。")
    action_cols[1].caption("系统建议只读；保存后会作为你的手动计划，不影响系统建议。")

    with st.form(f"{key_prefix}:form"):
        st.markdown('<div class="plan-editor-section-title">A. 我的买入计划</div>', unsafe_allow_html=True)
        tranches = _render_tranche_rows(key_prefix, plan, system_values, portfolio_context)
        first_buy_price = tranches[0]["price"]
        second_buy_price = tranches[1]["price"]
        third_buy_price = tranches[2]["price"]

        limit_cols = st.columns(2)
        target_position_pct = _text_field(
            limit_cols[0],
            key_prefix,
            "target_position_pct",
            "计划最大仓位 %",
            system_values["target_position_pct"],
            is_percent=True,
        )
        planned_position_pct = _text_field(
            limit_cols[1],
            key_prefix,
            "planned_position_pct",
            "当前新增上限 %",
            system_values["planned_position_pct"],
            is_percent=True,
        )

        st.markdown('<div class="plan-editor-section-title">B. 风控 / 复核条件</div>', unsafe_allow_html=True)
        no_chase_above = _text_field(
            st,
            key_prefix,
            "no_chase_above",
            "追高风险线",
            system_values["no_chase_above"],
        )
        risk_cols = st.columns(2)
        stop_adding_condition = risk_cols[0].text_area(
            "停止加仓条件",
            key=_field_key(key_prefix, "stop_adding_condition"),
            height=78,
        )
        risk_cols[0].caption(f"系统建议：{_text_hint(system_values['stop_adding_condition'])} · 用户可手动覆盖")
        invalidation_condition = risk_cols[1].text_area(
            "复核条件",
            key=_field_key(key_prefix, "invalidation_condition"),
            height=78,
        )
        risk_cols[1].caption(f"系统建议：{_text_hint(system_values['invalidation_condition'])} · 用户可手动覆盖")
        earnings_review_points = st.text_area(
            "财报复核点",
            key=_field_key(key_prefix, "earnings_review_points"),
            height=72,
        )
        st.caption(f"系统建议：{_text_hint(system_values['earnings_review_points'])} · 用户可手动覆盖")

        with st.expander("高级参数", expanded=False):
            st.caption("这些是系统买区边界，默认不需要手动改。")
            advanced_cols = st.columns(3)
            fair_value_low = _text_field(advanced_cols[0], key_prefix, "fair_value_low", "合理观察区下沿", system_values["fair_value_low"])
            fair_value_high = _text_field(advanced_cols[1], key_prefix, "fair_value_high", "合理观察区上沿", system_values["fair_value_high"])
            tranche_buy_low = _text_field(advanced_cols[2], key_prefix, "tranche_buy_low", "估值折价区下沿", system_values["tranche_buy_low"])
            advanced_cols_2 = st.columns(2)
            tranche_buy_high = _text_field(advanced_cols_2[0], key_prefix, "tranche_buy_high", "估值折价区上沿", system_values["tranche_buy_high"])
            heavy_buy_below = _text_field(advanced_cols_2[1], key_prefix, "heavy_buy_below", "极端恐慌区低于", system_values["heavy_buy_below"])

        st.markdown(
            f'<div class="plan-editor-save-preview">{escape(_plan_sentence(tranches, no_chase_above))}</div>',
            unsafe_allow_html=True,
        )
        submitted = st.form_submit_button("保存我的买入计划", width="stretch")

    if not submitted:
        return False

    plan_store.save_plan(
        ticker,
        {
            "target_position_pct": target_position_pct,
            "planned_position_pct": planned_position_pct,
            "first_buy_price": first_buy_price,
            "second_buy_price": second_buy_price,
            "third_buy_price": third_buy_price,
            "buy_plan_tranches": _saved_tranches(tranches),
            "no_chase_above": _override_input_value(plan, "no_chase_above", no_chase_above, system_values["no_chase_above"]),
            "fair_value_low": _override_input_value(plan, "fair_value_low", fair_value_low, system_values["fair_value_low"]),
            "fair_value_high": _override_input_value(plan, "fair_value_high", fair_value_high, system_values["fair_value_high"]),
            "tranche_buy_low": _override_input_value(plan, "tranche_buy_low", tranche_buy_low, system_values["tranche_buy_low"]),
            "tranche_buy_high": _override_input_value(plan, "tranche_buy_high", tranche_buy_high, system_values["tranche_buy_high"]),
            "heavy_buy_below": _override_input_value(plan, "heavy_buy_below", heavy_buy_below, system_values["heavy_buy_below"]),
            "stop_adding_condition": stop_adding_condition,
            "invalidation_condition": invalidation_condition,
            "earnings_review_points": earnings_review_points,
            "notes": plan.get("notes") or "",
        },
    )
    return True


def _compact_grid_html(rows: list[tuple[str, str]], class_name: str) -> str:
    items = "".join(
        f"<div><span>{escape(label)}</span><strong>{escape(str(value or 'N/A'))}</strong></div>"
        for label, value in rows
    )
    return f'<section class="research-card {escape(class_name)}"><div class="plan-editor-grid">{items}</div></section>'


def _text_field(container: Any, key_prefix: str, field: str, label: str, system_value: Any, *, is_percent: bool = False) -> str:
    value = container.text_input(label, key=_field_key(key_prefix, field))
    container.caption(f"系统建议：{_display_value(system_value, is_percent)} · 用户可手动覆盖")
    return value


def _render_tranche_rows(
    key_prefix: str,
    plan: dict,
    system_values: dict[str, Any],
    portfolio_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    st.markdown(
        '<div class="plan-tranche-head">'
        "<span>档位</span><span>买入价</span><span>买入股数</span><span>预计金额</span><span>买后总持仓</span><span>买后仓位</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    current_shares = _number((portfolio_context or {}).get("currentShares")) or 0.0
    cumulative_shares = 0.0
    cumulative_amount = 0.0
    rows: list[dict[str, Any]] = []
    for index, (_key, label, price_field) in enumerate(TRANCHE_DEFINITIONS):
        cols = st.columns([1.04, 1.02, 1.02, 1.08, 1.12, 1.12], gap="small")
        cols[0].markdown(f'<div class="plan-tranche-label"><b>{escape(label)}</b><span>系统建议：{escape(_money(system_values.get(price_field)))}</span></div>', unsafe_allow_html=True)
        price = cols[1].text_input("买入价", key=_field_key(key_prefix, _tranche_field(index, "price")), label_visibility="collapsed")
        shares = cols[2].text_input("买入股数", key=_field_key(key_prefix, _tranche_field(index, "shares")), label_visibility="collapsed")
        amount = _amount_from_price_and_shares(price, shares)
        row = _normalized_tranche(label, price, shares, amount, _session_value(key_prefix, _tranche_field(index, "note")))
        cumulative_shares += _number(row.get("shares")) or 0.0
        cumulative_amount += _number(row.get("amount")) or 0.0
        after = _after_buy_metrics(current_shares, cumulative_shares, cumulative_amount, portfolio_context)
        cols[3].markdown(_metric_cell_html(_money(row.get("amount")), "价格 × 股数"), unsafe_allow_html=True)
        cols[4].markdown(_metric_cell_html(_shares_text(after["totalShares"]), f"总市值 {after['marketValueText']}"), unsafe_allow_html=True)
        cols[5].markdown(_metric_cell_html(after["weightText"], after["cashText"]), unsafe_allow_html=True)
        rows.append(row)
    return rows


def _system_values(suggestion: Any, active_zone: Any, final_decision: Any = None) -> dict[str, Any]:
    return {
        "target_position_pct": _first_value(_attr(final_decision, "maxPortfolioWeightPercent"), _attr(suggestion, "maxPortfolioWeightPercent")),
        "planned_position_pct": _first_value(_attr(final_decision, "currentAddLimitPercent"), _attr(suggestion, "currentAddLimitPercent")),
        "first_buy_price": _precision_value(active_zone, _attr(suggestion, "firstBuyPrice"), "nextTriggerPrice", "trancheBuyHigh"),
        "second_buy_price": _precision_value(active_zone, _attr(suggestion, "secondBuyPrice"), "trancheBuyLow"),
        "third_buy_price": _precision_value(active_zone, _attr(suggestion, "thirdBuyPrice"), "heavyBuyBelow"),
        "no_chase_above": _precision_value(active_zone, _attr(active_zone, "noChaseAbove"), "noChaseAbove"),
        "fair_value_low": _precision_value(active_zone, _attr(active_zone, "fairValueLow"), "fairValueLow"),
        "fair_value_high": _precision_value(active_zone, _attr(active_zone, "fairValueHigh"), "fairValueHigh"),
        "tranche_buy_low": _precision_value(active_zone, _attr(active_zone, "trancheBuyLow"), "trancheBuyLow"),
        "tranche_buy_high": _precision_value(active_zone, _attr(active_zone, "trancheBuyHigh"), "trancheBuyHigh"),
        "heavy_buy_below": _precision_value(active_zone, _attr(active_zone, "heavyBuyBelow"), "heavyBuyBelow"),
        "stop_adding_condition": _attr(suggestion, "stopAddingCondition") or "",
        "invalidation_condition": _attr(suggestion, "thesisBreakCondition") or "",
        "earnings_review_points": _attr(suggestion, "earningsReviewCondition") or "",
    }


def _current_state_values(plan: dict, system_values: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field, system_value in system_values.items():
        values[field] = _input_text(_plan_or_system(plan, field, system_value))
    for index, tranche in enumerate(_plan_tranches(plan, system_values)):
        values[_tranche_field(index, "price")] = _input_text(tranche.get("price"))
        values[_tranche_field(index, "shares")] = _input_text(tranche.get("shares"))
        values[_tranche_field(index, "note")] = str(tranche.get("note") or "")
    return values


def _state_values(system_values: dict[str, Any], plan: dict | None = None) -> dict[str, str]:
    values = {field: _input_text(value) for field, value in system_values.items()}
    saved_tranches = (plan or {}).get("buy_plan_tranches") if isinstance(plan, dict) else []
    saved_tranches = saved_tranches if isinstance(saved_tranches, list) else []
    for index, (_key, _label, price_field) in enumerate(TRANCHE_DEFINITIONS):
        values[_tranche_field(index, "price")] = _input_text(system_values.get(price_field))
        values[_tranche_field(index, "shares")] = ""
        saved = saved_tranches[index] if index < len(saved_tranches) and isinstance(saved_tranches[index], dict) else {}
        values[_tranche_field(index, "note")] = str(saved.get("note") or "")
    return values


def _precision_contract(source: Any) -> dict[str, Any]:
    contract = getattr(source, "precisionContract", None)
    return dict(contract) if isinstance(contract, dict) else {}


def _precision_field_allowed(source: Any, field: str) -> bool:
    contract = _precision_contract(source)
    if not contract:
        return True
    return str(field) in set(contract.get("allowedPriceFields") or [])


def _precision_value(source: Any, value: Any, *fields: str) -> Any:
    if not fields:
        return value
    return value if any(_precision_field_allowed(source, field) for field in fields) else None


def _ensure_editor_state(key_prefix: str, values: dict[str, str]) -> None:
    for field, value in values.items():
        st.session_state.setdefault(_field_key(key_prefix, field), value)


def _set_editor_state(key_prefix: str, values: dict[str, str]) -> None:
    for field, value in values.items():
        st.session_state[_field_key(key_prefix, field)] = value


def _session_value(key_prefix: str, field: str) -> str:
    return str(st.session_state.get(_field_key(key_prefix, field)) or "")


def _field_key(key_prefix: str, field: str) -> str:
    return f"{key_prefix}:{field}"


def _tranche_field(index: int, field: str) -> str:
    return f"tranche_{index}_{field}"


def _plan_or_system(plan: dict, field: str, system_value: Any) -> Any:
    return plan.get(field) if _has_plan_value(plan, field) else system_value


def _plan_tranches(plan: dict, system_values: dict[str, Any]) -> list[dict[str, Any]]:
    raw = plan.get("buy_plan_tranches")
    raw = raw if isinstance(raw, list) else []
    tranches: list[dict[str, Any]] = []
    for index, (_key, label, price_field) in enumerate(TRANCHE_DEFINITIONS):
        saved = raw[index] if index < len(raw) and isinstance(raw[index], dict) else {}
        price = _first_value(saved.get("price"), _plan_or_system(plan, price_field, system_values.get(price_field)))
        tranches.append(
            _normalized_tranche(
                str(saved.get("label") or label),
                price,
                saved.get("shares"),
                saved.get("amount"),
                saved.get("note"),
            )
        )
    return tranches


def _normalized_tranche(label: str, price: Any, shares: Any, amount: Any, note: Any = "") -> dict[str, Any]:
    price_number = _number(price)
    shares_number = _number(shares)
    amount_number = _number(amount)
    if amount_number is None and price_number is not None and shares_number is not None:
        amount_number = price_number * shares_number
    if shares_number is None and price_number is not None and price_number > 0 and amount_number is not None:
        shares_number = amount_number / price_number
    return {
        "label": label,
        "price": price_number,
        "shares": shares_number,
        "amount": amount_number,
        "note": str(note or "").strip(),
    }


def _saved_tranches(tranches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "label": str(tranche.get("label") or ""),
            "price": _number(tranche.get("price")),
            "shares": _number(tranche.get("shares")),
            "amount": _number(tranche.get("amount")),
            "note": str(tranche.get("note") or "").strip(),
        }
        for tranche in tranches
    ]


def _tranche_summary(tranche: dict[str, Any]) -> str:
    price = _money(tranche.get("price"))
    shares = _shares_text(tranche.get("shares"))
    amount = _money(tranche.get("amount"))
    return f"{price} / {shares} / 约 {amount}"


def _has_plan_value(plan: dict, field: str) -> bool:
    value = plan.get(field)
    return value is not None and value != ""


def _value_with_source(value: Any, is_manual: bool, is_percent: bool) -> str:
    source = "手动覆盖" if is_manual else "系统建议"
    return f"{_display_value(value, is_percent)} · {source}"


def _is_percent_field(field: str) -> bool:
    return field in {"target_position_pct", "planned_position_pct"}


def _display_value(value: Any, is_percent: bool) -> str:
    if is_percent:
        return _percent(value)
    return _money(value)


def _money(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    return format_currency(number)


def _percent(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    return format_percent(number)


def _range_money(low: Any, high: Any) -> str:
    low_text = _money(low)
    high_text = _money(high)
    if low_text == "N/A" and high_text == "N/A":
        return "N/A"
    return f"{low_text} - {high_text}"


def _text_hint(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未生成"
    return text if len(text) <= 70 else text[:69] + "…"


def _input_text(value: Any) -> str:
    if value is None:
        return ""
    number = _number(value)
    if number is not None:
        return f"{number:g}"
    return str(value)


def _override_input_value(plan: dict, field: str, input_value: Any, system_value: Any) -> Any:
    if input_value is None or str(input_value).strip() == "":
        return None
    if _has_plan_value(plan, field):
        return input_value
    input_number = _number(input_value)
    system_number = _number(system_value)
    if input_number is not None and system_number is not None and abs(input_number - system_number) < 1e-9:
        return None
    return input_value


def _amount_from_price_and_shares(price: Any, shares: Any) -> float | None:
    price_number = _number(price)
    shares_number = _number(shares)
    if price_number is None or shares_number is None:
        return None
    return price_number * shares_number


def _after_buy_metrics(
    current_shares: float,
    cumulative_shares: float,
    cumulative_amount: float,
    portfolio_context: dict[str, Any] | None,
) -> dict[str, str | float]:
    total_shares = current_shares + cumulative_shares
    current_market_value = _number((portfolio_context or {}).get("currentMarketValue")) or 0.0
    total_market_value = current_market_value + cumulative_amount
    total_portfolio_value = _number((portfolio_context or {}).get("totalPortfolioValue"))
    cash_balance = _number((portfolio_context or {}).get("cashBalance"))
    return {
        "totalShares": total_shares,
        "marketValueText": _money(total_market_value) if total_market_value > 0 else "N/A",
        "weightText": _percent(total_market_value / total_portfolio_value * 100) if total_portfolio_value and total_portfolio_value > 0 else "N/A",
        "cashText": f"现金余量 {_money(cash_balance - cumulative_amount)}" if cash_balance is not None else "现金 N/A",
    }


def _metric_cell_html(primary: str, secondary: str) -> str:
    return f'<div class="plan-tranche-metric"><b>{escape(primary)}</b><span>{escape(secondary)}</span></div>'


def _tranche_auto_hint(price: Any, shares: Any, amount: Any, row: dict[str, Any]) -> str:
    if _number(shares) is not None and _number(amount) is None and _number(row.get("amount")) is not None:
        return f"自动估算金额：{_money(row.get('amount'))}"
    if _number(amount) is not None and _number(shares) is None and _number(row.get("shares")) is not None:
        return f"自动估算股数：{_shares_text(row.get('shares'))}"
    if _number(price) is None:
        return "先填买入价，再估算股数或金额。"
    return ""


def _shares_text(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    return f"{number:g} 股"


def _plan_sentence(tranches: list[dict[str, Any]], no_chase: Any) -> str:
    parts = []
    for tranche in tranches:
        price = _money(tranche.get("price"))
        shares = _shares_text(tranche.get("shares"))
        amount = _money(tranche.get("amount"))
        parts.append(f"{price} 买 {shares}，约 {amount}")
    return f"计划：{'；'.join(parts)}。超过 {_money(no_chase)} 不追。"


def _combined_entry(active_zone: Any) -> dict[str, Any]:
    combined = _attr(active_zone, "combinedEntry")
    return combined if isinstance(combined, dict) else {}


def _technical_entry(active_zone: Any) -> dict[str, Any]:
    technical = _attr(active_zone, "technicalEntry")
    return technical if isinstance(technical, dict) else {}


def _attr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _first_value(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _render_plan_editor_styles() -> None:
    st.markdown(
        """
        <style>
        .plan-reference-card,
        .plan-preview-card {
            padding: 0.74rem 0.82rem;
            margin-bottom: 0.65rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            border-radius: 0.5rem;
            background: #ffffff;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.025);
        }
        .plan-editor-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.48rem;
        }
        .plan-editor-grid div {
            min-height: 3rem;
            padding: 0.48rem 0.55rem;
            border-radius: 0.5rem;
            background: rgba(248, 250, 252, 0.82);
            border: 1px solid rgba(15, 23, 42, 0.06);
        }
        .plan-editor-grid span {
            display: block;
            color: #64748b;
            font-size: 0.72rem;
            font-weight: 650;
            line-height: 1.2;
        }
        .plan-editor-grid strong {
            display: block;
            margin-top: 0.24rem;
            color: #0f172a;
            font-size: 0.84rem;
            line-height: 1.25;
            font-weight: 720;
            overflow-wrap: anywhere;
        }
        .plan-editor-section-title {
            margin: 0.3rem 0 0.46rem;
            color: #0f172a;
            font-size: 0.78rem;
            font-weight: 760;
            letter-spacing: 0;
        }
        .plan-tranche-head {
            display: grid;
            grid-template-columns: 1.04fr 1.02fr 1.02fr 1.08fr 1.12fr 1.12fr;
            gap: 0.38rem;
            margin-bottom: 0.24rem;
            color: #64748b;
            font-size: 0.7rem;
            font-weight: 720;
        }
        .plan-tranche-label {
            min-height: 2.45rem;
            padding: 0.32rem 0.44rem;
            border-radius: 0.45rem;
            border: 1px solid rgba(15, 23, 42, 0.06);
            background: rgba(248, 250, 252, 0.82);
        }
        .plan-tranche-label b,
        .plan-tranche-metric b {
            display: block;
            color: #0f172a;
            font-size: 0.78rem;
            line-height: 1.2;
            font-weight: 760;
        }
        .plan-tranche-label span,
        .plan-tranche-metric span {
            display: block;
            margin-top: 0.15rem;
            color: #64748b;
            font-size: 0.69rem;
            line-height: 1.2;
            font-weight: 620;
        }
        .plan-tranche-metric {
            min-height: 2.45rem;
            padding: 0.35rem 0.45rem;
            border-radius: 0.45rem;
            border: 1px solid rgba(15, 23, 42, 0.06);
            background: rgba(248, 250, 252, 0.74);
        }
        .plan-editor-save-preview {
            margin: 0.45rem 0 0.55rem;
            padding: 0.5rem 0.62rem;
            border-radius: 0.48rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            background: rgba(248, 250, 252, 0.82);
            color: #334155;
            font-size: 0.78rem;
            font-weight: 650;
            line-height: 1.35;
        }
        @media (max-width: 900px) {
            .plan-editor-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
