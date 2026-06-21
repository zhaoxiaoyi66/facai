from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from data.market_context import build_market_context, build_market_history
from data.prices import CACHE_PATH
from indicators.technicals import add_technical_indicators, latest_technical_snapshot


STRUCTURE_CONFIRMED = "STRUCTURE_CONFIRMED"
STRUCTURE_FORMING = "STRUCTURE_FORMING"
DIP_ONLY = "DIP_ONLY"
STRUCTURE_BROKEN = "STRUCTURE_BROKEN"
DATA_MISSING = "DATA_MISSING"

THESIS_INTACT = "INTACT"
THESIS_WEAKENING = "WEAKENING"
THESIS_BROKEN = "BROKEN"
THESIS_UNKNOWN = "UNKNOWN"

ACCEPTABLE_DECLINE_REASONS = {"宏观冲击", "行业回调", "流动性冲击"}
BROKEN_DECLINE_REASONS = {"公司基本面恶化"}

STATUS_LABELS = {
    STRUCTURE_CONFIRMED: "结构确认",
    STRUCTURE_FORMING: "结构形成中",
    DIP_ONLY: "只是下跌",
    STRUCTURE_BROKEN: "结构破坏",
    DATA_MISSING: "数据不足",
}

STATUS_HINTS = {
    STRUCTURE_CONFIRMED: "结构较好，可结合仓位计划执行。",
    STRUCTURE_FORMING: "结构尚未完全确认，建议小仓或等待确认。",
    DIP_ONLY: "价格回落，但结构证据还不完整，需继续复核承接、主线和相对强弱。",
    STRUCTURE_BROKEN: "基本面或技术结构存在风险。",
    DATA_MISSING: "缺少判断数据。",
}


def _unknown_status_label(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    if all(char.isascii() and (char.isalnum() or char in {"_", "-", "^", ".", " ", "&"}) for char in text):
        return fallback
    return text


@dataclass(frozen=True)
class StructureEntryAdvisor:
    structure_status: str
    structure_score: float
    decline_reason: str
    thesis_status: str
    support_confirmation: str
    close_confirmation: str
    relative_strength_status: str
    volume_confirmation: str
    structure_reasons: list[str] = field(default_factory=list)
    structure_warnings: list[str] = field(default_factory=list)
    next_confirmation_steps: list[str] = field(default_factory=list)
    structure_checked_at: str | None = None

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.structure_status, _unknown_status_label(self.structure_status, "结构待确认"))

    @property
    def action_hint(self) -> str:
        return STATUS_HINTS.get(self.structure_status, "结构提示仅供复核。")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"status_label": self.status_label, "action_hint": self.action_hint}


