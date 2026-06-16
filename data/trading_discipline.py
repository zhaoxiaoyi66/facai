from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "trading_discipline.yaml"

HOLD_ACTIONS = {"", "hold", "none", "watch"}
SELL_ACTIONS = {"sell", "trim"}
REENTRY_REQUIRED_REASONS = {"technical", "valuation"}
B_CLASS_EXIT_REASONS = {"downgrade_watch", "unclear_thesis", "thesis_broken", "risk_control"}
B_CLASS_ORDINARY_SELL_CAP = 0.25
B_CLASS_EXIT_REVIEW_CAP = 0.50
EVENT_EXIT_REASONS = {
    "earnings_catalyst_done",
    "event_trade_done",
    "catalyst_failed",
    "no_post_earnings_reaction",
    "planned_exit",
}
NOW_STYLE_RISK_BLOCKER = "now_style_error_risk"
PLANNED_ACTUAL_MISMATCH_BLOCKER = "planned_actual_sell_pct_mismatch"
ACTUAL_SELL_LEVEL_LIMIT_BLOCKER = "actual_sell_pct_exceeds_sell_level_limit"
PLANNED_SELL_LEVEL_LIMIT_BLOCKER = "planned_sell_pct_exceeds_sell_level_limit"
A_CLASS_EMOTIONAL_SELL_CAP_BLOCKER = "a_class_macro_or_emotional_sell_exceeds_20_pct"
A_CLASS_CORE_FLOOR_BLOCKER = "a_class_core_floor_breached"
PLANNED_CORE_FLOOR_BLOCKER = "planned_sell_pct_breaches_core_floor"
B_CLASS_POSITION_SIZE_NOT_OVER_LIMIT_BLOCKER = "b_class_position_size_requires_actual_overlimit"
B_CLASS_LOW_SELL_NEEDS_EXIT_REASON_BLOCKER = "b_class_low_sell_requires_downgrade_or_thesis"
NOW_STYLE_RISK_TEXT = (
    "NOW 式错误风险：A 类核心股在投资逻辑未破坏时，不应因宏观恐慌或情绪压力卖出核心仓。"
    "若你不愿右侧追回，就不能全卖低位买到的好公司。"
)
NOW_STYLE_RISK_MOODS = {"anxiety", "macro_fear", "panic_sell", "焦虑", "宏观恐慌", "恐慌卖出"}

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "position_classes": {
        "A": {
            "core_position_pct": 0.70,
            "trading_position_pct": 0.30,
            "forbid_core_sale_gain_low_pct": 0.00,
            "forbid_core_sale_gain_high_pct": 0.25,
        },
        "B": {
            "core_position_pct": 0.50,
            "trading_position_pct": 0.50,
            "forbid_core_sale_gain_low_pct": 0.00,
            "forbid_core_sale_gain_high_pct": 0.00,
        },
        "C": {
            "core_position_pct": 0.00,
            "trading_position_pct": 1.00,
            "forbid_core_sale_gain_low_pct": 0.00,
            "forbid_core_sale_gain_high_pct": 0.00,
        },
    },
    "sell_levels": {
        "L0": {"max_allowed_sell_pct": 0.00, "can_sell_core": False, "requires_reentry_plan": False},
        "L1": {"max_allowed_sell_pct": 0.10, "can_sell_core": False, "requires_reentry_plan": True},
        "L2": {"max_allowed_sell_pct": 0.20, "can_sell_core": False, "requires_reentry_plan": False},
        "L3": {"max_allowed_sell_pct": 0.30, "can_sell_core": False, "requires_reentry_plan": False},
        "L4": {"max_allowed_sell_pct": 0.70, "can_sell_core": True, "requires_reentry_plan": False},
        "L5": {"max_allowed_sell_pct": 1.00, "can_sell_core": True, "requires_reentry_plan": False},
    },
}


@dataclass(frozen=True)
class TradingDisciplineResult:
    disciplineStatus: str
    sellLevel: str
    maxAllowedSellPct: float
    canSellCore: bool
    requiresReentryPlan: bool
    actualSellPct: float
    plannedActualMismatch: float
    blockers: list[str]
    warnings: list[str]
    reminderText: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_trading_discipline_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return DEFAULT_CONFIG.copy()
    parsed = _parse_simple_yaml_mapping(config_path.read_text(encoding="utf-8"))
    return _merge_config(DEFAULT_CONFIG, parsed)


