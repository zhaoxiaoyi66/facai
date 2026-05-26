from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from data.review_queue_builder import ReviewQueueStore, SCORING_AFFECTS


ACTIVE_REVIEW_STATUSES = {"pending_review", "needs_data", "needs_evidence", "stale"}
HANDLED_REVIEW_STATUSES = {
    "approved",
    "auto_approved_by_ai",
    "rejected",
    "manually_corrected",
    "auto_archived",
    "duplicate_archived",
    "invalid_review_item",
}
EXPLICIT_EVIDENCE_SOURCES = {"SEC_8K", "SEC_10Q", "SEC_10K", "IR_RELEASE", "IR_PRESENTATION"}
AI_SPECULATIVE_SOURCES = {"AI", "AI_MODEL", "QWEN", "MODEL", "LOCAL_AI"}
AUTO_CONFIRM_TRIAGE_STATUSES = {"auto_approved_by_ai", "ai_recommend_approve"}
AUTO_ARCHIVE_TRIAGE_STATUSES = {"ai_auto_archived", "ai_skipped", "extraction_rejected_by_rule"}
EVIDENCE_GAP_TRIAGE_STATUSES = {"ai_not_enough_evidence", "needs_more_source", "ai_invalid_output"}
AI_CORRECTION_TRIAGE_STATUSES = {"ai_recommend_correct"}
AUTO_ARCHIVE_ITEM_TYPES = {
    "analyst_estimate_needed",
    "derived_low_confidence",
    "manual_override_needed",
    "qualitative_risk",
}
CONFIDENCE_SCORES = {"high": 3, "medium": 2, "low": 1, "unknown": 0}

REVIEW_CENTER_GROUPS = (
    ("highPriorityPending", "\u9ad8\u4f18\u5148\u7ea7\u5f85\u5904\u7406"),
    ("scoringImpactNeedsHuman", "\u5f71\u54cd\u8bc4\u5206\uff0c\u9700\u8981\u4eba\u5de5\u786e\u8ba4"),
    ("autoConfirmCandidates", "\u53ef\u81ea\u52a8\u786e\u8ba4"),
    ("autoArchiveCandidates", "\u53ef\u81ea\u52a8\u5f52\u6863"),
    ("aiSuggestedCorrections", "AI \u5efa\u8bae\u4fee\u6b63"),
    ("insufficientEvidence", "\u8bc1\u636e\u4e0d\u8db3"),
    ("recentlyHandled", "\u6700\u8fd1\u5df2\u5904\u7406"),
)


@dataclass(frozen=True)
class _ReviewCenterRow:
    row: dict
    item: dict
    affects_scoring: bool
    has_explicit_evidence: bool
    missing_evidence: bool
    active: bool
    handled: bool
    ai_correction: bool
    priority_score: int
    confidence_score: int
    source_score: int
    handled_at: str


class ReviewCenterViewModel:
    def __init__(self, store: ReviewQueueStore | None = None) -> None:
        self.store = store or ReviewQueueStore()

    def build(self, symbol: str | None = None, rows: Iterable[dict] | None = None, recent_limit: int = 20) -> dict:
        return build_review_center_view_model(rows=rows, store=self.store, symbol=symbol, recent_limit=recent_limit)


def build_review_center_view_model(
    rows: Iterable[dict] | None = None,
    store: ReviewQueueStore | None = None,
    symbol: str | None = None,
    recent_limit: int = 20,
) -> dict:
    queue_rows = list(rows) if rows is not None else (store or ReviewQueueStore()).list_items(symbol=symbol)
    prepared = [_prepare_row(dict(row)) for row in queue_rows]
    active_rows = sorted([row for row in prepared if row.active], key=_active_sort_key)
    recent_rows = sorted([row for row in prepared if row.handled], key=_recent_sort_key, reverse=True)[: max(0, int(recent_limit))]

    group_rows = {
        "highPriorityPending": [row for row in active_rows if _is_high_priority_pending(row)],
        "scoringImpactNeedsHuman": [row for row in active_rows if _needs_human_for_scoring(row)],
        "autoConfirmCandidates": [row for row in active_rows if row.item["canAutoConfirm"]],
        "autoArchiveCandidates": [row for row in active_rows if row.item["canAutoArchive"]],
        "aiSuggestedCorrections": [row for row in active_rows if row.ai_correction],
        "insufficientEvidence": [row for row in active_rows if row.missing_evidence],
        "recentlyHandled": recent_rows,
    }
    groups = [
        {
            "key": key,
            "label": label,
            "count": len(group_rows[key]),
            "items": [row.item for row in group_rows[key]],
        }
        for key, label in REVIEW_CENTER_GROUPS
    ]
    group_counts = {group["key"]: group["count"] for group in groups}
    return {
        "summary": {
            "total": len(prepared),
            "active": len(active_rows),
            "recentlyHandled": len(recent_rows),
            "groupCounts": group_counts,
        },
        "groups": groups,
        "items": [row.item for row in sorted(prepared, key=_all_items_sort_key)],
    }