def evaluate_structure_entry(
    *,
    ticker: str = "",
    technicals: dict[str, Any] | None = None,
    history: pd.DataFrame | None = None,
    decline_reason: str = "未知",
    thesis_status: str = THESIS_UNKNOWN,
    relative_strength_status: str | None = None,
    checked_at: datetime | None = None,
) -> StructureEntryAdvisor:
    enriched = _technical_context(technicals or {}, history)
    price = _first_number(enriched, "price", "close", "current_price")
    if price is None:
        return _missing_advisor("缺少当前价格", decline_reason=decline_reason, checked_at=checked_at)
    if not _has_any_technical_context(enriched):
        return _missing_advisor("缺少 K 线 / EMA / 支撑数据", decline_reason=decline_reason, checked_at=checked_at)

    normalized_decline = _normalize_decline_reason(decline_reason)
    normalized_thesis = _normalize_thesis_status(thesis_status)
    support = _support_confirmation(enriched)
    close = _close_confirmation(enriched, support)
    relative_strength = relative_strength_status or _relative_strength_from_technicals(enriched)
    volume = _volume_confirmation(enriched)

    score = (
        _decline_score(normalized_decline, normalized_thesis)
        + _thesis_score(normalized_thesis)
        + support["score"]
        + close["score"]
        + _relative_volume_score(relative_strength, volume)
    )
    score = max(0.0, min(100.0, round(score, 1)))

    reasons = [
        f"下跌原因：{normalized_decline}。",
        f"主线状态：{_thesis_label(normalized_thesis)}。",
        support["reason"],
        close["reason"],
        f"相对强弱：{relative_strength}。",
        f"量能：{volume}。",
    ]
    warnings: list[str] = []
    steps: list[str] = []

    if normalized_decline in BROKEN_DECLINE_REASONS:
        warnings.append("下跌来自公司基本面恶化，不能当作普通回调。")
    if normalized_thesis == THESIS_BROKEN:
        warnings.append("主线逻辑已破坏，先停止把下跌当买点。")
    if support["broken"]:
        warnings.append("价格跌破关键支撑，缺少承接证据。")
    if close["weak"]:
        warnings.append("收盘确认不足，需观察是否继续创新低或尾盘跳水。")
    if relative_strength in {"弱于 SPY/QQQ", "相对强弱缺失"}:
        steps.append("观察是否重新强于 SPY / QQQ / 行业。")
    if volume in {"量能不足", "量能缺失"}:
        steps.append("观察回踩区内是否缩量止跌或放量承接。")
    if support["broken"]:
        steps.append("等待重新站回 EMA20 / EMA50 或 recent swing low。")
    if close["weak"]:
        steps.append("等待收盘站回关键支撑且不再创新低。")
    if not steps:
        steps.append("结合仓位计划、目标价和情绪状态复核后再执行。")

    data_gaps = _structure_data_gaps(
        thesis_status=normalized_thesis,
        support=support,
        close=close,
        relative_strength=relative_strength,
        volume=volume,
    )
    hard_broken = _has_clear_breakdown_evidence(
        decline_reason=normalized_decline,
        thesis_status=normalized_thesis,
        support=support,
        close=close,
        relative_strength=relative_strength,
    )
    if hard_broken:
        status = STRUCTURE_BROKEN
    elif data_gaps:
        status = DATA_MISSING
    elif score >= 80:
        status = STRUCTURE_CONFIRMED
    elif score >= 60:
        status = STRUCTURE_FORMING
    else:
        status = DIP_ONLY
    if status == DATA_MISSING:
        warnings.append("结构提示缺少关键数据，不能判定结构破坏。")
        warnings.extend(data_gaps)
        steps.append("补齐主线状态、K 线、相对强弱和量能后再复核。")
    if status == DIP_ONLY and not warnings:
        warnings.append("结构证据不足，暂按只是下跌处理。")

    return StructureEntryAdvisor(
        structure_status=status,
        structure_score=score,
        decline_reason=normalized_decline,
        thesis_status=normalized_thesis,
        support_confirmation=support["label"],
        close_confirmation=close["label"],
        relative_strength_status=relative_strength,
        volume_confirmation=volume,
        structure_reasons=_dedupe([item for item in reasons if item]),
        structure_warnings=_dedupe(warnings),
        next_confirmation_steps=_dedupe(steps),
        structure_checked_at=_checked_at(checked_at),
    )


def build_structure_entry_advisor_for_symbol(
    ticker: str,
    *,
    path: Path = CACHE_PATH,
    decline_reason: str = "未知",
    thesis_status: str = THESIS_UNKNOWN,
    now: datetime | None = None,
) -> StructureEntryAdvisor:
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        return _missing_advisor("缺少股票代码", decline_reason=decline_reason, checked_at=now)
    history = build_market_history(symbol, path=path, now=now)
    if history is None or history.empty:
        return _missing_advisor("缺少 K 线历史", decline_reason=decline_reason, checked_at=now)
    with_indicators = add_technical_indicators(history)
    technicals = latest_technical_snapshot(with_indicators)
    latest = with_indicators.iloc[-1].to_dict() if not with_indicators.empty else {}
    technicals.update(
        {
            "open": _number(latest.get("open")),
            "high": _number(latest.get("high")),
            "low": _number(latest.get("low")),
            "close": _number(latest.get("close")),
            "volume": _number(latest.get("volume")),
            "avg_volume_20d": _number(latest.get("avg_volume_20d")),
        }
    )
    market = build_market_context(symbol, path=path, now=now)
    if _number(market.get("currentPrice")) is not None:
        technicals["price"] = _number(market.get("currentPrice"))
    return evaluate_structure_entry(
        ticker=symbol,
        technicals=technicals,
        history=with_indicators,
        decline_reason=decline_reason,
        thesis_status=thesis_status,
        relative_strength_status=_benchmark_relative_strength(symbol, with_indicators, path=path, now=now),
        checked_at=now,
    )


