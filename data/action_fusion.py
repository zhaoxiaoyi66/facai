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

LEFT_PROBE_ALLOWED = "LEFT_PROBE_ALLOWED"
LEFT_ADD_ALLOWED = "LEFT_ADD_ALLOWED"
LEFT_WAIT_BETTER_PRICE = "LEFT_WAIT_BETTER_PRICE"
LEFT_NOT_ALLOWED = "LEFT_NOT_ALLOWED"
EVENT_REVIEW_ONLY = "EVENT_REVIEW_ONLY"
CHASE_BLOCKED = "CHASE_BLOCKED"
POSITION_LIMITED = "POSITION_LIMITED"

HIGH_QUALITY_PULLBACK = "HIGH_QUALITY_PULLBACK"
VALUE_REPAIR = "VALUE_REPAIR"
TREND_CONFIRMATION = "TREND_CONFIRMATION"
BREAKDOWN_REPAIR = "BREAKDOWN_REPAIR"
OVEREXTENDED_CHASE = "OVEREXTENDED_CHASE"
EVENT_GAP_REVIEW = "EVENT_GAP_REVIEW"
PORTFOLIO_OVERWEIGHT = "PORTFOLIO_OVERWEIGHT"
LOW_CONFIDENCE = "LOW_CONFIDENCE"

ACTION_LABELS = {
    ALLOW_SMALL_BUY: "小仓观察建议",
    WAIT_CONFIRMATION: "等待确认",
    ADD_ON_PULLBACK: "回踩复核观察",
    ADD_ON_BREAKOUT: "放量确认后复核",
    HOLD_NO_ADD: "仓位偏高，暂不建议加",
    REDUCE_RISK: "降低风险",
    BLOCK_CHASE: "追高风险提示",
    BREAKDOWN_REVIEW: "破位复核",
    EVENT_REVIEW: "事件冲击复核，谨慎处理",
    DATA_INSUFFICIENT: "数据不足，需人工判断",
}

VOLUME_STATUS_LABELS = {
    "ACCEPTANCE_CONFIRMED": "承接确认",
    "FORMING": "承接形成中",
    "UNCONFIRMED": "量价未确认",
    "FAILED": "承接失败",
    "OVEREXTENDED_SUPPORT_READ": "脱离观察区",
    "DATA_MISSING": "数据不足",
}

LEFT_SIDE_ACTION_LABELS = {
    LEFT_PROBE_ALLOWED: "左侧试仓参考",
    LEFT_ADD_ALLOWED: "左侧小幅新增参考",
    LEFT_WAIT_BETTER_PRICE: "等待更低左侧价",
    LEFT_NOT_ALLOWED: "左侧不建议买入",
    EVENT_REVIEW_ONLY: "事件复核，不做左侧摊低",
    CHASE_BLOCKED: "脱离观察区，追高风险提示",
    POSITION_LIMITED: "仓位接近上限，建议控制节奏",
}

ROLE_LEFT_SIDE_CAP_RATIOS = {
    "ai_core": 0.80,
    "ai_hardware_core": 0.70,
    "ai_platform_core": 0.75,
    "ai_software_core": 0.75,
    "ai_software_repair": 0.45,
    "ai_infra_satellite": 0.50,
    "ai_network_satellite": 0.50,
    "ai_hardware_satellite": 0.50,
    "ai_memory_cycle": 0.50,
    "event_trade": 0.35,
    "watch_only": 0.25,
}

LEFT_SIDE_QUALITY_EXEMPT_ROLES = {"ai_core", "ai_platform_core", "ai_software_core"}


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
    advisory_warnings_cn: list[str] = field(default_factory=list)
    risk_bullets_cn: list[str] = field(default_factory=list)
    watch_levels: dict[str, float | None] = field(default_factory=dict)
    portfolio_role: str = ""
    current_shares: float = 0.0
    avg_cost: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None
    current_weight: float | None = None
    target_weight: float | None = None
    max_weight: float | None = None
    portfolio_updated_at: str | None = None
    position_status_cn: str = ""
    position_action_cn: str = ""
    left_side_allowed: bool = False
    left_side_action_cn: str = ""
    left_probe_size_cn: str = ""
    left_add_levels_cn: str = ""
    right_confirm_trigger_cn: str = ""
    left_side_warning_cn: str = ""
    left_side_plan: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def blocker_bullets_cn(self) -> list[str]:
        return self.advisory_warnings_cn


