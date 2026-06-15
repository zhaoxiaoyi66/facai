from __future__ import annotations

import calendar
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


HKT_TIMEZONE = "Asia/Hong_Kong"
TRADE_ACTIONS = {"buy", "add", "sell", "trim"}
BUY_ACTIONS = {"buy", "add"}
SELL_ACTIONS = {"sell", "trim"}
FREQUENCY_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def group_trade_decisions(
    trades: list[dict[str, Any]],
    *,
    merge_window_minutes: int = 30,
    timezone: str = HKT_TIMEZONE,
) -> list[dict[str, Any]]:
    window = timedelta(minutes=max(1, int(merge_window_minutes)))
    groups: list[dict[str, Any]] = []
    for trade in sorted(_trade_entries(trades), key=lambda item: _trade_timestamp_hkt(item, timezone)):
        ticker = _symbol(trade)
        side = _side(trade)
        timestamp = _trade_timestamp_hkt(trade, timezone)
        matched = None
        for group in reversed(groups):
            if group["ticker"] != ticker or group["side"] != side:
                continue
            if timestamp - group["last_ts"] <= window:
                matched = group
                break
        if matched is None:
            groups.append(
                {
                    "ticker": ticker,
                    "side": side,
                    "first_ts": timestamp,
                    "last_ts": timestamp,
                    "entries": [trade],
                    "record_count": 1,
                }
            )
        else:
            matched["last_ts"] = timestamp
            matched["entries"].append(trade)
            matched["record_count"] += 1
    return groups


def build_daily_trade_activity(
    target_date: date | str,
    trades: list[dict[str, Any]],
    *,
    timezone: str = HKT_TIMEZONE,
) -> dict[str, Any]:
    day = _parse_date(target_date)
    day_trades = [
        trade
        for trade in _trade_entries(trades)
        if _trade_date_hkt(trade, timezone) == day
    ]
    day_trades.sort(key=lambda item: _trade_timestamp_hkt(item, timezone))
    decisions = group_trade_decisions(day_trades, timezone=timezone)
    buy_count = sum(1 for trade in day_trades if _side(trade) == "buy")
    sell_count = sum(1 for trade in day_trades if _side(trade) == "sell")
    ticker_counts: dict[str, int] = defaultdict(int)
    side_by_ticker: dict[str, set[str]] = defaultdict(set)
    for decision in decisions:
        ticker_counts[decision["ticker"]] += 1
        side_by_ticker[decision["ticker"]].add(decision["side"])
    reverse_trade_count = sum(1 for sides in side_by_ticker.values() if {"buy", "sell"}.issubset(sides))
    loss_sell_trades = [trade for trade in day_trades if _side(trade) == "sell" and _realized_pnl(trade) is not None and float(_realized_pnl(trade) or 0) < 0]
    first_loss_ts = _trade_timestamp_hkt(loss_sell_trades[0], timezone) if loss_sell_trades else None
    trades_after_loss_count = (
        sum(1 for trade in day_trades if _trade_timestamp_hkt(trade, timezone) > first_loss_ts)
        if first_loss_ts
        else 0
    )
    late_night_trade_count = sum(1 for trade in day_trades if 0 <= _trade_timestamp_hkt(trade, timezone).hour < 5)
    liquidation_count = sum(1 for trade in day_trades if str(trade.get("action_type") or "").lower() == "sell")
    high_risk_advisory_count = sum(1 for trade in day_trades if _is_high_risk_advisory(trade))
    total_notional = sum(_notional(trade) for trade in day_trades)
    realized_pnl_values = [_realized_pnl(trade) for trade in day_trades if _realized_pnl(trade) is not None]
    realized_pnl_today = sum(float(value or 0) for value in realized_pnl_values) if realized_pnl_values else None
    base_activity = {
        "date_hkt": day.isoformat(),
        "trade_record_count": len(day_trades),
        "trade_decision_count": len(decisions),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "unique_ticker_count": len({_symbol(trade) for trade in day_trades}),
        "same_ticker_repeat_count": sum(max(0, count - 1) for count in ticker_counts.values()),
        "reverse_trade_count": reverse_trade_count,
        "loss_sell_count": len(loss_sell_trades),
        "trades_after_loss_count": trades_after_loss_count,
        "late_night_trade_count": late_night_trade_count,
        "liquidation_count": liquidation_count,
        "high_risk_advisory_count": high_risk_advisory_count,
        "total_notional": round(total_notional, 2),
        "realized_pnl_today": None if realized_pnl_today is None else round(realized_pnl_today, 2),
        "decisions": decisions,
        "trades": day_trades,
    }
    classification = classify_daily_trade_frequency(base_activity)
    base_activity.update(classification)
    return base_activity


