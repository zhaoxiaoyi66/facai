from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from data.decision_log import TradeJournalStore
from data.post_sell_obligation import build_post_sell_obligations
from data.prices import CACHE_PATH
from data.sell_fly_review import PRIMARY_SELL_FLY_HORIZON, build_sell_fly_review_results


SELL_TRIM_ACTIONS = {"sell", "trim"}
LEVEL_ORDER = {"normal": 0, "caution": 1, "danger": 2, "stop": 3}
NOW_STYLE_RISK_BLOCKER = "now_style_error_risk"
EMOTIONAL_SELL_MOODS = {"anxiety", "macro_fear", "panic_sell", "regret_chase"}
NOW_STYLE_RISK_TEXT_PREFIX = "NOW 式错误风险"


@dataclass(frozen=True)
class TradingDisciplineStatsSummary:
    periodStart: str
    periodEnd: str
    totalTradesThisWeek: int
    sellTrimCountThisWeek: int
    aClassSellCountThisWeek: int
    macroSellCountThisWeek: int
    noReentryPlanSellCount: int
    disciplineBlockerCount: int
    disciplineWarningCount: int
    nowStyleRiskCount: int
    fomoTradeCount: int
    anxietyPanicTradeCount: int
    revengeTradeCount: int
    reasonedPlanTradeCount: int
    suspectedSellFlyCount: int
    reentryObligationCount: int
    reentryObligationTriggeredCount: int
    reentryObligationOverdueCount: int
    reentryObligationMissingPlanCount: int
    overTradingLevel: str
    disciplineScore: int
    disciplineLevel: str
    shouldPauseTrading: bool
    pauseReason: str
    mainViolations: list[str]
    suggestedAction: str
    warnings: list[str]
    reminderText: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_trading_discipline_stats(
    path: Path = CACHE_PATH,
    current_date: date | str | None = None,
) -> dict[str, Any]:
    current = _parse_date(current_date) or date.today()
    period_start = current.fromordinal(current.toordinal() - current.weekday())
    entries = [
        entry
        for entry in TradeJournalStore(path).list_entries()
        if _entry_in_period(entry, period_start, current)
    ]
    sell_trim_entries = [entry for entry in entries if str(entry.get("action_type") or "").lower() in SELL_TRIM_ACTIONS]
    a_class_sells = [
        entry
        for entry in sell_trim_entries
        if str(entry.get("position_class") or "").strip().upper() == "A"
    ]
    macro_sells = [
        entry
        for entry in sell_trim_entries
        if str(entry.get("sell_reason_type") or "").strip().lower() == "macro"
    ]
    no_reentry_sells = [
        entry
        for entry in sell_trim_entries
        if _has_discipline_snapshot(entry) and _is_false(entry.get("has_reentry_plan"))
    ]
    blocker_entries = [
        entry
        for entry in sell_trim_entries
        if _json_list(entry.get("blockers"), entry.get("blockers_json"))
    ]
    warning_entries = [
        entry
        for entry in sell_trim_entries
        if _json_list(entry.get("warnings"), entry.get("warnings_json"))
    ]
    now_style_risk_entries = [
        entry
        for entry in sell_trim_entries
        if _has_now_style_risk(entry)
    ]
    fomo_entries = _mood_entries(entries, {"fomo"})
    anxiety_panic_entries = _mood_entries(entries, {"anxiety", "macro_fear", "panic_sell"})
    revenge_entries = _mood_entries(entries, {"revenge_trade"})
    reasoned_plan_entries = _mood_entries(entries, {"well_reasoned", "plan_execution"})
    emotional_sell_entries = _mood_entries(sell_trim_entries, EMOTIONAL_SELL_MOODS)
    suspected_sell_fly_count = _suspected_sell_fly_count(path, period_start, current)
    reentry_obligations = _open_reentry_obligations(path, current)
    triggered_reentry = [item for item in reentry_obligations if item.get("status") == "triggered"]
    overdue_reentry = [item for item in reentry_obligations if item.get("status") == "overdue"]
    missing_plan_obligations = [item for item in reentry_obligations if item.get("status") == "missing_plan"]

    warnings: list[str] = []
    level = "normal"
    total_count = len(entries)
    if total_count > 10:
        level = _max_level(level, "danger")
        warnings.append("本周交易次数超过 10 次，已进入焦虑交易危险区。")
    elif total_count > 5:
        level = _max_level(level, "caution")
        warnings.append("本周交易次数超过 5 次，进入操作频率警戒区。")
    if len(a_class_sells) > 1:
        level = _max_level(level, "danger")
        warnings.append("A 类股票本周 sell / trim 超过 1 次，需暂停复核核心仓纪律。")
    if macro_sells:
        level = _max_level(level, "caution")
        warnings.append("本周存在宏观原因 sell / trim，宏观风险只能降低总仓，不能单独清仓强个股。")
    if no_reentry_sells:
        level = _max_level(level, "danger")
        warnings.append("本周存在无回补计划的 sell / trim，需停止高抛低吸式操作。")
    if blocker_entries:
        level = _max_level(level, "danger")
        warnings.append("本周存在纪律 blocker 的 sell / trim，需先处理阻断项再行动。")

    if now_style_risk_entries:
        level = _max_level(level, "danger")
        warnings.append("本周出现 NOW 式错误风险，建议暂停非必要卖出。")

    if triggered_reentry:
        level = _max_level(level, "danger")
        warnings.append("存在已触发的回补计划，先复核是否需要按计划买回。")
    if overdue_reentry:
        level = _max_level(level, "danger")
        warnings.append("存在已到期的回补计划，不能继续假装卖出后不用处理。")
    if missing_plan_obligations:
        level = _max_level(level, "danger")
        warnings.append("存在没有具体回补计划的卖出记录，需要补计划或标记为违规复盘。")

    score_result = _discipline_score(
        total_trades=total_count,
        blocker_count=len(blocker_entries),
        now_style_risk_count=len(now_style_risk_entries),
        no_reentry_sell_count=len(no_reentry_sells),
        suspected_sell_fly_count=suspected_sell_fly_count,
        emotional_sell_count=len(emotional_sell_entries),
        a_class_sell_count=len(a_class_sells),
        macro_sell_count=len(macro_sells),
    )

    summary = TradingDisciplineStatsSummary(
        periodStart=period_start.isoformat(),
        periodEnd=current.isoformat(),
        totalTradesThisWeek=total_count,
        sellTrimCountThisWeek=len(sell_trim_entries),
        aClassSellCountThisWeek=len(a_class_sells),
        macroSellCountThisWeek=len(macro_sells),
        noReentryPlanSellCount=len(no_reentry_sells),
        disciplineBlockerCount=len(blocker_entries),
        disciplineWarningCount=len(warning_entries),
        nowStyleRiskCount=len(now_style_risk_entries),
        fomoTradeCount=len(fomo_entries),
        anxietyPanicTradeCount=len(anxiety_panic_entries),
        revengeTradeCount=len(revenge_entries),
        reasonedPlanTradeCount=len(reasoned_plan_entries),
        suspectedSellFlyCount=suspected_sell_fly_count,
        reentryObligationCount=len(reentry_obligations),
        reentryObligationTriggeredCount=len(triggered_reentry),
        reentryObligationOverdueCount=len(overdue_reentry),
        reentryObligationMissingPlanCount=len(missing_plan_obligations),
        overTradingLevel=level,
        disciplineScore=score_result["disciplineScore"],
        disciplineLevel=score_result["disciplineLevel"],
        shouldPauseTrading=score_result["shouldPauseTrading"],
        pauseReason=score_result["pauseReason"],
        mainViolations=score_result["mainViolations"],
        suggestedAction=score_result["suggestedAction"],
        warnings=warnings,
        reminderText=_reminder_text(score_result["disciplineLevel"]),
    )
    return summary.to_dict()


def build_trading_discipline_summary(
    path: Path = CACHE_PATH,
    current_date: date | str | None = None,
) -> dict[str, Any]:
    return build_trading_discipline_stats(path, current_date)


def _entry_in_period(entry: dict, period_start: date, period_end: date) -> bool:
    trade_date = _parse_date(entry.get("trade_date"))
    return bool(trade_date is not None and period_start <= trade_date <= period_end)


def _has_discipline_snapshot(entry: dict) -> bool:
    return bool(str(entry.get("discipline_status") or "").strip())


def _mood_entries(entries: list[dict], moods: set[str]) -> list[dict]:
    return [
        entry
        for entry in entries
        if str(entry.get("decision_mood") or "").strip().lower() in moods
    ]


def _has_now_style_risk(entry: dict) -> bool:
    blockers = [str(item) for item in _json_list(entry.get("blockers"), entry.get("blockers_json"))]
    warnings = [str(item) for item in _json_list(entry.get("warnings"), entry.get("warnings_json"))]
    return NOW_STYLE_RISK_BLOCKER in blockers or any(
        NOW_STYLE_RISK_TEXT_PREFIX in warning for warning in warnings
    )


def _suspected_sell_fly_count(path: Path, period_start: date, period_end: date) -> int:
    try:
        reviews = build_sell_fly_review_results(path, period_end)
    except Exception:
        return 0
    count = 0
    for item in reviews:
        if str(item.get("horizon") or "") != PRIMARY_SELL_FLY_HORIZON:
            continue
        if not bool(item.get("suspectedSellFly")):
            continue
        trade_date = _parse_date(item.get("tradeDate"))
        if trade_date is not None and period_start <= trade_date <= period_end:
            count += 1
    return count


