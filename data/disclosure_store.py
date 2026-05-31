from __future__ import annotations

import sqlite3
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from data.metric_dictionary import CONFIDENCE_PRIORITY, SOURCE_PRIORITY, metric_definition_by_key
from data.metric_source_map import metric_source_definition
from data.metric_variants import metric_variant_for_key, target_basis_for_metric
from data.normalize_metric_value import display_percent_to_scoring_ratio, is_business_percent_metric, normalize_metric_period
from data.prices import CACHE_PATH


REVIEW_STATUSES = {"pending_review", "approved", "rejected", "manually_corrected", "stale", "auto_approved_by_ai", "undone"}
SCORING_REVIEW_STATUSES = {"approved", "manually_corrected", "auto_approved_by_ai"}
PENDING_REVIEW_STATUS = "pending_review"
SCORING_FORBIDDEN_REVIEW_STATUSES = {
    "pending_review",
    "needs_evidence",
    "needs_data",
    "rejected",
    "auto_archived",
    "duplicate_archived",
    "invalid_review_item",
    "not_enough_evidence",
    "stale",
    "undone",
}
SCORING_FORBIDDEN_RESOLUTION_STATUSES = {
    "company_not_disclosed",
    "manual_override_required",
    "requires_ir_scrape",
    "requires_sec_filing",
    "requires_analyst_estimates",
    "missing_inputs",
    "semi_auto_low_confidence",
}
SCORING_FORBIDDEN_AI_TRIAGE_STATUSES = {
    "ai_not_enough_evidence",
    "extraction_rejected_by_rule",
    "needs_more_source",
}
SCORING_FORBIDDEN_ITEM_TYPES = {"evidence_missing_extracted_value"}
SCORING_STRUCTURED_SOURCE_TYPES = {"FMP", "CALCULATED", "calculated", "estimated", "reported"}
SCORING_MANUAL_SOURCE_TYPES = {"MANUAL", "MANUAL_CORRECTION", "AI_ASSISTED_MANUAL_CORRECTION"}
SCORING_BLOCKED_MANUAL_ACTORS = {"ai", "qwen", "autopilot", "pipeline", "system"}
CRITICAL_MISSING_IMPACTS = {"CRITICAL_QUALITY", "CRITICAL_RISK"}
REVIEW_PRIORITY = {
    "manually_corrected": 400,
    "approved": 300,
    "pending_review": 100,
    "stale": 10,
    "rejected": 0,
}


