from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from data.prices import CACHE_PATH


BUY_ATTENTION_FLAG_LABELS = [
    "新增小仓风险",
    "怕错过风险",
    "无下跌预案",
    "长期跟踪不足",
    "组合碎片化风险",
]

SELL_ATTENTION_FLAG_LABELS = [
    "临时卖出风险",
    "卖出依据不清",
    "卖出比例未想清楚",
    "资金安排不清",
    "无回补预案",
    "卖出后组合不清晰",
]

TRADE_INTENT_ATTENTION_FLAG_LABELS = BUY_ATTENTION_FLAG_LABELS + SELL_ATTENTION_FLAG_LABELS


BUY_INTENT_FIELDS = {
    "primary_intent": [
        "计划内买入",
        "加深已有方向",
        "回补前次卖出",
        "试探观察仓",
        "价格到位执行",
        "怕错过",
        "参与感小仓",
    ],
    "position_intent": [
        "让组合更集中",
        "保持现有结构",
        "替换其他持仓",
        "提高现金使用",
        "小仓观察",
    ],
    "timing_intent": [
        "到达计划价",
        "量价承接改善",
        "分批第一笔",
        "分批追加",
        "临时决定",
    ],
    "risk_intent": [
        "按计划执行",
        "接受波动后复盘",
        "先小额观察",
        "不确定但想参与",
    ],
}

SELL_INTENT_FIELDS = {
    "primary_intent": [
        "计划内止盈",
        "计划内止损",
        "降低仓位风险",
        "换仓",
        "清仓结束跟踪",
        "情绪压力",
        "释放现金",
    ],
    "position_intent": [
        "降低集中度",
        "释放现金",
        "保留底仓",
        "完全退出",
        "等待回补",
    ],
    "timing_intent": [
        "到达目标价",
        "跌破计划线",
        "财报前调整",
        "事件后兑现",
        "临时决定",
    ],
    "risk_intent": [
        "按计划执行",
        "担心继续回撤",
        "担心卖飞",
        "先降低情绪压力",
    ],
}

INTENT_FIELD_LABELS = {
    "primary_intent": "这笔交易主要是",
    "position_intent": "仓位意图",
    "timing_intent": "触发原因",
    "risk_intent": "当下真实状态",
}

BUY_INTENT_QUESTIONS = [
    {
        "field": "core_direction_intent",
        "question": "这笔买入是在加强我的核心方向吗？",
        "options": [
            "是，在加强核心方向",
            "不是，只是新增一个小仓",
            "还没想清楚",
        ],
        "attention": "新增小仓风险",
    },
    {
        "field": "objective_reason_intent",
        "question": "我现在买入的客观理由是什么？",
        "options": [
            "承接变好 / 回到买区 / 赔率合适",
            "怕错过上涨 / 被别人观点影响",
            "还没想清楚",
        ],
        "attention": "怕错过风险",
    },
    {
        "field": "drawdown_plan_intent",
        "question": "如果买入后继续跌 10%-15%，我有处理计划吗？",
        "options": [
            "有，已想好持有、加仓或止错计划",
            "没有，跌了大概率会焦虑",
            "还没想清楚",
        ],
        "attention": "无下跌预案",
    },
    {
        "field": "tracking_commitment_intent",
        "question": "这家公司我愿意长期跟踪吗？",
        "options": [
            "愿意，后续会持续跟踪和复盘",
            "不太愿意，只是临时觉得有机会",
            "还没想清楚",
        ],
        "attention": "长期跟踪不足",
    },
    {
        "field": "portfolio_clarity_intent",
        "question": "买完以后，我的组合会更清晰吗？",
        "options": [
            "会，更聚焦于核心方向",
            "不会，会让组合更碎片化",
            "还没想清楚",
        ],
        "attention": "组合碎片化风险",
    },
]

