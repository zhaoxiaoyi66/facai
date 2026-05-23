from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Iterable

from ai.qwen_review_service import qwen_review_eligibility
from ai.review_automation import ReviewAutomationService
from data.ai_review_assistant import AIReviewStore
from data.disclosure_pipeline import DisclosurePipeline
from data.evidence_backfill import backfill_evidence_for_current_filters
from data.fundamentals import FundamentalCache
from data.metric_source_map import metric_source_definition
from data.prices import PriceCache
from data.review_queue_builder import EXTRACTED_VALUE_SOURCES, ReviewQueueBuilder, ReviewQueueStore
from indicators.technicals import add_technical_indicators, latest_technical_snapshot
from settings import load_watchlist


AUTOPILOT_MODE = "autopilot_with_guardrails"
TECHNICAL_METRICS = {"EMA20", "EMA50", "EMA200", "RSI14", "drawdown", "return20d", "return60d", "volumeTrend"}


@dataclass(frozen=True)
class AutoFillCapability:
    canAutoFill: bool
    autoFillType: str
    reason: str


@dataclass(frozen=True)
class ReviewAutopilotResult:
    runId: str
    scannedCount: int = 0
    processableCount: int = 0
    autoFillAttemptedCount: int = 0
    autoFillSuccessCount: int = 0
    autoFillFailedCount: int = 0
    evidenceBackfilledCount: int = 0
    unsupportedCount: int = 0
    qwenEligibleCount: int = 0
    qwenReviewedCount: int = 0
    autoApprovedCount: int = 0
    autoArchivedCount: int = 0
    needsHumanCount: int = 0
    skippedCount: int = 0
    failedCount: int = 0
    errors: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def automationRate(self) -> float:
        if self.processableCount <= 0:
            return 0.0
        return (self.autoFillSuccessCount + self.autoApprovedCount + self.autoArchivedCount) / self.processableCount

    @property
    def scanned(self) -> int:
        return self.scannedCount

    @property
    def autoFillAttempted(self) -> int:
        return self.autoFillAttemptedCount

    @property
    def autoFillSucceeded(self) -> int:
        return self.autoFillSuccessCount

    @property
    def autoFillFailed(self) -> int:
        return self.autoFillFailedCount

    @property
    def qwenReviewed(self) -> int:
        return self.qwenReviewedCount

    @property
    def autoApproved(self) -> int:
        return self.autoApprovedCount

    @property
    def autoArchived(self) -> int:
        return self.autoArchivedCount

    @property
    def humanRemaining(self) -> int:
        return self.needsHumanCount

    @property
    def failed(self) -> int:
        return self.failedCount


@dataclass(frozen=True)
class AutoFillOutcome:
    status: str
    reason: str
    result: dict | None = None
    errorType: str | None = None