def classify_daily_trade_frequency(activity: dict[str, Any]) -> dict[str, Any]:
    decision_count = int(activity.get("trade_decision_count") or 0)
    if decision_count >= 8:
        level = "CRITICAL"
    elif decision_count >= 5:
        level = "HIGH"
    elif decision_count >= 3:
        level = "MEDIUM"
    else:
        level = "LOW"
    reasons: list[str] = []
    if int(activity.get("same_ticker_repeat_count") or 0) >= 2:
        reasons.append("同一 ticker 当日多次操作，建议复核是否为冲动交易。")
        level = _raise_level(level)
    if int(activity.get("reverse_trade_count") or 0) > 0:
        reasons.append("当日出现同一 ticker 买卖反向交易。")
        level = _raise_level(level)
    if int(activity.get("trades_after_loss_count") or 0) >= 2:
        reasons.append("亏损卖出后仍继续交易，建议暂停复盘。")
        level = _raise_level(level)
    if int(activity.get("late_night_trade_count") or 0) >= 2:
        reasons.append("深夜 HKT 00:00-05:00 交易较多。")
        level = _raise_level(level)
    if int(activity.get("high_risk_advisory_count") or 0) >= 2:
        reasons.append("当日高风险 advisory 次数较多。")
        level = _raise_level(level)
    if not reasons and decision_count:
        reasons.append("按交易决策次数评估。")
    return {
        "advisory_level": level,
        "advisory_text": _advisory_text(level),
        "advisory_reasons": reasons,
    }


def build_monthly_trade_calendar(
    year: int,
    month: int,
    trades: list[dict[str, Any]],
    *,
    timezone: str = HKT_TIMEZONE,
) -> dict[str, Any]:
    _, days_in_month = calendar.monthrange(int(year), int(month))
    days = [
        build_daily_trade_activity(date(int(year), int(month), day), trades, timezone=timezone)
        for day in range(1, days_in_month + 1)
    ]
    trade_days = [day for day in days if int(day["trade_record_count"]) > 0]
    high_days = [day for day in days if day["advisory_level"] in {"HIGH", "CRITICAL"}]
    total_decisions = sum(int(day["trade_decision_count"]) for day in days)
    return {
        "year": int(year),
        "month": int(month),
        "days": days,
        "summary": {
            "trade_day_count": len(trade_days),
            "monthly_trade_decision_count": total_decisions,
            "high_frequency_day_count": len(high_days),
            "max_daily_decision_count": max((int(day["trade_decision_count"]) for day in days), default=0),
            "avg_daily_decision_count": round(total_decisions / max(1, len(days)), 2),
            "trade_record_count": sum(int(day["trade_record_count"]) for day in days),
        },
    }


def daily_activity_snapshot_fields(activity: dict[str, Any], *, user_confirmed: bool = False) -> dict[str, Any]:
    return {
        "dailyTradeRecordCount": int(activity.get("trade_record_count") or 0),
        "dailyTradeDecisionCount": int(activity.get("trade_decision_count") or 0),
        "dailyTradeAdvisoryLevel": str(activity.get("advisory_level") or "LOW"),
        "dailyTradeAdvisoryText": str(activity.get("advisory_text") or ""),
        "dailyTradeAdvisoryReasons": list(activity.get("advisory_reasons") or []),
        "userConfirmedDailyTradeAdvisory": bool(user_confirmed),
    }


def activity_level_label(level: object) -> str:
    return {
        "LOW": "LOW｜节奏正常",
        "MEDIUM": "MEDIUM｜偏活跃",
        "HIGH": "HIGH｜频率较高",
        "CRITICAL": "CRITICAL｜疑似过度交易",
    }.get(str(level or "").upper(), "LOW｜节奏正常")


def _raise_level(level: str) -> str:
    index = FREQUENCY_LEVELS.index(level)
    return FREQUENCY_LEVELS[min(index + 1, len(FREQUENCY_LEVELS) - 1)]


def _advisory_text(level: str) -> str:
    return {
        "LOW": "今日交易节奏正常。",
        "MEDIUM": "今日操作偏活跃，下一笔交易前建议复核是否必要。",
        "HIGH": "今日交易频率较高，建议暂停 30 分钟，避免冲动交易。",
        "CRITICAL": "今日疑似过度交易，建议停止新增操作，仅记录已发生交易并复盘原因。",
    }[level]


def _trade_entries(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [trade for trade in trades if str(trade.get("action_type") or "").strip().lower() in TRADE_ACTIONS]


def _side(trade: dict[str, Any]) -> str:
    return "buy" if str(trade.get("action_type") or "").strip().lower() in BUY_ACTIONS else "sell"


def _symbol(trade: dict[str, Any]) -> str:
    return str(trade.get("symbol") or trade.get("ticker") or "").strip().upper()


def _trade_date_hkt(trade: dict[str, Any], timezone: str) -> date:
    value = trade.get("trade_date") or trade.get("tradeDate") or trade.get("created_at")
    parsed = _parse_datetime(value, timezone)
    return parsed.date()


def _trade_timestamp_hkt(trade: dict[str, Any], timezone: str) -> datetime:
    zone = ZoneInfo(timezone)
    trade_day = _trade_date_hkt(trade, timezone)
    created = _parse_datetime(trade.get("created_at") or trade.get("createdAt") or trade.get("trade_date"), timezone)
    return datetime.combine(trade_day, created.timetz().replace(tzinfo=None), tzinfo=zone)


def _parse_date(value: date | str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return _parse_datetime(value, HKT_TIMEZONE).date()


def _parse_datetime(value: object, timezone: str) -> datetime:
    zone = ZoneInfo(timezone)
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time(12, 0))
    else:
        text = str(value or "").strip()
        if not text:
            dt = datetime.now(zone)
        else:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                dt = datetime.combine(date.fromisoformat(text[:10]), time(12, 0))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=zone)
    return dt.astimezone(zone)


def _notional(trade: dict[str, Any]) -> float:
    quantity = _number(trade.get("quantity"))
    price = _number(trade.get("price"))
    return float(quantity or 0) * float(price or 0)


def _realized_pnl(trade: dict[str, Any]) -> float | None:
    for key in ("realized_pnl", "realizedPnl", "realized_pnl_today", "pnl", "matched_realized_pnl"):
        number = _number(trade.get(key))
        if number is not None:
            return number
    return None


def _is_high_risk_advisory(trade: dict[str, Any]) -> bool:
    values = [
        trade.get("advisory_level"),
        trade.get("advisoryLevel"),
        trade.get("sell_warning_level"),
        trade.get("sellWarningLevel"),
    ]
    return any(str(value or "").strip().upper() in {"HIGH_RISK", "CRITICAL", "DANGER"} for value in values)


def _number(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def snapshot_json(activity: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in activity.items()
        if key not in {"trades", "decisions"}
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
