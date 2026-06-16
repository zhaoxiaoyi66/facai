from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from data.decision_log import TradeJournalStore
from data.portfolio import PortfolioPositionStore
from data.prices import CACHE_PATH
from data.trade_intent import TradeIntentStore, build_trade_intent_review_stats


DEFAULT_PRINCIPLES = (
    "少而硬：做高信念集中的少量股票。\n"
    "不基金化分散。\n"
    "不为参与感买小仓。\n"
    "好公司很多，但适合我持有的好公司很少。\n"
    "买点看客观 Setup，仓位看纪律。\n"
    "现金也是仓位，等待也是操作。"
)

SELF_CHECK_QUESTIONS = [
    "这笔交易是在加强我的核心方向，还是又多买一个小仓？",
    "我买它是因为 Setup 承接好，还是因为怕错过？",
    "如果继续跌 10%-15%，我会按计划处理，还是会焦虑？",
    "这只股票我是否愿意长期跟踪？",
    "这笔交易会让组合更清晰，还是更碎片化？",
]

DISCIPLINE_TAG_LABELS = {
    "plan_followed": "符合计划",
    "chase": "追高",
    "participation_small_position": "参与感小仓",
    "emotional_buy": "情绪买入",
    "borrowed_view": "因别人观点买入",
    "low_setup_buy": "Setup 低分仍买入",
    "sell_without_reentry_plan": "卖出无回补计划",
    "panic_sell": "恐慌卖出",
    "right_process": "做对了",
    "wrong_process": "做错了",
}

DEFAULT_SETTINGS = {
    "target_holding_min": 3,
    "target_holding_max": 5,
    "small_position_threshold_pct": 3.0,
    "target_core_min": 1,
    "target_core_max": 3,
}