def structure_entry_snapshot_fields(
    advisor: StructureEntryAdvisor,
    *,
    checked_at: datetime | str | None = None,
) -> dict[str, Any]:
    checked_text = checked_at if isinstance(checked_at, str) else _checked_at(checked_at)
    return {
        "structureStatus": advisor.structure_status,
        "structureScore": advisor.structure_score,
        "structureReasons": list(advisor.structure_reasons),
        "structureWarnings": list(advisor.structure_warnings),
        "structureCheckedAt": checked_text or advisor.structure_checked_at,
    }


def structure_entry_summary_text(advisor: StructureEntryAdvisor) -> str:
    score_text = "待补数据" if advisor.structure_status == DATA_MISSING else f"{advisor.structure_score:g}分"
    return f"结构买入提示：{advisor.status_label}｜{score_text}｜{advisor.action_hint}"


def structure_entry_hint_html(advisor: StructureEntryAdvisor) -> str:
    reasons = "；".join(advisor.structure_reasons[:2]) or "暂无结构说明。"
    warnings = "；".join(advisor.structure_warnings[:2])
    steps = "；".join(advisor.next_confirmation_steps[:2])
    warning_html = f"<span>{_escape(warnings)}</span>" if warnings else ""
    decline = "下跌原因待维护" if advisor.decline_reason == "未知" else advisor.decline_reason
    return (
        '<div class="structure-entry-advisor">'
        f"<strong>{_escape(structure_entry_summary_text(advisor))}</strong>"
        f"<span>下跌原因：{_escape(decline)}｜主线：{_escape(_thesis_label(advisor.thesis_status))}</span>"
        f"<span>承接：{_escape(advisor.support_confirmation)}｜收盘：{_escape(advisor.close_confirmation)}｜相对强弱：{_escape(advisor.relative_strength_status)}</span>"
        f"<small>{_escape(reasons)}</small>"
        f"{warning_html}"
        f"<small>下一步：{_escape(steps)}</small>"
        "</div>"
    )


def _technical_context(technicals: dict[str, Any], history: pd.DataFrame | None) -> dict[str, Any]:
    result = dict(technicals or {})
    if history is not None and not history.empty:
        frame = add_technical_indicators(history) if "ema20" not in history.columns else history
        snapshot = latest_technical_snapshot(frame)
        latest = frame.iloc[-1].to_dict()
        result = {
            **snapshot,
            **result,
            "open": _first_present(result.get("open"), latest.get("open")),
            "high": _first_present(result.get("high"), latest.get("high")),
            "low": _first_present(result.get("low"), latest.get("low")),
            "close": _first_present(result.get("close"), latest.get("close")),
            "volume": _first_present(result.get("volume"), latest.get("volume")),
            "avg_volume_20d": _first_present(result.get("avg_volume_20d"), latest.get("avg_volume_20d")),
        }
    return result


def _has_any_technical_context(technicals: dict[str, Any]) -> bool:
    return any(_number(technicals.get(key)) is not None for key in ("ema20", "ema50", "ema200", "recent_swing_low"))


def _support_confirmation(technicals: dict[str, Any]) -> dict[str, Any]:
    price = _first_number(technicals, "price", "close")
    ema20 = _number(technicals.get("ema20"))
    ema50 = _number(technicals.get("ema50"))
    ema200 = _number(technicals.get("ema200"))
    swing = _number(technicals.get("recent_swing_low"))
    atr = _number(technicals.get("atr14")) or (price * 0.02 if price else 0)
    support = max([value for value in (swing, ema50, ema200) if value is not None], default=None)
    reclaimed = [label for label, value in (("EMA20", ema20), ("EMA50", ema50), ("EMA200", ema200)) if price is not None and value and price >= value]
    if price is None or (support is None and not reclaimed):
        return {
            "label": "数据不足",
            "score": 0,
            "reason": "技术承接：缺 K 线、EMA 或 swing 支撑，无法判断承接。",
            "broken": False,
            "missing": True,
        }
    held_support = price is not None and support is not None and price >= support - atr * 0.25
    lower_wick = _has_daily_range(technicals) and _close_position_in_range(technicals) >= 0.55
    score = 0
    reasons: list[str] = []
    if held_support:
        score += 13
        reasons.append("守住关键支撑")
    if reclaimed:
        score += min(10, 4 * len(reclaimed))
        reasons.append("站回 " + "/".join(reclaimed))
    if lower_wick:
        score += 7
        reasons.append("日线收在区间上半部，有下影线承接")
    if not reasons:
        return {"label": "承接不足", "score": 0, "reason": "技术承接：尚未守住支撑或站回均线。", "broken": True, "missing": False}
    label = "承接确认" if score >= 24 else "有初步承接"
    return {"label": label, "score": min(score, 30), "reason": "技术承接：" + "、".join(reasons) + "。", "broken": False, "missing": False}


