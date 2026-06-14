from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from data.buy_zone_display import build_buy_zone_display
from data.buy_zone_engine import build_buy_zone_context


BUY_MOOD_BLOCKERS = {"fomo", "anxiety", "bottom_fishing_impulse", "revenge_trade", "regret_chase"}
MISSING_BUY_GATE_REASON = "Radar 买入提示缺失，需人工判断；可手动继续，系统会记录为人工 override。"


@dataclass(frozen=True)
class BuyGateResult:
    ticker: str
    action: str
    decision: str
    is_blocked: bool
    can_sync_portfolio: bool
    severity: str
    warning_level: str
    reasons: list[str]
    advisory_warnings: list[str]
    allowed_add_pct: float
    core_max_pct: float
    trade_max_pct: float
    position_bucket: str
    mood: str
    is_observation_only: bool
    mood_gate_blocked: bool
    position_gate_blocked: bool
    gate_hard_blocked: bool
    radar_advisory_only: bool
    price_position: str
    entry_display_label: str
    entry_action_hint: str
    entry_display_reason: str
    buy_zone_snapshot: Any
    buy_zone_context: dict[str, Any]
    buy_zone_display: dict[str, Any]
    setup_score: float | None
    buy_zone_action: str
    buy_zone_action_text: str
    primary_zone_text: str
    technical_entry_zone: Any
    deep_valuation_zone: Any
    chase_above_price: float | None
    required_actions: list[str]
    gate_checked_at: str

    @property
    def can_continue(self) -> bool:
        return not self.is_blocked

    @property
    def can_sync_to_portfolio(self) -> bool:
        return self.can_sync_portfolio

    @property
    def status(self) -> str:
        if self.is_blocked:
            return "blocked"
        if self.severity == "warning":
            return "warning"
        return "pass"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["can_continue"] = self.can_continue
        data["can_sync_to_portfolio"] = self.can_sync_to_portfolio
        data["status"] = self.status
        return data


def evaluate_buy_gate(
    report: object,
    *,
    action_type: str,
    position_bucket: str = "trade",
    planned_after_position_pct: float | None = None,
    decision_mood: str = "",
    observation_only: bool = False,
    buy_reason: str = "",
    checked_at: datetime | None = None,
) -> BuyGateResult:
    action = str(action_type or "").strip().lower()
    data = _report_dict(report)
    ticker = str(data.get("ticker") or data.get("symbol") or "").strip().upper()
    decision = str(data.get("decision") or "DATA_MISSING").strip().upper()
    buy_zone_context = _buy_zone_context(data)
    buy_zone_display = build_buy_zone_display(buy_zone_context, data, mode="trade_gate")
    mood = str(decision_mood or "").strip().lower()
    bucket = _position_bucket(position_bucket)
    reasons: list[str] = []
    required: list[str] = []

    if action not in {"buy", "add"}:
        return BuyGateResult(
            ticker=ticker,
            action=action,
            decision=decision,
            is_blocked=False,
            can_sync_portfolio=True,
            severity="not_applicable",
            warning_level="info",
            reasons=[],
            advisory_warnings=[],
            allowed_add_pct=_number(data.get("allowed_add_pct")) or 0.0,
            core_max_pct=_number(data.get("core_max_pct")) or 0.0,
            trade_max_pct=_number(data.get("trade_max_pct")) or 0.0,
            position_bucket=bucket,
            mood=mood,
            is_observation_only=bool(observation_only),
            mood_gate_blocked=False,
            position_gate_blocked=False,
            gate_hard_blocked=False,
            radar_advisory_only=False,
            price_position=_price_position(data),
            entry_display_label=str(data.get("entry_display_label") or ""),
            entry_action_hint=str(data.get("entry_action_hint") or ""),
            entry_display_reason=str(data.get("entry_display_reason") or ""),
            buy_zone_snapshot=data.get("buy_zone"),
            buy_zone_context=buy_zone_context,
            buy_zone_display=buy_zone_display,
            setup_score=_context_number(buy_zone_context, "setup_score"),
            buy_zone_action=str(buy_zone_context.get("current_action") or ""),
            buy_zone_action_text=str(buy_zone_context.get("action_text") or ""),
            primary_zone_text=str(buy_zone_context.get("primary_zone_text") or ""),
            technical_entry_zone=_technical_entry_zone(data),
            deep_valuation_zone=data.get("deep_valuation_zone") or data.get("buy_zone"),
            chase_above_price=_number(data.get("chase_above_price")),
            required_actions=[],
            gate_checked_at=_checked_at(checked_at),
        )

    advisory_warnings = _decision_advisory_warnings(data, decision, bool(observation_only))
    advisory_warnings.extend(_buy_zone_context_warnings(buy_zone_context))
    mood_reasons = evaluate_mood_gate(mood)
    advisory_warnings.extend(mood_reasons)
    position_reasons = evaluate_position_limit(data, bucket, planned_after_position_pct)
    advisory_warnings.extend(position_reasons)
    advisory_warnings.extend(evaluate_position_advisory(data, bucket, planned_after_position_pct))
    if decision == "ALLOW_BUY" and not str(buy_reason or "").strip():
        advisory_warnings.append("ALLOW_BUY 仍建议填写买入理由，防止临场拍脑袋。")

    is_blocked = False
    can_sync = not bool(observation_only)
    if observation_only:
        advisory_warnings.append("仅观察不是一笔真实买入；请用计划买入或价格提醒记录观察，不写入真实账本。")

    warning_level = _warning_level(data, decision, advisory_warnings)
    severity = "warning" if advisory_warnings else "pass"
    return BuyGateResult(
        ticker=ticker,
        action=action,
        decision=decision,
        is_blocked=is_blocked,
        can_sync_portfolio=can_sync,
        severity=severity,
        warning_level=warning_level,
        reasons=_dedupe(reasons),
        advisory_warnings=_dedupe(advisory_warnings),
        allowed_add_pct=_number(data.get("allowed_add_pct")) or 0.0,
        core_max_pct=_number(data.get("core_max_pct")) or 0.0,
        trade_max_pct=_number(data.get("trade_max_pct")) or 0.0,
        position_bucket=bucket,
        mood=mood,
        is_observation_only=bool(observation_only),
        mood_gate_blocked=False,
        position_gate_blocked=False,
        gate_hard_blocked=False,
        radar_advisory_only=bool(advisory_warnings),
        price_position=_price_position(data),
        entry_display_label=str(data.get("entry_display_label") or ""),
        entry_action_hint=str(data.get("entry_action_hint") or ""),
        entry_display_reason=str(data.get("entry_display_reason") or ""),
        buy_zone_snapshot=buy_zone_context or data.get("buy_zone"),
        buy_zone_context=buy_zone_context,
        buy_zone_display=buy_zone_display,
        setup_score=_context_number(buy_zone_context, "setup_score"),
        buy_zone_action=str(buy_zone_context.get("current_action") or ""),
        buy_zone_action_text=str(buy_zone_context.get("action_text") or ""),
        primary_zone_text=str(buy_zone_context.get("primary_zone_text") or ""),
        technical_entry_zone=_technical_entry_zone(data),
        deep_valuation_zone=data.get("deep_valuation_zone") or data.get("buy_zone"),
        chase_above_price=_number(data.get("chase_above_price")),
        required_actions=_dedupe(required),
        gate_checked_at=_checked_at(checked_at),
    )