def evaluate_trading_discipline(
    *,
    symbol: str,
    positionClass: str,
    corePositionPct: float | int | None,
    tradingPositionPct: float | int | None,
    unrealizedGainPct: float | int | None,
    plannedAction: str,
    plannedSellPct: float | int | None,
    sellReasonType: str,
    thesisBroken: bool,
    positionOverLimit: bool,
    hasReentryPlan: bool,
    actualSellPct: float | int | None = None,
    decisionMood: str | None = None,
    emotionTags: list[str] | str | None = None,
    belowTargetSellPrice: bool = False,
    inBuyZoneOrBelow: bool = False,
    config: dict[str, Any] | None = None,
) -> TradingDisciplineResult:
    rules = config or load_trading_discipline_config()
    position_class = str(positionClass or "").strip().upper() or "C"
    class_rules = dict(rules.get("position_classes", {}).get(position_class, {}))
    sell_reason = str(sellReasonType or "").strip().lower()
    planned_action = str(plannedAction or "").strip().lower()
    planned_sell_pct = _normalize_pct(plannedSellPct)
    actual_sell_pct = _normalize_pct(actualSellPct) if actualSellPct is not None else planned_sell_pct
    effective_sell_pct = actual_sell_pct
    unrealized_gain_pct = _normalize_pct(unrealizedGainPct)
    core_pct = _normalize_pct(corePositionPct)
    trading_pct = _normalize_pct(tradingPositionPct)
    if core_pct is None:
        core_pct = _number(class_rules.get("core_position_pct"), 0.0)
    if trading_pct is None:
        trading_pct = _number(class_rules.get("trading_position_pct"), max(0.0, 1.0 - core_pct))

    gate_sell_pct = max(effective_sell_pct, planned_sell_pct)
    sell_level = _sell_level(planned_action, sell_reason, gate_sell_pct, thesisBroken, positionOverLimit)
    level_rules = dict(rules.get("sell_levels", {}).get(sell_level, DEFAULT_CONFIG["sell_levels"]["L0"]))
    max_allowed_sell_pct = _number(level_rules.get("max_allowed_sell_pct"), 0.0)
    level_allows_core = bool(level_rules.get("can_sell_core", False))
    b_class_exit_review = (
        position_class == "B"
        and planned_action in SELL_ACTIONS
        and (sell_reason in B_CLASS_EXIT_REASONS or thesisBroken)
    )
    b_class_ordinary_sell = position_class == "B" and planned_action in SELL_ACTIONS and not b_class_exit_review
    if b_class_exit_review:
        max_allowed_sell_pct = max(max_allowed_sell_pct, B_CLASS_EXIT_REVIEW_CAP)
    elif b_class_ordinary_sell:
        max_allowed_sell_pct = max(max_allowed_sell_pct, B_CLASS_ORDINARY_SELL_CAP)
    emotional_or_macro_sell = planned_action in SELL_ACTIONS and (
        sell_reason == "macro" or _has_now_style_risk_mood(decisionMood, emotionTags)
    )
    a_class_emotional_or_macro = (
        position_class == "A"
        and planned_action in SELL_ACTIONS
        and not thesisBroken
        and not positionOverLimit
        and emotional_or_macro_sell
    )
    if a_class_emotional_or_macro:
        max_allowed_sell_pct = max(max_allowed_sell_pct, 0.20)
    c_class_event_exit = position_class == "C" and planned_action in SELL_ACTIONS and sell_reason in EVENT_EXIT_REASONS
    if c_class_event_exit:
        max_allowed_sell_pct = max(max_allowed_sell_pct, 1.0)
    requires_reentry_plan = bool(level_rules.get("requires_reentry_plan", False)) or (
        planned_action in SELL_ACTIONS and sell_reason in REENTRY_REQUIRED_REASONS and gate_sell_pct > 0
    ) or (
        position_class == "B" and planned_action in SELL_ACTIONS and gate_sell_pct > 0
    ) or (
        emotional_or_macro_sell and gate_sell_pct > 0
    )
    actual_touches_core = effective_sell_pct > trading_pct + 1e-9
    planned_touches_core = planned_sell_pct > trading_pct + 1e-9
    touches_core = actual_touches_core or planned_touches_core
    actual_clears_position = effective_sell_pct >= max(core_pct + trading_pct, 0.95) - 1e-9
    planned_clears_position = planned_sell_pct >= max(core_pct + trading_pct, 0.95) - 1e-9
    clears_position = actual_clears_position or planned_clears_position
    can_sell_core = bool(level_allows_core and (thesisBroken or position_class != "A"))
    planned_actual_mismatch = abs(effective_sell_pct - planned_sell_pct)

    blockers: list[str] = []
    warnings: list[str] = []

    if planned_action in HOLD_ACTIONS or gate_sell_pct <= 0:
        return TradingDisciplineResult(
            disciplineStatus="hold",
            sellLevel="L0",
            maxAllowedSellPct=0.0,
            canSellCore=False,
            requiresReentryPlan=False,
            actualSellPct=0.0,
            plannedActualMismatch=0.0,
            blockers=[],
            warnings=[],
            reminderText=f"{symbol.upper()} 当前没有卖出计划，继续按核心仓纪律持有。",
        )

    if planned_actual_mismatch > 0.02 + 1e-9:
        blockers.append(PLANNED_ACTUAL_MISMATCH_BLOCKER)
    if position_class == "A" and not thesisBroken and clears_position and core_pct > 0:
        blockers.append("a_class_core_clear_requires_thesis_break")
    if position_class == "A" and not thesisBroken and (1.0 - effective_sell_pct) < core_pct - 1e-9:
        blockers.append(A_CLASS_CORE_FLOOR_BLOCKER)
    if position_class == "A" and not thesisBroken and (1.0 - planned_sell_pct) < core_pct - 1e-9:
        blockers.append(PLANNED_CORE_FLOOR_BLOCKER)
    if a_class_emotional_or_macro and gate_sell_pct > 0.20 + 1e-9:
        blockers.append(A_CLASS_EMOTIONAL_SELL_CAP_BLOCKER)
    if position_class == "A" and touches_core and _within_core_gain_freeze(unrealized_gain_pct, class_rules):
        blockers.append("a_class_core_sale_blocked_while_gain_0_to_25_pct")
    if position_class == "A" and touches_core and not can_sell_core:
        blockers.append("sell_level_does_not_allow_core_sale")
    if b_class_ordinary_sell and sell_reason == "position_size" and not positionOverLimit:
        blockers.append(B_CLASS_POSITION_SIZE_NOT_OVER_LIMIT_BLOCKER)
    if (
        b_class_ordinary_sell
        and (belowTargetSellPrice or inBuyZoneOrBelow)
        and sell_reason not in {"thesis_broken", "risk_control"}
        and not thesisBroken
        and not positionOverLimit
    ):
        blockers.append(B_CLASS_LOW_SELL_NEEDS_EXIT_REASON_BLOCKER)
    if sell_reason == "macro" and clears_position:
        blockers.append("macro_risk_cannot_trigger_single_name_exit")
    if requires_reentry_plan and not hasReentryPlan:
        blockers.append("reentry_plan_required_before_trim_or_sell")
    if effective_sell_pct > max_allowed_sell_pct + 1e-9:
        blockers.append(ACTUAL_SELL_LEVEL_LIMIT_BLOCKER)
    if planned_sell_pct > max_allowed_sell_pct + 1e-9:
        blockers.append(PLANNED_SELL_LEVEL_LIMIT_BLOCKER)
    if (
        position_class == "A"
        and planned_action in SELL_ACTIONS
        and not thesisBroken
        and _has_now_style_risk_mood(decisionMood, emotionTags)
    ):
        if touches_core:
            blockers.append(NOW_STYLE_RISK_BLOCKER)
        else:
            warnings.append(NOW_STYLE_RISK_TEXT)

    if sell_reason == "valuation":
        warnings.append("估值偏贵只能作为减交易仓提示，不能替代投资逻辑破裂。")
    if sell_reason == "technical":
        warnings.append("技术过热只能作为择时提示，不能替代投资逻辑破裂。")
    if sell_reason == "position_size" or positionOverLimit:
        warnings.append("仓位过重可优先降低交易仓，但不自动建议卖核心仓。")
    if sell_reason == "macro":
        warnings.append("宏观风险只能用于降低组合总风险，不能单独清仓强个股。")
    if b_class_ordinary_sell:
        warnings.append("B 类普通减仓默认控制在 20%-25%，超过上限需改为降级/退出复核。")
    if b_class_exit_review:
        warnings.append("B 类降级为观察 / 买入逻辑不清 / thesis 失效可进入复核，但必须写清逻辑变化和回补条件。")
    if position_class == "B" and (belowTargetSellPrice or inBuyZoneOrBelow):
        warnings.append("B 类低于目标价或买区内卖出，需要说明基本面证伪、降级为观察或风险控制理由。")
    if planned_action in SELL_ACTIONS and not c_class_event_exit:
        warnings.append("财报前如需降低风险，优先处理交易仓并保留回补条件。")

    if position_class == "A" and planned_action in SELL_ACTIONS and not hasReentryPlan:
        warnings.append("A 类核心股卖出 / 减仓前应写清具体回补计划，避免把长期仓当短线筹码处理。")

    status = "blocked" if blockers else ("warning" if warnings else "allowed")
    return TradingDisciplineResult(
        disciplineStatus=status,
        sellLevel=sell_level,
        maxAllowedSellPct=round(max_allowed_sell_pct, 4),
        canSellCore=can_sell_core,
        requiresReentryPlan=requires_reentry_plan,
        actualSellPct=round(effective_sell_pct, 4),
        plannedActualMismatch=round(planned_actual_mismatch, 4),
        blockers=blockers,
        warnings=warnings,
        reminderText=_reminder_text(symbol, position_class, sell_level, status, effective_sell_pct),
    )


