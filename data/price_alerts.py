from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from data.market_context import build_market_context
from data.prices import CACHE_PATH


ALERT_DIRECTIONS = {"below", "above", "near"}
LINKED_PLAN_TYPES = {"buy_plan", "sell_plan", "risk_review", "manual"}
DEFAULT_NEAR_THRESHOLD_PCT = 2.0
PRICE_ALERT_FIELD_LABELS = {
    "triggerPrice": "触发价格",
}


class PriceAlertStore:
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
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    trigger_direction TEXT NOT NULL,
                    trigger_price REAL NOT NULL,
                    alert_reason TEXT,
                    alert_type TEXT,
                    linked_plan_type TEXT,
                    source TEXT,
                    source_id TEXT,
                    near_threshold_pct REAL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    triggered_at TEXT,
                    archived_at TEXT,
                    deleted_at TEXT,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            existing = {row[1] for row in conn.execute("PRAGMA table_info(price_alerts)").fetchall()}
            if "deleted_at" not in existing:
                conn.execute("ALTER TABLE price_alerts ADD COLUMN deleted_at TEXT")
            if "alert_type" not in existing:
                conn.execute("ALTER TABLE price_alerts ADD COLUMN alert_type TEXT")
            if "source" not in existing:
                conn.execute("ALTER TABLE price_alerts ADD COLUMN source TEXT")
            if "source_id" not in existing:
                conn.execute("ALTER TABLE price_alerts ADD COLUMN source_id TEXT")
            if "near_threshold_pct" not in existing:
                conn.execute("ALTER TABLE price_alerts ADD COLUMN near_threshold_pct REAL")

    def create_alert(
        self,
        symbol: str,
        *,
        triggerDirection: str,
        triggerPrice: float | int | str,
        alertReason: str = "",
        alertType: str = "PRICE",
        linkedPlanType: str = "manual",
        source: str = "",
        sourceId: str = "",
        nearThresholdPct: float | int | str | None = None,
        isActive: bool = True,
        note: str = "",
    ) -> dict[str, Any]:
        now = _now_iso()
        cleaned = {
            "symbol": _symbol(symbol),
            "trigger_direction": _direction(triggerDirection),
            "trigger_price": _required_number(triggerPrice, "triggerPrice"),
            "alert_reason": _clean_text(alertReason),
            "alert_type": _clean_text(alertType) or "PRICE",
            "linked_plan_type": _linked_plan_type(linkedPlanType),
            "source": _clean_text(source),
            "source_id": _clean_text(sourceId),
            "near_threshold_pct": _near_threshold_pct(nearThresholdPct),
            "is_active": 1 if isActive else 0,
            "note": _clean_text(note),
            "created_at": now,
            "updated_at": now,
        }
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO price_alerts (
                    symbol,
                    trigger_direction,
                    trigger_price,
                    alert_reason,
                    alert_type,
                    linked_plan_type,
                    source,
                    source_id,
                    near_threshold_pct,
                    is_active,
                    note,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cleaned["symbol"],
                    cleaned["trigger_direction"],
                    cleaned["trigger_price"],
                    cleaned["alert_reason"],
                    cleaned["alert_type"],
                    cleaned["linked_plan_type"],
                    cleaned["source"],
                    cleaned["source_id"],
                    cleaned["near_threshold_pct"],
                    cleaned["is_active"],
                    cleaned["note"],
                    cleaned["created_at"],
                    cleaned["updated_at"],
                ),
            )
            alert_id = int(cursor.lastrowid)
        return self.get_alert(alert_id) or {}

    def find_source_alert(
        self,
        *,
        symbol: str,
        alertType: str,
        source: str,
        sourceId: str,
        include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        clauses = [
            "symbol = ?",
            "alert_type = ?",
            "source = ?",
            "source_id = ?",
        ]
        params: list[object] = [_symbol(symbol), _clean_text(alertType) or "PRICE", _clean_text(source), _clean_text(sourceId)]
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                SELECT *
                FROM price_alerts
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                params,
            )
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return _row_to_alert(row, columns) if row else None

    def upsert_source_alert(
        self,
        symbol: str,
        *,
        alertType: str,
        source: str,
        sourceId: str,
        triggerDirection: str,
        triggerPrice: float | int | str,
        alertReason: str = "",
        linkedPlanType: str = "manual",
        nearThresholdPct: float | int | str | None = None,
        isActive: bool = True,
        note: str = "",
    ) -> dict[str, Any]:
        existing = self.find_source_alert(symbol=symbol, alertType=alertType, source=source, sourceId=sourceId)
        if existing:
            updated = self.update_alert(
                int(existing["id"]),
                triggerPrice=triggerPrice,
                triggerDirection=triggerDirection,
                alertReason=alertReason,
                linkedPlanType=linkedPlanType,
                nearThresholdPct=nearThresholdPct,
                alertType=alertType,
                source=source,
                sourceId=sourceId,
                note=note,
            )
            return self.set_active(int(updated["id"]), isActive) if updated else {}
        return self.create_alert(
            symbol,
            triggerDirection=triggerDirection,
            triggerPrice=triggerPrice,
            alertReason=alertReason,
            alertType=alertType,
            linkedPlanType=linkedPlanType,
            source=source,
            sourceId=sourceId,
            nearThresholdPct=nearThresholdPct,
            isActive=isActive,
            note=note,
        )

    def get_alert(self, alert_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM price_alerts WHERE id = ?", (alert_id,))
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return _row_to_alert(row, columns) if row else None

    def list_alerts(
        self,
        symbol: str | None = None,
        *,
        include_archived: bool = True,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[object] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(_symbol(symbol))
        if not include_archived:
            clauses.append("archived_at IS NULL")
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                SELECT *
                FROM price_alerts
                {where}
                ORDER BY triggered_at DESC, created_at DESC, id DESC
                """,
                params,
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_alert(row, columns) for row in rows]

    def mark_triggered(self, alert_id: int, triggered_at: str | None = None) -> dict[str, Any]:
        timestamp = triggered_at or _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE price_alerts
                SET triggered_at = COALESCE(triggered_at, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (timestamp, _now_iso(), alert_id),
            )
        return self.get_alert(alert_id) or {}

    def update_alert(
        self,
        alert_id: int,
        *,
        triggerPrice: float | int | str | None = None,
        triggerDirection: str | None = None,
        alertReason: str | None = None,
        alertType: str | None = None,
        linkedPlanType: str | None = None,
        source: str | None = None,
        sourceId: str | None = None,
        nearThresholdPct: float | int | str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_alert(alert_id)
        if not current:
            return {}
        fields = {
            "trigger_direction": _direction(triggerDirection) if triggerDirection is not None else current["triggerDirection"],
            "trigger_price": _required_number(triggerPrice, "triggerPrice") if triggerPrice is not None else current["triggerPrice"],
            "alert_reason": _clean_text(alertReason) if alertReason is not None else current["alertReason"],
            "alert_type": _clean_text(alertType) if alertType is not None else current["alertType"],
            "linked_plan_type": _linked_plan_type(linkedPlanType) if linkedPlanType is not None else current["linkedPlanType"],
            "source": _clean_text(source) if source is not None else current["source"],
            "source_id": _clean_text(sourceId) if sourceId is not None else current["sourceId"],
            "near_threshold_pct": _near_threshold_pct(nearThresholdPct) if nearThresholdPct is not None else current["nearThresholdPct"],
            "note": _clean_text(note) if note is not None else current["note"],
            "updated_at": _now_iso(),
        }
        changed_trigger = (
            fields["trigger_direction"] != current["triggerDirection"]
            or fields["trigger_price"] != current["triggerPrice"]
            or fields["near_threshold_pct"] != current["nearThresholdPct"]
        )
        triggered_at = None if current["status"] == "triggered" and changed_trigger else current["triggeredAt"]
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE price_alerts
                SET trigger_direction = ?,
                    trigger_price = ?,
                    alert_reason = ?,
                    alert_type = ?,
                    linked_plan_type = ?,
                    source = ?,
                    source_id = ?,
                    near_threshold_pct = ?,
                    note = ?,
                    triggered_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    fields["trigger_direction"],
                    fields["trigger_price"],
                    fields["alert_reason"],
                    fields["alert_type"],
                    fields["linked_plan_type"],
                    fields["source"],
                    fields["source_id"],
                    fields["near_threshold_pct"],
                    fields["note"],
                    triggered_at,
                    fields["updated_at"],
                    alert_id,
                ),
            )
        return self.get_alert(alert_id) or {}

    def archive_alert(self, alert_id: int, archived_at: str | None = None) -> dict[str, Any]:
        timestamp = archived_at or _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE price_alerts
                SET archived_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (timestamp, _now_iso(), alert_id),
            )
        return self.get_alert(alert_id) or {}

    def soft_delete_alert(self, alert_id: int, deleted_at: str | None = None) -> dict[str, Any]:
        timestamp = deleted_at or _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE price_alerts
                SET deleted_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (timestamp, _now_iso(), alert_id),
            )
        return self.get_alert(alert_id) or {}

    def set_active(self, alert_id: int, is_active: bool) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE price_alerts
                SET is_active = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (1 if is_active else 0, _now_iso(), alert_id),
            )
        return self.get_alert(alert_id) or {}


def evaluate_price_alerts(
    path: Path = CACHE_PATH,
    *,
    symbol: str | None = None,
    symbols: list[str] | tuple[str, ...] | set[str] | None = None,
    entry_contexts: dict[str, Any] | None = None,
    now: datetime | None = None,
    quote_max_age_hours: float | None = 24,
) -> list[dict[str, Any]]:
    store = PriceAlertStore(path)
    selected_symbols = {_symbol(item) for item in (symbols or []) if _symbol(item)}
    if symbol:
        selected_symbols.add(_symbol(symbol))
    alerts = store.list_alerts(include_archived=True, include_deleted=False)
    results: list[dict[str, Any]] = []
    for alert in alerts:
        if selected_symbols and alert["symbol"] not in selected_symbols:
            continue
        market = build_market_context(
            alert["symbol"],
            path=path,
            now=now,
            quote_max_age_hours=quote_max_age_hours,
        )
        current_price = market.get("currentPrice")
        price_stale = bool(market.get("isStale"))
        triggered_now = False
        entry_context = (entry_contexts or {}).get(alert["symbol"], {}) if isinstance(entry_contexts, dict) else {}
        if not price_stale and _should_trigger(alert, current_price, entry_context):
            triggered_at = _time_iso(now)
            alert = store.mark_triggered(int(alert["id"]), triggered_at)
            triggered_now = True
        results.append(_alert_result(alert, market, price_stale, triggered_now))
    return results


def triggered_price_alerts(
    path: Path = CACHE_PATH,
    *,
    symbols: list[str] | tuple[str, ...] | set[str] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    return [
        alert
        for alert in evaluate_price_alerts(path, symbols=symbols, now=now)
        if alert["status"] == "triggered"
    ]


def sync_buy_plan_price_alert(
    path: Path = CACHE_PATH,
    *,
    symbol: str,
    plan: dict[str, Any],
    is_active: bool | None = None,
) -> dict[str, Any]:
    trigger_price = _buy_plan_trigger_price(plan)
    if trigger_price is None:
        return {}
    mode = str(plan.get("alert_mode") or plan.get("alertMode") or "price_below").strip().lower()
    direction = "near" if mode in {"price_near", "radar_pullback"} else "below"
    plan_status = str(plan.get("plan_status") or plan.get("planStatus") or "active").strip().lower()
    active = plan_status not in {"paused", "cancelled", "completed", "expired"} if is_active is None else bool(is_active)
    reason = {
        "price_near": "计划买入：接近目标价提醒",
        "radar_pullback": "计划买入：进入买区回踩区提醒",
    }.get(mode, "计划买入：跌到目标价提醒")
    note_parts = [
        f"plan_status={plan_status or 'active'}",
        f"alert_mode={mode or 'price_below'}",
    ]
    if mode == "radar_pullback":
        note_parts.append("买区回踩区触发依赖刷新后的买区状态；价格提醒保留目标价兜底。")
    return PriceAlertStore(path).upsert_source_alert(
        symbol,
        alertType="BUY_PLAN_TRIGGER",
        source="buy_plan",
        sourceId=_symbol(symbol),
        triggerDirection=direction,
        triggerPrice=trigger_price,
        alertReason=reason,
        linkedPlanType="buy_plan",
        nearThresholdPct=plan.get("near_threshold_pct") or plan.get("nearThresholdPct"),
        isActive=active,
        note="；".join(note_parts),
    )


def deactivate_buy_plan_price_alert(path: Path = CACHE_PATH, *, symbol: str) -> dict[str, Any]:
    store = PriceAlertStore(path)
    alert = store.find_source_alert(
        symbol=symbol,
        alertType="BUY_PLAN_TRIGGER",
        source="buy_plan",
        sourceId=_symbol(symbol),
    )
    if not alert:
        return {}
    return store.set_active(int(alert["id"]), False)


def _should_trigger(alert: dict[str, Any], current_price: float | None, entry_context: dict[str, Any] | None = None) -> bool:
    if alert["status"] != "active" or current_price is None:
        return False
    if _is_radar_pullback_alert(alert) and _entry_context_in_pullback(entry_context or {}):
        return True
    trigger_price = _number(alert.get("triggerPrice"))
    if trigger_price is None:
        return False
    if alert.get("triggerDirection") == "below":
        return current_price <= trigger_price
    if alert.get("triggerDirection") == "above":
        return current_price >= trigger_price
    if alert.get("triggerDirection") == "near":
        threshold = _number(alert.get("nearThresholdPct"))
        threshold = DEFAULT_NEAR_THRESHOLD_PCT if threshold is None else threshold
        return abs(current_price - trigger_price) / trigger_price * 100 <= threshold
    return False


def _is_radar_pullback_alert(alert: dict[str, Any]) -> bool:
    text = " ".join(
        str(alert.get(key) or "")
        for key in ("alertReason", "note", "triggerDirection")
    )
    return "买区回踩区" in text or "Radar 回踩区" in text or "radar_pullback" in text


def _entry_context_in_pullback(entry_context: dict[str, Any]) -> bool:
    status = str(
        entry_context.get("entry_context_status")
        or entry_context.get("entryContextStatus")
        or entry_context.get("technical_position")
        or entry_context.get("technicalPosition")
        or ""
    ).strip().upper()
    if status in {"IN_TECHNICAL_PULLBACK_ZONE", "IN_EFFECTIVE_TECHNICAL_ZONE"}:
        return True
    current = _number(entry_context.get("current_price") or entry_context.get("currentPrice"))
    low = _number(
        entry_context.get("effective_technical_entry_zone_low")
        or entry_context.get("technical_entry_zone_low")
        or entry_context.get("technicalEntryZoneLow")
    )
    high = _number(
        entry_context.get("effective_technical_entry_zone_high")
        or entry_context.get("technical_entry_zone_high")
        or entry_context.get("technicalEntryZoneHigh")
    )
    return current is not None and low is not None and high is not None and low <= current <= high


def _buy_plan_trigger_price(plan: dict[str, Any]) -> float | None:
    direct = _number(plan.get("target_alert_price") or plan.get("targetAlertPrice"))
    if direct is not None:
        return direct
    tranches = plan.get("buy_plan_tranches") or plan.get("buyPlanTranches") or []
    if not isinstance(tranches, list):
        return None
    for item in tranches:
        if not isinstance(item, dict):
            continue
        price = _number(item.get("trigger_price") or item.get("price"))
        if price is not None:
            return price
    return None


def _alert_result(
    alert: dict[str, Any],
    market: dict[str, Any],
    price_stale: bool,
    triggered_now: bool,
) -> dict[str, Any]:
    current_price = market.get("currentPrice")
    result = dict(alert)
    result.update(
        {
            "currentPrice": current_price,
            "priceStatus": market.get("priceSource"),
            "priceSource": market.get("priceSource"),
            "quotePrice": market.get("quotePrice"),
            "latestClose": market.get("latestClose"),
            "fetchedAt": market.get("fetchedAt"),
            "historyStatus": market.get("historyStatus"),
            "historyLatestDate": market.get("historyLatestDate"),
            "historyTickerKey": market.get("historyTickerKey"),
            "marketWarning": market.get("warning") or "",
            "priceDataStale": price_stale,
            "triggeredNow": triggered_now,
            "message": _alert_message(alert, current_price, price_stale) + _stale_hold_message(alert, price_stale),
        }
    )
    return result


def _stale_hold_message(alert: dict[str, Any], price_stale: bool) -> str:
    if not price_stale or alert["status"] == "triggered":
        return ""
    return " 价格数据可能过期，不作为触发信号。"


def _alert_message(alert: dict[str, Any], current_price: float | None, price_stale: bool) -> str:
    symbol = alert.get("symbol") or ""
    trigger = _money(alert.get("triggerPrice"))
    stale = "价格数据可能过期，" if price_stale else ""
    if alert["status"] == "triggered":
        return (
            f"{symbol} 已到达你设置的价格提醒 {trigger}。{stale}"
            "请检查买区、技术结构、历史入口字段、数据健康和交易纪律；价格到达不代表自动可以买。"
        )
    if current_price is None:
        return f"{symbol} 暂无可用价格，提醒未触发。"
    direction = "低于或等于" if alert.get("triggerDirection") == "below" else "高于或等于"
    if alert.get("triggerDirection") == "near":
        threshold = _number(alert.get("nearThresholdPct")) or DEFAULT_NEAR_THRESHOLD_PCT
        direction = f"接近 {threshold:g}% 以内"
    return f"{symbol} 当前价 {_money(current_price)}，等待{direction} {trigger}。"


def _row_to_alert(row: tuple[Any, ...], columns: list[str]) -> dict[str, Any]:
    raw = dict(zip(columns, row))
    return {
        "id": raw.get("id"),
        "symbol": raw.get("symbol"),
        "triggerDirection": raw.get("trigger_direction"),
        "triggerPrice": raw.get("trigger_price"),
        "alertReason": raw.get("alert_reason") or "",
        "alertType": raw.get("alert_type") or "PRICE",
        "linkedPlanType": raw.get("linked_plan_type") or "manual",
        "source": raw.get("source") or "",
        "sourceId": raw.get("source_id") or "",
        "nearThresholdPct": raw.get("near_threshold_pct"),
        "isActive": bool(raw.get("is_active")),
        "triggeredAt": raw.get("triggered_at"),
        "archivedAt": raw.get("archived_at"),
        "deletedAt": raw.get("deleted_at"),
        "note": raw.get("note") or "",
        "createdAt": raw.get("created_at"),
        "updatedAt": raw.get("updated_at"),
        "status": _status(raw),
    }


def _status(raw: dict[str, Any]) -> str:
    if raw.get("deleted_at"):
        return "deleted"
    if raw.get("archived_at"):
        return "archived"
    if not bool(raw.get("is_active")):
        return "disabled"
    if raw.get("triggered_at"):
        return "triggered"
    return "active"


def _symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _direction(value: object) -> str:
    direction = str(value or "").strip().lower()
    if direction not in ALERT_DIRECTIONS:
        raise ValueError("提醒方向必须选择低于、高于或接近")
    return direction


def _linked_plan_type(value: object) -> str:
    plan_type = str(value or "").strip().lower()
    return plan_type if plan_type in LINKED_PLAN_TYPES else "manual"


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _required_number(value: object, field_name: str) -> float:
    number = _number(value)
    if number is None:
        label = PRICE_ALERT_FIELD_LABELS.get(str(field_name or "").strip(), "该字段")
        raise ValueError(f"{label}需要填写数字")
    return number


def _near_threshold_pct(value: object) -> float:
    number = _number(value)
    if number is None or number <= 0:
        return DEFAULT_NEAR_THRESHOLD_PCT
    return number


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value: object) -> str:
    number = _number(value)
    return "暂缺" if number is None else f"${number:,.2f}"


def _time_iso(value: datetime | None) -> str:
    if value is None:
        return _now_iso()
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