def evaluate_action_fusion(
    *,
    ticker: str,
    context: dict[str, Any] | None = None,
    portfolio_context: dict[str, Any] | None = None,
) -> ActionFusionResult:
    source = _source_with_buy_zone_context(context or {})
    portfolio = dict(portfolio_context or {})
    symbol = str(ticker or source.get("ticker") or source.get("symbol") or "").upper()
    price = _first_number(source, "current_price", "currentPrice", "price")
    observation_low = _first_number(
        source,
        "observation_low",
        "observationLow",
        "near_term_repair_zone_low",
        "technical_pullback_zone_low",
        "effective_technical_entry_zone_low",
    )
    observation_high = _first_number(
        source,
        "observation_high",
        "observationHigh",
        "near_term_repair_zone_high",
        "technical_pullback_zone_high",
        "effective_technical_entry_zone_high",
    )
    invalid_line = _first_number(source, "invalid_line", "invalidLine", "invalidation_price", "invalidationPrice")
    confirm_line = _first_number(source, "confirm_line", "confirmLine", "confirmation_price", "confirmationPrice")
    valuation_low = _first_number(
        source,
        "valuation_zone_low",
        "valuationReferenceZoneLow",
        "valuation_reference_zone_low",
        "deep_valuation_zone_low",
        "entry_reference_low",
    )
    valuation_high = _first_number(
        source,
        "valuation_zone_high",
        "valuationReferenceZoneHigh",
        "valuation_reference_zone_high",
        "deep_valuation_zone_high",
        "entry_reference_high",
    )
    deep_support_low = _first_number(source, "deep_support_zone_low", "deepSupportZoneLow")
    deep_support_high = _first_number(source, "deep_support_zone_high", "deepSupportZoneHigh")

    buy_zone_action = str(_value(source, "buy_zone_action", "buyZoneAction") or "").upper()
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
    market_value = _first_number(portfolio, "market_value", "marketValue")
    unrealized_pnl = _first_number(portfolio, "unrealized_pnl", "unrealizedPnl")
    unrealized_pnl_pct = _first_number(portfolio, "unrealized_pnl_pct", "unrealizedPnlPct")
    weight = _first_number(portfolio, "portfolio_weight", "portfolioWeight", "positionPct")
    target_weight = _first_number(portfolio, "target_weight", "targetWeight", "targetPositionPct")
    max_weight = _first_number(portfolio, "max_weight", "maxWeight", "maxAcceptablePositionPct", "maxPortfolioWeightPercent")
    portfolio_updated_at = _value(portfolio, "portfolio_updated_at", "portfolioUpdatedAt", "updatedAt", "updated_at")
    role = str(_value(portfolio, "role", "portfolio_role", "portfolioRole") or "").strip()
    cash = _first_number(portfolio, "available_cash", "availableCash", "cashBalance")

    evidence = _evidence(symbol, price, observation_low, observation_high, volume_status, volume_score, volume_regime, quality_score, valuation_score)
    advisory_warnings: list[str] = []
    risks: list[str] = []
    watch_levels = {
        "observation_low": observation_low,
        "observation_high": observation_high,
        "confirm_line": confirm_line,
        "invalid_line": invalid_line,
        "valuation_low": valuation_low,
        "valuation_high": valuation_high,
    }

    critical_missing = bool(_value(source, "critical_data_missing", "criticalDataMissing")) or price is None or buy_zone_action == DATA_INSUFFICIENT
    overextended = (price is not None and observation_high is not None and price > observation_high) or volume_status == "OVEREXTENDED_SUPPORT_READ"
    chase_context = buy_zone_action == BLOCK_CHASE or radar_decision == "BLOCK_CHASE" or zone_status == "IN_CHASE_ZONE" or overextended
    position_state = _position_state(weight=weight, target_weight=target_weight, max_weight=max_weight)
    role_warning = _role_warning(role)
    if role_warning:
        advisory_warnings.append(role_warning)

    def finish(action_code: str, setup_type: str, confidence: str) -> ActionFusionResult:
        left_side_plan = _build_left_side_plan(
            action_code=action_code,
            price=price,
            quality_score=quality_score,
            observation_low=observation_low,
            observation_high=observation_high,
            valuation_low=valuation_low,
            valuation_high=valuation_high,
            deep_support_low=deep_support_low,
            deep_support_high=deep_support_high,
            confirm_line=confirm_line,
            invalid_line=invalid_line,
            volume_status=volume_status,
            volume_score=volume_score,
            volume_ratio=volume_ratio,
            gap_down=gap_down,
            volume_reason=volume_reason,
            critical_missing=critical_missing,
            chase_context=chase_context,
            overextended=overextended,
            position_state=position_state,
            shares=shares,
            weight=weight,
            target_weight=target_weight,
            max_weight=max_weight,
            role=role,
        )
        return _result(
            action_code,
            setup_type,
            confidence,
            symbol,
            price,
            shares,
            avg_cost,
            market_value,
            unrealized_pnl,
            unrealized_pnl_pct,
            weight,
            target_weight,
            max_weight,
            str(portfolio_updated_at) if portfolio_updated_at not in (None, "") else None,
            role,
            position_state,
            cash,
            advisory_warnings,
            risks,
            evidence,
            watch_levels,
            left_side_plan,
        )

    if critical_missing:
        advisory_warnings.append("缺少价格或核心区间，暂无可靠交易建议。")
        return finish(DATA_INSUFFICIENT, LOW_CONFIDENCE, "低")

    if position_state["code"] in {"AT_MAX", "OVER_MAX"}:
        advisory_warnings.append("当前仓位已达到/超过上限，即使技术承接也不继续加仓。")
        return finish(HOLD_NO_ADD, PORTFOLIO_OVERWEIGHT, "中")

    if buy_zone_action in {"RISK_REVIEW", "AVOID"}:
        advisory_warnings.append("统一买区已进入风控复核，暂停新增买入并人工复核。")
        return finish(BREAKDOWN_REVIEW, BREAKDOWN_REPAIR, "高")

    if chase_context and overextended:
        advisory_warnings.append("好公司但价格已脱离回踩观察区，不构成低吸。")
        return finish(BLOCK_CHASE, OVEREXTENDED_CHASE, "高")

    if volume_status == "FAILED":
        advisory_warnings.append("量价承接失败，暂停加仓。")
        return finish(BREAKDOWN_REVIEW, BREAKDOWN_REPAIR, "高")

    if invalid_line is not None and price is not None and price < invalid_line:
        advisory_warnings.append(f"价格跌破失效线 {_money(invalid_line)}，先做破位复核。")
        return finish(BREAKDOWN_REVIEW, BREAKDOWN_REPAIR, "高")

    if (volume_ratio is not None and volume_ratio >= 2.0 and gap_down) or "爆量跳空下跌" in volume_reason or "高量跳空下跌" in volume_reason:
        advisory_warnings.append("高量跳空下跌，需复核财报/消息冲击，不做无确认摊低。")
        return finish(EVENT_REVIEW, EVENT_GAP_REVIEW, "高")

    if technical_status == "BREAKDOWN_REVIEW":
        risks.append("技术结构仍处于破位复核，不能只因估值便宜加仓。")

    in_observation = _in_range(price, observation_low, observation_high)
    in_value_area = _in_range(price, valuation_low, valuation_high) or _in_range(price, deep_support_low, deep_support_high)
    quality_ok = quality_score is None or quality_score >= 60
    valuation_ok = valuation_score is None or valuation_score >= 55 or in_value_area
    under_target = target_weight is None or weight is None or weight < target_weight
    near_or_above_target = position_state["code"] == "NEAR_TARGET"

    if volume_status == "ACCEPTANCE_CONFIRMED" and quality_ok and valuation_ok and under_target:
        if near_or_above_target:
            advisory_warnings.append("仓位已接近目标仓，新增买入需等待更强确认或更低价格。")
            return finish(HOLD_NO_ADD if shares > 0 else WAIT_CONFIRMATION, PORTFOLIO_OVERWEIGHT, "中")
        return finish(ALLOW_SMALL_BUY, HIGH_QUALITY_PULLBACK, "中高")

    if in_observation and volume_status == "FORMING":
        if volume_score is not None and volume_score < 55:
            advisory_warnings.append("初步承接，尚未确认；等待放量站上确认线。")
            if near_or_above_target:
                advisory_warnings.append("仓位已接近目标仓，等待确认，不追不加。")
            return finish(WAIT_CONFIRMATION, VALUE_REPAIR if in_value_area else HIGH_QUALITY_PULLBACK, "中")
        if under_target and quality_ok and valuation_ok:
            if near_or_above_target:
                advisory_warnings.append("仓位已接近目标仓，新增买入需等待更强确认或更低价格。")
                return finish(HOLD_NO_ADD if shares > 0 else WAIT_CONFIRMATION, PORTFOLIO_OVERWEIGHT, "中")
            risks.append("承接形成中但未完全确认，适合小仓或等待确认。")
            return finish(ALLOW_SMALL_BUY, HIGH_QUALITY_PULLBACK, "中")

    if in_observation:
        advisory_warnings.append("价格在观察区，但量价承接尚未确认。")
        if near_or_above_target:
            advisory_warnings.append("仓位已接近目标仓，新增买入需等待更强确认或更低价格。")
        return finish(WAIT_CONFIRMATION, VALUE_REPAIR if in_value_area else TREND_CONFIRMATION, "中")

    if shares > 0:
        advisory_warnings.append("暂无新的低吸或确认加仓信号。")
        return finish(HOLD_NO_ADD, LOW_CONFIDENCE, "中")

    advisory_warnings.append("缺少可执行确认条件，先放入观察。")
    return finish(WAIT_CONFIRMATION, LOW_CONFIDENCE, "低")