def _sell_level(
    planned_action: str,
    sell_reason: str,
    planned_sell_pct: float,
    thesis_broken: bool,
    position_over_limit: bool,
) -> str:
    if planned_action in HOLD_ACTIONS or planned_sell_pct <= 0:
        return "L0"
    if thesis_broken or sell_reason == "thesis_broken":
        return "L5" if planned_sell_pct >= 0.95 else "L4"
    if sell_reason in {"downgrade_watch", "unclear_thesis", "risk_control"}:
        return "L3"
    if sell_reason in EVENT_EXIT_REASONS:
        return "L3"
    if sell_reason == "target_price":
        return "L2"
    if sell_reason == "position_size" or position_over_limit:
        return "L2"
    if sell_reason == "macro":
        return "L1"
    if sell_reason in REENTRY_REQUIRED_REASONS:
        return "L1"
    return "L1"


def _within_core_gain_freeze(unrealized_gain_pct: float, class_rules: dict[str, Any]) -> bool:
    low = _number(class_rules.get("forbid_core_sale_gain_low_pct"), 0.0)
    high = _number(class_rules.get("forbid_core_sale_gain_high_pct"), 0.0)
    return high > low and low <= unrealized_gain_pct <= high


def _has_now_style_risk_mood(decision_mood: str | None, emotion_tags: list[str] | str | None) -> bool:
    values: list[str] = []
    if decision_mood:
        values.append(str(decision_mood))
    if isinstance(emotion_tags, str):
        values.append(emotion_tags)
    elif emotion_tags:
        values.extend(str(item) for item in emotion_tags)
    for value in values:
        normalized = value.strip().lower()
        if normalized in NOW_STYLE_RISK_MOODS:
            return True
        if any(token in normalized for token in NOW_STYLE_RISK_MOODS if not token.isascii()):
            return True
    return False


