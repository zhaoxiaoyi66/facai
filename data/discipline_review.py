from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from data.decision_log import TradeJournalStore
from data.market_context import build_market_context
from data.portfolio import PortfolioPositionStore, PortfolioSettingsStore
from data.prices import CACHE_PATH
from data.trade_intent import TradeIntentStore, build_trade_intent_review_stats


DEFAULT_PRINCIPLES = (
    "少而硬：做高信念、能长期拿住的少数股票。\n"
    "不基金化分散。\n"
    "不为参与感买小仓。\n"
    "好公司很多，但适合我持有的好公司很少。\n"
    "买点看客观 Setup，仓位看纪律。\n"
    "现金也是仓位，等待也是操作。"
)

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

MISTAKE_MARKET_TYPES = ["美股", "港股", "币安现货", "币安合约", "其他"]

MISTAKE_TAG_OPTIONS = [
    "没设止损",
    "没设止盈",
    "忘记持仓",
    "隔夜暴露",
    "无计划开仓",
    "仓位过大",
    "情绪交易",
    "怕错过",
    "追涨杀跌",
    "听别人观点交易",
    "无回补计划",
    "执行纪律问题",
]

MISTAKE_REVIEW_STATUSES = ["已记录", "已形成规则", "已设置防线"]

PERIODIC_RETURN_TYPES = ["周复盘", "月复盘"]

