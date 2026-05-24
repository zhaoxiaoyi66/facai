from __future__ import annotations

import json
import sqlite3
import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

from data.disclosure_store import DisclosureStore, canMetricEnterScoring
from data.extract_metric_from_text import validate_extracted_metric_candidate
from data.fundamentals import FundamentalCache
from data.metric_source_map import metric_source_definition
from data.metric_variants import metric_variant_for_key, target_basis_for_metric
from data.normalize_metric_value import normalize_metric_period, normalize_metric_value
from data.prices import CACHE_PATH, PriceCache
from indicators.technicals import add_technical_indicators, latest_technical_snapshot
from scoring.total_score import calculate_total_score
from settings import load_watchlist


EXTRACTED_VALUE_SOURCES = {"SEC_8K", "SEC_10Q", "SEC_10K", "IR_RELEASE", "IR_PRESENTATION", "FMP_TRANSCRIPT"}
QUEUE_REVIEW_STATUSES = {
    "pending_review",
    "needs_data",
    "needs_evidence",
    "invalid_review_item",
    "approved",
    "rejected",
    "manually_corrected",
    "auto_approved_by_ai",
    "stale",
    "auto_archived",
    "duplicate_archived",
}
TERMINAL_REVIEW_STATUSES = {"approved", "auto_approved_by_ai", "rejected", "manually_corrected", "auto_archived", "duplicate_archived", "invalid_review_item"}
SCORING_QUEUE_STATUSES = {"approved", "manually_corrected", "auto_approved_by_ai"}
STALE_TRIGGER_STATUSES = SCORING_QUEUE_STATUSES | {"rejected", "auto_archived", "duplicate_archived", "invalid_review_item"}
SCORING_AFFECTS = {"Quality", "Entry", "Risk"}
AI_TRIAGE_STATUSES = {
    "auto_approved_by_ai",
    "ai_recommend_approve",
    "ai_recommend_correct",
    "ai_recommend_reject",
    "ai_needs_human_review",
    "ai_not_enough_evidence",
    "ai_invalid_output",
    "ai_skipped",
    "ai_auto_archived",
    "extraction_rejected_by_rule",
    "needs_more_source",
    "ready_for_qwen",
}


@dataclass(frozen=True)
class QueueBuildResult:
    symbols: list[str]
    created: int
    updated: int
    skipped: int
    total: int
    item_type_counts: dict[str, int]