@dataclass(frozen=True)
class PortfolioDisciplineSummary:
    current_holding_count: int
    target_holding_min: int
    target_holding_max: int
    top1_weight_pct: float
    top3_weight_pct: float
    small_position_count: int
    new_position_count_this_week: int
    unplanned_trade_count_this_week: int
    target_core_min: int
    target_core_max: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DisciplineReviewStore:
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
                CREATE TABLE IF NOT EXISTS discipline_principles (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    text TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS discipline_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    target_holding_min INTEGER NOT NULL,
                    target_holding_max INTEGER NOT NULL,
                    small_position_threshold_pct REAL NOT NULL,
                    target_core_min INTEGER NOT NULL,
                    target_core_max INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_discipline_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_entry_id INTEGER NOT NULL,
                    tag TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(trade_entry_id, tag)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS discipline_review_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_date TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def get_principles(self) -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT text FROM discipline_principles WHERE id = 1").fetchone()
        return str(row[0]) if row else DEFAULT_PRINCIPLES

    def save_principles(self, text: str) -> str:
        clean = str(text or "").strip() or DEFAULT_PRINCIPLES
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO discipline_principles (id, text, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at
                """,
                (clean, now),
            )
        return clean

    def reset_principles(self) -> str:
        return self.save_principles(DEFAULT_PRINCIPLES)

    def get_settings(self) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM discipline_settings WHERE id = 1")
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        if not row:
            return dict(DEFAULT_SETTINGS)
        data = _row_to_dict(columns, row)
        return {key: data.get(key, value) for key, value in DEFAULT_SETTINGS.items()}

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        settings = _clean_settings(values)
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO discipline_settings (
                    id,
                    target_holding_min,
                    target_holding_max,
                    small_position_threshold_pct,
                    target_core_min,
                    target_core_max,
                    updated_at
                )
                VALUES (1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    target_holding_min = excluded.target_holding_min,
                    target_holding_max = excluded.target_holding_max,
                    small_position_threshold_pct = excluded.small_position_threshold_pct,
                    target_core_min = excluded.target_core_min,
                    target_core_max = excluded.target_core_max,
                    updated_at = excluded.updated_at
                """,
                (
                    settings["target_holding_min"],
                    settings["target_holding_max"],
                    settings["small_position_threshold_pct"],
                    settings["target_core_min"],
                    settings["target_core_max"],
                    now,
                ),
            )
        return settings

    def save_trade_tags(self, trade_entry_id: int, tags: list[str], notes: str = "") -> list[dict[str, Any]]:
        clean_id = int(trade_entry_id)
        clean_tags = _dedupe([tag for tag in tags if tag in DISCIPLINE_TAG_LABELS])
        clean_notes = str(notes or "").strip()
        now = _now()
        with self.connect() as conn:
            conn.execute("DELETE FROM trade_discipline_tags WHERE trade_entry_id = ?", (clean_id,))
            for tag in clean_tags:
                conn.execute(
                    """
                    INSERT INTO trade_discipline_tags (trade_entry_id, tag, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (clean_id, tag, clean_notes, now, now),
                )
        return self.list_tags_for_trade(clean_id)

    def list_tags_for_trade(self, trade_entry_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM trade_discipline_tags
                WHERE trade_entry_id = ?
                ORDER BY tag
                """,
                (int(trade_entry_id),),
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]

    def list_trade_tags(self, *, days: int | None = None, current_date: date | str | None = None) -> list[dict[str, Any]]:
        current = _parse_date(current_date) or date.today()
        params: list[Any] = []
        where = ""
        if days is not None:
            start = current - timedelta(days=max(0, int(days) - 1))
            where = "WHERE substr(e.trade_date, 1, 10) >= ? AND substr(e.trade_date, 1, 10) <= ?"
            params.extend([start.isoformat(), current.isoformat()])
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                SELECT
                    t.*,
                    e.symbol,
                    e.trade_date,
                    e.action_type,
                    e.quantity,
                    e.price,
                    e.decision_mood
                FROM trade_discipline_tags t
                LEFT JOIN trade_journal_entries e ON e.id = t.trade_entry_id
                {where}
                ORDER BY e.trade_date DESC, t.updated_at DESC, t.id DESC
                """,
                params,
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]


def build_discipline_review_stats(
    entries: list[dict[str, Any]],
    tag_rows: list[dict[str, Any]],
    *,
    current_date: date | str | None = None,
) -> dict[str, Any]:
    current = _parse_date(current_date) or date.today()
    return {
        "seven_days": _period_stats(entries, tag_rows, current=current, days=7),
        "thirty_days": _period_stats(entries, tag_rows, current=current, days=30),
    }


def build_portfolio_discipline_summary(
    positions: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    settings: dict[str, Any] | None = None,
    *,
    current_date: date | str | None = None,
) -> dict[str, Any]:
    clean_settings = _clean_settings(settings or DEFAULT_SETTINGS)
    active = [position for position in positions if _number(position.get("quantity")) > 0]
    weights = sorted((_position_weight(position) for position in active), reverse=True)
    total = sum(weights)
    normalized = [weight / total * 100 for weight in weights] if total > 0 else []
    threshold = float(clean_settings["small_position_threshold_pct"])
    current = _parse_date(current_date) or date.today()
    week_start = current - timedelta(days=current.weekday())
    week_entries = [entry for entry in entries if _date_in_range(entry.get("trade_date"), week_start, current)]
    new_symbols = {
        str(entry.get("symbol") or "").upper()
        for entry in week_entries
        if str(entry.get("action_type") or "").lower() in {"buy", "add"}
    }
    unplanned = [entry for entry in week_entries if _looks_unplanned_trade(entry)]
    summary = PortfolioDisciplineSummary(
        current_holding_count=len(active),
        target_holding_min=int(clean_settings["target_holding_min"]),
        target_holding_max=int(clean_settings["target_holding_max"]),
        top1_weight_pct=round(normalized[0], 2) if normalized else 0.0,
        top3_weight_pct=round(sum(normalized[:3]), 2) if normalized else 0.0,
        small_position_count=sum(1 for value in normalized if value < threshold),
        new_position_count_this_week=len(new_symbols),
        unplanned_trade_count_this_week=len(unplanned),
        target_core_min=int(clean_settings["target_core_min"]),
        target_core_max=int(clean_settings["target_core_max"]),
    )
    return summary.to_dict()


def build_dashboard_discipline_snapshot(
    path: Path = CACHE_PATH,
    *,
    current_date: date | str | None = None,
) -> dict[str, Any]:
    store = DisciplineReviewStore(path)
    principles = store.get_principles()
    positions = PortfolioPositionStore(path).list_active_positions()
    entries = TradeJournalStore(path).list_entries()
    intent_reviews = TradeIntentStore(path).list_intents()
    intent_stats = build_trade_intent_review_stats(
        entries,
        intent_reviews,
        current_date=current_date,
    )
    summary = build_portfolio_discipline_summary(
        positions,
        entries,
        store.get_settings(),
        current_date=current_date,
    )
    return {
        "principle_first_line": principles.splitlines()[0] if principles else "",
        "portfolio": summary,
        "trade_intent": intent_stats["thirty_days"],
    }


def label_for_tag(tag: object) -> str:
    return DISCIPLINE_TAG_LABELS.get(str(tag or ""), str(tag or ""))


def _period_stats(entries: list[dict[str, Any]], tag_rows: list[dict[str, Any]], *, current: date, days: int) -> dict[str, Any]:
    start = current - timedelta(days=days - 1)
    period_entries = [entry for entry in entries if _date_in_range(entry.get("trade_date"), start, current)]
    period_tags = [tag for tag in tag_rows if _date_in_range(tag.get("trade_date"), start, current)]
    tag_counts = _tag_counts(period_tags)
    tagged_trade_ids = {int(row.get("trade_entry_id") or 0) for row in period_tags if int(row.get("trade_entry_id") or 0) > 0}
    plan_followed_count = tag_counts.get("plan_followed", 0)
    denominator = len(tagged_trade_ids) or len(period_entries) or 1
    return {
        "days": days,
        "trade_count": len(period_entries),
        "participation_small_position_count": tag_counts.get("participation_small_position", 0),
        "chase_count": tag_counts.get("chase", 0),
        "low_setup_buy_count": tag_counts.get("low_setup_buy", 0),
        "panic_sell_count": tag_counts.get("panic_sell", 0),
        "sell_without_reentry_plan_count": tag_counts.get("sell_without_reentry_plan", 0),
        "plan_followed_ratio": round(plan_followed_count / denominator * 100, 1),
        "tag_counts": tag_counts,
    }


def _tag_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        tag = str(row.get("tag") or "")
        if not tag:
            continue
        counts[tag] = counts.get(tag, 0) + 1
    return counts


def _clean_settings(values: dict[str, Any]) -> dict[str, Any]:
    target_min = max(1, int(_number(values.get("target_holding_min"), DEFAULT_SETTINGS["target_holding_min"])))
    target_max = max(target_min, int(_number(values.get("target_holding_max"), DEFAULT_SETTINGS["target_holding_max"])))
    core_min = max(0, int(_number(values.get("target_core_min"), DEFAULT_SETTINGS["target_core_min"])))
    core_max = max(core_min, int(_number(values.get("target_core_max"), DEFAULT_SETTINGS["target_core_max"])))
    return {
        "target_holding_min": target_min,
        "target_holding_max": target_max,
        "small_position_threshold_pct": max(
            0.1,
            float(_number(values.get("small_position_threshold_pct"), DEFAULT_SETTINGS["small_position_threshold_pct"])),
        ),
        "target_core_min": core_min,
        "target_core_max": core_max,
    }


def _position_weight(position: dict[str, Any]) -> float:
    quantity = _number(position.get("quantity"))
    price = _number(position.get("current_price") or position.get("price") or position.get("market_price"))
    average_cost = _number(position.get("average_cost") or position.get("averageCost"))
    return max(0.0, quantity * (price or average_cost))


def _looks_unplanned_trade(entry: dict[str, Any]) -> bool:
    mood = str(entry.get("decision_mood") or "").strip().lower()
    if mood in {"fomo", "anxiety", "bottom_fishing_impulse", "revenge_trade", "panic_sell", "regret_chase"}:
        return True
    note = str(entry.get("notes") or "").lower()
    return any(token in note for token in ("fomo", "冲动", "参与感", "panic", "追高"))


def _date_in_range(value: object, start: date, end: date) -> bool:
    parsed = _parse_date(value)
    return bool(parsed is not None and start <= parsed <= end)


def _parse_date(value: date | str | object | None) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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


def _row_to_dict(columns: list[str], row: tuple) -> dict[str, Any]:
    return {columns[index]: row[index] for index in range(len(columns))}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
