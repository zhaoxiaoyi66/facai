from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


WAIT_PULLBACK = "WAIT_PULLBACK"
WAIT_CONFIRMATION = "WAIT_CONFIRMATION"
ALLOW_SMALL_BUY = "ALLOW_SMALL_BUY"
ALLOW_ADD_ON_PULLBACK = "ALLOW_ADD_ON_PULLBACK"
BLOCK_CHASE = "BLOCK_CHASE"
RISK_REVIEW = "RISK_REVIEW"
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
AVOID = "AVOID"

ACTION_TEXT = {
    WAIT_PULLBACK: "等待回踩",
    WAIT_CONFIRMATION: "等待确认",
    ALLOW_SMALL_BUY: "允许小仓观察",
    ALLOW_ADD_ON_PULLBACK: "允许回踩复核加仓",
    BLOCK_CHASE: "禁止追高",
    RISK_REVIEW: "进入风控复核",
    DATA_INSUFFICIENT: "技术承接数据不足",
    AVOID: "暂不参与",
}

ZONE_TEXT = {
    "DEEP_ACCEPTANCE": "深度承接区",
    "PULLBACK_BUY": "回踩买区",
    "REPAIR_WATCH": "修复观察区",
    "CONFIRMATION_REVIEW": "确认复核区",
    "CHASE_RISK": "追高禁区",
    "INVALIDATION": "失效风控区",
    "DATA_INSUFFICIENT": "技术承接数据不足",
}


