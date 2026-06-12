from __future__ import annotations

import math
from typing import Any


PRICE_POSITIONS = {
    "IN_BUY_ZONE": "买区内",
    "ABOVE_BUY_ZONE": "高于买区",
    "IN_CHASE_ZONE": "追高区",
    "BELOW_BUY_ZONE": "低于估值参考，待复核",
    "BELOW_VALUATION_REFERENCE": "低于估值参考，待复核",
    "BELOW_TECHNICAL_PULLBACK_ZONE": "跌破结构区，先复核",
    "ZONE_MISSING": "无法判断",
}


def build_entry_display(report_or_summary: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    source = dict(report_or_summary or {})
    source.update({key: value for key, value in overrides.items() if value is not None})
    current_price = _number(_value(source, "current_price", "currentPrice"))
    buy_zone = _value(source, "buy_zone", "buyZone") or {}
    chase_zone = _value(source, "chase_zone", "chaseZone") or {}
    data_status = str(_value(source, "data_status", "dataStatus") or "OK").strip()
    price_position = str(_value(source, "price_position", "zone_status", "zoneStatus") or "ZONE_MISSING").strip()
    decision = str(_value(source, "decision", "radar_decision", "radarDecision") or "").strip()
    final_score = _number(_value(source, "final_score", "finalScore"))
    valuation_score = _number(_value(source, "valuation_score", "valuationScore"))
    risk_score = _number(_value(source, "risk_score", "riskScore"))
    distance_pct = current_vs_entry_pct(current_price, buy_zone, price_position)
    explicit_missing_fields = _list_value(_value(source, "missing_entry_fields", "missingEntryFields"))
    missing_fields = explicit_missing_fields or explain_missing_entry_fields(
        data_status=data_status,
        buy_zone=buy_zone,
        valuation_score=valuation_score,
    )
    reference_low = _zone_number(buy_zone, "lower")
    reference_high = _zone_number(buy_zone, "upper")
    chase_above = _zone_number(chase_zone, "lower")
    technical_zone = _value(source, "technical_entry_zone", "technicalEntryZone") or {}
    technical_low = (
        _zone_number(technical_zone, "low")
        or _zone_number(technical_zone, "lower")
        or _number(_value(source, "technical_entry_zone_low", "technicalEntryZoneLow"))
    )
    technical_high = (
        _zone_number(technical_zone, "high")
        or _zone_number(technical_zone, "upper")
        or _number(_value(source, "technical_entry_zone_high", "technicalEntryZoneHigh"))
    )
    technical_chase_overlap = _technical_chase_overlap(technical_high, chase_above)
    effective_technical_high = _effective_technical_high(technical_high, chase_above) if technical_chase_overlap else technical_high
    technical_reason = str(
        _value(source, "technical_entry_reason", "technicalEntryReason")
        or _value(technical_zone, "reason")
        or ""
    ).strip()
    technical_source = str(
        _value(source, "technical_entry_source", "technicalEntrySource")
        or _value(technical_zone, "source")
        or ""
    ).strip()
    technical_missing_fields = _list_value(_value(source, "technical_entry_missing_fields", "technicalEntryMissingFields"))
    if not technical_missing_fields:
        technical_missing_fields = _list_value(_value(technical_zone, "missing_fields", "missingFields"))
    technical_missing_reason = str(
        _value(source, "technical_entry_missing_reason", "technicalEntryMissingReason")
        or _value(technical_zone, "missing_reason", "missingReason")
        or ""
    ).strip()
    technical_confidence = str(
        _value(source, "technical_entry_confidence", "technicalEntryConfidence")
        or _value(technical_zone, "confidence")
        or ""
    ).strip()
    technical_structure_status = str(
        _value(source, "technical_structure_status", "technicalStructureStatus")
        or _value(technical_zone, "technical_structure_status", "technicalStructureStatus")
        or ""
    ).strip()
    technical_structure_label = str(
        _value(source, "technical_structure_label", "technicalStructureLabel")
        or _value(technical_zone, "technical_structure_label", "technicalStructureLabel")
        or ""
    ).strip()
    technical_pullback_low = _number(
        _value(source, "technical_pullback_zone_low", "technicalPullbackZoneLow")
        or _value(technical_zone, "technical_pullback_zone_low", "technicalPullbackZoneLow")
    )
    technical_pullback_high = _number(
        _value(source, "technical_pullback_zone_high", "technicalPullbackZoneHigh")
        or _value(technical_zone, "technical_pullback_zone_high", "technicalPullbackZoneHigh")
    )
    technical_repair_low = _number(
        _value(source, "technical_repair_zone_low", "technicalRepairZoneLow")
        or _value(technical_zone, "technical_repair_zone_low", "technicalRepairZoneLow")
    )
    technical_repair_high = _number(
        _value(source, "technical_repair_zone_high", "technicalRepairZoneHigh")
        or _value(technical_zone, "technical_repair_zone_high", "technicalRepairZoneHigh")
    )
    support_watch_low = _number(
        _value(source, "support_watch_zone_low", "supportWatchZoneLow")
        or _value(technical_zone, "support_watch_zone_low", "supportWatchZoneLow")
    )
    support_watch_high = _number(
        _value(source, "support_watch_zone_high", "supportWatchZoneHigh")
        or _value(technical_zone, "support_watch_zone_high", "supportWatchZoneHigh")
    )
    confirmation_price = _number(
        _value(source, "confirmation_price", "confirmationPrice")
        or _value(technical_zone, "confirmation_price", "confirmationPrice")
    )
    invalidation_price = _number(
        _value(source, "invalidation_price", "invalidationPrice")
        or _value(technical_zone, "invalidation_price", "invalidationPrice")
    )
    technical_structure_reason = str(
        _value(source, "technical_structure_reason", "technicalStructureReason")
        or _value(technical_zone, "technical_structure_reason", "technicalStructureReason")
        or technical_reason
        or ""
    ).strip()
    technical_structure_missing_fields = _list_value(_value(source, "technical_missing_fields", "technicalMissingFields"))
    if not technical_structure_missing_fields:
        technical_structure_missing_fields = _list_value(
            _value(technical_zone, "technical_missing_fields", "technicalMissingFields")
        )
    next_technical_steps = _list_value(_value(source, "next_technical_steps", "nextTechnicalSteps"))
    if not next_technical_steps:
        next_technical_steps = _list_value(_value(technical_zone, "next_technical_steps", "nextTechnicalSteps"))
    technical_position = _technical_position(current_price, technical_low, technical_high)
    technical_zone_text = _zone_text(technical_low, technical_high)
    effective_technical_zone_text = _zone_text(technical_low, effective_technical_high)
    if (technical_low is None or technical_high is None) and not technical_missing_reason and technical_reason:
        technical_missing_reason = technical_reason
    result: dict[str, Any] = {
        "entry_reference_low": reference_low,
        "entry_reference_high": reference_high,
        "next_action_price": _next_action_price(price_position, buy_zone, current_price),
        "chase_above_price": chase_above,
        "current_vs_entry_pct": distance_pct,
        "missing_entry_fields": missing_fields,
        "technical_entry_zone_low": technical_low,
        "technical_entry_zone_high": technical_high,
        "effective_technical_entry_zone_low": technical_low,
        "effective_technical_entry_zone_high": effective_technical_high,
        "technical_chase_overlap": technical_chase_overlap,
        "technical_entry_source": technical_source,
        "technical_entry_reason": technical_reason,
        "technical_entry_missing_fields": technical_missing_fields,
        "technical_entry_missing_reason": technical_missing_reason,
        "technical_entry_confidence": technical_confidence,
        "technical_structure_status": technical_structure_status,
        "technical_structure_label": technical_structure_label,
        "technical_pullback_zone_low": technical_pullback_low,
        "technical_pullback_zone_high": technical_pullback_high,
        "technical_repair_zone_low": technical_repair_low,
        "technical_repair_zone_high": technical_repair_high,
        "support_watch_zone_low": support_watch_low,
        "support_watch_zone_high": support_watch_high,
        "confirmation_price": confirmation_price,
        "invalidation_price": invalidation_price,
        "technical_structure_reason": technical_structure_reason,
        "technical_missing_fields": technical_structure_missing_fields,
        "next_technical_steps": next_technical_steps,
        "technical_position": technical_position,
        "entry_context_status": price_position,
        "valuation_deep_zone_label": format_buy_zone(buy_zone),
        "entry_display_label": "",
        "entry_display_reason": "",
        "entry_action_hint": "",
    }

    if missing_fields:
        reason = _missing_reason_text(missing_fields)
        result.update(
            {
                "entry_display_label": f"暂无参考买区：{reason}",
                "entry_display_reason": reason,
                "entry_action_hint": "补齐数据后再复核",
                "entry_context_status": "ZONE_MISSING",
            }
        )
        return result

    zone_text = format_buy_zone(buy_zone)
    use_technical_pullback = (
        technical_low is not None
        and technical_high is not None
        and price_position in {"ABOVE_BUY_ZONE", "IN_CHASE_ZONE"}
    )
    if price_position == "IN_CHASE_ZONE":
        label_zone = effective_technical_zone_text if use_technical_pullback else zone_text
        reason = _distance_reason(distance_pct, "高于买区", chase_above)
        if use_technical_pullback:
            reason = _technical_pullback_reason(
                effective_technical_zone_text,
                zone_text,
                technical_reason,
                fallback=reason,
                overlap=technical_chase_overlap,
                raw_technical_zone_text=technical_zone_text,
            )
        result.update(
            {
                "entry_display_label": f"禁止追高，技术回踩参考 {label_zone}",
                "entry_display_reason": reason,
                "entry_action_hint": "进入追高区，禁止新增",
                "entry_context_status": "IN_CHASE_ZONE",
            }
        )
        return result
    if price_position == "ABOVE_BUY_ZONE":
        label = f"等待回落 {zone_text}"
        reason = _distance_reason(distance_pct, "高于买区", chase_above)
        hint = "只观察，等待回到纪律买区"
        context_status = price_position
        if use_technical_pullback:
            reason = _technical_pullback_reason(
                effective_technical_zone_text,
                zone_text,
                technical_reason,
                fallback=reason,
                overlap=technical_chase_overlap,
                raw_technical_zone_text=technical_zone_text,
            )
            if technical_position == "IN_TECHNICAL_PULLBACK_ZONE":
                label = f"回踩区内 {effective_technical_zone_text}"
                reason = "当前价已进入技术回踩区上沿；" + reason
                hint = "需复核，不自动买入"
                context_status = "IN_TECHNICAL_PULLBACK_ZONE"
            elif technical_position == "BELOW_TECHNICAL_PULLBACK_ZONE":
                label = f"跌破结构区 {effective_technical_zone_text}"
                reason = "当前价跌破技术结构参考；" + reason
                hint = "跌破结构区，先复核"
                context_status = "BELOW_TECHNICAL_PULLBACK_ZONE"
            else:
                label = f"等待技术回踩 {effective_technical_zone_text}"
                hint = "只观察，等待技术回踩或基本面复核"
                context_status = "ABOVE_TECHNICAL_PULLBACK_ZONE"
        result.update(
            {
                "entry_display_label": label,
                "entry_display_reason": reason,
                "entry_action_hint": hint,
                "entry_context_status": context_status,
            }
        )
        return result
    if price_position == "IN_BUY_ZONE":
        result.update(
            {
                "entry_display_label": f"买区内 {zone_text}",
                "entry_display_reason": "当前位于纪律买区",
                "entry_context_status": "IN_DISCIPLINE_BUY_ZONE",
                "entry_action_hint": format_entry_action_hint(
                    decision=decision,
                    final_score=final_score,
                    valuation_score=valuation_score,
                    risk_score=risk_score,
                ),
            }
        )
        return result
    if price_position == "BELOW_BUY_ZONE":
        if technical_low is not None and technical_high is not None:
            if technical_position == "BELOW_TECHNICAL_PULLBACK_ZONE":
                result.update(
                    {
                        "entry_display_label": f"跌破结构区 {effective_technical_zone_text}",
                        "entry_display_reason": _technical_breakdown_reason(
                            effective_technical_zone_text,
                            zone_text,
                            technical_reason,
                            fallback=_below_valuation_reference_reason(distance_pct, technical_missing_reason),
                        ),
                        "entry_action_hint": "跌破结构区，先复核",
                        "entry_context_status": "BELOW_TECHNICAL_PULLBACK_ZONE",
                    }
                )
                return result
            if technical_position == "IN_TECHNICAL_PULLBACK_ZONE":
                result.update(
                    {
                        "entry_display_label": f"回踩区内 {effective_technical_zone_text}",
                        "entry_display_reason": _technical_pullback_reason(
                            effective_technical_zone_text,
                            zone_text,
                            technical_reason,
                            fallback=_below_valuation_reference_reason(distance_pct, technical_missing_reason),
                            overlap=technical_chase_overlap,
                            raw_technical_zone_text=technical_zone_text,
                        ),
                        "entry_action_hint": "需复核，不自动买入",
                        "entry_context_status": "IN_TECHNICAL_PULLBACK_ZONE",
                    }
                )
                return result
        result.update(
            {
                "entry_display_label": f"低于估值参考 {zone_text}",
                "entry_display_reason": _below_valuation_reference_reason(distance_pct, technical_missing_reason),
                "entry_action_hint": "待复核，等结构确认",
                "entry_context_status": "BELOW_VALUATION_REFERENCE",
            }
        )
        return result

    result.update(
        {
            "entry_display_label": f"参考买区 {zone_text}",
            "entry_display_reason": "可参考纪律买区，但当前价格状态无法精确归类",
            "entry_action_hint": "只观察，等待数据复核",
            "entry_context_status": price_position,
        }
    )
    return result


def format_buy_zone(buy_zone: Any) -> str:
    lower = _zone_number(buy_zone, "lower")
    upper = _zone_number(buy_zone, "upper")
    return _zone_text(lower, upper)


def _zone_text(lower: float | None, upper: float | None) -> str:
    if lower is not None and upper is not None:
        return f"{_price_text(lower)} - {_price_text(upper)}"
    if upper is not None:
        return f"<= {_price_text(upper)}"
    if lower is not None:
        return f">= {_price_text(lower)}"
    return "N/A"


def format_zone_status(zone_status: Any) -> str:
    text = str(zone_status or "").strip()
    return PRICE_POSITIONS.get(text, text or "N/A")


def format_entry_action_hint(
    *,
    decision: str,
    final_score: float | None,
    valuation_score: float | None,
    risk_score: float | None,
) -> str:
    if decision == "ALLOW_BUY":
        return "价格在买区，仍需按交易计划执行"
    if _value_or_zero(final_score) < 70:
        return "买区内但总分低于 70，需复核"
    if _value_or_zero(valuation_score) < 40:
        return "买区内但估值评分低于 40，需复核"
    if _value_or_zero(risk_score) < 60:
        return "买区内但风险评分不足，需复核"
    return "买区内但门禁未放行，需复核"


def explain_missing_entry_fields(
    *,
    data_status: str,
    buy_zone: Any,
    valuation_score: float | None,
) -> list[str]:
    status = str(data_status or "").strip().upper()
    if status == "STALE":
        return ["数据 stale"]
    if status == "MISSING_PRICE":
        return ["缺当前价格"]
    if status == "MISSING_VALUATION":
        return ["缺估值指标"]
    if status == "MISSING_SCORE":
        return ["缺评分输入"]
    if status == "MISSING_BUY_ZONE" or _zone_number(buy_zone, "upper") is None:
        return ["缺 52 周高低", "无法生成纪律买区"]
    return []


def current_vs_entry_pct(current_price: Any, buy_zone: Any, price_position: str) -> float | None:
    price = _number(current_price)
    if price is None:
        return None
    lower = _zone_number(buy_zone, "lower")
    upper = _zone_number(buy_zone, "upper")
    if price_position == "BELOW_BUY_ZONE" and lower:
        return round(((price - lower) / lower) * 100, 1)
    if price_position in {"ABOVE_BUY_ZONE", "IN_CHASE_ZONE"} and upper:
        return round(((price - upper) / upper) * 100, 1)
    if price_position == "IN_BUY_ZONE":
        return 0.0
    return None


def _next_action_price(price_position: str, buy_zone: Any, current_price: float | None) -> float | None:
    if price_position == "BELOW_BUY_ZONE":
        return _zone_number(buy_zone, "lower") or _zone_number(buy_zone, "upper")
    if price_position == "IN_BUY_ZONE":
        return _number(current_price) or _zone_number(buy_zone, "upper")
    return _zone_number(buy_zone, "upper")


def _distance_reason(distance_pct: float | None, prefix: str, chase_above_price: float | None) -> str:
    parts: list[str] = []
    if distance_pct is not None:
        parts.append(f"当前{prefix} {abs(distance_pct):g}%")
    if chase_above_price is not None:
        parts.append(f"追高禁区 >{_price_text(chase_above_price)}")
    return "；".join(parts) if parts else "等待价格回到纪律买区"


def _technical_pullback_reason(
    technical_zone_text: str,
    deep_value_zone_text: str,
    technical_reason: str,
    *,
    fallback: str,
    overlap: bool = False,
    raw_technical_zone_text: str = "",
) -> str:
    parts = [
        f"技术回踩区 {technical_zone_text}",
        f"深度估值区 {deep_value_zone_text}",
    ]
    if overlap:
        parts.append(f"原技术回踩区 {raw_technical_zone_text} 与追高禁区重叠，超过追高线部分不作为新增参考")
    if technical_reason:
        parts.append(technical_reason)
    if fallback:
        parts.append(fallback)
    return "；".join(parts)


def _technical_breakdown_reason(
    technical_zone_text: str,
    deep_value_zone_text: str,
    technical_reason: str,
    *,
    fallback: str,
) -> str:
    parts = [
        f"当前价跌破技术结构参考 {technical_zone_text}",
        f"估值参考区 {deep_value_zone_text}",
        "只有跌破技术支撑 / EMA / swing low 或基本面恶化时，才按跌破结构处理",
    ]
    if technical_reason:
        parts.append(technical_reason)
    if fallback:
        parts.append(fallback)
    return "；".join(parts)


def _below_valuation_reference_reason(distance_pct: float | None, technical_missing_reason: str = "") -> str:
    parts: list[str] = []
    if distance_pct is not None:
        parts.append(f"当前低于估值参考 {abs(distance_pct):g}%")
    parts.append("低于估值参考不等于结构破坏，需要等待 EMA / 相对强弱 / 收盘确认")
    if technical_missing_reason:
        parts.append(technical_missing_reason)
    else:
        parts.append("技术回踩区未确认")
    return "；".join(parts)


def _is_deep_value_zone_far_from_price(current_price: float | None, buy_zone_upper: float | None) -> bool:
    price = _number(current_price)
    upper = _number(buy_zone_upper)
    if price is None or upper is None or price <= 0:
        return False
    return upper <= price * 0.75


def _technical_position(current_price: float | None, technical_low: float | None, technical_high: float | None) -> str:
    price = _number(current_price)
    low = _number(technical_low)
    high = _number(technical_high)
    if price is None or low is None or high is None:
        return ""
    if price < low:
        return "BELOW_TECHNICAL_PULLBACK_ZONE"
    if price <= high:
        return "IN_TECHNICAL_PULLBACK_ZONE"
    return "ABOVE_TECHNICAL_PULLBACK_ZONE"


def _technical_chase_overlap(technical_high: float | None, chase_above: float | None) -> bool:
    high = _number(technical_high)
    chase = _number(chase_above)
    return bool(high is not None and chase is not None and high > chase)


def _effective_technical_high(technical_high: float | None, chase_above: float | None) -> float | None:
    high = _number(technical_high)
    chase = _number(chase_above)
    if high is None:
        return chase
    if chase is None:
        return high
    return min(high, chase)


def _missing_reason_text(fields: list[str]) -> str:
    if not fields:
        return "无法生成纪律买区"
    if "数据 stale" in fields:
        return "数据 stale"
    if "缺当前价格" in fields:
        return "缺当前价格"
    if "缺估值指标" in fields:
        return "缺估值指标"
    if "缺评分输入" in fields:
        return "缺评分输入"
    if "缺 52 周高低" in fields:
        return "缺 52 周高低或无法生成纪律买区"
    return "、".join(fields)


def _zone_number(zone: Any, key: str) -> float | None:
    if isinstance(zone, dict):
        return _number(zone.get(key))
    return _number(getattr(zone, key, None))


def _price_text(value: float | None) -> str:
    number = _number(value)
    return "N/A" if number is None else f"${number:,.2f}"


def _value_or_zero(value: float | None) -> float:
    return 0.0 if value is None else value


def _value(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source:
            return source.get(key)
    return None


def _list_value(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
