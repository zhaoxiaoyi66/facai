from __future__ import annotations

from dataclasses import asdict, dataclass, field
from html import escape
from typing import Any


ALLOW_SMALL_BUY = "ALLOW_SMALL_BUY"
WAIT_CONFIRMATION = "WAIT_CONFIRMATION"
ADD_ON_PULLBACK = "ADD_ON_PULLBACK"
ADD_ON_BREAKOUT = "ADD_ON_BREAKOUT"
HOLD_NO_ADD = "HOLD_NO_ADD"
REDUCE_RISK = "REDUCE_RISK"
BLOCK_CHASE = "BLOCK_CHASE"
BREAKDOWN_REVIEW = "BREAKDOWN_REVIEW"
EVENT_REVIEW = "EVENT_REVIEW"
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"

HIGH_QUALITY_PULLBACK = "HIGH_QUALITY_PULLBACK"
VALUE_REPAIR = "VALUE_REPAIR"
TREND_CONFIRMATION = "TREND_CONFIRMATION"
BREAKDOWN_REPAIR = "BREAKDOWN_REPAIR"
OVEREXTENDED_CHASE = "OVEREXTENDED_CHASE"
EVENT_GAP_REVIEW = "EVENT_GAP_REVIEW"
PORTFOLIO_OVERWEIGHT = "PORTFOLIO_OVERWEIGHT"
LOW_CONFIDENCE = "LOW_CONFIDENCE"

ACTION_LABELS = {
    ALLOW_SMALL_BUY: "允许小仓试探",
    WAIT_CONFIRMATION: "等待确认",
    ADD_ON_PULLBACK: "回踩到位再加",
    ADD_ON_BREAKOUT: "放量确认后加",
    HOLD_NO_ADD: "持有，不加",
    REDUCE_RISK: "降低风险",
    BLOCK_CHASE: "禁止追高",
    BREAKDOWN_REVIEW: "破位复核",
    EVENT_REVIEW: "事件冲击复核",
    DATA_INSUFFICIENT: "数据不足",
}

VOLUME_STATUS_LABELS = {
    "ACCEPTANCE_CONFIRMED": "承接确认",
    "FORMING": "承接形成中",
    "UNCONFIRMED": "量价未确认",
    "FAILED": "承接失败",
    "OVEREXTENDED_SUPPORT_READ": "脱离观察区",
    "DATA_MISSING": "数据不足",
}