def action_fusion_card_html(result: ActionFusionResult) -> str:
    evidence = "".join(f"<li>{escape(item)}</li>" for item in result.evidence_bullets_cn[:4])
    advisory_warnings = "".join(f"<li>{escape(item)}</li>" for item in result.advisory_warnings_cn[:4])
    risks = "".join(f"<li>{escape(item)}</li>" for item in result.risk_bullets_cn[:3])
    warnings_html = f"<div><b>待确认事项</b><ul>{advisory_warnings}</ul></div>" if advisory_warnings else ""
    risks_html = f"<div><b>风险</b><ul>{risks}</ul></div>" if risks else ""
    position_html = _position_constraint_html(result)
    left_side_html = _left_side_plan_html(result)
    return (
        '<section class="action-fusion-card">'
        '<div class="action-fusion-kicker">系统建议</div>'
        f'<div class="action-fusion-headline">{escape(result.action_cn)} · {escape(result.confidence_level)}</div>'
        f'<p>{escape(result.buy_plan_cn)}</p>'
        '<div class="action-fusion-grid">'
        f'<div><b>证据</b><ul>{evidence}</ul></div>'
        f"{warnings_html}"
        f"{risks_html}"
        f'<div><b>下一触发位</b><span>{escape(result.next_trigger_cn)}</span></div>'
        f'<div><b>失效条件</b><span>{escape(result.invalidation_cn)}</span></div>'
        f'<div><b>仓位建议</b><span>{escape(result.position_advice_cn)}</span></div>'
        f"{position_html}"
        f"{left_side_html}"
        "</div>"
        "<small>Action Fusion 仅作辅助依据展示，不改变买区主建议、Radar 研究状态或组合同步。</small>"
        "</section>"
    )


