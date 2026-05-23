from __future__ import annotations

import urllib.request
from typing import Iterable

from data.extract_metric_from_text import validate_extracted_metric_candidate
from data.metric_source_map import metric_source_definition
from data.normalize_metric_value import build_evidence_window, normalize_metric_value
from data.review_queue_builder import ReviewQueueStore


def backfill_evidence_for_review_item(review_item_id: int, store: ReviewQueueStore | None = None) -> dict:
    queue_store = store or ReviewQueueStore()
    row = queue_store.get_item(int(review_item_id))
    if not row:
        return {"status": "not_found", "reviewItemId": int(review_item_id)}
    source_url = str(row.get("sourceUrl") or "").strip()
    if not source_url:
        return _mark_needs_evidence(queue_store, row, "missing_source_url")
    try:
        source_text = _read_source_text(source_url)
    except Exception as exc:  # pragma: no cover - defensive network/file path
        return _mark_needs_evidence(queue_store, row, f"source_fetch_failed:{exc}")
    normalized = normalize_metric_value(row.get("value"), row.get("unit"), source_text, str(row.get("metricKey") or ""))
    aliases = _aliases_for_metric(row)
    evidence = build_evidence_window(source_text, aliases=aliases, value_display=normalized.displayValue, limit=1200)
    if evidence.evidenceInsufficient:
        return _mark_needs_evidence(queue_store, row, "evidence_not_found")
    valid, reason = validate_extracted_metric_candidate(str(row.get("metricKey") or ""), evidence.evidenceWindow)
    if not valid:
        queue_store.set_ai_triage(int(row["id"]), "extraction_rejected_by_rule", explanation_zh=reason)
        queue_store.auto_archive_item(int(row["id"]), reason)
        return {"status": "extraction_rejected_by_rule", "reviewItemId": int(row["id"]), "reason": reason}
    queue_store.update_evidence(
        int(row["id"]),
        evidence.evidenceWindow,
        source_url=source_url,
        source_document_title=row.get("sourceDocumentTitle") or "Source document",
        metric_period=row.get("metricPeriod") or row.get("period"),
        fiscal_period=row.get("fiscalPeriod"),
        extraction_rule="evidence_backfill_keyword_window",
    )
    return {"status": "backfilled", "reviewItemId": int(row["id"])}


def backfill_evidence_for_symbol(symbol: str, store: ReviewQueueStore | None = None) -> dict:
    queue_store = store or ReviewQueueStore()
    rows = [
        row for row in queue_store.list_items(symbol=str(symbol).upper())
        if str(row.get("reviewStatus") or "") == "needs_evidence"
        or str(row.get("itemType") or "") == "evidence_missing_extracted_value"
    ]
    return _backfill_rows(rows, queue_store)


def backfill_evidence_for_current_filters(filters: dict | None = None, store: ReviewQueueStore | None = None) -> dict:
    queue_store = store or ReviewQueueStore()
    filters = filters or {}
    rows = queue_store.list_items(
        symbol=filters.get("symbol"),
        metric_key=filters.get("metric_key"),
        item_type=filters.get("item_type"),
        source_type=filters.get("source_type"),
        confidence=filters.get("confidence"),
        review_status=filters.get("review_status"),
        model_type=filters.get("model_type"),
        affects_scoring=bool(filters.get("affects_scoring")),
    )
    rows = [
        row for row in rows
        if str(row.get("reviewStatus") or "") == "needs_evidence"
        or str(row.get("itemType") or "") == "evidence_missing_extracted_value"
    ]
    return _backfill_rows(rows, queue_store)


def _backfill_rows(rows: Iterable[dict], queue_store: ReviewQueueStore) -> dict:
    result = {"attempted": 0, "backfilled": 0, "failed": 0, "details": []}
    for row in rows:
        result["attempted"] += 1
        outcome = backfill_evidence_for_review_item(int(row["id"]), queue_store)
        result["details"].append(outcome)
        if outcome.get("status") == "backfilled":
            result["backfilled"] += 1
        else:
            result["failed"] += 1
    return result


def _mark_needs_evidence(queue_store: ReviewQueueStore, row: dict, reason: str) -> dict:
    with queue_store.connect() as conn:
        conn.execute(
            """
            UPDATE review_queue_items
            SET reviewStatus = 'needs_evidence',
                itemType = 'evidence_missing_extracted_value',
                aiTriageStatus = 'needs_more_source',
                qwenEligible = 0,
                qwenIneligibleReason = ?,
                recommendedAction = COALESCE(NULLIF(recommendedAction, ''), '重新抓取IR/SEC或人工确认来源'),
                updatedAt = datetime('now')
            WHERE id = ?
            """,
            (reason, int(row["id"])),
        )
    return {"status": "needs_evidence", "reviewItemId": int(row["id"]), "reason": reason}


def _read_source_text(source_url: str) -> str:
    request = urllib.request.Request(source_url, headers={"User-Agent": "ZHXResearch/1.0 evidence-backfill"})
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - URL comes from local review source metadata.
        raw = response.read()
    return raw.decode("utf-8", errors="ignore")


def _aliases_for_metric(row: dict) -> list[str]:
    aliases = [str(row.get("metricKey") or ""), str(row.get("displayName") or "")]
    definition = metric_source_definition(str(row.get("metricKey") or ""))
    if definition:
        aliases.append(definition.displayName)
    return [alias for alias in aliases if alias]