def _open_reentry_obligations(path: Path, current: date) -> list[dict[str, Any]]:
    try:
        obligations = build_post_sell_obligations(path, current)
    except Exception:
        return []
    return [
        item
        for item in obligations
        if str(item.get("status") or "") in {"missing_plan", "triggered", "overdue"}
    ]


def _discipline_score(
    *,
    total_trades: int,
    blocker_count: int,
    now_style_risk_count: int,
    no_reentry_sell_count: int,
    suspected_sell_fly_count: int,
    emotional_sell_count: int,
    a_class_sell_count: int,
    macro_sell_count: int,
) -> dict[str, Any]:
    penalties: list[tuple[str, int]] = []
    _add_penalty(penalties, "纪律阻断", blocker_count, 25)
    _add_penalty(penalties, "NOW 式错误风险", now_style_risk_count, 30)
    _add_penalty(penalties, "无回补计划卖出", no_reentry_sell_count, 22)
    _add_penalty(penalties, "疑似卖飞", suspected_sell_fly_count, 15)
    _add_penalty(penalties, "情绪型卖出/减仓", emotional_sell_count, 10)
    _add_penalty(penalties, "A 类核心股卖出/减仓", a_class_sell_count, 8)
    _add_penalty(penalties, "宏观原因卖出", macro_sell_count, 8)
    if total_trades > 10:
        penalties.append(("本周交易次数超过 10 次", 20))
    elif total_trades > 5:
        penalties.append(("本周交易次数超过 5 次", 10))

    total_penalty = min(100, sum(points for _, points in penalties))
    score = max(0, 100 - total_penalty)
    level = _discipline_level(score, blocker_count, now_style_risk_count, no_reentry_sell_count)
    main_violations = [label for label, _ in sorted(penalties, key=lambda item: item[1], reverse=True)[:4]]
    return {
        "disciplineScore": score,
        "disciplineLevel": level,
        "shouldPauseTrading": level in {"danger", "stop"},
        "pauseReason": _pause_reason(level, main_violations),
        "mainViolations": main_violations,
        "suggestedAction": _suggested_action(level),
    }


def _add_penalty(penalties: list[tuple[str, int]], label: str, count: int, points: int) -> None:
    if count <= 0:
        return
    penalties.append((label, count * points))


def _discipline_level(score: int, blocker_count: int, now_style_risk_count: int, no_reentry_sell_count: int) -> str:
    if score < 35 or now_style_risk_count > 0 or blocker_count >= 2:
        return "stop"
    if score < 60 or blocker_count > 0 or no_reentry_sell_count > 0:
        return "danger"
    if score < 80:
        return "caution"
    return "normal"


def _pause_reason(level: str, violations: list[str]) -> str:
    if level not in {"danger", "stop"}:
        return ""
    if not violations:
        return "本周纪律风险偏高。"
    return "；".join(violations[:3])


def _suggested_action(level: str) -> str:
    return {
        "normal": "纪律正常",
        "caution": "本周操作偏多，降低交易频率",
        "danger": "纪律风险高，暂停非必要交易",
        "stop": "本周停止主动卖出，只允许复核和计划",
    }.get(level, "纪律正常")


def _is_false(value: object) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    return str(value).strip().lower() in {"0", "false", "no", "n", "off"}


def _json_list(list_value: object, json_value: object) -> list:
    if isinstance(list_value, list):
        return list_value
    if list_value:
        return [list_value]
    if not json_value:
        return []
    try:
        import json

        parsed = json.loads(str(json_value))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def _parse_date(value: date | str | object) -> date | None:
    if isinstance(value, date):
        return value
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _max_level(current: str, candidate: str) -> str:
    return candidate if LEVEL_ORDER.get(candidate, 0) > LEVEL_ORDER.get(current, 0) else current


def _reminder_text(level: str) -> str:
    if level == "stop":
        return "本周停止主动卖出，只允许复核、计划和纠错。"
    if level == "danger":
        return "本周纪律风险偏高，建议暂停新增 sell / trim，先复核 thesis、核心仓和回补计划。"
    if level == "caution":
        return "本周操作频率或卖出理由需要降速，先确认是否属于焦虑式操作。"
    return "本周交易纪律正常，继续保持低频、高质量操作。"