def _result(
    action_code: str,
    setup_type: str,
    confidence: str,
    symbol: str,
    price: float | None,
    shares: float,
    avg_cost: float | None,
    market_value: float | None,
    unrealized_pnl: float | None,
    unrealized_pnl_pct: float | None,
    weight: float | None,
    target_weight: float | None,
    max_weight: float | None,
    portfolio_updated_at: str | None,
    role: str,
    position_state: dict[str, str],
    cash: float | None,
    advisory_warnings: list[str],
    risks: list[str],
    evidence: list[str],
    watch_levels: dict[str, float | None],
    left_side_plan: dict[str, Any],
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
        advisory_warnings_cn=advisory_warnings,
        risk_bullets_cn=risks,
        watch_levels=watch_levels,
        portfolio_role=role,
        current_shares=shares,
        avg_cost=avg_cost,
        market_value=market_value,
        unrealized_pnl=unrealized_pnl,
        unrealized_pnl_pct=unrealized_pnl_pct,
        current_weight=weight,
        target_weight=target_weight,
        max_weight=max_weight,
        portfolio_updated_at=portfolio_updated_at,
        position_status_cn=position_state["label"],
        position_action_cn=position_state["action"],
        left_side_allowed=bool(left_side_plan.get("allowed")),
        left_side_action_cn=str(left_side_plan.get("action_cn") or ""),
        left_probe_size_cn=str(left_side_plan.get("probe_size_cn") or ""),
        left_add_levels_cn=str(left_side_plan.get("add_levels_cn") or ""),
        right_confirm_trigger_cn=str(left_side_plan.get("right_confirm_trigger_cn") or ""),
        left_side_warning_cn=str(left_side_plan.get("warning_cn") or ""),
        left_side_plan=dict(left_side_plan),
    )