SELL_INTENT_QUESTIONS = [
    {
        "field": "sell_reason_intent",
        "question": "我为什么卖出？",
        "options": [
            "计划内止盈 / 止错 / 减仓 / 仓位控制",
            "短期波动让我不舒服，想先卖了再说",
            "还没想清楚",
        ],
        "attention": "临时卖出风险",
    },
    {
        "field": "sell_basis_intent",
        "question": "这笔卖出的核心依据是什么？",
        "options": [
            "基本面变差 / 技术破位 / 估值极端 / 仓位过重",
            "别人唱空 / 短期下跌 / 盘中情绪影响",
            "还没想清楚",
        ],
        "attention": "卖出依据不清",
    },
    {
        "field": "sell_size_intent",
        "question": "这次卖出多少，是否想清楚了？",
        "options": [
            "只减一部分，保留核心仓或观察仓",
            "全部卖出，暂时退出这只股票",
            "还没想清楚卖多少",
        ],
        "attention": "卖出比例未想清楚",
        "attention_on": [2],
    },
    {
        "field": "capital_plan_intent",
        "question": "卖出后的资金安排是什么？",
        "options": [
            "提高现金 / 降低风险 / 等更好买点",
            "换入我认为更好的机会",
            "还没想清楚",
        ],
        "attention": "资金安排不清",
        "attention_on": [2],
    },
    {
        "field": "rebound_plan_intent",
        "question": "如果卖出后股价继续上涨，我怎么处理？",
        "options": [
            "有明确回补条件，或者明确接受不回补",
            "没有计划，涨了可能会后悔或追回",
            "还没想清楚",
        ],
        "attention": "无回补预案",
    },
    {
        "field": "portfolio_clarity_after_sell_intent",
        "question": "卖完以后，我的组合会更清晰吗？",
        "options": [
            "会，减少噪音、降低风险或让仓位更聚焦",
            "不会，可能卖掉核心仓，或者制造新的换股冲动",
            "还没想清楚",
        ],
        "attention": "卖出后组合不清晰",
    },
]