class DisclosureStore:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS disclosure_metric_values (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    metricKey TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT,
                    period TEXT,
                    fiscalYear INTEGER,
                    fiscalQuarter TEXT,
                    sourceType TEXT NOT NULL,
                    sourceUrl TEXT,
                    sourceDocumentTitle TEXT,
                    accessionNumber TEXT,
                    extractedText TEXT,
                    confidence TEXT NOT NULL,
                    reviewStatus TEXT NOT NULL DEFAULT 'pending_review',
                    reviewedAt TEXT,
                    reviewedBy TEXT,
                    correctionNotes TEXT,
                    updatedAt TEXT NOT NULL
                )
                """
            )
            _ensure_column(conn, "disclosure_metric_values", "displayName", "TEXT")
            _ensure_column(conn, "disclosure_metric_values", "reviewStatus", "TEXT NOT NULL DEFAULT 'pending_review'")
            _ensure_column(conn, "disclosure_metric_values", "reviewedAt", "TEXT")
            _ensure_column(conn, "disclosure_metric_values", "reviewedBy", "TEXT")
            _ensure_column(conn, "disclosure_metric_values", "correctionNotes", "TEXT")
            _ensure_column(conn, "disclosure_metric_values", "metricVariant", "TEXT")
            _ensure_column(conn, "disclosure_metric_values", "targetBasis", "TEXT")
            _ensure_column(conn, "disclosure_metric_values", "freshnessStatus", "TEXT")
            conn.execute(
                """
                UPDATE disclosure_metric_values
                SET reviewStatus = 'approved'
                WHERE reviewStatus = 'pending_review' AND sourceType IN ('CALCULATED', 'FMP', 'MANUAL')
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_disclosure_metric_symbol_key
                ON disclosure_metric_values(symbol, metricKey)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS disclosure_fetch_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    sourceType TEXT NOT NULL,
                    url TEXT,
                    status TEXT NOT NULL,
                    errorMessage TEXT,
                    fetchedAt TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS missing_metric_resolution (
                    symbol TEXT NOT NULL,
                    metricKey TEXT NOT NULL,
                    status TEXT NOT NULL,
                    sourceTried TEXT,
                    reason TEXT,
                    recommendedAction TEXT,
                    updatedAt TEXT NOT NULL,
                    PRIMARY KEY (symbol, metricKey)
                )
                """
            )

    def save_metric(
        self,
        symbol: str,
        metric_key: str,
        value: float,
        unit: str | None,
        period: str | None,
        source_type: str,
        source_url: str | None,
        source_document_title: str | None,
        extracted_text: str | None,
        confidence: str,
        accession_number: str | None = None,
        fiscal_year: int | None = None,
        fiscal_quarter: str | None = None,
        review_status: str | None = None,
        reviewed_by: str | None = None,
        correction_notes: str | None = None,
        display_name: str | None = None,
        metric_variant: str | None = None,
        target_basis: str | None = None,
        freshness_status: str | None = None,
    ) -> None:
        definition = metric_definition_by_key(metric_key)
        value = _scoring_storage_value(metric_key, value, unit)
        metric_variant = metric_variant or metric_variant_for_key(metric_key)
        target_basis = target_basis or target_basis_for_metric(metric_variant or metric_key)
        freshness_status = freshness_status or "active_current"
        review_status = _normalize_review_status(review_status or _default_review_status(source_type))
        reviewed_at = _now() if review_status in {"approved", "rejected", "manually_corrected"} else None
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO disclosure_metric_values (
                    symbol, metricKey, displayName, value, unit, period, fiscalYear, fiscalQuarter,
                    sourceType, sourceUrl, sourceDocumentTitle, accessionNumber,
                    extractedText, confidence, reviewStatus, reviewedAt, reviewedBy, correctionNotes,
                    metricVariant, targetBasis, freshnessStatus, updatedAt
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol.upper(),
                    metric_key,
                    display_name or (definition.display_name if definition else metric_key),
                    value,
                    unit,
                    period,
                    fiscal_year,
                    fiscal_quarter,
                    source_type,
                    source_url,
                    source_document_title,
                    accession_number,
                    extracted_text,
                    confidence,
                    review_status,
                    reviewed_at,
                    reviewed_by,
                    correction_notes,
                    metric_variant,
                    target_basis,
                    freshness_status,
                    _now(),
                ),
            )
            self._refresh_freshness_status(conn, symbol.upper(), metric_variant)

    def _refresh_freshness_status(self, conn: sqlite3.Connection, symbol: str, metric_variant: str | None) -> None:
        if not metric_variant:
            return
        rows = conn.execute(
            """
            SELECT id, symbol, metricKey, metricVariant, period, fiscalYear, fiscalQuarter,
                   sourceDocumentTitle, extractedText, reviewStatus, updatedAt
            FROM disclosure_metric_values
            WHERE symbol = ? AND metricVariant = ?
            """,
            (symbol.upper(), metric_variant),
        ).fetchall()
        if not rows:
            return
        sorted_rows = sorted((dict(row) for row in rows), key=_freshness_sort_key, reverse=True)
        active_id = sorted_rows[0]["id"]
        conn.execute(
            """
            UPDATE disclosure_metric_values
            SET freshnessStatus = CASE WHEN id = ? THEN 'active_current' ELSE 'historical_value' END
            WHERE symbol = ? AND metricVariant = ?
            """,
            (active_id, symbol.upper(), metric_variant),
        )

    def log_fetch(self, symbol: str, source_type: str, url: str | None, status: str, error_message: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO disclosure_fetch_logs (symbol, sourceType, url, status, errorMessage, fetchedAt)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (symbol.upper(), source_type, url, status, error_message, _now()),
            )

    def save_resolution(
        self,
        symbol: str,
        metric_key: str,
        status: str,
        source_tried: str | None,
        reason: str | None,
        recommended_action: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO missing_metric_resolution (
                    symbol, metricKey, status, sourceTried, reason, recommendedAction, updatedAt
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, metricKey) DO UPDATE SET
                    status = excluded.status,
                    sourceTried = excluded.sourceTried,
                    reason = excluded.reason,
                    recommendedAction = excluded.recommendedAction,
                    updatedAt = excluded.updatedAt
                """,
                (symbol.upper(), metric_key, status, source_tried, reason, recommended_action, _now()),
            )

    def get_resolutions(self, symbol: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM missing_metric_resolution
                WHERE symbol = ?
                ORDER BY updatedAt DESC
                """,
                (symbol.upper(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_metrics(self, symbol: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM disclosure_metric_values
                WHERE symbol = ?
                ORDER BY updatedAt DESC, id DESC
                """,
                (symbol.upper(),),
            ).fetchall()
        return [_decorate_row(dict(row)) for row in rows]

    def list_metrics(
        self,
        symbol: str | None = None,
        metric_key: str | None = None,
        source_type: str | None = None,
        confidence: str | None = None,
        review_status: str | None = None,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[object] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if metric_key:
            clauses.append("metricKey = ?")
            params.append(metric_key)
        if source_type:
            clauses.append("sourceType = ?")
            params.append(source_type)
        if confidence:
            clauses.append("confidence = ?")
            params.append(confidence)
        if review_status:
            clauses.append("reviewStatus = ?")
            params.append(review_status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM disclosure_metric_values
                {where}
                ORDER BY updatedAt DESC, id DESC
                """,
                params,
            ).fetchall()
        return [_decorate_row(dict(row)) for row in rows]

    def review_summary(self, symbol: str | None = None) -> dict:
        rows = self.list_metrics(symbol=symbol)
        return _review_summary_from_rows(rows)

    def update_review_status(
        self,
        metric_id: int,
        review_status: str,
        reviewed_by: str = "local_user",
        correction_notes: str | None = None,
    ) -> None:
        status = _normalize_review_status(review_status)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE disclosure_metric_values
                SET reviewStatus = ?,
                    reviewedAt = ?,
                    reviewedBy = ?,
                    correctionNotes = COALESCE(?, correctionNotes),
                    updatedAt = ?
                WHERE id = ?
                """,
                (status, _now(), reviewed_by, correction_notes, _now(), metric_id),
            )

    def correct_metric(
        self,
        metric_id: int,
        value: float,
        unit: str | None,
        period: str | None,
        correction_notes: str | None,
        reviewed_by: str = "local_user",
        source_type: str = "MANUAL_CORRECTION",
    ) -> None:
        metric_key = self._metric_key_for_id(metric_id)
        value = _scoring_storage_value(metric_key, value, unit)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE disclosure_metric_values
                SET value = ?,
                    unit = ?,
                    period = ?,
                    sourceType = ?,
                    confidence = 'high',
                    reviewStatus = 'manually_corrected',
                    reviewedAt = ?,
                    reviewedBy = ?,
                    correctionNotes = ?,
                    updatedAt = ?
                WHERE id = ?
                """,
                (value, unit, period, source_type, _now(), reviewed_by, correction_notes, _now(), metric_id),
            )

    def _metric_key_for_id(self, metric_id: int) -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT metricKey FROM disclosure_metric_values WHERE id = ?", (int(metric_id),)).fetchone()
        return str(row["metricKey"] if row else "")

    def clear_symbol_metrics(self, symbol: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE disclosure_metric_values
                SET reviewStatus = 'stale', updatedAt = ?
                WHERE symbol = ? AND reviewStatus NOT IN ('approved', 'manually_corrected', 'rejected')
                """,
                (_now(), symbol.upper()),
            )
            conn.execute("DELETE FROM missing_metric_resolution WHERE symbol = ?", (symbol.upper(),))

    def best_metrics(self, symbol: str, scoring_only: bool = False) -> dict[str, dict]:
        best: dict[str, dict] = {}
        for row in self.get_metrics(symbol):
            if scoring_only and not _eligible_for_scoring(row):
                continue
            current = best.get(row["metricKey"])
            if current is None or _metric_rank(row) > _metric_rank(current):
                best[row["metricKey"]] = row
        return best

    def metric_supplement(self, symbol: str, scoring_only: bool = True) -> dict:
        rows = self.get_metrics(symbol)
        payload_rows = [row for row in rows if _eligible_for_scoring(row)] if scoring_only else rows
        supplement: dict = {
            "metric_sources": {},
            "metric_statuses": {},
            "disclosureMetrics": [_metric_payload(row) for row in payload_rows],
            "disclosureReviewSummary": _review_summary_from_rows(payload_rows),
            "criticalPendingReviewMetrics": [] if scoring_only else _critical_pending_metrics(rows),
            "criticalPendingReviewCount": 0 if scoring_only else len(_critical_pending_metrics(rows)),
        }
        for row in ([] if scoring_only else rows):
            definition = metric_definition_by_key(row["metricKey"])
            if definition and row.get("reviewStatus") == PENDING_REVIEW_STATUS:
                supplement["metric_statuses"][definition.snapshot_key] = {
                    "status": "pending_review",
                    "sourceType": row["sourceType"],
                    "confidence": row["confidence"],
                    "reviewStatus": row["reviewStatus"],
                    "label": definition.display_name,
                }
        for metric_key, row in self.best_metrics(symbol, scoring_only=True).items():
            definition = metric_definition_by_key(metric_key)
            if not definition:
                continue
            supplement[definition.snapshot_key] = row["value"]
            supplement["metric_sources"][definition.snapshot_key] = {
                "sourceType": _source_type_for_scoring(metric_key, row["sourceType"], row["confidence"], row.get("reviewStatus")),
                "sourceUrl": row["sourceUrl"],
                "sourceDocumentTitle": row["sourceDocumentTitle"],
                "extractedText": row["extractedText"],
                "confidence": row["confidence"],
                "period": row["period"],
                "reviewStatus": row.get("reviewStatus"),
                "reviewedAt": row.get("reviewedAt"),
                "metricVariant": row.get("metricVariant"),
                "targetBasis": row.get("targetBasis"),
                "freshnessStatus": row.get("freshnessStatus"),
            }
            supplement["metric_statuses"][definition.snapshot_key] = {
                "status": "available",
                "sourceType": row["sourceType"],
                "confidence": row["confidence"],
                "reviewStatus": row.get("reviewStatus"),
                "label": definition.display_name,
                "metricVariant": row.get("metricVariant"),
                "targetBasis": row.get("targetBasis"),
                "freshnessStatus": row.get("freshnessStatus"),
            }
        resolutions = self.get_resolutions(symbol)
        if resolutions:
            supplement["missingMetricResolutions"] = resolutions
            for row in resolutions:
                definition = metric_definition_by_key(row["metricKey"])
                if definition:
                    supplement["metric_statuses"][definition.snapshot_key] = {
                        "status": row["status"],
                        "sourceTried": row["sourceTried"],
                        "reason": row["reason"],
                        "recommendedAction": row["recommendedAction"],
                    }
        return supplement