def _source_with_buy_zone_context(context: dict[str, Any]) -> dict[str, Any]:
    source = dict(context or {})
    zone_context = source.get("buy_zone_context") or source.get("buyZoneContext")
    if not isinstance(zone_context, dict):
        return source
    mapped = {
        "buy_zone_action": zone_context.get("current_action"),
        "observation_low": zone_context.get("pullback_zone_low") or zone_context.get("support_zone_low"),
        "observation_high": zone_context.get("pullback_zone_high") or zone_context.get("support_zone_high"),
        "deep_support_zone_low": zone_context.get("support_zone_low"),
        "deep_support_zone_high": zone_context.get("support_zone_high"),
        "confirmation_price": zone_context.get("confirmation_price"),
        "invalidation_price": zone_context.get("invalidation_price"),
        "chase_price": zone_context.get("chase_price"),
        "current_zone": zone_context.get("primary_zone"),
    }
    return {**source, **{key: value for key, value in mapped.items() if value not in (None, "")}}


def _buy_plan(action_code: str, symbol: str, next_trigger: str) -> str:
    if action_code == ALLOW_SMALL_BUY:
        return f"{symbol} 处于小仓观察参考状态，仍需人工确认；{next_trigger}"
    if action_code == BLOCK_CHASE:
        return "不建议追高，等待回踩观察区或新的确认结构。"
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
    return f"当前不建议新增；{next_trigger}"


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
        return f"放量站上确认线 {_money(confirm)} 后重新评估。"
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