@dataclass(frozen=True)
class BuyZoneContext:
    primary_zone: str
    primary_zone_text: str
    setup_score: float
    technical_structure_score: float
    volume_acceptance_score: float
    risk_reward_score: float
    support_zone_low: float | None
    support_zone_high: float | None
    pullback_zone_low: float | None
    pullback_zone_high: float | None
    confirmation_price: float | None
    invalidation_price: float | None
    chase_price: float | None
    current_action: str
    action_text: str
    existing_position_action_text: str
    no_position_action_text: str
    zone_selection_reason: str
    missing_fields: list[str] = field(default_factory=list)
    core_position_allowed: bool = True
    core_position_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_buy_zone_context(
    source: dict[str, Any] | None = None,
    *,
    technicals: dict[str, Any] | None = None,
    volume_snapshot: dict[str, Any] | None = None,
) -> BuyZoneContext:
    data = {**(source or {}), **(technicals or {})}
    volume = dict(volume_snapshot or {})
    price = _first_number(data, "current_price", "currentPrice", "price", "close")
    support_low = _first_number(data, "deep_support_zone_low", "support_watch_zone_low", "recent_swing_low")
    support_high = _first_number(data, "deep_support_zone_high", "support_watch_zone_high", "recent_swing_low")
    pullback_low = _first_number(
        data,
        "effective_technical_entry_zone_low",
        "technical_pullback_zone_low",
        "technical_entry_zone_low",
        "near_term_repair_zone_low",
        "ema50",
        "ema20",
    )
    pullback_high = _first_number(
        data,
        "effective_technical_entry_zone_high",
        "technical_pullback_zone_high",
        "technical_entry_zone_high",
        "near_term_repair_zone_high",
        "ema20",
        "ema50",
    )
    repair_low = _first_number(data, "near_term_repair_zone_low", "technical_repair_zone_low")
    repair_high = _first_number(data, "near_term_repair_zone_high", "technical_repair_zone_high")
    confirmation = _first_number(data, "confirmation_price", "radar_confirmation_price", "confirm_line")
    invalidation = _first_number(data, "invalidation_price", "radar_invalidation_price", "invalid_line")
    chase = _first_number(data, "chase_above_price", "radar_chase_above_price", "chase_price")
    ma20 = _first_number(data, "ma20", "ema20")
    ma50 = _first_number(data, "ma50", "ema50")
    ma200 = _first_number(data, "ma200", "ema200")
    atr = _first_number(data, "atr_14", "atr14")
    resistance = _first_number(data, "resistance_zone_high", "resistance_zone_low", "recent_swing_high", "recent_breakout_level", "confirmation_price")
    final_score = _first_number(data, "final_score", "finalScore")
    risk_score = _first_number(data, "risk_score", "riskScore")
    volume_status = str(_value(volume, "volume_price_status", "volumePriceStatus") or _value(data, "volume_price_status", "volumePriceStatus") or "").upper()
    volume_score_input = _first_number(volume, "volume_price_score", "volumePriceScore") or _first_number(data, "volume_price_score", "volumePriceScore")
    volume_ratio = _first_number(volume, "volume_ratio", "volumeRatio") or _first_number(data, "volume_ratio", "volumeRatio")
    missing = _missing_fields(
        price=price,
        support_low=support_low,
        support_high=support_high,
        pullback_low=pullback_low,
        pullback_high=pullback_high,
        confirmation=confirmation,
        invalidation=invalidation,
        chase=chase,
        ma20=ma20,
        ma50=ma50,
        ma200=ma200,
        atr=atr,
        resistance=resistance,
        volume_status=volume_status,
        volume_ratio=volume_ratio,
    )
    core_allowed = final_score is None or final_score >= 70
    core_reason = "" if core_allowed else "综合评分低于70，禁止核心仓买入；小仓观察仍以技术承接和量价确认为准。"
    if missing:
        return BuyZoneContext(
            primary_zone="DATA_INSUFFICIENT",
            primary_zone_text=ZONE_TEXT["DATA_INSUFFICIENT"],
            setup_score=0.0,
            technical_structure_score=0.0,
            volume_acceptance_score=0.0,
            risk_reward_score=0.0,
            support_zone_low=None,
            support_zone_high=None,
            pullback_zone_low=None,
            pullback_zone_high=None,
            confirmation_price=confirmation,
            invalidation_price=invalidation,
            chase_price=chase,
            current_action=DATA_INSUFFICIENT,
            action_text=ACTION_TEXT[DATA_INSUFFICIENT],
            existing_position_action_text="已有持仓：技术承接数据不足，先控制新增买入并人工复核。",
            no_position_action_text="未持仓：技术承接数据不足，不给明确买入区。",
            zone_selection_reason="缺少历史K线、成交量或关键技术区间，不能用基本面或估值替代买区。",
            missing_fields=missing,
            core_position_allowed=core_allowed,
            core_position_reason=core_reason,
        )

    primary_zone = _primary_zone(
        price=price,
        support_low=support_low,
        support_high=support_high,
        pullback_low=pullback_low,
        pullback_high=pullback_high,
        repair_low=repair_low,
        repair_high=repair_high,
        confirmation=confirmation,
        invalidation=invalidation,
        chase=chase,
    )
    technical_score = _technical_structure_score(primary_zone)
    volume_score = _volume_acceptance_score(volume_status, volume_score_input)
    rr_score = _risk_reward_score(price=price, confirmation=confirmation, invalidation=invalidation, chase=chase, primary_zone=primary_zone)
    setup_score = round(technical_score * 0.45 + volume_score * 0.35 + rr_score * 0.20, 1)
    action = _current_action(primary_zone, setup_score, volume_status, volume_score, rr_score)
    return BuyZoneContext(
        primary_zone=primary_zone,
        primary_zone_text=ZONE_TEXT.get(primary_zone, "修复观察区"),
        setup_score=setup_score,
        technical_structure_score=technical_score,
        volume_acceptance_score=volume_score,
        risk_reward_score=rr_score,
        support_zone_low=support_low,
        support_zone_high=support_high,
        pullback_zone_low=pullback_low,
        pullback_zone_high=pullback_high,
        confirmation_price=confirmation,
        invalidation_price=invalidation,
        chase_price=chase,
        current_action=action,
        action_text=ACTION_TEXT[action],
        existing_position_action_text=_existing_position_action(action),
        no_position_action_text=_no_position_action(action),
        zone_selection_reason=_zone_reason(primary_zone, volume_status, rr_score, core_reason),
        missing_fields=[],
        core_position_allowed=core_allowed,
        core_position_reason=core_reason,
    )