def _metric_rank(row: dict) -> tuple[int, int, int, int, str]:
    freshness_rank = {"active_current": 20, "": 10, None: 10, "historical_value": 0}.get(row.get("freshnessStatus"), 10)
    return (
        REVIEW_PRIORITY.get(str(row.get("reviewStatus")), 0),
        freshness_rank,
        SOURCE_PRIORITY.get(str(row.get("sourceType")), 0),
        CONFIDENCE_PRIORITY.get(str(row.get("confidence")), 0),
        str(row.get("period") or row.get("updatedAt") or ""),
    )


def _freshness_sort_key(row: dict) -> tuple[int, int, int, int, int, int, str]:
    """Sort metric values by the period the metric describes, not filing date."""
    period = normalize_metric_period(row).metricPeriod or normalize_metric_period(row).fiscalPeriod or str(row.get("period") or "")
    review_rank = REVIEW_PRIORITY.get(str(row.get("reviewStatus") or ""), 0)
    year = 0
    quarter = 0
    month = 0
    day = 0
    text = str(period or "")
    date_match = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", text)
    if date_match:
        year = int(date_match.group(1))
        month = int(date_match.group(2))
        day = int(date_match.group(3))
    year_match = re.search(r"(20\d{2})", text)
    if year_match and not date_match:
        year = int(year_match.group(1))
    quarter_match = re.search(r"Q([1-4])", text, flags=re.IGNORECASE)
    if quarter_match:
        quarter = int(quarter_match.group(1))
    elif text.upper().startswith("FY"):
        quarter = 5
    return (review_rank, year, quarter, month, day, int(row.get("id") or 0), str(row.get("updatedAt") or ""))


def _source_type_for_scoring(metric_key: str, source_type: str, confidence: str, review_status: str | None = None) -> str:
    if review_status == "manually_corrected":
        if metric_key in {"nonGaapOperatingMargin", "nonGaapFcfMargin"}:
            return "non_gaap_reported"
        return "reported_ir"
    if source_type == "CALCULATED":
        return "calculated"
    if source_type == "FMP_TRANSCRIPT" and confidence == "low":
        return "estimated"
    if metric_key in {"nonGaapOperatingMargin", "nonGaapFcfMargin"}:
        return "non_gaap_reported"
    if metric_key in {"sbcRatio", "sbcToRevenue", "fcfMargin", "directFcfMargin", "netDebtToEbitda", "interestCoverage"} and source_type in {"SEC_XBRL", "CALCULATED"}:
        return "calculated"
    if metric_key in {"peg", "forwardRevenueMultiple"}:
        return "estimated"
    if source_type in {"IR_RELEASE", "SEC_8K", "SEC_10Q", "SEC_10K", "IR_PRESENTATION"}:
        return "reported_ir"
    if source_type == "SEC_XBRL":
        return "reported_sec"
    if source_type == "FMP":
        return "estimated"
    return "reported_ir"


def canMetricEnterScoring(metric: dict) -> bool:
    if str(metric.get("freshnessStatus") or metric.get("freshness_status") or "active_current") == "historical_value":
        return False
    if str(metric.get("itemType") or metric.get("item_type") or "") in SCORING_FORBIDDEN_ITEM_TYPES:
        return False
    triage_status = str(metric.get("aiTriageStatus") or metric.get("ai_triage_status") or "")
    if triage_status in SCORING_FORBIDDEN_AI_TRIAGE_STATUSES:
        return False

    resolution_status = str(metric.get("resolutionStatus") or metric.get("resolution_status") or "")
    explicit_scoring_allowed = _truthy(metric.get("scoringAllowed", metric.get("scoring_allowed")))
    if resolution_status in SCORING_FORBIDDEN_RESOLUTION_STATUSES:
        return False
    if resolution_status == "low_confidence_derived" and not explicit_scoring_allowed:
        return False

    review_status = str(metric.get("reviewStatus") or metric.get("review_status") or "").strip().lower()
    if review_status in SCORING_FORBIDDEN_REVIEW_STATUSES:
        return False

    source_type = str(metric.get("sourceType") or metric.get("source_type") or "").strip()
    if source_type in SCORING_MANUAL_SOURCE_TYPES:
        return _manual_metric_is_user_confirmed(metric, review_status, explicit_scoring_allowed)
    if review_status in SCORING_REVIEW_STATUSES:
        return True
    if source_type in SCORING_STRUCTURED_SOURCE_TYPES and not review_status:
        return True
    return explicit_scoring_allowed


def can_metric_enter_scoring(metric: dict) -> bool:
    return canMetricEnterScoring(metric)


def _eligible_for_scoring(row: dict) -> bool:
    return canMetricEnterScoring(row)


def _normalize_review_status(status: str) -> str:
    normalized = str(status or PENDING_REVIEW_STATUS).strip().lower()
    if normalized not in REVIEW_STATUSES:
        raise ValueError(f"Unsupported reviewStatus: {status}")
    return normalized


def _default_review_status(source_type: str) -> str:
    if source_type in {"MANUAL_CORRECTION", "AI_ASSISTED_MANUAL_CORRECTION"}:
        return "manually_corrected"
    if source_type in {"CALCULATED", "FMP", "MANUAL"}:
        return "approved"
    return PENDING_REVIEW_STATUS


def _manual_metric_is_user_confirmed(metric: dict, review_status: str, explicit_scoring_allowed: bool) -> bool:
    if review_status and review_status not in SCORING_REVIEW_STATUSES:
        return False
    actor = str(
        metric.get("reviewedBy")
        or metric.get("reviewed_by")
        or metric.get("createdBy")
        or metric.get("created_by")
        or ""
    ).strip().lower()
    if actor in SCORING_BLOCKED_MANUAL_ACTORS:
        return False
    source_type = str(metric.get("sourceType") or metric.get("source_type") or "").strip()
    if source_type in {"MANUAL_CORRECTION", "AI_ASSISTED_MANUAL_CORRECTION"}:
        return review_status in {"manually_corrected", "approved"} or explicit_scoring_allowed
    return actor in {"local_user", "user", "manual", "manual_override"} or explicit_scoring_allowed


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "allowed", "scoring_allowed"}
    return False


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _decorate_row(row: dict) -> dict:
    definition = metric_definition_by_key(str(row.get("metricKey")))
    if not row.get("displayName"):
        row["displayName"] = definition.display_name if definition else row.get("metricKey")
    if not row.get("reviewStatus"):
        row["reviewStatus"] = PENDING_REVIEW_STATUS
    row["metricVariant"] = row.get("metricVariant") or metric_variant_for_key(str(row.get("metricKey") or ""))
    row["targetBasis"] = row.get("targetBasis") or target_basis_for_metric(row.get("metricVariant") or row.get("metricKey"))
    row["freshnessStatus"] = row.get("freshnessStatus") or "active_current"
    row["eligibleForScoring"] = _eligible_for_scoring(row)
    return row