def _position_constraint_html(result: ActionFusionResult) -> str:
    if not any(
        value not in (None, "")
        for value in (
            result.current_weight,
            result.target_weight,
            result.max_weight,
            result.portfolio_role,
        )
    ):
        return ""
    detail = "；".join(
        item
        for item in (
            f"当前 {_pct_text(result.current_weight)}",
            f"目标 {_pct_text(result.target_weight)}",
            f"上限 {_pct_text(result.max_weight)}",
            f"状态 {result.position_status_cn or '待确认'}",
            f"动作 {result.position_action_cn or '等待确认'}",
            f"角色 {result.portfolio_role}" if result.portfolio_role else "",
        )
        if item
    )
    return f"<div><b>仓位约束</b><span>{escape(detail)}</span></div>"


def _left_side_plan_html(result: ActionFusionResult) -> str:
    if not result.left_side_action_cn and not result.left_side_plan:
        return ""
    detail = "；".join(
        item
        for item in (
            result.left_side_action_cn,
            result.left_probe_size_cn,
            result.left_add_levels_cn,
            result.right_confirm_trigger_cn,
            result.left_side_warning_cn,
        )
        if item
    )
    return f"<div><b>左侧计划</b><span>{escape(detail)}</span></div>"


def _build_left_side_plan(
    *,
    action_code: str,
    price: float | None,
    quality_score: float | None,
    observation_low: float | None,
    observation_high: float | None,
    valuation_low: float | None,
    valuation_high: float | None,
    deep_support_low: float | None,
    deep_support_high: float | None,
    confirm_line: float | None,
    invalid_line: float | None,
    volume_status: str,
    volume_score: float | None,
    volume_ratio: float | None,
    gap_down: bool,
    volume_reason: str,
    critical_missing: bool,
    chase_context: bool,
    overextended: bool,
    position_state: dict[str, str],
    shares: float,
    weight: float | None,
    target_weight: float | None,
    max_weight: float | None,
    role: str,
) -> dict[str, Any]:
    normalized_role = _normalized_role(role)
    cap_ratio = _left_side_cap_ratio(normalized_role)
    left_cap_weight = target_weight * cap_ratio if target_weight is not None else None
    left_context = _left_side_context(
        price=price,
        observation_low=observation_low,
        observation_high=observation_high,
        valuation_low=valuation_low,
        valuation_high=valuation_high,
        deep_support_low=deep_support_low,
        deep_support_high=deep_support_high,
    )
    common = {
        "right_confirm_trigger_cn": _right_confirm_trigger(confirm_line),
        "invalidation_cn": _left_invalidation(invalid_line, observation_low),
        "add_levels_cn": _left_add_levels(left_context, invalid_line),
        "left_cap_ratio": cap_ratio,
        "left_cap_weight": left_cap_weight,
    }
    event_shock = bool(
        (volume_ratio is not None and volume_ratio >= 2.0 and gap_down)
        or "爆量跳空下跌" in volume_reason
        or "高量跳空下跌" in volume_reason
        or action_code == EVENT_REVIEW
    )
    if critical_missing or action_code == DATA_INSUFFICIENT or volume_status == "DATA_MISSING":
        return _left_plan(
            LEFT_NOT_ALLOWED,
            False,
            "数据不足，左侧不建议买入；先补齐价格、K线和核心区间。",
            **common,
        )
    if normalized_role == "event_trade" and volume_status != "ACCEPTANCE_CONFIRMED":
        return _left_plan(
            LEFT_NOT_ALLOWED,
            False,
            "事件仓信号未确认，左侧不建议买入；先补足数据和事件复核。",
            **common,
        )
    if event_shock:
        return _left_plan(
            EVENT_REVIEW_ONLY,
            False,
            "估值便宜但存在事件/爆量冲击，不做无确认摊低。",
            **common,
        )
    if chase_context or overextended or volume_status == "OVEREXTENDED_SUPPORT_READ" or action_code == BLOCK_CHASE:
        return _left_plan(
            CHASE_BLOCKED,
            False,
            "价格已脱离回踩观察区，左侧也不追。",
            **common,
        )
    if position_state["code"] in {"AT_MAX", "OVER_MAX", "NEAR_TARGET"}:
        warning = "仓位已接近或达到上限，即使技术承接也不继续加仓。"
        return _left_plan(POSITION_LIMITED, False, warning, **common)
    if volume_status == "FAILED":
        return _left_plan(
            LEFT_NOT_ALLOWED,
            False,
            "量价承接失败，左侧暂停；等待重新站回支撑或确认线。",
            **common,
        )
    quality_allows_left = (quality_score is not None and quality_score >= 70) or normalized_role in LEFT_SIDE_QUALITY_EXEMPT_ROLES
    if not quality_allows_left:
        return _left_plan(
            LEFT_NOT_ALLOWED,
            False,
            "基本面质量未达左侧试探要求，先等待更强确认。",
            **common,
        )
    if not left_context["in_left_zone"]:
        return _left_plan(
            LEFT_WAIT_BETTER_PRICE,
            False,
            "价格已进入观察范围，但左侧赔率不够，等待更低支撑位。",
            **common,
        )
    if weight is not None and left_cap_weight is not None and weight >= left_cap_weight:
        return _left_plan(
            LEFT_WAIT_BETTER_PRICE,
            False,
            f"左侧累计仓位已达到目标仓位 {cap_ratio * 100:.0f}% 参考上限，等待更低价或右侧确认，不主动左侧加。",
            **common,
        )
    if shares > 0 and (target_weight is None or weight is None or weight < target_weight):
        return _left_plan(
            LEFT_ADD_ALLOWED,
            True,
            "左侧小幅新增参考，但未过确认线，不能一次打满。",
            probe_size_cn=_left_add_size(weight, target_weight, cap_ratio),
            **common,
        )
    return _left_plan(
        LEFT_PROBE_ALLOWED,
        True,
        "左侧试仓参考，但尚未确认；先用目标仓位的 20%-30%，等待量价继续确认。",
        probe_size_cn=_left_probe_size(target_weight, cap_ratio),
        **common,
    )