EQUITY_SOURCE_PORTFOLIO = "当前持仓汇总"
EQUITY_SOURCE_LEGACY_PORTFOLIO = "持仓记录"
EQUITY_SOURCE_ACCOUNT_SNAPSHOT = "账户快照"
EQUITY_SOURCE_PREVIOUS_REVIEW = "上一条复盘"
EQUITY_SOURCE_MANUAL = "手动录入"
EQUITY_SOURCE_NOT_FOUND = "未找到快照"
EQUITY_FILL_AUTO = "自动读取"
EQUITY_FILL_MANUAL = "手动修改"


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mistake_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_date TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    symbol TEXT,
                    scene_or_symbol TEXT,
                    loss_amount REAL,
                    loss_impact_text TEXT,
                    trigger_event TEXT,
                    action_taken TEXT,
                    result_text TEXT,
                    mistake_tags_json TEXT NOT NULL,
                    reflection TEXT,
                    improvement_rule TEXT,
                    next_defense TEXT,
                    review_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_mistake_review_columns(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS periodic_return_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_type TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    starting_equity REAL,
                    ending_equity REAL,
                    deposit_amount REAL,
                    withdrawal_amount REAL,
                    profit_amount REAL,
                    return_rate REAL,
                    biggest_contributor TEXT,
                    biggest_drag TEXT,
                    what_went_well TEXT,
                    what_went_wrong TEXT,
                    next_period_rule TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_periodic_return_review_columns(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS account_equity_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_time TEXT NOT NULL,
                    account_equity REAL NOT NULL,
                    cash REAL,
                    market_value REAL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def _ensure_mistake_review_columns(self, conn: sqlite3.Connection) -> None:
        existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(mistake_reviews)").fetchall()}
        additions = {
            "scene_or_symbol": "TEXT",
            "loss_impact_text": "TEXT",
        }
        for column, column_type in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE mistake_reviews ADD COLUMN {column} {column_type}")

    def _ensure_periodic_return_review_columns(self, conn: sqlite3.Connection) -> None:
        existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(periodic_return_reviews)").fetchall()}
        additions = {
            "starting_equity_source": "TEXT",
            "ending_equity_source": "TEXT",
            "starting_equity_snapshot_date": "TEXT",
            "ending_equity_snapshot_date": "TEXT",
            "starting_equity_is_manual_override": "INTEGER NOT NULL DEFAULT 0",
            "ending_equity_is_manual_override": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, column_type in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE periodic_return_reviews ADD COLUMN {column} {column_type}")

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

    def save_mistake_review(self, values: dict[str, Any]) -> dict[str, Any]:
        review_date = _parse_date(values.get("review_date")) or date.today()
        market_type = _clean_choice(values.get("market_type"), MISTAKE_MARKET_TYPES, "其他")
        review_status = _clean_choice(values.get("review_status"), MISTAKE_REVIEW_STATUSES, "已记录")
        tags = _dedupe([tag for tag in values.get("mistake_tags", []) if tag in MISTAKE_TAG_OPTIONS])
        scene_or_symbol = _clean_text(values.get("scene_or_symbol") or values.get("symbol"))
        symbol = str(values.get("symbol") or scene_or_symbol or "").strip().upper()
        loss_impact_text = _clean_text(values.get("impact_summary") or values.get("loss_impact_text"))
        loss_amount = _optional_nonnegative_number(values.get("loss_amount_usd"))
        if loss_amount is None:
            loss_amount = _optional_nonnegative_number(values.get("loss_amount"))
        now = _now()
        fields = {
            "review_date": review_date.isoformat(),
            "market_type": market_type,
            "symbol": symbol,
            "scene_or_symbol": scene_or_symbol,
            "loss_amount": loss_amount,
            "loss_impact_text": loss_impact_text,
            "trigger_event": _clean_text(values.get("trigger_event")),
            "action_taken": _clean_text(values.get("action_taken")),
            "result_text": _clean_text(values.get("result_text")),
            "mistake_tags_json": json.dumps(tags, ensure_ascii=False),
            "reflection": _clean_text(values.get("reflection")),
            "improvement_rule": _clean_text(values.get("improvement_rule")),
            "next_defense": _clean_text(values.get("next_defense")),
            "review_status": review_status,
            "created_at": now,
            "updated_at": now,
        }
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO mistake_reviews (
                    review_date,
                    market_type,
                    symbol,
                    scene_or_symbol,
                    loss_amount,
                    loss_impact_text,
                    trigger_event,
                    action_taken,
                    result_text,
                    mistake_tags_json,
                    reflection,
                    improvement_rule,
                    next_defense,
                    review_status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fields["review_date"],
                    fields["market_type"],
                    fields["symbol"],
                    fields["scene_or_symbol"],
                    fields["loss_amount"],
                    fields["loss_impact_text"],
                    fields["trigger_event"],
                    fields["action_taken"],
                    fields["result_text"],
                    fields["mistake_tags_json"],
                    fields["reflection"],
                    fields["improvement_rule"],
                    fields["next_defense"],
                    fields["review_status"],
                    fields["created_at"],
                    fields["updated_at"],
                ),
            )
            review_id = int(cursor.lastrowid)
        saved = self.get_mistake_review(review_id)
        return saved or {"id": review_id, **fields, "mistake_tags": tags}

    def get_mistake_review(self, review_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM mistake_reviews WHERE id = ?", (int(review_id),))
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return _mistake_row_to_dict(columns, row) if row else None

    def list_mistake_reviews(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM mistake_reviews
                ORDER BY review_date DESC, updated_at DESC, id DESC
                """
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_mistake_row_to_dict(columns, row) for row in rows]

    def save_account_equity_snapshot(self, values: dict[str, Any]) -> dict[str, Any]:
        snapshot_time = _parse_datetime(values.get("snapshot_time")) or datetime.now()
        account_equity = _optional_nonnegative_number(values.get("account_equity"))
        if account_equity is None or account_equity <= 0:
            raise ValueError("account_equity is required")
        cash = _optional_nonnegative_number(values.get("cash"))
        market_value = _optional_nonnegative_number(values.get("market_value"))
        source = _clean_choice(
            values.get("source"),
            [EQUITY_SOURCE_PORTFOLIO, EQUITY_SOURCE_LEGACY_PORTFOLIO, EQUITY_SOURCE_MANUAL],
            EQUITY_SOURCE_PORTFOLIO,
        )
        latest = self.get_latest_account_equity_snapshot()
        snapshot_time_text = snapshot_time.isoformat(timespec="seconds")
        if latest:
            latest_equity = _optional_nonnegative_number(latest.get("account_equity"))
            latest_source = str(latest.get("source") or "")
            latest_day = str(latest.get("snapshot_time") or "")[:10]
            if (
                latest_equity is not None
                and abs(latest_equity - account_equity) < 0.01
                and latest_source == source
                and latest_day == snapshot_time_text[:10]
            ):
                return latest
        now = _now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO account_equity_snapshots (
                    snapshot_time,
                    account_equity,
                    cash,
                    market_value,
                    source,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_time_text,
                    round(account_equity, 2),
                    round(cash, 2) if cash is not None else None,
                    round(market_value, 2) if market_value is not None else None,
                    source,
                    now,
                ),
            )
            snapshot_id = int(cursor.lastrowid)
        return self.get_account_equity_snapshot(snapshot_id) or {
            "id": snapshot_id,
            "snapshot_time": snapshot_time_text,
            "account_equity": round(account_equity, 2),
            "cash": round(cash, 2) if cash is not None else None,
            "market_value": round(market_value, 2) if market_value is not None else None,
            "source": source,
            "created_at": now,
        }

    def capture_current_account_equity_snapshot(self) -> dict[str, Any] | None:
        nav = get_current_account_nav(self.path)
        total_equity = _optional_nonnegative_number(nav.get("account_nav"))
        if total_equity is None or total_equity <= 0:
            return None
        return self.save_account_equity_snapshot(
            {
                "snapshot_time": datetime.now(),
                "account_equity": total_equity,
                "cash": nav.get("cash"),
                "market_value": nav.get("market_value"),
                "source": nav.get("source") or EQUITY_SOURCE_PORTFOLIO,
            }
        )

    def get_account_equity_snapshot(self, snapshot_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM account_equity_snapshots WHERE id = ?", (int(snapshot_id),))
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return _row_to_dict(columns, row) if row else None

    def get_latest_account_equity_snapshot(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM account_equity_snapshots
                ORDER BY snapshot_time DESC, id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return _row_to_dict(columns, row) if row else None

    def list_account_equity_snapshots(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM account_equity_snapshots
                ORDER BY snapshot_time DESC, id DESC
                """
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]

    def find_account_equity_snapshot(
        self,
        target_date: date | str | None,
        *,
        allow_latest_if_today: bool = False,
    ) -> dict[str, Any] | None:
        target = _parse_date(target_date)
        if target is None:
            return None
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM account_equity_snapshots
                WHERE substr(snapshot_time, 1, 10) <= ?
                ORDER BY snapshot_time DESC, id DESC
                LIMIT 1
                """,
                (target.isoformat(),),
            )
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        if row:
            return _row_to_dict(columns, row)
        if allow_latest_if_today and target == date.today():
            return self.get_latest_account_equity_snapshot()
        return None

    def build_periodic_return_prefill(
        self,
        *,
        start_date: date | str | None,
        end_date: date | str | None,
        previous_ending_equity: object = None,
        use_current_nav_fallback: bool = True,
    ) -> dict[str, Any]:
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        latest = self.get_latest_account_equity_snapshot()
        starting_snapshot = self.find_account_equity_snapshot(start)
        ending_snapshot = self.find_account_equity_snapshot(end, allow_latest_if_today=end == date.today() if end else False)
        current_nav = get_current_account_nav(self.path) if use_current_nav_fallback else {}
        previous_ending = _optional_nonnegative_number(previous_ending_equity)
        starting_equity = _optional_nonnegative_number((starting_snapshot or {}).get("account_equity"))
        starting_source = EQUITY_SOURCE_ACCOUNT_SNAPSHOT if starting_snapshot else EQUITY_SOURCE_NOT_FOUND
        if starting_equity is None and previous_ending is not None:
            starting_equity = previous_ending
            starting_source = EQUITY_SOURCE_PREVIOUS_REVIEW
        ending_equity = _optional_nonnegative_number((ending_snapshot or {}).get("account_equity"))
        ending_source = EQUITY_SOURCE_ACCOUNT_SNAPSHOT if ending_snapshot else EQUITY_SOURCE_NOT_FOUND
        if ending_equity is None:
            ending_equity = _optional_nonnegative_number(current_nav.get("account_nav"))
            if ending_equity is not None:
                ending_source = EQUITY_SOURCE_PORTFOLIO
        only_latest_available = latest is not None and starting_snapshot is None and ending_snapshot is None
        return {
            "starting_equity": starting_equity,
            "ending_equity": ending_equity,
            "starting_snapshot": starting_snapshot,
            "ending_snapshot": ending_snapshot,
            "starting_equity_source": starting_source,
            "ending_equity_source": ending_source,
            "starting_equity_snapshot_date": _snapshot_date_text(starting_snapshot),
            "ending_equity_snapshot_date": _snapshot_date_text(ending_snapshot),
            "latest_snapshot": latest,
            "current_nav": current_nav,
            "only_latest_available": only_latest_available,
        }

    def save_periodic_return_review(self, values: dict[str, Any], review_id: int | None = None) -> dict[str, Any]:
        period_type = _clean_choice(values.get("period_type"), PERIODIC_RETURN_TYPES, "周复盘")
        start_date = _parse_date(values.get("start_date")) or date.today()
        end_date = _parse_date(values.get("end_date")) or start_date
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        starting_equity = _optional_nonnegative_number(values.get("starting_equity"))
        ending_equity = _optional_nonnegative_number(values.get("ending_equity"))
        deposit_amount = _number(values.get("deposit_amount"), 0.0)
        withdrawal_amount = _number(values.get("withdrawal_amount"), 0.0)
        profit_amount, return_rate = _calculate_period_return(
            starting_equity=starting_equity,
            ending_equity=ending_equity,
            deposit_amount=deposit_amount,
            withdrawal_amount=withdrawal_amount,
        )
        now = _now()
        fields = {
            "period_type": period_type,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "starting_equity": starting_equity,
            "ending_equity": ending_equity,
            "deposit_amount": deposit_amount,
            "withdrawal_amount": withdrawal_amount,
            "profit_amount": profit_amount,
            "return_rate": return_rate,
            "biggest_contributor": _clean_text(values.get("biggest_contributor")),
            "biggest_drag": _clean_text(values.get("biggest_drag")),
            "what_went_well": _clean_text(values.get("what_went_well")),
            "what_went_wrong": _clean_text(values.get("what_went_wrong")),
            "next_period_rule": _clean_text(values.get("next_period_rule")),
            "notes": _clean_text(values.get("notes")),
            "starting_equity_source": _clean_text(values.get("starting_equity_source")),
            "ending_equity_source": _clean_text(values.get("ending_equity_source")),
            "starting_equity_snapshot_date": _clean_text(values.get("starting_equity_snapshot_date")),
            "ending_equity_snapshot_date": _clean_text(values.get("ending_equity_snapshot_date")),
            "starting_equity_is_manual_override": 1 if bool(values.get("starting_equity_is_manual_override")) else 0,
            "ending_equity_is_manual_override": 1 if bool(values.get("ending_equity_is_manual_override")) else 0,
            "updated_at": now,
        }
        with self.connect() as conn:
            if review_id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO periodic_return_reviews (
                        period_type,
                        start_date,
                        end_date,
                        starting_equity,
                        ending_equity,
                        deposit_amount,
                        withdrawal_amount,
                        profit_amount,
                        return_rate,
                        biggest_contributor,
                        biggest_drag,
                        what_went_well,
                        what_went_wrong,
                        next_period_rule,
                        notes,
                        starting_equity_source,
                        ending_equity_source,
                        starting_equity_snapshot_date,
                        ending_equity_snapshot_date,
                        starting_equity_is_manual_override,
                        ending_equity_is_manual_override,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fields["period_type"],
                        fields["start_date"],
                        fields["end_date"],
                        fields["starting_equity"],
                        fields["ending_equity"],
                        fields["deposit_amount"],
                        fields["withdrawal_amount"],
                        fields["profit_amount"],
                        fields["return_rate"],
                        fields["biggest_contributor"],
                        fields["biggest_drag"],
                        fields["what_went_well"],
                        fields["what_went_wrong"],
                        fields["next_period_rule"],
                        fields["notes"],
                        fields["starting_equity_source"],
                        fields["ending_equity_source"],
                        fields["starting_equity_snapshot_date"],
                        fields["ending_equity_snapshot_date"],
                        fields["starting_equity_is_manual_override"],
                        fields["ending_equity_is_manual_override"],
                        now,
                        fields["updated_at"],
                    ),
                )
                saved_id = int(cursor.lastrowid)
            else:
                saved_id = int(review_id)
                conn.execute(
                    """
                    UPDATE periodic_return_reviews
                    SET period_type = ?,
                        start_date = ?,
                        end_date = ?,
                        starting_equity = ?,
                        ending_equity = ?,
                        deposit_amount = ?,
                        withdrawal_amount = ?,
                        profit_amount = ?,
                        return_rate = ?,
                        biggest_contributor = ?,
                        biggest_drag = ?,
                        what_went_well = ?,
                        what_went_wrong = ?,
                        next_period_rule = ?,
                        notes = ?,
                        starting_equity_source = ?,
                        ending_equity_source = ?,
                        starting_equity_snapshot_date = ?,
                        ending_equity_snapshot_date = ?,
                        starting_equity_is_manual_override = ?,
                        ending_equity_is_manual_override = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        fields["period_type"],
                        fields["start_date"],
                        fields["end_date"],
                        fields["starting_equity"],
                        fields["ending_equity"],
                        fields["deposit_amount"],
                        fields["withdrawal_amount"],
                        fields["profit_amount"],
                        fields["return_rate"],
                        fields["biggest_contributor"],
                        fields["biggest_drag"],
                        fields["what_went_well"],
                        fields["what_went_wrong"],
                        fields["next_period_rule"],
                        fields["notes"],
                        fields["starting_equity_source"],
                        fields["ending_equity_source"],
                        fields["starting_equity_snapshot_date"],
                        fields["ending_equity_snapshot_date"],
                        fields["starting_equity_is_manual_override"],
                        fields["ending_equity_is_manual_override"],
                        fields["updated_at"],
                        saved_id,
                    ),
                )
        return self.get_periodic_return_review(saved_id) or {"id": saved_id, **fields}

    def get_periodic_return_review(self, review_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM periodic_return_reviews WHERE id = ?", (int(review_id),))
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return _row_to_dict(columns, row) if row else None

    def list_periodic_return_reviews(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                SELECT *
                FROM periodic_return_reviews
                ORDER BY end_date DESC, start_date DESC, id DESC
                """
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        return [_row_to_dict(columns, row) for row in rows]

    def delete_periodic_return_review(self, review_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM periodic_return_reviews WHERE id = ?", (int(review_id),))


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


def build_mistake_review_summary(
    rows: list[dict[str, Any]],
    *,
    current_date: date | str | None = None,
) -> dict[str, Any]:
    current = _parse_date(current_date) or date.today()
    recent_start = current - timedelta(days=29)
    recent_rows = [row for row in rows if _date_in_range(row.get("review_date"), recent_start, current)]
    all_tag_counts = _mistake_tag_counts(rows)
    recent_tag_counts = _mistake_tag_counts(recent_rows)
    most_common = sorted(all_tag_counts.items(), key=lambda item: (-item[1], item[0]))
    repeated = [tag for tag, count in sorted(recent_tag_counts.items(), key=lambda item: (-item[1], item[0])) if count >= 2]
    recent_loss = sum(_optional_nonnegative_number(row.get("loss_amount")) or 0.0 for row in recent_rows)
    recent_loss_impact_count = sum(1 for row in recent_rows if _has_loss_amount(row))
    recent_loss_text = _loss_amount_summary(recent_loss)
    return {
        "total_count": len(rows),
        "recent_30_count": len(recent_rows),
        "recent_30_loss_amount": round(recent_loss, 2),
        "recent_30_loss_impact_count": recent_loss_impact_count,
        "recent_30_loss_amount_text": recent_loss_text,
        "recent_30_loss_impact_text": recent_loss_text,
        "most_common_mistake_type": most_common[0][0] if most_common else "",
        "most_common_mistake_count": most_common[0][1] if most_common else 0,
        "unruled_count": sum(1 for row in rows if str(row.get("review_status") or "") not in {"已形成规则", "已设置防线"}),
        "repeated_mistake_types": repeated,
        "tag_counts": all_tag_counts,
        "recent_tag_counts": recent_tag_counts,
    }


def build_periodic_return_review_summary(
    rows: list[dict[str, Any]],
    *,
    current_date: date | str | None = None,
) -> dict[str, Any]:
    current = _parse_date(current_date) or date.today()
    week_start = current - timedelta(days=current.weekday())
    week_end = week_start + timedelta(days=6)
    month_start = current.replace(day=1)
    if current.month == 12:
        next_month_start = current.replace(year=current.year + 1, month=1, day=1)
    else:
        next_month_start = current.replace(month=current.month + 1, day=1)
    month_end = next_month_start - timedelta(days=1)
    weekly_rows = _sorted_period_rows(rows, "周复盘")
    monthly_rows = _sorted_period_rows(rows, "月复盘")
    current_week_row = _latest_overlapping_period(weekly_rows, week_start, week_end)
    current_month_row = _latest_overlapping_period(monthly_rows, month_start, month_end)
    recent_4_week_rows = weekly_rows[:4]
    recent_3_month_rows = monthly_rows[:3]
    return {
        "weekly_count": len(weekly_rows),
        "monthly_count": len(monthly_rows),
        "has_current_week_review": current_week_row is not None,
        "has_current_month_review": current_month_row is not None,
        "current_week_profit": _row_profit(current_week_row),
        "current_week_return": _row_return_rate(current_week_row),
        "current_month_profit": _row_profit(current_month_row),
        "current_month_return": _row_return_rate(current_month_row),
        "recent_4_week_profit": _sum_profit(recent_4_week_rows),
        "recent_4_week_max_loss": _max_loss(recent_4_week_rows),
        "recent_3_month_profit": _sum_profit(recent_3_month_rows),
        "max_weekly_loss": _max_loss(weekly_rows),
        "max_monthly_loss": _max_loss(monthly_rows),
    }


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
    periodic_summary = build_periodic_return_review_summary(
        store.list_periodic_return_reviews(),
        current_date=current_date,
    )
    return {
        "principle_first_line": principles.splitlines()[0] if principles else "",
        "portfolio": summary,
        "trade_intent": intent_stats["thirty_days"],
        "periodic_returns": periodic_summary,
    }


def label_for_tag(tag: object) -> str:
    return DISCIPLINE_TAG_LABELS.get(str(tag or ""), str(tag or ""))


def get_current_account_nav(path: Path = CACHE_PATH) -> dict[str, Any]:
    settings = PortfolioSettingsStore(path).get_settings()
    positions = PortfolioPositionStore(path).list_active_positions()
    total_equity = _optional_nonnegative_number(settings.get("total_portfolio_value"))
    cash = _optional_nonnegative_number(settings.get("cash_balance"))
    market_value = _portfolio_market_value_from_snapshot(positions, path)
    if cash is None and total_equity is not None:
        cash = round(total_equity - market_value, 2) if market_value is not None else total_equity
    if market_value is not None and cash is not None:
        account_nav = round(market_value + cash, 2)
    elif market_value is not None:
        account_nav = market_value
    elif cash is not None:
        account_nav = cash
    else:
        account_nav = None
    if account_nav is None or account_nav <= 0:
        return {
            "account_nav": None,
            "cash": cash,
            "market_value": market_value,
            "source": EQUITY_SOURCE_NOT_FOUND,
            "updated_at": settings.get("updated_at") or "",
        }
    return {
        "account_nav": round(account_nav, 2),
        "cash": round(cash, 2) if cash is not None else None,
        "market_value": round(market_value, 2) if market_value is not None else None,
        "source": EQUITY_SOURCE_PORTFOLIO,
        "updated_at": settings.get("updated_at") or _now(),
    }


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


def _mistake_tag_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        tags = row.get("mistake_tags") or []
        if isinstance(tags, str):
            tags = _parse_json_list(tags)
        for tag in tags:
            text = str(tag or "").strip()
            if not text:
                continue
            counts[text] = counts.get(text, 0) + 1
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


def _clean_choice(value: object, allowed: list[str], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def _clean_text(value: object) -> str:
    return str(value or "").strip()


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


def _parse_datetime(value: datetime | str | object | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def default_period_dates(period_type: str, *, today: date | str | None = None) -> tuple[date, date]:
    current = _parse_date(today) or date.today()
    if str(period_type or "") == "月复盘":
        month_end = current.replace(day=1) - timedelta(days=1)
        month_start = month_end.replace(day=1)
        return month_start, month_end
    current_week_start = current - timedelta(days=current.weekday())
    week_end = current_week_start - timedelta(days=1)
    week_start = week_end - timedelta(days=6)
    return week_start, week_end


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _optional_nonnegative_number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _calculate_period_return(
    *,
    starting_equity: float | None,
    ending_equity: float | None,
    deposit_amount: float,
    withdrawal_amount: float,
) -> tuple[float | None, float | None]:
    if starting_equity is None or ending_equity is None:
        return None, None
    profit = ending_equity - starting_equity - deposit_amount + withdrawal_amount
    if starting_equity <= 0:
        return round(profit, 2), None
    return round(profit, 2), round(profit / starting_equity, 6)


def _portfolio_market_value_from_snapshot(positions: list[dict[str, Any]], path: Path) -> float | None:
    total = 0.0
    has_value = False
    for position in positions:
        quantity = _number(position.get("quantity"))
        if quantity <= 0:
            continue
        symbol = str(position.get("symbol") or "").strip().upper()
        price = None
        if symbol:
            try:
                market = build_market_context(symbol, path=path)
                price = _optional_nonnegative_number(market.get("currentPrice"))
            except Exception:
                price = None
        if price is None:
            price = _optional_nonnegative_number(position.get("average_cost"))
        if price is None:
            continue
        total += quantity * price
        has_value = True
    return round(total, 2) if has_value else None


def _snapshot_date_text(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return ""
    return str(snapshot.get("snapshot_time") or "")[:10]


def _sorted_period_rows(rows: list[dict[str, Any]], period_type: str) -> list[dict[str, Any]]:
    filtered = [row for row in rows if str(row.get("period_type") or "") == period_type]
    return sorted(filtered, key=lambda row: (str(row.get("end_date") or ""), str(row.get("start_date") or ""), int(row.get("id") or 0)), reverse=True)


def _latest_overlapping_period(rows: list[dict[str, Any]], start: date, end: date) -> dict[str, Any] | None:
    for row in rows:
        row_start = _parse_date(row.get("start_date"))
        row_end = _parse_date(row.get("end_date"))
        if row_start is None or row_end is None:
            continue
        if row_start <= end and row_end >= start:
            return row
    return None


def _row_profit(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    value = row.get("profit_amount")
    return float(value) if value is not None else None


def _row_return_rate(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    value = row.get("return_rate")
    return float(value) if value is not None else None


def _sum_profit(rows: list[dict[str, Any]]) -> float | None:
    values = [_row_profit(row) for row in rows]
    clean = [value for value in values if value is not None]
    return round(sum(clean), 2) if clean else None


def _max_loss(rows: list[dict[str, Any]]) -> float | None:
    losses = [value for value in (_row_profit(row) for row in rows) if value is not None and value < 0]
    return round(min(losses), 2) if losses else None


def _plain_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(round(float(value), 2))


def _has_loss_amount(row: dict[str, Any]) -> bool:
    return (_optional_nonnegative_number(row.get("loss_amount")) or 0.0) > 0


def _loss_amount_summary(recent_loss: float) -> str:
    if recent_loss > 0:
        return f"${recent_loss:,.2f}"
    return "暂无记录"


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


def _parse_json_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _row_to_dict(columns: list[str], row: tuple) -> dict[str, Any]:
    return {columns[index]: row[index] for index in range(len(columns))}


def _mistake_row_to_dict(columns: list[str], row: tuple) -> dict[str, Any]:
    data = _row_to_dict(columns, row)
    data["mistake_tags"] = _parse_json_list(data.get("mistake_tags_json"))
    data["scene_or_symbol"] = _clean_text(data.get("scene_or_symbol")) or _clean_text(data.get("symbol"))
    data["loss_amount_usd"] = _optional_nonnegative_number(data.get("loss_amount"))
    data["impact_summary"] = _clean_text(data.get("loss_impact_text"))
    return data


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
