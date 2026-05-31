from __future__ import annotations

from typing import Any


DATA_BLOCKING_CATEGORIES = {"cache_missing", "missing_price", "final_decision_error"}
DATA_REVIEW_CATEGORIES = {
    "missing_history",
    "stale_history",
    "stale_quote",
    "portfolio_missing_price",
    "outcome_missing",
}
PRECISE_BUY_ZONE_STATES = {"tranche_buy", "heavy_buy", "below_heavy_buy"}
BUY_ZONE_PRECISION_BLOCKERS = {
    "no_chase",
    "invalid_zone",
    "invalid_manual_override",
    "data_insufficient",
    "low_confidence_zone",
    "unsupported_buy_zone_model",
}


def build_decision_readiness(
    symbol: str,
    *,
    data_health: dict[str, Any] | None = None,
    final_decision: Any = None,
    buy_zone: Any = None,
    sync_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ticker = str(symbol or "").strip().upper()
    data_blockers, data_reviews = _data_health_reasons(ticker, data_health or {})
    final_blockers, final_reviews = _final_decision_reasons(final_decision)
    precision_blockers, precision_reviews = _buy_zone_reasons(buy_zone)
    can_sync_trade = bool((sync_policy or {"canSync": True}).get("canSync", True))
    sync_reason = str((sync_policy or {}).get("reason") or "").strip()

    blocking_reasons = [*data_blockers, *final_blockers]
    review_reasons = [*data_reviews, *final_reviews, *precision_reviews]
    if not can_sync_trade and sync_reason:
        blocking_reasons.append(_reason("trade_sync_blocked", ticker, sync_reason))

    can_decide = not data_blockers and _final_decision_present(final_decision)
    can_show_precise_buy_zone = can_decide and not precision_blockers
    status = "blocked" if blocking_reasons or precision_blockers and not can_decide else ("review_required" if review_reasons or precision_blockers else "ready")

    return {
        "symbol": ticker,
        "status": status,
        "canDecide": can_decide,
        "canShowPreciseBuyZone": can_show_precise_buy_zone,
        "canSyncTrade": can_sync_trade,
        "blockingDataReasons": blocking_reasons,
        "reviewRequiredReasons": review_reasons,
        "precisionBlockedReasons": precision_blockers,
    }


def _data_health_reasons(symbol: str, summary: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for issue in summary.get("topIssues") or []:
        if not isinstance(issue, dict):
            continue
        issue_symbol = str(issue.get("symbol") or "").strip().upper()
        category = str(issue.get("category") or "").strip()
        if issue_symbol and issue_symbol != symbol:
            continue
        reason = _reason(category, issue_symbol or symbol, str(issue.get("message") or category))
        if category in DATA_BLOCKING_CATEGORIES:
            blockers.append(reason)
        elif category in DATA_REVIEW_CATEGORIES:
            reviews.append(reason)
    return blockers, reviews


def _final_decision_reasons(final_decision: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if final_decision is None:
        return [_reason("final_decision_missing", "", "缺少 finalDecision，不能作为当前操作依据。")], []
    blockers = [
        _reason(str(item), "", _final_reason_message(str(item), blocking=True))
        for item in _list_value(final_decision, "blockReasons", "block_reasons")
    ]
    reviews = [
        _reason(str(item), "", _final_reason_message(str(item), blocking=False))
        for item in _list_value(final_decision, "reviewReasons", "review_reasons")
    ]
    return blockers, reviews


def _buy_zone_reasons(buy_zone: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if buy_zone is None:
        return [_reason("buy_zone_missing", "", "缺少买区结果，不能展示精确买点。")], []
    zone = str(_value(buy_zone, "currentZone", "current_zone", default="") or "")
    confidence = str(_value(buy_zone, "confidence", default="") or "").lower()
    validation_errors = _list_value(buy_zone, "validationErrors", "validation_errors")
    blockers: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    if zone in BUY_ZONE_PRECISION_BLOCKERS or zone not in PRECISE_BUY_ZONE_STATES:
        blockers.append(_reason("buy_zone_precision_blocked", "", _buy_zone_message(zone)))
    if confidence == "low":
        reviews.append(_reason("buy_zone_low_confidence", "", "买区置信度偏低，需要复核后再使用精确价格。"))
    for item in validation_errors:
        reviews.append(_reason(str(item), "", _buy_zone_validation_message(str(item))))
    return blockers, reviews


def _final_decision_present(final_decision: Any) -> bool:
    return bool(str(_value(final_decision, "finalAction", "final_action", default="") or "").strip())


def _buy_zone_message(zone: str) -> str:
    labels = {
        "no_chase": "当前处于禁止追高或等待回踩状态，不展示精确买点。",
        "invalid_zone": "买区校验异常，不能展示精确买点。",
        "invalid_manual_override": "手动买区参数异常，不能展示精确买点。",
        "data_insufficient": "买区核心输入不足，不能展示精确买点。",
        "low_confidence_zone": "买区置信度不足，不能展示精确买点。",
        "unsupported_buy_zone_model": "暂无可用买区模型，不能展示精确买点。",
    }
    return labels.get(zone, "当前买区不属于可执行精确买点区。")


def _buy_zone_validation_message(reason: str) -> str:
    messages = {
        "data_confidence_low": "数据置信度偏低，精确买点需要复核。",
        "buy_zone_model_not_supported": "暂无可用买区模型，不能用假精确价格替代。",
    }
    return messages.get(reason, reason)


def _final_reason_message(reason: str, *, blocking: bool) -> str:
    messages = {
        "buy_zone": "finalDecision 已被买区守门阻断。",
        "data_confidence": "finalDecision 已被数据置信度阻断。",
        "quality": "质量或评分条件需要复核。",
    }
    if reason in messages:
        return messages[reason]
    return f"finalDecision {'阻断' if blocking else '要求复核'}：{reason}"


def _list_value(source: Any, *names: str) -> list[Any]:
    value = _value(source, *names, default=[])
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _value(source: Any, *names: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        for name in names:
            if name in source:
                return source[name]
        return default
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    return default


def _reason(category: str, symbol: str, message: str) -> dict[str, Any]:
    return {"category": category, "symbol": symbol, "message": message}
