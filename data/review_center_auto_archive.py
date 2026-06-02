from __future__ import annotations

from typing import Any

from data.review_center_view_model import build_review_center_view_model
from data.review_queue_builder import HOOD_BUY_ZONE_CORE_METRICS, ReviewQueueStore, SCORING_AFFECTS


PROTECTED_SOURCE_TYPES = {"SEC_8K", "SEC_10Q", "SEC_10K", "IR_RELEASE", "IR_PRESENTATION"}
PROTECTED_AI_CLOUD_GUARDRAIL_METRICS = {
    "aiCloudContractedBacklog",
    "aiCloudRpo",
    "aiCloudGpuFleetCapacity",
    "aiCloudUtilization",
    "aiCloudCapexCommitments",
    "aiCloudCapexIntensity",
    "aiCloudNetDebt",
    "aiCloudDebtMaturity",
    "aiCloudInterestBurden",
    "aiCloudCustomerConcentration",
    "aiCloudNvidiaSupplyExposure",
    "aiCloudHyperscalerExposure",
}
PROTECTED_NORMALIZED_METRIC_TOKENS = {
    "normalizedearnings",
    "normalizedebitda",
    "adjustedearnings",
    "adjustedebitda",
    "nongaapnetincome",
}


def auto_archive_low_priority_review_items(
    *,
    store: ReviewQueueStore | None = None,
    symbol: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    queue_store = store or ReviewQueueStore()
    rows = queue_store.list_items(symbol=symbol)
    rows_by_id = {int(row["id"]): row for row in rows if row.get("id") is not None}
    view = build_review_center_view_model(rows=rows, store=queue_store, symbol=symbol)
    candidates = _auto_archive_candidates(view)
    planned: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for item in candidates:
        item_id = _item_id(item)
        row = rows_by_id.get(item_id)
        if row is None:
            continue
        reason = _archive_reason(item, row)
        safe, skip_reason = _safe_to_archive(row, item, reason)
        payload = _result_item(item, row, reason if safe else skip_reason)
        if not safe:
            skipped.append(payload)
            continue
        planned.append(payload)

    archived: list[dict[str, Any]] = []
    if not dry_run:
        for item in planned:
            old, new = queue_store.auto_archive_item(int(item["id"]), str(item["reason"]))
            if new and str(new.get("reviewStatus") or "") == "auto_archived":
                archived.append(item)

    return {
        "dryRun": bool(dry_run),
        "eligibleCount": len(planned),
        "archivedCount": 0 if dry_run else len(archived),
        "items": planned if dry_run else archived,
        "skipped": skipped,
        "alreadyArchived": [_already_archived_item(row) for row in rows if str(row.get("reviewStatus") or "") == "duplicate_archived"],
    }


def _auto_archive_candidates(view: dict[str, Any]) -> list[dict[str, Any]]:
    for group in view.get("groups") or []:
        if group.get("key") == "autoArchiveCandidates":
            return [item for item in group.get("items") or [] if isinstance(item, dict)]
    return []


def _safe_to_archive(row: dict, item: dict, reason: str) -> tuple[bool, str]:
    if not item.get("canAutoArchive"):
        return False, "not_marked_auto_archive"
    if _is_protected_hood_metric(row):
        return False, "protected_hood_operating_field"
    if _is_protected_ai_cloud_guardrail_metric(row):
        return False, "protected_ai_cloud_guardrail_field"
    if _is_protected_normalized_metric(row):
        return False, "protected_normalized_profitability_metric"
    if item.get("riskObservation"):
        return True, ""
    if _affects_scoring(row) and _has_review_value(row):
        return False, "scoring_impact_value"
    if _is_sec_or_ir_candidate(row) and _has_review_value(row):
        return False, "protected_sec_ir_candidate"
    if _missing_evidence(row) and reason not in {"stale", "historical_value", "risk_observation", "duplicate_archived"}:
        return False, "insufficient_evidence"
    return True, ""


def _archive_reason(item: dict, row: dict) -> str:
    status = str(row.get("reviewStatus") or "").strip()
    if status == "stale":
        return "stale"
    if str(row.get("freshnessStatus") or "").strip() == "historical_value":
        return "historical_value"
    if item.get("riskObservation"):
        return "risk_observation"
    triage = str(row.get("aiTriageStatus") or "").strip()
    if triage == "ai_auto_archived":
        return "ai_auto_archived"
    reason = str(item.get("reasonSummary") or "").strip()
    return reason or "low_priority_review_noise"


def _result_item(item: dict, row: dict, reason: str) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "symbol": item.get("symbol") or row.get("symbol"),
        "metric": item.get("metric") or row.get("displayName") or row.get("metricKey"),
        "metricKey": item.get("metricKey") or row.get("metricKey"),
        "reason": reason,
    }


def _already_archived_item(row: dict) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "symbol": row.get("symbol"),
        "metric": row.get("displayName") or row.get("metricKey"),
        "metricKey": row.get("metricKey"),
        "reason": "duplicate_archived",
    }


def _item_id(item: dict) -> int:
    return int(item.get("id") or 0)


def _affects_scoring(row: dict) -> bool:
    return bool(_affects(row) & SCORING_AFFECTS)


def _affects(row: dict) -> set[str]:
    value = row.get("affects")
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    return {part.strip() for part in str(value or "").replace(";", ",").split(",") if part.strip()}


def _has_review_value(row: dict) -> bool:
    return any(row.get(key) not in (None, "") for key in ("displayValue", "normalizedValue", "value"))


def _is_sec_or_ir_candidate(row: dict) -> bool:
    return str(row.get("sourceType") or "").strip().upper() in PROTECTED_SOURCE_TYPES


def _is_protected_hood_metric(row: dict) -> bool:
    if str(row.get("symbol") or "").strip().upper() != "HOOD":
        return False
    metric_key = str(row.get("metricKey") or "").strip()
    return metric_key in HOOD_BUY_ZONE_CORE_METRICS


def _is_protected_ai_cloud_guardrail_metric(row: dict) -> bool:
    metric_key = str(row.get("metricKey") or "").strip()
    return metric_key in PROTECTED_AI_CLOUD_GUARDRAIL_METRICS


def _is_protected_normalized_metric(row: dict) -> bool:
    token = _normalized_token(row.get("metricKey"))
    display = _normalized_token(row.get("displayName"))
    combined = f"{token} {display}"
    return any(protected in combined for protected in PROTECTED_NORMALIZED_METRIC_TOKENS)


def _missing_evidence(row: dict) -> bool:
    status = str(row.get("reviewStatus") or "").strip()
    triage = str(row.get("aiTriageStatus") or "").strip()
    item_type = str(row.get("itemType") or "").strip()
    return status in {"needs_data", "needs_evidence"} or triage in {"ai_not_enough_evidence", "needs_more_source"} or item_type == "evidence_missing_extracted_value"


def _normalized_token(value: object) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())