def evaluate_position_limit(data: dict[str, Any], position_bucket: str, planned_after_position_pct: float | None) -> list[str]:
    after_pct = _number(planned_after_position_pct)
    if after_pct is None:
        return []
    bucket = _position_bucket(position_bucket)
    if bucket == "core":
        limit = _number(data.get("core_max_pct"))
        label = "核心仓"
    elif bucket == "trade":
        limit = _number(data.get("trade_max_pct"))
        label = "交易仓"
    else:
        return ["未选择核心仓/交易仓，无法判断买入后是否偏离 Radar 仓位参考。"]
    if limit is None or limit <= 0:
        return []
    if after_pct > limit:
        return [f"当前买入偏离系统建议：买入后仓位 {after_pct:.1f}% 高于 Radar {label}参考上限 {limit:.1f}%；可手动继续，系统会记录为人工 override。"]
    return []


def evaluate_position_advisory(data: dict[str, Any], position_bucket: str, planned_after_position_pct: float | None) -> list[str]:
    after_pct = _number(planned_after_position_pct)
    if after_pct is None:
        return []
    bucket = _position_bucket(position_bucket)
    if bucket == "core":
        limit = _number(data.get("core_max_pct"))
        label = "核心仓"
    elif bucket == "trade":
        limit = _number(data.get("trade_max_pct"))
        label = "交易仓"
    else:
        return ["未选择核心仓/交易仓，无法给出 Radar 仓位参考。"]
    if limit is None:
        return [f"缺少 Radar {label}参考上限，需人工判断仓位。"]
    if limit <= 0:
        return [f"Radar {label}参考上限为 0%，这是风险提示；可手动继续，系统会记录为人工 override。"]
    return []


def evaluate_mood_gate(mood: str) -> list[str]:
    text = str(mood or "").strip().lower()
    if text in BUY_MOOD_BLOCKERS:
        return ["买入风险提示：当前存在 FOMO / 焦虑 / 抄底冲动 / 复仇交易倾向。可手动继续，系统会记录为人工 override。"]
    return []