def _metric_payload(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "symbol": row.get("symbol"),
        "metricKey": row.get("metricKey"),
        "displayName": row.get("displayName") or row.get("metricKey"),
        "value": row.get("value"),
        "unit": row.get("unit"),
        "period": row.get("period"),
        "fiscalYear": row.get("fiscalYear"),
        "fiscalQuarter": row.get("fiscalQuarter"),
        "sourceType": row.get("sourceType"),
        "sourceUrl": row.get("sourceUrl"),
        "sourceDocumentTitle": row.get("sourceDocumentTitle"),
        "accessionNumber": row.get("accessionNumber"),
        "extractedText": row.get("extractedText"),
        "confidence": row.get("confidence"),
        "reviewStatus": row.get("reviewStatus"),
        "reviewedAt": row.get("reviewedAt"),
        "reviewedBy": row.get("reviewedBy"),
        "correctionNotes": row.get("correctionNotes"),
        "metricVariant": row.get("metricVariant"),
        "targetBasis": row.get("targetBasis"),
        "freshnessStatus": row.get("freshnessStatus"),
        "updatedAt": row.get("updatedAt"),
        "eligibleForScoring": row.get("eligibleForScoring"),
    }


def _scoring_storage_value(metric_key: object, value: float, unit: object) -> float:
    if str(unit or "").strip().lower() in {"percent", "%", "percentage", "pct"} and is_business_percent_metric(metric_key):
        converted = display_percent_to_scoring_ratio(value, unit, str(metric_key or ""))
        if converted is not None:
            return converted
    return value


def _review_summary_from_rows(rows: list[dict]) -> dict:
    summary = {
        "total": len(rows),
        "pending_review": 0,
        "approved": 0,
        "rejected": 0,
        "manually_corrected": 0,
        "stale": 0,
        "pendingHighConfidence": 0,
        "pendingMediumConfidence": 0,
        "pendingLowConfidence": 0,
    }
    for row in rows:
        status = str(row.get("reviewStatus") or PENDING_REVIEW_STATUS)
        if status in summary:
            summary[status] += 1
        if status == PENDING_REVIEW_STATUS:
            confidence = str(row.get("confidence") or "").lower()
            if confidence == "high":
                summary["pendingHighConfidence"] += 1
            elif confidence == "medium":
                summary["pendingMediumConfidence"] += 1
            elif confidence == "low":
                summary["pendingLowConfidence"] += 1
    return summary


def _critical_pending_metrics(rows: list[dict]) -> list[str]:
    metrics: list[str] = []
    for row in rows:
        if row.get("reviewStatus") != PENDING_REVIEW_STATUS:
            continue
        definition = metric_source_definition(str(row.get("metricKey")))
        if definition and definition.missingImpact in CRITICAL_MISSING_IMPACTS:
            metrics.append(definition.displayName)
    return sorted(set(metrics))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