class ReviewAutopilot:
    def __init__(
        self,
        queue_store: ReviewQueueStore | None = None,
        disclosure_pipeline: DisclosurePipeline | None = None,
        builder: ReviewQueueBuilder | None = None,
        automation_service: ReviewAutomationService | None = None,
        fundamental_cache: FundamentalCache | None = None,
        price_cache: PriceCache | None = None,
    ) -> None:
        self.queue_store = queue_store or ReviewQueueStore()
        self.ai_store = AIReviewStore(self.queue_store.path)
        self.disclosure_pipeline = disclosure_pipeline or DisclosurePipeline(store=self.queue_store.disclosure_store)
        self.fundamental_cache = fundamental_cache or FundamentalCache(self.queue_store.path)
        self.price_cache = price_cache or PriceCache(self.queue_store.path)
        self.builder = builder or ReviewQueueBuilder(
            queue_store=self.queue_store,
            disclosure_store=self.queue_store.disclosure_store,
            fundamental_cache=self.fundamental_cache,
            price_cache=self.price_cache,
        )
        self.automation_service = automation_service or ReviewAutomationService(
            queue_store=self.queue_store,
            ai_store=self.ai_store,
        )

    def run_review_autopilot(self, filters: dict | None = None) -> ReviewAutopilotResult:
        filters = filters or {}
        run_id = str(uuid.uuid4())
        errors: list[str] = []
        unsupported: list[str] = []
        symbols = _symbols_from_filters(filters)

        sync_result = self._sync_review_queue(symbols)
        self.queue_store.cleanup_stale_review_items(symbols)
        backfill_result = backfill_evidence_for_current_filters(filters, self.queue_store)
        rows = _filter_rows(self.queue_store.list_items(), filters)
        scanned = len(rows)
        processable_rows = [row for row in rows if _is_processable(row)]
        processable_count = len(processable_rows)
        self.queue_store.log_autopilot_run_start(run_id, filters, scanned, processable_count)
        missing_items = identify_missing_data_items(processable_rows)

        auto_fill_attempted = 0
        auto_fill_success = 0
        auto_fill_failed = 0
        unsupported_count = 0
        auto_fill_symbols: set[str] = set()
        for row in missing_items:
            capability = auto_fill_capability(row)
            self.queue_store.update_auto_fill_status(
                int(row["id"]),
                capability.canAutoFill,
                capability.autoFillType,
                "not_started" if capability.canAutoFill else "not_available",
                None if capability.canAutoFill else capability.reason,
            )
            if not capability.canAutoFill:
                self.queue_store.log_automation_action(
                    run_id,
                    "auto_fill_not_available",
                    row,
                    str(row.get("reviewStatus") or ""),
                    str(row.get("reviewStatus") or ""),
                    capability.reason,
                )
                continue
            auto_fill_attempted += 1
            self.queue_store.update_auto_fill_status(int(row["id"]), True, capability.autoFillType, "running")
            outcome = self._run_auto_fill(row, capability)
            if outcome.status == "success":
                self.queue_store.update_auto_fill_status(int(row["id"]), True, capability.autoFillType, "success")
                self.queue_store.log_automation_action(
                    run_id,
                    "auto_fill_success",
                    row,
                    str(row.get("reviewStatus") or ""),
                    str(row.get("reviewStatus") or ""),
                    capability.reason,
                )
                self.queue_store.log_autopilot_run_item(
                    run_id,
                    row,
                    "auto_fill_success",
                    outcome.reason,
                    str(row.get("reviewStatus") or ""),
                    str(row.get("reviewStatus") or ""),
                )
                auto_fill_success += 1
                auto_fill_symbols.add(str(row.get("symbol") or "").upper())
                self._convert_successful_autofill_to_review_items(str(row.get("symbol") or ""), outcome.result or {})
            elif outcome.status in {"unsupported_by_current_pipeline", "not_auto_fillable_yet", "company_not_disclosed", "needs_manual_source"}:
                message = _friendly_unsupported_reason(outcome.reason)
                unsupported.append(f"{row.get('symbol')} {row.get('metricKey')}: {message}")
                unsupported_count += 1
                self.queue_store.update_auto_fill_status(int(row["id"]), True, capability.autoFillType, "not_available", message)
                self.queue_store.log_automation_action(
                    run_id,
                    outcome.status,
                    row,
                    str(row.get("reviewStatus") or ""),
                    str(row.get("reviewStatus") or ""),
                    message,
                )
                self.queue_store.log_autopilot_run_item(
                    run_id,
                    row,
                    outcome.status,
                    message,
                    str(row.get("reviewStatus") or ""),
                    str(row.get("reviewStatus") or ""),
                    error_type=outcome.status,
                    error_message=message,
                )
            else:
                message = outcome.reason
                errors.append(f"{row.get('symbol')} {row.get('metricKey')}: {message}")
                self.queue_store.update_auto_fill_status(int(row["id"]), True, capability.autoFillType, "failed", message)
                self.queue_store.log_automation_action(
                    run_id,
                    "auto_fill_failed",
                    row,
                    str(row.get("reviewStatus") or ""),
                    str(row.get("reviewStatus") or ""),
                    message,
                )
                self.queue_store.log_autopilot_run_item(
                    run_id,
                    row,
                    "auto_fill_failed",
                    message,
                    str(row.get("reviewStatus") or ""),
                    str(row.get("reviewStatus") or ""),
                    error_type=outcome.errorType or "unexpected_exception",
                    error_message=message,
                )
                auto_fill_failed += 1

        rebuild_symbols = sorted(set(symbols) | auto_fill_symbols)
        if rebuild_symbols:
            self.builder.build_review_queue_for_watchlist(rebuild_symbols)

        post_rows = _filter_rows(self.queue_store.list_items(), filters)
        qwen_eligible_count = self._record_qwen_eligibility(post_rows)
        before = {int(row["id"]): dict(row) for row in post_rows}
        automation_result = self.automation_service.automate_rows(
            post_rows,
            mode="autopilot",
            selected_filters={**filters, "automationMode": AUTOPILOT_MODE, "runId": run_id},
        )
        after_rows = _filter_rows(self.queue_store.list_items(), filters)
        auto_archive_extra = apply_auto_archive_rules(after_rows, self.queue_store, run_id)
        if auto_archive_extra:
            after_rows = _filter_rows(self.queue_store.list_items(), filters)
        after = {int(row["id"]): dict(row) for row in after_rows}
        for item_id, old in before.items():
            new = after.get(item_id)
            if not new:
                continue
            old_status = str(old.get("reviewStatus") or "")
            new_status = str(new.get("reviewStatus") or "")
            if old_status == new_status:
                continue
            if new_status in {"approved", "auto_archived"}:
                self.queue_store.log_automation_action(
                    run_id,
                    "auto_apply_safe_result" if new_status == "approved" else "auto_archive",
                    old,
                    old_status,
                    new_status,
                    str(new.get("aiExplanationZh") or new.get("correctionNotes") or "autopilot"),
                    actor="qwen" if new_status == "approved" else "autopilot",
                )

        human_remaining = _human_remaining(after_rows)
        result = ReviewAutopilotResult(
            runId=run_id,
            scannedCount=scanned,
            processableCount=processable_count,
            autoFillAttemptedCount=auto_fill_attempted,
            autoFillSuccessCount=auto_fill_success,
            autoFillFailedCount=auto_fill_failed,
            evidenceBackfilledCount=int(backfill_result.get("backfilled") or 0),
            unsupportedCount=unsupported_count,
            qwenEligibleCount=qwen_eligible_count,
            qwenReviewedCount=int(automation_result.qwen_reviewed or 0),
            autoApprovedCount=int(automation_result.auto_approved or 0),
            autoArchivedCount=int(automation_result.auto_archived or 0) + auto_archive_extra,
            needsHumanCount=human_remaining,
            skippedCount=max(0, scanned - processable_count),
            failedCount=auto_fill_failed + int(automation_result.failed or 0),
            errors=[*errors, *(automation_result.errors or [])],
            unsupported=unsupported,
            message=(
                f"扫描 {scanned} 条，可处理 {processable_count} 条，自动补齐成功 {auto_fill_success} 条，"
                f"Qwen复核 {int(automation_result.qwen_reviewed or 0)} 条，自动确认 {int(automation_result.auto_approved or 0)} 条，"
                f"自动归档 {int(automation_result.auto_archived or 0) + auto_archive_extra} 条，仍需人工 {human_remaining} 条，"
                f"暂不支持 {unsupported_count} 条，失败 {auto_fill_failed + int(automation_result.failed or 0)} 条。"
            ),
        )
        self.queue_store.log_autopilot_run_finish(
            run_id,
            {
                "scannedCount": result.scannedCount,
                "processableCount": result.processableCount,
                "autoFillSuccessCount": result.autoFillSuccessCount,
                "evidenceBackfilledCount": result.evidenceBackfilledCount,
                "qwenReviewedCount": result.qwenReviewedCount,
                "autoApprovedCount": result.autoApprovedCount,
                "autoArchivedCount": result.autoArchivedCount,
                "needsHumanCount": result.needsHumanCount,
                "unsupportedCount": result.unsupportedCount,
                "failedCount": result.failedCount,
            },
        )
        self.queue_store.log_operation(
            "review_autopilot",
            {**filters, "runId": run_id, "automationMode": AUTOPILOT_MODE, "synced": sync_result.total},
            processable_count,
            result.autoApprovedCount + result.autoArchivedCount + result.autoFillSuccessCount,
            result.skippedCount,
            result.failedCount,
            result.errors,
        )
        return result

    def run_auto_fill_only(self, rows: list[dict]) -> ReviewAutopilotResult:
        run_id = str(uuid.uuid4())
        attempted = succeeded = failed = unsupported = 0
        errors: list[str] = []
        unsupported_messages: list[str] = []
        for row in identify_missing_data_items(rows):
            capability = auto_fill_capability(row)
            self.queue_store.update_auto_fill_status(
                int(row["id"]),
                capability.canAutoFill,
                capability.autoFillType,
                "not_started" if capability.canAutoFill else "not_available",
                None if capability.canAutoFill else capability.reason,
            )
            if not capability.canAutoFill:
                unsupported += 1
                continue
            attempted += 1
            outcome = self._run_auto_fill(row, capability)
            if outcome.status == "success":
                self.queue_store.update_auto_fill_status(int(row["id"]), True, capability.autoFillType, "success")
                succeeded += 1
                self._convert_successful_autofill_to_review_items(str(row.get("symbol") or ""), outcome.result or {})
            elif outcome.status in {"unsupported_by_current_pipeline", "not_auto_fillable_yet", "company_not_disclosed", "needs_manual_source"}:
                unsupported += 1
                message = _friendly_unsupported_reason(outcome.reason)
                unsupported_messages.append(message)
                self.queue_store.update_auto_fill_status(int(row["id"]), True, capability.autoFillType, "not_available", message)
            else:
                failed += 1
                message = outcome.reason
                errors.append(message)
                self.queue_store.update_auto_fill_status(int(row["id"]), True, capability.autoFillType, "failed", message)
        return ReviewAutopilotResult(
            runId=run_id,
            scannedCount=len(rows),
            processableCount=len([row for row in rows if _is_processable(row)]),
            autoFillAttemptedCount=attempted,
            autoFillSuccessCount=succeeded,
            autoFillFailedCount=failed,
            unsupportedCount=unsupported,
            skippedCount=max(0, len(rows) - len([row for row in rows if _is_processable(row)])),
            failedCount=failed,
            errors=errors,
            unsupported=unsupported_messages,
        )

    def _sync_review_queue(self, symbols: Iterable[str]):
        symbols = [symbol for symbol in symbols if symbol]
        if symbols:
            return self.builder.build_review_queue_for_watchlist(symbols)
        return self.builder.build_review_queue_for_watchlist(load_watchlist())

    def _run_auto_fill(self, row: dict, capability: AutoFillCapability) -> AutoFillOutcome:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            return AutoFillOutcome("failed", "missing symbol", errorType="unexpected_exception")
        if capability.autoFillType == "analyst_estimates":
            if not os.getenv("FMP_API_KEY"):
                return AutoFillOutcome("not_auto_fillable_yet", "analyst estimates endpoint not configured", errorType="not_auto_fillable_yet")
        snapshot = self.fundamental_cache.get_snapshot(symbol, max_age_hours=24 * 3650) or {"ticker": symbol, "symbol": symbol}
        history = self.price_cache.get_history(f"FMP:{symbol}", max_age_hours=24 * 3650, min_rows=20)
        technicals = latest_technical_snapshot(add_technical_indicators(history)) if history is not None and not history.empty else {}
        if capability.autoFillType == "technical_indicator" and not technicals:
            return AutoFillOutcome("needs_manual_source", "historical price data unavailable for technical recalculation", errorType="no_source_available")
        model_type = str(row.get("modelType") or "SAAS_SOFTWARE")
        try:
            result = self.disclosure_pipeline.run(
                symbol,
                model_type=model_type,
                current_snapshot=snapshot,
                current_technicals=technicals,
                price_history=history,
                force_refresh=False,
            )
        except Exception as exc:  # pragma: no cover - defensive production path
            return AutoFillOutcome("failed", str(exc), errorType="unexpected_exception")
        logs = [log for log in (result.get("logs") or []) if isinstance(log, dict)]
        if logs and all(str(log.get("status")) == "skipped" for log in logs):
            return AutoFillOutcome(
                "unsupported_by_current_pipeline",
                "当前数据管线暂不支持该指标自动补齐",
                result,
                errorType="unsupported_by_current_pipeline",
            )
        return AutoFillOutcome("success", "auto fill completed", result)

    def _convert_successful_autofill_to_review_items(self, symbol: str, result: dict) -> int:
        symbol = str(symbol or result.get("symbol") or "").upper()
        if not symbol:
            return 0
        created = 0
        for saved in result.get("saved") or []:
            if not isinstance(saved, dict):
                continue
            source_type = str(saved.get("sourceType") or "")
            if source_type not in EXTRACTED_VALUE_SOURCES:
                continue
            if saved.get("value") is None:
                continue
            outcome = self.queue_store.upsert_item(
                {
                    "symbol": symbol,
                    "metricKey": saved.get("metricKey"),
                    "displayName": saved.get("displayName") or saved.get("metricKey"),
                    "itemType": "extracted_value",
                    "value": saved.get("value"),
                    "unit": saved.get("unit"),
                    "period": saved.get("period") or "latest",
                    "sourceType": source_type,
                    "sourceUrl": saved.get("sourceUrl"),
                    "sourceDocumentTitle": saved.get("sourceDocumentTitle"),
                    "extractedText": saved.get("extractedText") or saved.get("evidenceWindow") or "",
                    "evidenceText": saved.get("evidenceText") or saved.get("extractedText") or saved.get("evidenceWindow") or "",
                    "metricPeriod": saved.get("metricPeriod") or saved.get("period"),
                    "fiscalPeriod": saved.get("fiscalPeriod"),
                    "extractionRule": saved.get("extractionRule"),
                    "confidence": saved.get("confidence") or "medium",
                    "affects": _affects_for_autofill_metric(saved.get("metricKey")),
                    "reviewStatus": "pending_review",
                    "recommendedAction": "Qwen证据复核后确认",
                    "resolutionStatus": "available",
                    "sourceKind": "autopilot_saved_metric",
                    "sourceMetricId": None,
                    "modelType": result.get("modelType"),
                    "explanation": "自动补齐后生成的复核项。",
                }
            )
            if outcome == "created":
                created += 1
        return created

    def _record_qwen_eligibility(self, rows: list[dict]) -> int:
        eligible_count = 0
        for row in rows:
            if row.get("id") is None:
                continue
            eligible, reason = qwen_review_eligibility(row)
            if eligible:
                eligible_count += 1
            self.queue_store.update_qwen_eligibility(int(row["id"]), eligible, None if eligible else reason)
        return eligible_count