def buy_gate_entry_fields(result: BuyGateResult | None, *, action_type: str = "") -> dict[str, Any]:
    if result is None:
        if str(action_type or "").strip().lower() in {"buy", "add"}:
            return {
                "radarDecision": "DATA_MISSING",
                "radarBlocked": False,
                "radarBlockReasons": [],
                "gateHardBlocked": False,
                "radarAdvisoryOnly": True,
                "radarAdvisoryWarnings": [MISSING_BUY_GATE_REASON],
                "warningLevel": "warning",
                "moodGateBlocked": False,
                "positionGateBlocked": False,
                "radarObservationOnly": False,
                "buyZoneContext": {},
                "buyZoneDisplay": {},
                "setupScore": None,
                "buyZoneAction": "",
                "buyZoneActionText": "",
                "primaryZoneText": "",
                "gateCheckedAt": _checked_at(None),
            }
        return {
            "radarDecision": "",
            "radarBlocked": False,
            "radarBlockReasons": [],
            "gateHardBlocked": False,
            "radarAdvisoryOnly": False,
            "radarAdvisoryWarnings": [],
            "warningLevel": "info",
            "moodGateBlocked": False,
            "positionGateBlocked": False,
            "radarObservationOnly": False,
            "buyZoneContext": {},
            "buyZoneDisplay": {},
            "setupScore": None,
            "buyZoneAction": "",
            "buyZoneActionText": "",
            "primaryZoneText": "",
            "gateCheckedAt": "",
        }
    return {
        "radarDecision": result.decision,
        "radarBlocked": False,
        "radarBlockReasons": result.reasons + result.required_actions,
        "gateHardBlocked": False,
        "radarAdvisoryOnly": result.radar_advisory_only,
        "radarAdvisoryWarnings": result.advisory_warnings,
        "warningLevel": result.warning_level,
        "pricePosition": result.price_position,
        "entryDisplayLabel": result.entry_display_label,
        "entryActionHint": result.entry_action_hint,
        "entryDisplayReason": result.entry_display_reason,
        "buyZoneSnapshot": result.buy_zone_snapshot,
        "buyZoneContext": result.buy_zone_context,
        "buyZoneDisplay": result.buy_zone_display,
        "setupScore": result.setup_score,
        "buyZoneAction": result.buy_zone_action,
        "buyZoneActionText": result.buy_zone_action_text,
        "primaryZoneText": result.primary_zone_text,
        "technicalEntryZone": result.technical_entry_zone,
        "deepValuationZone": result.deep_valuation_zone,
        "chaseAbovePrice": result.chase_above_price,
        "moodGateBlocked": result.mood_gate_blocked,
        "positionGateBlocked": result.position_gate_blocked,
        "radarObservationOnly": result.is_observation_only,
        "gateCheckedAt": result.gate_checked_at,
    }


def _decision_advisory_warnings(data: dict[str, Any], decision: str, observation_only: bool) -> list[str]:
    block_reasons = [str(item) for item in (data.get("block_reasons") or []) if str(item).strip()]
    if decision == "DATA_MISSING":
        return block_reasons or ["Radar / 买区数据不足，需人工判断；可手动继续，系统会记录为人工 override。"]
    if decision == "BLOCK_CHASE":
        return block_reasons or ["当前处于追高风险区，系统不建议追高；如仍继续，将记录为人工 override。"]
    if decision == "AVOID":
        return block_reasons or ["Radar 风险提示较高，需人工复核；如仍继续，将记录为人工 override。"]
    if decision == "WAIT":
        reason = "Radar 建议等待或复核；如仍继续，将记录为人工 override。"
        if observation_only:
            reason = "当前仅为观察记录，不是一笔真实买入；请用计划买入或价格提醒记录。"
        return [reason]
    if decision == "ALLOW_BUY":
        return []
    return ["Radar 提示状态未知，需人工判断；如仍继续，将记录为人工 override。"]


def _buy_zone_context(data: dict[str, Any]) -> dict[str, Any]:
    existing = data.get("buy_zone_context") or data.get("buyZoneContext")
    if isinstance(existing, dict) and existing:
        return dict(existing)
    try:
        return build_buy_zone_context(data, volume_snapshot=_volume_snapshot_from_report(data)).to_dict()
    except Exception:
        return {
            "current_action": "DATA_INSUFFICIENT",
            "action_text": "技术承接数据不足",
            "primary_zone_text": "技术承接数据不足",
            "setup_score": 0,
            "missing_fields": ["buy_zone_context"],
        }