def _reminder_text(symbol: str, position_class: str, sell_level: str, status: str, planned_sell_pct: float) -> str:
    ticker = str(symbol or "").upper()
    pct_text = f"{planned_sell_pct * 100:.1f}%"
    if status == "blocked":
        return f"{ticker} 卖出计划触发高风险提醒：先复核核心仓、回补计划和卖出等级上限。"
    if sell_level == "L0":
        return f"{ticker} 当前不卖，继续按 {position_class} 类纪律持有。"
    return f"{ticker} 当前为 {sell_level} 卖出纪律，计划卖出 {pct_text} 前请优先处理交易仓并保留回补条件。"


def _normalize_pct(value: float | int | None) -> float:
    number = _number(value, 0.0)
    return number / 100 if abs(number) > 1 else number


def _number(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _merge_config(defaults: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_copy(defaults)
    for key, value in parsed.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _deep_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_copy(item) for item in value]
    return value


def _parse_simple_yaml_mapping(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        line_without_comment = raw_line.split("#", 1)[0].rstrip()
        if not line_without_comment.strip():
            continue
        indent = len(line_without_comment) - len(line_without_comment.lstrip(" "))
        line = line_without_comment.strip()
        if ":" not in line or line.startswith("-"):
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip().strip('"').strip("'")
        raw_value = raw_value.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if raw_value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(raw_value)
    return root


def _parse_scalar(raw_value: str) -> Any:
    value = raw_value.strip().strip('"').strip("'")
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