def run_review_autopilot(filters: dict | None = None) -> ReviewAutopilotResult:
    return ReviewAutopilot().run_review_autopilot(filters)


def identify_missing_data_items(rows: list[dict]) -> list[dict]:
    return [
        row for row in rows
        if str(row.get("reviewStatus") or "") == "needs_data"
        or str(row.get("resolutionStatus") or "") in {
            "requires_ir_scrape",
            "requires_sec_filing",
            "requires_analyst_estimates",
            "missing_inputs",
            "manual_override_required",
        }
    ]


def auto_fill_capability(row: dict) -> AutoFillCapability:
    status = str(row.get("resolutionStatus") or "")
    metric = str(row.get("metricKey") or "")
    action = f"{row.get('recommendedAction') or ''} {row.get('explanation') or ''}".lower()
    if status == "company_not_disclosed":
        return AutoFillCapability(False, "not_auto_fillable", "公司未披露，不能无限重试自动补齐。")
    if status == "requires_ir_scrape":
        return AutoFillCapability(True, "ir_release", "抓取 IR / 8-K 财报材料。")
    if status == "requires_sec_filing":
        return AutoFillCapability(True, "sec_filing", "抓取 SEC 10-K / 10-Q / 8-K。")
    if status == "requires_analyst_estimates":
        if os.getenv("FMP_API_KEY"):
            return AutoFillCapability(True, "analyst_estimates", "从分析师预期端点补齐。")
        return AutoFillCapability(False, "not_auto_fillable", "未配置 analyst estimates 数据源。")
    if status == "missing_inputs":
        if metric in TECHNICAL_METRICS or "ema" in metric.lower() or "rsi" in metric.lower() or "technical" in action:
            return AutoFillCapability(True, "technical_indicator", "重新计算技术指标。")
        return AutoFillCapability(True, "calculated_metric", "刷新结构化数据并重新计算。")
    if status == "manual_override_required":
        if "ir" in action or "8-k" in action:
            return AutoFillCapability(True, "ir_release", "先尝试 IR / 8-K 自动补齐。")
        if "sec" in action or "10-k" in action or "10-q" in action:
            return AutoFillCapability(True, "sec_filing", "先尝试 SEC 文件自动补齐。")
        return AutoFillCapability(False, "not_auto_fillable", "该项没有明确自动来源，建议人工补充。")
    if str(row.get("reviewStatus") or "") == "needs_data":
        return AutoFillCapability(True, "ir_release", "需要补齐项，先尝试 IR / SEC 数据补全。")
    return AutoFillCapability(False, "not_auto_fillable", "当前状态不需要自动补齐。")


