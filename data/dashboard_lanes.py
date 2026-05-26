from __future__ import annotations

import pandas as pd


DASHBOARD_BUY_ACTIONS = {"可小仓分批", "可正常分批"}
DASHBOARD_WAIT_ACTIONS = {"等回踩", "只观察", "财报后复核", "可小仓观察，待关键数据复核后再加仓", "待复核，暂不新增"}
DASHBOARD_BLOCKED_ACTIONS = {"禁止追高", "剔除", "数据不足，需复核", "待复核，暂不新增"}
DASHBOARD_NEAR_VALUATION_STATUSES = {"击球区附近", "回撤买点", "回撤后有吸引力", "合理偏便宜"}

LaneGroup = tuple[str, str, str, list[pd.Series], str]


def row_value(row: pd.Series, key: str) -> object | None:
    value = row.get(key)
    if _is_missing(value):
        return None
    return value


def row_final_action(row: pd.Series) -> str:
    return str(row_value(row, "finalAction") or row_value(row, "action") or "")


def row_decision_lane(row: pd.Series) -> str:
    lane = str(row_value(row, "decisionLane") or "")
    if lane:
        return lane
    action = row_final_action(row)
    if row_is_actionable(row):
        return "actionable"
    if action in DASHBOARD_BLOCKED_ACTIONS:
        return "blocked"
    if action in DASHBOARD_WAIT_ACTIONS:
        return "wait"
    return ""


def row_is_actionable(row: pd.Series) -> bool:
    explicit = row_value(row, "isActionable")
    if explicit is not None:
        explicit_text = str(explicit).lower()
        if explicit_text in {"true", "false"}:
            return explicit_text == "true"
    return row_final_action(row) in DASHBOARD_BUY_ACTIONS and row.get("dataConfidence") in {"medium", "high"}


def row_current_add_text(row: pd.Series) -> str:
    return str(row_value(row, "currentAddLimit") or row_value(row, "maxSuggestedPosition") or "")


def actionable_rows(table: pd.DataFrame) -> list[pd.Series]:
    rows = [
        row
        for _, row in table.iterrows()
        if row_is_actionable(row)
    ]
    return sorted(rows, key=lambda row: row.get("totalScore", 0), reverse=True)


def near_buy_zone_rows(table: pd.DataFrame) -> list[pd.Series]:
    rows = [
        row
        for _, row in table.iterrows()
        if row_decision_lane(row) == "nearBuyZone"
        or (
            row_decision_lane(row) in {"", "wait"}
            and row.get("valuationStatus") in DASHBOARD_NEAR_VALUATION_STATUSES
            and row_final_action(row) not in {"可正常分批", "禁止追高", "剔除"}
        )
    ]
    return sorted(rows, key=lambda row: row.get("totalScore", 0), reverse=True)


def wait_or_confirm_rows(table: pd.DataFrame) -> list[pd.Series]:
    rows = [
        row
        for _, row in table.iterrows()
        if row_decision_lane(row) in {"wait", "review"}
        or (
            not row_value(row, "decisionLane")
            and row_final_action(row) in DASHBOARD_WAIT_ACTIONS
        )
    ]
    return sorted(rows, key=lambda row: row.get("totalScore", 0), reverse=True)


def blocked_or_risky_rows(table: pd.DataFrame) -> list[pd.Series]:
    rows = [
        row
        for _, row in table.iterrows()
        if row_decision_lane(row) == "blocked"
        or row_final_action(row) in DASHBOARD_BLOCKED_ACTIONS
        or row.get("riskRating") in {"高", "高风险"}
        or _numeric(row.get("overheatScore")) >= 60
        or row.get("highRiskFlagCount", 0) > 0
    ]
    return sorted(rows, key=lambda row: (_numeric(row.get("overheatScore")), row.get("highRiskFlagCount", 0)), reverse=True)


def summary_lane_groups(table: pd.DataFrame) -> list[LaneGroup]:
    raw_groups = {
        "actionable": actionable_rows(table),
        "nearBuyZone": near_buy_zone_rows(table),
        "waitOrReview": wait_or_confirm_rows(table),
        "noChaseHighRisk": blocked_or_risky_rows(table),
    }
    priority = ["noChaseHighRisk", "actionable", "nearBuyZone", "waitOrReview"]
    assigned_symbols: set[str] = set()
    exclusive: dict[str, list[pd.Series]] = {key: [] for key in raw_groups}
    for lane_key in priority:
        for row in raw_groups[lane_key]:
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol or symbol in assigned_symbols:
                continue
            assigned_symbols.add(symbol)
            exclusive[lane_key].append(row)
    return [
        ("actionable", "可行动", "可执行小仓或正常分批", exclusive["actionable"], "green"),
        ("nearBuyZone", "接近击球区", "回撤较深但仍需确认", exclusive["nearBuyZone"], "blue"),
        ("waitOrReview", "待确认", "等待更清晰的买点", exclusive["waitOrReview"], "yellow"),
        ("noChaseHighRisk", "风险隔离", "暂不新增，先看原因", exclusive["noChaseHighRisk"], "red"),
    ]


def lane_filter_rows(table: pd.DataFrame, lane_key: str) -> list[pd.Series]:
    for current_key, _title, _subtitle, rows, _color in summary_lane_groups(table):
        if lane_key == current_key:
            return rows
    return [row for _, row in table.iterrows()]


def today_priority_rows(table: pd.DataFrame, limit: int = 5) -> list[tuple[str, pd.Series, str]]:
    items: list[tuple[str, pd.Series, str]] = []
    seen_symbols: set[str] = set()
    for lane_key, _title, _subtitle, rows, color in summary_lane_groups(table):
        added_for_lane = 0
        for row in rows:
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol or symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            items.append((lane_key, row, color))
            added_for_lane += 1
            if len(items) >= limit:
                return items
            if added_for_lane >= 2:
                break
    return items


def _numeric(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value)
    except Exception:
        return False