def _prepare_row(row: dict) -> _ReviewCenterRow:
    status = str(row.get("reviewStatus") or "pending_review").strip()
    triage = str(row.get("aiTriageStatus") or "").strip()
    confidence = _normalize_confidence(row.get("confidence"))
    confidence_score = CONFIDENCE_SCORES.get(confidence, 0)
    affects = _affects(row)
    affects_scoring = bool(affects & SCORING_AFFECTS)
    impact_level = _impact_level(affects)
    has_explicit_evidence = _has_explicit_evidence(row)
    missing_evidence = _missing_evidence(row)
    active = status in ACTIVE_REVIEW_STATUSES and status not in HANDLED_REVIEW_STATUSES
    handled = status in HANDLED_REVIEW_STATUSES or bool(row.get("reviewedAt") or row.get("approvedAt"))
    ai_correction = triage in AI_CORRECTION_TRIAGE_STATUSES or bool(_correction_candidate(row))
    can_auto_confirm = _can_auto_confirm(row, active, confidence_score, has_explicit_evidence, missing_evidence)
    can_auto_archive = _can_auto_archive(row, active, impact_level, missing_evidence)
    priority_score = _priority_score(
        affects_scoring=affects_scoring,
        confidence_score=confidence_score,
        source_score=_source_score(row, has_explicit_evidence),
        missing_evidence=missing_evidence,
        active=active,
        can_auto_archive=can_auto_archive,
    )
    item = {
        "id": row.get("id"),
        "symbol": str(row.get("symbol") or "").upper(),
        "metric": row.get("displayName") or row.get("metricKey") or "",
        "metricKey": row.get("metricKey"),
        "itemType": row.get("itemType"),
        "currentValue": _current_value(row),
        "proposedValue": _proposed_value(row, can_auto_confirm),
        "source": _source(row),
        "confidence": confidence,
        "impactLevel": impact_level,
        "reviewStatus": status,
        "aiTriageStatus": triage or None,
        "suggestedAction": _suggested_action(
            status=status,
            triage=triage,
            affects_scoring=affects_scoring,
            ai_correction=ai_correction,
            can_auto_confirm=can_auto_confirm,
            can_auto_archive=can_auto_archive,
            missing_evidence=missing_evidence,
            handled=handled,
        ),
        "reasonSummary": _reason_summary(row, affects_scoring, missing_evidence, can_auto_archive),
        "evidenceSummary": _evidence_summary(row),
        "canAutoConfirm": can_auto_confirm,
        "canAutoArchive": can_auto_archive,
        "reviewedAt": row.get("reviewedAt") or row.get("approvedAt"),
        "updatedAt": row.get("updatedAt"),
    }
    return _ReviewCenterRow(
        row=row,
        item=item,
        affects_scoring=affects_scoring,
        has_explicit_evidence=has_explicit_evidence,
        missing_evidence=missing_evidence,
        active=active,
        handled=handled,
        ai_correction=ai_correction,
        priority_score=priority_score,
        confidence_score=confidence_score,
        source_score=_source_score(row, has_explicit_evidence),
        handled_at=str(row.get("approvedAt") or row.get("reviewedAt") or row.get("updatedAt") or ""),
    )


def _affects(row: dict) -> set[str]:
    value = row.get("affects")
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    return {part.strip() for part in str(value or "").replace(";", ",").split(",") if part.strip()}


def _impact_level(affects: set[str]) -> str:
    if affects & SCORING_AFFECTS:
        return "high"
    if affects & {"Action", "Position", "maxPosition"}:
        return "medium"
    return "low"


def _normalize_confidence(value: object) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip().lower()
    if text in CONFIDENCE_SCORES:
        return text
    try:
        numeric = float(text)
    except ValueError:
        return text or "unknown"
    if numeric >= 0.85:
        return "high"
    if numeric >= 0.6:
        return "medium"
    return "low"


def _has_explicit_evidence(row: dict) -> bool:
    source_type = str(row.get("sourceType") or "").strip().upper()
    if source_type not in EXPLICIT_EVIDENCE_SOURCES:
        return False
    return bool(
        str(row.get("evidenceText") or row.get("extractedText") or row.get("evidenceQuote") or "").strip()
        or str(row.get("sourceUrl") or "").strip()
        or str(row.get("sourceDocumentTitle") or "").strip()
    )


def _missing_evidence(row: dict) -> bool:
    status = str(row.get("reviewStatus") or "").strip()
    triage = str(row.get("aiTriageStatus") or "").strip()
    if status == "needs_evidence" or triage in EVIDENCE_GAP_TRIAGE_STATUSES:
        return True
    if str(row.get("itemType") or "") == "evidence_missing_extracted_value":
        return True
    if str(row.get("itemType") or "") != "extracted_value":
        return status in {"needs_data"} and not _has_explicit_evidence(row)
    return not _has_explicit_evidence(row)


def _source_score(row: dict, has_explicit_evidence: bool) -> int:
    if has_explicit_evidence:
        return 2
    source = str(row.get("sourceType") or row.get("sourceKind") or "").strip().upper()
    if source in AI_SPECULATIVE_SOURCES:
        return 0
    return 1 if source else 0


