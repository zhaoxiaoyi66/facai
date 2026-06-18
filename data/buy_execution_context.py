from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from data.action_fusion import ActionFusionResult, action_fusion_card_html, evaluate_action_fusion
from data.ai_stock_radar import build_cached_ai_stock_radar_report
from data.macro_regime import load_macro_regime
from data.market_context import build_market_context, build_market_history
from data.portfolio_structure_health import build_portfolio_structure_check
from data.portfolio_targets import apply_portfolio_target
from data.portfolio_view_model import build_portfolio_view_model
from data.prices import CACHE_PATH
from data.pullback_acceptance import (
    PullbackAcceptanceSnapshot,
    evaluate_pullback_acceptance,
    pullback_acceptance_context_lines,
    pullback_acceptance_hint_html,
)
from data.structure_entry import (
    DATA_MISSING,
    StructureEntryAdvisor,
    build_structure_entry_advisor_for_symbol,
)
from data.volume_price_acceptance import (
    VolumePriceAcceptanceSnapshot,
    evaluate_volume_price_acceptance,
    volume_price_acceptance_hint_html,
)


STRUCTURE_OK = "STRUCTURE_OK"
STRUCTURE_PARTIAL = "STRUCTURE_PARTIAL"
STRUCTURE_STALE = "STRUCTURE_STALE"
STRUCTURE_MISSING = "STRUCTURE_MISSING"

STRUCTURE_LABELS = {
    STRUCTURE_OK: "结构可参考",
    STRUCTURE_PARTIAL: "结构待确认",
    STRUCTURE_STALE: "结构信息偏旧",
    STRUCTURE_MISSING: "待补数据",
}

STRUCTURE_MESSAGES = {
    STRUCTURE_OK: "已有关键技术结构，可结合买区提示和仓位计划判断。",
    STRUCTURE_PARTIAL: "已有部分结构信息，但缺少相对强弱 / 量能 / 下跌原因等辅助确认。",
    STRUCTURE_STALE: "沿用最近一次技术结构，建议先更新技术。",
    STRUCTURE_MISSING: "缺少价格 / K线 / 均线 / swing，暂时无法生成结构判断。",
}

TECHNICAL_MAP_STATUSES = {
    "UPTREND_PULLBACK",
    "WEAK_TREND_REPAIR",
    "BREAKDOWN_REVIEW",
    "RANGE_BASE_BUILDING",
}


@dataclass(frozen=True)
class BuyExecutionStructureHint:
    status: str
    label: str
    message: str
    source: str
    structure_status: str | None = None
    structure_score: float | None = None
    missing_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BuyExecutionAdvisoryContext:
    ticker: str
    current_price: float | None
    radar_report: dict[str, Any]
    structure_hint: BuyExecutionStructureHint
    structure_advisor: StructureEntryAdvisor | None
    pullback_acceptance: PullbackAcceptanceSnapshot
    volume_price_acceptance: VolumePriceAcceptanceSnapshot
    action_fusion: ActionFusionResult
    macro_regime: str | None
    portfolio_structure_status: str | None
    data_source_text: str
    technical_freshness: str
    radar_freshness: str
    macro_freshness: str