def _has_buy_zone_context_inputs(data: dict[str, Any]) -> bool:
    level_keys = (
        "current_price",
        "currentPrice",
        "price",
        "deep_support_zone_low",
        "support_watch_zone_low",
        "effective_technical_entry_zone_low",
        "technical_pullback_zone_low",
        "near_term_repair_zone_low",
        "confirmation_price",
        "invalidation_price",
        "chase_above_price",
        "ma20",
        "ema20",
        "ma50",
        "ema50",
        "ma200",
        "ema200",
        "atr_14",
        "atr14",
        "recent_swing_high",
        "resistance_zone_high",
    )
    has_context_seed = any(data.get(key) not in (None, "") for key in level_keys)
    has_volume = any(
        data.get(key) not in (None, "")
        for key in ("volume_price_status", "volumePriceStatus", "volume_ratio", "volumeRatio", "volume_price_score", "volumePriceScore")
    )
    return has_context_seed or has_volume


def _volume_snapshot_from_report(data: dict[str, Any]) -> dict[str, Any]:
    snapshot = data.get("volume_price_acceptance") or data.get("volumePriceAcceptance")
    if isinstance(snapshot, dict):
        return snapshot
    return {
        "volume_price_status": data.get("volume_price_status") or data.get("volumePriceStatus"),
        "volume_price_score": data.get("volume_price_score") or data.get("volumePriceScore"),
        "volume_ratio": data.get("volume_ratio") or data.get("volumeRatio"),
    }


def _buy_zone_context_warnings(context: dict[str, Any]) -> list[str]:
    if not context:
        return []
    action = str(context.get("current_action") or "").strip().upper()
    action_text = str(context.get("action_text") or "").strip()
    zone_text = str(context.get("primary_zone_text") or "").strip()
    reason = str(context.get("zone_selection_reason") or "").strip()
    if action in {"ALLOW_SMALL_BUY", "ALLOW_ADD_ON_PULLBACK"}:
        warning = str(context.get("core_position_reason") or "").strip()
        return [warning] if warning else []
    if action == "BLOCK_CHASE":
        return [f"统一买区：{zone_text or '追高禁区'}，{action_text or '禁止追高'}；如仍继续，将记录为人工 override。"]
    if action == "RISK_REVIEW":
        return [f"统一买区：{zone_text or '失效风控区'}，{action_text or '进入风控复核'}；{reason or '跌破失效线，暂停新增买入。'}"]
    if action == "DATA_INSUFFICIENT":
        missing = context.get("missing_fields") or []
        missing_text = "、".join(str(item) for item in missing[:4]) if isinstance(missing, list) else ""
        suffix = f"暂缺：{missing_text}" if missing_text else "技术承接数据不足"
        return [f"统一买区：技术承接数据不足，不给明确买入区；{suffix}。"]
    if action in {"WAIT_CONFIRMATION", "WAIT_PULLBACK"}:
        return [f"统一买区：{zone_text or action_text}，{action_text or '等待确认'}；可手动继续，系统会记录为人工 override。"]
    return []


def _warning_level(data: dict[str, Any], decision: str, advisory_warnings: list[str]) -> str:
    if not advisory_warnings:
        return "info"
    price_position = _price_position(data)
    text = " ".join([decision, price_position, *[str(item) for item in advisory_warnings]]).upper()
    if any(token in text for token in ("BLOCK_CHASE", "IN_CHASE_ZONE", "AVOID", "FAILED", "EVENT", "追高", "冲击", "仓位")):
        return "danger"
    if any(token in text for token in ("风控复核", "数据不足", "禁止追高", "失效")):
        return "danger"
    return "warning"


def _context_number(context: dict[str, Any], key: str) -> float | None:
    if not context:
        return None
    return _number(context.get(key))


def _price_position(data: dict[str, Any]) -> str:
    debug = data.get("debug") if isinstance(data.get("debug"), dict) else {}
    return str(data.get("price_position") or data.get("zone_status") or debug.get("price_position") or "").strip().upper()


def _technical_entry_zone(data: dict[str, Any]) -> Any:
    if data.get("technical_entry_zone") not in (None, ""):
        return data.get("technical_entry_zone")
    low = _number(data.get("technical_entry_zone_low"))
    high = _number(data.get("technical_entry_zone_high"))
    if low is None and high is None:
        return None
    return {"lower": low, "upper": high}


def _report_dict(report: object) -> dict[str, Any]:
    if hasattr(report, "to_dict"):
        value = report.to_dict()
        return dict(value) if isinstance(value, dict) else {}
    return dict(report) if isinstance(report, dict) else {}


def _position_bucket(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"core", "核心仓"}:
        return "core"
    if text in {"trade", "trading", "交易仓"}:
        return "trade"
    return text


def _checked_at(value: datetime | None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def reasons_json(value: list[str]) -> str:
    return json.dumps(_dedupe(value), ensure_ascii=False)