@dataclass(frozen=True)
class ActionFusionResult:
    action_code: str
    action_cn: str
    confidence_level: str
    setup_type: str
    position_advice_cn: str
    buy_plan_cn: str
    add_plan_cn: str
    reduce_plan_cn: str
    invalidation_cn: str
    next_trigger_cn: str
    evidence_bullets_cn: list[str] = field(default_factory=list)
    blocker_bullets_cn: list[str] = field(default_factory=list)
    risk_bullets_cn: list[str] = field(default_factory=list)
    watch_levels: dict[str, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_action_fusion(
    *,
    ticker: str,
    context: dict[str, Any] | None = None,
    portfolio_context: dict[str, Any] | None = None,
) -> ActionFusionResult:
    source = dict(context or {})
    portfolio = dict(portfolio_context or {})
    symbol = str(ticker or source.get("ticker") or source.get("symbol") or "").upper()
    price = _first_number(source, "current_price", "currentPrice", "price")
    observation_low = _first_number(source, "observation_low", "observationLow", "near_term_repair_zone_low", "technical_pullback_zone_low", "effective_technical_entry_zone_low")
    observation_high = _first_number(source, "observation_high", "observationHigh", "near_term_repair_zone_high", "technical_pullback_zone_high", "effective_technical_entry_zone_high")
    invalid_line = _first_number(source, "invalid_line", "invalidLine", "invalidation_price", "invalidationPrice")
    confirm_line = _first_number(source, "confirm_line", "confirmLine", "confirmation_price", "confirmationPrice")
    valuation_low = _first_number(source, "valuation_zone_low", "valuationReferenceZoneLow", "valuation_reference_zone_low", "deep_valuation_zone_low", "entry_reference_low")
    valuation_high = _first_number(source, "valuation_zone_high", "valuationReferenceZoneHigh", "valuation_reference_zone_high", "deep_valuation_zone_high", "entry_reference_high")
    deep_support_low = _first_number(source, "deep_support_zone_low", "deepSupportZoneLow")
    deep_support_high = _first_number(source, "deep_support_zone_high", "deepSupportZoneHigh")

    radar_decision = str(_value(source, "radar_decision", "decision", "finalDecision") or "").upper()
    zone_status = str(_value(source, "zone_status", "price_position", "pricePosition", "current_zone", "currentZone") or "").upper()
    technical_status = str(_value(source, "technical_structure_status", "technicalStructureStatus") or "").upper()
    volume_status = str(_value(source, "volume_price_status", "volumePriceStatus") or "").upper()
    volume_score = _first_number(source, "volume_price_score", "volumePriceScore")
    volume_ratio = _first_number(source, "volume_ratio", "volumeRatio")
    volume_regime = str(_value(source, "volume_regime_cn", "volumeRegimeCn") or "")
    volume_reason = str(_value(source, "volume_price_reason_cn", "volumePriceReasonCn", "acceptance_reason_cn", "reason_cn") or "")
    gap_down = bool(_value(source, "gap_down", "gapDown")) or "跳空下跌" in volume_reason
    quality_score = _first_number(source, "quality_score", "qualityScore", "radar_score", "totalScore", "total_score")
    valuation_score = _first_number(source, "valuation_score", "valuationScore")

    shares = _first_number(portfolio, "current_shares", "currentShares", "quantity") or 0.0
    avg_cost = _first_number(portfolio, "avg_cost", "averageCost")
    weight = _first_number(portfolio, "portfolio_weight", "portfolioWeight", "positionPct")
    target_weight = _first_number(portfolio, "target_weight", "targetWeight", "targetPositionPct")
    max_weight = _first_number(portfolio, "max_weight", "maxWeight", "maxAcceptablePositionPct", "maxPortfolioWeightPercent")
    cash = _first_number(portfolio, "available_cash", "availableCash", "cashBalance")

    evidence = _evidence(symbol, price, observation_low, observation_high, volume_status, volume_score, volume_regime, quality_score, valuation_score)
    blockers: list[str] = []
    risks: list[str] = []
    watch_levels = {
        "observation_low": observation_low,
        "observation_high": observation_high,
        "confirm_line": confirm_line,
        "invalid_line": invalid_line,
        "valuation_low": valuation_low,
        "valuation_high": valuation_high,
    }

    critical_missing = bool(_value(source, "critical_data_missing", "criticalDataMissing")) or price is None
    overextended = (price is not None and observation_high is not None and price > observation_high) or volume_status == "OVEREXTENDED_SUPPORT_READ"
    chase_context = radar_decision == "BLOCK_CHASE" or zone_status == "IN_CHASE_ZONE" or overextended
    overweight = max_weight is not None and weight is not None and weight >= max_weight

    if critical_missing:
        blockers.append("缺少价格或核心区间，不能形成可靠交易建议。")
        return _result(DATA_INSUFFICIENT, LOW_CONFIDENCE, "低", symbol, price, shares, avg_cost, weight, target_weight, max_weight, cash, blockers, risks, evidence, watch_levels)

    if overweight:
        blockers.append("当前仓位已接近或超过系统参考上限。")
        return _result(HOLD_NO_ADD, PORTFOLIO_OVERWEIGHT, "中", symbol, price, shares, avg_cost, weight, target_weight, max_weight, cash, blockers, risks, evidence, watch_levels)

    if chase_context and overextended:
        blockers.append("好公司但价格已脱离回踩观察区，不构成低吸。")
        return _result(BLOCK_CHASE, OVEREXTENDED_CHASE, "高", symbol, price, shares, avg_cost, weight, target_weight, max_weight, cash, blockers, risks, evidence, watch_levels)

    if volume_status == "FAILED":
        blockers.append("量价承接失败，暂停加仓。")
        return _result(BREAKDOWN_REVIEW, BREAKDOWN_REPAIR, "高", symbol, price, shares, avg_cost, weight, target_weight, max_weight, cash, blockers, risks, evidence, watch_levels)

    if invalid_line is not None and price is not None and price < invalid_line:
        blockers.append(f"价格跌破失效线 {_money(invalid_line)}，先做破位复核。")
        return _result(BREAKDOWN_REVIEW, BREAKDOWN_REPAIR, "高", symbol, price, shares, avg_cost, weight, target_weight, max_weight, cash, blockers, risks, evidence, watch_levels)

    if (volume_ratio is not None and volume_ratio >= 2.0 and gap_down) or "爆量跳空下跌" in volume_reason or "高量跳空下跌" in volume_reason:
        blockers.append("高量跳空下跌，需复核财报/消息冲击，不做无确认摊低。")
        return _result(EVENT_REVIEW, EVENT_GAP_REVIEW, "高", symbol, price, shares, avg_cost, weight, target_weight, max_weight, cash, blockers, risks, evidence, watch_levels)

    if technical_status == "BREAKDOWN_REVIEW":
        risks.append("技术结构仍处于破位复核，不能只因估值便宜加仓。")

    in_observation = _in_range(price, observation_low, observation_high)
    in_value_area = _in_range(price, valuation_low, valuation_high) or _in_range(price, deep_support_low, deep_support_high)
    quality_ok = quality_score is None or quality_score >= 60
    valuation_ok = valuation_score is None or valuation_score >= 55 or in_value_area
    under_target = target_weight is None or weight is None or weight < target_weight

    if volume_status == "ACCEPTANCE_CONFIRMED" and quality_ok and valuation_ok and under_target:
        return _result(ALLOW_SMALL_BUY, HIGH_QUALITY_PULLBACK, "中高", symbol, price, shares, avg_cost, weight, target_weight, max_weight, cash, blockers, risks, evidence, watch_levels)

    if in_observation and volume_status == "FORMING":
        if volume_score is not None and volume_score < 55:
            blockers.append("初步承接，尚未确认；等待放量站上确认线。")
            return _result(WAIT_CONFIRMATION, VALUE_REPAIR if in_value_area else HIGH_QUALITY_PULLBACK, "中", symbol, price, shares, avg_cost, weight, target_weight, max_weight, cash, blockers, risks, evidence, watch_levels)
        if under_target and quality_ok and valuation_ok:
            risks.append("承接形成中但未完全确认，适合小仓或等待确认。")
            return _result(ALLOW_SMALL_BUY, HIGH_QUALITY_PULLBACK, "中", symbol, price, shares, avg_cost, weight, target_weight, max_weight, cash, blockers, risks, evidence, watch_levels)

    if in_observation:
        blockers.append("价格在观察区，但量价承接尚未确认。")
        return _result(WAIT_CONFIRMATION, VALUE_REPAIR if in_value_area else TREND_CONFIRMATION, "中", symbol, price, shares, avg_cost, weight, target_weight, max_weight, cash, blockers, risks, evidence, watch_levels)

    if shares > 0:
        blockers.append("暂无新的低吸或确认加仓信号。")
        return _result(HOLD_NO_ADD, LOW_CONFIDENCE, "中", symbol, price, shares, avg_cost, weight, target_weight, max_weight, cash, blockers, risks, evidence, watch_levels)

    blockers.append("缺少可执行确认条件，先放入观察。")
    return _result(WAIT_CONFIRMATION, LOW_CONFIDENCE, "低", symbol, price, shares, avg_cost, weight, target_weight, max_weight, cash, blockers, risks, evidence, watch_levels)


def action_fusion_card_html(result: ActionFusionResult) -> str:
    evidence = "".join(f"<li>{escape(item)}</li>" for item in result.evidence_bullets_cn[:4])
    blockers = "".join(f"<li>{escape(item)}</li>" for item in result.blocker_bullets_cn[:4])
    risks = "".join(f"<li>{escape(item)}</li>" for item in result.risk_bullets_cn[:3])
    blockers_html = f"<div><b>阻碍</b><ul>{blockers}</ul></div>" if blockers else ""
    risks_html = f"<div><b>风险</b><ul>{risks}</ul></div>" if risks else ""
    return (
        '<section class="action-fusion-card">'
        '<div class="action-fusion-kicker">系统建议</div>'
        f'<div class="action-fusion-headline">{escape(result.action_cn)} · {escape(result.confidence_level)}</div>'
        f'<p>{escape(result.buy_plan_cn)}</p>'
        '<div class="action-fusion-grid">'
        f'<div><b>证据</b><ul>{evidence}</ul></div>'
        f"{blockers_html}"
        f"{risks_html}"
        f'<div><b>下一触发位</b><span>{escape(result.next_trigger_cn)}</span></div>'
        f'<div><b>失效条件</b><span>{escape(result.invalidation_cn)}</span></div>'
        f'<div><b>仓位建议</b><span>{escape(result.position_advice_cn)}</span></div>'
        '</div>'
        '<small>Action Fusion 仅作交易建议融合展示，不改变 ALLOW_BUY / Radar decision / portfolio sync。</small>'
        '</section>'
    )


def _result(
    action_code: str,
    setup_type: str,
    confidence: str,
    symbol: str,
    price: float | None,
    shares: float,
    avg_cost: float | None,
    weight: float | None,
    target_weight: float | None,
    max_weight: float | None,
    cash: float | None,
    blockers: list[str],
    risks: list[str],
    evidence: list[str],
    watch_levels: dict[str, float | None],
) -> ActionFusionResult:
    action_cn = ACTION_LABELS[action_code]
    next_trigger = _next_trigger(action_code, watch_levels)
    invalidation = _invalidation(watch_levels)
    position = _position_advice(shares, avg_cost, weight, target_weight, max_weight, cash)
    buy_plan = _buy_plan(action_code, symbol, next_trigger)
    return ActionFusionResult(
        action_code=action_code,
        action_cn=action_cn,
        confidence_level=confidence,
        setup_type=setup_type,
        position_advice_cn=position,
        buy_plan_cn=buy_plan,
        add_plan_cn=_add_plan(action_code, next_trigger),
        reduce_plan_cn=_reduce_plan(action_code),
        invalidation_cn=invalidation,
        next_trigger_cn=next_trigger,
        evidence_bullets_cn=evidence,
        blocker_bullets_cn=blockers,
        risk_bullets_cn=risks,
        watch_levels=watch_levels,
    )


def _buy_plan(action_code: str, symbol: str, next_trigger: str) -> str:
    if action_code == ALLOW_SMALL_BUY:
        return f"{symbol} 可小仓试探，但仍需人工确认；{next_trigger}"
    if action_code == BLOCK_CHASE:
        return "禁止追高，等待回踩观察区或新的确认结构。"
    if action_code == EVENT_REVIEW:
        return "便宜也先复核事件冲击，不做无确认摊低。"
    if action_code == BREAKDOWN_REVIEW:
        return "暂停加仓，先确认支撑和趋势是否修复。"
    if action_code == HOLD_NO_ADD:
        return "核心持有，不追不加；等回踩或放量确认再复核。"
    return f"等待确认；{next_trigger}"


def _add_plan(action_code: str, next_trigger: str) -> str:
    if action_code in {ALLOW_SMALL_BUY, ADD_ON_BREAKOUT, ADD_ON_PULLBACK}:
        return f"仅按计划小额分批；{next_trigger}"
    return f"暂不加仓；{next_trigger}"


def _reduce_plan(action_code: str) -> str:
    if action_code == REDUCE_RISK:
        return "仓位或结构风险偏高，优先降风险。"
    if action_code in {BREAKDOWN_REVIEW, EVENT_REVIEW}:
        return "若后续确认破位或基本面改写，再进入卖出纪律复核。"
    return "本模块不触发卖出，只提示持仓复核。"


def _next_trigger(action_code: str, levels: dict[str, float | None]) -> str:
    confirm = levels.get("confirm_line")
    obs_low = levels.get("observation_low")
    obs_high = levels.get("observation_high")
    if action_code == BLOCK_CHASE and obs_high is not None:
        return f"等待回到观察区上沿 {_money(obs_high)} 以下。"
    if confirm is not None:
        return f"放量站上确认线 {_money(confirm)}。"
    if obs_low is not None and obs_high is not None:
        return f"回到观察区 {_money(obs_low)} - {_money(obs_high)} 后复核。"
    return "等待下一根K线和量价确认。"


def _invalidation(levels: dict[str, float | None]) -> str:
    invalid = levels.get("invalid_line")
    if invalid is not None:
        return f"跌破失效线 {_money(invalid)}。"
    obs_low = levels.get("observation_low")
    if obs_low is not None:
        return f"跌破观察区下沿 {_money(obs_low)}。"
    return "缺少失效线，需人工补充。"


def _position_advice(
    shares: float,
    avg_cost: float | None,
    weight: float | None,
    target_weight: float | None,
    max_weight: float | None,
    cash: float | None,
) -> str:
    parts: list[str] = []
    if shares > 0:
        text = f"当前持仓 {shares:g} 股"
        if avg_cost is not None:
            text += f"，成本 {_money(avg_cost)}"
        parts.append(text)
    else:
        parts.append("当前未持仓")
    if weight is not None:
        parts.append(f"仓位 {weight:.1f}%")
    if target_weight is not None:
        parts.append(f"目标 {target_weight:.1f}%")
    if max_weight is not None:
        parts.append(f"上限 {max_weight:.1f}%")
    if cash is not None:
        parts.append(f"可用现金 {_money(cash)}")
    return "；".join(parts) + "。"


def _evidence(
    symbol: str,
    price: float | None,
    observation_low: float | None,
    observation_high: float | None,
    volume_status: str,
    volume_score: float | None,
    volume_regime: str,
    quality_score: float | None,
    valuation_score: float | None,
) -> list[str]:
    items = [f"{symbol} 当前价 {_money(price)}。"]
    if observation_low is not None and observation_high is not None:
        items.append(f"观察区 {_money(observation_low)} - {_money(observation_high)}。")
    if volume_status:
        score_text = "" if volume_score is None else f" {volume_score:g}分"
        volume_label = VOLUME_STATUS_LABELS.get(str(volume_status).upper(), str(volume_status))
        items.append(f"量价承接 {volume_label}{score_text}，{volume_regime or '量能待确认'}。")
    if quality_score is not None:
        items.append(f"质量/综合分 {quality_score:g}。")
    if valuation_score is not None:
        items.append(f"估值分 {valuation_score:g}。")
    return items


def _in_range(value: float | None, low: float | None, high: float | None) -> bool:
    if value is None:
        return False
    if low is not None and value < low:
        return False
    if high is not None and value > high:
        return False
    return low is not None or high is not None


def _first_number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = _number(_value(source, key))
        if number is not None:
            return number
    return None


def _value(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source:
            return source.get(key)
    debug = source.get("debug")
    if isinstance(debug, dict):
        for key in keys:
            if key in debug:
                return debug.get(key)
    return None


def _number(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value: float | None) -> str:
    if value is None:
        return "暂缺"
    return f"${value:,.2f}"