def _left_plan(
    action_code: str,
    allowed: bool,
    warning_cn: str,
    *,
    probe_size_cn: str = "",
    add_levels_cn: str = "",
    right_confirm_trigger_cn: str = "",
    invalidation_cn: str = "",
    left_cap_ratio: float | None = None,
    left_cap_weight: float | None = None,
) -> dict[str, Any]:
    return {
        "action_code": action_code,
        "allowed": allowed,
        "action_cn": LEFT_SIDE_ACTION_LABELS.get(action_code, action_code),
        "probe_size_cn": probe_size_cn,
        "add_levels_cn": add_levels_cn,
        "right_confirm_trigger_cn": right_confirm_trigger_cn,
        "invalidation_cn": invalidation_cn,
        "warning_cn": warning_cn,
        "left_cap_ratio": left_cap_ratio,
        "left_cap_weight": left_cap_weight,
    }


def _left_side_context(
    *,
    price: float | None,
    observation_low: float | None,
    observation_high: float | None,
    valuation_low: float | None,
    valuation_high: float | None,
    deep_support_low: float | None,
    deep_support_high: float | None,
) -> dict[str, Any]:
    in_observation = _in_range(price, observation_low, observation_high)
    in_valuation = _in_range(price, valuation_low, valuation_high)
    in_deep_support = _in_range(price, deep_support_low, deep_support_high)
    return {
        "in_left_zone": in_observation or in_valuation or in_deep_support,
        "observation_low": observation_low,
        "observation_high": observation_high,
        "valuation_low": valuation_low,
        "valuation_high": valuation_high,
        "deep_support_low": deep_support_low,
        "deep_support_high": deep_support_high,
    }


def _left_probe_size(target_weight: float | None, cap_ratio: float) -> str:
    if target_weight is None:
        return "试探仓：目标仓位的 20%-30%，等待量价继续确认。"
    left_cap = target_weight * cap_ratio
    return f"试探仓：目标仓位的 20%-30%（约 {target_weight * 0.2:.1f}%-{target_weight * 0.3:.1f}% 组合）；未确认前左侧累计上限约 {left_cap:.1f}%。"


