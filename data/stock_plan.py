from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from data.prices import CACHE_PATH


NUMERIC_PLAN_FIELDS = [
    "target_position_pct",
    "planned_position_pct",
    "core_position_min_pct",
    "trading_position_max_pct",
    "first_buy_price",
    "second_buy_price",
    "third_buy_price",
    "no_chase_above",
    "fair_value_low",
    "fair_value_high",
    "tranche_buy_low",
    "tranche_buy_high",
    "heavy_buy_below",
    "max_position_pct",
    "max_shares",
    "target_sell_price",
    "stop_loss_price",
    "target_alert_price",
    "planned_amount",
    "planned_shares",
    "near_threshold_pct",
]

TEXT_PLAN_FIELDS = [
    "plan_type",
    "plan_status",
    "alert_mode",
    "position_class",
    "classification_note",
    "thesis",
    "follow_up_plan",
    "stop_adding_condition",
    "invalidation_condition",
    "earnings_review_points",
    "event_name",
    "event_date",
    "exit_if_no_reaction",
    "notes",
    "buy_plan_tranches_json",
]

VALID_PLAN_TYPES = {"starter_position", "ladder_buy", "event_trade", "watch_only"}
VALID_PLAN_STATUSES = {"active", "triggered", "paused", "completed", "cancelled", "expired"}
INACTIVE_PLAN_STATUSES = {"completed", "cancelled", "expired"}
VALID_ALERT_MODES = {"price_below", "price_near", "radar_pullback"}
BUY_PLAN_STATUS_LABELS = {
    "no_plan": "暂无计划",
    "waiting": "等待触发",
    "near_trigger": "接近触发",
    "triggered": "已触发",
    "over_allocated": "需复核",
    "stale_or_missing_data": "数据需复核",
    "executed": "已执行",
    "needs_review": "需复核",
    "paused": "已暂停",
    "completed": "已执行",
    "cancelled": "已取消",
    "expired": "已失效",
}


