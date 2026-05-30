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
            _money(_first_value(combined.get("combinedTriggerPrice"), _attr(suggestion, "firstBuyPrice"))),
        ),
        (
            "深度折价区",
            _money(
                _first_value(
                    combined.get("deepDiscountPrice"),
                    _attr(active_zone, "heavyBuyBelow"),
                    _attr(suggestion, "thirdBuyPrice"),
                )
            ),
        ),
    ]
    st.markdown(_compact_grid_html(rows, "plan-reference-card"), unsafe_allow_html=True)


def render_plan_preview_card(plan: dict, suggestion: Any, active_zone: Any, final_decision: Any = None) -> None:
    _render_plan_editor_styles()
    system_values = _system_values(suggestion, active_zone, final_decision)
    rows = []
    for field, label in (*BUY_FIELDS, *LIMIT_FIELDS):
        value = _plan_or_system(plan, field, system_values[field])
        rows.append((label, _value_with_source(value, _has_plan_value(plan, field), _is_percent_field(field))))
    rows.append(
        (
            "禁止追高价",
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
) -> bool:
    _render_plan_editor_styles()
    system_values = _system_values(suggestion, active_zone, final_decision)
    defaults = _current_state_values(plan, system_values)
    _ensure_editor_state(key_prefix, defaults)

    action_cols = st.columns([1.2, 3.8])
    if action_cols[0].button("使用系统建议填充", key=f"{key_prefix}:fill-system", width="stretch"):
        _set_editor_state(key_prefix, _state_values(system_values))
        st.toast(f"{ticker} 已填入系统建议，可继续手动调整。")
    action_cols[1].caption("系统建议只读；保存后会作为你的手动计划，不影响系统建议。")

    with st.form(f"{key_prefix}:form"):
        st.markdown('<div class="plan-editor-section-title">A. 我的买入计划</div>', unsafe_allow_html=True)
        buy_cols = st.columns(3)
        first_buy_price = _text_field(
            buy_cols[0],
            key_prefix,
            "first_buy_price",
            "第一笔买入价",
            system_values["first_buy_price"],
        )
        second_buy_price = _text_field(
            buy_cols[1],
            key_prefix,
            "second_buy_price",
            "第二笔买入价",
            system_values["second_buy_price"],
        )
        third_buy_price = _text_field(
            buy_cols[2],
            key_prefix,
            "third_buy_price",
            "深度折价买入价",
            system_values["third_buy_price"],
        )

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
            "禁止追高价",
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
            f'<div class="plan-editor-save-preview">{escape(_plan_sentence(first_buy_price, second_buy_price, third_buy_price, no_chase_above))}</div>',
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


def _system_values(suggestion: Any, active_zone: Any, final_decision: Any = None) -> dict[str, Any]:
    return {
        "target_position_pct": _first_value(_attr(final_decision, "maxPortfolioWeightPercent"), _attr(suggestion, "maxPortfolioWeightPercent")),
        "planned_position_pct": _first_value(_attr(final_decision, "currentAddLimitPercent"), _attr(suggestion, "currentAddLimitPercent")),
        "first_buy_price": _attr(suggestion, "firstBuyPrice"),
        "second_buy_price": _attr(suggestion, "secondBuyPrice"),
        "third_buy_price": _attr(suggestion, "thirdBuyPrice"),
        "no_chase_above": _attr(active_zone, "noChaseAbove"),
        "fair_value_low": _attr(active_zone, "fairValueLow"),
        "fair_value_high": _attr(active_zone, "fairValueHigh"),
        "tranche_buy_low": _attr(active_zone, "trancheBuyLow"),
        "tranche_buy_high": _attr(active_zone, "trancheBuyHigh"),
        "heavy_buy_below": _attr(active_zone, "heavyBuyBelow"),
        "stop_adding_condition": _attr(suggestion, "stopAddingCondition") or "",
        "invalidation_condition": _attr(suggestion, "thesisBreakCondition") or "",
        "earnings_review_points": _attr(suggestion, "earningsReviewCondition") or "",
    }


def _current_state_values(plan: dict, system_values: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field, system_value in system_values.items():
        values[field] = _input_text(_plan_or_system(plan, field, system_value))
    return values


def _state_values(system_values: dict[str, Any]) -> dict[str, str]:
    return {field: _input_text(value) for field, value in system_values.items()}


def _ensure_editor_state(key_prefix: str, values: dict[str, str]) -> None:
    for field, value in values.items():
        st.session_state.setdefault(_field_key(key_prefix, field), value)


def _set_editor_state(key_prefix: str, values: dict[str, str]) -> None:
    for field, value in values.items():
        st.session_state[_field_key(key_prefix, field)] = value


def _field_key(key_prefix: str, field: str) -> str:
    return f"{key_prefix}:{field}"


def _plan_or_system(plan: dict, field: str, system_value: Any) -> Any:
    return plan.get(field) if _has_plan_value(plan, field) else system_value


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


def _plan_sentence(first_buy: Any, second_buy: Any, third_buy: Any, no_chase: Any) -> str:
    return (
        f"计划：{_money(first_buy)} 买第一笔，{_money(second_buy)} 买第二笔，"
        f"{_money(third_buy)} 深度折价买入；超过 {_money(no_chase)} 不追。"
    )


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
