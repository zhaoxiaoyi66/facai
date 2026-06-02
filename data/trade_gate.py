from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


BUY_MOOD_BLOCKERS = {"fomo", "anxiety", "bottom_fishing_impulse", "revenge_trade", "regret_chase"}
MISSING_BUY_GATE_REASON = "Radar 买入门禁结果缺失，禁止自动同步组合持仓。"


@dataclass(frozen=True)
class BuyGateResult:
    ticker: str
    action: str
    decision: str
    is_blocked: bool
    can_sync_portfolio: bool
    severity: str
    reasons: list[str]
    allowed_add_pct: float
    core_max_pct: float
    trade_max_pct: float
    position_bucket: str
    mood: str
    is_observation_only: bool
    mood_gate_blocked: bool
    position_gate_blocked: bool
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
        return "blocked" if self.is_blocked else "pass"

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
            reasons=[],
            allowed_add_pct=_number(data.get("allowed_add_pct")) or 0.0,
            core_max_pct=_number(data.get("core_max_pct")) or 0.0,
            trade_max_pct=_number(data.get("trade_max_pct")) or 0.0,
            position_bucket=bucket,
            mood=mood,
            is_observation_only=bool(observation_only),
            mood_gate_blocked=False,
            position_gate_blocked=False,
            required_actions=[],
            gate_checked_at=_checked_at(checked_at),
        )

    reasons.extend(_decision_gate_reasons(data, decision, bool(observation_only)))
    mood_reasons = evaluate_mood_gate(mood)
    reasons.extend(mood_reasons)
    position_reasons = evaluate_position_limit(data, bucket, planned_after_position_pct)
    reasons.extend(position_reasons)
    if decision == "ALLOW_BUY" and not str(buy_reason or "").strip():
        required.append("ALLOW_BUY 仍需填写买入理由，防止临场拍脑袋。")

    is_blocked = bool(reasons or required)
    can_sync = not is_blocked and not bool(observation_only)
    if observation_only and is_blocked:
        required.append("仅观察记录不会同步到组合持仓；这不是一笔真实买入。")

    severity = "block" if is_blocked else "pass"
    return BuyGateResult(
        ticker=ticker,
        action=action,
        decision=decision,
        is_blocked=is_blocked,
        can_sync_portfolio=can_sync,
        severity=severity,
        reasons=_dedupe(reasons),
        allowed_add_pct=_number(data.get("allowed_add_pct")) or 0.0,
        core_max_pct=_number(data.get("core_max_pct")) or 0.0,
        trade_max_pct=_number(data.get("trade_max_pct")) or 0.0,
        position_bucket=bucket,
        mood=mood,
        is_observation_only=bool(observation_only),
        mood_gate_blocked=bool(mood_reasons),
        position_gate_blocked=bool(position_reasons),
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
        return ["未选择核心仓/交易仓，不能判断买入后是否超过 Radar 仓位上限。"]
    if limit is None:
        return [f"缺少 {label} 上限，不能继续新增。"]
    if after_pct > limit:
        return [f"买入后仓位 {after_pct:.1f}% 超过 Radar {label}上限 {limit:.1f}%。"]
    return []


def evaluate_mood_gate(mood: str) -> list[str]:
    text = str(mood or "").strip().lower()
    if text in BUY_MOOD_BLOCKERS:
        return ["情绪交易风险：FOMO / 焦虑 / 抄底冲动 / 复仇交易不能绕过 Radar 门禁。"]
    return []


def buy_gate_entry_fields(result: BuyGateResult | None, *, action_type: str = "") -> dict[str, Any]:
    if result is None:
        if str(action_type or "").strip().lower() in {"buy", "add"}:
            return {
                "radarDecision": "DATA_MISSING",
                "radarBlocked": True,
                "radarBlockReasons": [MISSING_BUY_GATE_REASON],
                "moodGateBlocked": False,
                "positionGateBlocked": False,
                "radarObservationOnly": False,
                "gateCheckedAt": _checked_at(None),
            }
        return {
            "radarDecision": "",
            "radarBlocked": False,
            "radarBlockReasons": [],
            "moodGateBlocked": False,
            "positionGateBlocked": False,
            "radarObservationOnly": False,
            "gateCheckedAt": "",
        }
    return {
        "radarDecision": result.decision,
        "radarBlocked": result.is_blocked,
        "radarBlockReasons": result.reasons + result.required_actions,
        "moodGateBlocked": result.mood_gate_blocked,
        "positionGateBlocked": result.position_gate_blocked,
        "radarObservationOnly": result.is_observation_only,
        "gateCheckedAt": result.gate_checked_at,
    }


def _decision_gate_reasons(data: dict[str, Any], decision: str, observation_only: bool) -> list[str]:
    block_reasons = [str(item) for item in (data.get("block_reasons") or []) if str(item).strip()]
    if decision in {"DATA_MISSING", "BLOCK_CHASE"}:
        return block_reasons or [f"Radar 结论为 {decision}，禁止新增。"]
    if decision == "AVOID":
        return ["Radar 结论为 AVOID，禁止把风险票当成新增仓位。"]
    if decision == "WAIT":
        reason = "Radar 结论为 WAIT，默认禁止真实买入/加仓。"
        if observation_only:
            reason = "Radar 结论为 WAIT；仅可保存为观察记录，不同步组合持仓。"
        return [reason]
    if decision == "ALLOW_BUY":
        return []
    return [f"Radar 结论未知：{decision or 'missing'}，禁止新增。"]


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