def _left_add_size(weight: float | None, target_weight: float | None, cap_ratio: float) -> str:
    if target_weight is None:
        return "小幅加仓：未确认前不能一次打满。"
    left_cap = target_weight * cap_ratio
    if weight is not None:
        room = max(0.0, left_cap - weight)
        return f"小幅加仓：左侧累计上限为目标仓位的 {cap_ratio * 100:.0f}%（约 {left_cap:.1f}% 组合），当前约 {weight:.1f}%，剩余额度约 {room:.1f}%。"
    return f"小幅加仓：左侧累计上限为目标仓位的 {cap_ratio * 100:.0f}%（约 {left_cap:.1f}% 组合），未过确认线不能一次打满。"


def _left_add_levels(context: dict[str, Any], invalid_line: float | None) -> str:
    levels = []
    if context.get("observation_low") is not None:
        levels.append(f"观察区下沿 {_money(context['observation_low'])}")
    if context.get("deep_support_low") is not None:
        levels.append(f"深度支撑 {_money(context['deep_support_low'])}")
    if context.get("valuation_low") is not None:
        levels.append(f"估值下沿 {_money(context['valuation_low'])}")
    if invalid_line is not None:
        levels.append(f"失效线 {_money(invalid_line)}")
    return "下一档低吸价：" + "；".join(levels[:4]) if levels else "下一档低吸价：等待观察区或支撑位补齐。"


def _right_confirm_trigger(confirm_line: float | None) -> str:
    if confirm_line is None:
        return "右侧确认：等待放量站上确认线或关键均线。"
    return f"右侧确认：放量站上确认线 {_money(confirm_line)} 后再考虑确认加仓。"


def _left_invalidation(invalid_line: float | None, observation_low: float | None) -> str:
    if invalid_line is not None:
        return f"左侧失效：跌破失效线 {_money(invalid_line)} 暂停。"
    if observation_low is not None:
        return f"左侧失效：跌破观察区下沿 {_money(observation_low)} 暂停。"
    return "左侧失效：缺少失效线，需人工补充。"


def _position_state(
    *,
    weight: float | None,
    target_weight: float | None,
    max_weight: float | None,
) -> dict[str, str]:
    if weight is not None and max_weight is not None:
        if weight > max_weight:
            return {"code": "OVER_MAX", "label": "超过上限", "action": "需降低风险"}
        if weight >= max_weight:
            return {"code": "AT_MAX", "label": "已达上限", "action": "不再加仓"}
    if weight is not None and target_weight is not None:
        if weight >= target_weight or weight >= target_weight * 0.9:
            return {"code": "NEAR_TARGET", "label": "接近目标", "action": "只能等待"}
    return {"code": "BELOW_TARGET", "label": "低于目标", "action": "可建仓"}


def _role_warning(role: str) -> str:
    normalized = _normalized_role(role)
    if normalized == "event_trade":
        return "事件仓仅限小仓，不转为核心仓。"
    if normalized in {"repair", "ai_software_repair"} or normalized.endswith("_repair"):
        return "修复仓以估值修复为主，仓位上限较低。"
    if normalized.endswith("_satellite") or normalized in {"satellite", "watch_only"}:
        return "卫星/观察仓按较低上限管理，不作为核心仓加仓。"
    if normalized == "ai_memory_cycle":
        return "周期型 AI 存储仓按低上限管理，等待低吸结构而不追高。"
    return ""


def _normalized_role(role: str) -> str:
    return str(role or "").strip().lower()


def _left_side_cap_ratio(role: str) -> float:
    return ROLE_LEFT_SIDE_CAP_RATIOS.get(role, ROLE_LEFT_SIDE_CAP_RATIOS["watch_only"])


def _pct_text(value: float | None) -> str:
    if value is None:
        return "未配置"
    return f"{value:.1f}%"


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