def _symbols_from_filters(filters: dict) -> list[str]:
    symbol = filters.get("symbol")
    if symbol:
        return [str(symbol).upper()]
    return load_watchlist()


def _filter_rows(rows: list[dict], filters: dict) -> list[dict]:
    result = []
    for row in rows:
        if filters.get("symbol") and str(row.get("symbol") or "").upper() != str(filters["symbol"]).upper():
            continue
        if filters.get("metric_key") and str(row.get("metricKey") or "") != str(filters["metric_key"]):
            continue
        if filters.get("item_type") and str(row.get("itemType") or "") != str(filters["item_type"]):
            continue
        if filters.get("source_type") and str(row.get("sourceType") or "") != str(filters["source_type"]):
            continue
        if filters.get("confidence") and str(row.get("confidence") or "") != str(filters["confidence"]):
            continue
        if filters.get("review_status") and str(row.get("reviewStatus") or "") != str(filters["review_status"]):
            continue
        result.append(row)
    return result


def apply_auto_archive_rules(rows: list[dict], queue_store: ReviewQueueStore, run_id: str | None = None) -> int:
    archived = 0
    for row in rows:
        if not _should_auto_archive(row):
            continue
        old_status = str(row.get("reviewStatus") or "")
        old, new = queue_store.auto_archive_item(int(row["id"]), _auto_archive_reason(row))
        if not new:
            continue
        archived += 1
        if run_id:
            queue_store.log_automation_action(
                run_id,
                "auto_archive",
                row,
                old_status,
                "auto_archived",
                _auto_archive_reason(row),
            )
            queue_store.log_autopilot_run_item(
                run_id,
                row,
                "auto_archive",
                _auto_archive_reason(row),
                old_status,
                "auto_archived",
            )
    return archived


def _human_remaining(rows: list[dict]) -> int:
    return sum(1 for row in rows if _needs_human_attention(row))


def _is_processable(row: dict) -> bool:
    if str(row.get("reviewStatus") or "") in {"approved", "rejected", "manually_corrected", "auto_archived", "duplicate_archived", "invalid_review_item"}:
        return False
    if str(row.get("aiTriageStatus") or "") in {"auto_approved_by_ai", "ai_auto_archived"}:
        return False
    if row.get("hiddenByDefault"):
        return False
    if str(row.get("resolutionStatus") or "") in {"calculated", "available", "not_applicable"} and str(row.get("itemType") or "") != "extracted_value":
        return False
    return True


def _needs_human_attention(row: dict) -> bool:
    if not _is_processable(row):
        return False
    if _should_auto_archive(row):
        return False
    affects = _affects(row.get("affects"))
    triage = str(row.get("aiTriageStatus") or "")
    status = str(row.get("reviewStatus") or "")
    item_type = str(row.get("itemType") or "")
    autofill_status = str(row.get("autoFillStatus") or "")
    if triage in {"ai_recommend_correct", "ai_recommend_reject"}:
        return True
    if triage == "ai_not_enough_evidence" and affects & {"Quality", "Entry", "Risk"}:
        return True
    if item_type == "qualitative_risk" and (affects & {"Risk", "Action", "Position", "maxPosition"}):
        return True
    if status == "needs_data" and affects & {"Quality", "Entry", "Risk"}:
        return True
    if status == "needs_evidence" and affects & {"Quality", "Entry", "Risk", "Action", "Position", "maxPosition"}:
        return True
    if item_type == "manual_override_needed" and affects & {"Quality", "Entry", "Risk"}:
        return True
    if autofill_status == "failed" and affects & {"Quality", "Entry", "Risk"}:
        return True
    return False


def _should_auto_archive(row: dict) -> bool:
    if str(row.get("reviewStatus") or "") in {"approved", "rejected", "manually_corrected", "auto_archived"}:
        return False
    if str(row.get("aiTriageStatus") or "") in {"auto_approved_by_ai", "ai_auto_archived"}:
        return False
    affects = _affects(row.get("affects"))
    item_type = str(row.get("itemType") or "")
    status = str(row.get("resolutionStatus") or "")
    confidence = str(row.get("confidence") or "")
    if status == "not_applicable":
        return True
    if not affects or affects <= {"ConfidenceOnly", "ExplanationOnly"}:
        return True
    if str(row.get("reviewStatus") or "") == "needs_evidence" and not (affects & {"Quality", "Entry", "Risk", "Action", "Position", "maxPosition"}):
        return True
    if str(row.get("itemType") or "") == "evidence_missing_extracted_value" and not (affects & {"Quality", "Entry", "Risk", "Action", "Position", "maxPosition"}):
        return True
    if item_type == "analyst_estimate_needed" and affects <= {"Entry", "ConfidenceOnly", "ExplanationOnly"}:
        return True
    if item_type == "derived_low_confidence" and status == "derived_score" and confidence in {"medium", "high"}:
        return not (affects & {"Action", "Risk", "Position", "maxPosition"})
    if item_type == "missing_kpi" and "proxy" in str(row.get("explanation") or row.get("recommendedAction") or "").lower():
        return not (affects & {"Action", "Risk", "Position", "maxPosition"})
    if (
        str(row.get("modelType") or "") == "POWER_GENERATION"
        and str(row.get("metricKey") or "") in {"adjustedEbitda", "adjustedFcfBeforeGrowth", "hedgeCoverage"}
        and str(row.get("autoFillStatus") or "") == "not_available"
    ):
        return True
    if item_type == "qualitative_risk" and not (affects & {"Risk", "Action", "Position", "maxPosition"}):
        return True
    if status == "company_not_disclosed" and not (affects & {"Quality", "Risk", "Action", "Position", "maxPosition"}):
        return True
    if str(row.get("autoFillStatus") or "") == "not_available" and not (affects & {"Quality", "Entry", "Risk"}):
        return True
    return False


def _auto_archive_reason(row: dict) -> str:
    status = str(row.get("resolutionStatus") or "")
    item_type = str(row.get("itemType") or "")
    if status == "not_applicable":
        return "不适用指标，自动归档。"
    if item_type == "analyst_estimate_needed":
        return "仅影响买点/估值置信度，不进入人工主队列。"
    if item_type == "derived_low_confidence":
        return "规则推导仅低权重保留，不影响 Action / 风险 / 仓位。"
    if item_type == "missing_kpi":
        return "已有可用代理或非核心缺口，自动归档为低优先级。"
    return "低优先级或仅解释项，自动归档。"


def _friendly_unsupported_reason(reason: str) -> str:
    raw = str(reason or "")
    if "pipeline skipped for this model" in raw or "暂不支持" in raw:
        return "当前数据管线暂不支持该指标自动补齐"
    if "not configured" in raw or "未配置" in raw:
        return "当前未配置对应数据源"
    if "not disclosed" in raw or "未披露" in raw:
        return "公司未披露或当前来源未覆盖"
    return raw or "当前数据管线暂不支持该指标自动补齐"


def _affects(value: object) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if item}
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def _affects_for_autofill_metric(metric_key: object) -> str:
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