class StockPlanStore:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        numeric_columns = ",\n                    ".join(f"{field} REAL" for field in NUMERIC_PLAN_FIELDS)
        text_columns = ",\n                    ".join(f"{field} TEXT" for field in TEXT_PLAN_FIELDS)
        with self.connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS stock_action_plans (
                    ticker TEXT PRIMARY KEY,
                    {numeric_columns},
                    {text_columns},
                    created_at TEXT,
                    material_updated_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_columns(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(stock_action_plans)").fetchall()}
        for field in NUMERIC_PLAN_FIELDS:
            if field not in existing:
                conn.execute(f"ALTER TABLE stock_action_plans ADD COLUMN {field} REAL")
        for field in TEXT_PLAN_FIELDS:
            if field not in existing:
                conn.execute(f"ALTER TABLE stock_action_plans ADD COLUMN {field} TEXT")
        if "created_at" not in existing:
            conn.execute("ALTER TABLE stock_action_plans ADD COLUMN created_at TEXT")
        if "material_updated_at" not in existing:
            conn.execute("ALTER TABLE stock_action_plans ADD COLUMN material_updated_at TEXT")
        if "updated_at" not in existing:
            conn.execute("ALTER TABLE stock_action_plans ADD COLUMN updated_at TEXT")

    def get_plan(self, ticker: str) -> dict:
        with self.connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM stock_action_plans WHERE ticker = ?",
                (ticker.upper(),),
            )
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []

        plan = _empty_plan(ticker)
        if not row:
            return plan
        for column, value in zip(columns, row):
            plan[column] = value
        plan["buy_plan_tranches"] = _load_json_list(plan.get("buy_plan_tranches_json"))
        return plan

    def save_plan(self, ticker: str, values: dict) -> dict:
        cleaned = _empty_plan(ticker)
        for field in NUMERIC_PLAN_FIELDS:
            cleaned[field] = _to_number(values.get(field, values.get(_camel_case(field))))
        for field in TEXT_PLAN_FIELDS:
            if field == "position_class":
                cleaned[field] = _clean_position_class(values.get(field, values.get("positionClass")))
            elif field == "plan_type":
                cleaned[field] = _clean_plan_type(values.get(field, values.get("planType")))
            elif field == "plan_status":
                cleaned[field] = _clean_plan_status(values.get(field, values.get("planStatus")))
            elif field == "alert_mode":
                cleaned[field] = _clean_alert_mode(values.get(field, values.get("alertMode")))
            elif field == "buy_plan_tranches_json":
                cleaned[field] = _clean_json_text(values.get(field, values.get("buy_plan_tranches")))
            else:
                cleaned[field] = _clean_text(values.get(field, values.get(_camel_case(field))))
        now = datetime.now(timezone.utc).isoformat()
        existing_created_at = _clean_text(values.get("created_at"))
        if not existing_created_at:
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT created_at FROM stock_action_plans WHERE ticker = ?",
                    (ticker.upper(),),
                ).fetchone()
            existing_created_at = _clean_text(row[0]) if row and row[0] else ""
        cleaned["created_at"] = existing_created_at or now
        existing_material_updated_at = _clean_text(values.get("material_updated_at"))
        if not existing_material_updated_at:
            existing_material_updated_at = _clean_text(values.get("materialUpdatedAt"))
        cleaned["material_updated_at"] = existing_material_updated_at or now
        cleaned["updated_at"] = now

        fields = [*NUMERIC_PLAN_FIELDS, *TEXT_PLAN_FIELDS, "created_at", "material_updated_at", "updated_at"]
        columns = ["ticker", *fields]
        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(f"{field} = excluded.{field}" for field in fields)
        with self.connect() as conn:
            conn.execute(
                f"""
                INSERT INTO stock_action_plans ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(ticker) DO UPDATE SET
                    {assignments}
                """,
                (ticker.upper(), *(cleaned[field] for field in fields)),
            )
        cleaned["buy_plan_tranches"] = _load_json_list(cleaned.get("buy_plan_tranches_json"))
        return cleaned

    def clear_buy_zone_override(self, ticker: str) -> dict:
        plan = self.get_plan(ticker)
        for field in (
            "no_chase_above",
            "fair_value_low",
            "fair_value_high",
            "tranche_buy_low",
            "tranche_buy_high",
            "heavy_buy_below",
        ):
            plan[field] = None
        return self.save_plan(ticker, plan)

    def close_plan(self, ticker: str, status: str, note: str = "") -> dict:
        clean_status = _clean_plan_status(status)
        if clean_status not in INACTIVE_PLAN_STATUSES:
            raise ValueError("plan close status must be completed, cancelled, or expired")
        plan = self.get_plan(ticker)
        if not _has_plan_content(plan):
            return plan
        existing_notes = _clean_text(plan.get("notes"))
        clean_note = _clean_text(note)
        if clean_note:
            plan["notes"] = "\n".join(item for item in (existing_notes, clean_note) if item)
        plan["plan_status"] = clean_status
        return self.save_plan(ticker, plan)


def _empty_plan(ticker: str) -> dict:
    return {
        "ticker": ticker.upper(),
        **{field: None for field in NUMERIC_PLAN_FIELDS},
        **{field: "" for field in TEXT_PLAN_FIELDS},
        "buy_plan_tranches": [],
        "created_at": None,
        "material_updated_at": None,
        "updated_at": None,
    }


def is_active_buy_plan(plan: dict) -> bool:
    if not _has_plan_content(plan):
        return False
    status = _clean_plan_status(plan.get("plan_status") or plan.get("planStatus"))
    return status not in INACTIVE_PLAN_STATUSES


def _has_plan_content(plan: dict | None) -> bool:
    if not isinstance(plan, dict):
        return False
    if _clean_plan_type(plan.get("plan_type") or plan.get("planType")):
        return True
    if _to_number(plan.get("target_alert_price") or plan.get("targetAlertPrice")) is not None:
        return True
    tranches = plan.get("buy_plan_tranches") or plan.get("buyPlanTranches") or []
    return isinstance(tranches, list) and bool(tranches)