def build_buy_execution_advisory_context(
    ticker: str,
    *,
    path: Path = CACHE_PATH,
    radar_report: object | None = None,
    now: datetime | None = None,
) -> BuyExecutionAdvisoryContext:
    symbol = str(ticker or "").strip().upper()
    current_time = _as_utc(now or datetime.now(timezone.utc))
    report = _normalize_report(radar_report)
    if not report and symbol:
        report = _load_cached_radar_report(symbol, path=path, now=current_time)

    market = _safe_market_context(symbol, path=path, now=current_time)
    current_price = _first_number(
        _value(report, "current_price", "currentPrice", "price"),
        _value(market, "currentPrice", "current_price", "price"),
    )

    structure_advisor = _advisor_from_report(report)
    structure_hint = _build_structure_hint(
        symbol=symbol,
        report=report,
        market=market,
        path=path,
        now=current_time,
        structure_advisor=structure_advisor,
    )
    pullback_acceptance = evaluate_pullback_acceptance(
        ticker=symbol,
        technicals={**report, **market},
        checked_at=current_time,
    )
    volume_price_acceptance = evaluate_volume_price_acceptance(
        ticker=symbol,
        daily_bars=_safe_market_history(symbol, path=path, now=current_time),
        technicals={**report, **market},
        checked_at=current_time,
    )
    portfolio_context = _portfolio_context_for_symbol(symbol, path=path)
    action_fusion = evaluate_action_fusion(
        ticker=symbol,
        context={
            **report,
            **market,
            "volume_price_status": volume_price_acceptance.volume_price_status,
            "volume_price_score": volume_price_acceptance.volume_price_score,
            "volume_ratio": volume_price_acceptance.volume_ratio,
            "volume_regime_cn": volume_price_acceptance.volume_regime_cn,
            "volume_price_reason_cn": volume_price_acceptance.acceptance_reason_cn,
            "pullback_acceptance_status": pullback_acceptance.acceptance_status,
            "pullback_acceptance_score": pullback_acceptance.acceptance_score,
        },
        portfolio_context=portfolio_context,
    )
    macro_regime, macro_freshness = _macro_context(path=path, now=current_time)
    portfolio_structure_status = _portfolio_structure_status(path=path, macro_regime=None)
    technical_freshness = _technical_freshness(report, market, current_time)
    radar_freshness = _radar_freshness(report, market, current_time)
    data_source_text = f"技术：{technical_freshness}｜买区提示：{radar_freshness}｜宏观：{macro_freshness}"
    return BuyExecutionAdvisoryContext(
        ticker=symbol,
        current_price=current_price,
        radar_report=report,
        structure_hint=structure_hint,
        structure_advisor=structure_advisor,
        pullback_acceptance=pullback_acceptance,
        volume_price_acceptance=volume_price_acceptance,
        action_fusion=action_fusion,
        macro_regime=macro_regime,
        portfolio_structure_status=portfolio_structure_status,
        data_source_text=data_source_text,
        technical_freshness=technical_freshness,
        radar_freshness=radar_freshness,
        macro_freshness=macro_freshness,
    )


def buy_execution_advisory_context_html(context: BuyExecutionAdvisoryContext) -> str:
    hint = context.structure_hint
    details = _join_text([*hint.warnings[:2], *hint.next_steps[:2]])
    detail_html = f"<small>{escape(details)}</small>" if details else ""
    structure_html = (
        '<div class="structure-entry-advisor buy-execution-advisory-context">'
        f"<strong>结构买入提示：{escape(hint.label)}</strong>"
        f"<span>{escape(hint.message)}</span>"
        f"<span>数据来源：{escape(context.data_source_text)}</span>"
        f"{detail_html}"
        "<small>仅作买入提示，不阻止真实买入 / 加仓入账。</small>"
        "</div>"
    )
    acceptance_context = {**context.radar_report, "current_price": context.current_price}
    context_lines = pullback_acceptance_context_lines(context.pullback_acceptance, acceptance_context)
    return (
        action_fusion_card_html(context.action_fusion)
        + structure_html
        + pullback_acceptance_hint_html(context.pullback_acceptance, context_lines=context_lines)
        + volume_price_acceptance_hint_html(context.volume_price_acceptance)
    )


