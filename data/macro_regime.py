from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd

from data.cache_read_model import CacheReadModel
from data.market_context import build_market_context
from data.prices import CACHE_PATH


FEAR_GREED = "fear_greed"
VIX = "vix"
HY_OAS = "hy_oas"

REGIME_RISK_ON = "风险偏好"
REGIME_NEUTRAL = "中性"
REGIME_RISK_OFF = "风险收缩"
REGIME_STRESS = "压力环境"
REGIME_PANIC = "恐慌环境"
REGIME_DATA_GAP = "数据不足"

INDICATOR_LABELS = {
    FEAR_GREED: "恐惧与贪婪指数",
    VIX: "VIX 波动率指数",
    HY_OAS: "美高收益债信用利差",
}


@dataclass(frozen=True)
class MacroIndicatorSnapshot:
    indicator: str
    value: float | None
    change_1d: float | None = None
    change_5d: float | None = None
    change_20d: float | None = None
    percentile_1y: float | None = None
    percentile_5y: float | None = None
    source: str = "cache/manual"
    updated_at: str | None = None
    is_stale: bool = False
    regime: str = REGIME_DATA_GAP
    risk_score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    action_hints: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return INDICATOR_LABELS.get(self.indicator, self.indicator)


@dataclass(frozen=True)
class MacroRegimeSnapshot:
    regime: str
    risk_score: float
    indicators: list[MacroIndicatorSnapshot]
    reasons: list[str]
    action_hints: list[str]
    updated_at: str | None = None
    is_stale: bool = False
    source: str = "local cache"

    def indicator(self, name: str) -> MacroIndicatorSnapshot | None:
        normalized = _normalize_indicator(name)
        return next((item for item in self.indicators if item.indicator == normalized), None)