def _close_confirmation(technicals: dict[str, Any], support: dict[str, Any]) -> dict[str, Any]:
    close_pos = _close_position_in_range(technicals)
    price = _first_number(technicals, "price", "close")
    swing = _number(technicals.get("recent_swing_low"))
    if price is None or (not _has_daily_range(technicals) and swing is None):
        return {
            "label": "数据不足",
            "score": 0,
            "reason": "收盘确认：缺 K 线区间或 swing 支撑，无法判断收盘承接。",
            "weak": False,
            "missing": True,
        }
    not_new_low = price is not None and (swing is None or price >= swing)
    score = 0
    reasons: list[str] = []
    if close_pos >= 0.65:
        score += 8
        reasons.append("收盘靠近日内高位")
    elif close_pos >= 0.5:
        score += 5
        reasons.append("收盘位于日内中上部")
    if not_new_low and not support.get("broken"):
        score += 7
        reasons.append("收盘守住关键支撑且未继续创新低")
    if not reasons:
        return {"label": "收盘未确认", "score": 0, "reason": "收盘确认：未站稳关键支撑或日内位置偏弱。", "weak": True, "missing": False}
    label = "收盘确认" if score >= 12 else "收盘初步确认"
    return {"label": label, "score": min(score, 15), "reason": "收盘确认：" + "、".join(reasons) + "。", "weak": False, "missing": False}


def _relative_strength_from_technicals(technicals: dict[str, Any]) -> str:
    value = _number(technicals.get("relative_strength_20d"))
    if value is None:
        return "相对强弱缺失"
    if value >= 1:
        return "强于 SPY/QQQ"
    if value <= -1:
        return "弱于 SPY/QQQ"
    return "相对中性"


def _volume_confirmation(technicals: dict[str, Any]) -> str:
    trend = _number(technicals.get("volume_trend"))
    volume = _number(technicals.get("volume"))
    avg = _number(technicals.get("avg_volume_20d"))
    if trend is None and volume is not None and avg:
        trend = volume / avg - 1
    if trend is None:
        return "量能缺失"
    if trend >= 0.1:
        return "放量承接"
    if trend >= -0.15:
        return "量能正常"
    return "量能不足"


def _structure_data_gaps(
    *,
    thesis_status: str,
    support: dict[str, Any],
    close: dict[str, Any],
    relative_strength: str,
    volume: str,
) -> list[str]:
    gaps: list[str] = []
    if thesis_status == THESIS_UNKNOWN:
        gaps.append("主线状态未维护，结构判断降级为待确认。")
    if support.get("missing"):
        gaps.append("缺 K 线，无法判断承接。")
    if close.get("missing"):
        gaps.append("缺收盘区间或 swing 数据，无法判断收盘确认。")
    if relative_strength == "相对强弱缺失":
        gaps.append("缺 SPY / QQQ 相对强弱。")
    if volume == "量能缺失":
        gaps.append("缺成交量或 20 日均量。")
    return _dedupe(gaps)


def _has_clear_breakdown_evidence(
    *,
    decline_reason: str,
    thesis_status: str,
    support: dict[str, Any],
    close: dict[str, Any],
    relative_strength: str,
) -> bool:
    if thesis_status == THESIS_BROKEN:
        return True
    if decline_reason in BROKEN_DECLINE_REASONS:
        return True
    return bool(support.get("broken") and close.get("weak") and relative_strength == "弱于 SPY/QQQ")


def _benchmark_relative_strength(
    symbol: str,
    history: pd.DataFrame,
    *,
    path: Path,
    now: datetime | None,
) -> str:
    own_gain = _history_gain_20d(history)
    if own_gain is None:
        return "相对强弱缺失"
    benchmark_gains: list[float] = []
    for benchmark in ("SPY", "QQQ"):
        frame = build_market_history(benchmark, path=path, now=now)
        gain = _history_gain_20d(frame)
        if gain is not None:
            benchmark_gains.append(gain)
    if not benchmark_gains:
        return "相对强弱缺失"
    relative = own_gain - max(benchmark_gains)
    if relative >= 1:
        return "强于 SPY/QQQ"
    if relative <= -1:
        return "弱于 SPY/QQQ"
    return "相对中性"


def _history_gain_20d(history: pd.DataFrame | None) -> float | None:
    if history is None or history.empty or "close" not in history:
        return None
    closes = pd.to_numeric(history["close"], errors="coerce").dropna()
    if len(closes) < 21:
        return None
    start = float(closes.iloc[-21])
    end = float(closes.iloc[-1])
    if start <= 0:
        return None
    return (end / start - 1) * 100


def _decline_score(decline_reason: str, thesis_status: str) -> float:
    if decline_reason in ACCEPTABLE_DECLINE_REASONS:
        return 20
    if decline_reason == "财报冲击" and thesis_status != THESIS_BROKEN:
        return 12
    if decline_reason in BROKEN_DECLINE_REASONS:
        return 0
    return 8


def _thesis_score(thesis_status: str) -> float:
    if thesis_status == THESIS_INTACT:
        return 25
    if thesis_status == THESIS_WEAKENING:
        return 12
    if thesis_status == THESIS_BROKEN:
        return 0
    return 8


def _relative_volume_score(relative_strength: str, volume: str) -> float:
    score = 0
    if relative_strength == "强于 SPY/QQQ":
        score += 5
    elif relative_strength == "相对中性":
        score += 3
    if volume == "放量承接":
        score += 5
    elif volume == "量能正常":
        score += 3
    return score


def _close_position_in_range(technicals: dict[str, Any]) -> float:
    close = _first_number(technicals, "close", "price")
    high = _number(technicals.get("high"))
    low = _number(technicals.get("low"))
    if close is None or high is None or low is None or high <= low:
        return 0.0
    return max(0.0, min(1.0, (close - low) / (high - low)))


def _has_daily_range(technicals: dict[str, Any]) -> bool:
    high = _number(technicals.get("high"))
    low = _number(technicals.get("low"))
    return high is not None and low is not None and high > low


def _normalize_decline_reason(value: str) -> str:
    text = str(value or "").strip()
    aliases = {
        "macro": "宏观冲击",
        "market": "宏观冲击",
        "sector": "行业回调",
        "liquidity": "流动性冲击",
        "earnings": "财报冲击",
        "fundamental": "公司基本面恶化",
        "unknown": "未知",
    }
    if text.lower() in aliases:
        return aliases[text.lower()]
    if text and all(ch.isascii() and (ch.isalnum() or ch in {"_", "-"}) for ch in text):
        return "未知"
    return text if text else "未知"


def _normalize_thesis_status(value: str) -> str:
    text = str(value or "").strip().upper()
    return text if text in {THESIS_INTACT, THESIS_WEAKENING, THESIS_BROKEN, THESIS_UNKNOWN} else THESIS_UNKNOWN


def _thesis_label(value: str) -> str:
    return {
        THESIS_INTACT: "主线仍在",
        THESIS_WEAKENING: "主线转弱",
        THESIS_BROKEN: "主线破坏",
        THESIS_UNKNOWN: "主线待维护",
    }.get(value, "主线待维护")


def _missing_advisor(reason: str, *, decline_reason: str, checked_at: datetime | None) -> StructureEntryAdvisor:
    return StructureEntryAdvisor(
        structure_status=DATA_MISSING,
        structure_score=0,
        decline_reason=_normalize_decline_reason(decline_reason),
        thesis_status=THESIS_UNKNOWN,
        support_confirmation="数据不足",
        close_confirmation="数据不足",
        relative_strength_status="相对强弱缺失",
        volume_confirmation="量能缺失",
        structure_reasons=[reason],
        structure_warnings=["结构提示缺少必要行情或 K 线数据。"],
        next_confirmation_steps=["补齐价格、日线、EMA、成交量和相对强弱后再复核。"],
        structure_checked_at=_checked_at(checked_at),
    )


def _first_number(values: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = _number(values.get(key))
        if number is not None:
            return number
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _checked_at(value: datetime | None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _escape(value: object) -> str:
    from html import escape

    return escape(str(value or ""))


def structure_entry_json(value: StructureEntryAdvisor | dict[str, Any]) -> str:
    payload = value.to_dict() if isinstance(value, StructureEntryAdvisor) else dict(value)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