def _build_structure_hint(
    *,
    symbol: str,
    report: dict[str, Any],
    market: dict[str, Any],
    path: Path,
    now: datetime,
    structure_advisor: StructureEntryAdvisor | None,
) -> BuyExecutionStructureHint:
    stale = _technical_cache_is_stale(report, market)
    if stale and (_has_technical_structure_map(report) or _has_core_structure_context(report, market)):
        return _hint(
            STRUCTURE_STALE,
            source="radar_technical_structure",
            structure_advisor=structure_advisor,
            missing_fields=_technical_missing_fields(report),
            warnings=["技术结构缓存偏旧，建议点击“更新技术”后再复核。"],
            next_steps=_next_technical_steps(report),
        )

    if _has_technical_structure_map(report):
        missing = _auxiliary_missing_fields(report, structure_advisor)
        status = STRUCTURE_PARTIAL if missing else STRUCTURE_OK
        return _hint(
            status,
            source="radar_technical_structure",
            structure_advisor=structure_advisor,
            missing_fields=missing,
            warnings=_technical_warnings(report, missing),
            next_steps=_next_technical_steps(report),
        )

    if structure_advisor is None and symbol:
        structure_advisor = _safe_structure_advisor(symbol, path=path, now=now)
    if structure_advisor is not None and structure_advisor.structure_status != DATA_MISSING:
        return _hint(
            STRUCTURE_OK,
            source="structure_entry_advisor",
            structure_advisor=structure_advisor,
            warnings=list(structure_advisor.structure_warnings),
            next_steps=list(structure_advisor.next_confirmation_steps),
        )

    if _has_core_structure_context(report, market):
        missing = _auxiliary_missing_fields(report, structure_advisor)
        if not missing:
            missing = ["relative_strength", "volume", "decline_reason"]
        return _hint(
            STRUCTURE_PARTIAL,
            source="partial_technical_context",
            structure_advisor=structure_advisor,
            missing_fields=missing,
            warnings=["已有价格 / 均线 / 支撑等核心结构，但辅助确认不足。"],
            next_steps=_next_technical_steps(report) or ["补齐相对强弱、量能和下跌原因后再复核。"],
        )

    missing = _core_missing_fields(report, market)
    if structure_advisor is not None:
        missing.extend(list(structure_advisor.structure_warnings))
    return _hint(
        STRUCTURE_MISSING,
        source="missing_core_context",
        structure_advisor=structure_advisor,
        missing_fields=_dedupe(missing),
        warnings=["缺少核心技术上下文，买入页不作结构判断。"],
        next_steps=["点击“更新技术”，补齐价格历史、均线和 swing 数据。"],
    )


def _hint(
    status: str,
    *,
    source: str,
    structure_advisor: StructureEntryAdvisor | None = None,
    missing_fields: list[str] | None = None,
    warnings: list[str] | None = None,
    next_steps: list[str] | None = None,
) -> BuyExecutionStructureHint:
    return BuyExecutionStructureHint(
        status=status,
        label=STRUCTURE_LABELS[status],
        message=STRUCTURE_MESSAGES[status],
        source=source,
        structure_status=getattr(structure_advisor, "structure_status", None),
        structure_score=_number(getattr(structure_advisor, "structure_score", None)),
        missing_fields=_dedupe(missing_fields or []),
        warnings=_dedupe(warnings or []),
        next_steps=_dedupe(next_steps or []),
    )


def _load_cached_radar_report(symbol: str, *, path: Path, now: datetime) -> dict[str, Any]:
    try:
        report = build_cached_ai_stock_radar_report(symbol, path=path, now=now)
    except Exception:
        return {}
    return _normalize_report(report)


def _normalize_report(report: object | None) -> dict[str, Any]:
    if report is None:
        return {}
    if isinstance(report, dict):
        return dict(report)
    if hasattr(report, "to_dict"):
        try:
            data = report.to_dict()
        except Exception:
            data = None
        if isinstance(data, dict):
            return dict(data)
    result: dict[str, Any] = {}
    for key in (
        "ticker",
        "current_price",
        "data_updated_at",
        "history_status",
        "history_latest_date",
        "technical_structure_status",
        "technical_structure_label",
    ):
        if hasattr(report, key):
            result[key] = getattr(report, key)
    return result