def get_buy_plan_status(
    plan: dict,
    *,
    current_price: object = None,
    is_stale: bool = False,
    prior_level_quantities: dict[str, float] | None = None,
) -> dict:
    levels = _plan_levels_with_remaining(plan, prior_level_quantities or {})
    plan_type = _clean_plan_type(plan.get("plan_type") or plan.get("planType"))
    plan_status = _clean_plan_status(plan.get("plan_status") or plan.get("planStatus"))
    if plan_status in {"paused", "completed", "cancelled", "expired"}:
        return _plan_status(plan_status, levels[0] if levels else _quick_target_level(plan), None, BUY_PLAN_STATUS_LABELS[plan_status])
    if not plan_type and not levels and _to_number(plan.get("target_alert_price")) is None:
        return _plan_status("no_plan", None, None, "暂无计划")
    if is_stale:
        return _plan_status("stale_or_missing_data", None, None, "价格数据缺失或过期")
    if not _clean_text(plan.get("thesis")):
        return _plan_status("needs_review", levels[0] if levels else _quick_target_level(plan), None, "计划缺少 thesis / 买入理由")
    if not levels:
        quick_level = _quick_target_level(plan)
        if not quick_level:
            return _plan_status("needs_review", None, None, "计划缺少目标提醒价")
        levels = [quick_level]
    next_level = next(
        (
            level
            for level in levels
            if level.get("remaining_quantity") is None or (level.get("remaining_quantity") or 0) > 0
        ),
        None,
    )
    if next_level is None:
        return _plan_status("executed", levels[-1], 0, "计划档位已执行完")
    price = _to_number(current_price)
    trigger = next_level.get("trigger_price")
    if price is None or trigger is None:
        return _plan_status("stale_or_missing_data", next_level, None, "缺少当前价或触发价")
    distance_pct = (price - trigger) / trigger * 100 if trigger else None
    if price <= trigger:
        return _plan_status("triggered", next_level, distance_pct, "当前价已触发计划档位")
    alert_mode = _clean_alert_mode(plan.get("alert_mode") or plan.get("alertMode"))
    threshold = _to_number(plan.get("near_threshold_pct")) or 2.0
    if alert_mode == "price_near" and abs(distance_pct or 0) <= threshold:
        return _plan_status("near_trigger", next_level, distance_pct, f"距离目标提醒价 {threshold:g}% 以内")
    if distance_pct is not None and distance_pct <= 3:
        return _plan_status("near_trigger", next_level, distance_pct, "距离计划档位 3% 以内")
    return _plan_status("waiting", next_level, distance_pct, "等待触发")


def _plan_levels_with_remaining(plan: dict, prior: dict[str, float]) -> list[dict]:
    raw = plan.get("buy_plan_tranches") or []
    levels: list[dict] = []
    for index, item in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        label = _clean_text(item.get("label")) or f"第 {index + 1} 档"
        trigger = _to_number(item.get("trigger_price", item.get("price")))
        qty = _to_number(item.get("planned_quantity", item.get("shares")))
        bought = float(prior.get(label) or 0)
        remaining = None if qty is None else max(0.0, qty - bought)
        levels.append(
            {
                "label": label,
                "trigger_price": trigger,
                "planned_quantity": qty,
                "planned_amount": _to_number(item.get("planned_amount", item.get("amount"))),
                "remaining_quantity": remaining,
                "note": _clean_text(item.get("note")),
            }
        )
    return levels


def _quick_target_level(plan: dict) -> dict | None:
    trigger = _to_number(plan.get("target_alert_price") or plan.get("targetAlertPrice"))
    if trigger is None:
        return None
    qty = _to_number(plan.get("planned_shares") or plan.get("plannedShares"))
    return {
        "label": "目标提醒价",
        "trigger_price": trigger,
        "planned_quantity": qty,
        "planned_amount": _to_number(plan.get("planned_amount") or plan.get("plannedAmount")),
        "remaining_quantity": qty,
        "note": "快捷计划",
    }


def _plan_status(status: str, level: dict | None, distance_pct: float | None, message: str) -> dict:
    return {
        "status": status,
        "label": BUY_PLAN_STATUS_LABELS.get(status, status),
        "level": level,
        "distance_pct": distance_pct,
        "message": message,
    }


def _to_number(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_position_class(value) -> str:
    text = _clean_text(value).upper()
    return text if text in {"A", "B", "C"} else ""


def _clean_plan_type(value) -> str:
    text = _clean_text(value).lower()
    return text if text in VALID_PLAN_TYPES else ""


def _clean_plan_status(value) -> str:
    text = _clean_text(value).lower()
    return text if text in VALID_PLAN_STATUSES else "active"


def _clean_alert_mode(value) -> str:
    text = _clean_text(value).lower()
    return text if text in VALID_ALERT_MODES else "price_below"


def _camel_case(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def _clean_json_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return ""
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json_list(value) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []
