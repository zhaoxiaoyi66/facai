from __future__ import annotations

from typing import Any


PRICE_POSITIONS = {
    "IN_BUY_ZONE": "买区内",
    "ABOVE_BUY_ZONE": "高于买区",
    "IN_CHASE_ZONE": "追高区",
    "BELOW_BUY_ZONE": "低于买区，需复核",
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
    result: dict[str, Any] = {
        "entry_reference_low": reference_low,
        "entry_reference_high": reference_high,
        "next_action_price": _next_action_price(price_position, buy_zone, current_price),
        "chase_above_price": chase_above,
        "current_vs_entry_pct": distance_pct,
        "missing_entry_fields": missing_fields,
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
            }
        )
        return result

    zone_text = format_buy_zone(buy_zone)
    if price_position == "IN_CHASE_ZONE":
        result.update(
            {
                "entry_display_label": f"禁止追高，参考买区 {zone_text}",
                "entry_display_reason": _distance_reason(distance_pct, "高于买区", chase_above),
                "entry_action_hint": "进入追高区，禁止新增",
            }
        )
        return result
    if price_position == "ABOVE_BUY_ZONE":
        result.update(
            {
                "entry_display_label": f"等待回落 {zone_text}",
                "entry_display_reason": _distance_reason(distance_pct, "高于买区", chase_above),
                "entry_action_hint": "只观察，等待回到纪律买区",
            }
        )
        return result
    if price_position == "IN_BUY_ZONE":
        result.update(
            {
                "entry_display_label": f"买区内 {zone_text}",
                "entry_display_reason": "当前位于纪律买区",
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
        result.update(
            {
                "entry_display_label": f"低于买区 {zone_text}",
                "entry_display_reason": "低于买区不等于自动更便宜，需确认基本面恶化、财报冲击、趋势破位",
                "entry_action_hint": "低于买区，不自动买入",
            }
        )
        return result

    result.update(
        {
            "entry_display_label": f"参考买区 {zone_text}",
            "entry_display_reason": "可参考纪律买区，但当前价格状态无法精确归类",
            "entry_action_hint": "只观察，等待数据复核",
        }
    )
    return result


def format_buy_zone(buy_zone: Any) -> str:
    lower = _zone_number(buy_zone, "lower")
    upper = _zone_number(buy_zone, "upper")
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
        return float(value)
    except (TypeError, ValueError):
        return None