def _advisor_from_report(report: dict[str, Any]) -> StructureEntryAdvisor | None:
    raw = _value(report, "structureEntryAdvisor", "structure_entry_advisor", "structureEntry")
    if not isinstance(raw, dict):
        return None
    try:
        return StructureEntryAdvisor(
            structure_status=str(raw.get("structure_status") or raw.get("structureStatus") or DATA_MISSING),
            structure_score=float(raw.get("structure_score") or raw.get("structureScore") or 0),
            decline_reason=str(raw.get("decline_reason") or raw.get("declineReason") or "未知"),
            thesis_status=str(raw.get("thesis_status") or raw.get("thesisStatus") or "UNKNOWN"),
            support_confirmation=str(raw.get("support_confirmation") or raw.get("supportConfirmation") or "数据不足"),
            close_confirmation=str(raw.get("close_confirmation") or raw.get("closeConfirmation") or "数据不足"),
            relative_strength_status=str(
                raw.get("relative_strength_status") or raw.get("relativeStrengthStatus") or "相对强弱缺失"
            ),
            volume_confirmation=str(raw.get("volume_confirmation") or raw.get("volumeConfirmation") or "量能缺失"),
            structure_reasons=list(raw.get("structure_reasons") or raw.get("structureReasons") or []),
            structure_warnings=list(raw.get("structure_warnings") or raw.get("structureWarnings") or []),
            next_confirmation_steps=list(raw.get("next_confirmation_steps") or raw.get("nextConfirmationSteps") or []),
            structure_checked_at=raw.get("structure_checked_at") or raw.get("structureCheckedAt"),
        )
    except Exception:
        return None


def _safe_structure_advisor(symbol: str, *, path: Path, now: datetime) -> StructureEntryAdvisor | None:
    try:
        return build_structure_entry_advisor_for_symbol(symbol, path=path, now=now)
    except Exception:
        return None


def _safe_market_context(symbol: str, *, path: Path, now: datetime) -> dict[str, Any]:
    if not symbol:
        return {}
    try:
        return build_market_context(symbol, path=path, now=now)
    except Exception:
        return {}


def _safe_market_history(symbol: str, *, path: Path, now: datetime):
    if not symbol:
        return None
    try:
        return build_market_history(symbol, path=path, now=now)
    except Exception:
        return None


def _macro_context(*, path: Path, now: datetime) -> tuple[str | None, str]:
    try:
        snapshot = load_macro_regime(path, now=now)
    except Exception:
        return None, "缺失"
    return str(getattr(snapshot, "regime", "") or "") or None, _relative_time_text(
        _parse_datetime(getattr(snapshot, "updated_at", None)), now
    )


def _portfolio_structure_status(*, path: Path, macro_regime: object | None) -> str | None:
    try:
        view = build_portfolio_view_model(path)
        check = build_portfolio_structure_check(view, macro_regime=macro_regime)
    except Exception:
        return None
    return str(getattr(check, "status", "") or "") or None


def _portfolio_context_for_symbol(symbol: str, *, path: Path) -> dict[str, Any]:
    if not symbol:
        return {}
    try:
        view = build_portfolio_view_model(path)
    except Exception:
        return {}
    summary = dict(view.get("summary") or {})
    for row in view.get("rows") or []:
        if str(row.get("symbol") or "").upper() != symbol:
            continue
        return apply_portfolio_target(
            symbol,
            {
                "current_shares": row.get("quantity"),
                "avg_cost": row.get("averageCost"),
                "market_value": row.get("marketValue"),
                "portfolio_weight": row.get("positionPct"),
                "target_weight": row.get("targetPositionPct"),
                "max_weight": row.get("maxAcceptablePositionPct"),
                "available_cash": summary.get("cashBalance"),
            },
        )
    return apply_portfolio_target(symbol, {"available_cash": summary.get("cashBalance")})


def _has_technical_structure_map(report: dict[str, Any]) -> bool:
    status = str(_value(report, "technical_structure_status", "technicalStructureStatus") or "").strip().upper()
    if status in TECHNICAL_MAP_STATUSES:
        return True
    zone_pairs = (
        ("technical_pullback_zone_low", "technical_pullback_zone_high"),
        ("technical_entry_zone_low", "technical_entry_zone_high"),
        ("effective_technical_entry_zone_low", "effective_technical_entry_zone_high"),
        ("near_term_repair_zone_low", "near_term_repair_zone_high"),
        ("trend_reclaim_zone_low", "trend_reclaim_zone_high"),
    )
    if any(_has_zone_pair(report, low_key, high_key) for low_key, high_key in zone_pairs):
        return True
    return _first_number(
        _value(report, "confirmation_price", "confirmationPrice"),
        _value(report, "invalidation_price", "invalidationPrice"),
    ) is not None


def _has_core_structure_context(report: dict[str, Any], market: dict[str, Any]) -> bool:
    price = _first_number(_value(report, "current_price", "currentPrice", "price"), _value(market, "currentPrice"))
    has_ema = _first_number(
        _value(report, "ema20"),
        _value(report, "ema50"),
        _value(report, "ema100"),
        _value(report, "ema200"),
    ) is not None
    has_swing = _first_number(
        _value(report, "recent_swing_low", "recentSwingLow"),
        _value(report, "recent_swing_high", "recentSwingHigh"),
        _value(report, "recent_breakout_level", "recentBreakoutLevel"),
    ) is not None
    has_history = str(_value(report, "history_status", "historyStatus") or _value(market, "historyStatus") or "").lower() not in {
        "",
        "missing",
    }
    return price is not None and (has_ema or has_swing or has_history)


def _technical_cache_is_stale(report: dict[str, Any], market: dict[str, Any]) -> bool:
    explicit = _value(report, "technical_cache_stale", "technicalCacheStale", "technical_is_stale", "technicalIsStale")
    if isinstance(explicit, bool):
        return explicit
    text = " ".join(
        str(item or "").lower()
        for item in (
            explicit,
            _value(report, "technical_cache_status", "technicalCacheStatus"),
            _value(report, "history_status", "historyStatus"),
            _value(market, "historyStatus"),
        )
    )
    return "stale" in text or "过期" in text


def _technical_freshness(report: dict[str, Any], market: dict[str, Any], now: datetime) -> str:
    if _technical_cache_is_stale(report, market):
        return "过期"
    status = str(_value(report, "history_status", "historyStatus") or _value(market, "historyStatus") or "").lower()
    if status == "missing":
        return "缺失"
    latest = _parse_datetime(
        _value(report, "technical_updated_at", "technicalUpdatedAt", "history_latest_date", "historyLatestDate")
        or _value(market, "historyLatestDate")
    )
    if latest is not None:
        return _relative_day_text(latest, now)
    return "可用" if status else "缺失"


def _radar_freshness(report: dict[str, Any], market: dict[str, Any], now: datetime) -> str:
    updated_at = _parse_datetime(
        _value(report, "data_updated_at", "dataUpdatedAt", "updated_at", "updatedAt")
        or _value(market, "fetchedAt")
    )
    return _relative_time_text(updated_at, now)


def _auxiliary_missing_fields(report: dict[str, Any], advisor: StructureEntryAdvisor | None) -> list[str]:
    missing: list[str] = []
    if not _has_relative_strength(report, advisor):
        missing.append("relative_strength")
    if not _has_volume_context(report, advisor):
        missing.append("volume")
    if not _has_decline_reason(report, advisor):
        missing.append("decline_reason")
    if not _has_thesis_status(report, advisor):
        missing.append("thesis_status")
    return missing


def _technical_missing_fields(report: dict[str, Any]) -> list[str]:
    raw = _value(report, "technical_missing_fields", "technicalMissingFields", "technical_entry_missing_fields")
    result = _list_value(raw)
    return result or _core_missing_fields(report, {})