class TradeIntentStore:
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
                CREATE TABLE IF NOT EXISTS trade_intent_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_entry_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    intent_side TEXT NOT NULL,
                    primary_intent TEXT,
                    position_intent TEXT,
                    timing_intent TEXT,
                    risk_intent TEXT,
                    source TEXT,
                    payload_json TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(trade_entry_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trade_intent_records_symbol_date
                ON trade_intent_records(symbol, created_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_intent_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    review_type TEXT NOT NULL,
                    question_1_answer TEXT,
                    question_2_answer TEXT,
                    question_3_answer TEXT,
                    question_4_answer TEXT,
                    question_5_answer TEXT,
                    question_6_answer TEXT,
                    attention_flags_json TEXT,
                    setup_score_snapshot REAL,
                    technical_structure_score_snapshot REAL,
                    volume_acceptance_score_snapshot REAL,
                    risk_reward_score_snapshot REAL,
                    buy_zone_context_snapshot TEXT,
                    buy_zone_display_snapshot TEXT,
                    position_quantity_snapshot REAL,
                    position_weight_snapshot REAL,
                    payload_json TEXT,
                    source TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(trade_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trade_intent_reviews_symbol_date
                ON trade_intent_reviews(symbol, created_at)
                """
            )

    def save_intent(
        self,
        trade_entry_id: int,
        symbol: str,
        action_type: str,
        intent: dict[str, Any],
        *,
        source: str = "trade_entry",
        snapshots: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_id = int(trade_entry_id)
        if clean_id <= 0:
            raise ValueError("trade_entry_id is required")
        normalized = normalize_trade_intent_payload(intent)
        if not normalized:
            return {}
        now = _hkt_now()
        ticker = str(symbol or "").strip().upper()
        action = str(action_type or "").strip().lower()
        side = normalized["intent_side"]
        review_type = intent_title(side)
        answers = _review_answers(normalized)
        attention_flags = _attention_flags(normalized)
        snapshot_values = _clean_snapshot_values(snapshots or {})
        payload_json = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO trade_intent_reviews (
                    trade_id,
                    symbol,
                    side,
                    review_type,
                    question_1_answer,
                    question_2_answer,
                    question_3_answer,
                    question_4_answer,
                    question_5_answer,
                    question_6_answer,
                    attention_flags_json,
                    setup_score_snapshot,
                    technical_structure_score_snapshot,
                    volume_acceptance_score_snapshot,
                    risk_reward_score_snapshot,
                    buy_zone_context_snapshot,
                    buy_zone_display_snapshot,
                    position_quantity_snapshot,
                    position_weight_snapshot,
                    payload_json,
                    source,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    side = excluded.side,
                    review_type = excluded.review_type,
                    question_1_answer = excluded.question_1_answer,
                    question_2_answer = excluded.question_2_answer,
                    question_3_answer = excluded.question_3_answer,
                    question_4_answer = excluded.question_4_answer,
                    question_5_answer = excluded.question_5_answer,
                    question_6_answer = excluded.question_6_answer,
                    attention_flags_json = excluded.attention_flags_json,
                    setup_score_snapshot = excluded.setup_score_snapshot,
                    technical_structure_score_snapshot = excluded.technical_structure_score_snapshot,
                    volume_acceptance_score_snapshot = excluded.volume_acceptance_score_snapshot,
                    risk_reward_score_snapshot = excluded.risk_reward_score_snapshot,
                    buy_zone_context_snapshot = excluded.buy_zone_context_snapshot,
                    buy_zone_display_snapshot = excluded.buy_zone_display_snapshot,
                    position_quantity_snapshot = excluded.position_quantity_snapshot,
                    position_weight_snapshot = excluded.position_weight_snapshot,
                    payload_json = excluded.payload_json,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_id,
                    ticker,
                    side,
                    review_type,
                    answers[0],
                    answers[1],
                    answers[2],
                    answers[3],
                    answers[4],
                    answers[5],
                    json.dumps(attention_flags, ensure_ascii=False),
                    snapshot_values["setup_score_snapshot"],
                    snapshot_values["technical_structure_score_snapshot"],
                    snapshot_values["volume_acceptance_score_snapshot"],
                    snapshot_values["risk_reward_score_snapshot"],
                    snapshot_values["buy_zone_context_snapshot"],
                    snapshot_values["buy_zone_display_snapshot"],
                    snapshot_values["position_quantity_snapshot"],
                    snapshot_values["position_weight_snapshot"],
                    payload_json,
                    str(source or "trade_entry"),
                    now,
                    now,
                ),
            )
        return self.get_intent_for_trade(clean_id) or normalized

    def get_intent_for_trade(self, trade_entry_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM trade_intent_reviews WHERE trade_id = ?", (int(trade_entry_id),))
            row = cursor.fetchone()
            columns = [item[0] for item in cursor.description] if cursor.description else []
            if row:
                return _review_row_to_dict(columns, row)
            cursor = conn.execute("SELECT * FROM trade_intent_records WHERE trade_entry_id = ?", (int(trade_entry_id),))
            row = cursor.fetchone()
            columns = [item[0] for item in cursor.description] if cursor.description else []
        return _row_to_dict(columns, row) if row else None

    def list_intents(self, symbol: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if symbol:
                cursor = conn.execute(
                    """
                    SELECT *
                    FROM trade_intent_reviews
                    WHERE UPPER(symbol) = ?
                    ORDER BY created_at DESC, id DESC
                    """,
                    (str(symbol).strip().upper(),),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT *
                    FROM trade_intent_reviews
                    ORDER BY created_at DESC, id DESC
                """
                )
            rows = cursor.fetchall()
            columns = [item[0] for item in cursor.description] if cursor.description else []
        return [_review_row_to_dict(columns, row) for row in rows]


def build_trade_intent_review_stats(
    entries: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    *,
    current_date: date | str | None = None,
) -> dict[str, Any]:
    current = _parse_date(current_date) or datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
    return {
        "seven_days": _period_review_stats(entries, reviews, current=current, days=7),
        "thirty_days": _period_review_stats(entries, reviews, current=current, days=30),
    }


def normalize_trade_intent_payload(payload: Any, *, side: str | None = None) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    clean_side = _clean_side(payload.get("intent_side") or payload.get("side") or side)
    if not clean_side:
        return {}
    if clean_side == "buy":
        return _normalize_buy_intent_payload(payload)
    if clean_side == "sell":
        return _normalize_sell_intent_payload(payload)
    options = BUY_INTENT_FIELDS if clean_side == "buy" else SELL_INTENT_FIELDS
    result: dict[str, str] = {"intent_side": clean_side}
    for field, allowed in options.items():
        value = str(payload.get(field) or "").strip()
        if value not in allowed:
            value = allowed[0]
        result[field] = value
    return result


def _normalize_buy_intent_payload(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {"intent_side": "buy"}
    for item in BUY_INTENT_QUESTIONS:
        field = str(item["field"])
        options = list(item["options"])
        value = str(payload.get(field) or "").strip()
        if value not in options:
            value = options[-1]
        result[field] = value
    result["primary_intent"] = result["core_direction_intent"]
    result["position_intent"] = result["portfolio_clarity_intent"]
    result["timing_intent"] = result["objective_reason_intent"]
    result["risk_intent"] = result["drawdown_plan_intent"]
    attention = buy_intent_attention_points(result)
    result["attention_points"] = json.dumps(attention, ensure_ascii=False)
    return result


def buy_intent_attention_points(payload: dict[str, Any]) -> list[str]:
    points: list[str] = []
    for item in BUY_INTENT_QUESTIONS:
        field = str(item["field"])
        options = list(item["options"])
        value = str(payload.get(field) or "").strip()
        attention_on = set(int(index) for index in item.get("attention_on", [1, 2]))
        if any(0 <= index < len(options) and value == options[index] for index in attention_on):
            points.append(str(item["attention"]))
    return points


def _normalize_sell_intent_payload(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {"intent_side": "sell"}
    for item in SELL_INTENT_QUESTIONS:
        field = str(item["field"])
        options = list(item["options"])
        value = str(payload.get(field) or "").strip()
        if value not in options:
            value = options[-1]
        result[field] = value
    result["primary_intent"] = result["sell_reason_intent"]
    result["position_intent"] = result["sell_size_intent"]
    result["timing_intent"] = result["sell_basis_intent"]
    result["risk_intent"] = result["rebound_plan_intent"]
    attention = sell_intent_attention_points(result)
    result["attention_points"] = json.dumps(attention, ensure_ascii=False)
    return result


def sell_intent_attention_points(payload: dict[str, Any]) -> list[str]:
    points: list[str] = []
    for item in SELL_INTENT_QUESTIONS:
        field = str(item["field"])
        options = list(item["options"])
        value = str(payload.get(field) or "").strip()
        attention_on = set(int(index) for index in item.get("attention_on", [1, 2]))
        if any(0 <= index < len(options) and value == options[index] for index in attention_on):
            points.append(str(item["attention"]))
    return points


def intent_side_for_action(action_type: object) -> str:
    action = str(action_type or "").strip().lower()
    if action in {"sell", "trim", "close", "exit"}:
        return "sell"
    return "buy"


def intent_title(side: str) -> str:
    return "卖出前记录" if str(side or "").strip().lower() == "sell" else "买入前记录"


def _review_answers(payload: dict[str, Any]) -> list[str | None]:
    side = str(payload.get("intent_side") or "").strip().lower()
    questions = SELL_INTENT_QUESTIONS if side == "sell" else BUY_INTENT_QUESTIONS
    answers = [str(payload.get(str(item["field"])) or "").strip() or None for item in questions]
    while len(answers) < 6:
        answers.append(None)
    return answers[:6]


def _attention_flags(payload: dict[str, Any]) -> list[str]:
    side = str(payload.get("intent_side") or "").strip().lower()
    if side == "sell":
        return sell_intent_attention_points(payload)
    if side == "buy":
        return buy_intent_attention_points(payload)
    return []


def _clean_snapshot_values(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "setup_score_snapshot": _number(values.get("setup_score_snapshot") or values.get("setup_score")),
        "technical_structure_score_snapshot": _number(
            values.get("technical_structure_score_snapshot") or values.get("technical_structure_score")
        ),
        "volume_acceptance_score_snapshot": _number(
            values.get("volume_acceptance_score_snapshot") or values.get("volume_acceptance_score")
        ),
        "risk_reward_score_snapshot": _number(values.get("risk_reward_score_snapshot") or values.get("risk_reward_score")),
        "buy_zone_context_snapshot": _json_snapshot(values.get("buy_zone_context_snapshot") or values.get("buy_zone_context")),
        "buy_zone_display_snapshot": _json_snapshot(values.get("buy_zone_display_snapshot") or values.get("buy_zone_display")),
        "position_quantity_snapshot": _number(values.get("position_quantity_snapshot") or values.get("position_quantity")),
        "position_weight_snapshot": _number(values.get("position_weight_snapshot") or values.get("position_weight")),
    }


def _json_snapshot(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False)


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _period_review_stats(
    entries: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    *,
    current: date,
    days: int,
) -> dict[str, Any]:
    start = current - timedelta(days=max(1, int(days)) - 1)
    period_entries = [entry for entry in entries if _is_trade_entry(entry) and _date_in_range(entry.get("trade_date"), start, current)]
    period_reviews = [review for review in reviews if _date_in_range(review.get("created_at"), start, current)]
    flag_counts = {flag: 0 for flag in TRADE_INTENT_ATTENTION_FLAG_LABELS}
    attention_review_count = 0
    buy_review_count = 0
    sell_review_count = 0
    low_setup_buy_count = 0
    low_volume_acceptance_buy_count = 0

    for review in period_reviews:
        side = str(review.get("intent_side") or review.get("side") or "").strip().lower()
        if side == "buy":
            buy_review_count += 1
            setup_score = _number(review.get("setup_score_snapshot"))
            volume_score = _number(review.get("volume_acceptance_score_snapshot"))
            if setup_score is not None and setup_score < 70:
                low_setup_buy_count += 1
            if volume_score is not None and volume_score < 50:
                low_volume_acceptance_buy_count += 1
        elif side == "sell":
            sell_review_count += 1

        flags = review.get("attention_flags")
        if not isinstance(flags, list):
            flags = _loads_list(review.get("attention_flags_json"))
        clean_flags = [str(flag) for flag in flags if str(flag or "").strip()]
        if clean_flags:
            attention_review_count += 1
        for flag in clean_flags:
            if flag in flag_counts:
                flag_counts[flag] += 1

    return {
        "days": days,
        "trade_count": len(period_entries),
        "attention_trade_count": attention_review_count,
        "buy_review_count": buy_review_count,
        "sell_review_count": sell_review_count,
        "low_setup_buy_count": low_setup_buy_count,
        "low_volume_acceptance_buy_count": low_volume_acceptance_buy_count,
        "attention_flag_counts": flag_counts,
    }


def _is_trade_entry(entry: dict[str, Any]) -> bool:
    return str(entry.get("action_type") or "").strip().lower() in {"buy", "add", "sell", "trim"}


def _date_in_range(value: object, start: date, end: date) -> bool:
    parsed = _parse_date(value)
    return bool(parsed and start <= parsed <= end)


def _parse_date(value: date | str | object | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _loads_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    text = str(value or "").strip()
    if not text:
        return []
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return []
    return result if isinstance(result, list) else []


def _clean_side(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"buy", "sell"}:
        return text
    return ""


def _hkt_now() -> str:
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds")


def _row_to_dict(columns: list[str], row: Any) -> dict[str, Any]:
    result = dict(zip(columns, row))
    raw_payload = str(result.get("payload_json") or "").strip()
    try:
        result["payload"] = json.loads(raw_payload) if raw_payload else {}
    except json.JSONDecodeError:
        result["payload"] = {}
    return result


def _review_row_to_dict(columns: list[str], row: Any) -> dict[str, Any]:
    result = dict(zip(columns, row))
    raw_payload = str(result.get("payload_json") or "").strip()
    try:
        payload = json.loads(raw_payload) if raw_payload else {}
    except json.JSONDecodeError:
        payload = {}
    raw_flags = str(result.get("attention_flags_json") or "").strip()
    try:
        flags = json.loads(raw_flags) if raw_flags else []
    except json.JSONDecodeError:
        flags = []
    side = str(result.get("side") or payload.get("intent_side") or "").strip().lower()
    result["payload"] = payload
    result["attention_flags"] = flags
    result["trade_entry_id"] = result.get("trade_id")
    result["action_type"] = side
    result["intent_side"] = side
    result["primary_intent"] = payload.get("primary_intent") or result.get("question_1_answer")
    result["position_intent"] = payload.get("position_intent") or result.get("question_3_answer")
    result["timing_intent"] = payload.get("timing_intent") or result.get("question_2_answer")
    result["risk_intent"] = payload.get("risk_intent") or result.get("question_5_answer")
    return result