class MacroRegimeStore:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def save_indicator(self, snapshot: MacroIndicatorSnapshot) -> None:
        updated_at = snapshot.updated_at or datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(
                """
                INSERT INTO macro_indicator_snapshots (
                    indicator, value, change_1d, change_5d, change_20d,
                    percentile_1y, percentile_5y, source, updated_at, meta_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(indicator) DO UPDATE SET
                    value = excluded.value,
                    change_1d = excluded.change_1d,
                    change_5d = excluded.change_5d,
                    change_20d = excluded.change_20d,
                    percentile_1y = excluded.percentile_1y,
                    percentile_5y = excluded.percentile_5y,
                    source = excluded.source,
                    updated_at = excluded.updated_at,
                    meta_json = excluded.meta_json
                """,
                (
                    _normalize_indicator(snapshot.indicator),
                    snapshot.value,
                    snapshot.change_1d,
                    snapshot.change_5d,
                    snapshot.change_20d,
                    snapshot.percentile_1y,
                    snapshot.percentile_5y,
                    snapshot.source,
                    updated_at,
                    json.dumps(
                        {
                            "reasons": snapshot.reasons,
                            "action_hints": snapshot.action_hints,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.commit()

    def load_indicator(
        self,
        indicator: str,
        *,
        now: datetime | None = None,
        stale_after_hours: float = 36,
    ) -> MacroIndicatorSnapshot | None:
        normalized = _normalize_indicator(indicator)
        with closing(sqlite3.connect(self.path)) as conn:
            if not _table_exists(conn, "macro_indicator_snapshots"):
                return None
            row = conn.execute(
                """
                SELECT indicator, value, change_1d, change_5d, change_20d,
                       percentile_1y, percentile_5y, source, updated_at, meta_json
                FROM macro_indicator_snapshots
                WHERE indicator = ?
                """,
                (normalized,),
            ).fetchone()
        if not row:
            return None
        meta = _json_dict(row[9])
        updated_at = str(row[8] or "") or None
        return MacroIndicatorSnapshot(
            indicator=normalized,
            value=_number(row[1]),
            change_1d=_number(row[2]),
            change_5d=_number(row[3]),
            change_20d=_number(row[4]),
            percentile_1y=_number(row[5]),
            percentile_5y=_number(row[6]),
            source=str(row[7] or "cache/manual"),
            updated_at=updated_at,
            is_stale=_is_stale(updated_at, stale_after_hours, now=now),
            reasons=list(meta.get("reasons") or []),
            action_hints=list(meta.get("action_hints") or []),
        )

    def _ensure_schema(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS macro_indicator_snapshots (
                    indicator TEXT PRIMARY KEY,
                    value REAL,
                    change_1d REAL,
                    change_5d REAL,
                    change_20d REAL,
                    percentile_1y REAL,
                    percentile_5y REAL,
                    source TEXT,
                    updated_at TEXT NOT NULL,
                    meta_json TEXT
                )
                """
            )
            existing = _table_columns(conn, "macro_indicator_snapshots")
            for column, definition in {
                "change_1d": "REAL",
                "change_5d": "REAL",
                "change_20d": "REAL",
                "percentile_1y": "REAL",
                "percentile_5y": "REAL",
                "source": "TEXT",
                "updated_at": "TEXT",
                "meta_json": "TEXT",
            }.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE macro_indicator_snapshots ADD COLUMN {column} {definition}")
            conn.commit()


def load_macro_regime(path: Path = CACHE_PATH, *, now: datetime | None = None) -> MacroRegimeSnapshot:
    store = MacroRegimeStore(path)
    indicators = [
        _load_vix_snapshot(path, now=now) or store.load_indicator(VIX, now=now),
        store.load_indicator(FEAR_GREED, now=now),
        store.load_indicator(HY_OAS, now=now),
    ]
    return evaluate_macro_regime([item for item in indicators if item is not None], now=now)


def evaluate_macro_regime(
    indicators: list[MacroIndicatorSnapshot] | dict[str, MacroIndicatorSnapshot],
    *,
    now: datetime | None = None,
) -> MacroRegimeSnapshot:
    items = list(indicators.values()) if isinstance(indicators, dict) else list(indicators)
    normalized_items = [_with_indicator_regime(item) for item in items]
    by_name = {item.indicator: item for item in normalized_items}
    vix = by_name.get(VIX)
    fear = by_name.get(FEAR_GREED)
    hy = by_name.get(HY_OAS)
    vix_value = _usable_value(vix)
    fear_value = _usable_value(fear)
    hy_value = _usable_value(hy)
    credit_widening = _credit_spread_widening(hy)
    any_stale = any(item.is_stale for item in normalized_items)
    reasons: list[str] = []

    if not normalized_items or all(item.value is None for item in normalized_items):
        return MacroRegimeSnapshot(
            regime=REGIME_DATA_GAP,
            risk_score=60,
            indicators=normalized_items,
            reasons=["宏观指标缺失，不能把缺数据当成风险偏好。"],
            action_hints=_action_hints(REGIME_DATA_GAP),
            updated_at=None,
            is_stale=True,
        )

    if any_stale:
        reasons.append("部分宏观指标已过期，不能据此判断为风险偏好。")
    if vix_value is not None:
        reasons.append(f"VIX 当前 {vix_value:.1f}。")
    if hy_value is not None:
        reasons.append(f"美高收益债信用利差当前 {hy_value:.1f}%。")
    if credit_widening:
        reasons.append("信用利差走阔，风险偏好收缩。")
    if fear_value is not None:
        reasons.append(f"恐惧与贪婪指数当前 {fear_value:.0f}。")

    if vix_value is not None and vix_value > 30 and fear_value is not None and fear_value <= 25:
        regime = REGIME_PANIC
    elif vix_value is not None and vix_value > 25 and (credit_widening or (hy_value is not None and hy_value >= 4.5)):
        regime = REGIME_STRESS
    elif (vix_value is not None and vix_value > 20) or credit_widening or (hy_value is not None and hy_value >= 4.0):
        regime = REGIME_RISK_OFF
    elif (
        not any_stale
        and vix_value is not None
        and vix_value < 15
        and _credit_spread_tightening(hy)
        and (fear_value is None or 45 <= fear_value <= 80)
    ):
        regime = REGIME_RISK_ON
    else:
        regime = REGIME_NEUTRAL

    if any_stale and regime == REGIME_RISK_ON:
        regime = REGIME_NEUTRAL
    risk_score = _macro_risk_score(vix_value, fear_value, hy_value, credit_widening, any_stale)
    return MacroRegimeSnapshot(
        regime=regime,
        risk_score=risk_score,
        indicators=normalized_items,
        reasons=_dedupe(reasons),
        action_hints=_action_hints(regime),
        updated_at=_latest_updated_at(normalized_items),
        is_stale=any_stale,
    )


def macro_regime_status_text(snapshot: MacroRegimeSnapshot) -> str:
    fear = _indicator_value_text(snapshot.indicator(FEAR_GREED), empty="缺")
    vix = _indicator_value_text(snapshot.indicator(VIX), empty="缺")
    hy = _indicator_value_text(snapshot.indicator(HY_OAS), empty="缺", suffix="%")
    hint = snapshot.action_hints[0] if snapshot.action_hints else "按个股纪律执行。"
    return f"大盘环境：{snapshot.regime}｜恐惧与贪婪 {fear}｜VIX {vix}｜高收益债利差 {hy}｜纪律提示：{hint}"


def macro_regime_status_html(snapshot: MacroRegimeSnapshot) -> str:
    tone = _regime_tone(snapshot.regime)
    return (
        f'<div class="macro-regime-status {escape(tone)}">'
        f"<strong>{escape(macro_regime_status_text(snapshot))}</strong>"
        "</div>"
    )


def macro_regime_detail_html(snapshot: MacroRegimeSnapshot) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(item.label)}</td>"
        f"<td>{escape(_indicator_value_text(item, empty='缺'))}{'%' if item.indicator == HY_OAS and item.value is not None else ''}</td>"
        f"<td>{escape(_change_text(item))}</td>"
        f"<td>{escape(item.source or 'cache/manual')}</td>"
        f"<td>{escape('过期' if item.is_stale else '有效')}</td>"
        "</tr>"
        for item in snapshot.indicators
    )
    reasons = "".join(f"<li>{escape(reason)}</li>" for reason in snapshot.reasons) or "<li>暂无宏观判断原因。</li>"
    hints = "".join(f"<li>{escape(hint)}</li>" for hint in snapshot.action_hints) or "<li>按个股纪律执行。</li>"
    return (
        '<section class="macro-regime-detail">'
        f"<div><strong>大盘环境：{escape(snapshot.regime)}</strong><span>只读提示，不改变买卖门禁。</span></div>"
        '<table><thead><tr><th>指标</th><th>当前值</th><th>近期变化</th><th>来源</th><th>状态</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
        f'<div class="macro-regime-detail-grid"><div><b>判断原因</b><ul>{reasons}</ul></div><div><b>纪律提示</b><ul>{hints}</ul></div></div>'
        "</section>"
    )


def macro_regime_trade_hint_text(snapshot: MacroRegimeSnapshot, *, context: str = "buy") -> str:
    prefix = "买入提示" if context == "buy" else "卖出提示"
    if snapshot.regime == REGIME_RISK_OFF:
        hint = "不追涨，A类等回踩；这只是提示，不改变允许新增仓位。"
    elif snapshot.regime == REGIME_STRESS:
        hint = "C类暂停新增，优先复核仓位和现金；这只是提示，不改变门禁。"
    elif snapshot.regime == REGIME_PANIC:
        hint = "只做计划内核心仓，避免情绪化交易；这只是提示，不改变门禁。"
    elif snapshot.regime == REGIME_DATA_GAP:
        hint = "宏观数据不足，先补齐再复核；这只是提示，不改变门禁。"
    else:
        hint = "按个股 Radar、买入计划和纪律门禁执行。"
    return f"大盘环境：{snapshot.regime}｜{prefix}：{hint}"


def _load_vix_snapshot(path: Path, *, now: datetime | None = None) -> MacroIndicatorSnapshot | None:
    for symbol in ("^VIX", "VIX"):
        context = build_market_context(
            symbol,
            path=path,
            now=now,
            quote_max_age_hours=24,
            history_max_age_hours=96,
        )
        value = _number(context.get("currentPrice"))
        if value is None:
            continue
        history = CacheReadModel(
            path,
            now=now,
            quote_max_age_hours=24,
            history_max_age_hours=96,
        ).get_price_history(symbol)
        changes = _history_changes(history, value)
        percentiles = _history_percentiles(history, value)
        return MacroIndicatorSnapshot(
            indicator=VIX,
            value=value,
            change_1d=changes.get("change_1d"),
            change_5d=changes.get("change_5d"),
            change_20d=changes.get("change_20d"),
            percentile_1y=percentiles.get("percentile_1y"),
            percentile_5y=percentiles.get("percentile_5y"),
            source=f"{symbol} local market cache",
            updated_at=str(context.get("fetchedAt") or "") or None,
            is_stale=bool(context.get("isStale")),
        )
    return None


def _with_indicator_regime(snapshot: MacroIndicatorSnapshot) -> MacroIndicatorSnapshot:
    regime, score, reasons, hints = _indicator_regime(snapshot)
    return MacroIndicatorSnapshot(
        indicator=_normalize_indicator(snapshot.indicator),
        value=snapshot.value,
        change_1d=snapshot.change_1d,
        change_5d=snapshot.change_5d,
        change_20d=snapshot.change_20d,
        percentile_1y=snapshot.percentile_1y,
        percentile_5y=snapshot.percentile_5y,
        source=snapshot.source,
        updated_at=snapshot.updated_at,
        is_stale=snapshot.is_stale,
        regime=regime,
        risk_score=score,
        reasons=_dedupe([*snapshot.reasons, *reasons]),
        action_hints=_dedupe([*snapshot.action_hints, *hints]),
    )


def _indicator_regime(snapshot: MacroIndicatorSnapshot) -> tuple[str, float, list[str], list[str]]:
    if snapshot.value is None:
        return REGIME_DATA_GAP, 60, [f"{snapshot.label}缺失。"], ["补齐数据后再判断。"]
    if snapshot.is_stale:
        return REGIME_DATA_GAP, 55, [f"{snapshot.label}数据过期。"], ["过期数据不能当成风险偏好。"]
    value = float(snapshot.value)
    if snapshot.indicator == VIX:
        if value > 30:
            return REGIME_PANIC, 90, ["VIX 高于 30。"], ["只做计划内核心仓。"]
        if value > 25:
            return REGIME_STRESS, 78, ["VIX 高于 25。"], ["降低主动新增节奏。"]
        if value > 20:
            return REGIME_RISK_OFF, 65, ["VIX 高于 20。"], ["不追涨。"]
        if value < 15:
            return REGIME_RISK_ON, 25, ["VIX 低位。"], ["仍按个股纪律执行。"]
        return REGIME_NEUTRAL, 40, ["VIX 中性。"], ["按计划执行。"]
    if snapshot.indicator == FEAR_GREED:
        if value <= 20:
            return REGIME_PANIC, 85, ["恐惧与贪婪指数极低。"], ["避免恐慌杀跌。"]
        if value <= 35:
            return REGIME_RISK_OFF, 62, ["市场情绪偏恐惧。"], ["等待确认。"]
        if value >= 80:
            return REGIME_RISK_ON, 35, ["市场情绪偏贪婪。"], ["不因情绪追涨。"]
        return REGIME_NEUTRAL, 40, ["市场情绪正常。"], ["按计划执行。"]
    if snapshot.indicator == HY_OAS:
        if value >= 7:
            return REGIME_PANIC, 90, ["信用利差进入恐慌区。"], ["优先控制风险。"]
        if value >= 5:
            return REGIME_STRESS, 78, ["信用利差偏高。"], ["减少非核心新增。"]
        if value >= 4 or _credit_spread_widening(snapshot):
            return REGIME_RISK_OFF, 64, ["信用利差走阔或偏高。"], ["不追涨。"]
        return REGIME_NEUTRAL, 35, ["信用利差未显示压力。"], ["按计划执行。"]
    return REGIME_DATA_GAP, 50, [], []


def _macro_risk_score(
    vix: float | None,
    fear: float | None,
    hy: float | None,
    credit_widening: bool,
    stale: bool,
) -> float:
    scores: list[float] = []
    if vix is not None:
        scores.append(max(0, min(100, (vix - 12) * 4.5)))
    if fear is not None:
        scores.append(max(0, min(100, 100 - fear)))
    if hy is not None:
        scores.append(max(0, min(100, (hy - 2.5) * 22)))
    if credit_widening:
        scores.append(65)
    if stale:
        scores.append(55)
    if not scores:
        return 60
    return round(sum(scores) / len(scores), 1)


def _action_hints(regime: str) -> list[str]:
    return {
        REGIME_RISK_ON: ["按个股纪律执行，不因大盘风险偏好追高。"],
        REGIME_NEUTRAL: ["按个股 Radar 和买入计划执行。"],
        REGIME_RISK_OFF: ["不追涨，A类等回踩。"],
        REGIME_STRESS: ["C类暂停新增，优先复核仓位和现金。"],
        REGIME_PANIC: ["只做计划内核心仓，避免情绪化追涨杀跌。"],
        REGIME_DATA_GAP: ["先补齐宏观指标，不把缺数据当成风险偏好。"],
    }.get(regime, ["按个股纪律执行。"])


def _credit_spread_widening(snapshot: MacroIndicatorSnapshot | None) -> bool:
    if snapshot is None or snapshot.value is None or snapshot.is_stale:
        return False
    return any(
        change is not None and change >= threshold
        for change, threshold in (
            (snapshot.change_1d, 0.10),
            (snapshot.change_5d, 0.20),
            (snapshot.change_20d, 0.35),
        )
    )


def _credit_spread_tightening(snapshot: MacroIndicatorSnapshot | None) -> bool:
    if snapshot is None or snapshot.value is None or snapshot.is_stale:
        return False
    if snapshot.value >= 4:
        return False
    changes = [snapshot.change_5d, snapshot.change_20d]
    return any(change is not None and change <= -0.10 for change in changes)


def _usable_value(snapshot: MacroIndicatorSnapshot | None) -> float | None:
    if snapshot is None:
        return None
    return _number(snapshot.value)


def _history_changes(history: pd.DataFrame, current_value: float) -> dict[str, float | None]:
    closes = _numeric_closes(history)
    return {
        "change_1d": _point_change(closes, current_value, 1),
        "change_5d": _point_change(closes, current_value, 5),
        "change_20d": _point_change(closes, current_value, 20),
    }


def _history_percentiles(history: pd.DataFrame, current_value: float) -> dict[str, float | None]:
    closes = _numeric_closes(history)
    return {
        "percentile_1y": _percentile(closes.tail(252), current_value),
        "percentile_5y": _percentile(closes.tail(1260), current_value),
    }


def _numeric_closes(history: pd.DataFrame) -> pd.Series:
    if history is None or history.empty or "close" not in history.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(history["close"], errors="coerce").dropna()


def _point_change(closes: pd.Series, current_value: float, days: int) -> float | None:
    if closes.empty or len(closes) <= days:
        return None
    base = _number(closes.iloc[-days - 1])
    return round(current_value - base, 2) if base is not None else None


def _percentile(closes: pd.Series, current_value: float) -> float | None:
    if closes.empty:
        return None
    return round(float((closes <= current_value).sum()) / float(len(closes)) * 100, 1)


def _indicator_value_text(snapshot: MacroIndicatorSnapshot | None, *, empty: str = "—", suffix: str = "") -> str:
    if snapshot is None or snapshot.value is None:
        return empty
    value = float(snapshot.value)
    text = f"{value:.0f}" if snapshot.indicator == FEAR_GREED else f"{value:.1f}"
    return f"{text}{suffix}"


def _change_text(snapshot: MacroIndicatorSnapshot) -> str:
    parts = []
    for label, value in (("1日", snapshot.change_1d), ("5日", snapshot.change_5d), ("20日", snapshot.change_20d)):
        if value is not None:
            parts.append(f"{label} {value:+.2f}")
    return " / ".join(parts) if parts else "—"


def _regime_tone(regime: str) -> str:
    return {
        REGIME_RISK_ON: "ok",
        REGIME_NEUTRAL: "neutral",
        REGIME_RISK_OFF: "warning",
        REGIME_STRESS: "stress",
        REGIME_PANIC: "panic",
        REGIME_DATA_GAP: "missing",
    }.get(regime, "neutral")


def _latest_updated_at(items: list[MacroIndicatorSnapshot]) -> str | None:
    parsed = [_parse_datetime(item.updated_at) for item in items if item.updated_at]
    parsed = [item for item in parsed if item is not None]
    if not parsed:
        return None
    return max(parsed).isoformat()


def _is_stale(value: str | None, stale_after_hours: float, *, now: datetime | None = None) -> bool:
    parsed = _parse_datetime(value)
    if parsed is None:
        return True
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) - parsed > timedelta(hours=stale_after_hours)


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_dict(value: object) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_indicator(value: object) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "fear & greed": FEAR_GREED,
        "fear_greed": FEAR_GREED,
        "cnn_fear_greed": FEAR_GREED,
        "vix": VIX,
        "^vix": VIX,
        "hy_oas": HY_OAS,
        "bamlh0a0hym2": HY_OAS,
        "hy spread": HY_OAS,
    }
    return aliases.get(text, text)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result
