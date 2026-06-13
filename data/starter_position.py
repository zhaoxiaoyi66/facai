from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


BLOCKED_STARTER_MOODS = {"fomo", "anxiety", "bottom_fishing_impulse", "revenge_trade", "regret_chase"}
DEFAULT_STARTER_MAX_PCT = 7.0


@dataclass(frozen=True)
class StarterPositionResult:
    starter_position: bool
    can_sync_to_portfolio: bool
    starter_match_status: str
    starter_max_pct: float
    starter_position_before_pct: float | None
    starter_position_after_pct: float | None
    starter_block_reasons: list[str]
    starter_notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_starter_position(
    *,
    ticker: str,
    entry_mode: str,
    position_tier: str,
    radar_report: object | None,
    before_position_pct: object,
    after_position_pct: object,
    decision_mood: str,
    buy_reason: str,
    target_sell_price: object,
    thesis: str,
    add_plan: str,
    invalidation_condition: str,
    starter_max_pct: object = DEFAULT_STARTER_MAX_PCT,
) -> StarterPositionResult:
    mode = str(entry_mode or "").strip().lower()
    max_pct = _number(starter_max_pct) or DEFAULT_STARTER_MAX_PCT
    before_pct = _number(before_position_pct)
    after_pct = _number(after_position_pct)
    if mode != "starter_position":
        return _blocked("not_selected", [], max_pct, before_pct, after_pct)

    tier = str(position_tier or "").strip().upper()
    decision = str(_report_value(radar_report, "decision") or "").strip().upper()
    data_status = str(_report_value(radar_report, "data_status") or "").strip().upper()
    is_stale = bool(_report_value(radar_report, "is_stale"))
    valuation_score = _number(_report_value(radar_report, "valuation_score"))
    mood = str(decision_mood or "").strip().lower()
    reasons: list[str] = []
    notes: list[str] = []

    if tier != "A":
        reasons.append("只有 A 类持仓可以使用底仓建仓模式。")
    if before_pct is not None and before_pct >= max_pct:
        reasons.append(f"买前仓位 {before_pct:.1f}% 已达到或超过底仓阈值 {max_pct:.1f}%。")
    if after_pct is None:
        reasons.append("缺少买后仓位测算，不能判断底仓上限。")
    elif after_pct > max_pct + 1e-9:
        reasons.append(f"买后仓位 {after_pct:.1f}% 超过底仓上限 {max_pct:.1f}%。")
    if mood in BLOCKED_STARTER_MOODS:
        reasons.append("当前交易心理属于情绪交易风险，不能用底仓建仓绕过提示。")
    if not str(buy_reason or "").strip():
        reasons.append("缺少买入理由。")
    if _number(target_sell_price) is None:
        reasons.append("缺少目标卖出价。")
    if not str(thesis or "").strip():
        reasons.append("缺少 thesis / 买入逻辑。")
    if not str(add_plan or "").strip():
        reasons.append("缺少后续加仓计划。")
    if not str(invalidation_condition or "").strip():
        reasons.append("缺少失效条件。")
    if data_status in {"DATA_MISSING", "MISSING"} or decision == "DATA_MISSING":
        notes.append("Radar 买区数据缺失；这是提示，不会阻止底仓同步。")
    if is_stale:
        notes.append("Radar / 价格数据过期；这是提示，不会阻止底仓同步。")

    if reasons:
        return _blocked("starter_blocked", reasons, max_pct, before_pct, after_pct)

    if (decision == "BLOCK_CHASE" or _has_chase_reason(radar_report)) and not _is_large_down_day(radar_report):
        notes.append("当前价进入追高区，属于 Radar 买区提示；需复核后再买，但不单独阻止底仓同步。")

    if valuation_score is not None and valuation_score < 40:
        notes.append("估值评分低于 40：只允许作为 A 类小底仓，并保留估值风险提示。")
    final_score = _number(_report_value(radar_report, "final_score"))
    if final_score is not None and final_score < 70:
        notes.append("综合评分低于 70：不能视为完整核心仓，只能按底仓上限执行。")
    if _is_large_down_day(radar_report):
        notes.append("财报后大跌 / 高波动：不等同于追高，但仍需遵守底仓上限和失效条件。")

    notes.append(f"A 类底仓建仓通过：买后仓位不超过 {max_pct:.1f}%。")
    return StarterPositionResult(
        starter_position=True,
        can_sync_to_portfolio=True,
        starter_match_status="allow_starter_position",
        starter_max_pct=max_pct,
        starter_position_before_pct=before_pct,
        starter_position_after_pct=after_pct,
        starter_block_reasons=[],
        starter_notes=notes,
    )


def _blocked(
    status: str,
    reasons: list[str],
    max_pct: float,
    before_pct: float | None,
    after_pct: float | None,
) -> StarterPositionResult:
    return StarterPositionResult(
        starter_position=False,
        can_sync_to_portfolio=False,
        starter_match_status=status,
        starter_max_pct=max_pct,
        starter_position_before_pct=before_pct,
        starter_position_after_pct=after_pct,
        starter_block_reasons=reasons,
        starter_notes=[],
    )


def _report_value(report: object, key: str) -> Any:
    if report is None:
        return None
    if isinstance(report, dict):
        return report.get(key)
    if hasattr(report, key):
        return getattr(report, key)
    if hasattr(report, "to_dict"):
        data = report.to_dict()
        if isinstance(data, dict):
            return data.get(key)
    return None


def _has_chase_reason(report: object | None) -> bool:
    raw_reasons = _report_value(report, "block_reasons") or []
    if isinstance(raw_reasons, str):
        raw_reasons = [raw_reasons]
    for reason in raw_reasons:
        text = str(reason or "").lower()
        if "chase" in text or "追高" in text:
            return True
    return False


def _is_large_down_day(report: object | None) -> bool:
    value = _first_report_number(
        report,
        "one_day_change_pct",
        "daily_change_pct",
        "day_change_pct",
        "price_change_pct_1d",
        "change_percent",
        "changes_percentage",
        "changesPercentage",
    )
    if value is None:
        return False
    if -1 < value < 1 and value != 0:
        value *= 100
    return value <= -8


def _first_report_number(report: object | None, *keys: str) -> float | None:
    debug = _report_value(report, "debug")
    for key in keys:
        number = _number(_report_value(report, key))
        if number is not None:
            return number
        if isinstance(debug, dict):
            number = _number(debug.get(key))
            if number is not None:
                return number
    return None


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