def _can_auto_confirm(
    row: dict,
    active: bool,
    confidence_score: int,
    has_explicit_evidence: bool,
    missing_evidence: bool,
) -> bool:
    if not active or missing_evidence or not has_explicit_evidence:
        return False
    triage = str(row.get("aiTriageStatus") or "").strip()
    if triage in AUTO_CONFIRM_TRIAGE_STATUSES:
        return True
    return confidence_score >= CONFIDENCE_SCORES["high"]


def _can_auto_archive(row: dict, active: bool, impact_level: str, missing_evidence: bool) -> bool:
    if not active or impact_level != "low":
        return False
    triage = str(row.get("aiTriageStatus") or "").strip()
    item_type = str(row.get("itemType") or "").strip()
    status = str(row.get("reviewStatus") or "").strip()
    if triage in AUTO_ARCHIVE_TRIAGE_STATUSES:
        return True
    if bool(row.get("hiddenByDefault")):
        return True
    return missing_evidence and (status in {"needs_data", "needs_evidence"} or item_type in AUTO_ARCHIVE_ITEM_TYPES)


def _priority_score(
    affects_scoring: bool,
    confidence_score: int,
    source_score: int,
    missing_evidence: bool,
    active: bool,
    can_auto_archive: bool,
) -> int:
    if not active:
        return 0
    score = 100 if affects_scoring else 10
    score += confidence_score * 10
    score += source_score * 4
    if affects_scoring and missing_evidence:
        score += 50
    if can_auto_archive:
        score -= 40
    return score


def _current_value(row: dict) -> object:
    for key in ("displayValue", "normalizedValue", "value"):
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _proposed_value(row: dict, can_auto_confirm: bool) -> object:
    candidate = _correction_candidate(row)
    for key in ("correctedValue", "proposedValue", "value"):
        value = candidate.get(key)
        if value not in (None, ""):
            return value
    return _current_value(row) if can_auto_confirm else None


def _correction_candidate(row: dict) -> dict:
    value = row.get("correctionCandidate")
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source(row: dict) -> str:
    for key in ("sourceType", "sourceKind"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return "unknown"


def _suggested_action(
    status: str,
    triage: str,
    affects_scoring: bool,
    ai_correction: bool,
    can_auto_confirm: bool,
    can_auto_archive: bool,
    missing_evidence: bool,
    handled: bool,
) -> str:
    if handled:
        return "review_completed"
    if ai_correction:
        return "review_ai_correction"
    if can_auto_confirm:
        return "auto_confirm_candidate"
    if can_auto_archive:
        return "auto_archive_candidate"
    if missing_evidence and affects_scoring:
        return "manual_confirm_after_evidence_review"
    if missing_evidence or triage in EVIDENCE_GAP_TRIAGE_STATUSES:
        return "collect_evidence"
    if affects_scoring:
        return "manual_confirm"
    if status in ACTIVE_REVIEW_STATUSES:
        return "review_later"
    return "no_action"


def _reason_summary(row: dict, affects_scoring: bool, missing_evidence: bool, can_auto_archive: bool) -> str:
    for key in ("recommendedAction", "aiExplanationZh", "explanation", "systemReason", "correctionNotes"):
        text = _clean_text(row.get(key))
        if text:
            return _truncate(text, 180)
    if affects_scoring and missing_evidence:
        return "Scoring-impact item is missing verifiable evidence."
    if affects_scoring:
        return "Item can affect scoring and needs confirmation."
    if can_auto_archive:
        return "Low-priority non-scoring gap is an archive candidate."
    return "Review queue item needs triage."


def _evidence_summary(row: dict) -> str:
    parts = [
        _clean_text(row.get("sourceDocumentTitle")),
        _clean_text(row.get("evidenceQuote")),
        _clean_text(row.get("evidenceText")),
        _clean_text(row.get("extractedText")),
        _clean_text(row.get("sourceUrl")),
    ]
    summary = " | ".join(part for part in parts if part)
    return _truncate(summary, 220) if summary else "No verifiable evidence attached."


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _truncate(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}\u2026"


def _is_high_priority_pending(row: _ReviewCenterRow) -> bool:
    return row.active and not row.item["canAutoArchive"] and (
        row.affects_scoring or row.ai_correction or row.missing_evidence or row.priority_score >= 80
    )


def _needs_human_for_scoring(row: _ReviewCenterRow) -> bool:
    return row.active and row.affects_scoring and not row.item["canAutoConfirm"]


def _active_sort_key(row: _ReviewCenterRow) -> tuple:
    return (-row.priority_score, -row.confidence_score, -row.source_score, row.item["symbol"], str(row.item["metricKey"] or ""))


def _recent_sort_key(row: _ReviewCenterRow) -> tuple:
    return (row.handled_at, str(row.item.get("id") or ""))


def _all_items_sort_key(row: _ReviewCenterRow) -> tuple:
    return (0 if row.active else 1, *_active_sort_key(row))