def _core_missing_fields(report: dict[str, Any], market: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if _first_number(_value(report, "current_price", "currentPrice", "price"), _value(market, "currentPrice")) is None:
        missing.append("price")
    if str(_value(report, "history_status", "historyStatus") or _value(market, "historyStatus") or "").lower() == "missing":
        missing.append("K-line")
    if _first_number(_value(report, "ema20"), _value(report, "ema50"), _value(report, "ema200")) is None:
        missing.append("EMA")
    if _first_number(_value(report, "recent_swing_low", "recentSwingLow"), _value(report, "recent_swing_high", "recentSwingHigh")) is None:
        missing.append("swing")
    return missing


def _technical_warnings(report: dict[str, Any], missing: list[str]) -> list[str]:
    warnings = _list_value(_value(report, "technical_structure_warnings", "structureWarnings"))
    reason = str(_value(report, "technical_structure_reason", "technicalStructureReason") or "").strip()
    if reason:
        warnings.append(reason)
    if missing:
        warnings.append("辅助确认不足：" + " / ".join(missing))
    return _dedupe(warnings)


def _next_technical_steps(report: dict[str, Any]) -> list[str]:
    return _list_value(_value(report, "next_technical_steps", "nextTechnicalSteps"))[:3]


def _has_zone_pair(report: dict[str, Any], low_key: str, high_key: str) -> bool:
    return _number(_value(report, low_key)) is not None and _number(_value(report, high_key)) is not None


def _has_relative_strength(report: dict[str, Any], advisor: StructureEntryAdvisor | None) -> bool:
    advisor_value = str(getattr(advisor, "relative_strength_status", "") or "")
    if advisor_value and advisor_value not in {"相对强弱缺失", "缺失", "UNKNOWN"}:
        return True
    return _first_number(
        _value(report, "relative_strength_vs_SPY", "relativeStrengthVsSpy"),
        _value(report, "relative_strength_vs_QQQ", "relativeStrengthVsQqq"),
    ) is not None


def _has_volume_context(report: dict[str, Any], advisor: StructureEntryAdvisor | None) -> bool:
    advisor_value = str(getattr(advisor, "volume_confirmation", "") or "")
    if advisor_value and advisor_value not in {"量能缺失", "缺失", "UNKNOWN"}:
        return True
    return _first_number(_value(report, "volume"), _value(report, "avg_volume", "avgVolume", "avg_volume_20d")) is not None


def _has_decline_reason(report: dict[str, Any], advisor: StructureEntryAdvisor | None) -> bool:
    value = str(_value(report, "decline_reason", "declineReason") or getattr(advisor, "decline_reason", "") or "").strip()
    return bool(value and value.upper() not in {"UNKNOWN", "未知"})


def _has_thesis_status(report: dict[str, Any], advisor: StructureEntryAdvisor | None) -> bool:
    value = str(_value(report, "thesis_status", "thesisStatus") or getattr(advisor, "thesis_status", "") or "").strip()
    return bool(value and value.upper() not in {"UNKNOWN", "未知"})


def _value(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source:
            return source.get(key)
    debug = source.get("debug")
    if isinstance(debug, dict):
        for key in keys:
            if key in debug:
                return debug.get(key)
    return None


def _list_value(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _first_number(*values: object) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:10])
        except ValueError:
            return None
    return _as_utc(parsed)


def _relative_time_text(updated_at: datetime | None, now: datetime) -> str:
    if updated_at is None:
        return "缺失"
    seconds = max(0, int((now - updated_at).total_seconds()))
    if seconds < 5 * 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60}分钟前"
    if seconds < 24 * 3600:
        return f"{seconds // 3600}小时前"
    return f"{seconds // (24 * 3600)}天前"


def _relative_day_text(updated_at: datetime, now: datetime) -> str:
    if updated_at.date() == now.date():
        return "今日"
    if (now.date() - updated_at.date()).days == 1:
        return "昨日收盘"
    days = max(0, (now.date() - updated_at.date()).days)
    return f"{days}天前"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _join_text(items: list[str]) -> str:
    return "；".join(_dedupe(items))


def _dedupe(items: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