def _missing_fields(**values: Any) -> list[str]:
    fields: list[str] = []
    for key in (
        "price",
        "support_low",
        "support_high",
        "pullback_low",
        "pullback_high",
        "confirmation",
        "invalidation",
        "chase",
        "ma20",
        "ma50",
        "ma200",
        "atr",
        "resistance",
    ):
        if values.get(key) is None:
            fields.append(_missing_label(key))
    if not values.get("volume_status") or values.get("volume_status") == "DATA_MISSING":
        fields.append("volume_acceptance")
    if values.get("volume_ratio") is None:
        fields.append("volume_ratio")
    return fields


def _primary_zone(
    *,
    price: float,
    support_low: float,
    support_high: float,
    pullback_low: float,
    pullback_high: float,
    repair_low: float | None,
    repair_high: float | None,
    confirmation: float,
    invalidation: float,
    chase: float,
) -> str:
    if price < invalidation:
        return "INVALIDATION"
    if price >= chase:
        return "CHASE_RISK"
    if _in_range(price, support_low, support_high):
        return "DEEP_ACCEPTANCE"
    if _in_range(price, pullback_low, pullback_high):
        return "PULLBACK_BUY"
    if repair_low is not None and repair_high is not None and _in_range(price, repair_low, repair_high):
        return "REPAIR_WATCH"
    if price >= confirmation:
        return "CONFIRMATION_REVIEW"
    if price > pullback_high:
        return "REPAIR_WATCH"
    return "WAIT_PULLBACK"


def _technical_structure_score(primary_zone: str) -> float:
    return {
        "DEEP_ACCEPTANCE": 82.0,
        "PULLBACK_BUY": 78.0,
        "REPAIR_WATCH": 58.0,
        "CONFIRMATION_REVIEW": 62.0,
        "CHASE_RISK": 18.0,
        "INVALIDATION": 5.0,
        "WAIT_PULLBACK": 45.0,
    }.get(primary_zone, 40.0)


def _volume_acceptance_score(status: str, explicit_score: float | None) -> float:
    if status == "ACCEPTANCE_CONFIRMED":
        return max(78.0, explicit_score or 82.0)
    if status == "FORMING":
        return max(45.0, min(72.0, explicit_score or 56.0))
    if status == "UNCONFIRMED":
        return min(48.0, explicit_score or 42.0)
    if status == "FAILED":
        return 0.0
    if status == "OVEREXTENDED_SUPPORT_READ":
        return 20.0
    return 0.0


def _risk_reward_score(
    *,
    price: float,
    confirmation: float,
    invalidation: float,
    chase: float,
    primary_zone: str,
) -> float:
    if primary_zone in {"INVALIDATION", "CHASE_RISK"}:
        return 5.0 if primary_zone == "INVALIDATION" else 18.0
    downside = max(price - invalidation, 0.01)
    upside = max(confirmation - price, chase - price, 0.0)
    ratio = upside / downside
    if ratio >= 2.0:
        return 88.0
    if ratio >= 1.4:
        return 75.0
    if ratio >= 1.0:
        return 62.0
    if ratio >= 0.6:
        return 45.0
    return 28.0


def _current_action(primary_zone: str, setup_score: float, volume_status: str, volume_score: float, rr_score: float) -> str:
    if primary_zone == "INVALIDATION" or volume_status == "FAILED":
        return RISK_REVIEW
    if primary_zone == "CHASE_RISK" or volume_status == "OVEREXTENDED_SUPPORT_READ":
        return BLOCK_CHASE
    if primary_zone in {"DEEP_ACCEPTANCE", "PULLBACK_BUY"} and setup_score >= 62 and volume_score >= 50 and rr_score >= 55:
        return ALLOW_SMALL_BUY
    if primary_zone == "REPAIR_WATCH":
        return WAIT_CONFIRMATION
    if primary_zone == "CONFIRMATION_REVIEW":
        return WAIT_CONFIRMATION
    return WAIT_PULLBACK