class ReviewQueueStore:
    def __init__(self, path: Path = CACHE_PATH, disclosure_store: DisclosureStore | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.disclosure_store = disclosure_store or DisclosureStore(path)
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
                CREATE TABLE IF NOT EXISTS review_queue_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    metricKey TEXT NOT NULL,
                    displayName TEXT,
                    itemType TEXT NOT NULL,
                    value REAL,
                    unit TEXT,
                    period TEXT,
                    sourceType TEXT,
                    sourceUrl TEXT,
                    sourceDocumentTitle TEXT,
                    extractedText TEXT,
                    evidenceText TEXT,
                    systemReason TEXT,
                    evidenceHash TEXT,
                    confidence TEXT NOT NULL DEFAULT 'low',
                    affects TEXT,
                    reviewStatus TEXT NOT NULL,
                    recommendedAction TEXT,
                    resolutionStatus TEXT,
                    sourceKind TEXT NOT NULL DEFAULT 'metric_resolution',
                    sourceMetricId INTEGER,
                    modelType TEXT,
                    explanation TEXT,
                    normalizedValue REAL,
                    displayValue TEXT,
                    metricPeriod TEXT,
                    fiscalPeriod TEXT,
                    metricVariant TEXT,
                    targetBasis TEXT,
                    freshnessStatus TEXT,
                    extractionRule TEXT,
                    updatedAt TEXT NOT NULL,
                    reviewedAt TEXT,
                    correctionNotes TEXT,
                    aiTriageStatus TEXT,
                    aiReviewResultId INTEGER,
                    approvedBy TEXT,
                    approvedAt TEXT,
                    rejectedBy TEXT,
                    evidenceQuote TEXT,
                    aiExplanationZh TEXT,
                    correctionCandidate TEXT,
                    hiddenByDefault INTEGER NOT NULL DEFAULT 0,
                    canAutoFill INTEGER NOT NULL DEFAULT 0,
                    autoFillType TEXT,
                    autoFillStatus TEXT NOT NULL DEFAULT 'not_started',
                    autoFillError TEXT,
                    lastAutoFillAt TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS review_automation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    runId TEXT NOT NULL,
                    reviewItemId INTEGER,
                    action TEXT NOT NULL,
                    symbol TEXT,
                    metricKey TEXT,
                    oldStatus TEXT,
                    newStatus TEXT,
                    reason TEXT,
                    actor TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_automation_operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actionName TEXT NOT NULL,
                    selectedFilters TEXT,
                    eligibleItemCount INTEGER NOT NULL DEFAULT 0,
                    processedCount INTEGER NOT NULL DEFAULT 0,
                    skippedCount INTEGER NOT NULL DEFAULT 0,
                    failedCount INTEGER NOT NULL DEFAULT 0,
                    errorMessages TEXT,
                    startedAt TEXT NOT NULL,
                    finishedAt TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS autopilot_runs (
                    runId TEXT PRIMARY KEY,
                    startedAt TEXT NOT NULL,
                    finishedAt TEXT,
                    filters TEXT,
                    scannedCount INTEGER NOT NULL DEFAULT 0,
                    processableCount INTEGER NOT NULL DEFAULT 0,
                    autoFillSuccessCount INTEGER NOT NULL DEFAULT 0,
                    qwenReviewedCount INTEGER NOT NULL DEFAULT 0,
                    autoApprovedCount INTEGER NOT NULL DEFAULT 0,
                    autoArchivedCount INTEGER NOT NULL DEFAULT 0,
                    needsHumanCount INTEGER NOT NULL DEFAULT 0,
                    unsupportedCount INTEGER NOT NULL DEFAULT 0,
                    failedCount INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS autopilot_run_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    runId TEXT NOT NULL,
                    reviewItemId INTEGER,
                    symbol TEXT,
                    metricKey TEXT,
                    oldStatus TEXT,
                    newStatus TEXT,
                    actionTaken TEXT NOT NULL,
                    reason TEXT,
                    errorType TEXT,
                    errorMessage TEXT,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS review_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reviewItemId INTEGER NOT NULL,
                    symbol TEXT,
                    metricKey TEXT,
                    action TEXT NOT NULL,
                    oldReviewStatus TEXT,
                    newReviewStatus TEXT,
                    oldValue REAL,
                    newValue REAL,
                    oldConfidence TEXT,
                    newConfidence TEXT,
                    actor TEXT NOT NULL,
                    reason TEXT,
                    createdAt TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metric_value_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reviewItemId INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    metricKey TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    value REAL,
                    unit TEXT,
                    period TEXT,
                    sourceType TEXT,
                    sourceUrl TEXT,
                    evidenceText TEXT,
                    confidence TEXT,
                    reviewStatus TEXT NOT NULL,
                    isActive INTEGER NOT NULL DEFAULT 0,
                    createdAt TEXT NOT NULL,
                    createdBy TEXT NOT NULL,
                    supersededByVersionId INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_score_status (
                    symbol TEXT PRIMARY KEY,
                    scoreStatus TEXT NOT NULL,
                    staleReason TEXT,
                    lastScoredAt TEXT,
                    lastScoreRunId TEXT,
                    updatedAt TEXT NOT NULL
                )
                """
            )
            _ensure_column(conn, "review_queue_items", "aiTriageStatus", "TEXT")
            _ensure_column(conn, "review_queue_items", "evidenceText", "TEXT")
            _ensure_column(conn, "review_queue_items", "systemReason", "TEXT")
            _ensure_column(conn, "review_queue_items", "evidenceHash", "TEXT")
            _ensure_column(conn, "review_queue_items", "aiReviewResultId", "INTEGER")
            _ensure_column(conn, "review_queue_items", "approvedBy", "TEXT")
            _ensure_column(conn, "review_queue_items", "approvedAt", "TEXT")
            _ensure_column(conn, "review_queue_items", "rejectedBy", "TEXT")
            _ensure_column(conn, "review_queue_items", "evidenceQuote", "TEXT")
            _ensure_column(conn, "review_queue_items", "aiExplanationZh", "TEXT")
            _ensure_column(conn, "review_queue_items", "correctionCandidate", "TEXT")
            _ensure_column(conn, "review_queue_items", "hiddenByDefault", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "review_queue_items", "canAutoFill", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "review_queue_items", "autoFillType", "TEXT")
            _ensure_column(conn, "review_queue_items", "autoFillStatus", "TEXT NOT NULL DEFAULT 'not_started'")
            _ensure_column(conn, "review_queue_items", "autoFillError", "TEXT")
            _ensure_column(conn, "review_queue_items", "lastAutoFillAt", "TEXT")
            _ensure_column(conn, "review_queue_items", "qwenEligible", "INTEGER")
            _ensure_column(conn, "review_queue_items", "qwenIneligibleReason", "TEXT")
            _ensure_column(conn, "review_queue_items", "normalizedValue", "REAL")
            _ensure_column(conn, "review_queue_items", "displayValue", "TEXT")
            _ensure_column(conn, "review_queue_items", "metricPeriod", "TEXT")
            _ensure_column(conn, "review_queue_items", "fiscalPeriod", "TEXT")
            _ensure_column(conn, "review_queue_items", "metricVariant", "TEXT")
            _ensure_column(conn, "review_queue_items", "targetBasis", "TEXT")
            _ensure_column(conn, "review_queue_items", "freshnessStatus", "TEXT")
            _ensure_column(conn, "review_queue_items", "extractionRule", "TEXT")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_review_queue_source_metric
                ON review_queue_items(sourceKind, sourceMetricId)
                WHERE sourceMetricId IS NOT NULL
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_review_queue_symbol_metric_type
                ON review_queue_items(symbol, metricKey, itemType)
                WHERE sourceMetricId IS NULL
                """
            )

    def upsert_item(self, item: dict) -> str:
        normalized = _normalize_queue_item(item)
        existing = self._find_existing(normalized)
        if existing and str(existing.get("reviewStatus")) in TERMINAL_REVIEW_STATUSES:
            return "skipped"

        if existing:
            with self.connect() as conn:
                conn.execute(
                    """
                    UPDATE review_queue_items
                    SET displayName = ?,
                        value = ?,
                        unit = ?,
                        period = ?,
                        sourceType = ?,
                        sourceUrl = ?,
                        sourceDocumentTitle = ?,
                        extractedText = ?,
                        evidenceText = ?,
                        systemReason = ?,
                        evidenceHash = ?,
                        confidence = ?,
                        affects = ?,
                        reviewStatus = ?,
                        recommendedAction = ?,
                        resolutionStatus = ?,
                        sourceKind = ?,
                        sourceMetricId = ?,
                        modelType = ?,
                        explanation = ?,
                        normalizedValue = ?,
                        displayValue = ?,
                        metricPeriod = ?,
                        fiscalPeriod = ?,
                        metricVariant = ?,
                        targetBasis = ?,
                        freshnessStatus = ?,
                        extractionRule = ?,
                        aiTriageStatus = COALESCE(?, aiTriageStatus),
                        hiddenByDefault = CASE WHEN ? = 'auto_archived' THEN 1 ELSE hiddenByDefault END,
                        updatedAt = ?
                    WHERE id = ?
                    """,
                    (
                        normalized["displayName"],
                        normalized["value"],
                        normalized["unit"],
                        normalized["period"],
                        normalized["sourceType"],
                        normalized["sourceUrl"],
                        normalized["sourceDocumentTitle"],
                        normalized["extractedText"],
                        normalized["evidenceText"],
                        normalized["systemReason"],
                        normalized["evidenceHash"],
                        normalized["confidence"],
                        normalized["affects"],
                        normalized["reviewStatus"],
                        normalized["recommendedAction"],
                        normalized["resolutionStatus"],
                        normalized["sourceKind"],
                        normalized["sourceMetricId"],
                        normalized["modelType"],
                        normalized["explanation"],
                        normalized["normalizedValue"],
                        normalized["displayValue"],
                        normalized["metricPeriod"],
                        normalized["fiscalPeriod"],
                        normalized["metricVariant"],
                        normalized["targetBasis"],
                        normalized["freshnessStatus"],
                        normalized["extractionRule"],
                        normalized["aiTriageStatus"],
                        normalized["reviewStatus"],
                        _now(),
                        existing["id"],
                    ),
                )
            return "updated"

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO review_queue_items (
                    symbol, metricKey, displayName, itemType, value, unit, period,
                    sourceType, sourceUrl, sourceDocumentTitle, extractedText,
                    evidenceText, systemReason, evidenceHash, confidence,
                    affects, reviewStatus, recommendedAction, resolutionStatus, sourceKind,
                    sourceMetricId, modelType, explanation, normalizedValue, displayValue,
                    metricPeriod, fiscalPeriod, metricVariant, targetBasis, freshnessStatus,
                    extractionRule, aiTriageStatus, hiddenByDefault, updatedAt
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["symbol"],
                    normalized["metricKey"],
                    normalized["displayName"],
                    normalized["itemType"],
                    normalized["value"],
                    normalized["unit"],
                    normalized["period"],
                    normalized["sourceType"],
                    normalized["sourceUrl"],
                    normalized["sourceDocumentTitle"],
                    normalized["extractedText"],
                    normalized["evidenceText"],
                    normalized["systemReason"],
                    normalized["evidenceHash"],
                    normalized["confidence"],
                    normalized["affects"],
                    normalized["reviewStatus"],
                    normalized["recommendedAction"],
                    normalized["resolutionStatus"],
                    normalized["sourceKind"],
                    normalized["sourceMetricId"],
                    normalized["modelType"],
                    normalized["explanation"],
                    normalized["normalizedValue"],
                    normalized["displayValue"],
                    normalized["metricPeriod"],
                    normalized["fiscalPeriod"],
                    normalized["metricVariant"],
                    normalized["targetBasis"],
                    normalized["freshnessStatus"],
                    normalized["extractionRule"],
                    normalized["aiTriageStatus"],
                    1 if normalized["reviewStatus"] == "auto_archived" else 0,
                    _now(),
                ),
            )
        return "created"

    def list_items(
        self,
        symbol: str | None = None,
        metric_key: str | None = None,
        item_type: str | None = None,
        source_type: str | None = None,
        confidence: str | None = None,
        review_status: str | None = None,
        model_type: str | None = None,
        affects_scoring: bool = False,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[object] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if metric_key:
            clauses.append("metricKey = ?")
            params.append(metric_key)
        if item_type:
            clauses.append("itemType = ?")
            params.append(item_type)
        if source_type:
            clauses.append("sourceType = ?")
            params.append(source_type)
        if confidence:
            clauses.append("confidence = ?")
            params.append(confidence)
        if review_status:
            clauses.append("reviewStatus = ?")
            params.append(review_status)
        if model_type:
            clauses.append("modelType = ?")
            params.append(model_type)
        if affects_scoring:
            clauses.append("(affects LIKE '%Quality%' OR affects LIKE '%Entry%' OR affects LIKE '%Risk%')")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM review_queue_items
                {where}
                ORDER BY
                    CASE aiTriageStatus
                        WHEN 'ai_recommend_reject' THEN 0
                        WHEN 'ai_recommend_correct' THEN 1
                        WHEN 'ai_not_enough_evidence' THEN 2
                        WHEN 'ai_needs_human_review' THEN 3
                        WHEN 'ai_recommend_approve' THEN 4
                        WHEN 'auto_approved_by_ai' THEN 5
                        WHEN 'ai_auto_archived' THEN 6
                        ELSE 6
                    END,
                    CASE reviewStatus
                        WHEN 'pending_review' THEN 0
                        WHEN 'needs_evidence' THEN 1
                        WHEN 'needs_data' THEN 2
                        WHEN 'manually_corrected' THEN 3
                        WHEN 'approved' THEN 4
                        WHEN 'auto_approved_by_ai' THEN 4
                        WHEN 'rejected' THEN 5
                        WHEN 'auto_archived' THEN 6
                        ELSE 6
                    END,
                    symbol,
                    itemType,
                    metricKey
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self, symbol: str | None = None) -> dict:
        rows = self.list_items(symbol=symbol)
        summary = {
            "total": len(rows),
            "symbols": len({row["symbol"] for row in rows}),
            "pending_review": 0,
            "needs_data": 0,
            "needs_evidence": 0,
            "invalid_review_item": 0,
            "approved": 0,
            "auto_approved_by_ai_status": 0,
            "rejected": 0,
            "manually_corrected": 0,
            "stale": 0,
            "duplicate_archived": 0,
            "extracted_value": 0,
            "evidence_missing_extracted_value": 0,
            "missing_kpi": 0,
            "derived_low_confidence": 0,
            "qualitative_risk": 0,
            "analyst_estimate_needed": 0,
            "manual_override_needed": 0,
            "ai_reviewed": 0,
            "auto_approved_by_ai": 0,
            "ai_recommend_approve": 0,
            "ai_recommend_correct": 0,
            "ai_recommend_reject": 0,
            "ai_needs_human_review": 0,
            "ai_not_enough_evidence": 0,
            "ai_invalid_output": 0,
            "ai_skipped": 0,
            "ai_auto_archived": 0,
            "extraction_rejected_by_rule": 0,
            "needs_more_source": 0,
            "ready_for_qwen": 0,
            "ai_hidden_auto_approved": 0,
            "auto_archived": 0,
        }
        for row in rows:
            status = str(row.get("reviewStatus") or "")
            item_type = str(row.get("itemType") or "")
            triage_status = str(row.get("aiTriageStatus") or "")
            if status in summary:
                summary[status] += 1
            if status == "auto_approved_by_ai":
                summary["auto_approved_by_ai_status"] += 1
            if item_type in summary:
                summary[item_type] += 1
            if triage_status:
                summary["ai_reviewed"] += 1
            if triage_status in summary:
                summary[triage_status] += 1
            if triage_status == "auto_approved_by_ai" and row.get("hiddenByDefault"):
                summary["ai_hidden_auto_approved"] += 1
        return summary

    def get_item(self, item_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM review_queue_items WHERE id = ?", (int(item_id),)).fetchone()
        return dict(row) if row else None

    def update_review_status(self, item_id: int, review_status: str, notes: str | None = None, actor: str = "user") -> None:
        status = _normalize_queue_status(review_status)
        old = self.get_item(int(item_id))
        if not old:
            return
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE review_queue_items
                SET reviewStatus = ?, reviewedAt = ?, correctionNotes = COALESCE(?, correctionNotes), updatedAt = ?
                WHERE id = ?
                """,
                (status, _now(), notes, _now(), item_id),
            )
        new = self.get_item(int(item_id))
        self._after_status_change(old, new, _audit_action_for_status_change(str(old.get("reviewStatus") or ""), status, notes), actor, notes)
        if old["sourceKind"] == "disclosure_metric_values" and old["sourceMetricId"]:
            if status in {"approved", "rejected", "manually_corrected", "stale"}:
                self.disclosure_store.update_review_status(int(old["sourceMetricId"]), status)
            elif status in {"pending_review", "needs_data"}:
                self.disclosure_store.update_review_status(int(old["sourceMetricId"]), "pending_review")

    def mark_item_current_value(self, item_id: int, actor: str = "user") -> tuple[dict | None, dict | None]:
        old = self.get_item(int(item_id))
        if not old:
            return None, None
        metric_variant = old.get("metricVariant") or metric_variant_for_key(old.get("metricKey"))
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE review_queue_items
                SET freshnessStatus = CASE WHEN id = ? THEN 'active_current' ELSE 'historical_value' END,
                    reviewStatus = CASE WHEN id = ? THEN 'pending_review' ELSE reviewStatus END,
                    aiTriageStatus = CASE WHEN id = ? THEN NULL ELSE aiTriageStatus END,
                    hiddenByDefault = CASE WHEN id = ? THEN 0 ELSE hiddenByDefault END,
                    updatedAt = ?
                WHERE symbol = ? AND metricVariant = ?
                """,
                (int(item_id), int(item_id), int(item_id), int(item_id), _now(), old.get("symbol"), metric_variant),
            )
        if old.get("sourceKind") == "disclosure_metric_values" and old.get("sourceMetricId"):
            with self.disclosure_store.connect() as conn:
                conn.execute(
                    """
                    UPDATE disclosure_metric_values
                    SET freshnessStatus = CASE WHEN id = ? THEN 'active_current' ELSE 'historical_value' END,
                        updatedAt = ?
                    WHERE symbol = ? AND metricVariant = ?
                    """,
                    (int(old["sourceMetricId"]), _now(), old.get("symbol"), metric_variant),
                )
        new = self.get_item(int(item_id))
        self._after_status_change(old, new, "set_current_value", actor, "historical_value_promoted_to_current")
        return old, new

    def auto_archive_item(self, item_id: int, reason: str | None = None) -> tuple[dict | None, dict | None]:
        old = self.get_item(item_id)
        if not old:
            return None, None
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE review_queue_items
                SET reviewStatus = 'auto_archived',
                    reviewedAt = ?,
                    correctionNotes = COALESCE(?, correctionNotes),
                    aiTriageStatus = 'ai_auto_archived',
                    aiExplanationZh = COALESCE(?, aiExplanationZh),
                    hiddenByDefault = 1,
                    updatedAt = ?
                WHERE id = ?
                """,
                (now, reason or "AI自动归档低优先级项", reason, now, int(item_id)),
            )
        new = self.get_item(item_id)
        self._after_status_change(old, new, "archive", "user", reason)
        return old, new

    def undo_review_status(self, item_id: int, target_status: str = "pending_review", reason: str | None = None, actor: str = "user") -> tuple[dict | None, dict | None]:
        status = _normalize_queue_status(target_status)
        if status in SCORING_QUEUE_STATUSES:
            status = "pending_review"
        old = self.get_item(int(item_id))
        if not old:
            return None, None
        old_status = str(old.get("reviewStatus") or "")
        action = _undo_action_for_row(old)
        restored_version = self._previous_confirmed_version(int(item_id)) if old_status == "manually_corrected" else None
        restored_status = str(restored_version.get("reviewStatus") or "approved") if restored_version else None
        with self.connect() as conn:
            if restored_version and restored_status in SCORING_QUEUE_STATUSES:
                conn.execute(
                    """
                    UPDATE review_queue_items
                    SET reviewStatus = ?,
                        value = ?,
                        unit = ?,
                        period = ?,
                        sourceType = COALESCE(?, sourceType),
                        sourceUrl = COALESCE(?, sourceUrl),
                        evidenceText = COALESCE(?, evidenceText),
                        extractedText = COALESCE(?, extractedText),
                        confidence = COALESCE(?, confidence),
                        aiTriageStatus = NULL,
                        hiddenByDefault = 0,
                        approvedBy = NULL,
                        approvedAt = NULL,
                        rejectedBy = NULL,
                        reviewedAt = ?,
                        correctionNotes = COALESCE(?, correctionNotes),
                        updatedAt = ?
                    WHERE id = ?
                    """,
                    (
                        restored_status,
                        restored_version.get("value"),
                        restored_version.get("unit"),
                        restored_version.get("period"),
                        restored_version.get("sourceType"),
                        restored_version.get("sourceUrl"),
                        restored_version.get("evidenceText"),
                        restored_version.get("evidenceText"),
                        restored_version.get("confidence"),
                        _now(),
                        reason or action,
                        _now(),
                        int(item_id),
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE review_queue_items
                    SET reviewStatus = ?,
                        aiTriageStatus = CASE
                            WHEN aiTriageStatus IN ('auto_approved_by_ai', 'ai_auto_archived') THEN NULL
                            ELSE aiTriageStatus
                        END,
                        hiddenByDefault = 0,
                        approvedBy = NULL,
                        approvedAt = NULL,
                        rejectedBy = NULL,
                        reviewedAt = ?,
                        correctionNotes = COALESCE(?, correctionNotes),
                        updatedAt = ?
                    WHERE id = ?
                    """,
                    (status, _now(), reason or action, _now(), int(item_id)),
                )
        if old.get("sourceKind") == "disclosure_metric_values" and old.get("sourceMetricId"):
            if restored_version and restored_status in SCORING_QUEUE_STATUSES:
                self._restore_disclosure_metric_from_version(int(old["sourceMetricId"]), restored_version, actor, reason or action)
            else:
                disclosure_status = status if status in {"approved", "rejected", "manually_corrected", "stale"} else "pending_review"
                self.disclosure_store.update_review_status(int(old["sourceMetricId"]), disclosure_status, reviewed_by=actor, correction_notes=reason or action)
        new = self.get_item(int(item_id))
        self._after_status_change(old, new, action, actor, reason)
        return old, new

    def log_review_audit(self, old: dict | None, new: dict | None, action: str, actor: str = "user", reason: str | None = None) -> int:
        row = new or old or {}
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO review_audit_logs (
                    reviewItemId, symbol, metricKey, action,
                    oldReviewStatus, newReviewStatus, oldValue, newValue,
                    oldConfidence, newConfidence, actor, reason, createdAt
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(row.get("id") or 0),
                    row.get("symbol"),
                    row.get("metricKey"),
                    action,
                    old.get("reviewStatus") if old else None,
                    new.get("reviewStatus") if new else None,
                    old.get("value") if old else None,
                    new.get("value") if new else None,
                    old.get("confidence") if old else None,
                    new.get("confidence") if new else None,
                    actor,
                    reason,
                    _now(),
                ),
            )
            return int(cursor.lastrowid)

    def list_review_audit_logs(self, item_id: int | None = None, limit: int | None = None) -> list[dict]:
        clauses: list[str] = []
        params: list[object] = []
        if item_id is not None:
            clauses.append("reviewItemId = ?")
            params.append(int(item_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_sql = " LIMIT ?" if limit else ""
        if limit:
            params.append(int(limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM review_audit_logs {where} ORDER BY createdAt DESC, id DESC{limit_sql}",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recent_confirmed_items(self, days: int = 7) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(int(days), 0))).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM review_queue_items
                WHERE (reviewStatus IN ('approved', 'manually_corrected', 'auto_approved_by_ai')
                   OR aiTriageStatus = 'auto_approved_by_ai')
                  AND COALESCE(approvedAt, reviewedAt, updatedAt) >= ?
                ORDER BY COALESCE(approvedAt, reviewedAt, updatedAt) DESC, id DESC
                """,
                (cutoff,),
            ).fetchall()
        results: list[dict] = []
        for row in rows:
            item = dict(row)
            item["reviewItemId"] = int(item.get("id") or 0)
            item["confirmedAt"] = item.get("approvedAt") or item.get("reviewedAt") or item.get("updatedAt")
            item["canEnterScoring"] = canMetricEnterScoring(item)
            results.append(item)
        return results

    def list_recent_confirmed(self, days: int = 7) -> list[dict]:
        return self.list_recent_confirmed_items(days)

    def list_metric_versions(self, item_id: int | None = None) -> list[dict]:
        clauses: list[str] = []
        params: list[object] = []
        if item_id is not None:
            clauses.append("reviewItemId = ?")
            params.append(int(item_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM metric_value_versions {where} ORDER BY reviewItemId, version DESC, id DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_score_stale(self, symbol: str, reason: str, run_id: str | None = None) -> None:
        if not symbol:
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO stock_score_status (symbol, scoreStatus, staleReason, lastScoredAt, lastScoreRunId, updatedAt)
                VALUES (?, 'stale', ?, NULL, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    scoreStatus = 'stale',
                    staleReason = excluded.staleReason,
                    lastScoreRunId = COALESCE(excluded.lastScoreRunId, stock_score_status.lastScoreRunId),
                    updatedAt = excluded.updatedAt
                """,
                (str(symbol).upper(), reason, run_id, _now()),
            )

    def mark_score_fresh(self, symbol: str, run_id: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO stock_score_status (symbol, scoreStatus, staleReason, lastScoredAt, lastScoreRunId, updatedAt)
                VALUES (?, 'fresh', NULL, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    scoreStatus = 'fresh',
                    staleReason = NULL,
                    lastScoredAt = excluded.lastScoredAt,
                    lastScoreRunId = excluded.lastScoreRunId,
                    updatedAt = excluded.updatedAt
                """,
                (str(symbol).upper(), _now(), run_id, _now()),
            )

    def get_score_status(self, symbol: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM stock_score_status WHERE symbol = ?", (str(symbol).upper(),)).fetchone()
        return dict(row) if row else {"symbol": str(symbol).upper(), "scoreStatus": "fresh", "staleReason": None}

    def _after_status_change(self, old: dict | None, new: dict | None, action: str, actor: str, reason: str | None) -> None:
        if not old or not new:
            return
        self.log_review_audit(old, new, action, actor, reason)
        old_status = str(old.get("reviewStatus") or "")
        new_status = str(new.get("reviewStatus") or "")
        if old_status in STALE_TRIGGER_STATUSES or new_status in STALE_TRIGGER_STATUSES or action.startswith("undo") or action in {"manual_correct", "auto_approve"}:
            self._write_metric_version(new, action, actor)
            self.mark_score_stale(str(new.get("symbol") or ""), f"{action}: {new.get('metricKey')}", None)

    def _write_metric_version(self, row: dict, action: str, actor: str) -> None:
        item_id = int(row.get("id") or 0)
        is_active = str(row.get("reviewStatus") or "") in SCORING_QUEUE_STATUSES
        with self.connect() as conn:
            previous = conn.execute(
                "SELECT id, version FROM metric_value_versions WHERE reviewItemId = ? AND isActive = 1 ORDER BY version DESC, id DESC LIMIT 1",
                (item_id,),
            ).fetchone()
            next_version = int((previous["version"] if previous else 0) or 0) + 1
            cursor = conn.execute(
                """
                INSERT INTO metric_value_versions (
                    reviewItemId, symbol, metricKey, version, value, unit, period,
                    sourceType, sourceUrl, evidenceText, confidence, reviewStatus,
                    isActive, createdAt, createdBy, supersededByVersionId
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    str(row.get("symbol") or "").upper(),
                    str(row.get("metricKey") or ""),
                    next_version,
                    row.get("value"),
                    row.get("unit"),
                    row.get("period") or row.get("metricPeriod") or row.get("fiscalPeriod"),
                    row.get("sourceType"),
                    row.get("sourceUrl"),
                    row.get("evidenceText") or row.get("extractedText"),
                    row.get("confidence"),
                    row.get("reviewStatus"),
                    1 if is_active else 0,
                    _now(),
                    actor,
                    int(previous["id"]) if previous else None,
                ),
            )
            new_version_id = int(cursor.lastrowid)
            conn.execute(
                """
                UPDATE metric_value_versions
                SET isActive = 0,
                    supersededByVersionId = COALESCE(supersededByVersionId, ?)
                WHERE reviewItemId = ? AND id <> ?
                """,
                (new_version_id, item_id, new_version_id),
            )

    def _previous_confirmed_version(self, item_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM metric_value_versions
                WHERE reviewItemId = ?
                  AND reviewStatus IN ('approved', 'auto_approved_by_ai')
                ORDER BY version DESC, id DESC
                LIMIT 1
                """,
                (int(item_id),),
            ).fetchone()
        return dict(row) if row else None

    def _restore_disclosure_metric_from_version(self, metric_id: int, version: dict, actor: str, reason: str) -> None:
        with self.disclosure_store.connect() as conn:
            conn.execute(
                """
                UPDATE disclosure_metric_values
                SET value = ?,
                    unit = ?,
                    period = ?,
                    sourceType = COALESCE(?, sourceType),
                    sourceUrl = COALESCE(?, sourceUrl),
                    extractedText = COALESCE(?, extractedText),
                    confidence = COALESCE(?, confidence),
                    reviewStatus = ?,
                    reviewedAt = ?,
                    reviewedBy = ?,
                    correctionNotes = COALESCE(?, correctionNotes),
                    updatedAt = ?
                WHERE id = ?
                """,
                (
                    version.get("value"),
                    version.get("unit"),
                    version.get("period"),
                    version.get("sourceType"),
                    version.get("sourceUrl"),
                    version.get("evidenceText"),
                    version.get("confidence"),
                    version.get("reviewStatus") or "approved",
                    _now(),
                    actor,
                    reason,
                    _now(),
                    int(metric_id),
                ),
            )

    def set_ai_triage(
        self,
        item_id: int,
        ai_triage_status: str,
        ai_review_result_id: int | None = None,
        evidence_quote: str | None = None,
        explanation_zh: str | None = None,
        correction_candidate: dict | None = None,
    ) -> None:
        triage = _normalize_ai_triage_status(ai_triage_status)
        candidate_json = json.dumps(correction_candidate, ensure_ascii=False) if correction_candidate else None
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE review_queue_items
                SET aiTriageStatus = ?,
                    aiReviewResultId = COALESCE(?, aiReviewResultId),
                    evidenceQuote = COALESCE(?, evidenceQuote),
                    aiExplanationZh = COALESCE(?, aiExplanationZh),
                    correctionCandidate = COALESCE(?, correctionCandidate),
                    updatedAt = ?
                WHERE id = ?
                """,
                (triage, ai_review_result_id, evidence_quote, explanation_zh, candidate_json, _now(), int(item_id)),
            )

    def update_auto_fill_status(
        self,
        item_id: int,
        can_auto_fill: bool,
        auto_fill_type: str,
        auto_fill_status: str,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE review_queue_items
                SET canAutoFill = ?,
                    autoFillType = ?,
                    autoFillStatus = ?,
                    autoFillError = ?,
                    lastAutoFillAt = ?,
                    updatedAt = ?
                WHERE id = ?
                """,
                (
                    1 if can_auto_fill else 0,
                    auto_fill_type,
                    auto_fill_status,
                    error,
                    _now(),
                    _now(),
                    int(item_id),
                ),
            )

    def archive_duplicate_items(self, symbols: Iterable[str] | None = None) -> int:
        symbol_set = {str(symbol).upper() for symbol in symbols or [] if str(symbol).strip()}
        rows = self.list_items()
        if symbol_set:
            rows = [row for row in rows if str(row.get("symbol") or "").upper() in symbol_set]
        groups: dict[tuple, list[dict]] = {}
        for row in rows:
            if str(row.get("itemType") or "") != "extracted_value":
                continue
            if str(row.get("reviewStatus") or "") in TERMINAL_REVIEW_STATUSES:
                continue
            key = (
                str(row.get("symbol") or ""),
                str(row.get("metricKey") or ""),
                str(row.get("period") or ""),
                str(row.get("sourceType") or ""),
                str(row.get("sourceUrl") or ""),
                str(row.get("value") or ""),
                str(row.get("evidenceHash") or ""),
            )
            groups.setdefault(key, []).append(row)
        duplicate_ids: list[int] = []
        for grouped in groups.values():
            if len(grouped) <= 1:
                continue
            sorted_group = sorted(grouped, key=lambda row: (str(row.get("updatedAt") or ""), int(row.get("id") or 0)), reverse=True)
            duplicate_ids.extend(int(row["id"]) for row in sorted_group[1:])
        if not duplicate_ids:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                UPDATE review_queue_items
                SET reviewStatus = 'duplicate_archived',
                    aiTriageStatus = 'ai_auto_archived',
                    hiddenByDefault = 1,
                    correctionNotes = COALESCE(correctionNotes, 'duplicate_archived'),
                    updatedAt = ?
                WHERE id = ?
                """,
                [(_now(), item_id) for item_id in duplicate_ids],
            )
        return len(duplicate_ids)

    def log_automation_action(
        self,
        run_id: str,
        action: str,
        row: dict | None,
        old_status: str | None,
        new_status: str | None,
        reason: str,
        actor: str = "autopilot",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO review_automation_logs (
                    runId, reviewItemId, action, symbol, metricKey,
                    oldStatus, newStatus, reason, actor, timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(row["id"]) if row and row.get("id") is not None else None,
                    action,
                    row.get("symbol") if row else None,
                    row.get("metricKey") if row else None,
                    old_status,
                    new_status,
                    reason,
                    actor,
                    _now(),
                ),
            )

    def update_qwen_eligibility(self, item_id: int, eligible: bool, reason: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE review_queue_items
                SET qwenEligible = ?,
                    qwenIneligibleReason = ?,
                    updatedAt = ?
                WHERE id = ?
                """,
                (1 if eligible else 0, reason, _now(), int(item_id)),
            )

    def update_evidence(
        self,
        item_id: int,
        evidence_text: str,
        source_url: str | None = None,
        source_document_title: str | None = None,
        metric_period: str | None = None,
        fiscal_period: str | None = None,
        extraction_rule: str | None = None,
    ) -> tuple[dict | None, dict | None]:
        old = self.get_item(int(item_id))
        if not old:
            return None, None
        evidence = str(evidence_text or "").strip()
        normalized = normalize_metric_value(old.get("value"), old.get("unit"), evidence, str(old.get("metricKey") or ""))
        periods = normalize_metric_period({**old, "extractedText": evidence, "period": metric_period or old.get("period")})
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE review_queue_items
                SET itemType = 'extracted_value',
                    reviewStatus = 'pending_review',
                    aiTriageStatus = 'ready_for_qwen',
                    sourceUrl = COALESCE(?, sourceUrl),
                    sourceDocumentTitle = COALESCE(?, sourceDocumentTitle),
                    extractedText = ?,
                    evidenceText = ?,
                    evidenceHash = ?,
                    normalizedValue = ?,
                    displayValue = ?,
                    unit = COALESCE(?, unit),
                    metricPeriod = COALESCE(?, metricPeriod),
                    fiscalPeriod = COALESCE(?, fiscalPeriod),
                    extractionRule = COALESCE(?, extractionRule),
                    qwenEligible = NULL,
                    qwenIneligibleReason = NULL,
                    hiddenByDefault = 0,
                    updatedAt = ?
                WHERE id = ?
                """,
                (
                    source_url,
                    source_document_title,
                    evidence,
                    evidence,
                    _evidence_hash(evidence),
                    normalized.normalizedValue,
                    normalized.displayValue,
                    normalized.unit,
                    metric_period or periods.metricPeriod,
                    fiscal_period or periods.fiscalPeriod,
                    extraction_rule,
                    _now(),
                    int(item_id),
                ),
            )
        return old, self.get_item(int(item_id))

    def cleanup_stale_review_items(self, symbols: Iterable[str] | None = None) -> dict:
        symbol_set = {str(symbol).upper() for symbol in symbols or [] if str(symbol).strip()}
        rows = self.list_items()
        if symbol_set:
            rows = [row for row in rows if str(row.get("symbol") or "").upper() in symbol_set]
        counts = {
            "needsEvidence": 0,
            "invalidReviewItem": 0,
            "systemReasonMoved": 0,
            "duplicateArchived": 0,
        }
        now = _now()
        with self.connect() as conn:
            for row in rows:
                if str(row.get("reviewStatus") or "") in {"approved", "rejected", "manually_corrected", "auto_archived", "duplicate_archived"}:
                    continue
                item_type = str(row.get("itemType") or "")
                if item_type != "extracted_value":
                    continue
                value = row.get("value")
                normalized = normalize_metric_value(value, row.get("unit"), str(row.get("evidenceText") or ""), str(row.get("metricKey") or ""))
                if value is None or normalized.normalizedValue is None:
                    conn.execute(
                        """
                        UPDATE review_queue_items
                        SET reviewStatus = 'invalid_review_item',
                            aiTriageStatus = 'ai_skipped',
                            hiddenByDefault = 1,
                            correctionNotes = COALESCE(correctionNotes, 'invalid_review_item'),
                            updatedAt = ?
                        WHERE id = ?
                        """,
                        (now, int(row["id"])),
                    )
                    counts["invalidReviewItem"] += 1
                    continue
                evidence_text = str(row.get("evidenceText") or row.get("extractedText") or "").strip()
                if _looks_like_system_reason(evidence_text):
                    conn.execute(
                        """
                        UPDATE review_queue_items
                        SET evidenceText = '',
                            extractedText = '',
                            evidenceHash = NULL,
                            systemReason = COALESCE(NULLIF(systemReason, ''), ?),
                            updatedAt = ?
                        WHERE id = ?
                        """,
                        (evidence_text, now, int(row["id"])),
                    )
                    row = {**row, "evidenceText": "", "extractedText": "", "systemReason": evidence_text, "evidenceHash": None}
                    evidence_text = ""
                    counts["systemReasonMoved"] += 1
                missing_evidence = _extracted_value_missing_evidence({**row, "evidenceText": evidence_text})
                if missing_evidence:
                    hidden = 0 if _has_high_value_affect(row) else 1
                    conn.execute(
                        """
                        UPDATE review_queue_items
                        SET itemType = 'evidence_missing_extracted_value',
                            reviewStatus = 'needs_evidence',
                            aiTriageStatus = 'needs_more_source',
                            hiddenByDefault = ?,
                            recommendedAction = '重新抓取IR/SEC或人工确认来源',
                            explanation = COALESCE(NULLIF(explanation, ''), '该值已生成，但缺少可验证原文，需重新抓取来源后再复核。'),
                            updatedAt = ?
                        WHERE id = ?
                        """,
                        (hidden, now, int(row["id"])),
                    )
                    counts["needsEvidence"] += 1
        counts["duplicateArchived"] = self.archive_duplicate_items(symbols)
        return counts

    def log_autopilot_run_start(self, run_id: str, filters: dict | None, scanned: int, processable: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO autopilot_runs (
                    runId, startedAt, filters, scannedCount, processableCount
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, _now(), json.dumps(filters or {}, ensure_ascii=False), int(scanned), int(processable)),
            )

    def log_autopilot_run_finish(self, run_id: str, summary: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE autopilot_runs
                SET finishedAt = ?,
                    scannedCount = ?,
                    processableCount = ?,
                    autoFillSuccessCount = ?,
                    qwenReviewedCount = ?,
                    autoApprovedCount = ?,
                    autoArchivedCount = ?,
                    needsHumanCount = ?,
                    unsupportedCount = ?,
                    failedCount = ?
                WHERE runId = ?
                """,
                (
                    _now(),
                    int(summary.get("scannedCount", 0)),
                    int(summary.get("processableCount", 0)),
                    int(summary.get("autoFillSuccessCount", 0)),
                    int(summary.get("qwenReviewedCount", 0)),
                    int(summary.get("autoApprovedCount", 0)),
                    int(summary.get("autoArchivedCount", 0)),
                    int(summary.get("needsHumanCount", 0)),
                    int(summary.get("unsupportedCount", 0)),
                    int(summary.get("failedCount", 0)),
                    run_id,
                ),
            )

    def log_autopilot_run_item(
        self,
        run_id: str,
        row: dict | None,
        action_taken: str,
        reason: str | None = None,
        old_status: str | None = None,
        new_status: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO autopilot_run_items (
                    runId, reviewItemId, symbol, metricKey, oldStatus, newStatus,
                    actionTaken, reason, errorType, errorMessage, timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(row["id"]) if row and row.get("id") is not None else None,
                    row.get("symbol") if row else None,
                    row.get("metricKey") if row else None,
                    old_status,
                    new_status,
                    action_taken,
                    reason,
                    error_type,
                    error_message,
                    _now(),
                ),
            )

    def list_autopilot_run_items(self, run_id: str | None = None) -> list[dict]:
        clauses = []
        params: list[object] = []
        if run_id:
            clauses.append("runId = ?")
            params.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM autopilot_run_items {where} ORDER BY id DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_automation_logs(self, run_id: str | None = None) -> list[dict]:
        clauses = []
        params: list[object] = []
        if run_id:
            clauses.append("runId = ?")
            params.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM review_automation_logs {where} ORDER BY id DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def undo_automation_run(self, run_id: str) -> int:
        logs = self.list_automation_logs(run_id)
        count = 0
        for log in reversed(logs):
            item_id = log.get("reviewItemId")
            old_status = log.get("oldStatus")
            new_status = log.get("newStatus")
            if not item_id or new_status not in {"auto_archived", "approved"} or not old_status:
                continue
            row = self.get_item(int(item_id))
            if not row or row.get("reviewStatus") != new_status:
                continue
            old, new = self.undo_review_status(int(item_id), str(old_status), "undo_automation_run", actor="system")
            if old and new:
                count += 1
        return count

    def apply_ai_auto_approval(
        self,
        item_id: int,
        ai_review_result_id: int | None = None,
        evidence_quote: str | None = None,
        explanation_zh: str | None = None,
    ) -> tuple[dict | None, dict | None]:
        old = self.get_item(item_id)
        if not old:
            return None, None
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE review_queue_items
                SET reviewStatus = 'approved',
                    reviewedAt = ?,
                    correctionNotes = COALESCE(correctionNotes, 'auto_approved_by_ai'),
                    aiTriageStatus = 'auto_approved_by_ai',
                    aiReviewResultId = COALESCE(?, aiReviewResultId),
                    approvedBy = 'ai',
                    approvedAt = ?,
                    evidenceQuote = COALESCE(?, evidenceQuote),
                    aiExplanationZh = COALESCE(?, aiExplanationZh),
                    updatedAt = ?
                WHERE id = ?
                """,
                (now, ai_review_result_id, now, evidence_quote, explanation_zh, now, int(item_id)),
            )
        if old["sourceKind"] == "disclosure_metric_values" and old["sourceMetricId"]:
            self.disclosure_store.update_review_status(
                int(old["sourceMetricId"]),
                "approved",
                reviewed_by="ai",
                correction_notes="auto_approved_by_ai",
            )
        new = self.get_item(item_id)
        self._after_status_change(old, new, "auto_approve", "ai", explanation_zh)
        return old, new

    def accept_ai_correction(self, item_id: int, ai_result: dict | None = None, ai_review_result_id: int | None = None) -> tuple[dict | None, dict | None]:
        row = self.get_item(item_id)
        if not row:
            return None, None
        candidate = _correction_candidate_from_row(row)
        if ai_result:
            candidate.update(
                {
                    "correctedValue": ai_result.get("correctedValue"),
                    "correctedUnit": ai_result.get("correctedUnit"),
                    "correctedPeriod": ai_result.get("correctedPeriod"),
                    "correctionReason": ai_result.get("explanationZh"),
                    "evidenceQuote": ai_result.get("evidenceQuote"),
                }
            )
        value = candidate.get("correctedValue")
        if value is None:
            return row, row
        unit = candidate.get("correctedUnit")
        period = candidate.get("correctedPeriod")
        notes = candidate.get("correctionReason") or "AI assisted manual correction"
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE review_queue_items
                SET value = ?,
                    unit = ?,
                    period = ?,
                    sourceType = 'AI_ASSISTED_MANUAL_CORRECTION',
                    confidence = 'high',
                    reviewStatus = 'manually_corrected',
                    reviewedAt = ?,
                    correctionNotes = ?,
                    aiTriageStatus = 'ai_recommend_correct',
                    aiReviewResultId = COALESCE(?, aiReviewResultId),
                    updatedAt = ?
                WHERE id = ?
                """,
                (float(value), unit, period, _now(), notes, ai_review_result_id, _now(), int(item_id)),
            )
        if row["sourceKind"] == "disclosure_metric_values" and row["sourceMetricId"]:
            self.disclosure_store.correct_metric(
                int(row["sourceMetricId"]),
                float(value),
                unit,
                period,
                notes,
                source_type="AI_ASSISTED_MANUAL_CORRECTION",
            )
        else:
            self.disclosure_store.save_metric(
                str(row["symbol"]),
                str(row["metricKey"]),
                float(value),
                unit,
                period,
                "AI_ASSISTED_MANUAL_CORRECTION",
                None,
                "Manual Review Center",
                notes,
                "high",
                review_status="manually_corrected",
                reviewed_by="local_user",
                correction_notes=notes,
                display_name=row["displayName"],
            )
        new = self.get_item(item_id)
        self._after_status_change(row, new, "manual_correct", "user", notes)
        return row, new

    def accept_ai_reject(self, item_id: int, ai_review_result_id: int | None = None, reason: str | None = None) -> tuple[dict | None, dict | None]:
        old = self.get_item(item_id)
        if not old:
            return None, None
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE review_queue_items
                SET reviewStatus = 'rejected',
                    reviewedAt = ?,
                    rejectedBy = 'user_after_ai_recommendation',
                    correctionNotes = COALESCE(?, correctionNotes),
                    aiTriageStatus = 'ai_recommend_reject',
                    aiReviewResultId = COALESCE(?, aiReviewResultId),
                    updatedAt = ?
                WHERE id = ?
                """,
                (_now(), reason, ai_review_result_id, _now(), int(item_id)),
            )
        if old["sourceKind"] == "disclosure_metric_values" and old["sourceMetricId"]:
            self.disclosure_store.update_review_status(
                int(old["sourceMetricId"]),
                "rejected",
                reviewed_by="user_after_ai_recommendation",
                correction_notes=reason,
            )
        new = self.get_item(item_id)
        self._after_status_change(old, new, "reject", "user", reason)
        return old, new

    def batch_accept_ai_auto_approved(self, item_ids: Iterable[int] | None = None) -> list[tuple[dict | None, dict | None]]:
        ids = [int(item_id) for item_id in item_ids] if item_ids is not None else None
        rows = self.list_items()
        changes = []
        for row in rows:
            if ids is not None and int(row["id"]) not in ids:
                continue
            if row.get("aiTriageStatus") != "auto_approved_by_ai":
                continue
            if row.get("reviewStatus") != "approved":
                changes.append(self.apply_ai_auto_approval(int(row["id"]), row.get("aiReviewResultId")))
        return changes

    def hide_auto_approved_items(self, item_ids: Iterable[int] | None = None) -> int:
        ids = [int(item_id) for item_id in item_ids] if item_ids is not None else None
        rows = [row for row in self.list_items() if row.get("aiTriageStatus") == "auto_approved_by_ai"]
        if ids is not None:
            rows = [row for row in rows if int(row["id"]) in ids]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                "UPDATE review_queue_items SET hiddenByDefault = 1, updatedAt = ? WHERE id = ?",
                [(_now(), int(row["id"])) for row in rows],
            )
        return len(rows)

    def mark_ai_needs_human(self, item_ids: Iterable[int]) -> int:
        ids = [int(item_id) for item_id in item_ids]
        if not ids:
            return 0
        with self.connect() as conn:
            conn.executemany(
                "UPDATE review_queue_items SET aiTriageStatus = 'ai_needs_human_review', updatedAt = ? WHERE id = ?",
                [(_now(), item_id) for item_id in ids],
            )
        return len(ids)

    def batch_archive_low_priority(self, item_ids: Iterable[int]) -> int:
        count = 0
        for item_id in item_ids:
            row = self.get_item(int(item_id))
            if not row:
                continue
            if row.get("aiTriageStatus") == "ai_auto_archived" or row.get("reviewStatus") == "auto_archived":
                continue
            affects = str(row.get("affects") or "")
            if any(part in affects for part in ("Action", "maxPosition")):
                continue
            if str(row.get("itemType") or "") in {"analyst_estimate_needed", "derived_low_confidence", "manual_override_needed", "qualitative_risk"}:
                self.auto_archive_item(int(item_id), "批量归档低优先级项")
                count += 1
        return count

    def log_operation(
        self,
        action_name: str,
        selected_filters: dict | None,
        eligible_count: int,
        processed_count: int,
        skipped_count: int,
        failed_count: int,
        error_messages: list[str] | None = None,
        started_at: str | None = None,
    ) -> None:
        started = started_at or _now()
        finished = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ai_automation_operation_logs (
                    actionName, selectedFilters, eligibleItemCount, processedCount,
                    skippedCount, failedCount, errorMessages, startedAt, finishedAt
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_name,
                    json.dumps(selected_filters or {}, ensure_ascii=False),
                    int(eligible_count),
                    int(processed_count),
                    int(skipped_count),
                    int(failed_count),
                    json.dumps(error_messages or [], ensure_ascii=False),
                    started,
                    finished,
                ),
            )

    def latest_operation_log(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM ai_automation_operation_logs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["errorMessages"] = json.loads(result.get("errorMessages") or "[]")
        except json.JSONDecodeError:
            result["errorMessages"] = []
        try:
            result["selectedFilters"] = json.loads(result.get("selectedFilters") or "{}")
        except json.JSONDecodeError:
            result["selectedFilters"] = {}
        return result

    def correct_item(
        self,
        item_id: int,
        value: float,
        unit: str | None,
        period: str | None,
        notes: str | None = None,
    ) -> None:
        old: dict | None = None
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM review_queue_items WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return
            old = dict(row)
            conn.execute(
                """
                UPDATE review_queue_items
                SET value = ?,
                    unit = ?,
                    period = ?,
                    sourceType = 'MANUAL_CORRECTION',
                    confidence = 'high',
                    reviewStatus = 'manually_corrected',
                    reviewedAt = ?,
                    correctionNotes = ?,
                    updatedAt = ?
                WHERE id = ?
                """,
                (value, unit, period, _now(), notes, _now(), item_id),
            )
        if row["sourceKind"] == "disclosure_metric_values" and row["sourceMetricId"]:
            self.disclosure_store.correct_metric(int(row["sourceMetricId"]), value, unit, period, notes)
        else:
            self.disclosure_store.save_metric(
                str(row["symbol"]),
                str(row["metricKey"]),
                value,
                unit,
                period,
                "MANUAL_CORRECTION",
                None,
                "Manual Review Center",
                notes,
                "high",
                review_status="manually_corrected",
                correction_notes=notes,
                display_name=row["displayName"],
            )
        new = self.get_item(int(item_id))
        self._after_status_change(old, new, "manual_correct", "user", notes)

    def _find_existing(self, item: dict) -> dict | None:
        with self.connect() as conn:
            if item.get("sourceMetricId") is not None:
                row = conn.execute(
                    """
                    SELECT * FROM review_queue_items
                    WHERE sourceKind = ? AND sourceMetricId = ?
                    """,
                    (item["sourceKind"], item["sourceMetricId"]),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM review_queue_items
                    WHERE symbol = ? AND metricKey = ? AND itemType = ? AND sourceMetricId IS NULL
                    """,
                    (item["symbol"], item["metricKey"], item["itemType"]),
                ).fetchone()
        return dict(row) if row else None


class ReviewQueueBuilder:
    def __init__(
        self,
        queue_store: ReviewQueueStore | None = None,
        disclosure_store: DisclosureStore | None = None,
        fundamental_cache: FundamentalCache | None = None,
        price_cache: PriceCache | None = None,
    ) -> None:
        self.queue_store = queue_store or ReviewQueueStore()
        self.disclosure_store = disclosure_store or self.queue_store.disclosure_store
        self.fundamental_cache = fundamental_cache or FundamentalCache(self.queue_store.path)
        self.price_cache = price_cache or PriceCache(self.queue_store.path)

    def build_review_queue_for_symbol(self, symbol: str) -> QueueBuildResult:
        symbol = symbol.upper()
        items = [*self._extracted_value_items(symbol), *self._metric_resolution_items(symbol)]
        return self._persist_items([symbol], items)

    def build_review_queue_for_watchlist(self, symbols: Iterable[str]) -> QueueBuildResult:
        normalized = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
        all_items: list[dict] = []
        for symbol in normalized:
            all_items.extend(self._extracted_value_items(symbol))
            all_items.extend(self._metric_resolution_items(symbol))
        return self._persist_items(normalized, all_items)

    def sync_review_queue(self) -> QueueBuildResult:
        return self.build_review_queue_for_watchlist(load_watchlist())

    def _persist_items(self, symbols: list[str], items: list[dict]) -> QueueBuildResult:
        counters = {"created": 0, "updated": 0, "skipped": 0}
        item_type_counts: dict[str, int] = {}
        for item in items:
            outcome = self.queue_store.upsert_item(item)
            counters[outcome] += 1
            item_type = str(item.get("itemType") or "unknown")
            item_type_counts[item_type] = item_type_counts.get(item_type, 0) + 1
        self.queue_store.archive_duplicate_items(symbols)
        self.queue_store.cleanup_stale_review_items(symbols)
        return QueueBuildResult(
            symbols=symbols,
            created=counters["created"],
            updated=counters["updated"],
            skipped=counters["skipped"],
            total=len(items),
            item_type_counts=item_type_counts,
        )

    def _extracted_value_items(self, symbol: str) -> list[dict]:
        items = []
        for row in self.disclosure_store.get_metrics(symbol):
            if str(row.get("sourceType") or "") not in EXTRACTED_VALUE_SOURCES:
                continue
            evidence_text = row.get("extractedText") if row.get("sourceUrl") else ""
            valid, reason = validate_extracted_metric_candidate(str(row.get("metricKey") or ""), str(evidence_text or ""))
            freshness_status = row.get("freshnessStatus") or "active_current"
            recommended = "approve, reject, or correct extracted value" if valid else "extraction rejected by rule"
            if freshness_status == "historical_value":
                recommended = "keep as historical value; set current only with confirmation"
            items.append(
                {
                    "symbol": symbol,
                    "metricKey": row.get("metricKey"),
                    "displayName": row.get("displayName") or row.get("metricKey"),
                    "itemType": "extracted_value",
                    "value": row.get("value"),
                    "unit": row.get("unit"),
                    "period": row.get("period"),
                    "sourceType": row.get("sourceType"),
                    "sourceUrl": row.get("sourceUrl"),
                    "sourceDocumentTitle": row.get("sourceDocumentTitle"),
                    "extractedText": evidence_text,
                    "evidenceText": evidence_text,
                    "systemReason": "" if valid else reason,
                    "confidence": row.get("confidence") or "low",
                    "affects": _affects_for_metric(row.get("metricKey")),
                    "reviewStatus": (row.get("reviewStatus") or "pending_review") if valid else "auto_archived",
                    "aiTriageStatus": None if valid else "extraction_rejected_by_rule",
                    "recommendedAction": recommended,
                    "resolutionStatus": "available",
                    "sourceKind": "disclosure_metric_values",
                    "sourceMetricId": row.get("id"),
                    "modelType": _model_type_for_symbol(symbol),
                    "metricVariant": row.get("metricVariant") or metric_variant_for_key(row.get("metricKey")),
                    "targetBasis": row.get("targetBasis") or target_basis_for_metric(row.get("metricVariant") or row.get("metricKey")),
                    "freshnessStatus": freshness_status,
                    "recommendedAction": recommended,
                    "explanation": "Extracted from SEC / IR / transcript; confirmation is required before scoring." if valid else reason,
                }
            )
        return items

    def _metric_resolution_items(self, symbol: str) -> list[dict]:
        snapshot = self._snapshot_from_cache(symbol)
        score = self._score_from_snapshot(symbol, snapshot)
        items = []
        for row in score.metricResolutionStatus:
            item_type = _item_type_for_resolution(row)
            if not item_type:
                continue
            review_status = _review_status_for_resolution(row)
            explanation = row.get("explanation") or ""
            recommended = row.get("recommendedAction") or _recommended_action_for_item_type(item_type)
            if str(row.get("metricKey") or "") == "debtMaturityPressure" and _debt_maturity_low_materiality(snapshot):
                review_status = "auto_archived"
                explanation = "公司净现金或杠杆较低，债务到期压力暂不构成核心复核项。"
                recommended = "低优先级，已自动归档"
            if str(row.get("metricKey") or "") == "sbcToRevenue":
                recommended = "优先使用 FMP cash flow + revenue 自动计算；若 FMP 缺失再试 SEC XBRL。"
            items.append(
                {
                    "symbol": symbol,
                    "metricKey": row.get("metricKey"),
                    "displayName": row.get("displayName") or row.get("metricKey"),
                    "itemType": item_type,
                    "value": row.get("value"),
                    "unit": row.get("unit"),
                    "period": None,
                    "sourceType": row.get("sourceType") or "metric_resolution",
                    "sourceUrl": None,
                    "sourceDocumentTitle": None,
                    "extractedText": "",
                    "evidenceText": "",
                    "systemReason": explanation,
                    "confidence": row.get("confidence") or "low",
                    "affects": ",".join(sorted(_affects(row))),
                    "reviewStatus": review_status,
                    "aiTriageStatus": "ai_auto_archived" if review_status == "auto_archived" else None,
                    "recommendedAction": recommended,
                    "resolutionStatus": row.get("resolutionStatus"),
                    "sourceKind": "metric_resolution",
                    "sourceMetricId": None,
                    "modelType": score.modelType,
                    "explanation": explanation,
                }
            )
        return items

    def _score_from_cache(self, symbol: str):
        return self._score_from_snapshot(symbol, self._snapshot_from_cache(symbol))

    def _snapshot_from_cache(self, symbol: str) -> dict:
        snapshot = self.fundamental_cache.get_snapshot(symbol, max_age_hours=24 * 3650) or {"ticker": symbol, "symbol": symbol}
        overrides = self.fundamental_cache.get_manual_overrides(symbol)
        if overrides:
            snapshot = {**snapshot, **overrides}
        return {**snapshot, **self.disclosure_store.metric_supplement(symbol)}

    def _score_from_snapshot(self, symbol: str, snapshot: dict):
        history = self.price_cache.get_history(f"FMP:{symbol}", max_age_hours=24 * 3650, min_rows=20)
        technicals = {}
        if history is not None and not history.empty:
            technicals = latest_technical_snapshot(add_technical_indicators(history))
        return calculate_total_score(snapshot, technicals)


def build_review_queue_for_symbol(symbol: str) -> QueueBuildResult:
    return ReviewQueueBuilder().build_review_queue_for_symbol(symbol)


def build_review_queue_for_watchlist(symbols: Iterable[str]) -> QueueBuildResult:
    return ReviewQueueBuilder().build_review_queue_for_watchlist(symbols)


def sync_review_queue() -> QueueBuildResult:
    return ReviewQueueBuilder().sync_review_queue()


def _item_type_for_resolution(row: dict) -> str | None:
    if "defaultReviewQueue" in row and not row.get("defaultReviewQueue"):
        return None
    route = str(row.get("missingResolutionRoute") or "")
    status = str(row.get("resolutionStatus") or "")
    confidence = str(row.get("confidence") or "")
    affects = _affects(row)
    if status in {"available", "calculated", "not_applicable", "missing_inputs"}:
        return None
    if not affects & SCORING_AFFECTS:
        return None
    if route == "human_review_required" and status in {"requires_ir_scrape", "requires_sec_filing"}:
        return "missing_kpi"
    if route == "human_review_required":
        return "manual_override_needed"
    if status in {"requires_ir_scrape", "requires_sec_filing"}:
        return "missing_kpi"
    if status == "requires_analyst_estimates":
        return "analyst_estimate_needed"
    if status in {"manual_override_required", "company_not_disclosed"}:
        return "manual_override_needed"
    if status == "semi_auto_low_confidence":
        return "qualitative_risk"
    if status == "derived_score" and confidence in {"low", "medium"}:
        return "derived_low_confidence"
    return None


def _review_status_for_resolution(row: dict) -> str:
    route = str(row.get("missingResolutionRoute") or "")
    if route == "low_priority_archive":
        return "auto_archived"
    status = str(row.get("resolutionStatus") or "")
    if status in {"requires_ir_scrape", "requires_sec_filing", "requires_analyst_estimates", "manual_override_required", "company_not_disclosed"}:
        return "needs_data"
    return "pending_review"


def _affects(row: dict) -> set[str]:
    value = row.get("affects")
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if item}
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    return set()


def _recommended_action_for_item_type(item_type: str) -> str:
    return {
        "extracted_value": "确认、驳回或手动修正",
        "missing_kpi": "抓取 IR / SEC 文件或补充关键 KPI",
        "derived_low_confidence": "复核规则推导依据",
        "qualitative_risk": "人工确认风险叙事",
        "analyst_estimate_needed": "补齐分析师预期",
        "manual_override_needed": "建议人工补充",
    }.get(item_type, "复核")


def _affects_for_metric(metric_key: object) -> str:
    definition = metric_source_definition(str(metric_key or ""))
    if not definition:
        return "Quality"
    impact = str(definition.missingImpact or "")
    if impact == "CRITICAL_RISK":
        return "Risk"
    if impact == "VALUATION_ONLY":
        return "Entry"
    if impact == "TECHNICAL_ONLY":
        return "Technical"
    if impact == "CRITICAL_QUALITY":
        return "Quality"
    return "Quality"


def _model_type_for_symbol(symbol: str) -> str:
    from scoring.sector_models import classifyStockModel

    return classifyStockModel({"symbol": symbol})


def _extracted_value_missing_evidence(item: dict) -> bool:
    if str(item.get("itemType") or "") not in {"extracted_value", "evidence_missing_extracted_value"}:
        return False
    required_text = str(item.get("evidenceText") or item.get("extractedText") or "").strip()
    if not required_text:
        return True
    if not str(item.get("sourceUrl") or "").strip():
        return True
    if not str(item.get("sourceDocumentTitle") or "").strip():
        return True
    if not str(item.get("sourceType") or "").strip():
        return True
    if not str(item.get("unit") or "").strip():
        return True
    if not str(item.get("evidenceHash") or _evidence_hash(required_text) or "").strip():
        return True
    if not (str(item.get("metricPeriod") or "").strip() or str(item.get("fiscalPeriod") or "").strip() or str(item.get("period") or "").strip()):
        return True
    return False


def _looks_like_system_reason(text: str) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned:
        return False
    markers = (
        "需要查看",
        "需查看",
        "需人工",
        "建议人工",
        "公司未披露",
        "缺少计算输入",
        "该字段",
        "manual",
        "requires_",
        "not disclosed",
    )
    return any(marker.lower() in cleaned.lower() for marker in markers)


def _has_high_value_affect(row: dict) -> bool:
    affects = _affects(row)
    return bool(affects & {"Quality", "Entry", "Risk", "Action", "Position", "maxPosition"})


def _normalize_queue_item(item: dict) -> dict:
    status = _normalize_queue_status(str(item.get("reviewStatus") or "pending_review"))
    item_type = str(item.get("itemType") or "")
    evidence_text = item.get("evidenceText")
    if evidence_text is None:
        evidence_text = item.get("extractedText") if item.get("sourceUrl") else ""
    evidence_text = str(evidence_text or "")
    system_reason = str(item.get("systemReason") or item.get("explanation") or "")
    if _looks_like_system_reason(evidence_text):
        system_reason = system_reason or evidence_text
        evidence_text = ""
    normalized_value = normalize_metric_value(item.get("value"), item.get("unit"), evidence_text, str(item.get("metricKey") or ""))
    periods = normalize_metric_period({**item, "extractedText": evidence_text})
    metric_variant = item.get("metricVariant") or metric_variant_for_key(str(item.get("metricKey") or ""))
    target_basis = item.get("targetBasis") or target_basis_for_metric(metric_variant or item.get("metricKey"))
    freshness_status = item.get("freshnessStatus") or "active_current"
    source_url = item.get("sourceUrl")
    source_title = item.get("sourceDocumentTitle")
    ai_triage_status = item.get("aiTriageStatus")
    recommended_action = item.get("recommendedAction") or ""
    explanation = item.get("explanation") or ""
    if item_type == "extracted_value":
        if item.get("value") is None or normalized_value.normalizedValue is None:
            status = "invalid_review_item"
            ai_triage_status = "ai_skipped"
            recommended_action = recommended_action or "抽取值为空，已移入数据问题"
        elif _extracted_value_missing_evidence(
            {
                **item,
                "evidenceText": evidence_text,
                "sourceUrl": source_url,
                "sourceDocumentTitle": source_title,
                "evidenceHash": _evidence_hash(evidence_text),
                "unit": normalized_value.unit,
                "metricPeriod": item.get("metricPeriod") or periods.metricPeriod,
                "fiscalPeriod": item.get("fiscalPeriod") or periods.fiscalPeriod,
            }
        ):
            item_type = "evidence_missing_extracted_value"
            status = "needs_evidence"
            ai_triage_status = "needs_more_source"
            recommended_action = recommended_action or "重新抓取IR/SEC或人工确认来源"
            explanation = explanation or "该值已生成，但缺少可验证原文，需重新抓取来源后再复核。"
    return {
        "symbol": str(item.get("symbol") or "").upper(),
        "metricKey": str(item.get("metricKey") or ""),
        "displayName": item.get("displayName") or item.get("metricKey"),
        "itemType": item_type,
        "value": item.get("value"),
        "unit": normalized_value.unit or item.get("unit"),
        "period": item.get("period"),
        "sourceType": item.get("sourceType"),
        "sourceUrl": source_url,
        "sourceDocumentTitle": source_title,
        "extractedText": evidence_text,
        "evidenceText": evidence_text,
        "systemReason": system_reason,
        "evidenceHash": _evidence_hash(evidence_text),
        "confidence": str(item.get("confidence") or "low"),
        "affects": item.get("affects") or "",
        "reviewStatus": status,
        "recommendedAction": recommended_action,
        "resolutionStatus": item.get("resolutionStatus"),
        "sourceKind": item.get("sourceKind") or "metric_resolution",
        "sourceMetricId": item.get("sourceMetricId"),
        "modelType": item.get("modelType"),
        "explanation": explanation,
        "normalizedValue": normalized_value.normalizedValue,
        "displayValue": normalized_value.displayValue,
        "metricPeriod": item.get("metricPeriod") or periods.metricPeriod,
        "fiscalPeriod": item.get("fiscalPeriod") or periods.fiscalPeriod,
        "metricVariant": metric_variant,
        "targetBasis": target_basis,
        "freshnessStatus": freshness_status,
        "extractionRule": item.get("extractionRule"),
        "aiTriageStatus": ai_triage_status,
    }


def _normalize_queue_status(status: str) -> str:
    normalized = str(status or "pending_review").strip().lower()
    if normalized not in QUEUE_REVIEW_STATUSES:
        raise ValueError(f"Unsupported review queue status: {status}")
    return normalized


def _audit_action_for_status_change(old_status: str, new_status: str, notes: str | None = None) -> str:
    note = str(notes or "").lower()
    if note.startswith("undo_") or note in {"undo_approval", "undo_auto_archive"}:
        if old_status == "approved":
            return "undo_approve"
        if old_status == "manually_corrected":
            return "undo_manual_correct"
        if old_status == "rejected":
            return "undo_reject"
        if old_status == "auto_archived":
            return "undo_archive"
    if new_status == "auto_approved_by_ai":
        return "auto_approve"
    if new_status == "approved":
        return "approve"
    if new_status == "rejected":
        return "reject"
    if new_status == "manually_corrected":
        return "manual_correct"
    if new_status == "auto_archived":
        return "archive"
    return f"{old_status or 'unknown'}_to_{new_status or 'unknown'}"


def _undo_action_for_row(row: dict) -> str:
    review_status = str(row.get("reviewStatus") or "")
    ai_triage_status = str(row.get("aiTriageStatus") or "")
    if review_status == "approved" and ai_triage_status == "auto_approved_by_ai":
        return "undo_auto_approve"
    return {
        "approved": "undo_approve",
        "auto_approved_by_ai": "undo_auto_approve",
        "manually_corrected": "undo_manual_correct",
        "auto_archived": "undo_archive",
        "rejected": "undo_reject",
    }.get(review_status, "undo_approve")


def _normalize_ai_triage_status(status: str) -> str:
    normalized = str(status or "ai_skipped").strip()
    if normalized not in AI_TRIAGE_STATUSES:
        raise ValueError(f"Unsupported aiTriageStatus: {status}")
    return normalized


def _evidence_hash(text: str) -> str | None:
    cleaned = str(text or "").strip()
    if not cleaned:
        return None
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def _debt_maturity_low_materiality(snapshot: dict) -> bool:
    net_debt = _number(snapshot.get("net_debt") or snapshot.get("netDebt"))
    net_debt_to_ebitda = _number(snapshot.get("net_debt_to_ebitda") or snapshot.get("netDebtToEbitda"))
    interest_coverage = _number(snapshot.get("interest_coverage") or snapshot.get("interestCoverage"))
    total_debt = _number(snapshot.get("total_debt") or snapshot.get("totalDebt"))
    market_cap = _number(snapshot.get("market_cap") or snapshot.get("marketCap"))
    if net_debt is not None and net_debt <= 0:
        return True
    if net_debt_to_ebitda is not None and net_debt_to_ebitda < 1:
        return True
    if interest_coverage is not None and interest_coverage >= 8:
        return True
    if total_debt is not None and market_cap and market_cap > 0 and total_debt / market_cap < 0.1:
        return True
    return False


def _number(value: object) -> float | None:
    try:
        if value in {None, ""}:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _correction_candidate_from_row(row: dict) -> dict:
    try:
        parsed = json.loads(row.get("correctionCandidate") or "{}")
    except json.JSONDecodeError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