def _existing_position_action(action: str) -> str:
    if action == ALLOW_SMALL_BUY:
        return "已有持仓：允许回踩复核加仓，但不能一次打满。"
    if action == BLOCK_CHASE:
        return "已有持仓：不追高加仓，等待回到承接区。"
    if action == RISK_REVIEW:
        return "已有持仓：进入风控复核，暂停新增买入。"
    if action == DATA_INSUFFICIENT:
        return "已有持仓：技术承接数据不足，先暂停新增买入。"
    return "已有持仓：持有观察，等待量价确认或更低回踩。"


def _no_position_action(action: str) -> str:
    if action == ALLOW_SMALL_BUY:
        return "未持仓：允许小仓观察，后续加仓必须等确认。"
    if action == BLOCK_CHASE:
        return "未持仓：禁止追高，等待回到回踩买区。"
    if action == RISK_REVIEW:
        return "未持仓：暂停买入，先复核失效风险。"
    if action == DATA_INSUFFICIENT:
        return "未持仓：技术承接数据不足，不给明确买入区。"
    if action == WAIT_PULLBACK:
        return "未持仓：等待回踩到承接区。"
    return "未持仓：等待重新评估线和量价确认。"


def _zone_reason(primary_zone: str, volume_status: str, rr_score: float, core_reason: str) -> str:
    base = {
        "DEEP_ACCEPTANCE": "价格接近强支撑 / 前低 / 承接区，按深度承接区处理。",
        "PULLBACK_BUY": "价格回到技术回踩买区，买区由技术结构和量价承接决定。",
        "REPAIR_WATCH": "价格已修复但量能或承接尚未给出确认，先观察。",
        "CONFIRMATION_REVIEW": "价格接近确认复核区，确认线只触发重新评估，不等于直接买入。",
        "CHASE_RISK": "价格远离承接区或进入追高阈值，盈亏比恶化。",
        "INVALIDATION": "价格跌破失效线，优先进入风控复核。",
        "WAIT_PULLBACK": "价格不在高质量承接区，等待回踩。",
    }.get(primary_zone, "买区由技术结构、量价承接和风险收益比共同决定。")
    details = [base, f"量价状态：{_volume_status_text(volume_status)}。", f"风险收益比分 {rr_score:.1f}。"]
    if core_reason:
        details.append(core_reason)
    return "".join(details)


def _volume_status_text(status: str) -> str:
    return {
        "ACCEPTANCE_CONFIRMED": "承接确认",
        "FORMING": "承接形成中",
        "UNCONFIRMED": "量价未确认",
        "FAILED": "承接失败",
        "OVEREXTENDED_SUPPORT_READ": "脱离观察区",
        "DATA_MISSING": "数据不足",
    }.get(status or "", "数据不足")


def _missing_label(key: str) -> str:
    return {
        "price": "current_price",
        "support_low": "support_zone_low",
        "support_high": "support_zone_high",
        "pullback_low": "pullback_zone_low",
        "pullback_high": "pullback_zone_high",
        "confirmation": "confirmation_price",
        "invalidation": "invalidation_price",
        "chase": "chase_price",
        "ma20": "ma20",
        "ma50": "ma50",
        "ma200": "ma200",
        "atr": "atr_14",
        "resistance": "resistance_zone",
    }.get(key, key)


def _in_range(price: float, low: float, high: float) -> bool:
    lower, upper = sorted((low, high))
    return lower <= price <= upper


def _first_number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in source:
            number = _number(source.get(key))
            if number is not None:
                return number
    return None


def _number(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str):
        value = value.strip().replace("$", "").replace(",", "").replace("%", "").replace("x", "").replace("X", "")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _value(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source.get(key) not in (None, ""):
            return source.get(key)
    return None
